# Artifacts, Files, Previews, and Hosted Media  `stage-13.1`

This stage is shared support for anything the system saves, shows, or serves as media from a conversation. It is not the main thinking loop. It is the backstage machinery that turns files and hosted sandbox sites into safe links, previews, and downloadable artifacts.

The artifact code defines a shared file object: it can be listed, inspected, deleted, copied back into a workspace, or downloaded. The download route serves the actual bytes only when a signed link proves access, and can help signed-in teammates refresh old links. Image preview code checks that preview files are real, safe-sized images before showing them, while the preview data model records where a preview lives and its dimensions.

For hosted sites, the ingress host code creates special per-conversation web addresses so each site is isolated, like giving every exhibit its own locked room. The ingress server checks signed access, then serves stored site files or forwards live requests to the sandbox. The site previewer asks an outside screenshot service to capture a PNG of a live site and saves it as an artifact. The share card code combines UFO branding with that screenshot for social previews.

## Files in this stage

### Hosted Site Access
These files establish isolated hosted-site addresses and serve public sandbox traffic through signed, site-specific sessions.

### `core/src/ufo/harness/sandbox/ingress_host.py`

`domain_logic` · `request handling and sandbox URL creation`

A browser treats different hostnames as different places. This file uses that fact as a safety boundary: every hosted sandbox site gets its own short DNS label, like a room number on a large building. The label encodes two pieces of information: the conversation ID and the sandbox port. It also includes a short cryptographic signature, which is a tamper-evident stamp made with the deployment secret.

The label is not the main permission check. A user still needs a valid token or session cookie elsewhere. Its job is narrower: if someone guesses or changes a hostname, the system can reject it before looking up conversation data or connecting to a sandbox. The file also insists on one canonical spelling for each label, because base32 encoding can otherwise allow multiple text labels to decode to the same bytes, and browsers would treat those spellings as separate origins with separate cookies.

The helper functions divide the work clearly. Some derive stable identities, such as the port for a conversation or the synthetic ID for a shipped app page. Others turn raw bytes into a DNS-safe base32 label, sign the address bytes, or parse and verify a label coming back from a request. Together, they make sandbox origins stable across redeploys while still refusing malformed, forged, or non-canonical hostnames.

#### Function details

##### `serve_port`  (lines 57–63)

```
def serve_port(conversation_id: UUID) -> int
```

**Purpose**: This function picks the stable sandbox port for a conversation. It means the same conversation will keep getting the same port without storing a separate port value in a database.

**Data flow**: It receives a conversation UUID. It turns the UUID into a large number, folds that number into the configured application-port range, adds the starting port, and returns the resulting port number.

**Call relations**: Other code can call this when it needs to build or recognize the address for a conversation's hosted site. It does not call any project helpers; it is the shared rule that keeps all producers of sandbox addresses in agreement.


##### `shipped_app_slug`  (lines 66–74)

```
def shipped_app_slug(provisioned_by: str | None) -> str | None
```

**Purpose**: This function extracts the stable page slug from the name of a provisioned shipped app. It avoids relying on a member-visible name that might change or get a suffix if there is a naming collision.

**Data flow**: It receives a provision source string, or nothing. If there is no string, it returns nothing. If the string exactly matches the expected form, such as an app prefix followed by lowercase letters or digits, it returns just the slug part. Otherwise it returns nothing.

**Call relations**: Code that builds origins or bundle paths for shipped app pages can use this to find the app's stable identity. It stands alone and does not hand work to other functions in this file.


##### `shipped_anchor`  (lines 77–82)

```
def shipped_anchor(workspace_id: UUID, slug: str) -> UUID
```

**Purpose**: This function creates a stable synthetic UUID for a shipped app page inside one workspace. It gives a shipped page its own browser storage and cookies even though it is not backed by a normal conversation row.

**Data flow**: It receives a workspace UUID and an app slug. It combines them with a fixed label and feeds that text into UUID version 5, which creates the same UUID every time for the same input, then returns that UUID.

**Call relations**: When a shipped app needs an origin-like identity, this function supplies it. It delegates the deterministic UUID creation to the standard uuid5 function.

*Call graph*: 1 external calls (uuid5).


##### `site_label`  (lines 85–90)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: This function turns a conversation ID and port into the DNS-safe label used in a sandbox hostname. It is used when the system needs to mint the official address for a hosted site.

**Data flow**: It receives a conversation UUID and a port number. First it rejects ports outside the valid network range. Then it packs the UUID bytes and the two-byte port into an address, creates a short signature for that address, appends the signature, encodes everything as lowercase base32 text, and returns that label.

**Call relations**: This is the label-making side of the flow. It calls _signature to add the tamper-evident stamp and _encode to turn the bytes into DNS-friendly text. Later, parse_site_label is the matching reader that checks labels made this way.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 93–105)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: This function reads a sandbox DNS label and proves it is well-formed, canonical, and signed by this deployment. If the label passes, it reveals which conversation and port it points to.

**Data flow**: It receives the label text from a hostname. It base32-decodes the text, allowing DNS-style case differences. It then re-encodes the bytes and compares that with the lowercase input to reject alternate spellings. Next it splits the decoded bytes into the address and signature, recomputes the expected signature, and compares the two safely. If anything is wrong, it raises SiteLabelError. If everything is right, it returns the UUID and port from the address bytes.

**Call relations**: This is the request-side counterpart to site_label. Incoming hostnames pass through it before the system trusts the embedded conversation and port. It calls _encode to enforce the one true spelling, _signature to verify the stamp, and standard library helpers for decoding, safe signature comparison, and UUID reconstruction.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 108–109)

```
def _encode(raw: bytes) -> str
```

**Purpose**: This small helper turns raw bytes into the lowercase base32 text used in DNS labels. Base32 is used because it produces hostname-friendly characters.

**Data flow**: It receives bytes. It base32-encodes them, converts the result to normal text, removes padding equals signs, lowercases the label, and returns that string.

**Call relations**: site_label uses it when creating labels, and parse_site_label uses it to check that an incoming label is written in the single accepted canonical form. It delegates the actual base32 conversion to Python's base64 library.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 112–114)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: This helper creates the short cryptographic stamp attached to each site label. The stamp lets the system detect guessed or altered labels before treating them as real addresses.

**Data flow**: It receives the address bytes, made from the conversation ID and port. It reads the deployment ingress secret, combines that secret with a fixed label kind and the address, computes an HMAC using SHA-256, keeps only the first four bytes, and returns those bytes.

