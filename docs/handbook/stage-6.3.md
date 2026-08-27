# Artifact, OAuth, Site, and Debugger Routes  `stage-6.3`

This stage is a set of side doors into the system. It is not the main work loop. Instead, it supports sharing, login handoffs, hosted site access, and safe inspection by trusted operators.

The sandbox ingress server receives public site requests. It checks signed site links, turns them into short-lived browser sessions, and then serves the site either from saved files in blob storage or by forwarding traffic to a live sandbox. The hosted site surface sits in front of that. It shows the public frame for a permanent site link, manages redirects to the real site origin, and decides who is allowed to enter before any site content is shown.

The artifact route does the same kind of guarding for shared files. It serves downloads or previews only when the link has a valid signature, and it can refresh expired links for signed-in workspace members. The CLI surface finishes OAuth, which is the “approve access on another service” flow, after the outside provider redirects back. The debugger surface gives trusted operators read-only pages and APIs to inspect sessions without changing them.

## Files in this stage

### Hosted Site Access
Routes and gates for turning public site links into authorized browser access and serving hosted site content.

### `core/src/ufo/sandbox/ingress_serve.py`

`entrypoint` · `startup and request handling`

This file is the front door for sites built or served by sandboxes. Each site gets its own signed subdomain, so the browser treats it as a separate origin, meaning its cookies and local storage stay separate from other sites. Without this server, a user could not safely open a sandbox-built site in the product: static files would not be fetched, live development servers would not be reachable, and authorization would be easy to bypass or cache incorrectly.

The flow is like a guarded office lobby. First, a special view link arrives at a reserved path. The server checks the signed token, confirms it belongs to the site named by the hostname, then sets a host-only session cookie and redirects to the requested site path. Later HTTP requests and WebSocket connections must present that cookie.

Once a request is allowed, the file chooses how to serve it. If the site was stored, it streams the named file directly from the blob store and adds strict cache and framing rules. If the site is live, it finds the sandbox port through the carrier system and proxies the request without reading the whole body into memory. It also cleans unsafe headers and cookies so sandbox code cannot steal the ingress session, set cookies for sibling hosts, or override who may frame the site. WebSockets get the same authorization gate plus an origin check, then are relayed both ways with size limits.

#### Function details

##### `IngressServe.app`  (lines 300–332)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI web application that receives all HTTP and WebSocket traffic for sandbox sites. It carefully reserves the special view-token path so tokens are not accidentally forwarded to user code.

**Data flow**: It starts with the configured IngressServe object → creates a FastAPI app → attaches routes for opening signed view links, proxying ordinary HTTP requests, refusing token paths over WebSocket, and relaying normal WebSockets → returns the ready app.

**Call relations**: The process startup code creates an IngressServe and passes this app to Uvicorn. After that, incoming traffic is dispatched to methods such as _open, _proxy, _no_socket_view, and _socket.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 334–340)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers the reserved view path when no token is present. It prevents an empty or query-string-only path from being treated as a valid site-opening link.

**Data flow**: It receives an HTTP request → checks whether the method is GET or HEAD → returns either a 405 method response or a 403 message saying the link is not valid.

**Call relations**: The route table calls this for the bare view path. It protects the same token-opening area that _open handles when a token is actually present.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 342–395)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Trades a signed one-time-style view token for a short-lived site session cookie. This is how a browser is allowed into a specific site origin without exposing the token to the site's own code.

**Data flow**: It receives the request and the path after the view prefix → splits out the token and optional entry path → reads the addressed site from the hostname → verifies the token, site identity, and optional framer → mints a session token, sets it as a secure host cookie, and redirects to the requested site path.

**Call relations**: FastAPI calls this for view-token URLs. It relies on _site to interpret the hostname and _framer_belongs to validate sibling framing, then hands the browser back into the normal _proxy path through a redirect.

*Call graph*: calls 2 internal fn (_framer_belongs, _site); 9 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, cookie_secure, set_session_cookie, quote).


##### `IngressServe._site`  (lines 397–408)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Figures out which sandbox site a request is trying to reach by reading the hostname. It returns the site identity only if the host belongs under this deployment’s configured wildcard domain and has a valid signed label.

**Data flow**: It receives an HTTP or WebSocket connection → reads the hostname → checks the expected base host suffix → parses the site label into a conversation id and port → returns that pair, or returns nothing if the host is not valid.

**Call relations**: _open uses this to make sure a view token is being used on the right host. _authorized uses it as the first step of the shared access check for HTTP and WebSocket traffic.

*Call graph*: called by 2 (_authorized, _open); 1 external calls (parse_site_label).


##### `IngressServe._authorized`  (lines 410–432)

```
def _authorized(self, connection: HTTPConnection) -> IngressClaims | SiteRefusal
```

**Purpose**: Applies the common access gate for both normal web requests and WebSockets. It confirms that the host names a real site and that the session cookie grants access to exactly that site.

**Data flow**: It receives a connection → asks _site what site the host names → reads the ingress session cookie → verifies the signed session token and expiry → compares the token’s site to the host’s site → returns claims for allowed traffic or a SiteRefusal explaining the denial.

**Call relations**: _proxy and _socket both call this before serving anything. This keeps static files, live HTTP proxying, and WebSockets behind the same authorization rule.

*Call graph*: calls 1 internal fn (_site); called by 2 (_proxy, _socket); 3 external calls (__init__, now, verify_ingress_token).


##### `IngressServe._stored_manifest`  (lines 434–472)

```
async def _stored_manifest(self, claims: IngressClaims) -> dict[str, StoredFile] | None
```

**Purpose**: Checks whether the requested site should be served from stored files instead of a live sandbox. If so, it returns a map of site paths to blob-store file records.

**Data flow**: It receives verified ingress claims → if the claims name a shipped app, it delegates to _shipped_manifest → otherwise it reads the hosted_site row for the workspace, conversation, and port → parses the stored JSON manifest → returns StoredFile entries, or nothing if the site is live rather than stored.

**Call relations**: _proxy calls this to decide between static file serving and live proxying. _socket also calls it so stored sites can refuse WebSockets instead of trying to dial a sandbox.

*Call graph*: calls 1 internal fn (_shipped_manifest); called by 2 (_proxy, _socket); 4 external calls (__init__, loads, select, workspace_tx).


##### `IngressServe._shipped_manifest`  (lines 474–506)

```
async def _shipped_manifest(self, shipped: ShippedClaim) -> dict[str, StoredFile] | None
```

**Purpose**: Builds or retrieves the file list for a deploy-wide shipped app bundle. These are shared application files stored by digest, not workspace-specific sandbox output.

**Data flow**: It receives a shipped-app claim → checks an in-memory cache → lists matching files in the fleet blob store if needed → converts each listing into a StoredFile keyed by the URL path that should serve it → returns the manifest or nothing if the digest is gone.

**Call relations**: _stored_manifest calls this when a session claim points to shipped application code. Later, _serve_stored reads the actual bytes described by the returned file records.

