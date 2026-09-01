# Web Portal and Workspace APIs  `stage-6.1`

This stage is the web-facing front door for a workspace. It runs during normal use, after the system is started, and provides the authenticated HTTP routes that the browser app calls. In simple terms, these routes are the guarded doors between a signed-in member and the workspace’s chats, agents, files, settings, memory, usage, and admin screens.

The main hub is surface.py. It serves the web app, signs members in, lets them chat with agents, streams replies as they arrive, and exposes the portal panels. panels.py connects web actions like button clicks and form submits to the project’s usual chat-style action system, and prepares safe settings data for editing agents. audience.py is the rulekeeper: it decides which members may see or talk to which agents, and gives admins tools to change access. listings.py provides reliable paging for long lists, so browsing does not skip or repeat items. community.py fetches public skill listings from skills.sh and reshapes them for display. openai_login.py guides members through OpenAI device-code sign-in and stores their credential. starters.py creates cached personalized start-screen suggestions. __init__.py simply makes this folder importable.

## Files in this stage

### Authenticated portal surface
The main web route layer serves the browser portal, enforces authenticated workspace access, and exposes chat, streaming, and workspace API endpoints.

### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling, live streaming, and scheduled background jobs`

This is the web surface: the boundary between a member's browser and the core UFO system. It is like the front desk of a shared workshop. It checks who you are, decides which rooms and tools you may see, takes your messages and files, sends them into the durable agent queue, and streams the agent's live work back to the page.

The file also serves the built portal assets and built app pages. It carefully publishes those assets to shared blob storage so a page loaded from one deployed version can still fetch its JavaScript or app files if a later pod answers the request. Sessions are kept in a signed cookie, not in URLs, so bearer tokens do not leak into browser history or logs.

Most route handlers follow the same pattern: authenticate the session, resolve the member's web audience (the agents and workspace views they are allowed to reach), validate request shape and size, then call the privileged SurfaceContext for the actual read or write. Chat messages can open new conversations, continue existing ones, answer agent questions, stop running turns, attach files, and comment into Slack or terminal conversations when allowed. Transcript code turns raw model/tool records into friendly chat bubbles, activity steps, file cards, app cards, questions, and subagent trees. The file also contains background jobs for conversation titles and first homepage seeding.

#### Function details

##### `load_assets`  (lines 311–321)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Reads the built portal asset files, such as JavaScript, CSS, fonts, and images, into memory. It only includes file types the server knows how to serve safely.

**Data flow**: It receives a directory path, scans the files in that directory, keeps only files with approved suffixes, reads their bytes, and returns a map from request name to file bytes and media type.

**Call relations**: This runs when the module is loaded. Later static-file handlers use the table it produced instead of reading arbitrary paths from disk.

*Call graph*: 1 external calls (glob).


##### `rum_config`  (lines 334–347)

```
def rum_config(environ: Mapping[str, str]) -> dict[str, str] | None
```

**Purpose**: Builds the browser monitoring configuration for Datadog Real User Monitoring, or turns monitoring off. It protects against half-configured deployments that would silently send recordings to the wrong place or nowhere.

**Data flow**: It reads environment variables, trims their values, returns None if none are set, raises an error if only some are set, and otherwise returns the complete configuration dictionary.

**Call relations**: portal_page calls it just before serving the portal shell, so each deployment can inject its own monitoring settings.

*Call graph*: called by 1 (portal_page).


##### `portal_shell`  (lines 350–359)

```
def portal_shell(html: str, config: Mapping[str, str] | None) -> str
```

**Purpose**: Injects the runtime monitoring configuration into the already-built HTML shell. It refuses to serve a build that does not contain the expected placeholder.

**Data flow**: It receives the HTML text and optional config, turns the config into safe JSON, replaces the single monitoring placeholder, and returns the final HTML.

**Call relations**: portal_page uses it after reading rum_config, so the static frontend bundle can stay the same while deployment-specific settings are filled in at request time.

*Call graph*: called by 1 (portal_page); 1 external calls (dumps).


##### `resolve_workspace`  (lines 372–414)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace an incoming web request belongs to before the route handler runs. It reads the signed session cookie, or the token posted during the one sign-in request, and redirects cold arrivals to sign-in.

**Data flow**: It reads cookies, request method, content type, form body when allowed, and query parameters. It returns a workspace id, a redirect or error response, or None for an unresolved request.

**Call relations**: The shared surface machinery calls this as the identify step. It delegates small parsing jobs to _framed_length, _form, and _chat_target, then route handlers later verify the member identity with _authenticate.

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 417–421)

```
def _chat_target(request: Request) -> UUID | None
```

**Purpose**: Extracts an optional conversation id from a sign-in arrival URL. This lets a link to a chat survive the trip through login.

**Data flow**: It reads the c query parameter, tries to parse it as a UUID, and returns that UUID or None.

**Call relations**: resolve_workspace calls it only when redirecting an unauthenticated browser to sign-in.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 424–434)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Finds a built portal asset for the requested static path. It avoids filesystem path tricks by looking up only preloaded asset names.

**Data flow**: It strips the static URL prefix, looks up the asset in the in-memory table, and returns a cached asset response or None if this build does not have it.

**Call relations**: static_asset calls it first; if it returns None, static_asset tries the shared blob store through _stored_asset.

*Call graph*: calls 1 internal fn (_asset_response); called by 1 (static_asset).


##### `_asset_response`  (lines 437–441)

```
def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response
```

**Purpose**: Creates the HTTP response for one static asset, including cache validation. It can answer 'not modified' when the browser already has the current content.

**Data flow**: It receives request headers, asset bytes, media type, and an ETag. It compares If-None-Match with the ETag and returns either a 304 response or the asset body.

**Call relations**: _static_response and _stored_asset both use this so local and stored assets behave the same way.

*Call graph*: called by 2 (_static_response, _stored_asset); 1 external calls (Response).


##### `load_apps`  (lines 467–500)

```
def load_apps(directory: Path) -> AppsBundle | None
```

**Purpose**: Reads the built app-page bundle that shipped with the web extension. It computes a content digest so app URLs can name the exact build they need.

**Data flow**: It receives a directory, walks all non-hidden files, reads their bytes, hashes the sorted path-and-byte contents, detects top-level app slugs, and returns an AppsBundle or None.

**Call relations**: This runs at import time. The apps function later enforces that the bundle exists before routes advertise or serve shipped app pages.

*Call graph*: 4 external calls (__init__, sha256, is_dir, rglob).


##### `apps`  (lines 506–512)

```
def apps() -> AppsBundle
```

**Purpose**: Returns the loaded app bundle or raises a clear build error. It keeps missing frontend builds from failing silently.

**Data flow**: It reads the module-level APPS value and either returns it or raises a RuntimeError naming the build command.

**Call relations**: agents_index, homepage, and _homepage_state call it when they need shipped app-page information.

*Call graph*: called by 3 (_homepage_state, agents_index, homepage).


##### `_publish_assets`  (lines 515–525)

```
async def _publish_assets(blob: BlobStore, apps: AppsBundle) -> None
```

**Purpose**: Copies this process's built portal and app assets into shared blob storage if they are not already there. This makes rolling deploys safe when old pages request old hashed files.

**Data flow**: It receives a blob store and app bundle, checks which keys already exist, and writes missing static and app files.

**Call relations**: _assets_published starts this work once per process and awaits it before serving pages that name those assets.

*Call graph*: calls 3 internal fn (exists, list, put); called by 1 (_assets_published).


##### `_assets_published`  (lines 528–555)

```
def _assets_published(blob: BlobStore, apps: AppsBundle) -> 'asyncio.Task[None]'
```

**Purpose**: Ensures the asset-publish task exists and retries it if the previous attempt failed. It prevents the portal from handing out asset URLs before the shared store can answer them.

**Data flow**: It reads the process-wide publish task, creates a new asyncio task when needed, stores it globally, and returns the task for callers to await.

**Call relations**: portal_page, agents_index, and homepage call it before serving shells or homepage links that depend on shared assets.

*Call graph*: calls 1 internal fn (_publish_assets); called by 3 (agents_index, homepage, portal_page); 1 external calls (create_task).


##### `_stored_asset`  (lines 558–580)

```
async def _stored_asset(blob: BlobStore, request: Request) -> Response
```

**Purpose**: Serves an asset from shared blob storage when the current process's build does not have it locally. This supports browsers holding pages from a different deployed version.

**Data flow**: It validates the requested asset name and suffix, checks a small in-process cache, fetches bytes from blob storage if needed, computes an ETag, and returns an asset response or 404.

**Call relations**: static_asset calls it after _static_response misses.

*Call graph*: calls 3 internal fn (exists, get, _asset_response); called by 1 (static_asset); 3 external calls (sha256, Path, Response).


##### `portal_page`  (lines 583–600)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main HTML page for the web portal to an authenticated member. It also ensures built assets are published before the page can ask for them.

**Data flow**: It checks that the frontend builds exist, awaits asset publication, injects monitoring config into the HTML shell, and returns a no-store HTML response.

**Call relations**: This is the GET route for the portal root and is the browser's entry into the web app after session resolution.

*Call graph*: calls 3 internal fn (_assets_published, portal_shell, rum_config); 1 external calls (HTMLResponse).


##### `_refused`  (lines 616–617)

```
def _refused(message: str) -> Response
```

**Purpose**: Creates a standard JSON response for account-connection refusals. It gives the frontend a consistent status and message shape.

**Data flow**: It takes a message string and returns JSON containing status 'refused' and that message.

**Call relations**: openai_device_poll and anthropic_code use it when an external account flow cannot continue.

*Call graph*: called by 2 (anthropic_code, openai_device_poll); 1 external calls (JSONResponse).


##### `workspace_accounts`  (lines 620–639)