**Call relations**: site_label calls it to stamp a newly created label. parse_site_label calls it again to recompute what the stamp should be and compare it with the stamp found in the incoming label. It relies on ingress_secret for the shared deployment secret and hmac.new for the cryptographic calculation.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/harness/sandbox/ingress_serve.py`

`entrypoint` · `startup, then HTTP and WebSocket request handling`

Think of this file as the guarded front desk for every sandbox-hosted web site. Each site gets its own signed hostname, and every request must prove, with a session cookie, that it is allowed to enter that exact site. Without this layer, one workspace could accidentally see another site's bytes, browsers or shared caches could keep private site files, and a site could steal or overwrite cookies that belong to the platform.

The main class, IngressServe, builds a FastAPI web app. Special view links first land on a token path, where a short-lived signed token is exchanged for a host-only session cookie. After that, normal HTTP requests go through a shared authorization check. If the site was published as static files, the file is streamed from blob storage. If it is still live inside a sandbox, the request is streamed to the sandbox port and the response is streamed back.

The file is careful about browser safety. It strips unsafe cache headers, controls who may frame the site, removes platform cookies before forwarding requests, confines site-set cookies to their own hostname, and rewrites security policy headers only where needed. It also supports WebSockets, but only after checking the browser's Origin header so one site cannot open a live socket into another site.

#### Function details

##### `IngressServe.app`  (lines 300–332)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI application and attaches all HTTP and WebSocket routes. This is where the ingress server decides which paths are special token-opening paths and which paths should be treated as ordinary site traffic.

**Data flow**: It starts with the IngressServe instance and creates a web application. It registers token routes, catch-all proxy routes, and WebSocket routes. The result is a configured FastAPI app ready for Uvicorn to run.

**Call relations**: The startup function run creates an IngressServe and hands this app to Uvicorn. Later, incoming requests are dispatched from these routes into _no_view_token, _open, _proxy, _no_socket_view, or _socket.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 334–340)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers visits to the token-opening path when no token was supplied. It prevents an empty or malformed link from being treated as a real site-opening link.

**Data flow**: It receives an HTTP request. If the method is not GET or HEAD, it returns a 405 response listing the allowed methods. Otherwise it returns a 403 response saying the link is not valid.

**Call relations**: IngressServe.app routes the bare view path here. This keeps malformed token requests away from _open and away from the sandbox catch-all proxy.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 342–395)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a signed view token into a short-lived session cookie for the site hostname. This is the bridge from a portal-generated site link to ordinary browser requests for that site.

**Data flow**: It receives the request and the path after the token prefix. It splits out the token, reads the site identity from the Host header, verifies the token, checks that the token matches that exact site, optionally checks that a framing sibling site belongs to the same workspace, then mints a session token and returns a redirect to the requested site path with a session cookie set.

**Call relations**: IngressServe.app sends token URLs here. It calls _site to identify the addressed site, verify_ingress_token to prove the link, _framer_belongs when the token names a sibling framer, and mint_ingress_token to create the session used later by _authorized.

*Call graph*: calls 2 internal fn (_framer_belongs, _site); 9 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, cookie_secure, set_session_cookie, quote).


##### `IngressServe._site`  (lines 397–408)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Reads the requested site identity from the hostname. The hostname label encodes which conversation and port the visitor is trying to reach.

**Data flow**: It takes an HTTP or WebSocket connection, reads its hostname, checks that it is under the configured base host, and parses the remaining label. It returns the conversation id and port, or None if the hostname does not belong to this ingress.

**Call relations**: _open uses this to make sure a view token is being redeemed at the right site address. _authorized uses it as the first step of the common access gate for both HTTP and WebSocket traffic.

*Call graph*: called by 2 (_authorized, _open); 1 external calls (parse_site_label).


##### `IngressServe._authorized`  (lines 410–432)

```
def _authorized(self, connection: HTTPConnection) -> IngressClaims | SiteRefusal
```

**Purpose**: Performs the shared access check before any site content or live socket is reached. It makes sure the session cookie is valid and belongs to the exact site named by the Host header.

**Data flow**: It receives an HTTP or WebSocket connection. It reads the site identity with _site, verifies the ingress session cookie, compares the cookie claims to the hostname's conversation and port, and returns either the verified claims or a SiteRefusal explaining why access is denied.

**Call relations**: _proxy calls this before serving files or forwarding HTTP. _socket calls it before opening a WebSocket to the site's server, so both protocols use the same gate.

*Call graph*: calls 1 internal fn (_site); called by 2 (_proxy, _socket); 3 external calls (__init__, now, verify_ingress_token).


##### `IngressServe._stored_manifest`  (lines 434–472)

```
async def _stored_manifest(self, claims: IngressClaims) -> dict[str, StoredFile] | None
```

**Purpose**: Finds out whether the authorized site is a stored static site and, if so, describes its files. If no stored manifest exists, the site is treated as live and must be dialed in its sandbox.

**Data flow**: It takes verified ingress claims. For shipped app content, it delegates to _shipped_manifest. Otherwise it queries the hosted_site table for the workspace, conversation, and port, parses the stored JSON manifest, and returns a dictionary from site paths to StoredFile records. It returns None when the site is not stored.

**Call relations**: _proxy uses this to choose between serving from blob storage and dialing a live sandbox. _socket uses it to refuse WebSockets for static stored sites. It calls _shipped_manifest for deploy-wide shipped app bundles.

*Call graph*: calls 1 internal fn (_shipped_manifest); called by 2 (_proxy, _socket); 4 external calls (__init__, loads, select, workspace_tx).


##### `IngressServe._shipped_manifest`  (lines 474–506)

```
async def _shipped_manifest(self, shipped: ShippedClaim) -> dict[str, StoredFile] | None
```

**Purpose**: Builds or reuses a file list for a shipped platform app bundle stored in the fleet blob store. These bundles are deploy-wide code, not per-workspace sandbox files.

**Data flow**: It receives a shipped claim containing a digest and slug. It first checks an in-memory cache. If missing, it lists blob entries under the digest, turns each entry into a StoredFile with guessed media type and digest-based etag, stores the result in the cache, and returns it. If no files exist for the digest, it returns None.

**Call relations**: _stored_manifest calls this when a session claim points at shipped app content. _serve_stored later uses the returned StoredFile keys to stream the actual bytes.

*Call graph*: called by 1 (_stored_manifest); 3 external calls (__init__, __init__, guess_type).


##### `IngressServe._dial_site`  (lines 508–538)

```
async def _dial_site(self, claims: IngressClaims) -> DialTarget | SiteRefusal
```

**Purpose**: Finds the live network address for a sandbox site that is not served from stored files. It turns a conversation and port into something the proxy or WebSocket relay can connect to.

**Data flow**: It receives verified claims. Inside the correct workspace context, it reads the stored sandbox handle, chooses the right carrier backend, builds a SandboxHandle, and asks the carrier to dial the requested port. It returns a DialTarget on success or a SiteRefusal if the sandbox is gone or unreachable.

**Call relations**: _proxy calls this before forwarding live HTTP requests. _socket calls it before opening a live WebSocket. It relies on _stored_handle and the carrier abstraction to hide whether the sandbox is local, docker-based, or resumed from another backend.

*Call graph*: calls 1 internal fn (_stored_handle); called by 2 (_proxy, _socket); 6 external calls (__init__, __init__, warn, sandbox_handle_backend, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 540–606)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Handles ordinary HTTP traffic for a hosted site. It is the central HTTP path: authorize, decide stored versus live, then stream the right response back safely.

**Data flow**: It receives a request and site path. It checks authorization, looks for a stored manifest, serves stored files when present, refuses missing shipped bundles, or dials the live sandbox. For live traffic, it builds an upstream URL and sanitized headers, streams the request body to the site, receives the upstream response, strips or rewrites unsafe headers, confines cookies, sets no-cache and framing protections, and streams the body back.

**Call relations**: IngressServe.app routes all normal HTTP paths here. It coordinates _authorized, _stored_manifest, _serve_stored, _dial_site, _upstream_url, _upstream_headers, _body, _frame_ancestors, _unframed_policy, and _confined_cookie.

*Call graph*: calls 10 internal fn (_authorized, _body, _confined_cookie, _dial_site, _frame_ancestors, _serve_stored, _stored_manifest, _unframed_policy, _upstream_headers, _upstream_url); 7 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, ws).


##### `IngressServe._serve_stored`  (lines 608–674)

```
async def _serve_stored(self, request: Request, claims: IngressClaims, files: dict[str, StoredFile], path: str) -> Response
```

**Purpose**: Serves one file from a stored static site or shipped app bundle. This lets published site bytes keep working even when the sandbox that built them is no longer running.

**Data flow**: It receives the request, verified claims, a manifest of stored files, and a path. It allows only GET and HEAD, maps the path to either the named file or an index.html file, sets cache, etag, content type, and framing headers, handles browser revalidation with 304, and streams the blob bytes from the fleet or workspace blob store. Missing files or missing blobs become clean 404 responses.

**Call relations**: _proxy calls this after _stored_manifest says the site has stored files. It calls _frame_ancestors to set the framing rule and _stored_body to stream bytes after the first chunk has already proved the blob exists.

*Call graph*: calls 2 internal fn (_frame_ancestors, _stored_body); called by 1 (_proxy); 5 external calls (__init__, __init__, Response, StreamingResponse, ws).


##### `IngressServe._stored_body`  (lines 676–682)

```
async def _stored_body(self, first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Streams the bytes of a stored file after the first chunk has already been read. Reading the first chunk early lets the caller return a clean 404 if the blob vanished before sending a 200 response.

**Data flow**: It receives the first bytes and an async iterator for the remaining bytes. It yields the first chunk, then yields each later chunk as it arrives. It produces a byte stream for the response.

**Call relations**: _serve_stored calls this when returning a StreamingResponse for a blob-backed file.

*Call graph*: called by 1 (_serve_stored).


##### `IngressServe._framer_belongs`  (lines 684–719)

```
async def _framer_belongs(self, workspace_id: UUID, conversation_id: UUID, port: int) -> bool
```

**Purpose**: Checks whether a sibling site named in a view token really belongs to the same workspace. This prevents a token from allowing an unrelated site to frame and use another site's session.

**Data flow**: It receives a workspace id, conversation id, and port. It queries the hosted_site table for a matching site. If none is found, it checks active agent provisions for shipped app identities and compares their generated anchor and serve port. It returns true only when the framer is recognized in that workspace.

**Call relations**: _open calls this when a token includes a framer claim. Its answer affects whether _open mints a session cookie or rejects the link.

*Call graph*: called by 1 (_open); 5 external calls (select, workspace_tx, serve_port, shipped_anchor, shipped_app_slug).


##### `IngressServe._frame_ancestors`  (lines 721–729)

```
def _frame_ancestors(self, claims: IngressClaims) -> str
```

**Purpose**: Builds the browser security rule that says which page is allowed to embed this site in a frame. A frame is an embedded page, like a document shown inside another page.

**Data flow**: It receives verified claims. If framing is globally disabled or no sibling framer is named, it returns the configured app origin or 'none'. If a framer is present, it adds that exact sibling site origin to the allowed list.

**Call relations**: _proxy and _serve_stored call this when setting Content-Security-Policy response headers. It uses site_label to turn a conversation and port back into the site hostname label.

*Call graph*: called by 2 (_proxy, _serve_stored); 1 external calls (site_label).


##### `IngressServe._upstream_url`  (lines 731–734)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the exact URL used to contact a live sandbox server. It preserves the requested path and query string while safely escaping path characters.

**Data flow**: It receives a scheme, host, path, and raw query string. It quotes the path, appends the query if present, and returns a full URL string.

**Call relations**: _proxy uses this for live HTTP forwarding. _socket uses it for live WebSocket forwarding.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 736–746)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Looks up the saved sandbox handle for a conversation in a workspace. The handle is what lets the carrier find the running or resumable sandbox container.

