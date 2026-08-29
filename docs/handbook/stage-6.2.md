# Web portal, hosted-site, and sandbox ingress routes  `stage-6.2`

This stage is the web-facing front door for people using UFO in a browser. It sits in the main running system, after startup has configured servers and storage, and it decides what a visitor is allowed to see. The web portal file serves the main app, signs users in, carries chat messages, streams live agent replies, and shows workspace pages like settings, memory, sources, usage, and admin. The sites surface is the doorway for hosted sites: it checks access, shows a protected frame, redirects portal iframes, serves share images, or hides private details behind a plain 404.

The sandbox ingress pieces let browser pages safely reach work happening inside isolated workspaces. One file builds temporary port URLs, another signs and checks the short-lived tokens inside those URLs, and another creates safe per-conversation hostnames so guessed names cannot expose private data. The ingress server then verifies the visitor and either serves saved static files or proxies traffic to a live sandbox port. Two supporting web surfaces let operators inspect stored memory and let users browse community skills from skills.sh with friendly error messages.

## Files in this stage

### Core sandbox ingress
Builds signed sandbox access URLs and hostnames, serves public site ingress traffic, and validates short-lived port tokens.

### `core/src/ufo/sandbox/ingress_url.py`

`domain_logic` · `request handling`

A sandbox may run a web app on an internal port, but a browser outside the system needs a safe public URL to reach it. This file creates that URL. Think of it like printing a time-limited visitor badge: the badge says which workspace, which conversation, which port, and when it expires.

The main function starts with the system’s public ingress URL. If there is no public URL configured, it returns nothing, because there is nowhere public to send the browser. It then creates claims, which are pieces of trusted information that will be sealed into an ingress token. The token includes the workspace, conversation, port, expiry time, and optional “shipped” details that identify a packaged version of the app.

The URL host also gets a site label derived from the conversation and port, so different sandbox ports can be separated by subdomain. The final path includes a fixed ingress view path, the minted token, and the requested entry path.

There is also a helper for framed pages. If the sandbox page is being shown inside another sandbox page, it checks whether the outer page comes from the same ingress base host and then records which conversation and port framed it. Invalid or unrelated framing origins are ignored rather than trusted.

#### Function details

##### `mint_ingress_view_url`  (lines 17–50)

```
def mint_ingress_view_url(public_url: str | None, workspace_id: UUID, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest:
```

**Purpose**: This function creates the full temporary URL a browser can use to view one sandbox port. It is used when the system needs to expose a workspace’s running web service safely, with an expiry time and enough identity information for later access checks.

**Data flow**: It receives the public ingress URL, workspace and conversation IDs, a port, an entry path, and optional framing or shipped-app details. It first returns nothing if no public URL exists. Otherwise it splits the public URL into parts, builds optional shipped information, creates signed token claims with an expiry time, asks for a host label for this conversation and port, safely escapes the entry path, and returns one complete URL string. It does not change stored state; its output is the minted URL.

**Call relations**: This is the file’s main outward-facing function. While building the token it asks `_framer_claim` whether the request came from another trusted sandbox URL, then passes the gathered claims to the ingress token code for signing and uses the ingress host code to create the subdomain label.

*Call graph*: calls 1 internal fn (_framer_claim); 7 external calls (__init__, __init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `_framer_claim`  (lines 53–73)

```
def _framer_claim(base: SplitResult, framed_from: str | None) -> FramerClaim | None
```

**Purpose**: This helper decides whether a `framed_from` URL is a trusted sandbox page that is embedding the new page. If it is trusted, it turns that source page into a small claim describing the framing conversation and port; otherwise it returns nothing.

**Data flow**: It receives the already-parsed base public URL and an optional URL for the page doing the framing. It parses the framing URL, compares its scheme, port, and host against the base ingress host, and only continues if the framing host is a subdomain of that base host. It then removes the base host suffix, parses the remaining site label into a conversation ID and port, and returns a `FramerClaim`. If any part is missing, malformed, or not from the expected host family, the result is `None`.

**Call relations**: `mint_ingress_view_url` calls this helper while preparing token claims. The helper delegates label decoding to the ingress host parser, and only hands a `FramerClaim` back when the framing page can be tied to another sandbox site under the same public ingress address.

*Call graph*: called by 1 (mint_ingress_view_url); 3 external calls (__init__, parse_site_label, urlsplit).


### `core/src/ufo/sandbox/ingress_host.py`

`domain_logic` · `request handling and sandbox URL creation`

Hosted sandbox sites need their own web address so that browser cookies, storage, redirects, and root-based asset paths stay separate from every other site. This file builds the single DNS label for that address from two facts: the conversation's unique ID and the sandbox port. It then adds a short cryptographic signature, like a tamper-evident seal, so the server can reject made-up labels before doing any deeper work.

The label is not the main permission check. A valid token or session cookie still decides whether a visitor may see the site. The label's job is earlier and narrower: it proves that the hostname was minted by this deployment and says which conversation and port it points to.

The file also keeps the spelling of labels strict. Base32 text can sometimes decode to the same bytes in more than one written form, and browsers would treat those spellings as different origins. To avoid sixteen lookalike addresses for the same site, parsing re-encodes the bytes and accepts only the one canonical lowercase spelling.

Besides labels, the file derives stable ports for conversation sites and stable synthetic IDs for shipped app pages that do not have a normal conversation row behind them.

#### Function details

##### `serve_port`  (lines 57–63)

```
def serve_port(conversation_id: UUID) -> int
```

**Purpose**: This chooses the stable local port for a conversation's hosted sandbox site. It means the system does not have to store a separate port value; everyone can recompute the same answer from the conversation ID.

**Data flow**: It receives a conversation UUID. It treats that ID as a large number, folds it into the allowed app-port range, adds the configured floor, and returns the resulting port number. It does not change anything outside itself.

**Call relations**: Other code that needs to publish or reach a conversation's hosted site can call this before building the site's address. Its result is commonly paired with site_label so the hostname and port agree.


##### `shipped_app_slug`  (lines 66–74)

```
def shipped_app_slug(provisioned_by: str | None) -> str | None
```

**Purpose**: This extracts the stable page slug from the name of a provisioned shipped app, if the name has the expected form. The slug is used because app identity should not depend on a display name that might be changed or suffixed after a naming collision.

**Data flow**: It receives a provision name, or nothing. If there is no name, it returns nothing. If the name exactly matches the pattern app_<letters-and-digits>, it returns the slug part after app_; otherwise it returns nothing.

**Call relations**: Code dealing with shipped app origins can call this before creating stable addresses or bundle paths. It is a small filter that turns a provision record's naming convention into the slug used by shipped_anchor and related origin-building code.


##### `shipped_anchor`  (lines 77–82)

```
def shipped_anchor(workspace_id: UUID, slug: str) -> UUID
```

**Purpose**: This creates a stable synthetic UUID for a shipped app page inside a workspace. It gives a shipped page its own browser storage and cookies even though there is no normal conversation record behind it.

**Data flow**: It receives a workspace UUID and an app slug. It combines them with a fixed anchor label and feeds that text into UUID version 5, which is a repeatable way to turn a name into a UUID. The same workspace and slug always produce the same UUID; different workspaces or slugs produce different anchors.

**Call relations**: After code has identified a shipped app slug, it can call this to get the conversation-like identity used for origins. Internally it hands the combined name to uuid.uuid5 so the identity is deterministic rather than random.

*Call graph*: 1 external calls (uuid5).


##### `site_label`  (lines 85–90)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: This builds the DNS label for one conversation's sandbox port. The label is the compact hostname piece that says, in signed form, "this conversation at this port."

**Data flow**: It receives a conversation UUID and a port. It first rejects ports outside the valid network range. Then it joins the conversation bytes and the two-byte port, signs those bytes, appends the signature, and turns the result into lowercase base32 text. The returned string is safe to use as a DNS label.

**Call relations**: When the system needs to publish or link to a hosted sandbox site, this is the function that mints the label. It relies on _signature to add the tamper-evident seal and _encode to turn the raw bytes into DNS-friendly text. parse_site_label later performs the reverse check on incoming hostnames.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 93–105)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: This reads a DNS label back into the conversation ID and port it claims to address, but only if the label is well-formed, canonical, and signed by this deployment. It is the guard at the door before the server trusts a sandbox hostname.

**Data flow**: It receives the label text from a hostname. It decodes the base32 text, checks that re-encoding gives exactly the canonical lowercase spelling, separates the address bytes from the signature bytes, verifies the signature using a constant-time comparison, and returns the UUID plus port. If any step fails, it raises SiteLabelError instead of returning possibly unsafe data.

**Call relations**: Request-handling code can call this when a sandbox hostname arrives. It hands decoding to base64.b32decode, canonical spelling to _encode, signature recreation to _signature, safe signature comparison to hmac.compare_digest, and UUID construction to uuid.UUID. Its output is meant to be checked against the visitor's token or session before any sandbox is contacted.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 108–109)

```
def _encode(raw: bytes) -> str
```

**Purpose**: This converts raw label bytes into the project's one accepted DNS-label spelling. It hides the base32 details so label creation and label checking use exactly the same text format.

**Data flow**: It receives bytes. It base32-encodes them, removes padding equals signs, lowercases the result, and returns that string. It does not read or change outside state.

**Call relations**: site_label calls this after assembling and signing label bytes. parse_site_label calls it again to make sure an incoming label is the canonical spelling of the bytes it decoded, which prevents multiple browser origins for the same underlying site.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 112–114)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: This creates the short cryptographic signature attached to a site label. The signature proves that the label bytes were made with this deployment's secret, without exposing that secret.

**Data flow**: It receives the raw address bytes, reads the ingress secret, and computes an HMAC, which is a keyed hash used as a tamper check. It includes a fixed label kind so this signature cannot be confused with signatures for another purpose, then returns only the first four bytes of the digest.

**Call relations**: site_label calls this when minting a new label, and parse_site_label calls it to recreate the expected signature for an incoming label. It depends on ingress_secret for the shared deployment secret and hmac.new for the cryptographic calculation.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/sandbox/ingress_serve.py`

`entrypoint` · `startup and request handling`

Think of this file as a guarded receptionist for many temporary websites. Each site gets its own hostname, and the hostname itself encodes which conversation and port the browser is trying to reach. Before any bytes are served, the server checks a signed token stored in a cookie, so one site link cannot be reused for another site.

There are two main ways a site can be served. A “stored” site is like a published folder: its files were copied into blob storage, so requests are answered directly from stored bytes. A “dialed” site is still running inside a sandbox container, so this server finds the right sandbox port and streams the request and response between the browser and that live process.

The file also protects important browser boundaries. It strips or rewrites headers that could leak sessions, poison caches, or stop the portal from framing the site. It prevents agent-authored sites from setting cookies in UFO’s reserved namespace. For WebSockets, it checks that the connection really comes from the same site origin, then relays messages both ways with size limits. Without this file, hosted sites would either be unreachable from the browser, or worse, reachable without the careful isolation that keeps workspaces, sessions, cookies, and cached content separate.

#### Function details

##### `IngressServe.app`  (lines 300–332)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI web application that receives all HTTP and WebSocket traffic for hosted sites. It claims the special view-token path before the catch-all proxy route so tokens are not accidentally forwarded to site code.

**Data flow**: It starts with the configured IngressServe object → creates an application with routes for opening view links, proxying ordinary HTTP paths, refusing token paths over WebSocket, and relaying other WebSockets → returns the ready-to-run web app.

**Call relations**: The process startup code asks this method for the application before handing it to the web server. After that, the routes it registers send requests into _open, _proxy, _no_view_token, _no_socket_view, or _socket depending on the path and protocol.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 334–340)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers requests to the bare view-token path when no token was provided. It prevents a malformed or empty link from being treated as a real site-opening link.

**Data flow**: It receives an HTTP request → checks whether the method is allowed for view links → returns either a 405 “method not allowed” response or a 403 saying the link is not valid.

**Call relations**: The app router sends bare view-path requests here instead of to _open. This keeps query-string tricks from being interpreted as a token.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 342–395)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a short-lived signed view link into a host-only session cookie for the specific site origin. This is the doorway from the portal frame into the hosted site.

**Data flow**: It receives the request and the path after the view prefix → extracts the token and optional entry path → checks the hostname, verifies the token, confirms it matches the addressed site, and optionally confirms the named framing site belongs to the workspace → mints a session token, sets it as a cookie, and redirects the browser to the site path.

**Call relations**: The route table sends token-opening HTTP requests here. It uses _site to understand the hostname and _framer_belongs when a sibling site is allowed to frame this one; successful requests then continue as normal site requests handled by _proxy.

*Call graph*: calls 2 internal fn (_framer_belongs, _site); 9 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, cookie_secure, set_session_cookie, quote).


##### `IngressServe._site`  (lines 397–408)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Figures out which site a request is trying to reach by reading the request hostname. If the host is not one of this ingress server’s signed site names, it returns nothing.

**Data flow**: It receives an HTTP or WebSocket connection → compares its hostname to the configured base host → parses the remaining label into a conversation ID and port → returns that pair, or None if the host is not valid.

**Call relations**: _open uses this to make sure a view token is being opened at the right origin. _authorized uses it as the first step in the shared gate for both HTTP and WebSocket traffic.

*Call graph*: called by 2 (_authorized, _open); 1 external calls (parse_site_label).


##### `IngressServe._authorized`  (lines 410–432)

```
def _authorized(self, connection: HTTPConnection) -> IngressClaims | SiteRefusal
```

**Purpose**: Checks whether an incoming HTTP request or WebSocket handshake is allowed to reach the addressed site. It is the shared security gate for static files, live proxying, and sockets.

**Data flow**: It receives a connection → reads the site identity from the host → verifies the session cookie as a signed ingress session token → checks that the token names the same conversation and port as the hostname → returns the claims if valid, or a SiteRefusal with the status and message to show if not.

**Call relations**: _proxy calls this before serving any HTTP response. _socket calls it before opening any WebSocket to a sandbox, so both protocols obey the same authorization rule.

*Call graph*: calls 1 internal fn (_site); called by 2 (_proxy, _socket); 3 external calls (__init__, now, verify_ingress_token).


##### `IngressServe._stored_manifest`  (lines 434–472)

```
async def _stored_manifest(self, claims: IngressClaims) -> dict[str, StoredFile] | None
```

**Purpose**: Finds out whether the authorized site should be served from stored files instead of a live sandbox. If so, it returns a map of request paths to stored file metadata.

**Data flow**: It receives verified ingress claims → if the claims name a shipped app, it delegates to _shipped_manifest → otherwise it reads the hosted-site row for this workspace, conversation, and port → parses the saved manifest JSON → returns StoredFile entries, or None if the site is not stored.

**Call relations**: _proxy uses this to decide between direct blob serving and live sandbox proxying. _socket uses it to reject sockets for static stored sites, because a folder of files has no socket server.

*Call graph*: calls 1 internal fn (_shipped_manifest); called by 2 (_proxy, _socket); 4 external calls (__init__, loads, select, workspace_tx).


##### `IngressServe._shipped_manifest`  (lines 474–506)

```
async def _shipped_manifest(self, shipped: ShippedClaim) -> dict[str, StoredFile] | None
```

**Purpose**: Builds or reuses a file manifest for a deploy-wide shipped app bundle. These are application files shared across workspaces and addressed by a content digest.

**Data flow**: It receives a shipped-app claim → checks an in-memory cache → lists files under the digest in the fleet blob store if needed → converts each blob entry into a StoredFile keyed by the URL path that should serve it → returns the manifest or None if the bundle is gone.

**Call relations**: _stored_manifest calls this when the session claims refer to shipped app code rather than a workspace-hosted site row. The result later lets _serve_stored stream those files from the fleet store.

*Call graph*: called by 1 (_stored_manifest); 3 external calls (__init__, __init__, guess_type).


##### `IngressServe._dial_site`  (lines 508–538)

```
async def _dial_site(self, claims: IngressClaims) -> DialTarget | SiteRefusal
```

**Purpose**: Locates the live sandbox endpoint for a site that is not being served from stored files. It turns an authorized site claim into the actual host, port, TLS setting, and extra headers needed to contact the sandbox.

**Data flow**: It receives verified claims → reads any stored sandbox handle for the conversation → chooses the current or resume carrier as needed → builds a sandbox handle → asks the carrier to dial the requested port → returns a DialTarget, or a SiteRefusal if the sandbox is gone or unreachable.

**Call relations**: _proxy calls this before forwarding HTTP traffic to a live site. _socket calls it before opening an upstream WebSocket.

*Call graph*: calls 1 internal fn (_stored_handle); called by 2 (_proxy, _socket); 6 external calls (__init__, __init__, warn, sandbox_handle_backend, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 540–606)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Handles ordinary HTTP requests for hosted sites. It either serves a stored file from blob storage or streams the request to a live sandbox and streams the response back.

**Data flow**: It receives the request and requested path → authorizes the session → checks for a stored manifest → serves stored files when present → otherwise dials the sandbox, builds an upstream request with safe headers and streamed body, sends it, and returns a streaming response with cleaned headers and protective cache/frame/cookie rules.

**Call relations**: This is the catch-all HTTP route registered by app. It depends on _authorized, _stored_manifest, _serve_stored, _dial_site, _upstream_url, _upstream_headers, _body, _frame_ancestors, _unframed_policy, and _confined_cookie to keep proxying safe.

*Call graph*: calls 10 internal fn (_authorized, _body, _confined_cookie, _dial_site, _frame_ancestors, _serve_stored, _stored_manifest, _unframed_policy, _upstream_headers, _upstream_url); 7 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, ws).


##### `IngressServe._serve_stored`  (lines 608–674)

```
async def _serve_stored(self, request: Request, claims: IngressClaims, files: dict[str, StoredFile], path: str) -> Response
```

**Purpose**: Serves one file from a stored static site or shipped app bundle. It lets a site keep working even if the sandbox that produced it is no longer running.

**Data flow**: It receives the request, verified claims, a manifest of files, and a path → checks that the method is GET or HEAD → chooses the exact file or an index.html under the requested directory → sets cache, ETag, content type, and framing headers → returns 304 for matching browser cache, a HEAD response, a 404 for missing blobs, or a streaming response with file bytes.

**Call relations**: _proxy calls this after _stored_manifest finds stored files. It uses _frame_ancestors for browser framing policy and _stored_body to stream after confirming the blob exists.

*Call graph*: calls 2 internal fn (_frame_ancestors, _stored_body); called by 1 (_proxy); 5 external calls (__init__, __init__, Response, StreamingResponse, ws).


##### `IngressServe._stored_body`  (lines 676–682)

```
async def _stored_body(self, first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Streams the rest of a stored file after the first chunk has already been safely read. Reading the first chunk early lets the server return a clean 404 if the blob disappeared before sending a 200 response.

**Data flow**: It receives the first bytes and an async iterator for the remaining bytes → yields the first chunk → yields each later chunk unchanged → produces the byte stream consumed by the HTTP response.

**Call relations**: _serve_stored calls this only after it has successfully pulled the first blob chunk. The streaming response then uses it to send the file to the browser.

*Call graph*: called by 1 (_serve_stored).


##### `IngressServe._framer_belongs`  (lines 684–719)

```
async def _framer_belongs(self, workspace_id: UUID, conversation_id: UUID, port: int) -> bool
```

**Purpose**: Checks whether a site named as an allowed framer really belongs to the same workspace. This prevents a signed link from allowing an unrelated site to wrap another site in an iframe.

**Data flow**: It receives a workspace ID, conversation ID, and port → looks for a matching hosted-site row → if none is found, checks provisioned shipped apps for a matching derived site identity → returns true if the framer is valid for that workspace, otherwise false.

**Call relations**: _open calls this while exchanging a view token for a session. If it returns false, _open refuses the link instead of setting a session cookie.

*Call graph*: called by 1 (_open); 5 external calls (select, workspace_tx, serve_port, shipped_anchor, shipped_app_slug).


##### `IngressServe._frame_ancestors`  (lines 721–729)

```
def _frame_ancestors(self, claims: IngressClaims) -> str
```

**Purpose**: Builds the browser rule that says which page is allowed to embed this site in a frame. It normally allows the main app origin, and may also allow one verified sibling site.

**Data flow**: It receives verified claims → if framing is disabled or no framer is named, returns the deploy-wide frame ancestor value → otherwise turns the framer’s site identity into a full origin and appends it → returns the value used in the Content-Security-Policy header.

**Call relations**: _proxy and _serve_stored call this before sending site content so every live and stored response carries the same framing boundary.

*Call graph*: called by 2 (_proxy, _serve_stored); 1 external calls (site_label).


##### `IngressServe._upstream_url`  (lines 731–734)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the exact URL used to contact a live sandbox server. It safely quotes the path and preserves the original query string.

**Data flow**: It receives a scheme, upstream host, path, and raw query bytes → encodes the path using the allowed safe characters → appends the query when present → returns the upstream URL string.

**Call relations**: _proxy uses this for HTTP forwarding. _socket uses it for WebSocket forwarding.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 736–746)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Reads the saved sandbox handle for a conversation in a workspace. The handle is needed to reconnect to the live sandbox that owns the requested port.

