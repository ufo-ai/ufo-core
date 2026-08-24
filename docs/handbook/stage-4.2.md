# Operator, site, and file-serving surfaces  `stage-4.2`

This stage is the system’s set of web “front doors” for people and browsers. It is not startup or shutdown code. It runs during normal use, when an operator opens a tool, a visitor views a hosted site, or someone follows a file link. Each door checks access before showing anything.

The artifacts route serves shared file downloads. It uses signed, time-limited links, like a ticket with an expiry date. If the ticket has expired, a signed-in member of the right workspace can refresh access; others cannot reach the file bytes.

The debugger surface gives operators a read-only window into one workspace’s sessions. It serves the debugger page, plus data and live update streams for conversations, turns, transcripts, files, and current activity.

The memory surface is another operator page. It shows stored workspace memory records and provides the small data endpoint the page uses to list them.

The sites surface serves public hosted-site frames. It checks whether the viewer may see the site, then safely embeds the real site from its separate hosting location.

## Files in this stage

### Signed file access
Core routes serve protected shared-file downloads through signed, time-limited links with workspace-member refresh rules.

### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the guarded doorway for artifact downloads. An artifact is a shared file, and the system does not expose the file directly. Instead, it gives out a signed URL: a link containing proof that the server created it, plus an expiry time. Think of it like a temporary ticket to a storage room. Without this file, shared file links would either fail to work through the web server, or worse, risk serving private bytes without checking the ticket.

The main route checks the link’s signature, expiry, workspace, file name, and optional preview request. If the link is valid, it looks up the file in the workspace’s blob store, which is the place where file bytes are kept. Normal downloads are streamed in chunks, so large files do not have to be loaded fully into memory. Image previews are treated more carefully: the file must prove it is the expected image type and size before it is shown inline in the browser.

If a link has expired, the file does not immediately become public again. Instead, the code checks whether the browser has a valid session cookie for a member of the same workspace that owns the shared artifact. If so, it creates a fresh signed URL and redirects there. If not, a browser is sent to sign in, while non-browser clients get a clear forbidden response.

#### Function details