**Data flow**: It receives a workspace id and conversation id. It queries the conversation table for the matching row and returns its sandbox_handle value, or None if no row exists.

**Call relations**: _dial_site calls this before asking a carrier to connect to a sandbox port.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 748–782)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Creates the safe set of request headers to send to the sandbox site. It removes platform secrets and protocol-only headers while preserving the visitor's real site headers.

**Data flow**: It receives the incoming connection and extra headers from the dial target. It drops hop-by-hop headers, Host, WebSocket handshake headers, and headers overridden by the dial target. It removes the ingress session cookie from Cookie headers, keeps the site's own cookies, and appends the dial target headers. The result is a list of header name-value pairs for the upstream request.

**Call relations**: _proxy uses this for live HTTP requests. _socket uses it for WebSocket handshakes to the upstream site.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 784–796)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes only the frame-ancestors rule from a site's Content-Security-Policy header. This keeps the site's other browser protections while letting core decide framing.

**Data flow**: It receives one policy string. It splits it into directives, filters out any directive named frame-ancestors, rejoins the rest, and returns the rewritten policy. If nothing remains, it returns an empty string.

**Call relations**: _proxy calls this while copying headers from a live sandbox response. The proxy then adds its own framing policy separately.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 798–825)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Sanitizes one Set-Cookie header from a site before passing it to the browser. It prevents the site from setting platform-reserved cookies or cookies that escape to parent domains.

**Data flow**: It receives a raw Set-Cookie header. It reads the cookie name, drops the whole cookie if the name is missing, malformed, or starts with the reserved ufo_ prefix, removes any Domain attribute, and returns the safer header. It returns None when the cookie should not be forwarded.

**Call relations**: _proxy calls this for every Set-Cookie header on a live sandbox response before appending it to the visitor-facing response.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 827–837)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw response bytes from a live sandbox and makes sure the upstream response is closed. This prevents connection leaks when a stream ends normally or fails partway through.

**Data flow**: It receives an httpx response from the upstream site. It yields each raw byte chunk to the outgoing response. In all cases, including errors, it closes the upstream response afterward.

**Call relations**: _proxy uses this as the body source for StreamingResponse when forwarding live HTTP responses.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 839–845)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Rejects WebSocket attempts to the token-opening path. View tokens are meant to be exchanged over HTTP only, not exposed to sandbox code through a socket route.

**Data flow**: It receives a WebSocket handshake and immediately sends a denial response saying the link is not valid.

**Call relations**: IngressServe.app routes WebSocket handshakes on the view path here. It uses _refuse so the denial looks like the same kind of access failure used elsewhere.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 847–903)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Relays a WebSocket between the browser and a live sandbox site. This supports site features such as live reload or push messages while keeping the same authorization boundary as HTTP.

**Data flow**: It receives a WebSocket and path. It first checks that the Origin header matches the addressed site, then authorizes the session, refuses static stored sites, dials the live sandbox, opens an upstream WebSocket with sanitized headers and offered subprotocols, accepts the browser socket using the upstream-selected subprotocol, and relays messages both ways. On failures, it sends a controlled close where possible.

**Call relations**: IngressServe.app routes catch-all WebSocket paths here. It coordinates _same_origin, _authorized, _stored_manifest, _dial_site, _upstream_url, _upstream_headers, _refuse, _relay, and _end.

*Call graph*: calls 9 internal fn (_authorized, _dial_site, _end, _refuse, _relay, _same_origin, _stored_manifest, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 905–917)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site hostname it is trying to connect to. This closes a browser loophole where WebSocket handshakes are not protected by normal cross-origin read rules.

**Data flow**: It receives a WebSocket handshake. It reads the Origin header, parses its hostname, compares it with the requested hostname, and returns true only when they match.

**Call relations**: _socket calls this before doing any authorization or dialing. A failed check is refused before the sandbox sees anything.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 919–926)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Turns a SiteRefusal into a WebSocket handshake denial response. This lets WebSocket failures return a real status code and message instead of a vague closed connection.

**Data flow**: It receives a WebSocket and a refusal object. It builds an HTTP response from the refusal's message, status, and media type, then sends it as the handshake denial.

**Call relations**: _no_socket_view and _socket use this whenever a WebSocket should not be accepted.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 928–945)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs both halves of a WebSocket relay at the same time. If either the browser or the site stops, it stops the other half too.

**Data flow**: It receives the accepted browser WebSocket and the upstream site connection. It starts one task for browser-to-site messages and one for site-to-browser messages, waits until one finishes, cancels the other, and re-raises any real failure from the completed task.

**Call relations**: _socket calls this after both WebSocket connections are open. It delegates the actual message copying to _viewer_to_site and _site_to_viewer.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 947–956)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the sandbox site. It preserves whether each message is text or binary because that can matter to the site's protocol.

**Data flow**: It repeatedly reads messages from the browser WebSocket. If the browser disconnected, it returns. Otherwise it sends either the text value or the byte value to the upstream site connection.

**Call relations**: _relay runs this as one of the two simultaneous relay tasks.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 958–971)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the sandbox site back to the browser and then closes the browser side with an appropriate close code. It avoids sending protocol-reserved close codes that are illegal on the wire.

**Data flow**: It reads messages from the upstream site connection. Text messages are sent as text and binary messages as bytes. When the upstream stream ends, it chooses a close code and reason, substitutes a safe code when needed, and asks _end to close the browser socket.

**Call relations**: _relay runs this as the site-to-browser task. It calls _end for the final close.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 973–983)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Closes the browser WebSocket without letting close-time errors hide the original problem. This is a cleanup helper for messy disconnect situations.

**Data flow**: It receives a WebSocket, close code, and reason. It attempts to close the socket and suppresses any exception, because the connection may already be gone.

**Call relations**: _site_to_viewer uses this after the upstream site closes. _socket also uses it after relay failures to report that the upstream is gone.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 986–997)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the wildcard base hostname that all hosted site names must live under. It refuses to start if this critical public URL is missing or unusable.

**Data flow**: It receives the configured ingress public URL. It parses the hostname and returns it. If no hostname is present, it raises an error explaining the required setting.