**Data flow**: It receives workspace and conversation IDs → queries the conversation row under that workspace → returns the sandbox handle string, or None if there is no matching conversation.

**Call relations**: _dial_site calls this before asking a carrier to dial the sandbox. A missing handle leads _dial_site to refuse the request as a gone site.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 748–782)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Chooses which browser headers are safe to send to the sandbox. It removes proxy-only headers, WebSocket handshake headers, the ingress session cookie, and anything the dial target must override.

**Data flow**: It receives the incoming request or handshake plus headers supplied by the dial target → walks through the viewer’s headers → drops unsafe or internal ones, edits Cookie to remove the UFO session cookie, and preserves repeatable site-owned headers → appends the dial target headers → returns the final header list for the upstream request.

**Call relations**: _proxy uses this for live HTTP requests and _socket uses it for upstream WebSocket connections. This is a key boundary between browser state owned by the site and private ingress state.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 784–796)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes only the frame-control part from a site’s Content-Security-Policy header. The rest of the site’s browser safety policy is preserved.

**Data flow**: It receives one Content-Security-Policy header value → splits it into directives → filters out frame-ancestors → joins the remaining directives → returns the rewritten policy, or an empty string if nothing remains.

**Call relations**: _proxy calls this while copying live sandbox response headers. The ingress then supplies its own frame-ancestors rule so framing behavior is controlled consistently.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 798–825)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Rewrites or drops a Set-Cookie header from a sandbox so the site cannot set cookies outside its own hostname or inside UFO’s reserved cookie namespace.

**Data flow**: It receives one Set-Cookie header string → parses the cookie name → drops nameless cookies, malformed cookies, and names starting with the reserved prefix → removes any Domain attribute from allowed cookies → returns the confined header, or None to drop it.

**Call relations**: _proxy calls this for each Set-Cookie response header from a live sandbox before sending it to the browser.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 827–837)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw response bytes from a live sandbox back to the browser and makes sure the upstream response is closed afterward. This avoids leaking pooled network connections when a stream ends early or fails.

**Data flow**: It receives an httpx upstream response → yields each raw byte chunk as it arrives → always closes the upstream response in the end → produces the stream used by the outgoing HTTP response.

**Call relations**: _proxy wraps this in a StreamingResponse after sending a live request upstream. It works with the background close task as a second safety net.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 839–845)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Rejects WebSocket attempts to the special view-token path. View tokens are only meant to be exchanged over HTTP, not exposed to site code through a socket path.

**Data flow**: It receives a WebSocket handshake → builds a refusal saying the link is not valid → sends that refusal instead of accepting the socket.

**Call relations**: The app registers this for the view path over WebSocket. It delegates the actual denial response to _refuse.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 847–903)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Handles WebSocket connections for live sandbox sites. It authorizes the viewer, checks same-origin safety, opens a matching WebSocket to the sandbox, then relays messages both ways.

**Data flow**: It receives the viewer’s WebSocket and requested path → rejects missing or foreign Origin headers → authorizes the session → refuses stored or shipped static sites → dials the sandbox → opens an upstream WebSocket with safe headers and offered subprotocols → accepts the viewer socket only after the upstream accepts → relays messages until one side ends.

**Call relations**: This is the catch-all WebSocket route registered by app. It relies on _same_origin, _authorized, _stored_manifest, _dial_site, _upstream_url, _upstream_headers, _refuse, _relay, and _end to keep socket behavior aligned with HTTP security.

*Call graph*: calls 9 internal fn (_authorized, _dial_site, _end, _refuse, _relay, _same_origin, _stored_manifest, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 905–917)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site host it is connecting to. This closes a browser gap where WebSockets are not protected by the usual cross-origin read rules.

**Data flow**: It receives the WebSocket handshake → reads the Origin header → compares the origin hostname with the target request hostname → returns true only when they match and Origin is present.

**Call relations**: _socket calls this before authorization and dialing. A false result is refused immediately so one hosted site cannot open a socket into another site using the victim site’s cookie.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 919–926)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Turns a SiteRefusal into a WebSocket handshake denial response. This gives socket clients the same status code and message an HTTP request would have received.

**Data flow**: It receives a WebSocket and a refusal object → creates an HTTP-style response with the refusal’s message, status, and media type → sends that as the denial instead of accepting the socket.

**Call relations**: _no_socket_view uses this for token-path socket attempts. _socket uses it for failed origin checks, failed authorization, static sites, missing sandboxes, and unreachable upstream sockets.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 928–945)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs the two halves of a WebSocket relay at the same time: viewer to site and site to viewer. When either side finishes, it stops the other side too.

**Data flow**: It receives the accepted viewer socket and upstream site socket → starts one task for each direction → waits until the first direction ends → cancels the other direction → re-raises any real failure from the completed side.

**Call relations**: _socket calls this after both WebSocket connections are open. It hands the actual message movement to _viewer_to_site and _site_to_viewer.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 947–956)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the sandbox without changing text frames into binary frames or vice versa. The frame type matters to many site protocols.

**Data flow**: It receives the viewer socket and upstream socket → repeatedly reads messages from the viewer → stops on viewer disconnect → sends text as text and bytes as bytes to the upstream site.

**Call relations**: _relay starts this as one of the two relay directions. It ends naturally when the viewer disconnects.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 958–971)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the sandbox back to the browser and then closes the browser socket with a suitable close code. It avoids sending close codes that the WebSocket standard reserves for local-only observations.

**Data flow**: It receives the upstream socket and viewer socket → forwards each upstream text or byte message to the viewer → reads the upstream close code and reason → substitutes a safe code when necessary → asks _end to close the viewer side.

**Call relations**: _relay starts this as the site-to-browser direction. It calls _end for the final close so shutdown errors do not hide the original end of the connection.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 973–983)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Closes the viewer WebSocket quietly. It is deliberately forgiving because the browser may already have gone away.

**Data flow**: It receives the viewer socket, close code, and reason → attempts to close the socket → suppresses any exception raised because the connection is already gone → returns nothing.

**Call relations**: _site_to_viewer calls this after the upstream site ends. _socket also calls it after relay failures to tell the viewer the upstream is gone when possible.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 986–997)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the base hostname used for all hosted site subdomains from configuration. It refuses to start the server if that value is missing or unusable.

**Data flow**: It receives the configured ingress public URL or None → parses out the hostname → returns the hostname when present → raises a startup error when no hostname can be found.

**Call relations**: run calls this during startup before creating IngressServe. Its result is later used by _site to decide whether a request hostname belongs to this ingress.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 1000–1010)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Builds the deploy-wide browser origin that is allowed to frame hosted sites. If no public app base URL is configured, it returns a rule meaning no one may frame them.

**Data flow**: It receives the configured public app URL or None → parses scheme, hostname, and optional port → returns an origin string like scheme://host:port, or the no-framing value when incomplete.

**Call relations**: run calls this at startup and stores the result on IngressServe. _frame_ancestors later uses it for every stored and proxied site response.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 1013–1036)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to talk to live sandbox servers. It is intentionally cookie-blind so cookies from one site are never stored and replayed to another.

**Data flow**: It creates timeout and connection-limit settings → creates a cookie jar whose policy accepts no domains → returns an AsyncClient configured with those limits and that empty, non-storing cookie jar.

**Call relations**: run calls this once during startup and passes the client into IngressServe. _proxy uses that client for all live HTTP forwarding.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 1039–1066)

```
def run() -> None
```

**Purpose**: Starts the ingress server process. It loads configuration, prepares logging, database access, carriers, blob storage, and then runs the web server.

**Data flow**: It reads configuration → initializes observability and database connectivity → verifies required ingress secrets and host settings → selects sandbox carriers and blob backend → creates an IngressServe instance → builds its FastAPI app → starts uvicorn on the configured ingress port with WebSocket size and compression settings.

**Call relations**: This is the file’s process entrypoint. It calls the small startup helpers ingress_base_host, ingress_frame_ancestor, and upstream_client, then hands IngressServe.app to uvicorn so incoming traffic can reach the route methods.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 14 external calls (__init__, run, blob_store_for, load_config, init_db, verify_db_reachable, load_manifests, init_o11y, log, owner_dsn (+4 more)).


### `core/src/ufo/sandbox/ingress_token.py`

`domain_logic` · `request handling`

A sandbox may expose a web page on a port, but the system cannot simply leave that port open to everyone. This file acts like a short-lived, tamper-proof visitor pass. The pass says which workspace, conversation, and port the visitor may reach, when the pass expires, and sometimes which enclosing frame or shipped app bundle is involved.

There are two different passes for two different steps. A “view” token is placed in the link that opens the sandbox view. A “session” token is later stored as that origin’s cookie. The token kind is included inside the signed content, so a session cookie cannot be reused as a view link, and a view link cannot be pasted in as a session cookie. This separation limits replay mistakes between the two browser hops.

The file also defines small data shapes for the claims inside a token. “Claims” are the facts the token asserts, such as the workspace ID and port number. Tokens are signed using one deploy-wide secret read from the environment. Verification rebuilds the claims, checks the signature, checks the expected kind, refuses invalid port numbers, and rejects expired tokens. Without this file, sandbox ingress would have no reliable way to know whether a browser request is allowed to reach a particular sandbox port.

#### Function details

##### `mint_ingress_token`  (lines 76–93)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: This function turns trusted ingress claims into a signed token string. Someone uses it when they need to create a browser link or session cookie that grants access to exactly one sandbox hop.

**Data flow**: It receives an IngressClaims object and a token kind, such as a view token or a session token. It converts the important fields into a JSON body, including optional shipped-bundle and frame information when present. It reads the shared ingress secret, signs that JSON body, and returns the signed token text that can be sent to the browser.

**Call relations**: When the system needs to issue an ingress pass, this function gathers the claims and asks ingress_secret for the shared secret. It then hands the prepared JSON bytes to sign_token, which produces the tamper-resistant token that verify_ingress_token will later be able to check.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 96–135)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: This function checks whether a token is real, still fresh, meant for the expected hop, and safe to use. If everything is valid, it returns the claims that say what sandbox access is allowed.

**Data flow**: It receives a token string, the current time, and the kind of token the caller expects. It reads the shared secret, verifies the signature, parses the JSON, checks that the token kind matches, rebuilds the workspace, conversation, port, shipped, and framer claims, and rejects malformed data. It also refuses ports outside the normal TCP port range and rejects tokens whose expiry time has passed. The output is an IngressClaims object, or it raises IngressTokenError if the token should not be trusted.

**Call relations**: During an ingress request, the caller uses this function before allowing access. It calls ingress_secret to get the same secret used at minting time, then uses verify_token to prove the token was not changed. After parsing, it builds ShippedClaim, FramerClaim, and IngressClaims objects so later code can make routing and security decisions from clean, typed data.

*Call graph*: calls 1 internal fn (ingress_secret); 8 external calls (__init__, __init__, __init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 138–145)

```
def ingress_secret() -> str
```

**Purpose**: This function reads the deploy-wide secret used to sign and verify ingress tokens. It makes missing configuration fail loudly instead of allowing the service to appear healthy but reject every real viewer later.

**Data flow**: It reads the environment variable named by UFO_TOKEN_SECRET_ENV. If the value exists, it returns that secret string. If it is missing or empty, it raises a RuntimeError explaining that the secret must be configured.

**Call relations**: Both mint_ingress_token and verify_ingress_token call this function so they use the same secret source. It is the small shared doorway between token logic and deployment configuration: minting needs it to sign new passes, and verification needs it to check existing passes.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).


### Operator memory surface
Exposes a restricted browser page for operators to inspect stored workspace memory records.

### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the web “front door” for the memory explorer. The memory system stores durable records for a workspace, including shared memories and member-specific ones. Operators need a safe way to look at those records, mainly for understanding, debugging, and auditing what the system may later recall. Without this surface, the memory store would still exist, but there would be no built-in browser page or JSON endpoint for an operator to inspect it.

The file does two simple jobs. First, it loads a static HTML page from `static/memory.html` and serves it to the browser. That page is the visual shell of the explorer. Second, it provides an API endpoint that returns every memory item for the currently selected workspace as JSON, ordered by the store helper it calls.

A key detail is access control. This surface relies on the shared operator session, meaning the caller must already be verified as an operator and bound to a workspace. Think of that binding like being given a visitor badge for one building: every read should stay inside that building. To read the extension’s own `memory_item` table, the file creates an `ExtensionContext`, which gives it a workspace-scoped database transaction for the memory extension rather than using some unrelated core storage path.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the browser. It is used when an operator opens the surface itself, before the page asks for memory data.

**Data flow**: It receives the surface context and the web request, but it does not need to inspect either one. It checks the HTML that was loaded when the file started; if the file was missing, it raises an error so the problem is obvious. If the HTML is available, it wraps that text in an HTML web response and sends it back.

**Call relations**: This is the page-serving half of the surface. The route table points the empty GET path to it, so the operator first receives the static explorer page. After that page loads in the browser, it can call the separate memory API provided by `memories`.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the workspace’s memory records as JSON for the explorer page. It is the read-only data feed behind the operator view.

**Data flow**: It receives the current surface context, including the workspace id that authorization has already selected. It builds an extension context for the memory extension so it can open the correct workspace-scoped transaction. It asks the store inventory helper for memory items in that workspace, turns each item into JSON-friendly data, and returns the list as a JSON web response.

**Call relations**: This is called when the explorer page requests `api/memories`. It relies on the earlier operator session binding to make sure the request is scoped to one workspace, then hands the actual database lookup to `ufo_ext_memory.store.inventory`. Once the store returns memory item models, this function converts them into the response format the browser can display.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).


### Hosted site doorway
Handles public and protected hosted-site visits, iframe redirects, share images, and privacy-preserving 404 responses.

### `extensions/sites/ufo_ext_sites/surface.py`

`orchestration` · `request handling`

A hosted site has a permanent link, but the link is only an address, not proof that the visitor is allowed in. This file is the gatekeeper for those links. When someone opens a site URL, it first checks the signed token in the URL to find the workspace and site name. Then it reads the site record, checks the visitor’s session cookie if needed, applies the site’s visibility rules, and finally creates a temporary ingress URL for the actual site bytes. The real site is shown inside an iframe, like putting the site behind a glass window: the frame can display it, but the site cannot easily steer the whole browser tab somewhere unsafe.

The file also supports special cases. If a site is an agent homepage, its visibility follows the agent instead of the site row. If a deploy-wide shipped app is opened, the visitor is sent through the portal when needed. Creators can change visibility through a form protected by a CSRF token, which is a signed value that proves the form came from this viewer’s own session.

Share previews are handled carefully. Public sites may expose their name and preview image to chat unfurlers. Non-public sites get generic metadata, so merely pasting a link does not leak private names or pictures.

#### Function details

##### `site_token`  (lines 174–183)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that names one hosted site. The token identifies the workspace, conversation, and site name, but it does not by itself grant permission to view the site.