```
async def workspace_accounts(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports whether the signed-in member has connected supported coding accounts such as OpenAI or Anthropic. It never returns the secret values.

**Data flow**: It authenticates the member, asks the context whether each credential slot is stored for them, and returns provider rows with connected booleans.

**Call relations**: The account settings and first-run screens call this route to draw current connection state.

*Call graph*: calls 2 internal fn (member_credential_stored, _authenticate); 1 external calls (JSONResponse).


##### `openai_device`  (lines 642–665)

```
async def openai_device(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts OpenAI's device-code sign-in flow. It gives the browser the short code the member types at OpenAI while hiding the longer device handle in an HttpOnly cookie.

**Data flow**: It authenticates the member, requests a device code from OpenAI, returns the user code and verification URL, and sets a cookie with the device flow state.

**Call relations**: The OpenAI connect UI calls this first; openai_device_poll later uses the cookie to claim the grant.

*Call graph*: calls 1 internal fn (_authenticate); 4 external calls (__init__, JSONResponse, set_session_cookie, openai_client_id).


##### `openai_device_poll`  (lines 668–685)

```
async def openai_device_poll(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Checks whether the member has finished the OpenAI device-code flow. When complete, it stores the resulting key in the member's private credential slot.

**Data flow**: It authenticates, reads the device-flow cookie, asks OpenAI login code to claim it, returns pending/refused when appropriate, or stores the key and returns connected.

**Call relations**: The browser polls this after openai_device starts the flow.

*Call graph*: calls 3 internal fn (put_member_credential, _authenticate, _refused); 4 external calls (__init__, JSONResponse, openai_client_id, log).


##### `anthropic_authorize`  (lines 688–699)

```
async def anthropic_authorize(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the Anthropic code-based authorization flow. It sends the member to Anthropic and keeps the verifier state in a cookie.

**Data flow**: It authenticates, creates an Anthropic authorization request, returns the external URL, and sets a state cookie.

**Call relations**: The Anthropic connect UI calls this before the member pastes the returned code to anthropic_code.

*Call graph*: calls 1 internal fn (_authenticate); 4 external calls (__init__, JSONResponse, set_session_cookie, anthropic_client_id).


##### `anthropic_code`  (lines 702–724)

```
async def anthropic_code(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Finishes the Anthropic connection by spending the pasted code and storing the resulting member credential. It verifies the key before saving it.

**Data flow**: It bounds and parses the form, authenticates, reads the pasted code and state cookie, claims the grant, verifies the access key, stores it, and returns connected or refused.

**Call relations**: This is the second half of anthropic_authorize's flow.

*Call graph*: calls 5 internal fn (put_member_credential, _authenticate, _form, _framed_length, _refused); 5 external calls (__init__, JSONResponse, anthropic_client_id, log, verified_key).


##### `account_disconnect`  (lines 727–740)

```
async def account_disconnect(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Removes one connected coding account from the signed-in member. It is for replacing revoked or rotated personal credentials.

**Data flow**: It authenticates, maps the provider path name to a credential slot, clears that member's slot, logs the event, and returns disconnected.

**Call relations**: Account settings calls this route; it only affects the member's own credential, not workspace or admin-held secrets.

*Call graph*: calls 2 internal fn (clear_member_credential, _authenticate); 2 external calls (JSONResponse, log).


##### `connect_arrival`  (lines 743–748)

```
async def connect_arrival(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Redirects account-connect arrival links into the portal's credential screen. It keeps all connection work inside the authenticated web app.

**Data flow**: It authenticates the request and returns a redirect to the portal with the credentials hash fragment.

**Call relations**: Sign-in and provider routes use this as a landing door after the session has resolved.

*Call graph*: calls 1 internal fn (_authenticate); 1 external calls (RedirectResponse).


##### `_authenticate`  (lines 751–775)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

**Purpose**: Verifies the session cookie and turns it into a member id and email. It also links the email to a member row on first use and checks that the member still has workspace access.

**Data flow**: It reads the session cookie, verifies it against the workspace, finds or creates the linked member, checks seat access, and returns member/email or an HTTP refusal.

**Call relations**: Most authenticated routes call this directly or through _audience_for.

*Call graph*: calls 3 internal fn (link_member, linked_member, member_has_access); called by 9 (_audience_for, account_disconnect, anthropic_authorize, anthropic_code, connect_arrival, fulfill_credential, openai_device, openai_device_poll, workspace_accounts); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 778–784)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves authenticated portal static files. It first tries this build's in-memory assets, then falls back to shared storage for assets from another build.

**Data flow**: It receives the request and context, asks _static_response for a local file, otherwise asks _stored_asset for a blob-backed file, and returns the response.

**Call relations**: This is the GET route under the portal static path.

*Call graph*: calls 2 internal fn (_static_response, _stored_asset).


##### `open_session`  (lines 787–818)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts a posted bearer token and stores it as the browser session cookie. The bearer travels in the form body, not in the URL.

**Data flow**: It bounds and parses the form, validates the token field shape, sets the session cookie, and redirects back to the portal URL.

**Call relations**: This is the one POST route that opens a web session after gateway sign-in or setup tooling.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 821–825)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Parses the agent id from a route path. It returns None when the path does not contain a valid UUID.

**Data flow**: It reads request path parameters, tries to build a UUID, and returns the UUID or None.

**Call relations**: Chat, transcript, panel, and member-chat gate functions use it before checking audience permissions.

*Call graph*: called by 4 (_member_chat_page, _panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 828–829)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the extension-store key for the web chat record belonging to one conversation. This gives web chats a stable lookup row.

**Data flow**: It receives a conversation id and returns a string key under the chat prefix.

**Call relations**: _open_conversation writes this key and _own_web_chat reads it.

*Call graph*: called by 2 (_open_conversation, _own_web_chat).


##### `_chat_title`  (lines 838–856)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates a short conversation title from the first message or attached filenames. It trims awkward endings so the rail label reads naturally.

**Data flow**: It receives message text and attachment paths, collapses whitespace, falls back to filenames when text is empty, cuts to the maximum title length, and returns the title.

**Call relations**: _open_conversation uses it for new chats, and summarize_chat_titles uses it to clean model-written summaries.

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_title_excerpt`  (lines 868–886)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

**Purpose**: Builds the small opening excerpt used to ask a model for a better chat title. It uses the first user text and first assistant answer when available.

**Data flow**: It receives transcript messages, extracts rendered text, strips user context wrapping, limits each side, and returns joined text or an empty string.

**Call relations**: summarize_chat_titles calls it for each candidate conversation.

*Call graph*: calls 1 internal fn (_rendered_text); called by 1 (summarize_chat_titles); 1 external calls (member_message_text).


##### `summarize_chat_titles`  (lines 889–935)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

**Purpose**: Background job that gives conversations better short titles after they have an opening exchange. It works across web, Slack, and terminal conversations.

**Data flow**: It asks for conversations needing titles, reads their transcripts, builds excerpts, optionally calls the model for a title, cleans that title, and records the title attempt.

**Call relations**: Scheduled infrastructure runs this job; it uses _title_excerpt and _chat_title before writing back through the extension context.

*Call graph*: calls 4 internal fn (conversations_awaiting_title, summarized_conversation_title, _chat_title, _title_excerpt); 2 external calls (__init__, __init__).


##### `seed_homepages`  (lines 938–1010)

```
async def seed_homepages(ctx: ExtensionContext, bucket: str | None=None) -> None
```

**Purpose**: Background job that asks each eligible agent to build its first homepage once. It marks agents that are already settled, archived, shipped, or successfully seeded.

**Data flow**: It reads workspace agents and existing markers, chooses an acting member, opens or reuses a conversation, invokes a homepage-building prompt, checks the outcome, and writes a marker.

**Call relations**: Scheduled infrastructure runs this job so agent home tabs eventually have useful pages without a member manually starting them.

*Call graph*: calls 5 internal fn (earliest_seated_admin, invoke, open_conversation, turn_outcomes, workspace_agents); 2 external calls (now, shipped_app_slug).


##### `_open_conversation`  (lines 1013–1046)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and records which agent/member it belongs to. It writes the web chat row before asking core to create the durable conversation.

**Data flow**: It receives agent, member, email, queue key, text, and attachment paths; writes a chat record, creates or finds the conversation, cleans up losing race rows, titles the conversation, and returns id/title.

**Call relations**: _new_chat_target calls it when the first message opens a new chat.

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_web_chat); called by 1 (_new_chat_target); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 1049–1056)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

**Purpose**: Reads the current title of one conversation from the standard conversation listing. It avoids keeping a second name source.

**Data flow**: It receives agent, member, and conversation ids, asks the context for that single listed conversation, and returns its title or an empty string.

**Call relations**: _open_conversation uses it when another process won the conversation-creation race.

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 1 (_open_conversation).


##### `_own_web_chat`  (lines 1059–1071)

```
async def _own_web_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is this member's own web chat with this agent. Anything mismatched is treated as not found.

**Data flow**: It reads the chat record from the extension store, validates it, compares agent id and email, and returns the record or None.

**Call relations**: _member_chat and _open_conversation use it as the web-specific ownership test.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 2 (_member_chat, _open_conversation).


##### `_member_chat`  (lines 1074–1119)

```
async def _member_chat(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, *, agent_visible: bool) -> ListedConversation | None
```

**Purpose**: Decides whether the member may continue or read a conversation through the portal. It covers normal web chats, private extension rooms, and commentable Slack or terminal conversations.

**Data flow**: It checks the web chat row, reads the conversation listing for the member, compares audience and surface rules, and returns the listed conversation or None.

**Call relations**: Chat target resolution, permalink resolution, transcript reads, stream authorization, and stop checks all rely on this one gate.

*Call graph*: calls 3 internal fn (list_agent_conversations, _commentable, _own_web_chat); called by 5 (_existing_chat_target, _member_chat_page, _member_turn, _resolve_chat, transcript); 1 external calls (conversation_audience).


##### `_commentable`  (lines 1122–1126)

```
def _commentable(conversation: ListedConversation, member_id: UUID) -> bool
```

**Purpose**: Tells whether a conversation can accept web comments from this member. Only selected surfaces and shared/member audiences qualify.

**Data flow**: It inspects the listed conversation's surface and audience and returns a boolean.

**Call relations**: _member_chat, _existing_chat_target, _member_turn, _resolve_chat, and _conversation_row use this to keep comment behavior consistent.

*Call graph*: called by 5 (_conversation_row, _existing_chat_target, _member_chat, _member_turn, _resolve_chat); 1 external calls (conversation_audience).


##### `_turn_context`  (lines 1129–1140)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

**Purpose**: Builds the extra context carried with an admitted chat turn, including sender email, browser timezone, and source label. Invalid timezone names are ignored rather than failing the send.

**Data flow**: It reads the timezone header and source string, creates a TurnContext with sender/source and maybe timezone, logs dropped invalid zones, and returns it.

**Call relations**: _admit_chat passes this context into core admission.

*Call graph*: called by 1 (_admit_chat); 2 external calls (__init__, log).


##### `_chat_url`  (lines 1143–1146)

```
def _chat_url(public_base_url: str | None, conversation_id: UUID) -> str | None
```

**Purpose**: Builds a public portal link to a conversation when the deployment has a public base URL. It returns None for private or unconfigured deployments.

**Data flow**: It receives a base URL and conversation id, trims the base, appends the portal path and hash route, and returns the URL or None.

**Call relations**: _chat_source and _comment_notice use it when describing where a portal message came from.

*Call graph*: called by 2 (_chat_source, _comment_notice).


##### `_chat_source`  (lines 1149–1158)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Creates the human-readable source string for a portal message. It includes a conversation link when possible and always includes the sender email.

**Data flow**: It receives base URL, conversation id, and email, builds a URL if possible, and returns either 'url (email)' or a fallback web source string.

**Call relations**: _admit_chat sends this into the turn context so downstream artifacts and logs know where the member spoke.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (_admit_chat).


##### `_comment_notice`  (lines 1161–1181)

```
def _comment_notice(public_base_url: str | None, conversation: ListedConversation, member_id: UUID, email: str, text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Builds the text that gets delivered into Slack or terminal conversations when a web member comments. It names the author, link, message text, and attachment names.

**Data flow**: It receives conversation, member, email, text, and paths, chooses an author label, builds an optional chat link, adds attachment names, and returns markdown text.

**Call relations**: _existing_chat_target creates this comment text when the target conversation is commentable.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (_existing_chat_target); 2 external calls (PurePosixPath, conversation_audience).


##### `_audience_for`  (lines 1184–1191)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Authenticates the request and computes the member's web audience: the agents and admin powers they can use in the portal.

**Data flow**: It calls _authenticate, then asks the web audience module for permissions based on the email, and returns member id, email, and audience or a refusal response.

**Call relations**: Nearly every route that reads or writes member-visible data starts here or through a gate helper.

*Call graph*: calls 1 internal fn (_authenticate); called by 27 (_member_chat_page, _member_turn, _object_gate, _panel_gate, action_views, admin_index, agents_index, agents_status, chat, chats_index (+15 more)); 2 external calls (web_audience, web_extension).


##### `_visibility_flag`  (lines 1218–1222)

```
def _visibility_flag(agent: AgentSummary) -> str | None
```

**Purpose**: Finds the feature flag that controls whether a particular agent is listed in the portal. Main and shipped app agents have different flag rules.

**Data flow**: It receives an agent summary, checks whether it is main or a shipped app, and returns the relevant flag key or None.

**Call relations**: _flag_reads and agents_index use it while building the boot-time agent list.

*Call graph*: called by 2 (_flag_reads, agents_index); 1 external calls (shipped_app_slug).


##### `_flag_reads`  (lines 1225–1245)

```
async def _flag_reads(agents: tuple[AgentSummary, ...]) -> dict[str, bool]
```

**Purpose**: Reads all feature flags needed for the portal boot response in one batch. Portal screens default open, while shipped app listings default closed until enabled.

**Data flow**: It collects unique flag keys from portal surfaces and agents, reads them concurrently, and returns a key-to-boolean map.

**Call relations**: agents_index calls it before deciding which screens and agents to mark visible or hidden.

*Call graph*: calls 1 internal fn (_visibility_flag); called by 1 (agents_index); 2 external calls (gather, flag_enabled).


##### `_setup_ready`  (lines 1248–1258)

```
def _setup_ready(state: SetupState) -> bool
```

**Purpose**: Decides whether an app's required setup is complete. An app is ready only when all declared connectors, credentials, and standing orders are settled.

**Data flow**: It receives a SetupState, checks each connector/grant, credential/fill, and standing-order/armed flag, and returns a boolean.

**Call relations**: agents_index uses it to mark setup_due, and workspace_starters uses it when offering starter rows for installed apps.

*Call graph*: called by 2 (agents_index, workspace_starters).


##### `agents_index`  (lines 1261–1382)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Builds the portal's first data payload: the signed-in member, visible agents, archived apps, homepage states, setup status, and enabled portal screens. This is the page's main boot read.

**Data flow**: It authenticates and gets audience, publishes assets, reads bound pages, setup states, archived agents, grants, and flags, then returns one JSON document for the frontend.

**Call relations**: The browser calls this after loading the shell. It coordinates helpers such as _bound_page, _homepage_state, _flag_reads, and _setup_ready.

*Call graph*: calls 9 internal fn (list_archived_agents, _assets_published, _audience_for, _bound_page, _flag_reads, _homepage_state, _setup_ready, _visibility_flag, apps); 6 external calls (Semaphore, gather, JSONResponse, shipped_app_slug, granted_emails, web_extension).


##### `agents_index.setup_of`  (lines 1311–1313)

```
async def setup_of(agent: AgentSummary) -> SetupState
```

**Purpose**: Reads setup state for one provisioned agent while limiting concurrency. It prevents a large workspace from opening too many setup reads at once.

**Data flow**: It receives an agent from the enclosing loop, waits for the semaphore, asks the context for setup state, and returns that state.

**Call relations**: agents_index creates many of these small tasks when building setup_due and stands_on_setup.


##### `agents_status`  (lines 1385–1426)

```
async def agents_status(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports live status for each visible agent, such as whether a turn is running, recent activity text, last activity time, and last failure. The portal polls it while open.

**Data flow**: It authenticates, asks for turn status across audience agents, peeks latest activity for running turns, and returns status rows.

**Call relations**: This route complements agents_index by providing frequently changing state without reloading the whole boot payload.

*Call graph*: calls 4 internal fn (agent_turn_statuses, latest_activity, _audience_for, _iso); 1 external calls (JSONResponse).


##### `_framed_length`  (lines 1429–1442)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Rejects whole-body form parses that cannot be safely bounded. It requires a real Content-Length and refuses chunked or oversized requests.

**Data flow**: It reads transfer and length headers, returns a length-required or too-large response when unsafe, otherwise returns None.

**Call relations**: Form-parsing routes call it before _form so malformed or unbounded bodies are stopped early.

*Call graph*: called by 6 (_parse_inbound, anthropic_code, fulfill_credential, open_session, preview, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 1445–1452)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses an HTTP form body and turns parser failures into a clear bad-request response. This avoids internal parser exceptions leaking out.

**Data flow**: It calls request.form, returns the parsed form on success, or a 400 response for malformed form data.

**Call relations**: Sign-in, chat multipart parsing, credential fulfillment, account flows, and preview routes use it after size checks.

*Call graph*: called by 6 (_parse_inbound, anthropic_code, fulfill_credential, open_session, preview, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 1455–1463)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a request body with a hard byte limit based on actual bytes received. It protects routes from trusting wrong or missing length headers.

**Data flow**: It streams chunks from the request into a buffer, stops with a 413 response if the limit is exceeded, and otherwise returns the bytes.

**Call relations**: _parse_inbound and upload_start use it for plain message bodies and small JSON requests.

*Call graph*: called by 2 (_parse_inbound, upload_start); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 1466–1519)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...], tuple[str, ...]] | Response
```

**Purpose**: Parses the chat composer submission into message text, inline uploaded files, and already-presigned upload keys. It supports plain text and multipart bodies while rejecting unsupported shapes.

**Data flow**: It inspects content type, reads plain bodies under a byte cap or parses bounded multipart forms, validates upload-key references and file count, and returns text/files/keys or a response.

**Call relations**: _chat_inbound calls it as the main request-body parser before chat admission.

*Call graph*: calls 4 internal fn (_bounded_body, _form, _framed_length, _uploaded_key); called by 1 (_chat_inbound); 1 external calls (Response).


##### `_uploaded_key`  (lines 1522–1531)

```
def _uploaded_key(raw: str) -> str | None
```

**Purpose**: Validates that a submitted uploaded_key points only under the web inbox upload prefix. This stops a browser from naming arbitrary blob-store objects.

**Data flow**: It receives a raw key string, checks it remains contained under the upload root, and returns the key or None.

**Call relations**: _parse_inbound calls it for each uploaded_key form part.

*Call graph*: called by 1 (_parse_inbound); 1 external calls (contained_relative).


##### `_inbox_paths`  (lines 1534–1545)

```
def _inbox_paths(uploads: tuple[UploadFile, ...], uploaded_keys: tuple[str, ...]) -> tuple[str, ...]
```

**Purpose**: Chooses safe workspace paths for files attached to one chat message. Duplicate names are numbered instead of overwritten.

**Data flow**: It receives inline uploads and presigned keys, derives filenames, runs them through inbox_name, and returns paths under web-inbox.

**Call relations**: _chat_inbound uses these paths before _deliver_uploads writes the bytes.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (PurePosixPath, inbox_name).


##### `_deliver_uploads`  (lines 1548–1563)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], uploaded_keys: tuple[str, ...], paths: tuple[str, ...]) -> None
```

**Purpose**: Copies every attached file into the conversation workspace before the agent turn starts. This ensures the sandbox can see the files named in the message.

**Data flow**: It receives uploads, blob keys, destination paths, streams inline files or blob streams, and writes each to the workspace through the context.

**Call relations**: _admit_chat calls it just before admitting the message.

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (_admit_chat).


##### `_files_note`  (lines 1566–1569)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Adds a hidden plain-text note naming saved attachment paths to an admitted message. The agent can read where the files were placed.

**Data flow**: It receives message text and paths, formats the attachment note, and returns the original text plus note or just the note.

**Call relations**: _chat_inbound uses it when a message includes attachments.

*Call graph*: called by 1 (_chat_inbound).


##### `_member_attachments`  (lines 1577–1585)

```
def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]
```

**Purpose**: Splits a displayed member message back into the member's words and the attachment paths note. This lets the UI draw files as cards instead of showing the storage sentence.

**Data flow**: It receives admitted text, searches for the attachment note at the end, and returns cleaned words plus a tuple of paths.

**Call relations**: _member_bubble uses it while rendering transcripts.

*Call graph*: called by 1 (_member_bubble).


##### `_attachment_preview`  (lines 1588–1600)

```
def _attachment_preview(public_base_url: str | None, agent_id: UUID, conversation_id: UUID, path: str) -> str | None
```

**Purpose**: Builds the URL for previewing an attached image in chat. It only does this for raster image types and deployments with a public base URL.

**Data flow**: It receives base URL, agent id, conversation id, and path, checks whether the path is a raster image, quotes the path, and returns a preview URL or None.

**Call relations**: _transcript_aids and _conversation_messages pass it as the attachment resolver used by _member_bubble.

*Call graph*: 2 external calls (raster_image_media_type, quote).


##### `_attachment_payload`  (lines 1603–1616)

```
def _attachment_payload(path: str, preview_url: str | None) -> dict[str, object]
```

**Purpose**: Creates the frontend payload for one member-attached file. It includes filename, media type, and optional preview URL but no download URL.

**Data flow**: It receives a workspace path and preview URL, derives filename and media type, and returns a dictionary for the chat UI.

**Call relations**: _member_bubble calls it for every path extracted by _member_attachments.

*Call graph*: called by 1 (_member_bubble); 2 external calls (PurePosixPath, raster_image_media_type).


##### `_member_bubble`  (lines 1619–1627)

```
def _member_bubble(said: str, attach: Attach | None) -> dict[str, object]
```

**Purpose**: Builds one displayed user chat bubble, including attached file cards when present. It hides the internal attachment note from the member.

**Data flow**: It receives admitted text and an optional attachment-preview function, separates words from attachment paths, builds a user bubble, and adds file payloads if needed.

**Call relations**: _TranscriptRenderer._member and _conversation_messages use it for transcript and live pending-message rendering.

*Call graph*: calls 2 internal fn (_attachment_payload, _member_attachments); called by 2 (_member, _conversation_messages).


##### `_upload_chunks`  (lines 1630–1632)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded file in fixed-size chunks. This avoids reading large inline attachments into memory all at once.

**Data flow**: It repeatedly reads chunks from an UploadFile until empty and yields each bytes chunk.

**Call relations**: _deliver_uploads passes this stream to workspace-file writing for inline form uploads.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_key`  (lines 1635–1640)

```
def _answer_key(conversation_id: UUID, turn_id: UUID, index: int) -> str
```

**Purpose**: Creates the idempotency key for a member's answer to a specific agent question. This makes double-clicked answers land only once.

**Data flow**: It receives conversation id, asking turn id, and question index, and returns a stable string key.

**Call relations**: _admit_chat uses it when admitting an answer, and _asks uses the same key to find answers later.

*Call graph*: called by 2 (_admit_chat, _asks).


##### `_answer_headers`  (lines 1643–1655)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Reads headers that say a chat message is answering an agent question. It validates them before anything is admitted.

**Data flow**: It reads answer-turn and answer-question headers, returns None for ordinary messages, a parsed turn/id pair for answers, or a bad-request response.

**Call relations**: _chat_inbound calls it after parsing the body.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 1658–1667)

```
def _stop_header(request: Request) -> UUID | None | Response
```

**Purpose**: Reads the header that says a request is stopping a running turn instead of sending a message. It validates the turn id early.

**Data flow**: It reads the stop-turn header, returns None if absent, a UUID if valid, or a bad-request response if malformed.

**Call relations**: _chat_inbound calls it before enforcing message/stop rules.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (Response, UUID).


##### `_chat_inbound`  (lines 1688–1710)

```
async def _chat_inbound(ctx: SurfaceContext, request: Request) -> _ChatInbound | Response
```

**Purpose**: Turns a raw chat POST into a validated internal chat input. It enforces stop-message exclusivity, non-empty sends, upload existence, path naming, length limits, and answer metadata.

**Data flow**: It reads the stop header, parses the body, checks uploaded blobs, builds inbox paths and final body text, reads answer headers, and returns _ChatInbound or an HTTP response.

**Call relations**: chat calls it before resolving the target conversation.

*Call graph*: calls 5 internal fn (_answer_headers, _files_note, _inbox_paths, _parse_inbound, _stop_header); called by 1 (chat); 2 external calls (__init__, Response).


##### `_new_chat_target`  (lines 1713–1738)

```
async def _new_chat_target(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, inbound: _ChatInbound) -> _ChatTarget | Response
```

**Purpose**: Prepares a target for a new conversation opened by the first message. It refuses answers or stops because those must name an existing conversation.

**Data flow**: It checks audience access to the agent, opens the conversation through _open_conversation, and returns conversation id, title, and no comment text.

**Call relations**: _resolve_chat_target calls it when the query parameter is the new-conversation sentinel.

*Call graph*: calls 2 internal fn (allows, _open_conversation); called by 1 (_resolve_chat_target); 3 external calls (__init__, Response, uuid4).


##### `_existing_chat_target`  (lines 1741–1772)

```
async def _existing_chat_target(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, inbound: _ChatInbound) -> _ChatTarge
```

**Purpose**: Prepares a target for a message into an existing conversation. If the target is commentable, it also builds the comment text for the foreign surface.

**Data flow**: It checks _member_chat authorization, optionally builds a comment notice, and returns a _ChatTarget or a not-found response.

**Call relations**: _resolve_chat_target calls it for UUID conversation parameters.

*Call graph*: calls 4 internal fn (allows, _comment_notice, _commentable, _member_chat); called by 1 (_resolve_chat_target); 2 external calls (__init__, Response).


##### `_resolve_chat_target`  (lines 1775–1796)

```
async def _resolve_chat_target(ctx: SurfaceContext, request: Request, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, inbound: _ChatInbound) -> _ChatTarget | Response
```

**Purpose**: Decides whether a chat POST opens a new conversation or continues an existing one. It is the conversation-selection step of chat admission.

**Data flow**: It reads the conversation query parameter, gets the web extension store, routes to _new_chat_target for 'new', parses UUIDs for existing conversations, and returns a target or response.

**Call relations**: chat calls it after parsing and validating the inbound message.

*Call graph*: calls 2 internal fn (_existing_chat_target, _new_chat_target); called by 1 (chat); 3 external calls (Response, web_extension, UUID).


##### `_stop_chat`  (lines 1799–1812)

```
async def _stop_chat(ctx: SurfaceContext, request: Request, conversation_id: UUID, turn_id: UUID) -> Response
```

**Purpose**: Stops a running turn in a conversation when the member presses stop. It does not add a message to the transcript.