*Call graph*: called by 1 (_stored_manifest); 3 external calls (__init__, __init__, guess_type).


##### `IngressServe._dial_site`  (lines 508–538)

```
async def _dial_site(self, claims: IngressClaims) -> DialTarget | SiteRefusal
```

**Purpose**: Finds the live network address for a sandbox site that is still served by a running container. It turns a conversation and port into a concrete host, protocol, and headers needed for proxying.

**Data flow**: It receives authorized claims → enters the workspace context → reads the stored sandbox handle → picks the current or resume carrier → turns the handle into a container id → asks the carrier to dial the requested port → returns a DialTarget or a SiteRefusal if the sandbox is gone or unreachable.

**Call relations**: _proxy uses this before forwarding live HTTP requests. _socket uses it before opening a WebSocket connection to the sandbox.

*Call graph*: calls 1 internal fn (_stored_handle); called by 2 (_proxy, _socket); 6 external calls (__init__, __init__, warn, sandbox_handle_backend, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 540–606)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Serves every ordinary HTTP request for a site. It either streams a stored file or forwards the request to a live sandbox server while enforcing authorization, cache safety, framing rules, and cookie isolation.

**Data flow**: It receives the request path → runs _authorized → asks _stored_manifest whether files exist → if stored, hands off to _serve_stored → otherwise dials the live site with _dial_site → builds an upstream URL and cleaned headers → streams the request and response → rewrites or drops unsafe response headers and returns the browser response.

**Call relations**: This is the catch-all HTTP route installed by app. It coordinates many helper methods: _body streams upstream bytes, _confined_cookie filters Set-Cookie headers, _unframed_policy edits security policy, and _frame_ancestors writes this server’s own framing rule.

*Call graph*: calls 10 internal fn (_authorized, _body, _confined_cookie, _dial_site, _frame_ancestors, _serve_stored, _stored_manifest, _unframed_policy, _upstream_headers, _upstream_url); 7 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, ws).


##### `IngressServe._serve_stored`  (lines 608–674)

```
async def _serve_stored(self, request: Request, claims: IngressClaims, files: dict[str, StoredFile], path: str) -> Response
```

**Purpose**: Serves one file from a stored static site directly out of blob storage. This lets a deployed site remain viewable even when its original sandbox is no longer running.

**Data flow**: It receives the request, claims, file manifest, and URL path → allows only GET and HEAD → finds either the exact file or an index.html under that path → prepares cache, ETag, content type, and framing headers → returns 304 when the browser’s copy is current, HEAD without a body when requested, or streams the blob bytes for GET.

**Call relations**: _proxy calls this after _stored_manifest proves the site is stored. It uses _frame_ancestors for browser embedding policy and _stored_body to stream already-opened blob content safely.

*Call graph*: calls 2 internal fn (_frame_ancestors, _stored_body); called by 1 (_proxy); 5 external calls (__init__, __init__, Response, StreamingResponse, ws).


##### `IngressServe._stored_body`  (lines 676–682)