**Data flow**: It receives a workspace ID, conversation ID, and site name. It puts those values into a signed surface token for the sites surface. It returns the token string that can be placed in a hosted site URL.

**Call relations**: This is the helper used by site_url when the system needs to publish or report a site’s permanent link. It hands off the actual signing work to the shared surface-token code.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 186–196)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the full public URL for a hosted site. It refuses to invent a link if the deployment has no public base URL configured, because such a link would not actually be openable.

**Data flow**: It receives the deployment’s public base URL plus the site’s workspace, conversation, and name. If the base URL is missing, it raises an explicit configuration error. Otherwise it creates a site token and appends it under the sites frame path, returning the full link.

**Call relations**: Callers use this when they need to hand a user the real hosted-site link. It relies on site_token to make the signed address part of that link.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `shipped_homepage_url`  (lines 208–223)

```
def shipped_homepage_url(public_base_url: str | None, workspace_id: UUID, slug: str, digest: str) -> str | None
```

**Purpose**: Builds a stable portal-embed URL for a deploy-wide shipped app bundle. This is for app code that belongs to the deployment, not for a user-created hosted site row.

**Data flow**: It receives a public base URL, workspace ID, app slug, and bundle digest. If there is no public base URL, it returns nothing. Otherwise it signs those values into a token marked as a portal embed and returns the frame URL that carries it.

**Call relations**: This is used by code that publishes a shipped app homepage address. Later, when that URL is opened, shipped_address and frame recognize the token shape and route it through the shipped-app path.

*Call graph*: 1 external calls (mint_surface_token).


##### `shipped_address`  (lines 226–240)

```
def shipped_address(token: str) -> ShippedAddress | None
```

**Purpose**: Checks whether a token is a valid shipped-app address and, if so, extracts the workspace, app slug, and bundle digest. It deliberately rejects normal site tokens so the two kinds of links cannot be confused.

**Data flow**: It receives a token string. It verifies the token signature and required claims, converts the workspace claim into a UUID, and returns a ShippedAddress object. If anything is missing, malformed, or not marked as a portal embed, it returns None.

**Call relations**: resolve_workspace uses this when deciding which workspace a request belongs to. frame uses it at the start of a request to decide whether to call _shipped_frame instead of the normal hosted-site flow.

*Call graph*: called by 2 (frame, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `site_card_url`  (lines 243–252)

```
def site_card_url(public_base_url: str | None, token: str, digest: str) -> str | None
```

**Purpose**: Builds the public URL for a site’s share-card image, the picture shown when a public site link is pasted into chat or social tools. It only returns a URL when the deployment has a public base address.

**Data flow**: It receives the public base URL, the site token, and the image digest. With no base URL it returns None. Otherwise it combines the base path, token, digest, and image extension into the anonymous share-card route.

**Call relations**: frame calls this while preparing page metadata. It is only used for sites that the caller has already decided are public and safe to name.

*Call graph*: called by 1 (frame).


##### `site_address`  (lines 255–274)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Checks whether a token is a valid hosted-site address and extracts the site identity from it. It accepts both normal permanent site links and portal-embed versions of those links.

**Data flow**: It receives a token string. It verifies the signed token, checks the optional portal-embed marker, converts the workspace and conversation IDs into UUIDs, and returns a SiteAddress object. If the token is invalid or missing required claims, it returns None.

**Call relations**: This is a central parser for site links. resolve_workspace uses it before reading any site row, frame uses it to open a site, homepage_embed_url uses it to convert normal links into portal-embed links, and _resolve uses it for helper routes.

*Call graph*: called by 4 (_resolve, frame, homepage_embed_url, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `homepage_embed_url`  (lines 277–292)

```
def homepage_embed_url(url: str) -> str
```

**Purpose**: Turns a normal hosted-site URL into a signed portal-iframe version of the same URL. This lets the portal open the site in its own embedded context while preserving the same site identity.

**Data flow**: It receives a URL, splits off the last path segment as the token, and verifies that the URL points at the hosted-site frame path. It reads the site address from the original token, then signs a new token with the portal-embed marker. It returns the same URL prefix with the new token.

**Call relations**: This function depends on site_address to prove the input is really a hosted-site URL. It then uses the shared token signer to mint the portal-specific form.

*Call graph*: calls 1 internal fn (site_address); 1 external calls (mint_surface_token).


##### `resolve_workspace`  (lines 295–305)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace a surface request belongs to before the app reads any database row. This matters because public visitors may not have a session cookie that would otherwise identify a workspace.

**Data flow**: It reads the token from the request path. It first tries to parse it as a normal site address, then as a shipped-app address. If either works, it returns the workspace ID. If neither works, it returns the same not-found response used for unknown sites.

**Call relations**: The surface routing layer calls this as the workspace-identification step. It delegates token understanding to site_address and shipped_address, and uses _not_found so invalid links do not reveal extra information.

*Call graph*: calls 3 internal fn (_not_found, shipped_address, site_address).


##### `frame`  (lines 308–384)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a hosted site link. It verifies the URL token, checks the site’s visibility rules, authenticates the viewer when needed, and then either renders a frame page, redirects a portal iframe to ingress, or returns a safe not-found response.

**Data flow**: It reads the site token and optional deep path from the request. If the token is for a shipped app, it hands off to _shipped_frame. Otherwise it parses the site address, reads the site row, prepares safe share-preview tags, checks the viewer from the session cookie, applies homepage or site visibility rules, asks the context for a temporary ingress URL, and returns HTML or a redirect. It may also create a CSRF token for the creator’s visibility form.

**Call relations**: This is the main GET handler declared in SITES_SURFACE. It calls many small helpers: _sites for storage, _viewer and _viewer_is_admin for access checks, _share_tags for metadata, _into_the_portal for portal-only pages, _framed_from for iframe context, _frame_page for final HTML, and _not_found whenever the request should reveal nothing.

*Call graph*: calls 18 internal fn (ingress_url, list_agents, _frame_page, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _page, _session_digest, _share_tags (+8 more)); 3 external calls (HTMLResponse, RedirectResponse, mint_surface_token).


##### `_shipped_frame`  (lines 387–426)

```
async def _shipped_frame(ctx: SurfaceContext, request: Request, shipped: ShippedAddress) -> Response
```

**Purpose**: Opens a deploy-wide shipped app bundle through the same frame route. It treats the bundle as public code and uses the ingress view token, not a hosted-site row, as the authority for the embedded request.

**Data flow**: It receives the already-parsed shipped address. If the request is not coming from the portal iframe, it looks up the matching agent and redirects the viewer to that agent’s portal page. If the request is inside the portal iframe, it builds the shipped app anchor and port, asks for an ingress URL that includes the shipped slug and digest, and redirects there. If ingress is unavailable, it returns an explanatory HTML page.

**Call relations**: frame calls this when shipped_address recognizes the token. It uses _is_portal_iframe_request to choose between portal redirect and iframe ingress, _into_the_portal to send users to the portal, and _unconfigured_page when the deployment cannot host the embedded app.

*Call graph*: calls 8 internal fn (ingress_url, list_agents, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _share_tags, _unconfigured_page); called by 1 (frame); 5 external calls (HTMLResponse, RedirectResponse, serve_port, shipped_anchor, shipped_app_slug).


##### `_is_portal_iframe_request`  (lines 429–430)

```
def _is_portal_iframe_request(request: Request) -> bool
```

**Purpose**: Detects whether the browser request is for an iframe. In this file, that is used as a signal that the portal is embedding the page rather than a person opening the link directly.

**Data flow**: It reads the Sec-Fetch-Dest request header. If the value says iframe, it returns true; otherwise it returns false.

**Call relations**: frame and _shipped_frame use this to decide whether to redirect into the portal or to proceed with iframe ingress. _framed_from uses it before trusting the referrer as the frame’s parent page.

*Call graph*: called by 3 (_framed_from, _shipped_frame, frame).


##### `_framed_from`  (lines 433–436)

```
def _framed_from(request: Request) -> str | None
```

**Purpose**: Returns the page that framed this request, but only when the request really appears to be for an iframe. This helps ingress know where the embedded content is being shown.

**Data flow**: It receives the request. If _is_portal_iframe_request says this is not an iframe request, it returns None. Otherwise it reads and returns the Referer header.

**Call relations**: frame and _shipped_frame pass this value into the context’s ingress_url call. It depends on _is_portal_iframe_request to avoid treating ordinary navigation referrers as frame parents.

*Call graph*: calls 1 internal fn (_is_portal_iframe_request); called by 2 (_shipped_frame, frame).


##### `_into_the_portal`  (lines 439–452)

```
def _into_the_portal(ctx: SurfaceContext, agent_id: UUID, share: str) -> Response
```

**Purpose**: Sends a visitor to the agent’s page inside the member portal when the app needs the portal bridge to work. If there is no portal installed, it returns a plain page explaining that.

**Data flow**: It receives the surface context, an agent ID, and share metadata. It asks the context for the portal home URL pointing at that agent. If a portal URL exists, it returns a redirect. If not, it builds an HTML page saying there is no portal.

**Call relations**: frame uses this for agent-homepage sites opened outside the proper portal iframe. _shipped_frame uses it for shipped app links opened cold. It relies on _page for the fallback HTML.

*Call graph*: calls 2 internal fn (home_url, _page); called by 2 (_shipped_frame, frame); 2 external calls (HTMLResponse, RedirectResponse).


##### `_unconfigured_page`  (lines 455–463)

```
def _unconfigured_page(title: str, share: str) -> str
```

**Purpose**: Builds the HTML shown when the deployment has no ingress origin configured for hosted content. Instead of showing an empty or broken iframe, it tells the viewer that site hosting is not configured.

**Data flow**: It receives a title and share metadata. It escapes the title for safe HTML, combines the base styles with frame styles, and returns a complete page containing the unconfigured-hosting message.

**Call relations**: frame uses this when a normal or homepage site cannot get an ingress URL. _shipped_frame uses it for shipped apps in the same situation. It delegates the common document wrapper to _page.

*Call graph*: calls 1 internal fn (_page); called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `share_card`  (lines 466–505)

```
async def share_card(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the preview image for a public site link. This route is intentionally anonymous because chat and social link preview crawlers do not carry a user session.

**Data flow**: It resolves the site from the token in the URL. It refuses the request unless the site exists, is not bound as an agent homepage, is currently public, has a stored card image, and the URL digest matches the site’s current card hash. If all checks pass, it reads the image bytes from blob storage and returns them with image headers and a short public cache time.

**Call relations**: SITES_SURFACE exposes this as a GET route before the deep-link route so it is not swallowed as a site path. It uses _resolve to find the row and _not_found for every refusal, making invalid, private, and unknown cards look the same.

*Call graph*: calls 2 internal fn (_not_found, _resolve); 1 external calls (Response).


##### `set_visibility`  (lines 508–529)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets a site creator change a site between private, workspace-visible, and public. It blocks everyone else with the same not-found response used for unknown sites, and it rejects forged form submissions with a CSRF check.

**Data flow**: It resolves the site, identifies the viewer from the session cookie, and confirms the viewer is the creator. It rejects homepage-bound sites because their visibility follows the agent. It reads the submitted form, checks the CSRF token against this session, validates the requested visibility level, writes the new level to storage, and redirects back to the site frame.

**Call relations**: SITES_SURFACE exposes this as the POST route under a site token. It works with _resolve, _viewer, _csrf_holds, and _sites, and it returns plain text errors for bad CSRF, invalid levels, or homepage-bound sites.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 532–536)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Looks up a hosted site row from the token in a request. It is a small shared helper for routes that need the site but do not need the full frame-opening flow.

**Data flow**: It reads the token path parameter, parses it with site_address, and returns None if the token is invalid. If the token is valid, it uses the hosted-sites store to read the row by conversation ID and site name, returning the site or None.

**Call relations**: share_card and set_visibility call this at the start of their work. It uses _sites to create the storage access object for the current workspace.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (set_visibility, share_card).


##### `_sites`  (lines 539–540)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the storage helper for hosted sites in the current workspace. It is the file’s shortcut for reading or updating hosted-site rows.

**Data flow**: It receives the surface context, takes the workspace ID and transaction provider from it, and returns a HostedSites store object.

**Call relations**: frame uses this to read a site row. _resolve uses it for shared lookup. set_visibility uses it to write a new visibility level.

*Call graph*: called by 3 (_resolve, frame, set_visibility); 1 external calls (__init__).


##### `_viewer_is_admin`  (lines 543–548)

```
async def _viewer_is_admin(ctx: SurfaceContext, viewer: UUID | None) -> bool
```

**Purpose**: Checks whether the current viewer is a workspace admin. Admins are allowed to view private sites or private agent homepages even when they are not the creator or owner.

**Data flow**: It receives the context and a possible viewer ID. If there is no viewer, it returns false. Otherwise it opens a transaction, reads the workspace seat snapshot, and returns true only if the viewer appears as an admin member.

**Call relations**: frame calls this during access checks for private sites and private agent homepages. It uses the Seats subsystem to answer the workspace-membership question.

*Call graph*: calls 1 internal fn (transaction); called by 1 (frame); 1 external calls (__init__).


##### `_viewer`  (lines 551–561)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace member represented by the request’s session cookie. If the cookie is missing, invalid, or for another workspace, it returns no viewer.

**Data flow**: It reads the ufo_session cookie from the request. It verifies the bearer token against the current workspace and gets an email address if valid. It then finds an already linked member for that email, or creates the link on first use, and returns the member ID.

**Call relations**: frame uses this to decide whether a visitor can pass the visibility gate. set_visibility uses it to prove the poster is the site creator. It relies on shared bearer-token verification and context member-linking methods.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 564–566)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility form token matches the viewer’s current session. This prevents another website from silently submitting a visibility change on the creator’s behalf.

**Data flow**: It receives the request and the submitted CSRF token. It verifies the token signature, computes the digest of the current session cookie, and returns true only when the token’s stored digest matches this request’s session digest.

**Call relations**: set_visibility calls this before accepting a form submission. It uses _session_digest for the value that ties the CSRF token to this browser session.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 569–573)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a safe fingerprint of the current session cookie for CSRF protection. It uses a hash so the actual cookie value does not need to be placed in the form token.

**Data flow**: It reads the ufo_session cookie from the request, or an empty string if missing. It hashes that text with SHA-256 and returns the hexadecimal digest.

**Call relations**: frame uses this when minting a CSRF token for the creator’s visibility selector. _csrf_holds uses it later to check that a submitted token belongs to the same session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 576–577)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard not-found response for this surface. The same response is used for unknown sites, invalid tokens, and unauthorized private resources so the page does not reveal which case happened.

**Data flow**: It takes no input. It returns a plain-text HTTP response with the body 'no such site' and status code 404.

**Call relations**: resolve_workspace, frame, _shipped_frame, share_card, and set_visibility all use this as the safe refusal response.

*Call graph*: called by 5 (_shipped_frame, frame, resolve_workspace, set_visibility, share_card); 1 external calls (PlainTextResponse).


##### `_page`  (lines 580–585)

```
def _page(title: str, style: str, body: str, share: str) -> str
```

**Purpose**: Builds a complete minimal HTML page from a title, CSS, body markup, and share-preview tags. It is the common wrapper for all small pages served by this surface.

**Data flow**: It receives the page title, style text, body HTML, and metadata tags. It combines them with a doctype, charset, viewport tag, title tag, and style block, then returns the HTML string.

**Call relations**: _frame_page uses this for normal framed site pages. _into_the_portal and _unconfigured_page use it for fallback pages. frame uses it directly for the not-signed-in page.

*Call graph*: called by 4 (_frame_page, _into_the_portal, _unconfigured_page, frame).


##### `_share_tags`  (lines 588–622)

```
def _share_tags(name: str | None, canonical: str | None, card: str | None) -> str
```

**Purpose**: Builds the metadata that link preview tools read, such as title, description, image, and image size. It only names the actual site when the caller has already decided the site is public.

**Data flow**: It receives an optional site name, optional canonical URL, and optional site-specific card URL. It escapes all values for safe HTML, chooses either the site title and card or the generic UFO title and card, and returns Open Graph and Twitter meta tags.

**Call relations**: frame calls this while rendering hosted-site pages, choosing site-specific values only for public non-homepage sites. _shipped_frame calls it with generic values for shipped apps.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `_frame_page`  (lines 625–664)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str, share: str) -> str
```

**Purpose**: Builds the visible hosted-site frame page. It shows the site name, either a creator visibility selector or a read-only badge, and the iframe that loads the actual hosted site from its temporary ingress URL.

**Data flow**: It receives the site row, optional embedded ingress URL, the stable frame path, an optional CSRF token, and share metadata. If a CSRF token is present, it builds the editable visibility selector; otherwise it shows a badge. If an ingress URL exists, it creates a sandboxed iframe; otherwise it shows the unconfigured-hosting message. It wraps everything with _page and returns the HTML.

**Call relations**: frame calls this after the visitor has passed the site visibility gate. _frame_page calls _selector when the viewer is the creator and uses _page for the final document.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 667–677)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the creator’s visibility form for a site. The form lets the creator choose who can open the permanent site link.

**Data flow**: It receives the current visibility level, the frame path to post back to, and the CSRF token. It creates one option for each visibility label, marks the current one selected, includes the CSRF hidden field, and returns the form HTML.

**Call relations**: _frame_page calls this only when frame has minted a CSRF token, which only happens for the site creator. The form posts to the set_visibility route.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).


### Web portal surfaces
Provides the user-facing UFO web portal along with community skill directory browsing and retrieval endpoints.

### `extensions/web/ufo_ext_web/community.py`

`io_transport` · `request handling`