**Data flow**: It verifies the member may reach the turn, asks core to stop it in the conversation, and returns whether it ended and any founded turn id.

**Call relations**: chat calls it when _chat_inbound found a stop-turn header.

*Call graph*: calls 2 internal fn (stop_turn, _member_turn); called by 1 (chat); 2 external calls (JSONResponse, Response).


##### `_admit_chat`  (lines 1815–1855)

```
async def _admit_chat(ctx: SurfaceContext, request: Request, target: _ChatTarget, inbound: _ChatInbound, member_id: UUID, email: str) -> Response
```

**Purpose**: Admits a validated chat message into the durable conversation queue. It also delivers attachments first and returns the ids the frontend needs to stream or wait.

**Data flow**: It builds an optional answer idempotency key, writes attachment files, creates turn context, calls ctx.admit with body/member/comment, and returns turn/conversation/title/opened-run information.

**Call relations**: chat calls it for normal sends after resolving the target.

*Call graph*: calls 6 internal fn (admit, admitted_body, _answer_key, _chat_source, _deliver_uploads, _turn_context); called by 1 (chat); 1 external calls (JSONResponse).


##### `chat`  (lines 1858–1893)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Main POST route for sending a web chat message, answering a question, commenting into a supported outside conversation, opening a new conversation, or stopping a turn. It is the browser's write path into agent conversations.

**Data flow**: It authenticates and checks chat access to the agent, parses inbound content, resolves the conversation target, then either stops a turn or admits the message.

**Call relations**: The frontend composer calls this route; it coordinates _chat_inbound, _resolve_chat_target, _stop_chat, and _admit_chat.

*Call graph*: calls 6 internal fn (_admit_chat, _agent_param, _audience_for, _chat_inbound, _resolve_chat_target, _stop_chat); 1 external calls (Response).


##### `_rendered_text`  (lines 1896–1907)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Extracts readable text from a stored model message. For user messages it strips the engine's context wrappers so the UI shows what the member actually said.

**Data flow**: It receives a Message, pulls text from either a string body or text blocks, removes context wrappers for user messages, trims it, and returns text.

**Call relations**: Transcript rendering and title generation call it before displaying or summarizing messages.

*Call graph*: called by 3 (_assistant, _member, _title_excerpt).


##### `_append_activity`  (lines 1910–1911)

```
def _append_activity(events: list[dict[str, str]], text: str) -> None
```

**Purpose**: Adds one activity event to an event list in the shape the chat UI expects.

**Data flow**: It receives a list and text, appends a dictionary with kind 'activity', and changes the list in place.

**Call relations**: _TranscriptRenderer._assistant and _subagent_activity use it while building tool-work timelines.

*Call graph*: called by 2 (_assistant, _subagent_activity).


##### `_stored_activity`  (lines 1914–1926)

```
def _stored_activity(block: ToolUseBlock, result: ToolResultBlock) -> str | None
```

**Purpose**: Chooses the best human-readable activity text for a stored tool call. It prefers explicit activity text, then user descriptions, then special names, then the tool name.

**Data flow**: It receives a tool-use block and its result block, inspects their fields, and returns display text or None when activity should be hidden.

**Call relations**: Assistant transcript rendering and subagent activity rendering use it to turn tool calls into understandable progress steps.

*Call graph*: called by 2 (_assistant, _subagent_activity).


##### `_subagent_activity`  (lines 1948–1972)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

**Purpose**: Builds the visible work log for a subagent run from its transcript. It captures notes and tool activities, not the final answer.

**Data flow**: It receives messages, matches tool-use blocks with activity results, collects note and activity events in order, limits the count, and returns them.

**Call relations**: _subagent_nodes calls it after reading each spawned run's transcript.

*Call graph*: calls 2 internal fn (_append_activity, _stored_activity); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 1975–1985)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

**Purpose**: Attempts to decode a run's final answer as a structured JSON object. It returns None when the answer is ordinary text.

**Data flow**: It receives an answer string, parses JSON if possible, and returns a dictionary payload or None.

**Call relations**: _run_answer calls it before deciding how to display subagent or profile-run answers.

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 1988–2011)

```
def _payload_prose(value: JsonValue) -> str
```

**Purpose**: Turns structured JSON values into readable prose for the chat UI. It makes lists, objects, booleans, and numbers legible instead of showing raw JSON.

**Data flow**: It receives a JSON-like value, recursively formats strings, lists, and dictionaries, and returns plain or markdown text.

**Call relations**: _run_answer uses it when a finish payload has multiple fields or non-simple content.

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 2014–2030)

```
def _run_answer(answer: str) -> str
```

**Purpose**: Returns the text a spawned run should display as its answer. It unwraps simple structured payloads and formats richer payloads into prose.

**Data flow**: It receives the raw answer string, tries _finish_payload, then returns plain text, a single string field, or formatted payload prose.

**Call relations**: _subagent_nodes and _TranscriptAids.render use it for subagent/profile-run conversations.

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (render, _subagent_nodes).


##### `_subagent_nodes`  (lines 2033–2072)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

**Purpose**: Builds a nested tree of subagent runs spawned by conversation turns. Each node includes its target, conversation id, work events, answer, and child runs.

**Data flow**: It receives turns, identifies spawned turns, fills names and answers, reads a bounded set of transcripts concurrently for activity, links children to parents, and returns a parent-keyed tree.

**Call relations**: _transcript_aids uses it for settled transcripts, and _events uses it when a live turn ends.

*Call graph*: calls 4 internal fn (list_agents, read_transcript, _run_answer, _subagent_activity); called by 2 (_events, _transcript_aids); 2 external calls (__init__, gather).


##### `_ReplyState.note_answer`  (lines 2083–2089)

```
def note_answer(self) -> '_ReplyState'
```

**Purpose**: Moves an accumulated assistant answer into the activity-note list when later work shows it was not the final answer. This keeps multi-step replies in the right visual order.

**Data flow**: It copies the pending event list, inserts the current answer as a note if allowed, clears the answer text, updates the note count, and returns a new state.

**Call relations**: _TranscriptRenderer._assistant and _TranscriptRenderer._member call it while walking transcript messages.

*Call graph*: called by 2 (_assistant, _member); 1 external calls (replace).


##### `_TranscriptRenderer.render`  (lines 2106–2122)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns raw transcript messages into frontend chat bubbles and reply cards. It groups assistant text, tool activity, user messages, questions, files, apps, connects, and subagents around the right turns.

**Data flow**: It builds a tool-activity lookup, walks messages in order through assistant/member handlers, flushes the final reply, and returns rendered message dictionaries.

**Call relations**: _rendered_messages creates a renderer and calls this as the central transcript projection.

*Call graph*: calls 3 internal fn (_assistant, _flush, _member); 1 external calls (__init__).


##### `_TranscriptRenderer._assistant`  (lines 2124–2142)

```
def _assistant(self, message: Message, activity: Mapping[str, ToolResultBlock], state: _ReplyState) -> _ReplyState
```

**Purpose**: Processes one assistant message during transcript rendering. It decides whether text is an answer or a note and adds visible activity steps for tool calls.

**Data flow**: It receives a message, activity lookup, and reply state; extracts text, updates answer state, scans tool blocks, appends activity events, and returns the new state.

**Call relations**: _TranscriptRenderer.render calls it for assistant-role messages.

*Call graph*: calls 4 internal fn (note_answer, _append_activity, _rendered_text, _stored_activity); called by 1 (render); 1 external calls (replace).


##### `_TranscriptRenderer._member`  (lines 2144–2174)

```
def _member(self, message: Message, state: _ReplyState, rendered: list[dict[str, object]]) -> _ReplyState
```

**Purpose**: Processes one member/user message during transcript rendering. It closes the previous assistant reply when needed and adds the member bubble unless another card already states that answer.

**Data flow**: It receives a message, state, and output list; extracts text and turn reference, flushes assistant state, skips agent-origin or already-stated answers, builds a user bubble, labels speaker/asked question, and appends it.

**Call relations**: _TranscriptRenderer.render calls it for non-assistant messages.

*Call graph*: calls 4 internal fn (note_answer, _flush, _member_bubble, _rendered_text); called by 1 (render); 2 external calls (replace, member_message_text).


##### `_TranscriptRenderer._flush`  (lines 2176–2213)

```
def _flush(self, state: _ReplyState, rendered: list[dict[str, object]], *, include_subagents: bool) -> _ReplyState
```

**Purpose**: Writes the current assistant reply to the rendered output if it has anything visible. It attaches related subagents, questions, files, apps, and connect controls to the reply.

**Data flow**: It receives current state and output list, gathers turn-linked extras, returns unchanged state if empty, otherwise appends a reply dictionary and clears reply state.

**Call relations**: _TranscriptRenderer._member and render call it whenever a turn boundary or transcript end is reached.

*Call graph*: called by 2 (_member, render); 1 external calls (replace).


##### `_rendered_messages`  (lines 2216–2291)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

**Purpose**: Convenience wrapper that renders messages with all optional transcript aids. It keeps every transcript screen using the same projection rules.

**Data flow**: It receives messages plus optional maps for subagents, speakers, questions, files, apps, connects, attachment previews, and answers, builds a _TranscriptRenderer, and returns rendered messages.

**Call relations**: _TranscriptAids.render calls it after gathering the side data a transcript needs.

*Call graph*: called by 1 (render); 1 external calls (__init__).


##### `_asks`  (lines 2310–2342)

```
def _asks(conversation_id: UUID, turns: tuple[Turn, ...], admitted: tuple[KeyedAdmission, ...]) -> _Asks
```

**Purpose**: Builds the question cards for turns that asked the member something, including answers already given. Older unanswered questions disappear because they are no longer actionable.

**Data flow**: It receives conversation id, turns, and keyed admissions, matches answer keys to admissions, creates per-turn card dictionaries, and returns cards plus message refs already stated by cards.

**Call relations**: _transcript_aids calls it so transcript rendering can attach questions and avoid duplicate answer bubbles.

*Call graph*: calls 1 internal fn (_answer_key); called by 1 (_transcript_aids); 2 external calls (__init__, member_message_text).


##### `_TranscriptAids.render`  (lines 2365–2384)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Renders a transcript using the gathered side information, with special answer formatting for subagent/profile-run conversations. It is the high-level transcript renderer object.

**Data flow**: It passes its maps and attachment resolver to _rendered_messages, optionally rewrites assistant text through _run_answer, and returns rendered bubbles.

**Call relations**: _conversation_messages and _history_messages use _transcript_aids to get an instance, then call this method.

*Call graph*: calls 2 internal fn (_rendered_messages, _run_answer).


##### `_transcript_aids`  (lines 2387–2439)

```
async def _transcript_aids(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, agent_origin: frozenset[str], speakers: dict[str, str], asked: dict[str, str], opens: frozenset[UUI
```

**Purpose**: Collects all non-message data needed to render a conversation consistently. This includes turns, subagents, shared files, answered questions, created apps, connect controls, speakers, and attachments.

**Data flow**: It reads turns, spawned turns, artifacts, and keyed admissions concurrently, builds file/app/question/speaker/connect maps, creates an attachment preview function, and returns _TranscriptAids.

**Call relations**: _conversation_messages and _history_messages call it before rendering live or older transcript windows.

*Call graph*: calls 9 internal fn (conversation_subagent_turns, keyed_admissions, list_conversation_artifacts, list_turns, _asks, _connect_controls, _created_apps, _file_payload, _subagent_nodes); called by 2 (_conversation_messages, _history_messages); 3 external calls (__init__, gather, partial).


##### `_connect_controls`  (lines 2442–2482)

```
async def _connect_controls(ctx: SurfaceContext, conversation_id: UUID, turns: tuple[Turn, ...], viewer: UUID) -> dict[str, dict[str, object]]
```

**Purpose**: Builds the connect-account controls that a transcript should show for the viewing member. Completed requests show the account that landed; open requests show a fresh press target.

**Data flow**: It checks whether connect support exists, scans turns for connect requests belonging to the viewer, optionally reads held accounts, and returns controls keyed by turn id.

**Call relations**: _transcript_aids calls it so connection handoffs appear on the assistant reply that requested them.

*Call graph*: calls 4 internal fn (connect_available, held_accounts, _connect_control, _provider_label); called by 1 (_transcript_aids).


##### `_conversation_messages`  (lines 2485–2607)

```
async def _conversation_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], Turn | None, int]
```

**Purpose**: Produces the rendered messages for a conversation, including settled transcript, running prompt, queued messages, compacted-history cursor, and pending waits. This is the shared projection for chat and read-only transcript pages.

**Data flow**: It reads transcript, origin refs, speakers, compactions, latest turn, turn detail, and queued arrivals; gathers aids when there is a transcript; appends live prompt and queued bubbles; and returns messages, latest turn, and earlier-history index.

**Call relations**: transcript and conversation_transcript call it so both live chat and panel transcripts display the same way.

*Call graph*: calls 10 internal fn (agent_origin_refs, arrival_speakers, latest_turn, list_compactions, queued_arrivals, read_transcript, turn_detail, _member_bubble, _transcript_aids, _verified_earlier); called by 2 (conversation_transcript, transcript); 3 external calls (gather, partial, member_message_text).


##### `_verified_earlier`  (lines 2610–2626)

```
async def _verified_earlier(ctx: SurfaceContext, conversation_id: UUID, indices: tuple[int, ...], messages: tuple[Message, ...]) -> int
```

**Purpose**: Finds the newest compacted-history page that genuinely sits above the current transcript window. It avoids offering history pages that would duplicate visible messages.

**Data flow**: It receives compaction indices and current messages, reads candidate 'after' windows newest first, compares them to the message prefix, and returns a verified index or 0.

**Call relations**: _conversation_messages uses it for the live tail cursor, and _history_messages uses it for older-page chaining.

*Call graph*: calls 1 internal fn (read_compaction_after); called by 2 (_conversation_messages, _history_messages).


##### `_history_cursor`  (lines 2629–2631)

```
def _history_cursor(index: int, end: int | None=None) -> str
```

**Purpose**: Encodes a compacted-history position into an opaque cursor string. The cursor can name a compaction index and optional ending message position.

**Data flow**: It receives an index and optional end, formats them as text, base64-url encodes them, strips padding, and returns the cursor.

**Call relations**: Transcript and history pagination helpers return these cursors to the browser.

*Call graph*: called by 4 (fits, _history_messages, conversation_transcript, transcript); 1 external calls (urlsafe_b64encode).


##### `_history_position`  (lines 2634–2649)

```
def _history_position(cursor: str) -> tuple[int, int | None]
```

**Purpose**: Decodes and validates an earlier-history cursor. Invalid or oversized cursors are rejected.

**Data flow**: It receives a cursor string, restores base64 padding, decodes it, validates numeric index/end fields, and returns them or raises ValueError.

**Call relations**: _history_messages calls it before reading a compaction page.

*Call graph*: called by 1 (_history_messages); 1 external calls (b64decode).


##### `_bounded_history_page`  (lines 2652–2674)

```
def _bounded_history_page(messages: list[dict[str, object]], index: int, end: int) -> tuple[list[dict[str, object]], int]
```

**Purpose**: Cuts a rendered history window into a page that fits both message-count and byte-size limits. This keeps old-history responses safe and predictable.

**Data flow**: It receives rendered messages, compaction index, and end position, tests JSON response sizes, binary-searches the earliest fitting start when needed, and returns the page and start index.

**Call relations**: _history_messages calls it after rendering a compacted window.

*Call graph*: called by 1 (_history_messages).


##### `_bounded_history_page.fits`  (lines 2657–2662)

```
def fits(start: int) -> bool
```

**Purpose**: Checks whether a candidate history slice fits the byte limit once packaged as JSON. It models the exact response shape the route will send.

**Data flow**: It receives a start position from the enclosing function, builds a messages payload with an earlier cursor, serializes it as a JSONResponse, and returns whether it is small enough.

**Call relations**: _bounded_history_page uses it directly and inside binary search.

*Call graph*: calls 1 internal fn (_history_cursor); 1 external calls (JSONResponse).


##### `_history_messages`  (lines 2677–2728)

```
async def _history_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, cursor: str, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], str | None] | None
```

**Purpose**: Reads and renders one older page of a compacted conversation. It trims the kept tail so pages connect to the live transcript without duplicate messages.

**Data flow**: It decodes the cursor, reads the compaction record, builds the renderable window, gathers speaker/origin data and transcript aids, renders it, slices it with _bounded_history_page, and returns messages plus a cursor above.

**Call relations**: _conversation_history calls it for cursor-based transcript pagination.

*Call graph*: calls 8 internal fn (agent_origin_refs, arrival_speakers, read_compaction, _bounded_history_page, _history_cursor, _history_position, _transcript_aids, _verified_earlier); called by 1 (_conversation_history); 1 external calls (gather).


##### `transcript`  (lines 2731–2775)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the current chat transcript for one member-accessible conversation with an agent. It also tells the frontend which turn is still running or which credential handoff remains open.

**Data flow**: It authenticates, checks chat access and conversation ownership, renders messages through _conversation_messages, adds an earlier cursor if present, and includes live turn or handoff data.

**Call relations**: The chat page calls this on load or reload before connecting to a live stream.

*Call graph*: calls 7 internal fn (_agent_param, _audience_for, _conversation_messages, _history_cursor, _member_chat, _open_handoffs, _opens); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 2778–2791)

```
async def _open_handoffs(ctx: SurfaceContext, terminal: TerminalFrame, member_id: UUID) -> dict[str, object]
```

**Purpose**: Reports terminal handoffs that still need member action after a page reload. Currently this covers pending credential prompts.

**Data flow**: It receives a terminal frame and member id, renews pending credential prompts when present, and returns a small handoff dictionary.

**Call relations**: transcript calls it when the newest turn has already ended.

*Call graph*: calls 1 internal fn (_pending_prompts); called by 1 (transcript).


##### `_connect_control`  (lines 2794–2798)

```
def _connect_control(ctx: SurfaceContext, provider: str, turn_id: UUID) -> dict[str, object]
```

**Purpose**: Creates the frontend payload for a connect-account button tied to one turn. It names the provider and turn, not a stale external consent URL.

**Data flow**: It receives context, provider, and turn id, resolves the provider label, and returns a dictionary for the UI.

**Call relations**: _connect_controls uses it for transcript rendering, and _events uses it during live streaming.

*Call graph*: calls 1 internal fn (_provider_label); called by 2 (_connect_controls, _events).


##### `_provider_label`  (lines 2801–2812)