```
async def _stored_body(self, first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Streams the remaining bytes of a stored blob after the first chunk has already been checked. This avoids sending a successful response before discovering that the file is missing.

**Data flow**: It receives the first byte chunk and an async iterator for the rest → yields the first chunk → yields each later chunk as it arrives → produces a streaming body for the HTTP response.

**Call relations**: _serve_stored calls this only after it has successfully read the first chunk. The result becomes the body of the StreamingResponse sent to the browser.

*Call graph*: called by 1 (_serve_stored).


##### `IngressServe._framer_belongs`  (lines 684–719)

```
async def _framer_belongs(self, workspace_id: UUID, conversation_id: UUID, port: int) -> bool
```

**Purpose**: Checks whether a site named as an allowed sibling framer really belongs to the same workspace. This prevents a signed view from allowing an unrelated workspace’s site to embed it.

**Data flow**: It receives a workspace id, conversation id, and port → looks for a matching hosted_site row → if not found, looks at provisioned shipped apps in the workspace and compares their derived site identity → returns true only if the framer is known inside that workspace.

**Call relations**: _open calls this when token claims include a framer site. Its answer controls whether the session cookie is minted or the link is rejected.

*Call graph*: called by 1 (_open); 5 external calls (select, workspace_tx, serve_port, shipped_anchor, shipped_app_slug).


##### `IngressServe._frame_ancestors`  (lines 721–729)

```
def _frame_ancestors(self, claims: IngressClaims) -> str
```

**Purpose**: Builds the browser rule that says which page is allowed to frame this site. It always includes the main app origin when configured, and may add one approved sibling site from the session claims.

**Data flow**: It receives verified claims → if framing is disabled or no sibling framer is present, returns the configured frame ancestor value → otherwise turns the sibling site identity into its origin URL → returns a space-separated browser policy value.

**Call relations**: _proxy and _serve_stored put this value into the Content-Security-Policy header. It works with _open and _framer_belongs, which decide whether a sibling framer may be present in the session at all.

*Call graph*: called by 2 (_proxy, _serve_stored); 1 external calls (site_label).


##### `IngressServe._upstream_url`  (lines 731–734)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Creates the exact URL used to contact a live sandbox server. It preserves the requested path and query string while safely quoting path characters.

**Data flow**: It receives a scheme, host, path, and raw query string → percent-encodes the path where needed → appends the decoded query string if present → returns the full upstream URL.

**Call relations**: _proxy uses this for live HTTP forwarding. _socket uses it for live WebSocket forwarding.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 736–746)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Reads the sandbox handle saved for a conversation. The handle is the stored reference needed to find the running or resumable container behind a live site.

**Data flow**: It receives a workspace id and conversation id → queries the conversation table under that workspace → returns the saved sandbox_handle value, or nothing if no matching conversation exists.

**Call relations**: _dial_site calls this before asking a carrier to dial a port. If no usable handle is found, _dial_site refuses the request as a gone site.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 748–782)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Builds the safe set of request headers to send to a live sandbox. It removes transport-only headers, the ingress session cookie, WebSocket handshake fields, and anything the dial layer must control.

**Data flow**: It receives the viewer connection and dial-supplied headers → walks through the incoming headers → drops or edits unsafe ones, including removing the ingress cookie from Cookie → appends the dial headers → returns the final header list for the upstream request.

**Call relations**: _proxy uses this when creating the HTTP request to the sandbox. _socket uses it when opening the upstream WebSocket handshake.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 784–796)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes a site’s own frame-ancestors directive from a Content-Security-Policy header while preserving the rest of the policy. This lets core decide who may frame hosted sites without discarding the site’s other browser protections.

**Data flow**: It receives one policy header string → splits it into semicolon-separated directives → filters out only the frame-ancestors directive → joins the remaining directives → returns the cleaned policy, or an empty string if nothing remains.

**Call relations**: _proxy calls this while copying response headers from a live sandbox. If it returns content, that cleaned policy is appended to the response beside core’s own framing policy.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 798–825)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Filters one Set-Cookie header from a sandbox response so the site can set only its own host-scoped cookies. It blocks cookies in the reserved ufo_ namespace and removes Domain attributes that would escape to sibling hosts.

**Data flow**: It receives a raw Set-Cookie header → parses the cookie name and attributes → drops nameless cookies, malformed cookies, and reserved-name cookies → removes any Domain attribute from the rest → returns the safe header or nothing.

**Call relations**: _proxy calls this for each Set-Cookie header coming back from a live sandbox. Safe cookies are forwarded to the browser; unsafe ones are silently not relayed.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 827–837)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw bytes from a live upstream HTTP response and makes sure the upstream connection is closed afterward. This prevents leaked pooled connections when streaming ends early or fails.

**Data flow**: It receives an httpx upstream response → yields each raw chunk to the client response → closes the upstream response in a finally step no matter how the stream ends.

**Call relations**: _proxy uses this as the body of a StreamingResponse for live sandbox traffic. The background close also exists, but this helper protects cases where streaming fails before background cleanup runs.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 839–845)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Refuses WebSocket attempts to the reserved view-token path. That path is only for exchanging a token over HTTP, not for opening a socket to user code.

**Data flow**: It receives a WebSocket handshake → creates a refusal saying the link is not valid → sends that refusal as an HTTP denial response instead of accepting the socket.

**Call relations**: The app route table calls this for WebSocket handshakes on the view path. It uses _refuse, the same helper used by _socket for denied handshakes.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 847–903)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Relays an authorized WebSocket between the browser and a live sandbox site. This supports things like live reload or application protocols that need two-way messages instead of plain HTTP.

**Data flow**: It receives the WebSocket and path → checks that the Origin host matches the requested site → runs _authorized → refuses stored or shipped sites because they have no live socket server → dials the sandbox → opens an upstream WebSocket with safe headers and subprotocols → accepts the browser socket only after the site accepts → relays messages until one side ends.

**Call relations**: This is the catch-all WebSocket route installed by app. It coordinates _same_origin, _authorized, _stored_manifest, _dial_site, _upstream_headers, _relay, _refuse, and _end.

*Call graph*: calls 9 internal fn (_authorized, _dial_site, _end, _refuse, _relay, _same_origin, _stored_manifest, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 905–917)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site host it is trying to reach. This blocks one sandbox site from using the browser’s cookie to open a socket into another site.

**Data flow**: It receives the WebSocket handshake → reads the Origin header → compares the origin hostname with the requested URL hostname → returns true only when they match and Origin is present.

**Call relations**: _socket calls this before any authorization or dialing. It is a WebSocket-only protection because browser WebSocket handshakes are not protected by normal cross-origin reading rules.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 919–926)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Rejects a WebSocket handshake with the same status and body an HTTP request would have received. This gives denied socket attempts a clear explanation instead of an unexplained close.

**Data flow**: It receives a WebSocket and a SiteRefusal → builds a normal HTTP Response from the refusal → sends it as the WebSocket denial response → leaves the socket unaccepted.

**Call relations**: _no_socket_view and _socket call this whenever a handshake should not proceed. It keeps denial behavior consistent across different WebSocket failure reasons.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 928–945)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs the two halves of a WebSocket relay at the same time: browser to site and site to browser. When either side finishes, it stops the other side too.

**Data flow**: It receives the accepted viewer socket and upstream site connection → starts two async tasks, one in each direction → waits for the first to finish → cancels the unfinished side → raises any real error from the completed side.

**Call relations**: _socket calls this after both WebSocket connections are open. It delegates the actual message copying to _viewer_to_site and _site_to_viewer.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 947–956)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the sandbox server. It preserves whether each message is text or bytes, because many socket protocols care about that difference.

**Data flow**: It receives the viewer socket and upstream connection → repeatedly reads the next browser message → stops if the browser disconnects → sends text messages as text and binary messages as bytes to the upstream site.

**Call relations**: _relay starts this as one of its two relay tasks. It runs until the viewer disconnects or an error stops the relay.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 958–971)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the sandbox server back to the browser and then closes the browser side with an appropriate close code. It avoids sending close codes that the WebSocket standard forbids on the wire.

**Data flow**: It receives the upstream connection and viewer socket → forwards each upstream text or byte message to the viewer → when upstream ends, chooses a safe close code and reason → asks _end to close the viewer socket.

**Call relations**: _relay starts this as the other relay task. It calls _end to finish the browser connection cleanly after the site stops speaking.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 973–983)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Closes the browser WebSocket without letting close-time errors hide the original problem. It treats an already-gone browser as an acceptable final state.

**Data flow**: It receives a viewer socket, close code, and reason → attempts to close the socket → suppresses any exception raised while closing → returns nothing.

**Call relations**: _site_to_viewer uses this for normal upstream shutdown. _socket also uses it after relay failures so the browser gets a terminal close when possible.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 986–997)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the wildcard base hostname used to recognize site subdomains. It fails startup if this required public ingress URL is missing or not a usable URL.

**Data flow**: It receives the configured ingress public URL → parses out the hostname → returns the host if present → raises a runtime error if no host can be found.

**Call relations**: run calls this during startup before the server begins accepting traffic. The result is stored on IngressServe and later used by _site to decide whether a request belongs to this ingress.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 1000–1010)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Builds the deploy-wide browser origin allowed to frame hosted sites. If the main app public URL is not configured, it returns a policy value that allows no framing.

**Data flow**: It receives the configured app base URL → parses scheme, host, and optional port → returns an origin string such as https://host:port, or 'none' when no valid origin exists.

**Call relations**: run calls this during startup and passes the result into IngressServe. Later _frame_ancestors uses it as the base framing rule for every site response.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 1013–1036)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to talk to live sandbox servers. It is deliberately cookie-blind so cookies from one sandbox cannot leak into requests to another sandbox.

**Data flow**: It creates timeout and connection-limit settings → creates a cookie jar whose policy stores no domains → builds and returns an httpx AsyncClient with those limits and the inert cookie jar.

**Call relations**: run calls this once at startup and passes the client into IngressServe. _proxy then uses that client for all live HTTP forwarding.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 1039–1066)

```
def run() -> None
```

**Purpose**: Starts the sandbox ingress process. It loads configuration, prepares observability, database access, carriers, blob storage, and finally runs the ASGI web server.

**Data flow**: It reads configuration → initializes logging/tracing and manifests → connects to the owner database and verifies it is reachable → ensures the ingress signing secret exists → selects sandbox carriers and blob storage → constructs IngressServe → logs startup → runs Uvicorn with WebSocket size and compression settings.

**Call relations**: This is the file’s process entrypoint. It calls ingress_base_host, ingress_frame_ancestor, and upstream_client to prepare the IngressServe instance, then hands IngressServe.app to Uvicorn so request-handling methods can take over.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 14 external calls (__init__, run, blob_store_for, load_config, init_db, verify_db_reachable, load_manifests, init_o11y, log, owner_dsn (+4 more)).


### `extensions/sites/ufo_ext_sites/surface.py`

`domain_logic` · `request handling`

A hosted site link is not a password. It is more like a street address: it names a workspace, conversation, and site name, but each visit still has to pass the front desk. This file runs that front desk. It verifies the signed site token, finds the site, checks the visitor's session cookie when needed, and applies the site's visibility rules: public, workspace-only, or private to the creator and admins. If the site is an agent homepage, the agent's visibility rules take over instead.

The file does not serve the site's actual app files. Instead, after access is allowed, it creates a short-lived ingress URL and either places it inside a safe HTML iframe or redirects a portal iframe there. The iframe is sandboxed, which means the generated site can run scripts and forms but cannot take over the whole browser tab.

It also builds the link preview tags used by chat apps and social crawlers. Public sites may show their real name and custom preview image. Non-public sites only show generic UFO branding, because crawlers have no session and anything in those tags becomes public. A separate anonymous route serves preview images, but only while the site is still public.

Finally, the creator can change visibility through a form protected by a CSRF token, a signed value tied to the viewer's own session so another website cannot secretly submit the change.

#### Function details

##### `site_token`  (lines 174–183)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that names one hosted site. Other code uses this token when it needs a stable link to a site.

**Data flow**: It receives a workspace ID, conversation ID, and site name. It puts those values into a signed surface token for the sites surface. It returns the token as text.

**Call relations**: When `site_url` needs to build a full browser URL, it calls `site_token` first to create the address part that safely identifies the site.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 186–196)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the public URL someone can open to view a hosted site. It refuses to guess if the deployment has no public base URL configured.

**Data flow**: It takes the deployment's public base URL plus the site's workspace, conversation, and name. If the base URL is missing, it raises `SiteHostingUnconfigured`; otherwise it creates a site token and appends it to the site frame path. The result is a complete shareable URL.

**Call relations**: This is the main producer of permanent site links. It delegates the signed-token part to `site_token`, then wraps that token in the deployment's public web address.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `shipped_homepage_url`  (lines 208–223)

```
def shipped_homepage_url(public_base_url: str | None, workspace_id: UUID, slug: str, digest: str) -> str | None
```

**Purpose**: Builds a stable portal-embed URL for a deploy-wide shipped app bundle. Unlike normal hosted sites, this names an app slug and bundle digest rather than a site row.

**Data flow**: It receives the public base URL, workspace ID, shipped app slug, and digest. If there is no public base URL, it returns `None`; otherwise it signs those claims into a surface token and returns the frame URL containing that token.

**Call relations**: This is used by code that needs a portal iframe link for an app bundle shipped with the deployment. Later, `shipped_address` and `frame` understand this token shape and route it through the shipped-app path.

*Call graph*: 1 external calls (mint_surface_token).


##### `shipped_address`  (lines 226–240)

```
def shipped_address(token: str) -> ShippedAddress | None
```

**Purpose**: Checks whether a token is a valid shipped-app token and, if so, extracts the workspace, app slug, and bundle digest from it. It cleanly rejects normal site tokens and broken tokens.

**Data flow**: It receives token text. It verifies the signature and expected surface, checks that the token is marked as a portal embed, converts the workspace claim into a UUID, and returns a `ShippedAddress`. If anything is missing or invalid, it returns `None`.

**Call relations**: `resolve_workspace` uses this before routing so the system can know which workspace the request belongs to. `frame` uses it to decide whether to open the shipped-app flow instead of the normal hosted-site flow.

*Call graph*: called by 2 (frame, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `site_card_url`  (lines 243–252)

```
def site_card_url(public_base_url: str | None, token: str, digest: str) -> str | None
```

**Purpose**: Builds the public URL for a site's custom share-preview image. It returns no URL when the deployment itself has no public address.

**Data flow**: It receives the public base URL, the site token, and the card digest. If the base URL is present, it combines them into the anonymous share-card route ending in the configured image extension. If not, it returns `None`.

**Call relations**: `frame` calls this only when a site is public and has a share-card hash, so `_share_tags` can place the card URL in the page head.

*Call graph*: called by 1 (frame).


##### `site_address`  (lines 255–274)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Checks whether a token is a valid hosted-site token and, if so, extracts the site address from it. This is how the web surface turns an incoming link back into a workspace, conversation, and site name.

**Data flow**: It receives token text. It verifies the token, checks the optional portal-embed marker, converts workspace and conversation IDs into UUIDs, and returns a `SiteAddress`. If the token is unsigned, malformed, or has the wrong kind of claims, it returns `None`.

**Call relations**: `resolve_workspace`, `frame`, `_resolve`, and `homepage_embed_url` all use this as the trusted parser for normal site links. It separates normal hosted-site tokens from shipped-app tokens.

*Call graph*: called by 4 (_resolve, frame, homepage_embed_url, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `homepage_embed_url`  (lines 277–292)

```
def homepage_embed_url(url: str) -> str
```

**Purpose**: Turns a normal hosted-site URL into the special signed version used inside the portal's iframe. It is used when a site is being treated as an agent homepage.

**Data flow**: It receives a full site URL, splits off the final token, and verifies that the URL points at the expected frame path. It then mints a new token with the same site address plus a portal-embed marker. It returns the same URL prefix with the new token.

**Call relations**: It relies on `site_address` to prove the original URL is really a hosted-site URL. The resulting token is later recognized by `frame`, which uses the portal-embed marker to decide whether to redirect directly into ingress or send the viewer back to the portal.

*Call graph*: calls 1 internal fn (site_address); 1 external calls (mint_surface_token).


##### `resolve_workspace`  (lines 295–305)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace an incoming surface request belongs to before any site row is read. This matters because public visitors may have no session cookie to identify a workspace.

**Data flow**: It reads the token from the request path. If it is a normal site token, it returns that token's workspace ID. If it is a shipped-app token, it returns the shipped workspace ID. If neither token is valid, it returns the same 404 response used for missing sites.

**Call relations**: The surface framework calls this early to choose the workspace context. It uses `site_address` and `shipped_address` as the two accepted token formats, and `_not_found` when neither format works.

*Call graph*: calls 3 internal fn (_not_found, shipped_address, site_address).


##### `frame`  (lines 308–384)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a hosted site link. It verifies the token, finds the site, checks who the viewer is allowed to be, and then either shows a safe iframe, redirects a portal iframe, or returns a deliberately vague 404.

**Data flow**: It reads the site token and optional deep path from the request. If the token names a shipped app, it hands off to `_shipped_frame`; otherwise it parses the site address, reads the hosted-site row, builds safe share tags, identifies the viewer from the session cookie, applies site or agent visibility rules, asks the context for an ingress URL, and returns HTML or a redirect. It may also mint a CSRF token for the creator's visibility form.

**Call relations**: This is the main GET route for site links. It coordinates helpers such as `_sites`, `_viewer`, `_viewer_is_admin`, `_share_tags`, `_frame_page`, `_into_the_portal`, `_unconfigured_page`, and `_not_found` so each request ends in exactly one visible result.

*Call graph*: calls 18 internal fn (ingress_url, list_agents, _frame_page, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _page, _session_digest, _share_tags (+8 more)); 3 external calls (HTMLResponse, RedirectResponse, mint_surface_token).


##### `_shipped_frame`  (lines 387–426)

```
async def _shipped_frame(ctx: SurfaceContext, request: Request, shipped: ShippedAddress) -> Response
```

**Purpose**: Opens a deploy-wide shipped app bundle through the same surface frame system. It treats the bundle as public code, while the later ingress session carries any real authority.

**Data flow**: It receives the request context, request, and already-verified shipped address. If the request is not inside the portal iframe, it looks up the matching agent and redirects the browser into the portal. If it is inside the portal iframe, it builds the shipped app's ingress anchor, asks for an ingress URL, and redirects there. If ingress is unavailable, it returns an explanatory HTML page.

**Call relations**: `frame` calls this whenever the token is a shipped-app token. It may call `_into_the_portal` for outside-the-portal visits, or use `_framed_from` and the context's ingress URL machinery for portal iframe visits.

*Call graph*: calls 8 internal fn (ingress_url, list_agents, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _share_tags, _unconfigured_page); called by 1 (frame); 5 external calls (HTMLResponse, RedirectResponse, serve_port, shipped_anchor, shipped_app_slug).


##### `_is_portal_iframe_request`  (lines 429–430)

```
def _is_portal_iframe_request(request: Request) -> bool
```

**Purpose**: Detects whether the browser says this request is loading inside an iframe. The code uses this to distinguish a cold browser visit from the portal embedding the page.

**Data flow**: It reads the `sec-fetch-dest` request header. If the value is `iframe`, it returns `true`; otherwise it returns `false`.

**Call relations**: `frame`, `_shipped_frame`, and `_framed_from` use this small check when choosing between redirecting to the portal and redirecting directly to ingress.

*Call graph*: called by 3 (_framed_from, _shipped_frame, frame).


##### `_framed_from`  (lines 433–436)

```
def _framed_from(request: Request) -> str | None
```

**Purpose**: Returns the referring page only when the current request is actually an iframe request. This gives ingress a safe hint about what page framed it.

**Data flow**: It receives the request. If `_is_portal_iframe_request` says the request is not for an iframe, it returns `None`; otherwise it returns the request's `referer` header.

**Call relations**: `frame` and `_shipped_frame` pass this value into `ctx.ingress_url` so the generated ingress URL can know what page is framing it.

*Call graph*: calls 1 internal fn (_is_portal_iframe_request); called by 2 (_shipped_frame, frame).


##### `_into_the_portal`  (lines 439–452)

```
def _into_the_portal(ctx: SurfaceContext, agent_id: UUID, share: str) -> Response
```

**Purpose**: Redirects a viewer to an agent's page inside the member portal. If the deployment has no portal installed, it returns a simple page explaining that there is nowhere to send them.

**Data flow**: It receives the surface context, an agent ID, and prebuilt share tags. It asks the context for the portal URL pointing at that agent. If no portal URL exists, it builds an HTML response; otherwise it returns a 303 redirect to the portal.

**Call relations**: `frame` uses this for agent homepages reached outside the portal iframe. `_shipped_frame` uses it for shipped-app links opened cold in a browser.

*Call graph*: calls 2 internal fn (home_url, _page); called by 2 (_shipped_frame, frame); 2 external calls (HTMLResponse, RedirectResponse).


##### `_unconfigured_page`  (lines 455–463)

```
def _unconfigured_page(title: str, share: str) -> str
```

**Purpose**: Builds the small HTML page shown when site hosting or ingress is not configured. It avoids showing an empty broken frame.

**Data flow**: It receives a title and share tags. It escapes the title for safe HTML, combines the base styles with frame styles, and returns a complete HTML document explaining that hosting is unconfigured.

**Call relations**: `frame` and `_shipped_frame` use this when access is allowed but the system cannot produce an ingress URL to the actual app.

*Call graph*: calls 1 internal fn (_page); called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `share_card`  (lines 466–505)

```
async def share_card(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a public site's custom link-preview image, and only under strict public conditions. This route is anonymous because chat and social preview crawlers do not carry a user's session.

**Data flow**: It resolves the site from the token in the request. It checks that the site exists, is not an agent homepage, is public, has a stored card image, and that the URL digest matches the row's current digest. If all checks pass, it reads the image bytes from blob storage and returns them with cache headers; otherwise it returns 404.

**Call relations**: This is the GET route for preview images referenced by `_share_tags`. It relies on `_resolve` to find the site and `_not_found` to make every refusal look the same.

*Call graph*: calls 2 internal fn (_not_found, _resolve); 1 external calls (Response).


##### `set_visibility`  (lines 508–529)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets the creator change a hosted site's visibility level. It protects the action so other viewers, unknown sites, homepage-bound sites, and forged form posts cannot change the setting.

**Data flow**: It resolves the site, identifies the viewer from the session cookie, and confirms the viewer is the creator. It rejects homepage-bound sites, checks that the submitted CSRF token matches this session, parses the requested visibility value, writes the new level to the store, and redirects back to the site frame.

**Call relations**: This is the POST route behind the visibility selector rendered by `_selector` inside `_frame_page`. It uses `_viewer`, `_csrf_holds`, `_sites`, and `_not_found` to enforce the same privacy posture as the viewing route.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 532–536)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Finds the hosted-site row named by the request token. It is a shared shortcut for routes that need the site but do not need the full frame-opening flow.