**Call relations**: run calls this during startup before the server begins accepting traffic. The result is stored on IngressServe and later used by _site.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 1000–1010)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Computes the main app origin that hosted sites may be embedded by. If no public app base URL is configured, it returns a rule that allows no framing.

**Data flow**: It receives the configured public base URL for the app. It parses the scheme, hostname, and optional port. If they are valid, it returns an origin string; otherwise it returns the no-framing value.

**Call relations**: run calls this during startup and passes the result into IngressServe. _frame_ancestors uses that configured value on each response.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 1013–1036)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to contact live sandbox sites. The client is deliberately cookie-blind so cookies from one site cannot be stored and replayed to another.

**Data flow**: It creates an httpx AsyncClient with bounded timeouts and connection limits. It gives the client a cookie jar whose policy accepts no domains, so the client does not retain site cookies. The configured client is returned.

**Call relations**: run calls this once at startup and passes the client into IngressServe. _proxy later uses that client to send live HTTP requests upstream.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 1039–1066)

```
def run() -> None
```

**Purpose**: Starts the ingress server process. It loads configuration, prepares dependencies, builds IngressServe, and hands its app to Uvicorn.

**Data flow**: It loads config, initializes observability and the database, verifies the database is reachable, checks the ingress signing secret, loads extension manifests, selects sandbox carriers, creates the HTTP client and blob store, constructs IngressServe, logs startup, and starts Uvicorn on the configured port.

**Call relations**: This is the file's process entry point. It calls ingress_base_host, ingress_frame_ancestor, upstream_client, and the wider project setup functions before Uvicorn begins routing requests into IngressServe.app.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 14 external calls (__init__, run, blob_store_for, load_config, init_db, verify_db_reachable, init_o11y, log, ingress_secret, select_carriers (+4 more)).


### Hosted Preview Media
These files capture hosted-site screenshots, describe stored preview images, and build custom share-card artwork for public links.

### `core/src/ufo/runtime/media/site_previewer.py`

`io_transport` · `request handling / preview generation`

When a conversation starts a web app in a sandbox, the system may want a visual preview of it, like a thumbnail of a website. This file provides that job through the `SitePreviewer` class. It builds a temporary public viewing URL for the sandbox port, sends that URL to a separate service called `ufo-preview`, and expects back a PNG image or metadata saying the image was already uploaded.

The file is careful because it is dealing with outside systems. It checks that the requested filename is safe, that the requested image size is reasonable, that the preview service does not send too much data, and that the result is really a PNG of the expected dimensions. If the workspace uses S3 storage, it gives the preview service a temporary upload link so the service can write the image directly to blob storage. If not, it asks the service to return the PNG bytes inline, then this code uploads them itself.

If anything goes wrong, such as the sandbox URL cannot be made, the preview service errors, the response is too large, or the image is not valid, it logs a short diagnostic message and returns `None`. This keeps preview failures from breaking the larger conversation flow.

#### Function details

##### `SitePreviewer.render`  (lines 47–124)

```
async def render(self, conversation_id: UUID, port: int, name: str, width: int, height: int) -> StoredPreview | None
```

**Purpose**: This method creates one screenshot preview for a sandboxed web page and stores it as a blob artifact. A caller uses it when they have a conversation ID, a sandbox port, a desired filename, and target image dimensions, and they want a saved PNG preview if one can be produced safely.

**Data flow**: It receives a conversation ID, port, preview name, width, and height. First it rejects unsafe names and unreasonable dimensions. It then uses the current workspace and conversation details to create a public sandbox URL. Next it chooses where the preview service should put the result: either directly into S3 using a temporary upload URL, or back in the HTTP response as raw PNG bytes. It sends a render request to the preview service, reads the response with a size limit, verifies success, checks the returned metadata or PNG bytes, and finally returns a `StoredPreview` pointing at the saved blob. If a network problem or validation problem happens, it logs the failure and returns `None` instead of raising.

**Call relations**: This is the main action provided by the file. During its work it calls `ws_current` to learn the active workspace, `mint_ingress_view_url` to make the sandbox page reachable by the preview service, `json.dumps` to package the render request, and `httpx.AsyncClient` with an `httpx.Timeout` to contact the external preview service. When storage is S3-backed, it relies on the blob store to make a temporary upload URL and then builds a `StoredPreview` from the returned metadata. When storage is not S3-backed, it uploads the returned PNG bytes itself before creating the `StoredPreview`. On failure, it hands a short error record to the logging function so operators can see why no preview was drawn.

*Call graph*: 9 external calls (__init__, AsyncClient, Timeout, dumps, PurePosixPath, log, mint_ingress_view_url, ws_current, uuid4).


### `core/src/ufo/runtime/media/previews.py`

`data_model` · `cross-cutting`

This file is a simple model for one piece of media information: a saved preview image. When the system stores a picture preview, other parts of the code need a reliable way to pass around two facts about it: its blob key, which is the workspace-relative storage name or path used to find the saved bytes later, and its exact size in bytes. The `StoredPreview` class packages those two facts together.

It is marked as a dataclass, which means Python automatically creates the usual boilerplate for a plain data container, such as initialization and readable representation. It is also frozen, meaning once a `StoredPreview` is created, its fields cannot be changed. That matters because it makes the object behave like a receipt: after storage is complete, the record of what was stored should not accidentally drift or be edited in place.

Without this file, code that saves or reports preview images would likely pass around loose strings and numbers, making it easier to mix up fields or lose track of what each value means.


### `extensions/sites/ufo_ext_sites/share_card.py`

`domain_logic` · `site deploy and share-card refresh`

A shared link often gets “unfurled”: the app fetches a title, description, and preview image to show a card. This file creates that preview image for hosted sites. Without it, every site would either keep using a generic brand card or have no freshly generated share image when its page changes.

The card has a fixed size: a dark branded panel on the left and the site’s own front page on the right. The file first takes a screenshot of the site using Chrome or Chromium running inside the same sandbox as the site. A sandbox is an isolated workspace, like a small sealed room, where the browser can see the local site but the outside system does not need direct access. The screenshot waits briefly for the page to finish drawing, rather than trusting the browser’s first “loaded” signal, because modern pages may still be animating or filling in content.

Then the file builds a small HTML page that represents the final card: logo, text, site name, and the screenshot inserted as an in-page image. It asks the browser to photograph that HTML page too. Finally, it uses Pillow, an image library, only to encode the finished picture as a progressive JPEG and compute a digest, which is a fingerprint used to name or identify the image.

The important safety rule is: failure is non-fatal. If screenshotting, composing, encoding, or storing fails, the problem is logged and the site keeps whatever card it already had.

#### Function details

##### `card_page`  (lines 486–511)

```
def card_page(name: str, drawn: str) -> str
```

**Purpose**: Builds the HTML page that will become the final share-card image. It combines the embedded font, the UFO logo, the fixed card layout, the site name, and a placeholder where the site screenshot will later be inserted.

**Data flow**: It receives the site name and a small CSS sizing rule that says how the screenshot should be drawn. It reads the local font file and logo asset, escapes the site name so it is safe to put into HTML, fills the card template, and returns a complete HTML document as text.

**Call relations**: This is used during composition, when _compose needs a browser-drawable version of the card. It calls _lockup to get the logo markup, then hands the finished HTML back to _compose so the sandbox can write it to a file and later screenshot it.

*Call graph*: calls 1 internal fn (_lockup); called by 1 (_compose); 2 external calls (b64encode, escape).


##### `_lockup`  (lines 514–518)

```
def _lockup() -> str
```

**Purpose**: Reads the UFO logo SVG and returns only the actual SVG markup that can be embedded inside another HTML page. This avoids putting a full standalone SVG document inside the card page.

**Data flow**: It reads the logo asset from disk as text, finds where the <svg> element begins, and returns the text from that point onward. Nothing outside the function is changed.

**Call relations**: card_page calls this while building the share-card HTML. Its output becomes the branded logo shown in the left panel of the card.

*Call graph*: called by 1 (card_page).


##### `shot_command`  (lines 521–541)

```
def shot_command(*, url: str, width: int, height: int, scale: int, shot: str, root: str) -> str
```