```
def _provider_label(ctx: SurfaceContext, provider: str) -> str
```

**Purpose**: Finds the human-friendly provider name the portal should display. It prefers the curated first-run catalog and falls back to deploy-provided labels or the raw slug.

**Data flow**: It scans provider tiles, optionally asks the context for a connect label, and returns a string label.

**Call relations**: Connect controls and agent_setup call it so setup and chat use the same provider wording.

*Call graph*: calls 2 internal fn (connect_available, connect_label); called by 3 (_connect_control, _connect_controls, agent_setup).


##### `_provider_summary`  (lines 2815–2822)

```
def _provider_summary(provider: str) -> str
```

**Purpose**: Returns the short explanation for a curated provider. Uncurated providers get an empty summary.

**Data flow**: It scans first-run provider tiles for the provider name and returns its summary or an empty string.

**Call relations**: agent_setup uses it when describing connector requirements.

*Call graph*: called by 1 (agent_setup).


##### `chats_index`  (lines 2825–2837)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Resolves a chat permalink to either a rail chat row or a conversation projection. It exists for links like '#/c/<conversation-id>'.

**Data flow**: It authenticates, reads the conversation query parameter, gets the web store, and delegates resolution to _resolve_chat.

**Call relations**: The frontend calls this when opening a conversation id directly rather than loading a full rail list.

*Call graph*: calls 2 internal fn (_audience_for, _resolve_chat); 2 external calls (Response, web_extension).


##### `_resolve_chat`  (lines 2840–2928)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

**Purpose**: Figures out what a conversation permalink should open for this member. It can return a web chat, a commentable external conversation, an admin-readable conversation, or nothing.

**Data flow**: It parses the requested UUID, checks each chat-capable agent with _member_chat, reads latest turn details when needed, otherwise checks the conversation's agent and listing, and returns JSON rows.

**Call relations**: chats_index delegates all permalink logic here.

*Call graph*: calls 9 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, allows, _commentable, _conversation_row, _iso, _member_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 2931–2944)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Common authorization gate for per-agent panel reads and intent routes. It authenticates, resolves audience, parses the path agent, and checks that the agent is visible.

**Data flow**: It calls _audience_for, parses the agent id, tests audience access, and returns member/email/audience/agent id or a response.

**Call relations**: Settings, setup, skills, community, conversations, connections, actions, intents, homepage, and readable-conversation checks use it.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 11 (_readable_conversation, actions, agent_setup, community_skill, community_skills, connections, conversations, homepage, intents, settings (+1 more)); 1 external calls (Response).


##### `_iso`  (lines 2947–2948)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Converts optional datetimes into ISO strings for JSON responses. None stays None.

**Data flow**: It receives a datetime or None and returns moment.isoformat() or None.

**Call relations**: Many response builders use it for consistent timestamp formatting.

*Call graph*: called by 6 (_conversation_row, _memory_rows, _resolve_chat, _usage_payload, agents_status, object_detail); 1 external calls (isoformat).


##### `_window_param`  (lines 2951–2967)

```
def _window_param(request: Request) -> int | None | Response
```

**Purpose**: Parses the requested usage-report time window. It accepts named ranges or a bounded number of seconds.

**Data flow**: It reads range or window_seconds query parameters, validates them, and returns seconds, None for all time, or a response explaining the error.

**Call relations**: workspace_usage calls it before reading spend reports.

*Call graph*: called by 1 (workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 2970–3010)

```
def _usage_payload(report: MemberSpendReport | SpendReport) -> dict[str, object]
```

**Purpose**: Formats a member or workspace spend report into the JSON shape the usage page expects. It includes totals, daily lines, and model/execution breakdowns.

**Data flow**: It receives a spend report, reads nested usage details, formats timestamps with _iso, and returns a dictionary.

**Call relations**: workspace_usage uses it for both the member's own usage and the admin workspace rollup.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_usage).


##### `skills`  (lines 3013–3037)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the skills available to the selected agent. These include workspace-authored and shared skills that the agent can load.

**Data flow**: It gates the agent panel, asks the context for agent skills, and returns their metadata and routing fields.

**Call relations**: The skills panel calls this route before edits or installs are submitted through intent/action paths.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 3043–3047)

```
def _community_refusal(fault: Exception) -> Response
```

**Purpose**: Turns community-skill directory failures into member-readable HTTP responses. A special header tells the frontend the body is safe explanatory copy.

**Data flow**: It receives an exception, converts it to text, and returns a 502 response with the refusal header.

**Call relations**: community_skills and community_skill use it when the remote community service is unavailable or errors.

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 3054–3070)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one page of community skill search results or leaderboard results for the selected agent. Results are candidates; applying them happens elsewhere.

**Data flow**: It gates the panel, validates a short enough search query, asks the community directory for results, and returns skill records or a refusal.

**Call relations**: The community skills browser calls this before fetching a specific skill document or submitting an apply intent.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 3073–3094)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches one community skill document for review. It validates owner, repository, and skill name shapes before contacting the directory.

**Data flow**: It gates the panel, validates path segments, fetches the skill from the community service, and returns the document, 404, or a refusal.

**Call relations**: The frontend calls this when a member opens a community skill detail page.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 3097–3172)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows or searches memory items visible to the member across reachable agents. It supports recent paged listing and cross-agent search.

**Data flow**: It authenticates, checks whether memory exists, either lists recent memory by subject/kind/cursor or searches each audience agent, deduplicates results, formats rows, and returns actions.

**Call relations**: The memory workspace tab calls this, and action buttons are produced through the generic object-action system.

*Call graph*: calls 7 internal fn (object_actions, recent_memory, search_memory, decode, _action_payloads, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 3175–3185)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Formats memory search/listing matches into simple JSON rows. It keeps references and timestamps readable.

**Data flow**: It receives MemoryMatch items and returns dictionaries with kind, text, optional ref, created time, and subject.

**Call relations**: workspace_memory calls it for both recent and searched memory responses.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `connections`  (lines 3188–3197)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connector accounts visible for one selected agent. It includes the member's private grants, agent-shared grants, and admin-visible edges.

**Data flow**: It gates the agent panel, asks the context for agent connections using member/admin scope, and returns serialized entries.

**Call relations**: The agent connections panel calls this route.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 3200–3210)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the member-visible pool of connector accounts across the workspace, filtered to agents the member can see. It is not tied to one agent.

**Data flow**: It authenticates, reads all visible connections, trims each connection's agent list to the member's audience, and returns JSON.

**Call relations**: Workspace-level connection screens use this broader view.

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 3213–3219)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports which GitHub connection legs are available for this member or admin view. It helps the portal decide whether GitHub setup is complete.

**Data flow**: It authenticates, asks the context for GitHub coverage with admin scope when allowed, and returns the coverage model as JSON.

**Call relations**: First-run and connector setup screens call this route or the same context method.

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 3222–3253)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists recent conversations for the selected agent that the member may see. It marks readability, disclosure possibility, and commentability.

**Data flow**: It gates the panel, parses a bounded search term, asks core for one extra row beyond the limit, formats rows, and returns rows plus a 'more' flag.

**Call relations**: The conversations panel calls this to show transcript entries.

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 3256–3260)

```
def _searched(request: Request) -> str | None
```

**Purpose**: Extracts and bounds a search string from a listing request. Empty search boxes become None.

**Data flow**: It reads q from query parameters, trims and cuts it to the maximum length, and returns the string or None.

**Call relations**: conversations uses it before calling the conversation listing read.

*Call graph*: called by 1 (conversations).


##### `_conversation_row`  (lines 3263–3297)

```
def _conversation_row(entry: ListedConversation, member_id: UUID, agent: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Formats one listed conversation for panels or permalink resolution. It includes source, speakers, timestamps, access flags, and optional agent identity.

**Data flow**: It receives a ListedConversation, viewer member id, and optional agent info, formats fields and timestamps, computes commentable, and returns a dictionary.

**Call relations**: conversations and _resolve_chat call it for conversation JSON rows.

*Call graph*: calls 2 internal fn (_commentable, _iso); called by 2 (_resolve_chat, conversations).


##### `_readable_conversation`  (lines 3300–3320)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Common gate for read-only conversation content, attachments, and slots. It confirms the agent is in scope and the conversation is readable to the viewer.

**Data flow**: It gates the agent panel, parses the conversation id if needed, asks core whether it is readable, and returns agent id, conversation id, and SlotViewer or a 404 response.

**Call relations**: conversation_transcript, _conversation_history, conversation_attachment, and _slot_target use it.

*Call graph*: calls 3 internal fn (readable_conversation, _opens, _panel_gate); called by 4 (_conversation_history, _slot_target, conversation_attachment, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 3323–3341)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a read-only rendered transcript for an authorized conversation. It can also route cursor requests to compacted-history pagination.

**Data flow**: It checks for a cursor and delegates history if present, otherwise authorizes the conversation, renders messages, adds an earlier cursor if available, and returns JSON.

**Call relations**: The conversations panel calls this; it shares rendering with the chat transcript route.

*Call graph*: calls 4 internal fn (_conversation_history, _conversation_messages, _history_cursor, _readable_conversation); 1 external calls (JSONResponse).


##### `_member_chat_page`  (lines 3344–3372)

```
async def _member_chat_page(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes transcript-related reads for the member's own chat page, including cases not covered by the per-agent panel gate. It returns a SlotViewer for chat-owned content.

**Data flow**: It authenticates, checks chat access, parses conversation id, verifies _member_chat, and returns agent id, conversation id, and viewer data or a 404.

**Call relations**: _conversation_history and conversation_attachment use it as a fallback after read-only conversation gating fails.

*Call graph*: calls 4 internal fn (_agent_param, _audience_for, _member_chat, _opens); called by 2 (_conversation_history, conversation_attachment); 4 external calls (__init__, Response, web_extension, UUID).


##### `_conversation_history`  (lines 3375–3396)

```
async def _conversation_history(ctx: SurfaceContext, request: Request, cursor: str) -> Response
```

**Purpose**: Returns one older page of a compacted transcript. It accepts authorization through either read-only panel access or the member's own chat access.

**Data flow**: It authorizes through _readable_conversation or _member_chat_page, asks _history_messages for the page, and returns messages plus optional earlier cursor.

**Call relations**: conversation_transcript delegates here when the request carries a cursor.

*Call graph*: calls 3 internal fn (_history_messages, _member_chat_page, _readable_conversation); called by 1 (conversation_transcript); 2 external calls (JSONResponse, Response).


##### `_inbox_attachment`  (lines 3399–3410)

```
def _inbox_attachment(path: str) -> bool
```

**Purpose**: Checks whether a path names a single file directly under the web inbox directory. It prevents attachment preview routes from reading arbitrary workspace files.

**Data flow**: It splits the path and returns true only for web-inbox/<safe leaf name>.

**Call relations**: conversation_attachment uses it before reading any workspace file.

*Call graph*: called by 1 (conversation_attachment).


##### `conversation_attachment`  (lines 3413–3453)

```
async def conversation_attachment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a safe inline preview of an image file the member attached to a message. It refuses non-images, oversized files, moved files, and files whose bytes do not match the claimed image type.

**Data flow**: It authorizes the conversation, validates path and media type, checks workspace file size, reads the file stream, validates the image preview, and returns image bytes or an error.

**Call relations**: Chat bubbles use URLs produced by _attachment_preview to call this route.

*Call graph*: calls 5 internal fn (list_workspace_files, read_workspace_file, _inbox_attachment, _member_chat_page, _readable_conversation); 5 external calls (__init__, Response, raster_image_media_type, validated_image_preview, log).


##### `_slot_target`  (lines 3473–3497)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

**Purpose**: Authorizes which conversation a slot read should use, including subagent conversations opened from a root conversation. It ties slot data to a readable parent when needed.

**Data flow**: It parses optional root and conversation ids, authorizes the root conversation, verifies spawned subagent linkage when root is present, and returns a SlotTarget.

**Call relations**: conversation_slots and conversation_slot use it before building slot context.

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 3500–3516)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

**Purpose**: Builds the context object passed to conversation slot providers. It includes conversation audience, messages, ids, extension context, and public base URL.

**Data flow**: It reads the conversation audience and transcript, combines them with the supplied extension context, and returns ConversationSlotContext or None.

**Call relations**: conversation_slots and conversation_slot call it before summarizing or reading a slot.

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 3519–3603)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

**Purpose**: Adds host-side projections needed by specific slot types, such as workspace changes, artifacts, sites, or automations. It also precomputes visible item lists for authorization-sensitive slots.

**Data flow**: It receives a slot context and slot metadata, reads the needed data from SurfaceContext, builds projection or visible-item structures, and returns an updated context.

**Call relations**: conversation_slots and conversation_slot call it before invoking each slot provider.

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 7 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit).


##### `_authorized_slot_payload`  (lines 3606–3650)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

**Purpose**: Filters a slot payload so it contains only items the viewer may open or read. For automations it can keep the row but hide private content fields.

**Data flow**: It receives a slot payload and context, compares payload items against visible_items, and returns the original or a filtered copy.

**Call relations**: conversation_slot applies it after a provider returns a full payload.

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 3653–3697)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the available typed side panels for one authorized conversation, with counts. Examples include changes and artifacts.

**Data flow**: It authorizes the slot target, builds shared context, loops over registered slot providers, projects context, asks each provider for a summary count, logs failures, and returns visible slots.

**Call relations**: The transcript side panel calls this before opening a particular slot through conversation_slot.

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 3700–3727)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full payload for one typed conversation slot. It checks the provider id and enforces the provider's declared payload type.

**Data flow**: It authorizes the target, finds the slot provider, builds and projects context, reads the slot payload, type-checks it, filters it for authorization, and returns JSON.

**Call relations**: The frontend calls it after conversation_slots says a slot exists.

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 3730–3733)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Retrieves the workspace-changes projection from a slot context and verifies it has the expected type.

**Data flow**: It reads ctx.projection, returns it if it is WorkspaceChanges, otherwise raises an error.

**Call relations**: _read_changes and _summarize_changes use it for the built-in Changes slot.

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 3736–3737)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Reads the full payload for the built-in Changes conversation slot.

**Data flow**: It receives a ConversationSlotContext and returns the WorkspaceChanges projection from it.

**Call relations**: The CHANGES_SLOT provider uses it as its read callback.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 3740–3741)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts visible change entries for the built-in Changes slot. Empty slots are hidden by returning None.

**Data flow**: It receives a slot context, gets the changes projection, returns its length or None when zero.

**Call relations**: The CHANGES_SLOT provider uses it as its summarize callback.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 3754–3757)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Retrieves the artifacts projection from a slot context and verifies it has the expected type.

**Data flow**: It reads ctx.projection, returns it if it is ArtifactsSlotPayload, otherwise raises an error.

**Call relations**: _read_artifacts and _summarize_artifacts use it for the built-in Artifacts slot.

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 3760–3761)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Reads the full payload for the built-in Artifacts conversation slot.

**Data flow**: It receives a ConversationSlotContext and returns the ArtifactsSlotPayload projection from it.

**Call relations**: The ARTIFACTS_SLOT provider uses it as its read callback.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 3764–3766)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts visible artifacts for the built-in Artifacts slot. Empty artifact slots are not shown.

**Data flow**: It receives a slot context, gets the artifact projection, counts artifacts, and returns the count or None.

**Call relations**: The ARTIFACTS_SLOT provider uses it as its summarize callback.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `workspace_credentials`  (lines 3779–3792)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists workspace credential slots that members may fill, along with fill state and available actions. It never returns secret values.

**Data flow**: It authenticates, asks for credential slots and collection actions, formats them, and returns JSON.

**Call relations**: The workspace credentials panel calls this route.

*Call graph*: calls 4 internal fn (list_credential_slots, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 3795–3814)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists workspace members, admin status, seat status, and whether the viewer can add members. It uses the same authority as the member object kind.

**Data flow**: It authenticates, reads members, reads member collection actions, includes can_add from audience.admin, and returns JSON.

**Call relations**: The team workspace panel calls this route.

*Call graph*: calls 4 internal fn (list_members, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 3817–3828)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the member, including shared sources and admin-visible sources. It keeps source ownership understandable without exposing hidden pages.

**Data flow**: It authenticates, asks the context for sources scoped by member/admin, serializes entries, and returns JSON.

**Call relations**: The workspace sources panel calls this route.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `workspace_surfaces`  (lines 3831–3842)

```
async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists installed non-web surfaces connected to agents the member can see. It supports topology views of which surfaces feed which agents.

**Data flow**: It authenticates, reads all installations, filters them by the member's agent audience, and returns serialized installations.

**Call relations**: The workspace surfaces/topology panel calls this route.

*Call graph*: calls 2 internal fn (list_installations, _audience_for); 1 external calls (JSONResponse).


##### `workspace_imessage_claim`  (lines 3882–3903)

```
async def workspace_imessage_claim(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports the signed-in member's own iMessage claim state for first-run setup. It says pending, connected, or expired without exposing anyone else's claim.

**Data flow**: It authenticates, reads the member's imessage surface claim, compares proof and expiry time, builds an ImessageClaim, and returns it.

**Call relations**: The first-run iMessage step polls or reads this route.

*Call graph*: calls 2 internal fn (member_surface_claim, _audience_for); 3 external calls (__init__, now, JSONResponse).


##### `workspace_first_run`  (lines 3906–3950)

```
async def workspace_first_run(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Builds the first-run connector setup payload. It lists curated providers, installed connector steps, iMessage availability, model-key status, and starter actions.

**Data flow**: It authenticates, reads installations, GitHub coverage, feature flags, model-key state, and object actions, then returns provider and connector rows.

**Call relations**: The onboarding and Connect pages call this route to decide what setup steps to show.

*Call graph*: calls 6 internal fn (github_coverage, list_installations, member_holds_own_model_key, object_actions, _action_payloads, _audience_for); 3 external calls (__init__, flag_enabled, JSONResponse).


##### `connector_catalog`  (lines 3953–3981)

```
async def connector_catalog(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connectable providers from installed brokers for the connector page. It supports bounded search and cursor paging.

**Data flow**: It authenticates, validates query and cursor lengths, asks the context for a catalog page, formats provider tiles, and returns the next cursor.

**Call relations**: The connector browser calls this route after workspace_first_run.

*Call graph*: calls 2 internal fn (connector_catalog, _audience_for); 3 external calls (__init__, JSONResponse, Response).


##### `_held_providers`  (lines 4032–4043)

```
async def _held_providers(ctx: SurfaceContext, member_id: UUID, *, admin: bool) -> frozenset[str]
```

**Purpose**: Computes which provider connections the workspace/member already has in the same vocabulary used by starter recommendations. It includes broker connections, Slack, and GitHub push coverage.

**Data flow**: It reads connection rows, surface installations, and GitHub coverage, combines their provider names, and returns a frozen set.

**Call relations**: workspace_starters calls it before deciding which recommended rows are buildable or locked.

*Call graph*: calls 3 internal fn (github_coverage, list_connections, list_installations); called by 1 (workspace_starters).


##### `fill_starters`  (lines 4046–4135)

```
def fill_starters(slate: Slate, held: frozenset[str], taken: frozenset[str], installed: tuple[StarterApp, ...]=()) -> tuple[tuple[StarterRow, ...], UnlockRow | None]
```

**Purpose**: Chooses which recommended starter rows the start screen should show based on available provider accounts, installed apps, and already-taken app names. It fills app slots first, then unlocks, then check-in.

**Data flow**: It receives a ranked slate, held providers, taken app names, and installed app states; filters and categorizes rows; builds StarterRow and UnlockRow models; and returns the selected rows and optional unlock.

**Call relations**: workspace_starters calls it after generating or reading a slate.

*Call graph*: called by 1 (workspace_starters); 4 external calls (__init__, __init__, __init__, get).


##### `_solvent`  (lines 4138–4147)

```
async def _solvent() -> bool
```

**Purpose**: Checks whether the workspace balance can afford generating starter recommendations. It asks accounting directly for spend headroom.

**Data flow**: It opens an extension transaction, reads headroom, and returns true if there is no limit or remaining usable balance.

**Call relations**: workspace_starters passes this into StarterCache so it can avoid spending when the workspace is out of headroom.

*Call graph*: called by 1 (workspace_starters); 2 external calls (read_headroom, web_extension).


##### `_recalled`  (lines 4150–4157)

```
async def _recalled(ctx: SurfaceContext, member_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a bounded set of recent memory snippets visible to this member for starter generation. It uses the member's own subject plus shared workspace subject.

**Data flow**: It checks memory availability, builds subjects, reads recent memory, truncates each text snippet, and returns a tuple of strings.

**Call relations**: workspace_starters uses these snippets as input to StarterCache.

*Call graph*: calls 1 internal fn (recent_memory); called by 1 (workspace_starters); 2 external calls (audience_subjects, conversation_audience).


##### `workspace_starters`  (lines 4160–4218)

```
async def workspace_starters(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns personalized starting suggestions for a member before they have asked anything. It combines model-ranked ideas with live account/setup availability.

**Data flow**: It authenticates, reads installed app setup, gets or creates a starter slate from memory and agent names, reads held providers, filters through fill_starters, and returns starter/unlock rows.

**Call relations**: The start screen calls this route after first-run and agents boot data.

*Call graph*: calls 7 internal fn (agent_setup, _audience_for, _held_providers, _recalled, _setup_ready, _solvent, fill_starters); 5 external calls (__init__, __init__, gather, JSONResponse, web_extension).


##### `workspace_usage`  (lines 4221–4296)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns usage and spend information for the signed-in member, and for admins also the workspace rollup. It is the portal's financial visibility route.

**Data flow**: It authenticates, parses the time window, reads member spend, formats usage and caps, and if admin reads and includes workspace spend breakdowns.

**Call relations**: The usage workspace tab calls this route.

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `connect_handoff`  (lines 4302–4326)

```
async def connect_handoff(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the external consent flow for a connect request left by an agent turn. It mints the provider URL only when the member presses the control.

**Data flow**: It authorizes the member's reach to the turn, asks the context for a fresh connect URL, redirects to it, or returns a friendly callback page if unavailable.

**Call relations**: Transcript connect controls link to this route.

*Call graph*: calls 2 internal fn (connect_url, _member_turn); 2 external calls (callback_page, RedirectResponse).


##### `_member_turn`  (lines 4329–4382)

```
async def _member_turn(ctx: SurfaceContext, request: Request, *, named_turn: UUID | None=None, allow_commentable: bool=False) -> tuple[UUID, UUID, str] | Response
```

**Purpose**: Authorizes access to a specific turn as the current member. It supports stricter mutation access and looser stream access for commentable conversations.

**Data flow**: It authenticates, parses or receives the turn id, reads turn detail and owner, checks agent visibility and optional _member_chat access, and returns member/turn/email or a refusal.

**Call relations**: _stop_chat, connect_handoff, and stream use it before acting on or reading a turn.

*Call graph*: calls 5 internal fn (turn_detail, turn_owner, _audience_for, _commentable, _member_chat); called by 3 (_stop_chat, connect_handoff, stream); 3 external calls (Response, web_extension, UUID).


##### `stream`  (lines 4385–4393)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens the Server-Sent Events stream for one live turn. Server-Sent Events are a browser-friendly stream of named messages over one HTTP response.

**Data flow**: It authorizes the turn, reads Last-Event-ID for resume, and returns a StreamingResponse that yields _events output.

**Call relations**: The chat UI calls this after chat admission or transcript load names a running turn.

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 4396–4398)

```
def _event(name: str, payload: dict[str, object], cursor: str='') -> bytes
```

**Purpose**: Formats a named Server-Sent Event with JSON data and optional cursor id. The cursor lets the browser resume after reconnecting.

**Data flow**: It receives event name, payload dictionary, and cursor, JSON-encodes the payload, and returns SSE-formatted bytes.

**Call relations**: _events uses it for custom web-only events such as files, apps, credentials, connect controls, and subagents.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 4401–4417)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest, member_id: UUID) -> dict[str, object] | None
```

**Purpose**: Builds the credential prompts still awaiting values for a member. It renews the sealed request so the form can be safely submitted after reload.

**Data flow**: It receives a credential request and member id, renews the sealed token, filters prompts still pending, and returns prompt data or None.

**Call relations**: _events uses it live when a turn ends, and _open_handoffs uses it for reloads.

*Call graph*: calls 2 internal fn (credential_prompt_pending, renew_credential_request); called by 2 (_events, _open_handoffs).


##### `_file_payload`  (lines 4420–4433)

```
def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]
```

**Purpose**: Formats one shared artifact file for the chat UI. It includes download and preview links created by the context.

**Data flow**: It receives a shared artifact, asks the context for artifact and preview links, and returns filename, subject, media type, size, and URLs.

**Call relations**: _events and _transcript_aids use it when a turn shares files.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 2 (_events, _transcript_aids).


##### `_opens`  (lines 4436–4437)

```
def _opens(audience: WebAudience) -> frozenset[UUID]
```

**Purpose**: Returns the set of agent ids the viewer may open. This is used to decide whether app cards should be visible.

**Data flow**: It receives a WebAudience and returns a frozen set of its agent ids.

**Call relations**: Transcript, readable-conversation, member-chat, and live event code use it when filtering created app cards.

*Call graph*: called by 4 (_events, _member_chat_page, _readable_conversation, transcript).


##### `_created_apps`  (lines 4440–4471)

```
async def _created_apps(ctx: SurfaceContext, created: Mapping[str, tuple[ObjectRef, ...]], opens: frozenset[UUID]) -> dict[str, list[dict[str, object]]]
```

**Purpose**: Builds app cards for agents created by a turn, but only for apps the viewer may open. It resolves created object names back to current agent rows.

**Data flow**: It receives terminal-created refs and the viewer's openable ids, reads agents, matches agent-kind refs by name and access, and returns turn-id keyed card lists.

**Call relations**: _transcript_aids uses it for settled transcripts, and _events uses it when a live terminal frame reports created apps.

*Call graph*: calls 1 internal fn (list_agents); called by 2 (_events, _transcript_aids).


##### `_events`  (lines 4474–4530)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str, email: str) -> AsyncIterator[bytes]
```