**Data flow**: It reads the token from the request path, parses it with `site_address`, and returns `None` if the token is invalid. If valid, it opens the hosted-sites store for the current workspace and reads the site by conversation ID and name.

**Call relations**: `share_card` and `set_visibility` call this before applying their own route-specific checks. It uses `_sites` to get the workspace-scoped store.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (set_visibility, share_card).


##### `_sites`  (lines 539–540)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates a workspace-scoped access object for hosted-site records. This keeps all site reads and writes tied to the current workspace and transaction setup.

**Data flow**: It receives the surface context, reads the workspace ID and transaction factory from it, and returns a `HostedSites` store object.

**Call relations**: `frame`, `_resolve`, and `set_visibility` call this whenever they need to read or update hosted-site rows.

*Call graph*: called by 3 (_resolve, frame, set_visibility); 1 external calls (__init__).


##### `_viewer_is_admin`  (lines 543–548)

```
async def _viewer_is_admin(ctx: SurfaceContext, viewer: UUID | None) -> bool
```

**Purpose**: Checks whether the current viewer is an admin member of the workspace. Admins are allowed to view private sites and private agent homepages even when they are not the creator or owner.

**Data flow**: It receives the context and a viewer member ID. If there is no viewer, it returns `false`. Otherwise it opens a transaction, reads the workspace seat snapshot, and returns whether one member entry matches the viewer and has the admin flag.