##### `download`  (lines 47–101)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='', workspace: Annotated[str, Query(alias='ws')]='') -> Response
```

**Purpose**: This is the HTTP endpoint that serves a shared file or a safe image preview. It checks that the URL is genuinely signed, still valid, and tied to a workspace before any file bytes are returned.

**Data flow**: It receives the web request, path parts such as the artifact id and filename, and query values such as expiry time, signature, preview details, and workspace id. It reads the blob store and signing secret from the application state, verifies the URL claims, then checks whether the requested blob exists in the claimed workspace. If the request is for a preview, it validates and returns bounded image data; otherwise it returns a streaming download response with safe headers that tell browsers not to guess the file type or cache the private result.

**Call relations**: This is called by FastAPI when a request matches the artifact download route. If the signature is expired but otherwise meaningful, it asks _refreshed_for_member to see whether a logged-in workspace member may receive a fresh link. For valid links, it hands preview streams to validated_image_preview when needed, or hands the blob stream to StreamingResponse for normal downloads.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 9 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, ws, quote).


##### `_refreshed_for_member`  (lines 104–154)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This helper gives expired artifact links a controlled second chance for real workspace members. It lets a teammate open an old shared link and be redirected to a new signed URL, while outsiders still get nothing.

**Data flow**: It receives the current request, the expired-but-authentic artifact claims, and the signing secret. It reads the session cookie, verifies the logged-in user, turns the workspace id from the session into a real UUID, and queries the database to confirm two things: the user is a member of that workspace, and the artifact belongs to that workspace. If both checks pass, it creates a new expiry time, mints a fresh artifact URL, and returns a redirect to that URL. If any check fails, it raises the refusal response instead.

**Call relations**: download calls this only after verify_artifact_url reports that the link has expired. This helper uses _refusal whenever the browser or client cannot prove workspace membership. When membership is proven, it hands off to mint_artifact_url to create the replacement link and returns a RedirectResponse so the client retries through the normal download path.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 9 external calls (now, RedirectResponse, or_, select, mint_artifact_url, verified_claims, workspace_tx, ws, UUID).


##### `_refusal`  (lines 157–162)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This helper creates the right rejection response when an expired link cannot be refreshed. Browsers are guided toward sign-in, while other clients get a plain forbidden error.

**Data flow**: It receives the request and looks at the Accept header to see whether the client wants HTML, which usually means a browser. For a browser, it builds a login URL that includes the original artifact link as the target to come back to after sign-in, then returns an HTTP exception that acts like a redirect. For non-browser clients, it returns an HTTP exception saying the expired download is forbidden.

**Call relations**: _refreshed_for_member calls this whenever there is no valid session, the session workspace is malformed, the user is not a member, or the artifact is not owned by that workspace. It is the final gatekeeper for the expired-link path, deciding whether the next step is sign-in or a hard denial.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).


### Operator inspection pages
Operator-facing extension surfaces expose read-only debugger and memory-browsing views for a workspace.

### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the bridge between the debugger web app and the stored session data for a workspace. Think of it like a viewing window into a workshop: operators can look at what happened and watch a live turn unfold, but this surface is not meant to change the underlying conversation.

The file first loads the built React app from static/index.html. If that file exists, the root page returns it as HTML. The rest of the routes under api/ return plain JSON snapshots from SurfaceContext, which is the object already scoped to the authorized workspace. That matters because the authorization and workspace binding happen before these functions run; each handler can ask for conversations, transcripts, files, or turns without choosing a workspace itself.

Most handlers follow the same pattern: read an ID from the URL, check that it is a valid UUID, ask SurfaceContext for the requested data, and return either JSON or a 404-style error. One route streams a live turn using Server-Sent Events, a simple web streaming format where the server sends named text events over one long HTTP response. The helper _sse turns each internal live frame into one of those browser-readable events. At the bottom, ROUTES connects URL paths to these functions.

#### Function details

##### `app_page`  (lines 52–57)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This returns the debugger’s main web page. It exists so a browser can load the built React app before asking the API routes for data.

**Data flow**: It receives the workspace-aware context and the incoming web request. It checks whether the prebuilt HTML file was available when the module loaded; if not, it raises a clear setup error telling the developer to build the frontend. If the file is present, it wraps the HTML text in an HTML response and sends it back to the browser.

**Call relations**: This function is used by the root GET route for the debugger surface. Once it returns the page, the browser-side app continues by calling the JSON and stream routes defined later in this file.

*Call graph*: 1 external calls (HTMLResponse).


##### `workspace_meta`  (lines 60–67)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This gives the frontend basic information about the current workspace. It includes the workspace ID and, when available, the connected Slack team ID.

**Data flow**: It receives the current SurfaceContext, asks it for the Slack installation value, and strips the stored team prefix when the value is in the expected Slack team format. It returns a JSON object containing the workspace ID as text and either a Slack team identifier or null.

**Call relations**: The workspace API route calls this when the debugger page needs to label or orient itself. It relies on SurfaceContext.installation for the stored Slack connection and then hands the result to JSONResponse for delivery to the browser.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 70–72)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This returns the list of conversations visible in the current workspace. It is the debugger’s overview endpoint.

**Data flow**: It asks SurfaceContext for the workspace’s conversations. Each returned entry is converted into JSON-friendly data, and the full list is sent back as a JSON response.

**Call relations**: The conversations API route uses this when the frontend needs to populate a conversation list. It delegates the actual lookup to SurfaceContext.list_conversations and only formats the result for HTTP.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 75–80)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This returns the turns inside one conversation. A turn is one unit of interaction or work within a conversation.

**Data flow**: It reads conversation_id from the URL and passes it through _uuid_param, which accepts only a valid UUID. If the ID is invalid, it returns a not-found JSON error. Otherwise it asks SurfaceContext for that conversation’s turns, converts each turn to JSON-friendly form, and returns the list.

**Call relations**: The conversation turns route calls this after the frontend selects a conversation. It uses _uuid_param for safe URL parsing, asks SurfaceContext.list_turns for the data, and wraps the answer in JSONResponse.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 83–90)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This returns the saved transcript for a conversation. The transcript is the readable record of what was said or exchanged.

**Data flow**: It pulls conversation_id from the request path and validates it as a UUID. If the ID is invalid, it returns a not-found error. If the ID is valid, it asks SurfaceContext for the transcript; a missing transcript also becomes a not-found error. A found transcript is converted into JSON and returned.

**Call relations**: The transcript route uses this when the debugger page needs the full conversation record. It shares _uuid_param with the other conversation endpoints and relies on SurfaceContext.read_transcript for the actual stored data.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 93–97)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This lists the compaction records for a conversation. A compaction is where earlier conversation messages were summarized or condensed to keep context manageable.

**Data flow**: It validates conversation_id from the URL. If the value is not a UUID, it returns a not-found JSON error. Otherwise it asks SurfaceContext for the compaction indexes or records available for that conversation, converts the async result to a list, and returns it as JSON.

**Call relations**: The compactions list route calls this when the frontend wants to show where a conversation was summarized. It depends on _uuid_param for safe parsing and SurfaceContext.list_compactions for the workspace-scoped read.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 100–115)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This returns the details of one compaction event. It lets an operator compare what messages existed before compaction, what remained after, and what summary was produced.

**Data flow**: It reads conversation_id and an index from the URL. The conversation ID must be a valid UUID, and the index must contain only digits; otherwise it returns a not-found error. It then asks SurfaceContext for that specific compaction. If found, it builds a JSON object with the index, before messages, after messages, and summary.

**Call relations**: The individual compaction route calls this after a user chooses a compaction from the list. It uses _uuid_param to reject bad conversation IDs, calls SurfaceContext.read_compaction for the record, and formats nested message objects before returning JSON.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 118–123)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This lists files associated with a conversation’s workspace area. It lets the debugger show what files were available or created during that conversation.

**Data flow**: It validates conversation_id from the path. If the value is not a valid UUID, it returns a not-found error. Otherwise it asks SurfaceContext for the files tied to that conversation, converts each file entry to JSON-friendly data, and returns the list.

**Call relations**: The files listing route calls this when the frontend opens the file view for a conversation. It uses _uuid_param for URL safety and SurfaceContext.list_workspace_files for the actual file metadata.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 126–136)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This downloads the contents of one workspace file for a conversation. It is used when the debugger needs the actual bytes of a file, not just its name or metadata.

**Data flow**: It validates the conversation_id from the URL and reads the requested file path from the route. If the ID is bad, the path is rejected, or no file stream exists, it returns a not-found JSON error. If the file is available, it returns a streaming response with generic binary data so the file can be downloaded or inspected.

**Call relations**: The file content route calls this when the frontend requests a specific file. It uses _uuid_param before asking SurfaceContext.read_workspace_file for a stream, and it uses StreamingResponse so large files do not have to be loaded all at once before sending.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 139–146)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This returns detailed information about one turn. It lets the debugger inspect a single unit of activity directly.

**Data flow**: It reads turn_id from the URL and validates it as a UUID. If the ID is invalid, or if SurfaceContext cannot find that turn, it returns a not-found JSON error. Otherwise it converts the turn detail into JSON-friendly data and returns it.

**Call relations**: The turn detail route calls this when the frontend opens a specific turn. It shares _uuid_param with the other ID-based routes and relies on SurfaceContext.turn_detail to fetch the scoped record.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 149–154)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This opens a live event stream for one turn. It lets the debugger watch raw turn activity as it happens or resume after a dropped connection.

**Data flow**: It validates turn_id and confirms the turn exists. If not, it returns a not-found error. It then reads the Last-Event-ID header, which is the browser’s way of saying where a previous stream stopped, and returns a Server-Sent Events stream produced by _events.

**Call relations**: The live stream route calls this when the frontend wants ongoing updates for a turn. It checks existence with SurfaceContext.turn_detail, then hands the long-running response to _events and wraps that async byte stream in StreamingResponse.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 157–160)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: This turns the backend’s live turn feed into a stream of browser-sendable event bytes. It is the small adapter between the internal live-frame tail and Server-Sent Events output.

**Data flow**: It receives the workspace context, a turn ID, and a cursor saying where to resume. It opens SurfaceContext.tail, which yields live frames with their cursors, then converts each frame by calling _sse. It yields each encoded event chunk to the HTTP streaming response.

**Call relations**: stream calls this after it has validated the turn and created the streaming HTTP response. _events stays close to SurfaceContext.tail for the live source and delegates the formatting of each individual frame to _sse.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 163–191)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: This formats one internal live frame as one Server-Sent Events message. It names the event by the kind of frame, such as text, tool call, cost update, or terminal result.

**Data flow**: It receives a cursor and a LiveFrame object. If the cursor is not empty, it writes it as the event ID so the browser can later resume from that point. It then matches the frame type, serializes the frame’s data to JSON text, and returns the complete event as bytes in the SSE format.

**Call relations**: _events calls this once for every live frame it receives from SurfaceContext.tail. If a new frame type reaches this function without a matching case, it raises an error rather than silently sending an unknown or misleading event.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 194–198)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: This safely reads a UUID-shaped identifier from a request path. It keeps the route handlers from treating malformed URL text as a real conversation or turn ID.

**Data flow**: It receives a request and the name of a path parameter. It takes the text value from request.path_params and tries to convert it into a UUID object. A valid value comes out as a UUID; an invalid value comes out as None.

**Call relations**: The conversation, compaction, file, turn, and stream handlers call this before reading workspace data. That common check keeps their error behavior consistent: bad IDs become simple not-found responses instead of leaking parsing errors to the user.

*Call graph*: called by 8 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, workspace_file, workspace_files); 1 external calls (UUID).


### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the “memory explorer” surface: a read-only view that lets an authorized operator inspect what the memory extension has stored for one workspace. In plain terms, it is like opening a filing cabinet and seeing every memory card inside, including old, shared, personal, indexed, and not-yet-indexed records.

The file defines where the browser page comes from, how it is returned to the operator, and how the page asks for memory data. The HTML is loaded from `static/memory.html` when the Python module is loaded. If that file is missing, the page endpoint fails clearly instead of returning a broken blank page.

For data, the file does not read from the core system directly. It creates an `ExtensionContext`, which is the extension’s way to open its own workspace-scoped database transaction. “Workspace-scoped” means the database read is limited to the workspace already selected and authorized by the operator session. This matters because memory data is private to a workspace, and an operator should only see the workspace they are currently bound to.

The exported routes connect three web actions: show the page, bind the operator session, and return the memory list as JSON for the page to render.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the browser. It is used when an operator opens the memory surface in the web interface.

**Data flow**: It receives the current surface context and the incoming web request, but it does not need to inspect either one. It checks whether the HTML file was successfully loaded earlier. If the page is available, it wraps that HTML text in an HTTP HTML response; if not, it raises a clear error saying the page file is missing.

**Call relations**: This is the handler for the main GET route of the surface. When the operator visits the memory explorer, the routing table sends the request here, and this function hands back the complete browser page using `HTMLResponse`.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns every stored memory item for the currently bound workspace as JSON. The browser page uses it to fill the memory explorer with actual records.

**Data flow**: It receives the surface context, which includes the selected workspace, and the incoming request. It builds an extension context with a scoped store for the memory extension and no declared credential access. Through that context, it opens the extension’s workspace-aware transaction and asks the memory store inventory function for the records in the current workspace. It then turns each memory item into plain JSON-friendly data and returns the list as a JSON HTTP response.

**Call relations**: This is the handler for the `api/memories` GET route. After the HTML page loads in the operator’s browser, the page can call this endpoint to fetch memory data. The function delegates the actual database listing to `ufo_ext_memory.store.inventory`, then packages the result for the browser with `JSONResponse`.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).


### Hosted site frames
Public site routes resolve permanent links, enforce viewer permissions, and open the hosted site on its separate origin.

### `extensions/sites/ufo_ext_sites/surface.py`

`io_transport` · `request handling`

A hosted site link is meant to be shareable, but the link itself is not permission to view everything. This file is the gatekeeper. When someone opens a site URL, it first checks that the token in the URL is genuine and says which workspace, conversation, and site name it belongs to. Then it looks up the site, checks whether the browser is signed in through the `ufo_session` cookie, and applies the site's visibility rules: public, workspace-only, or private. If the site is being used as an agent homepage, the agent's visibility rules take over instead.

The file does not serve the site's files directly. A permanent site link returns a small HTML page containing an `<iframe>` that points to the site's own ingress address. A signed portal homepage link redirects an iframe request to that ingress address; opening the link outside an iframe keeps the wrapper. In both cases, the site stays apart from the main app's cookies and routes, and model-created site code cannot steer the user's main tab somewhere misleading.

Creators can change a normal site's visibility from this frame. That form is protected with a CSRF token, which is a signed proof tied to the current browser session, so another website cannot secretly submit the creator's form.

#### Function details

##### `site_token`  (lines 101–110)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that names one hosted site. The token records the workspace, conversation, and site name, so the public route can later find the right site without relying on a logged-in user's context.

**Data flow**: It receives a workspace ID, conversation ID, and site name. It places those values into a signed surface token for the sites surface. It returns the token string that can be put into a URL.

**Call relations**: When a full public site URL is needed, `site_url` asks this function to make the token part. This function hands the actual signing work to the shared surface-token helper so the token can later be checked by the same system.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 113–123)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the public URL someone can open to view a hosted site. It refuses to invent a URL if the deployment has no public base address configured, because such a link would not work.

**Data flow**: It receives the deployment's public base URL plus the workspace, conversation, and site name. If the base URL is missing, it raises a clear configuration error. Otherwise it creates a site token and returns a URL made from the base path, the sites frame path, and that token.

**Call relations**: This is the producer of shareable hosted-site links. It relies on `site_token` for the signed address portion, then wraps that token in the public frame route used later by `frame`.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `site_address`  (lines 126–137)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Reads and verifies a site token, turning it back into the site address it claims to name. If the token is fake, for the wrong surface, incomplete, or malformed, it returns nothing.

**Data flow**: It receives a token string. It verifies the signature and expected surface, then tries to parse the workspace and conversation as UUID values and read the site name. On success it returns a `SiteAddress`; on any verification or parsing failure it returns `None`.

**Call relations**: `resolve_workspace` uses this before database lookup so even public viewers can be routed to the correct workspace. `_resolve` uses it again when loading the actual hosted-site row for viewing or visibility changes.

*Call graph*: called by 2 (_resolve, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `resolve_workspace`  (lines 140–145)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace a request belongs to by reading the site token in the URL. This matters because a public visitor may not have a session cookie, so the workspace must come from the link itself.

**Data flow**: It reads the token path parameter from the incoming request and passes it to `site_address`. If the token is valid, it returns the workspace ID. If not, it returns the same plain 404 response used for unknown sites.

**Call relations**: The surface routing layer calls this early, before the normal handler, to decide the workspace context. It delegates token interpretation to `site_address` and uses `_not_found` to avoid revealing whether a bad link was close to a real one.

*Call graph*: calls 2 internal fn (_not_found, site_address).


##### `frame`  (lines 148–194)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a hosted site. It checks the link, checks the viewer, applies visibility rules, then redirects a portal homepage iframe to ingress or returns the site's frame page.

**Data flow**: It receives the surface context and web request. It resolves the site from the token, identifies the viewer from the session cookie if possible, checks whether the viewer may see the site or agent homepage, and builds an ingress URL for the site path. A homepage receives that URL as a redirect. A normal site receives an HTML frame with an optional creator CSRF token. If anything should not be visible, it returns either a sign-in page for unauthenticated non-public access or a 404 response.

**Call relations**: This is the main GET route for both the site root and deep links. It calls `_resolve` to load the site, `_viewer` to identify the browser, `_viewer_is_admin` when admin status matters, and `_frame_page` to assemble the final HTML.

*Call graph*: calls 9 internal fn (ingress_url, list_agents, _frame_page, _not_found, _page, _resolve, _session_digest, _viewer, _viewer_is_admin); 2 external calls (HTMLResponse, mint_surface_token).


##### `set_visibility`  (lines 197–218)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Processes the creator's form for changing a hosted site's visibility. It only allows the site's creator to do this, and only when the submitted form proves it came from the current session.

**Data flow**: It receives the surface context and web request. It resolves the site, identifies the viewer, rejects anyone who is not the creator, rejects homepage-bound sites because their visibility follows the agent, reads the submitted form, checks the CSRF token, parses the requested visibility level, writes the new value to the hosted-sites store, and redirects back to the site frame.

**Call relations**: This is the POST route behind the visibility selector created by `_selector`. It uses `_resolve`, `_viewer`, `_csrf_holds`, and `_sites` before handing the changed visibility value to the stored site record.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 221–225)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Turns the request's site token into the hosted-site record in storage. It is the shared lookup step used before viewing a site or changing its visibility.

**Data flow**: It reads the token from the request path, verifies and parses it with `site_address`, then uses the conversation ID and site name to read the hosted-site row from storage. It returns the `HostedSite` if found, or `None` if the token is bad or no site row exists.

**Call relations**: `frame` calls this before rendering, and `set_visibility` calls it before updating permissions. It uses `_sites` to get the workspace-specific hosted-sites store.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (frame, set_visibility).


##### `_sites`  (lines 228–229)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the storage helper for hosted sites in the current workspace. This keeps callers from repeating how to connect hosted-site operations to the workspace and transaction system.

**Data flow**: It receives the surface context. It takes the workspace ID and transaction provider from that context and returns a `HostedSites` store object ready to read or update site records.

**Call relations**: `_resolve` uses this helper to read a site, and `set_visibility` uses it to save a new visibility level. It is the small bridge between request context and the hosted-site storage layer.

*Call graph*: called by 2 (_resolve, set_visibility); 1 external calls (__init__).


##### `_viewer_is_admin`  (lines 232–237)

```
async def _viewer_is_admin(ctx: SurfaceContext, viewer: UUID | None) -> bool
```

**Purpose**: Checks whether the current viewer is an admin member of the workspace. Admins are allowed to view private sites and private agent homepages even when they are not the creator or owner.

**Data flow**: It receives the surface context and a viewer member ID, which may be missing. If there is no viewer, it returns `False`. Otherwise it opens a transaction, reads the workspace seat snapshot, and returns whether that member appears as an admin.

**Call relations**: `frame` calls this only when visibility rules need to know whether a signed-in member has admin privileges. It gets membership information from the shared `Seats` system.

*Call graph*: calls 1 internal fn (transaction); called by 1 (frame); 1 external calls (__init__).


##### `_viewer`  (lines 240–250)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies the signed-in workspace member behind the current browser request. It reads the `ufo_session` cookie, verifies it, and links the session identity to a workspace member if needed.

**Data flow**: It reads the session token from the request cookies. If there is no token or verification fails for this workspace, it returns `None`. If verification yields an email, it finds the already linked member for that email or creates the link, then returns the member ID.

**Call relations**: `frame` uses this to decide whether a visitor may view a non-public site. `set_visibility` uses it to prove the requester is the site's creator before accepting a visibility change.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 253–255)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility form token matches the current browser session. This protects the creator from another website tricking their browser into changing a site setting.

**Data flow**: It receives the request and the submitted CSRF token. It verifies the token's signature, reads the stored session digest claim, compares it with a fresh digest of this request's session cookie, and returns `True` only if they match.

**Call relations**: `set_visibility` calls this after confirming the viewer is the creator but before accepting the form data. It relies on `_session_digest` to bind the form token to the same session that loaded the page.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 258–262)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a safe fingerprint of the current session cookie for CSRF protection. It avoids putting the raw cookie value into the form token while still tying the token to this browser session.

**Data flow**: It reads the `ufo_session` cookie from the request, uses an empty string if it is absent, hashes it with SHA-256, and returns the hexadecimal hash string.

**Call relations**: `frame` uses this when minting a CSRF token for the creator's visibility form. `_csrf_holds` uses it later to check that the submitted token belongs to the same session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 265–266)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard 404 response for missing, invalid, or unauthorized site access. Using the same response in several cases helps avoid leaking which private sites exist.

**Data flow**: It takes no input. It creates a plain-text response with the body `no such site` and HTTP status 404. It does not change any stored data.

**Call relations**: `resolve_workspace`, `frame`, and `set_visibility` all use this when a token is invalid, a site is missing, or a viewer should not be told more. It provides one consistent outward answer for these cases.

*Call graph*: called by 3 (frame, resolve_workspace, set_visibility); 1 external calls (PlainTextResponse).


##### `_page`  (lines 269–274)

```
def _page(title: str, style: str, body: str) -> str
```

**Purpose**: Wraps a title, CSS styles, and HTML body into a complete minimal HTML document. It is the common shell used for sign-in notices and site frame pages.

**Data flow**: It receives a page title, style text, and body HTML. It combines them with a document type, character encoding, viewport setting, title tag, and style tag. It returns the resulting HTML string.

**Call relations**: `frame` uses this directly for the not-signed-in page. `_frame_page` uses it to wrap the iframe and optional header into the final page sent to the browser.

*Call graph*: called by 2 (_frame_page, frame).


##### `_frame_page`  (lines 277–313)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str, share: str) -> str
```

**Purpose**: Builds the HTML for the hosted-site frame. It shows the site name and either a creator-only visibility selector or a read-only visibility badge, then embeds the real site in a sandboxed iframe.

**Data flow**: It receives the hosted-site record, the embedded ingress URL if one exists, the frame path, a CSRF token if the selector should be shown, and the share metadata. It escapes user-visible values for HTML safety, creates either an iframe or an unconfigured-hosting message, creates the visibility control, and returns a full HTML page string.

**Call relations**: `frame` calls this after all access checks pass and after it has minted any needed CSRF token. This function calls `_selector` when the creator should be allowed to change visibility, and `_page` to produce the complete document.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 316–326)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Creates the small HTML form that lets a site's creator choose private, workspace, or public visibility. It includes the CSRF token needed for the later POST to be accepted.

**Data flow**: It receives the current visibility level, the frame path to post back to, and the CSRF token. It builds option tags with the current level selected, escapes the form action and token for HTML safety, and returns the form HTML.

**Call relations**: `_frame_page` calls this only when a valid CSRF token was provided, which happens for the signed-in creator of a normal site. The form it returns posts to the route served by `set_visibility`.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).