**Purpose**: Converts a turn's live hub frames into browser Server-Sent Events, adding web-specific side events for files, subagents, connect controls, credentials, and created apps. It is the live chat tail.

**Data flow**: It tails the turn from an optional cursor, reacts to artifact and terminal frames by reading extra data, yields custom events, then yields the raw frame converted by _sse.

**Call relations**: stream returns this async iterator as the response body.

*Call graph*: calls 13 internal fn (connect_available, conversation_subagent_turns, shared_artifacts, tail, turn_detail, _connect_control, _created_apps, _event, _file_payload, _opens (+3 more)); called by 1 (stream); 2 external calls (web_audience, web_extension).


##### `fulfill_credential`  (lines 4533–4563)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores a private credential value entered by the member in response to an agent handoff. The value is not sent as chat text and does not enter the transcript.

**Data flow**: It authenticates, bounds and parses the form, validates sealed token/slot/value, checks secret size, calls fulfillment on the context, and returns stored or refusal.

**Call relations**: Credential prompt forms produced by _pending_prompts submit to this route.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `admin_index`  (lines 4566–4621)

```
async def admin_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace administration snapshot for admins only. It includes agents, installations, web audience grants, members and seats, spend caps, and deploy shape.

**Data flow**: It authenticates and checks admin status, reads installations, grants, seat snapshot, spend caps, and deploy metadata, then returns JSON.

**Call relations**: The admin screen calls this route; non-admins get not found.

*Call graph*: calls 5 internal fn (list_installations, object_actions, spend_caps, _action_payloads, _audience_for); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


##### `_object_gate`  (lines 4624–4638)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

**Purpose**: Common gate for generic object index and detail pages. It authenticates, resolves web audience, and checks that the requested object kind exists in this deployment.

**Data flow**: It calls _audience_for, reads the kind path parameter, asks the context for kind metadata, and returns member/audience/kind or a response.

**Call relations**: object_index and object_detail call it before accessing any kind-specific data.

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 4641–4651)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

**Purpose**: Selects and authorizes the agent namespace for an object read. Object rows live under an agent, so the query must name an agent the viewer can see.

**Data flow**: It parses the agent query parameter as a UUID, searches the audience agents, and returns the matching AgentSummary or a 404 response.

**Call relations**: object_index and object_detail use it for named-agent reads.

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_action_payloads`  (lines 4654–4655)

```
def _action_payloads(views: tuple[ActionView, ...]) -> list[dict[str, object]]
```

**Purpose**: Serializes action declarations into frontend JSON payloads. It drops None fields for cleaner responses.

**Data flow**: It receives action views and returns each model dumped as JSON-compatible dictionaries.

**Call relations**: Workspace panels, admin, memory, credentials, team, first-run, and action_views use it.

*Call graph*: called by 6 (action_views, admin_index, workspace_credentials, workspace_first_run, workspace_memory, workspace_team).


##### `_kind_payload`  (lines 4658–4665)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

**Purpose**: Builds shared metadata for an object kind response. It tells the frontend fields, schema, and whether apply/delete intents are available.

**Data flow**: It receives a PortalKind, reads its declared fields and schema, checks ApplyIntent capabilities, and returns a dictionary.

**Call relations**: object_index and object_detail include this in their responses.

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 4668–4675)

```
def _filter_value(raw: str) -> JsonValue
```

**Purpose**: Parses an object-list filter value from the query string into the scalar type it likely represents. For example, 'true' becomes a boolean and '3' becomes a number.

**Data flow**: It receives a raw string, tries JSON parsing, and returns the parsed value or the original string.

**Call relations**: object_index uses it for non-reserved query parameters.

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_fanout_token`  (lines 4678–4683)

```
def _fanout_token(walking: dict[str, str]) -> str | None
```

**Purpose**: Encodes per-agent cursors for a workspace-wide object index. This lets a fanned-out listing continue each agent's walk independently.

**Data flow**: It receives a map of agent id strings to cursors, returns None if empty, otherwise JSON-serializes and hex-encodes it.

**Call relations**: object_index returns this as next_cursor after multi-agent reads.

*Call graph*: called by 1 (object_index); 1 external calls (dumps).


##### `_fanout_walks`  (lines 4686–4703)

```
def _fanout_walks(token: str) -> dict[UUID, str] | None
```

**Purpose**: Decodes and validates a fanned-out object-list cursor. It rejects tokens not minted in the expected JSON/hex shape.

**Data flow**: It receives a token string, hex-decodes and JSON-parses it, validates non-empty string cursors keyed by UUIDs, and returns a UUID-to-cursor map or None.

**Call relations**: object_index uses it when continuing workspace-wide object listings.

*Call graph*: called by 1 (object_index); 2 external calls (loads, UUID).


##### `_merged_rank`  (lines 4706–4719)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```

**Purpose**: Computes a sorting key for merging object rows returned from multiple agents. It keeps absent values, booleans, numbers, and text in stable groups.

**Data flow**: It receives a row and order-by field, inspects the field value, and returns a tuple used for sorting with row name as tie-breaker.

**Call relations**: object_index uses it after fanning out object listings across agents.


##### `object_index`  (lines 4722–4812)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists rows of a generic object kind for the signed-in member. It can read one agent namespace or fan out across all audience agents with search, filters, sorting, and cursor continuation.

**Data flow**: It gates the kind and agent scope, parses ordering and filters, builds ObjectListQuery, reads pages from one or many agents, formats rows with agent identity, merges when needed, and returns objects plus next cursor.

**Call relations**: Generic object index pages call this route for kinds such as sites, credentials, memory-related objects, and extension-defined objects.

*Call graph*: calls 7 internal fn (list_member_objects, _fanout_token, _fanout_walks, _filter_value, _kind_payload, _object_agent, _object_gate); 4 external calls (__init__, replace, JSONResponse, Response).


##### `object_detail`  (lines 4815–4862)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reads one generic object row in an agent namespace. It returns visible spec, status fields, links, generation, and timestamps while respecting the kind's own visibility gate.

**Data flow**: It gates kind and agent, asks for the member-visible object, checks each typed link for openability, and returns detail JSON or 404.

**Call relations**: Generic object detail pages call this route after selecting a row from object_index.

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 4865–4897)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one core live frame as Server-Sent Event bytes. It maps each frame type to the event name the browser understands.

**Data flow**: It receives a cursor and LiveFrame, optionally writes an id line, serializes the frame payload, and returns SSE bytes or raises for unknown frame types.

**Call relations**: _events calls it for the raw hub frames after adding web-specific side events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 4900–4905)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Submits a prepared intent for a selected agent. Prepared intents are structured actions the panels can ask an agent to perform.

**Data flow**: It gates the agent panel, extracts member/email/agent, and delegates the request to submit_intent.

**Call relations**: Panel mutation buttons use this route when the action is modeled as an intent.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `actions`  (lines 4908–4926)