**Call relations**: `frame` calls this during visibility checks for private hosted sites and private agent homepages.

*Call graph*: calls 1 internal fn (transaction); called by 1 (frame); 1 external calls (__init__).


##### `_viewer`  (lines 551–561)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace member represented by the request's `ufo_session` cookie. If the cookie is missing, invalid, or not for this workspace, the viewer is treated as unauthenticated.

**Data flow**: It reads the session cookie from the request. It verifies the bearer token against the current workspace to get an email address. It then finds an already linked member for that email or creates the link, returning the member ID; otherwise it returns `None`.

**Call relations**: `frame` uses this to decide whether a visitor may see a non-public site. `set_visibility` uses it to prove that the form submitter is the site creator.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 564–566)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility-form token belongs to this browser session. This blocks another website from making a creator's browser change visibility without consent.

**Data flow**: It receives the request and submitted token. It verifies the signed token and compares its stored session digest with a fresh digest of the current request's session cookie. It returns `true` only when they match.

**Call relations**: `set_visibility` calls this after proving the viewer is the creator, before accepting the requested visibility change. It relies on `_session_digest` for the session fingerprint.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 569–573)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a one-way fingerprint of the current session cookie for CSRF protection. A one-way fingerprint means the original cookie cannot be read back from the digest.