The Community tab needs two things from the outside skills directory: a short list of skills to show, and the full SKILL.md document for one selected skill. This file is the bridge to that outside website. If the user has not typed a search, it reads the skills.sh homepage payload and extracts the public leaderboard. If the user has typed a search, it calls the public search API. Each result is reduced to the few facts the list can honestly show: skill name, source repository, and install count. Descriptions are not fetched for every row because the directory only publishes them on each skill page, and those reads are rate limited. To avoid hammering the directory, listings are cached for 15 minutes, and fetched documents are remembered in memory. Think of it like a librarian keeping a short stack of recently requested catalog cards on the desk. The file also validates shapes carefully: if the directory returns bad JSON, an oversized response, a rate limit, or a format this app does not understand, it raises CommunityUnavailable with a sentence suitable for the user-facing toast.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: Turns an HTTP failure code from the skills directory into a clear CommunityUnavailable error. It gives a special message for rate limiting so the user understands why the directory cannot be read right now.

**Data flow**: It receives a numeric status code from an HTTP response. If the code means “too many requests,” it builds a friendly explanation about the 60-reads-an-hour limit; otherwise it reports the exact code. The result is an exception object that callers raise instead of returning an empty or misleading answer.

**Call relations**: CommunitySkills._search and CommunitySkills._body call this when skills.sh answers with something other than success. It hands back the error object they raise, so the higher-level route can show a refusal message rather than pretending there are no community skills.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: Returns the community skill list for the Skills tab, either as a popular leaderboard or as search results. It uses a short-lived cache so reopening the same view does not keep rereading the outside directory.

**Data flow**: It receives a search query string. First it checks whether that exact query already has a fresh cached list; if so, it returns it. If not, it opens an HTTP client, asks _popular for the leaderboard when the query is empty or _search when it is not, cuts the answer down to the display limit, stores it with the current time, and returns the list of CommunitySkill objects.

**Call relations**: This is the main public listing method other web code calls when the Community narrowing needs rows. Inside, it delegates network setup to _client and chooses between _popular and _search depending on whether the user typed a query.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: Fetches the full document for one community skill so the install screen can show its description and instructions. It remembers both found and not-found results to avoid repeating the same directory read.

**Data flow**: It receives a source repository like owner/repo and a skill name. It combines them into a cache key and returns the cached CommunityDocument, or cached None, if already known. Otherwise it opens an HTTP client, downloads the directory’s package data, reads the JSON list of files, looks for SKILL.md, parses that file’s front matter and body, stores the result in the document cache, and returns either the parsed document or None.

**Call relations**: This is the public one-skill read used after a user chooses a listed skill. It relies on _client to create the HTTP session, _body to safely download the response, and _parse to turn SKILL.md text into the structured CommunityDocument shown by the install flow.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: Creates the asynchronous HTTP client used for talking to skills.sh. The client follows redirects and can use an injected transport in tests, which lets tests fake network replies without reaching the real internet.

**Data flow**: It receives a timeout value. It combines that timeout with the CommunitySkills transport setting and returns a ready-to-use httpx AsyncClient. It does not itself send any requests.

**Call relations**: CommunitySkills.listing and CommunitySkills.fetch call this right before they need network access. The returned client is then passed to _popular, _search, or _body so those helpers can perform the actual reads.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: Reads the public leaderboard from the skills.sh homepage payload when the user has not searched for anything. It extracts valid skill entries and sorts them by install count.

**Data flow**: It receives an HTTP client. It downloads the homepage payload through _body, scans the text for small JSON-like entries that contain a skillId, decodes each entry, converts valid ones with _entry, removes duplicates by source and name, and returns the resulting skills sorted from most installed to least installed. If nothing usable is found, it raises CommunityUnavailable.

**Call relations**: CommunitySkills.listing calls this for the default, no-query Community view. This helper depends on _body for the guarded download and _entry for the shared cleanup and validation of each skill record.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: Calls the skills.sh search API for a user’s query and returns matching skills in install-count order. It is the search-path counterpart to the leaderboard reader.

**Data flow**: It receives an HTTP client and the query text. It sends the query and result limit to the search endpoint, checks for a successful response, turns each returned skill-like object into a CommunitySkill with _entry, drops invalid entries, sorts the rest by installs, and returns the list.

**Call relations**: CommunitySkills.listing calls this when the user has typed a search term. If the API refuses the request, it asks _refusal to create the right user-facing error; otherwise it uses _entry so search results and leaderboard results are normalized the same way.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: Turns one raw directory entry into the small CommunitySkill object the UI list can use. It filters out entries that do not have a usable name or a safe-looking source repository.

**Data flow**: It receives an unknown object from parsed JSON. If the object is not a dictionary, or if the name/source fields are missing or malformed, it returns None. Otherwise it reads the skill name, source, and install count, fills in zero installs when absent, and returns a CommunitySkill.

**Call relations**: Both _popular and _search use this as their common gatekeeper. That means the two directory sources can have slightly different field names, but the rest of the app still receives one consistent shape.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: Downloads a response body safely, with status-code checks and a maximum size limit. This prevents the app from treating failures as data and protects it from unexpectedly huge replies.

**Data flow**: It receives an HTTP client, a URL, a byte-size cap, and optional request headers. It streams the response in chunks, rejects non-success status codes through _refusal, counts bytes as they arrive, raises CommunityUnavailable if the response grows beyond the allowed cap, and finally returns the collected bytes.

**Call relations**: CommunitySkills._popular uses this to read the leaderboard payload, and CommunitySkills.fetch uses it to read the downloadable skill package. When the remote server refuses or sends too much data, this helper turns that problem into a clear CommunityUnavailable error before parsing begins.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: Reads a SKILL.md document and extracts the parts the install review needs: name, description, instructions, and the original document text. It only accepts documents with valid front matter, which is the metadata block at the top of the Markdown file.

**Data flow**: It receives the raw SKILL.md text. It checks for a front matter block surrounded by --- lines, parses that block as YAML, requires a name and description, trims the remaining Markdown body into instructions, and returns a CommunityDocument. If any required part is missing or unreadable, it returns None.

**Call relations**: CommunitySkills.fetch calls this after it finds SKILL.md in the downloaded package. _parse is the final step that turns a raw text file from the directory into the structured document the install screen can display.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).


### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling, live streaming, and scheduled background jobs`

This is the web doorway into the system. It turns normal browser requests into safe, workspace-scoped actions against UFO agents and workspace data. Without it, the browser portal would have no trusted way to sign a member in, show their agents, send messages, upload files, stream a running answer, or read panels like memory, credentials, usage, conversations, and settings.

The file works like a reception desk with many counters. First, it verifies the session cookie and connects the email in that cookie to a workspace member. Then each route checks what that member is allowed to see or do. Chat routes open or continue conversations, save attachments into the conversation workspace, admit the message into the shared turn queue, and return the turn to follow. Streaming routes use server-sent events, a browser-friendly live feed, to deliver text, activity, files, prompts, and final results as an agent works.

It also serves the built web app and its static assets, including support for rolling deployments where different server pods may serve different frontend builds. Read routes project core data into shapes the portal can draw: agent lists, transcripts, subagent work trees, object indexes, setup states, shared artifacts, connection prompts, and spend reports. The important pattern is that the web layer does not invent authority. It asks the privileged SurfaceContext for every real read or write, and adds browser-specific packaging around it.

#### Function details

##### `load_assets`  (lines 251–261)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Reads the built portal asset files, such as JavaScript and CSS, into memory so the web server can serve them quickly. It only includes file types the server explicitly knows how to label.

**Data flow**: It receives a directory path, scans its files, keeps files with approved suffixes, reads their bytes, and returns a lookup from request name to bytes and media type.

**Call relations**: It runs during module setup and feeds the static asset tables that later requests use through _static_response and static_asset.

*Call graph*: 1 external calls (glob).


##### `rum_config`  (lines 274–287)

```
def rum_config(environ: Mapping[str, str]) -> dict[str, str] | None
```

**Purpose**: Builds the browser monitoring configuration for Datadog RUM, which records frontend sessions when enabled. It prevents half-configured monitoring from silently sending data nowhere or to the wrong place.

**Data flow**: It reads environment variables, trims them, returns None if none are set, returns a complete config if all are set, or raises an error if only some are set.

**Call relations**: portal_page calls it just before serving the portal shell so each deployment can inject its own monitoring settings.

*Call graph*: called by 1 (portal_page).


##### `portal_shell`  (lines 290–299)

```
def portal_shell(html: str, config: Mapping[str, str] | None) -> str
```

**Purpose**: Inserts the monitoring configuration into the built HTML shell that the browser loads first. It also checks that the expected placeholder exists, so a broken frontend build is caught loudly.

**Data flow**: It takes HTML and an optional config, converts the config to safe JSON, replaces one special script block, and returns the final HTML string.

**Call relations**: portal_page calls it after reading rum_config, then sends the result as the main portal page.

*Call graph*: called by 1 (portal_page); 1 external calls (dumps).


##### `resolve_workspace`  (lines 312–351)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace a web request belongs to before the route handler runs. It reads the signed bearer token from the session cookie, or from the one login POST that creates the cookie.

**Data flow**: It reads cookies, method, headers, form body when allowed, and query parameters; it returns a workspace ID, a redirect/response, or None for rejection.

**Call relations**: The shared surface router calls this as the first gate; it uses _framed_length, _form, and _chat_target to safely inspect only the login form when needed.

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 354–358)

```
def _chat_target(request: Request) -> UUID | None
```

**Purpose**: Safely extracts a conversation ID from the login redirect query string. It exists so only a real UUID can be carried through sign-in.

**Data flow**: It reads the c query parameter, tries to parse it as a UUID, and returns the UUID or None.

**Call relations**: resolve_workspace uses it when redirecting an unauthenticated portal visit to the login page.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 361–371)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Looks up one built frontend asset requested by the browser and prepares a response if this server build has it. It avoids touching the filesystem per request.

**Data flow**: It strips the static URL prefix, searches the in-memory asset table, and either returns None or passes the bytes to _asset_response.

**Call relations**: static_asset tries this first, then falls back to _stored_asset for assets from another build.

*Call graph*: calls 1 internal fn (_asset_response); called by 1 (static_asset).


##### `_asset_response`  (lines 374–378)

```
def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response
```

**Purpose**: Returns a static file with caching headers. It can answer “not modified” when the browser already has the current content.

**Data flow**: It receives request headers, bytes, media type, and an ETag hash; it returns either a 304 response or the full file response.

**Call relations**: _static_response and _stored_asset both use it so local and stored assets behave the same way.

*Call graph*: called by 2 (_static_response, _stored_asset); 1 external calls (Response).


##### `load_apps`  (lines 404–437)

```
def load_apps(directory: Path) -> AppsBundle | None
```

**Purpose**: Reads the shipped app pages that are bundled with the web extension. These are deploy-wide app homepages served to workspaces that have not forked their own page.

**Data flow**: It receives a directory, recursively reads non-hidden files, computes a content digest, discovers top-level app slugs, and returns an AppsBundle or None.

**Call relations**: It runs at import time to populate APPS, and later apps, _publish_assets, and homepage-related functions depend on that bundle.

*Call graph*: 4 external calls (__init__, sha256, is_dir, rglob).


##### `apps`  (lines 443–449)

```
def apps() -> AppsBundle
```

**Purpose**: Returns the loaded shipped app bundle or raises a clear build error. It prevents routes from pretending an app page exists when the frontend build was skipped.

**Data flow**: It reads the module-level APPS value and either returns it or raises RuntimeError with the build command to run.

**Call relations**: agents_index, homepage, and _homepage_state call it when they need shipped app page data.

*Call graph*: called by 3 (_homepage_state, agents_index, homepage).


##### `_publish_assets`  (lines 452–462)

```
async def _publish_assets(blob: BlobStore, apps: AppsBundle) -> None
```

**Purpose**: Copies this server’s built portal and app assets into the shared blob store. This lets another server pod serve files referenced by a page created on this pod.

**Data flow**: It receives a blob store and app bundle, checks which keys already exist, writes missing static files and app files, and returns nothing.

**Call relations**: _assets_published wraps it in a single reusable task so portal_page, agents_index, and homepage can wait for publishing before handing out links.

*Call graph*: calls 3 internal fn (exists, list, put); called by 1 (_assets_published).


##### `_assets_published`  (lines 465–492)

```
def _assets_published(blob: BlobStore, apps: AppsBundle) -> 'asyncio.Task[None]'
```

**Purpose**: Starts or reuses the one asset-publishing task for this process. It retries after failure instead of letting future pages reference missing files.

**Data flow**: It reads the global publishing task, creates a new asyncio task when none exists or the old one failed, stores it globally, and returns the task.

**Call relations**: portal_page, agents_index, and homepage call it before serving shells or homepage links that may name those assets.

*Call graph*: calls 1 internal fn (_publish_assets); called by 3 (agents_index, homepage, portal_page); 1 external calls (create_task).


##### `_stored_asset`  (lines 495–517)

```
async def _stored_asset(blob: BlobStore, request: Request) -> Response
```

**Purpose**: Serves an asset from the shared blob store when the current server build does not have it locally. This is important during rolling deploys.

**Data flow**: It validates the requested asset name and suffix, checks a small in-memory cache, reads bytes from the blob store if needed, caches them, and returns a static response or 404.

**Call relations**: static_asset calls it after _static_response misses, and it uses _asset_response to match local asset behavior.

*Call graph*: calls 3 internal fn (exists, get, _asset_response); called by 1 (static_asset); 3 external calls (sha256, Path, Response).


##### `portal_page`  (lines 520–537)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main browser portal page to an authenticated request. It refuses to serve if the frontend build is missing.

**Data flow**: It checks built HTML and apps exist, waits for assets to publish, injects RUM config into the shell, and returns no-store HTML.

**Call relations**: This is the GET route for the web surface root; resolve_workspace has already scoped the request before it runs.

*Call graph*: calls 3 internal fn (_assets_published, portal_shell, rum_config); 1 external calls (HTMLResponse).


##### `_authenticate`  (lines 540–558)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

**Purpose**: Verifies the session cookie and resolves it to a workspace member. If the email has never spoken before, it links or creates the member record.

**Data flow**: It reads the session cookie, verifies the token for the workspace, links the email to a member if possible, and returns either member ID plus email or a 401 response.

**Call relations**: _audience_for uses it for most web API routes, and fulfill_credential uses it directly for a credential handoff.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (_audience_for, fulfill_credential); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 561–567)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves frontend assets to authenticated portal users. It first tries this build’s files and then shared storage for files from another build.

**Data flow**: It receives context and request, calls _static_response, and if that misses calls _stored_asset using the fleet blob store.

**Call relations**: It is the route behind /static requests from the portal shell.

*Call graph*: calls 2 internal fn (_static_response, _stored_asset).


##### `open_session`  (lines 570–601)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts the signed login token from a form POST, stores it as the session cookie, and redirects into the portal. This keeps the bearer token out of URLs.

**Data flow**: It bounds and parses the form, validates that the token field exists and has a safe shape, writes the session cookie, and returns a redirect.

**Call relations**: resolve_workspace may have already used the same form token to scope the request; later routes re-check the cookie through _authenticate.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 604–608)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Reads an agent ID from the URL path and validates that it is a UUID. It avoids repeating path parsing in every agent route.

**Data flow**: It takes a request, parses request.path_params['agent_id'], and returns the UUID or None.

**Call relations**: chat, transcript, _panel_gate, and _member_chat_page use it before checking audience permissions.

*Call graph*: called by 4 (_member_chat_page, _panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 611–612)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage key for the web-specific record attached to a conversation. That record says which agent and email own the web chat.

**Data flow**: It receives a conversation UUID and returns a string key under the chat storage prefix.

**Call relations**: _open_conversation writes this key, and _own_web_chat reads it to prove a conversation is this member’s web chat.

*Call graph*: called by 2 (_open_conversation, _own_web_chat).


##### `_chat_title`  (lines 621–639)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates a short title for a new conversation from the first message or attached filenames. The title is trimmed to look good in the chat rail.

**Data flow**: It receives message text and attachment paths, collapses whitespace, falls back to filenames if needed, cuts at a sensible word boundary, and returns a title string.

**Call relations**: _open_conversation uses it for newly opened chats, and summarize_chat_titles also uses it to clean model-generated titles.

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_title_excerpt`  (lines 651–669)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

**Purpose**: Builds the small opening excerpt used to ask the model for a better conversation title. It uses the first user and assistant texts only.

**Data flow**: It receives transcript messages, extracts rendered text from the first user and assistant messages, removes web context from the user side, and returns joined text or an empty string.

**Call relations**: summarize_chat_titles calls it for each conversation waiting for a summary title.

*Call graph*: calls 1 internal fn (_rendered_text); called by 1 (summarize_chat_titles); 1 external calls (member_message_text).


##### `summarize_chat_titles`  (lines 672–718)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

**Purpose**: Background job that asks the model to write short titles for conversations that do not yet have one. This improves the chat rail without blocking live conversation.

**Data flow**: It reads candidate conversation IDs, loads their transcripts, makes an excerpt, asks the model for a title when possible, and records the summary result.

**Call relations**: It is a scheduled maintenance flow; it relies on _title_excerpt and _chat_title, then writes back through the extension context.

*Call graph*: calls 4 internal fn (conversations_awaiting_title, summarized_conversation_title, _chat_title, _title_excerpt); 2 external calls (__init__, __init__).


##### `seed_homepages`  (lines 721–793)

```
async def seed_homepages(ctx: ExtensionContext, bucket: str | None=None) -> None
```

**Purpose**: Background job that starts one homepage-building turn for each eligible agent that has never been seeded. This gives agents an initial Home page without requiring a member to ask manually.

**Data flow**: It reads workspace agents and existing seed markers, skips archived or shipped apps, chooses an acting owner/admin, opens a conversation, invokes the homepage prompt, and records a marker when accepted.

**Call relations**: It runs as scheduled work and uses core conversation and invocation APIs rather than web request handlers.

*Call graph*: calls 5 internal fn (earliest_seated_admin, invoke, open_conversation, turn_outcomes, workspace_agents); 2 external calls (now, shipped_app_slug).


##### `_open_conversation`  (lines 796–829)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and stores the web ownership row that later gates access. It is careful about races so duplicate first sends join the same conversation safely.

**Data flow**: It receives agent, member, email, queue key, message text, and file paths; it writes a provisional chat row, opens or finds the durable conversation, fixes race leftovers, titles the conversation, and returns ID plus title.

**Call relations**: chat calls it when the browser posts to the special new conversation target.

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_web_chat); called by 1 (chat); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 832–839)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

**Purpose**: Reads the official title of one conversation from the same listing used elsewhere. This keeps all screens naming the conversation the same way.

**Data flow**: It receives context, agent, member, and conversation IDs, asks for that one listed conversation, and returns its title or an empty string.

**Call relations**: _open_conversation uses it after losing an open race to the existing conversation.

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 1 (_open_conversation).


##### `_own_web_chat`  (lines 842–854)

```
async def _own_web_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is the current member’s own web chat with the selected agent. Anything else is treated as not found.