```
async def actions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Runs an action presented by an object kind or row for the selected agent. The route binds the target kind, optional name, and action path.

**Data flow**: It gates the panel, reads path parameters, and delegates to submit_action with agent, member, email, kind, name, and action.

**Call relations**: Panel action buttons post here; the delegated action handler still rechecks authority.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_action).


##### `action_views`  (lines 4929–4943)

```
async def action_views(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the actions declared for a kind collection or one named object row. It can draw controls even when the current panel is not a normal object-detail read.

**Data flow**: It authenticates, validates the kind exists, determines collection versus instance binding, serializes object actions, and returns them.

**Call relations**: Frontend panels call this to discover available controls before posting to actions.

*Call graph*: calls 4 internal fn (object_actions, object_kind, _action_payloads, _audience_for); 2 external calls (JSONResponse, Response).


##### `_write_agent`  (lines 4950–4968)

```
def _write_agent(request: Request, audience: WebAudience, stated: object=None) -> AgentSummary | Response
```

**Purpose**: Chooses the agent namespace for direct object writes from app frames. It accepts an explicit body agent, query agent, or falls back to the main agent.

**Data flow**: It reads a stated agent or query parameter, validates it against the audience, returns the agent, or returns not found/no-main responses.

**Call relations**: object_write calls it before creating apply or delete intents.

*Call graph*: called by 1 (object_write); 2 external calls (Response, UUID).


##### `_direct_result`  (lines 4971–4975)

```
def _direct_result(frame: TerminalFrame, name: str) -> Response
```

**Purpose**: Turns a terminal frame from a direct object-write turn into a simple success/failure JSON result. It carries the object name and detail text.

**Data flow**: It receives a terminal frame and name, returns ok true for done, otherwise ok false with an error/detail reason.

**Call relations**: object_write calls it when the intent turn reaches a terminal frame.

*Call graph*: called by 1 (object_write); 1 external calls (JSONResponse).


##### `object_write`  (lines 4978–5052)

```
async def object_write(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Allows an app frame to create, update, or delete an object under the member's web session. It runs the change as a prepared intent turn and waits briefly for the result.

**Data flow**: It authenticates, validates kind and request body/path, chooses the agent, builds an object_apply or object_delete ToolIntent, admits it to an intent conversation, tails the turn, and returns the terminal result or timeout.

**Call relations**: Embedded app bridge clients post here for direct object mutations.

*Call graph*: calls 7 internal fn (admit, conversation_for, object_kind, tail, _audience_for, _direct_result, _write_agent); 7 external calls (__init__, timeout, dumps, conversation_audience, JSONResponse, json, Response).


##### `object_changes`  (lines 5058–5086)

```
async def object_changes(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns recent object-change journal entries for admins. It states what changed and who caused it without exposing object specs.

**Data flow**: It authenticates, requires admin, reads recent changes, formats kind/name/verb/caller/agent/time, and returns JSON.

**Call relations**: The admin audit screen calls this route.

*Call graph*: calls 2 internal fn (recent_object_changes, _audience_for); 2 external calls (JSONResponse, Response).


##### `settings`  (lines 5089–5101)

```
async def settings(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns configuration details for the selected agent, such as prompt/spec and related settings. It also decides whether the viewer may archive the agent.

**Data flow**: It gates the panel, finds the agent summary, computes archivable based on main/owner/admin, and delegates to agent_settings.

**Call relations**: The agent settings panel calls this route.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_settings).


##### `agent_setup`  (lines 5104–5134)

```
async def agent_setup(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports what a selected app still needs before it works: connectors, credentials, standing orders, and whether it has its own page. It adds friendly provider labels and summaries.

**Data flow**: It gates the panel, reads setup state, enriches connector rows with labels/summaries, checks bound page state, and returns JSON.

**Call relations**: The app setup screen calls this route, and agents_index uses related setup reads for boot badges.

*Call graph*: calls 5 internal fn (agent_setup, _bound_page, _panel_gate, _provider_label, _provider_summary); 1 external calls (JSONResponse).


##### `_bound_page`  (lines 5137–5164)

```
async def _bound_page(ctx: SurfaceContext, summary: AgentSummary, member_id: UUID) -> ObjectRow | None
```

**Purpose**: Finds the hosted site row that this workspace built and bound as an agent's homepage. It reads past ordinary member visibility because homepage binding has its own access rule.

**Data flow**: It queries site objects for homepage_agent under the agent namespace with admin-style visibility, then returns the first row carrying site_url or None.

**Call relations**: agents_index, agent_setup, and homepage use it to decide whether an app stands on setup or has a page.

*Call graph*: calls 1 internal fn (list_member_objects); called by 3 (agent_setup, agents_index, homepage); 1 external calls (__init__).


##### `homepage`  (lines 5167–5186)

```
async def homepage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the homepage frame state for the selected agent. It can point to a workspace-built hosted site or a shipped app bundle.

**Data flow**: It gates the panel, publishes assets, reads the bound page, computes homepage state, and returns JSON.

**Call relations**: The Home tab calls this after boot and may poll it while a first build is expected.

*Call graph*: calls 5 internal fn (_assets_published, _bound_page, _homepage_state, _panel_gate, apps); 1 external calls (JSONResponse).


##### `_homepage_state`  (lines 5189–5236)

```
def _homepage_state(ctx: SurfaceContext, summary: AgentSummary, bound: ObjectRow | None, admin: bool, member_id: UUID) -> dict[str, JsonValue]
```

**Purpose**: Builds the JSON state for one agent's homepage. It enforces private-agent page visibility and falls back to shipped app bundle URLs when appropriate.

**Data flow**: It receives context, agent summary, optional bound site row, admin flag, and member id; returns set/url/generation for visible bound or shipped pages, otherwise none.

**Call relations**: agents_index includes this in the boot agent rows, and homepage returns it for a single agent.

*Call graph*: calls 1 internal fn (apps); called by 2 (agents_index, homepage); 3 external calls (shipped_app_slug, homepage_embed_url, shipped_homepage_url).


##### `preview`  (lines 5254–5285)

```
async def preview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders an uploaded file into a PNG preview for the composer before the member sends it. It does not store anything or admit a turn.

**Data flow**: It authenticates, requires bounded multipart form data, picks the first file, checks the suffix is previewable, sends bytes to the preview renderer, and returns a PNG or refusal.

**Call relations**: The browser composer calls this when showing document previews before chat submission.

*Call graph*: calls 4 internal fn (render_preview, _audience_for, _form, _framed_length); 2 external calls (PurePosixPath, Response).


##### `upload_start`  (lines 5288–5324)

```
async def upload_start(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Mints a presigned blob-store upload URL for one attachment. The browser can upload file bytes directly, then send only the stored key with the chat message.

**Data flow**: It authenticates, reads a bounded JSON request with size, checksum, and name, validates size, creates a safe upload key, asks the blob store for a presigned PUT URL, and returns key/url.

**Call relations**: The composer calls this before sending large attachments; _parse_inbound later accepts the returned key.

*Call graph*: calls 2 internal fn (_audience_for, _bounded_body); 5 external calls (loads, JSONResponse, Response, inbox_name, uuid4).


### Shared portal plumbing
Small support files provide reusable pagination behavior and package import wiring for the web extension.

### `core/src/ufo/runtime/listings.py`

`domain_logic` · `request handling`

This file solves a common listing problem: how to show “next page” and “previous page” links when the data may change at any moment. Instead of using page numbers or offsets, it uses a cursor, which is like a bookmark that names the exact row where the reader left off. Each cursor stores two pieces of position information: when the row was created and the row’s id. The id matters because many rows can share the same timestamp, so the cursor needs a tie-breaker.

All listings using this helper follow the same order: newest first. `page_query` prepares a database query for one page. It asks for one extra row beyond the requested limit so the code can tell whether another page exists. If the user is moving toward newer rows, it temporarily reverses the database order, then `page_of` flips the rows back into normal newest-first order before returning them.

The result is a `ListingPage`: the visible rows plus optional cursors for going older or newer. If a cursor is missing, that means there is no page in that direction. Without this file, each listing would need to reinvent this careful paging behavior, and small mistakes could cause duplicate rows, missing rows, or misleading navigation links.

#### Function details

##### `ListingCursor.encode`  (lines 42–45)

```
def encode(self) -> str
```

**Purpose**: This turns a cursor into a single text token that can travel in a link or query string. Someone would use it when building “older” or “newer” controls for a listing page.

**Data flow**: It starts with a `ListingCursor` containing a timestamp, an item id, and a direction flag. It converts the direction into the word `newer` or `older`, formats the timestamp as text, joins those pieces with a separator, and returns the finished token.

**Call relations**: After `page_of` creates boundary cursors for a page, higher-level listing code can call this method to place those cursors into navigation links. It does not call other project functions; it simply packages the cursor’s own data into a portable string.


##### `ListingCursor.decode`  (lines 48–60)

```
def decode(cls, token: str) -> 'ListingCursor'
```

**Purpose**: This reads a cursor token from a client and turns it back into a `ListingCursor`. It also protects the listing from bad or made-up tokens by rejecting anything that does not name a real-looking position.

**Data flow**: It receives a text token, splits it into direction, timestamp, and item id, then checks that the direction is valid and that both position fields are present. It parses the timestamp with `datetime.fromisoformat` and checks the item id with `UUID`. If anything is wrong, it raises `MalformedCursor`; otherwise it returns a cursor object that the paging code can use.

**Call relations**: The web workspace memory surface calls this when a request arrives with a cursor in it. If decoding succeeds, the resulting cursor can guide `page_query`; if decoding fails, the caller can report a client error instead of silently showing the wrong page.

*Call graph*: called by 1 (workspace_memory); 3 external calls (__init__, fromisoformat, UUID).


##### `page_query`  (lines 74–96)

```
def page_query(query: sa.Select[Any], cursor: ListingCursor | None, limit: int, *, created_at: sa.ColumnElement[datetime], ident: sa.ColumnElement[Any]) -> sa.Select[Any]
```

**Purpose**: This prepares a database query so it fetches exactly the slice of rows needed for one cursor-based page. It is the part that tells the database where to start and which direction to walk.

**Data flow**: It receives an existing SQLAlchemy query, an optional cursor, a page size, and the two database columns that define the listing position: creation time and id. It adds the correct ordering, asks for one more row than the visible limit, and, if there is a cursor, adds a condition that only selects rows older or newer than that cursor. The output is a new SQLAlchemy query ready to run.

**Call relations**: Listing code calls this before reading rows from the database. Internally it uses `UUID` to turn the cursor’s id back into a database-comparable value and `sqlalchemy.tuple_` to compare the timestamp and id together, so ties are handled correctly.

*Call graph*: 2 external calls (tuple_, UUID).


##### `page_of`  (lines 99–128)

```
def page_of(rows: Sequence[SourceT], cursor: ListingCursor | None, limit: int, *, render: Callable[[SourceT], RowT], position: Callable[[SourceT], tuple[datetime, str]]) -> ListingPage[RowT]
```

**Purpose**: This turns the raw rows returned by `page_query` into the page object that callers can return to users. It decides which rows are visible and whether “older” and “newer” controls should exist.

**Data flow**: It receives the fetched rows, the cursor that led here, the requested limit, a `render` function that converts each source row into the public row shape, and a `position` function that extracts the row’s timestamp and id. It checks whether there was an extra row beyond the limit, trims the page to the visible size, reverses rows back into newest-first order when needed, builds boundary cursors from the first and last visible rows, renders the rows, and returns a `ListingPage`.

**Call relations**: This is normally used after `page_query` has fetched rows. It calls its small inner helper `page_of.at` to make cursors for page boundaries, then creates the final `ListingPage` envelope that listing surfaces can send back to clients.

*Call graph*: 1 external calls (__init__).


##### `page_of.at`  (lines 118–120)

```
def at(source: SourceT, *, newer: bool) -> ListingCursor
```

**Purpose**: This small helper creates a cursor for one specific row at the edge of a page. It exists so `page_of` can build the “older” and “newer” bookmarks in the same consistent way.

**Data flow**: It receives one source row and a direction flag. It asks the supplied `position` function for that row’s timestamp and id, then creates and returns a `ListingCursor` with those values and the requested direction.

**Call relations**: Only `page_of` uses this helper, while deciding the boundary cursors for a returned page. It hands off to `ListingCursor` construction so the final `ListingPage` can carry navigation positions instead of making callers calculate them themselves.

*Call graph*: 1 external calls (__init__).


### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is used to say, “this folder is a package.” A package is a named bundle of Python files that can be imported together. This particular file is empty, so it does not run setup code, define shortcuts, or expose any public names. Its value is structural: it helps Python and developer tools recognize `extensions/web/ufo_ext_web` as an importable part of the system. Without it, some import styles or packaging tools might not treat this directory as a normal package, which could make the web extension harder to load or distribute. Think of it like a label on a folder in a filing cabinet: the label does not contain the documents, but it tells everyone that the folder belongs to a named section.


### Access and external credentials
These modules govern member visibility rules and connect the portal to outside services for community skill browsing and OpenAI device-code login.

### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling`

The web portal needs a clear answer to a simple question: “When this person signs in, which agents are they allowed to reach?” This file is that rulebook. It treats a member’s email address as their web identity, and stores explicit grants as small records keyed by agent ID and email. Without this file, the portal could either show too much, exposing private agents or transcripts, or too little, hiding agents that members should be able to use.

The main idea is layered access. Workspace admins can reach every agent. Regular seated members can reach agents marked as visible to the whole workspace, agents explicitly granted to their email, agents they own, and certain private extension conversations tied to them. The `WebAudience` object is the compact answer the rest of the web surface uses: it says whether the member is an admin and lists the agents they may use.

The file also defines three web-related tools. Admins can grant or revoke a member’s access to an agent. Admins can also acknowledge opening another member’s private transcript; that acknowledgement is recorded as an audit trail before the portal allows reading it. In everyday terms, this file is both the guest list at the web portal door and the logbook for sensitive transcript access.

#### Function details

##### `web_extension`  (lines 38–45)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Builds the web extension’s own access handle so code can read and write the web audience records. This matters because the portal’s access list lives in the web extension’s private store, not in a general shared table.

**Data flow**: It takes no outside input. It creates a scoped store for the web extension and an empty credential access object, then wraps them in an extension context. The result is a ready-to-use context for web audience storage and transactions.

**Call relations**: Surface code uses this when it needs to look up or change web audience data. Internally it constructs the pieces of an extension context: the scoped store, credential access, and final context object.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 48–49)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Creates the storage key used for one access grant: one agent plus one member email. It keeps all grant records in a predictable format so grant, revoke, and lookup operations talk about the same row.

**Data flow**: It receives an agent ID and an email address. It trims spaces from the email, lowercases it, and combines it with the audience prefix and agent ID. The output is a string key suitable for the extension store.

**Call relations**: The grant and revoke actions both call this helper before writing or deleting a grant. It is the shared label maker that keeps those two actions aimed at the same stored record.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 52–59)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads all web access grants and organizes them as agent IDs mapped to the emails granted for each agent. This is useful for an administration view that needs to show who has access to what.

**Data flow**: It receives a scoped store. It lists every stored row under the audience prefix, splits each key into an agent ID and email, groups emails under their agent, sorts the emails, and returns a dictionary from agent ID to email list.

**Call relations**: It reads the same stored rows that the grant and revoke tools write. It relies on the store’s listing operation and converts stored agent ID text back into UUID values.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 62–69)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds all agents that have been explicitly granted to one email address. This is one ingredient in deciding what the signed-in member can see in the portal.

**Data flow**: It receives the web extension store and an email address. It normalizes the email, scans all audience grant records, keeps only rows whose email matches, converts their agent IDs back into UUID values, and returns them as an immutable set.

**Call relations**: The main audience-building function calls this after it confirms the member is seated and not an admin. It supplies the explicit-grant part of that member’s web access.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 83–84)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this web audience may access a particular agent in the normal agent list. The portal uses this as a quick yes-or-no check before opening or routing chats.

**Data flow**: It receives an agent ID. It compares that ID with the IDs in the audience’s allowed agent list. It returns true if there is a match and false otherwise, without changing anything.

**Call relations**: Web surface chat-routing code calls this when resolving existing chats, new chats, and chat targets. It lets those routes enforce the audience decision made earlier by `web_audience`.

*Call graph*: called by 3 (_existing_chat_target, _new_chat_target, _resolve_chat).


##### `WebAudience.allows_chat`  (lines 86–87)

```
def allows_chat(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this audience may chat with a given agent, including special private conversation agents as well as ordinary visible agents. It is broader than `allows` because some conversations are permitted only through a member-private extension conversation.

**Data flow**: It receives an agent ID. It checks that ID against the combined chat-agent list and returns true if the agent appears there. It does not alter the audience object.

**Call relations**: It builds on the `chat_agents` property. Where callers need the chat-specific access rule, this method gives one simple answer.


##### `WebAudience.chat_agents`  (lines 90–91)

```
def chat_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Combines the normal allowed agents and the extra conversation-only agents into one list for chat access checks. It gives callers a single view of everything this member may chat with.

**Data flow**: It reads the `agents` tuple and the `conversation_agents` tuple from the audience object. It returns a new tuple containing both groups in order. Nothing is stored or changed.

**Call relations**: `allows_chat` uses this property to decide whether a specific agent is reachable for chat. It connects the regular audience list with the special private-conversation allowance.


##### `web_audience`  (lines 94–126)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the complete web access view for one signed-in email address. This is the central decision point for what the portal should show and allow.

**Data flow**: It receives a surface context, the web extension context, and an email address. It normalizes the email, reads the workspace seat list in a transaction, finds the matching member, lists agents from the surface, and then applies the rules: unseated or unknown members see nothing; admins see everything; regular members see workspace-visible agents, explicit grants, owned agents, and certain private extension conversation agents. It returns a `WebAudience` object.

**Call relations**: Portal routes call this before answering access questions. It uses the seat system to verify membership, the surface context to list agents and private extension conversation agents, and `_granted_agent_ids` to include explicit web grants.

*Call graph*: calls 4 internal fn (transaction, list_agents, member_extension_agent_ids, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 136–137)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error-style tool response with a human-readable refusal message. It keeps admin tool failures consistent and clear.

**Data flow**: It receives text explaining why the action cannot proceed. It wraps that text in a text content object and then in a tool result marked as an error. The output is the refusal result returned to the caller.

**Call relations**: `_gate` and `_read_private_transcript` use this whenever a speaker is missing permission, the target is invalid, or the context is unsafe. It hands back a finished tool response instead of raising an exception for normal permission denials.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_target_agent`  (lines 140–147)

```
def _target_agent(ctx: ToolContext) -> tuple[UUID, str]
```

**Purpose**: Figures out which agent a web access action applies to and chooses a friendly name for that agent in the reply. If the action did not name a separate agent, it uses the current agent.

**Data flow**: It receives the tool context. If the context has no target at all, it raises an error because the tool was dispatched incorrectly. If no agent was named on the target, it returns the current turn’s agent ID and the label “this agent”; otherwise it returns the named agent’s ID and name.

**Call relations**: Grant, revoke, and private transcript actions all call this after basic checks. It connects the tool’s target information to the exact agent ID used for storage or transcript acknowledgement.

*Call graph*: called by 3 (_grant, _read_private_transcript, _revoke).


##### `_gate`  (lines 150–165)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext) -> ToolResult | SeatEntry
```

**Purpose**: Performs the shared permission check for changing a member’s web access. It makes sure the speaker is a workspace admin and that the target member actually exists.

**Data flow**: It receives the tool context and extension context. It checks that there is a speaking member, asks whether that speaker is an admin, reads the workspace seat snapshot, and looks up the member named by the tool target. It returns either the matching seat entry or a refusal tool result explaining why the action cannot continue.

**Call relations**: Both `_grant` and `_revoke` call this before touching stored grants. It uses `_refusal` for ordinary denials and the seat snapshot to turn the target member ID into the member’s email and other seat information.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 2 external calls (__init__, UUID).


##### `_grant`  (lines 168–185)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that gives a workspace member web portal access to an agent. It writes the grant record that later audience checks will read.

**Data flow**: It receives the tool context and an empty input model. It confirms the extension context exists, runs `_gate` to verify the speaker and target member, resolves the target agent, and skips unnecessary grants for the main agent when appropriate. Otherwise it normalizes the member’s email, writes a grant row containing who granted it, and returns a success message.

**Call relations**: This is the handler behind the `grant_web_access` tool definition. It depends on `_gate` for permission and member lookup, `_target_agent` for the agent being granted, and `_grant_key` for the exact storage key used by later reads.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 188–208)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that removes a member’s explicit web portal grant for an agent. It deletes the stored grant record, while explaining that some access may remain for built-in reasons such as the main agent.

**Data flow**: It receives the tool context and an empty input model. It checks for the extension context, runs `_gate`, resolves the target agent, normalizes the member email, and deletes the matching grant key from the store. It then returns a message saying access was removed, or, for the main agent, that the member still reaches it because every member can.

**Call relations**: This is the handler behind the `revoke_web_access` tool definition. It shares the same gate, target resolution, and key builder as `_grant`, so both tools operate on the same kind of stored row.

*Call graph*: calls 4 internal fn (agent_is_main, _gate, _grant_key, _target_agent); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 219–255)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Implements the admin tool that records an acknowledgement before opening another member’s private transcript in the web portal. It is an audit step: the transcript is not returned here, but the system records who opened whose private conversation and when.

**Data flow**: It receives the tool context and an empty input model. It checks that there is a speaking member, that the speaker is an admin, and that the action is not happening in a foreign shared audience. It resolves the target conversation and agent, asks the surface layer to record transcript access, and returns either a refusal if there is nothing valid to acknowledge or a confirmation naming the subject email.

**Call relations**: This is the handler behind the `read_private_transcript` tool definition. It uses `_refusal` for permission and safety failures, `_target_agent` to bind the acknowledgement to the right agent, and `record_transcript_access` to write the audit record that the portal’s content gate later relies on.

*Call graph*: calls 3 internal fn (speaker_is_admin, _refusal, _target_agent); 4 external calls (__init__, __init__, record_transcript_access, UUID).


### `extensions/web/ufo_ext_web/community.py`

`io_transport` · `request handling`