**Data flow**: It reads the `ufo_session` cookie from the request, encodes it, hashes it with SHA-256, and returns the hexadecimal hash text.

**Call relations**: `frame` uses this when minting the creator's CSRF token. `_csrf_holds` uses it again later to check that a submitted form token matches the same session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 576–577)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard 404 response for unknown, invalid, or unauthorized site access. Using the same body helps avoid revealing which sites exist.

**Data flow**: It takes no input. It returns a plain-text response with the configured not-found body and HTTP status 404.

**Call relations**: Many routes call this when a token is bad, a site is missing, or a viewer is not allowed. This shared response keeps those cases intentionally indistinguishable.

*Call graph*: called by 5 (_shipped_frame, frame, resolve_workspace, set_visibility, share_card); 1 external calls (PlainTextResponse).


##### `_page`  (lines 580–585)

```
def _page(title: str, style: str, body: str, share: str) -> str
```

**Purpose**: Builds a minimal complete HTML document from a title, style block, body, and share-preview tags. It is the common wrapper for pages this surface renders itself.

**Data flow**: It receives text for the title, CSS styles, body HTML, and metadata tags. It concatenates them with the document header, viewport setting, and charset declaration, then returns the HTML string.

**Call relations**: `_frame_page`, `_into_the_portal`, `_unconfigured_page`, and `frame` use this to produce consistent simple pages.

*Call graph*: called by 4 (_frame_page, _into_the_portal, _unconfigured_page, frame).


##### `_share_tags`  (lines 588–622)

```
def _share_tags(name: str | None, canonical: str | None, card: str | None) -> str
```

**Purpose**: Builds the Open Graph and Twitter metadata that make a pasted site link unfurl nicely. It names the real site and image only when the caller has already decided that information is public.

**Data flow**: It receives an optional site name, optional canonical URL, and optional custom card URL. It escapes those values for safe HTML, falls back to generic UFO title and image when needed, and returns a string of metadata tags.

**Call relations**: `frame` calls this for normal hosted-site pages after checking whether the site is public. `_shipped_frame` calls it with generic values for shipped-app pages.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `_frame_page`  (lines 625–664)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str, share: str) -> str
```

**Purpose**: Builds the main hosted-site frame page: a header with the site name and visibility control or badge, plus the iframe containing the actual site. It keeps the hosted site isolated from the surrounding UFO page.

**Data flow**: It receives the site row, an optional ingress URL, the base frame path, an optional CSRF token, and share tags. If a CSRF token is present, it renders the creator's visibility selector; otherwise it renders a read-only badge. If an ingress URL exists, it embeds it in a sandboxed iframe; otherwise it shows the unconfigured-hosting message. It returns a full HTML document.

**Call relations**: `frame` calls this after the visitor has passed the visibility gate. `_frame_page` calls `_selector` for the creator controls and `_page` for the final document wrapper.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 667–677)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the visibility-changing form shown to the site creator. It lets the creator choose private, workspace, or public visibility.

**Data flow**: It receives the current visibility level, the canonical frame path, and the CSRF token. It creates one option per visibility label, marks the current one as selected, adds the hidden CSRF value, and returns the form HTML.

**Call relations**: `_frame_page` calls this only when `frame` has supplied a CSRF token, which means the current viewer is the site creator.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).


### Signed Artifact Links
Routes for serving signed artifact bytes and refreshing expired artifact links for signed-in workspace members.

### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the gatekeeper for shared files. A shared artifact is not served just because someone knows its path. The URL must carry a valid signature, which is like a tamper-proof stamp saying which file is allowed, which workspace owns it, and when the permission expires. Without this route, shared files could not be downloaded from stable links, and expired links in chat or web pages would either break permanently or risk serving files without proper checks.

The main route checks the signed URL, finds the file in the workspace's blob store, and streams it back. Streaming means the server sends the file in pieces instead of loading the whole thing into memory, which matters for large files or many downloads at once. For image previews, it does extra validation before returning bytes inline, so a preview link cannot pretend an unsafe or wrong-sized file is an image.

If the signed URL has expired, the file is not served immediately. Instead, the code checks whether the browser has a valid session cookie and whether that user is a member of the workspace that shared the file. If so, it redirects them to a newly signed URL. If not, a browser is sent to sign in, while non-browser clients get a refusal. This keeps old teammate links useful without making them public.

#### Function details

##### `download`  (lines 59–117)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='', workspace: Annotated[str, Query(alias='ws')]='') -> Response
```

**Purpose**: This is the HTTP endpoint that receives artifact download and preview requests. It verifies that the link is signed and current, then serves the requested file bytes or a validated image preview.

**Data flow**: It takes the incoming web request, the artifact id and filename from the path, and signature fields from the query string. It reads the blob store and signing secret from the application state, verifies the URL claims, checks that the file exists in the claimed workspace, and then either returns a small validated preview response or streams the full file as a download. If the URL is invalid it returns a forbidden error; if the file is missing it returns not found; if the URL is expired it asks the refresh helper to try to recover it for a signed-in workspace member.

**Call relations**: This route is called by FastAPI when a request matches the artifact URL pattern. Its first job is to ask the artifact URL verifier whether the link is legitimate. When verification says the link is expired but otherwise authentic, it hands the request to _refreshed_for_member. When serving a preview it delegates the safety check to validated_image_preview; when serving a download it uses artifact_media_type to choose the browser-facing content type and StreamingResponse to send bytes without buffering the whole file.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 9 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, ws, quote).


##### `_refreshed_for_member`  (lines 120–171)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This helper tries to turn an expired artifact link into a fresh one, but only for a signed-in member of the workspace that owns the shared file. It is what makes an old link in a teammate conversation start working again after sign-in.