**Purpose**: Creates the shell command that will run inside the sandbox to take a browser screenshot. It packages the browser-driving Python program, screenshot size, target URL, output path, and safety settings into one command string.

**Data flow**: It receives the page URL, viewport width and height, device scale, destination screenshot path, and sandbox root. It quotes paths and URLs so the shell reads them safely, inserts the browser driver script and constants, and returns a command ready for the sandbox to execute.

**Call relations**: _shoot calls this whenever it needs a screenshot, whether that screenshot is of the live site or of the composed card HTML. The returned command is then passed to the sandbox’s bash runner.

*Call graph*: called by 1 (_shoot); 2 external calls (quote, shell_path).


##### `draw_from_page`  (lines 544–562)

```
async def draw_from_page(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, port: int) -> None
```

**Purpose**: Creates a share card from a freshly deployed site by first photographing the site’s own front page. This is the normal path for a new deployment where the site is running locally in the sandbox.

**Data flow**: It receives the tool context, site store, conversation ID, site name, and local port where the site is being served. It chooses a runtime file path for the screenshot, asks _shoot to capture the local URL, and if that succeeds passes the screenshot to _compose. It returns no value; the visible result is a stored share-card record if all steps succeed.

**Call relations**: This is a higher-level entry into the share-card flow. It first depends on _shoot to capture the live page, then hands off to _compose to build, encode, store, and record the final card.

*Call graph*: calls 2 internal fn (_compose, _shoot).


##### `draw_from_stored_shot`  (lines 565–580)

```
async def draw_from_stored_shot(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, blob_key: str) -> None
```

**Purpose**: Creates a share card for an older site that already has a stored page screenshot but does not yet have a custom card. It lets existing sites get the new card format without needing a fresh live page screenshot.

**Data flow**: It receives the tool context, site store, conversation ID, site name, and blob key for the old stored screenshot. It downloads that screenshot from blob storage, writes it into the sandbox, and then asks _compose to build the card from it. If copying the screenshot into the sandbox fails, it logs the problem and stops.

**Call relations**: This is the backfill path for sites deployed before share cards existed. It skips _shoot for the site page because the page picture already exists, but still uses _compose because the final card is drawn by the sandbox browser.

*Call graph*: calls 2 internal fn (_compose, _undrawn).


##### `_shoot`  (lines 583–612)

```
async def _shoot(ctx: ToolContext, name: str, shot: str, *, url: str, width: int, height: int, scale: int) -> bool
```

**Purpose**: Takes one screenshot inside the sandbox and reports whether it succeeded. It is used both for photographing the hosted site and for photographing the final card HTML.

**Data flow**: It receives the tool context, site name, output screenshot path, target URL or file path, viewport size, and scale. It first empties the output file so an old screenshot cannot be mistaken for a new one, builds a browser screenshot command with shot_command, runs it in the sandbox with a timeout, and returns true only if the command exits successfully. On failure, it logs a short reason and returns false.

**Call relations**: draw_from_page uses this to capture the live site. _compose uses it again to capture the HTML page that represents the finished card. When anything goes wrong, _shoot delegates to _undrawn so failures are recorded but do not stop the site from being hosted.

*Call graph*: calls 2 internal fn (_undrawn, shot_command); called by 2 (_compose, draw_from_page).


##### `_compose`  (lines 615–665)

```
async def _compose(ctx: ToolContext, sites: HostedSites, conversation_id: UUID, name: str, shot: str, drawn: str) -> None
```

**Purpose**: Turns an existing site screenshot into the final share-card image, stores it, and records it on the site. This is the central assembly line for the card.

**Data flow**: It receives the tool context, site store, conversation ID, site name, screenshot path, and the rule for drawing that screenshot in the card. It writes the card HTML into the sandbox, replaces the screenshot placeholder with a data URI, screenshots the composed HTML page, converts that PNG to a progressive JPEG, reads the digest printed by the encoder, stores the JPEG as a preview artifact, and finally updates the hosted-site record with the blob key and digest. If any step fails, it logs the issue and leaves the existing card untouched.

**Call relations**: Both draw_from_page and draw_from_stored_shot hand their screenshot to _compose. Inside the flow, _compose calls card_page to build the card HTML, _shoot to photograph that HTML, ToolContext.store_preview to save the finished image, and HostedSites.set_share_card to attach the result to the site record.

*Call graph*: calls 5 internal fn (store_preview, _shoot, _undrawn, card_page, set_share_card); called by 2 (draw_from_page, draw_from_stored_shot).


##### `_undrawn`  (lines 668–669)

```
def _undrawn(name: str, detail: object) -> None
```

**Purpose**: Logs that a share card could not be drawn. It keeps failures visible to operators while deliberately avoiding a hard failure for the hosted site.

**Data flow**: It receives the site name and an error detail object. It turns the detail into text, trims it to a safe length, and writes a structured log event. It returns nothing and does not change the site record.

**Call relations**: draw_from_stored_shot, _shoot, and _compose call this whenever a recoverable card-generation step fails. It is the shared “note the problem and move on” endpoint for this file’s failure paths.

*Call graph*: called by 3 (_compose, _shoot, draw_from_stored_shot); 1 external calls (log).


### Artifact Lifecycle
These files define shared artifact behavior and expose signed download routes for safely retrieving artifact bytes.

### `core/src/ufo/host/kinds/artifacts.py`

`domain_logic` · `request handling`

An artifact is a file produced during a conversation and shared with others, such as a report, image, or document. This file gives those shared files a stable object interface, so the rest of the system can treat them like named workspace objects instead of loose database rows and blob-storage entries. Without it, users and agents could share files, but there would be no consistent way to find the latest version, reuse it in a later turn, show it in the portal, or remove all stored copies.

The main idea is that one artifact is identified by two things: the conversation it came from and the original filename. If the same filename is shared again in the same conversation, it becomes a new version of the same artifact. If the same filename appears in another conversation, it is a different artifact. The file creates readable names by combining a short conversation prefix with a cleaned-up filename, adding a digest only when names would otherwise collide.

The `ArtifactObjects` class is the working surface. It builds database queries that respect audience rules, groups shares into artifact versions, creates listing rows with useful fields and optional download or preview links, fetches details, copies small files back into the sandbox workspace on status/get, and deletes both database records and blob bytes. Creation and update are refused because artifacts are only made by the separate `share_file` tool.

#### Function details

##### `artifact_media`  (lines 79–87)

```
def artifact_media(media_type: str) -> str
```

**Purpose**: Sorts a file's MIME type, which is a standard label like `image/png` or `application/pdf`, into a simple bucket: image, document, or other. This makes artifact lists easy to filter without teaching callers every possible file type.

**Data flow**: It receives a media type string, lowercases it, checks whether it looks like an image or known document type, and returns one of three plain labels. It does not change any stored data.

**Call relations**: When `ArtifactObjects._row` prepares a listing row, it asks this helper for the broad media category so the row can expose a simple `media` field.

*Call graph*: called by 1 (_row).


##### `_document_media`  (lines 90–95)

```
def _document_media() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition used to find document-like artifacts. It covers text files, common Office document types, PDFs, and Word documents.

**Data flow**: It reads no live rows itself. It creates a SQL expression, meaning a database test that can later be attached to a query, and returns that expression to the caller.

**Call relations**: `ArtifactObjects._groups` uses this helper when a list request asks for `media=document`, or when it needs to exclude documents while finding `media=other`.

*Call graph*: called by 1 (_groups); 1 external calls (or_).


##### `_member_participated`  (lines 98–110)

```
def _member_participated() -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition that checks whether a member had already joined a conversation by the time an artifact was shared. This protects the member-facing artifact view from showing files from before the member was admitted.

**Data flow**: It creates a SQL `exists` test, which asks the database whether a matching admission turn exists at or before the share's turn. The result is a reusable query condition, not a direct yes/no value in Python.

**Call relations**: `ArtifactObjects._member_shares` adds this condition to the normal share query so member pages and member detail views only see eligible files.

*Call graph*: called by 1 (_member_shares); 2 external calls (literal, select).


##### `artifact_object_names`  (lines 113–133)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Creates stable, human-readable object names for artifact identities. It keeps files from different conversations separate, even when their filenames match.