The Community tab needs two things from the public skills directory: a short list of skills, and the full text for one skill when a user wants to install or review it. This file is the bridge to that outside service. Without it, the site would either have no Community results or would need to expose raw, unreliable web responses directly to the user.

The file uses asynchronous HTTP calls, meaning it can wait for the skills.sh website without blocking the whole web app. It has two main public actions: `listing`, which returns popular skills or search results, and `fetch`, which downloads one skill’s `SKILL.md` document. It also keeps small in-memory caches, like a notebook beside the desk, so repeated visits do not keep asking the outside site for the same information.

Because outside services can fail, rate-limit requests, or send unexpected data, the code is defensive. It checks status codes, limits how much data it will read, ignores malformed listing rows, and raises `CommunityUnavailable` with a user-readable message when the directory cannot be used safely. It also deliberately does not fetch descriptions for every listed skill, because the directory only exposes those on individual skill pages and those endpoints are rate limited.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: This helper turns an HTTP failure code from the skills directory into a clear `CommunityUnavailable` error. It gives a special, understandable message for rate limiting, where the directory has temporarily refused too many reads.

**Data flow**: It receives a numeric web response code. If the code means rate limited, it builds an error explaining that the deploy has reached the directory’s hourly read limit; otherwise it builds an error saying which response code came back. The result is an exception object ready to be raised.

**Call relations**: The lower-level web-reading functions call this when skills.sh answers with anything other than success. `_body` uses it for streamed downloads, and `_search` uses it for search responses, so both paths report failures in the same user-friendly way.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: This is the main way the web app asks for Community skill rows. With no search text it returns the popular leaderboard; with search text it returns matching skills.

**Data flow**: It receives a query string. First it checks the in-memory listing cache; if a fresh answer is already there, it returns that immediately. Otherwise it opens an HTTP client, asks either the popular-list reader or the search reader for results, trims the list to the display limit, stores it in the cache with the current time, and returns the skill rows.

**Call relations**: This is the public listing entry point for the Community narrowing. Inside, it creates a client through `_client`, then hands off to `_popular` when there is no query or `_search` when there is one. It uses the clock to decide whether cached results are still fresh.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: This is the main way the web app fetches the full document for one Community skill. It is used when a user opens a specific skill and needs the description and instructions, not just the listing row.

**Data flow**: It receives a source repository such as `owner/repo` and a skill name. It checks the document cache first. If not cached, it builds the directory download URL, downloads the response body, reads the JSON, looks for a file named `SKILL.md`, parses that markdown document, stores either the parsed document or `None` in the cache, and returns it.

**Call relations**: This public fetch path uses `_client` to create the web client, `_body` to safely download the directory response, and `_parse` to turn the `SKILL.md` text into a structured document. If the JSON cannot be read, it raises `CommunityUnavailable` so the route can show a clear failure instead of an empty or broken page.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: This small helper creates the asynchronous HTTP client used for talking to skills.sh. It centralizes timeout, redirect, and test-transport setup so all requests behave consistently.

**Data flow**: It receives a timeout value in seconds. It creates an `httpx.AsyncClient` configured with that timeout, the optional injected transport used in tests, and automatic redirect following. It returns the ready-to-use client.

**Call relations**: `listing` and `fetch` call this before making outside web requests. Those higher-level methods then pass the client into the more specific readers, such as `_popular`, `_search`, and `_body`.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: This reads the skills.sh homepage payload and extracts the popular skill leaderboard. It is used when the Community tab is opened without a search query.

**Data flow**: It receives an HTTP client. It downloads the homepage payload with a special header, scans the text for small JSON-looking entries that contain skill IDs, converts each valid entry into a `CommunitySkill`, removes duplicates by source and name, and returns the skills sorted from most installs to least. If nothing usable is found, it raises a clear unavailable error.

**Call relations**: `listing` calls this when the user has not searched for anything. `_popular` relies on `_body` for safe downloading and `_entry` for validating each possible skill row before it becomes part of the returned leaderboard.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: This asks the public skills.sh search endpoint for skills matching a user’s query. It turns the raw search response into clean skill rows for the Community listing.

**Data flow**: It receives an HTTP client and the search text. It sends a GET request with the query and list limit, checks that the response succeeded, reads the `skills` array from the JSON response, converts each usable entry into a `CommunitySkill`, sorts them by install count, and returns the list.

**Call relations**: `listing` calls this when a query is present. If the search endpoint refuses the request, `_search` asks `_refusal` to create the right user-facing error; for normal results, it delegates row validation to `_entry`.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: This turns one raw listing item from skills.sh into a safe `CommunitySkill` object. It filters out rows that are missing a name or have an invalid source repository.

**Data flow**: It receives one unknown object from an outside response. If the object is not a dictionary, or if it lacks a usable skill name or valid `owner/repo` source, it returns `None`. Otherwise it reads the name, source, and install count and returns a structured `CommunitySkill`.

**Call relations**: Both `_popular` and `_search` call this because they receive similar but not identical raw entries from skills.sh. This helper gives both paths the same cleanup rules before anything reaches the UI.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: This safely downloads a response body from skills.sh while enforcing a maximum size. The size limit protects the web app from accidentally reading a huge response into memory.

**Data flow**: It receives an HTTP client, a URL, a byte limit, and optional request headers. It opens a streaming GET request, checks for a successful status, reads the response in chunks, counts the bytes as they arrive, stops with a clear error if the limit is exceeded, and returns the combined bytes when complete.

**Call relations**: `_popular` uses this to read the leaderboard page, and `fetch` uses it to read one skill download response. When the web status is not successful it hands the code to `_refusal`; when the body is too large it raises `CommunityUnavailable` directly.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: This reads a downloaded `SKILL.md` markdown document and pulls out the pieces the web app needs: name, description, instructions, and the original document text.

**Data flow**: It receives the document as text. It first looks for YAML front matter, which is a metadata block between `---` lines at the top of the file. If the metadata is readable and includes both a name and a description, it returns a `CommunityDocument` with the remaining markdown as instructions; otherwise it returns `None`.

**Call relations**: `fetch` calls this after it has found the `SKILL.md` file in the directory download. This keeps document parsing separate from network downloading, so `fetch` can focus on finding the file and caching the result.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).


### `extensions/web/ufo_ext_web/openai_login.py`

`io_transport` · `OpenAI sign-in request handling`

This file is the bridge between the UFO web portal and OpenAI’s device sign-in process. Device sign-in is the pattern where a service shows a short code, the user types that code into a website, and the app waits until the user approves it. It is like getting a numbered ticket: the app holds the ticket number, and OpenAI later says whether that ticket has been approved.

The main class, OpenAiDeviceLogin, has two visible steps. First, request_code asks OpenAI for a temporary device sign-in code. If OpenAI is unreachable or sends an unexpected answer, the function quietly returns no code so the page can still be shown safely. Second, claim checks whether the user has approved the sign-in yet. While the user is still deciding, it reports “pending.” If OpenAI provides an authorization code and a verifier, the file redeems those at OpenAI’s normal token endpoint.

The final token is checked before it is trusted. The code uses helper functions from ufo.sdk.models to parse the token response and confirm it belongs to a ChatGPT account. Only then does it return a stored credential string. The file is careful not to treat every failed poll as a hard failure, because OpenAI may reply with errors that simply mean “not approved yet.”

#### Function details

##### `OpenAiDeviceLogin.request_code`  (lines 85–103)

```
async def request_code(self) -> DeviceAuthorization | None
```

**Purpose**: This starts the OpenAI device sign-in by asking OpenAI for a one-time user code. The portal can show that code to the member so they know what to enter on OpenAI’s verification page.

**Data flow**: It starts with the configured OpenAI client id and the URL used to request device codes. It sends those to OpenAI over HTTP, checks that the response is successful and contains the expected text fields, then turns the answer into a DeviceAuthorization object containing the internal device id, the visible user code, the verification page, and the polling interval. If the network call fails or the response is not usable, it returns None instead of raising an error.

**Call relations**: This is the first step of the sign-in story. It uses httpx.AsyncClient to talk to OpenAI and asks _interval to turn OpenAI’s suggested polling delay into a safe integer. The result is handed back to the web flow so the page can show the user code before later polling with claim.

*Call graph*: calls 1 internal fn (_interval); 2 external calls (__init__, AsyncClient).


##### `OpenAiDeviceLogin.claim`  (lines 105–126)

```
async def claim(self, device_auth_id: str, user_code: str) -> DeviceClaim
```

**Purpose**: This checks whether the member has approved the device sign-in at OpenAI yet. If approval is complete, it continues the exchange and tries to produce a stored credential for the workspace.

**Data flow**: It receives the device authorization id and user code from the earlier step. It posts both values to OpenAI’s device token endpoint. If OpenAI says the sign-in is still waiting, it returns a pending DeviceClaim. If OpenAI returns an authorization code and a code verifier, it passes those to _redeem. If the response is missing the needed values, it returns a refused DeviceClaim with a message saying no API key was returned.

**Call relations**: This is the polling step after request_code. It uses _unapproved to interpret non-success replies from OpenAI, because some of them mean “keep waiting” rather than “failed.” When OpenAI does approve the sign-in, claim hands the received authorization code and verifier to _redeem to finish the token exchange.

*Call graph*: calls 2 internal fn (_redeem, _unapproved); 2 external calls (__init__, AsyncClient).


##### `OpenAiDeviceLogin._redeem`  (lines 128–153)

```
async def _redeem(self, client: httpx.AsyncClient, code: str, verifier: str) -> DeviceClaim
```

**Purpose**: This turns an approved OpenAI device sign-in into a token the system can store and use. It is the final, stricter checkpoint before a credential is accepted.

**Data flow**: It receives an HTTP client, an authorization code, and a verifier. It sends them to OpenAI’s token endpoint as form data, along with the client id and redirect URI that identify this sign-in flow. If OpenAI replies successfully, it parses the token response, checks that there is an access token, and confirms that the token contains a ChatGPT account id. If all checks pass, it returns a granted DeviceClaim with the stored credential string; otherwise it returns a refused DeviceClaim.

**Call relations**: This function is called only by claim, after OpenAI has said the user approved the device sign-in. It relies on ufo.sdk.models.granted to understand OpenAI’s token response and chatgpt_account_id to verify the token belongs to the kind of account this system expects.

*Call graph*: called by 1 (claim); 4 external calls (__init__, post, chatgpt_account_id, granted).


##### `_interval`  (lines 156–162)

```
def _interval(raw: object) -> int
```

**Purpose**: This turns OpenAI’s suggested polling delay into a usable number of seconds. It prevents the portal from polling too aggressively if OpenAI omits the value or sends something unreadable.

**Data flow**: It receives a raw value from OpenAI, which may be a string, number, missing value, or malformed value. It tries to convert that value into an integer. If conversion fails, it returns the file’s default polling interval.

**Call relations**: OpenAiDeviceLogin.request_code calls this after receiving the first device sign-in response. The cleaned interval becomes part of the DeviceAuthorization object that tells the web flow how often it should check for approval.

*Call graph*: called by 1 (request_code).


##### `_unapproved`  (lines 165–177)

```
def _unapproved(polled: httpx.Response) -> DeviceClaim
```

**Purpose**: This interprets OpenAI polling responses that are not normal success responses. Its main job is to tell the difference between “the user has not approved yet” and “the sign-in really failed.”

**Data flow**: It receives an HTTP response from OpenAI after a polling attempt. If the status code or error code is one OpenAI uses for a still-pending device sign-in, it returns a pending DeviceClaim. Otherwise, it returns a refused DeviceClaim with a message telling the user to start again.

**Call relations**: OpenAiDeviceLogin.claim calls this whenever the device polling request does not return a successful approval. This helper keeps claim from treating expected waiting states, such as authorization still pending or slow-down notices, as final failures.

*Call graph*: called by 1 (claim); 2 external calls (__init__, json).


### Workspace panels and suggestions
These files translate portal UI actions into workspace operations and prepare personalized starter content for members.

### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The portal lets people change things, connect accounts, delete records, and run special actions from panels. This file makes sure those changes do not become a separate back door. Instead, every write is turned into a normal tool intent and admitted into a durable conversation, like putting a signed request into the same mailroom that chat actions use. That matters because the conversation becomes the audit trail, keeps actions in order, and avoids two competing lanes changing the same app at once.

The file first defines what a panel is allowed to submit. `ApplyIntent` is a strict shape for object changes: only known verbs, only known object kinds, and only sensible pairings, such as “connect” for a connection or “delete” for a credential slot. It also defines first-run provider tiles and “unlock” suggestions, which tell the portal what useful apps become possible after connecting certain services.

When a request arrives, the file checks its size, parses it, refuses unsafe or impossible requests early, fills in missing agent settings when a partial form is saved, converts the request into a `ToolIntent`, admits it into the portal action conversation, and waits for the final result. Special actions, such as Slack install links or billing portal links, get custom response parsing. The file also provides the settings projection: a clean JSON view of an agent, deploy capabilities, models, schema, and admin-only audience information.

#### Function details

##### `ApplyIntent.kinds`  (lines 97–101)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the full set of object kinds that the portal is allowed to mutate through this intent path. This keeps the portal controls tied to the same strict list the server accepts.

**Data flow**: It reads the declared allowed values from the `kind` field on `ApplyIntent` → turns those literal type choices into a frozen set of strings → returns that set to callers.

**Call relations**: This is the base list used by other helpers on `ApplyIntent`. Those helpers then decide which kinds can be applied or deleted, so the portal and the server do not drift apart.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 104–106)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that can be created or updated with an `apply` action. It excludes kinds that are only deleted or only connected.

**Data flow**: It starts with all allowed kinds → removes delete-only kinds such as credentials and source triggers, and connect-only kinds such as connections → returns the remaining set.

**Call relations**: The web surface uses this when building kind-specific page data, so it can show create or edit controls only where the submit lane will actually accept them.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 109–115)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that can be deleted through the panel lane. In this file, every named kind can be submitted for deletion, even if some kinds cannot be edited.

**Data flow**: It reads the same allowed kind list as `ApplyIntent.kinds` → returns it unchanged as a frozen set.

**Call relations**: The web surface calls this while deciding which object pages should display delete controls. It stays tied to the same validation rules that will later accept or reject the submitted intent.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 118–139)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: Checks that a submitted verb makes sense for the object kind. It prevents impossible or unsafe combinations, such as editing a credential value through a public panel form.

**Data flow**: It receives an already parsed `ApplyIntent` → checks verb, kind, spec, and create-only combinations → either returns the same intent if valid or raises a validation error before any action is admitted.

**Call relations**: This runs automatically during Pydantic model validation, which means bad panel submissions are stopped during parsing. Later preparation and submission code can rely on these basic rules already being true.


##### `Unlock._names_offered_tiles_and_a_drawn_mark`  (lines 328–337)

```
def _names_offered_tiles_and_a_drawn_mark(self) -> 'Unlock'
```

**Purpose**: Validates that an unlock suggestion can actually be shown in the portal. It makes sure the icon exists and every required provider has a matching tile.

**Data flow**: It reads the unlock’s icon and provider requirement groups → checks the icon against known drawable icons and each provider name against the first-run provider catalog → returns the unlock or raises an error during startup/import.

**Call relations**: This runs when unlock entries are constructed. It protects the start screen from offering a row with a missing icon, missing label, or impossible provider requirement.


##### `Unlock.missing`  (lines 339–343)

```
def missing(self, held: frozenset[str]) -> tuple[str, ...]
```

**Purpose**: Tells the start screen which provider accounts a member still needs before an unlock can run. It treats each requirement group as “any one of these is enough.”

**Data flow**: It receives the provider names the member already has → checks each requirement group → for unmet groups, chooses the first provider name as the recommended missing account → returns the missing provider names in catalog order.

**Call relations**: Portal code can use this to separate ready-to-build apps from apps that need one or more connections first. It relies on the `Unlock` validation that provider names are real tiles.


##### `_action_intent`  (lines 516–527)

```
def _action_intent(kind: str, name: str | None, action: str, body: dict[str, JsonValue]) -> ToolIntent
```

**Purpose**: Builds the tool intent for a presented portal action, such as an action button on an object page. It locks the target into the server-side route rather than trusting the browser to name it.

**Data flow**: It receives the object kind, optional object name, action name, and action-specific body → creates an `object_action` input with the route-bound target and the body under `input` → returns a `ToolIntent` ready to admit as a turn.

**Call relations**: `submit_action` calls this after it has checked the request body and confirmed the action exists. The returned intent is then admitted into the portal action conversation.

*Call graph*: called by 1 (submit_action); 1 external calls (__init__).


##### `_tool_intent`  (lines 530–565)

```
def _tool_intent(submitted: ApplyIntent) -> ToolIntent
```

**Purpose**: Converts a validated panel mutation into the actual tool call the runtime understands. It is the translator between the portal’s form language and the system’s tool language.

**Data flow**: It receives an `ApplyIntent` → maps connect requests to `connect_account`, delete or detach requests to `object_delete`, and apply-like requests to `object_apply` with a YAML manifest → returns a `ToolIntent`.

**Call relations**: `submit_intent` uses this after request preparation succeeds. The resulting tool intent is what gets stored and executed as the member’s portal action turn.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 568–584)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Turns a finished tool run into the standard JSON answer a panel expects. It reports success, credential prompts, or a clear failure message.

**Data flow**: It receives the terminal frame from a turn and the turn id → checks whether the run finished successfully, asked for credentials, or failed/refused → returns a JSON HTTP response containing `applied`, `message`, and `turn_id`.

**Call relations**: This is the default result reader. More specialized readers for Slack, GitHub, iMessage, billing, and rebuild actions call it when their run did not finish normally, and `_intent_result` uses it for ordinary panel intents.

*Call graph*: called by 7 (_action_outcome, _github_outcome, _imessage_outcome, _intent_result, _portal_outcome, _rebuild_outcome, _slack_outcome); 1 external calls (JSONResponse).


##### `_slack_outcome`  (lines 587–602)

```
def _slack_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result of the Slack connection flow. On success, it extracts the Slack install URL or a human explanation of why no URL was created.

**Data flow**: It receives a terminal frame and turn id → if the frame is not successful, delegates to `_outcome` → otherwise finds a JSON object in the frame text, reads `authorize_url` and `hint`, and returns them in a JSON response.

**Call relations**: This function is selected through the action outcome table for the Slack connect action. It lets `submit_action` return a useful install link instead of just saying “Saved.”

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_github_outcome`  (lines 605–613)