**Data flow**: It receives the current request, the expired-but-authentic artifact claims, and the signing secret. It reads the session cookie, verifies the logged-in user's workspace and email, checks the database to confirm that the user is a member and that the shared artifact belongs to that workspace, then creates a new expiry time and returns a redirect to a newly minted signed URL. If any check fails, it produces the same refusal flow used for expired links.

**Call relations**: download calls this only after the signature checker reports that a URL has expired. This helper then consults the session verifier and the workspace database transaction to prove the requester is allowed to refresh the grant. If the proof succeeds, it calls the artifact URL minting flow and returns a redirect; if not, it delegates to _refusal to decide whether to send the browser to login or return a plain forbidden response.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 10 external calls (now, RedirectResponse, or_, select, verified_claims, workspace_tx, artifact_url_expiry, mint_artifact_url, ws, UUID).


##### `_refusal`  (lines 174–179)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This small helper builds the response used when an expired link cannot be refreshed. For normal browsers it points the user toward sign-in; for other clients it gives a simple forbidden error.

**Data flow**: It looks at the request's Accept header to see whether the caller appears to want an HTML page. If so, it encodes the current artifact URL as a return target and creates a redirect-style HTTP error pointing at the login page. Otherwise it creates a forbidden HTTP error explaining that the download link expired.

**Call relations**: _refreshed_for_member calls this whenever there is no valid session, the session has an unusable workspace id, the user is not a member, or the artifact is not owned by that workspace. It centralizes the refusal behavior so all failed refresh attempts respond consistently.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).


### OAuth Return Flow
Browser-facing callback handling for completing external account OAuth connections.

### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

This file is the small web doorway that an external account provider sends the browser back to after a member grants permission. OAuth is a common “sign in or connect with another service” flow: the user approves access on the provider’s site, then the provider redirects back with a short code. This code must be checked and exchanged before the account is really connected.

The file defines a FastAPI router, which is a group of web routes. One route, `/v1/connect/callback`, receives the provider’s return request. It does not rely on a logged-in browser session. Instead, it trusts only a sealed `state` value created earlier by the system. That state proves which member, agent, and conversation started the connection. If the connection system is unavailable, if the state or code is missing, if the state is invalid, or if the provider is unknown, the route returns the right HTTP error.

When the connection succeeds, the file builds a friendly confirmation page using the shared callback page helper. If the original conversation was resumed, the page says the conversation continues. Otherwise it simply tells the user they can close the page.

The second route serves the UFO logo SVG used on that callback page. It serves the exact local asset because this page is reached without the normal frontend app or build system available.

#### Function details

##### `connect_callback`  (lines 38–64)

```
async def connect_callback(state: str='', code: str='') -> HTMLResponse
```

**Purpose**: This is the web endpoint that completes an account-connection return from an OAuth provider. Someone uses it indirectly when their browser is redirected back after approving access on the provider’s site.

**Data flow**: The browser sends in a `state` value and a `code`. The function first asks for the installed connection flow, then rejects the request if the connection service is unavailable or either value is missing. It gives the state and code to the flow, which verifies the sealed state and exchanges the code for the connected account. If that succeeds, it turns the recorded provider and account names into a human-readable headline and returns an HTML page telling the user the account is connected. If something is wrong, it returns an HTTP error instead.

**Call relations**: FastAPI calls this function when a request reaches `/v1/connect/callback`. Inside, it calls `installed_connect_flow` to get the object that knows how to finish the connection, and it relies on that flow’s `complete` step to do the actual verification and exchange. At the end it hands the result to `callback_page`, which creates the final browser page.

*Call graph*: 3 external calls (HTTPException, installed_connect_flow, callback_page).


##### `connect_logo`  (lines 68–76)

```
async def connect_logo() -> Response
```

**Purpose**: This is the web endpoint that serves the UFO logo shown on the connection callback page. It exists so the callback page can show the same logo even when the normal frontend application is not loaded.

**Data flow**: A browser asks for `/v1/connect/logo.svg`. The function reads the SVG logo file from disk, wraps those bytes in an HTTP response, labels it as an SVG image, and adds a long-lived cache header so browsers can safely keep a copy.

**Call relations**: FastAPI calls this function when the logo URL is requested. The function then creates a `Response` object directly, because it only needs to return static image bytes with the correct media type and cache instructions.

*Call graph*: 1 external calls (Response).


### Operator Debugger Inspection
Trusted read-only web and JSON endpoints for inspecting workspaces, conversations, artifacts, transcripts, and live session events.

### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the backend doorway for the debugger UI. Think of it like a glass control-room window: an authorized operator can look across the fleet and then zoom into one workspace, but the routes here only read data. The actual permission check is done before these handlers run by the shared operator workspace resolver; once a request has a SurfaceContext, all reads are tied to that workspace scope.

At startup, the file tries to load a built React app from static/index.html. The root GET route serves that page. The frontend then calls the api/ routes in this file to fetch plain JSON: fleet-wide workspace summaries, workspace metadata, conversations, turns, transcripts, compaction records, workspace files, and turn details.

Most functions follow the same pattern: read an ID from the URL, ask SurfaceContext for the matching data, return JSON, and return a 404-style JSON error when the ID is invalid or the record does not exist. File contents are streamed back as bytes. Live turn output is streamed using SSE, short for Server-Sent Events, a simple browser-friendly stream where the server keeps sending named events over one HTTP response. The ROUTES table at the bottom connects each URL and HTTP method to the right function.

#### Function details

##### `app_page`  (lines 56–61)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the built debugger web app to the browser. If the frontend has not been built yet, it fails loudly with a clear message so the operator is not shown a blank or misleading page.

**Data flow**: It receives the current surface context and HTTP request, reads the already-loaded APP_HTML value from this module, and wraps that HTML text in an HTML response. If APP_HTML is missing, it raises an error explaining how to build the debugger frontend.

**Call relations**: This is the handler for the root GET route. It is the first page an operator sees; after it returns the React app, that app calls the JSON API routes in this same file for the real debugger data.

*Call graph*: 1 external calls (HTMLResponse).


##### `fleet`  (lines 64–68)