**Data flow**: It receives pairs of conversation ID and filename. It makes a cleaned filename slug, prefixes it with part of the conversation ID, counts duplicate names, and adds a short hash only for collisions. It returns a mapping from each original identity to its final object name.

**Call relations**: `ArtifactObjects._identities` calls this after reading all visible artifact identities from the database. This helper uses `_slug` for readable names and `_identity_digest` as a tie-breaker when two identities would otherwise produce the same name.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_identities); 1 external calls (Counter).


##### `_slug`  (lines 136–138)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, safe name fragment for use in artifact object names. It removes punctuation-like runs and falls back to `artifact` if nothing readable remains.

**Data flow**: It receives a filename, lowercases it, replaces non-letter-or-number runs with dashes, trims it to the configured length, and returns the cleaned text. It changes no outside state.

**Call relations**: `artifact_object_names` calls this for each filename while building names that users and tools can refer to.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 141–143)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a stable fingerprint for one conversation-and-filename identity. It is used only when two generated artifact names collide.

**Data flow**: It receives a conversation ID and filename, combines them into text, hashes that text with SHA-256, and returns the hexadecimal hash string. The caller normally uses only the first few characters.

**Call relations**: `artifact_object_names` calls this when a readable base name is not unique, so the final object name can stay deterministic and distinct.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 168–175)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible to the current tool context. This is the agent-facing list operation for shared files.

**Data flow**: It takes the caller's tool context and list query, reads the caller's allowed subjects, builds the base share query, turns matching shares into object rows, and wraps those rows into a page result. It does not copy file bytes or modify storage.

**Call relations**: This is an outward-facing method on the artifact store. It hands the real work to `_shares` and `_rows`, then uses `object_page` to shape the result for the object system.

*Call graph*: calls 2 internal fn (_rows, _shares); 1 external calls (object_page).


##### `ArtifactObjects.member_page`  (lines 177–196)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Builds the signed-in member's Artifacts page. It shows only files that fall inside that member's audience and were shared after the member entered the relevant conversation.

**Data flow**: It receives member information and a list query, derives the subjects that member is allowed to see, narrows shares to member-eligible ones, builds rows, and returns a paged list. The `admin` flag is accepted but does not bypass the audience fence here.

**Call relations**: The portal-style member view calls this instead of the broader agent list. It uses audience helpers to find what the member can see, `_member_shares` to enforce participation timing, `_rows` to build rows, and `object_page` to paginate them.

*Call graph*: calls 2 internal fn (_member_shares, _rows); 3 external calls (object_page, audience_subjects, conversation_audience).


##### `ArtifactObjects.get`  (lines 198–200)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Fetches the stored description of one artifact by name for the current tool context. It returns metadata about the latest version, not the file bytes themselves.

**Data flow**: It receives a context and artifact name, searches visible shares for that name, and returns `None` if no matching artifact exists. If found, it converts the share versions into an object detail record.

**Call relations**: This is the normal object-detail read path. It calls `_shares` and `_find` to locate the artifact, then passes the versions to `_detail` to shape the response.

*Call graph*: calls 3 internal fn (_find, _shares, _detail).


##### `ArtifactObjects.member_detail`  (lines 202–219)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Fetches one artifact for the member-facing Artifacts view. It applies the same audience and admission rules as the member listing.

**Data flow**: It receives a member ID and artifact name, derives that member's readable subjects, searches member-visible shares, and returns `None` if absent. If present, it also reads source information for the conversation, builds a listing row, builds detail metadata, and packages both as a member object.

**Call relations**: This is the single-item partner to `member_page`. It uses `_member_shares` and `_find` to locate the file, `_sources` and `_row` to provide portal row fields, and `_detail` for the object metadata.

*Call graph*: calls 5 internal fn (_find, _member_shares, _row, _sources, _detail); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 221–265)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports the live status of an artifact and, for small enough files, copies the latest version back into the conversation workspace so a later turn can reuse it. It also creates a fresh temporary download link when token signing is configured.

**Data flow**: It receives a context, artifact name, and optional expected generation. It finds the visible artifact, optionally reads its bytes from blob storage if it is below the materialization size limit, rechecks that the conversation is still visible, writes the file into `artifacts/<name>/<filename>` when bytes were loaded, mints a download URL if possible, and returns size, share time, turn ID, version count, URL, and workspace path.

