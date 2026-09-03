# Mounted user-facing and operator-facing surfaces  `stage-5.2`

This stage is the system’s front desk. It sits in the main work path, where outside places like the web app, Slack, iMessage, the terminal, and operator tools enter the runtime. A “surface” means one of these user-facing doors.

The web portal serves the browser app, signs members in, lists agents, sends messages, streams replies, shows transcripts, connects accounts, and edits workspace data. Web panels turn button or form submissions into normal recorded tool actions, while starters prepare useful first-prompt suggestions.

Shared surface code turns outside requests into trusted workspace, member, and conversation records. The hub broadcasts live turn updates, and hub tail helps late or reconnecting viewers catch up and finish cleanly.

Slack code verifies Slack events, converts messages and clicks into turns, posts replies and files back, formats mentions, adds safe bot attribution, and runs Slack-specific hooks. iMessage code chooses a real Spectrum Cloud line or a safe local fake line, then converts iMessages to conversations and UFO replies back to texts. The terminal surface turns runtime events into simple directives for the command-line client.

Operator-only routes serve the debugger and memory viewer, with shared sign-in and workspace-picking. Package marker files only make these modules importable.

## Files in this stage

### Browser portal
The member web portal serves the app, authenticates browser users, handles chat and workspace APIs, and routes portal actions through normal conversation machinery.

### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling`

This file is the web front door around UFO’s agents. It checks the signed session cookie, works out which workspace and member a request belongs to, and then answers the browser with only the agents, conversations, files, settings, usage, and objects that member is allowed to see. Think of it like a staffed reception desk: every visitor must show a badge, and every room they enter is checked against their badge again.

It also serves the built web application and its static assets. To make rolling deploys safe, it publishes assets into shared blob storage so a page opened from one server version can still fetch files even if the next request lands on another version.

The main chat path opens or continues conversations, saves attachments into the conversation workspace, admits the member’s message to the durable turn queue, and returns the turn id the browser should follow. Live replies are delivered through SSE, server-sent events: a one-way stream where the server pushes activity, text, files, questions, connection prompts, and final status to the browser.

Beyond chat, this file exposes portal panels for agents, skills, memory, credentials, sources, connections, usage, object lists/details, app homepages, first-run setup, and account connection flows for providers such as OpenAI and Anthropic.

#### Function details

##### `load_assets`  (lines 324–334)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Reads the built portal asset files from disk and keeps only file types this server knows how to serve. This prevents stray build files from being published accidentally.

**Data flow**: It receives a directory path, scans its direct files, reads approved files into memory with their media type, and returns a name-to-bytes table.

**Call relations**: It runs during module loading to build the local static asset table used later by static asset responses.

*Call graph*: 1 external calls (glob).


##### `rum_config`  (lines 347–360)

```
def rum_config(environ: Mapping[str, str]) -> dict[str, str] | None
```

**Purpose**: Builds the browser monitoring configuration for Datadog RUM, which records real user sessions when fully configured. It refuses partial setup so recordings do not disappear into the wrong place.

**Data flow**: It reads environment variables, returns no config if all are blank, returns a complete config if all are set, or raises an error if only some are set.

**Call relations**: portal_page calls it just before serving the web shell so the page gets deploy-specific monitoring settings.

*Call graph*: called by 1 (portal_page).


##### `portal_shell`  (lines 363–372)

```
def portal_shell(html: str, config: Mapping[str, str] | None) -> str
```

**Purpose**: Writes the monitoring configuration into the HTML shell served to the browser. It also checks that the built page still contains the expected placeholder.

**Data flow**: It takes HTML and an optional config, turns the config into safe JSON, replaces the one RUM placeholder, and returns the filled HTML.

**Call relations**: portal_page calls it after choosing which shell to serve.

*Call graph*: called by 1 (portal_page); 1 external calls (dumps).


##### `resolve_workspace`  (lines 385–427)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace a web request belongs to before the route handler runs. It keeps login tokens out of URLs by accepting them from a cookie or the one session-opening form post.

**Data flow**: It reads the session cookie or a small urlencoded form body, extracts the workspace claim, redirects cold arrivals to login when appropriate, and otherwise returns a workspace id, a response, or nothing.

**Call relations**: The shared surface machinery calls this as the early workspace gate; it uses _framed_length and _form for safe form parsing and _chat_target to preserve conversation links through login.

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 430–434)

```
def _chat_target(request: Request) -> UUID | None
```

**Purpose**: Parses an optional conversation id from the login redirect query string. It accepts only real UUID values.

**Data flow**: It reads the c query parameter, tries to turn it into a UUID, and returns that UUID or None.

**Call relations**: resolve_workspace uses it when redirecting an unauthenticated browser to login.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 437–447)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Serves a static portal asset from the current build if the requested name is known. It avoids direct filesystem lookup, which blocks path traversal tricks.

**Data flow**: It maps the request path to an in-memory asset entry and returns a cached asset response, or None if this build does not have it.

**Call relations**: static_asset tries this first before falling back to blob-stored assets from another build.

*Call graph*: calls 1 internal fn (_asset_response); called by 1 (static_asset).


##### `_asset_response`  (lines 450–454)

```
def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response
```

**Purpose**: Creates the HTTP response for one static asset, including browser cache validation. It can answer “not modified” when the browser already has the same file.

**Data flow**: It receives bytes, media type, and an ETag, compares the request’s If-None-Match header, and returns either a 304 response or the bytes.

**Call relations**: _static_response and _stored_asset both use it so local and stored assets behave the same way.

*Call graph*: called by 2 (_static_response, _stored_asset); 1 external calls (Response).


##### `load_apps`  (lines 480–513)

```
def load_apps(directory: Path) -> AppsBundle | None
```

**Purpose**: Loads the built shipped app pages from disk and computes a content digest for the whole tree. The digest gives every deploy’s app bundle a stable, content-based name.

**Data flow**: It scans a directory recursively, ignores dot-prefixed paths, reads file bytes, hashes paths and bytes, detects top-level app slugs, and returns an AppsBundle or None.

**Call relations**: It runs at import time to initialize APPS, which later homepage and asset-publishing code depend on.

*Call graph*: 4 external calls (__init__, sha256, is_dir, rglob).


##### `apps`  (lines 519–525)

```
def apps() -> AppsBundle
```

**Purpose**: Returns the loaded shipped app bundle, or raises a clear build error if it is missing. This makes missing frontend build output fail at the route that needs it.

**Data flow**: It reads the module-level APPS value and either returns it or raises an error naming the build command.

**Call relations**: agents_index, homepage, and _homepage_state call it before advertising shipped app pages.

*Call graph*: called by 3 (_homepage_state, agents_index, homepage).


##### `_publish_assets`  (lines 528–538)

```
async def _publish_assets(blob: BlobStore, apps: AppsBundle) -> None
```

**Purpose**: Copies portal and app assets into shared blob storage if they are not already there. This lets different server versions serve each other’s already-open pages during rolling deploys.

**Data flow**: It receives a blob store and app bundle, checks each expected key, uploads missing static assets, lists the app digest prefix, and uploads missing app files.

**Call relations**: _assets_published starts this work and waits for it before pages advertise assets.

*Call graph*: calls 3 internal fn (exists, list, put); called by 1 (_assets_published).


##### `_assets_published`  (lines 541–568)

```
def _assets_published(blob: BlobStore, apps: AppsBundle) -> 'asyncio.Task[None]'
```

**Purpose**: Ensures this process has one in-flight asset publish task and reuses it. If a publish failed, it starts a fresh retry.

**Data flow**: It checks the module-level publish task, creates a new async task when needed, stores it, and returns the task to await.

**Call relations**: portal_page, agents_index, and homepage call it before serving pages or homepage links that depend on shared assets.

*Call graph*: calls 1 internal fn (_publish_assets); called by 3 (agents_index, homepage, portal_page); 1 external calls (create_task).


##### `_stored_asset`  (lines 571–593)

```
async def _stored_asset(blob: BlobStore, request: Request) -> Response
```

**Purpose**: Serves a static asset from shared blob storage when the current build does not have it locally. This is the safety net for pages opened during a rolling deploy.

**Data flow**: It validates the requested asset name and file type, checks an in-memory cache, fetches bytes from blob storage if needed, caches a bounded entry, and returns an asset response or 404.

**Call relations**: static_asset calls it after _static_response misses.

*Call graph*: calls 3 internal fn (exists, get, _asset_response); called by 1 (static_asset); 3 external calls (sha256, Path, Response).


##### `portal_page`  (lines 596–617)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main web portal HTML shell to an authenticated member. It chooses between two shell layouts based on a feature flag.

**Data flow**: It checks that built assets exist, waits for shared asset publishing, reads the shell flag and RUM config, fills the HTML, and returns a no-store HTML response.

**Call relations**: This is the GET route for the portal root and is the browser’s starting document after login.

*Call graph*: calls 3 internal fn (_assets_published, portal_shell, rum_config); 2 external calls (flag_enabled, HTMLResponse).


##### `_refused`  (lines 633–634)

```
def _refused(message: str) -> Response
```

**Purpose**: Builds a standard JSON refusal response for account connection polling flows. It gives the browser a status plus a readable message.

**Data flow**: It receives a message and returns JSON with status refused and that message.

**Call relations**: openai_device_poll and anthropic_code use it for provider-side refusals or unavailable flows.

*Call graph*: called by 2 (anthropic_code, openai_device_poll); 1 external calls (JSONResponse).


##### `workspace_accounts`  (lines 637–656)

```
async def workspace_accounts(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports whether the signed-in member has connected coding accounts such as OpenAI or Anthropic. It never returns secret values.

**Data flow**: It authenticates the member, checks each known provider credential slot for that member, and returns provider rows with connected booleans.

**Call relations**: The accounts settings and first-run screens call this route to show connection state.

*Call graph*: calls 2 internal fn (member_credential_stored, _authenticate); 1 external calls (JSONResponse).


##### `openai_device`  (lines 659–682)

```
async def openai_device(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the OpenAI device login flow, where the member types a short code on OpenAI’s site. The long device handle is kept in an HTTP-only cookie.

**Data flow**: It authenticates the member, asks OpenAI for a device code, stores the private device handle in a cookie, and returns the user code and verification URL.

**Call relations**: The browser calls this when the member chooses to connect OpenAI; openai_device_poll continues the same flow.

*Call graph*: calls 1 internal fn (_authenticate); 4 external calls (__init__, JSONResponse, set_session_cookie, openai_client_id).


##### `openai_device_poll`  (lines 685–702)

```
async def openai_device_poll(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Checks whether the OpenAI device login has completed and stores the resulting key when it has. It tells the browser whether to keep waiting, show a refusal, or mark connected.

**Data flow**: It authenticates, reads the device cookie, asks OpenAI to claim the grant, stores the member credential on success, logs the event, and returns a status.

**Call relations**: The browser repeatedly calls this after openai_device until the provider answers.

*Call graph*: calls 3 internal fn (put_member_credential, _authenticate, _refused); 4 external calls (__init__, JSONResponse, openai_client_id, log).


##### `anthropic_authorize`  (lines 705–716)

```
async def anthropic_authorize(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the Anthropic code-based login flow. It creates an authorization URL and saves verifier state in a cookie.

**Data flow**: It authenticates the member, asks the Anthropic login helper for a pending authorization, writes its cookie, and returns the URL to open.

**Call relations**: The browser calls this before sending the member to Anthropic; anthropic_code finishes the flow.

*Call graph*: calls 1 internal fn (_authenticate); 4 external calls (__init__, JSONResponse, set_session_cookie, anthropic_client_id).


##### `anthropic_code`  (lines 719–741)

```
async def anthropic_code(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts the code the member pasted back from Anthropic and stores the verified access credential. It rejects missing, invalid, or unverifiable codes.

**Data flow**: It bounds and parses the form, authenticates, reads the pasted code and state cookie, claims the grant, verifies the key, stores it for the member, and returns connected.

**Call relations**: It completes the flow started by anthropic_authorize.

*Call graph*: calls 5 internal fn (put_member_credential, _authenticate, _form, _framed_length, _refused); 5 external calls (__init__, JSONResponse, anthropic_client_id, log, verified_key).


##### `account_disconnect`  (lines 744–757)

```
async def account_disconnect(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Removes one of the signed-in member’s connected coding accounts. This lets a member replace a revoked or wrong key without touching anyone else’s credentials.

**Data flow**: It authenticates, maps the provider path value to a credential slot, clears that member’s slot, logs the action, and returns disconnected or 404.

**Call relations**: The account settings page calls this when a member disconnects OpenAI or Anthropic.

*Call graph*: calls 2 internal fn (clear_member_credential, _authenticate); 2 external calls (JSONResponse, log).


##### `connect_arrival`  (lines 760–765)

```
async def connect_arrival(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles provider sign-in arrival links by redirecting the member back into the portal credentials area. The real flow lives inside the portal UI.

**Data flow**: It authenticates the session and returns a redirect to the portal hash for workspace credentials.

**Call relations**: Sign-in paths for provider setup use this as their landing route.

*Call graph*: calls 1 internal fn (_authenticate); 1 external calls (RedirectResponse).


##### `_authenticate`  (lines 768–792)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

**Purpose**: Turns the session cookie into a workspace member and email. It also creates or links the member row on first contact, then checks the member still has access.

**Data flow**: It reads the signed cookie, verifies it for this workspace, links the email to a member if needed, checks seat access, and returns member id plus email or an error response.

**Call relations**: Nearly every authenticated route uses this directly or through _audience_for.

*Call graph*: calls 3 internal fn (link_member, linked_member, member_has_access); called by 9 (_audience_for, account_disconnect, anthropic_authorize, anthropic_code, connect_arrival, fulfill_credential, openai_device, openai_device_poll, workspace_accounts); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 795–801)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a portal asset to an already workspace-scoped request. It first tries this build’s in-memory asset and then the shared store.

**Data flow**: It receives the request, asks _static_response for a local response, and otherwise awaits _stored_asset from blob storage.

**Call relations**: This is the GET route under the portal static path.

*Call graph*: calls 2 internal fn (_static_response, _stored_asset).


##### `open_session`  (lines 804–835)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a browser session by placing a posted bearer token into the session cookie. The token is accepted from the form body, not from a URL.

**Data flow**: It bounds and parses the form, validates the token field shape, creates a redirect response, writes the session cookie, and returns the redirect.

**Call relations**: This is the only POST at the portal root and is paired with resolve_workspace’s early token scoping.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 838–842)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Parses the agent id from a route path parameter. It returns None for malformed ids so routes can answer not found.

**Data flow**: It reads agent_id from path parameters, attempts UUID parsing, and returns the UUID or None.

**Call relations**: Chat, transcript, panel, and member-chat page gates use it before agent-scoped work.

*Call graph*: called by 4 (_member_chat_page, _panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 845–846)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the store key for the web-specific record of a conversation. That record binds a web chat to an agent and member email.

**Data flow**: It takes a conversation id and returns the chat store key string.

**Call relations**: _open_conversation writes this key and _own_web_chat reads it.

*Call graph*: called by 2 (_open_conversation, _own_web_chat).


##### `_chat_title`  (lines 855–873)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates a short conversation title from the first message or attached filenames. It trims cleanly so chat rail labels are readable.

**Data flow**: It receives message text and attachment paths, collapses whitespace, falls back to filenames if needed, cuts to the maximum length, and returns a title.

**Call relations**: _open_conversation uses it for new chats, and summarize_chat_titles reuses it to clean model-generated titles.

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_title_excerpt`  (lines 885–903)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

**Purpose**: Extracts the first useful user-and-assistant exchange for title generation. It avoids naming failed or unanswered conversations from empty content.

**Data flow**: It receives transcript messages, pulls first user and assistant text, strips hidden context from the user side, bounds each part, and returns joined excerpt text.

**Call relations**: summarize_chat_titles calls it before asking the model for a better title.

*Call graph*: calls 1 internal fn (_rendered_text); called by 1 (summarize_chat_titles); 1 external calls (member_message_text).


##### `summarize_chat_titles`  (lines 906–952)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

**Purpose**: Background job that writes short summary titles for conversations that do not yet have them. It works across web, Slack, and terminal conversations.

**Data flow**: It asks core for conversations awaiting titles, reads their transcripts, builds excerpts, asks the model for a short title when possible, stores the title or an empty marker.

**Call relations**: Scheduled by the extension runtime as the chat title batch job.

*Call graph*: calls 4 internal fn (conversations_awaiting_title, summarized_conversation_title, _chat_title, _title_excerpt); 2 external calls (__init__, __init__).


##### `seed_homepages`  (lines 955–1027)

```
async def seed_homepages(ctx: ExtensionContext, bucket: str | None=None) -> None
```

**Purpose**: Background job that starts one homepage-building turn for each eligible agent that has never been seeded. This gives agents a first homepage without manual prompting.

**Data flow**: It lists agents and seed markers, skips archived or shipped apps, finds an acting owner/admin, opens a conversation, invokes the seed prompt with an idempotency key, and records success.

**Call relations**: Runs on a schedule and uses core turn admission rather than direct page creation.

*Call graph*: calls 5 internal fn (earliest_seated_admin, invoke, open_conversation, turn_outcomes, workspace_agents); 3 external calls (__init__, now, shipped_app_slug).


##### `_open_conversation`  (lines 1030–1063)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and writes the web ownership record for it. It handles races so duplicate opens land on the already-created conversation safely.

**Data flow**: It makes a title and new id, writes a chat record, asks core for the conversation under a queue key, cleans up if another id won, retitles new conversations, and returns id plus title.

**Call relations**: _new_chat_target calls it when a first message opens a chat.

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_web_chat); called by 1 (_new_chat_target); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 1066–1073)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

**Purpose**: Reads the current display title for one conversation. It uses the same listing source as the UI so names stay consistent.

**Data flow**: It asks core for the one matching conversation row and returns its title or an empty string.

**Call relations**: _open_conversation uses it after a race finds an existing conversation.

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 1 (_open_conversation).


##### `_own_web_chat`  (lines 1076–1088)

```
async def _own_web_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is this member’s own web chat with this agent. Anything else is treated as absent.

**Data flow**: It reads the web chat record from the store, validates it, compares agent id and email, and returns the record or None.

**Call relations**: _member_chat and _open_conversation use it as the web-chat ownership proof.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 2 (_member_chat, _open_conversation).


##### `_member_chat`  (lines 1091–1136)

```
async def _member_chat(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, *, agent_visible: bool) -> ListedConversation | None
```

**Purpose**: Decides whether a member may read or continue a conversation through the portal chat view. It covers own web chats, private extension rooms, spoken rooms, and commentable Slack or terminal conversations.

**Data flow**: It checks the web chat row, reads the conversation listing, compares audience, surface, queue key, and visibility, and returns the listed conversation or None.

**Call relations**: Chat target resolution, permalink resolution, transcript reads, streams, and turn checks all rely on this gate.

*Call graph*: calls 3 internal fn (list_agent_conversations, _commentable, _own_web_chat); called by 5 (_existing_chat_target, _member_chat_page, _member_turn, _resolve_chat, transcript); 1 external calls (conversation_audience).


##### `_commentable`  (lines 1139–1143)

```
def _commentable(conversation: ListedConversation, member_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation can accept a portal comment from this member. Only selected surfaces and audiences qualify.

**Data flow**: It receives a listed conversation and member id, tests the surface and audience values, and returns true or false.

**Call relations**: _member_chat, _existing_chat_target, _member_turn, _resolve_chat, and _conversation_row use this to label and allow comments.

*Call graph*: called by 5 (_conversation_row, _existing_chat_target, _member_chat, _member_turn, _resolve_chat); 1 external calls (conversation_audience).


##### `_turn_context`  (lines 1146–1157)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

**Purpose**: Builds the context attached to an admitted chat turn, including sender, browser timezone, and source. Invalid timezones are ignored rather than blocking the message.

**Data flow**: It reads the timezone header, tries to build a TurnContext with it, logs and drops it on validation failure, and returns the context.

**Call relations**: _admit_chat passes this context into core admission.

*Call graph*: called by 1 (_admit_chat); 2 external calls (__init__, log).


##### `_chat_url`  (lines 1160–1163)

```
def _chat_url(public_base_url: str | None, conversation_id: UUID) -> str | None
```

**Purpose**: Builds a public portal link to a conversation when the deployment has a public base URL. Without that base, no link is possible.

**Data flow**: It receives a base URL and conversation id, returns a full portal hash URL or None.

**Call relations**: _chat_source and _comment_notice use it when naming where a chat message came from.

*Call graph*: called by 2 (_chat_source, _comment_notice).


##### `_chat_source`  (lines 1166–1175)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Creates the human-readable source string carried with a portal message. It includes a link to the chat when possible and always names the member email.

**Data flow**: It receives base URL, conversation id, and email, builds a chat URL if possible, and returns a source label string.

**Call relations**: _admit_chat uses it inside the TurnContext.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (_admit_chat).


##### `_comment_notice`  (lines 1178–1198)

```
def _comment_notice(public_base_url: str | None, conversation: ListedConversation, member_id: UUID, email: str, text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Formats a portal comment so it can be delivered into a Slack or terminal conversation. It names who commented, links back to the portal when possible, and lists attachments.

**Data flow**: It receives conversation, member, email, text, and paths, chooses an author label, builds a comment verb/link, appends attachment names, and returns markdown-like text.

**Call relations**: _existing_chat_target uses it when the target conversation is commentable.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (_existing_chat_target); 2 external calls (PurePosixPath, conversation_audience).


##### `_audience_for`  (lines 1201–1208)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Combines authentication with the web audience lookup. The audience is the set of agents and admin powers this email has in the web portal.

**Data flow**: It authenticates, then asks the web audience module for permissions, and returns member id, email, and audience or an error response.

**Call relations**: Most portal API routes call this before doing member-visible work.

*Call graph*: calls 1 internal fn (_authenticate); called by 25 (_member_chat_page, _member_turn, _object_gate, _panel_gate, action_views, agents_index, agents_status, chat, chats_index, connection_pool (+15 more)); 2 external calls (web_audience, web_extension).


##### `_visibility_flag`  (lines 1234–1238)

```
def _visibility_flag(agent: AgentSummary) -> str | None
```

**Purpose**: Finds the feature flag that controls whether an agent should be shown in portal lists. Some agents are always shown and return no flag.

**Data flow**: It inspects whether the agent is main or shipped by an app extension and returns the relevant flag key or None.

**Call relations**: _flag_reads and agents_index use it when building the boot payload.

*Call graph*: called by 2 (_flag_reads, agents_index); 1 external calls (shipped_app_slug).


##### `_flag_reads`  (lines 1241–1261)

```
async def _flag_reads(agents: tuple[AgentSummary, ...]) -> dict[str, bool]
```

**Purpose**: Reads all feature flags needed for the portal boot response in one batch. Portal screens default open, while shipped apps default hidden until explicitly enabled.

**Data flow**: It derives flag keys from portal surfaces and agents, concurrently reads each flag with the right default, and returns a key-to-boolean map.

**Call relations**: agents_index calls it before returning visible surfaces and hidden agent state.

*Call graph*: calls 1 internal fn (_visibility_flag); called by 1 (agents_index); 2 external calls (gather, flag_enabled).


##### `_setup_configured`  (lines 1264–1275)

```
def _setup_configured(state: SetupState) -> bool
```

**Purpose**: Decides whether an installed app has accepted at least one setup offer or has no setup needs. This helps avoid offering duplicate starter work.

**Data flow**: It receives setup state, checks connectors, credentials, and standing orders, and returns whether setup is considered configured.

**Call relations**: workspace_starters uses it while deciding starter rows.

*Call graph*: called by 1 (workspace_starters).


##### `agents_index`  (lines 1278–1407)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the portal’s boot data: signed-in member, reachable agents, available portal screens, archived apps, homepage state, and setup status. This is the first API read the web app needs.

**Data flow**: It authenticates and resolves audience, publishes assets, reads homepages, setup state, archived agents, grants, and flags, then returns a structured JSON payload.

**Call relations**: The browser calls it on startup to know what to draw and which agents can be opened.

*Call graph*: calls 8 internal fn (list_archived_agents, _assets_published, _audience_for, _bound_page, _flag_reads, _homepage_state, _visibility_flag, apps); 6 external calls (Semaphore, gather, JSONResponse, shipped_app_slug, granted_emails, web_extension).


##### `agents_index.setup_of`  (lines 1329–1331)

```
async def setup_of(agent: AgentSummary) -> SetupState
```

**Purpose**: Reads setup state for one provisioned agent while respecting a concurrency limit. This keeps the boot request fast without flooding shared resources.

**Data flow**: It receives an agent from the surrounding agents_index flow, waits on the semaphore, asks core for that agent’s setup state, and returns it.

**Call relations**: agents_index creates many of these small tasks when building setup_due and stands_on_setup.


##### `agents_status`  (lines 1410–1451)

```
async def agents_status(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns live status summaries for the member’s visible agents. It tells the UI which agents are active, what they are doing, and whether their last turn failed.

**Data flow**: It resolves audience, asks core for status across agents, peeks latest activity for running turns, and returns status rows.

**Call relations**: The browser polls this beside the agent index while the portal is open.

*Call graph*: calls 4 internal fn (agent_turn_statuses, latest_activity, _audience_for, _iso); 1 external calls (JSONResponse).


##### `_framed_length`  (lines 1454–1467)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Checks that a route about to parse a whole body has a trustworthy Content-Length under a limit. It rejects chunked or missing-length bodies for those routes.

**Data flow**: It reads transfer and content-length headers, returns a 411 or 413 response on bad framing, or None when safe to parse.

**Call relations**: Form-parsing routes call it before _form to avoid unbounded buffering.

*Call graph*: called by 6 (_parse_inbound, anthropic_code, fulfill_credential, open_session, preview, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 1470–1477)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses a request form and converts parser failures into a clear bad-request response. This keeps malformed multipart data from becoming an internal error.

**Data flow**: It calls request.form, returns the parsed form on success, or returns a 400 response if the form parser rejects the body.

**Call relations**: Login, credential, chat multipart, preview, and provider code routes use it.

*Call graph*: called by 6 (_parse_inbound, anthropic_code, fulfill_credential, open_session, preview, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 1480–1488)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a request body while enforcing a hard byte limit based on actual bytes consumed. It protects routes that accept raw bodies.

**Data flow**: It streams chunks into a buffer, stops with 413 if the limit is exceeded, and returns the bytes otherwise.

**Call relations**: _parse_inbound and upload_start use it for text messages and upload-start JSON.

*Call graph*: called by 2 (_parse_inbound, upload_start); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 1491–1544)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...], tuple[str, ...]] | Response
```

**Purpose**: Parses a chat composer submission into text, inline files, and already-uploaded blob keys. It enforces body type and attachment-count limits.

**Data flow**: It checks content type, reads plain text or bounded multipart form data, validates uploaded_key entries, counts attachments, and returns text plus file references or an error.

**Call relations**: _chat_inbound calls it as the first step in admitting a chat message.

*Call graph*: calls 4 internal fn (_bounded_body, _form, _framed_length, _uploaded_key); called by 1 (_chat_inbound); 1 external calls (Response).


##### `_uploaded_key`  (lines 1547–1556)

```
def _uploaded_key(raw: str) -> str | None
```

**Purpose**: Validates that a submitted uploaded_key points only under the web upload prefix. This prevents a send from pulling arbitrary workspace blob data into a conversation.

**Data flow**: It receives a raw key string, checks containment under the upload root, and returns the key or None.

**Call relations**: _parse_inbound uses it for each presigned-upload reference.

*Call graph*: called by 1 (_parse_inbound); 1 external calls (contained_relative).


##### `_inbox_paths`  (lines 1559–1570)

```
def _inbox_paths(uploads: tuple[UploadFile, ...], uploaded_keys: tuple[str, ...]) -> tuple[str, ...]
```

**Purpose**: Chooses safe workspace paths for all attachments in a message. Duplicate names are numbered instead of overwriting each other.

**Data flow**: It receives inline uploads and uploaded blob keys, extracts filenames, sanitizes and uniquifies them, and returns web-inbox paths.

**Call relations**: _chat_inbound uses these paths before _deliver_uploads writes files there.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (PurePosixPath, inbox_name).


##### `_deliver_uploads`  (lines 1573–1588)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], uploaded_keys: tuple[str, ...], paths: tuple[str, ...]) -> None
```

**Purpose**: Copies all attachments into the conversation workspace before the agent turn starts. That way the agent sees the files at the paths mentioned in the message.

**Data flow**: It receives upload objects, uploaded blob keys, and target paths, streams inline uploads or blob streams, and writes each file through the workspace writer.

**Call relations**: _admit_chat calls it immediately before admitting the message.

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (_admit_chat).


##### `_files_note`  (lines 1591–1594)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Appends a machine-readable note listing saved attachment paths to the admitted message text. This lets transcript rendering later recover which files belonged to the member’s bubble.

**Data flow**: It receives text and paths, formats the attachment note, and returns text plus note or just the note.

**Call relations**: _chat_inbound uses it whenever a message has attachments.

*Call graph*: called by 1 (_chat_inbound).


##### `_member_attachments`  (lines 1602–1610)

```
def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]
```

**Purpose**: Separates a member’s visible words from the hidden attachment path note. This lets the UI show file cards instead of showing the storage sentence.

**Data flow**: It receives stored message text, searches for the attachment note, and returns cleaned words plus path tuple.

**Call relations**: _member_bubble uses it when rendering user messages.

*Call graph*: called by 1 (_member_bubble).


##### `_attachment_preview`  (lines 1613–1625)

```
def _attachment_preview(public_base_url: str | None, agent_id: UUID, conversation_id: UUID, path: str) -> str | None
```

**Purpose**: Builds a preview URL for an attached image file if the deployment can serve one. Non-image files and deployments without a public base get no preview URL.

**Data flow**: It receives base URL, agent id, conversation id, and path, checks the raster image type, URL-quotes the path, and returns a full preview route or None.

**Call relations**: _conversation_messages and transcript aids pass it as the attachment resolver used by message rendering.

*Call graph*: 2 external calls (raster_image_media_type, quote).


##### `_attachment_payload`  (lines 1628–1641)

```
def _attachment_payload(path: str, preview_url: str | None) -> dict[str, object]
```

**Purpose**: Creates the UI payload for one member-attached file. It names the file, media type, and optional preview, but no download URL.

**Data flow**: It receives a workspace path and preview URL, derives filename and media type, and returns a dictionary for the chat UI.

**Call relations**: _member_bubble uses it for each attachment path.

*Call graph*: called by 1 (_member_bubble); 2 external calls (PurePosixPath, raster_image_media_type).


##### `_member_bubble`  (lines 1644–1652)

```
def _member_bubble(said: str, attach: Attach | None) -> dict[str, object]
```

**Purpose**: Builds the rendered chat bubble for a member message. It includes cleaned text and any attached file cards.

**Data flow**: It receives stored member text and an optional preview-link function, splits attachments, builds the base user bubble, adds file payloads, and returns it.

**Call relations**: _TranscriptRenderer._member and _conversation_messages use it whenever they draw a user-side message.

*Call graph*: calls 2 internal fn (_attachment_payload, _member_attachments); called by 2 (_member, _conversation_messages).


##### `_upload_chunks`  (lines 1655–1657)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded multipart file in fixed-size chunks. This avoids reading large files into memory all at once.

**Data flow**: It receives an UploadFile, repeatedly reads chunks, and yields each non-empty chunk.

**Call relations**: _deliver_uploads uses it when writing inline uploads to the workspace.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_key`  (lines 1660–1665)

```
def _answer_key(conversation_id: UUID, turn_id: UUID, index: int) -> str
```

**Purpose**: Creates the idempotency key for a member’s answer to a specific agent question. This makes double-clicked answers join the same admitted message.

**Data flow**: It receives conversation id, asking turn id, and question index, and returns a stable key string.

**Call relations**: _admit_chat uses it when admitting answers, and _asks uses it to match answers back to questions.

*Call graph*: called by 2 (_admit_chat, _asks).


##### `_answer_headers`  (lines 1668–1680)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Parses headers that say a chat message is answering a specific question. Malformed headers are rejected before any message is admitted.

**Data flow**: It reads answer-turn and answer-question headers, returns None for ordinary messages, returns parsed UUID and index for answers, or a 400 response.

**Call relations**: _chat_inbound calls it after parsing the body.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 1683–1692)

```
def _stop_header(request: Request) -> UUID | None | Response
```

**Purpose**: Parses the header that says a request is stopping a running turn rather than sending a message. Bad turn ids are rejected early.

**Data flow**: It reads the stop-turn header, returns None when absent, returns a UUID when valid, or a 400 response.

**Call relations**: _chat_inbound calls it before message validation.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (Response, UUID).


##### `_chat_inbound`  (lines 1713–1735)

```
async def _chat_inbound(ctx: SurfaceContext, request: Request) -> _ChatInbound | Response
```

**Purpose**: Turns a raw chat request into a validated internal message object. It also enforces rules such as “stop requests have no body” and “messages cannot be empty.”

**Data flow**: It parses stop and answer headers, parses body/files, checks presigned uploads exist, assigns inbox paths, appends file notes, enforces character limits, and returns _ChatInbound or an error.

**Call relations**: chat calls it before resolving the target conversation.

*Call graph*: calls 5 internal fn (_answer_headers, _files_note, _inbox_paths, _parse_inbound, _stop_header); called by 1 (chat); 2 external calls (__init__, Response).


##### `_new_chat_target`  (lines 1738–1763)

```
async def _new_chat_target(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, inbound: _ChatInbound) -> _ChatTarget | Response
```

**Purpose**: Opens a new conversation target for a first chat message. It rejects answer or stop requests because those must name an existing conversation.

**Data flow**: It checks the audience can access the agent, opens the conversation with a fresh queue key, and returns conversation id and title.

**Call relations**: _resolve_chat_target uses it when the conversation query parameter is new.

*Call graph*: calls 2 internal fn (allows, _open_conversation); called by 1 (_resolve_chat_target); 3 external calls (__init__, Response, uuid4).


##### `_existing_chat_target`  (lines 1766–1797)

```
async def _existing_chat_target(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, inbound: _ChatInbound) -> _ChatTarge
```

**Purpose**: Resolves an existing conversation as the target for a message. It also prepares a comment notice when the target is a commentable Slack or terminal conversation.

**Data flow**: It gates the conversation through _member_chat, builds optional comment text, and returns _ChatTarget or a not-found response.

**Call relations**: _resolve_chat_target calls it for UUID conversation parameters.

*Call graph*: calls 4 internal fn (allows, _comment_notice, _commentable, _member_chat); called by 1 (_resolve_chat_target); 2 external calls (__init__, Response).


##### `_resolve_chat_target`  (lines 1800–1821)

```
async def _resolve_chat_target(ctx: SurfaceContext, request: Request, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, inbound: _ChatInbound) -> _ChatTarget | Response
```

**Purpose**: Chooses whether a chat request opens a new conversation or continues an existing one. It centralizes validation of the conversation query parameter.

**Data flow**: It reads the conversation query value, chooses the new-chat path or parses a UUID, then delegates to the matching target helper.

**Call relations**: chat calls it after parsing the incoming message.

*Call graph*: calls 2 internal fn (_existing_chat_target, _new_chat_target); called by 1 (chat); 3 external calls (Response, web_extension, UUID).


##### `_stop_chat`  (lines 1824–1837)

```
async def _stop_chat(ctx: SurfaceContext, request: Request, conversation_id: UUID, turn_id: UUID) -> Response
```

**Purpose**: Stops a turn in a conversation if the member is allowed to reach that turn. It returns whether the stop ended anything and any related founded turn.

**Data flow**: It authorizes the named turn, calls core stop_turn for the conversation, catches wrong-conversation errors, and returns JSON.

**Call relations**: chat calls it when _chat_inbound found a stop header.

*Call graph*: calls 2 internal fn (stop_turn, _member_turn); called by 1 (chat); 2 external calls (JSONResponse, Response).


##### `_admit_chat`  (lines 1840–1880)

```
async def _admit_chat(ctx: SurfaceContext, request: Request, target: _ChatTarget, inbound: _ChatInbound, member_id: UUID, email: str) -> Response
```

**Purpose**: Saves attachments and admits a member message into a conversation. It returns the turn and conversation information the browser needs to stream the reply.

**Data flow**: It builds an optional answer idempotency key, delivers uploads, calls core admit with body, context, speaker, and comment, then returns turn id, arrival id, title, and answer body when relevant.

**Call relations**: chat calls it for normal messages and answers.

*Call graph*: calls 6 internal fn (admit, admitted_body, _answer_key, _chat_source, _deliver_uploads, _turn_context); called by 1 (chat); 1 external calls (JSONResponse).


##### `chat`  (lines 1883–1918)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Main chat POST route for sending messages, answering questions, opening conversations, and stopping turns. It is the browser’s write path into agent conversation work.

**Data flow**: It resolves member audience, parses agent id, validates chat permission, parses inbound content, resolves the target conversation, then either stops a turn or admits the message.

**Call relations**: The route table exposes it at agents/{agent_id}/chat.

*Call graph*: calls 6 internal fn (_admit_chat, _agent_param, _audience_for, _chat_inbound, _resolve_chat_target, _stop_chat); 1 external calls (Response).


##### `_rendered_text`  (lines 1921–1932)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Extracts readable text from a stored message while stripping hidden context wrappers from user messages. This keeps prompts and transcript display separate.

**Data flow**: It receives a Message, reads either plain content or text blocks, removes user context markers, trims whitespace, and returns text.

**Call relations**: Title generation and transcript rendering call it.

*Call graph*: called by 3 (_assistant, _member, _title_excerpt).


##### `_append_activity`  (lines 1935–1936)

```
def _append_activity(events: list[dict[str, str]], text: str) -> None
```

**Purpose**: Adds one activity event to a rendered event list. It gives tool work a consistent shape for the chat UI.

**Data flow**: It receives an events list and text, appends a dictionary with kind activity, and mutates the list.

**Call relations**: _TranscriptRenderer._assistant and _subagent_activity use it.

*Call graph*: called by 2 (_assistant, _subagent_activity).


##### `_stored_activity`  (lines 1939–1951)

```
def _stored_activity(block: ToolUseBlock, result: ToolResultBlock) -> str | None
```

**Purpose**: Chooses the activity text to show for a tool call. It prefers stored activity text, then user descriptions, then skill names, and finally the tool name when appropriate.

**Data flow**: It receives a tool-use block and matching tool-result block, inspects their fields, and returns display text or None.

**Call relations**: Assistant and subagent transcript rendering use it to turn tool blocks into readable progress.

*Call graph*: called by 2 (_assistant, _subagent_activity).


##### `_subagent_activity`  (lines 1973–1997)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

**Purpose**: Renders the work steps inside a spawned subagent run. It shows notes and tool activities but not the final answer, which is handled separately.

**Data flow**: It receives subagent transcript messages, matches tool uses to activity results, collects note and activity events, bounds the list, and returns it.

**Call relations**: _subagent_nodes calls it after reading child-run transcripts.

*Call graph*: calls 2 internal fn (_append_activity, _stored_activity); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 2000–2010)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

**Purpose**: Tries to decode a subagent finish answer as a JSON object. Structured finish payloads can then be shown as readable prose.

**Data flow**: It receives answer text, returns None for blank or non-object JSON, or returns the decoded dictionary.

**Call relations**: _run_answer calls it before deciding how to display a run’s answer.

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 2013–2036)

```
def _payload_prose(value: JsonValue) -> str
```

**Purpose**: Turns a structured JSON value into plain text a member can read. Lists and objects are expanded instead of shown as raw JSON.

**Data flow**: It receives a JSON-like value, recursively formats strings, booleans, numbers, lists, and objects, and returns prose.

**Call relations**: _run_answer uses it for finish payloads with multiple fields.

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 2039–2055)

```
def _run_answer(answer: str) -> str
```

**Purpose**: Chooses the display text for a subagent run’s final answer. It unwraps simple structured payloads and formats richer ones.

**Data flow**: It receives answer text, decodes any JSON payload, returns a single prose field directly, formats multi-field payloads, or returns the original text.

**Call relations**: _subagent_nodes and _TranscriptAids.render use it when displaying profile-run conversations.

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (render, _subagent_nodes).


##### `_subagent_nodes`  (lines 2058–2097)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

**Purpose**: Builds the tree of subagent runs spawned by conversation turns. Each node carries the child run’s name, target, work events, answer, and nested children.

**Data flow**: It receives descendant turns, resolves agent names when needed, reads bounded child transcripts concurrently, fills activity events, nests nodes by parent turn, and returns a parent-keyed map.

**Call relations**: _transcript_aids uses it for settled transcripts, and _events uses it when a live turn finishes.

*Call graph*: calls 4 internal fn (list_agents, read_transcript, _run_answer, _subagent_activity); called by 2 (_events, _transcript_aids); 2 external calls (__init__, gather).


##### `_ReplyState.note_answer`  (lines 2108–2114)

```
def note_answer(self) -> '_ReplyState'
```

**Purpose**: Moves an accumulated assistant answer into the event list as a note when later work shows it was not the final answer. This preserves narration that happened before more tool activity.

**Data flow**: It copies pending events, inserts the current answer at its original position if allowed by the note limit, clears the answer, and returns a new state.

**Call relations**: _TranscriptRenderer._assistant and _TranscriptRenderer._member call it while grouping transcript messages.

*Call graph*: called by 2 (_assistant, _member); 1 external calls (replace).


##### `_TranscriptRenderer.render`  (lines 2131–2147)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts stored messages into the list of chat bubbles and replies the portal displays. It groups assistant text, activity, subagents, questions, files, apps, and controls around the right turn.

**Data flow**: It receives messages, builds an activity lookup, walks messages in order through assistant/member handlers, flushes the final reply, and returns rendered entries.

**Call relations**: _rendered_messages creates the renderer and calls this method.

*Call graph*: calls 3 internal fn (_assistant, _flush, _member); 1 external calls (__init__).


##### `_TranscriptRenderer._assistant`  (lines 2149–2167)

```
def _assistant(self, message: Message, activity: Mapping[str, ToolResultBlock], state: _ReplyState) -> _ReplyState
```

**Purpose**: Updates render state from one assistant message. It records final-looking text as the current answer and tool calls as activity events.

**Data flow**: It receives a message, activity map, and state, extracts text, possibly converts older answer text to a note, adds tool activity events, and returns updated state.

**Call relations**: render calls it for assistant-role messages.

*Call graph*: calls 4 internal fn (note_answer, _append_activity, _rendered_text, _stored_activity); called by 1 (render); 1 external calls (replace).


##### `_TranscriptRenderer._member`  (lines 2169–2199)

```
def _member(self, message: Message, state: _ReplyState, rendered: list[dict[str, object]]) -> _ReplyState
```

**Purpose**: Handles one user-role message while rendering a transcript. It decides whether the message is a real member bubble, starts or closes assistant replies by turn, and attaches speaker/question labels.

**Data flow**: It extracts text and message references, flushes pending assistant state as needed, skips hidden agent-origin or already-stated answers, builds a member bubble, and appends it.

**Call relations**: render calls it for non-assistant messages.

*Call graph*: calls 4 internal fn (note_answer, _flush, _member_bubble, _rendered_text); called by 1 (render); 2 external calls (replace, member_message_text).


##### `_TranscriptRenderer._flush`  (lines 2201–2238)

```
def _flush(self, state: _ReplyState, rendered: list[dict[str, object]], *, include_subagents: bool) -> _ReplyState
```

**Purpose**: Writes the current assistant reply into the rendered transcript if it has anything visible. It attaches related subagents, questions, files, apps, and connect controls.

**Data flow**: It receives state and output list, looks up data for the closing turn when requested, builds an assistant reply dictionary, appends it, and returns cleared state.

**Call relations**: render and _member call it whenever a reply boundary is reached.

*Call graph*: called by 2 (_member, render); 1 external calls (replace).


##### `_rendered_messages`  (lines 2241–2316)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

**Purpose**: Public helper for rendering a transcript window into portal message dictionaries. It hides internal prompts and attaches all extra conversation aids consistently.

**Data flow**: It receives messages plus optional maps for subagents, speakers, questions, files, apps, connections, and attachments, constructs a _TranscriptRenderer, and returns rendered messages.

**Call relations**: _TranscriptAids.render is the main caller.

*Call graph*: called by 1 (render); 1 external calls (__init__).


##### `_asks`  (lines 2335–2367)

```
def _asks(conversation_id: UUID, turns: tuple[Turn, ...], admitted: tuple[KeyedAdmission, ...]) -> _Asks
```

**Purpose**: Builds question cards for turns that asked the member something, including answers that already landed. It avoids drawing those answer messages twice.

**Data flow**: It receives conversation id, turns, and keyed admissions, matches answer idempotency keys, marks closed older answered questions, records message refs already stated, and returns _Asks.

**Call relations**: _transcript_aids calls it before rendering transcripts.

*Call graph*: calls 1 internal fn (_answer_key); called by 1 (_transcript_aids); 2 external calls (__init__, member_message_text).


##### `_TranscriptAids.render`  (lines 2390–2409)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Renders messages using all gathered transcript helper data. For profile-run conversations, it also rewrites assistant text through the run-answer formatter.

**Data flow**: It passes its stored maps into _rendered_messages, optionally rewrites assistant reply text, and returns the final list.

**Call relations**: _conversation_messages and _history_messages use aids produced by _transcript_aids.

*Call graph*: calls 2 internal fn (_rendered_messages, _run_answer).


##### `_transcript_aids`  (lines 2412–2464)

```
async def _transcript_aids(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, agent_origin: frozenset[str], speakers: dict[str, str], asked: dict[str, str], opens: frozenset[UUI
```

**Purpose**: Collects everything transcript rendering needs besides raw messages. This includes turns, subagent runs, shared files, created apps, speakers, answers, and connection controls.

**Data flow**: It concurrently reads turns, spawned turns, artifacts, and admissions, builds file/app/question/speaker maps, creates the attachment preview helper, and returns _TranscriptAids.

**Call relations**: _conversation_messages uses it for live transcript windows, and _history_messages uses it for earlier compacted pages.

*Call graph*: calls 9 internal fn (conversation_subagent_turns, keyed_admissions, list_conversation_artifacts, list_turns, _asks, _connect_controls, _created_apps, _file_payload, _subagent_nodes); called by 2 (_conversation_messages, _history_messages); 3 external calls (__init__, gather, partial).


##### `_connect_controls`  (lines 2467–2507)

```
async def _connect_controls(ctx: SurfaceContext, conversation_id: UUID, turns: tuple[Turn, ...], viewer: UUID) -> dict[str, dict[str, object]]
```

**Purpose**: Builds the connect-account controls that belong under specific assistant replies for the viewing member. Completed requests show the account that landed instead of an active button.

**Data flow**: It checks whether connect flows are available, walks turns, filters requests for the viewer, returns active controls for open requests, or landed account summaries for completed ones.

**Call relations**: _transcript_aids calls it while preparing transcript rendering.

*Call graph*: calls 4 internal fn (connect_available, held_accounts, _connect_control, _provider_label); called by 1 (_transcript_aids).


##### `_conversation_messages`  (lines 2510–2632)

```
async def _conversation_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], Turn | None, int]
```

**Purpose**: Creates the portal’s rendered view of a conversation, including settled transcript messages, a running prompt, queued arrivals, and an earlier-history cursor. This one projection is shared by chat and read-only transcript pages.

**Data flow**: It reads transcript, origin refs, speakers, compactions, latest turn, and queued arrivals; renders settled messages with aids; appends live or queued user bubbles; and returns messages, latest turn, and earlier page index.

**Call relations**: transcript and conversation_transcript call it for the main conversation payload.

*Call graph*: calls 10 internal fn (agent_origin_refs, arrival_speakers, latest_turn, list_compactions, queued_arrivals, read_transcript, turn_detail, _member_bubble, _transcript_aids, _verified_earlier); called by 2 (conversation_transcript, transcript); 3 external calls (gather, partial, member_message_text).


##### `_verified_earlier`  (lines 2635–2651)

```
async def _verified_earlier(ctx: SurfaceContext, conversation_id: UUID, indices: tuple[int, ...], messages: tuple[Message, ...]) -> int
```

**Purpose**: Finds the newest valid compaction page that sits immediately above a transcript window. It avoids offering history pages that would duplicate messages already visible.

**Data flow**: It receives compaction indices and current messages, reads each candidate after-window from newest to oldest, compares it with the current prefix, and returns the matching index or 0.

**Call relations**: _conversation_messages and _history_messages use it when producing earlier cursors.

*Call graph*: calls 1 internal fn (read_compaction_after); called by 2 (_conversation_messages, _history_messages).


##### `_history_cursor`  (lines 2654–2656)

```
def _history_cursor(index: int, end: int | None=None) -> str
```

**Purpose**: Encodes a compacted-history position into an opaque cursor string. The cursor hides internal page coordinates from the browser.

**Data flow**: It receives a compaction index and optional end position, formats them, base64-url encodes the result, strips padding, and returns the cursor.

**Call relations**: Transcript and history functions return these cursors to the browser.

*Call graph*: called by 4 (fits, _history_messages, conversation_transcript, transcript); 1 external calls (urlsafe_b64encode).


##### `_history_position`  (lines 2659–2674)

```
def _history_position(cursor: str) -> tuple[int, int | None]
```

**Purpose**: Decodes and validates a compacted-history cursor. Invalid cursors are rejected rather than trusted.

**Data flow**: It receives cursor text, checks length, decodes base64, parses index and optional end, validates numeric ranges, and returns them or raises ValueError.

**Call relations**: _history_messages calls it before reading a history page.

*Call graph*: called by 1 (_history_messages); 1 external calls (b64decode).


##### `_bounded_history_page`  (lines 2677–2699)

```
def _bounded_history_page(messages: list[dict[str, object]], index: int, end: int) -> tuple[list[dict[str, object]], int]
```

**Purpose**: Cuts a rendered history window down to a page that fits both message-count and byte-size limits. This prevents huge transcript pages from overwhelming the browser.

**Data flow**: It receives rendered messages, compaction index, and end position, tests JSON payload sizes, binary-searches the earliest fitting start when needed, and returns the page plus start index.

**Call relations**: _history_messages uses it after rendering a compacted window.

*Call graph*: called by 1 (_history_messages).


##### `_bounded_history_page.fits`  (lines 2682–2687)

```
def fits(start: int) -> bool
```

**Purpose**: Checks whether a proposed history slice fits the byte limit once serialized as JSON. It includes the cursor that would accompany the page.

**Data flow**: It receives a start index from the enclosing function, builds a sample payload, serializes it through JSONResponse, and returns whether the body is small enough.

**Call relations**: _bounded_history_page calls it during fast checks and binary search.

*Call graph*: calls 1 internal fn (_history_cursor); 1 external calls (JSONResponse).


##### `_history_messages`  (lines 2702–2753)

```
async def _history_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, cursor: str, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], str | None] | None
```

**Purpose**: Reads and renders one earlier page from a compacted conversation. It makes scrolling upward through long conversations safe and non-duplicating.

**Data flow**: It decodes the cursor, reads the compaction record, removes kept tail messages, gathers transcript aids, renders the window, bounds the page, computes the next cursor above, and returns page data or None.

**Call relations**: _conversation_history calls it when a transcript request includes a cursor.

*Call graph*: calls 8 internal fn (agent_origin_refs, arrival_speakers, read_compaction, _bounded_history_page, _history_cursor, _history_position, _transcript_aids, _verified_earlier); called by 1 (_conversation_history); 1 external calls (gather).


##### `transcript`  (lines 2756–2800)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the chat transcript for a member’s own portal-readable conversation. It also tells the browser which live turn to stream or which handoffs remain open.

**Data flow**: It authenticates, validates agent and conversation ids, gates through _member_chat, renders conversation messages, adds earlier cursor and live/terminal handoff data, and returns JSON.

**Call relations**: The chat view calls this when loading or reloading a conversation.

*Call graph*: calls 7 internal fn (_agent_param, _audience_for, _conversation_messages, _history_cursor, _member_chat, _open_handoffs, _opens); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 2803–2816)

```
async def _open_handoffs(ctx: SurfaceContext, terminal: TerminalFrame, member_id: UUID) -> dict[str, object]
```

**Purpose**: Reports handoffs from the newest committed turn that still need member action, currently pending credential prompts. It lets reloads redraw prompts that live streaming already showed.

**Data flow**: It receives a terminal frame and member id, renews pending credential prompts if present, and returns a dictionary of handoffs.

**Call relations**: transcript calls it for settled latest turns.

*Call graph*: calls 1 internal fn (_pending_prompts); called by 1 (transcript).


##### `_connect_control`  (lines 2819–2823)

```
def _connect_control(ctx: SurfaceContext, provider: str, turn_id: UUID) -> dict[str, object]
```

**Purpose**: Builds the payload for a connect-account button tied to a turn. It names the provider and turn, not a stale provider consent URL.

**Data flow**: It receives context, provider, and turn id, looks up the provider label, and returns a control dictionary.

**Call relations**: _connect_controls and _events use it for transcript and live stream controls.

*Call graph*: calls 1 internal fn (_provider_label); called by 2 (_connect_controls, _events).


##### `_provider_label`  (lines 2826–2837)

```
def _provider_label(ctx: SurfaceContext, provider: str) -> str
```

**Purpose**: Finds the friendly display name for a provider. It prefers the portal’s curated first-run catalog and falls back to the connect system or raw slug.

**Data flow**: It receives a provider name, searches FIRST_RUN_PROVIDERS, otherwise asks the context for a label if connect flows exist, and returns text.

**Call relations**: Connection controls and agent_setup use it so provider names are consistent.

*Call graph*: calls 2 internal fn (connect_available, connect_label); called by 3 (_connect_control, _connect_controls, agent_setup).


##### `_provider_summary`  (lines 2840–2847)

```
def _provider_summary(provider: str) -> str
```

**Purpose**: Finds the short explanation for a curated provider. Unknown providers get no summary rather than invented copy.

**Data flow**: It receives a provider slug, searches FIRST_RUN_PROVIDERS, and returns its summary or an empty string.

**Call relations**: agent_setup uses it when describing required connector accounts.

*Call graph*: called by 1 (agent_setup).


##### `chats_index`  (lines 2850–2862)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Resolves a portal conversation permalink into either a chat rail row or a readable conversation projection. It requires the conversation id query parameter.

**Data flow**: It authenticates, reads the requested conversation id string, and delegates to _resolve_chat, returning errors for missing input.

**Call relations**: The browser calls it for #/c/<id> links.

*Call graph*: calls 2 internal fn (_audience_for, _resolve_chat); 2 external calls (Response, web_extension).


##### `_resolve_chat`  (lines 2865–2953)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

**Purpose**: Determines what a conversation permalink means for this member. It can return an owned chat row, a commentable conversation, a readable admin conversation, or nothing.

**Data flow**: It parses the UUID, checks chat agents with _member_chat, reads latest turn details for chat rows, falls back to agent conversation lookup, and returns JSON rows or empty results.

**Call relations**: chats_index delegates all permalink resolution to it.

*Call graph*: calls 9 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, allows, _commentable, _conversation_row, _iso, _member_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 2956–2969)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Shared gate for per-agent panel reads and mutations. It authenticates the member and verifies the route’s agent is in their web audience.

**Data flow**: It resolves member, email, and audience, parses the agent id, checks audience access, and returns member/email/audience/agent id or 404.

**Call relations**: Settings, setup, skills, conversations, actions, intents, homepage, and connection routes use it.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 11 (_readable_conversation, actions, agent_setup, community_skill, community_skills, connections, conversations, homepage, intents, settings (+1 more)); 1 external calls (Response).


##### `_iso`  (lines 2972–2973)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Formats optional datetimes for JSON responses. It returns null for missing dates.

**Data flow**: It receives a datetime or None and returns ISO text or None.

**Call relations**: Many row-building helpers use it for timestamps.

*Call graph*: called by 6 (_conversation_row, _memory_rows, _resolve_chat, _usage_payload, agents_status, object_detail); 1 external calls (isoformat).


##### `_window_param`  (lines 2976–2992)

```
def _window_param(request: Request) -> int | None | Response
```

**Purpose**: Parses the requested usage reporting time window. It accepts named ranges or a bounded number of seconds.

**Data flow**: It reads query parameters, validates the range or window_seconds value, and returns seconds, None for all time, or an error response.

**Call relations**: workspace_usage calls it before reading spending reports.

*Call graph*: called by 1 (workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 2995–3035)

```
def _usage_payload(report: MemberSpendReport | SpendReport) -> dict[str, object]
```

**Purpose**: Converts a usage report into the JSON shape the portal charts expect. It includes totals, daily data, executions, and model breakdowns.

**Data flow**: It receives a member or workspace spend report, reads its nested usage fields, formats timestamps, and returns a dictionary.

**Call relations**: workspace_usage uses it for both member and admin workspace reports.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_usage).


##### `skills`  (lines 3038–3062)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists skills available to the selected agent. These are the skill documents the workspace page can display or edit.

**Data flow**: It gates the agent through _panel_gate, asks core for agent skills, and returns their metadata as JSON.

**Call relations**: The skills panel calls this route.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 3068–3072)

```
def _community_refusal(fault: Exception) -> Response
```

**Purpose**: Turns a community directory failure into a response meant for the member to read. A special header tells the UI the body is user-facing copy.

**Data flow**: It receives an exception, converts it to text, and returns a 502 response with the refusal header.

**Call relations**: community_skills and community_skill use it for directory outages or HTTP failures.

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 3079–3095)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists or searches community skills that could be installed for the selected agent. Short search strings are rejected to avoid noisy queries.

**Data flow**: It gates the agent, reads and validates q, asks the community directory for results, handles outages, and returns skill rows.

**Call relations**: The community skills panel calls it before an install intent is submitted elsewhere.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 3098–3119)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches one community skill document for review. It validates owner, repo, and skill names before asking the directory.

**Data flow**: It gates the agent, validates path segments, fetches the skill document, returns JSON when found, or 404/refusal on failure.

**Call relations**: The UI calls it when a member opens a community skill detail.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 3122–3197)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows or searches memory items visible to the member across reachable agents. It supports recent listing with cursors and search across agents.

**Data flow**: It resolves audience, returns empty unavailable data if memory is off, then either lists recent memory by subject/kind/cursor or searches every reachable agent and deduplicates results.

**Call relations**: The workspace memory tab calls it and uses _memory_rows for display rows.

*Call graph*: calls 7 internal fn (object_actions, recent_memory, search_memory, decode, _action_payloads, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 3200–3210)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Formats memory matches for JSON output. It keeps only display text, kind, reference, subject, and creation time.

**Data flow**: It receives memory matches, formats each optional reference and timestamp, and returns a list of dictionaries.

**Call relations**: workspace_memory uses it for both recent and search results.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `connections`  (lines 3213–3222)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connector accounts visible for one selected agent. It includes the member’s private grants, shared grants, and admin-visible edges.

**Data flow**: It gates the agent, asks core for agent connections scoped to the member/admin state, and returns JSON rows.

**Call relations**: The agent connections panel calls this route.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 3225–3235)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists all connector accounts visible to the member across the workspace, trimming each connection’s agent list to agents they can see.

**Data flow**: It resolves audience, reads connection pool rows, filters agent references by audience access, and returns JSON.

**Call relations**: Workspace connection views use this broader read.

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 3238–3244)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns GitHub coverage information visible to the member or admin. This likely powers a setup or diagnostics view for GitHub-connected work.

**Data flow**: It resolves audience, asks core for GitHub coverage using member and admin scope, and returns the model as JSON.

**Call relations**: The route table exposes it at github/coverage.

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 3247–3278)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists conversations for the selected agent that this member may see. Admins may also see disclosure state for conversations they cannot yet read.

**Data flow**: It gates the agent, reads a bounded conversation page with optional search, formats rows, and returns them plus a more flag.

**Call relations**: The agent conversations panel calls this route.

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 3281–3285)

```
def _searched(request: Request) -> str | None
```

**Purpose**: Reads and bounds a listing search string from the query parameters. Empty input becomes no search.

**Data flow**: It trims q, cuts it to the maximum search length, and returns the string or None.

**Call relations**: conversations uses it before calling the core listing.

*Call graph*: called by 1 (conversations).


##### `_conversation_row`  (lines 3288–3322)

```
def _conversation_row(entry: ListedConversation, member_id: UUID, agent: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Formats one conversation listing row for the portal. It includes source, audience, timestamps, speaker names, readability, disclosure, and commentability.

**Data flow**: It receives a ListedConversation, viewer member id, and optional agent summary, then builds a JSON dictionary with safe fields.

**Call relations**: conversations and _resolve_chat use it for panel and permalink results.

*Call graph*: calls 2 internal fn (_commentable, _iso); called by 2 (_resolve_chat, conversations).


##### `_readable_conversation`  (lines 3325–3345)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes a read-only content request for a conversation under an agent. It hides unreadable, wrong-agent, malformed, and absent conversations behind the same 404.

**Data flow**: It gates the agent, parses or receives conversation id, asks core whether it is readable for this member/admin, and returns agent id, conversation id, and SlotViewer or an error.

**Call relations**: Conversation transcript, history, attachment, and slot routes use it.

*Call graph*: calls 3 internal fn (readable_conversation, _opens, _panel_gate); called by 4 (_conversation_history, _slot_target, conversation_attachment, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 3348–3366)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a read-only transcript for an authorized conversation. It uses the same rendering as chat so all screens agree.

**Data flow**: It handles cursor requests through _conversation_history, otherwise authorizes the conversation, renders messages, adds an earlier cursor, and returns JSON.

**Call relations**: The conversations panel calls it when opening a conversation transcript.

*Call graph*: calls 4 internal fn (_conversation_history, _conversation_messages, _history_cursor, _readable_conversation); 1 external calls (JSONResponse).


##### `_member_chat_page`  (lines 3369–3397)

```
async def _member_chat_page(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes transcript-like reads for the member’s own chat conversation. This is used as a fallback where normal panel authorization does not apply.

**Data flow**: It resolves audience, validates agent and conversation ids, gates through _member_chat, and returns agent id, conversation id, and SlotViewer.

**Call relations**: _conversation_history and conversation_attachment use it after _readable_conversation fails.

*Call graph*: calls 4 internal fn (_agent_param, _audience_for, _member_chat, _opens); called by 2 (_conversation_history, conversation_attachment); 4 external calls (__init__, Response, web_extension, UUID).


##### `_conversation_history`  (lines 3400–3421)

```
async def _conversation_history(ctx: SurfaceContext, request: Request, cursor: str) -> Response
```

**Purpose**: Serves one earlier page of a compacted transcript. It accepts either a readable panel conversation or the member’s own chat page.

**Data flow**: It tries read-only authorization, falls back to member-chat authorization, reads a history page through _history_messages, and returns messages plus an optional cursor.

**Call relations**: conversation_transcript calls it when the request has a cursor.

*Call graph*: calls 3 internal fn (_history_messages, _member_chat_page, _readable_conversation); called by 1 (conversation_transcript); 2 external calls (JSONResponse, Response).


##### `_inbox_attachment`  (lines 3424–3435)

```
def _inbox_attachment(path: str) -> bool
```

**Purpose**: Checks whether a path names a direct file saved by the web composer. It blocks access to any other workspace path.

**Data flow**: It receives a path string, verifies it is web-inbox/<single-name> and not dot navigation, and returns true or false.

**Call relations**: conversation_attachment uses it before reading bytes.

*Call graph*: called by 1 (conversation_attachment).


##### `conversation_attachment`  (lines 3438–3478)

```
async def conversation_attachment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a safe image preview for one member-attached file. It only serves validated raster images, never arbitrary workspace files.

**Data flow**: It authorizes the conversation, validates path and image type, checks workspace file size, reads the file stream, validates the image bytes, and returns image bytes or an error.

**Call relations**: Attachment preview URLs generated during transcript rendering point to this route.

*Call graph*: calls 5 internal fn (list_workspace_files, read_workspace_file, _inbox_attachment, _member_chat_page, _readable_conversation); 5 external calls (__init__, Response, raster_image_media_type, validated_image_preview, log).


##### `_slot_target`  (lines 3498–3522)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

**Purpose**: Authorizes a conversation slot request and handles subagent conversation slots that are rooted in another conversation. Slots are typed side panels such as artifacts or changes.

**Data flow**: It reads optional root query, validates ids, authorizes the root conversation, checks spawned subagent membership when needed, and returns a SlotTarget.

**Call relations**: conversation_slots and conversation_slot call it before slot work.

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 3525–3541)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

**Purpose**: Builds the context object passed to a conversation slot provider. It includes audience, messages, conversation id, agent id, and public base URL.

**Data flow**: It reads the conversation audience and transcript, combines them with the extension context, and returns ConversationSlotContext or None.

**Call relations**: conversation_slots and conversation_slot use it before summarizing or reading slot content.

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 3544–3628)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

**Purpose**: Adds host-side projection data needed by certain slot providers, such as workspace changes, artifacts, sites, or automations. It also gathers visibility information used later for authorization.

**Data flow**: It inspects the extension and payload type, reads the relevant conversation data or objects, builds projection or visible-item entries, and returns an updated slot context.

**Call relations**: conversation_slots and conversation_slot call it for each provider before summary/read.

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 7 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit).


##### `_authorized_slot_payload`  (lines 3631–3675)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

**Purpose**: Filters slot payloads so they include only items the viewer may open or read. It redacts hidden automation content while preserving visible existence.

**Data flow**: It receives a slot payload and context visible-items list, filters sites or automations by name/generation, redacts protected fields when needed, and returns the safe payload.

**Call relations**: conversation_slot calls it before returning provider data.

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 3678–3722)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists which typed side panels are available for a conversation and how many items each has. Failed providers are logged and skipped instead of breaking the whole slot list.

**Data flow**: It authorizes the slot target, builds shared context, projects context per provider, asks each provider for a count, and returns slot summaries.

**Call relations**: The transcript UI calls it to decide which side tabs to show.

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 3725–3752)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full payload for one typed conversation slot. It verifies the provider exists and that it returns the expected payload type.

**Data flow**: It authorizes target, finds the slot provider, builds and projects context, reads provider payload, checks its type, filters it for visibility, and returns JSON.

**Call relations**: The UI calls it when opening a specific slot tab.

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 3755–3758)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Extracts the workspace changes projection from a slot context. It raises if the context was not prepared correctly.

**Data flow**: It receives a ConversationSlotContext, checks projection type, and returns WorkspaceChanges.

**Call relations**: _read_changes and _summarize_changes use it for the built-in changes slot.

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 3761–3762)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Reads the prepared workspace changes slot payload. It is the read function for the built-in changes slot provider.

**Data flow**: It receives slot context and returns the already attached WorkspaceChanges projection.

**Call relations**: CHANGES_SLOT references it as its provider read callback.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 3765–3766)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts changes for the changes slot summary. It hides the slot when there are no changes.

**Data flow**: It receives slot context, counts projection changes, and returns the count or None.

**Call relations**: CHANGES_SLOT references it as its summarize callback.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 3779–3782)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Extracts the artifacts projection from a slot context. It protects against calling the artifacts slot without host-prepared artifact data.

**Data flow**: It checks that context.projection is an ArtifactsSlotPayload and returns it, otherwise raises.

**Call relations**: _read_artifacts and _summarize_artifacts use it.

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 3785–3786)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Reads the prepared artifacts slot payload. It is the read function for the built-in artifacts slot provider.

**Data flow**: It receives slot context and returns the attached ArtifactsSlotPayload.

**Call relations**: ARTIFACTS_SLOT references it as its provider read callback.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 3789–3791)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts artifacts for the artifacts slot summary. It hides the slot when there are no artifacts.

**Data flow**: It receives slot context, counts artifacts in the projection, and returns the count or None.

**Call relations**: ARTIFACTS_SLOT references it as its summarize callback.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `workspace_credentials`  (lines 3804–3816)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists declared workspace credential slots and whether they are filled, without exposing values. It also returns collection actions for credential management.

**Data flow**: It authenticates, reads credential slot metadata, reads credential collection actions, and returns both as JSON.

**Call relations**: The workspace credentials panel calls this route.

*Call graph*: calls 4 internal fn (list_credential_slots, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 3819–3844)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace member roster and whether the current member can manage it. It also exposes member collection actions.

**Data flow**: It resolves audience, lists members, formats id/email/admin/seated fields, includes can_manage from admin status, and returns actions.

**Call relations**: The team panel calls this route.

*Call graph*: calls 4 internal fn (list_members, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 3847–3858)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the member, such as connected knowledge sources. Admins see all; members see their own and shared sources.

**Data flow**: It resolves audience, asks core for sources using member/admin scope, and returns JSON rows.

**Call relations**: The workspace sources view calls it.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `_connect_declared`  (lines 3872–3878)

```
def _connect_declared(ctx: SurfaceContext, surface: str, action: str) -> bool
```

**Purpose**: Checks whether this deployment registered the action needed to connect a chat surface. If not, the UI should not offer a button that cannot work.

**Data flow**: It asks for instance actions on the surface object and returns whether the named action is present.

**Call relations**: workspace_surfaces uses it for Slack and iMessage offering state.

*Call graph*: calls 1 internal fn (object_actions); called by 1 (workspace_surfaces).


##### `workspace_surfaces`  (lines 3881–3932)

```
async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports chat surface installations and per-member surface connection state for Slack, iMessage, and terminal. It also gives the terminal install command when possible.

**Data flow**: It resolves audience, reads installations and member surfaces, filters installations by visible agents, checks connect actions and iMessage availability, builds surface rows, and returns JSON.

**Call relations**: The workspace surfaces/settings view calls this route.

*Call graph*: calls 4 internal fn (list_installations, member_surfaces, _audience_for, _connect_declared); 3 external calls (__init__, JSONResponse, imessage_offered).


##### `workspace_first_run`  (lines 3961–4000)

```
async def workspace_first_run(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the connector and provider information needed by the first-run setup screen. It also says whether the member has their own model key.

**Data flow**: It authenticates, reads installed surfaces and member key state, builds provider and connector rows, gathers relevant collection actions, and returns JSON.

**Call relations**: The first-run UI calls it before showing setup choices.

*Call graph*: calls 6 internal fn (list_installations, member_holds_own_model_key, object_actions, object_kind, _action_payloads, _audience_for); 2 external calls (__init__, JSONResponse).


##### `connector_catalog`  (lines 4003–4031)

```
async def connector_catalog(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connectable providers from installed broker connectors. It supports bounded search and cursor paging.

**Data flow**: It authenticates, validates query and cursor lengths, asks core for a catalog page, formats provider tiles, and returns the next cursor.

**Call relations**: The connector page calls it as members search for providers.

*Call graph*: calls 2 internal fn (connector_catalog, _audience_for); 3 external calls (__init__, JSONResponse, Response).


##### `_held_providers`  (lines 4082–4090)

```
async def _held_providers(ctx: SurfaceContext, member_id: UUID, *, admin: bool) -> frozenset[str]
```

**Purpose**: Computes which provider accounts the workspace/member already has, using the same provider names as starter recommendations. Slack installation counts as a held provider.

**Data flow**: It reads connection pool rows and surface installations, collects provider slugs, adds Slack if installed, and returns a frozen set.

**Call relations**: workspace_starters calls it before filling starter and unlock rows.

*Call graph*: calls 2 internal fn (list_connections, list_installations); called by 1 (workspace_starters).


##### `fill_starters`  (lines 4093–4182)

```
def fill_starters(slate: Slate, held: frozenset[str], taken: frozenset[str], installed: tuple[StarterApp, ...]=()) -> tuple[tuple[StarterRow, ...], UnlockRow | None]
```

**Purpose**: Chooses which recommended starter rows the start screen should show based on available accounts and already-installed apps. It separates ready apps, unlock offers, and a check-in row.

**Data flow**: It receives a ranked slate, held providers, taken app names, and installed app state, filters duplicates and configured apps, builds StarterRow and UnlockRow objects, and returns rows plus the main unlock.

**Call relations**: workspace_starters calls it after loading or generating the member’s slate.

*Call graph*: called by 1 (workspace_starters); 4 external calls (__init__, __init__, __init__, get).


##### `_solvent`  (lines 4185–4194)

```
async def _solvent() -> bool
```

**Purpose**: Checks whether the workspace balance still allows spending for starter generation. It avoids model generation when the workspace is out of usable headroom.

**Data flow**: It opens an extension transaction, reads balance headroom, compares balance with reserve and grace, and returns true or false.

**Call relations**: workspace_starters passes the result into StarterCache.

*Call graph*: called by 1 (workspace_starters); 2 external calls (read_headroom, web_extension).


##### `_recalled`  (lines 4197–4204)

```
async def _recalled(ctx: SurfaceContext, member_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads recent memory snippets visible to this member for starter generation. It uses the member’s own and shared subjects, not other members’ private memory.

**Data flow**: It checks memory availability, builds subjects, reads a bounded recent memory page, truncates text, and returns snippets.

**Call relations**: workspace_starters uses these snippets as input to StarterCache.

*Call graph*: calls 1 internal fn (recent_memory); called by 1 (workspace_starters); 2 external calls (audience_subjects, conversation_audience).


##### `workspace_starters`  (lines 4207–4265)

```
async def workspace_starters(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns personalized starter suggestions for the member, such as apps they can build, a check-in, and an account unlock. Ranking may be cached, but access is decided live.

**Data flow**: It resolves audience, reads installed app setup state, recalls memory, checks solvency, loads a StarterCache slate, reads held providers and taken names, fills rows, and returns JSON.

**Call relations**: The start screen calls it before the member has chosen what to ask.

*Call graph*: calls 7 internal fn (agent_setup, _audience_for, _held_providers, _recalled, _setup_configured, _solvent, fill_starters); 5 external calls (__init__, __init__, gather, JSONResponse, web_extension).


##### `workspace_usage`  (lines 4268–4343)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the member’s usage and caps, and for admins also the workspace rollup. This is the portal’s financial visibility endpoint.

**Data flow**: It resolves audience, parses the window, reads member spend, formats usage and caps, optionally reads and formats workspace rollup, and returns JSON.

**Call relations**: The usage panel calls it, with admin-only workspace sections included when allowed.

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `connect_handoff`  (lines 4349–4373)

```
async def connect_handoff(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a provider consent URL for a connect request left by a turn. The URL is minted at click time so it is fresh.

**Data flow**: It authorizes the member’s turn, asks core for a connect URL, redirects to it, or returns a friendly callback page if the request is no longer valid.

**Call relations**: Connect buttons in transcripts point to this route.

*Call graph*: calls 2 internal fn (connect_url, _member_turn); 2 external calls (callback_page, RedirectResponse).


##### `_member_turn`  (lines 4376–4429)

```
async def _member_turn(ctx: SurfaceContext, request: Request, *, named_turn: UUID | None=None, allow_commentable: bool=False) -> tuple[UUID, UUID, str] | Response
```

**Purpose**: Authorizes access to a turn as the current member. Mutations require the member’s own turn, while streams may also allow commentable conversations.

**Data flow**: It resolves audience, parses or accepts a turn id, reads turn detail and owner, checks agent visibility and possible conversation access, and returns member id, turn id, and email or refusal.

**Call relations**: _stop_chat, connect_handoff, and stream use it before turn-level actions.

*Call graph*: calls 5 internal fn (turn_detail, turn_owner, _audience_for, _commentable, _member_chat); called by 3 (_stop_chat, connect_handoff, stream); 3 external calls (Response, web_extension, UUID).


##### `stream`  (lines 4432–4440)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the server-sent event stream for a live turn. The browser uses it to receive incremental reply frames.

**Data flow**: It authorizes the turn, reads Last-Event-ID for resume, wraps _events in a StreamingResponse with text/event-stream media type, and returns it.

**Call relations**: The chat UI opens this route after admission or reload.

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 4443–4445)

```
def _event(name: str, payload: dict[str, object], cursor: str='') -> bytes
```

**Purpose**: Formats a custom server-sent event with JSON data and optional cursor id. It is used for extra portal events not directly emitted by core frames.

**Data flow**: It receives an event name, payload dictionary, and optional cursor, JSON-encodes the payload, and returns SSE bytes.

**Call relations**: _events uses it for files, subagents, connect controls, credential prompts, and app cards.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 4448–4464)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest, member_id: UUID) -> dict[str, object] | None
```

**Purpose**: Finds credential prompts from a terminal request that are still waiting for this member. It renews the short-lived sealed request before returning it.

**Data flow**: It receives a credential request and member id, renews the seal, checks each prompt slot for pending status, and returns prompt data or None.

**Call relations**: _events and _open_handoffs use it to show credential entry forms.

*Call graph*: calls 2 internal fn (credential_prompt_pending, renew_credential_request); called by 2 (_events, _open_handoffs).


##### `_file_payload`  (lines 4467–4480)

```
def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]
```

**Purpose**: Formats one shared artifact for chat display, including download and preview links. It is used for files produced by the agent, not member inbox attachments.

**Data flow**: It receives a SharedArtifact, asks context for artifact and preview links, and returns filename, subject, media type, size, URL, and preview URL.

**Call relations**: _events and _transcript_aids use it when attaching files to replies.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 2 (_events, _transcript_aids).


##### `_opens`  (lines 4483–4484)

```
def _opens(audience: WebAudience) -> frozenset[UUID]
```

**Purpose**: Returns the set of agent ids this web audience can open. It is a compact helper for app-card visibility checks.

**Data flow**: It receives a WebAudience, extracts agent ids, and returns them as a frozen set.

**Call relations**: Transcript, readable-conversation, member-chat, and live event paths use it.

*Call graph*: called by 4 (_events, _member_chat_page, _readable_conversation, transcript).


##### `_created_apps`  (lines 4487–4518)

```
async def _created_apps(ctx: SurfaceContext, created: Mapping[str, tuple[ObjectRef, ...]], opens: frozenset[UUID]) -> dict[str, list[dict[str, object]]]
```

**Purpose**: Builds app cards for applications created by turns, but only for apps the viewer may open. Private apps that are not in the viewer’s audience are hidden.

**Data flow**: It receives created object refs and openable agent ids, lists agents, matches created agent names to visible agents, and returns turn-keyed card lists.

**Call relations**: _transcript_aids and _events use it for settled and live created-app displays.

*Call graph*: calls 1 internal fn (list_agents); called by 2 (_events, _transcript_aids).


##### `_events`  (lines 4521–4577)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str, email: str) -> AsyncIterator[bytes]
```

**Purpose**: Converts the core live turn tail into the SSE stream the portal consumes. It adds portal-specific events for files, subagents, connect prompts, credential prompts, and created apps.

**Data flow**: It tails core frames from a cursor, reacts to artifact and terminal frames with extra reads, yields custom events when needed, then yields the core frame through _sse.

**Call relations**: stream wraps this async generator in the HTTP streaming response.

*Call graph*: calls 13 internal fn (connect_available, conversation_subagent_turns, shared_artifacts, tail, turn_detail, _connect_control, _created_apps, _event, _file_payload, _opens (+3 more)); called by 1 (stream); 2 external calls (web_audience, web_extension).


##### `fulfill_credential`  (lines 4580–4610)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores one credential value entered privately by the member. The value is not admitted as chat text and does not enter the transcript.

**Data flow**: It authenticates, bounds and parses the form, validates sealed/slot/value fields and size, asks core to fulfill the sealed request, maps validation failures to responses, and returns stored slot.

**Call relations**: Credential prompt forms submit to this route.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `_object_gate`  (lines 4613–4627)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

**Purpose**: Shared gate for generic object index and detail pages. It authenticates the member, resolves web audience, and verifies the requested object kind exists.

**Data flow**: It resolves audience, reads kind from path, asks context for the kind declaration, and returns member id, audience, and kind or 404.

**Call relations**: object_index and object_detail call it before object reads.

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 4630–4640)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

**Purpose**: Finds the one agent namespace named by an object request. Object reads happen inside an agent’s namespace and must be in the viewer’s audience.

**Data flow**: It parses the agent query parameter, searches audience agents, and returns the matching AgentSummary or a 404 response.

**Call relations**: object_index and object_detail use it for agent-scoped reads.

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_action_payloads`  (lines 4643–4644)

```
def _action_payloads(views: tuple[ActionView, ...]) -> list[dict[str, object]]
```

**Purpose**: Serializes action declarations into JSON-ready dictionaries. It removes null fields to keep payloads compact.

**Data flow**: It receives action views and returns each view’s model dump with None values excluded.

**Call relations**: Action, credential, memory, team, and first-run endpoints use it.

*Call graph*: called by 5 (action_views, workspace_credentials, workspace_first_run, workspace_memory, workspace_team).


##### `_kind_payload`  (lines 4647–4654)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

**Purpose**: Builds common metadata for an object kind page. It tells the UI fields, schema, and whether apply/delete intents are supported.

**Data flow**: It receives a PortalKind, reads list fields and schema, checks ApplyIntent supported kinds, and returns a metadata dictionary.

**Call relations**: object_index and object_detail include this in their responses.

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 4657–4664)

```
def _filter_value(raw: str) -> JsonValue
```

**Purpose**: Parses a query-string filter value into a JSON scalar when possible. This lets filters match booleans or numbers, not only strings.

**Data flow**: It receives raw text, tries json.loads, and returns the parsed value or original string.

**Call relations**: object_index uses it for all non-reserved query parameters.

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_fanout_token`  (lines 4667–4672)

```
def _fanout_token(walking: dict[str, str]) -> str | None
```

**Purpose**: Encodes per-agent continuation cursors for object indexes that fan out across many agents. The result is opaque to the browser.

**Data flow**: It receives a mapping of agent id strings to cursors, returns None if empty, otherwise JSON-serializes and hex-encodes it.

**Call relations**: object_index returns this token when more rows remain in a multi-agent read.

*Call graph*: called by 1 (object_index); 1 external calls (dumps).


##### `_fanout_walks`  (lines 4675–4692)

```
def _fanout_walks(token: str) -> dict[UUID, str] | None
```

**Purpose**: Decodes and validates a fan-out object cursor. It rejects tokens this route did not mint.

**Data flow**: It receives token text, hex-decodes JSON, validates non-empty string cursors keyed by UUID agent ids, and returns a UUID-to-cursor map or None.

**Call relations**: object_index uses it to resume a multi-agent object listing.

*Call graph*: called by 1 (object_index); 2 external calls (loads, UUID).


##### `_merged_rank`  (lines 4695–4708)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```

**Purpose**: Computes a cross-agent sort key for object rows. It keeps rows ordered by the chosen field rather than grouped by agent.

**Data flow**: It receives a row and order field, classifies the field value as missing, boolean, number, or text, and returns a tuple with name as tie-breaker.

**Call relations**: object_index uses it after gathering rows from multiple agents.


##### `object_index`  (lines 4711–4803)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists objects of one kind visible to the member, either in one agent namespace or across all reachable agents. It supports search, filters, ordering, and cursors.

**Data flow**: It gates the object kind, parses order/cursor/filters, chooses one or many agents, calls the kind’s list reader per agent, merges and sorts rows for fan-out, and returns objects plus next cursor.

**Call relations**: The generic portal object index route calls it for objects/{kind}.

*Call graph*: calls 7 internal fn (list_member_objects, _fanout_token, _fanout_walks, _filter_value, _kind_payload, _object_agent, _object_gate); 4 external calls (__init__, replace, JSONResponse, Response).


##### `object_detail`  (lines 4806–4853)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one object’s detail as visible to the member. It includes spec when allowed, fields, links, generation, and timestamps.

**Data flow**: It gates object kind and agent, reads the named object, checks linked objects for openability, formats detail and links, and returns JSON or 404.

**Call relations**: The generic portal object detail route calls it for objects/{kind}/{name}.

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 4856–4888)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one core live frame as server-sent event bytes. The optional cursor becomes the SSE id so browser reconnects can resume.

**Data flow**: It receives a cursor and LiveFrame, pattern-matches the frame type, serializes the right event name and JSON data, and returns bytes.

**Call relations**: _events calls it for every core frame after adding portal-specific extras.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 4891–4896)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives prepared portal intents for a selected agent and dispatches them through the panels layer. Intents are structured actions the UI has prepared.

**Data flow**: It gates the agent, then passes context, request, agent id, member id, and email to submit_intent.

**Call relations**: The route table exposes it at agents/{agent_id}/intents.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `actions`  (lines 4899–4917)

```
async def actions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Dispatches a presented object action for a selected agent. The path names the kind, action, and optional object name; the action code validates its own input.

**Data flow**: It gates the agent, reads path parameters, and passes the bound target information to submit_action.

**Call relations**: Portal buttons for declared actions submit to this route.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_action).


##### `action_views`  (lines 4920–4934)

```
async def action_views(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the actions declared for an object kind or one object instance. This lets panels draw controls without reading the object content first.

**Data flow**: It authenticates, verifies the object kind exists, decides collection versus instance binding, serializes declared actions, and returns JSON.

**Call relations**: The UI calls it to discover available controls.

*Call graph*: calls 4 internal fn (object_actions, object_kind, _action_payloads, _audience_for); 2 external calls (JSONResponse, Response).


##### `_write_agent`  (lines 4941–4959)

```
def _write_agent(request: Request, audience: WebAudience, stated: object=None) -> AgentSummary | Response
```

**Purpose**: Chooses the agent namespace for a direct object write. It accepts a body agent, query agent, or falls back to the workspace main agent.

**Data flow**: It reads a stated or query agent id, validates it against the audience, or finds the main agent, returning an AgentSummary or error.

**Call relations**: object_write uses it before applying or deleting objects.

*Call graph*: called by 1 (object_write); 2 external calls (Response, UUID).


##### `_direct_result`  (lines 4962–4966)

```
def _direct_result(frame: TerminalFrame, name: str) -> Response
```

**Purpose**: Turns a write turn’s terminal frame into a direct write response. Successful turns return ok true; failures return readable detail.

**Data flow**: It receives a terminal frame and object name, checks status, and returns a JSON result with ok, name, and detail.

**Call relations**: object_write calls it when the prepared-intent turn reaches terminal.

*Call graph*: called by 1 (object_write); 1 external calls (JSONResponse).


##### `object_write`  (lines 4969–5043)

```
async def object_write(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets framed apps create, update, or delete objects through a prepared-intent turn under the member’s session. It waits synchronously for the turn result when possible.

**Data flow**: It authenticates, validates kind and body/path, builds an object_apply or object_delete ToolIntent, admits it into an intent conversation, tails the turn until terminal/parked/timeout, and returns a direct result.

**Call relations**: The generic POST object routes use it for bridge writes from app frames.

*Call graph*: calls 7 internal fn (admit, conversation_for, object_kind, tail, _audience_for, _direct_result, _write_agent); 7 external calls (__init__, timeout, dumps, conversation_audience, JSONResponse, json, Response).


##### `object_changes`  (lines 5049–5077)

```
async def object_changes(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a recent audit journal of object changes for admins only. It shows that changes happened without exposing private object specs.

**Data flow**: It resolves audience, rejects non-admins as not found, reads recent changes, formats kind/name/verb/caller/agent/time, and returns JSON.

**Call relations**: The workspace object-changes route calls it.

*Call graph*: calls 2 internal fn (recent_object_changes, _audience_for); 2 external calls (JSONResponse, Response).


##### `settings`  (lines 5080–5092)

```
async def settings(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns configuration data for one selected agent, including whether the viewer may archive it. It delegates detailed settings formatting to the panels module.

**Data flow**: It gates the agent, finds the agent summary, computes archivable from main/owner/admin state, and calls agent_settings.

**Call relations**: The agent settings panel calls this route.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_settings).


##### `agent_setup`  (lines 5095–5125)

```
async def agent_setup(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns setup requirements for one app agent: connectors, credentials, standing orders, and whether the workspace has built its own page. Provider labels and summaries are added for display.

**Data flow**: It gates the agent, reads setup state, enriches connector rows with labels and summaries, checks for a bound page, and returns JSON.

**Call relations**: The setup screen and boot logic use it for app readiness.

*Call graph*: calls 5 internal fn (agent_setup, _bound_page, _panel_gate, _provider_label, _provider_summary); 1 external calls (JSONResponse).


##### `_bound_page`  (lines 5128–5155)

```
async def _bound_page(ctx: SurfaceContext, summary: AgentSummary, member_id: UUID) -> ObjectRow | None
```

**Purpose**: Finds the hosted site row bound as an agent’s homepage, if the workspace built one. It uses an admin read so it can answer binding state separately from member visibility.

**Data flow**: It lists site objects filtered by homepage_agent, searches for a row with site_url, and returns that row or None.

**Call relations**: agents_index, agent_setup, and homepage use it when deciding page/setup state.

*Call graph*: calls 1 internal fn (list_member_objects); called by 3 (agent_setup, agents_index, homepage); 1 external calls (__init__).


##### `homepage`  (lines 5158–5177)

```
async def homepage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent’s homepage state and URL when available. It covers both workspace-built pages and shipped app bundle pages.

**Data flow**: It gates the agent, publishes app assets, reads any bound page, computes homepage state with _homepage_state, and returns JSON.

**Call relations**: The portal polls or reads this when showing an agent homepage.

*Call graph*: calls 5 internal fn (_assets_published, _bound_page, _homepage_state, _panel_gate, apps); 1 external calls (JSONResponse).


##### `_homepage_state`  (lines 5180–5227)

```
def _homepage_state(ctx: SurfaceContext, summary: AgentSummary, bound: ObjectRow | None, admin: bool, member_id: UUID) -> dict[str, JsonValue]
```

**Purpose**: Computes the JSON state for an agent homepage. It returns a hosted bound page, a shipped app bundle URL, or none, while respecting private-agent visibility for bound pages.

**Data flow**: It receives agent summary, optional bound row, admin/member context, and returns state set with URL and generation or state none.

**Call relations**: agents_index and homepage call it after _bound_page or for shipped app pages.

*Call graph*: calls 1 internal fn (apps); called by 2 (agents_index, homepage); 3 external calls (shipped_app_slug, homepage_embed_url, shipped_homepage_url).


##### `_preview_start_page`  (lines 5249–5254)

```
def _preview_start_page(form: FormData) -> int
```

**Purpose**: Parses the first page number requested for document preview. Bad input falls back to page 1.

**Data flow**: It reads start_page from form data, converts it to an integer, clamps to at least 1, and returns it.

**Call relations**: preview uses it before calling the preview renderer.

*Call graph*: called by 1 (preview); 1 external calls (get).


##### `_preview_pages`  (lines 5257–5262)

```
def _preview_pages(form: FormData) -> int
```

**Purpose**: Parses how many preview pages the browser wants and clamps it to the server maximum. Bad input asks for the default batch.

**Data flow**: It reads pages from form data, converts it to an integer, clamps between 1 and the batch limit, and returns it.

**Call relations**: preview uses it before rendering uploaded document previews.

*Call graph*: called by 1 (preview); 1 external calls (get).


##### `preview`  (lines 5265–5311)

```
async def preview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders a temporary preview of an uploaded document or media file for the composer. It stores nothing and admits no turn.

**Data flow**: It authenticates, requires multipart form data, bounds and parses the form, picks the first file, checks supported type, reads bytes, calls the preview renderer, base64-encodes pages, and returns JSON.

**Call relations**: The composer calls it before the member sends an attachment.

*Call graph*: calls 6 internal fn (render_preview, _audience_for, _form, _framed_length, _preview_pages, _preview_start_page); 4 external calls (b64encode, PurePosixPath, JSONResponse, Response).


##### `upload_start`  (lines 5314–5350)

```
async def upload_start(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Mints a presigned upload URL so the browser can PUT attachment bytes directly to blob storage. The later chat send carries only the resulting key.

**Data flow**: It authenticates, reads bounded JSON, validates size, checksum, and name, creates a safe upload key, asks the blob store for a signed PUT URL, and returns key plus URL.

**Call relations**: The composer calls it before direct-to-blob attachment uploads; _parse_inbound later accepts the returned key.

*Call graph*: calls 2 internal fn (_audience_for, _bounded_body); 5 external calls (loads, JSONResponse, Response, inbox_name, uuid4).


### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The portal does not have separate “edit this object” web endpoints. Instead, every write from a panel is admitted as a special conversation turn, like putting a signed request into a single orderly inbox. That matters because conversation turns already provide ordering, permissions, audit history, and tool dispatch. Without this file, panel actions could bypass that shared path or race each other in confusing ways.

The file defines what a panel is allowed to submit: an ApplyIntent says the verb, object kind, name, optional spec, and safety flags. Validators keep impossible combinations out, such as editing a connection instead of connecting or disconnecting it. The file also defines first-run provider tiles and “unlock” suggestions, which tell the start screen what useful automations become possible after connecting certain services.

For incoming web requests, submit_intent reads and checks a prepared panel intent, fills in missing agent settings when a form only changed part of an agent, converts the request into a ToolIntent, admits it to the member’s portal conversation, and waits for the terminal result. submit_action does the same for actions the portal explicitly presents, while preventing the browser body from changing the route-bound target. The result readers turn tool outcomes into simple JSON such as “Saved,” an OAuth link, a billing portal URL, or a refusal message.

Finally, agent_settings returns the data needed to draw the agent settings page, including current values, available models, schema-driven editable fields, and admin-only audience information.

#### Function details

##### `ApplyIntent.kinds`  (lines 95–99)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the full set of object kinds that panel forms are allowed to submit. This keeps the user interface and the server gate tied to the same fixed list.

**Data flow**: It reads the declared Literal type for ApplyIntent.kind → extracts the allowed string values → returns them as a frozen set that callers cannot accidentally change.

**Call relations**: This is the base list used by other ApplyIntent helpers. Those helpers are used by the web surface when deciding which controls to draw for each kind.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 102–104)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the kinds that can be created or updated with an apply action. It removes kinds that are only deleted or only connected.

**Data flow**: It starts with all allowed kinds from ApplyIntent.kinds → subtracts delete-only and connect-only categories → returns the remaining kinds as the set that can accept an apply.

**Call relations**: The web surface’s kind payload code calls this when deciding whether an object page should show create/update controls.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 107–113)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the kinds that can be deleted from the panel lane. In this contract, every named kind may be submitted with delete, though each kind still enforces its own permissions later.

**Data flow**: It reads the same allowed kind list as ApplyIntent.kinds → returns that list unchanged as a frozen set.

**Call relations**: The web surface’s kind payload code calls this when deciding whether to show delete or disconnect controls.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 116–137)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: Checks that the requested verb makes sense for the object kind. It prevents unsafe or meaningless requests, such as applying a credential value through a public panel or attaching something other than a connector grant.

**Data flow**: It receives a fully parsed ApplyIntent → inspects its verb, kind, spec, and create_only flag → either returns the same intent if the combination is allowed or raises a validation error before any turn is created.

**Call relations**: Pydantic, the validation library, runs this automatically when a submitted panel intent is parsed. Later code can trust that the intent’s shape obeys the portal contract.


##### `Unlock._names_offered_tiles_and_a_drawn_mark`  (lines 326–335)

```
def _names_offered_tiles_and_a_drawn_mark(self) -> 'Unlock'
```

**Purpose**: Checks that an unlock suggestion can actually be drawn on the start screen. It verifies that the icon exists and that every required provider is one of the provider tiles offered to the user.

**Data flow**: It reads the Unlock’s icon and required provider groups → compares the icon with known agent icons and provider names with the first-run catalog → returns the same Unlock or raises an error during setup.

**Call relations**: This validator runs when Unlock objects are created at import time. It protects the start screen from broken rows with missing icons or unrecognized provider names.


##### `Unlock.missing`  (lines 337–341)

```
def missing(self, held: frozenset[str]) -> tuple[str, ...]
```

**Purpose**: Tells the start screen which accounts a member still needs before an unlock can run. It chooses one preferred provider from each unmet group.

**Data flow**: It receives the set of provider names the member already holds → checks each required group to see whether any provider in that group is connected → returns the first provider name from each group that is still unmet, in catalog order.

**Call relations**: The start-screen logic can call this to split ideas into “ready now” and “connect these first” suggestions.


##### `_action_intent`  (lines 514–525)

```
def _action_intent(kind: str, name: str | None, action: str, body: dict[str, JsonValue]) -> ToolIntent
```

**Purpose**: Builds the ToolIntent for a portal-presented object action. It puts the route-owned target information together with the action’s own input body.

**Data flow**: It receives the object kind, optional object name, action name, and request body → creates an object_action payload with kind, action, optional name, and input → returns a ToolIntent ready to admit as a conversation turn.

**Call relations**: submit_action calls this after it has checked that the action exists and the body did not try to override the target.

*Call graph*: called by 1 (submit_action); 1 external calls (__init__).


##### `_tool_intent`  (lines 528–563)

```
def _tool_intent(submitted: ApplyIntent) -> ToolIntent
```

**Purpose**: Converts a validated ApplyIntent into the exact tool call the engine should run. It is the translation step between a panel form and the system’s tool-dispatch format.

**Data flow**: It receives an ApplyIntent → chooses connect_account, object_delete, or object_apply depending on the verb → for apply, writes the object data into a YAML manifest → returns a ToolIntent containing the selected tool and input.

**Call relations**: submit_intent calls this after request validation. The returned ToolIntent is admitted to the member’s portal conversation so the normal engine can run it.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 566–582)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Turns a terminal tool result into the simple JSON shape the web panel expects. It reports success, credential prompts, or a human-readable refusal message.

**Data flow**: It receives the final frame from a turn and the turn id → checks whether the status is done → returns JSON with applied true on success, credential details if requested, or applied false with a cleaned-up message on failure.

**Call relations**: This is the default result reader used by intent handling and by specialized readers when their action did not finish successfully.

*Call graph*: called by 6 (_action_outcome, _imessage_outcome, _intent_result, _portal_outcome, _rebuild_outcome, _slack_outcome); 1 external calls (JSONResponse).


##### `_slack_outcome`  (lines 585–600)

```
def _slack_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result from the Slack install action. Slack may return an install URL or a hint explaining why no URL was created.

**Data flow**: It receives the final frame and turn id → if the turn failed, delegates to _outcome → otherwise finds the JSON object embedded in the tool’s text, reads authorize_url and hint → returns JSON with the URL, message, success flag, and turn id.

**Call relations**: _action_outcome reaches this through the ACTION_OUTCOMES mapping for the Slack connect action.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_imessage_outcome`  (lines 603–624)

```
def _imessage_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the special result from the iMessage connection action. It reports whether the connection is pending or connected, plus the user instruction and optional opt-in link.

**Data flow**: It receives the final frame and turn id → if the turn failed, delegates to _outcome → otherwise parses the JSON object in the tool text → validates the expected state, instruction, and optional link → returns JSON for the panel.

**Call relations**: _action_outcome reaches this through the ACTION_OUTCOMES mapping for the iMessage connect action.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_portal_outcome`  (lines 627–641)

```
def _portal_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the billing portal URL produced by the billing-management action. It is used when the user asks to open the provider’s billing portal.

**Data flow**: It receives the final frame and turn id → if the turn failed, delegates to _outcome → otherwise parses the stated JSON object → extracts portal_url → returns JSON with the URL and turn id.

**Call relations**: _action_outcome calls this only for the workspace billing action when the posted operation is the portal operation.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (_action_outcome); 2 external calls (loads, JSONResponse).


##### `_rebuild_outcome`  (lines 644–651)

```
def _rebuild_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Returns the tool’s own message after a rebuild action. Rebuild actions usually queue background work, so the useful answer is the sentence saying what was queued.

**Data flow**: It receives the final frame and turn id → if the turn failed, delegates to _outcome → otherwise returns JSON with applied true, the frame text as the message, and the turn id.

**Call relations**: _action_outcome reaches this through the ACTION_OUTCOMES mapping for report and page rebuild actions.

*Call graph*: calls 1 internal fn (_outcome); 1 external calls (JSONResponse).


##### `_action_outcome`  (lines 665–672)

```
def _action_outcome(kind: str, action: str, body: dict[str, JsonValue], frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Chooses the right way to translate a completed action turn into a web response. Most actions use the normal outcome, but a few have special data such as URLs or queue messages.

**Data flow**: It receives the action kind, action name, posted body, final frame, and turn id → checks for the billing portal case → otherwise looks up a specialized reader or falls back to _outcome → returns the chosen JSON response.

**Call relations**: submit_action calls this when the admitted action turn reaches its terminal frame.

*Call graph*: calls 2 internal fn (_outcome, _portal_outcome); called by 1 (submit_action).


##### `_complete_agent_spec`  (lines 703–730)

```
async def _complete_agent_spec(ctx: SurfaceContext, submitted: ApplyIntent, submitted_fields: frozenset[str], agent_id: UUID, member_id: UUID) -> ApplyIntent | Response
```

**Purpose**: Fills in required agent settings that a partial settings form did not submit. This lets a panel update only one part of an agent without resending every required field.

**Data flow**: It receives the context, submitted intent, names of submitted fields, agent id, and member id → if the intent is not a partial agent apply, returns it unchanged → otherwise reads the current agent details → merges required current values under the submitted changes → returns the completed intent or a “No such app” response.

**Call relations**: _prepare_panel_intent calls this before turning a panel submission into a tool intent, so later validation and object application see a complete agent spec.

*Call graph*: calls 1 internal fn (agent_detail); called by 1 (_prepare_panel_intent); 2 external calls (model_copy, JSONResponse).


##### `_intent_refusal`  (lines 733–750)

```
async def _intent_refusal(ctx: SurfaceContext, submitted: ApplyIntent, submitted_fields: frozenset[str]) -> Response | None
```

**Purpose**: Performs quick refusals before a panel intent becomes a stored conversation turn. It catches problems that would otherwise create a bad or stuck request, such as an unknown model id.

**Data flow**: It receives the context, completed ApplyIntent, and submitted field names → checks agent model and sandbox-size rules, and verifies credential slot names when deleting credentials → returns a JSON refusal response if something is invalid, or None if the intent may proceed.

**Call relations**: _prepare_panel_intent calls this after completing agent specs and before submit_intent admits the turn.

*Call graph*: calls 1 internal fn (list_credential_slots); called by 1 (_prepare_panel_intent); 1 external calls (JSONResponse).


##### `_prepare_panel_intent`  (lines 753–782)

```
async def _prepare_panel_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID) -> ApplyIntent | Response
```

**Purpose**: Reads, parses, and validates a prepared panel intent from an HTTP request. It is the front-door safety check for normal panel form submissions.

**Data flow**: It reads the raw request body → rejects bodies over the byte limit → parses JSON into PanelIntent and ApplyIntent → checks framed-page access for connect actions → completes partial agent specs → applies early refusal checks → returns either a safe ApplyIntent or an HTTP response explaining the problem.

**Call relations**: submit_intent calls this first. It hands back a prepared intent for conversion, or stops the request before anything is written.

*Call graph*: calls 3 internal fn (frame_admits, _complete_agent_spec, _intent_refusal); called by 1 (submit_intent); 3 external calls (loads, JSONResponse, body).


##### `_oversized_manifest`  (lines 785–799)

```
def _oversized_manifest(intent: ToolIntent) -> Response | None
```

**Purpose**: Checks that the generated object_apply manifest is still within the size limit. A small JSON request can become a larger YAML manifest, so this second check protects the lane after conversion.

**Data flow**: It receives a ToolIntent → ignores non-object_apply tools → reads the manifest string → measures its encoded byte length → returns an error response if too large, otherwise None.

**Call relations**: submit_intent calls this after _tool_intent builds the final tool call and before admitting it to a conversation.

*Call graph*: called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `_intent_result`  (lines 802–829)

```
async def _intent_result(ctx: SurfaceContext, turn_id: UUID) -> Response
```

**Purpose**: Waits for a submitted intent turn to finish and converts its final state into a web response. It lets a form submit answer synchronously when possible.

**Data flow**: It receives the context and turn id → tails the turn’s stream of frames with a timeout → returns a normal outcome on terminal completion, a parked message if the turn pauses, or a timeout response if it takes too long.

**Call relations**: submit_intent calls this after admitting the ToolIntent. It uses _outcome for completed turns.

*Call graph*: calls 2 internal fn (tail, _outcome); called by 1 (submit_intent); 2 external calls (timeout, JSONResponse).


##### `submit_intent`  (lines 832–868)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: Accepts one prepared panel mutation, records it as a conversation turn, and returns the result to the browser. This is the main endpoint for settings forms and object panel changes.

**Data flow**: It receives the surface context, request, agent id, member id, and email → prepares and validates the submitted intent → converts it to a ToolIntent → rejects an oversized manifest → opens or reuses the member’s portal action conversation → retitles that conversation → admits the tool call as a turn → waits for and returns the final result.

**Call relations**: This is called by the web routing layer for panel intent submissions. It coordinates _prepare_panel_intent, _tool_intent, _oversized_manifest, conversation setup, admission, and _intent_result.

*Call graph*: calls 7 internal fn (admit, conversation_for, retitle_conversation, _intent_result, _oversized_manifest, _prepare_panel_intent, _tool_intent); 1 external calls (conversation_audience).


##### `submit_action`  (lines 871–960)

```
async def submit_action(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str, *, kind: str, name: str | None, action: str) -> Response
```

**Purpose**: Accepts one action that the portal already presented for an object or collection. It makes sure the route, not the browser body, decides what the action targets.

**Data flow**: It reads the request body → rejects oversized, malformed, non-object, or target-overriding bodies → checks that the requested action is actually available for the route target → checks framed-page permission when needed → builds an object_action ToolIntent → admits it to the portal conversation → tails the turn until completion, parking, or timeout → returns the action-specific web response.

**Call relations**: This is called by the web routing layer for presented portal actions. It uses _action_intent to build the tool call and _action_outcome to read the final answer.

*Call graph*: calls 8 internal fn (admit, conversation_for, frame_admits, object_actions, retitle_conversation, tail, _action_intent, _action_outcome); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `_update_schema`  (lines 963–975)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the schema used to render editable agent settings fields. It removes fields that the page draws with custom controls or that this deployment does not support.

**Data flow**: It receives the available sandbox sizes → gets the AgentSpec JSON schema → removes prompt, icon, purpose, input/output schemas, and sandbox_size when sandbox sizes are unavailable → returns the filtered schema.

**Call relations**: agent_settings calls this when assembling the settings projection sent to the browser.

*Call graph*: called by 1 (agent_settings); 1 external calls (model_json_schema).


##### `agent_settings`  (lines 978–1022)

```
async def agent_settings(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, *, admin: bool, archivable: bool) -> Response
```

**Purpose**: Returns the data needed to draw the agent settings page. It includes current agent details, deployment limits, available models, editable spec values, the matching schema, and admin-only audience grants.

**Data flow**: It receives the context, agent id, member id, and flags for admin and archivable status → reads agent details → returns 404 if the agent is missing → for admins, reads web audience grants → builds a JSON response with agent metadata, deployment capability, model list, current spec, schema, and optional audience.

**Call relations**: The web settings route calls this to populate the page. It uses _update_schema for the form description and the surface context for live agent information.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


### `extensions/web/ufo_ext_web/starters.py`

`domain_logic` · `start screen request handling`

The start screen needs a few good suggestions: short rows the member can press to ask the assistant to build something useful. This file creates those rows only when someone actually opens the screen, like a café making coffee when a customer orders instead of brewing pots all day. It uses the member's remembered work, the applications already in the workspace, and the product's catalog of possible applications. A language model ranks the best catalog items and writes the title, display sentence, and ask sentence for each row.

The file also protects the rest of the system from unnecessary cost and bad user experience. A generated “slate” is cached for 30 minutes, so repeated reads do not keep paying for the same model work. If the cached slate is old, the old slate is still returned first, because an outdated suggestion is better than an empty screen while fresh suggestions are being made. A short “claim” lock makes sure two browser tabs do not generate the same slate at once. If generation fails, a cooldown stamp prevents the next screen refresh from immediately hitting the same failure again.

The model's reply is treated carefully. Invalid rows, unknown catalog entries, and duplicates are quietly dropped. A totally missing tool reply is considered a real failure, because that means the model did not follow the contract at all.

#### Function details

##### `Slate.fresh`  (lines 112–113)

```
def fresh(self, now: datetime) -> bool
```

**Purpose**: This checks whether a saved start-screen slate is still safe to reuse. It matters because the system should avoid re-ranking too often, but should refresh when the slate is too old or was made under outdated instructions.

**Data flow**: It takes the current time as input and compares it with the slate's saved creation time. It also compares the saved prompt digest, which is a fingerprint of the ranking instructions, with the current digest. It returns true only if the slate is younger than the allowed cache time and was made with the current instructions.

**Call relations**: The cache-reading flow uses this after loading a stored slate. If it says the slate is fresh, the main read path can return it immediately instead of asking the model to rank new rows.


##### `starters_key`  (lines 124–125)

```
def starters_key(member_id: UUID) -> str
```

**Purpose**: This builds the storage key where one member's cached starter slate is saved. It gives every member their own slot so one person's suggestions cannot overwrite another's.

**Data flow**: It receives a member ID, turns that ID into the project's standard member subject string, and prefixes it with the starter-cache label. The result is a plain text key used to read or write the slate in the scoped store.

**Call relations**: When the cache wants to look up an existing slate, it asks this function for the key. After a new slate is generated, the main read flow uses the same key to store it back in the cache.

*Call graph*: called by 2 (_held, read); 1 external calls (member_subject).


##### `claim_key`  (lines 128–129)

```
def claim_key(member_id: UUID) -> str
```

**Purpose**: This builds the storage key for the temporary claim that says, “someone is already generating this member's slate.” It prevents duplicate model calls when the same screen is opened or refreshed in multiple places.

**Data flow**: It receives a member ID, converts it to the standard member subject string, and prefixes it with the claim label. The output is the store key used for the short-lived generation claim.

**Call relations**: The claim-making helper uses this key while trying to reserve the right to generate. The main read flow also uses it to delete the claim after the generation attempt finishes, whether it succeeded or failed.

*Call graph*: called by 2 (_claim, read); 1 external calls (member_subject).


##### `cooldown_key`  (lines 132–133)

```
def cooldown_key(member_id: UUID) -> str
```

**Purpose**: This builds the storage key for the failure cooldown stamp for a member. The cooldown keeps the start screen from repeatedly trying an expensive or broken generation path right after an error.

**Data flow**: It receives a member ID, turns it into the standard member subject string, and prefixes it with the cooldown label. The result is the store key where the last failure time is saved and later checked.

**Call relations**: Before generating, the permission check uses this key to see whether a recent failure should block another attempt. If generation fails, the main read flow writes a new failure stamp under this key.

*Call graph*: called by 2 (_may_generate, read); 1 external calls (member_subject).


##### `_stamped`  (lines 136–145)

```
def _stamped(held: object, key: str) -> datetime | None
```

**Purpose**: This safely reads a timestamp from a stored dictionary-like value. It is deliberately forgiving: if the stored value is missing, old, malformed, or half-written, it treats it as absent instead of crashing.

**Data flow**: It receives an unknown stored value and the name of the timestamp field to read. If the value is a dictionary and the chosen field is a string, it tries to parse that string as an ISO-format date and time. It returns the parsed time, or returns nothing if the value cannot be trusted.

**Call relations**: The cooldown check uses this to read the last failure time. The claim logic uses it to read when an existing generation claim was made, so it can decide whether the claim is still active or stale.

*Call graph*: called by 2 (_claim, _may_generate); 1 external calls (fromisoformat).


##### `StarterCache.read`  (lines 168–185)

```
async def read(self) -> Slate | None
```

**Purpose**: This is the main entry point for getting the start-screen slate for one member. It returns a usable cached slate when possible, starts a refresh when needed, and makes sure failures degrade gracefully instead of freezing the screen.

**Data flow**: It starts by noting the current time and loading any stored slate. If the slate is fresh, it returns it. If the slate is stale or missing, it checks whether this read is allowed to generate a new one. If not, it returns whatever was already stored. If generation is allowed, it asks the model to rank a new slate, saves it, and returns it. If anything goes wrong during generation, it records a cooldown, logs a warning, removes the generation claim, and returns the old slate if one exists.

**Call relations**: This method ties the whole cache flow together. It calls the stored-slate reader first, asks the generation gate whether work should happen, calls the ranking method only when allowed, and uses the key helpers to write cooldowns, delete claims, and save the finished slate.

*Call graph*: calls 6 internal fn (_held, _may_generate, _rank, claim_key, cooldown_key, starters_key); 2 external calls (now, warn).


##### `StarterCache._held`  (lines 187–194)

```
async def _held(self) -> Slate | None
```

**Purpose**: This loads the currently saved slate for the member, if there is one and it still has the expected shape. It protects callers from broken or outdated stored data.

**Data flow**: It builds the member's starter-cache key, reads the stored value, and checks that it is a dictionary-like object. It then validates that data as a Slate. If validation succeeds, it returns the Slate object; if the data is missing or invalid, it returns nothing.

**Call relations**: The main read method calls this at the start of every request. Its answer decides whether the read path can return a fresh slate immediately, return an old slate while skipping generation, or fall back to generating a new slate.

*Call graph*: calls 1 internal fn (starters_key); called by 1 (read).


##### `StarterCache._may_generate`  (lines 196–206)

```
async def _may_generate(self, now: datetime) -> bool
```

**Purpose**: This decides whether the current read is allowed to spend effort generating a new slate. It prevents generation when it would be useless, unaffordable, recently failed, or already underway somewhere else.

**Data flow**: It receives the current time and looks at the cache object's state: whether a model is available, whether there is recalled memory to rank, and whether the workspace is allowed to spend. It then reads the failure cooldown stamp and checks whether it is still recent. If all those checks pass, it tries to claim the right to generate and returns whether that claim succeeded.

**Call relations**: The main read method calls this before doing any model work. This helper hands off to the claim helper at the end, because even if generation is otherwise allowed, only one reader should actually perform it.

*Call graph*: calls 3 internal fn (_claim, _stamped, cooldown_key); called by 1 (read).


##### `StarterCache._claim`  (lines 208–225)

```
async def _claim(self, now: datetime) -> bool
```

**Purpose**: This tries to reserve slate generation for the current reader. It is a small lock, meaning a guard that stops two tabs or refreshes from paying for the same model ranking at the same time.

**Data flow**: It builds the member's claim key and prepares a claim record with the current time. First it tries to write that record only if no claim exists. If a claim already exists, it reads the existing claim time. A recent claim blocks this reader. An old claim is treated as abandoned, and this reader tries to replace exactly that stale value. The result is true if this reader now owns the claim, otherwise false.

**Call relations**: The generation-permission check calls this as its final step. It uses the claim-key helper to find the right store row and the timestamp reader to decide whether an existing claim is live or expired.

*Call graph*: calls 2 internal fn (_stamped, claim_key); called by 1 (_may_generate); 1 external calls (isoformat).


##### `StarterCache._rank`  (lines 227–254)

```
async def _rank(self, now: datetime) -> Slate
```

**Purpose**: This asks the language model to rank and write the member's starter rows. It turns remembered work and the app catalog into a structured model request, then converts the model's tool reply into a Slate.

**Data flow**: It gathers three inputs: recalled memory snippets, already-existing applications, and the catalog of buildable unlocks. It sends them to the configured surface model with detailed instructions and a required tool schema, which is a machine-readable shape the model must fill in. When the model replies, it passes that reply and the generation time to the slate-settling function. The output is a validated Slate ready to cache.

**Call relations**: The main read flow calls this only after cache and claim checks say generation should happen. This function performs the model turn and then hands the raw reply to settle_slate, which filters and packages the final result.

*Call graph*: calls 1 internal fn (settle_slate); called by 1 (read); 4 external calls (__init__, __init__, __init__, dumps).


##### `settle_slate`  (lines 257–290)

```
def settle_slate(reply: Message, generated_at: datetime) -> Slate
```

**Purpose**: This turns the model's structured reply into a clean Slate. It keeps good rows, drops bad ones, and raises an error if the model did not make the required recording call at all.

**Data flow**: It receives a model reply and the time the slate was generated. It searches the reply content for the expected record_slate tool use. From that tool input, it reads the ranked entries one by one. Each entry must fit the required text limits, name a known catalog unlock, and not repeat an unlock already kept. Invalid entries are skipped. It also tries to validate an optional check-in row. It returns a Slate containing the generation time, current prompt fingerprint, accepted ranked rows, and optional check-in.

**Call relations**: The ranking method calls this after the model responds. Its cleaned Slate goes back to the main read flow, which stores it under the member's starter-cache key and returns it to the start screen.

*Call graph*: called by 1 (_rank); 1 external calls (__init__).


### Operator web tools
The operator-facing routes provide read-only debugger and memory views backed by shared operator sign-in and workspace selection.

### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the bridge between the debugger web page and the system data it shows. An operator uses this surface to inspect what is happening across the fleet or inside one workspace without changing anything. Without it, the built debugger UI would have no routes to load, no way to list conversations, and no way to watch a live turn as it runs.

The file first loads the built React app from static/index.html. The main page route returns that HTML, while the api/ routes return plain JSON. Most routes follow the same pattern: read an ID from the URL, ask SurfaceContext for the workspace-scoped data, and return either the data or a clear 404 JSON error when the item does not exist. SurfaceContext is important because it carries the already-authorized workspace scope, so these reads stay tied to the workspace chosen by the operator session.

The file also exposes a server-sent events stream, often called SSE: a simple browser-friendly way for the server to keep sending events over one HTTP response. Here it tails live frames from a turn and converts each frame into a named SSE event, like text, reply, cost, or terminal. Think of it like a live ticker tape for one running conversation turn.

At the bottom, ROUTES maps URL paths to these handlers, making this file the debugger surface’s route table.

#### Function details

##### `app_page`  (lines 57–62)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the debugger web app HTML to the browser. It is used when an operator opens the debugger page itself, before the page starts calling the JSON API.

**Data flow**: It receives the current surface context and HTTP request. It checks whether the built frontend HTML was found when the module loaded; if not, it raises an error telling the developer to build the frontend. If the HTML exists, it wraps that text in an HTML response and sends it back.

**Call relations**: This is the handler for the GET route at the surface root. It does not fetch workspace data itself; it only hands the browser the app shell, which then calls the other API routes in this file.

*Call graph*: 1 external calls (HTMLResponse).


##### `fleet`  (lines 65–69)

```
async def fleet(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a fleet-wide index of workspaces and recent threads for the debugger landing page. This gives an authorized operator a broad overview before drilling into one workspace.

**Data flow**: It receives the request context, creates a FleetDirectory reader, reads the fleet directory, converts the result into JSON-friendly data, and returns it as a JSON response.

**Call relations**: This is called by the GET api/fleet route. It stands apart from the workspace-specific detail routes because it intentionally reads across the fleet after the operator-domain authorization has already happened outside this function.

*Call graph*: 2 external calls (__init__, JSONResponse).


##### `workspace_meta`  (lines 72–85)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns small bits of metadata about the current workspace, such as its workspace ID, Slack team ID if known, and Datadog site setting. The UI can use this to label the workspace and link to outside tools.

**Data flow**: It reads the Slack installation value from the SurfaceContext, removes the team: prefix when present, reads the DD_SITE environment variable, and returns those values with the current workspace ID as JSON.

**Call relations**: This is the handler for GET api/workspace. It relies on SurfaceContext for the already-selected workspace and asks that context for installation information before returning a compact metadata object to the frontend.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 88–90)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the conversations visible in the current workspace. This lets the debugger UI show the operator which sessions or threads can be inspected.

**Data flow**: It asks SurfaceContext for the workspace’s conversation list. Each returned entry is converted into JSON-friendly form, and the list is sent back as a JSON response.

**Call relations**: This is called by the GET api/conversations route. It is usually one of the first data calls made by the debugger UI after loading the workspace page.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 93–98)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns inside one conversation. A turn is one interaction cycle or unit of work within a conversation, so this endpoint lets the UI show the conversation’s timeline.

**Data flow**: It reads conversation_id from the URL and uses _uuid_param to make sure it is a valid UUID. If the ID is invalid, it returns a 404 error. Otherwise, it asks SurfaceContext for that conversation’s turns, converts them to JSON-friendly data, and returns the list.

**Call relations**: This is the handler for GET api/conversations/{conversation_id}/turns. It depends on _uuid_param for safe ID parsing and on SurfaceContext.list_turns for the actual workspace-scoped read.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 101–108)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full transcript for one conversation when it exists. This gives the debugger a readable record of what was said or exchanged in that conversation.

**Data flow**: It reads and validates conversation_id from the URL. If the ID is invalid, it returns a 404 error. It then asks SurfaceContext for the transcript; if none is found, it returns a 404 error. When found, it converts the transcript to JSON-friendly data and returns it.

**Call relations**: This is called by the GET api/conversations/{conversation_id}/transcript route. It uses _uuid_param before handing the valid ID to SurfaceContext.read_transcript.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 111–115)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is where earlier conversation history has been summarized or condensed, so this helps operators understand what context was kept or reduced.

**Data flow**: It reads conversation_id from the URL and validates it with _uuid_param. If the ID is invalid, it returns a 404 error. Otherwise, it asks SurfaceContext for the compaction indexes or records and returns them as a JSON list.

**Call relations**: This is the handler for GET api/conversations/{conversation_id}/compactions. It prepares the ID and then delegates the workspace-scoped lookup to SurfaceContext.list_compactions.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 118–133)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the detailed before-and-after record for one compaction. This lets an operator inspect exactly what messages were condensed and what summary replaced them.

**Data flow**: It reads conversation_id and an index from the URL. The conversation ID must be a valid UUID and the index must be numeric; otherwise it returns a 404 error. It asks SurfaceContext for that compaction record. If found, it returns the index, the messages before compaction, the messages after compaction, and the summary as JSON.

**Call relations**: This is called by GET api/conversations/{conversation_id}/compactions/{index}. It uses _uuid_param for the conversation ID, checks the index itself, and then asks SurfaceContext.read_compaction for the detailed record.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 136–141)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a conversation’s workspace. This helps the debugger show artifacts or workspace files that were available during that conversation.

**Data flow**: It reads conversation_id from the URL and validates it. If the ID is invalid, it returns a 404 error. Otherwise, it asks SurfaceContext for the workspace files connected to that conversation, converts each file entry into JSON-friendly data, and returns the list.

**Call relations**: This is the handler for GET api/conversations/{conversation_id}/files. It relies on _uuid_param for safe ID parsing and SurfaceContext.list_workspace_files for the actual file listing.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 144–154)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file to the browser. This lets an operator download or inspect a file connected to a conversation without loading it all into memory first.

**Data flow**: It reads and validates conversation_id from the URL, then takes the requested file path from the route. It asks SurfaceContext to open that file. If the ID is invalid, the path is rejected, or no file is found, it returns a 404 JSON error. If a stream is found, it returns a streaming binary response.

**Call relations**: This is called by GET api/conversations/{conversation_id}/files/{path:path}. It uses _uuid_param first, then hands the conversation ID and requested path to SurfaceContext.read_workspace_file, and finally wraps the returned stream for HTTP delivery.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 157–164)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn. This gives the debugger UI the main record for a specific unit of work in a conversation.

**Data flow**: It reads turn_id from the URL and validates it as a UUID. If invalid, it returns a 404 error. It asks SurfaceContext for the turn detail; if none is found, it returns a 404 error. Otherwise, it converts the detail to JSON-friendly data and returns it.

**Call relations**: This is the handler for GET api/turns/{turn_id}. It uses _uuid_param to protect the lookup from malformed IDs before calling SurfaceContext.turn_detail.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `turn_steps`  (lines 167–174)

```
async def turn_steps(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the step-by-step activity inside one turn. This lets an operator see how a turn progressed, rather than only seeing its final result.

**Data flow**: It reads turn_id from the URL and validates it. If the ID is invalid, it returns a 404 error. It asks SurfaceContext for the turn’s steps; if no turn is found, it returns a 404 error. Otherwise, it converts each step into JSON-friendly data and returns the list.

**Call relations**: This is called by GET api/turns/{turn_id}/steps. It follows the same safe lookup pattern as turn, then delegates the detailed step read to SurfaceContext.turn_steps.

*Call graph*: calls 2 internal fn (turn_steps, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 177–182)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger UI watch new activity arrive in real time, such as text updates, replies, terminal output, or cost ticks.

**Data flow**: It reads turn_id from the URL, validates it, and confirms the turn exists. If the ID is invalid or the turn is missing, it returns a 404 error. It reads the Last-Event-ID header, which tells the server where a previously dropped stream left off, then returns a streaming response that will yield server-sent events from _events.

**Call relations**: This is the handler for GET api/turns/{turn_id}/stream. It checks existence with SurfaceContext.turn_detail, then hands the live streaming work to _events and wraps that async stream in a text/event-stream HTTP response.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 185–188)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the live tail of a turn into a stream of browser-ready event bytes. It is the small adapter between the system’s internal live frames and the debugger’s SSE connection.

**Data flow**: It receives a SurfaceContext, a turn ID, and a cursor string showing where to resume. It opens SurfaceContext.tail for that turn, then for each incoming cursor and frame it calls _sse. It yields the resulting bytes one event at a time to the HTTP streaming response.

**Call relations**: stream calls this after it has checked that the requested turn exists. _events then calls SurfaceContext.tail to receive live frames and _sse to format each frame for the browser.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 191–219)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as a server-sent event. It gives each kind of live update a clear event name, such as text, reply, activity, or terminal.

**Data flow**: It receives a cursor and one LiveFrame object. If the cursor is not empty, it writes it as the SSE event ID so the browser can resume later. It then checks what kind of frame it received, serializes the frame to JSON, chooses the matching event name, and returns the complete SSE byte block. If it sees an unknown frame type, it raises an error because the debugger does not know how to label it.

**Call relations**: _events calls this for every live frame it receives from SurfaceContext.tail. The formatted bytes flow back through _events to the StreamingResponse created by stream.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 222–226)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID value from a route parameter. It keeps the route handlers from treating malformed URL IDs as real database or workspace IDs.

**Data flow**: It receives a request and the name of a path parameter. It tries to convert that parameter’s text into a UUID object. If the text is valid, it returns the UUID; if not, it returns None so the caller can send a 404 error.

**Call relations**: Many route handlers call this before looking up conversations or turns, including conversation_turns, conversation_transcript, conversation_compactions, compaction_record, workspace_files, workspace_file, turn, turn_steps, and stream. It is the shared ID-parsing helper for this file’s URL-based lookups.

*Call graph*: called by 9 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, turn_steps, workspace_file, workspace_files); 1 external calls (UUID).


### `core/src/ufo/runtime/ext/operator.py`

`domain_logic` · `request handling for operator surfaces and fleet directory reads`

Operator tools need stronger, shared access rules because they can look across many workspaces. This file is the common doorway. It checks a bearer token, which is a signed credential proving who the user is, and turns it into two facts: which workspace the token came from and which email address owns it. It accepts that token from the Authorization header, from a secure browser cookie, or from the one form post that opens an operator session. It deliberately never accepts the token from the URL, because URLs often end up in logs and browser history.

Once a token is verified, the file enforces the operator-domain rule: only users whose email domain matches the configured operator domain may use these surfaces. If allowed, the request can stay in its claimed workspace or use `?ws=` to look at another workspace by UUID or by workspace domain.

The file also defines `FleetDirectory`, which is like an operator’s front desk map. It first asks the owner-level database view which workspaces and conversations have recent activity. Then, for each workspace, it re-enters that workspace’s normal security boundary before reading human-facing details such as domain, title, surface, and queue key. This keeps broad cross-workspace discovery separate from ordinary workspace-scoped reads.

#### Function details

##### `operator_claims`  (lines 45–63)

```
async def operator_claims(request: Request) -> tuple[str, str] | None
```

**Purpose**: Finds and verifies the operator credential carried by an incoming web request. It checks safe places for the token in priority order: the Authorization header, then the operator session cookie, then the form body for the one POST that starts a session.

**Data flow**: It receives a request. It reads the Authorization header, cookies, and sometimes the posted form field named `token`. Each possible token is trimmed and sent for verification. If one works, it returns the workspace claim and email address from the token; if none work, it returns nothing.

**Call relations**: This is the first authentication step used by `resolve_operator_workspace`. It relies on `_candidate_claims` to do the actual token check, and it reads the form body only when the request is a POST so a fresh login can replace a stale cookie.

*Call graph*: calls 1 internal fn (_candidate_claims); called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `_candidate_claims`  (lines 66–68)

```
def _candidate_claims(candidate: str) -> tuple[str, str] | None
```

**Purpose**: Checks one possible token and turns it into trusted claims if it is valid. It is a small helper that keeps blank strings from being sent to the verifier.

**Data flow**: It receives a candidate token string. It removes surrounding spaces. If the result is empty, it returns nothing; otherwise it passes the token to `verified_claims`, which checks the signature and returns the workspace and email if the token is valid.

**Call relations**: This helper is called by `operator_claims` for each place a credential may appear. It hands the real security decision to `verified_claims`, so this module does not keep or directly use the signing secret.

*Call graph*: called by 1 (operator_claims); 1 external calls (verified_claims).


##### `resolve_operator_workspace`  (lines 71–111)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Decides which workspace an operator request is allowed to use. It also redirects unauthenticated browser visits to the shared login page instead of leaving the user at a dead end.

**Data flow**: It receives the request and the surface authentication context. It asks `operator_claims` for verified token claims. If there are no claims and this is a normal page GET, it returns a redirect to the operator login flow; otherwise it rejects by returning nothing. If claims exist, it checks that the email belongs to the operator domain. Then it resolves the target workspace: either the workspace inside the token, a UUID from `?ws=`, a workspace found by domain, or a predictable UUID made from that domain.

**Call relations**: This function is the main gate used by operator-only surfaces when a request arrives. It calls `operator_claims` to authenticate, uses `email_domain` to enforce the operator-domain rule, uses `workspace_by_domain` inside `owner_tx` when a domain needs lookup, and returns either a workspace UUID, a redirect response, or rejection.

*Call graph*: calls 1 internal fn (operator_claims); 6 external calls (owner_tx, email_domain, workspace_by_domain, RedirectResponse, UUID, uuid5).


##### `bind_operator_session`  (lines 114–132)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts an operator browser session after a valid login form post. It stores the posted bearer token in an HTTP-only cookie and sends the browser back to the operator page.

**Data flow**: It receives the surface context and request. It reads the posted form field named `token`. If the field is missing or blank, it returns a JSON error with status 400. If present, it creates a redirect response and adds the operator cookie to it, using the surface’s secure-cookie setting.

**Call relations**: This is used after the identifying step has already verified the same form token. It calls `Request.form` to read the body, returns `JSONResponse` for bad input, uses `RedirectResponse` for success, and delegates the cookie-writing details to `set_session_cookie`.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


##### `FleetWorkspace._aware_utc`  (lines 150–151)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Makes sure a workspace activity time includes timezone information. This prevents later code from confusing a plain timestamp with one tied to a real clock zone.

**Data flow**: It receives the `last_turn_at` value while a `FleetWorkspace` model is being built. If the value is missing or already has timezone information, it leaves it alone. If it has no timezone, it marks it as UTC and returns the adjusted value.

**Call relations**: This is a Pydantic field validator, so it runs automatically during `FleetWorkspace` creation. It is used indirectly when `FleetDirectory._scoped` builds workspace entries for the fleet listing.

*Call graph*: 1 external calls (replace).


##### `FleetThread._aware_utc`  (lines 169–170)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Makes sure a recent conversation’s last-activity time is timezone-aware. In plain terms, it labels an unlabeled timestamp as UTC so the UI and API can compare times safely.

**Data flow**: It receives the `last_turn_at` value while a `FleetThread` model is being built. If the timestamp already has timezone information, it is returned unchanged. If not, the function returns a copy marked as UTC.

**Call relations**: This validator runs automatically when `FleetThread` objects are created. `FleetDirectory.read` creates those objects after combining owner-level activity data with workspace-scoped conversation details.

*Call graph*: 1 external calls (replace).


##### `FleetDirectory.read`  (lines 202–233)

```
async def read(self) -> FleetListing
```

**Purpose**: Builds the full operator fleet listing: all workspaces plus the most recently active conversations across them. This is what lets an operator browse the deployment without already knowing each workspace’s domain or conversation ID.

**Data flow**: It starts by calling `_enumerate` to get workspace IDs and recent conversation IDs from the broad owner view. It groups the wanted conversation IDs by workspace. Then, one workspace at a time, it enters that workspace context with `ws(...)` and calls `_scoped` to fetch readable details. Finally it combines those pieces into a `FleetListing` containing `FleetWorkspace` entries and `FleetThread` entries.

**Call relations**: This is the public reading method of `FleetDirectory`. It orchestrates the two-pass design: `_enumerate` finds what exists and what is recent, while `_scoped` fetches details under each workspace’s normal safety boundary. It then constructs the returned `FleetListing` and `FleetThread` objects.

*Call graph*: calls 2 internal fn (_enumerate, _scoped); 3 external calls (__init__, __init__, ws).


##### `FleetDirectory._enumerate`  (lines 235–273)

```
async def _enumerate(self) -> tuple[Sequence[sa.Row[Any]], Sequence[sa.Row[Any]]]
```

**Purpose**: Performs the broad owner-level database pass for the fleet directory. It finds every workspace and the recently active root conversations, but only reads identifiers and ordering timestamps.

**Data flow**: It builds two database queries. One lists workspaces, ordered by their latest member-facing activity while still including workspaces with no activity. The other finds the most recently active conversations, counts their root turns, and limits the result to the directory’s thread limit. It runs both queries inside `owner_tx` and returns the rows.

**Call relations**: `FleetDirectory.read` calls this first. The function uses SQLAlchemy to build the queries and `owner_tx` to run them in the owner-level database context. It intentionally does not fetch display text such as titles or domains; that work is left to `_scoped`.

*Call graph*: called by 1 (read); 2 external calls (select, owner_tx).


##### `FleetDirectory._scoped`  (lines 275–323)

```
async def _scoped(self, workspace_id: UUID, last_turn_at: datetime | None, conversation_ids: Sequence[UUID]) -> tuple[FleetWorkspace, dict[UUID, sa.Row[Any]]]
```

**Purpose**: Reads the human-facing details for one workspace in the fleet directory. It does this inside that workspace’s normal database boundary, so the listing uses the same scoped access rules as ordinary workspace pages.

**Data flow**: It receives a workspace ID, that workspace’s latest activity time, and the conversation IDs that should be opened for display. Inside a workspace transaction, it reads the workspace domain, member count, non-subagent conversation count, and selected conversation details such as surface, queue key, and title. It returns a `FleetWorkspace` summary plus a dictionary of opened conversation rows keyed by conversation ID.

**Call relations**: `FleetDirectory.read` calls this once for each workspace found by `_enumerate`, after entering the workspace context with `ws(...)`. It uses `workspace_tx` for the scoped transaction, `workspace_domain` for the display domain, and SQLAlchemy queries for counts and conversation details.

*Call graph*: called by 1 (read); 4 external calls (__init__, select, workspace_tx, workspace_domain).


### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the doorway for a read-only “memory explorer” screen. Its job is to show an operator what the Memory extension has stored for a selected workspace, much like opening a filing cabinet and seeing every card inside. Without this file, the extension might still store memories, but operators would not have this simple web surface for inspecting what recall is based on.

The file does two main things. First, it loads a static HTML page from `static/memory.html` and serves it when the operator opens the surface. That page is self-contained, so the backend only needs to return the HTML text.

Second, it provides an API endpoint, `api/memories`, that returns all memory items for the workspace as JSON, newest first. JSON is a common plain-text data format used by web pages to fetch structured data. The code creates an `ExtensionContext`, which is the Memory extension’s way to open a workspace-scoped database transaction. “Workspace-scoped” means the read is limited to the workspace chosen by the authorized operator session, so one workspace’s memory is not mixed with another’s.

At the bottom, `ROUTES` connects web paths to the functions in this file: one route serves the page, one binds the operator session, and one returns the memory list.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the Memory explorer web page to the browser. It is used when an operator opens the surface’s main page.

**Data flow**: It receives the surface context and the HTTP request, but it does not need to read details from either one. It checks whether the HTML file was successfully loaded when the module started. If the HTML is available, it wraps that text in an HTML response and sends it back; if the file is missing, it raises an error so the missing page is noticed clearly.

**Call relations**: This function is attached to the GET route for the surface’s empty path. When a browser asks for the Memory explorer page, the route calls `app_page`, and `app_page` hands back an `HTMLResponse` containing the already-loaded page.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the workspace’s memory records as JSON for the explorer page to display. It is read-only, so it lets an operator inspect memory without editing it.

**Data flow**: It receives the surface context, which includes the selected workspace ID, and the HTTP request. It builds an extension context for the Memory extension, using a scoped store so database reads happen under the current workspace boundary. It then asks `ufo_ext_memory.store.inventory` for the memory items in that workspace, converts each item into JSON-friendly data, and returns the list in a JSON response.

**Call relations**: This function is attached to the GET route `api/memories`. After the operator-facing page loads, that page can call this API to fetch the data it needs. Inside the function, the work is handed off to `inventory`, which performs the actual memory-store lookup, and then `memories` packages the result for the browser with `JSONResponse`.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).


### Shared surface runtime
The shared runtime code admits external surface events into authenticated UFO context and streams live turn output reliably to mounted clients.

### `core/src/ufo/runtime/surfaces/hub_tail.py`

`orchestration` · `request handling`

A “turn” is a unit of work, such as an agent taking its next step in a conversation. While that turn is running, the system publishes live frames through a hub, which is like a small broadcast room for updates. But a listener may arrive late, reconnect after a dropped connection, or be running in a different event loop from the code that finished the turn. If this file only listened to the hub, it could miss the final “done” signal and leave the caller waiting forever.

To avoid that, this file races two sources into one stream. One source subscribes to the live hub and forwards frames as they arrive. The other source periodically reads the durable database record for the turn. That database poll looks for either a terminal frame, meaning the turn is finished, or a parked state, meaning the turn is paused and waiting for something like a seat, balance, or spending cap. Whichever source sees the ending first closes the stream.

The file also handles reconnects by asking whether the hub still has messages after the caller’s last cursor. If it does, it resumes from there; otherwise it safely redraws from what the hub still retains. The HubTailer class wraps this behavior so surface code can ask for a turn’s stream without knowing about the hub or the polling details.

#### Function details

##### `tail_frames`  (lines 35–67)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='', billing_url: str | None=None) -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: Streams all visible frames for one turn until the turn is finished or parked. It is used when a client or surface wants to watch a running turn, including after reconnecting or joining late.

**Data flow**: It receives a hub, a turn id, an optional cursor from the last frame the caller saw, and an optional billing URL. It checks whether the hub can resume from that cursor, starts one background task to read live hub frames, immediately checks the database for an already-finished or already-parked turn, and if needed starts another background task that keeps polling the database. It yields pairs of cursor and frame to the caller, then stops when it sees a terminal or parked frame. When the caller is done or the stream ends, it cancels the background tasks so they do not keep running.

**Call relations**: HubTailer.tail hands this generator to surface code. Inside, it asks Hub.covers whether a reconnect cursor is still usable, starts _pump to collect live hub updates, uses _read_status_frame to catch any durable final state, and starts _poll_status so a final or parked state committed elsewhere still reaches the stream.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, _read_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 70–79)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: Copies live frames from the hub subscription into the shared queue used by the tail stream. It filters out internal arrival-notice frames that are not meant to be sent to the surface.

**Data flow**: It receives the hub, the turn id, a starting cursor, and the queue shared with tail_frames. It subscribes to the hub from that cursor, reads each published frame, skips ArrivalQueued notices, and places all other frames into the queue with their cursor. If the subscription fails, it logs the error instead of crashing the whole tail.

**Call relations**: tail_frames starts this as a background task when a caller begins tailing a turn. It depends on Hub.subscribe for live messages and feeds the same queue that tail_frames reads from before yielding frames to the outside caller.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 82–94)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]], billing_url: str | None) -> None
```

**Purpose**: Keeps checking the database for the turn’s durable ending state. This is the safety net that prevents a stream from staying open forever if the live hub missed or never saw the final update.

**Data flow**: It receives the turn id, the shared frame queue, and the optional billing URL. Every fixed interval, it asks _read_status_frame whether the turn is now terminal or parked. If the read fails temporarily, it logs the problem and tries again later. Once a final or parked frame appears, it places that frame into the queue with an empty cursor and then stops.

**Call relations**: tail_frames starts this after an initial database check shows the turn is not already ended. It repeatedly calls _read_status_frame, and its result is consumed by tail_frames through the shared queue.

*Call graph*: calls 1 internal fn (_read_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `_read_status_frame`  (lines 97–115)

```
async def _read_status_frame(turn_id: UUID, billing_url: str | None) -> LiveFrame | None
```

**Purpose**: Reads the turn’s current durable status while carefully dealing with cancellation. It protects the database read long enough to finish cleanly, then returns the result or preserves the caller’s cancellation request.

**Data flow**: It receives a turn id and optional billing URL. It starts turn_status_frame as a separate asynchronous task and waits for it behind a shield, which means cancellation of the waiter does not immediately cancel the underlying read. If cancellation arrives, it remembers that fact, lets the read finish, then either returns the frame or re-raises the cancellation at the correct time. The output is a live frame if the turn is terminal or parked, or None if it is still active.

**Call relations**: tail_frames uses this for the first immediate durable check, and _poll_status uses it for repeated checks. It delegates the actual database and gate decisions to turn_status_frame.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 2 (_poll_status, tail_frames); 2 external calls (ensure_future, shield).


##### `turn_status_frame`  (lines 118–175)

```
async def turn_status_frame(turn_id: UUID, billing_url: str | None=None) -> LiveFrame | None
```

**Purpose**: Looks in the database and decides whether the turn’s stream should end now. It returns a Terminal frame for a completed turn, a Parked frame for a paused turn, or None if the turn is still queued or running.

**Data flow**: It receives a turn id and optional billing URL. It opens a workspace database transaction, loads the turn’s status, stored terminal data, workspace, agent, conversation, and member information. If the turn has a stored terminal frame, it validates that stored data and returns it as a Terminal frame. If the turn is not parked, it returns None. If the turn is parked, it checks the current reasons that could keep it paused: whether the user still has a seat, whether the workspace has enough balance, and whether spending caps allow more work. It returns a Parked frame with the most useful current message, or a generic pause message if no specific blocker is found.

**Call relations**: _read_status_frame is the only function here that calls it. It reaches out to the database through workspace_tx and SQL queries, uses turn_authority and Seats to check admission, uses read_headroom and balance_refusal_message for balance holds, and uses SpendEvaluator for spending-cap decisions.

*Call graph*: called by 1 (_read_status_frame); 11 external calls (__init__, __init__, __init__, __init__, model_validate, select, workspace_tx, turn_authority, applicable_caps_absent, balance_refusal_message (+1 more)).


##### `HubTailer.tail`  (lines 188–191)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Gives callers a scoped stream of frames for one turn. It hides the details of hub subscription, polling, cleanup, and billing message wiring behind a small method.

**Data flow**: It receives a turn id and optional last-seen cursor. It calls tail_frames with this HubTailer’s hub and billing URL, then wraps the async generator in a closing context so cleanup runs when the caller leaves the block. The result is an async iterator that yields cursor-and-frame pairs.

**Call relations**: Surface code calls this method when it wants to follow a turn. It hands the real work to tail_frames and uses aclosing so the background pump and poll are stopped when the caller is finished.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


##### `HubTailer.latest_activity`  (lines 193–194)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Returns the hub’s most recent known activity for a turn, if there is one. This lets a surface quickly ask what the live hub last saw without starting a full tail stream.

**Data flow**: It receives a turn id and asks the stored hub for the latest activity associated with that turn. It returns that activity object, or None if the hub has no activity to report. It does not change the database or the stream.

**Call relations**: This is a small pass-through method on HubTailer. It fits beside HubTailer.tail as part of the same surface-facing wrapper around the process hub.


### `core/src/ufo/runtime/ext/surface.py`

`orchestration` · `cross-cutting: request handling, live streaming, and background delivery`

A surface is the place where a human meets the agent: Slack, the browser portal, iMessage, a debugger, or another extension. This file defines the privileged doorway those surfaces use to reach core. That doorway matters because ordinary extensions are deliberately limited: they cannot claim who a member is or put a member’s words onto the durable turn queue. Without this file, outside messages could not safely become agent turns, and finished agent replies would not reliably get back to the place where the member spoke.

The file does three big jobs. First, it defines plain data shapes for things a surface shows: conversations, turns, files, connector accounts, credentials, agents, sources, and spend reports. Second, it defines `SurfaceContext`, the main toolbox handed to a surface route. Through it a surface can link an external user to a workspace member, find or create a conversation, admit a message, read transcripts and shared files, stream live frames, and ask for portal-style views. Third, it runs background delivery workers. Durable surfaces do not keep a browser connection open, so `WritebackPoller` and `MidTurnReplyPoller` claim database rows, call the surface’s send functions, renew leases while waiting, and retry safely if a provider fails.

An everyday analogy: this file is both the reception desk and the mailroom. The reception desk checks who arrived and which room they belong in. The mailroom makes sure replies and attachments are delivered, even if the first courier attempt fails.

#### Function details

##### `is_silence_sentinel`  (lines 238–246)

```
def is_silence_sentinel(answer: str) -> bool
```

**Purpose**: Checks whether a final agent answer is intentionally empty. Surfaces use this to avoid posting a fake-looking blank message.

**Data flow**: It receives answer text, trims surrounding whitespace, and compares the whole result to the accepted empty-response markers. It returns true only when the entire answer means silence.

**Call relations**: It supports durable reply delivery decisions; when a surface or poller sees a silent terminal answer, it can mark delivery complete without sending visible text.


##### `mint_marker`  (lines 249–259)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to wrap one member message safely. The marker makes the wrapper unique so member text cannot accidentally close or fake it.

**Data flow**: It takes no input, asks the secrets library for random bytes as hexadecimal text, and returns that marker string.

**Call relations**: It is normally used before `fence_member_message`, so each inbound message gets its own private boundary.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 262–275)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the text that becomes an inbound member message, with separate sections for context, the member’s words, and attachments. This helps the agent tell what was said from what was surrounding context.

**Data flow**: It receives a marker, ambient context text, message body, and attachment text. It wraps the body and attachments in marker-named tags and returns one combined inbound string.

**Call relations**: Surface ingesters prepare messages with this before admitting them; later readers can use `member_message_text` to recover only the member’s words.


##### `inbox_name`  (lines 278–307)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an untrusted attachment filename into a safe workspace filename. It prevents path tricks and avoids overwriting another attachment in the same batch.

**Data flow**: It receives a raw name and a set of names already used. It keeps only a safe leaf filename, replaces unsafe characters, preserves useful extensions where possible, adds a number if needed, updates the used set, and returns the safe name.

**Call relations**: Attachment download code in surfaces relies on this single shared rule so Slack, web uploads, and future surfaces do not each invent weaker filename cleanup.

*Call graph*: 1 external calls (contained_leaf).


##### `member_message_text`  (lines 310–324)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts what the member actually typed from a stored inbound prompt. This keeps UI displays from showing hidden context wrappers as if the member said them.

**Data flow**: It receives inbound text, removes engine-added context at the start and injected context at the end, then looks for the surface message wrapper. It returns the wrapped member body if found, otherwise the cleaned inbound text.

**Call relations**: It is called by `conversation_name` when opening messages are turned into conversation titles.

*Call graph*: called by 1 (conversation_name).


##### `MemberAdmitter.admit`  (lines 378–389)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Defines the contract for admitting a member message as a turn. Implementations use it when a trusted surface says, “this member spoke these words.”

**Data flow**: It receives conversation identity, message text, optional idempotency and context, speaker identity, and optional intent/config. It returns an `Admitted` result describing the turn created or joined.

**Call relations**: `SurfaceContext.admit` delegates to this protocol, keeping the concrete queue implementation outside this seam.


##### `TurnTailer.tail`  (lines 403–405)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how a live surface subscribes to a turn’s live frames. A frame is a piece of progress or output published while the turn runs.

**Data flow**: It receives a turn id and optional cursor, opens an async scope, and yields cursor/frame pairs until the turn ends.

**Call relations**: `SurfaceContext.tail` exposes this to web, debugger, and sample live surfaces without letting them touch the hub directly.


##### `TurnTailer.latest_activity`  (lines 407–407)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Defines how to peek at the newest retained activity for a running turn without subscribing. It is useful for status badges.

**Data flow**: It receives a turn id, reads the hub’s latest known activity, and returns it or none.

**Call relations**: `SurfaceContext.latest_activity` passes this through to portal status endpoints.


##### `TurnStopper.stop`  (lines 417–417)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines how a member-requested stop cancels a running turn. It also reports whether a waiting follow-up message founded a new turn.

**Data flow**: It receives workspace, conversation, and turn ids. It cancels or observes the turn and returns a `Stopped` result.

**Call relations**: `SurfaceContext.stop_turn` delegates to this so surfaces can offer a stop button while core owns cancellation details.


##### `TurnStepSource.read`  (lines 423–423)

```
async def read(self, workflow_id: str) -> tuple['TurnStep', ...]
```

**Purpose**: Defines how to read durable workflow steps for a turn. These steps explain what the runtime did during execution.

**Data flow**: It receives a workflow id, reads recorded steps, and returns them as `TurnStep` values.

**Call relations**: `SurfaceContext.turn_steps` calls this after proving the turn belongs to the workspace.


##### `SurfaceModel.model`  (lines 453–453)

```
def model(self) -> str
```

**Purpose**: Names the model available to a surface route for small helper work. It lets billing and attribution know which model was used.

**Data flow**: It reads the configured model id and returns it as a string.

**Call relations**: Surface-specific route code can inspect this through `SurfaceContext.model` when a deploy has wired one.


##### `SurfaceModel.turn`  (lines 455–455)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one model request for a surface route. It is meant for bounded work while a member waits.

**Data flow**: It receives a model request, sends it to the configured model access layer, and returns the model’s message.

**Call relations**: Routes call this only through the optional surface model exposed by `SurfaceContext.model`.


##### `shared_artifact_link`  (lines 551–562)

```
def shared_artifact_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a file an agent shared. It returns no link when public artifact delivery is not configured.

**Data flow**: It receives signing secret, public base URL, workspace id, and artifact metadata. If configured, it computes an expiry, mints a signed artifact path, and returns a full URL.

**Call relations**: `SurfaceContext.artifact_link` calls this for web, Slack, iMessage, and other surfaces that need links instead of direct uploads.

*Call graph*: called by 1 (artifact_link); 3 external calls (now, artifact_url_expiry, mint_artifact_url).


##### `shared_artifact_preview_link`  (lines 565–587)

```
def shared_artifact_preview_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed inline preview link for an image or rendered document preview. It refuses mismatched or non-image preview data.

**Data flow**: It receives signing data, workspace id, and artifact metadata. It picks either the preview blob or original blob, verifies the media type is a raster image, and returns a signed preview URL or none.

**Call relations**: `SurfaceContext.artifact_preview_link` uses this for portal file cards and conversation slot projections.

*Call graph*: called by 1 (artifact_preview_link); 2 external calls (mint_image_preview_url, raster_image_media_type).


##### `_scheduled_runs_query`  (lines 590–626)

```
def _scheduled_runs_query(workspace_id: UUID, member_id: UUID, agent_id: UUID | None) -> sa.Select[Any]
```

**Purpose**: Builds the database query for scheduled turns a member may read. Scheduled turns are agent runs that fired without a fresh member message.

**Data flow**: It receives workspace, member, and optional agent ids. It constructs a SQL query limited to terminal scheduled turns in readable conversations, optionally narrowed to one agent.

**Call relations**: `scheduled_runs` uses this as its base query before applying feed-specific filters.

*Call graph*: called by 1 (scheduled_runs); 3 external calls (or_, select, readable_audiences).


##### `scheduled_runs`  (lines 629–705)

```
async def scheduled_runs(workspace_id: UUID, member_id: UUID, *, limit: int, agent_id: UUID | None=None, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Returns recent scheduled runs for a member-facing feed. It includes failures and successful runs that actually reported something by sharing a file.

**Data flow**: It receives workspace/member ids and filters such as limit, agent, turn, and subjects. It reads matching turns and their shared artifacts, adds conversation source links, and returns `ScheduledRun` records.

**Call relations**: It calls `_scheduled_runs_query` for the safety fence and `ConversationDirectory.sources` to attach links back to the originating conversation.

*Call graph*: calls 1 internal fn (_scheduled_runs_query); 6 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx).


##### `conversation_name`  (lines 754–759)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Creates a conversation title from the member’s opening words. It ignores ambient channel context so the title reflects the actual request.

**Data flow**: It receives inbound text, extracts the member’s own message, trims it, caps its length, and returns the title.

**Call relations**: Conversation creation and spawned runs use this shared naming rule through `member_message_text`.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 762–779)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames an existing conversation when a surface has a better title. Blank titles are ignored.

**Data flow**: It receives workspace id, conversation id, and title. It trims and caps the title, then updates the matching conversation row if the result is not empty.

**Call relations**: `SurfaceContext.retitle_conversation` delegates here, and web or Slack code calls that context method after opening or renaming chats.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `summarize_conversation_title`  (lines 782–802)

```
async def summarize_conversation_title(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores an automatically summarized title and marks that summarization was attempted. This prevents paying for the same summary repeatedly.

**Data flow**: It receives workspace id, conversation id, and summary text. It trims and caps the title, writes it if nonblank, and sets the summarized flag either way.

**Call relations**: Titling jobs use this after model-based title generation; listing views then read the updated conversation row.

*Call graph*: 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 870–871)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes an agent update timestamp so it has timezone information. This avoids ambiguous time display.

**Data flow**: It receives a datetime, adds UTC if it was timezone-naive, and returns the normalized datetime.

**Call relations**: Pydantic calls this while building `AgentDetail` responses for portal settings.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 919–920)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a connector connection timestamp to timezone-aware UTC.

**Data flow**: It receives a datetime and returns it unchanged if already timezone-aware, otherwise with UTC attached.

**Call relations**: Pydantic runs this when `SurfaceContext.list_agent_connections` builds connection rows.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 946–947)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a workspace connection-pool timestamp to timezone-aware UTC.

**Data flow**: It receives a datetime and returns a UTC-aware version.

**Call relations**: Pydantic runs it for rows returned by `SurfaceContext.list_connections`.

*Call graph*: 1 external calls (replace).


##### `_binding_fields`  (lines 981–1009)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts connector binding identity fields from a stored source configuration. These fields let the portal submit edits that still name the same source.

**Data flow**: It receives backend name and config JSON. It validates the config as a connector source and returns binding fields, or returns null-like fields for non-connector configs.

**Call relations**: `SurfaceContext.list_sources` expands this into each `SourceView` so portal source actions have complete identity data.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 1041–1042)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a source sync time to timezone-aware UTC.

**Data flow**: It receives an optional datetime and returns none, the original aware time, or a UTC-marked time.

**Call relations**: Pydantic applies this when source rows are presented by `SurfaceContext.list_sources`.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 1060–1063)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation creation and activity times to timezone-aware UTC.

**Data flow**: It receives an optional datetime and returns a UTC-aware value when present.

**Call relations**: Pydantic applies this to conversation summaries from directory and debug listing reads.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 1076–1137)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged they are reading another member’s private transcript. The record temporarily opens access and creates an audit trail.

**Data flow**: It receives workspace, conversation, agent, and reader member ids. It verifies the conversation belongs to the agent and is another member’s private audience, inserts an access row, logs the disclosure, and returns the reader/subject emails or none.

**Call relations**: Portal prepared-intent flows call this before serving protected transcript content; `SurfaceContext.readable_conversation` later checks the recent access row.

*Call graph*: 9 external calls (__init__, now, insert, select, workspace_tx, log, audience_member, parse_audience, uuid4).


##### `ConversationDirectory.list`  (lines 1195–1328)

```
async def list(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, portal: bool | None=None, conversation_id: UUID | None=None, participation: Literal['mine',
```

**Purpose**: Lists one agent’s conversations for a portal-style view. It applies audience, admin, surface, search, and participation rules before returning content-bearing fields.

**Data flow**: It receives agent/member ids and filters. It queries conversation metadata, narrows by permissions, fetches source links and speakers only for readable rows, and returns `ListedConversation` records.

**Call relations**: `SurfaceContext.list_agent_conversations` delegates here; helper predicates in the same class build the participation and search filters.

*Call graph*: calls 6 internal fn (_matches, _member_admitted, _others, _participated, sources, speakers); 10 external calls (__init__, __init__, not_, or_, select, workspace_tx, audience_member, conversation_audience, parse_audience, readable_audiences).


##### `ConversationDirectory.sources`  (lines 1330–1367)

```
async def sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the source link or origin string from each listed conversation’s opening turn.

**Data flow**: It receives conversation ids, finds the first turn in each, parses its turn context, and returns a map from conversation id to source string or none.

**Call relations**: `ConversationDirectory.list`, `scheduled_runs`, and artifact listings call this to point users back to where a conversation began.

*Call graph*: called by 1 (list); 4 external calls (model_validate, and_, select, workspace_tx).


##### `ConversationDirectory.speakers`  (lines 1369–1425)

```
async def speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Finds the first few members who spoke in each listed conversation. This gives a compact “who was here” summary.

**Data flow**: It receives conversation ids, finds each speaker’s first turn, parses sender labels from turn context, and returns conversation-to-speakers mapping.

**Call relations**: `ConversationDirectory.list` calls this only for conversations whose content the reader may see.

*Call graph*: called by 1 (list); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `ConversationDirectory._member_admitted`  (lines 1427–1440)

```
def _member_admitted(self) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations that contain at least one real member-admitted turn.

**Data flow**: It takes no direct input beyond the directory workspace and current SQL conversation row. It returns an EXISTS condition over turns with member admission source.

**Call relations**: `ConversationDirectory.list` uses this when callers want human conversations rather than machine-only lanes.

*Call graph*: called by 1 (list); 2 external calls (literal, select).


##### `ConversationDirectory._spoken`  (lines 1442–1463)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for whether a member, or any member, has spoken in a conversation.

**Data flow**: It receives an optional member id. It returns an EXISTS condition over turn speaker ids for the current conversation row.

**Call relations**: `_participated` and `_others` compose this into member rail filters.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `ConversationDirectory._participated`  (lines 1465–1473)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations the given member participated in. Participation includes owning the conversation or speaking in it.

**Data flow**: It receives a member id and returns a SQL OR condition over conversation owner and spoken-turn existence.

**Call relations**: `ConversationDirectory.list` uses this for the “mine” participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 1 external calls (or_).


##### `ConversationDirectory._others`  (lines 1475–1487)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations where someone else spoke and this member did not participate.

**Data flow**: It receives a member id and returns a SQL condition excluding owned or spoken-by-this-member conversations while requiring some member speech.

**Call relations**: `ConversationDirectory.list` uses this for the “others” participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 2 external calls (and_, not_).


##### `ConversationDirectory._matches`  (lines 1489–1518)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a safe search condition for conversation listings. It searches metadata broadly but searches private content only when the member may read it.

**Data flow**: It receives search text and member id. It returns a SQL OR condition over surface labels, owner emails, readable titles, and readable speaker emails.

**Call relations**: `ConversationDirectory.list` applies this before limiting results so matching rows are not lost after pagination.

*Call graph*: called by 1 (list); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `LedgerEntry._aware_utc`  (lines 1532–1533)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a billing ledger timestamp to timezone-aware UTC.

**Data flow**: It receives a datetime and returns it with UTC attached if needed.

**Call relations**: Pydantic applies it when `SurfaceContext.turn_detail` builds ledger entries.

*Call graph*: 1 external calls (replace).


##### `TurnStep._aware_utc`  (lines 1554–1557)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes workflow step start and completion times to timezone-aware UTC.

**Data flow**: It receives an optional datetime and returns none, an already-aware value, or a UTC-marked value.

**Call relations**: Pydantic applies it when turn-step records are returned through `SurfaceContext.turn_steps`.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 1613–1614)

```
def _fulfilled_marker_key(request_id: UUID, slot: str) -> str
```

**Purpose**: Builds the blob-store key used to mark a private credential prompt as fulfilled.

**Data flow**: It receives a request id and credential slot name, formats them into a stable path string, and returns that path.

**Call relations**: `SurfaceContext.credential_prompt_pending` checks this marker, and `SurfaceContext.fulfill_credential_request` writes it.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request).


##### `_credential_request_id`  (lines 1617–1620)

```
def _credential_request_id(state: CredentialRequestState, sealed: str) -> UUID
```

**Purpose**: Finds a stable id for a credential request. Older sealed requests may not carry an explicit id, so this derives one from the seal.

**Data flow**: It receives opened request state and the sealed token. It returns the state’s request id if present, otherwise a UUID made from a SHA-256 digest of the seal.

**Call relations**: Credential prompt renewal, pending checks, and fulfillment all use this to agree on the same request identity.

*Call graph*: called by 3 (credential_prompt_pending, fulfill_credential_request, renew_credential_request); 2 external calls (sha256, UUID).


##### `_main_agent`  (lines 1623–1636)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. It is the fallback when a surface has no explicit agent binding.

**Data flow**: It receives a workspace id, queries the agent table for the main row, and returns its id. It raises an error if the workspace is malformed and has none.

**Call relations**: `_bind_surface_installation` and `SurfaceContext._surface_agent` call this when they need a default agent.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 1639–1678)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str, *, routes_ingress: bool) -> None
```

**Purpose**: Creates or updates the binding between a workspace and an external surface installation. This is how core knows which workspace owns a Slack team or similar provider identity.

**Data flow**: It receives workspace id, surface name, installation id, and whether the installation routes incoming traffic. It upserts the installation row, preserving the bound agent on updates, and raises a conflict if another workspace already owns a routing installation.

**Call relations**: Both surface OAuth callbacks through `SurfaceContext.bind_installation` and manifest-scoped tools through `SurfaceInstallationAccess.bind` use this shared writer.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.fleet_blob`  (lines 1752–1755)

```
def fleet_blob(self) -> FleetBlobStore
```

**Purpose**: Provides access to deploy-wide shared blobs rather than workspace-only blobs.

**Data flow**: It reads the backend from the workspace blob store and returns a `FleetBlobStore` using the same storage backend.

**Call relations**: Surface routes use this when they need static or fleet-level assets, while workspace data remains under `SurfaceContext.blob`.

*Call graph*: 1 external calls (__init__).


##### `SurfaceContext.conversation_slots`  (lines 1758–1760)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Returns the extension-provided conversation slots configured for this deploy. Slots are extra panels or summaries a surface can show beside a conversation.

**Data flow**: It reads the context’s stored slot tuple and returns it unchanged.

**Call relations**: Web surface routes inspect these slots before calling the read or summarize methods.


##### `SurfaceContext.read_conversation_slot`  (lines 1762–1767)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Runs one conversation slot’s read provider under the conversation’s agent identity.

**Data flow**: It receives a bound slot and slot context, temporarily binds the agent id, calls the provider’s read method, and returns the payload.

**Call relations**: The web surface calls this when rendering a specific conversation slot.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1769–1774)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Runs one conversation slot’s summary provider under the conversation’s agent identity.

**Data flow**: It receives a bound slot and context, binds the agent id, calls summarize, and returns a count or none.

**Call relations**: The web surface calls this while building slot overviews.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.runtime`  (lines 1777–1779)

```
def runtime(self) -> RuntimeIdentity | None
```

**Purpose**: Exposes the runtime identity configured by the application root. This may identify service and sandbox runtime details.

**Data flow**: It reads the stored runtime identity and returns it or none.

**Call relations**: Surface routes can display or use this fact without knowing how composition wired it.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1782–1785)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Tells whether the deployed system allows sandbox internet access at all. Agent settings can only narrow this, not exceed it.

**Data flow**: It returns the boolean stored on the context.

**Call relations**: Portal settings use this to decide whether to offer or explain internet access controls.


##### `SurfaceContext.system_skill_bundle`  (lines 1788–1790)

```
def system_skill_bundle(self) -> SystemSkillBundle
```

**Purpose**: Returns the immutable bundle of deploy-provided system skills. A terminal can cache this before running a turn.

**Data flow**: It reads and returns the stored skill bundle.

**Call relations**: Surfaces expose the bundle indirectly where runtime setup or display needs it.


##### `SurfaceContext.models`  (lines 1793–1797)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Returns the model ids this deploy can run. The portal uses this as the closed list for agent model selection.

**Data flow**: It reads the stored tuple of model names and returns it.

**Call relations**: `validate_runtime_config` uses the same list to reject unknown turn model choices.


##### `SurfaceContext.validate_runtime_config`  (lines 1799–1807)

```
def validate_runtime_config(self, runtime_config: TurnRuntimeConfig) -> None
```

**Purpose**: Checks that a proposed turn runtime configuration is allowed on this deploy. It prevents choosing an unknown model or using environment documents where unsupported.

**Data flow**: It receives a runtime config, compares its model to the deploy model list, and checks whether environment-document storage is wired. It returns nothing or raises a value error.

**Call relations**: The UFO extension calls this before admitting turns with user-selected runtime options.

*Call graph*: called by 1 (_runtime_config).


##### `SurfaceContext.store_environment_document`  (lines 1809–1815)

```
async def store_environment_document(self, body: bytes) -> str
```

**Purpose**: Stores an environment document for a turn and returns its digest. Environment documents describe files or settings the runtime should pin.

**Data flow**: It receives document bytes, verifies the deploy allows environment documents, stores the content through the configured writer, and returns a digest string.

**Call relations**: The UFO surface calls this from environment upload routes; `validate_runtime_config` enforces the same deployment gate.

*Call graph*: called by 1 (store_environment).


##### `SurfaceContext.store_environment_file`  (lines 1817–1822)

```
async def store_environment_file(self, body: bytes) -> str
```

**Purpose**: Stores a file referenced by an environment document and returns its digest.

**Data flow**: It receives file bytes, verifies environment-file storage is configured, writes through the configured function, and returns the digest.

**Call relations**: The UFO surface uses this when uploading files that an environment document names.

*Call graph*: called by 1 (store_environment_file).


##### `SurfaceContext.sandbox_sizes`  (lines 1825–1828)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Returns the sandbox sizes this deployment can provision. An empty list means there is no user-facing choice.

**Data flow**: It reads and returns the stored tuple of size names.

**Call relations**: Portal agent settings use this to decide whether to show a sandbox-size selector.


##### `SurfaceContext.credential`  (lines 1830–1833)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential slot for a trusted surface. It never exposes values through ordinary extension context.

**Data flow**: It receives a slot name, requires a credential store, reads the value for this workspace, and returns it.

**Call relations**: Slack surface functions call this for bot tokens, signing secrets, and posting credentials.

*Call graph*: called by 8 (_bot_token, _channel_origin, _ctx_signing_secret, _post_ephemeral, _to_inbound, attach, post, speak).


##### `SurfaceContext.put_member_credential`  (lines 1835–1841)

```
async def put_member_credential(self, member_id: UUID, slot: str, value: str) -> None
```

**Purpose**: Stores a credential value that belongs to one member. This is used after a surface has authenticated that member directly.

**Data flow**: It receives member id, slot, and value, converts the slot to a member-scoped key, and writes the value to the credential store.

**Call relations**: Web OAuth/device flows call this when a member connects their own provider account.

*Call graph*: called by 2 (anthropic_code, openai_device_poll); 1 external calls (member_slot).


##### `SurfaceContext.member_credential_stored`  (lines 1843–1852)

```
async def member_credential_stored(self, member_id: UUID, slot: str) -> bool
```

**Purpose**: Checks whether a member has a value stored for a credential slot without revealing it.

**Data flow**: It receives member id and slot, tries to read the member-scoped credential, and returns true if found or false if absent/no store.

**Call relations**: The web surface uses this to show account connection state.

*Call graph*: called by 1 (workspace_accounts); 1 external calls (member_slot).


##### `SurfaceContext.clear_member_credential`  (lines 1854–1860)

```
async def clear_member_credential(self, member_id: UUID, slot: str) -> None
```

**Purpose**: Deletes one member’s own credential value so they can disconnect or replace it.

**Data flow**: It receives member id and slot, requires a credential store, builds the member-scoped slot, and clears it.

**Call relations**: The web account disconnect route calls this.

*Call graph*: called by 1 (account_disconnect); 1 external calls (member_slot).


##### `SurfaceContext.member_holds_own_model_key`  (lines 1862–1875)

```
async def member_holds_own_model_key(self, member_id: UUID) -> bool
```

**Purpose**: Checks whether a member has any personal model-provider key stored. This helps decide whether features that need their own provider account can run.

**Data flow**: It receives a member id, scans the configured member-routed slots, and returns true on the first stored credential found.

**Call relations**: The web first-run flow calls this so UI and runtime gates agree.

*Call graph*: called by 1 (workspace_first_run); 1 external calls (member_slot).


##### `SurfaceContext.credential_prompt_pending`  (lines 1877–1900)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs a value for one slot.

**Data flow**: It receives a sealed request and slot, opens and validates the seal, checks the fulfillment table and blob marker, and returns whether the prompt is still pending.

**Call relations**: The web surface calls this when deciding which credential prompts to render.

*Call graph*: calls 2 internal fn (_credential_request_id, _fulfilled_marker_key); called by 1 (_pending_prompts); 3 external calls (select, workspace_tx, open_credential_request).


##### `SurfaceContext.renew_credential_request`  (lines 1902–1928)

```
async def renew_credential_request(self, sealed: str, member_id: UUID) -> str | None
```

**Purpose**: Renews a still-valid credential request for an authenticated member during page reloads.

**Data flow**: It receives a sealed token and member id, validates workspace and member claims, checks renewal age and admin status, adds stable request metadata, and returns a new sealed token or none.

**Call relations**: The web pending-prompt flow calls this so private credential pages can survive reloads safely.

*Call graph*: calls 1 internal fn (_credential_request_id); called by 1 (_pending_prompts); 5 external calls (now, workspace_tx, open_credential_request, seal_credential_request, member_is_admin).


##### `SurfaceContext.open_credential_authorization`  (lines 1930–1938)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential authorization token for a workspace-bound surface. It is used when a provider callback must recover which member and slot are being fulfilled.

**Data flow**: It receives a sealed string, requires a credential store, verifies and decrypts it, and returns the request state or raises on invalid tokens.

**Call relations**: Slack OAuth callback handling calls this before completing a credential handoff.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 1940–1989)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Stores the credential value requested by a sealed handoff after checking it belongs to the current workspace, member, and slot.

**Data flow**: It receives seal, slot, value, and member id. It validates the seal and declared slot, rejects wrong members or duplicates, writes the credential, and records a fulfillment marker for private prompts.

**Call relations**: Slack OAuth, UFO secret fulfillment, and web credential forms call this to complete credential collection.

*Call graph*: calls 2 internal fn (_credential_request_id, _fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 5 external calls (__init__, now, dumps, warn, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 1991–1999)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation id to the current workspace. This lets later provider requests resolve to the right workspace.

**Data flow**: It receives an installation id and passes workspace id, surface name, and routing mode to the shared installation writer.

**Call relations**: Slack OAuth callback code calls this after a team installation succeeds.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.address_claim`  (lines 2001–2026)

```
async def address_claim(self, address: str) -> AddressClaim | None
```

**Purpose**: Reads this workspace’s claim on an address used by an addressed surface, such as a phone or sender address.

**Data flow**: It receives an address, queries the surface-address table for this workspace and surface, normalizes expiry time, and returns an `AddressClaim` or none.

**Call relations**: The iMessage surface checks this before deciding whether an inbound message proves an address or becomes a turn.

*Call graph*: called by 1 (_admit_message); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.confirm_address`  (lines 2028–2041)

```
async def confirm_address(self, address: str, proved_by: str) -> None
```

**Purpose**: Marks an address reservation as proved. After this, the address routes to the member without expiring.

**Data flow**: It receives address and proof message id, clears the expiry, stores the proof id, and updates the row timestamp.

**Call relations**: The iMessage proof flow calls this once an inbound message proves ownership.

*Call graph*: called by 1 (_prove); 2 external calls (update, workspace_tx).


##### `SurfaceContext.release_address`  (lines 2043–2052)

```
async def release_address(self, address: str) -> None
```

**Purpose**: Deletes this workspace’s claim on an addressed-surface address. The address can then be claimed again.

**Data flow**: It receives an address and deletes the matching surface-address row for this workspace and surface.

**Call relations**: The iMessage proof flow calls this when a claim should be abandoned.

*Call graph*: called by 1 (_prove); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.public_base_url`  (lines 2055–2058)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deployment’s public base URL, if configured. Surfaces use it to build callback and portal links.

**Data flow**: It reads the stored base URL and returns it or none.

**Call relations**: Surface handlers use this instead of each computing deployment reachability.


##### `SurfaceContext.cookie_secure`  (lines 2061–2065)

```
def cookie_secure(self) -> bool
```

**Purpose**: Tells whether cookies should be marked Secure for this deployment. Secure cookies are sent only over HTTPS.

**Data flow**: It parses the configured public base URL scheme and asks the HTTP helper whether that scheme should use secure cookies.

**Call relations**: Browser surfaces use this when setting sessions.

*Call graph*: 2 external calls (cookie_secure, urlsplit).


##### `SurfaceContext.home_url`  (lines 2067–2077)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link into the browser portal, if this deploy has a public base and a home surface. Non-browser surfaces can use it to send members to the portal.

**Data flow**: It receives an optional URL fragment. If base URL and home surface exist, it returns `/surface/<home>` plus the fragment; otherwise none.

**Call relations**: iMessage, Slack, and sites surfaces call this when they need to direct a member to a richer UI.

*Call graph*: called by 3 (_terminal_text, _into_the_portal, _reply_with_oversize_links).


##### `SurfaceContext.shared_artifacts`  (lines 2079–2117)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Lists the files a turn shared. Live surfaces use this to show download cards; durable surfaces receive the same data through writeback.

**Data flow**: It receives a turn id, queries shared-artifact rows in share order, and returns `SharedArtifact` records.

**Call relations**: Web and UFO surfaces call this when rendering events or shared files.

*Call graph*: called by 2 (shared_files, _events); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 2119–2127)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a shared artifact in this workspace.

**Data flow**: It receives artifact metadata and passes deployment signing data plus workspace id to `shared_artifact_link`.

**Call relations**: Web, Slack, iMessage, and UFO surfaces call this when a file should be referenced by URL.

*Call graph*: calls 1 internal fn (shared_artifact_link); called by 5 (_terminal_text, _oversize_link_line, shared_files, _file_payload, _project_slot_context).


##### `SurfaceContext.artifact_preview_link`  (lines 2129–2139)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview-image link for a shared artifact when eligible.

**Data flow**: It receives artifact metadata and delegates to `shared_artifact_preview_link` with this workspace’s signing context.

**Call relations**: The web surface calls this for file payloads and conversation slot displays.

*Call graph*: calls 1 internal fn (shared_artifact_preview_link); called by 2 (_file_payload, _project_slot_context).


##### `SurfaceContext.ingress_url`  (lines 2141–2187)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest: str | None=None) -> str | None
```

**Purpose**: Builds a signed browser URL for opening a conversation sandbox port. This lets a surface expose an app or site running inside the sandbox without handing out raw secrets.

**Data flow**: It receives conversation id, port, entry path, and optional framing or shipped-app data. It mints and returns an ingress view URL, or none if ingress is not configured.

**Call relations**: The sites extension calls this when serving frames and shipped app bundles.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (mint_ingress_view_url).


##### `SurfaceContext._identity_member`  (lines 2189–2202)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up which member an external surface identity is linked to. It is the shared private helper for current and peer surfaces.

**Data flow**: It receives a surface name and external id, queries the identity table in this workspace, and returns the member id or none.

**Call relations**: `linked_member` calls it for this surface; `adopt_identity` calls it for a peer surface.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 2204–2205)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the workspace member already linked to this surface’s external user id.

**Data flow**: It receives an external id and returns the member id found by `_identity_member` for this surface.

**Call relations**: Sample, sites, Slack, UFO, and web authentication flows call this before admitting or authorizing a member.

*Call graph*: calls 1 internal fn (_identity_member); called by 7 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, _authenticated_member, _authenticate).


##### `SurfaceContext.member_has_access`  (lines 2207–2209)

```
async def member_has_access(self, member_id: UUID) -> bool
```

**Purpose**: Checks whether a member is admitted by the workspace’s seat rules. This prevents linked identities without current access from using the workspace.

**Data flow**: It receives a member id, opens a workspace transaction, asks the seats service about that member authority, and returns true or false.

**Call relations**: Authentication and Slack live-fold checks call this after resolving an identity.

*Call graph*: called by 4 (_viewer, _folds_into_live_turn, _authenticated_member, _authenticate); 3 external calls (__init__, __init__, workspace_tx).


##### `SurfaceContext.is_operator_workspace`  (lines 2211–2218)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether this workspace belongs to the fleet operator. It gates internal-only display details.

**Data flow**: It reads the workspace domain and compares it to the operator domain constant, returning a boolean.

**Call relations**: Surface renderers can call this before showing operator debugging or accounting footers.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 2220–2243)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to the same member already known by a peer surface. This lets one human keep one member identity across surfaces.

**Data flow**: It receives peer surface and external id, looks up the peer’s member, inserts this surface identity if found, tolerates race conflicts, and returns the member id or none.

**Call relations**: The sample live-admit flow calls this when borrowing identity from another surface.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 2245–2267)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing workspace member by email. It returns none if no member has that email.

**Data flow**: It receives external id and email, finds the oldest case-insensitive matching member, then calls `link_member_id` to create the identity link.

**Call relations**: Authentication flows call this; `join_member` uses it before trying domain-based member creation.

*Call graph*: calls 1 internal fn (link_member_id); called by 5 (join_member, _surface_ingest, _viewer, _authenticated_member, _authenticate); 2 external calls (select, workspace_tx).


##### `SurfaceContext.link_member_id`  (lines 2269–2298)

```
async def link_member_id(self, external_id: str, member_id: UUID) -> UUID | None
```

**Purpose**: Links this surface’s external id to a specific member id after the surface has proved that member requested it.

**Data flow**: It receives external id and member id, verifies the member exists in the workspace, inserts a surface identity, tolerates race conflicts, and returns the member id or none.

**Call relations**: `link_member` delegates here after resolving email to member id.

*Call graph*: called by 1 (link_member); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 2300–2317)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links an external id by email, creating a new member if the email domain matches the workspace domain. This supports first-contact team joins from verified channels.

**Data flow**: It receives external id and email, tries `link_member`, checks workspace and email domains, creates a member when allowed, and links again.

**Call relations**: Slack member resolution calls this for channel-verified emails.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 2319–2329)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the query for finding this surface’s conversation by queue key. A queue key is the surface’s stable channel/thread identifier.

**Data flow**: It receives a queue key and returns a SQL select for conversation id, member, audience, and label in this workspace and surface.

**Call relations**: `find_conversation`, `conversation_for`, and `terminal_op_body` use this helper.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 2331–2337)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for this surface queue key without creating one.

**Data flow**: It receives a queue key, runs `_conversation_lookup`, and returns the conversation id or none.

**Call relations**: Slack and UFO surface routes call this when they need to check existing participation or workspace files without side effects.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 4 (_handle_answer_submit, _participating_conversation, workspace_file, workspace_listing); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 2339–2352)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the agent permanently bound to a conversation. This lets routes check the agent wall before reading content.

**Data flow**: It receives a conversation id, queries the conversation row in this workspace, and returns agent id or none.

**Call relations**: The web surface uses this while resolving chat routes.

*Call graph*: called by 1 (_resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 2354–2357)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation in this workspace through the shared retitle helper.

**Data flow**: It receives conversation id and title, adds this context’s workspace id, and calls `retitle_conversation`.

**Call relations**: Slack and web flows call this after opening, submitting, or renaming conversations.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 4 (_admit_inbound, submit_action, submit_intent, _open_conversation).


##### `SurfaceContext.conversation_for`  (lines 2359–2453)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation for this surface queue key. It also safely narrows audiences and binds new conversations to an agent.

**Data flow**: It receives queue key, audience, optional agent/conversation id, and label. It looks for an existing row, narrows audience or updates label if needed, or inserts a new row with the selected surface agent, handling creation races by re-reading.

**Call relations**: All admitting surfaces call this before `admit`, including Slack, iMessage, sample, UFO, and web panel flows.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, _handle_answer_submit, channel, workspace_upload, submit_action, submit_intent, _open_conversation (+1 more)); 9 external calls (insert, select, update, workspace_tx, log, audience_member, narrow_audience, parse_audience, uuid4).


##### `SurfaceContext._surface_agent`  (lines 2455–2467)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Finds the agent this surface should use in this workspace. It falls back to the workspace main agent.

**Data flow**: It queries a surface installation binding for an agent id. If no binding exists, it returns `_main_agent`.

**Call relations**: `conversation_for` uses this for new conversations, and `_surface_agent_model` uses it for ambient classifier fallback.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (_surface_agent_model, conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 2469–2529)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Decides whether the agent should respond to an unaddressed ambient message in a thread. It avoids creating turns for chatter where the agent is not wanted.

**Data flow**: It receives the message and recent history, asks the ambient reply classifier with a timeout, retries with the surface agent model on some spend refusals, logs outcomes, and returns true when a reply should be admitted.

**Call relations**: Slack and iMessage call this before admitting ambient traffic; failures generally favor admitting rather than silently dropping possible requests.

*Call graph*: calls 1 internal fn (_surface_agent_model); called by 2 (_admit_message, _ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext._surface_agent_model`  (lines 2531–2541)

```
async def _surface_agent_model(self) -> str
```

**Purpose**: Reads the model configured on this surface’s bound agent. It supports fallback decisions for ambient reply classification.

**Data flow**: It finds the surface agent id, queries that agent’s model, and returns the model name.

**Call relations**: `ambient_reply_wanted` calls this when the default classifier cannot be used because of spend policy.

*Call graph*: calls 1 internal fn (_surface_agent); called by 1 (ambient_reply_wanted); 2 external calls (select, workspace_tx).


##### `SurfaceContext.admit`  (lines 2543–2578)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, comment:
```

**Purpose**: Admits a surface message into the durable turn queue as a member or intent. This is the privileged action ordinary extensions do not have.

**Data flow**: It receives conversation id, body, optional idempotency/context/intent/comment/config, and speaker id. It delegates to the injected `MemberAdmitter` and returns the admitted turn result.

**Call relations**: iMessage, sample, Slack, UFO, web chat, and panel routes call this after resolving conversation and member identity.

*Call graph*: called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, _handle_answer_submit, _channel_message, _send, submit_action, submit_intent, _admit_chat (+1 more)).


##### `SurfaceContext.connect_url`  (lines 2580–2586)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates a provider connection authorization URL for a terminal connect request. It lets a member grant an external account.

**Data flow**: It receives turn id and member id, verifies connect flow is installed, creates a handoff, and returns the authorization URL or raises a connect-request error.

**Call relations**: iMessage, Slack, and web surfaces call this when rendering or handling connect controls.

*Call graph*: called by 3 (_terminal_text, _handle_connect_click, connect_handoff); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.held_accounts`  (lines 2588–2608)

```
async def held_accounts(self, owner_member_id: UUID) -> dict[str, str]
```

**Purpose**: Lists the newest held account label per provider for one member. This helps display settled connection choices.

**Data flow**: It receives owner member id, queries that member’s connections, and returns a provider-to-label map.

**Call relations**: The web surface uses this while building connect controls.

*Call graph*: called by 1 (_connect_controls); 2 external calls (select, workspace_tx).


##### `SurfaceContext.connect_available`  (lines 2610–2617)

```
def connect_available(self) -> bool
```

**Purpose**: Reports whether the deployment has connect-flow machinery configured.

**Data flow**: It tries to resolve the installed connect flow and returns false if unavailable, true otherwise.

**Call relations**: The web surface gates connect controls, events, and provider labels with this.

*Call graph*: called by 3 (_connect_controls, _events, _provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connect_label`  (lines 2619–2621)

```
def connect_label(self, provider: str) -> str
```

**Purpose**: Returns the display label for a connect provider.

**Data flow**: It receives a provider id, reads the installed connect flow, and returns that provider’s label.

**Call relations**: The web surface calls this when showing provider names.

*Call graph*: called by 1 (_provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connector_catalog`  (lines 2623–2625)

```
async def connector_catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads a page of available connector providers from the deployment catalog.

**Data flow**: It receives search text, limit, and cursor, passes them to the connector registry, and returns the catalog page.

**Call relations**: The web connector catalog route calls this directly.

*Call graph*: called by 1 (connector_catalog).


##### `SurfaceContext.admitted_body`  (lines 2627–2652)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Finds the message body admitted under an idempotency key. This helps a surface settle races between repeated clicks or deliveries.

**Data flow**: It receives a key, first checks turn rows, then queued inbound-message rows, and returns the stored body or none.

**Call relations**: Slack and web admission flows call this to verify which submitted answer actually landed.

*Call graph*: called by 3 (_handle_answer_submit, _unseen_tail, _admit_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 2654–2668)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member whose conversation owns a turn. Live surfaces use this to prevent one member from tailing another’s turn.

**Data flow**: It receives a turn id, joins turn to conversation in this workspace, and returns the conversation member id or none.

**Call relations**: Sample and web live-turn routes call this before opening a frame stream.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 2670–2676)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops a running turn in an already-authorized conversation.

**Data flow**: It receives conversation and turn ids, adds the workspace id, delegates to the injected stopper, and returns whether the stop ended anything and whether a follow-up founded a new turn.

**Call relations**: UFO and web surfaces call this from stop controls.

*Call graph*: called by 2 (_channel_stop, _stop_chat).


##### `SurfaceContext.retract_arrival`  (lines 2678–2696)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Deletes a member’s queued message if no turn has consumed it yet. It is an “unsend” for still-waiting arrivals.

**Data flow**: It receives conversation, arrival, and member ids. It deletes the matching unconsumed inbound row only if the same member spoke it, and returns whether one row was removed.

**Call relations**: The UFO surface calls this from its unsend route.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 2698–2715)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a final state using the database, not the live hub. This avoids posting progress after the final answer.

**Data flow**: It receives a turn id, reads its status in this workspace, and returns true if missing or terminal.

**Call relations**: The UFO channel message flow calls this before side-channel updates.

*Call graph*: called by 1 (_channel_message); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 2717–2734)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the newest turn in a conversation. This helps reconnecting live clients resume the right stream or render open handoffs.

**Data flow**: It receives a conversation id, queries turns ordered by sequence descending, and returns the latest turn id or none.

**Call relations**: Slack, UFO, and web routes call this when resolving chats, stops, operations, and message projections.

*Call graph*: called by 6 (_participating_conversation, _channel_message, _channel_op_reply, _channel_stop, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.absorbing_turn`  (lines 2736–2780)

```
async def absorbing_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Checks whether a new message would fold into an existing live turn rather than start a new one. It also checks spend and balance gates.

**Data flow**: It receives a conversation id, reads the oldest non-terminal turn, ignores parked turns, evaluates spend and balance, and returns the turn id only if admission would currently be allowed.

**Call relations**: Slack uses this before ambient-reply decisions so it does not drop messages that admission would fold into a live turn.

*Call graph*: called by 1 (_folds_into_live_turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.tail`  (lines 2782–2788)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn. This is how live surfaces show progress while a turn runs.

**Data flow**: It receives turn id and optional cursor, delegates to the injected tailer, and returns an async context manager yielding frames.

**Call relations**: Debugger, sample, web panels, web events, and object-write flows call this to stream runtime output.

*Call graph*: called by 6 (_events, _surface_frames, _intent_result, submit_action, _events, object_write).


##### `SurfaceContext.latest_activity`  (lines 2790–2794)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Peeks at the latest retained live activity for a turn without opening a stream.

**Data flow**: It receives a turn id, delegates to the injected tailer, and returns an activity record or none.

**Call relations**: The web agents-status route calls this after `agent_turn_statuses` identifies running turns.

*Call graph*: called by 1 (agents_status).


##### `SurfaceContext.spend_rollup`  (lines 2796–2799)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace usage and cost for a selected time window or all time.

**Data flow**: It receives an optional window in seconds, opens a transaction, asks `SpendRollup` to read the report, and returns it.

**Call relations**: Sample and web usage views call this.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 2801–2816)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded file into a conversation’s sandbox workspace before the turn runs.

**Data flow**: It receives conversation id, relative path, and byte chunks. It accumulates chunks up to a configured cap, rejects oversize input, and writes the final bytes through the sandbox carrier.

**Call relations**: iMessage, sample, Slack, UFO, and web upload paths use this for inbound attachments.

*Call graph*: called by 5 (_downloaded_files, _surface_ingest, _download_files, workspace_upload, _deliver_uploads).


##### `SurfaceContext.render_preview`  (lines 2818–2877)

```
async def render_preview(self, kind: str, data: bytes, start_page: int=1, pages: int=1) -> PreviewRender | None
```

**Purpose**: Asks an external preview service to render document pages as PNG thumbnails. Surfaces can show previews without rasterizing files themselves.

**Data flow**: It receives file kind, bytes, start page, and page count. If preview service is configured, it posts the file and render request, accepts PNG or ZIP results, unpacks pages, reads page count, and returns `PreviewRender` or none.

**Call relations**: The web preview route calls this when a member previews an upload.

*Call graph*: called by 1 (preview); 5 external calls (__init__, AsyncClient, BytesIO, dumps, ZipFile).


##### `SurfaceContext.list_agents`  (lines 2879–2916)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists active agents in the workspace, with the main agent first. Surfaces use it when a member can choose or view agents.

**Data flow**: It queries unarchived agent rows, orders them, and returns `AgentSummary` records.

**Call relations**: Sites and web audience, app, and subagent views call this.

*Call graph*: called by 5 (_shipped_frame, frame, web_audience, _created_apps, _subagent_nodes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_archived_agents`  (lines 2918–2949)

```
async def list_archived_agents(self) -> tuple[ArchivedAgent, ...]
```

**Purpose**: Lists archived agents so the portal can show restore options.

**Data flow**: It queries archived agent rows, chooses archived display names, orders newest first, and returns `ArchivedAgent` records.

**Call relations**: The web agents index calls this.

*Call graph*: called by 1 (agents_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_extension_agent_ids`  (lines 2951–2966)

```
async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]
```

**Purpose**: Finds agents that have private extension conversations for a member.

**Data flow**: It receives a member id, queries matching extension-surface conversations, and returns distinct agent ids.

**Call relations**: The web audience builder uses this when deciding what a member can see.

*Call graph*: called by 1 (web_audience); 3 external calls (select, workspace_tx, conversation_audience).


##### `SurfaceContext.agent_detail`  (lines 2968–3026)

```
async def agent_detail(self, agent_id: UUID, member_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads one agent’s full settings for the portal. It includes prompt digest and surface bindings.

**Data flow**: It receives agent and member ids, queries the agent row and bound surfaces, computes the prompt digest, and returns `AgentDetail` or none.

**Call relations**: Web panel settings and complete-agent-spec code call this.

*Call graph*: called by 2 (_complete_agent_spec, agent_settings); 4 external calls (__init__, select, workspace_tx, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 3028–3040)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for an object kind registered by the deployment. This tells the portal which fields and schema it can render.

**Data flow**: It receives a kind name, looks up the bound kind, and returns a `PortalKind` with list fields and optional schema or none.

**Call relations**: Web object gates, action views, writes, and first-run flows call this before showing object pages.

*Call graph*: called by 4 (_object_gate, action_views, object_write, workspace_first_run); 1 external calls (__init__).


##### `SurfaceContext.object_actions`  (lines 3042–3057)

```
def object_actions(self, kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Builds the action controls the portal should show for a specific object target.

**Data flow**: It receives kind, binding, optional name, and generation. It asks the object-view helper to present actions from the registered action map and returns action views.

**Call relations**: Web panels and workspace pages call this when rendering buttons or forms for actions.

*Call graph*: called by 7 (submit_action, _connect_declared, action_views, workspace_credentials, workspace_first_run, workspace_memory, workspace_team); 1 external calls (presented_action_views).


##### `SurfaceContext.frame_admits`  (lines 3059–3062)

```
def frame_admits(self, callable_id: str) -> bool
```

**Purpose**: Checks whether an embedded frame is allowed to post a callable action. This protects frame-originated tool calls.

**Data flow**: It receives a callable id and returns whether it is in the precomputed admissible set.

**Call relations**: Web panel preparation and submit routes call this before accepting frame-posted intents.

*Call graph*: called by 2 (_prepare_panel_intent, submit_action).


##### `SurfaceContext.agent_skills`  (lines 3064–3103)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists deploy-provided and member-authored skills available to an agent. Deploy skills win if names collide.

**Data flow**: It receives agent id, binds that agent, reads member skills, drops member skills shadowed by deploy skills, and returns `PortalSkill` records for top-level deploy and member skills.

**Call relations**: The web skills route calls this to render the skills page.

*Call graph*: called by 1 (skills); 3 external calls (__init__, log, agent).


##### `SurfaceContext.model`  (lines 3106–3110)

```
def model(self) -> 'SurfaceModel | None'
```

**Purpose**: Returns the optional metered model access wired for surface helper calls.

**Data flow**: It reads and returns the stored `SurfaceModel` or none.

**Call relations**: Surface routes gate optional model-powered helpers on this property.


##### `SurfaceContext.memory_available`  (lines 3113–3117)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory-search provider is installed. The portal uses this to hide memory features when absent.

**Data flow**: It checks whether the stored memory provider is not none and returns a boolean.

**Call relations**: `search_memory`, `recent_memory`, and `memory_kinds` require callers to check this first.


##### `SurfaceContext.search_memory`  (lines 3119–3129)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches memory items visible to a given reader. Memory means stored source or conversation-derived knowledge.

**Data flow**: It receives a reader and query strings, verifies a memory provider exists, forwards the search, and returns matches.

**Call relations**: The web workspace memory route calls this after building the same reader shape used by turn tools.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 3131–3144)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items for readable subjects without a search query.

**Data flow**: It receives subjects, limit, optional kinds, and cursor, verifies memory is available, asks the provider for a page, and returns it.

**Call relations**: The web recalled-items and workspace memory views call this.

*Call graph*: called by 2 (_recalled, workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 3147–3152)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the list of memory item kinds the installed provider can list.

**Data flow**: It verifies a memory provider exists and returns its listable kind names.

**Call relations**: Memory UI filters use this alongside `memory_available`.


##### `SurfaceContext.member_spend`  (lines 3154–3159)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads one member’s usage report and caps for a time window or all time.

**Data flow**: It receives member id and optional window, opens a transaction, asks `SpendRollup` for the member report, and returns it.

**Call relations**: The web workspace usage route calls this for per-member usage.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 3161–3213)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to one agent that the viewer may see. Connector accounts are external accounts like GitHub or similar providers.

**Data flow**: It receives agent/member ids and admin flag, queries grants joined to connections and owners, applies visibility rules, and returns `ConnectionView` records.

**Call relations**: The web connections page calls this for an agent.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.list_connections`  (lines 3215–3294)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists the connection pool visible to a member, along with live agents attached to each connection.

**Data flow**: It receives member id and admin flag, queries visible shared or owned connections and non-archived agent holders, groups rows by provider/account, and returns `ConnectionPoolView` records.

**Call relations**: The web held-providers and connection-pool routes call this.

*Call graph*: called by 2 (_held_providers, connection_pool); 7 external calls (__init__, __init__, and_, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.github_coverage`  (lines 3296–3333)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports whether the viewer has visible GitHub API connections and GitHub sources. It powers coverage/status UI.

**Data flow**: It receives member id and admin flag, builds visibility conditions, checks existence of GitHub connection and source rows, and returns booleans.

**Call relations**: The web GitHub coverage endpoint calls this.

*Call graph*: called by 1 (github_coverage); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.recent_object_changes`  (lines 3335–3374)

```
async def recent_object_changes(self, limit: int) -> tuple[ObjectChange, ...]
```

**Purpose**: Reads recent object-change audit entries for the workspace. These record create, update, and delete actions.

**Data flow**: It receives a limit, queries object-change rows newest first, normalizes timestamps, and returns `ObjectChange` records.

**Call relations**: The web object changes page calls this after applying its own admin gate.

*Call graph*: called by 1 (object_changes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversation_artifacts`  (lines 3376–3445)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists recent files shared by one conversation. Authorization is expected to happen before calling.

**Data flow**: It receives conversation id and limit, queries shared artifacts joined to turn and conversation metadata, gets the conversation source, and returns `ListedArtifact` records.

**Call relations**: The web surface calls this for slot context and transcript aids.

*Call graph*: called by 2 (_project_slot_context, _transcript_aids); 5 external calls (__init__, __init__, __init__, select, workspace_tx).


##### `SurfaceContext.agent_turn_statuses`  (lines 3447–3575)

```
async def agent_turn_statuses(self, agent_ids: Sequence[UUID], member_id: UUID) -> tuple[AgentTurnStatus, ...]
```

**Purpose**: Summarizes live and recent turn status for a set of agents as visible to one member. It avoids leaking another member’s private turns.

**Data flow**: It receives agent ids and member id, computes readable audiences, queries live turns and latest readable activity per agent, then returns statuses in the same order as requested.

**Call relations**: The web agents-status route calls this, then may call `latest_activity` for running turn ids.

*Call graph*: called by 1 (agents_status); 5 external calls (__init__, case, select, workspace_tx, readable_audiences).


##### `SurfaceContext.agent_setup`  (lines 3577–3616)

```
async def agent_setup(self, agent_id: UUID, member_id: UUID) -> SetupState
```

**Purpose**: Computes what an agent still needs before it is ready: accounts, credentials, standing orders, and related setup state.

**Data flow**: It receives agent and member ids, defines an inner object-checking helper, enters workspace scope, asks setup logic for state, and returns it.

**Call relations**: The web agent setup and workspace starters routes call this; its inner `armed` helper reads object state.

*Call graph*: called by 2 (agent_setup, workspace_starters); 2 external calls (setup_state, ws).


##### `SurfaceContext.agent_setup.armed`  (lines 3599–3613)

```
async def armed(kind: str, name: str | None) -> ArmedOrder
```

**Purpose**: Checks whether a required standing order object is present for an agent setup step.

**Data flow**: It receives object kind and optional name. It either reads the named object detail or lists objects of that kind, then returns an `ArmedOrder` including schedule when available.

**Call relations**: It is passed into `setup_state` by `SurfaceContext.agent_setup` so extension-defined order kinds can answer readiness.

*Call graph*: calls 2 internal fn (list_member_objects, member_object); 2 external calls (__init__, __init__).


##### `SurfaceContext.list_member_objects`  (lines 3618–3644)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Lists one registered object kind as a signed-in member can see it.

**Data flow**: It receives kind, agent, member, admin flag, and query. It verifies the kind exists and supports member listing, binds the agent, stamps supported fields into the query, and returns a page or none.

**Call relations**: Web object indexes and `agent_setup.armed` call this.

*Call graph*: called by 3 (armed, _bound_page, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 3646–3661)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one object detail as a signed-in member can see it.

**Data flow**: It receives kind, name, agent, member, and admin flag. It verifies the kind supports member detail, binds the agent, and returns the object or none.

**Call relations**: Web object detail pages and `agent_setup.armed` call this.

*Call graph*: called by 2 (armed, object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 3663–3685)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants associated with a conversation for a member-visible view.

**Data flow**: It receives kind, agent, conversation, member, admin flag, and limit. It verifies support, binds the agent, and asks the object store for conversation rows.

**Call relations**: The web surface calls this while projecting conversation slot context.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 3687–3714)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists declared bring-your-own-key credential slots and whether each is filled, never the secret value.

**Data flow**: It reads filled credential slot names from the database, maps declarations to object names, and returns sorted `CredentialSlotView` records.

**Call relations**: Web credential pages and panel refusal rendering call this.

*Call graph*: called by 2 (_intent_refusal, workspace_credentials); 4 external calls (__init__, select, workspace_tx, named_slots).


##### `SurfaceContext.workspace_domain`  (lines 3716–3722)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Returns the workspace’s domain used for domain-based joining, or none for personal-email workspaces.

**Data flow**: It opens a workspace transaction, asks the seats helper for the domain, and returns it.

**Call relations**: `join_member` and `is_operator_workspace` call this.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.list_members`  (lines 3724–3732)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Lists the workspace roster in stable email order.

**Data flow**: It reads a seats snapshot from the database and returns the member entries sorted by email.

**Call relations**: The web workspace team page calls this.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 3734–3781)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists active source bindings visible to a member. Sources feed workspace or member-private knowledge into the system.

**Data flow**: It receives member id and admin flag, queries non-removed source rows with owner data, applies visibility rules, adds connector binding fields, and returns `SourceView` records.

**Call relations**: The web workspace sources page calls this; `_binding_fields` supplies action identity fields.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 3783–3799)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations bound to this workspace and the agent each routes to.

**Data flow**: It queries installation rows ordered by surface and returns `InstallationSummary` records.

**Call relations**: Web first-run, held-provider, and workspace-surfaces views call this.

*Call graph*: called by 3 (_held_providers, workspace_first_run, workspace_surfaces); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_surfaces`  (lines 3801–3816)

```
async def member_surfaces(self, member_id: UUID) -> frozenset[str]
```

**Purpose**: Lists surfaces where a member is reachable through identity links or proved addresses.

**Data flow**: It receives a member id, unions surface identities and proved surface addresses for that member, and returns a set of surface names.

**Call relations**: The web workspace-surfaces page calls this to show member reachability.

*Call graph*: called by 1 (workspace_surfaces); 3 external calls (select, union, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 3818–3867)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists workspace conversations across all surfaces for debug-style views.

**Data flow**: It receives an optional limit, computes turn counts and latest turn times, queries conversations newest activity first, and returns `ConversationSummary` records.

**Call relations**: The debugger surface calls this for conversation browsing.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 3869–3892)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Lists conversations for one agent through the shared conversation directory.

**Data flow**: It receives agent/member ids and filters, creates a `ConversationDirectory` for this workspace, and delegates the listing.

**Call relations**: Web member-chat, named-chat, resolve-chat, and conversations routes call this.

*Call graph*: called by 4 (_member_chat, _named, _resolve_chat, conversations); 1 external calls (__init__).


##### `SurfaceContext.readable_conversation`  (lines 3894–3935)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Checks whether a member may read a conversation’s content. Admins need a recent recorded disclosure for another member’s private conversation.

**Data flow**: It receives conversation, agent, member, and admin flag. It verifies the conversation belongs to the agent, checks normal readable audiences, then checks recent transcript-access rows for eligible admin disclosure.

**Call relations**: The web surface calls this through its readable-conversation gate before serving transcripts and files.

*Call graph*: called by 1 (_readable_conversation); 6 external calls (now, select, workspace_tx, audience_member, parse_audience, readable_audiences).


##### `SurfaceContext.conversation_audience`  (lines 3937–3947)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience bound to a conversation for a given agent.

**Data flow**: It receives conversation and agent ids, reads the stored audience string, parses it, and returns an audience object or none.

**Call relations**: The web slot-context builder calls this before projecting conversation-specific data.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, workspace_tx, parse_audience).


##### `SurfaceContext.conversation_subagent_turns`  (lines 3949–3986)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists turns spawned beneath a conversation’s turns, including nested subagents. This lets UIs show the work tree under a parent conversation.

**Data flow**: It receives conversation id and limit, builds a recursive query over parent-turn links, reads turn rows breadth first, and converts them to `Turn` records.

**Call relations**: The web surface calls this for events, slot targets, and transcript aids.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_events, _slot_target, _transcript_aids); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 3988–4004)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists the recent turns of a conversation in admission order.

**Data flow**: It receives conversation id and limit, queries the newest turn rows by descending sequence, reverses them to oldest-first, and returns `Turn` records.

**Call relations**: Debugger and web transcript-aid routes call this.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _transcript_aids); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 4006–4041)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Finds transcript message references that came from scheduled or subagent machine-origin messages rather than member prose.

**Data flow**: It receives conversation id, unions matching turn ids and inbound-message ids, and returns them as strings.

**Call relations**: Web conversation message builders use this to avoid rendering machine envelopes as member chat bubbles.

*Call graph*: called by 2 (_conversation_messages, _history_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 4043–4093)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn with its billing ledger and direct child turns.

**Data flow**: It receives a turn id, reads the turn row, child turn rows, and ledger rows, converts them to models, and returns `TurnDetail` or none.

**Call relations**: Debugger and web routes call this for turn pages, streams, member-turn resolution, and conversation messages.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.turn_steps`  (lines 4095–4108)

```
async def turn_steps(self, turn_id: UUID) -> tuple[TurnStep, ...] | None
```

**Purpose**: Reads durable workflow steps for a turn if it belongs to this workspace.

**Data flow**: It receives a turn id, reads the running attempt id or falls back to the turn id, then delegates to the injected step source. It returns steps or none.

**Call relations**: The debugger turn-steps route calls this.

*Call graph*: called by 1 (turn_steps); 2 external calls (select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 4110–4151)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Lists admitted messages that are not yet visible in the written transcript. This keeps reloads from hiding messages accepted during a running turn.

**Data flow**: It receives conversation id and optional draining turn id, checks ownership, queries unconsumed or currently drained inbound rows, and returns `QueuedArrival` records.

**Call relations**: The web conversation message projection calls this beside turn rows.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 4153–4185)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Reads attribution for member-admitted queued messages, including already-drained ones.

**Data flow**: It receives conversation id, checks ownership, reads member-admitted inbound-message contexts, parses sender and question data, and returns `SpokenArrival` records.

**Call relations**: Web conversation and history message builders call this to label folded messages.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (_conversation_messages, _history_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.keyed_admissions`  (lines 4187–4223)

```
async def keyed_admissions(self, conversation_id: UUID) -> tuple[KeyedAdmission, ...]
```

**Purpose**: Lists all messages in a conversation that arrived with idempotency keys. This helps a surface recognize its own prior submissions.

**Data flow**: It receives conversation id, checks ownership, unions keyed turn rows and keyed inbound-message rows, and returns `KeyedAdmission` records.

**Call relations**: The web transcript-aids flow calls this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_transcript_aids); 4 external calls (__init__, select, union_all, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 4225–4236)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads the durable transcript blob for a conversation if it belongs to this workspace.

**Data flow**: It receives conversation id, checks ownership, reads the transcript blob by key, decodes it, and returns a `Conversation` or none if missing.

**Call relations**: Debugger and web message, slot, and subagent views call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 4 (conversation_transcript, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 4238–4249)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists saved compaction record indices for a conversation. Compaction records explain transcript summarization windows.

**Data flow**: It receives conversation id, checks ownership, lists compaction blob keys, extracts numeric indices, sorts them, and returns them.

**Call relations**: Debugger and web conversation-message views call this when showing compacted history.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_compactions, _conversation_messages).


##### `SurfaceContext.read_compaction`  (lines 4251–4257)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one full compaction record for a conversation.

**Data flow**: It receives conversation id and index, checks ownership, reads the compaction record from blob storage, and returns it or none.

**Call relations**: Debugger and web history-message routes call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (compaction_record, _history_messages); 1 external calls (read_compaction_record).


##### `SurfaceContext.read_compaction_after`  (lines 4259–4267)

```
async def read_compaction_after(self, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the post-compaction message window for a compaction record.

**Data flow**: It receives conversation id and index, checks ownership, reads the compacted-after window, and returns messages or none.

**Call relations**: The web verified-earlier flow calls this when comparing later transcript windows.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_verified_earlier); 1 external calls (read_compaction_after).


##### `SurfaceContext.list_workspace_files`  (lines 4269–4275)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files currently in a conversation’s sandbox workspace.

**Data flow**: It receives conversation id, checks ownership, asks the sandbox for entries, and returns them or an empty tuple.

**Call relations**: Debugger, UFO, and web attachment/listing routes call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 3 (workspace_files, workspace_listing, conversation_attachment).


##### `SurfaceContext.conversation_changes`  (lines 4277–4283)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last recorded workspace file changes for a conversation.

**Data flow**: It receives conversation id, checks ownership, then reads recorded workspace changes or returns a no-change value.

**Call relations**: The web project-slot context uses this to show changed files.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 4285–4294)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one file from a conversation’s sandbox workspace if readable.

**Data flow**: It receives conversation id and relative path, checks ownership, asks the sandbox for a byte stream, and returns the stream or none.

**Call relations**: Debugger, UFO, and web attachment routes call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 3 (workspace_file, workspace_file, conversation_attachment).


##### `SurfaceContext.terminal_connect`  (lines 4296–4302)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Registers a held client terminal as available for a conversation sandbox.

**Data flow**: It receives conversation id, working directory, optional member id, and runtime id, and records the terminal connection in the sandbox terminal registry.

**Call relations**: Live terminal transports pair this with `terminal_disconnect` around connection lifetime.


##### `SurfaceContext.terminal_disconnect`  (lines 4304–4305)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the connected terminal binding for a conversation.

**Data flow**: It receives a conversation id and tells the sandbox terminal registry to disconnect it.

**Call relations**: Terminal transports call this when a held stream closes.


##### `SurfaceContext.claim_terminal`  (lines 4307–4313)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a fresh conversation to a connected terminal before the first sandbox open. This helps the turn use the member’s terminal instead of provisioning a default sandbox.

**Data flow**: It receives conversation id and working directory, asks the sandbox carrier to claim the terminal if unbound, and returns whether this call made the claim.

**Call relations**: The UFO channel send/message flow calls this during admission.

*Call graph*: called by 2 (_channel_message, _send).


##### `SurfaceContext.next_terminal_op`  (lines 4315–4322)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next operation a turn asks of a connected terminal.

**Data flow**: It receives conversation id and optional operation id to exclude, asks the terminal registry for the next operation, and returns it.

**Call relations**: Held terminal streams use this beside turn frames to render run directives.


##### `SurfaceContext.terminal_resolve`  (lines 4324–4338)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Answers a pending terminal operation with the client’s reply or failure.

**Data flow**: It receives conversation id, operation id, reply bytes, optional failure text, and member id. It forwards them to the terminal registry, which enforces operation and member matching, and returns whether it resolved.

**Call relations**: The UFO channel operation-reply route calls this.

*Call graph*: called by 1 (_channel_op_reply).


##### `SurfaceContext.terminal_op_body`  (lines 4340–4352)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads staged bytes for an in-flight terminal operation without creating a conversation.

**Data flow**: It receives queue key, operation id, and member id, finds the existing conversation by queue key, then asks the terminal registry for the staged body if the member may read it.

**Call relations**: The UFO operation-body route calls this.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 4354–4367)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface. It is useful for deep links or debug metadata.

**Data flow**: It receives a peer surface name, queries the installation table, and returns the installation id or none.

**Call relations**: The debugger workspace metadata route calls this.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 4370–4379)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Yields a raw workspace transaction for a surface extension’s own tables. The caller must still scope its SQL to the workspace.

**Data flow**: It opens a workspace transaction, yields the connection, commits on normal exit, and rolls back on error.

**Call relations**: The sites surface uses this for admin checks over extension-owned rows.

*Call graph*: called by 1 (_viewer_is_admin); 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 4381–4391)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace.

**Data flow**: It receives a conversation id, queries the conversation table under this workspace, and returns true if found.

**Call relations**: Transcript, compaction, workspace-file, queued-arrival, and related read methods call this before touching unscoped blob or sandbox storage.

*Call graph*: called by 10 (arrival_speakers, conversation_changes, keyed_admissions, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_compaction_after, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 4393–4413)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the standard SQL select for turn rows. This keeps all turn projections using the same column set.

**Data flow**: It takes no input and returns a SQL select with all fields needed to rebuild a `Turn` model.

**Call relations**: `conversation_subagent_turns`, `list_turns`, and `turn_detail` use this before calling `_turn_record`.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 4415–4435)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database row into a typed `Turn` object.

**Data flow**: It receives a row, validates nested context and terminal JSON when present, and returns a `Turn` instance.

**Call relations**: Turn listing, subagent listing, and turn detail reads call this after database queries.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.reserve_address`  (lines 4467–4527)

```
async def reserve_address(self, surface: str, address: str, member_id: UUID, claim_expires_at: datetime) -> AddressClaimState
```

**Purpose**: Reserves an addressed-surface address for a workspace member until they prove it. It reports whether the address is reserved, already linked, or taken.

**Data flow**: It receives surface, address, member id, and expiry. It checks the surface was declared as addressed, upserts or takes over an expired reservation in the owner database, and returns an address-claim state.

**Call relations**: Tools use this manifest-scoped access object when setting up addressed surfaces.

*Call graph*: 7 external calls (__init__, now, and_, or_, select, owner_tx, ws_current).


##### `SurfaceInstallationAccess.installation`  (lines 4529–4542)

```
async def installation(self, surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for a manifest-declared surface.

**Data flow**: It receives a surface name, rejects undeclared surfaces, queries the workspace installation row, and returns the id or none.

**Call relations**: Extension tools call this when they need to inspect their own surface installation.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `SurfaceInstallationAccess.bind`  (lines 4544–4556)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Binds a declared surface installation to the current workspace from a tool context.

**Data flow**: It receives surface and installation id, rejects undeclared surfaces, chooses routing mode based on whether the surface is addressed, and calls `_bind_surface_installation`.

**Call relations**: Manifest-scoped installation tools use this shared path, the same writer used by surface OAuth callbacks.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 4568–4579)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Resolves a routing installation id to the workspace that owns it. This happens before a shared surface request is workspace-bound.

**Data flow**: It receives an installation id, queries the owner database for a matching routing installation, and returns the workspace id or none.

**Call relations**: Slack workspace resolution calls this for incoming requests.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.addressed_workspace`  (lines 4581–4594)

```
async def addressed_workspace(self, address: str) -> UUID | None
```

**Purpose**: Resolves an addressed-surface address to the workspace that claimed it.

**Data flow**: It receives an address, queries the owner database for this surface/address pair, and returns workspace id or none.

**Call relations**: Addressed listeners use this through `SurfaceListenerContext.addressed`.

*Call graph*: 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 4596–4606)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential handoff before a workspace has been bound. It is useful for OAuth state callbacks.

**Data flow**: It receives a sealed token, returns none if no store or invalid seal, otherwise returns the credential request state.

**Call relations**: Slack workspace resolution calls this while handling OAuth-style callbacks.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 4608–4624)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential slot for a specific workspace during pre-binding authentication.

**Data flow**: It receives workspace id and slot, rejects undeclared slots, verifies the workspace exists, enters workspace scope, and returns the credential value.

**Call relations**: Slack authentication reads signing secrets through this resolver path.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceListenerContext.workspace`  (lines 4672–4680)

```
async def workspace(self, installation_id: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds a persistent listener event to the workspace that owns an installation id.

**Data flow**: It receives installation id, verifies this process still owns the listener lease, resolves workspace id, enters workspace scope if found, and yields a `SurfaceContext` or none.

**Call relations**: Persistent listener implementations use this before admitting or reading any installation-routed event.

*Call graph*: 1 external calls (ws).


##### `SurfaceListenerContext.addressed`  (lines 4683–4694)

```
async def addressed(self, address: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds a persistent listener event to the workspace reached by a sender address.

**Data flow**: It receives an address, verifies listener ownership, resolves the workspace by address, enters workspace scope if found, and yields a surface context or none.

**Call relations**: The iMessage listener calls this while processing events.

*Call graph*: called by 1 (_process_event); 1 external calls (ws).


##### `SurfaceListenerContext.cursor`  (lines 4696–4711)

```
async def cursor(self, installation_id: str) -> int | None
```

**Purpose**: Reads the saved stream cursor for a persistent listener installation. A cursor is the last processed provider sequence number.

**Data flow**: It receives installation id, reads the surface stream cursor, and returns the sequence only if it belongs to the same installation.

**Call relations**: The iMessage listener calls this when starting or resuming stream reads.

*Call graph*: called by 1 (listen); 2 external calls (select, owner_tx).


##### `SurfaceListenerContext.store_cursor`  (lines 4713–4736)

```
async def store_cursor(self, installation_id: str, sequence: int) -> None
```

**Purpose**: Saves the listener’s current stream position.

**Data flow**: It receives installation id and sequence, upserts the surface cursor row in the owner database, and records the new sequence.

**Call relations**: The iMessage listener calls this after catching up or processing events.

*Call graph*: called by 2 (_catch_up, _process_event); 1 external calls (owner_tx).


##### `SurfaceListenerContext.clear_cursor`  (lines 4738–4745)

```
async def clear_cursor(self) -> None
```

**Purpose**: Deletes the saved stream cursor so the next listener run starts from the provider’s current head.

**Data flow**: It deletes the cursor row for this surface from the owner database.

**Call relations**: The iMessage listener calls this when it must reset stream position.

*Call graph*: called by 1 (listen); 2 external calls (delete, owner_tx).


##### `SurfaceListenerRunner.run`  (lines 4772–4811)

```
async def run(self) -> None
```

**Purpose**: Runs one persistent surface listener only while this process owns a fleet-wide lease. It prevents multiple replicas from consuming the same provider stream.

**Data flow**: It loops forever, waits for ownership, starts the listener and an ownership watcher, reacts to listener failure or lost ownership, logs failures, parks non-database failures, and cancels child tasks on exit.

**Call relations**: The application starts this runner for surfaces that declare a listener; it coordinates `SurfaceListenerContext` creation and lease monitoring.

*Call graph*: calls 2 internal fn (_wait_until_not_owned, _wait_until_owned); 9 external calls (__init__, CancelledError, create_task, ensure_future, gather, sleep, wait, emit_metric, log).


##### `SurfaceListenerRunner._wait_until_not_owned`  (lines 4813–4818)

```
async def _wait_until_not_owned(self) -> None
```

**Purpose**: Waits until this process no longer owns the listener lease.

**Data flow**: It repeatedly checks ownership and sleeps between checks. It returns when ownership is explicitly false.

**Call relations**: `run` starts this as the watcher beside the listener task.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._wait_until_owned`  (lines 4820–4824)

```
async def _wait_until_owned(self) -> None
```

**Purpose**: Waits until this process successfully owns the listener lease.

**Data flow**: It repeatedly attempts an ownership tick and sleeps until the result is true.

**Call relations**: `run` calls this before starting the persistent listener.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._owned_on_tick`  (lines 4826–4835)

```
async def _owned_on_tick(self) -> bool | None
```

**Purpose**: Attempts one safe ownership check and claim refresh. Database errors are logged and treated as unknown.

**Data flow**: It calls `_owns`, returns its boolean result, or logs SQL errors and returns none.

**Call relations**: Both ownership wait loops use this helper.

*Call graph*: calls 1 internal fn (_owns); called by 2 (_wait_until_not_owned, _wait_until_owned); 1 external calls (log).


##### `SurfaceListenerRunner._owns`  (lines 4837–4855)

```
async def _owns(self) -> bool
```

**Purpose**: Runs the ownership claim transaction in a cancellation-safe way. This avoids leaving database locks behind if the task is cancelled mid-claim.

**Data flow**: It starts `_claim` as a task, shields it until done, remembers cancellation, re-raises cancellation after the claim settles, and returns the claim result.

**Call relations**: `_owned_on_tick` calls this on each lease tick.

*Call graph*: calls 1 internal fn (_claim); called by 1 (_owned_on_tick); 2 external calls (ensure_future, shield).


##### `SurfaceListenerRunner._claim`  (lines 4857–4897)

```
async def _claim(self) -> bool
```

**Purpose**: Claims or renews the fleet-wide listener lease for this surface.

**Data flow**: It computes a new expiry, upserts the listener-claim row if expired or already owned by this runner token, and returns whether the stored token is this runner’s token.

**Call relations**: `_owns` calls this inside its cancellation-safe wrapper.

*Call graph*: called by 1 (_owns); 7 external calls (now, timedelta, and_, insert, insert, or_, owner_tx).


##### `SurfaceDeliveryError.__init__`  (lines 4904–4908)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that can carry a provider retry delay. The delay tells pollers when to try again.

**Data flow**: It receives a message and optional retry-after seconds, rejects negative delays, stores the delay, and initializes the runtime error.

**Call relations**: Slack posting code can raise this; writeback and mid-turn pollers read it in retry logic.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 4971–4995)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the condition for terminal writebacks ready to deliver. It waits until mid-turn replies for the same turn are no longer pending.

**Data flow**: It receives the current time and returns a SQL condition over terminal turn status, no pending/claimed mid-turn replies, and claimable writeback status.

**Call relations**: `writeback_workspaces.due` and `WritebackPoller._claim` use this to find deliverable terminal replies.

*Call graph*: called by 2 (_claim, due); 3 external calls (and_, exists, or_).


##### `writeback_workspaces`  (lines 4998–5033)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace candidate reader for terminal writebacks. It keeps the poller from scanning or locking all workspaces at once.

**Data flow**: It initializes a cursor, builds an owner-scoped candidate reader around the nested due query, and returns the async candidate function.

**Call relations**: The `WritebackPoller` receives this candidate function at construction.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 5005–5019)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query of workspace ids that have due terminal writebacks.

**Data flow**: It reads the current cursor and time, selects workspace ids with due writebacks, groups and orders them, applies the cursor if present, and returns the SQL query.

**Call relations**: The candidate reader inside `writeback_workspaces` calls this.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 5023–5031)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with deliverable writebacks.

**Data flow**: It runs the due reader, wraps back to the start when a cursor page is empty, updates the cursor to the last id returned, and returns the ids.

**Call relations**: `WritebackPoller.run` and `WritebackPoller.drain` use this through the poller’s `candidates` field.


##### `_WritebackDeliveryFailed.__init__`  (lines 5041–5044)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a delivery failure with the phase that failed: posting the reply or attaching files.

**Data flow**: It receives phase and original exception, stores them, and initializes the error message from the exception.

**Call relations**: `WritebackPoller._deliver_claimed` raises this so `_deliver` can log and retry with phase detail.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 5067–5100)

```
async def run(self) -> None
```

**Purpose**: Continuously drains terminal writebacks in the background. It keeps a bounded number of workspace drain tasks in flight.

**Data flow**: It loops, cleans up finished workspace tasks, fetches more candidate workspaces when capacity allows, starts drain tasks, logs failures, sleeps between polls, and cancels in-flight tasks on shutdown.

**Call relations**: The application runs this for durable surfaces; it calls `_drain_workspace` for each workspace.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 5102–5111)

```
async def drain(self) -> None
```

**Purpose**: Runs one bounded drain pass for due terminal writebacks. This is useful for tests or manual flushing.

**Data flow**: It gets candidate workspaces, creates a semaphore, drains them concurrently, collects exceptions, and raises an exception group if any drains failed.

**Call relations**: It shares `_drain_workspace` with the continuous `run` loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 5113–5131)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers a batch of writebacks for one workspace under a concurrency limit.

**Data flow**: It enters workspace scope, claims rows, starts lease-renewal tasks for each, delivers each row, then cancels and joins renewal tasks.

**Call relations**: `run` and `drain` call this; it coordinates `_claim`, `_renew_claim`, and `_deliver`.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 5133–5166)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due terminal writebacks for this worker. A claim prevents another worker from delivering the same row at the same time.

**Data flow**: It receives workspace id, selects due writeback turn ids, updates them to claimed with worker id and expiry, and returns claimed rows with reply references and last errors.

**Call relations**: `_drain_workspace` calls this before starting delivery.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 5168–5200)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed terminal writeback and records success or retry/failure. It logs the outcome and elapsed time.

**Data flow**: It receives workspace id, turn id, existing reply ref, and renewal task. It calls `_deliver_with_lease`; on claim loss it logs, on delivery failure it asks `_fail_or_retry`, otherwise logs delivery.

**Call relations**: `_drain_workspace` calls this for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 5202–5231)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while the claim-renewal task is alive, then marks the row delivered. It stops renewal before the final commit.

**Data flow**: It starts `_deliver_claimed`, waits for delivery or renewal failure, cancels the loser, waits for cleanup, and then calls `_mark_delivered`.

**Call relations**: `_deliver` calls this as the protected delivery section.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 5233–5263)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Calls the destination surface to post a terminal reply and attach files. It records the post reference before attachments.

**Data flow**: It builds the writeback, finds the surface spec, skips if no delivery handlers, creates a context, posts if no reply ref exists, records the reply ref, and calls attach.

**Call relations**: `_deliver_with_lease` calls this; it uses `_build` and `_record_ref`, and wraps post/attach errors as `_WritebackDeliveryFailed`.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 5265–5268)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Refreshes a writeback claim repeatedly while delivery is in progress.

**Data flow**: It receives a turn id, sleeps for the refresh interval, and calls `_refresh_claim` forever until cancelled or failed.

**Call relations**: `_drain_workspace` starts one renewal task per claimed writeback.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 5270–5286)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends this worker’s lease on a claimed writeback row.

**Data flow**: It receives a turn id, updates the claim expiry if the row is still claimed by this worker, and raises claim-lost if no row was updated.

**Call relations**: `_renew_claim` calls this on each refresh tick.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 5288–5340)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the `Writeback` object handed to a durable surface. It gathers terminal frame, conversation key, surface name, and shared files.

**Data flow**: It receives a turn id, queries the turn and conversation, reads shared artifacts, validates the terminal frame, and returns the writeback plus surface name.

**Call relations**: `_deliver_claimed` calls this before selecting the surface handler.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 5342–5354)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Stores the provider’s reply reference after posting. This prevents a crash during attachments from reposting the main reply.

**Data flow**: It receives turn id and reply reference, updates the claimed writeback row if still owned by this worker, and raises claim-lost if not.

**Call relations**: `_deliver_claimed` calls this between post and attach.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 5356–5373)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a claimed terminal writeback as delivered and clears claim fields.

**Data flow**: It receives a turn id, updates the claimed row to delivered if still owned by this worker, and raises claim-lost if the compare-and-swap fails.

**Call relations**: `_deliver_with_lease` calls this after external delivery completes.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 5375–5424)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed writeback for retry or marks it permanently failed after it ages out.

**Data flow**: It receives turn id and delivery failure, computes retry delay from provider retry-after or fixed backoff, caps stored error text, updates the row to pending or failed, and returns outcome details.

**Call relations**: `_deliver` calls this after `_deliver_with_lease` raises `_WritebackDeliveryFailed`.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `mid_turn_reply_workspaces`  (lines 5427–5458)

```
def mid_turn_reply_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace candidate reader for deliverable mid-turn replies. Mid-turn replies are messages sent before the final turn answer.

**Data flow**: It initializes a cursor, wraps a due-query builder in an owner-scoped candidate reader, and returns the async candidate function.

**Call relations**: `MidTurnReplyPoller` receives this candidate reader at construction.

*Call graph*: 1 external calls (owner_candidates).


##### `mid_turn_reply_workspaces.due`  (lines 5433–5444)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query of workspace ids that have due mid-turn replies.

**Data flow**: It gets the current time, selects workspace ids from due mid-turn rows, groups and orders them, applies the cursor if present, and returns the SQL query.

**Call relations**: The candidate reader inside `mid_turn_reply_workspaces` calls this.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); 2 external calls (now, select).


##### `mid_turn_reply_workspaces.candidates`  (lines 5448–5456)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with deliverable mid-turn replies.

**Data flow**: It runs the due reader, wraps to the beginning when needed, updates the cursor to the last returned workspace id, and returns the ids.

**Call relations**: `MidTurnReplyPoller.run` calls this through `drain`.


##### `_mid_turn_reply_due`  (lines 5461–5474)

```
def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the condition for mid-turn reply rows that can be claimed. These do not wait for the turn to finish.

**Data flow**: It receives the current time and returns a SQL condition matching pending rows with no/live-expired claim or claimed rows whose lease expired.

**Call relations**: `mid_turn_reply_workspaces.due` and `MidTurnReplyPoller._claim` use this.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `MidTurnReplyPoller.run`  (lines 5502–5508)

```
async def run(self) -> None
```

**Purpose**: Continuously delivers due mid-turn replies in the background.

**Data flow**: It loops forever, calls `drain`, logs drain errors, and sleeps between polls.

**Call relations**: The application runs this for durable surfaces that support before-terminal messages.

*Call graph*: calls 1 internal fn (drain); 2 external calls (sleep, log).


##### `MidTurnReplyPoller.drain`  (lines 5510–5528)

```
async def drain(self) -> None
```

**Purpose**: Runs one drain pass for mid-turn replies across candidate workspaces.

**Data flow**: It gets candidate workspace ids, enters each workspace scope, claims rows, starts renewal tasks, delivers rows in order, and cancels renewals afterward.

**Call relations**: `run` calls this each poll; it coordinates `_claim`, `_renew_claim`, and `_deliver`.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 1 (run); 4 external calls (create_task, gather, log, ws).


##### `MidTurnReplyPoller._claim`  (lines 5530–5571)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due mid-turn replies for this worker in delivery order.

**Data flow**: It receives workspace id, selects due reply ids ordered by creation and span order, updates them to claimed, and returns sorted claimed rows.

**Call relations**: `drain` calls this before delivery.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); called by 1 (drain); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `MidTurnReplyPoller._deliver`  (lines 5573–5598)

```
async def _deliver(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed mid-turn reply and records success or retry/failure.

**Data flow**: It receives workspace id, claimed row, and renewal task. It calls `_deliver_with_lease`, handles claim loss or delivery errors, updates retry/failure state, and logs the outcome.

**Call relations**: `drain` calls this for each claimed reply.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (drain); 2 external calls (now, log).


##### `MidTurnReplyPoller._deliver_with_lease`  (lines 5600–5621)

```
async def _deliver_with_lease(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Sends one mid-turn reply while its claim is being renewed, then marks it delivered.

**Data flow**: It starts `_speak`, waits for send completion or renewal failure, cleans up tasks, then calls `_mark_delivered` with the reply reference.

**Call relations**: `_deliver` calls this as the protected send section.

*Call graph*: calls 2 internal fn (_mark_delivered, _speak); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `MidTurnReplyPoller._speak`  (lines 5623–5665)

```
async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None
```

**Purpose**: Calls the surface’s mid-turn send handler, or completes silently if no handler exists. It reuses an existing reply reference to avoid duplicate posts.

**Data flow**: It receives workspace id and reply row. If a reply ref already exists it returns it; otherwise it reads turn/conversation routing data, finds the surface spec, builds `MidTurnReply`, and calls `speak` when available.

**Call relations**: `_deliver_with_lease` calls this; durable surfaces implement `speak` to post progress messages or comment notices.

*Call graph*: called by 1 (_deliver_with_lease); 4 external calls (__init__, select, workspace_tx, log).


##### `MidTurnReplyPoller._renew_claim`  (lines 5667–5670)

```
async def _renew_claim(self, reply_id: UUID) -> None
```

**Purpose**: Refreshes a mid-turn reply claim repeatedly while delivery waits.

**Data flow**: It receives a reply id, sleeps for the refresh interval, and calls `_refresh_claim` until cancelled or failed.

**Call relations**: `drain` starts one renewal task per claimed reply.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (drain); 1 external calls (sleep).


##### `MidTurnReplyPoller._refresh_claim`  (lines 5672–5688)

```
async def _refresh_claim(self, reply_id: UUID) -> None
```

**Purpose**: Extends this worker’s lease on a claimed mid-turn reply row.

**Data flow**: It receives a reply id, updates the claim expiry if the row is still claimed by this worker, and raises claim-lost if not.

**Call relations**: `_renew_claim` calls this on each refresh tick.

*Call graph*: called by 1 (_renew_claim); 4 external calls (now, timedelta, update, workspace_tx).


##### `MidTurnReplyPoller._mark_delivered`  (lines 5690–5708)

```
async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Marks a mid-turn reply row as delivered and stores its provider reply reference if any.

**Data flow**: It receives reply id and optional reply ref, updates the claimed row to delivered and clears claim fields, and raises claim-lost if this worker no longer owns it.

**Call relations**: `_deliver_with_lease` calls this after `_speak` finishes.

*Call graph*: called by 1 (_deliver_with_lease); 2 external calls (update, workspace_tx).


##### `MidTurnReplyPoller._fail_or_retry`  (lines 5710–5757)

```
async def _fail_or_retry(self, reply_id: UUID, error: Exception) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed mid-turn reply for retry or marks it failed after the delivery window expires.

**Data flow**: It receives reply id and error, computes retry delay, stores a shortened error message, updates the row to pending or failed, and returns outcome, error text, and next attempt time.

**Call relations**: `_deliver` calls this when sending a mid-turn reply fails.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### `core/src/ufo/runtime/hub.py`

`io_transport` · `cross-cutting during live turn execution and surface streaming`

This file is like a small in-memory radio station for each running turn. The worker publishes frames, which are small updates such as text chunks, tool activity, cost ticks, delivered replies, or a final terminal frame. Any surface, such as a CLI or web page, can subscribe and receive those frames live.

The important problem is disconnection. A terminal client may briefly drop and reconnect, especially around operations handed to the user’s machine. To avoid losing a frame during that gap, the hub keeps a bounded ring buffer, meaning a fixed-size “last N messages” memory. Subscribers carry a cursor, which is just a per-turn sequence number. When they reconnect, the hub replays only frames after that cursor.

Publishing never waits for slow subscribers. Each subscriber has a limited queue; if it fills, the oldest queued frame is dropped so the producer can keep moving. Shared state is protected by a lock because publishers and subscribers may run on different asyncio event loops, sometimes even on different threads. Terminal and parked frames end a stream, and the hub frees memory when it is safe. Subagent activity is treated specially: it is only mirrored onto an already-running root stream, never used to recreate a finished one.

#### Function details

##### `Hub.publish`  (lines 151–151)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This is the interface contract for adding one live frame to a turn’s stream. Code that only knows about the abstract hub can call this without caring whether the hub is in memory, Redis-backed, or supplied by an extension.

**Data flow**: A caller provides a turn id and a frame. A concrete hub implementation appends that frame, assigns or returns a cursor for it, and sends it to any live subscribers. The caller receives the cursor string for later resume or replay use.

**Call relations**: The queue code calls this contract when it needs to publish a failed terminal result. In this file, InProcessHub.publish is the concrete version that actually stores and fans out the frame.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 153–153)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This is the interface contract for watching a turn’s live stream. It lets a surface ask for frames after a cursor, so a reconnect can pick up where it left off.

**Data flow**: A caller provides a turn id and optionally the last cursor it has already seen. A concrete hub first yields replayed frames after that cursor, then continues yielding new frames as they arrive. The output is an asynchronous stream of cursor-and-frame pairs.

**Call relations**: The hub tailing code calls this when it pumps frames to a surface. InProcessHub.subscribe supplies the in-memory behavior behind this contract.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 155–155)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface contract for asking whether a saved cursor is still covered by the hub’s replay memory. A surface uses this to decide whether it can resume cleanly or must redraw from durable state.

**Data flow**: A caller gives a turn id and a cursor. A concrete hub checks whether its retained replay buffer still reaches back far enough. It returns true when replay can bridge the gap, and false when the cursor is empty, missing, or too old.

**Call relations**: The tailing flow calls this before deciding how to resume a stream. InProcessHub.covers is the concrete in-memory check.

*Call graph*: called by 1 (tail_frames).


##### `Hub.latest_activity`  (lines 157–157)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This is the interface contract for asking what a running turn appears to be doing right now, without opening a full subscription. It is used for lightweight status reads.

**Data flow**: A caller provides a turn id. A concrete hub looks at recent retained frames for that turn and returns the newest Activity frame if one is still meaningful, otherwise it returns nothing.

**Call relations**: No caller is shown in the provided graph, but this method is part of the hub contract. InProcessHub.latest_activity implements it by peeking into the replay buffer.


##### `_offer`  (lines 160–163)

```
def _offer(queue: asyncio.Queue[tuple[str, HubFrame]], item: tuple[str, HubFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber’s queue without ever blocking the publisher. If the subscriber is too slow and its queue is full, it discards the oldest queued frame to make room.

**Data flow**: It receives a subscriber queue and a cursor-frame item. It checks whether the queue is full; if so, it removes one old item. Then it inserts the new item, changing only that queue and returning nothing.

**Call relations**: InProcessHub.publish schedules this helper onto each subscriber’s event loop. That handoff is important because subscribers may live on a different asyncio event loop or thread from the publisher.


##### `InProcessHub._stream`  (lines 210–220)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This private helper finds or creates the live state for one turn. It is the place where a turn gets its replay buffer, subscriber list, and next cursor sequence.

**Data flow**: It receives a turn id and reads the hub’s dictionaries while the caller is holding the lock. If a stream already exists, it returns it. If not, it creates a new _TurnStream with a bounded deque as the replay ring and starts its sequence from the remembered mark for that turn, then stores and returns it.

**Call relations**: InProcessHub.publish calls this when a frame needs somewhere to go. InProcessHub.subscribe calls it when a subscriber starts watching a turn, so replay and live delivery share the same per-turn state.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 222–241)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This is the in-memory implementation for publishing one frame to a turn. It records the frame for possible replay, gives it a cursor, and fans it out to current subscribers without waiting for them.

**Data flow**: It receives a turn id and a frame. Under a lock, it may ignore subagent activity if the root stream does not exist or has ended; otherwise it gets the turn stream, increments the sequence number, appends the frame to the replay buffer, snapshots the current subscribers, and marks terminal or parked endings. After releasing the lock, it schedules delivery to each subscriber queue and returns the cursor string.

**Call relations**: This method fulfills the Hub.publish contract. It relies on InProcessHub._stream to create or fetch per-turn state, and it hands each live delivery to _offer through the subscriber’s event loop so cross-thread publishing stays safe.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 243–270)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This is the in-memory implementation for following a turn’s stream. It first replays remembered frames after a cursor, then waits for new live frames.

**Data flow**: It receives a turn id and optional cursor. It creates a bounded queue for live frames and records the current asyncio event loop. Under the lock, it registers that queue as a subscriber and takes a snapshot of buffered frames newer than the cursor. It yields the replay snapshot first, then yields items from the live queue until the subscriber stops. On exit, it unregisters the queue and may delete finished or empty stream state.

**Call relations**: This method fulfills the Hub.subscribe contract used by the surface tail pump. It calls InProcessHub._stream to share the same state used by publishers, and its locking rules are arranged so a frame is either included in replay or delivered live, not both and not neither.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 272–280)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still contains enough history for a client’s cursor. It helps decide whether reconnecting can be seamless.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, or the turn has no retained buffer, it returns false. Otherwise it compares the oldest retained cursor with the requested cursor and returns true when the retained history starts at or before that point.

**Call relations**: This method fulfills the Hub.covers contract used by the surface tailing flow. It does not subscribe or publish; it only reads the current replay ring under the lock.


##### `InProcessHub.latest_activity`  (lines 282–299)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This returns the most recent short activity summary for a running turn, if one is still present near the end of the replay buffer. It gives status screens a cheap way to say what the turn is doing now.

**Data flow**: It receives a turn id. Under the lock, it finds that turn’s stream and scans backward through only a limited number of recent retained frames. If it finds an Activity frame, it returns it; if the stream is gone or no recent Activity appears, it returns none.

**Call relations**: This method fulfills the Hub.latest_activity contract. It uses a bounded backward peek rather than scanning the whole replay ring, because status polling may happen often and long text streams should not make every poll expensive.

*Call graph*: 1 external calls (islice).


### `core/src/ufo/runtime/surfaces/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can act like a named module when it contains an `__init__.py` file. That lets the rest of the project refer to this folder with imports such as `ufo.runtime.surfaces...` instead of treating it as an ordinary directory. Think of it like a label on a drawer: the label does not contain the tools, but it tells Python that the drawer is part of the organized toolbox.

Because the file is empty, it does not run setup code, expose shortcuts, or create shared objects. Its value is structural. Without it, depending on the Python version and packaging setup, imports from this folder could fail or behave differently. Keeping it present makes the package layout explicit and predictable.


### iMessage bridge
The iMessage extension selects a hosted or development provider, receives incoming messages, and sends UFO replies and files back to iMessage.

### `extensions/imessage/ufo_ext_imessage/cloud.py`

`io_transport` · `startup, request handling, and live message streaming`

This file is the bridge between the app and iMessage delivery. The app itself does not talk directly to Apple’s iMessage network. Instead, in production it signs in to Spectrum Cloud using a project id and secret, gets a short-lived line token, and then uses that token to send messages, receive message events, and upload or download attachments.

The main object is SpectrumProject, which represents one configured Spectrum project. It keeps a cached token so the app does not ask Spectrum for a new one before every message. Like a temporary building pass, the token is reused until it is close to expiring, then refreshed. The class also opens secure gRPC channels. gRPC is a network calling system where code calls remote services almost like local methods.

The file also has startup-style helpers that inspect environment variables. If the Spectrum credentials are present, the iMessage provider is SpectrumProject. If this is a plain local development setup, it uses LocalLine instead. Otherwise it raises ProviderNotConfigured with instructions.

Incoming events are filtered carefully. The code ignores system, spam, service, corrupt, outgoing, empty, hidden sticker, or senderless messages, and turns valid incoming messages into the project’s simpler InboundMessage shape.

#### Function details

##### `SpectrumProject.installation_id`  (lines 94–95)

```
def installation_id(self) -> str
```

**Purpose**: Returns a stable name for this Spectrum installation. Other parts of the system can use it to identify which iMessage provider instance they are talking to.

**Data flow**: It reads the project id stored on the SpectrumProject and prefixes it with "project:". The result is a simple string; nothing else changes.

**Call relations**: This is a small identity helper on SpectrumProject. It does not call out to the network or hand work to other functions.


##### `SpectrumProject.line`  (lines 97–118)

```
async def line(self) -> SpectrumLine
```

**Purpose**: Gets the shared Spectrum iMessage line token needed to call the remote iMessage services. It reuses a still-valid token and only asks Spectrum Cloud for a new one when needed.

**Data flow**: It starts with the project’s cached token state for the current async event loop. If the cached SpectrumLine exists and is not close to expiry, it returns it. Otherwise it sends a cloud request for a new shared token, checks that the response has the expected shape, stores the new token and expiry time, and returns a SpectrumLine. If Spectrum refuses or sends malformed data, it raises SpectrumCloudError.

**Call relations**: The sending, receiving, attachment, and catch-up methods all call this first because every gRPC request needs a bearer token. It uses _loop to find the right per-event-loop state and _request to talk to Spectrum Cloud.

*Call graph*: calls 2 internal fn (_loop, _request); called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 4 external calls (__init__, __init__, TypeAdapter, monotonic).


##### `SpectrumProject._loop`  (lines 120–136)

```
def _loop(self) -> SpectrumLoop
```

**Purpose**: Finds or creates the HTTP client, lock, and token cache that belong to the currently running async event loop. This prevents async objects created for one loop from being reused unsafely in another.

**Data flow**: It reads the current asyncio event loop and checks a protected dictionary of loop-specific state. If state already exists, it returns it. If not, it creates a SpectrumLoop with an HTTP client, an async lock, and a token cache, stores it, and returns it.

**Call relations**: line, _request, and invalidate call this whenever they need loop-local state. It is the quiet plumbing that lets the same SpectrumProject object work across different async loops without sharing the wrong client or lock.

*Call graph*: called by 3 (_request, invalidate, line); 4 external calls (__init__, Lock, get_running_loop, AsyncClient).


##### `SpectrumProject.assign_line`  (lines 138–168)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: Makes sure a phone number is registered with Spectrum and returns the shared phone number Spectrum assigned for messaging it. This matters because a shared iMessage line cannot start a conversation with a phone that has never contacted it.

**Data flow**: It takes a member phone number and an idempotency key, which is a safety label that lets repeated requests avoid creating duplicates. It first fetches the existing Spectrum users for the project. If the phone number is already registered, it returns the existing assigned number. If not, it sends a registration request and returns the assigned number from the created user. Bad or rejected responses become SpectrumCloudError.

**Call relations**: This uses _request for Spectrum Cloud HTTP calls. It is separate from the gRPC send and receive methods because it configures a user relationship in Spectrum Cloud rather than sending an individual message.

*Call graph*: calls 1 internal fn (_request); 2 external calls (__init__, TypeAdapter).


##### `SpectrumProject._request`  (lines 170–193)

```
async def _request(self, method: str, path: str, *, json: dict[str, str] | None=None, idempotency_key: str | None=None) -> object
```

**Purpose**: Sends an authenticated HTTP request to Spectrum Cloud and returns the JSON body. It centralizes the project-id/project-secret sign-in and converts HTTP failures into a project-specific error.

**Data flow**: It receives an HTTP method, a path, optional JSON data, and an optional idempotency key. It builds headers, signs the request with basic authentication using the project id and secret, sends it through the loop-local HTTP client, checks for HTTP error status codes, and returns the parsed JSON response. If the server reports an HTTP error, it raises SpectrumCloudError.

**Call relations**: line uses this to fetch shared tokens, and assign_line uses it to list or register users. It relies on _loop to get the correct HTTP client for the active async loop.

*Call graph*: calls 1 internal fn (_loop); called by 2 (assign_line, line); 2 external calls (__init__, BasicAuth).


##### `SpectrumProject.channel`  (lines 195–196)

```
def channel(self) -> grpc.aio.Channel
```

**Purpose**: Creates a secure gRPC channel to Spectrum’s iMessage service. A channel is the network connection object used by generated service clients to call remote methods.

**Data flow**: It uses the fixed Spectrum iMessage server address and SSL credentials, which means the connection is encrypted. It returns a gRPC async channel and does not store it.

**Call relations**: The catch-up, subscribe, send_text, send_attachment, and download_attachment methods call this when they need to contact Spectrum’s live iMessage services. Those methods then attach service-specific stubs to the channel.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 1 external calls (ssl_channel_credentials).


##### `SpectrumProject.invalidate`  (lines 198–201)

```
async def invalidate(self) -> None
```

**Purpose**: Forgets the cached Spectrum line token. This is useful after an authentication problem or any situation where the next call should force a fresh token.

**Data flow**: It finds the token state for the current async loop, takes the async lock so no other task changes it at the same time, and clears the stored values. It returns nothing.

**Call relations**: This uses _loop to find the right cache. It supports recovery flows elsewhere in the provider system by making the next call to line fetch a new token.

*Call graph*: calls 1 internal fn (_loop).


##### `SpectrumProject.invalid_cursor`  (lines 203–207)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: Recognizes a specific remote error meaning an event cursor is invalid. A cursor is a saved position in a stream, used to resume from the right place.

**Data flow**: It receives an exception and checks whether it is a gRPC async error with the INVALID_ARGUMENT status code. It returns true for that case and false otherwise.

**Call relations**: This is a classification helper for higher-level retry or resync logic. It does not call Spectrum; it only interprets an error object produced by gRPC.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.external_error`  (lines 209–210)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: Tells the rest of the system whether an exception came from Spectrum or the network rather than from local application logic.

**Data flow**: It receives an exception and checks whether it is a gRPC error, a SpectrumCloudError, or an HTTPX network/HTTP error. It returns a boolean and changes nothing.

**Call relations**: This is used as an error-labeling helper by provider orchestration code. It groups together the kinds of failures that mean the outside service or network had trouble.


##### `SpectrumProject.error_code`  (lines 212–215)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: Turns an exception into a short error label suitable for logging, metrics, or user-facing diagnostics.

**Data flow**: It receives an exception. If it is a gRPC async error, it returns the gRPC status name, such as INVALID_ARGUMENT. For other errors, it returns the Python exception class name.

**Call relations**: This complements external_error and invalid_cursor. Higher-level code can use it to describe a failure without needing to know all the exception types.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.catch_up`  (lines 217–238)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Reads missed iMessage events from Spectrum after a saved sequence number. This lets the app recover messages that arrived while it was offline or disconnected.

**Data flow**: It receives an optional sequence number. It gets a line token, builds a catch-up request, includes the sequence if one was provided, opens a secure gRPC channel, and streams frames from Spectrum. Completion frames become ProviderEvent values with a head sequence. Message-change frames are filtered through _inbound_message and yielded as ProviderEvent values.

**Call relations**: This starts by calling line for authentication and channel for the network connection. It uses rpc_metadata to attach the token to the gRPC call, and _inbound_message to convert raw Spectrum protobuf events into the simpler message form used by the provider layer.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 3 external calls (__init__, CatchUpEventsRequest, EventServiceStub).


##### `SpectrumProject.subscribe`  (lines 240–256)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Listens for new live iMessage events from Spectrum. It is the ongoing stream used while the app is running and waiting for incoming messages.

**Data flow**: It receives an asyncio Event named ready, gets a line token, creates a subscribe request, opens a secure gRPC channel, and starts the remote stream. Once the stream is established, it sets ready to signal that listening has begun. For each incoming frame, it yields a ProviderEvent with an optional sequence and, when present and valid, an inbound message.

**Call relations**: Like catch_up, it depends on line, channel, rpc_metadata, and _inbound_message. It is the live counterpart to catch_up: catch_up fills gaps, while subscribe keeps watching for what happens next.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 4 external calls (__init__, set, SubscribeMessageEventsRequest, MessageServiceStub).


##### `SpectrumProject.send_text`  (lines 258–271)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: Sends a plain text iMessage through Spectrum and returns the id of the message that Spectrum created.

**Data flow**: It receives a conversation id, text, and an idempotency key. It gets a line token, builds a send-text request using the conversation id and text, opens a secure gRPC channel, sends the request with authentication metadata and the idempotency key, and returns the remote message guid from the response.

**Call relations**: This is one of the main outgoing-message paths. It calls line before sending, channel to reach Spectrum, and rpc_metadata so Spectrum can authenticate the request and safely recognize retries.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (SendTextMessageRequest, MessageServiceStub).


##### `SpectrumProject.send_attachment`  (lines 273–300)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: Sends a file attachment as an iMessage. It first uploads the file to Spectrum, then sends a message that points at the uploaded attachment.

**Data flow**: It receives a conversation id, filename, file bytes, and an idempotency key. It gets a line token and opens a secure gRPC channel. First it uploads the attachment bytes and receives an attachment guid. Then it sends an attachment message to the conversation using that guid. It returns the guid of the final sent message.

**Call relations**: This chains two Spectrum services together: the attachment service for upload, then the message service for sending. It uses line, channel, and rpc_metadata for the same authentication and retry-safety pattern used by send_text.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 4 external calls (UploadAttachmentRequest, AttachmentServiceStub, SendAttachmentMessageRequest, MessageServiceStub).


##### `SpectrumProject.download_attachment`  (lines 302–312)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: Downloads an attachment from Spectrum in chunks. Streaming chunks avoids needing to hold the whole file in memory at once.

**Data flow**: It receives an attachment id, gets a line token, opens a secure gRPC channel, and starts a download request. As Spectrum sends frames, it yields only the primary file chunks as bytes. The caller receives a stream of byte pieces rather than one finished file object.

**Call relations**: This is the inbound-file counterpart to send_attachment. It calls line for a token, channel for the gRPC connection, and rpc_metadata to authorize the download request.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (DownloadAttachmentRequest, AttachmentServiceStub).


##### `_spectrum_pair`  (lines 321–326)

```
def _spectrum_pair() -> tuple[str, str] | None
```

**Purpose**: Looks for the Spectrum project id and secret in the deployment environment. It answers the basic question: can this process sign in to Spectrum Cloud?

**Data flow**: It asks deploy_env for the project id and project secret. If either value is missing or empty, it returns None. If both are present, it returns them as a pair of strings.

**Call relations**: spectrum_configured and spectrum_project call this. It is the shared low-level configuration check used before deciding whether Spectrum can be used.

*Call graph*: called by 2 (spectrum_configured, spectrum_project); 1 external calls (deploy_env).


##### `spectrum_configured`  (lines 329–331)

```
def spectrum_configured() -> bool
```

**Purpose**: Reports whether Spectrum credentials are available. This is a simple yes-or-no check for production iMessage support.

**Data flow**: It calls _spectrum_pair. If a project id and secret are present, it returns true; otherwise it returns false.

**Call relations**: imessage_offered and line_provider use this to decide whether Spectrum Cloud should be offered or selected.

*Call graph*: calls 1 internal fn (_spectrum_pair); called by 2 (imessage_offered, line_provider).


##### `imessage_offered`  (lines 334–337)

```
def imessage_offered(public_base_url: str | None) -> bool
```

**Purpose**: Decides whether this deployment should offer iMessage connection at all. It allows either real Spectrum credentials or a plain local development mode.

**Data flow**: It receives the public base URL, checks whether Spectrum is configured, and also checks whether the URL represents a plain local development setup. It returns true if either path is available, false otherwise.

**Call relations**: This is a feature-availability helper. It calls spectrum_configured for production setup and plain_local for the local development exception.

*Call graph*: calls 1 internal fn (spectrum_configured); 1 external calls (plain_local).


##### `line_provider`  (lines 340–347)

```
def line_provider(public_base_url: str | None) -> MessageProvider
```

**Purpose**: Chooses the actual iMessage provider object for this deployment. It returns Spectrum in production, LocalLine in plain local development, or a clear configuration error anywhere else.

**Data flow**: It receives the public base URL. If Spectrum credentials exist, it returns the cached SpectrumProject. If not, but the deployment is plain local, it creates and returns a LocalLine. If neither condition is true, it raises ProviderNotConfigured with instructions about the missing environment variables.

**Call relations**: This is the main selection point for callers that need a MessageProvider. It calls spectrum_configured and spectrum_project for the cloud path, plain_local and LocalLine for the development path, and ProviderNotConfigured for the refusal path.

*Call graph*: calls 2 internal fn (spectrum_configured, spectrum_project); 3 external calls (__init__, __init__, plain_local).


##### `spectrum_project`  (lines 351–362)

```
def spectrum_project() -> SpectrumProject
```

**Purpose**: Builds and caches the SpectrumProject object for this process. Caching means all callers share the same configured provider instead of creating a new one every time.

**Data flow**: It reads the Spectrum credential pair. If the pair is missing, it raises ProviderNotConfigured. If present, it creates a SpectrumProject with the project id, project secret, an async HTTP client, an async lock, and an empty token cache, then returns it. Because it is cached, later calls return the same object.

**Call relations**: line_provider calls this when Spectrum is configured. The returned SpectrumProject is then used for token fetching, message streaming, sending, and attachment transfer.

*Call graph*: calls 1 internal fn (_spectrum_pair); called by 1 (line_provider); 4 external calls (__init__, __init__, Lock, AsyncClient).


##### `rpc_metadata`  (lines 365–369)

```
def rpc_metadata(token: str, idempotency_key: str | None=None) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the small set of headers sent with Spectrum gRPC calls. These headers carry the bearer token, and sometimes an idempotency key for safe retries.

**Data flow**: It receives a token and optionally an idempotency key. It always creates an authorization header in the form "Bearer <token>". If an idempotency key is provided, it adds an x-idempotency-key header. It returns the headers as a tuple.

**Call relations**: All Spectrum gRPC operations call this before making remote requests: catch_up, subscribe, send_text, send_attachment, and download_attachment. It is the common packaging step for authentication metadata.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe).


##### `_inbound_message`  (lines 372–410)

```
def _inbound_message(event: object) -> InboundMessage | None
```

**Purpose**: Converts a raw Spectrum message-change event into the app’s simpler InboundMessage object, but only if it is a real incoming message worth processing.

**Data flow**: It receives an event object. If the object is not the expected message-change type, is not a received-message change, came from this account, has no sender, is a system/service/spam/corrupt message, or has neither text nor usable attachments, it returns None. Otherwise it gathers the sender, text, visible non-sticker attachments, conversation id, message id, and whether the chat looks direct, then returns an InboundMessage.

**Call relations**: catch_up and subscribe call this for raw message events coming from Spectrum streams. It is the filter and translator between Spectrum’s detailed protocol objects and the provider layer’s clean inbound-message shape.

*Call graph*: called by 2 (catch_up, subscribe); 2 external calls (__init__, __init__).


### `extensions/imessage/ufo_ext_imessage/surface.py`

`io_transport` · `main loop and message handling`

This file is the doorway between Apple-style chat messages and the rest of UFO. Without it, a text sent to the shared iMessage line would never become a UFO turn, and UFO’s answers would have no way to get back to the chat.

The file has two main jobs. First, it listens to a message provider, which is the lower-level service that knows how to read and send iMessages. It keeps a cursor, meaning a saved bookmark in the provider’s event stream, so messages are not processed twice and missed messages can be caught up after a reconnect. If the provider disconnects, the listener waits briefly, resets bad cursors when needed, and tries again.

Second, it decides what to do with each message. A phone number must be claimed and proved before its messages are accepted. Proving works by sending a one-time code in a direct message. Once proved, direct messages go to that member’s conversation, while group chats go to a room-like conversation. Attachments are downloaded into the workspace if they are small enough; oversized or unavailable files are reported in the message text instead.

For outgoing traffic, the file formats final replies, mid-turn replies, questions, connection links, credential prompts, and shared artifacts so they make sense inside iMessage.

#### Function details

##### `read_claim`  (lines 63–73)

```
def read_claim(stored: object) -> PendingClaim | None
```

**Purpose**: This reads a stored pending phone-number claim and checks that it still has the expected shape. If the stored data is missing or malformed, it quietly treats it as unusable instead of stopping all incoming message processing.

**Data flow**: It receives a raw stored value. If the value is empty, it returns nothing. If the value can be validated as a pending claim, it returns that claim; otherwise it logs that the claim could not be read and returns nothing.

**Call relations**: During phone-number proof, ImessageSurface._prove asks this helper to interpret the saved claim record. If the record cannot be trusted, proof simply does not continue.

*Call graph*: called by 1 (_prove); 1 external calls (log).


##### `MessageStreamDisconnected.__init__`  (lines 87–90)

```
def __init__(self, cursor: int | None, error: Exception) -> None
```

**Purpose**: This creates a clear error object for the case where the live iMessage stream has dropped. It keeps both the last known stream cursor and the original error, so reconnect logic can decide where to resume.

**Data flow**: It receives a cursor and an underlying exception. It stores both on the object and sets the human-readable error message to the original exception’s text.

**Call relations**: ImessageSurface._consume_connected raises this when catch-up, live reading, or message processing fails because of an outside provider problem. ImessageSurface.listen catches it and performs the reconnect path.

*Call graph*: called by 1 (_consume_connected).


##### `contact_card`  (lines 103–115)

```
def contact_card(assigned_phone_number: str) -> bytes
```

**Purpose**: This builds a small contact card for the assigned iMessage phone line. Sending it helps the member save the line as a known contact, which can remove iMessage’s “Report Junk” warning.

**Data flow**: It receives the assigned phone number. It places that number into a vCard, which is a standard contact-card text format, and returns the card as bytes ready to upload.

**Call relations**: ImessageSurface._send_contact_card calls this after a member successfully proves a phone number, then sends the resulting card through the provider.

*Call graph*: called by 1 (_send_contact_card).


##### `claim_key`  (lines 118–119)

```
def claim_key(member_id: UUID, phone_number: str) -> str
```

**Purpose**: This creates the storage key used for a member’s pending claim on a phone number. The key is hashed so the raw member-and-phone pairing is not placed directly into the store key.

**Data flow**: It receives a member ID and a phone number. It combines them, hashes the combination with SHA-256, and returns a namespaced key string.

**Call relations**: ImessageSurface._prove uses this key to find and later delete the saved opt-in code for the member and phone number being proved.

*Call graph*: called by 1 (_prove); 1 external calls (sha256).


##### `queue_key`  (lines 122–123)

```
def queue_key(conversation_id: str, *, direct: bool) -> str
```

**Purpose**: This makes a stable conversation key for an iMessage chat. It records both the provider’s conversation ID and whether the chat is direct or group, because those are treated differently.

**Data flow**: It receives a conversation ID and a direct-or-group flag. It turns them into a compact JSON string that can later be stored as the queue key for outgoing replies.

**Call relations**: ImessageSurface._admit_message uses this when it creates or finds the UFO conversation that should receive an incoming iMessage.

*Call graph*: called by 1 (_admit_message); 1 external calls (dumps).


##### `conversation_from_queue`  (lines 126–134)

```
def conversation_from_queue(queue: str) -> ConversationAddress
```

**Purpose**: This reverses queue_key: it reads a saved queue key and recovers the iMessage conversation address. It protects outgoing sends from malformed queue keys by rejecting anything that is not a valid direct or group conversation record.

**Data flow**: It receives a queue-key string. It parses the JSON, checks whether it names a non-empty direct or group conversation, and returns a ConversationAddress with the ID and direct/group flag. Invalid data raises an error.

**Call relations**: ImessageSurface.post, ImessageSurface.speak, and ImessageSurface.attach all call this before sending text or files, so they know which iMessage conversation to send to.

*Call graph*: called by 3 (attach, post, speak); 2 external calls (__init__, loads).


##### `_attachment_content`  (lines 137–138)

```
async def _attachment_content(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: This wraps a downloaded attachment’s bytes in the streaming shape expected by the workspace file writer. It is a tiny adapter between “all bytes in memory” and “async chunks.”

**Data flow**: It receives bytes for a file. When read, it yields those bytes once and then finishes.

**Call relations**: ImessageSurface._downloaded_files uses this after downloading an attachment, handing it to the workspace file-writing API.

*Call graph*: called by 1 (_downloaded_files).


##### `ImessageSurface.listen`  (lines 145–169)

```
async def listen(self, context: SurfaceListenerContext) -> None
```

**Purpose**: This is the long-running listener for incoming iMessage events. It keeps the connection alive, resumes from the saved cursor, and reconnects after provider-side failures.

**Data flow**: It receives a listener context that can provide public URLs, saved cursors, address routing, and cursor storage. It builds a provider, reads the current cursor, then repeatedly consumes the stream. On disconnect, it reloads or clears the cursor if needed, invalidates the provider connection, logs the problem, waits briefly, and tries again.

**Call relations**: This is the top-level incoming-message loop for the surface. It calls ImessageSurface._consume_connected for each connected session and responds to MessageStreamDisconnected errors raised from that lower-level flow.

*Call graph*: calls 3 internal fn (clear_cursor, cursor, _consume_connected); 3 external calls (Event, sleep, log).


##### `ImessageSurface._consume_connected`  (lines 171–216)

```
async def _consume_connected(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> None
```

**Purpose**: This runs one connected session with the iMessage provider. It catches up on missed events first, then processes live events in order while guarding against duplicates.

**Data flow**: It receives the listener context, provider, installation ID, and starting cursor. It starts a background live-stream pump, waits until the live stream is ready, catches up from the cursor if needed, then reads live frames from a queue. Valid new events are processed and the cursor advances; provider failures are wrapped as stream-disconnect errors.

**Call relations**: ImessageSurface.listen calls this for each connection attempt. It starts ImessageSurface._pump_live, calls ImessageSurface._catch_up when resuming, and hands individual messages to ImessageSurface._process_event.

*Call graph*: calls 5 internal fn (external_error, _catch_up, _process_event, _pump_live, __init__); called by 1 (listen); 4 external calls (Event, Queue, create_task, gather).


##### `ImessageSurface._pump_live`  (lines 218–236)

```
async def _pump_live(self, provider: MessageProvider, ready: asyncio.Event, frames: asyncio.Queue[LiveFrame | LiveFailure]) -> None
```

**Purpose**: This background task pulls live events from the provider and places them into a queue for the main consumer. It also turns provider errors or a clean stream ending into queue messages, so the main loop notices them.

**Data flow**: It receives a provider, a readiness event, and a queue. It subscribes to the provider’s live stream, wraps each event as a LiveFrame, and puts it on the queue. If an error happens, it puts a LiveFailure instead; when it finishes, it makes sure the readiness event is set.

**Call relations**: ImessageSurface._consume_connected starts this task. The pump feeds that method live frames while the main method handles catch-up and processing.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (_consume_connected); 4 external calls (__init__, __init__, __init__, set).


##### `ImessageSurface._catch_up`  (lines 238–257)

```
async def _catch_up(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> int
```

**Purpose**: This processes events that happened while the listener was offline or behind. It brings UFO up to the provider’s current stream position before live events are handled.

**Data flow**: It receives the context, provider, installation ID, and old cursor. It asks the provider for catch-up frames, updates the current head position, processes real message events, and finally stores the newest cursor. It returns the cursor position reached.

**Call relations**: ImessageSurface._consume_connected calls this before reading live frames when a saved cursor exists. For each catch-up message, it calls ImessageSurface._process_event.

*Call graph*: calls 3 internal fn (store_cursor, catch_up, _process_event); called by 1 (_consume_connected).


##### `ImessageSurface._process_event`  (lines 259–274)

```
async def _process_event(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, sequence: int, message: InboundMessage | None) -> None
```

**Purpose**: This handles one provider event and then records that the event has been seen. It deliberately advances the stream even when no workspace owns the sender’s phone number, so unknown senders do not block later messages.

**Data flow**: It receives a sequence number and possibly an inbound message. If there is a message, it asks the listener context which workspace is addressed by the sender. If a workspace is found, it admits the message there. Finally, it stores the sequence number as the cursor.

**Call relations**: Both ImessageSurface._catch_up and ImessageSurface._consume_connected call this for individual events. It delegates the real message decision-making to ImessageSurface._admit_message.

*Call graph*: calls 3 internal fn (addressed, store_cursor, _admit_message); called by 2 (_catch_up, _consume_connected).


##### `ImessageSurface._admit_message`  (lines 276–320)

```
async def _admit_message(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage) -> None
```

**Purpose**: This decides whether an inbound iMessage should become a UFO conversation turn. It checks phone-number ownership, handles opt-in proof messages, filters group messages that do not need a reply, saves attachments, and admits accepted text into the right conversation.

**Data flow**: It receives a workspace surface context, provider, and inbound message. It looks up the claim for the sender. If the claim is still in proof mode, it sends the message to the proof flow. Otherwise it builds an audience, finds or creates the matching UFO conversation, downloads allowed attachments, wraps the text in UFO’s member-message format, and admits it with an idempotency key so the same message is not admitted twice.

**Call relations**: ImessageSurface._process_event calls this after routing a sender to a workspace. This method may call ImessageSurface._prove for unconfirmed claims, ImessageSurface._downloaded_files for attachments, and queue_key to create the conversation queue identity.

*Call graph*: calls 7 internal fn (address_claim, admit, ambient_reply_wanted, conversation_for, _downloaded_files, _prove, queue_key); called by 1 (_process_event); 7 external calls (__init__, __init__, sha256, conversation_audience, room_audience, fence_member_message, mint_marker).


##### `ImessageSurface._prove`  (lines 322–361)

```
async def _prove(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage, claim: AddressClaim) -> None
```

**Purpose**: This checks whether a direct iMessage contains the one-time code needed to connect a phone number to a member. It confirms the phone number on success, expires or releases claims when needed, and gives the sender helpful text when the code is wrong or expired.

**Data flow**: It receives the workspace context, provider, message, and address claim. It ignores non-direct or already-final claims. It builds the claim storage key, checks whether the claim expired, honors opt-out words like “stop,” reads the pending code, normalizes the user’s typed text, and compares it with the expected code. On success, it sends a connected message, sends a contact card, confirms the address, and deletes the stored claim.

**Call relations**: ImessageSurface._admit_message calls this when a sender’s address claim still needs proof. It uses claim_key and read_claim to find the pending code, calls ImessageSurface._send_contact_card after success, and uses the provider to send status texts.

*Call graph*: calls 6 internal fn (confirm_address, release_address, send_text, _send_contact_card, claim_key, read_claim); called by 1 (_admit_message); 2 external calls (__init__, now).


##### `ImessageSurface._send_contact_card`  (lines 363–384)

```
async def _send_contact_card(self, provider: MessageProvider, message: InboundMessage, assigned_phone_number: str) -> None
```

**Purpose**: This sends the shared line’s contact card after a successful connection. If the provider refuses the card for an outside reason, it logs the problem but does not undo the phone connection.

**Data flow**: It receives the provider, the proving message, and the assigned phone number. It builds the vCard bytes, sends them as an attachment to the same conversation, and uses the message ID to make the send idempotent. Provider-side send failures are logged if they are external errors; unexpected internal errors are raised.

**Call relations**: ImessageSurface._prove calls this after the opt-in code matches. It uses contact_card to create the file content and provider methods to send or classify errors.

*Call graph*: calls 4 internal fn (error_code, external_error, send_attachment, contact_card); called by 1 (_prove); 1 external calls (log).


##### `ImessageSurface._downloaded_files`  (lines 386–435)

```
async def _downloaded_files(self, ctx: SurfaceContext, provider: MessageProvider, conversation_id: UUID, attachments: tuple[MessageAttachment, ...]) -> str
```

**Purpose**: This downloads inbound iMessage attachments into the UFO workspace when they are small enough. It also creates a plain-text note saying which files were saved, skipped for size, or unavailable.

**Data flow**: It receives the workspace context, provider, UFO conversation ID, and attachment list. For each attachment, it chooses a safe inbox filename, skips files over the limit, streams download chunks into memory while enforcing the same limit, writes successful files into the workspace inbox, and records a note about the outcome. It returns those notes as text to include with the admitted message.

**Call relations**: ImessageSurface._admit_message calls this before admitting a message with attachments. It relies on the provider for downloads, the context for writing workspace files, and _attachment_content to present saved bytes as a stream.

*Call graph*: calls 4 internal fn (write_workspace_file, download_attachment, external_error, _attachment_content); called by 1 (_admit_message); 1 external calls (inbox_name).


##### `ImessageSurface.post`  (lines 437–444)

```
async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: This sends the final UFO turn reply back to the original iMessage conversation. It formats the terminal result so connection prompts, questions, credential prompts, and large-file links are understandable in chat.

**Data flow**: It receives a surface context and a writeback, which is UFO’s outgoing final response. It decodes the queue key to find the iMessage conversation, builds the final text, sends that text through the provider, and returns the provider’s send reference.

**Call relations**: The UFO runtime calls this when a turn is complete and needs to be written back to iMessage. It uses conversation_from_queue to locate the chat and ImessageSurface._terminal_text to prepare the message body.

*Call graph*: calls 2 internal fn (_terminal_text, conversation_from_queue).


##### `ImessageSurface.speak`  (lines 446–450)

```
async def speak(self, ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: This sends a mid-turn reply to iMessage, before the full UFO turn is finished. It is for interim messages that should appear promptly in the chat.

**Data flow**: It receives a surface context and a mid-turn reply. It decodes the queue key, takes the reply text, sends it through the provider using the reply ID as the idempotency key, and returns the provider’s send reference.

**Call relations**: The UFO runtime calls this when a live partial reply should be spoken into the iMessage conversation. It uses conversation_from_queue to turn the saved queue key back into the provider conversation ID.

*Call graph*: calls 1 internal fn (conversation_from_queue).


##### `ImessageSurface.attach`  (lines 452–464)

```
async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None
```

**Purpose**: This uploads files produced by UFO to the iMessage conversation after a reply. It only sends files that fit within the provider’s attachment size limit.

**Data flow**: It receives a surface context, a writeback containing artifacts, and a reply reference that this implementation does not need. It decodes the iMessage conversation, loops over artifacts, skips oversized files, reads each allowed artifact’s bytes from blob storage, and sends it as an iMessage attachment.

**Call relations**: The UFO runtime calls this when outgoing artifacts should be shared in iMessage. It uses conversation_from_queue for the destination and ImessageSurface._artifact_bytes to safely read each file before upload.

*Call graph*: calls 2 internal fn (_artifact_bytes, conversation_from_queue).


##### `ImessageSurface._terminal_text`  (lines 466–488)

```
async def _terminal_text(self, ctx: SurfaceContext, writeback: Writeback, *, direct: bool) -> str
```

**Purpose**: This turns a completed UFO response into text suitable for iMessage. It adds helpful extras such as questions, connection links, portal instructions, and links for files that are too large to send as attachments.

**Data flow**: It receives the surface context, writeback, and whether the destination is a direct chat. It starts with the terminal reply text and question text, then adds a direct connection URL or direct-message instruction if account connection is requested, adds a credential prompt link when needed, and appends links or filenames for oversized artifacts. It returns the combined text, or a status fallback if nothing else exists.

**Call relations**: ImessageSurface.post calls this before sending a final reply. It calls ImessageSurface._question_text for question formatting and asks the context for connection URLs, home URLs, and artifact links.

*Call graph*: calls 4 internal fn (artifact_link, connect_url, home_url, _question_text); called by 1 (post).


##### `ImessageSurface._question_text`  (lines 490–501)

```
def _question_text(self, writeback: Writeback) -> str
```

**Purpose**: This formats UFO’s structured question prompt into plain chat text. It makes titles, questions, options, and current answers readable in iMessage.

**Data flow**: It receives a writeback. If there is no question, it returns an empty string. Otherwise it builds lines from the question title, each question’s text, any available option labels, and any already chosen answer, then returns the lines joined with newlines.

**Call relations**: ImessageSurface._terminal_text calls this while preparing the final reply text for ImessageSurface.post.

*Call graph*: called by 1 (_terminal_text).


##### `ImessageSurface._artifact_bytes`  (lines 503–513)

```
async def _artifact_bytes(self, ctx: SurfaceContext, blob_key: str, size_bytes: int) -> bytes
```

**Purpose**: This reads an outgoing UFO artifact from blob storage and verifies it is safe to upload to iMessage. It protects the provider from oversized or unexpectedly changed files.

**Data flow**: It receives the surface context, blob key, and expected size. It rejects the file immediately if the expected size is over the iMessage limit. Then it streams the blob into memory, rejects it if the bytes exceed the limit, checks that the final byte count still matches the expected size, and returns the bytes.

**Call relations**: ImessageSurface.attach calls this before sending each allowed artifact as an iMessage attachment.

*Call graph*: called by 1 (attach).


### `extensions/imessage/ufo_ext_imessage/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools in other files, but this label lets the rest of the system find the drawer reliably. For this iMessage extension, the file makes paths such as `ufo_ext_imessage.something` possible. Without it, some Python setups or tooling might not recognize the folder as a proper package, which could break imports, packaging, tests, or extension discovery. Because the file is empty, it performs no setup, stores no settings, and changes no runtime state. Its value is structural: it gives the extension a clear place in the project’s module layout.


### `extensions/imessage/ufo_ext_imessage/local_line.py`

`domain_logic` · `development connect flow and provider listener runtime`

This file is a “pretend phone line” used when the iMessage extension needs to let the connection flow run, but must not contact real users. Think of it like a display phone in a shop: it looks enough like a phone for the setup screens to work, but it cannot place calls.

The LocalLine class matches the shape of a real iMessage provider. When the app asks for a line, it always returns the same fixed number. That lets the rest of the system keep moving through steps such as showing a QR code or an sms: link. When the app starts listening for incoming provider events, this class marks the listener as ready and then waits forever, producing no events. That is intentional: there is no real provider stream here, but the background listener should still appear healthy.

Any attempt to send a text, send an attachment, or download an attachment fails with a clear error message: “The local line delivers nothing.” This prevents a development-only setup from accidentally messaging someone. The remaining methods provide simple answers for cleanup and error classification, so code that expects a normal provider can still call them without special cases.

#### Function details

##### `LocalLine.installation_id`  (lines 18–19)

```
def installation_id(self) -> str
```

**Purpose**: This property identifies this provider installation as the built-in local dummy line. Other parts of the system can use this stable name to recognize that they are talking to the development-only provider.

**Data flow**: Nothing is passed in beyond the LocalLine object itself. It reads the fixed local installation constant and returns that text value unchanged.

**Call relations**: When provider code asks which installation this object represents, this property answers with the local-line identity. It does not hand work to any other helper because the identity is always fixed.


##### `LocalLine.assign_line`  (lines 21–22)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This function pretends to assign an iMessage line to a phone number, but always gives back the same safe fake number. It exists so the normal connect flow can continue even when there is no real messaging backend.

**Data flow**: It receives a requested phone number and an idempotency key, which is a repeat-safe request identifier. It ignores both because there is no real assignment to make, then returns the fixed local phone number.

**Call relations**: The connect or setup flow calls this when it needs a provider to assign a line. Instead of contacting an outside service, it immediately returns the local test line so the rest of the flow can proceed.


##### `LocalLine.catch_up`  (lines 24–26)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This function represents the step where a real provider would replay missed events, but the local line has no events to replay. It quietly produces nothing.

**Data flow**: It receives an optional sequence number saying where to resume from. It does not use that value, does not read any event source, and returns an asynchronous iterator that ends without yielding real events.

**Call relations**: A listener may call this before live subscription to catch up after downtime. The function contains an unreachable ProviderEvent construction only to keep the async-generator shape expected by the provider interface; in normal use it hands back no events.

*Call graph*: 1 external calls (__init__).


##### `LocalLine.subscribe`  (lines 28–31)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This function starts a pretend live event subscription. It tells the caller that the listener is ready, then waits forever because the local line never receives messages.

**Data flow**: It receives an asyncio.Event, which is a small signal object used by async code to say “ready.” It sets that signal, creates a fresh event, waits on it forever, and therefore produces no provider events.

**Call relations**: The provider listener calls this when it wants to begin receiving live events. This method calls the ready signal’s set method so startup can continue, then uses an asyncio event as a never-ending wait. Like catch_up, its ProviderEvent yield is unreachable and only preserves the expected async-generator form.

*Call graph*: 3 external calls (__init__, Event, set).


##### `LocalLine.send_text`  (lines 33–34)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This function blocks text sending through the local dummy provider. It protects development deployments from accidentally sending a real message.

**Data flow**: It receives a conversation id, message text, and idempotency key. Instead of sending anything or returning a message id, it raises a RuntimeError with the local-line failure message.

**Call relations**: Message-sending code may call this through the provider interface. Here the call stops immediately with a clear error, and nothing is handed off to an external messaging service.


##### `LocalLine.send_attachment`  (lines 36–43)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This function blocks attachment sending through the local dummy provider. It exists for the same safety reason as text sending: this provider must never deliver anything.

**Data flow**: It receives the target conversation id, filename, raw file bytes, and idempotency key. It ignores the content and raises a RuntimeError instead of uploading or sending the attachment.

**Call relations**: Attachment-sending code can call it just as it would call a real provider. The local implementation deliberately ends the flow at this point, with no network upload and no provider message id returned.


##### `LocalLine.download_attachment`  (lines 45–47)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This function blocks attachment downloads because the local line has no real provider storage to download from. It makes that limitation explicit instead of pretending data exists.

**Data flow**: It receives an attachment id. It raises a RuntimeError before producing any bytes, so the caller gets an error rather than a file stream.

**Call relations**: Code that wants attachment contents may call this through the provider interface. This implementation refuses immediately; the unreachable yield only keeps the return shape as an asynchronous byte stream.


##### `LocalLine.invalidate`  (lines 49–50)

```
async def invalidate(self) -> None
```

**Purpose**: This function is the cleanup hook for the local provider, but there is nothing to clean up. It succeeds without doing any work.

**Data flow**: It receives the LocalLine object and no other data. It changes no state, closes no connections, and returns nothing.

**Call relations**: Shutdown or reset code can call this the same way it would call a real provider’s cleanup method. Because the local line opens no external connection, the cleanup story ends here.


##### `LocalLine.invalid_cursor`  (lines 52–53)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This function answers whether an error means the event cursor is no longer usable. For the local line, that never happens because there is no real event cursor.

**Data flow**: It receives an exception object. It does not inspect it and always returns false.

**Call relations**: Listener error-handling code can ask this provider whether it should reset its position in an event stream. The local provider always says no, because its stream never has real positions to lose.


##### `LocalLine.external_error`  (lines 55–56)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This function answers whether an error came from an outside provider service. For the local line, there is no outside service, so it always says no.

**Data flow**: It receives an exception object. It ignores the details and returns false.

**Call relations**: General provider error-handling code can use this method to classify failures. The local provider keeps the classification simple: errors here are not treated as external provider failures.


##### `LocalLine.error_code`  (lines 58–59)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This function turns any local-line error into a stable short code. That gives logs or API responses a consistent label for failures from this dummy provider.

**Data flow**: It receives an exception object, does not inspect it, and returns the fixed local-line error code.

**Call relations**: When higher-level code needs a provider-specific error label, it calls this method. The method returns the local-line code directly, with no further lookup or translation.


### Slack connector
The Slack extension verifies Slack events, turns them into UFO turns, posts replies back to threads, and applies Slack-specific message polish.

### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `startup, install, request handling, turn execution, reply delivery`

This file is the bridge between Slack and the core ufo system. Without it, Slack messages could not safely become agent work, and agent answers could not return to the right Slack thread. It does several jobs. First, it proves that incoming HTTP requests really came from Slack by checking Slack signatures and install state. Then it figures out which workspace, Slack team, channel, thread, and member the message belongs to. A direct message always addresses the agent; a channel message usually needs an @-mention, except when it is already in a thread where the agent is participating. The file also fetches useful background messages from Slack so the agent understands the conversation around a request. If users attach files, it streams them into the workspace rather than loading whole files into memory. While a turn runs, this file can update Slack’s thread status, such as “Thinking…” or “Generating…”, and can post longer-running progress notes. When the turn finishes, it formats the answer for Slack, splits long text safely, renders question forms and connection buttons, uploads shared files, and avoids duplicate posts when Slack retries. It also supports Slack installation through OAuth or a manually supplied Slack app. In short, it is both the front door from Slack into ufo and the delivery truck from ufo back to Slack.

#### Function details

##### `_env_signing_secret`  (lines 230–234)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from environment variables. This is the fallback secret used when a workspace does not have its own stored Slack secret.

**Data flow**: Environment variable in → checks whether it has a non-empty value → returns the secret string or None.

**Call relations**: Workspace-specific secret lookups fall back to this helper when verifying Slack requests.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 237–244)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret for a workspace that is already bound to a request. This is needed before accepting Slack events or interactive button submissions.

**Data flow**: Surface context in → tries the workspace credential store first → falls back to the deploy environment secret → returns a secret or None.

**Call relations**: The event and interactivity routes call this before checking Slack’s signature.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 247–255)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the right signing secret before the request has been fully attached to a workspace. This supports shared routing for incoming Slack requests.

**Data flow**: Auth helper and workspace id in → reads that workspace’s credential slot → falls back to the deploy secret → returns None if the workspace is unknown or no secret exists.

**Call relations**: Workspace resolution uses it after Slack’s team id points to a candidate workspace.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 258–262)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client id from the process environment. OAuth installation cannot begin without it.

**Data flow**: Environment in → checks for the client id → returns it or raises a clear runtime error.

**Call relations**: The OAuth token exchange calls this when presenting this deploy’s Slack app identity to Slack.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 265–269)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret from the process environment. It is required to exchange Slack’s temporary code for a bot token.

**Data flow**: Environment in → checks for the client secret → returns it or raises a clear runtime error.

**Call relations**: The OAuth exchange uses this alongside the client id.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 272–274)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should redirect to after an install. It keeps Slack’s configured redirect path consistent.

**Data flow**: Public base URL in → trims any trailing slash → appends the Slack surface OAuth path → returns the full URL.

**Call relations**: The OAuth callback uses the same URL when exchanging Slack’s code, because Slack requires the redirect URL to match.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 277–289)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the “Add to Slack” link for installing the app. The link includes requested Slack permissions and sealed state tying the install to a ufo workspace.

**Data flow**: Client id, redirect URL, and state in → URL-encodes Slack OAuth parameters → returns a Slack authorization URL.

**Call relations**: This is used by install flows outside this route to start Slack OAuth.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 293–295)

```
def __init__(self, error: str)
```

**Purpose**: Stores a readable reason when Slack identity proof fails. Callers use it to distinguish Slack install or token problems from ordinary crashes.

**Data flow**: Error text in → saves it on the exception → creates a RuntimeError carrying that text.

**Call relations**: Identity proof and OAuth exchange raise this when Slack gives malformed or rejected identity data.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `bot_token_fingerprint`  (lines 312–313)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack bot token. The fingerprint lets the system tell whether stored identity data belongs to the current token without storing the token in metadata.

**Data flow**: Bot token in → hashes it with SHA-256 → returns the hexadecimal fingerprint.

**Call relations**: Identity reads and install writes use this to reject stale identity records after reinstall.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 316–328)

```
async def read_identity(blob: BlobStore, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack team and bot-user identity, but only if it matches the current bot token. This prevents routing events with identity data from an old install.

**Data flow**: Blob store and bot token in → checks for the identity blob → parses it → compares token fingerprint → returns a SlackIdentity or None.

**Call relations**: Identity resolution, inbound event handling, and self-user lookup all rely on this safe read.

*Call graph*: calls 3 internal fn (exists, get, bot_token_fingerprint); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 331–337)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Finds the Slack bot user id for the current workspace if Slack is installed. Other extension code can use it to know which Slack user represents the app.

**Data flow**: Identity context in → reads the bot token credential → reads matching identity from blob storage → returns the bot user id or None.

**Call relations**: This is a lightweight identity lookup used by extension identity plumbing.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_bot_token`  (lines 340–347)

```
async def _bot_token(ctx: SurfaceContext) -> str | None
```

**Purpose**: Fetches the Slack bot token for a workspace without treating a missing token as a crash. A workspace may be partially installed or have had its credential removed.

**Data flow**: Surface context in → reads the bot token credential → returns the token or None if the slot is unset.

**Call relations**: Slack event and interactivity routes call it before trying to talk back to Slack.

*Call graph*: calls 1 internal fn (credential); called by 2 (ingest, interactive).


##### `_identity`  (lines 350–354)

```
async def _identity(ctx: SurfaceContext, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the Slack identity for a bot token and mirrors the bot user id into the extension store when available. The mirror is needed by hooks that do not have blob access.

**Data flow**: Context and bot token in → reads matching identity → optionally writes bot user id to scoped store → returns identity or None.

**Call relations**: Inbound, interactive, and mention-mapping paths use it to recognize the bot and route safely.

*Call graph*: calls 2 internal fn (_mirror_self_user_id, read_identity); called by 3 (_reply_mention_ids, ingest, interactive).


##### `_mirror_self_user_id`  (lines 360–376)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the verified Slack bot user id into a scoped store. This is best-effort because a failed mirror should not make Slack event handling fail.

**Data flow**: Workspace id and bot user id in → skips if already mirrored in this process → writes to scoped store → updates an in-memory cache.

**Call relations**: Identity reads and OAuth installs call it so later turn-time hooks can find the bot id.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 389–395)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Gets or proves the Slack identity for a manually configured Slack app. It avoids calling Slack again if a valid identity is already stored.

**Data flow**: Resolver with blob store and token → tries stored identity → otherwise proves token through Slack and saves result → returns SlackIdentity.

**Call relations**: Background identity proof uses this when a bring-your-own Slack app has a token but no identity blob.

*Call graph*: calls 2 internal fn (_prove, read_identity).


##### `SlackIdentityResolver._prove`  (lines 397–422)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack which team and bot user a pasted bot token belongs to. This turns a raw token into trustworthy routing metadata.

**Data flow**: Bot token in resolver → calls Slack auth.test → validates response shape and id formats → returns a SlackIdentity or raises SlackIdentityError.

**Call relations**: Called only by SlackIdentityResolver.resolve when no usable identity is already stored.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 428–442)

```
def _prove_identity_in_background(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Starts one background task to prove Slack identity for a workspace. This lets a retry succeed without delaying the current Slack event response.

**Data flow**: Context and token in → checks whether a proof task already exists → starts one if needed → records it until it finishes.

**Call relations**: The missing-identity response path calls it when there is enough token information to recover.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 1 (_identity_unavailable); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 438–440)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished identity-proof task from the in-memory task table. This prevents stale task entries from blocking future proof attempts.

**Data flow**: Completed task in → compares it with the tracked task for the workspace → deletes the entry if it still matches.

**Call relations**: It is attached as the done callback for tasks started by _prove_identity_in_background.


##### `_run_identity_proof`  (lines 445–451)

```
async def _run_identity_proof(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Runs the actual background identity proof and logs any failure. It keeps proof errors out of Slack’s immediate request path.

**Data flow**: Context and token in → creates a resolver → resolves identity → logs Slack-specific or unexpected errors.

**Call relations**: Started by _prove_identity_in_background as the work behind the background task.

*Call graph*: called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `_identity_unavailable`  (lines 459–487)

```
def _identity_unavailable(ctx: SurfaceContext, bot_token: str | None) -> Response
```

**Purpose**: Builds the response when Slack is verified but the workspace cannot provide a usable bot identity. It either starts recovery or reports an incomplete install.

**Data flow**: Context and optional token in → if token missing, logs once and returns a no-retry error → if token exists, starts proof and returns retryable 503.

**Call relations**: Event and interactive routes use this instead of trying to post with an unknown or missing bot.

*Call graph*: calls 1 internal fn (_prove_identity_in_background); called by 2 (ingest, interactive); 2 external calls (Response, warn).


##### `signing_secret_fingerprint`  (lines 494–497)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Makes a non-reversible fingerprint of a Slack signing secret. This lets the system remember that a URL was verified with the current secret without storing the secret itself.

**Data flow**: Signing secret in → hashes it with SHA-256 → returns the fingerprint.

**Call relations**: URL verification and live-install checks compare these fingerprints.

*Call graph*: called by 2 (_mark_url_verified, verifying_fingerprint); 1 external calls (sha256).


##### `verifying_fingerprint`  (lines 500–507)

```
async def verifying_fingerprint(credentials: CredentialAccess) -> str | None
```

**Purpose**: Finds the fingerprint of the signing secret currently used for a workspace. It answers “what proof should count as current?”

**Data flow**: Credential access in → reads the signing secret slot → fingerprints it → returns None if no workspace secret is stored.

**Call relations**: install_is_live uses it to reject old URL-verification markers after a secret rotation.

*Call graph*: calls 2 internal fn (get, signing_secret_fingerprint); called by 1 (install_is_live).


##### `install_is_live`  (lines 510–524)

```
async def install_is_live(ext: ExtensionContext) -> bool
```

**Purpose**: Reports whether Slack installation is actually usable for the workspace. It checks both directions: Slack can reach ufo, and ufo has a bot token to answer.

**Data flow**: Extension context in → computes current secret fingerprint → checks bot token slot → compares scoped-store verification marker → returns true or false.

**Call relations**: Connector setup/status code can call this to show whether Slack is connected.

*Call graph*: calls 1 internal fn (verifying_fingerprint).


##### `slack_oauth_exchange`  (lines 544–577)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Completes Slack OAuth by trading Slack’s temporary code for a bot token and identity data. It refuses malformed responses so broken installs are not stored.

**Data flow**: OAuth code and redirect URI in → posts to Slack OAuth API with client credentials → validates token, team id, bot user id, and optional app id → returns SlackInstall.

**Call relations**: oauth_callback calls this after checking the sealed install state.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `slack_app_dm_url`  (lines 580–585)

```
def slack_app_dm_url(app_id: str, team_id: str) -> str
```

**Purpose**: Builds a browser link that opens the installed Slack app’s direct message. This gives the installer a friendly next step after install.

**Data flow**: Slack app id and team id in → URL-encodes them for Slack app_redirect → returns the URL.

**Call relations**: oauth_callback includes this link on the success page when Slack supplied an app id.

*Call graph*: called by 1 (oauth_callback); 1 external calls (urlencode).


##### `SlackConversationSearch.run`  (lines 672–686)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations by channel details or DM participants. It gives the agent a bounded way to find where a user wants to send or inspect messages.

**Data flow**: Search object with token, bot id, and query → lists conversations, resolves DM people, builds searchable records → returns matches plus a truncation flag.

**Call relations**: It coordinates the helper methods that page Slack conversations and enrich DM records.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 688–707)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches pages of Slack conversations up to a fixed limit. The limit prevents one search from running forever in a huge workspace.

**Data flow**: HTTP client in → repeatedly calls Slack conversations.list with cursor parameters → accumulates raw conversation objects → returns them and whether paging was cut off.

**Call relations**: SlackConversationSearch.run calls this before filtering and resolving people.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 709–717)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list page. It keeps the page request shape in one place.

**Data flow**: Cursor string in → creates type, archive, and limit parameters → adds cursor if present → returns a parameter dictionary.

**Call relations**: Used by _list for every Slack conversation page request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 719–722)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a response. A missing or malformed cursor means paging is done.

**Data flow**: Slack response payload in → looks inside response_metadata → returns a cursor string or an empty string.

**Call relations**: _list uses this after each page to decide whether to continue.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 724–752)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves the visible people in DMs and group DMs so searches can match names and emails. It also caps how many DMs it enriches.

**Data flow**: HTTP client and listed raw conversations in → finds DM-like conversations → reads member ids → looks up user labels → returns conversation-id-to-labels plus capped flag.

**Call relations**: run uses these labels when building searchable SlackConversation objects.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 754–761)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as public channel, private channel, group DM, or one-to-one DM. This gives later code a simple label instead of many Slack booleans.

**Data flow**: Raw Slack conversation dictionary in → checks Slack flags → returns a kind string.

**Call relations**: Conversation building, member lookup, and people enrichment all use this classification.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 763–777)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Finds member ids for one DM or group DM. One-to-one DMs carry the user directly; group DMs need a Slack members call.

**Data flow**: HTTP client, raw conversation, and conversation id in → reads direct user or calls conversations.members → returns a tuple of member ids.

**Call relations**: _people calls this before turning member ids into human labels.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 779–784)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Chooses the best searchable label for a Slack user. It prefers name plus email when both are available.

**Data flow**: Optional SlackUser and fallback user id in → combines name/email when possible → returns a readable label.

**Call relations**: _people uses it after resolving Slack users.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 786–803)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts one raw Slack conversation into the smaller SlackConversation model used by search results. Invalid raw records are skipped.

**Data flow**: Raw object and people labels in → validates id and extracts name, topic, purpose, kind, and membership → returns SlackConversation or None.

**Call relations**: run calls it for each raw Slack conversation before applying the query filter.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 805–807)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely extracts Slack’s nested topic or purpose text. Slack may omit or reshape these fields.

**Data flow**: Nested field object in → reads its value if it is a dictionary with a string value → returns the text or an empty string.

**Call relations**: _conversation uses it while building searchable channel records.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 982–997)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a Slack request is authentic and recent. This protects the system from forged or replayed Slack requests.

**Data flow**: Headers, raw body, signing secret, and optional clock in → validates timestamp and HMAC signature → returns nothing or raises SlackSignatureError.

**Call relations**: Workspace resolution, event ingest, and interactivity ingest all call this before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 1004–1023)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw HTTP request body with a size limit. Signature checks need the exact bytes Slack sent.

**Data flow**: Request in → returns cached body if present → otherwise streams chunks, counting bytes → stores bytes or an overflow marker → returns bytes or raises SlackBodyTooLarge.

**Call relations**: All Slack request handlers use it before signature verification or payload decoding.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 1026–1035)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Recognizes Slack’s URL verification handshake. During setup, Slack sends a challenge that must be echoed back.

**Data flow**: Raw body bytes in → tries JSON parsing → checks for url_verification type → returns challenge text, empty string, or None.

**Call relations**: Workspace resolution and event ingest use it to answer setup probes without treating them as messages.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 1038–1055)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Extracts a Slack team id from an event or interactive payload before the request is trusted. The id is only a hint until the signature is checked.

**Data flow**: Raw body bytes in → parses JSON or form-encoded payload → finds team_id or team.id → validates Slack team id shape → returns it or None.

**Call relations**: resolve_workspace uses this hint to choose which workspace secret should verify the request.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 1058–1059)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Creates the stable installation key for a Slack team. This maps one Slack workspace to one ufo workspace.

**Data flow**: Slack team id in → prefixes it with team: → returns the installation id string.

**Call relations**: OAuth binding writes this key, and request resolution reads it.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 1062–1100)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace an incoming Slack route belongs to. It handles OAuth callbacks, Slack URL verification, and signed Slack POSTs.

**Data flow**: Request and surface auth in → for GET, opens sealed install state; for POST, reads body, handles challenge, extracts team, finds workspace, verifies signature → returns workspace id, response, or None.

**Call relations**: The shared surface router calls this before dispatching to Slack route handlers.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 1103–1106)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether sealed credential state belongs to Slack OAuth install. This prevents unrelated credential links from being accepted as Slack installs.

**Data flow**: Credential claims in → compares payload marker and requested credential slots → returns true or false.

**Call relations**: Both resolve_workspace and oauth_callback use it when trusting OAuth state.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 1109–1114)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds ufo’s conversation key for a Slack thread. Channels are keyed by channel plus root message; DMs are keyed by the DM channel.

**Data flow**: Channel id, root timestamp, and DM flag in → chooses channel-only for DMs or channel:root for channel threads → returns queue key.

**Call relations**: Inbound messages and interactive submissions use this to find or create the right conversation.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `slack_message_addressed`  (lines 1117–1135)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly asking the agent to act. DMs always count; channel messages usually need a real @-mention of the bot.

**Data flow**: Slack event, bot user id, and DM flag in → scans message bodies for addressing mentions while ignoring attribution footer noise → returns true or false.

**Call relations**: _to_inbound uses this before deciding whether a message should be admitted, dropped, or sent to ambient reply decision.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1138–1141)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts links in a reply so Slack previews can be limited. Too many previews can bury the actual answer.

**Data flow**: Text in → counts Markdown links, removes them, then counts bare URLs → returns total link count.

**Call relations**: slack_reply_body uses it to turn off unfurling when a message has many links.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `_slack_atomic_spans`  (lines 1144–1182)

```
def _slack_atomic_spans(text: str) -> list[tuple[int, int]]
```

**Purpose**: Finds code blocks and table-like sections that should not be split in the middle. This keeps long Slack replies readable.

**Data flow**: Text in → walks line by line looking for fenced code and table runs → returns start/end character spans.

**Call relations**: slack_reply_parts uses these spans when choosing safe cut points.

*Call graph*: called by 1 (slack_reply_parts); 1 external calls (match).


##### `_slack_reply_cut`  (lines 1185–1205)

```
def _slack_reply_cut(text: str, start: int, limit: int, atomic_spans: list[tuple[int, int]]) -> int
```

**Purpose**: Chooses the best place to split one Slack reply part. It prefers paragraph, line, sentence, or word boundaries that do not cut through protected spans.

**Data flow**: Text, start position, size limit, and protected spans in → searches boundary patterns → returns a cut index.

**Call relations**: slack_reply_parts calls it repeatedly for long replies.

*Call graph*: called by 1 (slack_reply_parts); 1 external calls (finditer).


##### `slack_reply_parts`  (lines 1208–1225)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long reply text into Slack-sized pieces without breaking Markdown more than necessary. Slack rejects messages over its limits.

**Data flow**: Reply text and size limit in → validates inputs → returns one part if small enough or many safe parts if long.

**Call relations**: post, speak, and slack_reply_body use it before sending text to Slack.

*Call graph*: calls 2 internal fn (_slack_atomic_spans, _slack_reply_cut); called by 3 (post, slack_reply_body, speak).


##### `slack_reply_body`  (lines 1228–1295)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for a Slack chat.postMessage call. It can include rich Markdown blocks, action buttons, metadata for duplicate detection, and footer text.

**Data flow**: Channel, optional thread, text, metadata, delivery id, and rendering options in → creates Slack message payload → falls back to plain text if blocks would be too large → returns encoded JSON bytes.

**Call relations**: Terminal replies, mid-turn replies, and progress posts all use this before posting to Slack.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 3 (_say, post, speak); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1298–1299)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a simple Slack section block containing Markdown text. It trims text to Slack’s section limit.

**Data flow**: Text in → wraps it in Slack Block Kit section shape → returns a block dictionary.

**Call relations**: Question rendering uses it for titles and prose fallback.

*Call graph*: called by 2 (_ask_prose, slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1302–1346)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders a ufo question as Slack form blocks when possible. If Slack controls cannot represent the question safely, it renders prose instead.

**Data flow**: Optional AskUserInput in → builds title, one control per question, and submit button, or prose fallback → returns Slack blocks or None.

**Call relations**: post attaches these blocks to the final reply when a turn asks the user a question.

*Call graph*: calls 3 internal fn (_ask_control, _ask_prose, _mrkdwn_section); called by 1 (post).


##### `_ask_control`  (lines 1349–1401)

```
def _ask_control(index: int, ask: AskQuestion) -> dict[str, object] | None
```

**Purpose**: Turns one question into one Slack input block. It chooses radio buttons, checkboxes, or a text box depending on the question.

**Data flow**: Question index and AskQuestion in → validates Slack limits and attachment support → builds the right control or returns None for prose fallback.

**Call relations**: slack_ask_blocks calls it for each question in the ask.

*Call graph*: calls 1 internal fn (_ask_option); called by 1 (slack_ask_blocks).


##### `_ask_option`  (lines 1404–1414)

```
def _ask_option(option: QuestionOption) -> dict[str, object]
```

**Purpose**: Converts one answer option into Slack’s option format. It preserves the label and optional description.

**Data flow**: QuestionOption in → creates text and value fields, plus trimmed description when present → returns option dictionary.

**Call relations**: _ask_control uses it when building radio buttons or checkboxes.

*Call graph*: called by 1 (_ask_control).


##### `_ask_prose`  (lines 1417–1425)

```
def _ask_prose(ask: AskQuestion) -> dict[str, object]
```

**Purpose**: Renders one question as readable Slack prose. This is used when Slack controls cannot safely represent the question.

**Data flow**: AskQuestion in → builds lines for header, question, options, and multi-select note → wraps them in a Markdown section block.

**Call relations**: slack_ask_blocks uses it in fallback mode.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (slack_ask_blocks).


##### `slack_connect_blocks`  (lines 1428–1454)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Builds the Slack button that lets a member privately complete an external connection request. The button points back to the turn that requested it.

**Data flow**: Optional ConnectRequest and turn id in → if no request, returns None → otherwise returns Slack action block with provider label and turn id value.

**Call relations**: post includes these blocks in a terminal reply when the turn asks for a connection.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1457–1461)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required non-empty string field from a Slack payload. It fails early when Slack data is missing or malformed.

**Data flow**: Mapping and field name in → checks the value is a non-empty string → returns it or raises ValueError.

**Call relations**: Inbound and interaction parsing use it for required Slack ids and timestamps.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `_inbound_files`  (lines 1464–1476)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable file references from a Slack message. It ignores hidden, tombstoned, malformed, or over-count files.

**Data flow**: Slack event in → scans up to the inbound file limit → collects names and private URLs → returns InboundFile objects.

**Call relations**: _to_inbound reads files directly from events, and _declared_files uses it after refetching a message.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1479–1510)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Refetches a Slack message to find files that may not have appeared in the original event body. This covers some app_mention deliveries.

**Data flow**: Token, channel, message timestamp, and optional root in → reads the exact message from Slack replies API → extracts files → returns file references or empty tuple.

**Call relations**: _to_inbound calls it when a mention event might have hidden file declarations.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1518–1593)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the Slack “Add to Slack” installation. It validates the install state, exchanges the code, stores credentials and identity, binds the Slack team, and shows a result page.

**Data flow**: Surface context and browser request in → handles cancellation or missing data → validates sealed state → exchanges code → binds team and stores token/identity → returns success or error HTML response.

**Call relations**: This is the OAuth install endpoint reached after Slack redirects the owner back to ufo.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, slack_app_dm_url, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 3 external calls (__init__, __init__, callback_page).


##### `_mark_url_verified`  (lines 1599–1617)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this deploy using the current signing secret. This is a proof that the request URL is live.

**Data flow**: Context and signing secret in → fingerprints secret → writes marker blob → mirrors fingerprint to scoped store → caches success.

**Call relations**: ingest and interactive call it after a verified Slack request.

*Call graph*: calls 2 internal fn (mirror_url_verified, signing_secret_fingerprint); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `mirror_url_verified`  (lines 1620–1635)

```
async def mirror_url_verified(workspace_id: UUID, fingerprint: str) -> None
```

**Purpose**: Copies the URL-verification proof into scoped storage. This lets extension-level code read connection status without blob access.

**Data flow**: Workspace id and fingerprint in → skips repeated writes → writes fingerprint to scoped store → updates process cache.

**Call relations**: _mark_url_verified calls it after writing the blob marker.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (__init__).


##### `ingest`  (lines 1638–1682)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack event requests, especially messages and mentions. It verifies Slack, filters irrelevant events, and admits valid member messages into ufo.

**Data flow**: Context and request in → reads body, verifies signature, handles URL challenge, loads token and identity, parses inbound message → admits directly, folds into live turn, or starts ambient decision → returns Slack acknowledgement.

**Call relations**: This is the main Slack Events API route; it hands real work to _to_inbound, _admit_inbound, and ambient decision helpers.

*Call graph*: calls 12 internal fn (_admit_inbound, _bot_token, _ctx_signing_secret, _decide_ambient_in_background, _folds_into_live_turn, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_inbound (+2 more)); 3 external calls (loads, JSONResponse, Response).


##### `_folds_into_live_turn`  (lines 1685–1723)

```
async def _folds_into_live_turn(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> bool
```

**Purpose**: Checks whether an unmentioned thread reply should be absorbed by an already-running turn. If so, it should not be blocked by the ambient reply classifier.

**Data flow**: Context, token, and inbound message in → finds absorbing turn → resolves speaker and access → logs skipped ambient gate → returns true if the live turn should receive it.

**Call relations**: ingest calls this before deciding whether to run the ambient reply decision.

*Call graph*: calls 4 internal fn (absorbing_turn, member_has_access, _resolve_member, _slack_user); called by 1 (ingest); 1 external calls (log).


##### `_admit_inbound`  (lines 1726–1776)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Turns a verified Slack message into a ufo turn. It gathers sender data, background context, files, names, conversation mapping, and then calls core admission.

**Data flow**: Context, token, inbound message, and identity in → fetches Slack context and downloads files → resolves member and conversation → writes thread mirrors → builds fenced member message → admits turn → starts followers when a run opens.

**Call relations**: ingest and the ambient decision task call this when a Slack message should become agent work.

*Call graph*: calls 13 internal fn (admit, conversation_for, retitle_conversation, _ambient_context, _anchor_dm_thread, _arm_followers, _download_files, _mirror_thread, _resolve_member, _slack_permalink (+3 more)); called by 2 (_run_ambient_decision, ingest); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1782–1807)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts a background decision for an unmentioned thread reply. This keeps Slack’s required fast acknowledgement separate from slower model judgment.

**Data flow**: Context, token, inbound, and identity in → skips if a task for the message already exists → starts task and tracks it until done.

**Call relations**: ingest calls it after acknowledging a message that might or might not need an agent reply.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1803–1805)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished ambient-decision task from memory. This keeps the task table from growing forever.

**Data flow**: Completed task in → compares it with the tracked task for the message id → deletes the entry if it matches.

**Call relations**: It is the done callback for tasks created by _decide_ambient_in_background.


##### `_run_ambient_decision`  (lines 1810–1825)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the background ambient reply decision and admits the message if the agent is wanted. It logs failures because Slack will not retry after the route already acknowledged.

**Data flow**: Context, token, inbound, and identity in → asks whether reply is wanted → admits if yes → logs any later failure.

**Call relations**: _decide_ambient_in_background starts this task.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1828–1835)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages from users outside the installed Slack team in shared Slack Connect channels. Those users are skipped because the app cannot safely serve them as workspace members.

**Data flow**: Event and bound team id in → compares source/user team fields against team id → returns true for foreign authors.

**Call relations**: _to_inbound uses it before doing member or turn work.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1851–1887)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines who should be able to see a Slack-originated conversation and what label it should show. Public, private, shared, DM, and group conversations have different disclosure rules.

**Data flow**: Context, payload, event, channel id, and whether audience is already known in → uses event fields or Slack channel info → returns audience and optional channel label.

**Call relations**: _to_inbound calls it while converting Slack events into Inbound records.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1890–1940)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Parses a Slack event payload into the small Inbound object the rest of the file uses. It filters bots, unsupported subtypes, foreign users, top-level ambient chatter, and irrelevant events.

**Data flow**: Context, Slack payload, and identity in → validates event shape → decides addressed/thread status → computes queue key and audience → gathers files → returns Inbound or None.

**Call relations**: ingest calls this after request verification and identity lookup.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1943–1954)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has an admitted ufo turn. This is stricter than merely finding a conversation row.

**Data flow**: Context and queue key in → finds conversation → checks latest turn exists → returns conversation id or None.

**Call relations**: _to_inbound uses it to decide whether unmentioned thread replies are part of an existing agent conversation.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1957–1989)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches basic Slack user information such as name, confirmed email, timezone, and team id. It is best-effort so a slow Slack lookup does not break ordinary channel messages.

**Data flow**: Bot token and Slack user id in → calls users.info → validates profile fields → returns SlackUser or None.

**Call relations**: Admission, member resolution, conversation search, and name resolution all use it.

*Call graph*: calls 1 internal fn (_slack_ok); called by 5 (_people, _name, _admit_inbound, _folds_into_live_turn, _handle_answer_submit); 2 external calls (__init__, AsyncClient).


##### `_conversation_members`  (lines 2003–2023)

```
async def _conversation_members(bot_token: str, channel: str) -> tuple[str, ...]
```

**Purpose**: Reads a bounded roster of member ids for a Slack conversation. This limits outbound @-mention mapping to people already in the conversation.

**Data flow**: Bot token and channel id in → calls Slack conversations.members with a fixed limit → returns member ids or empty tuple on failure.

**Call relations**: SlackNames.mention_ids uses it before converting written names into Slack mentions.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (mention_ids); 1 external calls (AsyncClient).


##### `SlackNames.of`  (lines 2044–2052)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack user and channel ids mentioned in inbound text into readable names. This makes the prompt easier for the agent to understand.

**Data flow**: Texts and extra user ids in → extracts mentioned ids → resolves them through cache and Slack → returns id-to-name map.

**Call relations**: _admit_inbound and ambient digest creation use it before rendering Slack markup.

*Call graph*: calls 1 internal fn (_resolved); 2 external calls (mentioned_channels, mentioned_users).


##### `SlackNames.mention_ids`  (lines 2054–2068)

```
async def mention_ids(self, channel: str, identity: SlackIdentity) -> dict[str, str]
```

**Purpose**: Builds a safe map from human @names in an agent reply to Slack user ids in the current conversation. This prevents notifying people outside the thread’s audience.

**Data flow**: Channel id and identity in → reads channel roster → resolves names for same-team non-bot members → returns mention lookup map.

**Call relations**: _reply_mention_ids uses this before post and speak transform agent text for Slack.

*Call graph*: calls 2 internal fn (_resolved, _conversation_members); 1 external calls (mention_index).


##### `SlackNames._resolved`  (lines 2070–2078)

```
async def _resolved(self, wanted: Mapping[str, str]) -> dict[str, _NamedId]
```

**Purpose**: Combines cached name lookups with fresh Slack lookups. It bounds how many new ids are resolved at once.

**Data flow**: Wanted id-to-API mapping in → reads cached names → fetches a capped set of missing names concurrently → stores results → returns combined map.

**Call relations**: Both inbound name rendering and outbound mention mapping flow through this helper.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); called by 2 (mention_ids, of); 1 external calls (gather).


##### `SlackNames._remembered`  (lines 2080–2101)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, _NamedId]
```

**Purpose**: Reads still-fresh Slack name cache entries from scoped storage. Expired or malformed entries are ignored.

**Data flow**: Sequence of ids in → reads store rows → checks name, timestamp, team, and age → returns id-to-_NamedId map.

**Call relations**: _resolved calls it before making Slack API requests.

*Call graph*: called by 1 (_resolved); 3 external calls (__init__, __init__, now).


##### `SlackNames._name`  (lines 2103–2120)

```
async def _name(self, id_: str, url: str) -> _NamedId | None
```

**Purpose**: Fetches and cleans the display name for one Slack user or channel id. It also records a user’s Slack team when available.

**Data flow**: Id and Slack API URL in → calls user or channel info helper → strips unsafe delimiters and trims length → returns _NamedId or None.

**Call relations**: _resolved calls it for ids missing from cache.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (_resolved); 1 external calls (__init__).


##### `SlackNames._remember`  (lines 2122–2132)

```
async def _remember(self, names: Mapping[str, _NamedId]) -> None
```

**Purpose**: Writes resolved Slack names into scoped storage with a timestamp. Cache failures are logged but do not stop the message flow.

**Data flow**: Id-to-name map in → adds current timestamp → writes each row to store → logs individual write failures.

**Call relations**: _resolved calls it after successful fresh lookups.

*Call graph*: called by 1 (_resolved); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 2135–2155)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Asks Slack for the canonical link to one message. This link is attached to turn context so ufo can point back to the source message.

**Data flow**: Bot token, channel id, and timestamp in → calls chat.getPermalink → returns permalink string or None on failure.

**Call relations**: _admit_inbound uses it for message sources, and answer submission uses it for question-answer context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 2158–2175)

```
def _turn_context(sender: SlackUser | None, source: str | None, question: str | None=None) -> TurnContext
```

**Purpose**: Builds the contextual metadata for an admitted turn: sender, timezone, source link, and optional answered question. It drops invalid timezones instead of failing the turn.

**Data flow**: Optional SlackUser, optional source URL, and optional question in → formats sender line and validates TurnContext → returns TurnContext.

**Call relations**: _admit_inbound and _handle_answer_submit pass its result into core admission.

*Call graph*: called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (__init__).


##### `_resolve_member`  (lines 2178–2196)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member. It first checks existing links, then can join a same-domain teammate by Slack-confirmed email.

**Data flow**: Context, Slack user id, DM flag, and optional SlackUser in → reads linked member → if absent, uses confirmed email to join → returns member id, None, or raises when a DM cannot resolve.

**Call relations**: Admission, live-turn folding, and answer submission use it to identify the speaker.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 3 (_admit_inbound, _folds_into_live_turn, _handle_answer_submit); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 2199–2224)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether an unmentioned thread reply deserves a new agent turn. It uses recent Slack thread history before any ufo turn is created.

**Data flow**: Context, token, inbound, and identity in → fetches ambient history → admits by default when history is empty → otherwise calls core ambient decision → returns true or false and logs silence decisions.

**Call relations**: _run_ambient_decision calls it before admitting an ambient reply.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 2227–2249)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Builds recent Slack thread history for the ambient reply decision. It reads Slack directly because not every Slack message becomes a ufo turn.

**Data flow**: Token, inbound, and identity in → fetches thread tail → converts valid member/agent messages to AmbientMessage objects → returns oldest-first bounded history.

**Call relations**: _ambient_reply_wanted uses it as evidence for the model decision.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 2252–2298)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Fetches messages before a given point in a Slack thread, walking pages when needed. It returns None if the tail cannot be trusted.

**Data flow**: Token, channel, root timestamp, and latest timestamp in → pages conversations.replies within limits → returns raw message items or None on failure/overflow.

**Call relations**: Ambient history and unseen-tail digest both depend on it.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 2301–2321)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Converts one raw Slack message into an ambient-history entry when it is useful. It skips malformed messages, unrelated bots, empty text, and messages at or after the inbound.

**Data flow**: Raw item, inbound message, and identity in → validates user, timestamp, text, and bot ownership → returns timestamp plus AmbientMessage or None.

**Call relations**: _ambient_history applies it to each fetched Slack message.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 2324–2379)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Fetches background Slack messages that the agent’s transcript would otherwise not include. This helps the agent understand a mid-thread mention or recent channel context.

**Data flow**: Context, token, inbound, identity, and marker in → chooses DM/no-op, unseen-tail, thread history, or channel history → fetches names and messages → returns a fenced digest string.

**Call relations**: _admit_inbound gathers this before building the admitted member message.

*Call graph*: calls 4 internal fn (_digest_names, _slack_ok, _unseen_tail, ambient_digest); called by 1 (_admit_inbound); 1 external calls (AsyncClient).


##### `_digest_names`  (lines 2382–2391)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves the authors and mentioned ids inside messages used for an ambient digest. This makes the digest readable without many separate lookups.

**Data flow**: Bot token and raw messages in → collects text mentions and author ids → asks SlackNames for names → returns id-to-name map.

**Call relations**: _ambient_context and _unseen_tail call it before ambient_digest.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2394–2440)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds a digest of Slack thread messages that were previously ignored by ambient decisions and therefore are not in the ufo transcript. This keeps the agent’s view aligned with Slack’s visible thread.

**Data flow**: Context, token, inbound, identity, and marker in → fetches prior thread tail → walks backward until an admitted message is found → renders the remaining unseen messages → returns digest text.

**Call relations**: _ambient_context uses it once a Slack conversation already has turns.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2443–2511)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders fetched Slack messages as safe, bounded background context for the agent. It avoids treating other people’s Slack text as instructions from the current speaker.

**Data flow**: Raw messages, bot id, note, marker, and name map in → filters member messages, skips bot-addressed messages, renders names and timestamps, caps length → returns fenced context string or empty string.

**Call relations**: _ambient_context and _unseen_tail use it after fetching Slack messages and names.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2514–2516)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks whether a file download URL belongs to Slack. This prevents the bot token from being sent to an attacker-controlled host.

**Data flow**: URL in → parses hostname → returns true only for slack.com or Slack subdomains.

**Call relations**: _stream_download calls it before making an authenticated download request.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2519–2537)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private Slack file download in chunks with authentication and a size cap. It avoids buffering the whole file in memory.

**Data flow**: Bot token and URL in → verifies host → streams bytes from Slack → counts total bytes → yields chunks or raises if too large.

**Call relations**: _download_files passes this stream directly to workspace file writing.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2549–2565)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack attachments into the ufo workspace before the turn runs. Oversized files are skipped and reported instead of partially saved.

**Data flow**: Context, conversation id, bot token, and inbound files in → creates unique inbox names → streams each file into workspace storage → returns delivered and skipped filenames.

**Call relations**: _admit_inbound calls it when a Slack message includes files.

*Call graph*: calls 2 internal fn (write_workspace_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2568–2577)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates a plain note telling the agent which Slack files were saved and which were too large. This note becomes part of the admitted message.

**Data flow**: DownloadedFiles in → formats saved workspace paths and skipped original names → returns a newline-separated note.

**Call relations**: _admit_inbound appends this to the fenced member message after downloads.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2589–2594)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored Slack thread mirror in either old or current format. This preserves compatibility with existing stored rows.

**Data flow**: Stored JSON-like row in → if it is a string, treats it as queue key → otherwise validates as MirroredThread → returns MirroredThread.

**Call relations**: follow_turn and speak use it when recovering the Slack thread for a turn.


##### `MirroredThread.anchor`  (lines 2596–2600)

```
def anchor(self) -> str | None
```

**Purpose**: Finds the Slack message timestamp that status and progress updates should attach to. Channel threads use the root timestamp; DMs use a separately stored anchor.

**Data flow**: MirroredThread in → checks queue key for root timestamp → falls back to message_ts → returns timestamp or None.

**Call relations**: _track_status and ThreadProgress use this to know where Slack should show live feedback.

*Call graph*: called by 1 (_track_status).


##### `_thread_mirror_key`  (lines 2603–2604)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the scoped-store key for a conversation’s Slack thread mirror. One conversation gets one durable thread pointer.

**Data flow**: Conversation id in → prefixes it with the Slack thread key prefix → returns store key.

**Call relations**: _mirror_thread writes this key; follow_turn and speak read it.

*Call graph*: called by 3 (_mirror_thread, follow_turn, speak).


##### `_mirror_thread`  (lines 2607–2615)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Stores which Slack thread belongs to a ufo conversation. Turn-time hooks need this because they run without the original Slack request.

**Data flow**: Conversation id and MirroredThread in → builds store key → writes serialized thread to scoped store.

**Call relations**: Inbound admission and answer submission call it before the turn can execute.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, _handle_answer_submit); 2 external calls (__init__, model_dump).


##### `_dm_anchor_key`  (lines 2618–2624)

```
def _dm_anchor_key(turn_id: UUID, message_ref: UUID | None=None) -> str
```

**Purpose**: Builds the scoped-store key linking a DM turn or absorbed message to its Slack message timestamp. DMs need this because their queue key does not include a thread root.

**Data flow**: Turn id and optional message ref in → chooses turn-only or turn/ref suffix → returns store key.

**Call relations**: DM anchoring, reply-thread lookup, and cleanup all use this key format.

*Call graph*: called by 3 (_anchor_dm_thread, _drop_turn_reply_records, _reply_thread).


##### `_anchor_dm_thread`  (lines 2627–2634)

```
async def _anchor_dm_thread(admitted: Admitted, message_ts: str) -> None
```

**Purpose**: Records which Slack DM message a ufo message is answering. This lets replies appear as threaded replies under the user’s DM message.

**Data flow**: Admitted turn info and Slack message timestamp in → builds DM anchor key → writes timestamp to scoped store.

**Call relations**: Inbound admission and answer submission call it for DM conversations.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (__init__).


##### `_reply_thread`  (lines 2637–2652)

```
async def _reply_thread(queue_key: str, turn_id: UUID, message_ref: UUID | None=None) -> str | None
```

**Purpose**: Finds the Slack thread timestamp where a reply should be posted. It handles both channel threads and DM message anchors.

**Data flow**: Queue key, turn id, and optional message ref in → returns root timestamp from queue key or reads DM anchor from store → returns timestamp or None for top-level DM post.

**Call relations**: post, speak, and attach call it before sending replies or files.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 3 (attach, post, speak); 1 external calls (__init__).


##### `FollowerContext.workspace_id`  (lines 2663–2663)

```
def workspace_id(self) -> UUID
```

**Purpose**: Defines that follower code can read the current ufo workspace id. Followers need it to key status state and build footer links.

**Data flow**: Follower context object → exposes workspace UUID → caller reads it.

**Call relations**: Thread status, progress, and footer code rely on implementations of this protocol property.


##### `FollowerContext.public_base_url`  (lines 2666–2666)

```
def public_base_url(self) -> str | None
```

**Purpose**: Defines that follower code can read the public base URL when one exists. This is used to build links back to the web UI.

**Data flow**: Follower context object → exposes optional base URL → caller reads it.

**Call relations**: _slack_footer uses this through the FollowerContext protocol.


##### `FollowerContext.credential`  (lines 2668–2668)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Defines how follower code reads a credential such as the Slack bot token. It hides whether the caller has a surface context or hook context.

**Data flow**: Credential slot name in → implementation reads credential → returns secret string.

**Call relations**: ThreadStatus and ThreadProgress call this before posting to Slack.


##### `FollowerContext.tail`  (lines 2670–2672)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how follower code listens to live turn frames. These frames drive status and progress updates.

**Data flow**: Turn id and optional cursor in → implementation returns an async stream context → callers receive live frames.

**Call relations**: ThreadStatus and ThreadProgress use this protocol method while following a running turn.


##### `FollowerContext.turn_is_terminal`  (lines 2674–2674)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Defines how progress code checks whether a turn has already ended. This prevents progress posts after the final answer.

**Data flow**: Turn id in → implementation checks durable turn state → returns true or false.

**Call relations**: ThreadProgress uses it at timed checkpoints.


##### `FollowerContext.is_operator_workspace`  (lines 2676–2676)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Defines how footer code checks whether extra operator-only links may be shown. Non-operator workspaces get less internal detail.

**Data flow**: Follower context object → implementation checks workspace type → returns true or false.

**Call relations**: _slack_footer calls it before adding accounting/debug information.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2733–2734)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Gives the unique workspace/channel/thread identity for a Slack status line. This is how the file prevents multiple turns from fighting over one thread status.

**Data flow**: ThreadStatus fields in → combines workspace id, Slack channel, and thread timestamp → returns tuple key.

**Call relations**: Status tracking and restamping compare this key across live status tasks.


##### `ThreadStatus.run`  (lines 2736–2755)

```
async def run(self) -> None
```

**Purpose**: Runs the Slack native thread-status follower for one turn. It sets an initial “Thinking…” line, follows live frames, and clears status when appropriate.

**Data flow**: ThreadStatus object in → reads bot token → opens HTTP client → writes initial status → follows frames → clears status on normal end or failure path.

**Call relations**: _run_status calls it inside a supervised background task.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2757–2797)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one Slack assistant thread status line. It returns whether Slack accepted the update so the follower knows what is actually visible.

**Data flow**: HTTP client, bot token, and status text in → checks writer ownership → posts assistant.threads.setStatus → logs success or failure → returns true or false.

**Call relations**: ThreadStatus.run, _follow, and _clear all use this single Slack write helper.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2799–2808)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears the Slack status line only when no sibling turn still needs it. Thread status belongs to the whole Slack thread, not just one turn.

**Data flow**: HTTP client and bot token in → checks other live status tasks on the same thread → writes empty status only if this is the last one.

**Call relations**: ThreadStatus.run calls it when following ends or an exception requires cleanup.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2810–2864)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Listens to live turn frames and turns them into short Slack status text. It also refreshes the same line so Slack does not expire it during quiet periods.

**Data flow**: HTTP client, bot token, and currently shown text in → waits for live frames or restamp events → maps activity/text/resume/absorb frames to short statuses → writes accepted changes → exits on terminal frames.

**Call relations**: ThreadStatus.run calls it after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2874–2881)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers for a thread so they rewrite the current status. This is needed because posting a progress message can clear Slack’s native status indicator.

**Data flow**: Workspace id, channel, and thread timestamp in → finds matching live ThreadStatus objects → sets their wake event.

**Call relations**: ThreadProgress._say calls it after successfully posting into a thread.

*Call graph*: called by 1 (_say).


##### `_track_status`  (lines 2884–2905)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts one status follower task for a turn in this process. It also records that the newest turn owns writing status for the Slack thread.

**Data flow**: Follower context, turn id, and MirroredThread in → finds channel and anchor → skips if unanchored or already tracked → creates ThreadStatus and background task.

**Call relations**: _arm_followers calls it from admission and turn-execution hooks.

*Call graph*: calls 2 internal fn (anchor, _run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 2908–2936)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Supervises a ThreadStatus task and cleans up in-memory tracking when it ends. It also hands writer ownership back to an older live turn if needed.

**Data flow**: ThreadStatus in → runs it → logs fatal follower errors → removes task/status entries → updates or deletes thread writer claim.

**Call relations**: _track_status starts this as the background task body.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 2948–2952)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the progress-reporting schedule. It prevents nonsensical intervals such as zero or a cap smaller than the first wait.

**Data flow**: ProgressCadence values in → checks base and cap values → raises ValueError on invalid schedule.

**Call relations**: _track_progress creates ProgressCadence before starting a progress reporter.


##### `ProgressCadence.intervals`  (lines 2954–2965)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait lengths for long-turn progress posts. The waits double with elapsed time until they reach a cap.

**Data flow**: Cadence settings in → yields base wait, then elapsed-based waits capped at cap_seconds forever.

**Call relations**: checkpoints_after uses it to compute future checkpoint times.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 2967–2976)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Finds future progress checkpoints after a turn has already been running for some time. This lets resumed reporters continue the original schedule.

**Data flow**: Elapsed seconds in → walks generated intervals and accumulated checkpoint times → yields only checkpoints greater than elapsed.

**Call relations**: ThreadProgress._follow uses it when a reporter starts or resumes.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.update`  (lines 2989–2991)

```
def update(self, summary: str) -> None
```

**Purpose**: Records the latest completed activity summary for a running turn. It clears any partial streamed text because a new activity has taken over.

**Data flow**: Activity summary in → collapses whitespace and trims length → stores as current activity and clears streaming buffer.

**Call relations**: ThreadProgress._follow calls it on activity and subagent activity frames.


##### `TurnActivity.stream`  (lines 2993–2994)

```
def stream(self, text: str) -> None
```

**Purpose**: Records that response text is currently streaming. Progress messages use this only as a signal, not as content to reveal.

**Data flow**: Text delta in → appends it to the streaming list → returns nothing.

**Call relations**: ThreadProgress._follow calls it on TextDelta frames.


##### `TurnActivity.current_step`  (lines 2996–3000)

```
def current_step(self) -> str
```

**Purpose**: Chooses the member-facing current step for a progress post. Streaming response text is described as preparing the response.

**Data flow**: Stored activity and streaming list in → returns “Preparing the response” if streaming, otherwise last activity string.

**Call relations**: TurnActivity.report calls it before formatting a progress line.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 3002–3012)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Formats the current activity and elapsed wait into a short progress message. It returns nothing when the turn has produced no useful signal.

**Data flow**: Elapsed seconds in → gets current step → formats minutes or hours/minutes → returns progress text or None.

**Call relations**: ThreadProgress._post uses it at each checkpoint.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 3053–3056)

```
async def run(self) -> None
```

**Purpose**: Runs the long-turn progress reporter for one turn. It posts occasional Slack messages while the turn is still running.

**Data flow**: ThreadProgress object in → reads bot token → opens HTTP client → delegates to _follow.

**Call relations**: _run_progress supervises this method in a background task.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 3058–3061)

```
def _elapsed(self) -> float
```

**Purpose**: Calculates how long the member has been waiting since the turn’s durable start time. This survives process restarts better than a local timer.

**Data flow**: Current UTC time and stored started_at in → subtracts start time → returns elapsed seconds.

**Call relations**: ThreadProgress._follow and _post_resumed use it when scheduling and posting.

*Call graph*: called by 2 (_follow, _post_resumed); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 3063–3116)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Listens to live turn frames and posts progress at scheduled checkpoints. It also posts a delayed restart notice when a resumed turn keeps running.

**Data flow**: HTTP client and token in → creates checkpoint iterator and activity tracker → waits for frames or deadlines → updates activity/spend/resume state or posts progress → exits on terminal/parked/end.

**Call relations**: ThreadProgress.run calls it as the main reporter loop.

*Call graph*: calls 3 internal fn (_elapsed, _post, _post_resumed); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 3118–3145)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one scheduled progress update if there is meaningful activity to report. Empty checkpoints are logged and skipped.

**Data flow**: Client, token, activity, elapsed time, spend, and first-message flag in → formats activity report → sends through _say if present → returns whether a message landed.

**Call relations**: ThreadProgress._follow calls it when a cadence checkpoint arrives.

*Call graph*: calls 2 internal fn (_say, report); called by 1 (_follow); 1 external calls (log).


##### `ThreadProgress._post_resumed`  (lines 3147–3158)

```
async def _post_resumed(self, client: httpx.AsyncClient, bot_token: str, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts the special notice that work resumed after a restart. It uses the same sending path as ordinary progress.

**Data flow**: Client, token, spend, and first-message flag in → computes elapsed time → sends resume notice through _say → returns whether it landed.

**Call relations**: ThreadProgress._follow calls it after a resume grace period.

*Call graph*: calls 2 internal fn (_elapsed, _say); called by 1 (_follow).


##### `ThreadProgress._say`  (lines 3160–3200)

```
async def _say(self, client: httpx.AsyncClient, bot_token: str, text: str, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Sends one progress message into the Slack thread. The first delivered progress message may include the normal footer link back to ufo.

**Data flow**: Client, token, text, elapsed time, spend, and first flag in → builds optional footer and Slack message body → posts to Slack → logs result and restamps thread status → returns success.

**Call relations**: _post and _post_resumed both hand their text to this function.

*Call graph*: calls 4 internal fn (_footer, _restamp_thread_status, _slack_ok, slack_reply_body); called by 2 (_post, _post_resumed); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 3202–3219)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the footer for a progress post using the latest known cost information, if any. Progress happens before the turn is terminal, so some final accounting is not available.

**Data flow**: Bot token, channel, and optional cost tick in → formats cost/tokens when present → calls _slack_footer → returns footer text or None.

**Call relations**: ThreadProgress._say calls it only for the first progress message.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_say).


##### `_track_progress`  (lines 3225–3252)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, thread: MirroredThread, started_at: datetime) -> None
```

**Purpose**: Starts one progress reporter for a turn in this process. It is intended to be armed by the turn execution that owns the work.

**Data flow**: Follower context, turn id, conversation id, thread, and start time in → skips if already tracked → creates ThreadProgress with cadence → starts background task.

**Call relations**: _arm_followers calls it when the turn’s durable start time is known.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 4 external calls (__init__, __init__, create_task, now).


##### `_run_progress`  (lines 3255–3270)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Supervises a ThreadProgress task and removes it from tracking when done. Fatal reporter errors are logged without stopping the turn.

**Data flow**: ThreadProgress in → runs it → logs abandoned reporter errors → removes task from in-memory table.

**Call relations**: _track_progress starts this as the background task body.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 3284–3298)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts the live Slack side tasks for a turn: native status and, when possible, long-running progress messages. It centralizes follower setup for both admission and execution hooks.

**Data flow**: Follower context, followed turn, and mirrored thread in → tracks status always → tracks progress only when start time is available.

**Call relations**: Admission, answer submission, and follow_turn all call this to attach Slack feedback to a turn.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, _handle_answer_submit, follow_turn).


##### `_HookFollowerContext.workspace_id`  (lines 3310–3311)

```
def workspace_id(self) -> UUID
```

**Purpose**: Adapts a hook extension context to the follower protocol by exposing the workspace id. This lets hook-armed followers use the same code as request-armed followers.

**Data flow**: Adapter in → returns ext.workspace_id.

**Call relations**: follow_turn creates this adapter before calling _arm_followers.


##### `_HookFollowerContext.public_base_url`  (lines 3314–3315)

```
def public_base_url(self) -> str | None
```

**Purpose**: Exposes the hook context’s public base URL through the follower protocol. This supports Slack footer links from hook-started progress reporters.

**Data flow**: Adapter in → returns ext.public_base_url.

**Call relations**: Follower footer generation reads this through the common protocol.


##### `_HookFollowerContext.credential`  (lines 3317–3318)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Lets followers read credentials from a hook context. Hook contexts store credentials under extension credentials rather than surface methods.

**Data flow**: Credential slot in → calls ext.credentials.get → returns secret value.

**Call relations**: ThreadStatus and ThreadProgress use it after follow_turn arms them.


##### `_HookFollowerContext.tail`  (lines 3320–3323)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Lets followers subscribe to live turn frames from a hook context. This is how hook-armed status and progress tasks see the running turn.

**Data flow**: Turn id and optional cursor in → forwards to ext.tail → returns async frame stream context.

**Call relations**: ThreadStatus and ThreadProgress consume this stream through FollowerContext.


##### `_HookFollowerContext.turn_is_terminal`  (lines 3325–3326)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets progress reporters ask durable turn state through a hook context. This avoids posting progress after a turn has finished.

**Data flow**: Turn id in → forwards to ext.turn_is_terminal → returns boolean.

**Call relations**: ThreadProgress uses it at checkpoint deadlines.


##### `_HookFollowerContext.is_operator_workspace`  (lines 3328–3329)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Lets Slack footer code know whether a hook-context workspace is an operator workspace. This controls whether debug/accounting details may appear.

**Data flow**: Adapter in → forwards to ext.is_operator_workspace → returns boolean.

**Call relations**: _slack_footer calls this through the follower protocol.


##### `follow_turn`  (lines 3332–3374)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Hook handler that re-arms Slack status and progress followers from inside the turn execution. This is important after restarts because the execution that owns the turn can recreate the side-channel writers.

**Data flow**: Hook context in → ignores missing/subagent turns → reads mirrored Slack thread with a short timeout → adapts context → arms followers → returns no hook outcome.

**Call relations**: The manifest hook calls this during user_prompt_submit, before the turn continues.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `_handle_connect_click`  (lines 3421–3435)

```
async def _handle_connect_click(ctx: SurfaceContext, interaction: ConnectClick, member_id: UUID | None) -> None
```

**Purpose**: Responds to a Slack connect button click with a private link or a private error. The response is visible only to the clicking Slack user.

**Data flow**: Context, click info, and optional member id in → tries to create a connect URL when allowed → chooses message text → schedules ephemeral Slack post.

**Call relations**: interactive calls it after parsing a ConnectClick.

*Call graph*: calls 2 internal fn (connect_url, _ephemeral_in_background); called by 1 (interactive).


##### `_handle_answer_submit`  (lines 3438–3492)

```
async def _handle_answer_submit(ctx: SurfaceContext, bot_token: str, interaction: AnswerSubmit, member_id: UUID | None) -> Response | None
```

**Purpose**: Processes a submitted Slack question form as the next ufo turn. It admits all answers together and rewrites the Slack form only if this submit won idempotency.

**Data flow**: Context, token, AnswerSubmit, and optional member id in → finds conversation → validates answers → resolves sender/member → admits answer body → anchors DM if needed → arms followers → schedules rewrite when accepted → returns optional response.

**Call relations**: interactive calls it after parsing an AnswerSubmit.

*Call graph*: calls 13 internal fn (admit, admitted_body, conversation_for, find_conversation, _anchor_dm_thread, _arm_followers, _ephemeral_in_background, _mirror_thread, _resolve_member, _rewrite_in_background (+3 more)); called by 1 (interactive); 7 external calls (__init__, __init__, gather, conversation_audience, JSONResponse, fence_member_message, mint_marker).


##### `interactive`  (lines 3495–3537)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack interactive payloads such as form submits and connect button clicks. It verifies Slack first, then routes the action.

**Data flow**: Context and request in → reads body, verifies signature, loads token/identity, parses interaction → marks URL verified → handles connect click or answer submit → returns Slack acknowledgement.

**Call relations**: This is the Slack interactivity endpoint for buttons and submitted question forms.

*Call graph*: calls 11 internal fn (linked_member, _bot_token, _ctx_signing_secret, _handle_answer_submit, _handle_connect_click, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_interaction (+1 more)); 2 external calls (JSONResponse, Response).


##### `_rewrite_in_background`  (lines 3543–3546)

```
def _rewrite_in_background(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Schedules a Slack message rewrite after an ask form has been accepted. It keeps the interactive acknowledgement fast.

**Data flow**: Bot token and AnswerSubmit in → starts rewrite task → stores task reference until it finishes.

**Call relations**: _handle_answer_submit calls it after confirming this submit’s body was admitted.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (_handle_answer_submit); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3549–3553)

```
async def _run_rewrite(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Runs the ask-form rewrite and logs failures. A rewrite failure should not undo the admitted answer.

**Data flow**: Bot token and submit in → calls replacement helper → logs any exception.

**Call relations**: _rewrite_in_background starts this task.

*Call graph*: calls 1 internal fn (_replace_controls_with_answers); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3556–3561)

```
def _ephemeral_in_background(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Schedules a private Slack ephemeral message. This keeps click handling fast while still informing the user.

**Data flow**: Context, channel, Slack user id, optional thread timestamp, and text in → starts background post task → tracks it until done.

**Call relations**: Connect-click and empty-answer paths call it to respond privately.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 2 (_handle_answer_submit, _handle_connect_click); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3564–3591)

```
async def _post_ephemeral(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Posts a Slack ephemeral message visible only to one user, optionally inside a thread. It is used for private button feedback.

**Data flow**: Context, channel, user id, thread timestamp, and text in → reads bot token → calls chat.postEphemeral → logs failure.

**Call relations**: _ephemeral_in_background runs this in a background task.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_interaction`  (lines 3594–3656)

```
def _to_interaction(raw: bytes, identity: SlackIdentity) -> AnswerSubmit | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactive form body into a supported action. Unsupported actions are ignored.

**Data flow**: Raw form bytes and identity in → decodes payload → validates team and action → returns ConnectClick, AnswerSubmit, or None.

**Call relations**: interactive calls it after signature and identity checks.

*Call graph*: calls 4 internal fn (_dict_field, _string_field, _submitted_answers, slack_thread_key); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_submitted_answers`  (lines 3659–3685)

```
def _submitted_answers(blocks: tuple[Mapping[str, object], ...], state: object) -> tuple[SubmittedAnswer, ...]
```

**Purpose**: Reads all question answers from a Slack ask form submission. Slack sends the whole form state at submit time.

**Data flow**: Delivered blocks and state object in → finds ask input blocks → reads held values → returns SubmittedAnswer records in rendered order.

**Call relations**: _to_interaction uses it when parsing ask-submit actions.

*Call graph*: calls 1 internal fn (_held_answer); called by 1 (_to_interaction); 1 external calls (__init__).


##### `_held_answer`  (lines 3688–3706)

```
def _held_answer(field: object) -> str
```

**Purpose**: Converts one Slack control’s submitted state into answer text. It handles radio buttons, checkboxes, and text boxes.

**Data flow**: Control state object in → checks control type → extracts selected value(s) or typed text → returns answer string or empty string.

**Call relations**: _submitted_answers calls it for each input block.

*Call graph*: calls 1 internal fn (_option_value); called by 1 (_submitted_answers); 1 external calls (get).


##### `_option_value`  (lines 3709–3713)

```
def _option_value(option: object) -> str
```

**Purpose**: Safely extracts the value from one Slack option object. In this file, the value is the option label shown to the user.

**Data flow**: Option object in → checks it is a dictionary with string value → returns value or empty string.

**Call relations**: _held_answer uses it for radio and checkbox controls.

*Call graph*: called by 1 (_held_answer).


##### `_dict_field`  (lines 3716–3720)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload. It turns malformed payload shape into a clear error.

**Data flow**: Payload and field name in → checks the value is a dictionary → returns it or raises ValueError.

**Call relations**: _to_interaction uses it for nested user, channel, and message objects.

*Call graph*: called by 1 (_to_interaction).


##### `_replace_controls_with_answers`  (lines 3723–3756)

```
async def _replace_controls_with_answers(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Rewrites a Slack question message so controls are replaced by the submitted answers. This makes the thread show what was committed.

**Data flow**: Bot token and AnswerSubmit in → walks original delivered blocks → swaps input blocks for answer context lines and submit button for submitter line → calls Slack update.

**Call relations**: _run_rewrite calls it after a winning answer submit.

*Call graph*: calls 2 internal fn (_context_line, _rewrite_slack_message); called by 1 (_run_rewrite).


##### `connect_message_key`  (lines 3778–3781)

```
def connect_message_key(member_id: UUID, provider: str) -> str
```

**Purpose**: Builds the store key for the Slack message containing a connect button. The key is based on the requester and provider.

**Data flow**: Member id and provider name in → formats scoped-store key → returns string.

**Call relations**: _hold_connect_message writes this key so later connection-settle hooks can find the Slack message.

*Call graph*: called by 1 (_hold_connect_message).


##### `_hold_connect_message`  (lines 3784–3814)

```
async def _hold_connect_message(store: ScopedStore, request: ConnectRequest | None, posted: dict[str, object], channel: str, ts: str | None) -> None
```

**Purpose**: Remembers where a posted connect button lives in Slack. Later, when the connection succeeds, the button can be rewritten into an account line.

**Data flow**: Store, connect request, posted body, channel, and message timestamp in → verifies the posted blocks include a connect action → writes ConnectMessage to store.

**Call relations**: post calls it only after Slack accepts a reply containing a connect button.

*Call graph*: calls 3 internal fn (put, _is_connect_action, connect_message_key); called by 1 (post); 1 external calls (__init__).


##### `_is_connect_action`  (lines 3817–3824)

```
def _is_connect_action(block: Mapping[str, object]) -> bool
```

**Purpose**: Checks whether a Slack block contains this file’s connect button. It avoids rewriting messages that never actually offered a button.

**Data flow**: Slack block in → checks actions block and child elements for connect action id → returns true or false.

**Call relations**: _hold_connect_message and settle_connect_message use it to find or remove connect buttons.

*Call graph*: called by 2 (_hold_connect_message, settle_connect_message).


##### `_rewrite_slack_message`  (lines 3827–3844)

```
async def _rewrite_slack_message(bot_token: str, channel: str, ts: str, text: str, blocks: list[dict[str, object]]) -> None
```

**Purpose**: Updates a bot-authored Slack message with new text and blocks. It is the shared write path for ask and connect message rewrites.

**Data flow**: Bot token, channel, timestamp, text, and block list in → posts chat.update JSON to Slack → raises on Slack error.

**Call relations**: _replace_controls_with_answers and settle_connect_message both call it.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_replace_controls_with_answers, settle_connect_message); 2 external calls (AsyncClient, dumps).


##### `_held_connect_message`  (lines 3847–3883)

```
async def _held_connect_message(bot_token: str, held: ConnectMessage) -> Mapping[str, object] | None
```

**Purpose**: Reads the current Slack message that holds a connect button. It reads live Slack blocks so it does not overwrite other rewrites, such as submitted answers.

**Data flow**: Bot token and ConnectMessage in → reads exact message from thread or channel history → returns message mapping or None.

**Call relations**: settle_connect_message calls it before rewriting the connect button.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (settle_connect_message); 1 external calls (AsyncClient).


##### `settle_connect_message`  (lines 3886–3912)

```
async def settle_connect_message(bot_token: str, held: ConnectMessage, provider: str, account: str) -> None
```

**Purpose**: Rewrites a connect button into a line saying which account was connected. This gives the Slack thread a durable record of the completed authorization.

**Data flow**: Bot token, held message, provider, and account in → reads current Slack message → removes connect button blocks if still present → appends connected account line → updates Slack message.

**Call relations**: Connection-completion code outside this file calls it when an OAuth connection lands.

*Call graph*: calls 4 internal fn (_context_line, _held_connect_message, _is_connect_action, _rewrite_slack_message).


##### `_context_line`  (lines 3915–3919)

```
def _context_line(text: str) -> dict[str, object]
```

**Purpose**: Creates a small Slack context block for status-like text inside a message. It trims the text to Slack’s context limit.

**Data flow**: Text in → wraps it as mrkdwn context element → returns block dictionary.

**Call relations**: Ask rewrites and connect-settle rewrites use it for final summary lines.

*Call graph*: called by 2 (_replace_controls_with_answers, settle_connect_message).


##### `_reply_text`  (lines 3922–3932)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a terminal turn reply. It turns failed, cancelled, empty, and normal results into member-facing text.

**Data flow**: Writeback in → checks terminal status and text → returns failure line, cancellation reason/line, final text, or empty placeholder.

**Call relations**: _reply_with_oversize_links builds on it before Slack posting.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 3935–3964)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds Slack-specific extra text to a terminal reply: credential instructions and links for files too large for Slack upload. This prevents important outcomes from disappearing.

**Data flow**: Context and writeback in → starts with reply text → appends credential location if needed → appends oversized artifact link lines → returns final reply text.

**Call relations**: post calls it before splitting and sending the terminal reply.

*Call graph*: calls 3 internal fn (home_url, _oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 3967–3970)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one too-large artifact as a Markdown list item with a temporary link when possible. This is Slack’s fallback for files over the upload cap.

**Data flow**: Context and artifact in → asks context for artifact link → formats filename/link and byte size → returns one line.

**Call relations**: _reply_with_oversize_links calls it for each oversized shared artifact.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_reply_mention_ids`  (lines 3973–3984)

```
async def _reply_mention_ids(ctx: SurfaceContext, bot_token: str, channel: str, text: str) -> dict[str, str]
```

**Purpose**: Builds the safe outbound mention map for a Slack reply, but only when the text contains an @ sign. This avoids unnecessary Slack roster reads.

**Data flow**: Context, token, channel, and text in → returns empty map if no @ or no identity → otherwise resolves channel-safe mention ids → returns map.

**Call relations**: _reply_mentions_mapped calls it before replacing names with Slack mention markup.

*Call graph*: calls 1 internal fn (_identity); called by 1 (_reply_mentions_mapped); 1 external calls (__init__).


##### `_channel_info`  (lines 3987–4001)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for one channel. It is best-effort and returns None if Slack cannot answer quickly.

**Data flow**: Bot token and channel id in → calls conversations.info → returns channel dictionary or None.

**Call relations**: Channel origin, shared-channel checks, and SlackNames channel-name lookup use it.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 4004–4017)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel crosses workspace boundaries. If the check fails, it assumes shared to avoid leaking operator-only information.

**Data flow**: Bot token and channel id in → reads channel info → checks Slack shared-channel flags → returns true or false.

**Call relations**: _slack_footer uses it before adding accounting or debug links.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 4020–4052)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the small footer shown under Slack progress posts and final replies. It may include web-chat, accounting, and debug links, but hides operator details in externally shared channels.

**Data flow**: Follower context, token, channel, conversation id, turn id, and optional accounting in → builds web link → checks workspace/channel privacy → returns joined footer text or None.

**Call relations**: ThreadProgress._footer and post use it for Slack-visible footer metadata.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 4074–4079)

```
def _slack_reply_progress_key(turn_id: UUID, reply_id: UUID | None=None) -> str
```

**Purpose**: Builds the store key for exactly-once Slack reply delivery progress. Terminal replies and mid-turn replies use different suffixes.

**Data flow**: Turn id and optional reply id in → formats key prefix plus ids → returns store key.

**Call relations**: post, speak, and cleanup use this key family.

*Call graph*: called by 3 (_drop_turn_reply_records, post, speak).


##### `_slack_reply_progress`  (lines 4082–4095)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Reads or creates the delivery-progress record for a Slack reply. This record prevents duplicate Slack messages during retries.

**Data flow**: Store and key in → reads existing progress or atomically creates an empty one → returns progress model and stored raw value.

**Call relations**: post and speak call it before sending any reply parts.

*Call graph*: calls 2 internal fn (get, put_if); called by 2 (post, speak); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 4098–4107)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Atomically writes an updated Slack reply delivery checkpoint. If another worker changed the record, it raises so duplicate delivery is avoided.

**Data flow**: Store, key, expected old value, and progress object in → serializes progress → compare-and-swaps into store → returns new progress and raw value.

**Call relations**: Delivery, mention mapping, post, and speak use it between Slack send steps.

*Call graph*: calls 1 internal fn (put_if); called by 4 (_deliver_slack_reply, _reply_mentions_mapped, post, speak); 2 external calls (__init__, model_dump).


##### `_drop_turn_reply_records`  (lines 4110–4120)

```
async def _drop_turn_reply_records(store: ScopedStore, turn_id: UUID) -> None
```

**Purpose**: Deletes temporary delivery and DM-anchor records for a finished turn. Once core has recorded delivery, these retry aids are no longer needed.

**Data flow**: Store and turn id in → lists reply-progress and DM-anchor prefixes → deletes each matching key.

**Call relations**: attach calls it after terminal delivery is recorded, and post calls it when a silent reply posts nothing.

*Call graph*: calls 4 internal fn (delete, list, _dm_anchor_key, _slack_reply_progress_key); called by 2 (attach, post).


##### `_slack_reply_delivery`  (lines 4123–4138)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Checks whether a Slack message carries this file’s delivery metadata id. This helps recover from uncertain post attempts.

**Data flow**: Raw Slack message and delivery id in → inspects metadata and timestamp → returns Slack timestamp if it matches, otherwise None.

**Call relations**: _reconcile_slack_reply scans Slack history with this helper.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 4141–4181)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Looks back through recent Slack messages to see whether a pending delivery actually posted. This closes the gap where Slack accepted a message but the response was lost.

**Data flow**: HTTP client, token, channel, optional thread, and delivery id in → pages recent history/replies with metadata → returns matching timestamp, None, or raises on page overflow.

**Call relations**: post and speak call it before retrying a reply with a pending delivery id.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 2 (post, speak); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 4184–4224)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Posts one Slack reply body with exactly-once bookkeeping. It records pending delivery before sending and accepted timestamp after sending.

**Data flow**: Client, token, store, key, progress, expected value, delivery id, and body in → skips if already delivered → checkpoints pending → posts to Slack → handles invalid_blocks specially → checkpoints delivered timestamp → returns updated progress and payload.

**Call relations**: post and speak call it for every reply part and fallback attempt.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 2 (post, speak); 2 external calls (__init__, model_copy).


##### `_reply_mentions_mapped`  (lines 4227–4257)

```
async def _reply_mentions_mapped(ctx: SurfaceContext, bot_token: str, channel: str, text: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue) -> tuple[_SlackReplyPro
```

**Purpose**: Replaces safe @names in agent text with Slack mention markup, using a pinned map for repeatable retries. This keeps message splitting stable across attempts.

**Data flow**: Context, token, channel, text, store, key, progress, and expected value in → reads or creates mention map → applies mention markup → returns updated progress and mapped text.

**Call relations**: post and speak call it before splitting reply text.

*Call graph*: calls 2 internal fn (_checkpoint_slack_reply, _reply_mention_ids); called by 2 (post, speak); 2 external calls (model_copy, mention_markup).


##### `post`  (lines 4260–4456)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str | NothingDelivered
```

**Purpose**: Delivers a terminal turn reply to Slack. It handles silence, long-message splitting, forms, connect buttons, footers, duplicate prevention, and Slack block fallbacks.

**Data flow**: Context and Writeback in → decides whether to suppress → finds reply thread → prepares text/actions/footer/mentions → reconciles pending sends → posts each part with checkpoints and fallbacks → records completion → returns first Slack message ref or NOTHING_DELIVERED.

**Call relations**: Core writeback delivery calls this when a turn finishes.

*Call graph*: calls 17 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _drop_turn_reply_records, _hold_connect_message, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _reply_with_oversize_links (+7 more)); 7 external calls (__init__, __init__, __init__, AsyncClient, loads, log, is_silence_sentinel).


##### `speak`  (lines 4459–4556)

```
async def speak(ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Delivers a mid-turn reply to Slack before the final answer. It posts plain threaded messages with exactly-once tracking.

**Data flow**: Context and MidTurnReply in → finds channel/thread anchor → reads delivery progress → maps mentions → splits text → reconciles pending send → posts parts and checkpoints completion → returns first Slack message ref.

**Call relations**: Core calls this for live replies or comments emitted during a turn.

*Call graph*: calls 12 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _slack_reply_progress, _slack_reply_progress_key, _thread_mirror_key (+2 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 4559–4599)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request and parses Slack’s response without immediately treating Slack ok:false as fatal. This lets callers handle recoverable invalid block errors.

**Data flow**: HTTP client, token, and encoded body in → posts to Slack → converts HTTP errors, including rate limits, to SurfaceDeliveryError → returns JSON payload.

**Call relations**: _deliver_slack_reply uses it for all reply-part sends.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 4602–4608)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the Slack timestamp from a successful post response. The timestamp becomes the durable message reference.

**Data flow**: Slack response payload in → verifies ok:true and non-empty ts → returns timestamp or raises SlackApiError.

**Call relations**: _deliver_slack_reply, post, and speak call it after posting.

*Call graph*: called by 3 (_deliver_slack_reply, post, speak); 1 external calls (__init__).


##### `attach`  (lines 4611–4650)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shared turn artifacts to Slack after the reply message is recorded. It streams files, batches them, and shares what succeeds.

**Data flow**: Context, writeback, and reply ref in → finds thread and cleans retry records → filters uploadable artifacts → uploads files concurrently → shares successful uploads in Slack batches → logs per-file or per-share failures.

**Call relations**: Core calls this after post succeeds and records the terminal reply reference.

*Call graph*: calls 6 internal fn (credential, _attachment_batches, _drop_turn_reply_records, _reply_thread, _share_uploaded_files, _upload_artifact); 4 external calls (__init__, gather, AsyncClient, Timeout).


##### `_attachment_batches`  (lines 4653–4657)

```
def _attachment_batches(files: Sequence[dict[str, str]]) -> Iterator[Sequence[dict[str, str]]]
```

**Purpose**: Splits uploaded file descriptors into batches Slack will accept in one share call. Small shares stay as one message.

**Data flow**: Sequence of file dictionaries in → slices by Slack attachment cap → yields batches.

**Call relations**: attach uses it before calling the final Slack share step.

*Call graph*: called by 1 (attach).


##### `_upload_artifact`  (lines 4660–4687)

```
async def _upload_artifact(ctx: SurfaceContext, client: httpx.AsyncClient, bot_token: str, artifact: SharedArtifact) -> str
```

**Purpose**: Performs the reservation and byte upload steps of Slack’s external file upload flow. The file bytes stream from blob storage instead of loading into memory.

**Data flow**: Context, HTTP client, token, and artifact in → requests Slack upload URL → streams blob bytes to that URL → returns Slack file id.

**Call relations**: attach runs many of these concurrently before sharing files into the thread.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (__init__, post).


##### `_share_uploaded_files`  (lines 4690–4714)

```
async def _share_uploaded_files(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, files: Sequence[dict[str, str]]) -> None
```

**Purpose**: Completes Slack’s external upload flow by sharing uploaded file ids into a channel or thread. This is the step that makes uploaded files visible.

**Data flow**: HTTP client, token, channel, optional thread, and file descriptors in → posts files.completeUploadExternal JSON → returns nothing or raises on Slack error.

**Call relations**: attach calls it once per upload batch.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (post, dumps).


##### `_slack_ok`  (lines 4717–4726)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Common helper for Slack Web API calls that should return ok:true. It turns Slack API failures into SlackApiError with useful detail.

**Data flow**: Awaitable HTTP response in → awaits it → checks HTTP status → parses JSON → checks ok field → returns payload or raises.

**Call relations**: Most Slack read, write, install, upload, status, and rewrite helpers use it as their success gate.

*Call graph*: called by 18 (_list, _members, _say, _set, _ambient_context, _channel_info, _conversation_members, _declared_files, _held_connect_message, _post_ephemeral (+8 more)); 1 external calls (__init__).


### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `connector send and Slack event handling`

When this system sends a Slack message through a connector, the message may be posted by a user’s connected Slack account rather than by the bot itself. That creates two problems. First, readers still need a clear way to know which bot or agent produced the message. Second, Slack may treat the bot mention in that footer as if someone had directly addressed the bot, which could cause the system to react to its own attribution line.

This file solves both sides of that problem. On the outgoing side, it recognizes connector calls that are Slack message sends and adds the standard attribution footer, but with the actual Slack bot user mentioned instead of a generic product name. It relies on the shared connector attribution code so the footer has the same shape everywhere and does not drift into a slightly different format.

On the incoming side, it can tell whether a Slack message really mentions the bot, after removing any attribution footer this system wrote itself. It also knows where Slack may hide text: not just in the top-level message text, but inside blocks and nested rich-text structures. In everyday terms, this file is both the label maker and the “don’t mistake our own label for a request” guard.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: Decides whether a connector call is the kind of Slack call that publishes a message. This matters because only those calls should receive the Slack attribution footer.

**Data flow**: It receives a connector provider name and a connector slug, which is a short name for the action being called. It lowercases the slug, checks that the provider is Slack, checks that the action name is about messages, and checks that the action name contains one of the send-style words. It returns true only when all of those clues say this is a Slack message send.

**Call relations**: This is the local mirror of the connector tool’s own test for whether to add attribution. It helps the Slack extension and the connector attribution logic agree about exactly which outgoing calls should be marked.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: Adds an attribution footer to Slack send arguments, using a real Slack bot mention as the footer’s subject. If the message is already attributed, it leaves it alone so the footer does not stack up on repeated processing.

**Data flow**: It receives the outgoing connector arguments and the bot user ID. It formats the special attribution subject as a Slack mention for that bot, then passes the original arguments and that subject to the shared connector attribution helper. The result is a new or unchanged argument dictionary ready to send.

**Call relations**: This function hands off the actual footer-shaping work to the connector attribution helper, so Slack uses the same footer structure as other connector sends. Its only Slack-specific contribution is choosing the bot mention as the visible subject.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: Checks whether a piece of Slack text really mentions the bot, ignoring mentions that appear only inside this system’s own attribution footer. This prevents the bot from treating its own footer as a user calling for attention.

**Data flow**: It receives some text and the bot user ID. First it removes any recognized attribution footer from the text. Then it looks for Slack’s mention form for that bot, like a tagged username. It returns true if the bot is still mentioned after the footer text has been stripped away.

**Call relations**: This is used on the inbound side, when deciding whether Slack text is addressed to the bot. It relies on the connector attribution stripper so it uses the same understanding of the footer that the outgoing attribution code uses.

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: Collects every text string in a Slack message event where a bot mention might appear. This includes the main message text and text buried inside Slack blocks.

**Data flow**: It receives a Slack event represented like a dictionary. It reads the normal text field, then reads the blocks field and walks through nested lists and objects inside it looking for strings. It returns all found text as a tuple, with the top-level text first.

**Call relations**: This function is the bridge between Slack’s message format and the mention-checking logic. It calls `_nested_strings` to search through the deeper block structure, because Slack may deliver meaningful text somewhere other than the main text field.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: Finds all strings inside a nested value made of dictionaries, lists, and strings. It is a small helper for digging through Slack block data without needing to know every possible block shape in advance.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, it checks each stored value inside it. If it is a list, it checks each item. Other kinds of values are ignored. The output is a stream of strings found anywhere inside the original value.

**Call relations**: This helper is called by `message_bodies` when Slack message blocks need to be searched for possible mention text. It stays generic so the higher-level function can simply ask for all nested strings instead of caring about every Slack block detail.

*Call graph*: called by 1 (message_bodies).


### `extensions/slack/ufo_ext_slack/hooks.py`

`orchestration` · `event hooks during tool use and after connection recording`

This file solves two user-facing problems around Slack. First, when the system sends a Slack message through a generic connector tool, that tool does not know the Slack bot user’s identity. This hook looks up the bot user ID that the Slack extension previously saved, then rewrites the outgoing message so its footer can mention the bot properly. If the lookup is slow, fails, or the ID is missing, the hook backs off and lets the message go out with the connector’s normal generic footer. That is important because this hook runs before a tool call, and a failed pre-check could otherwise stop the Slack message from being sent at all.

Second, this file updates an old Slack “connect account” button after a user has successfully connected an account elsewhere. Imagine a sign-up button still sitting in a chat after you already signed up; this hook turns that stale button into a settled message showing what account was connected. It reads the saved Slack message details, uses the Slack bot token to update the message, and then deletes the saved record so it is not reused. If there was no saved Slack button, it simply does nothing.

#### Function details

##### `attribute_connector_send`  (lines 38–52)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook runs before a connector tool is used and only acts when the tool is about to send a Slack message. It adds a Slack bot mention footer to the message when this workspace has a known bot user ID.

**Data flow**: It receives a hook context containing the event payload. If the payload is a pre-tool-use event for a Slack send, it asks `_mirrored_self_user_id` for the saved bot user ID. If no valid ID is available, it returns nothing and leaves the message unchanged. If an ID is found, it rewrites the tool input’s arguments with `mention_attributed`, then returns a modified tool input so the connector sends the adjusted text.

**Call relations**: This is the public hook that the extension loader calls before tool use. It relies on `is_slack_send` to decide whether the tool call is really a Slack send, calls `_mirrored_self_user_id` to safely read the saved bot identity, and then hands the edited arguments back through `ModifyInput` so the connector tool can continue normally.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 55–69)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper safely reads the Slack bot user ID that the Slack surface previously saved for this workspace. It is deliberately cautious: if the read fails or takes too long, it returns `None` instead of risking blocking the outgoing message.

**Data flow**: It receives the hook context and uses the extension’s scoped store to read the saved bot user ID. The read is wrapped in a one-second timeout, which means it has a strict time budget. If an error happens, it logs the error type and returns `None`. If the stored value is a string matching the expected Slack bot user ID pattern, it returns that string; otherwise it returns `None`.

**Call relations**: `attribute_connector_send` calls this helper when it needs a bot identity for the footer. This function does not contact Slack directly; it only reads the extension store, which keeps the pre-tool hook fast and safe.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


##### `settle_connect_button`  (lines 72–101)

```
async def settle_connect_button(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook runs after an external account connection has been recorded, and updates the original Slack connect button so it no longer invites the user to do something already completed. It turns the button message into a settled confirmation showing the connected account.

**Data flow**: It receives a hook context whose payload should describe a newly recorded connection, including the provider, account ID, optional account label, and owning member. It uses the owner and provider to look up the saved Slack connect message. If none is found, it returns without changing anything. If a saved message exists, it reads the Slack bot token, validates the saved message data, asks Slack to update the message, and then deletes the saved record from the store.

**Call relations**: The extension system calls this after a `ConnectionRecorded` event. It uses `connect_message_key` to find the stored Slack message, `ConnectMessage.model_validate` to turn stored data back into the expected message shape, and `settle_connect_message` to perform the Slack update. If the hook is somehow called with the wrong kind of payload, it raises an error because that would mean the hook was wired incorrectly.

*Call graph*: 3 external calls (model_validate, connect_message_key, settle_connect_message).


### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message ingest and outbound Slack reply sending`

Slack does not send messages exactly as people see them. A person mention arrives as something like `<@U123>`, a channel as `<#C456|general>`, and a labeled link as `<https://example.com|docs>`. Those forms are useful for Slack’s systems, but they are hard for humans and language models to understand. This file is the translator between Slack’s machine format and the words readers expect.

On the way in, `render_markup` rewrites known Slack entities into readable text, such as `@Alex` or `#general`. If a user or channel id cannot be resolved, it leaves the original code alone instead of guessing. That matters because guessing a name could mislead the agent or the reader.

On the way out, `mention_markup` does the reverse only for safe, known names. If the agent writes `@Alex`, this file can turn that into `<@U123>` so Slack actually notifies Alex. It avoids dangerous places such as code blocks, links, email addresses, and quoted URLs. It also limits how many mentions can be converted, so an agent summarizing a busy thread does not accidentally notify a crowd.

The file also keeps Slack’s escaping rules separate. Turning `&lt;` back into `<` is only safe for the original author’s own words, not for quoted bystanders’ text.

#### Function details

##### `mentioned_users`  (lines 67–70)

```
def mentioned_users(text: str) -> frozenset[str]
```

**Purpose**: Finds the Slack user ids that are explicitly mentioned in a piece of Slack-formatted text. A caller can use these ids to look up real display names before rendering the message.

**Data flow**: It receives a raw Slack message string. It asks the shared mention-finding helper to look only for user mention markers, then returns a frozen set of user ids found in the text.

**Call relations**: This is a small public entry point for user mentions. When another part of the Slack integration needs to know which people must be resolved, it calls this function, which delegates the actual scanning to `_mentioned` with the user kind.

*Call graph*: calls 1 internal fn (_mentioned).


##### `mentioned_channels`  (lines 73–75)

```
def mentioned_channels(text: str) -> frozenset[str]
```

**Purpose**: Finds the Slack channel ids that are explicitly mentioned in a piece of Slack-formatted text. A caller can use these ids to ask Slack for channel names before showing the message to people or the agent.

**Data flow**: It receives a raw Slack message string. It asks the shared mention-finding helper to look only for channel mention markers, then returns a frozen set of channel ids found in the text.

**Call relations**: This is the channel-focused companion to `mentioned_users`. When the surrounding Slack code needs channel ids to resolve, it calls this function, which passes the channel kind into `_mentioned`.

*Call graph*: calls 1 internal fn (_mentioned).


##### `_mentioned`  (lines 78–83)

```
def _mentioned(text: str, kind: str) -> frozenset[str]
```

**Purpose**: Scans Slack markup and extracts ids for one specific kind of entity, such as users or channels. It prevents callers from guessing what an id means based only on its letters.

**Data flow**: It receives a text string and a kind marker, such as `@` for users or `#` for channels. It runs the Slack entity pattern across the text, keeps only matches of that kind that have an id, and returns those ids as a frozen set.

**Call relations**: `mentioned_users` and `mentioned_channels` both rely on this helper so the parsing rules live in one place. They choose the kind; this function does the actual extraction.

*Call graph*: called by 2 (mentioned_channels, mentioned_users).


##### `render_markup`  (lines 86–96)

```
def render_markup(text: str, names: Mapping[str, str]) -> str
```

**Purpose**: Turns Slack’s encoded entities into the readable words a person would expect to see. This is used so stored messages, transcripts, titles, and model input all see the same human-friendly version.

**Data flow**: It receives Slack-formatted text and a map from Slack ids to readable names. It replaces each recognized Slack entity with a readable form, such as `@Name`, `#channel`, `@here`, or a labeled URL, while leaving unresolved or unknown forms unchanged. It returns the rewritten text and does not change the input map.

**Call relations**: This is the inbound translation step. It is called when a Slack message is admitted into the system, after callers have resolved any needed ids, and it applies the entity-rendering rule to each Slack markup match.


##### `unescape`  (lines 99–108)

```
def unescape(text: str) -> str
```

**Purpose**: Turns Slack’s escaped versions of `<`, `>`, and `&` back into the characters the original speaker typed. It is deliberately separate from mention rendering because unescaping someone else’s quoted words can be unsafe.

**Data flow**: It receives a text string that may contain `&amp;`, `&lt;`, or `&gt;`. It replaces those escape sequences with `&`, `<`, and `>` in order, then returns the unescaped text.

**Call relations**: This function is used only when the system is dealing with the current speaker’s own words. It is not part of the general entity-rendering path, because quoted bystander text should keep its protective escaping.


##### `mention_key`  (lines 111–115)

```
def mention_key(name: str) -> str
```

**Purpose**: Normalizes a display name so it can be matched reliably. It ignores case differences and treats repeated whitespace as a single space, so `Alex  Graveley` and `alex graveley` compare the same.

**Data flow**: It receives a name string. It splits the name into words, joins them with single spaces, applies case-insensitive folding, and returns the normalized key.

**Call relations**: `mention_index` uses this when building the outbound lookup table, and `_mention_at` uses it when checking whether text after an `@` matches a known name. This keeps both sides of the lookup using the same spelling rules.

*Call graph*: called by 2 (_mention_at, mention_index).


##### `mention_index`  (lines 118–132)

```
def mention_index(names: Mapping[str, str]) -> dict[str, str]
```

**Purpose**: Builds the safe lookup table used when converting readable `@Name` text into Slack notification markup. It only keeps names that point to exactly one id, so the system does not notify the wrong person when two people share a display name.

**Data flow**: It receives a mapping from Slack ids to names. It normalizes each name with `mention_key`, ignores blank names and broadcast words like `here`, groups ids by normalized name, then returns only the names claimed by exactly one id.

**Call relations**: This prepares data for outbound mention conversion. Before `mention_markup` can safely rewrite `@Alex`, another part of the system can call `mention_index` to create the trusted name-to-id map that `mention_markup` will use.

*Call graph*: calls 1 internal fn (mention_key).


##### `mention_markup`  (lines 135–166)

```
def mention_markup(text: str, ids: Mapping[str, str], limit: int=MENTION_MARKUP_MAX) -> str
```

**Purpose**: Converts safe, readable `@Name` mentions in an outgoing reply into Slack’s `<@id>` form so notifications work. It is careful not to rewrite text inside code, links, URLs, email addresses, or other places where an `@` is not a person mention.

**Data flow**: It receives outgoing text, a trusted name-to-id map, and a maximum number of mentions to convert. It first finds spans that must be skipped, then walks through every `@` sign, rejects unsafe or over-limit cases, asks `_mention_at` whether a known name starts there, and builds a new string with approved mentions replaced by `<@id>`. It returns the rewritten text; all unmatched text stays as written.

**Call relations**: This is the outbound translation step used before sending the agent’s reply to Slack. It calls `_mention_at` to decide whether the words after each usable `@` match a known person, and it relies on regular expression scanning to find both skip zones and possible mention starts.

*Call graph*: calls 1 internal fn (_mention_at); 1 external calls (finditer).


##### `_mention_at`  (lines 169–182)

```
def _mention_at(text: str, start: int, ids: Mapping[str, str]) -> tuple[int, str] | None
```

**Purpose**: Checks whether a known mentionable name appears immediately after an `@` sign, and chooses the longest valid name if several could match. This lets names with spaces work without relying on a simple one-word pattern.

**Data flow**: It receives the full text, the position just after an `@`, and the trusted name-to-id map. It looks only at a short slice on the same line, refuses names that start with whitespace, considers up to a few words, trims sentence punctuation from the end, normalizes each candidate with `mention_key`, and returns the end position plus Slack id for the longest match. If nothing matches, it returns nothing.

**Call relations**: `mention_markup` calls this whenever it finds an `@` that is not inside a skipped area and not part of something like an email address. This helper supplies the exact replacement boundary and the id that should be written into Slack markup.

*Call graph*: calls 1 internal fn (mention_key); called by 1 (mention_markup); 2 external calls (islice, finditer).


##### `_entity`  (lines 185–201)

```
def _entity(match: re.Match[str], names: Mapping[str, str]) -> str
```

**Purpose**: Turns one matched Slack entity into the readable text that should replace it. It knows the separate rules for user mentions, channel mentions, broadcasts, and links.

**Data flow**: It receives one regular-expression match and the id-to-name map. For user and channel entities, it prefers the resolved name, then Slack’s label, and otherwise leaves the original markup unchanged. For broadcasts, it returns forms like `@here` only for recognized broadcast names. For links, it returns either the URL alone or `label (URL)` when the label differs.

**Call relations**: This is the per-entity formatting rule used by the inbound rendering flow. `render_markup` applies it to each recognized Slack entity so the whole message becomes readable one piece at a time.


### Terminal directives
The CLI-facing surface converts UFO activity into simple text directives that the terminal client can render.

### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling and live stream handling`

This file is the bridge between a person using the `ufo` shell client and the server-side conversation engine. The client makes HTTP requests, often held open like a phone call, and the server replies with one line at a time. Each line is a directive such as `say`, `txt`, `ask`, `run`, `file`, or `listen`, with tab-separated fields. The shell client reads those lines and updates the terminal.

The file solves several problems at once. It authenticates the caller with a bearer token, finds or creates the right conversation for that user and channel, admits new messages, resumes old streams without printing duplicate text, and watches live agent frames until the turn finishes or the hold time expires. If the hold expires, it tells the client to poll again. If the turn ends, it tells the client to listen for future background activity.

It also connects the agent to the member's local terminal when needed. Agent-requested terminal operations are sent back as `run` directives, and the next client request returns the result. The file also supports private credential entry, workspace file upload/download/listing, environment uploads, and system skill bundle downloads. Without this file, the command-line client would have no clear protocol for talking to the server, resuming safely, or showing live agent work.

#### Function details

##### `terminal_runtime_id`  (lines 118–120)

```
def terminal_runtime_id(channel: str) -> str
```

**Purpose**: Creates a stable short identifier for the local terminal runtime tied to one channel. This lets the server recognize the same terminal workspace across reconnects without exposing the raw channel name.

**Data flow**: It takes a channel string, hashes it with SHA-256, and returns the first fixed number of hexadecimal characters. Nothing else is changed.

**Call relations**: When a channel stream is bound to a terminal, `_ChannelStream._bound` calls this to name the runtime connection before notifying the surface context that the terminal is connected.

*Call graph*: called by 1 (_bound); 1 external calls (sha256).


##### `directive`  (lines 123–131)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one protocol line for the shell client. It turns a command word and its fields into a safe tab-separated byte line.

**Data flow**: It receives a verb and text fields, escapes tabs, newlines, and backslashes so fields cannot break the line format, drops carriage returns, joins everything with tabs, appends a newline, and returns bytes.

**Call relations**: Almost every response-building path uses this helper before sending data to the client. Higher-level functions decide what should happen; this function formats that decision into the terminal wire language.

*Call graph*: called by 13 (_answer, _channel_message, _channel_op_reply, _channel_stop, _client_update, _fulfill_secret, _say_lines, _send, _stream_end_directives, _subagent_note (+3 more)).


##### `shared_files`  (lines 145–156)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files an agent shared during a turn and prepares them for terminal display. Each file gets a name, size, and download link if links are configured.

**Data flow**: It receives the surface context and a turn id, asks the context for that turn's shared artifacts, asks the context to turn each artifact into a public link, and returns a tuple of `SharedFile` records.

**Call relations**: The streaming code passes this as the file lookup used when a terminal frame is rendered. It depends on the privileged surface context because only that context knows how to read artifacts and mint safe links.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 159–166)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Finds which workspace an incoming request claims to belong to before the route handler runs. A missing or invalid bearer token means the request cannot be scoped.

**Data flow**: It reads the `Authorization` header, extracts a bearer token, asks the bearer codec for the workspace claim, and returns a workspace UUID or `None`.

**Call relations**: This is the surface identification hook used by the shared routing layer. The later request handlers separately verify the same token for member identity.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 172–231)

```
def history_directives(conversation: Conversation) -> tuple[bytes, ...]
```

**Purpose**: Turns past conversation history into terminal directives for a fresh resume. It shows the user's earlier messages and completed agent replies without duplicating the latest live turn.

**Data flow**: It reads conversation messages, extracts readable text, counts completed tool steps, keeps the newest content within a character budget, and returns `you`, `note`, and `say` directive lines.

**Call relations**: `_ChannelStream.response` calls this when a client reconnects without a saved cursor and needs enough transcript context before live frames resume. It uses `_history_text`, `_dispatched`, and `directive` to build the lines.

*Call graph*: calls 3 internal fn (_dispatched, _history_text, directive); called by 1 (response).


##### `_dispatched`  (lines 234–241)

```
def _dispatched(message: Message, active: set[str]) -> int
```

**Purpose**: Counts how many tool calls in an assistant message actually became active work. This avoids showing fake progress for calls that were written but never dispatched.

**Data flow**: It receives a message and a set of active tool-use ids. If the message has structured content, it counts tool-use blocks whose ids are active; plain text messages count as zero.

**Call relations**: `history_directives` uses this count to roll earlier agent work into concise `Completed step` notes during transcript replay.

*Call graph*: called by 1 (history_directives).


##### `_history_text`  (lines 244–249)

```
def _history_text(message: Message) -> str
```

**Purpose**: Extracts the human-readable text from one stored message. It normalizes user messages differently from assistant messages so terminal history matches what the member saw.

**Data flow**: It receives a message, reads either its plain string content or its text blocks, joins structured text blocks, and applies member-message cleanup for user messages.

**Call relations**: `history_directives` calls this while rebuilding the transcript shown to a reconnecting terminal.

*Call graph*: called by 1 (history_directives); 1 external calls (member_message_text).


##### `directives_for`  (lines 252–306)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True, runtime: RuntimeIde
```

**Purpose**: Converts one live engine frame into the directive lines the terminal client understands. It is the main translator from internal events to terminal protocol.

**Data flow**: It receives a live frame plus context such as whether text was already streamed, pending credential prompts, shared files, connection messages, exit behavior, and runtime identity. It pattern-matches the frame type and returns the appropriate directive bytes.

**Call relations**: `_render_stream_frame` calls this after gathering any extra information needed for terminal frames. It delegates terminal endings to `_answer` and subagent progress to `_subagent_note`.

*Call graph*: calls 3 internal fn (_answer, _subagent_note, directive); called by 1 (_render_stream_frame); 1 external calls (__init__).


##### `_subagent_note`  (lines 309–315)

```
def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]
```

**Purpose**: Turns subagent activity into a short progress note for the terminal. It avoids printing extra lines for subagent start and end events that are already explained elsewhere.

**Data flow**: It receives a subagent activity frame, chooses a label from the subagent name or profile, and returns a `note` directive only when there is actual activity text.

**Call relations**: `directives_for` calls this when it sees a `SubagentActivity` frame, keeping the main translator simpler.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for).


##### `_answer`  (lines 318–388)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True, runtime: RuntimeIdentity
```

**Purpose**: Builds the final terminal lines for a completed, failed, or cancelled turn. It decides whether to show the answer, shared files, credential prompts, connection links, an `ask` prompt, or an `exit` instruction.

**Data flow**: It receives a terminal frame and surrounding context. It creates optional runtime information, file lines, secret prompts, connection text, safe error text, and final prompt or exit directives depending on the terminal status.

**Call relations**: `directives_for` hands terminal frames to this function. `_answer` uses `_say_lines` and `directive` to produce the exact wire lines.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for); 1 external calls (__init__).


##### `_say_lines`  (lines 391–392)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Formats ordinary text as one or more `say` directives. Splitting by line keeps multi-line messages readable in the terminal protocol.

**Data flow**: It receives a string, splits it into lines, and returns one `say` directive per line, preserving even an empty text as a single line.

**Call relations**: `_answer` uses this whenever final answer text or error text must be sent as normal spoken output.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `_render_stream_frame`  (lines 403–450)

```
async def _render_stream_frame(frame: LiveFrame, streamed: bool, pending: Callable[[str, str], Awaitable[bool]] | None, connect: Callable[[], Awaitable[str]] | None, files: Callable[[], Awaitable[tupl
```

**Purpose**: Prepares one live frame for rendering by gathering the extra facts terminal endings may need. It also reports whether this frame streamed text, ended the turn, or left the user at a prompt.

**Data flow**: It receives a live frame, stream state, optional callbacks for pending secrets, connection URLs, shared files, exit behavior, and runtime identity. It checks credential prompts, connection requests, and shared files when relevant, calls `directives_for`, and returns a `_RenderedFrame` summary.

**Call relations**: `stream_directives` calls this for each frame it reads from the turn tail. It is the step between raw frame reading and sending directive bytes.

*Call graph*: calls 1 internal fn (directives_for); called by 1 (stream_directives); 1 external calls (__init__).


##### `_stream_end_directives`  (lines 453–476)

```
async def _stream_end_directives(turn_id: UUID, rendered_cursor: str, terminated: bool, ran: bool, prompting: bool, moved_on: Callable[[], Awaitable[bool]] | None) -> tuple[bytes, ...]
```

**Purpose**: Decides what instruction to send when a held stream is about to close. It tells the client whether to poll soon, listen while idle, or do nothing.

**Data flow**: It receives the turn id, last rendered cursor, whether the turn ended, whether a terminal operation was started, whether the prompt is available, and an optional check for a newer active turn. It returns `since` plus `poll` or `listen` directives when the client should reconnect.

**Call relations**: `stream_directives` calls this after its frame-reading loop ends. This is what makes reconnects safe and prevents duplicated output.

*Call graph*: calls 1 internal fn (directive); called by 1 (stream_directives).


##### `_cancel_stream_tasks`  (lines 479–486)

```
async def _cancel_stream_tasks(*tasks: asyncio.Task[Any] | None) -> None
```

**Purpose**: Cleans up background tasks created while racing live frames against terminal operations. This prevents leftover asynchronous work after the stream ends.

**Data flow**: It receives optional asyncio tasks, cancels any that exist, then awaits them while ignoring expected cancellation and terminal-gone errors.

**Call relations**: `stream_directives` calls this in a `finally` block so cleanup happens whether the stream ended normally, timed out, or was interrupted.

*Call graph*: called by 1 (stream_directives); 1 external calls (suppress).


##### `stream_directives`  (lines 489–613)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Streams live turn activity to the terminal client for a limited time. It is the core long-polling loop: read frames, render them, send terminal operations when needed, and tell the client how to resume.

**Data flow**: It receives a tail of live frames, a hold timeout, optional callbacks for secrets, connection URLs, shared files, terminal operations, and resume state. It opens the tail, waits for either the next frame or the next terminal operation, yields directive bytes, updates the cursor, stops on terminal/park/run/timeout, then yields reconnect instructions if needed.

**Call relations**: `_ChannelStream.response` uses this to produce the body of a streaming HTTP response. It calls `_next`, `_render_stream_frame`, `_stream_end_directives`, `_cancel_stream_tasks`, and `directive` while coordinating the live flow.

*Call graph*: calls 5 internal fn (_cancel_stream_tasks, _next, _render_stream_frame, _stream_end_directives, directive); called by 1 (response); 3 external calls (ensure_future, get_running_loop, wait).


##### `_next`  (lines 616–622)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next live frame from an async iterator, returning `None` at the end instead of raising an exception. This makes the stream loop easier to race with timeouts.

**Data flow**: It receives an async frame iterator, awaits its next item, and returns either the `(cursor, frame)` pair or `None` if the iterator is exhausted.

**Call relations**: `stream_directives` wraps this in an asyncio task while waiting for either a frame or a terminal operation to finish first.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 625–629)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Verifies the request's bearer token and extracts the email it proves. It is the low-level authentication check for member identity.

**Data flow**: It reads the `Authorization` header, extracts the bearer token, verifies it against the workspace id, and returns an email string or `None`.

**Call relations**: `_authenticated_member` calls this first, then links that email to a member record and checks access.

*Call graph*: called by 1 (_authenticated_member); 1 external calls (verify_token).


##### `_authenticated_member`  (lines 632–645)

```
async def _authenticated_member(ctx: SurfaceContext, request: Request) -> tuple[str, UUID | None] | None
```

**Purpose**: Authenticates a request and resolves it to an email plus an optional member id. It allows an email with no member row to have an unlinked conversation, but rejects revoked members.

**Data flow**: It receives the surface context and request, verifies the email token, looks up or creates the member link, checks whether the linked member still has access, and returns `(email, member_id)` or `None`.

**Call relations**: All protected endpoints call this before doing work, including chat channels, terminal op reads, skill downloads, environment uploads, and workspace file endpoints.

*Call graph*: calls 4 internal fn (link_member, linked_member, member_has_access, _authenticated_email); called by 8 (channel, op_body, store_environment, store_environment_file, system_skills, workspace_file, workspace_listing, workspace_upload).


##### `_utf8_header`  (lines 648–655)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Recovers a header value that the shell client meant as UTF-8 text. This matters for paths that may contain non-ASCII characters.

**Data flow**: It reads a header string, reverses the server's Latin-1 header decoding back to bytes, decodes those bytes as UTF-8 with replacement for invalid data, and returns the result.

**Call relations**: `channel` uses this for the current working directory header, and `_channel_op_reply` uses it for terminal operation error text.

*Call graph*: called by 2 (_channel_op_reply, channel).


##### `_stale_client`  (lines 658–662)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Checks whether the command-line script version in the request differs from the version the server wants clients to use. This lets the server prompt an update when needed.

**Data flow**: It reads the served client version from the environment and compares it with the request's script-version header. If no served version is configured, it returns false.

**Call relations**: `channel` computes this once and passes the result into message, stop, and operation-reply paths so they can decide whether to continue or ask the client to update.

*Call graph*: called by 1 (channel).


##### `_client_update`  (lines 665–666)

```
def _client_update() -> bytes
```

**Purpose**: Builds the terminal instructions that tell the client to install the updated script and show a short update message.

**Data flow**: It creates an `install` directive followed by a `say` directive and returns the combined bytes.

**Call relations**: `_channel_message` and `_channel_stop` use this when a stale client should be upgraded instead of continuing normally.

*Call graph*: calls 1 internal fn (directive); called by 2 (_channel_message, _channel_stop).


##### `_resumed_from`  (lines 669–676)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Finds the cursor a reconnecting client can safely resume from for the current turn. It ignores cursors that belong to a different turn.

**Data flow**: It reads the `x-ufo-since` header, splits it into turn id and cursor, compares the turn id to the current turn, and returns the cursor only on a match.

**Call relations**: `_ChannelStream.response` calls this before opening the tail, so live replay starts at the right point and does not skip frames from a new turn.

*Call graph*: called by 1 (response).


##### `_turn_context`  (lines 679–691)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the context attached to a newly admitted user message. It records who sent it, where it came from, and optionally the user's timezone.

**Data flow**: It receives the sender email and request, reads the timezone header, creates a `TurnContext`, and if the timezone is invalid logs the problem and falls back to a context without timezone.

**Call relations**: `_channel_message` and `_send` call this just before admitting a message into the conversation engine.

*Call graph*: called by 2 (_channel_message, _send); 2 external calls (__init__, log).


##### `_runtime_config`  (lines 694–712)

```
def _runtime_config(ctx: SurfaceContext, request: Request) -> TurnRuntimeConfig | None
```

**Purpose**: Reads optional per-turn runtime choices from request headers, such as model, internet narrowing, or environment digest. It validates those choices before the turn is admitted.

**Data flow**: It reads model, internet, and environment headers. If none are present it returns `None`; otherwise it builds a `TurnRuntimeConfig`, rejects invalid values, asks the context to validate it, and returns the config.

**Call relations**: `_channel_message` and `_send` use this so a terminal request can pin runtime choices for the turn it is starting or extending.

*Call graph*: calls 1 internal fn (validate_runtime_config); called by 2 (_channel_message, _send); 1 external calls (__init__).


##### `_channel_op_reply`  (lines 724–745)

```
async def _channel_op_reply(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, op_id: str, stale: bool) -> Response | _ChannelTurn
```

**Purpose**: Receives the client-side result of a terminal operation the agent asked to run. It records the result and then resumes the relevant turn stream.

**Data flow**: It reads the request body as the operation reply, rejects oversized replies, reads any operation error header, resolves the operation through the context, and returns either an immediate HTTP response or a `_ChannelTurn` to stream.

**Call relations**: `channel` calls this when the request contains an operation id header. It hands the operation result to the surface context and then points the channel flow back at the latest turn.

*Call graph*: calls 4 internal fn (latest_turn, terminal_resolve, _utf8_header, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_channel_stop`  (lines 748–759)

```
async def _channel_stop(ctx: SurfaceContext, request: Request, conversation_id: UUID, stale: bool) -> Response | _ChannelTurn
```

**Purpose**: Processes a user stop request, such as pressing Escape. It asks the engine to stop the current turn without admitting a new message.

**Data flow**: It rejects any request body, finds the latest turn, stops it if present, and returns either a prompt/update response or a `_ChannelTurn` for the stopped turn's tail.

**Call relations**: `channel` calls this when the stop header is present. The returned turn is streamed so the client sees the cancellation frame committed by the engine.

*Call graph*: calls 4 internal fn (latest_turn, stop_turn, _client_update, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_channel_message`  (lines 762–812)

```
async def _channel_message(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str, stale: bool, marked: bool) -> Response | _ChannelTurn
```

**Purpose**: Processes the normal channel request path: either admit a new message or resume/listen to an existing turn when the body is empty.

**Data flow**: It reads and trims the request body. Empty bodies find the latest turn and decide whether to prompt, update the client, idle-listen, or resume. Non-empty bodies check client version, size, runtime config, terminal workspace claim, and then admit the message, returning turn information and a `sent` acknowledgement.

**Call relations**: `channel` calls this when the request is not a secret, send, unsend, operation reply, or stop. If it returns a `_ChannelTurn`, `channel` wraps it in `_ChannelStream` for streaming.

*Call graph*: calls 8 internal fn (admit, claim_terminal, latest_turn, turn_is_terminal, _client_update, _runtime_config, _turn_context, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_ChannelStream.response`  (lines 825–861)

```
async def response(self) -> Response
```

**Purpose**: Builds the streaming HTTP response for one channel turn. It prepares resume state, optional history, connection helpers, file lookup, terminal operation lookup, and the directive stream.

**Data flow**: It reads request headers to find the resume cursor, may load transcript history for a fresh resume, wires callbacks into `stream_directives`, and returns a plain-text `StreamingResponse` whose body comes from `_bound`.

**Call relations**: `channel` creates a `_ChannelStream` after resolving a request to a turn, then calls this method to produce the actual held response.

*Call graph*: calls 4 internal fn (_bound, _resumed_from, history_directives, stream_directives); 2 external calls (partial, StreamingResponse).


##### `_ChannelStream._moved_on`  (lines 863–869)

```
async def _moved_on(self) -> bool
```

**Purpose**: Checks whether the conversation has already advanced to a newer non-terminal turn. This tells the stream whether it should immediately poll into the next turn after finishing the current one.

**Data flow**: It asks the context for the latest turn in the conversation, compares it with this stream's turn id, checks whether the latest turn is still active, and returns a boolean.

**Call relations**: _ChannelStream.response passes this method into `stream_directives` as the `moved_on` callback. The stream uses it when deciding its final reconnect directives.


##### `_ChannelStream._bound`  (lines 871–888)

```
async def _bound(self, history: tuple[bytes, ...], directives: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Wraps the directive stream with terminal connection lifetime and initial lines. It connects the terminal while streaming and disconnects it afterward.

**Data flow**: It may compute a runtime id and announce terminal connection, then yields the `sent` acknowledgement, replayed history, workspace note, and live directives in order. In a final cleanup step, it disconnects the terminal if one was connected.

**Call relations**: _ChannelStream.response uses this as the body iterator for the `StreamingResponse`. It calls `terminal_runtime_id` when a current working directory is present.

*Call graph*: calls 1 internal fn (terminal_runtime_id); called by 1 (response).


##### `channel`  (lines 891–948)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles the main `POST /{channel}` endpoint for the terminal client. It authenticates the member, chooses what kind of request this is, and routes it to the right helper.

**Data flow**: It authenticates the request, handles secret fulfillment early, reads the working directory, finds or creates the conversation, checks client freshness, and then dispatches to send, unsend, operation reply, stop, or normal message handling. If the helper returns a turn, it creates a `_ChannelStream` and returns its streaming response.

**Call relations**: This is the central request router for the terminal conversation. It calls the authentication, stale-client, message, stop, operation, send, unsend, and secret helpers, and it is registered in `ROUTES`.

*Call graph*: calls 10 internal fn (conversation_for, _authenticated_member, _channel_message, _channel_op_reply, _channel_stop, _fulfill_secret, _send, _stale_client, _unsend, _utf8_header); 3 external calls (__init__, conversation_audience, PlainTextResponse).


##### `_send`  (lines 951–1011)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str) -> Response
```

**Purpose**: Admits a message through the fast send path and returns immediately with an acknowledgement. It is used when another held stream is already available to show the consequences.

**Data flow**: It validates a UUID send id, reads and validates the message body, validates runtime config, optionally claims the terminal workspace, admits the message with an idempotency key, and returns `sent` plus any workspace note.

**Call relations**: `channel` calls this when the send header is present. Unlike the normal message path, it does not open a streaming response; the existing held stream will receive the turn frames.

*Call graph*: calls 5 internal fn (admit, claim_terminal, _runtime_config, _turn_context, directive); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 1014–1036)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Retracts a queued message that has not yet been absorbed into a turn. This lets the client take back words that are still pending.

**Data flow**: It rejects request bodies, requires a real member id, parses the arrival id, asks the context to retract that arrival for this member and conversation, and returns success or a conflict message.

**Call relations**: `channel` calls this when the unsend header is present. It does not admit a turn or start a stream.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 1039–1060)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores a private credential value entered by the member. The value is not treated as a chat message and is not added to the transcript.

**Data flow**: It reads the credential slot header and secret body, rejects empty or oversized values, asks the context to fulfill the sealed credential request, and returns a `say` directive describing success or why storage failed.

**Call relations**: `channel` calls this early when the secret header is present. The context performs the privileged checks around seal validity, member, slot, and credential value.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 1063–1075)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the byte body for an in-flight terminal operation. This lets the client fetch operation payloads, such as data to write to a local file.

**Data flow**: It authenticates the member, builds the member-scoped queue key, asks the context for the operation body by channel and operation id, and returns bytes or a not-found response.

**Call relations**: This is registered as a GET route for `{channel}/op/{op_id}`. It uses the same authentication helper as the chat endpoint but does not create or admit anything.

*Call graph*: calls 2 internal fn (terminal_op_body, _authenticated_member); 2 external calls (PlainTextResponse, Response).


##### `system_skills`  (lines 1078–1090)

```
async def system_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets an authenticated client download the current system skill bundle as a zip archive. It supports caching through an entity tag, or ETag, which is a version fingerprint.

**Data flow**: It authenticates the request, reads the bundle digest and archive from the context, compares the request's `if-none-match` header with the current ETag, and returns either 304 not modified or the zip bytes.

**Call relations**: This is registered as the `{channel}/skills` GET route. It relies on `_authenticated_member` to protect access.

*Call graph*: calls 1 internal fn (_authenticated_member); 2 external calls (PlainTextResponse, Response).


##### `store_environment`  (lines 1093–1103)

```
async def store_environment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores an environment document and returns its digest. Later turns can refer to that digest to pin the environment they should run with.

**Data flow**: It authenticates the request, reads the body bytes, asks the context to store the document, and returns the digest or a bad-request error if the document is invalid.

**Call relations**: This route is used before or around turn admission when the client needs to upload runtime environment metadata. `channel` later reads the environment digest from a header through `_runtime_config`.

*Call graph*: calls 2 internal fn (store_environment_document, _authenticated_member); 2 external calls (PlainTextResponse, body).


##### `store_environment_file`  (lines 1106–1115)

```
async def store_environment_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores a file referenced by an environment document and returns its digest. The same file bytes produce the same digest, so uploads are content-addressed.

**Data flow**: It authenticates the request, reads the uploaded bytes, asks the context to store them as an environment file, and returns the digest or a bad-request message.

**Call relations**: This complements `store_environment`: files are uploaded first or alongside a document, and the document can then refer to their digests.

*Call graph*: calls 2 internal fn (store_environment_file, _authenticated_member); 2 external calls (PlainTextResponse, body).


##### `workspace_file`  (lines 1118–1138)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams a file out of a channel's workspace for `ufo cp` downloads. It only reads existing conversation workspace data and does not create a conversation.

**Data flow**: It authenticates the member, builds the member-scoped queue key, finds the conversation, asks the context to open the requested workspace file, and returns a byte stream or a uniform not-found response.

**Call relations**: This is the download half of workspace file transfer. It uses `_authenticated_member` and context file-reading methods, then returns a `StreamingResponse` for successful reads.

*Call graph*: calls 3 internal fn (find_conversation, read_workspace_file, _authenticated_member); 2 external calls (PlainTextResponse, StreamingResponse).


##### `workspace_upload`  (lines 1141–1159)

```
async def workspace_upload(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Uploads one file into a channel's workspace for `ufo cp`. It can create the conversation so files may be staged before the first message.

**Data flow**: It authenticates the member, validates the path, gets or creates the conversation, streams the request body into the workspace writer, and returns 204 on success or an error if the upload is refused.

**Call relations**: This is the upload half of workspace file transfer. It shares conversation scoping with the main channel route and uses the context to enforce workspace write limits.

*Call graph*: calls 3 internal fn (conversation_for, write_workspace_file, _authenticated_member); 4 external calls (conversation_audience, PlainTextResponse, stream, Response).


##### `workspace_listing`  (lines 1162–1187)

```
async def workspace_listing(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files in a channel's workspace so the client can compare and sync local files. A channel with no conversation simply lists no files.

**Data flow**: It authenticates the member, finds the conversation for the member and channel, asks the context for file entries if the conversation exists, and returns JSON with path, size, and modified time for each file.

**Call relations**: This supports `ufo cp` synchronization. It is registered as the `{channel}/files` GET route and depends on the same member-scoped queue key used by chat and file endpoints.

*Call graph*: calls 3 internal fn (find_conversation, list_workspace_files, _authenticated_member); 3 external calls (dumps, PlainTextResponse, Response).
