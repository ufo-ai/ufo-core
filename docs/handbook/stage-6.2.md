# Member-facing and operator-facing surfaces  `stage-6.2`

This stage is the system’s set of front doors. It is used during the main work loop, whenever a member, operator, or outside chat service talks to UFO. The web portal serves the browser app, signs members in, shows agents and transcripts, sends messages, and streams replies. Its panels turn button and form actions into the same chat-style work path, while community and starters fill the home screen with public skills and suggested starting ideas.

Other doors connect outside apps. Slack, iMessage, and the terminal UFO client translate incoming messages into UFO conversation turns, then translate replies, files, status updates, and questions back out. Slack helper files keep mentions readable and make bot attribution clear.

Hosted-site files open public or permission-checked app pages, build safe sandbox URLs, and route browser traffic to stored files or live sandbox ports. Operator tools provide a debugger, workspace directory, memory viewer, and problem reports, with strict operator sign-in rules. Behind all of this, the hub and surface bridge carry live updates, replay missed events, match users to workspaces, and deliver final replies.

## Files in this stage

### Member Web Portal
Browser-facing member pages and helpers provide authenticated chat, workspace actions, community skill browsing, and personalized starter prompts.

### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling, live streaming, and scheduled background jobs`

Think of this file as the front desk for the web version of UFO. A browser request arrives, and this code checks the signed session cookie, works out which workspace and member it belongs to, and then routes the request to the right piece of portal behavior. It serves the built HTML, JavaScript, CSS, and app pages; it also publishes those assets into shared blob storage so rolling deploys do not break pages that were loaded from another server version.

Most of the file is made of HTTP route handlers. Some are simple reads, such as listing agents, sources, team members, credentials, skills, usage, and connections. Others are active paths: opening a session, sending a chat message, stopping a turn, uploading an attachment, fulfilling a credential prompt, starting account-login flows, or applying an object change.

The chat pieces are especially important. They create or find the right conversation, save uploaded files into the conversation workspace, admit the member's message into the durable queue, and then stream live agent frames back to the browser through SSE, which is a simple browser-friendly event stream. The transcript renderer turns raw model messages, tool activity, spawned subagents, questions, shared files, created apps, and connection prompts into the clean chat bubbles the portal displays.

#### Function details

##### `load_assets`  (lines 324–334)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Reads the portal's built static files from disk and keeps only file types the server knows how to serve. This prevents accidental files, such as source maps or stray build output, from being published without an explicit decision.

**Data flow**: It receives a directory path → scans the files directly inside it → reads allowed files into memory with their media type → returns a lookup table from request name to bytes and content type.

**Call relations**: It runs during module loading to build the static asset table later used by static file responses.

*Call graph*: 1 external calls (glob).


##### `rum_config`  (lines 347–360)

```
def rum_config(environ: Mapping[str, str]) -> dict[str, str] | None
```

**Purpose**: Builds the browser monitoring configuration for Datadog RUM, or disables it when no monitoring settings are present. It refuses partial configuration so sessions are not silently recorded to the wrong place.

**Data flow**: It receives environment variables → collects the required monitoring fields → returns either a complete config dictionary or None, or raises an error if only some fields are set.

**Call relations**: portal_page calls it just before serving the HTML shell so the deployed environment, not the build, decides whether monitoring is active.

*Call graph*: called by 1 (portal_page).


##### `portal_shell`  (lines 363–372)

```
def portal_shell(html: str, config: Mapping[str, str] | None) -> str
```

**Purpose**: Injects the runtime monitoring configuration into the built HTML shell. It makes sure the expected placeholder exists, so a broken frontend build fails loudly.

**Data flow**: It receives HTML and optional config → serializes the config safely for a script tag → replaces the single RUM placeholder → returns finished HTML.

**Call relations**: portal_page uses this after choosing which shell to serve.

*Call graph*: called by 1 (portal_page); 1 external calls (dumps).


##### `resolve_workspace`  (lines 385–427)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace a request belongs to before the route handler runs. It reads the signed bearer from the session cookie, or from the one allowed sign-in POST, and redirects cold browser arrivals to login.

**Data flow**: It receives the HTTP request → checks the session cookie, possibly checks a small form body, and possibly preserves a chat target → returns a workspace UUID, a redirect/error response, or None for unauthorized.

**Call relations**: The shared surface router calls this as the first scoping step; it relies on _framed_length and _form for safe form reading and _chat_target for preserving conversation links.

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 430–434)

```
def _chat_target(request: Request) -> UUID | None
```

**Purpose**: Extracts an optional conversation id from the URL when a member arrives through a chat link. It only accepts real UUIDs.

**Data flow**: It reads the request query parameter → tries to parse it as a UUID → returns the UUID or None.

**Call relations**: resolve_workspace uses it when redirecting an unauthenticated browser to login.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 437–447)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Serves a known local portal asset if the current build has it. It avoids arbitrary file reads by looking up the asset in a prebuilt in-memory table.

**Data flow**: It receives a request → strips the static URL prefix → finds the asset bytes and content type → returns a cache-aware response or None.

**Call relations**: static_asset tries this first before falling back to stored assets from another build.

*Call graph*: calls 1 internal fn (_asset_response); called by 1 (static_asset).


##### `_asset_response`  (lines 450–454)

```
def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response
```

**Purpose**: Builds the HTTP response for one static asset, including browser cache revalidation. If the browser already has the current version, it sends a 304 instead of the bytes.

**Data flow**: It receives request headers, asset bytes, media type, and an ETag hash → compares the browser's cached tag → returns either an empty 304 response or the asset body.

**Call relations**: _static_response and _stored_asset both use it so local and shared-store assets behave the same way.

*Call graph*: called by 2 (_static_response, _stored_asset); 1 external calls (Response).


##### `load_apps`  (lines 480–513)

```
def load_apps(directory: Path) -> AppsBundle | None
```

**Purpose**: Loads the built shipped app pages from disk and gives the whole tree a content digest. The digest lets the system serve app bundles safely across rolling deploys.

**Data flow**: It receives an app build directory → reads all non-hidden files under it → hashes paths and bytes → returns an AppsBundle with files, digest, and available app slugs, or None if absent.

**Call relations**: It runs during import; apps() later enforces that the bundle exists when a route needs it.

*Call graph*: 4 external calls (__init__, sha256, is_dir, rglob).


##### `apps`  (lines 519–525)

```
def apps() -> AppsBundle
```

**Purpose**: Returns the built app bundle or raises a clear build error. This lets the extension load even if frontend assets are missing, while failing only routes that truly need them.

**Data flow**: It reads the module-level APPS value → returns it if present → otherwise raises an instruction to run the build.

**Call relations**: agents_index, homepage, and _homepage_state call it before handing out app page links.

*Call graph*: called by 3 (_homepage_state, agents_index, homepage).


##### `_publish_assets`  (lines 528–538)

```
async def _publish_assets(blob: BlobStore, apps: AppsBundle) -> None
```

**Purpose**: Copies this process's portal and app assets into shared blob storage if they are not already there. This keeps old and new browser pages working during mixed-version deployments.

**Data flow**: It receives a blob store and app bundle → checks which keys already exist → writes missing static files and app files → changes blob storage, returns nothing.

**Call relations**: _assets_published wraps this so the publish happens once per process and can be retried after failure.

*Call graph*: calls 3 internal fn (exists, list, put); called by 1 (_assets_published).


##### `_assets_published`  (lines 541–568)

```
def _assets_published(blob: BlobStore, apps: AppsBundle) -> 'asyncio.Task[None]'
```

**Purpose**: Starts or reuses the process-wide task that publishes built assets to shared storage. It prevents every page request from doing the same work.

**Data flow**: It receives blob storage and an app bundle → checks the cached async task → starts publishing if needed → returns the task callers can await.

**Call relations**: portal_page, agents_index, and homepage await this before serving pages or page links that depend on published assets.

*Call graph*: calls 1 internal fn (_publish_assets); called by 3 (agents_index, homepage, portal_page); 1 external calls (create_task).


##### `_stored_asset`  (lines 571–593)

```
async def _stored_asset(blob: BlobStore, request: Request) -> Response
```

**Purpose**: Serves a static asset from shared blob storage when the current process does not have that build locally. This is the safety net for pages loaded during a rolling deploy.

**Data flow**: It receives blob storage and a request → validates the asset name and suffix → reads and caches the bytes from the blob store → returns the same kind of asset response as local files.

**Call relations**: static_asset uses it after _static_response fails to find a local asset.

*Call graph*: calls 3 internal fn (exists, get, _asset_response); called by 1 (static_asset); 3 external calls (sha256, Path, Response).


##### `portal_page`  (lines 596–617)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main web portal HTML shell to an authenticated member. It chooses between the lane and sidebar shells by feature flag and inserts runtime monitoring settings.

**Data flow**: It receives context and request → checks built assets, publishes them, reads a flag and environment config → returns no-store HTML.

**Call relations**: This is the GET route for the portal root; it depends on _assets_published, portal_shell, and rum_config.

*Call graph*: calls 3 internal fn (_assets_published, portal_shell, rum_config); 2 external calls (flag_enabled, HTMLResponse).


##### `_refused`  (lines 633–634)

```
def _refused(message: str) -> Response
```

**Purpose**: Creates a standard JSON refusal used by account connection flows. It gives the browser a clear status and message.

**Data flow**: It receives a message → wraps it with status 'refused' → returns a JSON response.

**Call relations**: openai_device_poll and anthropic_code use it for provider-side failures.

*Call graph*: called by 2 (anthropic_code, openai_device_poll); 1 external calls (JSONResponse).


##### `workspace_accounts`  (lines 637–656)

```
async def workspace_accounts(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports whether the signed-in member has connected their coding accounts, such as OpenAI or Anthropic. It never returns secret values.

**Data flow**: It authenticates the request → checks each known member credential slot → returns provider rows with connected true or false.

**Call relations**: The account settings and first-run screens call this route to draw the same connection state.

*Call graph*: calls 2 internal fn (member_credential_stored, _authenticate); 1 external calls (JSONResponse).


##### `openai_device`  (lines 659–682)

```
async def openai_device(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the OpenAI device login flow, where the member types a short code at OpenAI. The sensitive device handle stays in an HttpOnly cookie.

**Data flow**: It authenticates the member → asks OpenAI for a device code → stores the hidden claim handle in a cookie → returns the user code, verification URL, and polling interval.

**Call relations**: The browser calls this before polling openai_device_poll.

*Call graph*: calls 1 internal fn (_authenticate); 4 external calls (__init__, JSONResponse, set_session_cookie, openai_client_id).


##### `openai_device_poll`  (lines 685–702)

```
async def openai_device_poll(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Checks whether the member finished the OpenAI device login. When OpenAI grants a key, this stores it in the member's private credential slot.

**Data flow**: It authenticates the member → reads the device cookie → asks OpenAI whether the grant is pending, refused, or complete → returns status and may store the key.

**Call relations**: It follows openai_device and uses _refused for failed or unavailable flows.

*Call graph*: calls 3 internal fn (put_member_credential, _authenticate, _refused); 4 external calls (__init__, JSONResponse, openai_client_id, log).


##### `anthropic_authorize`  (lines 705–716)

```
async def anthropic_authorize(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the Anthropic code login flow. It gives the browser a provider URL and stores the verifier state in a cookie.

**Data flow**: It authenticates the member → creates an Anthropic authorization request → sets a state cookie → returns the URL the browser should open.

**Call relations**: The browser later posts the pasted code to anthropic_code.

*Call graph*: calls 1 internal fn (_authenticate); 4 external calls (__init__, JSONResponse, set_session_cookie, anthropic_client_id).


##### `anthropic_code`  (lines 719–741)

```
async def anthropic_code(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Finishes Anthropic login using a code the member pasted back. It verifies the grant and stores the resulting credential for that member.

**Data flow**: It bounds and parses the form → authenticates the member → validates the pasted code with the stored state cookie → verifies the key → stores it or returns a refusal.

**Call relations**: It is the completion step after anthropic_authorize and shares _form, _framed_length, and _refused with other form routes.

*Call graph*: calls 5 internal fn (put_member_credential, _authenticate, _form, _framed_length, _refused); 5 external calls (__init__, JSONResponse, anthropic_client_id, log, verified_key).


##### `account_disconnect`  (lines 744–757)

```
async def account_disconnect(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Removes one of the member's connected coding accounts so they can replace or revoke it. It only clears the member's own credential.

**Data flow**: It authenticates → maps the provider name to a credential slot → clears that slot → returns disconnected or unknown provider.

**Call relations**: Account settings call this route when a member disconnects a provider.

*Call graph*: calls 2 internal fn (clear_member_credential, _authenticate); 2 external calls (JSONResponse, log).


##### `connect_arrival`  (lines 760–765)

```
async def connect_arrival(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Sends a member who clicked a provider sign-in arrival link back into the portal credentials screen. The actual flow lives in the portal UI.

**Data flow**: It authenticates the request → returns a redirect to the portal hash for workspace credentials.

**Call relations**: The sign-in routes for OpenAI and Anthropic use this simple landing behavior.

*Call graph*: calls 1 internal fn (_authenticate); 1 external calls (RedirectResponse).


##### `_authenticate`  (lines 768–792)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

**Purpose**: Verifies the session cookie and turns it into a workspace member id and email. It also links the email to a member record on first use and reports whether access was removed.

**Data flow**: It reads the session cookie → verifies the bearer for the current workspace → finds or creates the member link → checks seat access → returns member id and email or an HTTP refusal.

**Call relations**: Nearly every authenticated route reaches this directly or through _audience_for.

*Call graph*: calls 3 internal fn (link_member, linked_member, member_has_access); called by 9 (_audience_for, account_disconnect, anthropic_authorize, anthropic_code, connect_arrival, fulfill_credential, openai_device, openai_device_poll, workspace_accounts); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 795–801)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a portal static asset to an already scoped session. Assets contain no workspace data, but the shell only points to them after authentication.

**Data flow**: It receives context and request → tries local build assets → if missing, tries shared blob storage → returns the asset or a 404.

**Call relations**: This is the route behind /static; it combines _static_response and _stored_asset.

*Call graph*: calls 2 internal fn (_static_response, _stored_asset).


##### `open_session`  (lines 804–835)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Turns a posted bearer token into the browser's session cookie. The token travels in the form body, not the URL.

**Data flow**: It bounds and parses the form → validates that the token field is present and cookie-safe → sets the session cookie → redirects back to the portal.

**Call relations**: This is the one POST that opens a web session; later requests are verified by resolve_workspace and _authenticate.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 838–842)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Parses the agent id from a route path. It returns None instead of throwing when the path does not contain a valid UUID.

**Data flow**: It reads request path parameters → tries to build a UUID → returns the UUID or None.

**Call relations**: Chat, transcript, panel, and member-chat gates use it before checking audience access.

*Call graph*: called by 4 (_member_chat_page, _panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 845–846)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the store key for the web-specific record attached to a conversation. That record proves which agent and email own a web chat.

**Data flow**: It receives a conversation id → formats it under the chat store prefix → returns the key string.

**Call relations**: _open_conversation writes this key, and _own_web_chat reads it.

*Call graph*: called by 2 (_open_conversation, _own_web_chat).


##### `_chat_title`  (lines 855–873)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates the short title shown in the chat rail from the first message or attached filenames. It trims cleanly so titles do not end on dangling words.

**Data flow**: It receives message text and attachment paths → collapses whitespace or uses filenames → cuts to the maximum length at a sensible word boundary → returns a title string.

**Call relations**: _open_conversation uses it for new chats, and summarize_chat_titles uses it to clean model-written titles.

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_title_excerpt`  (lines 885–903)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

**Purpose**: Builds the small conversation excerpt used to ask the model for a better chat title. It includes the first user and assistant text when an assistant answer exists.

**Data flow**: It receives transcript messages → extracts rendered user and assistant text → removes web context from the user side → returns a bounded excerpt or an empty string.

**Call relations**: summarize_chat_titles uses it before calling the model.

*Call graph*: calls 1 internal fn (_rendered_text); called by 1 (summarize_chat_titles); 1 external calls (member_message_text).


##### `summarize_chat_titles`  (lines 906–952)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

**Purpose**: Background job that gives conversations nicer titles after they have an opening exchange. It records every attempt so conversations that cannot be summarized do not block the queue.

**Data flow**: It reads conversations awaiting a title → fetches transcripts → builds excerpts → optionally asks the model for a short title → stores the summary or an empty marker.

**Call relations**: This scheduled job uses _title_excerpt and _chat_title and writes back through the extension context.

*Call graph*: calls 4 internal fn (conversations_awaiting_title, summarized_conversation_title, _chat_title, _title_excerpt); 2 external calls (__init__, __init__).


##### `seed_homepages`  (lines 955–1027)

```
async def seed_homepages(ctx: ExtensionContext, bucket: str | None=None) -> None
```

**Purpose**: Background job that asks eligible agents to build their first homepage once. It marks skipped or completed agents so settled work does not repeat forever.

**Data flow**: It reads workspace agents and existing seed markers → skips archived, shipped, disallowed, or ownerless agents as appropriate → opens a conversation and invokes a homepage build turn → records the marker when accepted.

**Call relations**: This scheduled job uses core conversation and invocation APIs rather than web request routes.

*Call graph*: calls 5 internal fn (earliest_seated_admin, invoke, open_conversation, turn_outcomes, workspace_agents); 3 external calls (__init__, now, shipped_app_slug).


##### `_open_conversation`  (lines 1030–1063)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and writes the web ownership row first. This avoids a conversation existing without the web record that later gates access.

**Data flow**: It receives agent, member, email, queue key, message text, and paths → writes a tentative chat row → asks core for the durable conversation → cleans up if another request won the same queue key → returns conversation id and title.

**Call relations**: _new_chat_target calls it when the first message opens a chat.

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_web_chat); called by 1 (_new_chat_target); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 1066–1073)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

**Purpose**: Reads the current title of one conversation from the same listing used elsewhere. This keeps rail titles and response titles consistent.

**Data flow**: It receives agent, member, and conversation ids → asks for the one matching conversation → returns its title or an empty string.

**Call relations**: _open_conversation uses it after losing a creation race.

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 1 (_open_conversation).


##### `_own_web_chat`  (lines 1076–1088)

```
async def _own_web_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is this member's own web chat with this agent. Anything mismatched is treated as not found.

**Data flow**: It receives the store, agent id, email, and conversation id → reads the stored chat row → validates agent and email → returns the ChatRecord or None.

**Call relations**: _member_chat and _open_conversation use it as the web-chat ownership proof.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 2 (_member_chat, _open_conversation).


##### `_member_chat`  (lines 1091–1136)

```
async def _member_chat(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, *, agent_visible: bool) -> ListedConversation | None
```

**Purpose**: Decides whether the signed-in member may continue or comment on a conversation through the portal. It covers web chats, private extension rooms, and commentable Slack or terminal conversations.

**Data flow**: It receives context, store, ids, email, and visibility → checks the web chat row and core conversation listing → applies audience and surface rules → returns the listed conversation or None.

**Call relations**: Chat target resolution, permalink resolution, stream authorization, and transcript reads all use this shared decision.

*Call graph*: calls 3 internal fn (list_agent_conversations, _commentable, _own_web_chat); called by 5 (_existing_chat_target, _member_chat_page, _member_turn, _resolve_chat, transcript); 1 external calls (conversation_audience).


##### `_commentable`  (lines 1139–1143)

```
def _commentable(conversation: ListedConversation, member_id: UUID) -> bool
```

**Purpose**: Checks whether a listed conversation can receive portal comments. Only Slack and terminal-style conversations with shared or member audience qualify.

**Data flow**: It receives a conversation row and member id → compares its surface and audience → returns true or false.

**Call relations**: _member_chat, _existing_chat_target, _member_turn, _resolve_chat, and _conversation_row use the same rule.

*Call graph*: called by 5 (_conversation_row, _existing_chat_target, _member_chat, _member_turn, _resolve_chat); 1 external calls (conversation_audience).


##### `_turn_context`  (lines 1146–1157)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

**Purpose**: Builds the extra context attached to an admitted member turn, including sender, source, and browser timezone. Invalid timezone names are ignored rather than blocking a message.

**Data flow**: It reads the timezone header and sender email → validates the timezone through TurnContext → returns context with or without timezone.

**Call relations**: _admit_chat uses it when sending a message into core.

*Call graph*: called by 1 (_admit_chat); 2 external calls (__init__, log).


##### `_chat_url`  (lines 1160–1163)

```
def _chat_url(public_base_url: str | None, conversation_id: UUID) -> str | None
```

**Purpose**: Builds an absolute portal link to a conversation when the deployment has a public base URL. Without that base, no link is possible.

**Data flow**: It receives a base URL and conversation id → joins them with the portal hash route → returns the URL or None.

**Call relations**: _chat_source and _comment_notice use it to name where a message was said.

*Call graph*: called by 2 (_chat_source, _comment_notice).


##### `_chat_source`  (lines 1166–1175)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Creates the source label attached to a portal message. It includes a conversation link when possible and always includes the sender email.

**Data flow**: It receives base URL, conversation id, and email → builds a chat URL if possible → returns a readable source string.

**Call relations**: _admit_chat passes this into the turn context.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (_admit_chat).


##### `_comment_notice`  (lines 1178–1198)

```
def _comment_notice(public_base_url: str | None, conversation: ListedConversation, member_id: UUID, email: str, text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Builds the text sent into a Slack or terminal conversation when a portal member comments. It names the commenter, links back to the portal if possible, and mentions attached files.

**Data flow**: It receives conversation, member, email, text, and paths → decides the author label → builds markdown-style comment text → returns the notice string.

**Call relations**: _existing_chat_target uses it when an existing conversation is commentable.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (_existing_chat_target); 2 external calls (PurePosixPath, conversation_audience).


##### `_audience_for`  (lines 1201–1208)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Combines authentication with web audience lookup. The audience is the set of agents and admin powers this member may use in the portal.

**Data flow**: It authenticates the request → asks the web audience module for grants → returns member id, email, and audience, or a response refusal.

**Call relations**: Most web API routes call this directly or through narrower gates.

*Call graph*: calls 1 internal fn (_authenticate); called by 25 (_member_chat_page, _member_turn, _object_gate, _panel_gate, action_views, agents_index, agents_status, chat, chats_index, connection_pool (+15 more)); 2 external calls (web_audience, web_extension).


##### `_visibility_flag`  (lines 1235–1239)

```
def _visibility_flag(agent: AgentSummary) -> str | None
```

**Purpose**: Finds the feature flag that controls whether an agent should appear in portal lists. Main and shipped app agents can be controlled differently.

**Data flow**: It receives an agent summary → checks whether it is main or provisioned by a shipped app → returns a flag key or None.

**Call relations**: _flag_reads and agents_index use it while preparing the boot payload.

*Call graph*: called by 2 (_flag_reads, agents_index); 1 external calls (shipped_app_slug).


##### `_flag_reads`  (lines 1242–1262)

```
async def _flag_reads(agents: tuple[AgentSummary, ...]) -> dict[str, bool]
```

**Purpose**: Reads all feature flags needed for the portal boot response in one batch. Portal screens default open, while shipped apps default closed unless explicitly enabled.

**Data flow**: It receives the visible agents → collects unique flag keys → reads each flag asynchronously → returns a dictionary of flag answers.

**Call relations**: agents_index uses this to mark hidden agents and available portal sections.

*Call graph*: calls 1 internal fn (_visibility_flag); called by 1 (agents_index); 2 external calls (gather, flag_enabled).


##### `_setup_configured`  (lines 1265–1276)

```
def _setup_configured(state: SetupState) -> bool
```

**Purpose**: Decides whether an app's setup is considered accepted. If there are no setup rows, it is already configured; otherwise at least one offered setup item must be complete.

**Data flow**: It receives setup state → inspects connectors, credentials, and standing orders → returns true or false.

**Call relations**: workspace_starters uses it to decide whether an installed app should still be suggested for setup.

*Call graph*: called by 1 (workspace_starters).


##### `agents_index`  (lines 1279–1408)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Builds the portal's main boot payload: the member, the screens they can see, the agents they can use, archived apps, homepage state, and setup status. This is the first data request the portal needs after loading.

**Data flow**: It authenticates and resolves audience → publishes assets → reads homepage bindings, setup states, archived agents, grants, and flags → returns one JSON object for the UI to draw.

**Call relations**: It coordinates many helpers, including _audience_for, _assets_published, _bound_page, _homepage_state, _flag_reads, and its inner setup_of.

*Call graph*: calls 8 internal fn (list_archived_agents, _assets_published, _audience_for, _bound_page, _flag_reads, _homepage_state, _visibility_flag, apps); 6 external calls (Semaphore, gather, JSONResponse, shipped_app_slug, granted_emails, web_extension).


##### `agents_index.setup_of`  (lines 1330–1332)

```
async def setup_of(agent: AgentSummary) -> SetupState
```

**Purpose**: Reads setup state for one provisioned agent while respecting a concurrency limit. This keeps the boot request from opening too many database transactions at once.

**Data flow**: It receives an agent from the surrounding function → waits for the semaphore → asks core for that agent's setup state → returns the state.

**Call relations**: agents_index creates and gathers this helper for each provisioned agent.


##### `agents_status`  (lines 1411–1452)

```
async def agents_status(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a live status snapshot for all agents the member can see. It tells the portal which agents are working, recently active, or recently failed.

**Data flow**: It authenticates → asks core for per-agent turn statuses → peeks latest activity for running turns → returns status rows with timestamps and activity text.

**Call relations**: The portal polls this beside agents_index while the UI is open.

*Call graph*: calls 4 internal fn (agent_turn_statuses, latest_activity, _audience_for, _iso); 1 external calls (JSONResponse).


##### `_framed_length`  (lines 1455–1468)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Rejects whole-body form parsing unless the request declares a trustworthy content length under a limit. This protects memory and temp-file parsing from unbounded uploads.

**Data flow**: It reads transfer and content-length headers → rejects chunked, missing, non-numeric, or oversized bodies → returns a refusal response or None.

**Call relations**: Form-based routes call it before _form, including login, credential, preview, and multipart chat paths.

*Call graph*: called by 6 (_parse_inbound, anthropic_code, fulfill_credential, open_session, preview, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 1471–1478)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses a request form and turns parser failures into a normal 400 response. This keeps malformed client bodies from escaping as internal errors.

**Data flow**: It receives a request → calls the framework form parser → returns parsed form data or a bad-request response.

**Call relations**: All form-reading routes share this helper after their size checks.

*Call graph*: called by 6 (_parse_inbound, anthropic_code, fulfill_credential, open_session, preview, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 1481–1489)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a raw request body with a hard byte cap based on bytes actually received. It does not trust declared headers.

**Data flow**: It streams chunks from the request → accumulates until the limit is exceeded or complete → returns bytes or a too-large response.

**Call relations**: _parse_inbound and upload_start use it for plain text or JSON bodies.

*Call graph*: called by 2 (_parse_inbound, upload_start); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 1492–1545)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...], tuple[str, ...]] | Response
```

**Purpose**: Parses the browser's chat composer submission into message text, inline files, and already-uploaded blob keys. It rejects unsupported body types and unsafe uploaded keys.

**Data flow**: It reads content type → for plain bodies, reads bounded UTF-8 text → for multipart, bounds and parses form parts → validates file count and uploaded keys → returns text, uploads, and keys or a refusal.

**Call relations**: _chat_inbound uses it before deciding whether the send is a message, answer, or stop.

*Call graph*: calls 4 internal fn (_bounded_body, _form, _framed_length, _uploaded_key); called by 1 (_chat_inbound); 1 external calls (Response).


##### `_uploaded_key`  (lines 1548–1557)

```
def _uploaded_key(raw: str) -> str | None
```

**Purpose**: Checks that an uploaded blob key is one minted by this web surface. This stops a send from naming arbitrary workspace data in blob storage.

**Data flow**: It receives a raw key string → verifies it stays under the upload root → returns the key or None.

**Call relations**: _parse_inbound uses it for each uploaded_key form part.

*Call graph*: called by 1 (_parse_inbound); 1 external calls (contained_relative).


##### `_inbox_paths`  (lines 1560–1571)

```
def _inbox_paths(uploads: tuple[UploadFile, ...], uploaded_keys: tuple[str, ...]) -> tuple[str, ...]
```

**Purpose**: Chooses safe workspace file paths for chat attachments. Duplicate names in the same send are numbered instead of overwriting each other.

**Data flow**: It receives inline uploads and presigned-upload keys → collects filenames → normalizes them through inbox_name → returns web-inbox paths.

**Call relations**: _chat_inbound uses it after parsing attachments.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (PurePosixPath, inbox_name).


##### `_deliver_uploads`  (lines 1574–1589)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], uploaded_keys: tuple[str, ...], paths: tuple[str, ...]) -> None
```

**Purpose**: Copies chat attachments into the conversation workspace before the agent turn runs. This makes the files available to the sandbox under the paths mentioned in the message.

**Data flow**: It receives uploads, blob keys, and target paths → streams inline files from the request and presigned files from blob storage → writes each into the workspace.

**Call relations**: _admit_chat calls it just before admitting the text.

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (_admit_chat).


##### `_files_note`  (lines 1592–1595)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Adds a machine-readable note to a message listing where attached files were saved. The agent can read the paths, while the transcript later turns the note back into file cards.

**Data flow**: It receives message text and paths → appends or creates the attachment note → returns the admitted body.

**Call relations**: _chat_inbound uses it when attachments are present.

*Call graph*: called by 1 (_chat_inbound).


##### `_member_attachments`  (lines 1603–1611)

```
def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]
```

**Purpose**: Splits a stored member message back into the member's words and attached file paths. It recognizes the note created by _files_note.

**Data flow**: It receives stored text → searches for the attachment note at the end → returns cleaned words and a tuple of paths.

**Call relations**: _member_bubble uses it while rendering transcripts.

*Call graph*: called by 1 (_member_bubble).


##### `_attachment_preview`  (lines 1614–1626)

```
def _attachment_preview(public_base_url: str | None, agent_id: UUID, conversation_id: UUID, path: str) -> str | None
```

**Purpose**: Builds the URL for an inline image preview of one attached workspace file. It only does this for raster image filenames and only when a public base URL exists.

**Data flow**: It receives base URL, agent id, conversation id, and path → checks image type → URL-encodes the path → returns an absolute preview URL or None.

**Call relations**: _transcript_aids and _conversation_messages pass it as the attachment lookup used by rendered bubbles.

*Call graph*: 2 external calls (raster_image_media_type, quote).


##### `_attachment_payload`  (lines 1629–1642)

```
def _attachment_payload(path: str, preview_url: str | None) -> dict[str, object]
```

**Purpose**: Builds the JSON description of one member-attached file for the chat UI. Images may get a preview URL; other files become named cards.

**Data flow**: It receives a workspace path and optional preview URL → derives filename and media type → returns a file payload dictionary.

**Call relations**: _member_bubble uses it for each path found by _member_attachments.

*Call graph*: called by 1 (_member_bubble); 2 external calls (PurePosixPath, raster_image_media_type).


##### `_member_bubble`  (lines 1645–1653)

```
def _member_bubble(said: str, attach: Attach | None) -> dict[str, object]
```

**Purpose**: Builds one user chat bubble from stored text, including attachment cards. It hides the internal file-note sentence from the member-facing transcript.

**Data flow**: It receives stored text and optional attachment URL function → separates words and paths → returns a user-role dictionary with optional files.

**Call relations**: The transcript renderer and live conversation projection use it wherever member messages are displayed.

*Call graph*: calls 2 internal fn (_attachment_payload, _member_attachments); called by 2 (_member, _conversation_messages).


##### `_upload_chunks`  (lines 1656–1658)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded file in fixed-size chunks. This avoids loading the whole file into memory at once during workspace writes.

**Data flow**: It receives an UploadFile → repeatedly reads chunks → yields bytes until the file ends.

**Call relations**: _deliver_uploads uses it for inline multipart attachments.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_key`  (lines 1661–1666)

```
def _answer_key(conversation_id: UUID, turn_id: UUID, index: int) -> str
```

**Purpose**: Creates the idempotency key for a member answer to a specific agent question. This lets double-clicks or retries attach to the same answer instead of making duplicates.

**Data flow**: It receives conversation id, asking turn id, and question index → formats a stable key string → returns it.

**Call relations**: _admit_chat uses it when admitting answers, and _asks uses the same format to find answers later.

*Call graph*: called by 2 (_admit_chat, _asks).


##### `_answer_headers`  (lines 1669–1681)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Reads the headers that identify a message as an answer to an agent question. Bad answer headers are rejected before any conversation is created.

**Data flow**: It reads answer-turn and answer-question headers → if absent, returns None → otherwise parses UUID and integer index → returns them or a bad-request response.

**Call relations**: _chat_inbound uses it after parsing the message body.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 1684–1693)

```
def _stop_header(request: Request) -> UUID | None | Response
```

**Purpose**: Reads the header that turns a chat POST into a stop request. It validates the turn id before reading further state.

**Data flow**: It reads the stop-turn header → if absent, returns None → otherwise parses it as a UUID → returns the UUID or a bad-request response.

**Call relations**: _chat_inbound uses it to distinguish stop presses from normal messages.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (Response, UUID).


##### `_chat_inbound`  (lines 1714–1736)

```
async def _chat_inbound(ctx: SurfaceContext, request: Request) -> _ChatInbound | Response
```

**Purpose**: Turns a chat HTTP request into a clean internal inbound object. It enforces empty-body rules for stops, non-empty rules for messages, attachment existence, and final character limits.

**Data flow**: It reads stop headers, parses body and attachments, checks uploaded blobs exist, chooses inbox paths, adds file notes, and reads answer headers → returns _ChatInbound or an error response.

**Call relations**: chat calls it before resolving the target conversation.

*Call graph*: calls 5 internal fn (_answer_headers, _files_note, _inbox_paths, _parse_inbound, _stop_header); called by 1 (chat); 2 external calls (__init__, Response).


##### `_new_chat_target`  (lines 1739–1764)

```
async def _new_chat_target(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, inbound: _ChatInbound) -> _ChatTarget | Response
```

**Purpose**: Creates the target for a first message in a new conversation. Answers and stops are refused because they must refer to an existing conversation.

**Data flow**: It checks the audience can use the agent → opens a conversation with a fresh queue key → returns conversation id and title.

**Call relations**: _resolve_chat_target calls it when the query parameter says 'new'.

*Call graph*: calls 2 internal fn (allows, _open_conversation); called by 1 (_resolve_chat_target); 3 external calls (__init__, Response, uuid4).


##### `_existing_chat_target`  (lines 1767–1798)

```
async def _existing_chat_target(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, inbound: _ChatInbound) -> _ChatTarge
```

**Purpose**: Builds the target for a message going into an existing conversation. If the conversation is commentable, it also prepares the comment text for the original surface.

**Data flow**: It checks _member_chat for access → optionally builds a comment notice → returns _ChatTarget or not-found.

**Call relations**: _resolve_chat_target calls it for UUID conversation parameters.

*Call graph*: calls 4 internal fn (allows, _comment_notice, _commentable, _member_chat); called by 1 (_resolve_chat_target); 2 external calls (__init__, Response).


##### `_resolve_chat_target`  (lines 1801–1822)

```
async def _resolve_chat_target(ctx: SurfaceContext, request: Request, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, inbound: _ChatInbound) -> _ChatTarget | Response
```

**Purpose**: Chooses whether a chat POST opens a new conversation or continues an existing one. It validates the conversation query parameter.

**Data flow**: It reads the conversation query value → uses the web store → dispatches to _new_chat_target or _existing_chat_target → returns a target or an error.

**Call relations**: chat calls it after parsing and authorization.

*Call graph*: calls 2 internal fn (_existing_chat_target, _new_chat_target); called by 1 (chat); 3 external calls (Response, web_extension, UUID).


##### `_stop_chat`  (lines 1825–1838)

```
async def _stop_chat(ctx: SurfaceContext, request: Request, conversation_id: UUID, turn_id: UUID) -> Response
```

**Purpose**: Stops a running turn in an authorized conversation. It returns whether the stop ended the turn and, if relevant, the founded turn id.

**Data flow**: It checks the member may reach the named turn → asks core to stop it in the conversation → returns a JSON outcome or not-found.

**Call relations**: chat calls it when _chat_inbound found a stop header.

*Call graph*: calls 2 internal fn (stop_turn, _member_turn); called by 1 (chat); 2 external calls (JSONResponse, Response).


##### `_admit_chat`  (lines 1841–1881)

```
async def _admit_chat(ctx: SurfaceContext, request: Request, target: _ChatTarget, inbound: _ChatInbound, member_id: UUID, email: str) -> Response
```

**Purpose**: Saves attachments and admits a member message into the durable conversation queue. It returns the turn and conversation details the browser needs for live streaming.

**Data flow**: It computes an idempotency key for answers → writes attachments → builds turn context → admits the body → returns turn id, conversation id, title, arrival id, and answer body when relevant.

**Call relations**: chat calls it for normal messages and answers after target resolution.

*Call graph*: calls 6 internal fn (admit, admitted_body, _answer_key, _chat_source, _deliver_uploads, _turn_context); called by 1 (chat); 1 external calls (JSONResponse).


##### `chat`  (lines 1884–1919)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Main POST route for sending a portal message, answering an agent question, or stopping a turn. It is the only web chat mutation path.

**Data flow**: It authenticates and checks the agent → parses inbound content → resolves the target conversation → either stops a turn or admits the message → returns JSON or an HTTP refusal.

**Call relations**: This route ties together _audience_for, _chat_inbound, _resolve_chat_target, _stop_chat, and _admit_chat.

*Call graph*: calls 6 internal fn (_admit_chat, _agent_param, _audience_for, _chat_inbound, _resolve_chat_target, _stop_chat); 1 external calls (Response).


##### `_rendered_text`  (lines 1922–1933)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Extracts the member-facing text from a stored model message. For user messages, it removes internal context wrappers before display.

**Data flow**: It receives a Message → reads plain string content or text blocks → strips internal context for user messages → returns cleaned text.

**Call relations**: Title building and transcript rendering use it whenever raw messages become visible text.

*Call graph*: called by 3 (_assistant, _member, _title_excerpt).


##### `_append_activity`  (lines 1936–1937)

```
def _append_activity(events: list[dict[str, str]], text: str) -> None
```

**Purpose**: Adds one activity event to a list in the format the portal expects. It is a tiny helper for consistent transcript event objects.

**Data flow**: It receives an event list and text → appends an activity dictionary → changes the list in place.

**Call relations**: _stored_activity callers use it in _subagent_activity and _TranscriptRenderer._assistant.

*Call graph*: called by 2 (_assistant, _subagent_activity).


##### `_stored_activity`  (lines 1940–1952)

```
def _stored_activity(block: ToolUseBlock, result: ToolResultBlock) -> str | None
```

**Purpose**: Chooses the best human-readable activity label for a tool call. It prefers explicit activity text, then user descriptions, then special skill-loading text, then the tool name.

**Data flow**: It receives a tool-use block and its result block → inspects stored activity fields → returns a display string or None.

**Call relations**: _subagent_activity and _TranscriptRenderer._assistant use it to turn tool work into timeline events.

*Call graph*: called by 2 (_assistant, _subagent_activity).


##### `_subagent_activity`  (lines 1974–1998)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

**Purpose**: Builds the visible work log for a subagent run. It includes notes and tool activity, bounded so one child run cannot grow without limit.

**Data flow**: It receives messages from a subagent conversation → matches tool uses with activity results → emits note and activity event dictionaries → returns a capped list.

**Call relations**: _subagent_nodes calls it after reading each spawned run's transcript.

*Call graph*: calls 2 internal fn (_append_activity, _stored_activity); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 2001–2011)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

**Purpose**: Attempts to read a subagent finish answer as a structured JSON object. If the answer is normal text or invalid JSON, it leaves it alone.

**Data flow**: It receives answer text → tries JSON decoding → returns a dictionary payload or None.

**Call relations**: _run_answer uses it to decide how to present a run's final output.

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 2014–2037)

```
def _payload_prose(value: JsonValue) -> str
```

**Purpose**: Turns structured JSON values into readable prose. It makes booleans, numbers, lists, and objects understandable in chat instead of showing raw JSON.

**Data flow**: It receives a JSON-like value → recursively formats it into text → returns prose, 'none', or an empty string.

**Call relations**: _run_answer uses it when a finish payload has multiple fields or non-simple content.

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 2040–2056)

```
def _run_answer(answer: str) -> str
```

**Purpose**: Turns a subagent or profile run's final answer into the text the portal should display. It unwraps simple structured payloads but keeps meaningful fields visible.

**Data flow**: It receives answer text → checks for a JSON finish payload → returns plain prose or the original answer.

**Call relations**: _subagent_nodes and _TranscriptAids.render use it for run-style conversations.

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (render, _subagent_nodes).


##### `_subagent_nodes`  (lines 2059–2098)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

**Purpose**: Builds the tree of subagent runs spawned by conversation turns. Each node carries who ran, what they did, what they answered, and any children they spawned.

**Data flow**: It receives spawned turns → names profiles or agent children → reads recent child transcripts for activity → nests children under parents → returns a mapping from parent turn id to nodes.

**Call relations**: _transcript_aids uses it for transcript reads, and _events uses it when a live terminal frame completes.

*Call graph*: calls 4 internal fn (list_agents, read_transcript, _run_answer, _subagent_activity); called by 2 (_events, _transcript_aids); 2 external calls (__init__, gather).


##### `_ReplyState.note_answer`  (lines 2109–2115)

```
def note_answer(self) -> '_ReplyState'
```

**Purpose**: Moves a pending assistant answer into the event list as a note when later tool work shows it was not the final answer. This preserves narration in the right order.

**Data flow**: It reads the current pending answer and note count → inserts the answer as a note if allowed → returns a new reply state with the answer cleared.

**Call relations**: _TranscriptRenderer._assistant and _TranscriptRenderer._member call it while grouping transcript messages into replies.

*Call graph*: called by 2 (_assistant, _member); 1 external calls (replace).


##### `_TranscriptRenderer.render`  (lines 2132–2148)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts raw transcript messages into portal chat bubbles and assistant replies. It groups text, activity, questions, files, apps, connect controls, and subagents into the right visible reply.

**Data flow**: It receives messages → builds an activity lookup → walks messages in order through assistant/member handlers → flushes the final reply → returns rendered message dictionaries.

**Call relations**: _rendered_messages constructs the renderer and calls this method.

*Call graph*: calls 3 internal fn (_assistant, _flush, _member); 1 external calls (__init__).


##### `_TranscriptRenderer._assistant`  (lines 2150–2168)

```
def _assistant(self, message: Message, activity: Mapping[str, ToolResultBlock], state: _ReplyState) -> _ReplyState
```

**Purpose**: Accumulates assistant text and tool activity into the current pending reply. It distinguishes final answer text from narration before tool work.

**Data flow**: It receives an assistant message, activity lookup, and current state → extracts visible text and activity events → returns updated state.

**Call relations**: _TranscriptRenderer.render calls it for assistant messages.

*Call graph*: calls 4 internal fn (note_answer, _append_activity, _rendered_text, _stored_activity); called by 1 (render); 1 external calls (replace).


##### `_TranscriptRenderer._member`  (lines 2170–2200)

```
def _member(self, message: Message, state: _ReplyState, rendered: list[dict[str, object]]) -> _ReplyState
```

**Purpose**: Flushes any pending assistant reply and appends a user bubble when a real member message appears. It skips machine-origin prompts and messages already shown inside question cards.

**Data flow**: It receives a user message, state, and rendered list → extracts turn reference and member text → flushes replies as needed → may append a bubble with speaker or answered-question labels → returns updated state.

**Call relations**: _TranscriptRenderer.render calls it for non-assistant messages.

*Call graph*: calls 4 internal fn (note_answer, _flush, _member_bubble, _rendered_text); called by 1 (render); 2 external calls (replace, member_message_text).


##### `_TranscriptRenderer._flush`  (lines 2202–2239)

```
def _flush(self, state: _ReplyState, rendered: list[dict[str, object]], *, include_subagents: bool) -> _ReplyState
```

**Purpose**: Writes the current assistant reply into the rendered transcript if it has anything visible. It attaches related subagents, questions, shared files, created apps, and connection controls.

**Data flow**: It receives reply state and output list → gathers side payloads for the closing turn → appends an assistant reply dictionary when non-empty → returns a cleared state.

**Call relations**: _TranscriptRenderer.render and _TranscriptRenderer._member call it whenever a reply boundary is reached.

*Call graph*: called by 2 (_member, render); 1 external calls (replace).


##### `_rendered_messages`  (lines 2242–2317)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

**Purpose**: Top-level helper for rendering transcript messages with all optional side information. It keeps live chat, read-only transcripts, and history pages visually consistent.

**Data flow**: It receives raw messages plus maps for subagents, speakers, questions, files, apps, and attachments → creates a _TranscriptRenderer → returns rendered dictionaries.

**Call relations**: _TranscriptAids.render calls it after gathering the side data.

*Call graph*: called by 1 (render); 1 external calls (__init__).


##### `_asks`  (lines 2336–2368)

```
def _asks(conversation_id: UUID, turns: tuple[Turn, ...], admitted: tuple[KeyedAdmission, ...]) -> _Asks
```

**Purpose**: Builds question cards for turns that asked the member something, including recorded answers. It recognizes answers by idempotency key rather than by guessing from text.

**Data flow**: It receives conversation id, turns, and keyed admissions → matches each question slot to admitted answers → returns cards and the message refs already displayed by those cards.

**Call relations**: _transcript_aids uses it so transcript rendering can attach question state to assistant replies.

*Call graph*: calls 1 internal fn (_answer_key); called by 1 (_transcript_aids); 2 external calls (__init__, member_message_text).


##### `_TranscriptAids.render`  (lines 2391–2410)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Renders messages using the side data gathered for a conversation. If the conversation is a profile run, it also formats assistant replies as run answers.

**Data flow**: It receives messages → passes all stored aids into _rendered_messages → optionally rewrites assistant text through _run_answer → returns rendered rows.

**Call relations**: _conversation_messages and _history_messages use _TranscriptAids objects to render current and older transcript windows.

*Call graph*: calls 2 internal fn (_rendered_messages, _run_answer).


##### `_transcript_aids`  (lines 2413–2465)

```
async def _transcript_aids(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, agent_origin: frozenset[str], speakers: dict[str, str], asked: dict[str, str], opens: frozenset[UUI
```

**Purpose**: Gathers everything needed to render a transcript besides the messages themselves. This includes turns, subagents, shared files, apps, questions, speakers, attachments, and connect controls.

**Data flow**: It receives context, agent, conversation, viewer, and attribution maps → reads core side tables concurrently → builds file/app/question/control maps → returns a _TranscriptAids object.

**Call relations**: _conversation_messages and _history_messages call it before rendering transcript windows.

*Call graph*: calls 9 internal fn (conversation_subagent_turns, keyed_admissions, list_conversation_artifacts, list_turns, _asks, _connect_controls, _created_apps, _file_payload, _subagent_nodes); called by 2 (_conversation_messages, _history_messages); 3 external calls (__init__, gather, partial).


##### `_connect_controls`  (lines 2468–2508)

```
async def _connect_controls(ctx: SurfaceContext, conversation_id: UUID, turns: tuple[Turn, ...], viewer: UUID) -> dict[str, dict[str, object]]
```

**Purpose**: Builds connection controls left by turns for the current viewer. It shows a fresh connect action if still open, or the connected account if the request landed.

**Data flow**: It receives turns and viewer id → skips requests for other members or unavailable connect flows → reads held accounts only when needed → returns controls keyed by turn id.

**Call relations**: _transcript_aids uses it so connection prompts stay attached to the reply that asked for them.

*Call graph*: calls 4 internal fn (connect_available, held_accounts, _connect_control, _provider_label); called by 1 (_transcript_aids).


##### `_conversation_messages`  (lines 2511–2633)

```
async def _conversation_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], Turn | None, int]
```

**Purpose**: Builds the portal's rendered view of a conversation, including settled transcript, live running prompt, queued arrivals, and earlier-history cursor. It is the shared projection for chat and transcript pages.

**Data flow**: It reads transcript, agent-origin refs, speakers, compactions, latest turn, and queued arrivals → renders settled messages with aids → appends running and waiting messages → returns rendered rows, latest turn, and earlier index.

**Call relations**: transcript and conversation_transcript call it for current conversation views.

*Call graph*: calls 10 internal fn (agent_origin_refs, arrival_speakers, latest_turn, list_compactions, queued_arrivals, read_transcript, turn_detail, _member_bubble, _transcript_aids, _verified_earlier); called by 2 (conversation_transcript, transcript); 3 external calls (gather, partial, member_message_text).


##### `_verified_earlier`  (lines 2636–2652)

```
async def _verified_earlier(ctx: SurfaceContext, conversation_id: UUID, indices: tuple[int, ...], messages: tuple[Message, ...]) -> int
```

**Purpose**: Finds the newest valid compaction record that sits directly above the current transcript window. This prevents history pages from duplicating or skipping messages.

**Data flow**: It receives compaction indices and current messages → reads each candidate's after-window → compares it to the message prefix → returns the matching index or 0.

**Call relations**: _conversation_messages and _history_messages use it when creating history cursors.

*Call graph*: calls 1 internal fn (read_compaction_after); called by 2 (_conversation_messages, _history_messages).


##### `_history_cursor`  (lines 2655–2657)

```
def _history_cursor(index: int, end: int | None=None) -> str
```

**Purpose**: Encodes a transcript history position into an opaque cursor for the browser. The cursor hides implementation details while preserving page position.

**Data flow**: It receives a compaction index and optional end offset → formats and base64-url encodes them → returns a cursor string.

**Call relations**: Transcript responses and history page helpers use it when advertising earlier pages.

*Call graph*: called by 4 (fits, _history_messages, conversation_transcript, transcript); 1 external calls (urlsafe_b64encode).


##### `_history_position`  (lines 2660–2675)

```
def _history_position(cursor: str) -> tuple[int, int | None]
```

**Purpose**: Decodes and validates an earlier-history cursor. Invalid cursors are rejected instead of being treated as arbitrary positions.

**Data flow**: It receives a cursor string → base64-decodes and parses index/end values → returns them as integers or raises ValueError.

**Call relations**: _history_messages calls it before reading a compaction page.

*Call graph*: called by 1 (_history_messages); 1 external calls (b64decode).


##### `_bounded_history_page`  (lines 2678–2700)

```
def _bounded_history_page(messages: list[dict[str, object]], index: int, end: int) -> tuple[list[dict[str, object]], int]
```

**Purpose**: Cuts a rendered history window into a page that fits both message-count and byte-size limits. This keeps scrollback responses predictable.

**Data flow**: It receives rendered messages, compaction index, and end offset → searches for the earliest start that fits → returns the page slice and start offset.

**Call relations**: _history_messages uses it after rendering an older compaction window.

*Call graph*: called by 1 (_history_messages).


##### `_bounded_history_page.fits`  (lines 2683–2688)

```
def fits(start: int) -> bool
```

**Purpose**: Checks whether a proposed history slice fits the response byte limit. It includes the next cursor in the measured payload.

**Data flow**: It receives a start index from the outer helper → builds the JSON response body for that slice → returns true if it is small enough.

**Call relations**: _bounded_history_page uses it during its binary search.

*Call graph*: calls 1 internal fn (_history_cursor); 1 external calls (JSONResponse).


##### `_history_messages`  (lines 2703–2754)

```
async def _history_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, cursor: str, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], str | None] | None
```

**Purpose**: Reads and renders one earlier page of a compacted conversation. It stitches scrollback pages together without repeating the kept summary tail.

**Data flow**: It decodes the cursor → reads the compaction record → removes messages already present below → gathers render aids → renders and bounds the page → returns messages plus an optional cursor above.

**Call relations**: _conversation_history calls it after authorization.

*Call graph*: calls 8 internal fn (agent_origin_refs, arrival_speakers, read_compaction, _bounded_history_page, _history_cursor, _history_position, _transcript_aids, _verified_earlier); called by 1 (_conversation_history); 1 external calls (gather).


##### `transcript`  (lines 2757–2801)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the current rendered transcript for one of the member's chat conversations. It also tells the browser which running turn to stream or which credential prompt remains open.

**Data flow**: It authenticates, parses agent and conversation ids, checks _member_chat access → calls _conversation_messages → adds earlier cursor, running turn, or open handoffs → returns JSON.

**Call relations**: This is the chat-page transcript route and uses _open_handoffs for committed credential requests.

*Call graph*: calls 7 internal fn (_agent_param, _audience_for, _conversation_messages, _history_cursor, _member_chat, _open_handoffs, _opens); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 2804–2817)

```
async def _open_handoffs(ctx: SurfaceContext, terminal: TerminalFrame, member_id: UUID) -> dict[str, object]
```

**Purpose**: Reports handoffs from the newest committed turn that are still actionable after reload. Currently this means pending credential prompts.

**Data flow**: It receives a terminal frame and member id → renews and filters pending credential prompts when present → returns a small dictionary of open handoffs.

**Call relations**: transcript calls it when the latest turn has already ended.

*Call graph*: calls 1 internal fn (_pending_prompts); called by 1 (transcript).


##### `_connect_control`  (lines 2820–2824)

```
def _connect_control(ctx: SurfaceContext, provider: str, turn_id: UUID) -> dict[str, object]
```

**Purpose**: Builds the JSON shape for a connect button in chat. It names the provider, display label, and turn whose request should be opened.

**Data flow**: It receives context, provider, and turn id → resolves a label → returns a control dictionary.

**Call relations**: _connect_controls and _events use it for transcript and live stream output.

*Call graph*: calls 1 internal fn (_provider_label); called by 2 (_connect_controls, _events).


##### `_provider_label`  (lines 2827–2838)

```
def _provider_label(ctx: SurfaceContext, provider: str) -> str
```

**Purpose**: Returns the friendly display name for a connector provider. It prefers the portal's curated first-run catalog and falls back to the connect system or provider slug.

**Data flow**: It receives context and provider name → searches known provider tiles → otherwise asks the connect system if available → returns a label.

**Call relations**: Connection controls and agent setup payloads use it so provider names stay consistent.

*Call graph*: calls 2 internal fn (connect_available, connect_label); called by 3 (_connect_control, _connect_controls, agent_setup).


##### `_provider_summary`  (lines 2841–2848)

```
def _provider_summary(provider: str) -> str
```

**Purpose**: Returns the short explanation for a curated provider, if one exists. Unknown providers get an empty summary.

**Data flow**: It receives a provider name → searches first-run provider tiles → returns the summary or an empty string.

**Call relations**: agent_setup uses it when enriching connector rows for the UI.

*Call graph*: called by 1 (agent_setup).


##### `chats_index`  (lines 2851–2863)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Resolves a portal conversation permalink into either a chat rail row or a read-only conversation projection. It requires a conversation id.

**Data flow**: It authenticates → reads the conversation query parameter → calls _resolve_chat → returns that JSON response.

**Call relations**: The browser uses this for #/c/<id> links; the general rail listing comes from object/conversation elsewhere.

*Call graph*: calls 2 internal fn (_audience_for, _resolve_chat); 2 external calls (Response, web_extension).


##### `_resolve_chat`  (lines 2866–2954)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

**Purpose**: Finds what a conversation permalink means for this member. It may be their own chat, a commentable Slack or terminal conversation, an admin-readable row, or nothing.

**Data flow**: It parses the requested UUID → searches chat-capable agents through _member_chat → otherwise asks core for the conversation's agent and listing → returns chat rows, a conversation row, or empty results.

**Call relations**: chats_index delegates permalink resolution to it.

*Call graph*: calls 9 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, allows, _commentable, _conversation_row, _iso, _member_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 2957–2970)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Common authorization gate for per-agent panel routes and intent routes. It returns the member, email, audience, and valid agent id.

**Data flow**: It authenticates and resolves audience → parses the path agent id → checks the audience allows that agent → returns ids or a not-found response.

**Call relations**: Settings, setup, skills, conversations, actions, connections, homepage, and community routes share it.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 11 (_readable_conversation, actions, agent_setup, community_skill, community_skills, connections, conversations, homepage, intents, settings (+1 more)); 1 external calls (Response).


##### `_iso`  (lines 2973–2974)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Formats optional datetimes for JSON responses. None stays None.

**Data flow**: It receives a datetime or None → calls isoformat when present → returns a string or None.

**Call relations**: Many response builders use it for timestamps.

*Call graph*: called by 6 (_conversation_row, _memory_rows, _resolve_chat, _usage_payload, agents_status, object_detail); 1 external calls (isoformat).


##### `_window_param`  (lines 2977–2993)

```
def _window_param(request: Request) -> int | None | Response
```

**Purpose**: Parses the requested usage time window. It accepts named ranges or a bounded number of seconds.

**Data flow**: It reads query parameters → validates range or window_seconds → returns seconds, None for all time, or a bad-request response.

**Call relations**: workspace_usage uses it before reading spend reports.

*Call graph*: called by 1 (workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 2996–3036)

```
def _usage_payload(report: MemberSpendReport | SpendReport) -> dict[str, object]
```

**Purpose**: Converts a spend report's detailed usage into JSON. It includes totals, daily rows, and breakdowns by execution and model.

**Data flow**: It receives a member or workspace spend report → reads usage fields and timestamps → returns a nested dictionary.

**Call relations**: workspace_usage uses it for both the member view and admin workspace rollup.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_usage).


##### `skills`  (lines 3039–3063)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists skills available to the selected agent. These are loadable abilities the workspace or deployment provides.

**Data flow**: It passes the panel gate → asks core for agent skills → returns name, description, origin, instructions, dependencies, and agent routing metadata.

**Call relations**: The skills panel calls this for a gated agent.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 3069–3073)

```
def _community_refusal(fault: Exception) -> Response
```

**Purpose**: Turns a community directory failure into a response whose body is safe to show to the member. A special header tells the UI this is user-facing copy.

**Data flow**: It receives an exception → converts it to text → returns a 502 response with the refusal marker header.

**Call relations**: community_skills and community_skill use it for network or directory outages.

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 3080–3096)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a page of community skill search results for the selected agent. With no query it shows the directory's default listing.

**Data flow**: It passes the panel gate → validates query length → asks the community directory → returns skill summaries or a readable refusal.

**Call relations**: The community skills browser calls this before applying a skill through the intent lane.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 3099–3120)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches one community skill document for review before installation. It validates owner, repository, and skill names before calling the directory.

**Data flow**: It passes the panel gate → validates path segments → fetches the skill document → returns it, not-found, or a readable refusal.

**Call relations**: The community skill detail screen calls this after a listing row is selected.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 3123–3198)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows or searches memory items the member can reach across their agents. It supports recent paged listing and query search.

**Data flow**: It authenticates → if memory is unavailable returns empty fields → for listing, decodes cursors and filters kinds → for search, queries each reachable agent and deduplicates → returns memory rows and collection actions.

**Call relations**: The workspace memory tab calls this; it uses _memory_rows and _action_payloads for response shape.

*Call graph*: calls 7 internal fn (object_actions, recent_memory, search_memory, decode, _action_payloads, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 3201–3211)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts memory match objects into simple JSON rows. It includes kind, text, reference, created time, and subject.

**Data flow**: It receives memory matches → formats references and timestamps → returns a list of dictionaries.

**Call relations**: workspace_memory uses it for both recent and search results.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `connections`  (lines 3214–3223)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connector accounts visible for one selected agent. It includes the member's own private grants, shared grants, and admin-visible edges.

**Data flow**: It passes the panel gate → asks core for agent connections under the member/admin view → returns serialized connection rows.

**Call relations**: The per-agent connections panel calls this.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 3226–3236)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists all connector accounts visible to the member across the workspace, with agents filtered to their web audience. This supports workspace-level connection views.

**Data flow**: It authenticates → reads all visible connections → removes agent references the member cannot see → returns connection rows.

**Call relations**: The connector pages use it beside per-agent connection reads.

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 3239–3245)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns GitHub connection coverage for the member or, for admins, the workspace. This tells the UI which GitHub resources are wired.

**Data flow**: It authenticates → asks core for GitHub coverage with admin scope when allowed → returns the serialized coverage model.

**Call relations**: The GitHub coverage screen calls this directly.

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 3248–3279)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists recent conversations for a selected agent that the member may see. It reports whether more rows exist beyond the returned limit.

**Data flow**: It passes the panel gate → reads an optional search string → asks core for one row past the limit → returns conversation rows and a more flag.

**Call relations**: The conversations panel uses _conversation_row to shape each row.

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 3282–3286)

```
def _searched(request: Request) -> str | None
```

**Purpose**: Extracts and bounds a search query from a request. Empty searches become None.

**Data flow**: It reads q from query parameters → trims and cuts it to the maximum length → returns the string or None.

**Call relations**: conversations uses it before passing search to core.

*Call graph*: called by 1 (conversations).


##### `_conversation_row`  (lines 3289–3323)

```
def _conversation_row(entry: ListedConversation, member_id: UUID, agent: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Builds one conversation listing row for the portal. It includes source, audience, timestamps, readability, disclosure, and whether comments are allowed.

**Data flow**: It receives a listed conversation, viewer member id, and optional agent info → formats fields and timestamps → returns a JSON dictionary.

**Call relations**: conversations and _resolve_chat use it for panels and permalink resolution.

*Call graph*: calls 2 internal fn (_commentable, _iso); called by 2 (_resolve_chat, conversations).


##### `_readable_conversation`  (lines 3326–3346)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes a read-only content view of a conversation. It hides malformed, wrong-agent, private, or undisclosed conversations behind the same not-found response.

**Data flow**: It passes the panel gate → parses or receives conversation id → asks core if it is readable for this member/admin → returns agent id, conversation id, and SlotViewer or a 404.

**Call relations**: Conversation transcript, attachment, history, and slot routes use it as their main gate.

*Call graph*: calls 3 internal fn (readable_conversation, _opens, _panel_gate); called by 4 (_conversation_history, _slot_target, conversation_attachment, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 3349–3367)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a read-only rendered transcript for an authorized conversation. It is separate from continuing a chat.

**Data flow**: It handles history cursor requests first → otherwise authorizes the conversation → renders current messages through _conversation_messages → returns messages and optional earlier cursor.

**Call relations**: It delegates history pages to _conversation_history and normal reads to _readable_conversation plus _conversation_messages.

*Call graph*: calls 4 internal fn (_conversation_history, _conversation_messages, _history_cursor, _readable_conversation); 1 external calls (JSONResponse).


##### `_member_chat_page`  (lines 3370–3398)

```
async def _member_chat_page(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes transcript-like reads for the member's own chat conversation. This covers web and private extension chats that may not pass the general panel gate.

**Data flow**: It authenticates → parses agent and conversation ids → checks allows_chat and _member_chat → returns agent id, conversation id, and SlotViewer or 404.

**Call relations**: _conversation_history and conversation_attachment use it as a fallback gate.

*Call graph*: calls 4 internal fn (_agent_param, _audience_for, _member_chat, _opens); called by 2 (_conversation_history, conversation_attachment); 4 external calls (__init__, Response, web_extension, UUID).


##### `_conversation_history`  (lines 3401–3422)

```
async def _conversation_history(ctx: SurfaceContext, request: Request, cursor: str) -> Response
```

**Purpose**: Returns one earlier scrollback page for a compacted conversation. It accepts either read-only conversation authorization or member-chat authorization.

**Data flow**: It tries _readable_conversation, then _member_chat_page → reads the history page with _history_messages → returns messages and optional cursor or 404.

**Call relations**: conversation_transcript calls it when a cursor query parameter is present.

*Call graph*: calls 3 internal fn (_history_messages, _member_chat_page, _readable_conversation); called by 1 (conversation_transcript); 2 external calls (JSONResponse, Response).


##### `_inbox_attachment`  (lines 3425–3436)

```
def _inbox_attachment(path: str) -> bool
```

**Purpose**: Checks whether a path names a direct file under the web inbox directory. This keeps attachment preview routes from reading arbitrary workspace files.

**Data flow**: It receives a path string → verifies it has exactly web-inbox/name shape and no special segments → returns true or false.

**Call relations**: conversation_attachment uses it before serving any file bytes.

*Call graph*: called by 1 (conversation_attachment).


##### `conversation_attachment`  (lines 3439–3479)

```
async def conversation_attachment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves an inline image preview for a member-attached chat file. It only serves validated raster images from the safe inbox path.

**Data flow**: It authorizes the conversation → validates path and media type → checks workspace file size → reads and validates image bytes → returns the image or a refusal.

**Call relations**: Attachment preview URLs built by _attachment_preview land here.

*Call graph*: calls 5 internal fn (list_workspace_files, read_workspace_file, _inbox_attachment, _member_chat_page, _readable_conversation); 5 external calls (__init__, Response, raster_image_media_type, validated_image_preview, log).


##### `_slot_target`  (lines 3499–3523)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

**Purpose**: Finds the authorized conversation target for typed conversation slots. It also supports subagent conversations when a root conversation is supplied.

**Data flow**: It reads optional root query → authorizes the root or current conversation → verifies subagent linkage when needed → returns SlotTarget or 404.

**Call relations**: conversation_slots and conversation_slot use it before building slot context.

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 3526–3542)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

**Purpose**: Builds the context object passed to a conversation slot provider. This includes audience, conversation id, agent id, transcript messages, and public base URL.

**Data flow**: It receives a SlotTarget and extension context → reads conversation audience and transcript → returns ConversationSlotContext or None.

**Call relations**: conversation_slots and conversation_slot call it before summary or full slot reads.

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 3545–3629)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

**Purpose**: Adds host-provided projection data needed by special slot types, such as changes, artifacts, sites, and automations. It also precomputes visibility information.

**Data flow**: It receives slot context, extension name, content type, root id, and viewer → reads the needed artifacts, changes, or member objects → returns an updated slot context.

**Call relations**: conversation_slots and conversation_slot use it so providers receive the right extra data.

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 7 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit).


##### `_authorized_slot_payload`  (lines 3632–3676)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

**Purpose**: Filters a slot payload so it only includes items the viewer may open. For automations, it can keep the row while hiding sensitive content.

**Data flow**: It receives a slot payload and context → compares payload items with visible_items → returns the same payload type with hidden entries removed or redacted.

**Call relations**: conversation_slot applies it after reading a provider's full payload.

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 3679–3723)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists which typed side panels are available for an authorized conversation and how many items each contains. Broken providers are logged and skipped rather than breaking the whole response.

**Data flow**: It authorizes the target → builds shared context → for each registered slot, projects context and asks for a summary count → returns slot descriptors with non-empty counts.

**Call relations**: The conversation UI calls this before showing slot tabs.

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 3726–3753)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full payload for one typed conversation slot, such as artifacts or changes. It checks that the provider returns the declared model type.

**Data flow**: It authorizes the target → finds the requested slot provider → builds and projects context → reads the payload → filters it by authorization → returns JSON.

**Call relations**: The UI calls it after choosing a slot listed by conversation_slots.

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 3756–3759)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Extracts the workspace-changes projection from a slot context and fails if the wrong projection type is present.

**Data flow**: It receives a ConversationSlotContext → checks projection type → returns WorkspaceChanges or raises.

**Call relations**: _read_changes and _summarize_changes use it for the built-in changes slot.

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 3762–3763)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Returns the full changes slot payload from its prepared context.

**Data flow**: It receives slot context → extracts the changes projection → returns it.

**Call relations**: CHANGES_SLOT uses this as its read callback.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 3766–3767)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts changes for the changes slot and hides the slot when there are none.

**Data flow**: It receives slot context → extracts changes → returns their count or None.

**Call relations**: CHANGES_SLOT uses this as its summarize callback.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 3780–3783)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Extracts the artifacts projection from a slot context and fails if the context was not prepared for artifacts.

**Data flow**: It receives a ConversationSlotContext → checks projection type → returns ArtifactsSlotPayload or raises.

**Call relations**: _read_artifacts and _summarize_artifacts use it for the built-in artifacts slot.

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 3786–3787)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Returns the full artifacts slot payload from its prepared context.

**Data flow**: It receives slot context → extracts the artifacts projection → returns it.

**Call relations**: ARTIFACTS_SLOT uses this as its read callback.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 3790–3792)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts artifacts for the artifacts slot and hides the slot when there are none.

**Data flow**: It receives slot context → extracts artifacts → returns their count or None.

**Call relations**: ARTIFACTS_SLOT uses this as its summarize callback.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `workspace_credentials`  (lines 3805–3817)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists workspace credential slots and whether they are filled, without exposing secret values. It also returns available collection actions.

**Data flow**: It authenticates → reads credential slot declarations → reads actions for the credential collection → returns both as JSON.

**Call relations**: The workspace credentials panel calls this.

*Call graph*: calls 4 internal fn (list_credential_slots, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 3820–3845)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace member roster and whether the current member can manage it. It uses the same admin authority the actual member actions will enforce.

**Data flow**: It authenticates and resolves audience → lists members → adds can_manage and member collection actions → returns roster JSON.

**Call relations**: The team panel calls this and uses returned actions for mutations.

*Call graph*: calls 4 internal fn (list_members, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 3848–3859)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the member. Admins see all, while members see their own and shared registrations.

**Data flow**: It authenticates → asks core for sources under member/admin scope → returns serialized source rows.

**Call relations**: The workspace sources screen calls this.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `_connect_declared`  (lines 3873–3879)

```
def _connect_declared(ctx: SurfaceContext, surface: str, action: str) -> bool
```

**Purpose**: Checks whether this deployment actually declares the action needed to connect a chat surface. This prevents the UI from showing a button that cannot work.

**Data flow**: It receives a surface name and action name → inspects object actions for that surface instance → returns true if the action exists.

**Call relations**: workspace_surfaces uses it for Slack and iMessage offerings.

*Call graph*: calls 1 internal fn (object_actions); called by 1 (workspace_surfaces).


##### `workspace_surfaces`  (lines 3882–3933)

```
async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows chat surfaces and installations visible to the member, including Slack, iMessage, and terminal. It also returns the terminal install command when possible.

**Data flow**: It authenticates → reads installations and member-linked surfaces → filters installations by audience → builds fixed surface rows with offered/connected state → returns JSON.

**Call relations**: The workspace surfaces/topology screen calls this.

*Call graph*: calls 4 internal fn (list_installations, member_surfaces, _audience_for, _connect_declared); 3 external calls (__init__, JSONResponse, imessage_offered).


##### `workspace_first_run`  (lines 3962–4010)

```
async def workspace_first_run(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the data needed by the first-run setup experience: providers, model-key state, workspace domain, connector steps, and available actions.

**Data flow**: It authenticates → reads installations, whether the member has a model key, and founding domain → builds connector and action payloads → returns JSON.

**Call relations**: The first-run and connect pages call this to draw setup choices.

*Call graph*: calls 7 internal fn (founding_domain, list_installations, member_holds_own_model_key, object_actions, object_kind, _action_payloads, _audience_for); 3 external calls (__init__, gather, JSONResponse).


##### `connector_catalog`  (lines 4013–4041)

```
async def connector_catalog(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connectable providers from installed brokers for the connector page. It bounds query and cursor inputs to safe sizes.

**Data flow**: It authenticates → validates q and after parameters → asks core for a catalog page → returns provider tiles and next cursor.

**Call relations**: The connector search UI calls this when browsing broker-backed providers.

*Call graph*: calls 2 internal fn (connector_catalog, _audience_for); 3 external calls (__init__, JSONResponse, Response).


##### `_held_providers`  (lines 4092–4100)

```
async def _held_providers(ctx: SurfaceContext, member_id: UUID, *, admin: bool) -> frozenset[str]
```

**Purpose**: Returns provider names the workspace or member can already use. It combines broker connections with installed Slack.

**Data flow**: It reads visible connections and surface installations → collects provider names → adds Slack if installed → returns a frozen set.

**Call relations**: workspace_starters uses it to decide which starter rows are ready versus locked.

*Call graph*: calls 2 internal fn (list_connections, list_installations); called by 1 (workspace_starters).


##### `fill_starters`  (lines 4103–4192)

```
def fill_starters(slate: Slate, held: frozenset[str], taken: frozenset[str], installed: tuple[StarterApp, ...]=()) -> tuple[tuple[StarterRow, ...], UnlockRow | None]
```

**Purpose**: Chooses which starter suggestions to show from a ranked slate, based on currently held providers and installed apps. It fills ready app slots first, then nearby unlocks, then a check-in.

**Data flow**: It receives ranked slate, held providers, taken app names, and installed app states → filters duplicates and missing requirements → returns starter rows plus one highlighted unlock.

**Call relations**: workspace_starters calls it after reading live access and cached ranking data.

*Call graph*: called by 1 (workspace_starters); 4 external calls (__init__, __init__, __init__, get).


##### `_solvent`  (lines 4195–4204)

```
async def _solvent() -> bool
```

**Purpose**: Checks whether the workspace has enough balance headroom to spend on generating starter suggestions. It avoids invoking a model when spending would be refused.

**Data flow**: It opens an extension transaction → reads balance headroom → compares balance, reserve, and grace → returns true or false.

**Call relations**: workspace_starters passes this into StarterCache.

*Call graph*: called by 1 (workspace_starters); 2 external calls (read_headroom, web_extension).


##### `_recalled`  (lines 4207–4214)

```
async def _recalled(ctx: SurfaceContext, member_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a small slice of the member's own memory for starter suggestion ranking. It excludes colleagues' private memory.

**Data flow**: It checks memory availability → builds audience subjects for the member → reads recent memory → returns bounded memory text snippets.

**Call relations**: workspace_starters uses it as context for StarterCache.

*Call graph*: calls 1 internal fn (recent_memory); called by 1 (workspace_starters); 2 external calls (audience_subjects, conversation_audience).


##### `workspace_starters`  (lines 4217–4275)

```
async def workspace_starters(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns personalized starting suggestions before the member has asked anything. It combines cached ranking, memory, installed apps, held providers, and current audience.

**Data flow**: It authenticates → reads setup states for starter apps → gets or generates a slate → reads held providers and taken app names → fills starter and unlock rows → returns JSON.

**Call relations**: The start screen calls this; it coordinates _setup_configured, _recalled, _solvent, _held_providers, and fill_starters.

*Call graph*: calls 7 internal fn (agent_setup, _audience_for, _held_providers, _recalled, _setup_configured, _solvent, fill_starters); 5 external calls (__init__, __init__, gather, JSONResponse, web_extension).


##### `workspace_usage`  (lines 4278–4353)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns usage and spending for the signed-in member, and for admins also the workspace rollup. This is the portal's financial visibility path.

**Data flow**: It authenticates → parses the time window → reads member spend → builds usage and cap payloads → if admin, reads and adds workspace rollup → returns JSON.

**Call relations**: The usage screen calls it; _window_param and _usage_payload shape the request and response.

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `connect_handoff`  (lines 4359–4383)

```
async def connect_handoff(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a provider consent URL for a connection request left by a turn. It mints the URL only when the member clicks, so it is fresh.

**Data flow**: It authorizes the member's turn → asks core for a connect URL → redirects to the provider or returns a callback page explaining the request is gone.

**Call relations**: Connect controls produced by transcripts and streams point to this route.

*Call graph*: calls 2 internal fn (connect_url, _member_turn); 2 external calls (callback_page, RedirectResponse).


##### `_member_turn`  (lines 4386–4439)

```
async def _member_turn(ctx: SurfaceContext, request: Request, *, named_turn: UUID | None=None, allow_commentable: bool=False) -> tuple[UUID, UUID, str] | Response
```

**Purpose**: Authorizes access to a turn for member actions or streams. It allows the member's own turns and, when requested, commentable shared conversations.

**Data flow**: It authenticates → parses or receives the turn id → reads turn detail and owner → checks agent visibility and optional conversation commentability → returns member id, turn id, email, or refusal.

**Call relations**: _stop_chat, connect_handoff, and stream use it before touching a turn.

*Call graph*: calls 5 internal fn (turn_detail, turn_owner, _audience_for, _commentable, _member_chat); called by 3 (_stop_chat, connect_handoff, stream); 3 external calls (Response, web_extension, UUID).


##### `stream`  (lines 4442–4450)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the live SSE stream for one authorized turn. SSE, or Server-Sent Events, lets the browser receive a sequence of text events over one HTTP response.

**Data flow**: It authorizes the turn → reads Last-Event-ID for resume position → returns a StreamingResponse backed by _events.

**Call relations**: The chat UI opens this after a message admission or when loading a running turn.

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 4453–4455)

```
def _event(name: str, payload: dict[str, object], cursor: str='') -> bytes
```

**Purpose**: Formats one named SSE event with a JSON payload and optional cursor. The cursor lets browsers resume after reconnecting.

**Data flow**: It receives event name, payload, and cursor → serializes JSON → returns encoded SSE bytes.

**Call relations**: _events uses it for custom portal events such as files, apps, credentials, and subagents.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 4458–4474)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest, member_id: UUID) -> dict[str, object] | None
```

**Purpose**: Builds the credential prompt payload still awaiting values for a member. It renews the sealed request token during authenticated reads.

**Data flow**: It receives a credential request and member id → renews the seal → checks each prompt's pending state → returns reason, renewed seal, and pending prompts or None.

**Call relations**: _events and _open_handoffs use it for live and reload-time credential prompts.

*Call graph*: calls 2 internal fn (credential_prompt_pending, renew_credential_request); called by 2 (_events, _open_handoffs).


##### `_file_payload`  (lines 4477–4490)

```
def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]
```

**Purpose**: Builds the file card payload for an artifact shared by a turn. It includes download and preview links when available.

**Data flow**: It receives a shared artifact → asks context for artifact and preview links → returns filename, subject, media type, size, URL, and preview URL.

**Call relations**: _events and _transcript_aids use it when showing shared files.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 2 (_events, _transcript_aids).


##### `_opens`  (lines 4493–4494)

```
def _opens(audience: WebAudience) -> frozenset[UUID]
```

**Purpose**: Returns the set of agent ids the member's audience can open. This is used to decide whether created app cards should be visible.

**Data flow**: It receives a WebAudience → collects ids from its agents → returns a frozen set.

**Call relations**: Transcript and stream helpers use it before drawing app cards.

*Call graph*: called by 4 (_events, _member_chat_page, _readable_conversation, transcript).


##### `_created_apps`  (lines 4497–4528)

```
async def _created_apps(ctx: SurfaceContext, created: Mapping[str, tuple[ObjectRef, ...]], opens: frozenset[UUID]) -> dict[str, list[dict[str, object]]]
```

**Purpose**: Builds app cards for agents created by turns, but only when the viewer can open those agents. This prevents readable conversations from revealing private apps the viewer cannot access.

**Data flow**: It receives created object refs and openable agent ids → reads known agents → matches created agent refs → returns cards keyed by turn id.

**Call relations**: _transcript_aids and _events use it for settled and live created-app cards.

*Call graph*: calls 1 internal fn (list_agents); called by 2 (_events, _transcript_aids).


##### `_events`  (lines 4531–4587)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str, email: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams live turn frames to the browser and inserts extra portal events for files, subagents, connect controls, credentials, and created apps. It preserves normal core frames as SSE too.

**Data flow**: It tails the core hub from an optional cursor → reacts to artifact changes and terminal frames with extra reads → yields formatted custom events and core frame events.

**Call relations**: stream returns this async iterator as the response body.

*Call graph*: calls 13 internal fn (connect_available, conversation_subagent_turns, shared_artifacts, tail, turn_detail, _connect_control, _created_apps, _event, _file_payload, _opens (+3 more)); called by 1 (stream); 2 external calls (web_audience, web_extension).


##### `fulfill_credential`  (lines 4590–4620)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores one credential value entered privately in the web UI. The value is not admitted as chat text and does not appear in transcripts.

**Data flow**: It authenticates → bounds and parses the form → validates sealed token, slot, and value size → asks core to fulfill the credential request → returns stored slot or refusal.

**Call relations**: Credential prompt forms from _pending_prompts post here.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `_object_gate`  (lines 4623–4637)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

**Purpose**: Common gate for generic object index and detail pages. It authenticates, resolves audience, and confirms the requested object kind exists.

**Data flow**: It authenticates → reads kind from path → looks up the kind declaration → returns member id, audience, and kind metadata or not-found.

**Call relations**: object_index and object_detail use it before any object read.

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 4640–4650)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

**Purpose**: Finds the agent namespace named by the object request. Object reads run inside one agent's namespace unless they fan out.

**Data flow**: It parses the agent query parameter → checks it is in the member's audience → returns the agent summary or 404.

**Call relations**: object_index uses it for named-agent reads, and object_detail always needs it.

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_action_payloads`  (lines 4653–4654)

```
def _action_payloads(views: tuple[ActionView, ...]) -> list[dict[str, object]]
```

**Purpose**: Serializes action declarations for the portal. It drops null fields so the UI receives compact action descriptors.

**Data flow**: It receives action view objects → dumps each to JSON-compatible dictionaries → returns a list.

**Call relations**: Action-view and workspace panel routes use it wherever actions are shown.

*Call graph*: called by 5 (action_views, workspace_credentials, workspace_first_run, workspace_memory, workspace_team).


##### `_kind_payload`  (lines 4657–4664)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

**Purpose**: Builds common metadata about an object kind for index and detail pages. It also tells whether the web intent lane can apply or delete that kind.

**Data flow**: It receives kind metadata → collects fields, schema, and ApplyIntent capabilities → returns a dictionary.

**Call relations**: object_index and object_detail include it in their responses.

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 4667–4674)

```
def _filter_value(raw: str) -> JsonValue
```

**Purpose**: Parses a query-string filter value into the scalar type it appears to mean. For example, true can become a boolean instead of the string 'true'.

**Data flow**: It receives a raw string → tries JSON parsing → returns the parsed value or the original string.

**Call relations**: object_index uses it for non-reserved query parameters.

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_fanout_token`  (lines 4677–4682)

```
def _fanout_token(walking: dict[str, str]) -> str | None
```

**Purpose**: Encodes per-agent continuation cursors into one opaque token for fanned-out object listings. It returns None when no agents have more rows.

**Data flow**: It receives a mapping from agent id string to cursor → JSON-serializes and hex-encodes it → returns token or None.

**Call relations**: object_index uses it after merging multi-agent pages.

*Call graph*: called by 1 (object_index); 1 external calls (dumps).


##### `_fanout_walks`  (lines 4685–4702)

```
def _fanout_walks(token: str) -> dict[UUID, str] | None
```

**Purpose**: Decodes a fanned-out object listing cursor back into per-agent cursors. It rejects tokens this route did not mint.

**Data flow**: It receives a token string → hex-decodes and JSON-parses it → validates UUID keys and cursor strings → returns a UUID-to-cursor mapping or None.

**Call relations**: object_index uses it when continuing a fan-out read.

*Call graph*: called by 1 (object_index); 2 external calls (loads, UUID).


##### `_merged_rank`  (lines 4705–4718)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```

**Purpose**: Computes a sortable rank for one object row when results from multiple agents are merged. Missing values, booleans, numbers, and text sort consistently.

**Data flow**: It receives a row and order field → classifies the field value and adds the row name as a tie breaker → returns a tuple used for sorting.

**Call relations**: object_index uses it when no single agent is named.


##### `object_index`  (lines 4721–4813)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists rows for any registered object kind through the portal. It can read one agent namespace or fan out across all agents the member can see.

**Data flow**: It gates the kind and audience → parses sorting, filters, search, and cursor → reads object pages per agent → merges and ranks fan-out results → returns kind metadata, rows, and next cursor.

**Call relations**: This powers generic object index pages for many kinds.

*Call graph*: calls 7 internal fn (list_member_objects, _fanout_token, _fanout_walks, _filter_value, _kind_payload, _object_agent, _object_gate); 4 external calls (__init__, replace, JSONResponse, Response).


##### `object_detail`  (lines 4816–4863)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one object's detail as the member may read it. It includes visible spec, status fields, links, generation, and timestamps.

**Data flow**: It gates kind and agent → reads the named object under member/admin scope → checks each outgoing link's visibility → returns detail JSON or not-found.

**Call relations**: Generic object detail pages call this after selecting a row.

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 4866–4898)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Converts one core live frame into an SSE event for the browser. It maps each frame type to the event name the frontend expects.

**Data flow**: It receives a cursor and LiveFrame → serializes the frame as JSON → returns encoded SSE bytes, or raises for unknown frame types.

**Call relations**: _events uses it for the normal frame stream after adding portal-specific events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 4901–4906)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Submits a prepared portal intent for the selected agent. Prepared intents are structured actions from panels rather than free-form chat text.

**Data flow**: It passes the panel gate → forwards context, request, agent id, member id, and email to submit_intent → returns that response.

**Call relations**: The intent route is a thin authorized wrapper around the panels module.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `actions`  (lines 4909–4927)

```
async def actions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Dispatches a presented object action for a selected agent. The route binds the kind, action, and optional object name instead of trusting the browser body to choose the target.

**Data flow**: It passes the panel gate → reads path parameters → forwards the bound target and member identity to submit_action → returns that response.

**Call relations**: Panel buttons for collection and instance actions post here.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_action).


##### `action_views`  (lines 4930–4944)

```
async def action_views(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists actions declared for an object kind or a named object. It exposes declarations for the UI while leaving final authority checks to dispatch.

**Data flow**: It authenticates → validates the object kind exists → decides collection versus instance binding → serializes declared actions → returns JSON.

**Call relations**: Panels call this when they need controls for a kind or row.

*Call graph*: calls 4 internal fn (object_actions, object_kind, _action_payloads, _audience_for); 2 external calls (JSONResponse, Response).


##### `_write_agent`  (lines 4951–4969)

```
def _write_agent(request: Request, audience: WebAudience, stated: object=None) -> AgentSummary | Response
```

**Purpose**: Chooses which agent namespace a direct object write should run in. It uses a body agent, query agent, or the workspace main agent in that order.

**Data flow**: It receives request, audience, and optional stated agent → parses and checks an agent id or finds the main agent → returns an agent summary or refusal.

**Call relations**: object_write uses it for both apply and delete writes.

*Call graph*: called by 1 (object_write); 2 external calls (Response, UUID).


##### `_direct_result`  (lines 4972–4976)

```
def _direct_result(frame: TerminalFrame, name: str) -> Response
```

**Purpose**: Turns the terminal frame from a direct object write into a simple success or failure JSON response.

**Data flow**: It receives a terminal frame and object name → if done, returns ok true → otherwise returns ok false with a readable reason.

**Call relations**: object_write calls it when the write turn finishes.

*Call graph*: called by 1 (object_write); 1 external calls (JSONResponse).


##### `object_write`  (lines 4979–5053)

```
async def object_write(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Runs direct object create, update, or delete requests from app frames as prepared-intent turns. It waits for the turn result and returns a synchronous-looking response.

**Data flow**: It authenticates → validates kind, agent, JSON body or delete path → builds object_apply or object_delete intent → admits it to an intent conversation → tails until terminal, parked, or timeout → returns result JSON.

**Call relations**: Bridge clients and app frames use this route for object writes.

*Call graph*: calls 7 internal fn (admit, conversation_for, object_kind, tail, _audience_for, _direct_result, _write_agent); 7 external calls (__init__, timeout, dumps, conversation_audience, JSONResponse, json, Response).


##### `object_changes`  (lines 5059–5087)

```
async def object_changes(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the recent object-change audit log for admins. It states what changed and who caused it, without exposing the changed object spec.

**Data flow**: It authenticates → checks admin status → reads recent changes → formats kind, name, verb, caller, agent, and time → returns JSON or admin-only not-found.

**Call relations**: The workspace audit screen calls this.

*Call graph*: calls 2 internal fn (recent_object_changes, _audience_for); 2 external calls (JSONResponse, Response).


##### `settings`  (lines 5090–5102)

```
async def settings(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns configuration for the selected agent, including whether it can be archived by this member. Settings are visible to the agent's web audience.

**Data flow**: It passes the panel gate → finds the agent summary → computes archivable from main/owner/admin state → delegates to agent_settings.

**Call relations**: The settings panel calls this route.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_settings).


##### `agent_setup`  (lines 5105–5135)

```
async def agent_setup(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns what a selected app needs before it works: connector accounts, credentials, standing orders, and whether it already has its own page. It adds friendly provider labels.

**Data flow**: It passes the panel gate → reads setup state → enriches connector rows with labels and summaries → checks for a bound page → returns JSON.

**Call relations**: The app setup screen calls this; it shares provider naming with connection controls.

*Call graph*: calls 5 internal fn (agent_setup, _bound_page, _panel_gate, _provider_label, _provider_summary); 1 external calls (JSONResponse).


##### `_bound_page`  (lines 5138–5165)

```
async def _bound_page(ctx: SurfaceContext, summary: AgentSummary, member_id: UUID) -> ObjectRow | None
```

**Purpose**: Finds the hosted site row bound as an agent's homepage, if the workspace has built one. It reads past normal page visibility because this is about binding state.

**Data flow**: It receives an agent summary and member id → lists site objects filtered by homepage_agent under admin read → returns the first row with a site_url or None.

**Call relations**: agents_index, agent_setup, and homepage use it so setup and homepage state agree.

*Call graph*: calls 1 internal fn (list_member_objects); called by 3 (agent_setup, agents_index, homepage); 1 external calls (__init__).


##### `homepage`  (lines 5168–5187)

```
async def homepage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the current homepage state for a selected agent. It can be a workspace-built hosted site, a shipped app bundle, or none.

**Data flow**: It passes the panel gate → publishes assets → reads the bound page → computes homepage state → returns JSON.

**Call relations**: The portal polls this after initial boot to notice new homepage deployments.

*Call graph*: calls 5 internal fn (_assets_published, _bound_page, _homepage_state, _panel_gate, apps); 1 external calls (JSONResponse).


##### `_homepage_state`  (lines 5190–5237)

```
def _homepage_state(ctx: SurfaceContext, summary: AgentSummary, bound: ObjectRow | None, admin: bool, member_id: UUID) -> dict[str, JsonValue]
```

**Purpose**: Computes the member-visible homepage state for an agent. It applies private-agent rules for built pages and shipped-bundle rules for built-in apps.

**Data flow**: It receives context, agent summary, optional bound row, admin flag, and member id → returns set with URL and generation or none.

**Call relations**: agents_index and homepage use it to draw and refresh agent homepages.

*Call graph*: calls 1 internal fn (apps); called by 2 (agents_index, homepage); 3 external calls (shipped_app_slug, homepage_embed_url, shipped_homepage_url).


##### `_preview_start_page`  (lines 5259–5264)

```
def _preview_start_page(form: FormData) -> int
```

**Purpose**: Parses the first page number requested for document preview. Invalid values fall back to page 1.

**Data flow**: It receives form data → reads start_page → parses an integer and clamps to at least 1 → returns the page number.

**Call relations**: preview uses it before calling the render service.

*Call graph*: called by 1 (preview); 1 external calls (get).


##### `_preview_pages`  (lines 5267–5272)

```
def _preview_pages(form: FormData) -> int
```

**Purpose**: Parses how many preview pages the caller wants and clamps it to the server maximum. Invalid values request the default batch.

**Data flow**: It receives form data → reads pages → parses and clamps between 1 and the batch limit → returns the count.

**Call relations**: preview uses it before calling the render service.

*Call graph*: called by 1 (preview); 1 external calls (get).


##### `preview`  (lines 5275–5321)

```
async def preview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders a temporary preview of an uploaded document before it is sent in chat. Nothing is stored and no turn is admitted.

**Data flow**: It authenticates → validates multipart form and file type → reads one file → asks the preview service for PNG pages → returns page metadata and base64 image data.

**Call relations**: The chat composer calls this while the member is reviewing an attachment.

*Call graph*: calls 6 internal fn (render_preview, _audience_for, _form, _framed_length, _preview_pages, _preview_start_page); 4 external calls (b64encode, PurePosixPath, JSONResponse, Response).


##### `upload_start`  (lines 5324–5360)

```
async def upload_start(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Mints a short-lived direct-upload URL for a chat attachment. The browser can PUT file bytes to blob storage, then later send only the key with the message.

**Data flow**: It authenticates → reads bounded JSON with size, checksum, and name → validates size → creates a safe upload key → asks blob storage for a presigned PUT URL → returns key and URL.

**Call relations**: The chat composer uses this before _parse_inbound later accepts the uploaded_key in a send.

*Call graph*: calls 2 internal fn (_audience_for, _bounded_body); 5 external calls (loads, JSONResponse, Response, inbox_name, uuid4).


### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The portal lets a member change things like an agent’s settings, connected accounts, credentials, scheduled tasks, and other objects. This file makes sure those changes do not bypass the normal engine. Instead of having special web-only write endpoints, it turns each panel submission into a prepared tool intent and admits it into a dedicated conversation called “Portal actions.” Think of it like a service desk ticket: the web form fills out a fixed ticket, the ticket enters the normal queue, and the result of that ticket is the audit trail.

The file first defines what kinds of intent the portal is allowed to submit. `ApplyIntent` is deliberately narrow: each verb must match the kind of object it applies to. For example, credentials can only be deleted here because their secret values are collected through a private prompt, not a normal web form.

The file also defines first-run provider tiles and “unlocks,” which are suggested apps or workflows that become possible after certain accounts are connected.

For incoming requests, the file checks size limits, validates JSON, fills in missing required agent settings from the current agent, rejects impossible choices early, converts the request into a tool call, submits it to the conversation, then waits for the final result. Special actions, such as Slack install links or billing portal links, get custom response formatting.

#### Function details

##### `ApplyIntent.kinds`  (lines 94–98)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the fixed set of object kinds that portal forms are allowed to submit changes for. This keeps the portal from becoming a free-form object mutation endpoint.

**Data flow**: It reads the declared `kind` type on `ApplyIntent`, extracts the allowed literal values, and returns them as a frozen set. Nothing outside the class is changed.

**Call relations**: Other code can ask this class what kinds are accepted instead of keeping a separate list. `ApplyIntent.applying_kinds` and `ApplyIntent.deleting_kinds` build on this shared source of truth.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 101–103)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that may be created or updated through an `apply` intent. It excludes kinds that are only deleted or only connected.

**Data flow**: It starts with all allowed kinds, removes delete-only kinds and connection-only kinds, and returns the remaining names. It does not inspect a request body or change state.

**Call relations**: The web surface uses this when deciding which object pages should show create or edit controls. Because it is derived from the same rules as validation, the page and the server stay in agreement.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 106–112)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that may be deleted from the portal. In this file, every named kind can be deleted, though some kinds cannot be created or edited here.

**Data flow**: It returns the same fixed kind set used by `ApplyIntent`. There is no request input and no side effect.

**Call relations**: The web surface uses this when deciding which object pages should show delete controls. It mirrors the validator’s allowed actions so the portal does not display controls the server would reject.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 115–136)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: Checks that the submitted verb makes sense for the submitted object kind. It prevents unsafe or nonsensical combinations, such as editing a credential secret through a normal panel form.

**Data flow**: It receives a parsed `ApplyIntent`, inspects its verb, kind, spec, and `create_only` flag, and either returns the same intent or raises a validation error. No external state is read or written.

**Call relations**: Pydantic, the validation library used for request models, runs this automatically when an `ApplyIntent` is parsed. That means bad portal submissions are stopped before they can be turned into tool calls.


##### `Unlock._names_offered_tiles_and_a_drawn_mark`  (lines 325–334)

```
def _names_offered_tiles_and_a_drawn_mark(self) -> 'Unlock'
```

**Purpose**: Checks that an unlock suggestion can actually be drawn and explained in the portal. It verifies that its icon exists and that every required provider is one of the providers shown in the first-run catalog.

**Data flow**: It reads the unlock’s icon and required provider groups, compares them with known icons and provider tile names, and returns the unlock if everything is valid. If something cannot be shown correctly, it raises a validation error.

**Call relations**: Pydantic runs this automatically when unlock rows are created at import time. This catches broken catalog entries early, before the portal tries to show a missing icon or unknown provider.


##### `Unlock.missing`  (lines 336–340)

```
def missing(self, held: frozenset[str]) -> tuple[str, ...]
```

**Purpose**: Tells the portal which provider accounts a member still needs to connect before an unlock becomes usable. It chooses one preferred provider from each unmet group.

**Data flow**: It receives the set of provider names the member already has. For each required group, it checks whether any provider in that group is already held; if not, it adds the group’s first provider to the missing list and returns the result as a tuple.

**Call relations**: This supports the start screen’s “you can unlock this by connecting these accounts” behavior. It uses the catalog order as the preference order, so the displayed suggestions are predictable.


##### `_action_intent`  (lines 513–524)

```
def _action_intent(kind: str, name: str | None, action: str, body: dict[str, JsonValue]) -> ToolIntent
```

**Purpose**: Builds the tool intent for a portal action that was already presented to the user. The route supplies the target object, while the browser body supplies only the action’s own input.

**Data flow**: It receives an object kind, optional object name, action name, and body dictionary. It places the target and action into a fixed envelope, puts the body under `input`, and returns a `ToolIntent` for the `object_action` tool.

**Call relations**: `submit_action` calls this after it has validated the request and confirmed the action is available. The returned intent is then admitted into the portal action conversation.

*Call graph*: called by 1 (submit_action); 1 external calls (__init__).


##### `_tool_intent`  (lines 527–562)

```
def _tool_intent(submitted: ApplyIntent) -> ToolIntent
```

**Purpose**: Converts a validated panel intent into the exact tool call the engine should run. It is the translation step between web-form language and engine-tool language.

**Data flow**: It receives an `ApplyIntent`. Connect intents become `connect_account` calls, delete and detach intents become `object_delete` calls, and apply-style intents become `object_apply` calls with a YAML manifest. It returns a `ToolIntent` and does not submit it itself.

**Call relations**: `submit_intent` calls this after request preparation succeeds. The resulting tool intent becomes the durable turn admitted to the portal conversation.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 565–581)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Turns a terminal engine frame into a simple JSON response for the web browser. It covers the common “saved” or “not applied” result shape.

**Data flow**: It receives the final frame from a turn and the turn id. If the frame finished successfully, it returns a success response, including any credential request if one exists. If it failed or refused, it extracts a readable message and returns a failure response.

**Call relations**: This is the default result reader used by intent and action flows. More specialized readers call it when their special action did not finish successfully or when they can use the standard response format.

*Call graph*: called by 6 (_action_outcome, _imessage_outcome, _intent_result, _portal_outcome, _rebuild_outcome, _slack_outcome); 1 external calls (JSONResponse).


##### `_slack_outcome`  (lines 584–599)

```
def _slack_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the result of the Slack connect action and returns the install link, if one was created. If Slack refused or no link is needed, it returns the tool’s own explanation.

**Data flow**: It receives a terminal frame and turn id. On failure, it delegates to `_outcome`; on success, it finds the JSON object embedded in the frame text, reads the Slack authorization URL and hint, and returns them to the browser.

**Call relations**: This is registered in `ACTION_OUTCOMES` for the Slack connect action. When `submit_action` sees that terminal frame, `_action_outcome` routes the frame here so the portal can open the correct install flow.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_imessage_outcome`  (lines 602–623)

```
def _imessage_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the result of the iMessage connect action and reports whether the connection is pending or complete, plus any opt-in link. This gives the browser structured data instead of making it scrape text.

**Data flow**: It receives a terminal frame and turn id. On failure, it uses `_outcome`; on success, it extracts a JSON object from the frame text, validates the connection state, instruction, and optional link, then returns a JSON response.

**Call relations**: This is used for the iMessage connect action through `ACTION_OUTCOMES`. `_action_outcome` selects it when that action’s turn finishes.

*Call graph*: calls 1 internal fn (_outcome); 2 external calls (loads, JSONResponse).


##### `_portal_outcome`  (lines 626–640)

```
def _portal_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Reads the billing portal link produced by the billing action. It is used when the user asked to open the external billing portal.

**Data flow**: It receives a terminal frame and turn id. If the action did not finish successfully, it falls back to `_outcome`; otherwise it extracts a JSON object from the frame text, reads the portal URL, and returns it to the browser.

**Call relations**: `_action_outcome` calls this only for the workspace billing action when the requested operation is the billing portal. Other billing outcomes use the normal `_outcome` path.

*Call graph*: calls 1 internal fn (_outcome); called by 1 (_action_outcome); 2 external calls (loads, JSONResponse).


##### `_rebuild_outcome`  (lines 643–650)

```
def _rebuild_outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Returns the tool’s own message after a rebuild request, such as rebuilding a report digest or page facts. This matters because the visible work may happen later, so the user needs to know what was queued.

**Data flow**: It receives a terminal frame and turn id. If the frame is not successful, it uses `_outcome`; if it is successful, it returns `applied: true` with the frame text as the message.

**Call relations**: This is registered for rebuild actions in `ACTION_OUTCOMES`. `_action_outcome` uses it when those actions finish.

*Call graph*: calls 1 internal fn (_outcome); 1 external calls (JSONResponse).


##### `_action_outcome`  (lines 664–671)

```
def _action_outcome(kind: str, action: str, body: dict[str, JsonValue], frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Chooses the right response reader for a completed portal action. Most actions use the standard result shape, while a few need custom parsing for links or queued-work messages.

**Data flow**: It receives the action target, body, terminal frame, and turn id. It checks for the billing portal special case, otherwise looks up a custom outcome reader, and returns that reader’s response.

**Call relations**: `submit_action` calls this after the admitted action turn reaches a terminal frame. It hands off to `_portal_outcome`, one of the registered custom readers, or `_outcome`.

*Call graph*: calls 2 internal fn (_outcome, _portal_outcome); called by 1 (submit_action).


##### `_complete_agent_spec`  (lines 702–729)

```
async def _complete_agent_spec(ctx: SurfaceContext, submitted: ApplyIntent, submitted_fields: frozenset[str], agent_id: UUID, member_id: UUID) -> ApplyIntent | Response
```

**Purpose**: Fills in required agent settings that a small panel form did not show to the user. This lets a partial edit, such as changing only a prompt-related field, still produce a valid full agent spec.

**Data flow**: It receives the surface context, submitted intent, submitted field names, agent id, and member id. If the intent is not a partial agent apply, it returns it unchanged. Otherwise it reads the current agent detail, merges required existing fields under the submitted changes, and returns a copied intent; if the agent cannot be found, it returns an error response.

**Call relations**: `_prepare_panel_intent` calls this after parsing the request. It uses `ctx.agent_detail` to avoid rejecting a panel edit merely because hidden required fields were not posted.

*Call graph*: calls 1 internal fn (agent_detail); called by 1 (_prepare_panel_intent); 2 external calls (model_copy, JSONResponse).


##### `_intent_refusal`  (lines 732–749)

```
async def _intent_refusal(ctx: SurfaceContext, submitted: ApplyIntent, submitted_fields: frozenset[str]) -> Response | None
```

**Purpose**: Rejects panel intents that are valid in shape but impossible in this deployment. For example, it refuses an unknown model name or a credential slot that does not exist.

**Data flow**: It receives the surface context, prepared intent, and submitted field names. It checks agent model and sandbox-size rules against the deployment, and checks credential names against the current credential slots. It returns a JSON refusal response or `None` if the intent may continue.

**Call relations**: `_prepare_panel_intent` calls this after completing any partial agent spec. By refusing before a turn is admitted, it prevents bad stored changes from breaking later agent runs.

*Call graph*: calls 1 internal fn (list_credential_slots); called by 1 (_prepare_panel_intent); 1 external calls (JSONResponse).


##### `_prepare_panel_intent`  (lines 752–781)

```
async def _prepare_panel_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID) -> ApplyIntent | Response
```

**Purpose**: Reads and validates a raw panel intent request before it can become a tool call. It is the main safety gate for normal panel form submissions.

**Data flow**: It reads the request body, rejects bodies that are too large, parses JSON into `PanelIntent`, checks frame access for connect actions, fills missing agent fields, and applies deployment-specific refusal checks. It returns either a ready `ApplyIntent` or an HTTP response explaining the problem.

**Call relations**: `submit_intent` calls this first. It coordinates `_complete_agent_spec` and `_intent_refusal`, so later code only deals with a clean, allowed intent.

*Call graph*: calls 3 internal fn (frame_admits, _complete_agent_spec, _intent_refusal); called by 1 (submit_intent); 3 external calls (loads, JSONResponse, body).


##### `_oversized_manifest`  (lines 784–798)

```
def _oversized_manifest(intent: ToolIntent) -> Response | None
```

**Purpose**: Checks whether an `object_apply` manifest became too large after conversion to YAML. This catches cases where the original JSON was acceptable but the tool input is still too big.

**Data flow**: It receives a `ToolIntent`. If it is not an `object_apply`, it returns `None`. If it is, it reads the manifest string, measures its encoded byte length, and either returns `None` or a 413 error response.

**Call relations**: `submit_intent` calls this after `_tool_intent` builds the engine tool call. It is a final size guard before the turn is admitted.

*Call graph*: called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `_intent_result`  (lines 801–828)

```
async def _intent_result(ctx: SurfaceContext, turn_id: UUID) -> Response
```

**Purpose**: Waits for an admitted panel intent turn to finish and turns the final engine message into a browser response. It lets the form submission answer synchronously when possible.

**Data flow**: It receives the surface context and turn id. It tails the turn’s frames until it sees a terminal frame or parked frame, converts that into a JSON response, or returns a timeout response if the work takes too long.

**Call relations**: `submit_intent` calls this after admitting the intent. It uses `_outcome` for normal terminal results and directly reports parked or timed-out work.

*Call graph*: calls 2 internal fn (tail, _outcome); called by 1 (submit_intent); 2 external calls (timeout, JSONResponse).


##### `submit_intent`  (lines 831–867)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: Accepts one prepared panel intent from the web portal, submits it through the normal conversation-based engine path, and returns the final result. This is the main write path for standard panel forms.

**Data flow**: It receives the surface context, request, agent id, member id, and email. It prepares and validates the request, converts it to a tool intent, checks size, finds or creates the member’s portal action conversation for this agent, retitles that conversation, admits the turn, and returns the turn result.

**Call relations**: This is called by the web layer for panel submissions. It orchestrates `_prepare_panel_intent`, `_tool_intent`, `_oversized_manifest`, conversation setup, admission through `ctx.admit`, and `_intent_result`.

*Call graph*: calls 7 internal fn (admit, conversation_for, retitle_conversation, _intent_result, _oversized_manifest, _prepare_panel_intent, _tool_intent); 1 external calls (conversation_audience).


##### `submit_action`  (lines 870–959)

```
async def submit_action(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str, *, kind: str, name: str | None, action: str) -> Response
```

**Purpose**: Accepts one portal action that was presented for an object, submits it to the engine, and returns the action’s result. It protects the target object by taking the target from the route, not from the browser body.

**Data flow**: It reads the request body, enforces the size limit, parses a dictionary input, rejects body fields that try to override the route-bound target, verifies that the requested action exists for the object, checks frame access, builds an action intent, admits it to the portal conversation, waits for completion, and returns the right outcome response.

**Call relations**: This is the action counterpart to `submit_intent`. It calls `_action_intent` to build the tool call and `_action_outcome` to interpret the terminal frame, while using the surface context to check available actions and admit the turn.

*Call graph*: calls 8 internal fn (admit, conversation_for, frame_admits, object_actions, retitle_conversation, tail, _action_intent, _action_outcome); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `_update_schema`  (lines 962–974)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the JSON schema used by the settings page for editable agent fields. A JSON schema is a machine-readable description of form fields and their types.

**Data flow**: It asks `AgentSpec` for its schema, removes fields that the page renders with special custom controls, and also hides sandbox size when this deployment does not offer sandbox sizes. It returns the trimmed schema dictionary.

**Call relations**: `agent_settings` calls this when building the settings response. The frontend can then render ordinary settings from the schema without maintaining a separate field list.

*Call graph*: called by 1 (agent_settings); 1 external calls (model_json_schema).


##### `agent_settings`  (lines 977–1021)

```
async def agent_settings(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, *, admin: bool, archivable: bool) -> Response
```

**Purpose**: Returns the data needed to draw an agent’s settings page. It includes the current agent settings, available models, deployment capabilities, form schema, and optionally the web audience for admins.

**Data flow**: It receives the surface context, agent id, member id, and flags for admin and archivable state. It reads the agent detail, returns 404 if missing, optionally reads granted audience emails, builds the current spec and schema, and returns everything as JSON.

**Call relations**: The web settings page calls this as a read projection. It uses `ctx.agent_detail` for the agent’s current state, `_update_schema` for the editable field description, and the web audience helpers when an admin is viewing the page.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


### `extensions/web/ufo_ext_web/community.py`

`io_transport` · `request handling`

The web app needs to show community-published skills without overloading the public skills directory or surprising the user with blank failures. This file is the bridge to that outside website. Think of it like a careful librarian: it asks the directory for a shelf list, keeps recent answers on a clipboard, and only opens a full book when someone asks for that specific book.

There are two main public reads. `CommunitySkills.listing` returns up to 24 skills, either from a search query or, with no query, from the directory’s popular leaderboard. Each row includes only the skill name, its source repository, and install count, because the directory does not provide descriptions cheaply in list results. `CommunitySkills.fetch` downloads one skill’s document and extracts its name, description, and instructions from `SKILL.md`.

The file is deliberately defensive. It uses timeouts, follows redirects, rejects oversized responses, validates repository names, and turns bad responses into `CommunityUnavailable` messages that can be shown to the user instead of pretending nothing was found. It also caches listings for 15 minutes and remembers fetched documents for the life of the process, including “not found” results, so repeated browsing does not keep hitting the public service.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: This turns an HTTP failure code from the skills directory into a clear `CommunityUnavailable` error. It gives a special, user-friendly explanation for rate limiting, where the directory has temporarily refused more reads.

**Data flow**: It receives a numeric response code from the remote site. If the code means “too many requests,” it builds an error explaining the hourly read limit; otherwise it builds an error saying which code the directory returned. The result is an exception object ready to be raised.

**Call relations**: `CommunitySkills._body` and `CommunitySkills._search` call this when the remote site does not return a successful response. It centralizes the wording so both raw downloads and search requests fail in the same understandable way.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: This returns the list of community skills shown for the Community narrowing: either the popular leaderboard or search results for a typed query. It also avoids repeated network calls by reusing a recent cached answer.

**Data flow**: It receives a search string. First it checks whether that exact query is already cached and still fresh. If so, it returns the saved list. If not, it creates an HTTP client, asks either `_search` or `_popular` for results, trims the list to the display limit, refreshes the cache, and returns the skills.

**Call relations**: This is one of the main outward-facing methods of `CommunitySkills`. It calls `_client` to create the network client, then hands off to `_search` when there is a query or `_popular` when there is not. It uses the current clock time to decide whether cached listings are still usable.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: This fetches the full document for one community skill, so the install view can show the skill’s description and instructions. It remembers each result so reopening the same skill does not download it again.

**Data flow**: It receives a source repository like `owner/repo` and a skill name. It combines them into a cache key and returns the cached document if present. Otherwise it splits the source into owner and repository, downloads the directory’s JSON response, looks for a file named `SKILL.md`, parses that file, stores the parsed document or `None`, and returns it.

**Call relations**: This is the second main outward-facing method of `CommunitySkills`. It uses `_client` to make a network client, `_body` to safely download the response, and `_parse` to turn the markdown document into a structured `CommunityDocument`. If the downloaded JSON cannot be read, it raises `CommunityUnavailable` with a message suitable for the route to surface.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: This creates the HTTP client used for calls to skills.sh. It keeps client setup in one place, including timeout, redirect behavior, and the optional test transport.

**Data flow**: It receives a timeout value. It builds an asynchronous HTTP client with that timeout, the configured transport if one was supplied, and redirect-following enabled. It returns the ready-to-use client.

**Call relations**: `CommunitySkills.listing` and `CommunitySkills.fetch` call this before making network requests. Tests can inject a fake transport through `CommunitySkills`, and this helper is where that fake transport becomes part of the client.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: This reads the public leaderboard from the skills.sh home page and turns it into ranked `CommunitySkill` entries. It is used when the user opens Community skills without typing a search query.

**Data flow**: It downloads the leaderboard page payload with `_body`, scans the text for small JSON-looking entries that contain skill IDs, and tries to parse each one. Each parsed entry is passed through `_entry` for validation. Duplicate skill/source pairs are kept only once, and the final list is sorted by install count from highest to lowest. If nothing usable is found, it raises `CommunityUnavailable`.

**Call relations**: `CommunitySkills.listing` calls this when there is no search query. `_popular` relies on `_body` for safe downloading and `_entry` for turning raw directory data into clean skill rows.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: This asks the skills.sh search API for skills matching a user’s query. It returns validated results ordered by popularity.

**Data flow**: It receives an HTTP client and a query string. It sends a GET request to the search endpoint with the query and result limit. If the response is not successful, it turns the status code into a refusal error. Otherwise it reads the returned JSON, converts each raw skill entry with `_entry`, drops unusable entries, sorts the rest by install count, and returns them.

**Call relations**: `CommunitySkills.listing` calls this when the user has typed a query. It calls `_refusal` for failed responses and `_entry` for cleaning each skill record before the list is returned.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: This turns one raw skill record from the directory into a safe `CommunitySkill` object. It rejects records that are missing a name or have a source repository that does not look valid.

**Data flow**: It receives an unknown object, usually parsed from JSON. If it is not a dictionary, it returns `None`. Otherwise it extracts the skill name, source, and install count, checks that the source looks like `owner/repo`, and returns a `CommunitySkill`. Bad or incomplete entries become `None` instead of causing the whole listing to fail.

**Call relations**: `CommunitySkills._popular` and `CommunitySkills._search` both call this because the leaderboard and search endpoint use similar but not identical field names. `_entry` gives both paths one shared cleanup step.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: This safely downloads the raw bytes from a skills.sh URL. It protects the app from bad status codes and unexpectedly huge responses.

**Data flow**: It receives an HTTP client, a URL, a maximum byte count, and optional request headers. It streams the response in chunks. If the remote site returns a non-success code, it raises a refusal error. As chunks arrive, it counts the bytes and stops with `CommunityUnavailable` if the response grows beyond the allowed size. If all is well, it joins the chunks and returns the full byte body.

**Call relations**: `CommunitySkills._popular` uses this to read the leaderboard payload, and `CommunitySkills.fetch` uses it to download a skill document response. It calls `_refusal` for HTTP failures and otherwise acts as the shared guardrail for all large network reads in this file.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: This reads a downloaded `SKILL.md` file and extracts the parts the portal needs: name, description, instructions, and the original document. It returns `None` when the file is not in the expected shape.

**Data flow**: It receives the markdown text of a skill document. It first looks for YAML front matter, which is the metadata block between `---` lines at the top of the file. It safely parses that metadata, checks for a non-empty name and description, treats the remaining markdown as instructions, and returns a `CommunityDocument`. If the metadata is missing, invalid, or incomplete, it returns `None`.

**Call relations**: `CommunitySkills.fetch` calls this after it finds `SKILL.md` inside the downloaded file list. `_parse` is the final step that changes raw document text into the structured object the install view can show.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).


### `extensions/web/ufo_ext_web/starters.py`

`domain_logic` · `request handling for the start screen`

The start screen needs to show useful suggestions, but those suggestions depend on one person’s work, their workspace’s existing apps, and a catalog of apps the product can create. This file is the small factory and pantry for those rows: it makes a fresh “slate” only when someone actually opens the screen, stores it for a short time, and reuses it while it is still safe to reuse.

A slate contains ranked unlocks, which are possible app rows, and sometimes a check-in row, which asks about unfinished work that clearly belongs to the member. The file gives the language model strict writing rules: be concrete, do not suggest work an existing app already covers, and do not turn someone else’s problem into the member’s first-person task.

The cache is deliberately cautious. If the stored slate is fresh, it is returned. If it is old, the old one can still be shown while a new one is made, so the member does not see an empty screen. A short “claim” record acts like a ticket at a deli counter: only one browser tab gets to regenerate the slate. If generation fails, a cooldown record prevents repeated failed attempts for a while. The final model reply is also cleaned up: invalid rows, unknown catalog names, and duplicates are dropped rather than breaking the whole screen.

#### Function details

##### `Slate.fresh`  (lines 112–113)

```
def fresh(self, now: datetime) -> bool
```

**Purpose**: Checks whether a stored slate can still be trusted. A slate is fresh only if it is young enough and was made with the current version of the ranking instructions.

**Data flow**: It receives the current time and reads the slate’s saved creation time and prompt fingerprint. It compares the age against the allowed cache lifetime and compares the saved fingerprint against the current one. It returns true when both checks pass, otherwise false.

**Call relations**: The start-screen read flow uses this as its quick gate before doing any expensive work. If it says the slate is fresh, the cached rows can be shown immediately instead of asking the model again.


##### `starters_key`  (lines 124–125)

```
def starters_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key where one member’s cached starter slate is kept. It makes sure each member’s slate lives under a predictable, separate name.

**Data flow**: It takes a member ID, turns it into the project’s standard member subject string, and prefixes it with the starter-cache label. The result is a text key used to read or write the slate in shared storage.

**Call relations**: The cache reader uses this key when looking for an existing slate and when saving a newly generated one. It relies on the shared member-subject helper so storage keys match the rest of the system’s naming style.

*Call graph*: called by 2 (_held, read); 1 external calls (member_subject).


##### `claim_key`  (lines 128–129)

```
def claim_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key for the temporary “someone is already generating this slate” claim. This prevents two tabs or polls from paying for the same model work at the same time.

**Data flow**: It takes a member ID, converts it to the standard member subject string, and prefixes it with the claim label. The result is a text key used for the short-lived generation lock.

**Call relations**: The claim-making part of the cache flow uses this key to reserve the right to regenerate. The main read flow also deletes this key after the attempt, so later reads are not blocked forever.

*Call graph*: called by 2 (_claim, read); 1 external calls (member_subject).


##### `cooldown_key`  (lines 132–133)

```
def cooldown_key(member_id: UUID) -> str
```

**Purpose**: Builds the storage key that records a recent generation failure. This keeps the start screen from repeatedly hitting the same failing model or provider.

**Data flow**: It takes a member ID, converts it into the standard member subject string, and adds the cooldown prefix. The returned key points to a small record containing the time of the last failure.

**Call relations**: Before generating, the cache flow checks this key to see whether it should wait. If generation fails, the read flow writes to this key so the next read backs off for a while.

*Call graph*: called by 2 (_may_generate, read); 1 external calls (member_subject).


##### `_stamped`  (lines 136–145)

```
def _stamped(held: object, key: str) -> datetime | None
```

**Purpose**: Safely reads a timestamp from a small stored record. If the record is missing, old, malformed, or half-written, it treats it as absent instead of crashing.

**Data flow**: It receives an unknown stored value and the name of the timestamp field to look for. If the value is a dictionary with a string timestamp, it tries to parse that timestamp. It returns a real datetime when parsing works, or null when the value cannot be trusted.

**Call relations**: The generation checks use this helper for both cooldown records and claim records. By returning null on bad data, it lets the surrounding cache logic simply try again rather than failing the user’s start screen.

*Call graph*: called by 2 (_claim, _may_generate); 1 external calls (fromisoformat).


##### `StarterCache.read`  (lines 168–185)

```
async def read(self) -> Slate | None
```

**Purpose**: Runs the full start-screen slate flow for one member. It returns a usable cached slate when possible, regenerates only when allowed, and never lets a provider failure freeze the screen.

**Data flow**: It starts with the current time, reads any stored slate, and returns it immediately if it is fresh. If the slate is missing or stale, it checks whether generation is allowed. When allowed, it asks the model to rank rows, saves the new slate, and returns it. If generation fails, it records a cooldown, logs a warning, clears the generation claim, and returns the old slate if there was one.

**Call relations**: This is the method the start screen depends on. It coordinates the smaller pieces: it asks `_held` for stored data, `_may_generate` for permission, `_rank` for a new model-made slate, and the key helpers for storage cleanup and saving.

*Call graph*: calls 6 internal fn (_held, _may_generate, _rank, claim_key, cooldown_key, starters_key); 2 external calls (now, warn).


##### `StarterCache._held`  (lines 187–194)

```
async def _held(self) -> Slate | None
```

**Purpose**: Loads the currently stored slate for this member, if it is readable. Bad or outdated stored data is ignored rather than shown or allowed to break the screen.

**Data flow**: It builds the member’s starter-cache key, reads that value from storage, and checks that it looks like a dictionary. It then validates the dictionary as a Slate. A valid Slate comes out; missing or invalid data becomes null.

**Call relations**: The main `read` flow calls this first, before deciding whether anything needs to be regenerated. It uses `starters_key` so it reads from the same place where new slates are later saved.

*Call graph*: calls 1 internal fn (starters_key); called by 1 (read).


##### `StarterCache._may_generate`  (lines 196–206)

```
async def _may_generate(self, now: datetime) -> bool
```

**Purpose**: Decides whether this read is allowed to spend effort and model cost on a new slate. It blocks generation when the basics are missing, the workspace cannot spend, a recent failure is cooling down, or another read already has the claim.

**Data flow**: It looks at the cache object’s model, recalled memory, and solvency flag. It then reads the failure cooldown timestamp, if any, and compares it with the current time. If all those checks pass, it tries to claim the right to generate and returns whether that claim succeeded.

**Call relations**: The main `read` method asks this before calling the model. This method delegates the “only one reader gets to regenerate” decision to `_claim` and uses `_stamped` to understand stored cooldown times safely.

*Call graph*: calls 3 internal fn (_claim, _stamped, cooldown_key); called by 1 (read).


##### `StarterCache._claim`  (lines 208–225)

```
async def _claim(self, now: datetime) -> bool
```

**Purpose**: Tries to reserve generation for this one read. This avoids duplicate model calls when the same member has multiple tabs open or the pane polls repeatedly.

**Data flow**: It creates a small record saying when this read claimed generation. It first tries to insert that record only if no claim exists. If a claim already exists, it reads its timestamp: a recent claim blocks this read, while an expired claim can be replaced. It returns true only when this read owns the claim.

**Call relations**: Generation permission flows through `_may_generate`, which calls this as the final gate. It uses `claim_key` for the storage location and `_stamped` to decide whether an existing claim is still alive.

*Call graph*: calls 2 internal fn (_stamped, claim_key); called by 1 (_may_generate); 1 external calls (isoformat).


##### `StarterCache._rank`  (lines 227–254)

```
async def _rank(self, now: datetime) -> Slate
```

**Purpose**: Asks the language model to create a ranked starter slate for this member. It packages the member’s memory, existing apps, and app catalog into a strict structured request.

**Data flow**: It gathers remembered text, current application names, and catalog entries. It sends them to the configured model with detailed instructions and a required tool-style response, meaning the model must fill a named structured form rather than just write free text. The model’s reply is then passed to `settle_slate`, which turns it into a validated Slate.

**Call relations**: The main `read` method calls this only after cache and claim checks say regeneration is allowed. After the model returns, this method hands the raw reply to `settle_slate` so the rest of the system receives clean, usable rows.

*Call graph*: calls 1 internal fn (settle_slate); called by 1 (read); 4 external calls (__init__, __init__, __init__, dumps).


##### `settle_slate`  (lines 257–290)

```
def settle_slate(reply: Message, generated_at: datetime) -> Slate
```

**Purpose**: Turns the model’s structured reply into the final Slate that can be cached and shown. It is forgiving about bad individual rows but strict that the expected structured reply must exist.

**Data flow**: It receives a model message and the generation time. It looks for the expected recorded slate block. From that block, it validates each ranked row, keeps only rows that name a known catalog unlock, removes duplicates, limits the count, and separately validates the optional check-in. It returns a new Slate with the current prompt fingerprint and cleaned rows; if no recorded slate block exists, it raises an error.

**Call relations**: `StarterCache._rank` calls this after the model answers. Its cleaned result goes back to `read`, which stores it in the starter cache; if this function raises because the reply was not in the required shape, `read` treats that as a generation failure and falls back safely.

*Call graph*: called by 1 (_rank); 1 external calls (__init__).


### Chat Channel Bridges
External chat transports translate iMessage, Slack, and terminal interactions into UFO turns and convert replies back into each channel’s native form.

### `extensions/imessage/ufo_ext_imessage/surface.py`

`io_transport` · `main loop and message handling`

This file is the doorway between Apple Messages and the rest of the UFO system. Without it, a text sent to the shared iMessage line would never become a UFO turn, and UFO’s replies would have nowhere to go. The file also protects the connection between a member and a phone number: before a phone can speak for a member, it must prove itself with a short opt-in code.

The main class, ImessageSurface, keeps a long-running listener connected to a MessageProvider, which is the lower-level object that actually talks to the iMessage backend. It remembers a cursor, like a bookmark in a stream, so that after a crash or reconnect it can continue from the right place and avoid replaying old messages. Incoming messages are checked against claimed phone numbers, routed into either direct-message conversations or group-chat conversations, and optionally saved with downloaded attachments.

On the way out, UFO terminal replies, mid-turn replies, and shared files are sent back to the correct iMessage chat. The file is careful about limits: attachments over 20 MB are skipped or linked instead of uploaded. It also treats temporary provider failures as reconnectable, while letting real programming errors surface.

#### Function details

##### `read_claim`  (lines 63–73)

```
def read_claim(stored: object) -> PendingClaim | None
```

**Purpose**: Reads a saved phone-number claim and turns it into a trusted PendingClaim object if it is well formed. If the saved data is missing or broken, it safely treats it as no claim instead of stopping all incoming message processing.

**Data flow**: It receives an unknown stored value. If the value is empty, it returns nothing. If the value matches the expected claim shape, it returns a PendingClaim; if validation fails, it logs that the claim could not be read and returns nothing.

**Call relations**: ImessageSurface._prove calls this while checking whether a direct message contains the opt-in code. This helper keeps bad stored claim data from derailing the whole proof flow.

*Call graph*: called by 1 (_prove); 1 external calls (log).


##### `MessageStreamDisconnected.__init__`  (lines 87–90)

```
def __init__(self, cursor: int | None, error: Exception) -> None
```

**Purpose**: Builds an exception that means the iMessage event stream dropped, while remembering where the listener got to. The remembered cursor lets the listener restart from the right place.

**Data flow**: It receives the last known cursor and the original error. It stores both on the exception object and sets the exception message to the original error text.

**Call relations**: ImessageSurface._consume_connected creates this exception when the provider stream ends or reports a reconnectable outside failure. ImessageSurface.listen catches it and uses the saved cursor to reconnect safely.

*Call graph*: called by 1 (_consume_connected).


##### `contact_card`  (lines 103–115)

```
def contact_card(assigned_phone_number: str) -> bytes
```

**Purpose**: Creates a small digital contact card for the shared iMessage phone number. Members can save it as a known contact, which helps remove Apple’s “Report Junk” banner from the chat.

**Data flow**: It receives the assigned phone number. It formats that number into vCard text, which is a standard contact-card format, and returns the card as bytes ready to attach to a message.

**Call relations**: ImessageSurface._send_contact_card calls this after a member successfully proves a phone number. The generated bytes are handed to the provider as an attachment.

*Call graph*: called by 1 (_send_contact_card).


##### `claim_key`  (lines 118–119)

```
def claim_key(member_id: UUID, phone_number: str) -> str
```

**Purpose**: Creates a private storage key for one member and one phone number. It hashes the member ID and phone number so the raw pair is not used directly as the database key.

**Data flow**: It receives a member ID and phone number. It combines them, runs them through SHA-256, a one-way hashing function, adds a fixed prefix, and returns the resulting string key.

**Call relations**: ImessageSurface._prove uses this key to look up and later delete the saved opt-in claim for the sender’s phone number.

*Call graph*: called by 1 (_prove); 1 external calls (sha256).


##### `queue_key`  (lines 122–123)

```
def queue_key(conversation_id: str, *, direct: bool) -> str
```

**Purpose**: Creates the stable routing label UFO uses to remember which iMessage chat a conversation belongs to. It records both the conversation ID and whether it is a direct chat or group chat.

**Data flow**: It receives an iMessage conversation ID and a direct-or-group flag. It packs them into a compact JSON string and returns that string as the queue key.

**Call relations**: ImessageSurface._admit_message uses this when it asks UFO for the right conversation. Later, outbound functions decode the same key to send replies back to the matching iMessage chat.

*Call graph*: called by 1 (_admit_message); 1 external calls (dumps).


##### `conversation_from_queue`  (lines 126–134)

```
def conversation_from_queue(queue: str) -> ConversationAddress
```

**Purpose**: Turns a saved UFO queue key back into an iMessage destination. This is how outbound replies know which chat to send to and whether that chat was direct or group.

**Data flow**: It receives a queue-key string. It parses the JSON, checks that it has the expected direct-or-group shape, and returns a ConversationAddress with the chat ID and direct flag. If the key is malformed, it raises an error.

**Call relations**: ImessageSurface.post, ImessageSurface.speak, and ImessageSurface.attach call this before sending text or files. It reverses the queue_key format made during inbound admission.

*Call graph*: called by 3 (attach, post, speak); 2 external calls (__init__, loads).


##### `_attachment_content`  (lines 137–138)

```
async def _attachment_content(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps attachment bytes in the streaming shape expected by the workspace file writer. Even though the data is already in memory, the writer expects chunks.

**Data flow**: It receives bytes. It yields those same bytes once as an asynchronous stream, producing no changed data besides that stream form.

**Call relations**: ImessageSurface._downloaded_files calls this after downloading an iMessage attachment. The stream is then passed to the workspace file-writing API.

*Call graph*: called by 1 (_downloaded_files).


##### `ImessageSurface.listen`  (lines 145–169)

```
async def listen(self, context: SurfaceListenerContext) -> None
```

**Purpose**: Runs the long-lived iMessage listener. It connects to the provider, resumes from the saved stream position, and keeps reconnecting when the outside message stream drops.

**Data flow**: It receives a listener context containing things like the public base URL and cursor storage. It creates the provider, reads the saved cursor, consumes messages, and updates or clears the cursor as needed after disconnects. It normally does not return during active service.

**Call relations**: This is the top-level listener method for the surface. It calls ImessageSurface._consume_connected for each connected session, catches MessageStreamDisconnected, asks the provider whether the cursor became invalid, logs the disconnect, waits briefly, and tries again.

*Call graph*: calls 3 internal fn (clear_cursor, cursor, _consume_connected); 3 external calls (Event, sleep, log).


##### `ImessageSurface._consume_connected`  (lines 171–216)

```
async def _consume_connected(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> None
```

**Purpose**: Processes one connected session with the iMessage provider. It starts live listening, catches up on missed events, then admits new events in order.

**Data flow**: It receives the listener context, provider, installation ID, and last cursor. It starts a background pump for live frames, optionally replays missed events after the cursor, skips duplicates, processes new messages, and advances the in-memory cursor. If the provider fails in an outside/reconnectable way, it raises MessageStreamDisconnected with the last safe cursor.

**Call relations**: ImessageSurface.listen calls this after creating or reconnecting a provider. It starts ImessageSurface._pump_live, uses ImessageSurface._catch_up before live processing, and hands each message event to ImessageSurface._process_event.

*Call graph*: calls 5 internal fn (external_error, _catch_up, _process_event, _pump_live, __init__); called by 1 (listen); 4 external calls (Event, Queue, create_task, gather).


##### `ImessageSurface._pump_live`  (lines 218–236)

```
async def _pump_live(self, provider: MessageProvider, ready: asyncio.Event, frames: asyncio.Queue[LiveFrame | LiveFailure]) -> None
```

**Purpose**: Copies live provider events into an internal queue used by the connected-session loop. It separates reading from the provider from processing each event.

**Data flow**: It receives a provider, a readiness signal, and a queue. As provider.subscribe yields frames, it wraps them as LiveFrame objects and puts them in the queue. If the stream errors or ends, it puts a LiveFailure in the queue and marks the stream as ready before finishing.

**Call relations**: ImessageSurface._consume_connected starts this as a background task. That caller reads the queued frames and failures, while this function focuses only on feeding the queue from the provider stream.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (_consume_connected); 4 external calls (__init__, __init__, __init__, set).


##### `ImessageSurface._catch_up`  (lines 238–257)

```
async def _catch_up(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> int
```

**Purpose**: Replays missed iMessage events after a saved cursor. This prevents messages from being lost during downtime or reconnects.

**Data flow**: It receives the listener context, provider, installation ID, and cursor. It asks the provider for catch-up frames, processes each real message frame, tracks the newest sequence number seen, stores that sequence as the cursor, and returns it.

**Call relations**: ImessageSurface._consume_connected calls this before accepting live queued events. It uses ImessageSurface._process_event for each replayed message and then saves the final stream position through the context.

*Call graph*: calls 3 internal fn (store_cursor, catch_up, _process_event); called by 1 (_consume_connected).


##### `ImessageSurface._process_event`  (lines 259–274)

```
async def _process_event(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, sequence: int, message: InboundMessage | None) -> None
```

**Purpose**: Delivers one provider event, if it contains a message, and records that the stream position has been reached. Messages from unclaimed phone numbers are intentionally ignored but still advance the cursor.

**Data flow**: It receives a sequence number and an optional inbound message. If there is a message, it asks the listener context which workspace, if any, the sender belongs to. For a known workspace it calls ImessageSurface._admit_message. Finally it stores the sequence cursor either way.

**Call relations**: Both ImessageSurface._catch_up and ImessageSurface._consume_connected call this for message events. It is the narrow bridge from provider stream events into workspace-specific surface logic.

*Call graph*: calls 3 internal fn (addressed, store_cursor, _admit_message); called by 2 (_catch_up, _consume_connected).


##### `ImessageSurface._admit_message`  (lines 276–320)

```
async def _admit_message(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage) -> None
```

**Purpose**: Turns a valid incoming iMessage into a UFO conversation turn, unless the message is still part of phone-number proof or should be ignored. It is where routing, group-chat filtering, and attachment saving come together.

**Data flow**: It receives a workspace surface context, provider, and inbound message. It looks up the sender’s address claim. If the claim is still pending, it sends the message to ImessageSurface._prove instead. For proven claims, it may ask whether a group message deserves a reply, creates or finds the UFO conversation, downloads any attachments, wraps the member’s text, and admits the turn into UFO.

**Call relations**: ImessageSurface._process_event calls this after finding the workspace for a sender. This function calls queue_key to build the conversation route, ImessageSurface._downloaded_files for attachments, and the surface context’s admit method to hand the message to the rest of UFO.

*Call graph*: calls 7 internal fn (address_claim, admit, ambient_reply_wanted, conversation_for, _downloaded_files, _prove, queue_key); called by 1 (_process_event); 7 external calls (__init__, __init__, sha256, conversation_audience, room_audience, fence_member_message, mint_marker).


##### `ImessageSurface._prove`  (lines 322–361)

```
async def _prove(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage, claim: AddressClaim) -> None
```

**Purpose**: Checks whether a direct iMessage proves ownership of a phone number by containing the expected opt-in code. A successful proof connects the phone to the member without creating a normal UFO turn.

**Data flow**: It receives the surface context, provider, incoming message, and pending address claim. It ignores non-direct or non-pending cases, checks whether the claim expired, accepts opt-out words by releasing the address, reads the saved pending claim, compares the typed message to the expected code, and sends either an error message or a success message. On success it sends a contact card, confirms the address, and deletes the stored claim.

**Call relations**: ImessageSurface._admit_message calls this whenever the sender’s claim still has an expiration time. It uses claim_key and read_claim to find the stored code, uses provider.send_text to answer the user, and calls ImessageSurface._send_contact_card after a successful proof.

*Call graph*: calls 6 internal fn (confirm_address, release_address, send_text, _send_contact_card, claim_key, read_claim); called by 1 (_admit_message); 2 external calls (__init__, now).


##### `ImessageSurface._send_contact_card`  (lines 363–384)

```
async def _send_contact_card(self, provider: MessageProvider, message: InboundMessage, assigned_phone_number: str) -> None
```

**Purpose**: Sends the shared line’s contact card after a phone number is connected. If the provider refuses the card for an outside reason, the connection still remains valid because the card is only a convenience.

**Data flow**: It receives the provider, original inbound message, and assigned phone number. It builds the vCard bytes with contact_card and asks the provider to send them as an attachment. If an outside provider error happens, it logs the refusal instead of disconnecting the message stream.

**Call relations**: ImessageSurface._prove calls this after sending the “Connected” text. This helper hands the generated contact card to the provider and shields the proof flow from non-critical attachment failures.

*Call graph*: calls 4 internal fn (error_code, external_error, send_attachment, contact_card); called by 1 (_prove); 1 external calls (log).


##### `ImessageSurface._downloaded_files`  (lines 386–435)

```
async def _downloaded_files(self, ctx: SurfaceContext, provider: MessageProvider, conversation_id: UUID, attachments: tuple[MessageAttachment, ...]) -> str
```

**Purpose**: Downloads inbound iMessage attachments and saves the ones that fit into the UFO workspace. It also writes a human-readable note explaining which files were saved, skipped as too large, or unavailable.

**Data flow**: It receives the surface context, provider, UFO conversation ID, and attachment list. For each attachment it chooses a safe unique inbox filename, skips files over the size limit, streams downloadable data from the provider, stops if the file grows too large, writes accepted files into the workspace, and collects notes. It returns the notes as text to include with the admitted message.

**Call relations**: ImessageSurface._admit_message calls this before admitting a message with attachments. This function uses provider.download_attachment for iMessage data and SurfaceContext.write_workspace_file to place accepted files under the iMessage inbox directory.

*Call graph*: calls 4 internal fn (write_workspace_file, download_attachment, external_error, _attachment_content); called by 1 (_admit_message); 1 external calls (inbox_name).


##### `ImessageSurface.post`  (lines 437–444)

```
async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Sends the final UFO reply for a turn back to the appropriate iMessage conversation. This is used when a turn has finished and UFO has a terminal response.

**Data flow**: It receives a surface context and a Writeback containing the completed turn result. It decodes the queue key into an iMessage conversation, builds the final text with ImessageSurface._terminal_text, sends that text through a provider, and returns the provider’s send reference.

**Call relations**: This is an outbound surface method called by the UFO runtime when it needs to write the final answer. It relies on conversation_from_queue for routing and ImessageSurface._terminal_text for user-facing formatting.

*Call graph*: calls 2 internal fn (_terminal_text, conversation_from_queue).


##### `ImessageSurface.speak`  (lines 446–450)

```
async def speak(self, ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Sends a mid-turn reply to iMessage before the whole UFO turn is finished. This lets UFO give progress updates or intermediate messages.

**Data flow**: It receives a surface context and a MidTurnReply. It decodes the reply’s queue key, sends the reply text to that iMessage conversation using the provider, and returns the provider’s send reference.

**Call relations**: The UFO runtime calls this for replies produced during a turn. It uses conversation_from_queue to find the iMessage chat and then hands the text directly to provider.send_text.

*Call graph*: calls 1 internal fn (conversation_from_queue).


##### `ImessageSurface.attach`  (lines 452–464)

```
async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None
```

**Purpose**: Uploads UFO-produced files back into the iMessage chat when they fit within the iMessage attachment size limit. Oversized files are skipped here because they are represented as links in the text instead.

**Data flow**: It receives a surface context, a Writeback with artifacts, and an unused reply reference. It decodes the destination conversation, loops over artifacts, skips any over the limit, reads allowed artifact bytes from blob storage with ImessageSurface._artifact_bytes, and sends each as an iMessage attachment.

**Call relations**: The UFO runtime calls this after or alongside posting a reply that has shared files. It uses conversation_from_queue for routing and ImessageSurface._artifact_bytes to safely load each file before handing it to the provider.

*Call graph*: calls 2 internal fn (_artifact_bytes, conversation_from_queue).


##### `ImessageSurface._terminal_text`  (lines 466–488)

```
async def _terminal_text(self, ctx: SurfaceContext, writeback: Writeback, *, direct: bool) -> str
```

**Purpose**: Builds the final text that will be sent to iMessage for a completed turn. It combines the main answer with questions, connection instructions, credential instructions, and links for oversized artifacts.

**Data flow**: It receives the surface context, Writeback, and whether the destination is a direct chat. It gathers the terminal response text, formatted question text, optional connect or credential URLs/messages, and links or names for files too large to attach. It returns the joined message, or a fallback status message if there is no text.

**Call relations**: ImessageSurface.post calls this before sending the final iMessage text. It calls ImessageSurface._question_text for question formatting and asks the context for connect URLs, home URLs, and artifact links when needed.

*Call graph*: calls 4 internal fn (artifact_link, connect_url, home_url, _question_text); called by 1 (post).


##### `ImessageSurface._question_text`  (lines 490–501)

```
def _question_text(self, writeback: Writeback) -> str
```

**Purpose**: Formats any question UFO is asking the user into plain text suitable for iMessage. It includes the question title, each prompt, available options, and any current answer.

**Data flow**: It receives a Writeback. If there is no question, it returns an empty string. Otherwise it reads the question structure and builds a multi-line text block from its title, prompts, options, and chosen answers.

**Call relations**: ImessageSurface._terminal_text calls this while composing the final outbound message. It keeps question formatting separate from the rest of terminal reply formatting.

*Call graph*: called by 1 (_terminal_text).


##### `ImessageSurface._artifact_bytes`  (lines 503–513)

```
async def _artifact_bytes(self, ctx: SurfaceContext, blob_key: str, size_bytes: int) -> bytes
```

**Purpose**: Reads a UFO artifact from blob storage into memory so it can be uploaded as an iMessage attachment. It enforces the 20 MB limit and checks that the file did not change size while being read.

**Data flow**: It receives the surface context, blob key, and expected size. It rejects files that are already too large, streams chunks from blob storage into a byte array, rejects the file if the collected data grows too large, verifies the final byte count matches the expected size, and returns the bytes.

**Call relations**: ImessageSurface.attach calls this for each artifact it is going to upload. This helper protects provider.send_attachment from oversized or inconsistent file data.

*Call graph*: called by 1 (attach).


### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `startup install, request handling, turn execution, delivery, long-running turn monitoring`

This file makes Slack feel like a native place to talk to the agent. Without it, Slack events would be unsafe to trust, messages would not be connected to the right ufo conversation, Slack files would not reach the workspace, and the agent’s answers would not appear back in the right thread.

The file has three big jobs. First, it installs and identifies the Slack app for a workspace. It supports OAuth install, where Slack gives the bot token, and a bring-your-own-app path, where the owner stores credentials and the code proves them with Slack. Second, it receives Slack traffic. It checks Slack’s signature, ignores bot or foreign messages, decides whether the agent was addressed, gathers helpful surrounding thread context, downloads attached files, resolves the Slack user to a ufo member, and admits the message as a turn. For unmentioned thread chatter, it asks a model whether the agent should reply before creating a turn.

Third, it delivers output. Replies are split safely for Slack limits, threaded under the member message they answer, retried carefully so duplicates are avoided, and followed by uploaded shared files. Long-running turns also get live Slack status text and occasional progress messages, like a restaurant server saying “your food is still being prepared.” If the agent asks the user a question, this file renders a Slack form and later rewrites it into the submitted answers.

#### Function details

##### `_env_signing_secret`  (lines 230–234)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from the process environment. This is the fallback secret used to prove that inbound Slack requests really came from Slack.

**Data flow**: It reads one environment variable → treats an empty value as missing → returns the secret text or nothing.

**Call relations**: Workspace-level secret lookup functions call this when a workspace has no private signing secret stored.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 237–244)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use after a request has already been tied to a workspace. It prefers that workspace’s stored secret and falls back to the deploy-wide secret.

**Data flow**: It receives a surface context → asks for the workspace credential → if that slot is unset, reads the environment fallback → returns the secret or nothing.

**Call relations**: The event and interactivity routes call this before checking Slack’s request signature.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 247–255)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret during the early stage when the system is still figuring out which workspace a Slack request belongs to.

**Data flow**: It receives shared auth access and a workspace id → tries the workspace credential → falls back to the environment secret → returns the secret, or nothing if the workspace is unknown or unconfigured.

**Call relations**: Workspace resolution uses this so it can verify the raw Slack request before fully binding the tenant.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 258–262)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client id needed to start or complete an OAuth install. It fails loudly if the deploy is not configured.

**Data flow**: It reads the configured environment variable → returns the id if present → raises an error if missing.

**Call relations**: The OAuth exchange calls this when trading Slack’s temporary code for a bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 265–269)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret needed to complete installation. It prevents a half-configured deploy from silently failing later.

**Data flow**: It reads the configured environment variable → returns the secret if present → raises an error if missing.

**Call relations**: The OAuth exchange uses it alongside the client id.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 272–274)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should redirect to after an owner approves installation.

**Data flow**: It receives the public base URL of the deploy → appends the Slack surface OAuth path → returns the full redirect URL.

**Call relations**: The OAuth callback uses the same URL that the original Slack authorization link used.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 277–289)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the “Add to Slack” link for OAuth installation. The link includes the requested bot permissions and a sealed state value that ties the callback to the right workspace.

**Data flow**: It receives a client id, redirect URL, and state string → URL-encodes Slack’s expected query fields → returns a Slack authorization URL.

**Call relations**: Install flows use this to send workspace owners to Slack’s approval screen.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 293–295)

```
def __init__(self, error: str)
```

**Purpose**: Creates a clear error when Slack identity proof or OAuth identity data is unusable.

**Data flow**: It receives Slack’s error text or a local validation message → stores it on the exception → produces a normal runtime error with that message.

**Call relations**: Identity proving and OAuth exchange raise this when Slack cannot prove the team or bot user.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `bot_token_fingerprint`  (lines 312–313)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Makes a safe fingerprint of a Slack bot token. This lets the code tell whether a stored identity belongs to the current token without storing or comparing the raw token in metadata.

**Data flow**: It receives the token text → hashes it with SHA-256 → returns the hex fingerprint.

**Call relations**: Identity reads, OAuth install, and manifest-app identity proof all use this to tie identity records to a specific bot token.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 316–328)

```
async def read_identity(blob: BlobStore, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the saved Slack team and bot-user identity, but only if it matches the current bot token. This avoids routing events using stale identity data after a reinstall.

**Data flow**: It receives blob storage and a bot token → reads and validates the identity blob → compares its fingerprint with the token → returns the identity or nothing.

**Call relations**: Identity resolution, event handling, and self-user lookup rely on this before trusting a workspace’s Slack identity.

*Call graph*: calls 3 internal fn (exists, get, bot_token_fingerprint); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 331–337)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Finds the Slack bot user id for this workspace, if Slack is installed. Other parts can use this to recognize the app’s own Slack account.

**Data flow**: It receives an identity context → reads the bot token credential → reads the matching identity blob → returns the bot user id or nothing.

**Call relations**: This is a surface identity helper used outside the main event route when code needs to know the Slack bot’s own user id.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_bot_token`  (lines 340–347)

```
async def _bot_token(ctx: SurfaceContext) -> str | None
```

**Purpose**: Fetches the workspace’s Slack bot token without treating a missing token as a crash. A missing token means installation is incomplete or was unset.

**Data flow**: It asks the surface context for the bot token credential → returns it, or returns nothing if the slot is unset.

**Call relations**: The event and interactivity routes call this before making Slack API calls.

*Call graph*: calls 1 internal fn (credential); called by 2 (ingest, interactive).


##### `_identity`  (lines 350–354)

```
async def _identity(ctx: SurfaceContext, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the Slack identity for the current bot token and mirrors the bot user id into the extension store for later hooks.

**Data flow**: It receives context and bot token → reads a matching identity blob → if present, writes the bot user id to the scoped store → returns the identity or nothing.

**Call relations**: Inbound routes and reply mention mapping use this before acting as the Slack bot.

*Call graph*: calls 2 internal fn (_mirror_self_user_id, read_identity); called by 3 (_reply_mention_ids, ingest, interactive).


##### `_mirror_self_user_id`  (lines 360–376)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the known Slack bot user id into a simple scoped store that hook code can read. This bridges a gap because hooks do not have direct blob-storage access.

**Data flow**: It receives a workspace id and bot user id → skips if already mirrored in this process → writes it to the store best-effort → updates an in-memory cache.

**Call relations**: Identity reads and OAuth install call this; failures are logged but do not block Slack requests.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 389–395)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Gets a valid Slack identity for a manually configured Slack app. It reuses a saved identity when possible and otherwise proves the bot token with Slack.

**Data flow**: It receives its stored blob and bot token from the resolver object → checks for a matching identity → if absent, calls Slack proof and saves the result → returns the identity.

**Call relations**: Background identity proof and manifest-app setup use this to establish the team and bot user.

*Call graph*: calls 2 internal fn (_prove, read_identity).


##### `SlackIdentityResolver._prove`  (lines 397–422)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack’s auth.test endpoint what team and bot user a pasted bot token belongs to. This is the proof step for bring-your-own Slack apps.

**Data flow**: It sends the bot token to Slack → validates Slack’s response shape and id formats → returns a SlackIdentity with a token fingerprint.

**Call relations**: The resolver calls this only when no matching identity record already exists.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 428–442)

```
def _prove_identity_in_background(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Starts identity proof after a request arrives for a workspace that has a bot token but no readable identity record. It lets Slack retry later instead of blocking the current request.

**Data flow**: It receives context and token → if no proof task is already running for the workspace, creates one → stores the task until it finishes.

**Call relations**: The identity-unavailable response uses this for incomplete manifest-app installs.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 1 (_identity_unavailable); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 438–440)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished background identity-proof task from the in-memory task table.

**Data flow**: It receives the completed task → checks it is still the tracked task for that workspace → deletes the tracking entry.

**Call relations**: It is attached as the completion callback when a background identity proof is started.


##### `_run_identity_proof`  (lines 445–451)

```
async def _run_identity_proof(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Runs the actual background identity proof and logs any failure. It keeps identity problems visible to operators without failing the already-acknowledged Slack request.

**Data flow**: It receives context and token → creates a resolver → attempts to resolve and save identity → logs known Slack identity errors or unexpected exceptions.

**Call relations**: The background proof starter creates this task when identity is missing but a token exists.

*Call graph*: called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `_identity_unavailable`  (lines 459–487)

```
def _identity_unavailable(ctx: SurfaceContext, bot_token: str | None) -> Response
```

**Purpose**: Builds the response when Slack is installed incompletely or identity has not been proven yet. It either asks Slack to retry later or tells Slack not to retry when there is no token to prove.

**Data flow**: It receives context and maybe a bot token → if no token, logs once and returns a no-retry 503 → if a token exists, starts proof in the background and returns a retryable 503.

**Call relations**: Event and interactivity routes use this before they can safely process Slack traffic.

*Call graph*: calls 1 internal fn (_prove_identity_in_background); called by 2 (ingest, interactive); 2 external calls (Response, warn).


##### `signing_secret_fingerprint`  (lines 494–497)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Makes a safe fingerprint of a Slack signing secret. This lets the install-live marker prove it used the current secret without storing the secret itself.

**Data flow**: It receives the signing secret → hashes it → returns the hex fingerprint.

**Call relations**: URL verification marking and live-install checks use this.

*Call graph*: called by 2 (_mark_url_verified, verifying_fingerprint); 1 external calls (sha256).


##### `verifying_fingerprint`  (lines 500–507)

```
async def verifying_fingerprint(credentials: CredentialAccess) -> str | None
```

**Purpose**: Finds the fingerprint of the signing secret currently expected for the workspace. If no workspace-specific secret is stored, this function reports no fingerprint.

**Data flow**: It receives credential access → tries to read the Slack signing secret slot → fingerprints it → returns the fingerprint or nothing.

**Call relations**: The install-status check uses this to decide whether an old URL verification still counts.

*Call graph*: calls 2 internal fn (get, signing_secret_fingerprint); called by 1 (install_is_live).


##### `install_is_live`  (lines 510–524)

```
async def install_is_live(ext: ExtensionContext) -> bool
```

**Purpose**: Answers whether Slack is fully connected for the current workspace. It checks both directions: Slack can reach this deploy, and this deploy has a bot token to answer Slack.

**Data flow**: It reads the current signing-secret fingerprint → checks that the bot token slot is filled → compares the scoped-store verification marker → returns true or false.

**Call relations**: The Slack connect/setup experience uses this to show whether the install is connected.

*Call graph*: calls 1 internal fn (verifying_fingerprint).


##### `slack_oauth_exchange`  (lines 544–577)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Completes OAuth installation by trading Slack’s temporary authorization code for a bot token and identity facts.

**Data flow**: It receives the code and redirect URL → posts them with the app credentials to Slack → validates the returned token, team id, bot user id, and optional app id → returns a SlackInstall.

**Call relations**: The OAuth callback calls this after the owner approves the Slack app.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `slack_app_dm_url`  (lines 580–585)

```
def slack_app_dm_url(app_id: str, team_id: str) -> str
```

**Purpose**: Builds a browser URL that opens the installed Slack app’s direct-message home. This gives the owner a convenient next step after install.

**Data flow**: It receives app id and team id → URL-encodes them for Slack’s app redirect endpoint → returns the URL.

**Call relations**: The OAuth callback includes this link on the success page when Slack supplied an app id.

*Call graph*: called by 1 (oauth_callback); 1 external calls (urlencode).


##### `SlackConversationSearch.run`  (lines 672–686)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations by channel text or by the people in a DM. This helps the agent find where a user wants something sent or read.

**Data flow**: It trims the query → lists conversations → resolves people for DMs → builds searchable conversation records → returns matches plus a flag if the scan was capped.

**Call relations**: It coordinates the conversation-search helper methods and Slack API calls.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 688–707)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Reads a bounded number of Slack conversation-list pages. The bound prevents a huge workspace or bad cursor from making the search run forever.

**Data flow**: It starts with an empty cursor → repeatedly asks Slack for a page → appends channel objects → returns the collected objects and whether more pages remained.

**Call relations**: The search runner calls this before filtering and people resolution.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 709–717)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list request.

**Data flow**: It receives a cursor → creates parameters for conversation types, archived filtering, page size, and optional cursor → returns the parameter dictionary.

**Call relations**: The list reader calls it for each page.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 719–722)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a response.

**Data flow**: It receives a Slack payload → looks inside response metadata → returns the cursor string or an empty string.

**Call relations**: The list reader uses it to know whether to keep paging.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 724–752)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves member labels for DMs and group DMs so they can be searched by person, not only by Slack’s channel id.

**Data flow**: It receives an HTTP client and raw conversations → gathers member ids for a bounded number of DMs → looks up users once each → returns conversation-id-to-labels plus whether it hit the cap.

**Call relations**: The search runner uses these labels when building searchable conversation records.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 754–761)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies one Slack conversation as public channel, private channel, group DM, or one-to-one DM.

**Data flow**: It reads Slack boolean fields from a raw conversation object → returns a simple kind string.

**Call relations**: Conversation listing, member lookup, and record construction all use this common classification.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 763–777)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets the member ids for a DM or group DM. A one-to-one DM carries its user directly; a group DM needs a Slack members call.

**Data flow**: It receives client, raw conversation, and id → returns the direct user for an IM or asks Slack for members of a group DM → returns a tuple of user ids.

**Call relations**: The people resolver calls this for each DM-like conversation.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 779–784)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Turns a Slack user lookup into a human-readable search label.

**Data flow**: It receives a SlackUser or nothing plus a user id → prefers name with email, then name, then email, then the raw id → returns one label string.

**Call relations**: The people resolver uses this after user lookups.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 786–803)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts one raw Slack conversation object into the smaller searchable model used by this extension.

**Data flow**: It receives a raw object and resolved people labels → validates the id → pulls name, purpose, topic, kind, membership, and people → returns a SlackConversation or nothing.

**Call relations**: The search runner calls this for every listed raw conversation before matching the query.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 805–807)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely extracts Slack’s nested purpose or topic text.

**Data flow**: It receives a possible nested field → reads its value if it is a dictionary with text → returns that text or an empty string.

**Call relations**: Conversation conversion uses it for purpose and topic.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 988–1003)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a Slack request is recent and signed with the expected secret. This prevents forged or replayed HTTP requests from becoming user messages.

**Data flow**: It receives headers, raw body, signing secret, and optional current time → validates timestamp and HMAC signature → returns nothing on success or raises a signature error.

**Call relations**: Workspace resolution, event ingest, and interactive form ingest call this before trusting a request.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 1010–1029)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw request body with a size limit. The raw bytes must be preserved because Slack signs the exact body.

**Data flow**: It receives a request → returns cached bytes if already read → otherwise streams chunks, enforcing the max size → stores and returns the complete bytes.

**Call relations**: All Slack HTTP entry paths call this before signature verification or payload parsing.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 1032–1041)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack’s URL verification handshake and extracts the challenge to echo back.

**Data flow**: It receives raw bytes → parses JSON → if it is a url_verification payload, returns the challenge string → otherwise returns nothing.

**Call relations**: Workspace resolution and event ingest use this special case before normal event processing.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 1044–1061)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Extracts a Slack team id from an untrusted request body so the system can find the right workspace before verifying the signature.

**Data flow**: It tries JSON first and form-encoded interactivity payload second → looks for team id fields → validates the id shape → returns the team id or nothing.

**Call relations**: Workspace resolution uses this hint, then verifies the request with that workspace’s secret.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 1064–1065)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Builds the stable installation key used to bind one Slack team to one ufo workspace.

**Data flow**: It receives a Slack team id → prefixes it with the installation namespace → returns the binding id.

**Call relations**: OAuth install stores this binding, and workspace resolution looks it up.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 1068–1106)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace a Slack HTTP request belongs to, or handles Slack’s URL challenge without binding any workspace.

**Data flow**: For OAuth GET requests, it opens the sealed state and returns its workspace. For Slack POSTs, it reads the raw body, extracts the team, finds the bound workspace, verifies the signature, and returns the workspace id; otherwise it returns nothing or a challenge response.

**Call relations**: This is the pre-route resolver that protects all Slack routes from cross-workspace confusion.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 1109–1112)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state is specifically for Slack OAuth installation.

**Data flow**: It receives decoded credential claims → checks the payload marker and expected bot-token slot → returns true or false.

**Call relations**: Workspace resolution and OAuth callback both use it to reject unrelated sealed states.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 1115–1120)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the ufo conversation key for a Slack conversation. Channels are keyed by thread root, while DMs are keyed by channel because each DM message can become its own Slack thread.

**Data flow**: It receives channel id, root timestamp, and whether it is a DM → returns either the channel id or channel-root pair.

**Call relations**: Inbound message parsing and interactive answer parsing use this to find the right ufo conversation.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `slack_message_addressed`  (lines 1123–1141)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly asking the agent to participate. DMs always count; channel messages count when they mention the bot in real message content.

**Data flow**: It receives a Slack event, bot user id, and DM flag → scans message bodies for addressing mentions while avoiding footer self-mentions → returns true or false.

**Call relations**: Inbound parsing uses this to decide whether to admit immediately or consider ambient-reply logic.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1144–1147)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts links in reply text so Slack link previews can be disabled when there are many. This keeps source-heavy answers from being buried under preview cards.

**Data flow**: It receives text → counts Markdown links and remaining bare URLs → returns the total.

**Call relations**: Slack reply body building uses this before sending messages.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `_slack_atomic_spans`  (lines 1150–1188)

```
def _slack_atomic_spans(text: str) -> list[tuple[int, int]]
```

**Purpose**: Finds chunks of Markdown that should not be split in the middle, such as code fences and tables.

**Data flow**: It receives text → scans line by line → records start and end positions of fenced code or table-like blocks → returns the spans.

**Call relations**: Reply splitting uses these spans to avoid breaking readable formatting.

*Call graph*: called by 1 (slack_reply_parts); 1 external calls (match).


##### `_slack_reply_cut`  (lines 1191–1211)

```
def _slack_reply_cut(text: str, start: int, limit: int, atomic_spans: list[tuple[int, int]]) -> int
```

**Purpose**: Chooses a good cut point for one Slack message part. It prefers paragraph, line, sentence, or word boundaries and avoids cutting inside atomic Markdown spans.

**Data flow**: It receives text, a start point, a size limit, and protected spans → searches for the best boundary before the limit → returns the cut index.

**Call relations**: Reply splitting calls this repeatedly for long answers.

*Call graph*: called by 1 (slack_reply_parts); 1 external calls (finditer).


##### `slack_reply_parts`  (lines 1214–1231)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long Slack reply text into message-sized parts without damaging Markdown more than necessary.

**Data flow**: It receives text and a limit → validates them → if needed, computes protected spans and cuts the text into ordered parts → returns a list of strings.

**Call relations**: Terminal replies, mid-turn replies, and Block Kit body creation use this before posting to Slack.

*Call graph*: calls 2 internal fn (_slack_atomic_spans, _slack_reply_cut); called by 3 (post, slack_reply_body, speak).


##### `slack_reply_body`  (lines 1234–1301)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for one Slack message. It can include Markdown blocks, question controls, connect buttons, metadata for duplicate detection, and footer text.

**Data flow**: It receives channel, thread, text, optional metadata, delivery id, blocks/actions settings → constructs Slack chat.postMessage JSON within Slack size limits → returns encoded bytes or raises if too large.

**Call relations**: Posting replies, progress updates, and mid-turn messages use this before calling Slack.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 3 (_say, post, speak); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1304–1305)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a simple Slack section block containing Markdown text.

**Data flow**: It receives text → truncates it to Slack’s section limit → returns a block dictionary.

**Call relations**: Question rendering uses it for titles, prose fallbacks, and hints.

*Call graph*: called by 2 (_ask_prose, slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1308–1352)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question as Slack Block Kit form controls when possible. If Slack cannot express the question safely, it renders prose and asks the member to reply in the thread.

**Data flow**: It receives an optional ask object → builds a title and one control per question → returns blocks with a submit button, prose fallback blocks, or nothing.

**Call relations**: Terminal reply posting attaches these blocks when the turn ends by asking the user.

*Call graph*: calls 3 internal fn (_ask_control, _ask_prose, _mrkdwn_section); called by 1 (post).


##### `_ask_control`  (lines 1355–1407)

```
def _ask_control(index: int, ask: AskQuestion) -> dict[str, object] | None
```

**Purpose**: Builds one Slack input control for one question. It chooses text input, radio buttons, or checkboxes based on the question shape and Slack’s limits.

**Data flow**: It receives question index and question data → checks unsupported attachments or oversized choices → returns an input block or nothing for prose fallback.

**Call relations**: The ask-block renderer calls this for each question.

*Call graph*: calls 1 internal fn (_ask_option); called by 1 (slack_ask_blocks).


##### `_ask_option`  (lines 1410–1420)

```
def _ask_option(option: QuestionOption) -> dict[str, object]
```

**Purpose**: Converts one answer option into Slack’s option format.

**Data flow**: It receives a question option → places its label as text and value → adds a truncated description if present → returns the option dictionary.

**Call relations**: Question controls use this for radio-button and checkbox options.

*Call graph*: called by 1 (_ask_control).


##### `_ask_prose`  (lines 1423–1431)

```
def _ask_prose(ask: AskQuestion) -> dict[str, object]
```

**Purpose**: Renders one question as readable Slack prose when an interactive control cannot represent it.

**Data flow**: It receives a question → builds lines for the question, choices, and multi-select note → wraps them in a Markdown section block.

**Call relations**: The ask-block renderer uses this for fallback display.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (slack_ask_blocks).


##### `slack_connect_blocks`  (lines 1434–1460)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a Slack button that lets a member complete an external provider connection privately.

**Data flow**: It receives an optional connect request and turn id → if there is a request, returns an actions block with a button carrying the turn id → otherwise returns nothing.

**Call relations**: Terminal reply posting adds these blocks when the agent asks the member to connect another service.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1463–1467)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required non-empty string field from a Slack payload.

**Data flow**: It receives a mapping and field name → checks the value is a non-empty string → returns it or raises a clear error.

**Call relations**: Inbound and interaction parsing use it for required Slack ids and timestamps.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `_inbound_files`  (lines 1470–1482)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable files declared on a Slack message, skipping hidden or tombstoned entries.

**Data flow**: It receives a Slack event → looks at at most the allowed number of file entries → keeps entries with name and private URL → returns inbound file records.

**Call relations**: Inbound parsing and fallback file lookup both use this before downloading attachments.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1485–1516)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Looks up files for an app_mention event when Slack did not include file details directly. It reads exactly the message that Slack said had files.

**Data flow**: It receives bot token, channel, message timestamp, and optional root → fetches the bounded thread message from Slack → extracts files from the matching message → returns file records or none.

**Call relations**: Inbound parsing uses this only when it needs a second Slack read to discover attachments.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1524–1599)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes Slack OAuth installation after the owner approves the app. It stores the bot token, binds the Slack team to the workspace, saves identity, and shows a success or error page.

**Data flow**: It reads query parameters → validates sealed state → exchanges the code with Slack → binds installation → stores credential and identity → mirrors bot user id → returns a browser callback page.

**Call relations**: This is the browser-facing endpoint for OAuth installs and feeds the later event route with credentials and identity.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, slack_app_dm_url, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 3 external calls (__init__, __init__, callback_page).


##### `_mark_url_verified`  (lines 1605–1623)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this deploy using the current signing secret. This is used to show a manifest-app setup as connected.

**Data flow**: It receives context and signing secret → fingerprints the secret → writes a marker blob → mirrors the fingerprint into scoped storage → caches the write in memory.

**Call relations**: Event and interactivity routes call this after a signed Slack request or URL challenge is accepted.

*Call graph*: calls 2 internal fn (mirror_url_verified, signing_secret_fingerprint); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `mirror_url_verified`  (lines 1626–1641)

```
async def mirror_url_verified(workspace_id: UUID, fingerprint: str) -> None
```

**Purpose**: Copies the URL verification fingerprint into the extension store where setup/status code can read it.

**Data flow**: It receives workspace id and fingerprint → skips if already written in this process → writes to scoped storage best-effort → records the in-memory cache.

**Call relations**: URL verification marking calls this after writing the durable marker.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (__init__).


##### `ingest`  (lines 1644–1688)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Events API requests. It verifies the request, filters irrelevant events, admits addressed messages, and starts background decisions for unaddressed thread chatter.

**Data flow**: It reads the raw body → finds and checks the signing secret → handles URL verification → loads bot token and identity → converts the event to an inbound message → admits it or schedules ambient decision → returns Slack an acknowledgement.

**Call relations**: This is the main inbound Slack message route; it hands real work to parsing, admission, file, ambient, and follower helpers.

*Call graph*: calls 12 internal fn (_admit_inbound, _bot_token, _ctx_signing_secret, _decide_ambient_in_background, _folds_into_live_turn, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_inbound (+2 more)); 3 external calls (loads, JSONResponse, Response).


##### `_folds_into_live_turn`  (lines 1691–1729)

```
async def _folds_into_live_turn(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> bool
```

**Purpose**: Decides whether an unmentioned Slack reply should be absorbed by a turn already running in that thread. Such replies bypass the ambient decision because the active turn can act on them.

**Data flow**: It receives context, token, and inbound message → checks for an existing absorbing turn → resolves the sender to a member with access → returns true if the message can fold into that live turn.

**Call relations**: The ingest route calls this before deciding whether to ask the ambient-reply model.

*Call graph*: calls 4 internal fn (absorbing_turn, member_has_access, _resolve_member, _slack_user); called by 1 (ingest); 1 external calls (log).


##### `_admit_inbound`  (lines 1732–1782)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Turns a verified Slack message into a ufo turn. It resolves the sender, gathers context, downloads attachments, mirrors thread information, and submits the message to core admission.

**Data flow**: It receives context, token, inbound message, and identity → fetches sender, context, permalink, and names → resolves member and conversation → writes thread mirrors and files → builds fenced message text → admits it → anchors DM replies and starts status followers if needed.

**Call relations**: Ingest and ambient-decision tasks call this when a Slack message should become a turn.

*Call graph*: calls 13 internal fn (admit, conversation_for, retitle_conversation, _ambient_context, _anchor_dm_thread, _arm_followers, _download_files, _mirror_thread, _resolve_member, _slack_permalink (+3 more)); called by 2 (_run_ambient_decision, ingest); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1788–1813)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts a background task to decide whether an unaddressed thread message deserves an agent reply. This keeps Slack’s required quick acknowledgement fast.

**Data flow**: It receives the inbound message and related context → if no task already exists for that message id, creates one → tracks it until completion.

**Call relations**: The ingest route uses this after acknowledging ambient chatter that might need a reply.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1809–1811)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Stops tracking a completed ambient-decision task.

**Data flow**: It receives the completed task → checks it is still the task for that message id → removes it from the task table.

**Call relations**: It is registered as the completion callback for background ambient decisions.


##### `_run_ambient_decision`  (lines 1816–1831)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the ambient-reply decision and admits the message if the answer is yes. It logs failures because Slack has already been acknowledged.

**Data flow**: It receives context, token, inbound message, and identity → asks whether a reply is wanted → if yes, admits the inbound message → logs any exception.

**Call relations**: The background ambient scheduler creates this task.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1834–1841)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages from users outside the installed Slack workspace, such as Slack Connect guests. The app does not serve those authors directly.

**Data flow**: It receives an event and the workspace team id → compares Slack’s author-team fields with the bound team → returns true for foreign authors.

**Call relations**: Inbound parsing uses this early to skip messages the app should not admit.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1857–1893)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines the privacy audience and readable label for the Slack place where a message arrived. This controls who may see the resulting ufo conversation.

**Data flow**: It receives context, payload, event, channel id, and whether an audience is already known → uses event hints or Slack channel info → returns an audience plus optional label, or raises if unknowable.

**Call relations**: Inbound parsing calls this before creating or finding the conversation.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1896–1946)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Converts a raw Slack event into the smaller Inbound object this file can admit. It filters out bot messages, unsupported events, foreign authors, and irrelevant unaddressed traffic.

**Data flow**: It receives context, payload, and identity → validates event type and user → decides addressing and thread key → checks participation for unaddressed replies → resolves origin and files → returns an Inbound or nothing.

**Call relations**: The ingest route calls this after request verification and identity loading.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1949–1960)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has an admitted ufo turn. This distinguishes a real ongoing conversation from a row that was just created mid-ingest.

**Data flow**: It receives context and queue key → finds the conversation → checks whether it has a latest turn → returns the conversation id or nothing.

**Call relations**: Inbound parsing uses this to decide whether unaddressed thread replies can be considered.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1963–1995)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches Slack user profile facts, including confirmed email, display name, timezone, and team id. These facts help identify the member and annotate the turn.

**Data flow**: It receives bot token and Slack user id → calls users.info with a short timeout → validates profile fields → returns a SlackUser or nothing on failure.

**Call relations**: Admission, live-turn folding, answer submits, name resolution, and conversation search all use this.

*Call graph*: calls 1 internal fn (_slack_ok); called by 5 (_people, _name, _admit_inbound, _folds_into_live_turn, _handle_answer_submit); 2 external calls (__init__, AsyncClient).


##### `_conversation_members`  (lines 2009–2029)

```
async def _conversation_members(bot_token: str, channel: str) -> tuple[str, ...]
```

**Purpose**: Reads a bounded roster of Slack members in a conversation. This is used to safely map agent-written @names only to people already in the thread’s room.

**Data flow**: It receives token and channel → calls Slack conversations.members with a limit → returns member ids or an empty tuple on failure.

**Call relations**: SlackNames.mention_ids calls it before resolving outbound mentions.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (mention_ids); 1 external calls (AsyncClient).


##### `SlackNames.of`  (lines 2050–2058)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack ids mentioned in incoming text into readable names. This makes admitted messages easier for the agent to understand.

**Data flow**: It receives message texts and optional user ids → collects mentioned users and channels → resolves them through cache and Slack → returns id-to-name mappings.

**Call relations**: Inbound admission and ambient digest building use this to render Slack markup into human names.

*Call graph*: calls 1 internal fn (_resolved); 2 external calls (mentioned_channels, mentioned_users).


##### `SlackNames.mention_ids`  (lines 2060–2074)

```
async def mention_ids(self, channel: str, identity: SlackIdentity) -> dict[str, str]
```

**Purpose**: Builds the safe map from names in an agent reply to Slack mention ids for the current conversation. It only includes members of this Slack workspace and conversation.

**Data flow**: It receives channel and identity → reads the conversation roster → resolves user names → drops the bot and foreign-team users → returns a mention-key map.

**Call relations**: Reply delivery uses this before replacing @names with notifying Slack mentions.

*Call graph*: calls 2 internal fn (_resolved, _conversation_members); 1 external calls (mention_index).


##### `SlackNames._resolved`  (lines 2076–2084)

```
async def _resolved(self, wanted: Mapping[str, str]) -> dict[str, _NamedId]
```

**Purpose**: Combines cached name lookups with fresh Slack lookups, within a limit. This avoids repeated Slack calls while keeping names reasonably current.

**Data flow**: It receives wanted ids and their Slack API URLs → reads remembered names → fetches a bounded set of missing ones → writes them back to cache → returns the combined mapping.

**Call relations**: Both inbound name rendering and outbound mention mapping use this shared resolver.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); called by 2 (mention_ids, of); 1 external calls (gather).


##### `SlackNames._remembered`  (lines 2086–2107)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, _NamedId]
```

**Purpose**: Reads still-fresh cached Slack names from scoped storage.

**Data flow**: It receives ids → reads their cache rows → checks shape and timestamp against the TTL → returns valid remembered _NamedId entries.

**Call relations**: The resolver calls this before making Slack API requests.

*Call graph*: called by 1 (_resolved); 3 external calls (__init__, __init__, now).


##### `SlackNames._name`  (lines 2109–2126)

```
async def _name(self, id_: str, url: str) -> _NamedId | None
```

**Purpose**: Fetches and cleans the display name for one Slack user or channel id.

**Data flow**: It receives an id and which Slack endpoint to use → fetches user or channel info → removes dangerous delimiters and compresses whitespace → returns a bounded name with team information or nothing.

**Call relations**: The resolver calls this for cache misses.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (_resolved); 1 external calls (__init__).


##### `SlackNames._remember`  (lines 2128–2138)

```
async def _remember(self, names: Mapping[str, _NamedId]) -> None
```

**Purpose**: Writes freshly resolved Slack names into scoped storage for later reuse.

**Data flow**: It receives id-to-name records → stamps them with the current time → writes each cache row best-effort.

**Call relations**: The resolver calls this after successful fresh lookups.

*Call graph*: called by 1 (_resolved); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 2141–2161)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Fetches Slack’s own permanent link to a message. This gives the turn a trustworthy source link instead of trying to guess Slack URL formats.

**Data flow**: It receives bot token, channel, and timestamp → calls chat.getPermalink → returns the permalink or nothing on failure.

**Call relations**: Inbound admission and submitted-answer admission use this for turn context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 2164–2181)

```
def _turn_context(sender: SlackUser | None, source: str | None, question: str | None=None) -> TurnContext
```

**Purpose**: Builds the ufo context attached to an admitted Slack turn, such as sender, timezone, source link, and answered question.

**Data flow**: It receives optional Slack user, source URL, and optional question → formats sender identity → validates timezone → returns a TurnContext, dropping bad timezone data if needed.

**Call relations**: Inbound and form-answer admission call this before admitting a turn.

*Call graph*: called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (__init__).


##### `_resolve_member`  (lines 2184–2202)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member. Existing links win; otherwise a confirmed Slack email can link or join the member automatically.

**Data flow**: It receives context, Slack user id, DM flag, and Slack user facts → checks existing link → if needed, uses confirmed email to join → returns member id or nothing, with DM failure if user info is unavailable.

**Call relations**: Admission, live-turn folding, and answer submits call this to decide who spoke.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 3 (_admit_inbound, _folds_into_live_turn, _handle_answer_submit); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 2205–2230)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether an unaddressed thread reply should start a new agent turn. This prevents the bot from barging into side conversations.

**Data flow**: It receives context, token, inbound message, and identity → fetches recent Slack thread history → if history is empty, admits by default; otherwise asks core’s ambient decision → returns true or false and logs silence.

**Call relations**: The background ambient-decision task calls this before admission.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 2233–2255)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Builds the recent thread-history view used by the ambient-reply decision.

**Data flow**: It receives token, inbound message, and identity → fetches thread messages before the inbound timestamp → converts valid member and own-bot messages into AmbientMessage objects → returns the latest bounded set oldest first.

**Call relations**: The ambient decision uses this as the model’s evidence.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 2258–2304)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Fetches a trusted tail of a Slack thread before a given message. It walks pages so long threads do not accidentally return only the beginning.

**Data flow**: It receives token, channel, root timestamp, and latest timestamp → pages Slack replies up to a limit → returns message objects, or nothing if the read failed or exceeded the page limit.

**Call relations**: Ambient history and unseen-context gathering use this.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 2307–2327)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Converts one fetched Slack message into an ambient-decision entry, if it is useful and came before the inbound message.

**Data flow**: It receives a raw message, inbound message, and identity → validates user, timestamp, text, bot ownership, and order → returns timestamp plus AmbientMessage or nothing.

**Call relations**: Ambient history applies this to every fetched thread item.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 2330–2350)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds extra background context to include with an admitted channel turn. It covers thread or channel messages that are not already in the ufo transcript.

**Data flow**: It receives context, token, inbound message, identity, and marker → returns no context for DMs → for new conversations, fetches founding context → for existing conversations, fetches unseen thread and later channel context in parallel.

**Call relations**: Inbound admission calls this before fencing the member message.

*Call graph*: calls 3 internal fn (_founding_context, _later_channel_context, _unseen_tail); called by 1 (_admit_inbound); 1 external calls (gather).


##### `_founding_context`  (lines 2353–2398)

```
async def _founding_context(bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Gathers Slack messages from before the agent was pulled into a conversation. This helps the agent understand what people were discussing before the mention.

**Data flow**: It receives token, inbound message, identity, and marker → chooses channel history or thread replies based on whether this is a fresh thread → fetches one bounded page → renders it as an ambient digest.

**Call relations**: Ambient-context building calls this for conversation-founding messages.

*Call graph*: calls 3 internal fn (_digest_names, _slack_ok, ambient_digest); called by 1 (_ambient_context); 1 external calls (AsyncClient).


##### `_later_channel_context`  (lines 2401–2452)

```
async def _later_channel_context(bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Gathers recent channel messages posted beside an existing thread since the thread began. This covers people who respond outside the thread.

**Data flow**: It receives token, inbound message, identity, and marker → fetches channel history between the root and current message → keeps the latest bounded messages → renders them as an ambient digest.

**Call relations**: Ambient-context building calls this for later messages in existing conversations.

*Call graph*: calls 4 internal fn (_digest_names, _latest_between, _slack_ok, ambient_digest); called by 1 (_ambient_context); 1 external calls (AsyncClient).


##### `_latest_between`  (lines 2455–2475)

```
def _latest_between(messages: Sequence[object], after_ts: str, before_ts: str) -> list[object]
```

**Purpose**: Filters Slack messages to those between two timestamps and keeps the latest few in chronological order.

**Data flow**: It receives message objects and lower/upper timestamps → parses message timestamps → filters and sorts them → returns the bounded latest list.

**Call relations**: Later-channel context uses this after Slack returns history.

*Call graph*: called by 1 (_later_channel_context).


##### `_digest_names`  (lines 2478–2487)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves author and mentioned ids for a group of Slack messages so the ambient digest can use names instead of raw ids.

**Data flow**: It receives token and messages → extracts texts and authors → asks SlackNames to resolve them → returns id-to-name mappings.

**Call relations**: All ambient digest producers call this before rendering context.

*Call graph*: called by 3 (_founding_context, _later_channel_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2490–2536)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Finds thread messages that the agent has not yet read because they previously founded no turn. This keeps the agent’s view aligned with the Slack thread people see.

**Data flow**: It receives context, token, inbound message, identity, and marker → fetches the thread tail → walks backward until it finds an admitted message → digests the newer unseen member messages → returns context text.

**Call relations**: Ambient-context building uses this for existing conversations.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2539–2607)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders Slack background messages into a bounded, safe context block for the model. It avoids treating bystander text as direct instructions.

**Data flow**: It receives raw messages, bot id, explanatory note, marker, and name mappings → filters bots, direct bot mentions, empty text, and invalid timestamps → renders one safe line per message → trims overlong digests → returns a tagged context string.

**Call relations**: Founding, later-channel, and unseen-context helpers all use this final renderer.

*Call graph*: called by 3 (_founding_context, _later_channel_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2610–2612)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks that a private file-download URL belongs to Slack before sending the bot token to it.

**Data flow**: It receives a URL → parses the hostname → returns true only for slack.com or Slack subdomains.

**Call relations**: The streaming downloader calls this as a safety check.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2615–2633)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an inbound Slack file into the workspace without loading the whole file into memory. It also refuses non-Slack hosts and files over the workspace limit.

**Data flow**: It receives token and private URL → verifies the host → streams bytes from Slack in chunks → yields chunks until complete or raises if too large.

**Call relations**: File download writes use this as the data source.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2645–2661)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack message attachments into the ufo workspace before the turn runs.

**Data flow**: It receives context, conversation id, token, and file records → chooses unique inbox names → streams each file into workspace storage → records delivered and skipped names → returns a DownloadedFiles summary.

**Call relations**: Inbound admission calls this when a Slack message has files.

*Call graph*: calls 2 internal fn (write_workspace_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2664–2673)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates the note inserted into the admitted message describing which Slack files were saved or skipped.

**Data flow**: It receives a DownloadedFiles summary → builds lines for saved workspace paths and oversized skipped files → returns the note text.

**Call relations**: Inbound admission appends this to the fenced member message.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2685–2690)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored thread mirror row into the current MirroredThread model, including compatibility with older rows that were just strings.

**Data flow**: It receives stored JSON-like data → if it is a string, treats it as the queue key → otherwise validates it as a MirroredThread → returns the model.

**Call relations**: Hook followers and mid-turn comments use this when recovering Slack thread information from the store.


##### `MirroredThread.anchor`  (lines 2692–2696)

```
def anchor(self) -> str | None
```

**Purpose**: Finds the Slack timestamp that status and progress messages should thread under.

**Data flow**: It reads the root timestamp from the queue key, or the stored DM message timestamp → returns that timestamp or nothing.

**Call relations**: Status tracking and progress posting use this to address Slack’s thread APIs.

*Call graph*: called by 1 (_track_status).


##### `_thread_mirror_key`  (lines 2699–2700)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the scoped-store key for the Slack thread mirror of a ufo conversation.

**Data flow**: It receives a conversation id → prefixes it with the Slack thread namespace → returns the store key.

**Call relations**: Thread mirroring, follower hooks, and mid-turn comments use this key.

*Call graph*: called by 3 (_mirror_thread, follow_turn, speak).


##### `_mirror_thread`  (lines 2703–2711)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Records which Slack thread belongs to a ufo conversation. This lets later turn hooks post status and progress without having the original HTTP request.

**Data flow**: It receives a conversation id and mirrored thread → serializes the thread → writes it to scoped storage.

**Call relations**: Inbound admission and answer-submit admission write this before or alongside admitting a turn.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, _handle_answer_submit); 2 external calls (__init__, model_dump).


##### `_dm_anchor_key`  (lines 2714–2720)

```
def _dm_anchor_key(turn_id: UUID, message_ref: UUID | None=None) -> str
```

**Purpose**: Builds the scoped-store key for the Slack DM message that a turn reply should thread under.

**Data flow**: It receives a turn id and optional message reference → creates a base key for the turn or a child key for an absorbed message → returns the key.

**Call relations**: DM anchoring, reply-thread lookup, and cleanup use this.

*Call graph*: called by 3 (_anchor_dm_thread, _drop_turn_reply_records, _reply_thread).


##### `_anchor_dm_thread`  (lines 2723–2730)

```
async def _anchor_dm_thread(admitted: Admitted, message_ts: str) -> None
```

**Purpose**: Stores the Slack timestamp of a DM member message so replies can thread under the right DM message later.

**Data flow**: It receives an admitted turn result and message timestamp → computes the anchor key using the turn and arrival ids → writes the timestamp to scoped storage.

**Call relations**: Inbound and form-answer admission call this for DMs.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (__init__).


##### `_reply_thread`  (lines 2733–2748)

```
async def _reply_thread(queue_key: str, turn_id: UUID, message_ref: UUID | None=None) -> str | None
```

**Purpose**: Finds the Slack parent timestamp where a reply should be posted. Channels use the root in the queue key; DMs use saved anchors.

**Data flow**: It receives queue key, turn id, and optional message ref → returns channel root if present → otherwise reads the matching DM anchor, falling back to the founding anchor → returns a timestamp or nothing.

**Call relations**: Terminal replies, mid-turn replies, and file attachment sharing call this before posting.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 3 (attach, post, speak); 1 external calls (__init__).


##### `FollowerContext.workspace_id`  (lines 2759–2759)

```
def workspace_id(self) -> UUID
```

**Purpose**: Defines that follower code needs to know the current workspace id.

**Data flow**: A concrete context supplies the workspace id → follower code reads it when posting or restamping Slack status.

**Call relations**: SurfaceContext and the hook adapter satisfy this protocol for status and progress followers.


##### `FollowerContext.public_base_url`  (lines 2762–2762)

```
def public_base_url(self) -> str | None
```

**Purpose**: Defines that follower code may need the deploy’s public URL for footer links.

**Data flow**: A concrete context supplies the URL or nothing → footer building uses it to make web and debug links.

**Call relations**: The shared footer helper reads this through the protocol.


##### `FollowerContext.credential`  (lines 2764–2764)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Defines how follower code reads credentials such as the Slack bot token.

**Data flow**: Follower code provides a credential slot → the concrete context returns the secret value or raises if missing.

**Call relations**: Status, progress, and footer helpers use this through either a surface context or hook adapter.


##### `FollowerContext.tail`  (lines 2766–2768)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how follower code streams live turn frames. A frame is a small live event such as tool activity, text streaming, or terminal state.

**Data flow**: Follower code supplies a turn id and optional cursor → the concrete context returns an async stream of cursor-frame pairs.

**Call relations**: Thread status and progress reporters depend on this live feed.


##### `FollowerContext.turn_is_terminal`  (lines 2770–2770)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Defines how a progress reporter can ask whether a turn has already ended durably.

**Data flow**: It receives a turn id → returns whether the core turn state is terminal.

**Call relations**: Thread progress uses this when a timer fires without new live frames.


##### `FollowerContext.is_operator_workspace`  (lines 2772–2772)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Defines how footer code checks whether extra operator-only information may be shown.

**Data flow**: The concrete context answers true or false for the workspace → footer code uses that to decide whether to include accounting/debug details.

**Call relations**: The Slack footer helper reads this through the follower context protocol.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2829–2830)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Identifies the exact Slack thread whose live status this object writes.

**Data flow**: It combines workspace id, channel id, and thread timestamp → returns a tuple used as the thread-status key.

**Call relations**: Status tracking uses this to coordinate one current writer per Slack thread.


##### `ThreadStatus.run`  (lines 2832–2851)

```
async def run(self) -> None
```

**Purpose**: Runs the live Slack thread status lifecycle for one turn. It starts with “Thinking…”, follows live frames, and clears the status at the end.

**Data flow**: It reads the bot token → opens a Slack client → writes the initial status → follows frames and updates text → clears status when the turn ends or on handled failure.

**Call relations**: The status task wrapper calls this after _track_status starts a task.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2853–2893)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one status line to Slack’s assistant thread status API, if this turn is still the chosen writer for the thread.

**Data flow**: It receives client, token, and status text → checks writer ownership → sends Slack JSON with optional loading message → logs success or failure → returns whether Slack accepted it.

**Call relations**: Run, follow, and clear all call this for actual Slack status writes.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2895–2904)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears the Slack status when this turn ends, but only if no sibling turn is still running in the same thread.

**Data flow**: It checks other tracked statuses for the same thread → if none remain, sends an empty status line → otherwise leaves the sibling’s status alone.

**Call relations**: The status lifecycle calls this on normal end or recoverable failure.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2906–2960)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Watches live turn frames and converts them into short Slack status text. It also refreshes the last accepted status so Slack does not time it out.

**Data flow**: It receives client, token, and last shown text → waits for frames, blanking signals, or refresh timeout → maps activity/text/resume/absorbed frames to status lines → writes changed lines within rate limits → stops on terminal or parked frames.

**Call relations**: ThreadStatus.run calls this after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2970–2977)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers after a progress message posts in the same thread, because Slack clears thread status when the app replies there.

**Data flow**: It receives workspace, channel, and thread timestamp → finds matching local status followers → sets their blanked event.

**Call relations**: Thread progress calls this after a progress post lands.

*Call graph*: called by 1 (_say).


##### `_track_status`  (lines 2980–3001)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts one Slack status follower task for a turn in this process. It also marks the newest turn as the current writer for the Slack thread.

**Data flow**: It receives follower context, turn id, and mirrored thread → skips if already tracked → finds the Slack anchor → creates ThreadStatus and task → stores tracking maps.

**Call relations**: The shared follower armer calls this from admission and turn-execution hooks.

*Call graph*: calls 2 internal fn (anchor, _run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 3004–3032)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Wraps a ThreadStatus task so failures are logged and writer ownership is handed back correctly.

**Data flow**: It receives a ThreadStatus → runs it → logs task-level errors → removes tracking → if this turn held writer ownership, gives it to another live status on the same thread or clears the writer key.

**Call relations**: _track_status launches this task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 3044–3048)

```
def __post_init__(self) -> None
```

**Purpose**: Validates that the progress-report schedule makes sense.

**Data flow**: It reads base and cap intervals from the object → raises if the base is nonpositive or the cap is smaller than the base.

**Call relations**: Progress tracker construction relies on this to avoid impossible schedules.


##### `ProgressCadence.intervals`  (lines 3050–3061)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait lengths between progress posts. The waits double with elapsed time until they reach a cap.

**Data flow**: It starts at the base interval → yields each wait → increases elapsed time → yields larger waits capped at the maximum forever.

**Call relations**: checkpoint generation uses this schedule.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 3063–3072)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Finds future elapsed-time checkpoints that have not happened yet for a turn. This lets a restarted reporter resume the real schedule.

**Data flow**: It receives already elapsed seconds → walks the interval schedule → yields checkpoint times greater than the elapsed value.

**Call relations**: Thread progress uses this when starting or resuming a reporter.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.update`  (lines 3085–3087)

```
def update(self, summary: str) -> None
```

**Purpose**: Records the latest completed activity description for progress messages.

**Data flow**: It receives a summary string → clears any streaming text marker → normalizes and truncates the summary → stores it as current activity.

**Call relations**: Thread progress calls this when live frames report tool or subagent activity.


##### `TurnActivity.stream`  (lines 3089–3090)

```
def stream(self, text: str) -> None
```

**Purpose**: Records that response text is currently streaming.

**Data flow**: It receives streamed text → appends it to the streaming marker list.

**Call relations**: Thread progress uses this so progress text can say the response is being prepared.


##### `TurnActivity.current_step`  (lines 3092–3096)

```
def current_step(self) -> str
```

**Purpose**: Chooses the member-facing current step for a progress post.

**Data flow**: It checks whether text is streaming → returns “Preparing the response” if so → otherwise returns the last activity summary.

**Call relations**: The report builder calls this before formatting a progress line.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 3098–3108)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Formats the current activity and elapsed wait into a Slack progress message, or skips if there has been no signal.

**Data flow**: It receives elapsed seconds → asks for the current step → formats minutes or hours/minutes → returns text, or nothing if no step is known.

**Call relations**: ThreadProgress._post uses this at each checkpoint.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 3149–3152)

```
async def run(self) -> None
```

**Purpose**: Runs the long-turn progress reporter for one turn.

**Data flow**: It reads the Slack bot token → opens a Slack client → delegates to the follow loop.

**Call relations**: The progress task wrapper calls this after _track_progress starts a task.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 3154–3157)

```
def _elapsed(self) -> float
```

**Purpose**: Calculates how long the member has been waiting for this turn, using wall-clock time from the turn’s durable start.

**Data flow**: It reads current UTC time and the stored start time → returns elapsed seconds.

**Call relations**: The progress follow loop and resume post use this to stay aligned across restarts.

*Call graph*: called by 2 (_follow, _post_resumed); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 3159–3212)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches live turn frames and posts progress messages at scheduled checkpoints. It also posts a delayed resume notice when a turn is picked up after restart.

**Data flow**: It sets the first checkpoint → tails live frames → updates activity and cost from frames → on timer, checks terminal state, posts resume notice or progress, and advances the checkpoint → stops on terminal or parked frames.

**Call relations**: ThreadProgress.run calls this as the main reporter loop.

*Call graph*: calls 3 internal fn (_elapsed, _post, _post_resumed); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 3214–3241)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one scheduled progress update if there is meaningful activity to report.

**Data flow**: It receives client, token, activity, elapsed time, cost tick, and first-message flag → asks activity for report text → logs a skip or sends the message → returns whether it landed.

**Call relations**: The progress follow loop calls this at each checkpoint.

*Call graph*: calls 2 internal fn (_say, report); called by 1 (_follow); 1 external calls (log).


##### `ThreadProgress._post_resumed`  (lines 3243–3254)

```
async def _post_resumed(self, client: httpx.AsyncClient, bot_token: str, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts the one-time message saying a turn resumed after a service restart.

**Data flow**: It receives client, token, cost, and first-message flag → formats the fixed resume notice with current elapsed time → sends it through the common say helper → returns whether it landed.

**Call relations**: The follow loop calls this after a short grace period for Resumed frames.

*Call graph*: calls 2 internal fn (_elapsed, _say); called by 1 (_follow).


##### `ThreadProgress._say`  (lines 3256–3296)

```
async def _say(self, client: httpx.AsyncClient, bot_token: str, text: str, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Sends a progress message into the Slack thread and restamps live status if needed.

**Data flow**: It receives Slack client, token, text, elapsed seconds, cost, and first flag → builds optional footer → posts the Slack message → logs result → wakes status restamp for the same thread → returns success or failure.

**Call relations**: Both scheduled progress and resume notices use this common send path.

*Call graph*: calls 4 internal fn (_footer, _restamp_thread_status, _slack_ok, slack_reply_body); called by 2 (_post, _post_resumed); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 3298–3315)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the footer for the first progress message, including web/debug links and current cost if available.

**Data flow**: It receives token, channel, and optional cost tick → formats accounting if present → asks the shared Slack footer helper → returns footer text or nothing.

**Call relations**: ThreadProgress._say calls this only for the first delivered progress message.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_say).


##### `_track_progress`  (lines 3321–3348)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, thread: MirroredThread, started_at: datetime) -> None
```

**Purpose**: Starts one long-turn progress reporter for a turn in this process. It is meant to be armed by the execution that owns the turn.

**Data flow**: It receives context, turn id, conversation id, mirrored thread, and start time → skips if already tracked → creates cadence and ThreadProgress → starts and tracks the task.

**Call relations**: The shared follower armer calls this when it knows the turn’s durable start time.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 4 external calls (__init__, __init__, create_task, now).


##### `_run_progress`  (lines 3351–3366)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Wraps a ThreadProgress task so task-level failures are logged and tracking is cleaned up.

**Data flow**: It receives a ThreadProgress → runs it → logs abandoned reporting on exception → removes the task from the progress table.

**Call relations**: _track_progress launches this task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 3380–3394)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts every live Slack follower that a turn should have: immediate status, and progress reporting when the turn execution provides a start time.

**Data flow**: It receives follower context, followed-turn data, and mirrored thread → starts status tracking → starts progress tracking only if started_at is known.

**Call relations**: Admission, answer submits, and the turn hook all converge here so follower behavior is consistent.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, _handle_answer_submit, follow_turn).


##### `_HookFollowerContext.workspace_id`  (lines 3406–3407)

```
def workspace_id(self) -> UUID
```

**Purpose**: Exposes a hook extension context’s workspace id through the follower protocol.

**Data flow**: It reads the wrapped extension context → returns its workspace id.

**Call relations**: Follower tasks armed from hooks use this adapter just like surface-route followers.


##### `_HookFollowerContext.public_base_url`  (lines 3410–3411)

```
def public_base_url(self) -> str | None
```

**Purpose**: Exposes a hook extension context’s public base URL through the follower protocol.

**Data flow**: It reads the wrapped extension context → returns the public URL or nothing.

**Call relations**: Footer creation for hook-armed followers uses this.


##### `_HookFollowerContext.credential`  (lines 3413–3414)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Lets follower code read credentials from a hook extension context.

**Data flow**: It receives a credential slot → asks the extension credential store for that slot → returns the value.

**Call relations**: Status and progress tasks use this adapter to get the Slack bot token.


##### `_HookFollowerContext.tail`  (lines 3416–3419)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Lets follower code tail live turn frames from a hook extension context.

**Data flow**: It receives a turn id and optional cursor → delegates to the extension context’s tail stream.

**Call relations**: Hook-armed status and progress reporters use this for live updates.


##### `_HookFollowerContext.turn_is_terminal`  (lines 3421–3422)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets progress code check terminal turn state from a hook extension context.

**Data flow**: It receives a turn id → asks the extension context whether the turn is terminal → returns the answer.

**Call relations**: Hook-armed progress reporters use this when checkpoint timers fire.


##### `_HookFollowerContext.is_operator_workspace`  (lines 3424–3425)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Lets footer code ask whether the hook’s workspace is an operator workspace.

**Data flow**: It delegates to the wrapped extension context → returns true or false.

**Call relations**: The shared footer helper uses this for hook-armed progress messages.


##### `follow_turn`  (lines 3428–3470)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Hook handler that arms Slack status and progress followers from inside the turn’s own execution. This restores feedback after restarts and ensures the progress reporter belongs to the execution that owns the turn.

**Data flow**: It receives hook context → ignores missing or subagent turns → reads the mirrored Slack thread with a timeout → arms followers through the hook adapter → returns no outcome so it never changes the turn’s answer.

**Call relations**: The manifest hook calls this when a user prompt is submitted into a turn.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `_handle_connect_click`  (lines 3517–3531)

```
async def _handle_connect_click(ctx: SurfaceContext, interaction: ConnectClick, member_id: UUID | None) -> None
```

**Purpose**: Responds to a Slack connect button click with a private authorization link or an explanation that the request is unavailable.

**Data flow**: It receives context, click data, and optional member id → if allowed, asks core for a connect URL → posts an ephemeral Slack message visible only to the clicker.

**Call relations**: The interactive route calls this for connect-button interactions.

*Call graph*: calls 2 internal fn (connect_url, _ephemeral_in_background); called by 1 (interactive).


##### `_handle_answer_submit`  (lines 3534–3588)

```
async def _handle_answer_submit(ctx: SurfaceContext, bot_token: str, interaction: AnswerSubmit, member_id: UUID | None) -> Response | None
```

**Purpose**: Admits a submitted Slack question form as the next ufo turn and, if this submit won the idempotency race, schedules the form rewrite.

**Data flow**: It receives context, token, submit data, and optional member id → finds the conversation → rejects empty answers with a private hint → resolves sender/member → admits the combined answer with a stable key → anchors DM thread and arms followers → schedules rewrite if accepted.

**Call relations**: The interactive route calls this for ask-form submit buttons.

*Call graph*: calls 13 internal fn (admit, admitted_body, conversation_for, find_conversation, _anchor_dm_thread, _arm_followers, _ephemeral_in_background, _mirror_thread, _resolve_member, _rewrite_in_background (+3 more)); called by 1 (interactive); 7 external calls (__init__, __init__, gather, conversation_audience, JSONResponse, fence_member_message, mint_marker).


##### `interactive`  (lines 3591–3633)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack interactive payloads such as question-form submits and connect button clicks. It verifies Slack’s signature and acknowledges quickly.

**Data flow**: It reads and verifies raw form body → loads bot token and identity → parses the interaction → marks URL verification → resolves linked member → dispatches to connect or answer handling → returns an ok response.

**Call relations**: This is the Slack interactivity HTTP route.

*Call graph*: calls 11 internal fn (linked_member, _bot_token, _ctx_signing_secret, _handle_answer_submit, _handle_connect_click, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_interaction (+1 more)); 2 external calls (JSONResponse, Response).


##### `_rewrite_in_background`  (lines 3639–3642)

```
def _rewrite_in_background(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Starts a background task to rewrite a submitted Slack question message. This keeps the Slack interactivity acknowledgement fast.

**Data flow**: It receives token and submit data → creates a rewrite task → tracks it until completion.

**Call relations**: Answer-submit handling calls this after the accepted submit has been admitted.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (_handle_answer_submit); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3645–3649)

```
async def _run_rewrite(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Runs the question-message rewrite and logs failures.

**Data flow**: It receives token and submit data → calls the rewrite helper → logs any exception.

**Call relations**: The background rewrite starter launches this task.

*Call graph*: calls 1 internal fn (_replace_controls_with_answers); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3652–3657)

```
def _ephemeral_in_background(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Starts a background task to post a private Slack message to one user.

**Data flow**: It receives context, channel, user id, thread timestamp, and text → creates an ephemeral-post task → tracks it until completion.

**Call relations**: Connect-click and empty-answer handling use this for private feedback.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 2 (_handle_answer_submit, _handle_connect_click); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3660–3687)

```
async def _post_ephemeral(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Posts a Slack ephemeral message, visible only to one member, usually in the clicked thread.

**Data flow**: It reads the bot token → sends chat.postEphemeral with channel, user, text, and optional thread timestamp → logs failure without raising.

**Call relations**: The ephemeral background starter launches this.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_interaction`  (lines 3690–3752)

```
def _to_interaction(raw: bytes, identity: SlackIdentity) -> AnswerSubmit | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactive request into either an answer submit or a connect click.

**Data flow**: It decodes the form payload JSON → checks type, team, action, user, channel, and message → for connect buttons, returns a ConnectClick → for ask submits, extracts answers and returns an AnswerSubmit → otherwise returns nothing.

**Call relations**: The interactive route calls this after request verification and identity loading.

*Call graph*: calls 4 internal fn (_dict_field, _string_field, _submitted_answers, slack_thread_key); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_submitted_answers`  (lines 3755–3781)

```
def _submitted_answers(blocks: tuple[Mapping[str, object], ...], state: object) -> tuple[SubmittedAnswer, ...]
```

**Purpose**: Reads all answers from the state of a submitted Slack ask form.

**Data flow**: It receives the message blocks and Slack state → walks input blocks belonging to asks → pairs each question label with the held control value → returns submitted answer records.

**Call relations**: Interaction parsing calls this for ask-submit actions.

*Call graph*: calls 1 internal fn (_held_answer); called by 1 (_to_interaction); 1 external calls (__init__).


##### `_held_answer`  (lines 3784–3802)

```
def _held_answer(field: object) -> str
```

**Purpose**: Converts one Slack input control’s state into plain answer text.

**Data flow**: It receives a control state object → handles radio button, checkbox, or text input shapes → returns selected labels, typed text, or an empty string.

**Call relations**: Submitted-answer extraction calls this for each form control.

*Call graph*: calls 1 internal fn (_option_value); called by 1 (_submitted_answers); 1 external calls (get).


##### `_option_value`  (lines 3805–3809)

```
def _option_value(option: object) -> str
```

**Purpose**: Safely reads the stored value from a Slack option object.

**Data flow**: It receives an option-like object → returns its string value if present → otherwise returns an empty string.

**Call relations**: Held-answer parsing uses this for radio buttons and checkboxes.

*Call graph*: called by 1 (_held_answer).


##### `_dict_field`  (lines 3812–3816)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload.

**Data flow**: It receives a mapping and field name → verifies the field is a dictionary → returns it or raises an error.

**Call relations**: Interaction parsing uses it for nested user, channel, and message objects.

*Call graph*: called by 1 (_to_interaction).


##### `_replace_controls_with_answers`  (lines 3819–3852)

```
async def _replace_controls_with_answers(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Rewrites a Slack question message so controls become the submitted answers and the submit row names who submitted them.

**Data flow**: It receives token and submit data → copies all original blocks except ask inputs and submit row → replaces inputs with context lines showing answers → calls Slack message update.

**Call relations**: The rewrite background task calls this after an accepted form submit.

*Call graph*: calls 2 internal fn (_context_line, _rewrite_slack_message); called by 1 (_run_rewrite).


##### `connect_message_key`  (lines 3874–3877)

```
def connect_message_key(member_id: UUID, provider: str) -> str
```

**Purpose**: Builds the store key for remembering where a connect button was posted for a member and provider.

**Data flow**: It receives member id and provider name → formats a scoped key → returns it.

**Call relations**: Connect-message holding uses this so later connection completion can rewrite the right Slack message.

*Call graph*: called by 1 (_hold_connect_message).


##### `_hold_connect_message`  (lines 3880–3910)

```
async def _hold_connect_message(store: ScopedStore, request: ConnectRequest | None, posted: dict[str, object], channel: str, ts: str | None) -> None
```

**Purpose**: Remembers the Slack message that contains a connect button, but only if the posted body actually included the button.

**Data flow**: It receives store, connect request, posted body, channel, and timestamp → checks for a connect action block → writes channel, message timestamp, and thread timestamp to the store.

**Call relations**: Terminal reply posting calls this after Slack accepts a reply with a connect handoff.

*Call graph*: calls 3 internal fn (put, _is_connect_action, connect_message_key); called by 1 (post); 1 external calls (__init__).


##### `_is_connect_action`  (lines 3913–3920)

```
def _is_connect_action(block: Mapping[str, object]) -> bool
```

**Purpose**: Checks whether a Slack block contains the connect button action.

**Data flow**: It receives a block → checks it is an actions block → scans elements for the connect action id → returns true or false.

**Call relations**: Connect-message holding and settling use this to find or remove connect buttons.

*Call graph*: called by 2 (_hold_connect_message, settle_connect_message).


##### `_rewrite_slack_message`  (lines 3923–3940)

```
async def _rewrite_slack_message(bot_token: str, channel: str, ts: str, text: str, blocks: list[dict[str, object]]) -> None
```

**Purpose**: Updates a Slack message posted by the bot with replacement text and blocks.

**Data flow**: It receives token, channel, timestamp, fallback text, and blocks → sends chat.update to Slack → raises if Slack rejects the update.

**Call relations**: Question-answer rewrites and connect-settlement rewrites share this function.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_replace_controls_with_answers, settle_connect_message); 2 external calls (AsyncClient, dumps).


##### `_held_connect_message`  (lines 3943–3979)

```
async def _held_connect_message(bot_token: str, held: ConnectMessage) -> Mapping[str, object] | None
```

**Purpose**: Reads the current Slack message that holds a connect button, so settling a connection does not overwrite newer edits.

**Data flow**: It receives token and saved message location → fetches the exact message from thread replies or channel history → returns the message object or nothing.

**Call relations**: Connect settlement calls this before rewriting the button away.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (settle_connect_message); 1 external calls (AsyncClient).


##### `settle_connect_message`  (lines 3982–4008)

```
async def settle_connect_message(bot_token: str, held: ConnectMessage, provider: str, account: str) -> None
```

**Purpose**: Rewrites a connect-button message after the external account connection succeeds. The button becomes a short line naming the connected account.

**Data flow**: It receives token, saved message, provider, and account → reads the live Slack message → if the connect button is still present, removes it and appends a settled context line → updates the message.

**Call relations**: Connection-completion hooks outside this file call this using the saved ConnectMessage.

*Call graph*: calls 4 internal fn (_context_line, _held_connect_message, _is_connect_action, _rewrite_slack_message).


##### `_context_line`  (lines 4011–4015)

```
def _context_line(text: str) -> dict[str, object]
```

**Purpose**: Creates a small Slack context block with Markdown text.

**Data flow**: It receives text → truncates it to Slack’s context limit → returns a context block dictionary.

**Call relations**: Question rewrites and connect settlement use this for compact status lines.

*Call graph*: called by 2 (_replace_controls_with_answers, settle_connect_message).


##### `_reply_text`  (lines 4018–4028)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the visible text for a terminal turn outcome. It provides clear fallback text for failures, cancellations, and empty successful replies.

**Data flow**: It receives a Writeback → checks terminal status and text → returns failure text, cancellation reason or fallback, successful text, or an empty-reply placeholder.

**Call relations**: Reply text assembly calls this before adding credential hints and oversize-file links.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 4031–4060)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Builds final reply text with extra hints for credential requests and links for artifacts too large to upload to Slack.

**Data flow**: It receives context and writeback → starts with terminal reply text → adds a portal credentials hint if needed → adds Markdown links for oversized artifacts → returns the complete text.

**Call relations**: Terminal reply posting calls this before splitting and sending Slack messages.

*Call graph*: calls 3 internal fn (home_url, _oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 4063–4066)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one oversized shared artifact as a Markdown list item with a temporary download link when available.

**Data flow**: It receives context and artifact → asks for an artifact link → builds a filename link or plain filename with byte size → returns the line.

**Call relations**: Reply text assembly uses this for every over-Slack-limit artifact.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_reply_mention_ids`  (lines 4069–4080)

```
async def _reply_mention_ids(ctx: SurfaceContext, bot_token: str, channel: str, text: str) -> dict[str, str]
```

**Purpose**: Builds the map needed to turn agent-written @names into Slack notifications, but only when the reply contains @ at all.

**Data flow**: It receives context, token, channel, and text → if no @, returns an empty map → otherwise reads identity and resolves safe mention ids → returns the map.

**Call relations**: Reply mention mapping calls this on first delivery attempt.

*Call graph*: calls 1 internal fn (_identity); called by 1 (_reply_mentions_mapped); 1 external calls (__init__).


##### `_channel_info`  (lines 4083–4097)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for a channel, such as name and sharing/privacy flags.

**Data flow**: It receives token and channel id → calls conversations.info → returns the channel object or nothing on failure.

**Call relations**: Audience decisions, mention-name resolution, and footer privacy checks use this.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 4100–4113)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel may cross workspace boundaries. If Slack metadata cannot be read, it assumes external sharing to avoid leaking operator details.

**Data flow**: It receives token and channel → fetches channel info → checks Slack sharing flags → returns true if shared or unreadable, false otherwise.

**Call relations**: Footer building uses this to decide whether to hide accounting and debug links.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 4116–4148)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the small footer shown under Slack replies and first progress posts. It can include web chat, accounting, and debug links, subject to privacy rules.

**Data flow**: It receives follower context, token, channel, conversation id, turn id, and optional accounting → builds web link if possible → hides operator-only details outside operator-safe channels → returns joined footer text or nothing.

**Call relations**: Terminal reply posting and progress footer creation call this.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 4170–4175)

```
def _slack_reply_progress_key(turn_id: UUID, reply_id: UUID | None=None) -> str
```

**Purpose**: Builds the store key for delivery progress of a terminal or mid-turn Slack reply.

**Data flow**: It receives turn id and optional reply id → creates a base key for the terminal reply or child key for a span reply → returns the key.

**Call relations**: Reply posting, mid-turn speaking, and cleanup use this.

*Call graph*: called by 3 (_drop_turn_reply_records, post, speak).


##### `_slack_reply_progress`  (lines 4178–4191)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Reads or creates the checkpoint record for a Slack reply delivery. This record prevents duplicate messages across retries.

**Data flow**: It receives store and key → reads existing progress if present → otherwise creates an empty progress row with compare-and-set → returns the progress model and stored raw value.

**Call relations**: Terminal and mid-turn reply delivery call this before posting any parts.

*Call graph*: calls 2 internal fn (get, put_if); called by 2 (post, speak); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 4194–4203)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Atomically saves an updated Slack reply delivery checkpoint.

**Data flow**: It receives store, key, expected old value, and progress model → serializes the model → writes it only if the stored value is unchanged → returns updated progress and encoded value, or raises on conflict.

**Call relations**: Delivery, mention mapping, terminal posting, and mid-turn posting use this between Slack calls.

*Call graph*: calls 1 internal fn (put_if); called by 4 (_deliver_slack_reply, _reply_mentions_mapped, post, speak); 2 external calls (__init__, model_dump).


##### `_drop_turn_reply_records`  (lines 4206–4216)

```
async def _drop_turn_reply_records(store: ScopedStore, turn_id: UUID) -> None
```

**Purpose**: Deletes temporary delivery and DM-anchor records after a turn’s terminal delivery is settled or suppressed.

**Data flow**: It receives store and turn id → lists keys under reply-progress and DM-anchor prefixes → deletes each key.

**Call relations**: Terminal post cleanup and attachment cleanup call this once core no longer needs retry records.

*Call graph*: calls 4 internal fn (delete, list, _dm_anchor_key, _slack_reply_progress_key); called by 2 (attach, post).


##### `_slack_reply_delivery`  (lines 4219–4234)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Checks whether a Slack message carries the metadata marker for a specific delivery id.

**Data flow**: It receives a message object and delivery id → inspects Slack metadata and timestamp → returns the message timestamp if it matches, otherwise nothing.

**Call relations**: Reply reconciliation uses this while scanning Slack history after an uncertain post.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 4237–4277)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Searches recent Slack messages for a delivery metadata marker after a previous post may have succeeded but the response was lost.

**Data flow**: It receives client, token, channel, optional thread, and delivery id → pages recent history or replies → returns the matching timestamp, nothing if absent, or raises if the search cap is exceeded.

**Call relations**: Terminal and mid-turn reply posting call this when progress shows a pending delivery.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 2 (post, speak); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 4280–4320)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Posts one Slack reply part with checkpointing so retries do not duplicate accepted messages.

**Data flow**: It receives client, token, store, progress key/state, delivery id, and body → returns immediately if already delivered → marks the delivery pending → posts to Slack → handles invalid-block fallback signal → records the accepted timestamp → returns updated progress and Slack payload.

**Call relations**: Terminal and mid-turn reply loops use this for every message part and fallback attempt.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 2 (post, speak); 2 external calls (__init__, model_copy).


##### `_reply_mentions_mapped`  (lines 4323–4353)

```
async def _reply_mentions_mapped(ctx: SurfaceContext, bot_token: str, channel: str, text: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue) -> tuple[_SlackReplyPro
```

**Purpose**: Applies the safe @name-to-Slack-mention mapping to reply text and pins that mapping in the delivery checkpoint.

**Data flow**: It receives context, token, channel, text, store, key, and progress → if a map is already saved, uses it → otherwise resolves and checkpoints the map → returns updated progress and mapped text.

**Call relations**: Terminal and mid-turn reply delivery call this before splitting text into parts.

*Call graph*: calls 2 internal fn (_checkpoint_slack_reply, _reply_mention_ids); called by 2 (post, speak); 2 external calls (model_copy, mention_markup).


##### `post`  (lines 4356–4552)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str | NothingDelivered
```

**Purpose**: Delivers the terminal Slack reply for a completed turn. It handles silence, splitting, footers, question forms, connect buttons, duplicate-safe posting, and fallback formatting.

**Data flow**: It receives context and writeback → suppresses true silence if appropriate → finds reply thread and token → reads delivery progress → maps mentions → builds text/actions/footer → reconciles pending posts → posts each part with checkpoints and fallbacks → marks complete → returns the first Slack message ref or NOTHING_DELIVERED.

**Call relations**: Core’s surface delivery poller calls this when a turn reaches terminal writeback.

*Call graph*: calls 17 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _drop_turn_reply_records, _hold_connect_message, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _reply_with_oversize_links (+7 more)); 7 external calls (__init__, __init__, __init__, AsyncClient, loads, log, is_silence_sentinel).


##### `speak`  (lines 4555–4652)

```
async def speak(ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Posts a mid-turn Slack message before the final turn reply. It is duplicate-safe and threaded under the message it answers.

**Data flow**: It receives context and mid-turn reply → finds token and correct thread anchor → reads delivery progress → maps mentions → splits text → reconciles pending delivery → posts parts with checkpoints → marks complete → returns the first message ref.

**Call relations**: Core calls this when a turn emits an intermediate reply or comment.

*Call graph*: calls 12 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _slack_reply_progress, _slack_reply_progress_key, _thread_mirror_key (+2 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 4655–4695)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request and returns Slack’s parsed response while preserving recoverable Slack errors for callers.

**Data flow**: It receives HTTP client, bot token, and encoded body → posts to Slack → on HTTP error, extracts error and retry-after if possible and raises a delivery error → otherwise returns JSON.

**Call relations**: The duplicate-safe delivery helper uses this for actual message posts.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 4698–4704)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the timestamp of a successfully posted Slack message.

**Data flow**: It receives Slack’s payload → verifies ok is true and ts is a non-empty string → returns ts or raises a Slack API error.

**Call relations**: Delivery helpers and reply loops use this after Slack accepts a message.

*Call graph*: called by 3 (_deliver_slack_reply, post, speak); 1 external calls (__init__).


##### `attach`  (lines 4707–4746)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads and shares the turn’s inline-size artifacts into the same Slack thread after the terminal reply is recorded. Oversized files are linked in the reply text instead.

**Data flow**: It receives context, writeback, and reply ref → finds thread and drops temporary reply records → filters artifacts under Slack’s upload cap → uploads them concurrently → shares successful uploads in batches → logs failed uploads or shares.

**Call relations**: Core calls this after terminal reply posting when artifacts need to be attached.

*Call graph*: calls 6 internal fn (credential, _attachment_batches, _drop_turn_reply_records, _reply_thread, _share_uploaded_files, _upload_artifact); 4 external calls (__init__, gather, AsyncClient, Timeout).


##### `_attachment_batches`  (lines 4749–4753)

```
def _attachment_batches(files: Sequence[dict[str, str]]) -> Iterator[Sequence[dict[str, str]]]
```

**Purpose**: Splits uploaded Slack file descriptors into batches small enough for Slack’s complete-upload call.

**Data flow**: It receives a sequence of file dictionaries → yields slices no larger than Slack’s attachment limit.

**Call relations**: Attachment sharing uses this after uploads finish.

*Call graph*: called by 1 (attach).


##### `_upload_artifact`  (lines 4756–4783)

```
async def _upload_artifact(ctx: SurfaceContext, client: httpx.AsyncClient, bot_token: str, artifact: SharedArtifact) -> str
```

**Purpose**: Performs the reservation and byte upload steps of Slack’s external file-upload flow.

**Data flow**: It receives context, HTTP client, token, and artifact → reserves an upload URL with filename and exact length → streams blob bytes to that URL → returns Slack’s file id.

**Call relations**: Attachment delivery runs this concurrently for each uploadable artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (__init__, post).


##### `_share_uploaded_files`  (lines 4786–4810)

```
async def _share_uploaded_files(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, files: Sequence[dict[str, str]]) -> None
```

**Purpose**: Completes Slack external uploads by sharing already uploaded file ids into a channel or thread.

**Data flow**: It receives client, token, channel, optional thread timestamp, and files → posts files.completeUploadExternal JSON to Slack → raises if Slack rejects it.

**Call relations**: Attachment delivery calls this once per batch of uploaded files.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (post, dumps).


##### `_slack_ok`  (lines 4813–4822)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Common helper for Slack Web API calls that should return ok:true. It turns Slack-level errors into a consistent exception.

**Data flow**: It receives an awaitable HTTP response → waits for it → checks HTTP status → parses JSON → if ok is true, returns the payload; otherwise raises SlackApiError with Slack’s error message.

**Call relations**: Most Slack API reads and writes in this file use this helper.

*Call graph*: called by 19 (_list, _members, _say, _set, _channel_info, _conversation_members, _declared_files, _founding_context, _held_connect_message, _later_channel_context (+9 more)); 1 external calls (__init__).


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The `ufo` shell client is deliberately simple: it sends HTTP requests and reads back lines such as `say`, `txt`, `ask`, `run`, or `file`. This file decides what those lines should be. It is the bridge between a user typing in a terminal and the deeper conversation engine that runs the agent.

A normal request proves who the user is with a bearer token, finds or creates the right conversation for that user and channel, and either admits a new message or resumes watching the latest turn. The response is often a held-open stream: the server keeps the HTTP connection alive for a short time and sends live updates as the agent thinks, uses tools, asks for secrets, shares files, or finishes. If the answer takes too long, the stream tells the client to reconnect and continue from a saved cursor, like putting a bookmark in a book before closing it.

The file also supports special terminal behavior: stopping a turn, sending an extra message while a previous one is still running, replying to an operation the agent wants executed on the user’s machine, uploading and downloading workspace files, and storing environment documents. Without this file, the terminal client would have no safe protocol for joining conversations, resuming interrupted output, or executing local terminal tasks.

#### Function details

##### `terminal_runtime_id`  (lines 118–120)

```
def terminal_runtime_id(channel: str) -> str
```

**Purpose**: Creates a stable short identifier for the local terminal runtime tied to one channel. This lets the system recognize the same terminal conversation across reconnects without exposing the channel text directly.

**Data flow**: It takes a channel name, hashes it, and keeps the first fixed-length part of the hash. The output is a predictable hexadecimal string for that channel.

**Call relations**: When a stream is bound to a terminal connection, `_ChannelStream._bound` uses this helper to name the local runtime before announcing the terminal to the core context.

*Call graph*: called by 1 (_bound); 1 external calls (sha256).


##### `directive`  (lines 123–131)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one protocol line for the shell client. A directive is a plain text command, with fields separated by tabs, that tells the client what to print or do.

**Data flow**: It receives a verb such as `say` or `run` plus text fields. It escapes tabs, newlines, and backslashes so the client can split the line safely, then returns newline-ending bytes ready to stream.

**Call relations**: Most of the file depends on this small formatter whenever it needs to speak to the shell. Higher-level functions decide the meaning; this function makes the wire format safe and consistent.

*Call graph*: called by 13 (_answer, _channel_message, _channel_op_reply, _channel_stop, _client_update, _fulfill_secret, _say_lines, _send, _stream_end_directives, _subagent_note (+3 more)).


##### `shared_files`  (lines 145–156)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files an agent turn shared and prepares them for terminal display. It adds download links when the deployment is configured to provide them.

**Data flow**: It receives the surface context and a turn id. It asks the context for that turn’s shared artifacts, turns each artifact into a filename, size, and URL record, and returns them as an immutable group.

**Call relations**: Streaming code calls this after a turn reaches a terminal state, so the client sees the complete set of shared files rather than a partial list.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 159–166)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies which workspace an incoming request claims to belong to before the request handler runs. If the request has no usable bearer token, it refuses to identify a workspace.

**Data flow**: It reads the Authorization header, checks that it is a bearer token, and extracts the workspace claim from the token. It returns the workspace id or `None`.

**Call relations**: This is the surface-level identification hook used by the wider server routing layer. The route handler later verifies the same token again to learn the member email.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 172–231)

```
def history_directives(conversation: Conversation) -> tuple[bytes, ...]
```

**Purpose**: Turns an existing conversation transcript into terminal lines for a client that is freshly resuming. It avoids repeating the newest agent reply because the live tail will replay that latest turn.

**Data flow**: It reads the conversation messages, separates user text from assistant text and tool-work steps, keeps the newest content within a character budget, and returns `you`, `say`, and `note` directive lines.

**Call relations**: `_ChannelStream.response` uses this when an empty reconnect has no explicit cursor and needs enough past context to redraw the terminal sensibly before live streaming continues.

*Call graph*: calls 3 internal fn (_dispatched, _history_text, directive); called by 1 (response).


##### `_dispatched`  (lines 234–241)

```
def _dispatched(message: Message, active: set[str]) -> int
```

**Purpose**: Counts how many tool calls in an assistant message actually became live work. This matters because only dispatched work should appear as completed steps in reconstructed history.

**Data flow**: It receives one message and a set of active tool result ids. It scans tool-use blocks and counts only those whose ids appear in the active set.

**Call relations**: `history_directives` calls this while rebuilding a readable transcript so old tool activity is summarized in the same style as the live terminal originally showed it.

*Call graph*: called by 1 (history_directives).


##### `_history_text`  (lines 244–249)

```
def _history_text(message: Message) -> str
```

**Purpose**: Extracts readable text from a stored message. It hides or normalizes user-message wrapper details so history looks like what the member actually typed.

**Data flow**: It receives a message. If the content is plain text, it uses it directly; otherwise it joins the text blocks. For user messages, it applies member-message cleanup before returning the string.

**Call relations**: `history_directives` uses this as its text extractor before deciding whether each message becomes a `you`, `say`, or summarized `note` line.

*Call graph*: called by 1 (history_directives); 1 external calls (member_message_text).


##### `directives_for`  (lines 252–306)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True, runtime: RuntimeIde
```

**Purpose**: Converts one live engine frame into one or more shell directives. It is the main translator between internal conversation events and the terminal protocol.

**Data flow**: It receives a live frame plus context such as whether text has already streamed, pending credential prompts, shared files, and runtime identity. It pattern-matches the frame type and returns the appropriate directive bytes.

**Call relations**: `_render_stream_frame` calls this for each frame pulled from the live tail. It delegates terminal endings to `_answer` and subagent activity to `_subagent_note`.

*Call graph*: calls 3 internal fn (_answer, _subagent_note, directive); called by 1 (_render_stream_frame); 1 external calls (__init__).


##### `_subagent_note`  (lines 309–315)

```
def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]
```

**Purpose**: Formats progress from a subagent as a terminal note. It suppresses empty start/end events so the terminal only shows useful activity.

**Data flow**: It receives a subagent activity frame, chooses a label from the subagent name or profile, and returns a `note` directive if there is activity text. Otherwise it returns no lines.

**Call relations**: `directives_for` calls this when the live frame represents a subagent. The result becomes part of the same stream as ordinary agent progress.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for).


##### `_answer`  (lines 318–388)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True, runtime: RuntimeIdentity
```

**Purpose**: Builds the final terminal output for a turn when it finishes, fails, is cancelled, or parks waiting for later work. It decides whether to show an answer, prompt again, ask for secrets, show files, or exit.

**Data flow**: It receives a terminal frame and extra context such as streamed status, credential prompts, connection links, shared files, and runtime details. It returns the directive lines that close out the turn from the client’s point of view.

**Call relations**: `directives_for` hands terminal frames here. This function is where final statuses become user-facing behavior like `ask`, `secret`, `file`, or `exit`.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for); 1 external calls (__init__).


##### `_say_lines`  (lines 391–392)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Splits a block of text into separate `say` directives. This keeps multi-line messages printable in the terminal protocol.

**Data flow**: It receives text, splits it into lines, and wraps each line with the `say` directive formatter. It returns the resulting directive bytes.

**Call relations**: `_answer` uses this whenever a final answer or safe error message needs to be spoken to the user line by line.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `_render_stream_frame`  (lines 403–450)

```
async def _render_stream_frame(frame: LiveFrame, streamed: bool, pending: Callable[[str, str], Awaitable[bool]] | None, connect: Callable[[], Awaitable[str]] | None, files: Callable[[], Awaitable[tupl
```

**Purpose**: Prepares one live frame for streaming by gathering any extra information needed to display it correctly. This includes pending secrets, connection links, shared files, and prompt state.

**Data flow**: It receives a frame, current stream state, and optional callbacks. If the frame is terminal, it may ask which credential prompts are still pending, create a connection URL, and load shared files. It returns rendered lines plus flags saying whether text streamed, the turn ended, and the user is left at a prompt.

**Call relations**: `stream_directives` calls this for every frame it pulls from the tail. It then uses the returned flags to decide whether to stop the stream or keep waiting.

*Call graph*: calls 1 internal fn (directives_for); called by 1 (stream_directives); 1 external calls (__init__).


##### `_stream_end_directives`  (lines 453–476)

```
async def _stream_end_directives(turn_id: UUID, rendered_cursor: str, terminated: bool, ran: bool, prompting: bool, moved_on: Callable[[], Awaitable[bool]] | None) -> tuple[bytes, ...]
```

**Purpose**: Decides what instruction to send when a streaming response ends. It tells the client whether to poll soon, listen while idle, or do nothing.

**Data flow**: It receives the turn id, the last rendered cursor, and flags describing how the stream ended. It returns `since` plus `poll` or `listen` directives when the client should reconnect from a known place.

**Call relations**: `stream_directives` calls this after it stops reading frames. This is the small decision point that makes reconnects reliable instead of losing or repeating output.

*Call graph*: calls 1 internal fn (directive); called by 1 (stream_directives).


##### `_cancel_stream_tasks`  (lines 479–486)

```
async def _cancel_stream_tasks(*tasks: asyncio.Task[Any] | None) -> None
```

**Purpose**: Cleans up background waiting tasks used during streaming. It prevents unfinished frame or operation waits from leaking after the response is done.

**Data flow**: It receives optional async tasks, cancels each one, and awaits them while ignoring expected cancellation-style errors. It does not return data.

**Call relations**: `stream_directives` calls this in its cleanup path no matter why the stream ended: timeout, terminal frame, operation request, or disconnect from the loop.

*Call graph*: called by 1 (stream_directives); 1 external calls (suppress).


##### `stream_directives`  (lines 489–613)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Runs the live streaming loop for a turn. It watches for agent frames and local terminal operations, converts them to directives, and stops at the right boundary so the shell can reconnect safely.

**Data flow**: It receives a live-frame tail, a hold time, optional callbacks for prompts/files/operations, and cursor information. It yields directive bytes as frames arrive, operation requests appear, or the hold expires, then yields reconnect instructions when needed.

**Call relations**: `_ChannelStream.response` uses this as the body of the streaming HTTP response. Inside, it relies on `_next`, `_render_stream_frame`, `_stream_end_directives`, and `_cancel_stream_tasks` to keep the stream ordered and recoverable.

*Call graph*: calls 5 internal fn (_cancel_stream_tasks, _next, _render_stream_frame, _stream_end_directives, directive); called by 1 (response); 3 external calls (ensure_future, get_running_loop, wait).


##### `_next`  (lines 616–622)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next item from an async stream without letting end-of-stream escape as an exception. This makes the main streaming wait loop simpler.

**Data flow**: It receives an async iterator of cursor/frame pairs. It returns the next pair, or `None` if the iterator has ended.

**Call relations**: `stream_directives` wraps frame reads with this helper before racing them against terminal operation requests.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 625–629)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Verifies the request bearer token and extracts the email it proves. It is the first identity check for member-facing routes.

**Data flow**: It reads the Authorization header, rejects missing or non-bearer values, and verifies the token against the workspace id. It returns an email string or `None`.

**Call relations**: `_authenticated_member` calls this before linking the email to a member row and checking access.

*Call graph*: called by 1 (_authenticated_member); 1 external calls (verify_token).


##### `_authenticated_member`  (lines 632–645)

```
async def _authenticated_member(ctx: SurfaceContext, request: Request) -> tuple[str, UUID | None] | None
```

**Purpose**: Authenticates a request and resolves it to a member when possible. It allows an email with no member record to continue as an unlinked conversation, but blocks revoked or unauthorized members.

**Data flow**: It takes the surface context and request, verifies the email, looks up or creates a member link, checks access when a member id exists, and returns `(email, member_id)` or `None`.

**Call relations**: Every public route in this file calls this before reading or changing conversation, file, skill, environment, or terminal-operation data.

*Call graph*: calls 4 internal fn (link_member, linked_member, member_has_access, _authenticated_email); called by 8 (channel, op_body, store_environment, store_environment_file, system_skills, workspace_file, workspace_listing, workspace_upload).


##### `_utf8_header`  (lines 648–655)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Recovers a UTF-8 string from an HTTP header whose bytes may have been decoded by the server using a different header encoding rule. This is important for paths containing non-ASCII characters.

**Data flow**: It reads a named header, re-encodes it as latin-1 to recover the original bytes, decodes those bytes as UTF-8, and returns the best-effort string.

**Call relations**: `channel` uses it for the current working directory header, and `_channel_op_reply` uses it for operation error text.

*Call graph*: called by 2 (_channel_op_reply, channel).


##### `_stale_client`  (lines 658–662)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Checks whether the shell script version making the request is older than the version the server expects. This lets the server nudge users to update when the wire protocol changes.

**Data flow**: It reads the deployed client version from the environment and compares it with the request’s script-version header. It returns true only when a served version exists and the client differs.

**Call relations**: `channel` checks this once and passes the result into message and stop handling, where stale clients may receive install/update instructions instead of normal streaming.

*Call graph*: called by 1 (channel).


##### `_client_update`  (lines 665–666)

```
def _client_update() -> bytes
```

**Purpose**: Builds the short directive response that tells a stale shell client to install the updated client and explains why.

**Data flow**: It creates an `install` directive followed by a `say` directive with the update message. The output is ready to send as plain text.

**Call relations**: `_channel_message` and `_channel_stop` use this when a reconnect or stop request should teach the client to update before continuing.

*Call graph*: calls 1 internal fn (directive); called by 2 (_channel_message, _channel_stop).


##### `_resumed_from`  (lines 669–676)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Validates a client resume cursor for the current turn. It prevents a cursor from an older turn from accidentally skipping frames in a newer turn.

**Data flow**: It reads the `since` header, separates the named turn from the cursor, and returns the cursor only if the named turn matches the current turn id. Otherwise it returns an empty cursor.

**Call relations**: `_ChannelStream.response` calls this before opening the live tail, ensuring the stream starts at the right point.

*Call graph*: called by 1 (response).


##### `_turn_context`  (lines 679–691)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the context attached to a newly admitted message. It records who sent it, where it came from, and the user’s timezone when valid.

**Data flow**: It receives the email and request, reads the timezone header, and creates a turn context. If the timezone is invalid, it logs that fact and falls back to a context without a timezone.

**Call relations**: `_channel_message` and `_send` use this when they admit user text into the conversation engine.

*Call graph*: called by 2 (_channel_message, _send); 2 external calls (__init__, log).


##### `_runtime_config`  (lines 694–712)

```
def _runtime_config(ctx: SurfaceContext, request: Request) -> TurnRuntimeConfig | None
```

**Purpose**: Reads optional per-turn runtime choices from headers, such as model, internet restriction, or environment. It validates them before they reach the engine.

**Data flow**: It reads runtime-related headers, rejects unsupported internet values, builds a runtime config when at least one option is present, asks the context to validate it, and returns the config or `None`.

**Call relations**: Both normal message admission and fast send admission call this so each new turn can carry the same optional runtime controls.

*Call graph*: calls 1 internal fn (validate_runtime_config); called by 2 (_channel_message, _send); 1 external calls (__init__).


##### `_channel_op_reply`  (lines 724–745)

```
async def _channel_op_reply(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, op_id: str, stale: bool) -> Response | _ChannelTurn
```

**Purpose**: Processes the client’s reply to a terminal operation the agent asked it to run. The reply is not a chat message; it completes a pending operation.

**Data flow**: It reads the request body as the operation result, checks its size, reads any error header, and resolves the operation in the context. It then either returns a direct response or a channel turn to resume streaming.

**Call relations**: `channel` calls this when the request carries the operation header. After resolving the operation, the surrounding route may open a stream so the user sees what the turn does next.

*Call graph*: calls 4 internal fn (latest_turn, terminal_resolve, _utf8_header, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_channel_stop`  (lines 748–759)

```
async def _channel_stop(ctx: SurfaceContext, request: Request, conversation_id: UUID, stale: bool) -> Response | _ChannelTurn
```

**Purpose**: Handles the user pressing stop or escape for the current conversation. It admits no message; it asks the engine to stop the latest turn if one exists.

**Data flow**: It rejects any request body, finds the latest turn, stops it when present, and returns either a prompt/update response or a channel turn for streaming the cancellation result.

**Call relations**: `channel` calls this for stop requests. If there is a turn to watch, `_ChannelStream` then streams the committed cancellation frames back to the terminal.

*Call graph*: calls 4 internal fn (latest_turn, stop_turn, _client_update, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_channel_message`  (lines 762–812)

```
async def _channel_message(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str, stale: bool, marked: bool) -> Response | _ChannelTurn
```

**Purpose**: Handles the ordinary channel POST: either a new user message or an empty reconnect. It decides whether to admit text, resume a turn, prompt, listen, or ask the client to update.

**Data flow**: It reads and trims the body. Empty bodies look up the latest turn and resume or idle-listen as appropriate. Non-empty bodies are size-checked, runtime-checked, may claim the terminal workspace, and are admitted as a new conversation arrival.

**Call relations**: `channel` calls this when the request is not a secret, send, unsend, operation reply, or stop. It returns either an immediate HTTP response or a `_ChannelTurn` that `_ChannelStream` will stream.

*Call graph*: calls 8 internal fn (admit, claim_terminal, latest_turn, turn_is_terminal, _client_update, _runtime_config, _turn_context, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_ChannelStream.response`  (lines 825–861)

```
async def response(self) -> Response
```

**Purpose**: Builds the streaming HTTP response for a chosen conversation turn. It gathers history when needed and wires the live tail into the directive stream.

**Data flow**: It uses the request cursor to decide where to resume, optionally reads transcript history, prepares callbacks for connection URLs, shared files, credential prompts, and terminal operations, and returns a plain-text streaming response.

**Call relations**: The `channel` route creates a `_ChannelStream` after a request resolves to a turn. This method connects that route-level decision to `stream_directives` and `_bound`.

*Call graph*: calls 4 internal fn (_bound, _resumed_from, history_directives, stream_directives); 2 external calls (partial, StreamingResponse).


##### `_ChannelStream._moved_on`  (lines 863–869)

```
async def _moved_on(self) -> bool
```

**Purpose**: Checks whether the conversation has advanced to a newer non-terminal turn. This tells the stream whether to immediately poll into the next turn after the current one ends.

**Data flow**: It asks for the latest turn, compares it with the stream’s turn id, and checks whether the newer turn is still running. It returns a boolean.

**Call relations**: _ChannelStream.response passes this as the `moved_on` callback to `stream_directives`, which uses it when choosing end-of-stream reconnect directives.


##### `_ChannelStream._bound`  (lines 871–888)

```
async def _bound(self, history: tuple[bytes, ...], directives: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Wraps the outgoing directive stream with terminal connection setup and teardown. It also sends the initial acknowledgement, history, and workspace note before live lines.

**Data flow**: It may announce the terminal as connected using the current directory and runtime id, then yields sent acknowledgement, history lines, workspace note, and every live directive. On exit, it disconnects the terminal if it connected one.

**Call relations**: _ChannelStream.response uses this as the actual byte iterator for the streaming response. It calls `terminal_runtime_id` when binding a terminal-capable stream.

*Call graph*: calls 1 internal fn (terminal_runtime_id); called by 1 (response).


##### `channel`  (lines 891–948)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles the main `ufo` channel endpoint. This is where chat messages, reconnects, sends, stops, secret fulfillment, unsends, and terminal operation replies are sorted into the right path.

**Data flow**: It authenticates the request, handles secret fulfillment early, validates the current directory, finds or creates the conversation, checks client freshness, and dispatches based on request headers. It returns either an immediate plain response or a streaming response for a turn.

**Call relations**: This is the central route for terminal conversation traffic. It calls the smaller `_channel_*`, `_send`, `_unsend`, and `_fulfill_secret` helpers, then hands turn results to `_ChannelStream.response`.

*Call graph*: calls 10 internal fn (conversation_for, _authenticated_member, _channel_message, _channel_op_reply, _channel_stop, _fulfill_secret, _send, _stale_client, _unsend, _utf8_header); 3 external calls (__init__, conversation_audience, PlainTextResponse).


##### `_send`  (lines 951–1011)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str) -> Response
```

**Purpose**: Admits a message without holding a stream open. This is used when the user sends a second message while another request is already streaming the running turn.

**Data flow**: It validates a send id, reads and size-checks the body, validates runtime options, may claim the workspace, and admits the message with an idempotency key so retries do not duplicate it. It returns a `sent` acknowledgement and possibly a workspace note.

**Call relations**: `channel` calls this when the send header is present. The consequences of the admitted message are expected to appear on another already-held stream.

*Call graph*: calls 5 internal fn (admit, claim_terminal, _runtime_config, _turn_context, directive); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 1014–1036)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Retracts a queued message that has not yet been taken up by a turn. It is the server side of taking back words that are still waiting.

**Data flow**: It rejects bodies, requires a real member id, parses the arrival id, and asks the context to retract that arrival for that member. It returns empty success or a conflict message if the arrival is already gone or used.

**Call relations**: `channel` calls this when the unsend header is present. Unlike message admission, it never creates or resumes a turn.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 1039–1060)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores a credential value the user entered privately in response to a prompt. The secret does not become part of the chat transcript.

**Data flow**: It reads the credential slot header and body value, rejects empty or oversized values, and asks the context to fulfill the sealed credential request. It returns a `say` directive explaining whether the value was stored.

**Call relations**: `channel` calls this before normal conversation handling when the secret header is present. Terminal output from `_answer` may have prompted the client to make this request.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 1063–1075)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets the shell download the body for a pending terminal operation. This is used when an operation needs bytes staged locally, such as writing a file.

**Data flow**: It authenticates the user, builds the member-scoped conversation key, asks the context for the operation body by id, and returns raw bytes or a 404.

**Call relations**: This route complements the `run` directive emitted by `stream_directives`. The client receives an operation id, then may call this endpoint to fetch the operation payload.

*Call graph*: calls 2 internal fn (terminal_op_body, _authenticated_member); 2 external calls (PlainTextResponse, Response).


##### `system_skills`  (lines 1078–1090)

```
async def system_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the system skills bundle to authenticated clients. It supports cache validation so unchanged clients do not redownload the zip archive.

**Data flow**: It authenticates the request, reads the bundle digest and archive from the context, compares the request’s `if-none-match` header, and returns either 304 or the zip bytes with an ETag.

**Call relations**: This is a supporting GET route used by the terminal client to obtain skill assets. It shares the same authentication helper as conversation routes.

*Call graph*: calls 1 internal fn (_authenticated_member); 2 external calls (PlainTextResponse, Response).


##### `store_environment`  (lines 1093–1103)

```
async def store_environment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores an environment document and returns its digest. Later turns can refer to that digest to pin a specific environment.

**Data flow**: It authenticates the request, reads the whole body, asks the context to store it, and returns the digest as plain text. Validation errors become client errors.

**Call relations**: The environment upload route calls this directly. `_runtime_config` can later accept an environment header that points at a stored digest.

*Call graph*: calls 2 internal fn (store_environment_document, _authenticated_member); 2 external calls (PlainTextResponse, body).


##### `store_environment_file`  (lines 1106–1115)

```
async def store_environment_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores a file referenced by an environment document and returns its digest. The same file bytes always produce the same address-like value.

**Data flow**: It authenticates the request, reads the body bytes, asks the context to store the environment file, and returns the digest or a validation error.

**Call relations**: This route supports environment documents that include files. It uses the same authentication pattern as `store_environment`.

*Call graph*: calls 2 internal fn (store_environment_file, _authenticated_member); 2 external calls (PlainTextResponse, body).


##### `workspace_file`  (lines 1118–1138)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams a file out of the channel’s workspace for download. It is the read side of terminal workspace file copy.

**Data flow**: It authenticates the user, finds the conversation for that user and channel without creating one, asks the context for the requested path, and streams bytes if found. Missing conversations, sandboxes, or paths all become 404-style responses.

**Call relations**: This GET route supports `ufo cp` downloads. It is separate from chat streaming but scoped through the same member-and-channel conversation key.

*Call graph*: calls 3 internal fn (find_conversation, read_workspace_file, _authenticated_member); 2 external calls (PlainTextResponse, StreamingResponse).


##### `workspace_upload`  (lines 1141–1159)

```
async def workspace_upload(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Uploads one file into the channel’s workspace. This lets a user stage files before the first agent turn starts.

**Data flow**: It authenticates the user, validates the path, creates or finds the conversation, and streams the request body into the workspace writer. If the write is refused, it returns the refusal as an error; otherwise it returns no content.

**Call relations**: This PUT route is the upload half of workspace copy. It uses `conversation_for` because uploading is allowed to create the conversation before any message is sent.

*Call graph*: calls 3 internal fn (conversation_for, write_workspace_file, _authenticated_member); 4 external calls (conversation_audience, PlainTextResponse, stream, Response).


##### `workspace_listing`  (lines 1162–1187)

```
async def workspace_listing(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files in the channel workspace so the client can compare local and remote folders. A channel with no conversation simply lists nothing.

**Data flow**: It authenticates the user, finds the conversation if it exists, asks for workspace file entries, and returns JSON containing path, size, and modified time for each file.

**Call relations**: This GET route supports synchronization logic such as `ufo cp`. It reads workspace state but does not create conversations or admit messages.

*Call graph*: calls 3 internal fn (find_conversation, list_workspace_files, _authenticated_member); 3 external calls (dumps, PlainTextResponse, Response).


### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `Slack message sending and Slack event handling`

When this system sends a Slack message through a connector, that message is not rendered by the normal Slack surface code. So this file adds a small attribution footer itself, like a note at the bottom saying who it came from. Instead of using a plain product name, the footer mentions the workspace’s bot user, so a reader can click or contact the agent from the message.

The tricky part is that Slack treats bot mentions specially. If the system adds a footer containing “<@bot_id>”, Slack may later report that message as mentioning the bot. Without extra care, the agent could mistake its own attribution footer for a human asking it something. This file prevents that by stripping known attribution text before checking whether a message really addresses the bot.

It also knows how to inspect the places Slack can hide message text. Slack messages may have a simple text field, but they may also contain nested “blocks” and rich text pieces. The file gathers all those strings so mention detection can see the same message a person sees. In short, this file is the small bridge between outbound Slack attribution and inbound Slack address detection, making sure the system signs its messages without confusing itself.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: This function decides whether a connector call is the kind of call that publishes a Slack message. It uses the same test as the connector attribution code, so both sides agree about which outgoing calls should receive an attribution footer.

**Data flow**: It receives a connector provider name and a connector action slug. It lowercases the slug, then checks that the provider is Slack, that the slug refers to a message, and that it includes one of the known sending verbs. It returns true when all of those clues say “this sends a Slack message,” and false otherwise.

**Call relations**: This is the gatekeeper for Slack attribution decisions. Code that is about to prepare or inspect connector arguments can call it first, so only real Slack message sends move on to footer-related work.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: This function adds the project’s attribution footer to Slack send arguments, using the bot user mention as the footer’s subject. If the message already has such an attribution, it leaves the arguments alone so footers do not pile up.

**Data flow**: It receives the outgoing connector arguments and the Slack bot user ID. It builds the footer subject by placing that bot ID into the shared attribution mention template. Then it passes the arguments and subject to the connector attribution helper, which returns arguments shaped with the footer added when needed. The result is a new or unchanged argument dictionary ready to send.

**Call relations**: This is used after something has been identified as a Slack send. It hands off the detailed footer-building work to the shared connector helper, which keeps the Slack-specific version aligned with the generic attribution format.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: This function checks whether a piece of Slack text really mentions the bot, ignoring mentions that appear only inside the system’s own attribution footer. It protects the agent from treating its own signed messages as user requests.

**Data flow**: It receives some Slack text and the bot user ID. First it removes known attribution footer text from the message. Then it looks for Slack’s bot mention syntax, such as “<@BOTID>”, in what remains. It returns true if the bot is still mentioned after footer removal, and false if not.

**Call relations**: Inbound Slack event logic can call this when deciding whether a message is addressed to the agent. It relies on the shared attribution-stripping helper, so the rule for ignoring attribution matches the rule used when creating attribution.

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: This function collects every text string in a Slack message event where a bot mention might appear. It looks beyond the main text field because Slack messages can also store visible words inside nested blocks.

**Data flow**: It receives a Slack event-like mapping. It reads the event’s main text field, using an empty string if it is missing, and also reads the blocks field. It then asks the nested-string helper to walk through those blocks and pull out every string inside them. It returns all collected strings as a tuple.

**Call relations**: This supports inbound mention detection. A caller can run each returned body through mention-checking logic such as addressing_mention, making sure footer mentions and real mentions are noticed even when Slack stores them inside structured message blocks.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: This helper walks through nested Slack data and yields every string it finds. It is like checking every drawer inside a cabinet for notes, no matter how deeply they are tucked away.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, it looks through each value inside it and repeats the same process. If it is a list, it does the same for each list item. Other kinds of values produce nothing.

**Call relations**: This is an internal helper used by message_bodies. It does the recursive searching work so message_bodies can simply combine the top-level Slack text with all text found inside Slack’s nested block structure.

*Call graph*: called by 1 (message_bodies).


### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message ingest and reply sending`

Slack messages do not arrive as plain text. A person mention may look like `<@U123>`, a channel like `<#C456|team>`, and a link like `<https://example.com|docs>`. Those forms are useful for Slack’s computers, but confusing for humans and language models. This file is the translator between Slack’s wire format and the text the rest of the system wants to read.

On the way in, `render_markup` rewrites known Slack entities into normal-looking text such as `@Alex` or `#team`. If the system cannot safely match an id to a name, it leaves the original Slack code alone rather than guessing. Links keep both the visible label and the URL when they differ, so readers know what was shown and where it points.

On the way out, `mention_markup` does the reverse for one narrow case: if the agent writes `@Alex`, and the caller has confirmed exactly which Slack user that name means, it changes the text to `<@U123>` so Slack will send a notification. It deliberately avoids code blocks, links, URLs, email-like text, ambiguous names, broadcast words like `@here`, and too many mentions. Like a careful receptionist, it only pages people when it is sure who is meant.

#### Function details

##### `mentioned_users`  (lines 67–70)

```
def mentioned_users(text: str) -> frozenset[str]
```

*Call graph*: calls 1 internal fn (_mentioned).


##### `mentioned_channels`  (lines 73–75)

```
def mentioned_channels(text: str) -> frozenset[str]
```

*Call graph*: calls 1 internal fn (_mentioned).


##### `_mentioned`  (lines 78–83)

```
def _mentioned(text: str, kind: str) -> frozenset[str]
```

*Call graph*: called by 2 (mentioned_channels, mentioned_users).


##### `render_markup`  (lines 86–96)

```
def render_markup(text: str, names: Mapping[str, str]) -> str
```


##### `unescape`  (lines 99–108)

```
def unescape(text: str) -> str
```


##### `mention_key`  (lines 111–115)

```
def mention_key(name: str) -> str
```

*Call graph*: called by 2 (_mention_at, mention_index).


##### `mention_index`  (lines 118–132)

```
def mention_index(names: Mapping[str, str]) -> dict[str, str]
```

*Call graph*: calls 1 internal fn (mention_key).


##### `mention_markup`  (lines 135–166)

```
def mention_markup(text: str, ids: Mapping[str, str], limit: int=MENTION_MARKUP_MAX) -> str
```

*Call graph*: calls 1 internal fn (_mention_at); 1 external calls (finditer).


##### `_mention_at`  (lines 169–182)

```
def _mention_at(text: str, start: int, ids: Mapping[str, str]) -> tuple[int, str] | None
```

*Call graph*: calls 1 internal fn (mention_key); called by 1 (mention_markup); 2 external calls (islice, finditer).


##### `_entity`  (lines 185–201)

```
def _entity(match: re.Match[str], names: Mapping[str, str]) -> str
```


### Hosted Site Access
Public and signed web routes open hosted UFO sites, sandbox ports, stored artifacts, and preview assets safely for authorized viewers.

### `extensions/sites/ufo_ext_sites/surface.py`

`orchestration` · `request handling`

A hosted site link is treated like an address, not like a key that automatically grants access. This file is the gatehouse for those addresses. When someone opens a site link, it first proves that the link token was really made by UFO, then uses the token to find the workspace and site. After that it checks the viewer: public sites can be shown to anyone, workspace sites need a signed-in workspace member, and private sites are limited to the creator or workspace admins. If the site is an agent homepage, the agent’s visibility rules win instead.

The actual site files are not served here. Instead, this file creates or redirects to a short-lived ingress URL, which is like a temporary doorway to the site’s own isolated origin. The surrounding frame adds safety: the site runs in an iframe, with browser sandbox rules that stop model-created pages from taking over the viewer’s tab.

The file also builds the small HTML head tags used by chat apps and social tools when a link is pasted. Public sites may show their own name and preview image. Non-public sites get generic UFO text, so a crawler with no session cannot learn private names. A separate anonymous route serves public share-card images, but only while the site is still public.

#### Function details

##### `site_token`  (lines 174–183)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that names one hosted site. The token carries the workspace, conversation, and site name so the system can later find the site from the URL alone.

**Data flow**: It receives a workspace ID, conversation ID, and site name. It packages those values into signed token claims for the sites surface. It returns the token string that can be placed in a hosted-site URL.

**Call relations**: It is used by site_url when the system needs to produce the public link for a site. It hands the actual signing work to the shared surface-token helper.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 186–196)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the permanent public URL for a hosted site. It refuses to guess if the deployment has no public base URL, because a broken or missing link would be worse than a clear error.

**Data flow**: It receives the deployment’s public base URL plus the site’s workspace, conversation, and name. If the base URL is missing, it raises a SiteHostingUnconfigured error. Otherwise it asks site_token for a token and returns the full frame URL containing that token.

**Call relations**: This is the higher-level producer of site links. It relies on site_token to make the signed address part, then attaches it to the frame path.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `shipped_homepage_url`  (lines 208–223)

```
def shipped_homepage_url(public_base_url: str | None, workspace_id: UUID, slug: str, digest: str) -> str | None
```

**Purpose**: Builds a stable portal-embed URL for a deploy-wide shipped app bundle. This is used for app code that belongs to the deployment rather than to a particular hosted-site row.

**Data flow**: It receives the public base URL, workspace ID, app slug, and bundle digest. If there is no public base URL, it returns nothing. Otherwise it signs those values into a token marked as a portal embed and returns the full frame URL.

**Call relations**: This function creates the token shape later understood by shipped_address and _shipped_frame. It uses the shared surface-token signer directly.

*Call graph*: 1 external calls (mint_surface_token).


##### `shipped_address`  (lines 226–240)

```
def shipped_address(token: str) -> ShippedAddress | None
```

**Purpose**: Checks whether a token is for a shipped app bundle and, if so, extracts its address. It keeps shipped-app tokens separate from ordinary site tokens.

**Data flow**: It receives a token string and verifies its signature and surface name. It then checks for the portal-embed marker and required shipped-app fields. If everything is valid, it returns a ShippedAddress; otherwise it returns None.

**Call relations**: resolve_workspace uses it to discover a workspace before reading anything else. frame uses it to decide whether the request should go through the shipped-app path instead of the normal hosted-site path.

*Call graph*: called by 2 (frame, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `site_card_url`  (lines 243–252)

```
def site_card_url(public_base_url: str | None, token: str, digest: str) -> str | None
```

**Purpose**: Builds the public URL for a site’s share-card image. This is the preview picture used by unfurlers when a public site link is pasted into another app.

**Data flow**: It receives the public base URL, the site token, and the card digest. If hosting has no public base URL, it returns None. Otherwise it returns the share-card route URL with the token and digest in the path.

**Call relations**: frame calls this only for public, non-homepage sites that have a share-card hash. The returned URL is placed into the page’s sharing metadata.

*Call graph*: called by 1 (frame).


##### `site_address`  (lines 255–274)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Checks whether a token is a valid hosted-site address and extracts the site identity from it. It returns None for invalid tokens or tokens that are shaped like shipped-app links instead.

**Data flow**: It receives a token string, verifies it, reads the workspace, conversation, name, and optional portal-embed marker, and converts IDs into UUID values. If any required value is missing or malformed, it returns None. Otherwise it returns a SiteAddress.

**Call relations**: This is the main token reader for normal hosted sites. resolve_workspace, frame, _resolve, and homepage_embed_url all call it before they trust a URL.

*Call graph*: called by 4 (_resolve, frame, homepage_embed_url, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `homepage_embed_url`  (lines 277–292)

```
def homepage_embed_url(url: str) -> str
```

**Purpose**: Turns a normal hosted-site URL into the special signed version used inside the portal’s iframe. This lets an agent homepage be opened in the portal in a way the surface can recognize.

**Data flow**: It receives a URL, splits off the last path segment as the token, and verifies that the URL really points at the hosted-site frame path. It decodes the original site address, signs a new token with the portal-embed marker, and returns the same URL with the new token. If the input is not a hosted-site URL, it raises ValueError.

**Call relations**: It depends on site_address to validate and read the original token. It then uses the shared token signer to mint the portal-embed token.

*Call graph*: calls 1 internal fn (site_address); 1 external calls (mint_surface_token).


##### `resolve_workspace`  (lines 295–305)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace a request belongs to before the rest of the surface runs. This matters because public viewers may not have a session cookie, so the workspace must come from the signed URL itself.

**Data flow**: It reads the token path parameter from the request. It first tries to parse it as a normal site address, then as a shipped-app address. It returns the workspace ID when either succeeds, a not-found response when neither succeeds, or None only if the framework path leaves it with nothing useful.

**Call relations**: The surface framework calls this as the identification step for routes in this file. It delegates token decoding to site_address and shipped_address, and uses _not_found to hide bad tokens behind the same answer as missing sites.

*Call graph*: calls 3 internal fn (_not_found, shipped_address, site_address).


##### `frame`  (lines 308–384)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a hosted site link. It verifies the token, finds the site, checks the viewer’s permission, prepares sharing metadata, and either renders the outer frame or redirects a portal iframe to the site ingress.

**Data flow**: It receives the surface context and HTTP request. It reads the URL token, checks whether it is a shipped-app token, resolves the hosted-site row, builds public or generic share tags, identifies the viewer from the session cookie, applies visibility rules, asks the context for an ingress URL, and returns an HTML page, redirect, sign-in notice, not-found response, or unconfigured-hosting page.

**Call relations**: This is the main GET route for hosted-site links and deep links. It calls _shipped_frame for shipped app tokens, uses _sites and site_address to find rows, _viewer and _viewer_is_admin for permission checks, _into_the_portal for agent homepage routing, and _frame_page for the final normal page.

*Call graph*: calls 18 internal fn (ingress_url, list_agents, _frame_page, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _page, _session_digest, _share_tags (+8 more)); 3 external calls (HTMLResponse, RedirectResponse, mint_surface_token).


##### `_shipped_frame`  (lines 387–426)

```
async def _shipped_frame(ctx: SurfaceContext, request: Request, shipped: ShippedAddress) -> Response
```

**Purpose**: Opens a deploy-wide shipped app bundle through the same frame route. It treats the bundle as public code and uses ingress only to reach the correct app package.

**Data flow**: It receives the surface context, request, and decoded shipped-app address. If the request did not come from the portal iframe, it finds the matching agent and redirects the viewer into the portal. If it is already inside the portal iframe, it builds an ingress URL for the shipped bundle and redirects there, or returns an unconfigured page if ingress is missing.

**Call relations**: frame calls this after shipped_address identifies the token. It uses _is_portal_iframe_request and _framed_from to understand how the page was reached, _into_the_portal for cold visits, and _unconfigured_page when the deployment cannot host the app.

*Call graph*: calls 8 internal fn (ingress_url, list_agents, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _share_tags, _unconfigured_page); called by 1 (frame); 5 external calls (HTMLResponse, RedirectResponse, serve_port, shipped_anchor, shipped_app_slug).


##### `_is_portal_iframe_request`  (lines 429–430)

```
def _is_portal_iframe_request(request: Request) -> bool
```

**Purpose**: Checks whether the browser says this request is for an iframe. The file uses this as a signal that the request came from the portal’s embedded frame rather than from a normal tab.

**Data flow**: It reads the request’s Sec-Fetch-Dest header. If the header says iframe, it returns true; otherwise it returns false.

**Call relations**: frame and _shipped_frame use it to decide between redirecting into the portal and serving the embedded target. _framed_from calls it before trusting the referer header as the frame’s parent.

*Call graph*: called by 3 (_framed_from, _shipped_frame, frame).


##### `_framed_from`  (lines 433–436)

```
def _framed_from(request: Request) -> str | None
```

**Purpose**: Finds the parent page URL for requests that came from a portal iframe. This value is passed along when minting ingress so the embedded origin knows where it was framed from.

**Data flow**: It receives a request. If the request is not an iframe request, it returns None. If it is an iframe request, it returns the Referer header value.

**Call relations**: frame and _shipped_frame pass its result into ingress_url. It relies on _is_portal_iframe_request to avoid using referer information for ordinary visits.

*Call graph*: calls 1 internal fn (_is_portal_iframe_request); called by 2 (_shipped_frame, frame).


##### `_into_the_portal`  (lines 439–452)

```
def _into_the_portal(ctx: SurfaceContext, agent_id: UUID, share: str) -> Response
```

**Purpose**: Redirects a viewer to an agent’s screen inside the member portal. This is needed because some app pages only work when the portal provides their startup bridge.

**Data flow**: It receives the surface context, an agent ID, and share metadata. It asks the context for the portal home URL pointing at that agent. If a portal exists, it returns a redirect there. If no portal is installed, it returns a simple HTML page explaining that the page cannot be opened.

**Call relations**: frame uses it for agent homepages reached outside the proper portal embed. _shipped_frame uses it for shipped app links opened cold in a browser tab. It uses _page to build the fallback HTML.

*Call graph*: calls 2 internal fn (home_url, _page); called by 2 (_shipped_frame, frame); 2 external calls (HTMLResponse, RedirectResponse).


##### `_unconfigured_page`  (lines 455–463)

```
def _unconfigured_page(title: str, share: str) -> str
```

**Purpose**: Builds the small HTML page shown when this deployment has no ingress origin to embed. Instead of showing a blank iframe, it tells the viewer that site hosting is not configured.

**Data flow**: It receives a title and share metadata. It escapes the title for safe HTML, combines base styling with frame styling, and returns a complete HTML document string with the unconfigured-hosting message.

**Call relations**: frame and _shipped_frame use it when ctx.ingress_url cannot produce an embedded URL. It delegates the final document wrapper to _page.

*Call graph*: calls 1 internal fn (_page); called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `share_card`  (lines 466–505)

```
async def share_card(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the preview image for a public site link. It is intentionally anonymous, but it only returns an image while the site is still public and still matches the requested card digest.

**Data flow**: It receives the context and request, resolves the site from the token, and checks that the site exists, is not an agent homepage, is public, has a stored card blob, and has the exact requested hash. If any check fails, it returns the same not-found response as unknown sites. If all checks pass, it reads the blob bytes and returns them as the card image with cache headers.

**Call relations**: This is the GET route used by link unfurlers for public preview images. It calls _resolve to find the site and _not_found for every refusal, so the route does not reveal which private sites exist.

*Call graph*: calls 2 internal fn (_not_found, _resolve); 1 external calls (Response).


##### `set_visibility`  (lines 508–529)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Changes a site’s visibility setting from the frame page. Only the site creator may do this, and the request must include a valid CSRF token, which is a protection against another website submitting the form behind the creator’s back.

**Data flow**: It receives the context and request, resolves the site, identifies the viewer, rejects anyone who is not the creator, rejects homepage-bound sites, reads the submitted form, checks the CSRF value, validates the requested visibility level, writes the new level to storage, and redirects back to the site frame.

**Call relations**: This is the POST route behind the visibility selector in _frame_page. It uses _viewer to authenticate the browser, _csrf_holds to prove the form came from this session, _sites to update the row, and _not_found to avoid revealing private site details to outsiders.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 532–536)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Looks up the HostedSite row named by the request token. It is a small shared helper for routes that need the site but do not need the full frame-opening flow.

**Data flow**: It reads the token path parameter, decodes it with site_address, and returns None if the token is invalid. If the token is valid, it opens the hosted-sites store for the current workspace and reads the site by conversation and name.

**Call relations**: share_card and set_visibility call this before applying their own rules. It uses _sites to get the storage wrapper.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (set_visibility, share_card).


##### `_sites`  (lines 539–540)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the storage helper for hosted sites in the current workspace. This keeps the rest of the file from repeating how to connect the workspace ID and transaction object to the HostedSites store.

**Data flow**: It receives the surface context. It takes the workspace ID and transaction provider from that context and returns a HostedSites object ready to read or update site rows.

**Call relations**: frame uses it to read a site, _resolve uses it for shared lookups, and set_visibility uses it to write a new visibility level.

*Call graph*: called by 3 (_resolve, frame, set_visibility); 1 external calls (__init__).


##### `_viewer_is_admin`  (lines 543–548)

```
async def _viewer_is_admin(ctx: SurfaceContext, viewer: UUID | None) -> bool
```

**Purpose**: Checks whether a signed-in viewer is an active workspace admin. Admins are allowed to see private content in cases where ordinary members are not.

**Data flow**: It receives the context and an optional viewer member ID. If there is no viewer, it returns false. Otherwise it reads the workspace seat snapshot inside a transaction and returns true only if the matching member is seated and marked admin.

**Call relations**: frame calls this during private-site and private-agent visibility checks. It uses the Seats helper and the context transaction to read membership state.

*Call graph*: calls 1 internal fn (transaction); called by 1 (frame); 1 external calls (__init__).


##### `_viewer`  (lines 551–564)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace member represented by the browser’s ufo_session cookie. If the cookie is missing, invalid, or not allowed in this workspace, it returns None.

**Data flow**: It reads the session cookie from the request. It verifies the bearer token for the current workspace and gets an email identity. It then finds or creates the linked member record for that email and checks that the member has access. The result is the member UUID or None.

**Call relations**: frame uses it before deciding whether a viewer may see a site. set_visibility uses it before allowing a visibility change. It relies on shared bearer-token verification and context methods for member linking and access checks.

*Call graph*: calls 3 internal fn (link_member, linked_member, member_has_access); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 567–569)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility form token belongs to the current browser session. This stops a different website from silently changing a creator’s site visibility.

**Data flow**: It receives the request and submitted token string. It verifies the token and compares its CSRF claim with a digest of the current session cookie. It returns true only when they match.

**Call relations**: set_visibility calls it after confirming the viewer is the creator. It uses _session_digest to compute the session-specific value and the shared token verifier to check the submitted token.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 572–576)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a safe fingerprint of the current session cookie for CSRF protection. It does not expose the cookie itself; it hashes it into a fixed string.

**Data flow**: It reads the ufo_session cookie from the request, or an empty string if absent. It runs SHA-256 over that value and returns the hexadecimal digest.

**Call relations**: frame uses it when minting the creator’s CSRF token for the visibility form. _csrf_holds uses it later to check that the submitted token matches the same session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 579–580)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard not-found response for this surface. The same plain message is used for unknown, invalid, and unauthorized cases so the page does not become a way to probe what exists.

**Data flow**: It takes no input. It creates a plain-text HTTP response with the shared not-found body and a 404 status.

**Call relations**: resolve_workspace, frame, _shipped_frame, share_card, and set_visibility use this whenever the safe answer is to reveal nothing more.

*Call graph*: called by 5 (_shipped_frame, frame, resolve_workspace, set_visibility, share_card); 1 external calls (PlainTextResponse).


##### `_page`  (lines 583–588)

```
def _page(title: str, style: str, body: str, share: str) -> str
```

**Purpose**: Wraps a title, CSS, body HTML, and sharing metadata into a complete HTML document. It is the small template used by the frame and error pages.

**Data flow**: It receives a title string, style string, body HTML, and share-tag HTML. It combines them with the document doctype, charset, viewport, and title tags. It returns the final HTML string.

**Call relations**: _frame_page uses it for the normal hosted-site frame. frame, _into_the_portal, and _unconfigured_page use it for sign-in, no-portal, and unconfigured-hosting pages.

*Call graph*: called by 4 (_frame_page, _into_the_portal, _unconfigured_page, frame).


##### `_share_tags`  (lines 591–625)

```
def _share_tags(name: str | None, canonical: str | None, card: str | None) -> str
```

**Purpose**: Builds the metadata that chat apps and social platforms read when they preview a link. It names the site and shows its custom card only when the caller has already decided that information is public.

**Data flow**: It receives an optional site name, optional canonical URL, and optional card image URL. It escapes all user-facing values, chooses either the site-specific title/card or the generic UFO title/card, and returns a block of Open Graph and Twitter meta tags.

**Call relations**: frame calls it for normal site pages, choosing public or generic values based on site visibility. _shipped_frame calls it with generic values for shipped app pages.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `_frame_page`  (lines 628–667)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str, share: str) -> str
```

**Purpose**: Builds the outer HTML page that surrounds a hosted site. It shows the site name, either a creator visibility selector or a viewer badge, and the iframe that loads the site from its isolated ingress origin.

**Data flow**: It receives the HostedSite object, optional embedded ingress URL, canonical frame path, optional CSRF token, and share metadata. It builds the header controls, creates either an iframe with safety attributes or an unconfigured-hosting message, then returns the full HTML page.

**Call relations**: frame calls this after all access checks have passed for an ordinary hosted site. It calls _selector when the viewer is allowed to edit visibility and _page to wrap the final document.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 670–680)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the creator’s visibility form. The form lets the creator choose between private, workspace, and public visibility from the frame page.

**Data flow**: It receives the current visibility level, the frame path to post back to, and the CSRF token. It creates one option per visibility label, marks the current one as selected, includes the hidden CSRF field, and returns the form HTML.

**Call relations**: _frame_page calls this only when a CSRF token is present, which means the current viewer is the site creator. The form posts to the set_visibility route.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).


### `core/src/ufo/harness/sandbox/ingress_serve.py`

`entrypoint` · `startup and request handling`

This file is the bridge between a user’s browser and a site produced inside a sandbox. Each hosted site gets its own hostname, and the hostname encodes which conversation and port the site belongs to. That matters because the browser can then treat each site as its own origin, keeping cookies and storage separate from other sites.

The main object, `IngressServe`, builds a FastAPI web app. It has two jobs. First, it turns a signed “view” link into a short-lived session cookie for that exact site. Second, for every later request, it checks that cookie before serving anything. If the site was saved as static files, it reads those bytes directly from blob storage. If the site is still live in a sandbox, it finds the right sandbox container and streams the HTTP request and response without loading the whole body into memory.

The file is also careful about browser safety. It strips headers that could leak sessions, confuse caching, or stop the site from being framed in the product UI. It prevents sites from setting cookies in reserved UFO names, blocks cross-site WebSocket handshakes, and forces private/no-store caching where needed. Without this file, hosted sites would either be unreachable, unsafe to expose, or easy to mix up across users, workspaces, and sandbox origins.

#### Function details

##### `IngressServe.app`  (lines 368–400)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI application and declares which URLs belong to the ingress server. It makes sure special view-token paths are claimed before the catch-all proxy route can pass them to a sandbox.

**Data flow**: It starts with the configured `IngressServe` instance. It creates a FastAPI app, attaches HTTP routes for opening view links and proxying normal site traffic, attaches WebSocket routes for refusing token paths and relaying site sockets, and returns the ready app.

**Call relations**: This is used when the server starts. `run` creates an `IngressServe`, asks it for an app, and gives that app to Uvicorn so incoming browser requests can be routed to `_open`, `_proxy`, `_socket`, or the refusal helpers.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 402–408)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers requests to the bare view path when no token was supplied. It prevents a token from being smuggled through a query parameter.

**Data flow**: It receives an HTTP request. If the method is not GET or HEAD, it returns a 405 response listing the allowed methods; otherwise it returns a plain 403 message saying the link is not valid.

**Call relations**: The route table sends bare view-path requests here instead of to `_open`. This keeps token-opening behavior narrow and avoids leaking credentials to the catch-all proxy.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 410–463)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a valid signed view link into a browser session cookie for one exact hosted site. It is the controlled doorway from a portal link into the site’s own origin.

**Data flow**: It receives the request and the path after the view prefix. It extracts the token and optional entry path, checks the hostname, verifies the token, confirms it names the same conversation and port, optionally verifies the framing site belongs to the workspace, mints a short-lived session token, sets it as a cookie, and redirects the browser to the site path.

**Call relations**: FastAPI calls this for view-link URLs. It relies on `_site` to understand the hostname and `_framer_belongs` to validate sibling framing. After this succeeds, later ordinary requests go through `_authorized` and `_proxy` using the session cookie it set.

*Call graph*: calls 2 internal fn (_framer_belongs, _site); 9 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, cookie_secure, set_session_cookie, quote).


##### `IngressServe._site`  (lines 465–476)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Reads the site identity from the request hostname. It answers the question: “Which conversation and port does this hostname claim to represent?”

**Data flow**: It takes an HTTP or WebSocket connection, reads its host name, checks that it ends with the configured base host, and parses the remaining label. It returns a conversation ID and port when the label is valid, or `None` when the host is not one of this ingress server’s site hosts.

**Call relations**: `_open` uses this before accepting a view token, and `_authorized` uses it before allowing any HTTP or WebSocket traffic. It delegates the signed label parsing to the ingress host helper.

*Call graph*: called by 2 (_authorized, _open); 1 external calls (parse_site_label).


##### `IngressServe._authorized`  (lines 478–500)

```
def _authorized(self, connection: HTTPConnection) -> IngressClaims | SiteRefusal
```

**Purpose**: Checks whether a request or WebSocket handshake is allowed to reach the addressed site. It is the shared security gate for both static files and live sandbox traffic.

**Data flow**: It receives an HTTP-like connection, reads the site identity from the host, reads the ingress session cookie, verifies the cookie token, and confirms the token names the same conversation and port as the hostname. It returns the token claims when allowed, or a `SiteRefusal` with the status and message to send when denied.

**Call relations**: `_proxy` calls this before serving HTTP. `_socket` calls it before opening a WebSocket. It uses `_site` and token verification so both protocols enforce the same rule.

*Call graph*: calls 1 internal fn (_site); called by 2 (_proxy, _socket); 3 external calls (__init__, now, verify_ingress_token).


##### `IngressServe._stored_manifest`  (lines 502–540)

```
async def _stored_manifest(self, claims: IngressClaims) -> dict[str, StoredFile] | None
```

**Purpose**: Finds out whether the requested site is a stored static site and, if so, returns the list of files it can serve. If there is no stored manifest, the caller should treat the site as live and dial the sandbox.

**Data flow**: It takes verified ingress claims. For shipped app claims, it asks `_shipped_manifest`; otherwise it reads the hosted-site row from the database for the workspace, conversation, and port. If a JSON manifest is present, it converts each file entry into a `StoredFile`; if not, it returns `None`.

**Call relations**: `_proxy` calls this to choose between static serving and live proxying. `_socket` calls it to reject WebSockets for static sites, because a directory of stored files has no socket server.

*Call graph*: calls 1 internal fn (_shipped_manifest); called by 2 (_proxy, _socket); 4 external calls (__init__, loads, select, workspace_tx).


##### `IngressServe._shipped_manifest`  (lines 542–574)

```
async def _shipped_manifest(self, shipped: ShippedClaim) -> dict[str, StoredFile] | None
```

**Purpose**: Builds or reuses the file manifest for a deploy-wide shipped app bundle. These files are shared application code stored in the fleet blob store, not workspace-owned site output.

**Data flow**: It receives a shipped-app claim containing a slug and digest. It checks an in-memory cache, lists blob-store entries under the digest, maps app-local files and shared assets to request paths, guesses media types, stores the result in the cache, and returns it. If no files exist for that digest, it returns `None`.

**Call relations**: `_stored_manifest` calls this when the claims name shipped content. The returned manifest is later consumed by `_serve_stored`, which streams the actual bytes from the fleet store.

*Call graph*: called by 1 (_stored_manifest); 3 external calls (__init__, __init__, guess_type).


##### `IngressServe._dial_site`  (lines 576–606)

```
async def _dial_site(self, claims: IngressClaims) -> DialTarget | SiteRefusal
```

**Purpose**: Finds the live network address for a sandbox-hosted site. This is used when the site is not stored as static files and must be reached through its running sandbox container.

**Data flow**: It receives verified claims. Within the workspace context, it reads the conversation’s stored sandbox handle, chooses the current or resume carrier, turns the handle into a container ID, asks the carrier to dial the requested port, and returns a dial target. If the container is missing or unreachable, it returns a `SiteRefusal` explaining that the site is gone.

**Call relations**: `_proxy` uses this before forwarding HTTP to a live site. `_socket` uses it before opening an upstream WebSocket. It depends on `_stored_handle` for the persisted sandbox handle.

*Call graph*: calls 1 internal fn (_stored_handle); called by 2 (_proxy, _socket); 6 external calls (__init__, __init__, warn, sandbox_handle_backend, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 608–682)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Serves ordinary HTTP requests for hosted sites. It is the main request path: authorize first, then either serve static bytes or stream the request to a live sandbox.

**Data flow**: It receives a browser request and path. It checks authorization, looks for a stored manifest, serves stored files if present, returns 404 for missing shipped content, or dials a live sandbox. For live sites it builds an upstream URL and sanitized headers, streams the request to the sandbox, then streams the response back while rewriting unsafe headers and enforcing cache and framing rules.

**Call relations**: The catch-all HTTP route calls this for most site requests. It coordinates `_authorized`, `_stored_manifest`, `_serve_stored`, `_dial_site`, `_upstream_url`, `_upstream_headers`, `_body`, `_not_answering`, `_frame_ancestors`, `_unframed_policy`, and `_confined_cookie`.

*Call graph*: calls 11 internal fn (_authorized, _body, _confined_cookie, _dial_site, _frame_ancestors, _not_answering, _serve_stored, _stored_manifest, _unframed_policy, _upstream_headers (+1 more)); 8 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, warn, ws).


##### `IngressServe._not_answering`  (lines 684–711)

```
def _not_answering(self, request: HTTPConnection, claims: IngressClaims) -> Response
```

**Purpose**: Creates the user-facing response when a live site cannot be reached. For full page loads, it shows a waiting page and reports the outage; for other requests, it returns a short text error.

**Data flow**: It receives the failed request and the site claims. It builds a content-security policy for the same allowed frame ancestors as the real site. If the request is not a document load, it returns plain text. If it is a document load, it returns an HTML waiting page with a background report task.

**Call relations**: `_proxy` calls this when the upstream connection fails or returns a status that the edge proxy would replace. It uses `_frame_ancestors` so the fallback page can appear in the same frame as the site.

*Call graph*: calls 1 internal fn (_frame_ancestors); called by 1 (_proxy); 2 external calls (Response, BackgroundTask).


##### `IngressServe._serve_stored`  (lines 713–779)

```
async def _serve_stored(self, request: Request, claims: IngressClaims, files: dict[str, StoredFile], path: str) -> Response
```

**Purpose**: Serves one file from a stored static site or shipped app bundle. It avoids dialing any sandbox and reads directly from blob storage.

**Data flow**: It receives the request, verified claims, a manifest of files, and the requested path. It accepts only GET and HEAD, maps directories to `index.html`, checks cache validators such as ETag, chooses cache headers, and either returns headers only or streams the blob contents. If the file is absent or the blob vanished, it returns 404.

**Call relations**: `_proxy` calls this after `_stored_manifest` confirms that static files exist. It uses `_frame_ancestors` for framing policy and `_stored_body` to stream bytes after checking the first chunk.

*Call graph*: calls 2 internal fn (_frame_ancestors, _stored_body); called by 1 (_proxy); 5 external calls (__init__, __init__, Response, StreamingResponse, ws).


##### `IngressServe._stored_body`  (lines 781–787)

```
async def _stored_body(self, first: bytes, rest: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Streams stored-file bytes after the first chunk has already been safely read. This lets the caller detect a missing blob before sending a successful response.

**Data flow**: It receives the first bytes and an async iterator for the rest. It yields the first chunk, then yields every later chunk from storage unchanged.

**Call relations**: `_serve_stored` calls this only after it has successfully read the first chunk. The resulting stream is handed to the HTTP response machinery.

*Call graph*: called by 1 (_serve_stored).


##### `IngressServe._framer_belongs`  (lines 789–824)

```
async def _framer_belongs(self, workspace_id: UUID, conversation_id: UUID, port: int) -> bool
```

**Purpose**: Checks whether a site named as an allowed frame owner actually belongs to the same workspace. This prevents a signed link from letting an unrelated site frame the page.

**Data flow**: It receives a workspace, conversation ID, and port. It first looks for a hosted-site row with that identity. If not found, it checks provisioned shipped apps in the workspace and compares their derived anchor and port. It returns true only when the framer is recognized for that workspace.

**Call relations**: `_open` calls this when a view token includes an extra sibling-site framer. It reads the database and uses ingress-host helpers for shipped app identities.

*Call graph*: called by 1 (_open); 5 external calls (select, workspace_tx, serve_port, shipped_anchor, shipped_app_slug).


##### `IngressServe._frame_ancestors`  (lines 826–834)

```
def _frame_ancestors(self, claims: IngressClaims) -> str
```

**Purpose**: Builds the browser rule that says which origins may put this hosted site inside a frame. This protects one site from being silently embedded by another unauthorized site.

**Data flow**: It receives verified claims. If framing is disabled or no sibling framer is present, it returns the deploy-wide frame ancestor. Otherwise it adds the exact sibling site origin built from its conversation and port.

**Call relations**: `_proxy`, `_serve_stored`, and `_not_answering` use this when setting Content Security Policy headers. It uses the site-label helper to turn a sibling site identity back into a hostname.

*Call graph*: called by 3 (_not_answering, _proxy, _serve_stored); 1 external calls (site_label).


##### `IngressServe._upstream_url`  (lines 836–839)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the URL used to contact a live sandbox server. It preserves the requested path and query string while safely quoting path characters.

**Data flow**: It receives a scheme, host, path, and raw query string. It constructs a URL like `http://host/path`, quotes the path safely, appends the query when present, and returns the final string.

**Call relations**: `_proxy` uses this for HTTP forwarding, and `_socket` uses it for WebSocket forwarding.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 841–851)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Reads the saved sandbox handle for a conversation. The handle is the stored clue needed to find the live container for a dialed site.

**Data flow**: It receives a workspace ID and conversation ID. It queries the conversation table under that workspace and returns the sandbox handle if found, otherwise `None`.

**Call relations**: `_dial_site` calls this before choosing a carrier and dialing the sandbox. It is the database lookup part of live-site resolution.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 853–887)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Creates the exact request headers that the sandbox site is allowed to see. It removes transport-only headers, the ingress session cookie, WebSocket handshake internals, and any headers supplied by the dial layer.

**Data flow**: It receives the browser connection and extra dial headers. It loops through incoming headers, skips unsafe or inappropriate ones, strips the reserved UFO session cookie out of `Cookie`, keeps the site’s own cookies, then appends the dial headers. It returns a list of header pairs for the upstream request.

**Call relations**: `_proxy` uses this for live HTTP requests. `_socket` uses it for upstream WebSocket handshakes. It is one of the main places where the ingress prevents sandbox code from seeing or reusing the ingress session credential.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 889–901)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes a site’s own `frame-ancestors` rule from its Content Security Policy while preserving the rest. This lets UFO decide framing centrally without discarding the site’s other browser protections.

**Data flow**: It receives one policy header string. It splits it into directives, removes any directive named `frame-ancestors`, joins the remaining directives, and returns the rewritten policy. If nothing remains, it returns an empty string.

**Call relations**: `_proxy` calls this while copying live upstream response headers. UFO then adds its own framing policy separately through `_frame_ancestors`.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 903–930)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Rewrites or drops a site’s `Set-Cookie` header so the site can only set cookies for its own hostname and cannot use UFO’s reserved cookie names.

**Data flow**: It receives one raw `Set-Cookie` header. It parses the cookie name, drops invalid, nameless, or reserved-name cookies, removes any `Domain` attribute, and returns the confined cookie header. If the cookie is not allowed, it returns `None`.

**Call relations**: `_proxy` calls this for each `Set-Cookie` header coming back from a live sandbox. It allows normal site cookies while preventing a site from planting cookies for sibling sites or the app host.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 932–942)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw bytes from a live upstream HTTP response and makes sure the upstream response is closed afterward. This protects the connection pool even if streaming fails midway.

**Data flow**: It receives an `httpx` response. It yields each raw chunk from the upstream body, and in a final cleanup step closes the upstream response whether the stream completed, failed, or was abandoned.

**Call relations**: `_proxy` passes this stream into a `StreamingResponse` for live sandbox responses. It also pairs with a background close task, making cleanup robust in normal and abnormal endings.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 944–950)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Refuses WebSocket attempts to the view-token path. View tokens are meant to be exchanged by HTTP only, not exposed to sandbox socket handlers.

**Data flow**: It receives a WebSocket handshake, creates a 403 refusal saying the link is not valid, and sends that denial response instead of accepting the socket.

**Call relations**: The WebSocket routes for the view path call this. It uses `_refuse` so the rejection looks like the HTTP-side rejection rather than becoming a vague socket close.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 952–1008)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Relays an accepted WebSocket between the browser and a live sandbox site. This supports site features such as live reload or push messages.

**Data flow**: It receives a WebSocket handshake and path. It checks same-origin rules, authorizes the session, rejects static sites, dials the live sandbox, opens an upstream WebSocket with sanitized headers and offered subprotocols, accepts the viewer socket only after the upstream accepts, then relays messages both ways. On failure it refuses or closes with an appropriate code and message.

**Call relations**: The catch-all WebSocket route calls this. It coordinates `_same_origin`, `_authorized`, `_stored_manifest`, `_dial_site`, `_upstream_url`, `_upstream_headers`, `_refuse`, `_relay`, and `_end`.

*Call graph*: calls 9 internal fn (_authorized, _dial_site, _end, _refuse, _relay, _same_origin, _stored_manifest, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 1010–1022)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site hostname it is trying to connect to. This closes a browser loophole where cookies may be sent on cross-site WebSocket handshakes.

**Data flow**: It reads the handshake’s `Origin` header and compares its hostname to the WebSocket request hostname. It returns true only when an origin is present and both hostnames match.

**Call relations**: `_socket` calls this before authorization and dialing. HTTP requests do not use this exact check, but WebSockets need it because normal browser cross-origin read protections do not apply in the same way.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 1024–1037)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Rejects a WebSocket handshake with an HTTP-style response. This gives the browser the same status, body, and cache rule it would receive from the HTTP proxy gate.

**Data flow**: It receives a WebSocket and a `SiteRefusal`. It builds a response from the refusal’s status, message, and media type, adds a no-store cache header, and sends it as a denial response without accepting the socket.

**Call relations**: `_no_socket_view` and `_socket` call this whenever a socket must not be opened. It keeps WebSocket denials aligned with `_authorized` and normal HTTP refusals.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 1039–1056)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs the two halves of a WebSocket relay at the same time. If either side finishes or fails, it stops the other side too.

**Data flow**: It receives the viewer WebSocket and upstream site connection. It starts one task to copy viewer messages to the site and another to copy site messages to the viewer, waits for the first one to finish, cancels the other, and raises any real failure from the completed side.

**Call relations**: `_socket` calls this after both WebSocket connections are open. It delegates actual message copying to `_viewer_to_site` and `_site_to_viewer`.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 1058–1067)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the sandbox site while preserving whether each message is text or binary bytes.

**Data flow**: It repeatedly receives messages from the viewer. If the viewer disconnected, it returns. Otherwise it sends the message payload to the upstream site as text when it was text, or bytes when it was binary.

**Call relations**: `_relay` runs this as one of the two relay tasks. Its counterpart, `_site_to_viewer`, handles the opposite direction.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 1069–1082)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the sandbox site back to the browser, then closes the browser socket with a suitable close code when the site ends.

**Data flow**: It reads messages from the upstream WebSocket. Text messages are sent as text to the viewer, and binary messages as bytes. When upstream reading ends, it chooses the upstream close code or a normal close code, replaces forbidden wire codes with a safe error code, and asks `_end` to close the viewer socket.

**Call relations**: `_relay` runs this beside `_viewer_to_site`. It calls `_end` to finish the viewer side quietly and safely.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 1084–1094)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Closes a viewer WebSocket without letting close-time errors create a new failure. It treats the connection as already over if the browser has disappeared.

**Data flow**: It receives a viewer socket, close code, and reason. It attempts to close the socket with that information and suppresses any exception raised during the close attempt.

**Call relations**: `_site_to_viewer` calls this after the upstream site ends. `_socket` also calls it when the relay fails after acceptance, so the browser receives a clear terminal close when possible.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 1097–1108)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the base hostname under which all site hostnames must live. It refuses to start if the public ingress URL is missing or has no host.

**Data flow**: It receives a configured URL string or `None`. It parses the URL, returns the hostname when present, and raises a runtime error when not configured correctly.

**Call relations**: `run` calls this during startup before launching the server. The result is passed into `IngressServe`, where `_site` uses it to recognize valid site hostnames.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 1111–1121)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Builds the deploy-wide browser framing source from the product’s public app URL. If there is no app URL, it returns `'none'`, meaning no one may frame hosted sites by default.

**Data flow**: It receives the configured public base URL. It parses the scheme, hostname, and optional port, and returns an origin string such as `https://app.example.com`; if the URL is incomplete, it returns the no-framing value.

**Call relations**: `run` calls this at startup and passes the value into `IngressServe`. Later `_frame_ancestors` uses it as the base framing rule for proxied, stored, and waiting-page responses.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 1124–1147)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to contact live sandbox sites. The client is deliberately cookie-blind so one site’s cookies cannot leak into another site’s request.

**Data flow**: It creates an `httpx.AsyncClient` with timeouts, connection limits, and a cookie jar policy that accepts no domains. The returned client can stream upstream traffic but will not store or replay cookies on its own.

**Call relations**: `run` calls this once at startup and passes the client to `IngressServe` and `SiteReporter`. `_proxy` later uses the client for live HTTP forwarding.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 1150–1179)

```
def run() -> None
```

**Purpose**: Starts the ingress server process. It loads configuration, prepares dependencies, constructs `IngressServe`, and hands its web app to Uvicorn.

**Data flow**: It loads config, initializes observability, loads extension manifests, initializes and checks the database, verifies the ingress secret is available, selects sandbox carriers, creates the upstream HTTP client and blob store, builds the server object, logs startup, and starts Uvicorn on the configured port.

**Call relations**: This is the process entrypoint for this file. It wires together helper functions such as `ingress_base_host`, `ingress_frame_ancestor`, and `upstream_client`, then calls `IngressServe.app` indirectly by passing the built app into Uvicorn.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 15 external calls (__init__, __init__, run, blob_store_for, load_config, init_db, verify_db_reachable, init_o11y, log, ingress_secret (+5 more)).


### `core/src/ufo/harness/sandbox/ingress_url.py`

`io_transport` · `request handling`

A sandbox may run a web server on some internal port, but an outside browser needs a public URL to reach it. This file creates that URL. Think of it like printing a short-lived visitor pass: it names the exact workspace, conversation, and port, includes an expiry time, and wraps that information in a signed token so the public ingress service can later verify it.

The main function starts with a configured public base URL. If there is no public URL, it cannot make a link and returns nothing. Otherwise it prepares optional shipping information, such as a shipped app slug and digest, then creates an ingress token containing the workspace, conversation, port, expiry time, and optional frame information. It also creates a safe host label from the conversation and port, places that label as a subdomain of the public host, and appends the token and requested entry path.

The helper `_framer_claim` is a safety check for embedded pages. If a page says it was framed from another URL, this helper only accepts that claim when the framing page is on the same ingress base domain, uses the same scheme and port, and has a valid sandbox site label. This prevents arbitrary outside pages from pretending to be trusted sandbox frames.

#### Function details

##### `mint_ingress_view_url`  (lines 17–50)

```
def mint_ingress_view_url(public_url: str | None, workspace_id: UUID, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest:
```

**Purpose**: Creates a temporary public URL for viewing one port of one sandbox workspace in a browser. Someone uses it when they need to give the browser a safe, time-limited way to reach an app running inside the sandbox.

**Data flow**: It receives the public ingress base URL, workspace and conversation IDs, the sandbox port, the browser entry path, and optional information about a shipped app or framing page. If the public base URL is missing, it returns `None`. Otherwise it splits the base URL into parts, builds optional shipped-app data, asks `_framer_claim` whether the framing page is trustworthy, creates an expiring ingress token, builds a subdomain label from the conversation and port, safely quotes the entry path, and returns the finished browser URL as a string.

**Call relations**: This is the file's main doorway. While building the URL, it calls `_framer_claim` to add trusted frame context when available. It also relies on the ingress host helpers to create the subdomain label, and on the ingress token helper to mint the signed token that the public ingress service will later check.

*Call graph*: calls 1 internal fn (_framer_claim); 7 external calls (__init__, __init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `_framer_claim`  (lines 53–73)

```
def _framer_claim(base: SplitResult, framed_from: str | None) -> FramerClaim | None
```

**Purpose**: Checks whether a claimed framing page belongs to the same sandbox ingress system, and if so turns it into a small trusted claim. This matters because frame information should not be accepted just because a browser or caller supplied a URL.

**Data flow**: It receives the parsed public base URL and an optional `framed_from` URL string. It splits the framing URL, compares its scheme, port, and host against the base ingress URL, and rejects it if it is missing, malformed, or not under the same base host. If the host looks valid, it removes the base host suffix, parses the remaining site label into a conversation ID and port, and returns a `FramerClaim`. If any check fails, it returns `None`.

**Call relations**: It is called by `mint_ingress_view_url` while the expiring ingress URL is being assembled. Its result is handed into the ingress token claims, so later parts of the system can know which sandbox page framed this one, but only when that relationship passed these local safety checks.

*Call graph*: called by 1 (mint_ingress_view_url); 3 external calls (__init__, parse_site_label, urlsplit).


### Operator Inspection Tools
Operator-only surfaces expose debugger, directory, problem-reporting, and memory-inspection views under shared trusted-access rules.

### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the server-side doorway for the debugger surface. Think of it like a read-only control room: the browser loads one built React page, then that page asks these routes for the facts it should display. The actual safety gate is outside most of these functions: requests are scoped through a SurfaceContext, which means reads are tied to one workspace after operator authorization has already happened.

Most functions are thin translators. They take an HTTP request, pull out an identifier such as a conversation ID or turn ID, ask SurfaceContext for the matching stored data, and return it as JSON. If the ID is missing or invalid, they return a clear 404-style error instead of leaking details. One route serves workspace files as a byte stream, because files may be large or not text. Another route streams live turn updates using Server-Sent Events, a simple browser-friendly way for the server to keep sending new events over one open connection.

The ROUTES table at the bottom is the map that connects URL paths to these functions. Without this file, the debugger frontend would have no data source, and operators could not inspect what happened inside a workspace or follow a live turn.

#### Function details

##### `app_page`  (lines 57–62)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger web app page to the browser. If the frontend has not been built yet, it stops with an explicit error so the operator does not see a blank or misleading page.

**Data flow**: It receives the current surface context and the incoming web request, reads the already-loaded HTML text from disk state, and wraps that HTML in an HTTP response. If the HTML file was not found at startup, it raises an error telling the developer to build the frontend.

**Call relations**: The route table sends plain GET requests for the debugger root page here. This function does not fetch debugger data itself; it only delivers the shell of the app, which later calls the API routes in this same file.

*Call graph*: 1 external calls (HTMLResponse).


##### `fleet`  (lines 65–69)

```
async def fleet(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the fleet-wide index that the debugger landing page uses. It shows the deploy's workspaces and recent threads for an authorized operator.

**Data flow**: It receives the request, creates a FleetDirectory reader, asks it for the current fleet directory, converts that structured result into JSON-friendly data, and sends it back as a JSON response.

**Call relations**: The API route for the fleet index calls this when the frontend needs the landing-page overview. It hands the actual reading to FleetDirectory and only formats the result for the browser.

*Call graph*: 2 external calls (__init__, JSONResponse).


##### `workspace_meta`  (lines 72–85)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns basic facts about the currently selected workspace. This includes the workspace ID, the Slack team if one is connected, and the Datadog site setting if present.

**Data flow**: It reads the Slack installation marker from the SurfaceContext, removes the internal Slack prefix when present, reads the Datadog site value from the environment, and returns those fields as JSON.

**Call relations**: The workspace metadata API route calls this when the frontend needs context for the page it is showing. It relies on SurfaceContext for workspace-scoped installation data and on the process environment for the Datadog setting.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 88–90)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the conversations visible inside the current workspace. The debugger uses this to populate the conversation browser.

**Data flow**: It asks the SurfaceContext for the workspace's conversation list, converts each entry into JSON-friendly form, and returns the list to the browser.

**Call relations**: The conversations API route calls this during normal debugger browsing. It delegates the workspace-safe read to SurfaceContext and only turns the result into an HTTP JSON response.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 93–98)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the turns inside one conversation. A turn is one unit of interaction or work within a conversation.

**Data flow**: It takes the conversation ID from the URL, uses _uuid_param to check that it is a valid UUID, and returns a 404 error if not. If valid, it asks SurfaceContext for that conversation's turns, converts them to JSON-friendly objects, and returns them.

**Call relations**: The route for a conversation's turns calls this when the frontend opens a conversation. It uses _uuid_param for safe ID parsing, then hands the real lookup to SurfaceContext.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 101–108)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the saved transcript for one conversation. The transcript is the readable record of what was said or exchanged.

**Data flow**: It reads and validates the conversation ID from the URL. With a valid ID, it asks SurfaceContext for the transcript; if there is no matching transcript, it returns a 404 error, otherwise it returns the transcript as JSON.

**Call relations**: The transcript API route calls this when the frontend wants the full conversation text. It depends on _uuid_param to reject bad IDs before asking SurfaceContext for stored data.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 111–115)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is where older conversation content has been summarized or condensed to save space while keeping context.

**Data flow**: It validates the conversation ID from the URL. If the ID is valid, it asks SurfaceContext for that conversation's compaction indexes or records, turns the returned iterable into a list, and sends it as JSON.

**Call relations**: The compactions list route calls this when the debugger needs to show which summarization events exist. It uses _uuid_param first, then relies on SurfaceContext for the workspace-scoped read.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 118–133)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one specific compaction record, including what messages existed before, what remained after, and the summary that replaced the removed detail.

**Data flow**: It reads the conversation ID and compaction index from the URL, rejects them if the ID is invalid or the index is not a number, and asks SurfaceContext for that exact record. If found, it builds a JSON object containing the index, before messages, after messages, and summary.

**Call relations**: The route for a single compaction calls this when an operator drills into a compaction. It uses _uuid_param for the conversation ID, validates the numeric index itself, and asks SurfaceContext for the stored record.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 136–141)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a conversation's workspace area. This lets an operator see what artifacts or files were present for that conversation.

**Data flow**: It validates the conversation ID from the URL, returns a 404 error if it is invalid, then asks SurfaceContext for the file list. Each file entry is converted to JSON-friendly form and returned.

**Call relations**: The files-list API route calls this when the frontend shows conversation files. It uses _uuid_param for safe parsing and SurfaceContext for the actual workspace-scoped file listing.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 144–154)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file to the browser. It uses streaming so the file can be sent as bytes instead of forcing everything into a JSON response.

**Data flow**: It validates the conversation ID and reads the requested file path from the URL. It asks SurfaceContext for a readable stream; if the path is invalid, unsafe, or missing, it returns a 404 error. If a stream is available, it returns it as an octet-stream, meaning generic binary data.

**Call relations**: The individual-file API route calls this when an operator opens or downloads a file. It uses _uuid_param for the conversation ID, then hands file access to SurfaceContext and wraps the resulting stream in a StreamingResponse.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 157–164)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn. This gives the debugger a focused view of a single unit of work.

**Data flow**: It reads the turn ID from the URL, checks that it is a valid UUID, and asks SurfaceContext for the turn detail. If the ID is invalid or no turn exists, it returns a 404 error; otherwise it returns the detail as JSON.

**Call relations**: The turn-detail API route calls this when the frontend opens a specific turn. It relies on _uuid_param for ID validation and SurfaceContext for the stored turn data.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `turn_steps`  (lines 167–174)

```
async def turn_steps(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the step-by-step records inside one turn. This helps an operator see how the system got from the start of a turn to its result.

**Data flow**: It validates the turn ID from the URL. If valid, it asks SurfaceContext for the turn's steps; if there are none because the turn does not exist, it returns a 404 error. Otherwise it converts each step to JSON and returns the list.

**Call relations**: The turn-steps API route calls this when the frontend wants the turn timeline. It uses _uuid_param before asking SurfaceContext for the step records.

*Call graph*: calls 2 internal fn (turn_steps, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 177–182)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts a live event stream for one turn. This lets the debugger watch updates arrive while the turn is still running or being tailed.

**Data flow**: It validates the turn ID, checks that the turn exists, and reads the Last-Event-ID header if the browser is resuming a dropped stream. It then returns a streaming HTTP response whose body comes from _events and whose media type is Server-Sent Events.

**Call relations**: The live-stream API route calls this when the frontend subscribes to a turn. It checks existence through SurfaceContext, then hands ongoing event production to _events.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 185–188)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Converts the live tail of a turn into a sequence of bytes ready to send over the open HTTP stream. It is the bridge between stored live frames and browser-readable Server-Sent Events.

**Data flow**: It receives the surface context, a turn ID, and an optional resume cursor. It opens SurfaceContext.tail, then for each cursor and live frame it receives, it calls _sse to encode that frame and yields the resulting bytes.

**Call relations**: stream calls this after it has validated the requested turn. _events depends on SurfaceContext.tail for the live feed and hands each frame to _sse for the exact wire format.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 191–219)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as one Server-Sent Event. Server-Sent Events are a simple text format where each message has an event name and data for the browser to read.

**Data flow**: It receives a cursor and a LiveFrame object. If the cursor is non-empty, it writes it as the event ID so the browser can resume later. It inspects the frame type, chooses a readable event name such as terminal, activity, reply, or text, serializes the frame to JSON, and returns the complete event as bytes.

**Call relations**: _events calls this for every frame coming from SurfaceContext.tail. If a new kind of live frame appears and this function does not know how to name it, it raises an error instead of silently sending a misleading event.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 222–226)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID from a URL path parameter. A UUID is a standard long identifier used here for conversations and turns.

**Data flow**: It takes the request and the parameter name, reads that path value, and tries to turn it into a UUID object. If the value is not a valid UUID string, it returns None so the caller can respond with a not-found error.

**Call relations**: Most detail routes call this before reading data: conversation_turns, conversation_transcript, conversation_compactions, compaction_record, workspace_files, workspace_file, turn, turn_steps, and stream. It keeps the same validation pattern in one place so those routes can stay simple.

*Call graph*: called by 9 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, turn_steps, workspace_file, workspace_files); 1 external calls (UUID).


### `core/src/ufo/runtime/ext/operator.py`

`domain_logic` · `request handling`

Operator tools, such as a session debugger or memory explorer, need stronger access than normal user pages. This file is the shared front desk for those tools. It checks a bearer token, which is a signed proof of identity, from safe places only: the Authorization header, a protected cookie, or the one form post that starts a session. It deliberately never accepts the token in the URL, because URLs often end up in browser history, logs, and shared links.

Once a request is proven, the file decides which workspace the operator is looking at. By default, it uses the workspace named inside the token. If the operator belongs to the special operator email domain, they may use `?ws=` to switch to another workspace by workspace ID or by domain name. If an unauthenticated browser opens an operator page directly, it is redirected to the shared login page instead of simply failing.

The second half is the fleet directory. Think of it like an index page for a building manager: it lists all offices, how many people and conversations each has, and the most recently active conversation threads. It first reads only cross-workspace identifiers and timestamps, then re-enters each workspace to read human-facing details under that workspace’s normal safety rules. It also filters out subagent-only turns so automated background chatter does not crowd out real member-facing activity.

#### Function details

##### `operator_claims`  (lines 45–63)

```
async def operator_claims(request: Request) -> tuple[str, str] | None
```

**Purpose**: This function tries to prove who the operator request belongs to and which workspace their token names. It checks credentials in a safe order: Authorization header first, then the operator session cookie, then a posted form token for the login step.

**Data flow**: It receives a web request. It reads the Authorization header, cookies, and, only for POST requests, the submitted form body. Each possible token is trimmed and verified through `_candidate_claims`; the first valid one becomes a `(workspace, email)` pair. If none works, it returns nothing.

**Call relations**: When `resolve_operator_workspace` needs to decide whether a request is allowed and what workspace it should use, it calls this function first. This function delegates the actual token checking to `_candidate_claims`, and reads the form body only for the special POST that opens a browser session.

*Call graph*: calls 1 internal fn (_candidate_claims); called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `_candidate_claims`  (lines 66–68)

```
def _candidate_claims(candidate: str) -> tuple[str, str] | None
```

**Purpose**: This small helper turns one possible token string into verified identity claims, or rejects it if it is empty or invalid. It keeps the token-checking step consistent for headers, cookies, and form posts.

**Data flow**: It receives one candidate token string. It removes surrounding whitespace, ignores it if nothing is left, and otherwise passes it to `verified_claims`, which checks the signed token. The result is either a `(workspace, email)` pair or nothing.

**Call relations**: It is used only by `operator_claims`, which tries several token locations. The helper hands off the security-sensitive verification to `verified_claims`, so this file does not keep or use the signing secret directly.

*Call graph*: called by 1 (operator_claims); 1 external calls (verified_claims).


##### `resolve_operator_workspace`  (lines 71–111)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: This function decides whether an operator request is allowed, and if so, which workspace it should see. It also redirects a browser to the login page when someone opens an operator page without a usable session.

**Data flow**: It receives the incoming request and the surface authentication object. It asks `operator_claims` for verified claims. If there are no claims, a plain GET for an operator page is turned into a redirect to the login page; other requests are rejected by returning nothing. If claims exist, it checks that the email address belongs to the operator email domain. Then it either uses the workspace ID from the token, or reads the `ws` query value and resolves it as a workspace ID, a known workspace domain, or a predictable domain-based UUID.

**Call relations**: This is the main gate that operator surfaces call when scoping a request. It relies on `operator_claims` for identity, `email_domain` for the operator-domain check, `workspace_by_domain` inside an owner-level database transaction for domain lookup, and `RedirectResponse` when the right next step is browser sign-in.

*Call graph*: calls 1 internal fn (operator_claims); 6 external calls (owner_tx, email_domain, workspace_by_domain, RedirectResponse, UUID, uuid5).


##### `bind_operator_session`  (lines 114–132)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens the browser session after login by saving the posted bearer token into the shared operator cookie. It then sends the browser back to the operator page it was trying to reach.

**Data flow**: It receives the surface context and the request. It reads the form body and looks for the `token` field. If the field is missing or blank, it returns a JSON error with a bad-request status. Otherwise it builds a redirect response to the same URL and adds an HTTP-only session cookie containing the token, using the surface’s secure-cookie setting.

**Call relations**: This is called for the POST that starts an operator session. It uses `Request.form` to read the submitted token, `JSONResponse` for a clear error, `RedirectResponse` for the successful browser bounce, and `set_session_cookie` to create the cookie that later calls to `operator_claims` will read.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


##### `FleetWorkspace._aware_utc`  (lines 150–151)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This validator makes sure a workspace’s last-activity time has timezone information. That prevents later code from accidentally mixing timezone-aware and timezone-less times.

**Data flow**: It receives a `last_turn_at` value while a `FleetWorkspace` model is being built. If the value is missing or already has a timezone, it leaves it alone. If it is a plain datetime with no timezone, it marks it as UTC and returns that adjusted value.

**Call relations**: Pydantic, the data-checking library used by `FleetWorkspace`, calls this automatically during model creation. It uses `datetime.replace` only when it needs to attach UTC to a timezone-less timestamp.

*Call graph*: 1 external calls (replace).


##### `FleetThread._aware_utc`  (lines 169–170)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes sure a recent conversation thread’s last-activity time is timezone-aware. It protects callers from confusing timestamps that look similar but cannot be safely compared.

**Data flow**: It receives the thread’s `last_turn_at` value while a `FleetThread` model is being built. If the datetime already includes a timezone, it is returned unchanged. If not, the function labels it as UTC and returns the corrected datetime.

**Call relations**: Pydantic calls this automatically whenever `FleetThread` is constructed, including inside `FleetDirectory.read`. It relies on `datetime.replace` to attach UTC when the database value did not already carry timezone information.

*Call graph*: 1 external calls (replace).


##### `FleetDirectory.read`  (lines 202–233)

```
async def read(self) -> FleetListing
```

**Purpose**: This function builds the operator’s fleet index: all workspaces plus the most recently active conversations across them. It turns raw database rows into clean `FleetListing`, `FleetWorkspace`, and `FleetThread` objects suitable for an operator page or API response.

**Data flow**: It starts by calling `_enumerate` to get workspace IDs, conversation IDs, and recent activity times across the whole fleet. It groups the recent conversation IDs by workspace. Then, for each workspace, it temporarily scopes execution to that workspace and calls `_scoped` to collect readable details such as domain, member count, conversation count, surface, queue key, and title. Finally it combines those pieces into a `FleetListing` containing workspace summaries and recent thread summaries.

**Call relations**: This is the public read path for `FleetDirectory`. It calls `_enumerate` for the safe cross-workspace pass, uses `ws` to re-bind each workspace before reading details, calls `_scoped` for the per-workspace pass, and constructs `FleetThread` and `FleetListing` objects as the final handoff to the operator surface.

*Call graph*: calls 2 internal fn (_enumerate, _scoped); 3 external calls (__init__, __init__, ws).


##### `FleetDirectory._enumerate`  (lines 235–273)

```
async def _enumerate(self) -> tuple[Sequence[sa.Row[Any]], Sequence[sa.Row[Any]]]
```

**Purpose**: This function performs the broad fleet-wide scan, but only for IDs and timestamps. It answers two questions: which workspaces exist, and which root conversations were active most recently.

**Data flow**: It builds database queries that count only top-level turns, excluding subagent child turns. One query lists all workspaces ordered by most recent activity, including workspaces with no activity yet. The other query lists the most recently active conversations, with turn counts and last activity times, capped by the directory’s thread limit. It runs both queries inside an owner-level transaction and returns the resulting rows.

**Call relations**: `FleetDirectory.read` calls this first. The function uses SQLAlchemy to build the database queries and `owner_tx` to run them in the cross-workspace context that is allowed to see workspace and conversation identifiers.

*Call graph*: called by 1 (read); 2 external calls (select, owner_tx).


##### `FleetDirectory._scoped`  (lines 275–323)

```
async def _scoped(self, workspace_id: UUID, last_turn_at: datetime | None, conversation_ids: Sequence[UUID]) -> tuple[FleetWorkspace, dict[UUID, sa.Row[Any]]]
```

**Purpose**: This function reads the human-facing details for one workspace after the code has been scoped to that workspace. It gathers the domain, counts, and conversation labels that the operator directory displays.

**Data flow**: It receives a workspace ID, that workspace’s last activity time, and the conversation IDs that should be opened for details. Inside a workspace transaction, it looks up the workspace domain, counts members, counts non-subagent conversations, and, if needed, fetches each requested conversation’s surface, queue key, and title. It returns a `FleetWorkspace` summary and a dictionary of conversation rows keyed by conversation ID.

**Call relations**: `FleetDirectory.read` calls this once per workspace after entering that workspace with `ws`. This function uses `workspace_tx` so normal workspace-level database safety rules apply, calls `workspace_domain` for the display domain, and constructs the `FleetWorkspace` object that becomes part of the final listing.

*Call graph*: called by 1 (read); 4 external calls (__init__, select, workspace_tx, workspace_domain).


### `extensions/debugger/ufo_ext_debugger/report.py`

`domain_logic` · `tool invocation during request handling`

This file exists so the agent has a safe, structured way to say, “Something is broken here and the user cannot fix it from this conversation.” It is not for ordinary failed commands or retryable errors. It is for problems like missing credentials, broken authentication, a sandbox that will not respond, or a member explicitly asking that a problem be reported.

The file defines the shape of the report with `ReportProblemInput`. The report must include a short description of the problem, a category engineers can group and route, an impact level, and whether it came from an actual fault or a member request. The problem text is deliberately limited and checked so it does not include a credential-bearing URL, such as a copied proxy address with a secret inside it.

When `report_problem` runs, it builds a debugger link if the deployment has a public base URL. That link points to this extension’s debug surface and includes the workspace, conversation, and turn identifiers, so an engineer can land directly on the relevant transcript. Then it sends a single warning record through telemetry. It does not store anything itself, deduplicate reports, or reply with an engineer’s answer. Like dropping a marked note into an operations inbox, its job is simply to make the issue visible with the right labels and the right route back to evidence.

#### Function details

##### `ReportProblemInput._is_the_agents_own_account`  (lines 112–115)

```
def _is_the_agents_own_account(cls, value: str) -> str
```

**Purpose**: This validator checks the agent’s problem description before a report is accepted. Its main job is to stop accidental leakage of credentials by rejecting text that looks like a URL containing login information.

**Data flow**: It receives the proposed `problem` text. It searches that text for a credentialed URL pattern, meaning a web address shaped like it contains secret user information before an `@` sign. If it finds one, it raises an error and the report input is rejected; otherwise it returns the same text unchanged.

**Call relations**: This runs automatically as part of validating `ReportProblemInput` before `report_problem` receives the arguments. It protects the later telemetry call from being given sensitive text that should not be written into warning records.


##### `report_problem`  (lines 118–139)

```
async def report_problem(ctx: ToolContext, args: ReportProblemInput) -> ToolResult
```

**Purpose**: This is the tool action that actually reports the problem. It creates a telemetry warning with the problem details and, when possible, a direct link to the debugger view for the exact workspace conversation turn.

**Data flow**: It receives a tool context, which contains deployment and turn information, and a validated `ReportProblemInput`, which contains the agent’s report. It builds a debug URL if a public base URL is available, looks up the member identity from the authority information, and sends a warning event containing the problem, category, impact, origin, turn identifiers, agent identifier, member identifier, and debug link. It then returns a short tool result telling the agent that the report was sent and that no answer will arrive in the conversation.

**Call relations**: This function is registered as the handler for the `report_problem` tool. When the agent calls that tool, this function calls `authority_member_id` to identify the member when possible, calls `warn` to emit the telemetry event engineers will read, and wraps the confirmation message in `TextContent` and `ToolResult` so the tool call has a normal response.

*Call graph*: 4 external calls (__init__, __init__, authority_member_id, warn).


### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the small web surface for inspecting a workspace’s durable memory store. In plain terms, it is like a locked filing-cabinet viewer: once an operator has been allowed into a workspace, this page shows what memory records are in that workspace, but does not let the operator edit them.

At startup, the file looks for a bundled HTML page at `static/memory.html` and keeps its contents ready to serve. The route for the main page returns that HTML as-is, so the browser receives a self-contained interface. A second route is used by the page to ask for the actual memory data as JSON, which is a common browser-friendly data format.

The important safety detail is workspace scoping. The surrounding operator session decides which workspace the request is allowed to see. When the JSON endpoint reads from the memory extension’s own store, it opens a workspace-scoped transaction, meaning the database read is automatically limited to that workspace. This prevents the explorer from accidentally showing another workspace’s memories.

The route table at the bottom connects three web actions: load the page, bind the operator session, and fetch the list of memories.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function serves the memory explorer web page to the operator’s browser. It exists so the browser can load the visual interface before asking for memory data.

**Data flow**: It receives the current surface context and the web request. It checks whether the HTML file was successfully loaded earlier; if not, it stops with an error because there is no page to show. If the HTML is present, it wraps that text in an HTML web response and sends it back to the browser.

**Call relations**: This function is used by the GET route for the surface’s root path. When an operator opens the memory explorer, the route calls this function, and it hands the browser a full HTML page using `HTMLResponse`.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns every memory record visible in the currently bound workspace as JSON. The page uses it to fill the explorer with the same stored memories that recall can draw from.

**Data flow**: It receives the surface context, which includes the workspace identity, and the web request. It builds an extension context for the memory extension so it can open the extension’s own scoped database transaction. It asks the memory store inventory function for memory items in that workspace, turns each item into JSON-ready data, and returns the resulting list as a JSON web response.

**Call relations**: This function is used by the GET route at `api/memories`, normally after the HTML page has loaded in the browser. It creates `ScopedStore`, `CredentialAccess`, and `ExtensionContext` objects so the read happens through the memory extension’s own storage path, then delegates the actual lookup to `ufo_ext_memory.store.inventory` and sends the result back with `JSONResponse`.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).


### Live Turn Streams
Runtime stream helpers let user interfaces follow turn progress reliably, including reconnects, replay, completion, and pause detection.

### `core/src/ufo/runtime/surfaces/hub_tail.py`

`orchestration` · `request handling / live stream`

A “turn” is a unit of work whose progress can be streamed to a client, like watching words appear in a chat response. The hard part is that a viewer may connect after the turn has already started, or even after it has finished somewhere else. If this file only listened to the live hub, it could miss the final state. If it only polled the database, the stream would feel slow. So it does both.

The hub is the fast path: it sends live frames as they happen. A separate database poll is the safety net: it periodically checks whether the turn has reached a durable end state, meaning a final result saved in storage, or a parked state, meaning the turn is paused and can resume later. These two sources feed one queue, and the first ending frame wins.

The file also works out why a parked turn is paused. It may be because a seat was revoked, the workspace is out of spending room, or a spending cap blocks the turn. That reason is turned into a message for the live stream. The `HubTailer` class wraps all this behind a small interface, so surfaces can tail a turn without knowing the details of hubs, polling, billing, or seats.

#### Function details

##### `tail_frames`  (lines 35–67)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='', billing_url: str | None=None) -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: Streams frames for one turn until the turn either finishes or pauses. It is built to be safe for late connections, reconnects, and turns that finish on another event loop before the listener notices.

**Data flow**: It receives a hub, a turn id, an optional cursor called `since`, and an optional billing URL. It checks whether the hub can resume from that cursor; if not, it starts from the retained beginning. It starts one task that listens to the live hub, checks the database once immediately for an already-finished or parked turn, then starts a polling task if needed. Frames from both paths go into one queue. It yields each frame outward, and when a final or parked frame appears, it stops. When the caller closes the stream, it cancels the background tasks.

**Call relations**: This is the main worker behind `HubTailer.tail`. It calls `_pump` to collect live hub frames, `_read_status_frame` to catch a durable state immediately, and `_poll_status` to keep checking storage until an ending appears. It also asks the hub whether the reconnect cursor is still covered before subscribing.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, _read_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 70–79)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: Copies live frames from the hub into the shared queue used by the tail stream. It ignores bookkeeping frames that only say an arrival was queued, because those are not useful to the surface stream.

**Data flow**: It receives the hub, turn id, starting cursor, and the queue where frames should go. It subscribes to the hub and, for each real live frame, places the cursor and frame into the queue. If the hub subscription fails, it logs the failure instead of crashing the whole tail.

**Call relations**: `tail_frames` starts this as a background task. Its output is read by the main loop in `tail_frames`, alongside frames supplied by the polling path. It depends on `Hub.subscribe` for the live updates and reports unexpected failure through the logging system.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 82–94)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]], billing_url: str | None) -> None
```

**Purpose**: Periodically checks the database for the turn’s saved ending or paused state. This is the reliability backup for cases where the live hub does not deliver the ending to this listener.

**Data flow**: It receives a turn id, the shared frame queue, and an optional billing URL. Every configured interval, it asks `_read_status_frame` whether storage now shows a final or parked frame. Temporary read failures are logged and then ignored for that round. Once a frame is found, it puts that frame into the queue with an empty cursor and stops.

**Call relations**: `tail_frames` starts this after the immediate database check shows the turn is still active. It repeatedly delegates the actual database interpretation to `_read_status_frame`, then hands any discovered ending back to `tail_frames` through the same queue used by `_pump`.

*Call graph*: calls 1 internal fn (_read_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `_read_status_frame`  (lines 97–115)

```
async def _read_status_frame(turn_id: UUID, billing_url: str | None) -> LiveFrame | None
```

**Purpose**: Reads the turn’s durable status in a cancellation-safe way. Its job is to avoid leaving a database read half-handled when the surrounding live stream is being closed.

**Data flow**: It starts `turn_status_frame` as its own asynchronous task. While that task is running, it shields the read from outside cancellation, remembering if cancellation was requested. When the read finishes, it returns the frame or `None`. If an error happened, it raises that error, unless cancellation should be restored. If cancellation was requested after a successful read, it raises the cancellation so the caller still shuts down correctly.

**Call relations**: `tail_frames` uses this for the first immediate status check, and `_poll_status` uses it for repeated checks. It delegates the actual meaning of the stored turn row to `turn_status_frame`, while taking care of the awkward timing around task cancellation.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 2 (_poll_status, tail_frames); 2 external calls (ensure_future, shield).


##### `turn_status_frame`  (lines 118–175)

```
async def turn_status_frame(turn_id: UUID, billing_url: str | None=None) -> LiveFrame | None
```

**Purpose**: Turns the database state of a turn into the live-stream frame that should end the stream, if one exists. It returns a final frame for completed turns, a parked frame with a useful reason for paused turns, or nothing while the turn is still running.

**Data flow**: It opens a workspace database transaction and reads the turn row. If there is no row, or the turn is not parked and has no terminal record, it returns `None`. If a terminal record is stored, it validates that saved data and wraps it as a `Terminal` frame. If the turn is parked, it checks the likely reasons in order: whether the acting member still has a seat, whether the workspace has enough balance, and whether spending caps allow more work. It returns a `Parked` frame with the matching message, or a generic pause message if no specific blocker is found.

**Call relations**: _read_status_frame is the only caller here. This function reaches out to the database, seat admission checks, balance reading, turn authority calculation, and spending evaluation so the streaming layer can present the right end-of-stream frame without storing a stale park reason.

*Call graph*: called by 1 (_read_status_frame); 11 external calls (__init__, __init__, __init__, __init__, model_validate, select, workspace_tx, turn_authority, applicable_caps_absent, balance_refusal_message (+1 more)).


##### `HubTailer.tail`  (lines 188–191)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Provides the public tailing interface for one turn. It wraps `tail_frames` in an async closing context so callers can use the stream safely and have background work cleaned up when they leave.

**Data flow**: It receives a turn id and optional cursor. It passes its stored hub and billing URL into `tail_frames`, then wraps the resulting async generator with `aclosing`. The output is an async context manager that yields an async iterator of cursor-and-frame pairs.

**Call relations**: Surface code calls this instead of calling `tail_frames` directly. The wrapper matters because when the caller exits its context, the generator is closed, which triggers `tail_frames` to cancel the hub pump and polling task.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


##### `HubTailer.latest_activity`  (lines 193–194)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Asks the hub for the most recent known activity for a turn. This lets a surface check what has happened lately without opening the full tail stream.

**Data flow**: It receives a turn id and passes it to the hub held by this `HubTailer`. It returns the hub’s latest activity object, or `None` if the hub has no activity to report.

**Call relations**: This is a thin convenience method on the same tailing object used by surfaces. It delegates directly to `Hub.latest_activity`, keeping callers from needing direct access to the hub.


### `core/src/ufo/runtime/hub.py`

`io_transport` · `request handling / live streaming`

A running agent turn produces many small events: bits of text, tool activity, cost updates, delivered replies, child-agent progress, and finally an ending state. This file defines those event shapes and an in-memory hub that broadcasts them to any live viewer. Think of it like a radio station with a short rewind buffer: listeners get the live broadcast, and if they briefly disconnect, they can ask for everything after the last marker they heard.

The hub groups events by turn id. Each event gets a simple increasing cursor, like ticket number 42, 43, 44. Subscribers pass back their last cursor when they reconnect, and the hub replays buffered events after that point before sending new ones. The buffer is bounded, so memory cannot grow forever. If a subscriber is too slow, its oldest queued event is dropped rather than making the publisher wait; this keeps the running turn from stalling because one screen is lagging.

The file also has careful rules for ending streams. A Terminal frame means the turn is truly finished, while a Parked frame means it paused because of a spend cap and may resume later. Finished streams are cleaned up when safe. Parked streams keep enough cursor history to avoid reusing old cursor numbers when the same turn continues.

#### Function details

##### `Hub.publish`  (lines 151–151)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This is the interface promise for adding one live frame to a turn's stream. Code that only knows about the Hub interface can publish an update without caring whether the backing system is in-process memory, Redis, or something else.

**Data flow**: It receives a turn id and a frame describing something that happened. A concrete hub stores and broadcasts that frame, then returns the cursor that marks where the frame landed in the stream.

**Call relations**: Failure-handling code can call this interface when it needs to publish a final failed terminal frame. The actual work is supplied by an implementation such as InProcessHub.publish.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 153–153)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This is the interface promise for following a turn's live stream. A caller can optionally give its last cursor so it receives missed frames first, then continues with live frames.

**Data flow**: It receives a turn id and maybe a cursor. A concrete hub turns that into an asynchronous stream of cursor-and-frame pairs, first replaying old frames after the cursor and then yielding new ones as they arrive.

**Call relations**: The surface tailing code calls this when a CLI or web surface wants to watch a running turn. The implementation decides how replay and live delivery happen.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 155–155)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for asking whether a reconnect can safely resume from a cursor. It lets a viewer know whether the hub still has enough buffered history to avoid a gap.

**Data flow**: It receives a turn id and a cursor. A concrete hub checks its retained history and returns true if that cursor is still within the replayable range, or false if the caller should redraw or poll durable state instead.

**Call relations**: The live-tail orchestration asks this before deciding how to continue after a disconnect. Implementations such as InProcessHub.covers provide the actual answer.

*Call graph*: called by 1 (tail_frames).


##### `Hub.latest_activity`  (lines 157–157)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This is the interface promise for quickly asking what a turn appears to be doing right now. It is meant for status views that need one short activity line without opening a full live subscription.

**Data flow**: It receives a turn id. A concrete hub looks at recent retained frames and returns the newest Activity frame if one is still meaningful, otherwise it returns nothing.

**Call relations**: This method sits beside the streaming methods as a lightweight status-read path. The in-process version implements it by peeking into the replay buffer.


##### `_offer`  (lines 160–163)

```
def _offer(queue: asyncio.Queue[tuple[str, HubFrame]], item: tuple[str, HubFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber's queue without ever blocking the publisher. If the subscriber's queue is already full, it discards the oldest queued frame to make room.

**Data flow**: It receives a queue and a cursor-and-frame item. It checks whether the queue is full, removes one old item if needed, then adds the new item; it returns nothing but changes the queue contents.

**Call relations**: InProcessHub.publish schedules this helper on each subscriber's event loop. That keeps cross-thread delivery safe while preserving the rule that publishing must not wait for slow readers.


##### `InProcessHub._stream`  (lines 210–220)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This private helper finds the stored live-stream state for one turn, or creates it if this turn has not been seen recently. It centralizes the setup of the replay buffer, subscriber list, and cursor counter.

**Data flow**: It receives a turn id and reads the hub's internal maps. If a stream already exists, it returns it; otherwise it creates a new stream with a bounded ring buffer and starts its sequence number from the last remembered mark for that turn.

**Call relations**: Publishing and subscribing both call this while holding the hub's lock, so they agree on the same per-turn state. It uses the turn stream data object and a deque, which is a double-ended queue used here as a fixed-size rolling history.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 222–241)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This adds a new frame to a turn's stream, stores it for replay, and sends it to every current subscriber. It is designed so the running turn never blocks because a live viewer is slow or disconnected.

**Data flow**: It receives a turn id and a frame. Under a lock, it assigns the next cursor, appends the frame to the replay buffer, snapshots the current subscribers, and updates cleanup markers for terminal or parked endings. After releasing the lock, it schedules delivery to each subscriber queue and returns the cursor.

**Call relations**: Runtime code calls this whenever something live-worthy happens during a turn. It relies on InProcessHub._stream to get the per-turn state, and hands each subscriber delivery to _offer so full queues lose old frames instead of stopping publication.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 243–270)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This lets a caller watch one turn's stream, starting with buffered frames after a given cursor and then continuing with new frames. It is the main path used by live user interfaces.

**Data flow**: It receives a turn id and optional cursor. It creates a bounded queue for future frames, registers that queue as a subscriber, copies the replay frames newer than the cursor, yields those replayed frames, and then waits on the queue forever until the caller stops listening. When the caller leaves, it removes the subscriber and may clean up the stream.

**Call relations**: The surface tailing code uses this to feed live frames to a CLI or web client. It calls InProcessHub._stream while registering, and uses an asyncio queue tied to the current event loop so later publishes can safely deliver frames even from another thread.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 272–280)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still reaches back far enough for a given cursor. It protects clients from assuming they can resume smoothly when the needed history has already been dropped.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, there is no resumable position. Otherwise it looks up the stream and its earliest retained cursor, then returns whether the requested cursor is at or after that earliest retained point.

**Call relations**: Live-tail code asks this before deciding whether to resume from a cursor. Unlike subscribe, this does not create a stream; it only reports on history the hub already has.


##### `InProcessHub.latest_activity`  (lines 282–299)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This returns the newest recent Activity frame for a turn, giving a status view a quick one-line answer such as what tool step is currently running. It intentionally avoids scanning the whole large replay buffer.

**Data flow**: It receives a turn id. It looks up the stream, scans backward through only a limited number of recent buffered frames, and returns the first Activity frame it finds. If there is no stream or no recent activity frame, it returns nothing.

**Call relations**: Status readers can use this instead of opening a full subscription. It uses a bounded slice of the reversed buffer so repeated polling stays cheap, especially for turns that are mostly streaming text.

*Call graph*: 1 external calls (islice).


### Shared Surface Runtime
The common surface extension layer connects trusted member-facing and operator-facing transports to workspace lookup, admission, status, and reply delivery.

### `core/src/ufo/runtime/ext/surface.py`

`orchestration` · `cross-cutting: request handling, live streaming, background delivery, and admin/read views`

A surface is the place where a person talks to the system: a Slack thread, a web chat, iMessage, or a similar channel. This file defines the special, trusted doorway those surfaces use. Without it, outside messages could not safely become agent turns, replies might be lost or duplicated, and the web portal would have no common way to read conversations, files, agents, credentials, usage, and connection state.

The file does three big jobs. First, it defines small data shapes used by surfaces, such as conversation summaries, shared files, turn details, connector views, and delivery records. These are the “receipts” and “cards” the UI and chat integrations render.

Second, it defines `SurfaceContext`, the main toolbox handed to a surface after the workspace is known. Through it, a surface can link an external user to a member, create or find a conversation, admit a message, tail live turn events, stop a turn, read transcripts, upload files into a sandbox, mint download links, and inspect workspace configuration. It is deliberately powerful, so only trusted surface code receives it.

Third, it runs durable delivery. Live surfaces stream events directly, but durable surfaces need a background worker that retries until the final answer is posted. `WritebackPoller` sends terminal replies and files. `MidTurnReplyPoller` sends partial replies while a turn is still running. Both use database claims like numbered locker keys, so multiple workers do not deliver the same message at once.

#### Function details

##### `is_silence_sentinel`  (lines 239–247)

```
def is_silence_sentinel(answer: str) -> bool
```

**Purpose**: Checks whether a model’s final answer really means “say nothing.” This prevents the system from posting empty placeholder text as if it were a real reply.

**Data flow**: It receives an answer string, trims surrounding whitespace, compares the whole string against the accepted empty-response forms, and returns true or false.

**Call relations**: Delivery code can use this before posting a reply, so a durable surface can mark a silent turn as delivered without sending a visible message.


##### `mint_marker`  (lines 250–260)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to wrap one member message safely. The marker makes the wrapper unique so user text cannot accidentally close or imitate it.

**Data flow**: It takes no input, asks the secrets library for random bytes as hex text, and returns that marker string.

**Call relations**: Surfaces use the marker with `fence_member_message` when turning a raw outside message into the text the agent will read.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 263–276)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the prompt text for one incoming member message, keeping ambient context, the member’s own words, and attachment text in separate marked sections.

**Data flow**: It receives a marker, context text, message body, and attachment text, wraps the body and optional attachments in marker-named tags, and returns one combined string.

**Call relations**: This is part of the admission path: a surface prepares an inbound message before calling `SurfaceContext.admit`.


##### `inbox_name`  (lines 279–308)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an unsafe attachment filename from a surface into a safe workspace filename. It avoids path tricks, overly long names, empty names, and duplicates.

**Data flow**: It receives the raw name and a set of names already used in this batch, cleans and shortens the name, adds a number if needed, updates the used set, and returns the chosen name.

**Call relations**: Attachment download code in surfaces relies on this before writing files into a conversation workspace.

*Call graph*: 1 external calls (contained_leaf).


##### `member_message_text`  (lines 311–325)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts what the member actually said from the larger inbound text stored in a turn. This lets UIs show the human’s message instead of internal context wrappers.

**Data flow**: It receives stored inbound text, removes known engine context wrappers, looks for the unique member-message fence, and returns either the fenced message body or the original text.

**Call relations**: It is used by `conversation_name`, and indirectly by conversation creation, to name a conversation after the user’s words.

*Call graph*: called by 1 (conversation_name).


##### `MemberAdmitter.admit`  (lines 379–390)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Defines the interface for admitting a member message into the durable turn queue. A concrete implementation decides whether the message starts a turn, joins a running one, or is deduplicated.

**Data flow**: Inputs are conversation id, message text, optional idempotency key, context, speaker member id, intent/comment/config; output is an `Admitted` result describing the turn and whether a run opened.

**Call relations**: `SurfaceContext.admit` delegates to this protocol so surface code does not need to know the queue internals.


##### `TurnTailer.tail`  (lines 404–406)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how a live surface subscribes to live frames for one turn. It is the read side of live chat streaming.

**Data flow**: It receives a turn id and optional cursor, opens an async scoped stream, and yields cursor-plus-frame pairs until the turn ends.

**Call relations**: `SurfaceContext.tail` exposes this to web, debugger, and sample surfaces without letting them touch the hub directly.


##### `TurnTailer.latest_activity`  (lines 408–408)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Defines how to peek at the newest retained activity for a turn without subscribing to the whole stream.

**Data flow**: It receives a turn id and returns the latest activity object or none.

**Call relations**: `SurfaceContext.latest_activity` uses this for status screens that only need a snapshot.


##### `TurnStopper.stop`  (lines 418–418)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines how a surface asks core to stop a running turn in a specific conversation.

**Data flow**: It receives workspace, conversation, and turn ids, cancels or observes the turn state in the implementation, and returns what changed.

**Call relations**: `SurfaceContext.stop_turn` delegates here when a member presses stop.


##### `TurnStepSource.read`  (lines 424–424)

```
async def read(self, workflow_id: str) -> tuple['TurnStep', ...]
```

**Purpose**: Defines how to read recorded workflow steps for a turn. These steps are useful for debugger-style views.

**Data flow**: It receives a workflow id and returns a tuple of recorded step descriptions.

**Call relations**: `SurfaceContext.turn_steps` calls this after checking that the turn belongs to the workspace.


##### `SurfaceModel.model`  (lines 454–454)

```
def model(self) -> str
```

**Purpose**: Names the model available to a surface route for small background-style model calls.

**Data flow**: It reads no arguments and returns the model identifier.

**Call relations**: Surface routes can inspect this before making metered helper calls.


##### `SurfaceModel.turn`  (lines 456–456)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one model request for a surface route. It is meant for route-time helper work, not the main agent turn loop.

**Data flow**: It receives a model request, sends it through the configured model access layer, and returns a message.

**Call relations**: A surface uses this only when it can still answer gracefully if the call fails.


##### `shared_artifact_link`  (lines 552–563)

```
def shared_artifact_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed download link for a shared file, when public file delivery is configured.

**Data flow**: It receives signing secret, public base URL, workspace id, and artifact metadata; it returns a full URL or none if links cannot be minted.

**Call relations**: `SurfaceContext.artifact_link` wraps this for surfaces rendering shared files.

*Call graph*: called by 1 (artifact_link); 3 external calls (now, artifact_url_expiry, mint_artifact_url).


##### `shared_artifact_preview_link`  (lines 566–588)

```
def shared_artifact_preview_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a safe preview image link for an artifact if the file or its stored preview is actually a raster image.

**Data flow**: It receives signing settings, workspace id, and artifact metadata; it verifies media type and size expectations, then returns a preview URL or none.

**Call relations**: `SurfaceContext.artifact_preview_link` uses this for web previews.

*Call graph*: called by 1 (artifact_preview_link); 2 external calls (mint_image_preview_url, raster_image_media_type).


##### `_scheduled_runs_query`  (lines 591–627)

```
def _scheduled_runs_query(workspace_id: UUID, member_id: UUID, agent_id: UUID | None) -> sa.Select[Any]
```

**Purpose**: Builds the database query for scheduled turns a member may read. Scheduled turns are turns that fired on their own, such as timers or cron jobs.

**Data flow**: It receives workspace, member, and optional agent ids, constructs a permission-filtered SQL query, and returns that query object.

**Call relations**: `scheduled_runs` uses it as the base before adding feed-specific filters.

*Call graph*: called by 1 (scheduled_runs); 3 external calls (or_, select, readable_audiences).


##### `scheduled_runs`  (lines 630–706)

```
async def scheduled_runs(workspace_id: UUID, member_id: UUID, *, limit: int, agent_id: UUID | None=None, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Returns a feed of completed scheduled runs visible to a member, including their final text and shared files.

**Data flow**: It receives workspace/member plus limits and optional filters, reads turn and artifact rows, resolves conversation sources, and returns `ScheduledRun` objects.

**Call relations**: Portal surfaces call this kind of projection to show what autonomous work reported back.

*Call graph*: calls 1 internal fn (_scheduled_runs_query); 6 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx).


##### `conversation_name`  (lines 755–760)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Chooses the initial title for a conversation from the member’s own opening words.

**Data flow**: It receives inbound text, extracts the member text, trims and caps it, and returns the title.

**Call relations**: Conversation creation paths use this so ambient context does not become the conversation title.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 763–780)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Updates a conversation title when a surface has a better name for it.

**Data flow**: It receives workspace id, conversation id, and title, trims/caps the title, and updates the row if the result is not blank.

**Call relations**: `SurfaceContext.retitle_conversation` delegates here for web and Slack naming updates.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `summarize_conversation_title`  (lines 783–803)

```
async def summarize_conversation_title(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores a generated title summary and records that title summarization has already run.

**Data flow**: It receives workspace id, conversation id, and proposed title, writes the title if nonblank, and marks the row summarized.

**Call relations**: A title summarization job can call this to avoid paying for the same summary repeatedly.

*Call graph*: 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 871–872)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes agent update timestamps so they always include a timezone.

**Data flow**: It receives a datetime and returns it unchanged if timezone-aware, otherwise marks it as UTC.

**Call relations**: Pydantic calls this when building `AgentDetail` responses.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 920–921)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes connection timestamps to timezone-aware UTC values.

**Data flow**: It receives a datetime and returns a timezone-aware version.

**Call relations**: Pydantic calls this when building connection rows for the portal.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 947–948)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes pooled connection timestamps to timezone-aware UTC values.

**Data flow**: It receives a datetime and returns it with UTC if needed.

**Call relations**: Pydantic calls this for connection library rows.

*Call graph*: 1 external calls (replace).


##### `_binding_fields`  (lines 982–1010)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts the editable identity fields of a connector-backed source row. This lets portal actions submit the same source identity the object system expects.

**Data flow**: It receives backend name and stored config, validates connector config if possible, and returns display/apply fields or all-none fields.

**Call relations**: `SurfaceContext.list_sources` uses this to build `SourceView` rows.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 1042–1043)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes source sync timestamps to timezone-aware UTC values.

**Data flow**: It receives a datetime and returns a UTC-aware datetime.

**Call relations**: Pydantic calls this when source rows are created.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 1061–1064)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation timestamps, while allowing missing last-activity time.

**Data flow**: It receives a datetime or none, returns none unchanged, or adds UTC if needed.

**Call relations**: Pydantic applies it to conversation list responses.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 1077–1138)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged and opened another member’s private transcript. This creates the temporary grant that makes the transcript readable.

**Data flow**: It receives workspace, conversation, agent, and reader member ids; verifies the conversation is a private member audience, writes an access row, logs the disclosure, and returns the reader/subject emails or none.

**Call relations**: Portal prepared-intent flows call this before serving private transcript content to an admin.

*Call graph*: 9 external calls (__init__, now, insert, select, workspace_tx, log, audience_member, parse_audience, uuid4).


##### `ConversationDirectory.list`  (lines 1196–1329)

```
async def list(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, portal: bool | None=None, conversation_id: UUID | None=None, participation: Literal['mine',
```

**Purpose**: Lists conversations for one agent with the right visibility, search, surface, and participation filters.

**Data flow**: It receives agent/member/admin filters, queries conversation activity, fetches readable source links and speakers, and returns listed conversation objects.

**Call relations**: `SurfaceContext.list_agent_conversations` and other portal reads use this shared directory so all conversation listings behave the same.

*Call graph*: calls 6 internal fn (_matches, _member_admitted, _others, _participated, sources, speakers); 10 external calls (__init__, __init__, not_, or_, select, workspace_tx, audience_member, conversation_audience, parse_audience, readable_audiences).


##### `ConversationDirectory.sources`  (lines 1331–1368)

```
async def sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the original source link or label for each listed conversation.

**Data flow**: It receives conversation ids, reads the first turn context for each, and returns a map from conversation id to source string or none.

**Call relations**: `ConversationDirectory.list`, scheduled run feeds, and artifact listings use it to point back to the originating surface.

*Call graph*: called by 1 (list); 4 external calls (model_validate, and_, select, workspace_tx).


##### `ConversationDirectory.speakers`  (lines 1370–1426)

```
async def speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Lists the first few people who spoke in each conversation.

**Data flow**: It receives conversation ids, reads member-attributed turns in first-spoken order, and returns email/sender summaries grouped by conversation.

**Call relations**: `ConversationDirectory.list` uses it only for conversations whose content the viewer may read.

*Call graph*: called by 1 (list); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `ConversationDirectory._member_admitted`  (lines 1428–1441)

```
def _member_admitted(self) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations that contain a real member-admitted turn.

**Data flow**: It creates an SQL exists condition tied to the current conversation row.

**Call relations**: `ConversationDirectory.list` uses it to separate human conversations from machine-only lanes.

*Call graph*: called by 1 (list); 2 external calls (literal, select).


##### `ConversationDirectory._spoken`  (lines 1443–1464)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for whether any member, or a specific member, has spoken in a conversation.

**Data flow**: It receives an optional member id and returns an SQL exists condition over turn speaker rows.

**Call relations**: `_participated` and `_others` compose this condition for portal rail filters.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `ConversationDirectory._participated`  (lines 1466–1474)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations the member belongs to or has spoken in.

**Data flow**: It receives a member id and returns an SQL condition combining bound ownership and spoken turns.

**Call relations**: `ConversationDirectory.list` uses it for the “mine” participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 1 external calls (or_).


##### `ConversationDirectory._others`  (lines 1476–1488)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations with member speech where this member did not participate.

**Data flow**: It receives a member id and returns an SQL condition excluding their bound or spoken conversations.

**Call relations**: `ConversationDirectory.list` uses it for the “others” participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 2 external calls (and_, not_).


##### `ConversationDirectory._matches`  (lines 1490–1519)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a safe search condition for conversation rows. It only searches private content when the viewer may read it.

**Data flow**: It receives search text and member id, creates SQL checks against surface labels, owner email, readable titles, and readable speaker emails.

**Call relations**: `ConversationDirectory.list` applies it before limiting results, so matching rows are not accidentally cut off.

*Call graph*: called by 1 (list); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `LedgerEntry._aware_utc`  (lines 1533–1534)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes ledger timestamps to timezone-aware UTC.

**Data flow**: It receives a datetime and returns it with UTC if missing.

**Call relations**: Pydantic calls it when building turn accounting details.

*Call graph*: 1 external calls (replace).


##### `TurnStep._aware_utc`  (lines 1555–1558)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes optional workflow step timestamps.

**Data flow**: It receives a datetime or none and returns none, an already-aware value, or a UTC-marked value.

**Call relations**: Pydantic calls it when creating debugger step records.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 1614–1615)

```
def _fulfilled_marker_key(request_id: UUID, slot: str) -> str
```

**Purpose**: Builds the blob-store key used to mark a private credential prompt as fulfilled.

**Data flow**: It receives request id and slot name and returns a stable marker path string.

**Call relations**: Credential prompt checking and fulfillment both use the same key so prompts are not shown twice.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request).


##### `_credential_request_id`  (lines 1618–1621)

```
def _credential_request_id(state: CredentialRequestState, sealed: str) -> UUID
```

**Purpose**: Finds or derives a durable id for a sealed credential request.

**Data flow**: It receives request state and sealed text, returns the explicit request id if present, otherwise hashes the seal into a UUID.

**Call relations**: Credential renewal, pending checks, and fulfillment use this to refer to the same request.

*Call graph*: called by 3 (credential_prompt_pending, fulfill_credential_request, renew_credential_request); 2 external calls (sha256, UUID).


##### `_main_agent`  (lines 1624–1637)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Looks up the workspace’s main agent. This is the fallback agent for surfaces that have no specific binding.

**Data flow**: It receives a workspace id, reads the agent table, and returns the main agent id or raises if the workspace is malformed.

**Call relations**: Surface installation binding and surface-agent lookup call this.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 1640–1679)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str, *, routes_ingress: bool) -> None
```

**Purpose**: Creates or updates the mapping from a workspace and surface to an external installation id.

**Data flow**: It receives workspace, surface, installation id, and routing mode; upserts the installation row; on uniqueness conflict raises a surface-installation conflict.

**Call relations**: Both `SurfaceContext.bind_installation` and manifest-scoped `SurfaceInstallationAccess.bind` use this one writer.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.fleet_blob`  (lines 1753–1756)

```
def fleet_blob(self) -> FleetBlobStore
```

**Purpose**: Provides a deploy-wide blob-store view for shared static assets.

**Data flow**: It reads the current workspace blob backend and returns a fleet blob store using the same backend.

**Call relations**: Surface code can use this when it needs fleet assets rather than workspace-private blobs.

*Call graph*: 1 external calls (__init__).


##### `SurfaceContext.conversation_slots`  (lines 1759–1761)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Returns the extension-provided conversation slots registered for this deployment.

**Data flow**: It reads the context’s stored slot tuple and returns it unchanged.

**Call relations**: Web conversation slot routes use this to know what extra panels can be read.


##### `SurfaceContext.read_conversation_slot`  (lines 1763–1768)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Reads one conversation slot under the conversation’s agent identity.

**Data flow**: It receives a bound slot and slot context, binds the agent id for the duration, calls the provider, and returns its payload.

**Call relations**: The web surface calls this when rendering a single conversation slot.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1770–1775)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Asks a conversation slot provider to summarize its content under the right agent identity.

**Data flow**: It receives a bound slot and context, binds the agent, calls the provider’s summarize method, and returns an optional integer result.

**Call relations**: The web surface uses it when refreshing conversation slot summaries.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.runtime`  (lines 1778–1780)

```
def runtime(self) -> RuntimeIdentity | None
```

**Purpose**: Returns the runtime identity configured at startup, if one exists.

**Data flow**: It reads the stored runtime identity and returns it or none.

**Call relations**: Surfaces can display or branch on runtime information without constructing it themselves.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1783–1786)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Tells whether this deployment allows sandbox internet access at all.

**Data flow**: It returns the stored boolean deployment ceiling.

**Call relations**: Portal settings use it to decide whether an agent-level internet setting can be offered.


##### `SurfaceContext.system_skill_bundle`  (lines 1789–1791)

```
def system_skill_bundle(self) -> SystemSkillBundle
```

**Purpose**: Returns the immutable bundle of deploy-provided system skills.

**Data flow**: It reads and returns the prebuilt skill bundle.

**Call relations**: Turn execution and surfaces can share the same boot-time skill set.


##### `SurfaceContext.models`  (lines 1794–1798)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Lists the model ids this deployment can run.

**Data flow**: It returns the stored tuple of model names.

**Call relations**: Portal settings and runtime config validation use this closed list.


##### `SurfaceContext.validate_runtime_config`  (lines 1800–1808)

```
def validate_runtime_config(self, runtime_config: TurnRuntimeConfig) -> None
```

**Purpose**: Rejects turn runtime settings this deployment cannot support.

**Data flow**: It receives a runtime config, checks model membership and whether environment documents are allowed, and either returns normally or raises.

**Call relations**: The UFO surface calls this before admitting turns with custom runtime settings.

*Call graph*: called by 1 (_runtime_config).


##### `SurfaceContext.store_environment_document`  (lines 1810–1816)

```
async def store_environment_document(self, body: bytes) -> str
```

**Purpose**: Stores an environment document in the workspace blob store when this deployment allows it.

**Data flow**: It receives bytes, checks that a store function exists, writes through that function, and returns the digest.

**Call relations**: The UFO surface uses it for environment upload endpoints.

*Call graph*: called by 1 (store_environment).


##### `SurfaceContext.store_environment_file`  (lines 1818–1823)

```
async def store_environment_file(self, body: bytes) -> str
```

**Purpose**: Stores a file referenced by an environment document.

**Data flow**: It receives bytes, checks deployment support, stores the file through the configured function, and returns its digest.

**Call relations**: The UFO surface calls it for environment file uploads.

*Call graph*: called by 1 (store_environment_file).


##### `SurfaceContext.sandbox_sizes`  (lines 1826–1829)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Lists sandbox sizes available in this deployment.

**Data flow**: It returns the stored tuple of size names.

**Call relations**: Portal settings use it to decide whether to show sandbox sizing controls.


##### `SurfaceContext.credential`  (lines 1831–1834)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential slot for a trusted surface.

**Data flow**: It receives a slot name, requires a credential store, fetches the value for this workspace, and returns it.

**Call relations**: Slack surface helpers use it for bot tokens, signing secrets, and posting credentials.

*Call graph*: called by 8 (_bot_token, _channel_origin, _ctx_signing_secret, _post_ephemeral, _to_inbound, attach, post, speak).


##### `SurfaceContext.put_member_credential`  (lines 1836–1842)

```
async def put_member_credential(self, member_id: UUID, slot: str, value: str) -> None
```

**Purpose**: Stores a member-specific credential value, such as a user’s own provider key.

**Data flow**: It receives member id, slot, and value, rewrites the slot as member-scoped, and stores the value.

**Call relations**: Web account connection flows call this after authenticating the member.

*Call graph*: called by 2 (anthropic_code, openai_device_poll); 1 external calls (member_slot).


##### `SurfaceContext.member_credential_stored`  (lines 1844–1853)

```
async def member_credential_stored(self, member_id: UUID, slot: str) -> bool
```

**Purpose**: Checks whether a member has stored their own credential for a slot.

**Data flow**: It receives member id and slot, tries to read the member-scoped credential, and returns true if found.

**Call relations**: The web workspace accounts screen uses it to show connection state.

*Call graph*: called by 1 (workspace_accounts); 1 external calls (member_slot).


##### `SurfaceContext.clear_member_credential`  (lines 1855–1861)

```
async def clear_member_credential(self, member_id: UUID, slot: str) -> None
```

**Purpose**: Deletes one member’s stored credential for a slot.

**Data flow**: It receives member id and slot, converts to the member-scoped slot, and clears it from the store.

**Call relations**: The web account disconnect route calls it.

*Call graph*: called by 1 (account_disconnect); 1 external calls (member_slot).


##### `SurfaceContext.member_holds_own_model_key`  (lines 1863–1876)

```
async def member_holds_own_model_key(self, member_id: UUID) -> bool
```

**Purpose**: Checks whether a member has any credential slot that counts as their own model-provider key.

**Data flow**: It receives a member id, scans the configured member-routed slots, and returns true on the first stored credential.

**Call relations**: The web first-run screen uses this to align UI prompts with runtime capability.

*Call graph*: called by 1 (workspace_first_run); 1 external calls (member_slot).


##### `SurfaceContext.credential_prompt_pending`  (lines 1878–1901)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs a value for one slot.

**Data flow**: It opens the sealed request, verifies workspace and slot, checks fulfillment tables and marker blobs, and returns whether it is still pending.

**Call relations**: The web surface uses it when deciding which credential prompts to show.

*Call graph*: calls 2 internal fn (_credential_request_id, _fulfilled_marker_key); called by 1 (_pending_prompts); 3 external calls (select, workspace_tx, open_credential_request).


##### `SurfaceContext.renew_credential_request`  (lines 1903–1929)

```
async def renew_credential_request(self, sealed: str, member_id: UUID) -> str | None
```

**Purpose**: Refreshes a still-valid credential request for an authenticated member.

**Data flow**: It opens the sealed request with renewal rules, verifies workspace/member/admin conditions, stamps request id and issue time, and returns a new seal or none.

**Call relations**: The web surface uses it during prompt reloads.

*Call graph*: calls 1 internal fn (_credential_request_id); called by 1 (_pending_prompts); 5 external calls (now, workspace_tx, open_credential_request, seal_credential_request, member_is_admin).


##### `SurfaceContext.open_credential_authorization`  (lines 1931–1939)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential handoff and returns its claims.

**Data flow**: It receives sealed text, requires a credential store, verifies and decodes the seal, and returns request state or raises if invalid.

**Call relations**: Slack OAuth callback code uses this to finish credential authorization.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 1941–1990)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Verifies and stores the value for a requested credential slot.

**Data flow**: It receives seal, slot, value, and member id; validates workspace, member, slot declaration, and duplicate markers; stores the value; and writes a fulfillment marker for private prompts.

**Call relations**: Slack, UFO, and web fulfillment routes call this after collecting a secret.

*Call graph*: calls 2 internal fn (_credential_request_id, _fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 5 external calls (__init__, now, dumps, warn, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 1992–2000)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation, such as a Slack team, to the workspace.

**Data flow**: It receives an installation id and delegates to the shared installation upsert helper with ingress routing enabled.

**Call relations**: Slack OAuth callbacks call it after installation succeeds.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.address_claim`  (lines 2002–2027)

```
async def address_claim(self, address: str) -> AddressClaim | None
```

**Purpose**: Reads this workspace’s claim on an addressed-surface address, such as a phone or sender address.

**Data flow**: It receives an address, reads the surface-address row, normalizes expiry time, and returns an `AddressClaim` or none.

**Call relations**: The iMessage surface uses it before admitting an addressed inbound message.

*Call graph*: called by 1 (_admit_message); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.confirm_address`  (lines 2029–2042)

```
async def confirm_address(self, address: str, proved_by: str) -> None
```

**Purpose**: Marks an address reservation as proved and permanently linked.

**Data flow**: It receives address and proof id, clears expiry, stores the proof id, and updates the row.

**Call relations**: The iMessage proof flow calls it when a sender proves ownership.

*Call graph*: called by 1 (_prove); 2 external calls (update, workspace_tx).


##### `SurfaceContext.release_address`  (lines 2044–2053)

```
async def release_address(self, address: str) -> None
```

**Purpose**: Removes this workspace’s claim on an address.

**Data flow**: It receives an address and deletes the matching surface-address row.

**Call relations**: The iMessage proof flow calls it when a claim should be abandoned.

*Call graph*: called by 1 (_prove); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.public_base_url`  (lines 2056–2059)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deployment’s public base URL, if configured.

**Data flow**: It reads and returns the stored base URL or none.

**Call relations**: Surface handlers use this when rendering callback URLs and public links.


##### `SurfaceContext.cookie_secure`  (lines 2062–2066)

```
def cookie_secure(self) -> bool
```

**Purpose**: Decides whether session cookies should be marked Secure for this deployment.

**Data flow**: It reads the public URL scheme and returns the cookie security decision.

**Call relations**: Browser surfaces use it when setting cookies.

*Call graph*: 2 external calls (cookie_secure, urlsplit).


##### `SurfaceContext.home_url`  (lines 2068–2078)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link into the browser portal when this deployment has a home surface.

**Data flow**: It receives an optional fragment, combines public base URL, home surface name, and fragment, and returns a URL or none.

**Call relations**: Slack, iMessage, and sites surfaces use it when directing members to the portal.

*Call graph*: called by 3 (_terminal_text, _into_the_portal, _reply_with_oversize_links).


##### `SurfaceContext.shared_artifacts`  (lines 2080–2118)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Lists files shared by a turn in stable share order.

**Data flow**: It receives a turn id, reads shared artifact rows for this workspace, and returns `SharedArtifact` objects.

**Call relations**: Live surfaces read this directly; durable writeback builds similar records for attachment delivery.

*Call graph*: called by 2 (shared_files, _events); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 2120–2128)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed download link for a shared artifact in this workspace.

**Data flow**: It receives artifact metadata and passes context signing settings to `shared_artifact_link`.

**Call relations**: Slack, iMessage, UFO, and web surfaces use it when they cannot upload the file inline.

*Call graph*: calls 1 internal fn (shared_artifact_link); called by 5 (_terminal_text, _oversize_link_line, shared_files, _file_payload, _project_slot_context).


##### `SurfaceContext.artifact_preview_link`  (lines 2130–2140)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview-image link for an artifact when safe and possible.

**Data flow**: It receives artifact metadata and delegates to `shared_artifact_preview_link` with workspace signing settings.

**Call relations**: The web surface uses it for file preview payloads.

*Call graph*: calls 1 internal fn (shared_artifact_preview_link); called by 2 (_file_payload, _project_slot_context).


##### `SurfaceContext.ingress_url`  (lines 2142–2188)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest: str | None=None) -> str | None
```

**Purpose**: Creates a signed URL that opens a sandbox-hosted site for a conversation.

**Data flow**: It receives conversation, port, entry path, and optional framing/shipped-app claims, then returns a minted ingress URL or none.

**Call relations**: The sites surface uses it to serve conversation apps and shipped app frames.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (mint_ingress_view_url).


##### `SurfaceContext._identity_member`  (lines 2190–2203)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up the member linked to an external surface user id.

**Data flow**: It receives a surface name and external id, reads the identity table, and returns the member id or none.

**Call relations**: `linked_member` and `adopt_identity` use it as their common lookup.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 2205–2206)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the member linked to this surface’s external user id.

**Data flow**: It receives an external id and calls `_identity_member` for the current surface.

**Call relations**: Many surfaces call it during authentication or inbound user resolution.

*Call graph*: calls 1 internal fn (_identity_member); called by 7 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, _authenticated_member, _authenticate).


##### `SurfaceContext.member_has_access`  (lines 2208–2210)

```
async def member_has_access(self, member_id: UUID) -> bool
```

**Purpose**: Checks whether a member currently has a seat/access in the workspace.

**Data flow**: It receives a member id, asks the seats subsystem inside a transaction, and returns true or false.

**Call relations**: Surface authentication code uses it after identity lookup.

*Call graph*: called by 4 (_viewer, _folds_into_live_turn, _authenticated_member, _authenticate); 3 external calls (__init__, __init__, workspace_tx).


##### `SurfaceContext.is_operator_workspace`  (lines 2212–2219)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the workspace belongs to the fleet operator domain.

**Data flow**: It reads the workspace domain and compares it to the operator domain constant.

**Call relations**: Surfaces use it to gate operator-only debugging or accounting displays.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 2221–2244)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to the member already known by another surface.

**Data flow**: It receives peer surface and external id, finds the peer member, inserts this surface identity if possible, and returns the member id or none.

**Call relations**: Sample live admission uses it to carry identity across surfaces.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 2246–2268)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing workspace member by email.

**Data flow**: It receives external id and email, finds the oldest matching member case-insensitively, and delegates to `link_member_id`.

**Call relations**: Authentication and join flows call it before considering member creation.

*Call graph*: calls 1 internal fn (link_member_id); called by 5 (join_member, _surface_ingest, _viewer, _authenticated_member, _authenticate); 2 external calls (select, workspace_tx).


##### `SurfaceContext.link_member_id`  (lines 2270–2299)

```
async def link_member_id(self, external_id: str, member_id: UUID) -> UUID | None
```

**Purpose**: Links this surface’s external id to a specific member id after the surface has proved that member.

**Data flow**: It verifies the member exists in the workspace, inserts an identity row, tolerates race conflicts, and returns the member id or none.

**Call relations**: `link_member` delegates here.

*Call graph*: called by 1 (link_member); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 2301–2318)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links an external id by email, creating a new member when the email domain matches the workspace domain.

**Data flow**: It tries to link an existing member, checks domain rules, creates a member if allowed, and links again.

**Call relations**: Slack member resolution uses it for channel-verified workspace-domain users.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 2320–2330)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the query that finds a conversation by this surface’s queue key.

**Data flow**: It receives a queue key and returns a SQL select scoped to workspace and surface.

**Call relations**: Conversation lookup, creation, and terminal op body reads reuse it.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 2332–2338)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for a surface queue key without creating one.

**Data flow**: It receives a queue key, runs the shared lookup, and returns the conversation id or none.

**Call relations**: Slack and UFO routes use it when they need to inspect existing participation or files.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 4 (_handle_answer_submit, _participating_conversation, workspace_file, workspace_listing); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 2340–2353)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the agent permanently bound to a conversation.

**Data flow**: It receives a conversation id, reads the conversation row scoped to this workspace, and returns agent id or none.

**Call relations**: The web surface uses it when resolving opaque chat links.

*Call graph*: called by 1 (_resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 2355–2358)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation in this workspace.

**Data flow**: It receives conversation id and title and delegates to the module-level retitle helper.

**Call relations**: Slack and web routes call it after opening or updating a conversation.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 4 (_admit_inbound, submit_action, submit_intent, _open_conversation).


##### `SurfaceContext.conversation_for`  (lines 2360–2454)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Finds or creates the conversation for a surface queue key, audience, and optional agent.

**Data flow**: It receives queue key, audience, optional agent/id/label; it narrows existing audiences safely or inserts a new conversation bound to an agent, handling creation races.

**Call relations**: All main admission paths call this before `admit` so messages land in the right durable conversation.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, _handle_answer_submit, channel, workspace_upload, submit_action, submit_intent, _open_conversation (+1 more)); 9 external calls (insert, select, update, workspace_tx, log, audience_member, narrow_audience, parse_audience, uuid4).


##### `SurfaceContext._surface_agent`  (lines 2456–2468)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Finds the agent bound to this surface installation, falling back to the workspace main agent.

**Data flow**: It reads the surface installation row and returns its agent id, or calls `_main_agent` if none exists.

**Call relations**: Conversation creation and ambient reply fallback model lookup use it.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (_surface_agent_model, conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 2470–2530)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Decides whether an unaddressed ambient message should found a turn.

**Data flow**: It receives the message and recent history, calls the ambient classifier with a timeout, retries on policy refusal when possible, logs the result, and returns whether to reply.

**Call relations**: Slack and iMessage call it before admitting ambient traffic.

*Call graph*: calls 1 internal fn (_surface_agent_model); called by 2 (_admit_message, _ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext._surface_agent_model`  (lines 2532–2542)

```
async def _surface_agent_model(self) -> str
```

**Purpose**: Reads the model configured on the agent bound to this surface.

**Data flow**: It finds the surface agent, reads the agent model column, and returns the model name.

**Call relations**: `ambient_reply_wanted` uses it for fallback classification.

*Call graph*: calls 1 internal fn (_surface_agent); called by 1 (ambient_reply_wanted); 2 external calls (select, workspace_tx).


##### `SurfaceContext.admit`  (lines 2544–2579)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, comment:
```

**Purpose**: Admits a prepared inbound message into the turn system as a member or member-authorized intent.

**Data flow**: It receives conversation, body, idempotency key, context, speaker, intent/comment/config and delegates to the injected member admitter, returning an `Admitted` result.

**Call relations**: Surface routes call this after resolving identity and conversation.

*Call graph*: called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, _handle_answer_submit, _channel_message, _send, submit_action, submit_intent, _admit_chat (+1 more)).


##### `SurfaceContext.connect_url`  (lines 2581–2587)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates a provider connection authorization URL for a member from a turn’s connect request.

**Data flow**: It receives turn and member ids, finds the installed connect flow, and returns an authorization URL or raises a request error if unavailable.

**Call relations**: Slack, iMessage, and web surfaces call it when rendering connect controls.

*Call graph*: called by 3 (_terminal_text, _handle_connect_click, connect_handoff); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.held_accounts`  (lines 2589–2609)

```
async def held_accounts(self, owner_member_id: UUID) -> dict[str, str]
```

**Purpose**: Lists the provider accounts owned by one member, keyed by provider.

**Data flow**: It receives owner member id, reads connection rows, and returns provider-to-label/id mapping.

**Call relations**: The web surface uses it to draw connect controls.

*Call graph*: called by 1 (_connect_controls); 2 external calls (select, workspace_tx).


##### `SurfaceContext.connect_available`  (lines 2611–2618)

```
def connect_available(self) -> bool
```

**Purpose**: Reports whether the deployment has the connect flow configured.

**Data flow**: It attempts to load the installed connect flow and returns false if unavailable.

**Call relations**: The web surface uses it before showing connect buttons or labels.

*Call graph*: called by 3 (_connect_controls, _events, _provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connect_label`  (lines 2620–2622)

```
def connect_label(self, provider: str) -> str
```

**Purpose**: Returns the human-readable label for a connection provider.

**Data flow**: It receives a provider id, asks the installed connect flow for its label, and returns it.

**Call relations**: The web provider-label helper calls it.

*Call graph*: called by 1 (_provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connector_catalog`  (lines 2624–2626)

```
async def connector_catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads a page of connectable providers from the connector registry.

**Data flow**: It receives search text, limit, and cursor, passes them to the registry, and returns a catalog page.

**Call relations**: The web connector catalog route calls it.

*Call graph*: called by 1 (connector_catalog).


##### `SurfaceContext.admitted_body`  (lines 2628–2653)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Finds the message body that was admitted under an idempotency key.

**Data flow**: It receives a key, checks both turn rows and queued inbound-message rows, and returns the stored body or none.

**Call relations**: Slack and web use it to reconcile duplicate clicks or admissions.

*Call graph*: called by 3 (_handle_answer_submit, _unseen_tail, _admit_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 2655–2669)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member who owns the conversation containing a turn.

**Data flow**: It receives a turn id, joins turn to conversation, and returns the conversation member id or none.

**Call relations**: Live surfaces use it before allowing a member to tail a turn.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 2671–2677)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops a running turn in an already-authorized conversation.

**Data flow**: It receives conversation and turn ids, delegates to the injected stopper with workspace id, and returns what happened.

**Call relations**: UFO and web stop routes call it.

*Call graph*: called by 2 (_channel_stop, _stop_chat).


##### `SurfaceContext.retract_arrival`  (lines 2679–2697)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Deletes a member’s queued message if no turn has consumed it yet.

**Data flow**: It receives conversation, arrival, and member ids, deletes only matching unconsumed rows, and returns whether one row was removed.

**Call relations**: The UFO unsend route uses it.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 2699–2716)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has ended according to the database.

**Data flow**: It receives a turn id, reads its status, and returns true for missing or terminal turns.

**Call relations**: The UFO channel message path uses it to avoid posting progress after a final answer.

*Call graph*: called by 1 (_channel_message); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 2718–2735)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the newest turn in a conversation.

**Data flow**: It receives a conversation id, reads the highest sequence turn, and returns its id or none.

**Call relations**: Slack, UFO, and web routes use it when resuming or rendering conversation state.

*Call graph*: called by 6 (_participating_conversation, _channel_message, _channel_op_reply, _channel_stop, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.absorbing_turn`  (lines 2737–2781)

```
async def absorbing_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Checks whether a new message would fold into an existing live turn instead of starting a new one.

**Data flow**: It receives a conversation id, reads the oldest nonterminal turn, checks spend and balance gates, and returns that turn id only if folding is allowed.

**Call relations**: Slack uses it before deciding whether ambient reply classification should run.

*Call graph*: called by 1 (_folds_into_live_turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.tail`  (lines 2783–2789)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live frame stream for a turn.

**Data flow**: It receives a turn id and optional cursor and returns the injected tailer’s async stream context.

**Call relations**: Web, debugger, sample, and panel routes use it for live updates.

*Call graph*: called by 6 (_events, _surface_frames, _intent_result, submit_action, _events, object_write).


##### `SurfaceContext.latest_activity`  (lines 2791–2795)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Returns the newest retained activity for a turn.

**Data flow**: It receives a turn id and delegates to the injected tailer.

**Call relations**: The web agent status route uses it.

*Call graph*: called by 1 (agents_status).


##### `SurfaceContext.spend_rollup`  (lines 2797–2800)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace-wide usage and spending for a time window.

**Data flow**: It receives an optional window length, reads through `SpendRollup`, and returns a spend report.

**Call relations**: Sample and web usage views call it.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 2802–2817)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded file into a conversation’s sandbox workspace before the turn runs.

**Data flow**: It receives conversation id, relative path, and byte chunks; accumulates within a maximum size; then writes bytes through the sandbox carrier.

**Call relations**: Slack, iMessage, sample, UFO, and web upload paths use it.

*Call graph*: called by 5 (_downloaded_files, _surface_ingest, _download_files, workspace_upload, _deliver_uploads).


##### `SurfaceContext.render_preview`  (lines 2819–2878)

```
async def render_preview(self, kind: str, data: bytes, start_page: int=1, pages: int=1) -> PreviewRender | None
```

**Purpose**: Asks the preview service to render document bytes into preview PNG pages.

**Data flow**: It receives kind, data, start page, and page count; posts to the preview service; accepts PNG or ZIP responses; and returns `PreviewRender` or none.

**Call relations**: The web preview endpoint calls it before showing upload previews.

*Call graph*: called by 1 (preview); 5 external calls (__init__, AsyncClient, BytesIO, dumps, ZipFile).


##### `SurfaceContext.list_agents`  (lines 2880–2917)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists live agents in the workspace, main agent first.

**Data flow**: It reads non-archived agent rows and returns `AgentSummary` objects.

**Call relations**: Web and sites surfaces use it for agent pickers, shipped frames, and audience checks.

*Call graph*: called by 5 (_shipped_frame, frame, web_audience, _created_apps, _subagent_nodes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_archived_agents`  (lines 2919–2950)

```
async def list_archived_agents(self) -> tuple[ArchivedAgent, ...]
```

**Purpose**: Lists archived agents, newest archived first.

**Data flow**: It reads archived agent rows, choosing archived display names where present, and returns `ArchivedAgent` objects.

**Call relations**: The web agents index calls it for restore views.

*Call graph*: called by 1 (agents_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_extension_agent_ids`  (lines 2952–2967)

```
async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]
```

**Purpose**: Finds agents that have private extension conversations for a member.

**Data flow**: It receives a member id, queries distinct agent ids from matching extension conversations, and returns a frozen set.

**Call relations**: Web audience calculation uses it.

*Call graph*: called by 1 (web_audience); 3 external calls (select, workspace_tx, conversation_audience).


##### `SurfaceContext.agent_detail`  (lines 2969–3027)

```
async def agent_detail(self, agent_id: UUID, member_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads one agent’s settings, prompt digest, and bound surfaces.

**Data flow**: It receives agent and member ids, reads agent fields and surface installation rows, and returns `AgentDetail` or none.

**Call relations**: Web settings and panel completion code use it.

*Call graph*: called by 2 (_complete_agent_spec, agent_settings); 4 external calls (__init__, select, workspace_tx, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 3029–3041)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for a registered object kind.

**Data flow**: It receives a kind name, looks it up in the bound object registry, and returns list fields plus schema or none.

**Call relations**: Web object and first-run routes use it before rendering object pages or actions.

*Call graph*: called by 4 (_object_gate, action_views, object_write, workspace_first_run); 1 external calls (__init__).


##### `SurfaceContext.object_actions`  (lines 3043–3058)

```
def object_actions(self, kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Builds the portal action buttons/forms available for an object target.

**Data flow**: It receives kind, binding, optional name and generation, and returns presented action views.

**Call relations**: Web panels and workspace pages call it to render prepared actions.

*Call graph*: called by 7 (submit_action, _connect_declared, action_views, workspace_credentials, workspace_first_run, workspace_memory, workspace_team); 1 external calls (presented_action_views).


##### `SurfaceContext.frame_admits`  (lines 3060–3063)

```
def frame_admits(self, callable_id: str) -> bool
```

**Purpose**: Checks whether an embedded frame is allowed to post a callable action.

**Data flow**: It receives a callable id and tests membership in the configured admissible set.

**Call relations**: Web panel code uses it before preparing frame-submitted intents.

*Call graph*: called by 2 (_prepare_panel_intent, submit_action).


##### `SurfaceContext.agent_skills`  (lines 3065–3104)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists deploy and member-authored skills available to an agent.

**Data flow**: It binds the agent, reads member skills, filters out names shadowed by deploy skills, and returns portal skill records.

**Call relations**: The web skills route calls it.

*Call graph*: called by 1 (skills); 3 external calls (__init__, log, agent).


##### `SurfaceContext.model`  (lines 3107–3111)

```
def model(self) -> 'SurfaceModel | None'
```

**Purpose**: Returns the optional surface model helper configured for routes.

**Data flow**: It reads and returns the stored `SurfaceModel` or none.

**Call relations**: Surface handlers gate model-assisted route work on this property.


##### `SurfaceContext.memory_available`  (lines 3114–3118)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory search provider is installed.

**Data flow**: It returns true when the stored memory provider is not none.

**Call relations**: Web memory pages use it before offering search or browse.


##### `SurfaceContext.search_memory`  (lines 3120–3130)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches memory visible to a given reader.

**Data flow**: It receives a source reader and queries, requires a memory provider, forwards the search, and returns matches.

**Call relations**: The web workspace memory route calls it after checking availability.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 3132–3145)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items for readable subjects.

**Data flow**: It receives subjects, limit, optional kind filter and cursor; requires a memory provider; and returns one listing page.

**Call relations**: The web memory and recall views use it.

*Call graph*: called by 2 (_recalled, workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 3148–3153)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Lists memory item classes available for filtering.

**Data flow**: It requires a memory provider and returns its listable kinds.

**Call relations**: The web memory view uses it for filter options.


##### `SurfaceContext.member_spend`  (lines 3155–3160)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads usage and spending for one member, including caps.

**Data flow**: It receives member id and optional window, reads via `SpendRollup`, and returns a member spend report.

**Call relations**: The web workspace usage route calls it.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 3162–3214)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to one agent that this viewer may see.

**Data flow**: It receives agent/member/admin flags, queries grants and connections with visibility rules, and returns `ConnectionView` rows.

**Call relations**: The web connections page calls it.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.list_connections`  (lines 3216–3295)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists visible connector accounts in the workspace and the live agents attached to each.

**Data flow**: It receives member/admin flags, reads connections and grants, groups agents by account, and returns `ConnectionPoolView` rows.

**Call relations**: The web connection pool and held-provider helpers call it.

*Call graph*: called by 2 (_held_providers, connection_pool); 7 external calls (__init__, __init__, and_, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.github_coverage`  (lines 3297–3334)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports whether visible GitHub API connections and GitHub sources exist.

**Data flow**: It receives member/admin flags, runs two existence queries with visibility filters, and returns booleans.

**Call relations**: The web GitHub coverage endpoint calls it.

*Call graph*: called by 1 (github_coverage); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.recent_object_changes`  (lines 3336–3375)

```
async def recent_object_changes(self, limit: int) -> tuple[ObjectChange, ...]
```

**Purpose**: Reads the newest object-change audit rows for the workspace.

**Data flow**: It receives a limit, reads object change rows newest first, normalizes timestamps, and returns `ObjectChange` records.

**Call relations**: The web object changes admin page calls it after doing its own gate.

*Call graph*: called by 1 (object_changes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversation_artifacts`  (lines 3377–3446)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists newest shared files for one conversation.

**Data flow**: It receives conversation id and limit, reads artifact/turn/conversation rows, resolves the conversation source, and returns listed artifacts.

**Call relations**: The web transcript aids and slot projection code call it.

*Call graph*: called by 2 (_project_slot_context, _transcript_aids); 5 external calls (__init__, __init__, __init__, select, workspace_tx).


##### `SurfaceContext.agent_turn_statuses`  (lines 3448–3576)

```
async def agent_turn_statuses(self, agent_ids: Sequence[UUID], member_id: UUID) -> tuple[AgentTurnStatus, ...]
```

**Purpose**: Summarizes each agent’s readable live and recent turn state for one member.

**Data flow**: It receives agent ids and member id, queries live turns and latest terminal/readable activity, and returns status rows in input order.

**Call relations**: The web agents status poll calls it frequently.

*Call graph*: called by 1 (agents_status); 5 external calls (__init__, case, select, workspace_tx, readable_audiences).


##### `SurfaceContext.agent_setup`  (lines 3578–3617)

```
async def agent_setup(self, agent_id: UUID, member_id: UUID) -> SetupState
```

**Purpose**: Computes what an agent still needs before it is ready, such as accounts, credentials, or standing orders.

**Data flow**: It receives agent and member ids, defines an object-registry based `armed` helper, binds workspace context, and returns setup state.

**Call relations**: Web agent setup and starter routes call it.

*Call graph*: called by 2 (agent_setup, workspace_starters); 2 external calls (setup_state, ws).


##### `SurfaceContext.agent_setup.armed`  (lines 3600–3614)

```
async def armed(kind: str, name: str | None) -> ArmedOrder
```

**Purpose**: Checks whether a setup-required standing order object exists for an agent.

**Data flow**: It receives kind and optional name, reads either that named object or a page of objects, and returns an `ArmedOrder` with optional schedule.

**Call relations**: It is passed into `setup_state` inside `SurfaceContext.agent_setup`.

*Call graph*: calls 2 internal fn (list_member_objects, member_object); 2 external calls (__init__, __init__).


##### `SurfaceContext.list_member_objects`  (lines 3619–3645)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Lists one registered object kind as visible to a member.

**Data flow**: It receives kind, agent, member/admin, and query; verifies the kind supports member listing; binds the agent; and returns an object page or none.

**Call relations**: Web object index and setup checks use it.

*Call graph*: called by 3 (armed, _bound_page, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 3647–3662)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one registered object by name as visible to a member.

**Data flow**: It receives kind/name/agent/member/admin, verifies member-readable support, binds the agent, and returns the object or none.

**Call relations**: Web object detail and setup checks use it.

*Call graph*: called by 2 (armed, object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 3664–3686)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants tied to a conversation for a member.

**Data flow**: It receives kind, agent, conversation, member/admin, and limit; verifies the kind supports this listing; binds the agent; and returns rows or none.

**Call relations**: The web slot-context projection uses it.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 3688–3715)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists declared credential slots and whether each has a stored value, never the secret values.

**Data flow**: It reads filled credential slot names, maps declarations to object names, and returns sorted `CredentialSlotView` rows.

**Call relations**: Web credential pages and intent refusal UI call it.

*Call graph*: called by 2 (_intent_refusal, workspace_credentials); 4 external calls (__init__, select, workspace_tx, named_slots).


##### `SurfaceContext.workspace_domain`  (lines 3717–3723)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Reads the signup-domain identity for the workspace.

**Data flow**: It opens a workspace transaction and returns the workspace domain or none.

**Call relations**: Member joining, operator checks, and first-run domain checks use it.

*Call graph*: called by 3 (founding_domain, is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.founding_domain`  (lines 3725–3733)

```
async def founding_domain(self) -> str | None
```

**Purpose**: Returns the hosted signup domain only if it really founded this workspace.

**Data flow**: It reads the workspace domain, compares the derived signup workspace id, and returns the domain or none.

**Call relations**: The web first-run route uses it.

*Call graph*: calls 1 internal fn (workspace_domain); called by 1 (workspace_first_run); 1 external calls (signup_workspace_id).


##### `SurfaceContext.list_members`  (lines 3735–3743)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Lists workspace members sorted by email.

**Data flow**: It reads the seat snapshot and returns sorted member entries.

**Call relations**: The web workspace team page calls it.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 3745–3792)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists live source bindings visible to a member.

**Data flow**: It receives member/admin flags, queries non-removed sources with visibility rules, adds connector binding fields, and returns `SourceView` rows.

**Call relations**: The web workspace sources page calls it.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 3794–3810)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations for this workspace and their bound agents.

**Data flow**: It reads installation rows ordered by surface and returns summaries.

**Call relations**: Web first-run, surfaces, and provider-held views use it.

*Call graph*: called by 3 (_held_providers, workspace_first_run, workspace_surfaces); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_surfaces`  (lines 3812–3827)

```
async def member_surfaces(self, member_id: UUID) -> frozenset[str]
```

**Purpose**: Lists surfaces where a member is reachable by identity or proved address.

**Data flow**: It receives member id, unions identity and proved address rows, and returns a frozen set of surface names.

**Call relations**: The web workspace surfaces page calls it.

*Call graph*: called by 1 (workspace_surfaces); 3 external calls (select, union, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 3829–3878)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists recent conversations across the workspace for debug-style views.

**Data flow**: It receives a limit, reads conversations with turn counts and last activity, and returns summaries.

**Call relations**: The debugger surface conversations route calls it.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 3880–3903)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Lists conversations for one agent through the shared conversation directory.

**Data flow**: It receives filters and delegates to `ConversationDirectory.list` for this workspace.

**Call relations**: Web chat listing and resolution routes call it.

*Call graph*: called by 4 (_member_chat, _named, _resolve_chat, conversations); 1 external calls (__init__).


##### `SurfaceContext.readable_conversation`  (lines 3905–3946)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Checks whether a member may read a conversation’s content.

**Data flow**: It receives conversation, agent, member, and admin flag; checks audience, admin disclosure records, and agent ownership; and returns true or false.

**Call relations**: The web readable-conversation gate calls it before serving transcript, files, or subagent data.

*Call graph*: called by 1 (_readable_conversation); 6 external calls (now, select, workspace_tx, audience_member, parse_audience, readable_audiences).


##### `SurfaceContext.conversation_audience`  (lines 3948–3958)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience bound to a conversation.

**Data flow**: It receives conversation and agent ids, reads the stored audience string, parses it, and returns an audience object or none.

**Call relations**: The web slot-context builder uses it.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, workspace_tx, parse_audience).


##### `SurfaceContext.conversation_subagent_turns`  (lines 3960–3997)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists subagent turns spawned beneath a conversation’s turns.

**Data flow**: It receives conversation id and limit, builds a recursive descendant query, converts rows to `Turn` records, and returns them breadth-first.

**Call relations**: Web events, transcript aids, and slot targeting use it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_events, _slot_target, _transcript_aids); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 3999–4015)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists recent turns in a conversation in admission order.

**Data flow**: It receives conversation id and limit, reads the newest rows, reverses them to oldest-first, and returns `Turn` records.

**Call relations**: Debugger and web transcript routes call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _transcript_aids); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 4017–4052)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Finds message references that came from machine-origin prompts rather than human words.

**Data flow**: It receives a conversation id, unions matching turn ids and inbound-message ids, and returns their string refs.

**Call relations**: Web conversation message rendering uses it to avoid showing scheduled or subagent envelopes as user bubbles.

*Call graph*: called by 2 (_conversation_messages, _history_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 4054–4104)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn with its ledger entries and direct child turns.

**Data flow**: It receives a turn id, reads the turn, children, and accounting rows, and returns `TurnDetail` or none.

**Call relations**: Debugger and web routes use it for detailed turn pages and live event rendering.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.turn_steps`  (lines 4106–4119)

```
async def turn_steps(self, turn_id: UUID) -> tuple[TurnStep, ...] | None
```

**Purpose**: Reads durable workflow steps for a workspace-owned turn.

**Data flow**: It receives a turn id, verifies the turn belongs to the workspace, chooses its workflow id, and delegates to the turn-step source.

**Call relations**: The debugger turn steps route calls it.

*Call graph*: called by 1 (turn_steps); 2 external calls (select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 4121–4162)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Lists admitted messages not yet present in the written transcript.

**Data flow**: It receives conversation id and optional draining turn id, verifies ownership, reads waiting or just-drained inbound rows, and returns queued arrivals.

**Call relations**: The web conversation message projection uses it during live turns.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 4164–4196)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Reads attribution for member-admitted queued messages.

**Data flow**: It receives a conversation id, verifies ownership, reads contexts and speaker ids, and returns spoken-arrival records.

**Call relations**: Web conversation and history message renderers use it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (_conversation_messages, _history_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.keyed_admissions`  (lines 4198–4234)

```
async def keyed_admissions(self, conversation_id: UUID) -> tuple[KeyedAdmission, ...]
```

**Purpose**: Lists all messages in a conversation admitted with idempotency keys.

**Data flow**: It receives a conversation id, verifies ownership, unions turn and inbound-message keyed admissions, and returns refs, keys, and bodies.

**Call relations**: The web transcript aids code uses it to recognize previously submitted actions/messages.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_transcript_aids); 4 external calls (__init__, select, union_all, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 4236–4247)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads the durable transcript blob for a conversation.

**Data flow**: It receives a conversation id, verifies ownership, fetches the transcript blob, decodes it, and returns a conversation transcript or none.

**Call relations**: Debugger and web transcript/message routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 4 (conversation_transcript, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 4249–4260)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists saved transcript compaction record indices for a conversation.

**Data flow**: It receives a conversation id, verifies ownership, lists matching blob keys, extracts numeric indices, and returns them sorted.

**Call relations**: Debugger and web conversation-message views use it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_compactions, _conversation_messages).


##### `SurfaceContext.read_compaction`  (lines 4262–4268)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one full compaction record for a conversation.

**Data flow**: It receives conversation id and index, verifies ownership, reads the compaction record, and returns it or none.

**Call relations**: Debugger and web history routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (compaction_record, _history_messages); 1 external calls (read_compaction_record).


##### `SurfaceContext.read_compaction_after`  (lines 4270–4278)

```
async def read_compaction_after(self, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the after-window of a compaction record.

**Data flow**: It receives conversation id and index, verifies ownership, reads the compacted-after messages, and returns them or none.

**Call relations**: The web verified-earlier logic calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_verified_earlier); 1 external calls (read_compaction_after).


##### `SurfaceContext.list_workspace_files`  (lines 4280–4286)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files in a conversation sandbox workspace.

**Data flow**: It receives conversation id, verifies ownership, asks the sandbox for entries, and returns file records or an empty tuple.

**Call relations**: Debugger, UFO, and web attachment/listing routes use it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 3 (workspace_files, workspace_listing, conversation_attachment).


##### `SurfaceContext.conversation_changes`  (lines 4288–4294)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last recorded workspace file changes for a conversation.

**Data flow**: It receives conversation id, verifies ownership, and returns recorded changes or a nothing-changed value.

**Call relations**: The web slot projection uses it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 4296–4305)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one file out of a conversation sandbox workspace.

**Data flow**: It receives conversation id and relative path, verifies ownership, and returns an async byte stream or none.

**Call relations**: Debugger, UFO, and web file download routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 3 (workspace_file, workspace_file, conversation_attachment).


##### `SurfaceContext.terminal_connect`  (lines 4307–4313)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Registers a live terminal connection for a conversation.

**Data flow**: It receives conversation, cwd, optional member id, and runtime id, and records the terminal binding in the sandbox terminal registry.

**Call relations**: Live terminal transports pair it with `terminal_disconnect`.


##### `SurfaceContext.terminal_disconnect`  (lines 4315–4316)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes a live terminal connection for a conversation.

**Data flow**: It receives a conversation id and tells the sandbox terminal registry to disconnect it.

**Call relations**: Terminal transports call it when a held client connection ends.


##### `SurfaceContext.claim_terminal`  (lines 4318–4324)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Claims a connected terminal for a fresh conversation before the agent opens its sandbox.

**Data flow**: It receives conversation id and cwd, asks the sandbox to claim an empty binding, and returns whether the claim succeeded.

**Call relations**: UFO channel send paths call it around first admission.

*Call graph*: called by 2 (_channel_message, _send).


##### `SurfaceContext.next_terminal_op`  (lines 4326–4333)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next operation a turn asks its connected terminal to run.

**Data flow**: It receives conversation id and optional op id to exclude, then returns the next terminal operation.

**Call relations**: Terminal streaming routes use it to send run directives to the client.


##### `SurfaceContext.terminal_resolve`  (lines 4335–4349)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Completes an in-flight terminal operation with the client’s reply or failure.

**Data flow**: It receives conversation, op id, reply bytes, failure text, and member id, then resolves the staged operation if the terminal registry accepts it.

**Call relations**: The UFO channel op reply route calls it.

*Call graph*: called by 1 (_channel_op_reply).


##### `SurfaceContext.terminal_op_body`  (lines 4351–4363)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads staged bytes for an in-flight terminal operation.

**Data flow**: It receives queue key, op id, and member id, finds the conversation without creating one, and asks the terminal registry for staged bytes.

**Call relations**: The UFO op-body route calls it.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 4365–4378)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface.

**Data flow**: It receives a peer surface name, reads the installation row, and returns the installation id or none.

**Call relations**: The debugger workspace metadata route calls it.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 4381–4390)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Yields a raw workspace-scoped database transaction for surface-owned extension data.

**Data flow**: It opens `workspace_tx`, yields the connection, commits on normal exit, and rolls back on error.

**Call relations**: The sites surface uses it for viewer/admin checks over its own tables.

*Call graph*: called by 1 (_viewer_is_admin); 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 4392–4402)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace.

**Data flow**: It receives a conversation id, queries the conversation table, and returns true if found.

**Call relations**: Transcript, compaction, arrival, and workspace-file reads use it before touching unscoped stores.

*Call graph*: called by 10 (arrival_speakers, conversation_changes, keyed_admissions, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_compaction_after, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 4404–4424)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the common select list for turn rows.

**Data flow**: It returns an SQL select containing all fields needed to reconstruct a `Turn` model.

**Call relations**: Turn listing, turn detail, and subagent-turn reads add filters to this query.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 4426–4446)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database turn row into a typed `Turn` object.

**Data flow**: It receives a row, validates optional context and terminal JSON, and returns a `Turn` record.

**Call relations**: All turn-read methods use it after querying rows from `_turn_query`.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.reserve_address`  (lines 4478–4538)

```
async def reserve_address(self, surface: str, address: str, member_id: UUID, claim_expires_at: datetime) -> AddressClaimState
```

**Purpose**: Reserves an addressed-surface address for a workspace member until it is proved.

**Data flow**: It receives surface, address, member, and expiry; verifies the surface was declared; upserts or observes the fleet-wide address row; and returns reserved, linked, or taken.

**Call relations**: Tool code uses this manifest-scoped access to claim addresses without full `SurfaceContext` power.

*Call graph*: 7 external calls (__init__, now, and_, or_, select, owner_tx, ws_current).


##### `SurfaceInstallationAccess.installation`  (lines 4540–4553)

```
async def installation(self, surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for a declared surface.

**Data flow**: It receives a surface name, checks declaration, queries the installation row, and returns the id or none.

**Call relations**: Surface-related tools use it through manifest-scoped access.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `SurfaceInstallationAccess.bind`  (lines 4555–4567)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Binds a declared surface installation to the current workspace.

**Data flow**: It receives surface and installation id, verifies declaration, decides whether ingress routes by installation, and delegates to the shared binding helper.

**Call relations**: Tool handlers call it when configuring declared surfaces.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 4579–4590)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Resolves a shared incoming request’s installation id to a workspace.

**Data flow**: It receives an installation id, reads fleet installation rows for this surface with ingress routing enabled, and returns workspace id or none.

**Call relations**: Slack workspace resolution calls it before a `SurfaceContext` exists.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.addressed_workspace`  (lines 4592–4605)

```
async def addressed_workspace(self, address: str) -> UUID | None
```

**Purpose**: Resolves an addressed-surface sender address to a workspace.

**Data flow**: It receives an address, reads the fleet address table for this surface, and returns workspace id or none.

**Call relations**: Addressed listener contexts use it when events identify tenants by address.

*Call graph*: 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 4607–4617)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential handoff before workspace binding.

**Data flow**: It receives sealed text, returns decoded request state if valid, or none if no credential store or invalid seal.

**Call relations**: Slack workspace resolver uses it during OAuth-style pre-binding handshakes.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 4619–4635)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential for a workspace during pre-binding authentication.

**Data flow**: It receives workspace id and slot, verifies the slot was declared and workspace exists, binds workspace context, and returns the credential value.

**Call relations**: Slack request authentication uses it for signing secrets.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceListenerContext.workspace`  (lines 4683–4691)

```
async def workspace(self, installation_id: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds a persistent listener event to the workspace owning an installation id.

**Data flow**: It receives installation id, checks this process still owns the listener lease, resolves workspace, enters workspace scope, and yields a surface context or none.

**Call relations**: Persistent listeners use it around each installation-routed event.

*Call graph*: 1 external calls (ws).


##### `SurfaceListenerContext.addressed`  (lines 4694–4705)

```
async def addressed(self, address: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds a persistent listener event to the workspace that claimed an address.

**Data flow**: It receives an address, verifies listener ownership, resolves workspace by address, enters workspace scope, and yields a surface context or none.

**Call relations**: The iMessage listener uses it while processing addressed events.

*Call graph*: called by 1 (_process_event); 1 external calls (ws).


##### `SurfaceListenerContext.cursor`  (lines 4707–4722)

```
async def cursor(self, installation_id: str) -> int | None
```

**Purpose**: Reads the saved stream cursor for a listener installation.

**Data flow**: It receives installation id, reads the listener cursor row, and returns the sequence only if it belongs to that installation.

**Call relations**: The iMessage listener uses it when resuming from a provider stream.

*Call graph*: called by 1 (listen); 2 external calls (select, owner_tx).


##### `SurfaceListenerContext.store_cursor`  (lines 4724–4747)

```
async def store_cursor(self, installation_id: str, sequence: int) -> None
```

**Purpose**: Stores the listener’s latest provider stream position.

**Data flow**: It receives installation id and sequence, upserts the fleet cursor row for this surface, and returns nothing.

**Call relations**: The iMessage listener stores progress after catch-up and event processing.

*Call graph*: called by 2 (_catch_up, _process_event); 1 external calls (owner_tx).


##### `SurfaceListenerContext.clear_cursor`  (lines 4749–4756)

```
async def clear_cursor(self) -> None
```

**Purpose**: Deletes the saved listener cursor.

**Data flow**: It deletes the cursor row for this surface.

**Call relations**: The iMessage listener calls it when it must restart from the provider head.

*Call graph*: called by 1 (listen); 2 external calls (delete, owner_tx).


##### `SurfaceListenerRunner.run`  (lines 4783–4822)

```
async def run(self) -> None
```

**Purpose**: Runs a persistent surface listener only while this process owns the fleet-wide listener lease.

**Data flow**: It loops forever, waits for ownership, starts the listener and an ownership watcher, handles failure or ownership loss, logs/parks as needed, and cleans up tasks.

**Call relations**: The application lifecycle starts this runner for surfaces that declare listeners.

*Call graph*: calls 2 internal fn (_wait_until_not_owned, _wait_until_owned); 9 external calls (__init__, CancelledError, create_task, ensure_future, gather, sleep, wait, emit_metric, log).


##### `SurfaceListenerRunner._wait_until_not_owned`  (lines 4824–4829)

```
async def _wait_until_not_owned(self) -> None
```

**Purpose**: Waits until this process no longer owns the listener lease.

**Data flow**: It repeatedly checks ownership and sleeps between checks until ownership becomes false.

**Call relations**: `run` starts it alongside the listener as the lease-loss watcher.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._wait_until_owned`  (lines 4831–4835)

```
async def _wait_until_owned(self) -> None
```

**Purpose**: Waits until this process owns the listener lease.

**Data flow**: It repeatedly checks ownership and sleeps between checks until ownership is true.

**Call relations**: `run` calls it before starting the listener.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._owned_on_tick`  (lines 4837–4846)

```
async def _owned_on_tick(self) -> bool | None
```

**Purpose**: Checks listener ownership once, treating database errors as temporary unknowns.

**Data flow**: It calls `_owns`, logs SQL errors, and returns true, false, or none.

**Call relations**: Both wait loops call it.

*Call graph*: calls 1 internal fn (_owns); called by 2 (_wait_until_not_owned, _wait_until_owned); 1 external calls (log).


##### `SurfaceListenerRunner._owns`  (lines 4848–4866)

```
async def _owns(self) -> bool
```

**Purpose**: Safely runs the listener lease claim even if the task is cancelled midway.

**Data flow**: It starts `_claim`, shields it from cancellation until the database transaction finishes, then returns its result or re-raises cancellation.

**Call relations**: `_owned_on_tick` uses it for robust lease refreshes.

*Call graph*: calls 1 internal fn (_claim); called by 1 (_owned_on_tick); 2 external calls (ensure_future, shield).


##### `SurfaceListenerRunner._claim`  (lines 4868–4908)

```
async def _claim(self) -> bool
```

**Purpose**: Claims or refreshes the fleet-wide listener lease in the database.

**Data flow**: It computes a new expiry, upserts the claim row if expired or already owned by this runner, and returns whether the stored token matches this runner.

**Call relations**: `_owns` calls it as the actual database lease operation.

*Call graph*: called by 1 (_owns); 7 external calls (now, timedelta, and_, insert, insert, or_, owner_tx).


##### `SurfaceDeliveryError.__init__`  (lines 4915–4919)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that may carry a provider-specified retry delay.

**Data flow**: It receives a message and optional retry-after seconds, validates the delay is nonnegative, stores it, and initializes the runtime error.

**Call relations**: Slack posting code can raise it so pollers retry at the provider’s requested time.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 4982–5006)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the condition for terminal writebacks ready to be claimed.

**Data flow**: It receives the current time and returns an SQL condition requiring terminal turn status, no pending mid-turn replies, and an available or expired claim.

**Call relations**: Workspace candidate selection and `WritebackPoller._claim` use it.

*Call graph*: called by 2 (_claim, due); 3 external calls (and_, exists, or_).


##### `writeback_workspaces`  (lines 5009–5044)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating reader for workspaces that have due terminal writebacks.

**Data flow**: It sets up cursor state and returns an async candidates function that pages workspace ids and wraps around.

**Call relations**: A `WritebackPoller` receives this function to know which workspaces to drain.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 5016–5030)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query for workspace ids with due terminal writebacks.

**Data flow**: It reads the current cursor and time, constructs a grouped ordered SQL query, and returns it.

**Call relations**: The returned candidates function passes it to the owner candidate reader.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 5034–5042)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next page of workspace ids with terminal writeback work.

**Data flow**: It runs the due query reader, resets the cursor on wraparound, updates the cursor, and returns ids.

**Call relations**: `WritebackPoller.run` and `drain` call it through the poller’s `candidates` field.


##### `_WritebackDeliveryFailed.__init__`  (lines 5052–5055)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a post or attachment failure with the delivery phase that failed.

**Data flow**: It receives phase and original exception, stores both, and sets an error message.

**Call relations**: `WritebackPoller._deliver_claimed` raises it so `_deliver` can log and retry correctly.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 5078–5111)

```
async def run(self) -> None
```

**Purpose**: Continuously drains terminal writebacks across workspaces in the background.

**Data flow**: It keeps a bounded set of workspace drain tasks, asks for candidate workspaces, starts drains under a semaphore, logs finished errors, sleeps, and cancels tasks on shutdown.

**Call relations**: The service runs this for durable surfaces so terminal replies are eventually delivered.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 5113–5122)

```
async def drain(self) -> None
```

**Purpose**: Runs one bounded drain pass for currently due writeback workspaces.

**Data flow**: It gets workspace candidates, drains them concurrently under a semaphore, and raises an exception group if any failed.

**Call relations**: Tests or maintenance flows can call it for a single pass instead of the infinite loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 5124–5142)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers terminal writebacks for one workspace.

**Data flow**: It enters workspace scope, claims rows, starts lease-renewal tasks, delivers each claimed row, then cancels renewals.

**Call relations**: `run` and `drain` schedule it per workspace.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 5144–5177)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due terminal writeback rows for this worker.

**Data flow**: It receives workspace id, finds claimable rows, marks them claimed with worker id and expiry, and returns turn ids plus previous delivery state.

**Call relations**: `_drain_workspace` calls it before delivery.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 5179–5211)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed terminal writeback and records success or retry/failure.

**Data flow**: It receives workspace, turn, prior reply ref, and renewal task; runs delivery with lease; catches claim loss or delivery failure; updates retry state; and logs outcome.

**Call relations**: `_drain_workspace` calls it for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 5213–5242)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while making sure the claim-renewal task stays healthy.

**Data flow**: It starts delivery and races it against renewal failure, cancels/awaits both at the end, then marks the writeback delivered.

**Call relations**: `_deliver` uses it as the safe wrapper around `_deliver_claimed`.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 5244–5274)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Calls the surface’s post and attach handlers for one claimed terminal writeback.

**Data flow**: It builds the writeback payload, finds the surface spec, posts if no reply reference exists, records the reply reference, and calls attach.

**Call relations**: `_deliver_with_lease` calls it; it hands failures back as `_WritebackDeliveryFailed`.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 5276–5279)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps a claimed terminal writeback lease alive while delivery is in progress.

**Data flow**: It loops, sleeps for the refresh interval, and calls `_refresh_claim`.

**Call relations**: `_drain_workspace` starts one renewal task per claimed row.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 5281–5297)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim on a terminal writeback row.

**Data flow**: It receives turn id, updates the row only if still claimed by this worker, and raises claim-lost if no row changed.

**Call relations**: `_renew_claim` calls it repeatedly.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 5299–5351)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the `Writeback` payload for a terminal turn.

**Data flow**: It receives turn id, reads terminal frame, conversation key, surface, and shared artifacts, validates the terminal frame, and returns payload plus surface name.

**Call relations**: `_deliver_claimed` calls it before choosing the surface handler.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 5353–5365)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Records the provider’s reply reference after posting but before attachments.

**Data flow**: It receives turn id and reply ref, updates the claimed row if still owned by this worker, and raises claim-lost on mismatch.

**Call relations**: `_deliver_claimed` uses it to avoid reposting after a crash once the reference is stored.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 5367–5384)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a terminal writeback as fully delivered.

**Data flow**: It receives turn id, updates the claimed row to delivered and clears claim fields, or raises claim-lost if ownership changed.

**Call relations**: `_deliver_with_lease` calls it after post/attach completes or nothing was deliverable.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 5386–5435)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed terminal writeback for retry or marks it permanently failed after the age limit.

**Data flow**: It receives turn id and delivery failure, chooses retry delay from provider retry-after or fixed backoff, writes status/error/next attempt, and returns outcome details.

**Call relations**: `_deliver` calls it after `_deliver_with_lease` reports a delivery failure.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `mid_turn_reply_workspaces`  (lines 5438–5469)

```
def mid_turn_reply_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating reader for workspaces with due mid-turn replies.

**Data flow**: It sets up cursor state and returns an async candidates function that pages workspace ids and wraps around.

**Call relations**: `MidTurnReplyPoller` uses it to find workspaces with partial replies to send.

*Call graph*: 1 external calls (owner_candidates).


##### `mid_turn_reply_workspaces.due`  (lines 5444–5455)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query for workspace ids with due mid-turn replies.

**Data flow**: It uses current time and cursor to construct a grouped ordered query over mid-turn reply rows.

**Call relations**: The candidates closure passes it to the owner candidate reader.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); 2 external calls (now, select).


##### `mid_turn_reply_workspaces.candidates`  (lines 5459–5467)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next page of workspace ids with mid-turn reply work.

**Data flow**: It reads due ids, resets and wraps the cursor if needed, updates cursor state, and returns ids.

**Call relations**: `MidTurnReplyPoller.drain` calls it through the poller’s `candidates` field.


##### `_mid_turn_reply_due`  (lines 5472–5485)

```
def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the condition for mid-turn reply rows ready to be claimed.

**Data flow**: It receives current time and returns an SQL condition for pending or expired-claimed rows.

**Call relations**: Candidate selection and `MidTurnReplyPoller._claim` use it.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `MidTurnReplyPoller.run`  (lines 5513–5519)

```
async def run(self) -> None
```

**Purpose**: Continuously drains due mid-turn replies in the background.

**Data flow**: It loops forever, calls `drain`, logs drain failures, sleeps, and repeats.

**Call relations**: The service runs it for durable surfaces that support partial replies.

*Call graph*: calls 1 internal fn (drain); 2 external calls (sleep, log).


##### `MidTurnReplyPoller.drain`  (lines 5521–5539)

```
async def drain(self) -> None
```

**Purpose**: Claims and delivers due mid-turn replies for candidate workspaces.

**Data flow**: It gets workspace ids, enters each workspace scope, claims rows, starts renewals, delivers each row, and cleans up renewal tasks.

**Call relations**: `run` calls it once per polling interval.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 1 (run); 4 external calls (create_task, gather, log, ws).


##### `MidTurnReplyPoller._claim`  (lines 5541–5582)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due mid-turn reply rows for this worker.

**Data flow**: It receives workspace id, finds claimable rows ordered by creation and span order, marks them claimed, and returns sorted claimed rows.

**Call relations**: `drain` calls it before delivering replies.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); called by 1 (drain); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `MidTurnReplyPoller._deliver`  (lines 5584–5609)

```
async def _deliver(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed mid-turn reply and logs success, retry, failure, or claim loss.

**Data flow**: It receives workspace id, row, and renewal task; runs delivery with lease; updates retry/failure on errors; and logs the result.

**Call relations**: `drain` calls it for each claimed reply row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (drain); 2 external calls (now, log).


##### `MidTurnReplyPoller._deliver_with_lease`  (lines 5611–5632)

```
async def _deliver_with_lease(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs mid-turn reply sending while ensuring the claim renewal does not fail unnoticed.

**Data flow**: It races `_speak` against renewal failure, cancels/awaits tasks, then marks the row delivered with the reply reference.

**Call relations**: `_deliver` uses it for safe exactly-once-style progression.

*Call graph*: calls 2 internal fn (_mark_delivered, _speak); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `MidTurnReplyPoller._speak`  (lines 5634–5676)

```
async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None
```

**Purpose**: Calls the surface’s mid-turn reply sender, unless the reply was already posted or no sender exists.

**Data flow**: It receives workspace id and reply row, returns existing reply ref if present, otherwise reads turn/conversation surface data and calls the surface `speak` handler if configured.

**Call relations**: `_deliver_with_lease` calls it; surface handlers receive a `MidTurnReply` payload.

*Call graph*: called by 1 (_deliver_with_lease); 4 external calls (__init__, select, workspace_tx, log).


##### `MidTurnReplyPoller._renew_claim`  (lines 5678–5681)

```
async def _renew_claim(self, reply_id: UUID) -> None
```

**Purpose**: Keeps a claimed mid-turn reply lease alive during delivery.

**Data flow**: It loops, sleeps for the refresh interval, and calls `_refresh_claim`.

**Call relations**: `drain` starts one renewal task per claimed mid-turn reply.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (drain); 1 external calls (sleep).


##### `MidTurnReplyPoller._refresh_claim`  (lines 5683–5699)

```
async def _refresh_claim(self, reply_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim on a mid-turn reply row.

**Data flow**: It receives reply id, updates expiry only if still claimed by this worker, and raises claim-lost if not.

**Call relations**: `_renew_claim` calls it repeatedly.

*Call graph*: called by 1 (_renew_claim); 4 external calls (now, timedelta, update, workspace_tx).


##### `MidTurnReplyPoller._mark_delivered`  (lines 5701–5719)

```
async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Marks a mid-turn reply row delivered and stores its provider reply reference.

**Data flow**: It receives reply id and optional reply ref, updates the claimed row to delivered and clears claim fields, or raises claim-lost.

**Call relations**: `_deliver_with_lease` calls it after `_speak` completes.

*Call graph*: called by 1 (_deliver_with_lease); 2 external calls (update, workspace_tx).


##### `MidTurnReplyPoller._fail_or_retry`  (lines 5721–5768)

```
async def _fail_or_retry(self, reply_id: UUID, error: Exception) -> tuple[str, str, datetime | None]
```

**Purpose**: Schedules a failed mid-turn reply for retry or marks it failed after the age limit.

**Data flow**: It receives reply id and error, chooses retry delay, writes status/error/claim fields, and returns outcome details.

**Call relations**: `_deliver` calls it when sending fails.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).