**Call relations**: The object system calls this during the get/status flow when it needs current operational information. It relies on `_find` and `_shares` to locate the versions, `_unchanged_visible` to guard against a visibility change, the blob store for bytes, the sandbox for writing the workspace copy, and artifact URL helpers for signed links.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 6 external calls (__init__, now, workspace_tx, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects.apply`  (lines 267–276)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses attempts to create or update artifacts through the object API. Artifacts must come from sharing a file, not from editing this object kind directly.

**Data flow**: It receives the normal apply inputs but does not inspect or save the proposed spec. It immediately raises a `VerbNotSupported` error explaining that callers should write a file and use `share_file`.

**Call relations**: The object framework may call this for create or update verbs, but this artifact store deliberately stops that path. The actual producer is outside this file: the `share_file` tool.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 278–304)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact completely, including every version of the shared file. This removes both the database records and the stored blob bytes.

**Data flow**: It receives a context and artifact name, finds all visible versions, locks and rechecks the latest visible conversation record, deletes matching `shared_artifact` rows from the database, verifies the expected number were removed, then deletes each version's blob and preview blob from blob storage. If anything changed mid-delete, it raises an error rather than partly pretending success.

**Call relations**: This is the object delete operation. It uses `_shares` and `_find` to identify the artifact, `_unchanged_visible` to protect against races, a database transaction for record removal, and the blob interface for cleaning up bytes afterward.

*Call graph*: calls 3 internal fn (_find, _shares, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 306–313)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database check that confirms the artifact's conversation is still visible to the caller in the same way it was when the artifact was found. This prevents a stale read from being used after audience or conversation details changed.

**Data flow**: It receives the current context and the latest share row. It returns a SQL query that looks for the same conversation in the current workspace, selected agent, same audience, and caller-readable subjects.

**Call relations**: `status` and `delete` run this query just before writing a workspace copy or deleting records. It acts like a final door check before making a side effect.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._rows`  (lines 315–326)

```
async def _rows(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> tuple[ObjectRow, ...]
```

**Purpose**: Turns visible shared-file records into the rows used by artifact listings. It gathers grouped versions and adds conversation source information before formatting each row.

**Data flow**: It receives readable subjects, an optional viewer member ID, a list query, and a base share query. It groups the shares, reads source links for the involved conversations, converts each group into an `ObjectRow`, and returns all rows as a tuple.

**Call relations**: `list` and `member_page` call this after choosing which share projection is allowed. It delegates grouping to `_groups`, source lookup to `_sources`, and final row formatting to `_row`.

*Call graph*: calls 3 internal fn (_groups, _row, _sources); called by 2 (list, member_page).


##### `ArtifactObjects._find`  (lines 328–348)

```
async def _find(self, subjects: frozenset[str], name: str, shares: sa.Select) -> tuple[sa.Row, ...] | None
```

**Purpose**: Finds all versions of one named artifact within a supplied visibility projection. It resolves the public object name back to the underlying conversation and filename.

**Data flow**: It receives readable subjects, an artifact name, and a share query. It first builds the full visible name map, finds the identity matching the requested name, queries all shares for that conversation and filename, sorts versions newest first, and returns them. If the name is unknown or no rows remain, it returns `None`.

**Call relations**: `get`, `member_detail`, `status`, and `delete` all use this as their lookup step. It calls `_identities` so names are resolved consistently with listings, then runs the supplied share query inside a workspace transaction.

*Call graph*: calls 1 internal fn (_identities); called by 4 (delete, get, member_detail, status); 2 external calls (where, workspace_tx).


##### `ArtifactObjects._groups`  (lines 350–415)

```
async def _groups(self, subjects: frozenset[str], viewer: UUID | None, query: ObjectListQuery, shares: sa.Select) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Applies search and filter options to a share query, then groups the resulting shares by artifact identity. This is the core of artifact listing behavior.

**Data flow**: It receives readable subjects, an optional viewer, the requested filters, and a base share query. It narrows by text search, conversation ID, ownership, surface, and media category, reads a bounded set of recent rows, groups them by conversation and filename, assigns stable names, sorts versions newest first, and returns named groups.

**Call relations**: `_rows` calls this whenever a listing is needed. It uses `_document_media` for document filtering, `_identities` for stable names, and database transactions to read the filtered rows.

*Call graph*: calls 2 internal fn (_identities, _document_media); called by 1 (_rows); 5 external calls (false, not_, or_, workspace_tx, UUID).


##### `ArtifactObjects._identities`  (lines 417–440)

```
async def _identities(self, subjects: frozenset[str]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Reads every distinct artifact identity visible to the current audience and assigns each one its stable object name. This keeps names consistent even when a list view only scans recent rows.

**Data flow**: It receives readable subjects, queries the database for distinct conversation-and-filename pairs in the current workspace and selected agent, and passes those identities to `artifact_object_names`. It returns a dictionary from identity to object name.

**Call relations**: `_find` uses this to resolve a requested name, and `_groups` uses it to label grouped listing results. The naming itself is delegated to `artifact_object_names`.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, _groups); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `ArtifactObjects._shares`  (lines 442–477)

```
def _shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the base database query for artifact shares visible to an agent and audience. It gathers the file data together with conversation and owner information needed later.

**Data flow**: It receives readable subjects and returns a SQL select statement. That statement joins shared artifacts to their turn, conversation, and optional member owner, and filters by current workspace, selected agent, and allowed audience.

**Call relations**: Most public operations start here: `list`, `get`, `status`, and `delete` use this query directly, while `_member_shares` further narrows it for member views.

*Call graph*: called by 5 (_member_shares, delete, get, list, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._member_shares`  (lines 479–480)

```
def _member_shares(self, subjects: frozenset[str]) -> sa.Select
```

**Purpose**: Builds the member-safe version of the base share query. It only includes shares from conversations where a member had already participated by the share time.

**Data flow**: It receives readable subjects, creates the normal visible share query, adds the member-participation condition, and returns the narrowed SQL query.

**Call relations**: `member_page` and `member_detail` use this instead of `_shares` so the portal view follows member admission rules. It gets the base query from `_shares` and the timing check from `_member_participated`.

*Call graph*: calls 2 internal fn (_shares, _member_participated); called by 2 (member_detail, member_page).


##### `ArtifactObjects._sources`  (lines 482–519)

```
async def _sources(self, conversation_ids: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the opening source for each conversation, such as the permalink or entry point recorded on the first turn. This lets artifact rows show where a conversation came from.

**Data flow**: It receives conversation IDs. If none are provided, it returns an empty mapping. Otherwise it finds the earliest turn for each conversation, reads its stored context, validates that context, extracts the source field, and returns a mapping from conversation ID to source or `None`.

**Call relations**: `_rows` calls this for listing rows, and `member_detail` calls it for a single member object. `_row` then uses the returned mapping to fill the `source` field.

*Call graph*: called by 2 (_rows, member_detail); 5 external calls (model_validate, and_, select, workspace_tx, ws_current).


##### `ArtifactObjects._row`  (lines 521–549)

```
def _row(self, name: str, shares: tuple[sa.Row, ...], viewer: UUID | None, sources: dict[UUID, str | None]) -> ObjectRow
```

**Purpose**: Formats one artifact group into a listing row. It chooses the latest version for current fields while still summarizing version history.

**Data flow**: It receives an artifact name, its version rows, the optional viewer member ID, and conversation source data. It builds an `ObjectRow` with filename, caption, conversation, time, media category, size, owner, origin, source, ownership flag, and optional download and preview links.

**Call relations**: `_rows` uses this for normal listings, and `member_detail` uses it when returning a single portal object. It calls `_summary`, `artifact_media`, `_download_url`, and `_preview_url` to fill specialized fields.

*Call graph*: calls 4 internal fn (_download_url, _preview_url, _summary, artifact_media); called by 2 (_rows, member_detail); 1 external calls (__init__).


##### `ArtifactObjects._download_url`  (lines 551–560)

```
def _download_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a signed temporary download URL for an artifact version when this deployment is configured to publish links. Signed means the URL contains proof that it is allowed, so the blob can be served without exposing it openly.

**Data flow**: It receives the latest share row. If either the token secret or public base URL is missing, it returns `None`. Otherwise it mints an expiring artifact path for the blob and joins it to the public base URL.

**Call relations**: `_row` calls this while preparing list rows so the portal can show a download link when link minting is available.

*Call graph*: called by 1 (_row); 4 external calls (now, artifact_url_expiry, mint_artifact_url, ws_current).


##### `ArtifactObjects._preview_url`  (lines 562–582)

```
def _preview_url(self, latest: sa.Row) -> str | None
```

**Purpose**: Creates a signed image-preview URL when the artifact has a safe raster image preview. A raster image is a pixel-based image such as PNG or JPEG.

**Data flow**: It receives the latest share row, chooses either the stored preview blob or the original blob if the file itself is an image, checks that the blob key and declared media type agree, and returns a signed preview URL. If the file is not eligible, it returns `None`.

**Call relations**: `_row` calls this for each listing row. It relies on image-preview helpers to verify the media type and mint the preview link.

*Call graph*: called by 1 (_row); 3 external calls (mint_image_preview_url, raster_image_media_type, ws_current).


##### `_detail`  (lines 585–601)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Builds the detailed object metadata for an artifact. It describes the latest version and links the artifact back to the conversation where it was created.

**Data flow**: It receives all versions of an artifact sorted newest first. It takes the latest version for filename, media type, and subject, uses the oldest share time as creation time, the latest share time as update time, and returns an `ObjectDetail` with a `created_in` conversation link.

**Call relations**: `ArtifactObjects.get` and `ArtifactObjects.member_detail` call this after `_find` has located the artifact versions.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 604–610)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable summary for an artifact listing row. It includes the filename, media type, size, share date, and version count when there is more than one version.

**Data flow**: It receives the artifact's version rows, reads the latest row and the number of versions, builds a sentence-like string, and trims it to the configured summary length.

**Call relations**: `ArtifactObjects._row` uses this when building each `ObjectRow` so listings have a compact description.

*Call graph*: called by 1 (_row).


##### `artifact_object`  (lines 613–676)

```
def artifact_object(*, public_base_url: str | None=None, artifact_token_secret: str='') -> ObjectKind
```

**Purpose**: Registers the artifact object kind with the wider object system. It declares what artifacts are, what fields they expose, which verbs are allowed, and which store object implements the behavior.

**Data flow**: It receives optional public URL and token-secret settings for link creation. It builds an `ArtifactObjects` store with those settings, then returns an `ObjectKind` containing the name, description, guidance text, spec model, list fields, and allowed agent verbs.

**Call relations**: Startup or object-kind registration code calls this to make artifacts available. The returned `ObjectKind` points later list, get, status, and delete requests to the `ArtifactObjects` methods in this file.

*Call graph*: 2 external calls (__init__, __init__).


### `core/src/ufo/runtime/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the doorway for downloading artifacts, meaning files that the system has shared for a workspace. The main safety rule is simple: no valid signature, no file bytes. A signed URL works like a temporary claim ticket at a coat check. If the ticket is valid, the system streams the file. If the ticket is fake, expired, or points outside a workspace, the system refuses.

The route first checks the URL signature using a deploy-wide secret stored on the app. That signature says which blob, filename, expiry time, workspace, and optional preview are allowed. If the signature is valid, the file is read from the workspace's blob store. Normal downloads are streamed in chunks, so large files do not need to sit fully in memory. Image previews are stricter: the code validates that the bytes really match the requested preview type and size before returning them inline.

Expired links get one special path. If the browser has the portal session cookie, the code checks whether that user is a member of the workspace that owns the shared artifact. If yes, it creates a fresh signed URL and redirects there. If not, an HTML browser is sent to sign in and then come back; other clients get a clear forbidden response. Successful file responses can be briefly cached because the exact signed URL is already the permission grant, but refusals and redirects are never cached.

#### Function details