**Data flow**: It reads the chat row from scoped storage, validates it as ChatRecord, compares agent ID and email, and returns the record or None.

**Call relations**: _member_chat uses it as the first proof of web-chat ownership, and _open_conversation uses it after a race.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 2 (_member_chat, _open_conversation).


##### `_member_chat`  (lines 857–902)

```
async def _member_chat(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, *, agent_visible: bool) -> ListedConversation | None
```

**Purpose**: Decides whether this member may continue or read a conversation through the portal chat path. It covers ordinary web chats, private extension rooms, and commentable Slack or terminal conversations.

**Data flow**: It receives identity, agent, conversation, and visibility facts; it reads the conversation listing and chat row, applies the allowed shapes, and returns the listed conversation or None.

**Call relations**: chat, transcript, _resolve_chat, _member_turn, and _member_chat_page all depend on this shared gate.

*Call graph*: calls 3 internal fn (list_agent_conversations, _commentable, _own_web_chat); called by 5 (_member_chat_page, _member_turn, _resolve_chat, chat, transcript); 1 external calls (conversation_audience).


##### `_commentable`  (lines 905–909)

```
def _commentable(conversation: ListedConversation, member_id: UUID) -> bool
```

**Purpose**: Checks whether a listed conversation can accept a portal comment from this member. Only certain surfaces and audiences are eligible.

**Data flow**: It reads the conversation surface and audience, compares them with shared or member-specific audience values, and returns true or false.

**Call relations**: _member_chat, chat, _resolve_chat, _member_turn, and _conversation_row use it to label or allow comments consistently.

*Call graph*: called by 5 (_conversation_row, _member_chat, _member_turn, _resolve_chat, chat); 1 external calls (conversation_audience).


##### `_turn_context`  (lines 912–923)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

**Purpose**: Builds the extra context attached to a submitted turn: who sent it, where it came from, and the browser’s timezone if valid.

**Data flow**: It reads the timezone header, tries to build a TurnContext, logs and drops an invalid timezone, and returns the context.

**Call relations**: chat uses it when admitting a member message into the durable queue.

*Call graph*: called by 1 (chat); 2 external calls (__init__, log).


##### `_chat_url`  (lines 926–929)

```
def _chat_url(public_base_url: str | None, conversation_id: UUID) -> str | None
```

**Purpose**: Builds a public portal link to a conversation when the deployment has a public base URL. It returns None when no public URL is configured.

**Data flow**: It receives the base URL and conversation ID, joins them into a fragment route, and returns the URL or None.

**Call relations**: _chat_source and _comment_notice use it to include useful links in turn context and comments.

*Call graph*: called by 2 (_chat_source, _comment_notice).


##### `_chat_source`  (lines 932–941)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Creates the human-readable source label for a portal message. It tells downstream work where the message came from and who sent it.

**Data flow**: It receives public base URL, conversation ID, and email, builds a chat URL when possible, and returns a source string.

**Call relations**: chat passes this into _turn_context before admitting a message.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (chat).


##### `_comment_notice`  (lines 944–964)

```
def _comment_notice(public_base_url: str | None, conversation: ListedConversation, member_id: UUID, email: str, text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Formats a portal comment so it can be delivered into a Slack or terminal conversation. It names the speaker, link, text, and attachments.

**Data flow**: It receives conversation and sender details, chooses an author label, builds a chat link when available, appends attachment names, and returns markdown-like text.

**Call relations**: chat calls it when the target conversation is commentable rather than a normal web chat.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (chat); 2 external calls (PurePosixPath, conversation_audience).


##### `_audience_for`  (lines 967–974)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Authenticates the request and computes the member’s web audience, meaning the agents and admin powers they can reach. This is the common front door for most API routes.

**Data flow**: It calls _authenticate, then asks the web audience module for permissions tied to the member email, and returns member ID, email, and audience or a response.

**Call relations**: Most route handlers call it directly or through _panel_gate and _object_gate before doing any real work.

*Call graph*: calls 1 internal fn (_authenticate); called by 27 (_member_chat_page, _member_turn, _object_gate, _panel_gate, action_views, admin_index, agents_index, agents_status, chat, chats_index (+15 more)); 2 external calls (web_audience, web_extension).


##### `_visibility_flag`  (lines 995–999)

```
def _visibility_flag(agent: AgentSummary) -> str | None
```

**Purpose**: Finds the feature flag that controls whether an agent should be hidden in portal lists. Some built-in app agents are gated by product flags.

**Data flow**: It receives an AgentSummary and returns the main-agent flag, an app-specific flag, or None.

**Call relations**: _flag_reads gathers these flags, and agents_index uses the answers to mark hidden agents.

*Call graph*: called by 2 (_flag_reads, agents_index); 1 external calls (shipped_app_slug).


##### `_flag_reads`  (lines 1002–1022)

```
async def _flag_reads(agents: tuple[AgentSummary, ...]) -> dict[str, bool]
```

**Purpose**: Reads all feature flags needed for the portal boot response in one batch. Defaults are chosen so existing screens stay available during flag outages.

**Data flow**: It receives agents, builds a deduplicated list of portal and agent flags, reads them concurrently, and returns a map of flag key to boolean.

**Call relations**: agents_index calls it before returning surfaces and hidden-agent information.

*Call graph*: calls 1 internal fn (_visibility_flag); called by 1 (agents_index); 2 external calls (gather, flag_enabled).


##### `_setup_ready`  (lines 1025–1035)

```
def _setup_ready(state: SetupState) -> bool
```

**Purpose**: Answers whether an app’s setup is complete. Setup is complete only when all declared connectors, credentials, and standing orders are settled.

**Data flow**: It receives a SetupState, checks every connector, credential, and standing order, and returns true or false.

**Call relations**: agents_index uses it to mark setup_due, and workspace_starters uses it when deciding starter offers for installed apps.

*Call graph*: called by 2 (agents_index, workspace_starters).


##### `agents_index`  (lines 1038–1159)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Builds the portal’s initial boot payload: signed-in member, visible agents, homepages, setup state, archived apps, and enabled portal surfaces. This is the first API read the browser needs.

**Data flow**: It authenticates the member, reads web audience, publishes assets, reads homepages and setup states, reads flags and archived agents, and returns a JSON structure for the portal shell.

**Call relations**: The browser calls this after loading the portal; it fans out to helpers like _bound_page, _homepage_state, _flag_reads, and _setup_ready.

*Call graph*: calls 9 internal fn (list_archived_agents, _assets_published, _audience_for, _bound_page, _flag_reads, _homepage_state, _setup_ready, _visibility_flag, apps); 6 external calls (Semaphore, gather, JSONResponse, shipped_app_slug, granted_emails, web_extension).


##### `agents_index.setup_of`  (lines 1088–1090)

```
async def setup_of(agent: AgentSummary) -> SetupState
```

**Purpose**: Reads setup state for one provisioned agent while respecting a concurrency limit. It keeps the boot request from opening too many backend reads at once.

**Data flow**: It receives an agent from the outer function, waits for the semaphore, calls ctx.agent_setup, and returns the SetupState.

**Call relations**: agents_index creates this inner helper and runs many copies with asyncio.gather.


##### `agents_status`  (lines 1162–1203)

```
async def agents_status(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the live status of each agent the member can see, such as whether a turn is running and recent activity text. The portal polls it to keep the sidebar fresh.

**Data flow**: It authenticates, asks core for per-agent turn status, peeks latest activity for running turns, and returns JSON rows with timestamps and failure flags.

**Call relations**: It is a lightweight polling route beside agents_index and uses _iso for timestamp formatting.

*Call graph*: calls 4 internal fn (agent_turn_statuses, latest_activity, _audience_for, _iso); 1 external calls (JSONResponse).


##### `_framed_length`  (lines 1206–1219)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Checks that routes which parse a whole request body have a trustworthy Content-Length under a limit. It refuses chunked or missing-length bodies.

**Data flow**: It reads transfer and content length headers, compares the declared size with a limit, and returns None or a 411/413 response.

**Call relations**: resolve_workspace, open_session, _parse_inbound, preview, and fulfill_credential use it before parsing forms.

*Call graph*: called by 5 (_parse_inbound, fulfill_credential, open_session, preview, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 1222–1229)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses a request form and converts malformed form parser errors into a normal 400 response. This keeps bad client input from becoming an internal server error.

**Data flow**: It calls request.form, returns the parsed form on success, or returns a malformed-body response on parser failure.

**Call relations**: All form-reading routes share this helper, including login, chat uploads, previews, and credential fulfillment.

*Call graph*: called by 5 (_parse_inbound, fulfill_credential, open_session, preview, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 1232–1240)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a request body while enforcing a hard byte limit based on actual bytes consumed. It protects routes that accept raw bodies.

**Data flow**: It streams chunks from the request into memory, stops with a 413 if the limit is exceeded, and returns the full bytes otherwise.

**Call relations**: _parse_inbound uses it for plain chat text, and upload_start uses it for its small JSON request.

*Call graph*: called by 2 (_parse_inbound, upload_start); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 1243–1296)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...], tuple[str, ...]] | Response
```

**Purpose**: Parses a chat send from the browser, including plain text, multipart files, and references to already uploaded blobs. It enforces type and size limits before any turn is admitted.

**Data flow**: It reads content type, decodes raw text or parses multipart form data, validates message and upload parts, limits file count, and returns text, inline files, and uploaded blob keys or a response.

**Call relations**: chat calls it near the start of message handling before saving files or opening conversations.

*Call graph*: calls 4 internal fn (_bounded_body, _form, _framed_length, _uploaded_key); called by 1 (chat); 1 external calls (Response).


##### `_uploaded_key`  (lines 1299–1308)

```
def _uploaded_key(raw: str) -> str | None
```

**Purpose**: Validates that an uploaded_key form value names a blob under this web surface’s upload prefix. It prevents a browser from referencing arbitrary workspace storage.

**Data flow**: It receives a raw string, checks it stays contained under the upload root, and returns the key or None.

**Call relations**: _parse_inbound calls it for each presigned upload reference.

*Call graph*: called by 1 (_parse_inbound); 1 external calls (contained_relative).


##### `_inbox_paths`  (lines 1311–1322)

```
def _inbox_paths(uploads: tuple[UploadFile, ...], uploaded_keys: tuple[str, ...]) -> tuple[str, ...]
```

**Purpose**: Chooses safe destination paths for files attached to one message. It keeps duplicate filenames from overwriting each other.

**Data flow**: It receives inline UploadFiles and uploaded blob keys, extracts filenames, runs them through inbox_name with a used-name set, and returns web-inbox paths.

**Call relations**: chat calls it before _deliver_uploads and before adding the file note to the admitted text.

*Call graph*: called by 1 (chat); 2 external calls (PurePosixPath, inbox_name).


##### `_deliver_uploads`  (lines 1325–1340)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], uploaded_keys: tuple[str, ...], paths: tuple[str, ...]) -> None
```

**Purpose**: Copies attached file bytes into the conversation workspace before the agent turn runs. This makes the files available to the agent under the paths named in the message.

**Data flow**: It receives context, conversation ID, inline files, blob keys, and destination paths; it streams inline uploads or blob streams into workspace files.

**Call relations**: chat calls it after the conversation is chosen and before ctx.admit.

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (chat).


##### `_files_note`  (lines 1343–1346)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Adds a machine-readable note to the member’s message listing saved attachment paths. The transcript renderer later turns that note back into file cards.

**Data flow**: It receives text and paths, formats the attachment note, and appends it to the text or returns only the note.

**Call relations**: chat uses it when a send includes attachments, and _member_attachments later reads the same shape.

*Call graph*: called by 1 (chat).


##### `_member_attachments`  (lines 1354–1362)

```
def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]
```

**Purpose**: Splits a stored user message into the member’s real words and the attached file paths hidden in the trailing note. This keeps the UI from showing the bookkeeping sentence.

**Data flow**: It receives stored text, searches for the file note, and returns cleaned words plus a tuple of paths.

**Call relations**: _member_bubble calls it while rendering transcripts.

*Call graph*: called by 1 (_member_bubble).


##### `_attachment_preview`  (lines 1365–1377)

```
def _attachment_preview(public_base_url: str | None, agent_id: UUID, conversation_id: UUID, path: str) -> str | None
```

**Purpose**: Builds the URL for drawing an attached image inline in chat. It returns nothing for non-image files or deployments without a public base URL.

**Data flow**: It checks the path’s image media type, combines base URL, agent ID, conversation ID, and quoted path, and returns a preview URL or None.

**Call relations**: _conversation_messages and _transcript_aids pass it as the attachment lookup used by _member_bubble.

*Call graph*: 2 external calls (raster_image_media_type, quote).


##### `_attachment_payload`  (lines 1380–1393)

```
def _attachment_payload(path: str, preview_url: str | None) -> dict[str, object]
```

**Purpose**: Creates the file-card data for one member attachment. It includes filename, media type, and optional preview URL but no download link.

**Data flow**: It receives a workspace path and preview URL, infers media type from image/PDF/fallback rules, and returns a JSON-ready dictionary.

**Call relations**: _member_bubble calls it for each path extracted by _member_attachments.

*Call graph*: called by 1 (_member_bubble); 2 external calls (PurePosixPath, raster_image_media_type).


##### `_member_bubble`  (lines 1396–1404)

```
def _member_bubble(said: str, attach: Attach | None) -> dict[str, object]
```

**Purpose**: Builds the JSON representation of one user chat bubble. It separates text from attachments and attaches file cards when present.

**Data flow**: It receives stored user text and an optional preview function, extracts words and paths, builds a user bubble, and adds files if needed.

**Call relations**: _rendered_messages and _conversation_messages use it for settled and still-queued member messages.

*Call graph*: calls 2 internal fn (_attachment_payload, _member_attachments); called by 2 (_conversation_messages, _rendered_messages).


##### `_upload_chunks`  (lines 1407–1409)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded file in fixed-size chunks. This avoids reading large uploads all at once during workspace file delivery.

**Data flow**: It receives an UploadFile, repeatedly reads chunks until empty, and yields each bytes chunk.