```
async def fleet(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the debugger landing-page overview of the deploy’s workspaces and recent threads. It is meant for authorized operators who need to choose which workspace or session to inspect.

**Data flow**: It receives the request context, creates a FleetDirectory reader, asks it for the current fleet directory, converts that structured result into JSON-friendly data, and sends it as a JSON response.

**Call relations**: This is the handler behind the fleet API route. The frontend calls it when showing the broad index before the operator drills into one workspace.

*Call graph*: 2 external calls (__init__, JSONResponse).


##### `workspace_meta`  (lines 71–84)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns small bits of metadata about the currently selected workspace, such as its workspace ID, Slack team ID if known, and the configured Datadog site. This helps the debugger UI link what it is showing to outside systems.

**Data flow**: It asks the SurfaceContext for the Slack installation string, extracts the Slack team ID only when the string has the expected team: prefix, reads the DD_SITE environment variable, and returns those values with the workspace ID as JSON.

**Call relations**: The frontend calls this after the operator has a workspace scope. It relies on SurfaceContext for workspace-specific installation information and on the process environment for Datadog configuration.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 87–89)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the conversations visible in the current workspace scope. Operators use this to pick the session they want to inspect.

**Data flow**: It asks SurfaceContext for the workspace’s conversation list, converts each conversation entry into JSON-friendly form, and returns the list as a JSON response.

**Call relations**: This backs the conversations API route. It is usually called by the debugger app after workspace metadata has been loaded, before the operator opens a specific conversation.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 92–97)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the turns inside one conversation. A turn is one unit of back-and-forth work in a conversation, so this gives the operator a timeline to inspect.

**Data flow**: It reads the conversation_id path value and uses _uuid_param to turn it into a UUID. If that fails, it returns a JSON 404 error. Otherwise it asks SurfaceContext for the turns in that conversation, converts them to JSON-friendly dictionaries, and returns them.

**Call relations**: This route is called when the debugger UI opens a conversation. It uses _uuid_param as a small guard before handing the valid conversation ID to SurfaceContext.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 100–107)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the stored transcript for one conversation. This gives an operator the readable record of what was said or produced in that conversation.

**Data flow**: It converts the conversation_id from the URL with _uuid_param. If the ID is invalid, it returns a JSON 404 error. If the ID is valid, it asks SurfaceContext for the transcript; when no transcript exists, it returns a separate JSON 404 error, otherwise it returns the transcript data as JSON.

**Call relations**: The debugger app calls this when an operator opens the transcript view for a conversation. It depends on _uuid_param for safe ID parsing and SurfaceContext for the workspace-scoped read.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 110–114)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is when older conversation content is summarized or compressed, and this lets operators see where those changes happened.

**Data flow**: It reads and validates the conversation_id from the URL. If it is not a valid UUID, it returns a JSON 404 error. Otherwise it asks SurfaceContext for the compaction indexes or records for that conversation, turns the returned iterable into a list, and sends it as JSON.

**Call relations**: This route supports the debugger’s compaction overview. It prepares the conversation ID with _uuid_param, then delegates the actual read to SurfaceContext.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 117–132)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full detail for one compaction record, including what messages existed before, what remained after, and the summary that replaced them. This helps operators understand exactly how conversation history was shortened.

**Data flow**: It reads the conversation_id and compaction index from the URL. The conversation ID must be a valid UUID and the index must be digits; otherwise it returns a JSON 404 error. It then asks SurfaceContext for that specific compaction. If found, it returns the index, before messages, after messages, and summary as JSON.

**Call relations**: The frontend calls this after the operator selects one compaction from the compaction list. It uses _uuid_param for the conversation ID and hands the cleaned index to SurfaceContext.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 135–140)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with one conversation’s workspace. Operators use this to see what files were available or produced during that session.

**Data flow**: It turns the conversation_id URL value into a UUID with _uuid_param. If that fails, it returns a JSON 404 error. Otherwise it asks SurfaceContext for the files linked to that conversation and returns each file entry as JSON.

**Call relations**: This backs the files list in the debugger UI. It sits between the browser and SurfaceContext, doing only ID validation and JSON formatting.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 143–153)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file to the operator. It is used when the debugger UI needs to download or display the raw file behind a listed file entry.

**Data flow**: It validates the conversation_id from the URL. It then asks SurfaceContext to open the requested file path for that conversation. If the ID is bad, the path is rejected, or no stream exists, it returns a JSON 404 error. If a stream is found, it returns a binary streaming response.

**Call relations**: The frontend calls this after choosing a file from workspace_files. It relies on SurfaceContext to enforce what file paths are valid and uses StreamingResponse so the file can be sent without loading it all into memory first.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 156–163)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information for one turn. This is the main detail view for a single unit of conversation work.

**Data flow**: It reads the turn_id path parameter, converts it to a UUID using _uuid_param, and returns a JSON 404 error if that fails. It then asks SurfaceContext for the turn detail. If no detail exists, it returns a JSON 404 error; otherwise it returns the detail as JSON.

**Call relations**: The debugger UI calls this when an operator selects a turn. It uses _uuid_param for safe parsing, then relies on SurfaceContext for the workspace-scoped lookup.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `turn_steps`  (lines 166–173)

```
async def turn_steps(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the step-by-step records inside one turn. This lets an operator see how a turn progressed internally rather than only seeing the final result.

**Data flow**: It validates the turn_id from the URL. If invalid, it returns a JSON 404 error. It asks SurfaceContext for the turn’s steps; if none are found, it returns a JSON 404 error. Otherwise it converts each step to JSON-friendly form and returns the list.

**Call relations**: This supports the detailed turn timeline in the debugger UI. It follows the same pattern as turn: parse with _uuid_param, read through SurfaceContext, return JSON.

*Call graph*: calls 2 internal fn (turn_steps, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 176–181)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger watch new activity as it happens, rather than repeatedly asking for updates.

**Data flow**: It validates the turn_id and checks that the turn exists. If not, it returns a JSON 404 error. It reads the Last-Event-ID header, which tells the server where a dropped stream should resume, then returns a text/event-stream response built from _events.

**Call relations**: The frontend calls this when it wants live updates for a turn. This function performs the upfront checks, then hands the ongoing streaming work to _events.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 184–187)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the live tail of a turn into bytes suitable for an HTTP event stream. It is the bridge between internal live frames and what the browser receives.

**Data flow**: It receives the SurfaceContext, a turn UUID, and a resume cursor string. It opens SurfaceContext.tail for that turn, then for each incoming cursor and live frame, it calls _sse to encode one Server-Sent Event and yields those bytes to the streaming response.

**Call relations**: stream calls this after confirming the turn exists. _events keeps reading from SurfaceContext.tail and delegates the exact event formatting to _sse.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 190–216)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as a Server-Sent Events message. It names the event by the kind of frame, includes the frame’s raw JSON, and optionally includes an event ID so the browser can resume later.

**Data flow**: It receives a cursor string and one LiveFrame object. If the cursor is not empty, it writes it as the SSE id. It then checks the frame’s concrete kind, chooses an event name such as terminal, parked, cost, activity, reply, or text, serializes the frame to JSON, and returns the finished bytes. If the frame kind is unknown, it raises an error instead of silently sending a confusing event.

**Call relations**: _events calls this once for every live frame it reads. This function is deliberately low-level: it does not choose which frames to stream, only how to label and encode each one.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 219–223)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely converts a URL path parameter into a UUID, which is a standard unique identifier format. It gives the route handlers a simple way to reject malformed IDs.

**Data flow**: It reads the named path parameter from the request and tries to build a UUID from its text. If the text is valid, it returns the UUID object. If the text is not a valid UUID, it returns None so the caller can respond with a not-found error.

**Call relations**: Many route handlers call this before reading conversations, turns, files, transcripts, or compactions. It keeps the repeated ID-parsing logic in one small helper so each route can focus on its own read operation.

*Call graph*: called by 9 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, turn_steps, workspace_file, workspace_files); 1 external calls (UUID).