##### `download`  (lines 59–117)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='', workspace: Annotated[str, Query(alias='ws')]='') -> Response
```

**Purpose**: This is the actual HTTP endpoint for artifact links. It verifies that the link is allowed, then returns either the file download or a safe image preview, and refuses requests that do not have a valid grant.

**Data flow**: It receives the web request, the artifact id and filename from the path, and signature-related query values such as expiry, signature, preview, and workspace. It reads the blob store and artifact signing secret from the running app, checks the signed URL, then looks up the file inside the claimed workspace. If the request asks for a preview, it validates and returns preview bytes; otherwise it streams the original file with download headers. If the link is expired, it passes the expired claims to the refresh helper instead of serving bytes.

**Call relations**: FastAPI calls this function when a request matches the artifact download path. It relies on the artifact URL verifier to decide whether the link is trustworthy, uses the workspace blob store to find and stream bytes, calls the image preview validator when a preview was requested, and calls _refreshed_for_member when a real but expired link might be repairable for a signed-in teammate.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 9 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, ws, quote).


##### `_refreshed_for_member`  (lines 120–171)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This function tries to turn an expired but authentic artifact link into a fresh one for someone who is already signed in and belongs to the right workspace. It keeps old team links useful without opening the file to outsiders.

**Data flow**: It receives the web request, the claims from the expired signed URL, and the secret used to mint new artifact URLs. It reads the session cookie, verifies the session, turns the session workspace into a UUID, then checks the database to confirm two things: the user is a member of that workspace, and the requested blob belongs to a shared artifact in that same workspace. If both checks pass, it creates a new expiry time, mints a new signed URL for the same blob or preview, and returns a redirect there. If anything does not check out, it raises the refusal response.

**Call relations**: download calls this only after the artifact URL verifier says the link has expired but was otherwise authentic. This helper consults authentication, workspace context, and the database before handing off to the artifact URL minting code. When it cannot safely refresh the link, it delegates the response choice to _refusal.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 10 external calls (now, RedirectResponse, or_, select, workspace_tx, verified_claims, artifact_url_expiry, mint_artifact_url, ws, UUID).


##### `_refusal`  (lines 174–179)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This function builds the right refusal response for an expired link that cannot be refreshed immediately. Browsers that accept HTML are sent toward login, while non-browser clients receive a forbidden error.

**Data flow**: It receives the web request and checks the Accept header to see whether the caller looks like a normal browser expecting an HTML page. For a browser, it quotes the current artifact path and query string, places that as a target parameter on the login URL, and returns a redirect-style HTTP exception. For other callers, it returns a plain forbidden HTTP exception with the expired-link message.

**Call relations**: _refreshed_for_member calls this whenever there is no valid session, the session workspace is malformed, the user is not a member, or the artifact does not belong to that workspace. It is the final decision point for how a failed refresh attempt is reported to the caller.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).


### Media Package Utilities
These files make the runtime media package importable and validate raster image previews before they are accepted for display.

### `core/src/ufo/runtime/media/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That lets other parts of the project refer to code in this area using names like `ufo.runtime.media...` instead of dealing with raw file paths. Think of it like a label on a drawer: the label does not contain the tools, but it tells the rest of the system that this drawer is a recognized place to look. Because the file is empty, it does not run setup code, create shortcuts, or expose helper functions. Its importance is structural: without it, depending on the Python version and packaging setup, imports for media runtime code could fail or behave differently.


### `core/src/ufo/runtime/media/image_previews.py`

`domain_logic` · `request handling / media preview intake`

Image previews arrive as bytes, often from outside the program, so the system cannot simply trust them. This file acts like a security guard at the door for GIF, JPEG, PNG, and WebP previews. It checks that the file name looks like a supported image type, that the sender’s signed promise about the image size is true, and that the actual image data is safe to decode.

The main public path is `validated_image_preview`. It reads an asynchronous stream of byte chunks, counts every byte, and rejects the preview if it is larger than allowed or does not match the claimed size. Once all bytes are collected, it asks `_ImagePreviewValidator` to inspect the image in a worker thread, so expensive image decoding does not block the main event loop.

The validator uses Pillow, a Python image library, but treats warnings as errors because some images can be dangerous even if technically readable. It verifies the image container has a proper ending, confirms Pillow agrees with the claimed media type, checks every frame up to a limit, limits width and height, and caps the total number of decoded pixels. This matters because animated or specially crafted images can consume huge amounts of memory or CPU. If anything looks wrong, the file raises `InvalidImagePreview` instead of returning unsafe bytes.

#### Function details

##### `raster_image_media_type`  (lines 43–44)

```
def raster_image_media_type(path: str) -> RasterImageMediaType | None
```

**Purpose**: This function guesses the image media type from a path’s file extension. It is useful before validation, when the system needs to know whether a path looks like a supported raster image such as PNG or JPEG.

**Data flow**: It receives a path string. It looks only at the final suffix, lowercases it, and compares it with the known image suffixes. It returns a media type such as `image/png` when there is a match, or `None` when the path does not look like a supported preview image.

**Call relations**: This is the lightweight first check in the image-preview flow. It uses `PurePosixPath` to read the suffix in a path-like way, then hands back a simple answer that other code can use before asking for or validating preview bytes.

*Call graph*: 1 external calls (PurePosixPath).


##### `validated_image_preview`  (lines 47–61)

```
async def validated_image_preview(stream: AsyncIterator[bytes], grant: ImagePreviewGrant) -> bytes
```

**Purpose**: This function receives image preview bytes from an asynchronous stream and only returns them if they exactly match the signed size claim and pass image safety checks. It is the main gatekeeper used when accepting preview data.

**Data flow**: It takes a stream that yields byte chunks and an `ImagePreviewGrant`, which says the claimed media type and byte size. It counts chunks as they arrive, rejects the stream if it grows past the claim or the hard maximum, then joins the chunks into one byte string. It sends those bytes to the image validator in a background thread and, if nothing fails, returns the original bytes unchanged.

**Call relations**: This function sits between outside input and the rest of the system. When it sees an impossible or mismatched size, it raises `InvalidImagePreview` immediately. When the byte count is plausible, it uses `asyncio.to_thread` to call `_ImagePreviewValidator.validate`, keeping image decoding work away from the async event loop so other tasks can keep running.

*Call graph*: 2 external calls (__init__, to_thread).


##### `_ImagePreviewValidator.validate`  (lines 66–109)

```
def validate(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This function performs the deeper image inspection: it proves that the bytes are a real image of the claimed type and that decoding it will stay within safe limits. It is the part that catches malformed images, mislabeled media, too many animation frames, and oversized decoded content.

**Data flow**: It receives raw image bytes and the media type that was claimed for them. It first checks simple container endings, then opens the bytes with Pillow through an in-memory buffer. It verifies the file structure, reopens the image, walks through frames, checks dimensions, counts total decoded pixels, and forces each frame to load. It returns nothing on success; on any unsafe or inconsistent condition, it raises `InvalidImagePreview`.

**Call relations**: This validator is called after `validated_image_preview` has finished reading and counting the stream. It uses `warnings.catch_warnings` and `warnings.simplefilter` so Pillow’s decompression-bomb warnings become hard failures. It also relies on `_ImagePreviewValidator._validate_container` for quick format-specific completeness checks before doing heavier image decoding.

*Call graph*: 5 external calls (__init__, open, BytesIO, catch_warnings, simplefilter).


##### `_ImagePreviewValidator._validate_container`  (lines 112–125)

```
def _validate_container(data: bytes, media_type: RasterImageMediaType) -> None
```

**Purpose**: This helper checks whether the image bytes have the basic wrapper expected for their claimed format. It catches obviously incomplete JPEG, GIF, PNG, and WebP files before the more detailed Pillow checks run.

**Data flow**: It receives raw bytes and a claimed media type. For each supported format, it checks a small format-specific marker: for example, JPEG must end with its end marker, GIF must end with its trailer byte, PNG must end with its final `IEND` chunk, and WebP must have a consistent RIFF/WEBP header and length. It returns nothing if the container looks complete, or raises `InvalidImagePreview` if it does not.

**Call relations**: This function is used as an early checkpoint inside `_ImagePreviewValidator.validate`. It does not decode the image; it simply rejects files that are plainly cut off or structurally inconsistent, saving the heavier image library step for data that at least looks complete.

*Call graph*: 1 external calls (__init__).