```
def _github_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result of the GitHub connection flow. It looks for an install link in the tool’s text response.

**Data flow**: It receives a terminal frame and turn id → if the frame failed, delegates to `_outcome` → otherwise searches the text for a URL → returns that URL if found, or the tool’s text as the message if no URL exists.

**Call relations**: This is used for the GitHub connection action through the special outcome table. It gives the portal a clickable URL when GitHub installation requires one.

*Call graph*: calls 1 internal fn (_outcome); 1 external calls (JSONResponse).


##### `_imessage_outcome`  (lines 616–637)

```
def _imessage_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result of the iMessage connection flow. It reports whether the connection is pending or connected, gives the user instruction text, and may include an opt-in link.

**Data flow**: It receives a terminal frame and turn id → if not successful, delegates to `_outcome` → otherwise extracts a JSON object from the frame text, validates its state, instruction, and optional link → returns a JSON response for the portal.

**Call relations**: This is chosen for the iMessage connect action through the action outcome table. It lets `submit_action` answer with setup instructions rather than a generic success message.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_portal_outcome`  (lines 640–654)

```
def _portal_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the billing portal result when the billing action created a provider portal link. It returns the URL the user should open.

**Data flow**: It receives a terminal frame and turn id → if the run did not finish successfully, delegates to `_outcome` → otherwise extracts a JSON object from the text, reads `portal_url`, validates it is a string, and returns it.

**Call relations**: `_action_outcome` calls this only for the billing action when the body asks for the portal operation. Other billing outcomes use the normal `_outcome` path.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (_action_outcome); 2 external calls (loads, JSONResponse).


##### `_rebuild_outcome`  (lines 657–664)

```
def _rebuild_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Returns the tool’s own explanation after a rebuild request. This matters because rebuilds usually queue background work rather than changing the page immediately.

**Data flow**: It receives a terminal frame and turn id → if the run failed or was refused, delegates to `_outcome` → otherwise returns a success response whose message is the frame text.

**Call relations**: This is selected for report digest and page fact rebuild actions. It preserves the action’s own description of what was queued or skipped.

*Call graph*: calls 1 internal fn (_outcome); 1 external calls (JSONResponse).


##### `_action_outcome`  (lines 679–686)

```
def _action_outcome(kind: str, action: str, body: dict[str, JsonValue], frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Chooses the right way to read the result of a presented action. Most actions use the normal outcome, but a few need special parsing for links or queued-work messages.

**Data flow**: It receives action identity, request body, terminal frame, and turn id → checks for the billing portal special case → otherwise looks up a custom outcome reader or falls back to `_outcome` → returns the HTTP response.

**Call relations**: `submit_action` calls this after the admitted action turn reaches its terminal frame. It dispatches to `_portal_outcome` or other registered readers when the action’s answer has a special shape.

*Call graph*: calls 2 internal fn (_outcome, _portal_outcome); called by 1 (submit_action).


##### `_complete_agent_spec`  (lines 717–744)

```
async def _complete_agent_spec(ctx: SurfaceContext, submitted: ApplyIntent, submitted_fields: frozenset[str], agent_id: UUID, member_id: UUID) -> ApplyIntent | Response
```

**Purpose**: Fills in required agent settings that a partial settings form did not submit. This lets a panel save one field, such as a prompt-related setting, without resending every required agent field.

**Data flow**: It receives the current context, submitted intent, submitted field names, agent id, and member id → if the intent is not a partial agent apply, returns it unchanged → otherwise loads the current agent detail, copies required existing values under the submitted changes, and returns an updated intent or an error response.

**Call relations**: `_prepare_panel_intent` calls this before final refusal checks. It depends on `SurfaceContext.agent_detail` to read the existing agent state and prevents valid partial edits from failing later validation.

*Call graph*: calls 1 internal fn (agent_detail); called by 1 (_prepare_panel_intent); 2 external calls (model_copy, JSONResponse).


##### `_intent_refusal`  (lines 747–764)

```
async def _intent_refusal(ctx: SurfaceContext, submitted: ApplyIntent, submitted_fields: frozenset[str]) -> Response | None
```

**Purpose**: Performs early, friendly refusals for panel intents that are valid in shape but impossible in this deploy. It catches mistakes before creating a stored turn.

**Data flow**: It receives the context, prepared intent, and submitted field names → checks agent model names, sandbox-size availability, and credential slot existence → returns a JSON refusal response if something is wrong, or `None` if the intent may continue.

**Call relations**: `_prepare_panel_intent` calls this after completing partial agent specs. By refusing early, it avoids admitting turns that would later fail for predictable setup reasons.

*Call graph*: calls 1 internal fn (list_credential_slots); called by 1 (_prepare_panel_intent); 1 external calls (JSONResponse).


##### `_prepare_panel_intent`  (lines 767–796)

```
async def _prepare_panel_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID) -> ApplyIntent | Response
```

**Purpose**: Reads and validates a raw panel intent request. It is the front gate before a portal form submission can become a runtime tool intent.

**Data flow**: It reads the request body → rejects bodies that are too large or malformed → validates the JSON as a `PanelIntent` and `ApplyIntent` → checks frame access for connect actions → completes partial agent specs → applies early refusal rules → returns either a prepared `ApplyIntent` or an HTTP response explaining the problem.

**Call relations**: `submit_intent` calls this first. It hands off to `_complete_agent_spec` and `_intent_refusal`, so the later admission step only receives clean, allowed work.

*Call graph*: calls 3 internal fn (frame_admits, _complete_agent_spec, _intent_refusal); called by 1 (submit_intent); 3 external calls (loads, JSONResponse, body).


##### `_oversized_manifest`  (lines 799–813)

```
def _oversized_manifest(intent: ToolIntent) -> Response | None
```

**Purpose**: Checks whether a generated object-apply manifest became too large after conversion. This protects the action lane even when the original JSON request was within the size limit.

**Data flow**: It receives a `ToolIntent` → ignores non-`object_apply` tools → reads the YAML manifest from the intent → measures its encoded byte length → returns an HTTP 413 response if too large, otherwise returns `None`.

**Call relations**: `submit_intent` calls this after `_tool_intent` builds the runtime intent. It is a second size guard between request parsing and turn admission.

*Call graph*: called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `_intent_result`  (lines 816–843)

```
async def _intent_result(ctx: SurfaceContext, turn_id: UUID) -> Response
```

**Purpose**: Waits for an admitted panel intent to finish and turns its final state into an HTTP response. This is what makes the form submit feel synchronous to the user.

**Data flow**: It receives the context and turn id → tails the turn’s frames with a timeout → returns `_outcome` when a terminal frame arrives, returns a parked message if the turn pauses, returns a timeout response if it takes too long, or raises if the stream ends unexpectedly.

**Call relations**: `submit_intent` calls this after admitting the tool intent. It reads from `SurfaceContext.tail`, so the HTTP response reflects the actual recorded turn result.

*Call graph*: calls 2 internal fn (tail, _outcome); called by 1 (submit_intent); 2 external calls (timeout, JSONResponse).


##### `submit_intent`  (lines 846–882)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: Main handler for prepared panel mutation submissions. It validates the form’s intent, turns it into a tool call, admits it into the member’s portal action conversation, and waits for the result.

**Data flow**: It receives the surface context, HTTP request, agent id, member id, and email → prepares and validates the intent → converts it to a `ToolIntent` → checks final manifest size → finds or creates the member’s portal lane conversation → retitles it → admits the intent as a turn → returns the turn’s final outcome.

**Call relations**: This is the public orchestration point for ordinary panel writes. It calls `_prepare_panel_intent`, `_tool_intent`, `_oversized_manifest`, and `_intent_result`, while using the surface context to create the conversation and admit the turn.

*Call graph*: calls 7 internal fn (admit, conversation_for, retitle_conversation, _intent_result, _oversized_manifest, _prepare_panel_intent, _tool_intent); 1 external calls (conversation_audience).


##### `submit_action`  (lines 885–974)

```
async def submit_action(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str, *, kind: str, name: str | None, action: str) -> Response
```

**Purpose**: Main handler for action buttons the portal presents on object pages. It makes sure the route, not the browser body, decides the target of the action.

**Data flow**: It receives the context, request, agent and member identity, email, and route-bound action target → reads and checks the body size and JSON shape → rejects bodies that try to override envelope fields like kind or name → verifies the action is actually presented for that target → checks embedded-frame permission → builds and admits an `object_action` intent → waits for the terminal result and formats it.

**Call relations**: This is the action counterpart to `submit_intent`. It calls `_action_intent` to build the tool call and `_action_outcome` to interpret the result, while using context methods to discover actions, admit the turn, and tail its frames.

*Call graph*: calls 8 internal fn (admit, conversation_for, frame_admits, object_actions, retitle_conversation, tail, _action_intent, _action_outcome); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `_update_schema`  (lines 977–989)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the editable settings schema for the agent settings page. It removes fields that the page renders with custom controls instead of generic schema-driven form fields.

**Data flow**: It receives the deploy’s available sandbox sizes → starts from `AgentSpec`’s JSON schema → removes fields such as prompt, icon, purpose, input/output schemas, and sandbox size when unavailable → returns the trimmed schema.

**Call relations**: `agent_settings` calls this while building the settings JSON response. The front end can use the returned schema as the source of truth for ordinary editable fields.

*Call graph*: called by 1 (agent_settings); 1 external calls (model_json_schema).


##### `agent_settings`  (lines 992–1038)

```
async def agent_settings(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, *, admin: bool, archivable: bool) -> Response
```

**Purpose**: Returns the data needed to display an agent’s settings page. It includes current agent settings, deploy capabilities, available models, form schema, and admin-only audience information.

**Data flow**: It receives the context, agent id, member id, and flags for admin and archivable status → loads the agent detail → returns 404 if missing → optionally loads granted web audience emails for admins → builds a JSON response with agent metadata, deploy limits, model choices, current spec values, schema, and audience.

**Call relations**: This is the read-side companion to the submit handlers. It calls `_update_schema` for the form description, reads agent details through the surface context, and uses web audience helpers only when an admin is viewing the page.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


### `extensions/web/ufo_ext_web/starters.py`

`domain_logic` · `start screen request handling`

The start screen needs useful prompts, such as “Build me a tool for this task,” that feel specific to the member’s real work. This file decides what those prompts should be. It looks at three things: remembered facts about the member’s work, applications the workspace already has, and a catalog of applications the product knows how to create.

The main idea is a “slate,” which is a ranked list of possible starter rows plus an optional check-in question. A slate is cached for a short time, like keeping yesterday’s newspaper on the counter until a fresh one arrives. If the cached slate is still fresh, it is reused. If it is old, the old one can still be shown while a new one is generated, so the screen does not go blank.

Generation uses a language model, but with strict instructions and a tool-shaped response so the answer can be checked. Bad rows are dropped one by one: rows with invalid text, unknown catalog names, or duplicates do not poison the whole slate. The file also uses a short “claim” lock so two browser tabs do not pay for the same model work at once, and a cooldown after failures so repeated errors do not freeze or hammer the system.

#### Function details

##### `Slate.fresh`  (lines 112–113)

```
def fresh(self, now: datetime) -> bool
```

**Purpose**: Checks whether a stored slate is still safe to reuse. A slate is fresh only if it is young enough and was made with the current ranking instructions.

**Data flow**: It receives the current time and reads the slate’s saved generation time and prompt digest. It compares the age against the allowed lifetime and compares the saved instruction fingerprint against the current one. It returns true when both still match, otherwise false.

**Call relations**: When the cache is read, this check decides whether the stored slate can be returned immediately or whether the system should consider making a new one.


##### `starters_key`  (lines 124–125)

```
def starters_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key used to save and load one member’s cached starter slate. It keeps each member’s suggestions separate.

**Data flow**: It takes a member ID, turns it into the project’s standard member subject string, and prefixes it with the starter-cache label. The result is a single text key for the shared store.

**Call relations**: The cache reader uses this key when looking up an existing slate and when saving a newly generated slate.

*Call graph*: called by 2 (_held, read); 1 external calls (member_subject).


##### `claim_key`  (lines 128–129)

```
def claim_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key for the temporary claim that says one reader is already generating a slate for this member. This prevents duplicate model work from two tabs or repeated polling.

**Data flow**: It takes a member ID, converts it into the standard member subject string, and prefixes it with the claim label. The output is the text key where the claim stamp is stored.

**Call relations**: The claim-making flow uses this key to create or replace a generation claim, and the main read flow deletes it after generation finishes or fails.

*Call graph*: called by 2 (_claim, read); 1 external calls (member_subject).


##### `cooldown_key`  (lines 132–133)

```
def cooldown_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key for the failure cooldown marker for a member. This marker tells later reads not to immediately retry a model call that just failed.

**Data flow**: It takes a member ID, converts it into the standard member subject string, and prefixes it with the cooldown label. The result is the text key used to store the last failure time.

**Call relations**: The generation check reads this key before deciding whether to call the model, and the main read flow writes to it after a generation failure.

*Call graph*: called by 2 (_may_generate, read); 1 external calls (member_subject).


##### `_stamped`  (lines 136–145)

```
def _stamped(held: object, key: str) -> datetime | None
```

**Purpose**: Safely reads a timestamp from a stored dictionary-like value. If the stored value is missing, malformed, or from an older format, it treats it as absent instead of crashing.

**Data flow**: It receives an unknown stored value and the name of the timestamp field to look for. If the value is a dictionary with a readable ISO-format date string, it turns that string into a datetime. Otherwise it returns nothing.

**Call relations**: The cooldown check uses this to read failure times, and the claim logic uses it to read claim times. In both cases, a bad stamp simply means the system can do the safe fallback work.

*Call graph*: called by 2 (_claim, _may_generate); 1 external calls (fromisoformat).


##### `StarterCache.read`  (lines 168–185)

```
async def read(self) -> Slate | None
```

**Purpose**: Runs the full start-screen cache flow for one member. It returns a fresh slate if possible, returns an older one if that is the best available, and carefully tries to generate a replacement when allowed.

**Data flow**: It starts with the current time, reads any stored slate, and returns it immediately if it is fresh. If the slate is missing or stale, it checks whether this request is allowed to generate. If generation succeeds, it saves and returns the new slate. If generation fails, it records a cooldown warning and returns the old slate if there was one.

**Call relations**: This is the public path that ties the helper pieces together. It asks _held for the saved slate, asks _may_generate whether a model call should happen, calls _rank to make a new slate, and uses the storage-key helpers to save results, cool down failures, and clear the temporary claim.

*Call graph*: calls 6 internal fn (_held, _may_generate, _rank, claim_key, cooldown_key, starters_key); 2 external calls (now, warn).


##### `StarterCache._held`  (lines 187–194)

```
async def _held(self) -> Slate | None
```

**Purpose**: Loads the currently stored slate for this member, if there is a valid one. It ignores missing or broken stored data rather than letting a bad cache entry break the start screen.

**Data flow**: It builds the member’s starter-cache key, reads the stored value, and checks that it is a dictionary shaped like a Slate. If validation succeeds, it returns a Slate object. If the value is missing or invalid, it returns nothing.

**Call relations**: The main read flow calls this first so it knows whether there is anything usable to show before deciding whether to regenerate.

*Call graph*: calls 1 internal fn (starters_key); called by 1 (read).


##### `StarterCache._may_generate`  (lines 196–206)

```
async def _may_generate(self, now: datetime) -> bool
```

**Purpose**: Decides whether this read is allowed to spend work generating a new slate. It blocks generation when there is no model, no memory to rank from, the workspace cannot pay for model work, a recent failure is cooling down, or another reader already claimed the job.

**Data flow**: It reads the cache object’s model, remembered memory, and solvency flag, then reads any cooldown timestamp from storage. If these checks pass, it tries to acquire the generation claim. It returns true only when this request should be the one to generate.

**Call relations**: The main read flow calls this after finding no fresh slate. If it says no, the old slate is returned as-is. If it says yes, read moves on to _rank.

*Call graph*: calls 3 internal fn (_claim, _stamped, cooldown_key); called by 1 (read).


##### `StarterCache._claim`  (lines 208–225)

```
async def _claim(self, now: datetime) -> bool
```

**Purpose**: Tries to reserve the right to generate the slate for this member. This is a lightweight lock, meaning a marker that stops two requests doing the same expensive work at the same time.

**Data flow**: It writes a claim timestamp under the member’s claim key only if no claim exists. If a claim already exists, it reads its timestamp. A recent claim is respected, but an old abandoned claim can be replaced using the exact stored value as proof. It returns true if this request owns the claim.

**Call relations**: _may_generate calls this as its final gate before allowing a model call. The main read flow later deletes the claim key when the attempt is over.

*Call graph*: calls 2 internal fn (_stamped, claim_key); called by 1 (_may_generate); 1 external calls (isoformat).


##### `StarterCache._rank`  (lines 227–254)

```
async def _rank(self, now: datetime) -> Slate
```

**Purpose**: Asks the language model to rank useful starter rows for this member and turns the model’s structured reply into a Slate. This is where the personalized suggestions are actually created.

**Data flow**: It gathers the member’s recalled memory, existing applications, and known application catalog into a compact JSON payload. It sends that payload to the configured model with the long ranking instructions and a required tool response format. It then passes the model reply to settle_slate and returns the cleaned Slate.

**Call relations**: The main read flow calls this only after cache and claim checks pass. It hands the raw model answer to settle_slate so invalid or unsafe pieces can be filtered before anything is saved.

*Call graph*: calls 1 internal fn (settle_slate); called by 1 (read); 4 external calls (__init__, __init__, __init__, dumps).


##### `settle_slate`  (lines 257–290)

```
def settle_slate(reply: Message, generated_at: datetime) -> Slate
```

**Purpose**: Cleans and validates the model’s recorded starter ranking. It keeps good rows, drops bad ones, and raises an error if the model did not make the required structured record at all.

**Data flow**: It receives a model message and the generation time. It looks for the required record_slate tool call, reads its ranked entries, validates each one, keeps only entries that name a known catalog item and are not duplicates, and validates the optional check-in. It returns a Slate stamped with the generation time and current instruction digest.

**Call relations**: _rank calls this after the model replies. If settle_slate succeeds, the resulting Slate can be stored and shown. If the required tool call is missing, it raises an error so the read flow treats the generation as failed and falls back to the stored slate.

*Call graph*: called by 1 (_rank); 1 external calls (__init__).