**Call relations**: _deliver_uploads passes it to the workspace file writer for inline file attachments.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_key`  (lines 1412–1417)

```
def _answer_key(conversation_id: UUID, turn_id: UUID, index: int) -> str
```

**Purpose**: Creates the idempotency key for an answer to a specific question asked by a turn. This lets duplicate answer clicks land on the same admitted message.

**Data flow**: It receives conversation ID, asking turn ID, and question index, and returns a stable key string.

**Call relations**: chat uses it when admitting answers, and _asks uses it to match admitted answers back to question cards.

*Call graph*: called by 2 (_asks, chat).


##### `_answer_headers`  (lines 1420–1432)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Reads headers that identify a message as an answer to an agent’s question. It validates them before any conversation work happens.

**Data flow**: It reads answer turn and question headers, returns None for normal messages, returns parsed UUID and index, or returns a malformed-header response.

**Call relations**: chat calls it before choosing the idempotency key for admission.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 1435–1444)

```
def _stop_header(request: Request) -> UUID | None | Response
```

**Purpose**: Reads the header that asks to stop a running turn. It validates the turn ID without reading message content first.

**Data flow**: It reads the stop-turn header, returns None when absent, a UUID when valid, or a 400 response when malformed.

**Call relations**: chat calls it before deciding whether the POST is a stop request or a message.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `chat`  (lines 1447–1566)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles one browser chat POST: new message, answer to a question, comment into another surface, or stop request. It is the main write path for portal conversation activity.

**Data flow**: It authenticates, checks agent and conversation access, parses body and headers, saves uploads, opens or resolves the conversation, optionally stops a turn, admits the message, and returns turn/conversation details.

**Call relations**: It combines many helpers, including _parse_inbound, _open_conversation, _member_chat, _deliver_uploads, _turn_context, and _member_turn.

*Call graph*: calls 19 internal fn (admit, admitted_body, stop_turn, _agent_param, _answer_headers, _answer_key, _audience_for, _chat_source, _comment_notice, _commentable (+9 more)); 5 external calls (JSONResponse, Response, web_extension, UUID, uuid4).


##### `_rendered_text`  (lines 1569–1580)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Extracts readable text from a stored model message. For user messages, it strips internal context wrappers so the UI shows what the member actually said.

**Data flow**: It receives a Message, reads string content or text blocks, removes context/injected context from user text, trims it, and returns a string.

**Call relations**: _title_excerpt and _rendered_messages use it when projecting transcripts.

*Call graph*: called by 2 (_rendered_messages, _title_excerpt).


##### `_append_activity`  (lines 1583–1584)

```
def _append_activity(events: list[dict[str, str]], text: str) -> None
```

**Purpose**: Adds one activity event to a transcript event list. Activity is the short visible description of work a tool or skill did.

**Data flow**: It receives a list and text, appends a dictionary with kind activity, and mutates the list.

**Call relations**: _subagent_activity and _rendered_messages call it after _stored_activity finds display text.

*Call graph*: called by 2 (_rendered_messages, _subagent_activity).


##### `_stored_activity`  (lines 1587–1599)

```
def _stored_activity(block: ToolUseBlock, result: ToolResultBlock) -> str | None
```

**Purpose**: Chooses the best human-readable activity text for a tool call result. It prefers explicit activity text, then user descriptions, then useful fallbacks.

**Data flow**: It receives the tool-use block and matching result block, inspects their fields, and returns text or None when nothing should be shown.

**Call relations**: _subagent_activity and _rendered_messages use it while turning tool blocks into visible work events.

*Call graph*: called by 2 (_rendered_messages, _subagent_activity).


##### `_subagent_activity`  (lines 1621–1645)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

**Purpose**: Builds the visible work log for a subagent run from its transcript. It shows notes and tool activity but not the final answer.

**Data flow**: It receives messages, matches tool uses to activity results, collects note and activity events in order, bounds the list, and returns it.

**Call relations**: _subagent_nodes calls it after reading each spawned run’s transcript.

*Call graph*: calls 2 internal fn (_append_activity, _stored_activity); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 1648–1658)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

**Purpose**: Tries to decode a subagent finish answer as a JSON object. Structured answers can then be shown as readable prose instead of raw JSON.

**Data flow**: It receives answer text, returns None for empty or non-object JSON, or returns the decoded dictionary.

**Call relations**: _run_answer calls it before deciding how to display a run’s output.

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 1661–1684)

```
def _payload_prose(value: JsonValue) -> str
```

**Purpose**: Turns a JSON-like value into readable text. It makes strings, numbers, booleans, lists, and objects suitable for a chat bubble.

**Data flow**: It receives a JSON value, recursively formats it, and returns a prose string such as field labels or joined list entries.

**Call relations**: _run_answer calls it when a structured finish payload has more than one meaningful field.

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 1687–1703)

```
def _run_answer(answer: str) -> str
```

**Purpose**: Normalizes a subagent run’s final answer for display. It hides raw JSON wrappers when the payload clearly contains prose.

**Data flow**: It receives answer text, tries _finish_payload, returns a single prose field directly, formats larger payloads through _payload_prose, or returns the original text.

**Call relations**: _subagent_nodes uses it for subagent cards, and _TranscriptAids.render uses it for whole run conversations.

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (render, _subagent_nodes).


##### `_subagent_nodes`  (lines 1706–1745)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

**Purpose**: Builds a tree of subagent runs spawned by a conversation. Each node carries the child run’s target, work events, answer, and its own children.

**Data flow**: It receives spawned turns, resolves missing agent names, reads bounded transcripts for recent spawned runs, fills activity events, nests children under parents, and returns a map keyed by parent turn.

**Call relations**: _transcript_aids uses it for transcript projection, and _events uses it when a live terminal frame needs to announce subagent results.

*Call graph*: calls 4 internal fn (list_agents, read_transcript, _run_answer, _subagent_activity); called by 2 (_events, _transcript_aids); 2 external calls (__init__, gather).


##### `_rendered_messages`  (lines 1748–1911)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

**Purpose**: Projects raw transcript messages into the chat UI’s message list. It removes hidden engine context, turns tool calls into events, attaches files/questions/apps/connect controls, and groups assistant work into replies.

**Data flow**: It receives messages plus helper maps, walks them in order, builds user bubbles and assistant replies, folds notes/activity/subagents/cards into the right reply, and returns JSON-ready messages.

**Call relations**: _TranscriptAids.render is the public wrapper that supplies this function with all side data.

*Call graph*: calls 4 internal fn (_append_activity, _member_bubble, _rendered_text, _stored_activity); called by 1 (render); 1 external calls (member_message_text).


##### `_rendered_messages.note_answer`  (lines 1823–1828)

```
def note_answer() -> None
```

**Purpose**: Moves a pending assistant answer into the event list when later tool activity shows it was really a work note. This preserves the order of narration and actions.

**Data flow**: It reads the outer pending answer variables, inserts the text as a note event when allowed, increments the note count, and clears the answer.

**Call relations**: The outer _rendered_messages loop calls it while grouping assistant messages.


##### `_rendered_messages.flush_reply`  (lines 1830–1865)

```
def flush_reply(include_subagents: bool) -> None
```

**Purpose**: Emits the current assistant reply when a turn boundary is reached. It attaches any subagents, questions, files, apps, or connection controls belonging to that turn.

**Data flow**: It reads the outer accumulated answer and pending events, combines them with side maps for the closing turn, appends a reply if there is anything to show, and resets accumulators.

**Call relations**: The outer _rendered_messages loop calls it between user messages and once at the end.


##### `_asks`  (lines 1930–1962)

```
def _asks(conversation_id: UUID, turns: tuple[Turn, ...], admitted: tuple[KeyedAdmission, ...]) -> _Asks
```

**Purpose**: Builds question cards for turns that asked the member something and records which answer messages those cards already show. This prevents duplicate answer bubbles.

**Data flow**: It receives conversation turns and keyed admissions, matches answers by _answer_key, marks older answered questions closed, and returns cards plus stated message refs.

**Call relations**: _transcript_aids calls it, and _rendered_messages uses the result through _TranscriptAids.render.

*Call graph*: calls 1 internal fn (_answer_key); called by 1 (_transcript_aids); 2 external calls (__init__, member_message_text).


##### `_TranscriptAids.render`  (lines 1985–2004)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Renders transcript messages using the side data collected for a conversation. It also rewrites assistant replies as run answers when the conversation is a subagent run.

**Data flow**: It receives raw messages, calls _rendered_messages with stored maps and sets, optionally normalizes assistant text through _run_answer, and returns rendered messages.

**Call relations**: _conversation_messages and _history_messages get a _TranscriptAids object from _transcript_aids and call this method.

*Call graph*: calls 2 internal fn (_rendered_messages, _run_answer).


##### `_transcript_aids`  (lines 2007–2059)

```
async def _transcript_aids(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, agent_origin: frozenset[str], speakers: dict[str, str], asked: dict[str, str], opens: frozenset[UUI
```

**Purpose**: Collects everything needed to render a conversation consistently: turns, subagents, shared files, question answers, created apps, speakers, and connection controls.

**Data flow**: It reads several core views concurrently, builds maps for files, apps, speakers, questions, and controls, creates an attachment preview function, and returns a _TranscriptAids object.

**Call relations**: _conversation_messages uses it for the live transcript tail, and _history_messages uses it for older compacted pages.

*Call graph*: calls 9 internal fn (conversation_subagent_turns, keyed_admissions, list_conversation_artifacts, list_turns, _asks, _connect_controls, _created_apps, _file_payload, _subagent_nodes); called by 2 (_conversation_messages, _history_messages); 3 external calls (__init__, gather, partial).


##### `_connect_controls`  (lines 2062–2102)

```
async def _connect_controls(ctx: SurfaceContext, conversation_id: UUID, turns: tuple[Turn, ...], viewer: UUID) -> dict[str, dict[str, object]]
```

**Purpose**: Finds connection handoff controls that should be shown in a transcript for the current viewer. It shows only the requesting member’s own connect requests.

**Data flow**: It receives turns and viewer ID, skips unavailable connect machinery or other members’ requests, returns fresh connect controls for open requests or landed account labels for completed ones.

**Call relations**: _transcript_aids calls it so rendered replies can include connect buttons or completed account labels.

*Call graph*: calls 4 internal fn (connect_available, held_accounts, _connect_control, _provider_label); called by 1 (_transcript_aids).


##### `_conversation_messages`  (lines 2105–2227)

```
async def _conversation_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], Turn | None, int]
```

**Purpose**: Builds the complete message projection for a conversation as the portal displays it. It combines committed transcript, live running turn prompt, queued arrivals, and earlier-page cursor information.

**Data flow**: It reads transcript, origin refs, speakers, compactions, latest turn, and queue rows; renders stored messages through _transcript_aids; appends still-running or waiting messages; and returns rendered messages, latest turn, and earlier cursor index.

**Call relations**: transcript and conversation_transcript call it as the shared conversation renderer.

*Call graph*: calls 10 internal fn (agent_origin_refs, arrival_speakers, latest_turn, list_compactions, queued_arrivals, read_transcript, turn_detail, _member_bubble, _transcript_aids, _verified_earlier); called by 2 (conversation_transcript, transcript); 3 external calls (gather, partial, member_message_text).


##### `_verified_earlier`  (lines 2230–2246)

```
async def _verified_earlier(ctx: SurfaceContext, conversation_id: UUID, indices: tuple[int, ...], messages: tuple[Message, ...]) -> int
```

**Purpose**: Finds the newest compaction record that truly sits directly above the current transcript window. This avoids offering history pages that would repeat messages.

**Data flow**: It receives compaction indices and current messages, reads each candidate’s after-window, compares it with the transcript prefix, and returns the matching index or 0.

**Call relations**: _conversation_messages and _history_messages call it when constructing earlier-history cursors.

*Call graph*: calls 1 internal fn (read_compaction_after); called by 2 (_conversation_messages, _history_messages).


##### `_history_cursor`  (lines 2249–2251)

```
def _history_cursor(index: int, end: int | None=None) -> str
```

**Purpose**: Encodes a history paging position into an opaque cursor string for the browser. The cursor hides the internal index and optional end position.

**Data flow**: It receives an index and optional end, formats them, base64-url encodes the result, strips padding, and returns the cursor.

**Call relations**: transcript, conversation_transcript, _history_messages, and _bounded_history_page use it to advertise older pages.

*Call graph*: called by 4 (fits, _history_messages, conversation_transcript, transcript); 1 external calls (urlsafe_b64encode).


##### `_history_position`  (lines 2254–2269)

```
def _history_position(cursor: str) -> tuple[int, int | None]
```

**Purpose**: Decodes and validates a history cursor from the browser. It rejects malformed or overly long cursors.

**Data flow**: It receives a cursor string, restores base64 padding, decodes index and optional end, validates numeric bounds, and returns them or raises ValueError.

**Call relations**: _history_messages calls it before reading a compacted history record.

*Call graph*: called by 1 (_history_messages); 1 external calls (b64decode).


##### `_bounded_history_page`  (lines 2272–2294)

```
def _bounded_history_page(messages: list[dict[str, object]], index: int, end: int) -> tuple[list[dict[str, object]], int]
```

**Purpose**: Cuts a rendered history window into a page that fits both message-count and byte-size limits. This prevents very large history responses.

**Data flow**: It receives rendered messages, compaction index, and end position, tests candidate slices, binary-searches if needed, and returns the page plus its start index.

**Call relations**: _history_messages calls it after rendering an older compaction window.

*Call graph*: called by 1 (_history_messages).


##### `_bounded_history_page.fits`  (lines 2277–2282)

```
def fits(start: int) -> bool
```

**Purpose**: Checks whether one candidate history slice fits the response byte limit. It includes the cursor that would be returned with that slice.

**Data flow**: It receives a start index from the outer function, builds a JSON payload with messages and earlier cursor, measures its encoded body, and returns true or false.

**Call relations**: _bounded_history_page uses it first for the maximum slice and then during binary search.

*Call graph*: calls 1 internal fn (_history_cursor); 1 external calls (JSONResponse).


##### `_history_messages`  (lines 2297–2348)

```
async def _history_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, cursor: str, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], str | None] | None
```

**Purpose**: Returns one older page of a compacted conversation transcript. It ensures pages join cleanly with the live transcript and with each other.

**Data flow**: It decodes the cursor, reads the compaction record, trims the kept tail, gathers render aids, renders the window, bounds the page, finds any page above it, and returns messages plus next cursor.

**Call relations**: _conversation_history calls it after authorizing that the viewer may read the conversation.

*Call graph*: calls 8 internal fn (agent_origin_refs, arrival_speakers, read_compaction, _bounded_history_page, _history_cursor, _history_position, _transcript_aids, _verified_earlier); called by 1 (_conversation_history); 1 external calls (gather).


##### `transcript`  (lines 2351–2395)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the current member’s chat transcript for one agent conversation. It includes live turn information or any still-open handoff from the latest committed turn.

**Data flow**: It authenticates, validates agent and conversation access through _member_chat, renders messages through _conversation_messages, adds earlier cursor and live/handoff fields, and returns JSON.

**Call relations**: The browser calls it when opening a chat; it shares rendering with conversation_transcript.

*Call graph*: calls 7 internal fn (_agent_param, _audience_for, _conversation_messages, _history_cursor, _member_chat, _open_handoffs, _opens); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 2398–2409)

```
async def _open_handoffs(ctx: SurfaceContext, terminal: TerminalFrame) -> dict[str, object]
```

**Purpose**: Reports handoffs from the newest finished turn that still need the member’s attention. Currently this covers pending credential prompts.

**Data flow**: It receives a terminal frame, checks for a credential request, filters still-pending prompts with _pending_prompts, and returns a small payload.

**Call relations**: transcript calls it when the latest turn has ended but left a credential request open.

*Call graph*: calls 1 internal fn (_pending_prompts); called by 1 (transcript).


##### `_connect_control`  (lines 2412–2416)

```
def _connect_control(ctx: SurfaceContext, provider: str, turn_id: UUID) -> dict[str, object]
```

**Purpose**: Builds the JSON data for a connect button tied to a turn. The button names the provider and the turn to open, not a stale consent URL.

**Data flow**: It receives context, provider, and turn ID, resolves a provider label, and returns provider, label, and turn fields.

**Call relations**: _connect_controls and _events use it when drawing connection requests in transcripts or live streams.

*Call graph*: calls 1 internal fn (_provider_label); called by 2 (_connect_controls, _events).


##### `_provider_label`  (lines 2419–2430)

```
def _provider_label(ctx: SurfaceContext, provider: str) -> str
```

**Purpose**: Finds the friendly display name for a connection provider. It prefers the web first-run catalog and falls back to the connect system or raw provider slug.

**Data flow**: It receives context and provider name, searches FIRST_RUN_PROVIDERS, then optionally asks ctx.connect_label, and returns a string.

**Call relations**: _connect_control, _connect_controls, and agent_setup use it so provider names stay consistent.

*Call graph*: calls 2 internal fn (connect_available, connect_label); called by 3 (_connect_control, _connect_controls, agent_setup).


##### `_provider_summary`  (lines 2433–2440)

```
def _provider_summary(provider: str) -> str
```

**Purpose**: Returns the short explanatory text for a known provider from the first-run catalog. Unknown providers get no summary.

**Data flow**: It receives a provider name, searches FIRST_RUN_PROVIDERS, and returns the matching summary or an empty string.

**Call relations**: agent_setup uses it to enrich connector setup rows.

*Call graph*: called by 1 (agent_setup).


##### `chats_index`  (lines 2443–2455)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Resolves a permalink conversation ID into the chat or conversation data the portal should open. It is used for routes like #/c/<id>.

**Data flow**: It authenticates, requires a conversation query parameter, then delegates to _resolve_chat with the web store and audience.

**Call relations**: The portal calls this when landing on a conversation permalink rather than a selected agent page.

*Call graph*: calls 2 internal fn (_audience_for, _resolve_chat); 2 external calls (Response, web_extension).


##### `_resolve_chat`  (lines 2458–2546)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

**Purpose**: Figures out what a permalinked conversation is for this member: a web chat, a commentable external conversation, an admin-readable conversation, or nothing. It returns the right row shape for the portal.

**Data flow**: It parses the requested UUID, checks chat agents through _member_chat, reads latest turn details for web chats, or falls back to conversation-agent lookup and listed conversation access, then returns JSON.

**Call relations**: chats_index delegates all permalink resolution to this helper.

*Call graph*: calls 9 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, allows, _commentable, _conversation_row, _iso, _member_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 2549–2562)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Authenticates a per-agent panel request and verifies that the URL’s agent is in the member’s web audience. It is the common gate for settings, skills, setup, conversations, actions, and more.

**Data flow**: It calls _audience_for, parses the agent path parameter, checks audience.allows, and returns member ID, email, audience, and agent ID or a response.

**Call relations**: Many per-agent route handlers call it before performing their specific read or write.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 11 (_readable_conversation, actions, agent_setup, community_skill, community_skills, connections, conversations, homepage, intents, settings (+1 more)); 1 external calls (Response).


##### `_iso`  (lines 2565–2566)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Formats optional datetimes for JSON responses. It returns null-style None when there is no timestamp.

**Data flow**: It receives a datetime or None and returns ISO text or None.

**Call relations**: Status, usage, memory, object, conversation, and permalink responses use it for consistent timestamp formatting.

*Call graph*: called by 6 (_conversation_row, _memory_rows, _resolve_chat, _usage_payload, agents_status, object_detail); 1 external calls (isoformat).


##### `_window_param`  (lines 2569–2585)

```
def _window_param(request: Request) -> int | None | Response
```

**Purpose**: Parses the usage time range requested by the browser. It accepts named ranges or a custom number of seconds.

**Data flow**: It reads query parameters, validates allowed names or numeric bounds, and returns seconds, None for all time, or a 400 response.

**Call relations**: workspace_usage calls it before reading spend data.

*Call graph*: called by 1 (workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 2588–2628)

```
def _usage_payload(report: MemberSpendReport | SpendReport) -> dict[str, object]
```

**Purpose**: Turns a member or workspace spend report into the JSON shape the usage page draws. It includes totals, daily lines, and breakdowns.

**Data flow**: It receives a spend report, reads nested usage fields, formats timestamps, and returns a dictionary.

**Call relations**: workspace_usage uses it for both the reader’s own usage and the admin workspace rollup.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_usage).


##### `skills`  (lines 2631–2655)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists skills available to a selected agent. These are the deploy and workspace skills the member can manage from the portal.

**Data flow**: It gates the agent with _panel_gate, asks ctx.agent_skills, and returns skill metadata as JSON.

**Call relations**: It is the per-agent skills panel read.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 2661–2665)

```
def _community_refusal(fault: Exception) -> Response
```

**Purpose**: Formats a community directory failure as member-readable text. A special header tells the frontend to show the body directly.

**Data flow**: It receives an exception, converts it to text, and returns a 502 response with the refusal header.

**Call relations**: community_skills and community_skill call it when the community service is unavailable or HTTP access fails.

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 2672–2688)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists or searches community skills that a member may review for an agent. It enforces a minimum search length.

**Data flow**: It gates the agent, reads the q parameter, rejects too-short queries, calls the community listing service, and returns skill summaries or a readable failure.

**Call relations**: The community skills panel calls this before an apply intent is submitted elsewhere.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 2691–2712)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches one community skill document for review. It validates owner, repo, and skill name before contacting the directory.

**Data flow**: It gates the agent, validates path parts against safe patterns, fetches the skill, and returns JSON, 404, or a readable directory failure.

**Call relations**: The community skill detail view uses this; installation still goes through the intent/action lane.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 2715–2790)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows the member’s memory across agents they can reach, either as recent items or search results. It never exposes another member’s private memory subject.

**Data flow**: It authenticates, checks memory availability, parses kind/cursor or query, reads recent memory or searches per reachable agent, deduplicates results, and returns rows plus available actions.

**Call relations**: The workspace memory tab calls this, and it uses _memory_rows and _action_payloads for response shaping.

*Call graph*: calls 7 internal fn (object_actions, recent_memory, search_memory, decode, _action_payloads, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 2793–2803)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts memory match objects into simple JSON rows for the portal. It includes kind, text, optional object ref, time, and subject.

**Data flow**: It receives memory matches, formats refs and timestamps, and returns a list of dictionaries.

**Call relations**: workspace_memory uses it for both recent memory and search results.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `connections`  (lines 2806–2815)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connection accounts visible for one selected agent. It includes the member’s private grants and shared edges they may see.

**Data flow**: It gates the agent, asks ctx.list_agent_connections with member and admin status, and returns model-dumped rows.

**Call relations**: The per-agent connections panel calls this.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 2818–2828)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the member-visible pool of connection accounts across the workspace. Agents outside the member’s audience are filtered out of each row.

**Data flow**: It authenticates, reads connections with admin scope when allowed, filters each connection’s agent list by audience, and returns JSON.

**Call relations**: The workspace connections page calls this separate from an individual agent panel.

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 2831–2837)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports GitHub connection coverage for the member or admin. It tells the portal whether GitHub capabilities are installed or available.

**Data flow**: It authenticates, calls ctx.github_coverage with member and admin status, and returns the coverage model as JSON.

**Call relations**: workspace_first_run and _held_providers also use the same core coverage read.

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 2840–2871)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists conversations for one selected agent that the member can see. Admins may also see unreadable or disclosable rows according to core rules.

**Data flow**: It gates the agent, parses a bounded search string, asks for one extra row beyond the list limit, converts rows with _conversation_row, and returns rows plus a more flag.

**Call relations**: The conversations panel calls this before opening a conversation transcript route.

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 2874–2878)

```
def _searched(request: Request) -> str | None
```

**Purpose**: Reads and bounds the conversation search query from the URL. Empty searches become None.

**Data flow**: It takes a request, trims the q parameter to the maximum length, and returns the text or None.

**Call relations**: conversations uses it when calling the core conversation listing.

*Call graph*: called by 1 (conversations).


##### `_conversation_row`  (lines 2881–2915)

```
def _conversation_row(entry: ListedConversation, member_id: UUID, agent: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Converts a ListedConversation into the JSON row shown in conversation lists and permalink resolution. It includes readability and commentability flags.

**Data flow**: It receives a listed conversation, viewer member ID, and optional agent summary, then formats IDs, labels, timestamps, speakers, and permission booleans.

**Call relations**: conversations and _resolve_chat use it for consistent conversation row output.

*Call graph*: calls 2 internal fn (_commentable, _iso); called by 2 (_resolve_chat, conversations).


##### `_readable_conversation`  (lines 2918–2938)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes a read-only conversation content request. It verifies agent reach, conversation ID, and core readability, then creates a SlotViewer for later projections.

**Data flow**: It gates the panel, parses or receives a conversation ID, asks ctx.readable_conversation, and returns agent ID, conversation ID, and viewer or a 404 response.

**Call relations**: conversation_transcript, _conversation_history, conversation_attachment, and _slot_target use it as the shared content-read gate.

*Call graph*: calls 3 internal fn (readable_conversation, _opens, _panel_gate); called by 4 (_conversation_history, _slot_target, conversation_attachment, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 2941–2959)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a read-only transcript for an authorized conversation. This covers conversations the member can read but may not continue as their own chat.

**Data flow**: It routes cursor requests to _conversation_history, otherwise authorizes with _readable_conversation, renders messages through _conversation_messages, adds earlier cursor if present, and returns JSON.

**Call relations**: The conversations panel calls this after selecting a row.

*Call graph*: calls 4 internal fn (_conversation_history, _conversation_messages, _history_cursor, _readable_conversation); 1 external calls (JSONResponse).


##### `_member_chat_page`  (lines 2962–2990)

```
async def _member_chat_page(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes a transcript or attachment read for the member’s own chat route. It covers private extension conversations that normal panel gates may not cover.

**Data flow**: It authenticates, parses agent and conversation IDs, checks _member_chat, and returns agent ID, conversation ID, and SlotViewer or a 404 response.

**Call relations**: _conversation_history and conversation_attachment fall back to it when _readable_conversation is not enough.

*Call graph*: calls 4 internal fn (_agent_param, _audience_for, _member_chat, _opens); called by 2 (_conversation_history, conversation_attachment); 4 external calls (__init__, Response, web_extension, UUID).


##### `_conversation_history`  (lines 2993–3014)

```
async def _conversation_history(ctx: SurfaceContext, request: Request, cursor: str) -> Response
```

**Purpose**: Returns one earlier page for a compacted transcript after authorizing the same ways that can advertise such a cursor. It supports infinite scroll upward.

**Data flow**: It first tries read-only conversation authorization, then member-chat authorization, calls _history_messages, and returns messages plus another cursor or 404.

**Call relations**: conversation_transcript calls it when the request includes a cursor.

*Call graph*: calls 3 internal fn (_history_messages, _member_chat_page, _readable_conversation); called by 1 (conversation_transcript); 2 external calls (JSONResponse, Response).


##### `_inbox_attachment`  (lines 3017–3028)

```
def _inbox_attachment(path: str) -> bool
```

**Purpose**: Checks whether a path names a direct file saved by the web composer under web-inbox. It refuses nested paths and special directory names.

**Data flow**: It receives a path string, splits directory and filename, and returns true only for one safe leaf under the inbox directory.

**Call relations**: conversation_attachment uses it before serving any workspace file bytes.

*Call graph*: called by 1 (conversation_attachment).


##### `conversation_attachment`  (lines 3031–3071)

```
async def conversation_attachment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves an inline image preview for a file the member attached to a message. It deliberately refuses non-image and unsafe files so the browser cannot execute uploaded content.

**Data flow**: It authorizes the conversation, validates the requested path and media type, checks workspace file size, reads the file stream, validates it as the claimed image, and returns bytes or an error.

**Call relations**: Attachment preview URLs created by _attachment_preview point to this route.

*Call graph*: calls 5 internal fn (list_workspace_files, read_workspace_file, _inbox_attachment, _member_chat_page, _readable_conversation); 5 external calls (__init__, Response, raster_image_media_type, validated_image_preview, log).


##### `_slot_target`  (lines 3091–3115)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

**Purpose**: Finds the conversation target for a typed slot read, including subagent conversation reads rooted in a parent conversation. It ensures the subagent belongs to the authorized root.

**Data flow**: It parses optional root and conversation IDs, authorizes the root or conversation, checks spawned turns when needed, and returns a SlotTarget or response.

**Call relations**: conversation_slots and conversation_slot call it before asking providers for slot summaries or payloads.

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 3118–3134)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

**Purpose**: Builds the context object passed to a conversation slot provider. It includes audience, conversation ID, agent ID, transcript messages, and public base URL.

**Data flow**: It reads the conversation audience and transcript, merges the audience into the extension context, and returns a ConversationSlotContext or None.

**Call relations**: conversation_slots and conversation_slot use it as the base provider context.

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 3137–3221)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

**Purpose**: Adds host-side projections needed by built-in slot providers, such as workspace changes, artifacts, sites, or automations. It also precomputes which member objects are visible.

**Data flow**: It receives a slot context and provider identity, reads the needed core data for certain extension/content combinations, replaces projection or visible_items, and returns the updated context.

**Call relations**: conversation_slots calls it before summarizing each slot, and conversation_slot calls it before reading one slot payload.

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 7 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit).


##### `_authorized_slot_payload`  (lines 3224–3268)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

**Purpose**: Filters a slot payload so it only includes sites or automations the viewer may actually open or read. It can redact sensitive automation details while still showing the row exists.

**Data flow**: It receives a payload and slot context, compares payload items with visible_items, removes unauthorized items, redacts hidden fields when needed, and returns the payload.

**Call relations**: conversation_slot applies it after the provider returns a typed payload.

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 3271–3315)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists which typed side panels are available for an authorized conversation, with counts. Examples include changes and artifacts.

**Data flow**: It authorizes the slot target, builds shared context, loops through registered slot providers, projects context, asks each provider for a summary count, logs failures, and returns slot tiles.

**Call relations**: The transcript UI calls this to decide which conversation side panels to show.

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 3318–3345)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full payload for one typed conversation slot. It verifies the provider and checks the provider returned the expected model type.

**Data flow**: It authorizes the target, finds the slot provider by ID, builds and projects context, reads the provider payload, filters it through _authorized_slot_payload, and returns JSON.

**Call relations**: The portal calls this after the user opens a slot listed by conversation_slots.

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 3348–3351)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Retrieves the workspace-changes projection from a slot context and fails if the wrong projection was supplied. It is a type guard for the built-in changes slot.

**Data flow**: It receives a ConversationSlotContext, checks projection type, and returns WorkspaceChanges or raises an error.

**Call relations**: _read_changes and _summarize_changes call it for the CHANGES_SLOT provider.

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 3354–3355)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Returns the full changes-slot payload from the context. It is the read callback for the built-in changes slot.

**Data flow**: It receives slot context and returns the WorkspaceChanges projection from _changes_projection.

**Call relations**: CHANGES_SLOT registers it as the provider read function.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 3358–3359)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts workspace changes for the slot list. It hides the slot when there are no changes.

**Data flow**: It receives slot context, counts changes in the projection, and returns the count or None.

**Call relations**: CHANGES_SLOT registers it as the summary function used by conversation_slots.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 3372–3375)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Retrieves the artifacts projection from a slot context and fails if it is missing or wrong. It is a type guard for the built-in artifacts slot.

**Data flow**: It receives a ConversationSlotContext, checks projection type, and returns ArtifactsSlotPayload or raises.

**Call relations**: _read_artifacts and _summarize_artifacts call it for ARTIFACTS_SLOT.

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 3378–3379)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Returns the full artifacts-slot payload from the context. It is the read callback for the artifacts slot.

**Data flow**: It receives slot context and returns the ArtifactsSlotPayload projection from _artifacts_projection.

**Call relations**: ARTIFACTS_SLOT registers it as the provider read function.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 3382–3384)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts shared artifacts for the slot list. It hides the artifacts slot when there are none.

**Data flow**: It receives slot context, counts artifacts in the projection, and returns the count or None.

**Call relations**: ARTIFACTS_SLOT registers it as the summary function used by conversation_slots.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `workspace_credentials`  (lines 3397–3410)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists workspace credential slots that members can fill, without exposing any secret values. It also includes actions available on the credential collection.

**Data flow**: It authenticates, reads credential slot state, reads collection actions, formats both, and returns JSON.

**Call relations**: The workspace credentials page calls this.

*Call graph*: calls 4 internal fn (list_credential_slots, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 3413–3432)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace member roster and whether the current member can add people. It includes member collection actions.

**Data flow**: It authenticates, reads members, uses audience.admin for can_add, reads member collection actions, and returns JSON.

**Call relations**: The team page calls this, and admin_index offers deeper admin-only roster details.

*Call graph*: calls 4 internal fn (list_members, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 3435–3446)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the member, such as registered data sources. Admins see all; ordinary members see allowed private and shared sources.

**Data flow**: It authenticates, calls ctx.list_sources with member and admin scope, and returns source rows.

**Call relations**: The workspace sources page calls this.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `workspace_surfaces`  (lines 3449–3460)

```
async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists installed surfaces, such as Slack, that are attached to agents visible to this member. It filters out installations for agents outside the web audience.

**Data flow**: It authenticates, reads all installations, filters by audience.allows(agent_id), and returns JSON.

**Call relations**: The workspace surfaces/topology page calls this; admin_index has the admin-wide view.

*Call graph*: calls 2 internal fn (list_installations, _audience_for); 1 external calls (JSONResponse).


##### `workspace_imessage_claim`  (lines 3500–3521)

```
async def workspace_imessage_claim(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports the current member’s iMessage claim state for first-run setup. It says pending, connected, or expired without exposing other members’ claims.

**Data flow**: It authenticates, reads the member’s imessage surface claim, compares proof and expiry timestamps, and returns an ImessageClaim JSON model.

**Call relations**: The first-run iMessage step polls this while waiting for a phone verification.

*Call graph*: calls 2 internal fn (member_surface_claim, _audience_for); 3 external calls (__init__, now, JSONResponse).


##### `workspace_first_run`  (lines 3524–3564)

```
async def workspace_first_run(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the first-run connector catalog and which key setup steps are already installed. It helps new teams connect Slack, GitHub, iMessage, and other providers.

**Data flow**: It authenticates, reads installations and GitHub coverage, checks the iMessage feature flag and extension presence, formats provider tiles, connector steps, and actions, and returns JSON.

**Call relations**: The first-run flow and Connect page call this; it shares provider catalog naming with setup and starter helpers.

*Call graph*: calls 5 internal fn (github_coverage, list_installations, object_actions, _action_payloads, _audience_for); 3 external calls (__init__, flag_enabled, JSONResponse).


##### `connector_catalog`  (lines 3567–3595)

```
async def connector_catalog(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Searches the installed brokers’ catalog of connectable providers for the Connect page. It bounds query and cursor strings so the request stays controlled.

**Data flow**: It authenticates, validates q and after parameters, asks ctx.connector_catalog for a page, converts entries to tiles, and returns the next cursor.

**Call relations**: The Connect page calls this when browsing provider options.

*Call graph*: calls 2 internal fn (connector_catalog, _audience_for); 3 external calls (__init__, JSONResponse, Response).


##### `_held_providers`  (lines 3646–3657)

```
async def _held_providers(ctx: SurfaceContext, member_id: UUID, *, admin: bool) -> frozenset[str]
```

**Purpose**: Computes which provider accounts or surfaces the workspace already has in the vocabulary used by starter rows. It includes broker connections, Slack, and GitHub.

**Data flow**: It reads connections, surface installations, and GitHub coverage, combines provider names into a set, and returns it frozen.

**Call relations**: workspace_starters calls it before deciding which ranked starter ideas are ready or blocked.

*Call graph*: calls 3 internal fn (github_coverage, list_connections, list_installations); called by 1 (workspace_starters).


##### `fill_starters`  (lines 3660–3749)

```
def fill_starters(slate: Slate, held: frozenset[str], taken: frozenset[str], installed: tuple[StarterApp, ...]=()) -> tuple[tuple[StarterRow, ...], UnlockRow | None]
```

**Purpose**: Chooses which starter suggestions to show based on the ranked slate, installed apps, already-taken app names, and connected providers. It separates ready apps from unlocks that need accounts.

**Data flow**: It receives a slate and current workspace state, walks ranked entries, skips unavailable or duplicate items, fills up to two app rows, picks one unlock, adds a check-in row, and returns both lists.

**Call relations**: workspace_starters calls it after loading cached/generated starter data and live provider state.

*Call graph*: called by 1 (workspace_starters); 4 external calls (__init__, __init__, __init__, get).


##### `_solvent`  (lines 3752–3761)

```
async def _solvent() -> bool
```

**Purpose**: Checks whether the workspace balance still has room to spend money on generating starter suggestions. It avoids model work when the workspace is over its spending boundary.

**Data flow**: It opens an extension transaction, reads balance headroom, and returns true if no headroom is set or balance exceeds the reserve threshold.

**Call relations**: workspace_starters passes this result into StarterCache.

*Call graph*: called by 1 (workspace_starters); 2 external calls (read_headroom, web_extension).


##### `_recalled`  (lines 3764–3771)

```
async def _recalled(ctx: SurfaceContext, member_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads recent memory for the current member’s own audience to help generate personalized starter suggestions. It excludes other members’ private memory.

**Data flow**: It checks memory availability, builds audience subjects for the member, reads a bounded recent page, truncates each memory text, and returns a tuple.

**Call relations**: workspace_starters calls it before asking StarterCache for a slate.

*Call graph*: calls 1 internal fn (recent_memory); called by 1 (workspace_starters); 2 external calls (audience_subjects, conversation_audience).


##### `workspace_starters`  (lines 3774–3832)

```
async def workspace_starters(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns personalized starter rows for a member before they ask anything. It combines generated ranking with live connector and app setup state.

**Data flow**: It authenticates, reads installed app setup states, builds or reads a StarterCache slate using memory and solvency, reads held providers and taken names, calls fill_starters, and returns JSON.

**Call relations**: The portal start screen calls this after first-run and agent boot data are available.

*Call graph*: calls 7 internal fn (agent_setup, _audience_for, _held_providers, _recalled, _setup_ready, _solvent, fill_starters); 5 external calls (__init__, __init__, gather, JSONResponse, web_extension).


##### `workspace_usage`  (lines 3835–3910)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns spend and usage data for the current member, and for the whole workspace when the member is an admin. This powers the usage page.

**Data flow**: It authenticates, parses the requested window, reads member spend, formats usage and caps, optionally reads admin rollup, and returns JSON.

**Call relations**: It uses _window_param and _usage_payload to keep time-range parsing and spend formatting shared.

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `connect_handoff`  (lines 3916–3940)

```
async def connect_handoff(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the outbound OAuth-style connection flow for a connect request left by a turn. It mints the provider consent URL only when the member clicks.

**Data flow**: It authorizes the member’s access to the turn, asks ctx.connect_url for a fresh URL, redirects there, or returns a callback page if the request is no longer valid.

**Call relations**: Connect buttons produced by _connect_control point to this route.

*Call graph*: calls 2 internal fn (connect_url, _member_turn); 2 external calls (callback_page, RedirectResponse).


##### `_member_turn`  (lines 3943–3996)

```
async def _member_turn(ctx: SurfaceContext, request: Request, *, named_turn: UUID | None=None, allow_commentable: bool=False) -> tuple[UUID, UUID, str] | Response
```

**Purpose**: Authorizes access to a specific turn for mutation or streaming. It ensures the requester owns the turn or may read/comment on its conversation.

**Data flow**: It authenticates, parses or receives a turn ID, reads turn detail and owner, checks agent visibility and _member_chat when needed, and returns member ID, turn ID, and email or a refusal.

**Call relations**: chat uses it for stopping turns, connect_handoff uses it for connect presses, and stream uses it for live event access.

*Call graph*: calls 5 internal fn (turn_detail, turn_owner, _audience_for, _commentable, _member_chat); called by 3 (chat, connect_handoff, stream); 3 external calls (Response, web_extension, UUID).


##### `stream`  (lines 3999–4007)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a server-sent event stream for one turn’s live frames. This is how the browser watches an agent answer in real time.

**Data flow**: It authorizes the turn through _member_turn, reads Last-Event-ID for resume, and returns a StreamingResponse over _events.

**Call relations**: The chat UI calls it after chat returns a turn ID to tail.

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 4010–4011)

```
def _event(name: str, payload: dict[str, object]) -> bytes
```

**Purpose**: Formats a named server-sent event with a JSON payload. Server-sent events are simple text records the browser can consume as a live stream.

**Data flow**: It receives an event name and payload dictionary, JSON-encodes the payload, and returns event bytes.

**Call relations**: _events uses it for extra web-specific events such as subagent, connect, credentials, files, and apps.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 4014–4027)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest) -> dict[str, object] | None
```

**Purpose**: Filters a credential request down to prompts that are still unanswered. This prevents fulfilled or expired prompts from being shown again.

**Data flow**: It receives a credential request, checks each slot with ctx.credential_prompt_pending, and returns reason, seal, and pending prompts or None.

**Call relations**: _events uses it during live streaming, and _open_handoffs uses it during transcript reload.

*Call graph*: calls 1 internal fn (credential_prompt_pending); called by 2 (_events, _open_handoffs).


##### `_file_payload`  (lines 4030–4043)

```
def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]
```

**Purpose**: Formats a shared artifact as a chat file card. It includes download and preview links where the core context can mint them.

**Data flow**: It receives a SharedArtifact, asks context for artifact and preview links, copies metadata, and returns a dictionary.

**Call relations**: _events uses it for newly shared files, and _transcript_aids uses it for settled transcript files.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 2 (_events, _transcript_aids).


##### `_opens`  (lines 4046–4047)

```
def _opens(audience: WebAudience) -> frozenset[UUID]
```

**Purpose**: Returns the set of agent IDs this web audience can open. This is used to decide whether app cards should be visible.

**Data flow**: It receives a WebAudience and returns a frozen set of its agent IDs.

**Call relations**: transcript, _readable_conversation, _member_chat_page, and _events pass this set into app-card rendering.

*Call graph*: called by 4 (_events, _member_chat_page, _readable_conversation, transcript).


##### `_created_apps`  (lines 4050–4081)

```
async def _created_apps(ctx: SurfaceContext, created: Mapping[str, tuple[ObjectRef, ...]], opens: frozenset[UUID]) -> dict[str, list[dict[str, object]]]
```

**Purpose**: Builds app cards for agents created by one or more turns, but only for apps the viewer may open. It resolves object references back to live agent summaries.

**Data flow**: It receives created object refs and openable agent IDs, lists agents, matches created agent names, filters by opens, and returns cards keyed by turn ID.

**Call relations**: _transcript_aids uses it for settled turns, and _events uses it when a live terminal frame reports created apps.

*Call graph*: calls 1 internal fn (list_agents); called by 2 (_events, _transcript_aids).


##### `_events`  (lines 4084–4132)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str, email: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams live turn frames and adds web-specific events around terminal frames. It can announce subagent results, connection controls, credential prompts, files, created apps, and then the raw live frame.

**Data flow**: It tails the turn from an optional cursor, inspects each frame, performs extra reads on terminal frames, yields named events, and finally yields the frame converted by _sse.

**Call relations**: stream returns this async iterator to the browser as text/event-stream.

*Call graph*: calls 13 internal fn (connect_available, conversation_subagent_turns, shared_artifacts, tail, turn_detail, _connect_control, _created_apps, _event, _file_payload, _opens (+3 more)); called by 1 (stream); 2 external calls (web_audience, web_extension).


##### `fulfill_credential`  (lines 4135–4163)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores one credential value entered privately in the web UI. The secret is not admitted as a chat message and does not appear in transcripts.

**Data flow**: It authenticates, bounds and parses the form, validates sealed, slot, and value fields, enforces secret size, calls privileged credential fulfillment, and returns stored slot or an error.

**Call relations**: Credential prompt forms created by live streams or transcript handoffs post to this route.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `admin_index`  (lines 4166–4221)

```
async def admin_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the administration dashboard for workspace admins. It includes agents, installations, web grants, members, seats, spend caps, and deployment shape.

**Data flow**: It authenticates, rejects non-admins as not found, reads installations, grants, seat snapshot, spend caps, and deployment metadata, and returns JSON.

**Call relations**: The admin screen calls this; ordinary workspace reads use narrower routes like workspace_team and workspace_surfaces.

*Call graph*: calls 5 internal fn (list_installations, object_actions, spend_caps, _action_payloads, _audience_for); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


##### `_object_gate`  (lines 4224–4238)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

**Purpose**: Common gate for generic object index and detail pages. It authenticates, computes audience, and verifies that the requested object kind exists.

**Data flow**: It reads the kind path parameter, calls ctx.object_kind, and returns member ID, audience, and PortalKind or a response.

**Call relations**: object_index and object_detail both start here so unknown kinds and audience rules are consistent.

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 4241–4251)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

**Purpose**: Finds the agent namespace named by the object request’s agent query parameter. Object reads always happen inside one reachable agent namespace.

**Data flow**: It parses the agent query value as UUID, searches the audience’s agents, and returns the AgentSummary or a 404 response.

**Call relations**: object_index uses it for single-agent indexes, and object_detail uses it for detail reads.

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_action_payloads`  (lines 4254–4255)

```
def _action_payloads(views: tuple[ActionView, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts action view models into JSON dictionaries, omitting empty optional fields. These describe actions the UI may offer.

**Data flow**: It receives action views and returns model_dump dictionaries with None fields excluded.

**Call relations**: action_views, admin_index, workspace_credentials, workspace_first_run, workspace_memory, and workspace_team use it.

*Call graph*: called by 6 (action_views, admin_index, workspace_credentials, workspace_first_run, workspace_memory, workspace_team).


##### `_kind_payload`  (lines 4258–4265)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

**Purpose**: Builds common metadata for an object kind page, including list fields, schema, and whether apply/delete intents exist. This keeps object index and detail responses aligned.

**Data flow**: It receives a PortalKind, checks ApplyIntent support sets, and returns a dictionary of kind metadata.

**Call relations**: object_index and object_detail include it in their responses.

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 4268–4275)

```
def _filter_value(raw: str) -> JsonValue
```

**Purpose**: Parses query-string filter values into simple JSON scalars when possible. This lets filters like true or 3 behave as booleans or numbers instead of only strings.

**Data flow**: It receives a raw string, tries json.loads, and returns the parsed value or the original string.

**Call relations**: object_index calls it for all non-reserved query parameters.

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_fanout_token`  (lines 4278–4283)

```
def _fanout_token(walking: dict[str, str]) -> str | None
```

**Purpose**: Encodes per-agent cursors into one continuation token for object indexes that fan out across multiple agents. The browser treats it as opaque.

**Data flow**: It receives a map of agent ID strings to cursors, returns None if empty, or JSON-encodes and hex-encodes the map.

**Call relations**: object_index uses it after merging multi-agent pages.

*Call graph*: called by 1 (object_index); 1 external calls (dumps).


##### `_fanout_walks`  (lines 4286–4303)

```
def _fanout_walks(token: str) -> dict[UUID, str] | None
```

**Purpose**: Decodes a fan-out object index cursor back into per-agent cursors. It rejects tokens this route did not mint.

**Data flow**: It receives a token string, hex-decodes and JSON-parses it, validates non-empty string cursors and UUID agent keys, and returns a UUID map or None.

**Call relations**: object_index calls it when continuing a cross-agent object listing.

*Call graph*: called by 1 (object_index); 2 external calls (loads, UUID).


##### `_merged_rank`  (lines 4306–4319)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```

**Purpose**: Computes the sort key for one row in a merged multi-agent object listing. It gives consistent ordering across pages returned by different agents.

**Data flow**: It receives a row and order field, classifies the field as missing, boolean, number, or text, and returns a tuple with name as tie-breaker.

**Call relations**: object_index uses it when no single agent is named and rows must be merged together.


##### `object_index`  (lines 4322–4412)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists objects of one kind for the signed-in member, either within one agent or across all reachable agents. It supports search, filters, ordering, and cursors.

**Data flow**: It gates kind and audience, parses sorting and cursor options, builds ObjectListQuery, reads pages from one or many agents, merges and sorts when fanned out, and returns rows plus next cursor.

**Call relations**: The generic object index pages call this; it relies on _object_gate, _object_agent, _filter_value, _fanout_walks, _merged_rank, and _fanout_token.

*Call graph*: calls 7 internal fn (list_member_objects, _fanout_token, _fanout_walks, _filter_value, _kind_payload, _object_agent, _object_gate); 4 external calls (__init__, replace, JSONResponse, Response).


##### `object_detail`  (lines 4415–4462)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one object’s detail as the member may read it. It includes spec when visible, fields, links, generation, and timestamps.

**Data flow**: It gates kind and agent, reads the member object, checks each outgoing link for openability, and returns a detail JSON response or 404.

**Call relations**: The generic object detail page calls this; it shares kind metadata with object_index through _kind_payload.

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 4465–4497)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Converts one live hub frame into server-sent event bytes. It maps each frame type to the event name the browser expects.

**Data flow**: It receives a cursor and LiveFrame, optionally writes the SSE id line, serializes the frame payload, and returns event bytes.

**Call relations**: _events calls it for every raw live frame after adding any web-specific extra events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 4500–4505)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Submits a prepared intent for a selected agent. Prepared intents are panel-driven actions that are still admitted through the surface’s normal authority path.

**Data flow**: It gates the agent, then passes context, request, agent ID, member ID, and email to submit_intent.

**Call relations**: The route is a thin web gate around the intent logic in ufo_ext_web.panels.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `actions`  (lines 4508–4526)

```
async def actions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Submits an action offered by an object kind or object instance for a selected agent. The browser names the route; the server binds the real target.

**Data flow**: It gates the agent, reads kind/name/action path parameters, and delegates to submit_action with member and agent identity.

**Call relations**: Panels that show action buttons post here; submit_action performs the deeper dispatch.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_action).


##### `action_views`  (lines 4529–4543)

```
async def action_views(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists actions declared for an object collection or instance. It lets the UI draw available controls even when the actual dispatch will re-check authority later.

**Data flow**: It authenticates, validates object kind, chooses collection or instance binding from the path, reads ctx.object_actions, and returns action payloads.

**Call relations**: Object panels call this to discover controls; actions is the POST route that executes them.

*Call graph*: calls 4 internal fn (object_actions, object_kind, _action_payloads, _audience_for); 2 external calls (JSONResponse, Response).


##### `_write_agent`  (lines 4550–4568)

```
def _write_agent(request: Request, audience: WebAudience, stated: object=None) -> AgentSummary | Response
```

**Purpose**: Chooses the agent namespace for a direct object write. It uses an explicit body or query agent when present, otherwise falls back to the workspace main agent.

**Data flow**: It receives request, audience, and optional stated agent, parses a UUID when given, searches allowed agents, or returns the main agent or an error response.

**Call relations**: object_write uses it before admitting apply or delete intents.

*Call graph*: called by 1 (object_write); 2 external calls (Response, UUID).


##### `_direct_result`  (lines 4571–4575)

```
def _direct_result(frame: TerminalFrame, name: str) -> Response
```

**Purpose**: Turns the terminal result of a direct object-write turn into the synchronous response expected by an app frame. It reports success or refusal in a small JSON shape.

**Data flow**: It receives a terminal frame and object name, checks terminal status, and returns ok true with detail or ok false with an error reason.

**Call relations**: object_write calls it when the admitted write turn reaches a terminal frame.

*Call graph*: called by 1 (object_write); 1 external calls (JSONResponse).


##### `object_write`  (lines 4578–4652)

```
async def object_write(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets an app frame create, update, or delete an object under the member’s web session by admitting a prepared-intent turn. It waits briefly for the turn result and returns it synchronously.

**Data flow**: It authenticates, validates kind and body/path, chooses agent, builds an object_apply or object_delete ToolIntent, opens the intent conversation, admits the turn, tails it until terminal/parked/timeout, and returns JSON.

**Call relations**: Bridge clients post here; it uses _write_agent and _direct_result around the same turn machinery used by chat.

*Call graph*: calls 7 internal fn (admit, conversation_for, object_kind, tail, _audience_for, _direct_result, _write_agent); 7 external calls (__init__, timeout, dumps, conversation_audience, JSONResponse, json, Response).


##### `object_changes`  (lines 4658–4686)

```
async def object_changes(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a recent audit log of object create, update, and delete events for admins. It does not expose stored object specs.

**Data flow**: It authenticates, rejects non-admins, reads recent object changes, formats kind/name/verb/caller/agent/time, and returns JSON.

**Call relations**: The admin audit page calls this separately from object_index and object_detail.

*Call graph*: calls 2 internal fn (recent_object_changes, _audience_for); 2 external calls (JSONResponse, Response).


##### `settings`  (lines 4689–4701)

```
async def settings(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent’s settings page data. It also decides whether the current member may archive the agent.

**Data flow**: It gates the agent, finds the agent summary, computes archivable from main/admin/owner status, and delegates to agent_settings.

**Call relations**: The settings panel calls this; most formatting lives in ufo_ext_web.panels.agent_settings.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_settings).


##### `agent_setup`  (lines 4704–4734)

```
async def agent_setup(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns what a selected app still needs before it works: connectors, credentials, standing orders, and whether it already has its own page. Provider labels are made friendly here.

**Data flow**: It gates the agent, reads ctx.agent_setup, enriches connector rows with label and summary, checks _bound_page, and returns JSON.

**Call relations**: The app setup screen calls this, and agents_index uses related setup reads for boot badges.

*Call graph*: calls 5 internal fn (agent_setup, _bound_page, _panel_gate, _provider_label, _provider_summary); 1 external calls (JSONResponse).


##### `_bound_page`  (lines 4737–4764)

```
async def _bound_page(ctx: SurfaceContext, summary: AgentSummary, member_id: UUID) -> ObjectRow | None
```

**Purpose**: Finds the hosted site row that this workspace has built and bound as an agent’s homepage. It asks past normal row visibility because homepage state has its own agent-based gate.

**Data flow**: It queries site objects for homepage_agent equal to the agent ID, then returns the first row carrying site_url or None.

**Call relations**: agents_index, homepage, and agent_setup use it so homepage state and setup stand-on-page decisions agree.

*Call graph*: calls 1 internal fn (list_member_objects); called by 3 (agent_setup, agents_index, homepage); 1 external calls (__init__).


##### `homepage`  (lines 4767–4786)

```
async def homepage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent’s homepage state and URL if one is available. It covers both workspace-built pages and shipped app bundles.

**Data flow**: It gates the agent, publishes assets, reads the bound page, computes state with _homepage_state, and returns JSON.

**Call relations**: The portal polls or reads this route after boot; agents_index includes the same homepage state initially.

*Call graph*: calls 5 internal fn (_assets_published, _bound_page, _homepage_state, _panel_gate, apps); 1 external calls (JSONResponse).


##### `_homepage_state`  (lines 4789–4836)

```
def _homepage_state(ctx: SurfaceContext, summary: AgentSummary, bound: ObjectRow | None, admin: bool, member_id: UUID) -> dict[str, JsonValue]
```

**Purpose**: Computes the public homepage state for one agent. It returns set with a URL and generation for bound or shipped pages, or none when no page is available to this viewer.

**Data flow**: It receives context, agent summary, bound page, admin flag, and member ID; applies private-agent visibility rules, builds hosted-site or shipped-bundle URLs, and returns a small state dictionary.

**Call relations**: agents_index and homepage call it after obtaining the bound page and shipped apps bundle.

*Call graph*: calls 1 internal fn (apps); called by 2 (agents_index, homepage); 3 external calls (shipped_app_slug, homepage_embed_url, shipped_homepage_url).


##### `preview`  (lines 4854–4885)

```
async def preview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders an uploaded document file into a PNG preview for the composer before it is sent. It stores nothing and admits no turn.

**Data flow**: It authenticates, requires multipart form data, bounds and parses the form, chooses the first file, maps its suffix to a preview kind, calls ctx.render_preview, and returns PNG or an error.

**Call relations**: The attachment composer calls this for local preview display before chat submission.

*Call graph*: calls 4 internal fn (render_preview, _audience_for, _form, _framed_length); 2 external calls (PurePosixPath, Response).


##### `upload_start`  (lines 4888–4924)

```
async def upload_start(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Creates a presigned upload URL so large attachments can go straight from the browser to blob storage. The later chat send only references the uploaded key.

**Data flow**: It authenticates, reads a bounded JSON body with size, checksum, and name, validates size, creates a safe upload key, asks the blob store for a presigned PUT URL, and returns key plus URL.

**Call relations**: The browser calls this before uploading attachment bytes; _parse_inbound later accepts the returned key in a chat send.

*Call graph*: calls 2 internal fn (_audience_for, _bounded_body); 5 external calls (loads, JSONResponse, Response, inbox_name, uuid4).
