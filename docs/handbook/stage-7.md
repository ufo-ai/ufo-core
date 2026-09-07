# Request routing, public surfaces, and workspace object APIs  `stage-7`

This stage is part of the main work loop after a user is signed in. It decides where each web request, chat message, object view, download, or operator page should go. Think of it as a switchboard for all the places people can interact with UFO.

External chat and terminal ingress brings messages in from Slack, iMessage, web chat, and terminal clients, converts them into UFO’s common conversation format, and sends replies back in each service’s own style. Portal and object-oriented workspace APIs let the portal, tools, and agents safely read or change workspace items such as agents, tasks, sites, memories, members, and connectors.

The web surface serves the browser portal, live chat, streaming replies, and member actions. Starters prepares personalized suggestions for the start screen. Runtime steps turns workflow records into a readable timeline for debugging. The debugger and memory surfaces give trusted operators read-only inspection pages. Scheduled task slots show allowed automations inside a conversation. The sites surface serves hosted site pages, previews, and visibility changes. The package marker simply groups surface code in one place.

## Sub-stages

- [External chat and terminal ingress](stage-7.1.md) `stage-7.1` — 9 files
- [Portal and object-oriented workspace reads and writes](stage-7.2.md) `stage-7.2` — 23 files

## Files in this stage

### Web portal experience
Authenticated browser traffic enters through the main web portal, which serves workspace actions, chat streaming, and personalized starter suggestions.

### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling, live streaming, background scheduled jobs`

Think of this file as the front desk for the web version of UFO. A signed-in member arrives with a secure session cookie, and this code decides which workspace they belong to, which agents they may see, and which conversations or workspace resources they may read or change. Without it, the browser app would have no safe way to open, send messages, upload files, watch live replies, connect accounts, browse memory, view usage, or inspect objects such as sites and credentials.

The file does several jobs. First, it serves the built web shell and static assets, including fallbacks for rolling deploys where one server may serve a page built by another. Second, it authenticates every request and turns the member's email into a workspace member identity. Third, it provides chat: opening conversations, validating messages and attachments, stopping turns, delivering files, and streaming live frames through Server-Sent Events, a browser-friendly stream of small updates. Fourth, it renders stored transcripts into the cleaner chat shape the portal displays, including questions, shared files, spawned subagents, and connect prompts. Finally, it exposes workspace and agent panels: agents, settings, skills, conversations, memory, credentials, sources, team, usage, object lists, object writes, account connections, starters, and homepages.

#### Function details

##### `load_assets`  (lines 336–346)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Loads built portal asset files, such as JavaScript, CSS, fonts, and images, into memory when the module starts.

**Data flow**: It receives a directory path, scans files with known web media types, reads their bytes, and returns a map from request names to file bytes and media type.

**Call relations**: The module uses this at import time to build the static asset table that later static-file requests read from.

*Call graph*: 1 external calls (glob).


##### `rum_config`  (lines 359–372)

```
def rum_config(environ: Mapping[str, str]) -> dict[str, str] | None
```

**Purpose**: Builds the browser monitoring configuration for Datadog Real User Monitoring, if this deployment has enabled it.

**Data flow**: It reads environment variables, returns a complete configuration when all required values are present, returns none when all are absent, and raises an error for a half-configured deployment.

**Call relations**: portal_page calls it before serving the shell so the page records sessions only when the deployment is correctly configured.

*Call graph*: called by 1 (portal_page).


##### `portal_shell`  (lines 375–384)

```
def portal_shell(html: str, config: Mapping[str, str] | None) -> str
```

**Purpose**: Injects the deployment-specific monitoring configuration into the HTML shell safely.

**Data flow**: It takes HTML and an optional config, replaces the page's declared monitoring placeholder with JSON or null, and returns the final HTML string.

**Call relations**: portal_page uses it after choosing which shell to serve; it relies on the page containing exactly one monitoring placeholder.

*Call graph*: called by 1 (portal_page); 1 external calls (dumps).


##### `resolve_workspace`  (lines 397–439)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace a request belongs to before the real route handler runs.

**Data flow**: It reads the signed session cookie, or the posted login token for the one session-opening POST, extracts the workspace claim, and may redirect cold browser arrivals to sign-in.

**Call relations**: The shared surface framework calls this as the request identifier; it uses _form, _framed_length, and _chat_target for safe login-token handling and redirect preservation.

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 442–446)

```
def _chat_target(request: Request) -> UUID | None
```

**Purpose**: Parses an optional conversation id from the login redirect query string.

**Data flow**: It reads the c query parameter, tries to turn it into a UUID, and returns that UUID or none.

**Call relations**: resolve_workspace calls it so a member who signs in from a conversation link can land back on that conversation.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 449–459)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Serves one static portal asset from this process's built asset table.

**Data flow**: It strips the static URL prefix, looks up the asset bytes and media type, and returns a cached response or none if this build lacks the asset.

**Call relations**: static_asset tries this first, then falls back to _stored_asset for assets from another build.

*Call graph*: calls 1 internal fn (_asset_response); called by 1 (static_asset).


##### `_asset_response`  (lines 462–466)

```
def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response
```

**Purpose**: Builds a web response for immutable asset bytes with an ETag cache marker.

**Data flow**: It receives bytes, media type, and an ETag; if the browser already has that ETag it returns 304, otherwise it returns the bytes.

**Call relations**: _static_response and _stored_asset both use it so local and stored assets behave the same way.

*Call graph*: called by 2 (_static_response, _stored_asset); 1 external calls (Response).


##### `load_apps`  (lines 492–525)

```
def load_apps(directory: Path) -> AppsBundle | None
```

**Purpose**: Loads the built app-homepage bundle that shipped with this deployment.

**Data flow**: It scans a directory tree, ignores dot-prefixed files, reads every file, computes a content digest, detects top-level app slugs, and returns an AppsBundle or none.

**Call relations**: The module calls it at import time; later homepage and boot reads use apps() to require the bundle.

*Call graph*: 4 external calls (__init__, sha256, is_dir, rglob).


##### `apps`  (lines 531–537)

```
def apps() -> AppsBundle
```

**Purpose**: Returns the shipped app bundle or raises a clear build error.

**Data flow**: It reads the module-level bundle and either returns it or raises an exception naming the frontend build command.

**Call relations**: agents_index, homepage, and _homepage_state call it before they expose shipped app pages.

*Call graph*: called by 3 (_homepage_state, agents_index, homepage).


##### `_publish_assets`  (lines 540–550)

```
async def _publish_assets(blob: BlobStore, apps: AppsBundle) -> None
```

**Purpose**: Copies this deployment's web assets into shared blob storage so other pods can serve them during rolling deploys.

**Data flow**: It checks whether each static and app-bundle key already exists, writes missing bytes, and changes only the shared blob store.

**Call relations**: _assets_published runs this once per process before serving pages that name those assets.

*Call graph*: calls 3 internal fn (exists, list, put); called by 1 (_assets_published).


##### `_assets_published`  (lines 553–580)

```
def _assets_published(blob: BlobStore, apps: AppsBundle) -> 'asyncio.Task[None]'
```

**Purpose**: Starts or reuses the one background task that publishes built assets to shared storage.

**Data flow**: It receives the blob store and app bundle, reuses a healthy existing task, or creates a new publish task after failure or first use.

**Call relations**: portal_page, agents_index, and homepage await this so any page they serve can fetch its referenced assets.

*Call graph*: calls 1 internal fn (_publish_assets); called by 3 (agents_index, homepage, portal_page); 1 external calls (create_task).


##### `_stored_asset`  (lines 583–605)

```
async def _stored_asset(blob: BlobStore, request: Request) -> Response
```

**Purpose**: Serves an asset from shared storage when this process does not have that exact build locally.

**Data flow**: It validates the requested asset name, reads and caches the bytes from blob storage if present, and returns the same kind of asset response as local files.

**Call relations**: static_asset calls it after _static_response misses, which makes mixed-version web deployments safe.

*Call graph*: calls 3 internal fn (exists, get, _asset_response); called by 1 (static_asset); 3 external calls (sha256, Path, Response).


##### `portal_page`  (lines 608–629)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main authenticated HTML page for the portal.

**Data flow**: It checks that the frontend was built, publishes assets, reads a feature flag to choose the shell, injects monitoring config, and returns no-store HTML.

**Call relations**: This is the GET route for the portal root after resolve_workspace has scoped the request.

*Call graph*: calls 3 internal fn (_assets_published, portal_shell, rum_config); 2 external calls (flag_enabled, HTMLResponse).


##### `_refused`  (lines 645–646)

```
def _refused(message: str) -> Response
```

**Purpose**: Builds a standard JSON refusal for account-connection flows.

**Data flow**: It takes a message and returns JSON with status refused and that message.

**Call relations**: openai_device_poll and anthropic_code use it when the provider flow cannot continue.

*Call graph*: called by 2 (anthropic_code, openai_device_poll); 1 external calls (JSONResponse).


##### `workspace_accounts`  (lines 649–668)

```
async def workspace_accounts(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports whether the signed-in member has connected their coding model accounts.

**Data flow**: It authenticates the request, checks each known provider credential slot for that member, and returns provider rows with connected flags.

**Call relations**: The accounts settings and first-run screens read this so they agree on OpenAI and Anthropic connection state.

*Call graph*: calls 2 internal fn (member_credential_stored, _authenticate); 1 external calls (JSONResponse).


##### `openai_device`  (lines 671–694)

```
async def openai_device(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts an OpenAI device-code sign-in flow for the member.

**Data flow**: It authenticates, asks OpenAI for a device code, stores the private device handle in an HttpOnly cookie, and returns the short code and verification URL.

**Call relations**: The browser calls this when the member chooses to connect ChatGPT; openai_device_poll later checks completion.

*Call graph*: calls 1 internal fn (_authenticate); 4 external calls (__init__, JSONResponse, set_session_cookie, openai_client_id).


##### `openai_device_poll`  (lines 697–714)

```
async def openai_device_poll(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Checks whether the member finished the OpenAI device-code flow.

**Data flow**: It authenticates, reads the device cookie, polls OpenAI, stores the resulting key when connected, and returns pending, connected, or refused.

**Call relations**: The account UI calls this repeatedly after openai_device until the provider grants or refuses access.

*Call graph*: calls 3 internal fn (put_member_credential, _authenticate, _refused); 4 external calls (__init__, JSONResponse, openai_client_id, log).


##### `anthropic_authorize`  (lines 717–728)

```
async def anthropic_authorize(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts an Anthropic sign-in flow where the member later pastes back a code.

**Data flow**: It authenticates, creates an authorization URL and state cookie, and returns the URL to open.

**Call relations**: The browser starts Claude connection here; anthropic_code completes it.

*Call graph*: calls 1 internal fn (_authenticate); 4 external calls (__init__, JSONResponse, set_session_cookie, anthropic_client_id).


##### `anthropic_code`  (lines 731–753)

```
async def anthropic_code(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes Anthropic sign-in by exchanging a pasted code for a stored member credential.

**Data flow**: It bounds and parses the form, authenticates, validates the code and state cookie, verifies the key works, stores it, and returns connected or refused.

**Call relations**: It is the second half of anthropic_authorize and uses _refused for member-readable failures.

*Call graph*: calls 5 internal fn (put_member_credential, _authenticate, _form, _framed_length, _refused); 5 external calls (__init__, JSONResponse, anthropic_client_id, log, verified_key).


##### `account_disconnect`  (lines 756–769)

```
async def account_disconnect(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Removes one connected coding account from the signed-in member.

**Data flow**: It authenticates, maps the provider name to a known credential slot, clears that member slot, logs the change, and returns disconnected.

**Call relations**: The account settings page calls it when a member wants to replace or revoke their own OpenAI or Anthropic connection.

*Call graph*: calls 2 internal fn (clear_member_credential, _authenticate); 2 external calls (JSONResponse, log).


##### `connect_arrival`  (lines 772–777)

```
async def connect_arrival(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Redirects provider sign-in arrival links into the portal credentials screen.

**Data flow**: It authenticates the session and returns a redirect to the portal route with the credentials hash.

**Call relations**: The OpenAI and Anthropic sign-in paths use this because the actual connection UI lives inside the portal.

*Call graph*: calls 1 internal fn (_authenticate); 1 external calls (RedirectResponse).


##### `_authenticate`  (lines 780–804)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

**Purpose**: Turns a session cookie into a workspace member id and email, or returns a precise refusal.

**Data flow**: It verifies the signed cookie against the workspace, links or creates the member row for the email, checks seat access, and returns member id plus email.

**Call relations**: Most authenticated routes reach it directly or through _audience_for; it is the portal's basic security gate.

*Call graph*: calls 3 internal fn (link_member, linked_member, member_has_access); called by 9 (_audience_for, account_disconnect, anthropic_authorize, anthropic_code, connect_arrival, fulfill_credential, openai_device, openai_device_poll, workspace_accounts); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 807–813)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a static asset only after the request has a valid web session.

**Data flow**: It first tries this build's in-memory asset table, then tries shared blob storage, and returns the asset or a 404-style response.

**Call relations**: The route table maps portal asset URLs here; _static_response and _stored_asset do the actual lookup.

*Call graph*: calls 2 internal fn (_static_response, _stored_asset).


##### `open_session`  (lines 816–847)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Creates the browser session by placing a posted bearer token into a secure cookie.

**Data flow**: It bounds and parses a form, validates the token shape, sets the session cookie, and redirects back to the portal URL.

**Call relations**: This is the one POST that opens a web session; resolve_workspace may have used the same posted token to scope the request.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 850–854)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Parses the agent id from a route path.

**Data flow**: It reads the agent_id path parameter, converts it to a UUID, and returns none on malformed input.

**Call relations**: Chat, transcript, and panel gates use it before checking whether the member may see that agent.

*Call graph*: called by 4 (_member_chat_page, _panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 857–858)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the store key for the web-specific record of a conversation.

**Data flow**: It receives a conversation id and returns the chat/ key string used in the web extension store.

**Call relations**: _open_conversation writes this key and _own_web_chat reads it to prove a portal chat belongs to a member and agent.

*Call graph*: called by 2 (_open_conversation, _own_web_chat).


##### `_chat_title`  (lines 867–885)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates the first plain title for a conversation from the opening message or attached filenames.

**Data flow**: It collapses whitespace, falls back to filenames when text is empty, trims long titles at a reasonable word boundary, and returns a short label.

**Call relations**: _open_conversation uses it immediately; summarize_chat_titles also uses it to clean model-written titles.

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_title_excerpt`  (lines 897–915)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

**Purpose**: Extracts a small opening exchange for the title-writing model.

**Data flow**: It reads the first user and assistant text, strips system context from the user's message, bounds each side, and joins them.

**Call relations**: summarize_chat_titles calls it before asking the model to write a better conversation title.

*Call graph*: calls 1 internal fn (_rendered_text); called by 1 (summarize_chat_titles); 1 external calls (member_message_text).


##### `summarize_chat_titles`  (lines 918–964)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

**Purpose**: Background job that writes better titles for conversations whose opening exchange has landed.

**Data flow**: It reads candidate conversations, fetches their transcripts, builds excerpts, asks the model for short titles when possible, and records each as summarized.

**Call relations**: The scheduled title job calls this; it uses _title_excerpt and _chat_title and writes back through ExtensionContext.

*Call graph*: calls 4 internal fn (conversations_awaiting_title, summarized_conversation_title, _chat_title, _title_excerpt); 2 external calls (__init__, __init__).


##### `HomepageSeed.sweep`  (lines 1018–1041)

```
async def sweep(self) -> None
```

**Purpose**: Runs the homepage seeding pass for all workspace agents.

**Data flow**: It reads agents, settled markers, and attempt records, skips finished or ineligible agents, and either marks them settled or fires a build attempt.

**Call relations**: seed_homepages creates HomepageSeed and calls this; it delegates checks to _settled and work creation to _fire.

*Call graph*: calls 2 internal fn (_fire, _settled).


##### `HomepageSeed._settled`  (lines 1043–1050)

```
async def _settled(self, agent: WorkspaceAgent) -> str | None
```

**Purpose**: Decides whether an agent already needs no homepage build.

**Data flow**: It checks whether the agent is archived, is a shipped app, or already has a hosted homepage, and returns the reason or none.

**Call relations**: sweep calls this before spending an attempt on an agent.

*Call graph*: called by 1 (sweep); 1 external calls (shipped_app_slug).


##### `HomepageSeed._acting`  (lines 1052–1055)

```
async def _acting(self, agent: WorkspaceAgent) -> UUID | None
```

**Purpose**: Finds the member identity that can act for an automatic homepage build.

**Data flow**: It returns the agent owner when present, otherwise the earliest seated admin, or none if no one can act.

**Call relations**: _fire calls it because the build turn must run with member authority.

*Call graph*: called by 1 (_fire).


##### `HomepageSeed._fire`  (lines 1057–1085)

```
async def _fire(self, agent: WorkspaceAgent, key: str, attempt_key: str, attempt: HomepageSeedAttempt) -> None
```

**Purpose**: Starts one scheduled agent turn that asks the agent to build and bind its homepage.

**Data flow**: It chooses an acting member, opens or reuses a homepage conversation, invokes the seed prompt with an idempotency key, checks the outcome, and records success or another attempt.

**Call relations**: sweep calls it for due agents that are eligible and under the retry limit.

*Call graph*: calls 1 internal fn (_acting); called by 1 (sweep); 2 external calls (__init__, __init__).


##### `seed_homepages`  (lines 1088–1095)

```
async def seed_homepages(ctx: ExtensionContext, bucket: str | None=None) -> None
```

**Purpose**: Entry function for the scheduled homepage seeding job.

**Data flow**: It chooses today's bucket if none is supplied, builds a HostedSites helper, creates HomepageSeed, and runs its sweep.

**Call relations**: The scheduler calls this periodically so agents eventually get default homepages.

*Call graph*: 3 external calls (__init__, __init__, now).


##### `_open_conversation`  (lines 1098–1131)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and its web ownership record safely.

**Data flow**: It writes a chat record under a minted id, asks core to create or reuse the conversation, cleans up losing race records, and sets or reads the title.

**Call relations**: _new_chat_target calls it when the first message starts a fresh chat.

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_web_chat); called by 1 (_new_chat_target); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 1134–1141)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

**Purpose**: Reads the current title of one conversation from the shared conversation listing.

**Data flow**: It asks core for that exact agent conversation and returns its title or an empty string.

**Call relations**: _open_conversation uses it when another concurrent request already created the conversation.

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 1 (_open_conversation).


##### `_own_web_chat`  (lines 1144–1156)

```
async def _own_web_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is this member's own web chat with this agent.

**Data flow**: It reads the stored chat row, validates it, compares agent id and email, and returns the record or none.

**Call relations**: _member_chat and _open_conversation use it as the web-specific ownership proof.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 2 (_member_chat, _open_conversation).


##### `_member_chat`  (lines 1159–1204)

```
async def _member_chat(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, *, agent_visible: bool) -> ListedConversation | None
```

**Purpose**: Decides whether the member may continue or read a conversation through the portal chat lane.

**Data flow**: It combines the web chat row, core conversation listing, durable audience, surface type, room prefixes, and commentable rules to return a listed conversation or none.

**Call relations**: Chat, transcript, permalink resolution, stream authorization, and member-chat history all rely on this common gate.

*Call graph*: calls 3 internal fn (list_agent_conversations, _commentable, _own_web_chat); called by 5 (_existing_chat_target, _member_chat_page, _member_turn, _resolve_chat, transcript); 1 external calls (conversation_audience).


##### `_commentable`  (lines 1207–1211)

```
def _commentable(conversation: ListedConversation, member_id: UUID) -> bool
```

**Purpose**: Tells whether a listed conversation can receive web comments from this member.

**Data flow**: It checks that the conversation is on a supported surface and that its audience is shared or this member's private audience.

**Call relations**: _member_chat, _existing_chat_target, _member_turn, _resolve_chat, and _conversation_row use it to mark comment access consistently.

*Call graph*: called by 5 (_conversation_row, _existing_chat_target, _member_chat, _member_turn, _resolve_chat); 1 external calls (conversation_audience).


##### `_turn_context`  (lines 1214–1225)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

**Purpose**: Builds the context metadata attached to an admitted chat turn.

**Data flow**: It reads the browser timezone header, keeps it if valid, drops and logs it if invalid, and returns sender/source context.

**Call relations**: _admit_chat passes this to core admission so the agent knows who spoke, where, and in what timezone.

*Call graph*: called by 1 (_admit_chat); 2 external calls (__init__, log).


##### `_chat_url`  (lines 1228–1231)

```
def _chat_url(public_base_url: str | None, conversation_id: UUID) -> str | None
```

**Purpose**: Builds a portal link to a conversation when the deployment has a public base URL.

**Data flow**: It combines the public base, portal path, and conversation fragment, or returns none without a base URL.

**Call relations**: _chat_source and _comment_notice use it to produce human-readable source links.

*Call graph*: called by 2 (_chat_source, _comment_notice).


##### `_chat_source`  (lines 1234–1243)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Creates the source label an agent sees for a portal message.

**Data flow**: It uses _chat_url when possible and appends the member email; without a URL it falls back to a plain web source label.

**Call relations**: _admit_chat includes this in TurnContext so downstream work can point back to the conversation.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (_admit_chat).


##### `_comment_notice`  (lines 1246–1266)

```
def _comment_notice(public_base_url: str | None, conversation: ListedConversation, member_id: UUID, email: str, text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Formats a web comment so it can be delivered into another surface's conversation.

**Data flow**: It decides the display author, creates a comment link when possible, appends attachment names, and returns markdown-like text.

**Call relations**: _existing_chat_target builds this when the target conversation is Slack or terminal-commentable.

*Call graph*: calls 1 internal fn (_chat_url); called by 1 (_existing_chat_target); 2 external calls (PurePosixPath, conversation_audience).


##### `_audience_for`  (lines 1269–1276)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Authenticates and computes the member's web audience.

**Data flow**: It authenticates the session, then asks the web audience code which agents and admin privileges this email has.

**Call relations**: Most portal routes call this directly or through _panel_gate and _object_gate.

*Call graph*: calls 1 internal fn (_authenticate); called by 25 (_member_chat_page, _member_turn, _object_gate, _panel_gate, action_views, agents_index, agents_status, chat, chats_index, connection_pool (+15 more)); 2 external calls (web_audience, web_extension).


##### `_visibility_flag`  (lines 1307–1312)

```
def _visibility_flag(main: bool, provisioned_by: str | None) -> str | None
```

**Purpose**: Finds the feature flag that decides whether an agent should be listed in the portal.

**Data flow**: It returns the main-agent flag, an app-specific flag for shipped apps, or none for always-visible custom agents.

**Call relations**: agents_index uses it for live and archived agents, and its nested withheld helper reuses it.

*Call graph*: called by 2 (agents_index, withheld); 1 external calls (shipped_app_slug).


##### `_flag_reads`  (lines 1315–1331)

```
async def _flag_reads(flags: Iterable[str | None]) -> dict[str, bool]
```

**Purpose**: Reads all feature flags needed for the portal boot response in one batch.

**Data flow**: It deduplicates flag names, reads each with the correct default, and returns a flag-to-boolean map.

**Call relations**: agents_index calls it before marking screens and agents as visible or hidden.

*Call graph*: called by 1 (agents_index); 2 external calls (gather, flag_enabled).


##### `_setup_configured`  (lines 1334–1345)

```
def _setup_configured(state: SetupState) -> bool
```

**Purpose**: Says whether an installed app's setup has been accepted or has nothing to configure.

**Data flow**: It inspects connector, credential, and standing-order setup rows and returns true when there are no rows or at least one is settled.

**Call relations**: workspace_starters uses it to decide whether to offer setup for already-installed apps.

*Call graph*: called by 1 (workspace_starters).


##### `agents_index`  (lines 1348–1488)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the portal boot payload: signed-in member, visible agents, surfaces, archived apps, setup state, and homepages.

**Data flow**: It authenticates, reads audience, publishes assets, resolves homepages and setup state, reads flags, filters archived apps, and returns a large JSON snapshot.

**Call relations**: The frontend's first API call uses this; many helper functions feed it one consistent view.

*Call graph*: calls 8 internal fn (list_archived_agents, _assets_published, _audience_for, _bound_page, _flag_reads, _homepage_state, _visibility_flag, apps); 6 external calls (Semaphore, gather, JSONResponse, shipped_app_slug, granted_emails, web_extension).


##### `agents_index.setup_of`  (lines 1402–1404)

```
async def setup_of(agent: AgentSummary) -> SetupState
```

**Purpose**: Reads setup state for one provisioned agent while respecting a concurrency limit.

**Data flow**: It waits for the semaphore, asks core for that agent's setup state for the member, and returns it.

**Call relations**: agents_index runs many setup_of calls together so boot is parallel but does not overload the transaction pool.


##### `agents_index.withheld`  (lines 1437–1439)

```
def withheld(main: bool, provisioned_by: str | None) -> bool
```

**Purpose**: Checks whether a live or archived agent is hidden by its feature flag.

**Data flow**: It finds the relevant visibility flag and returns true only when that flag exists and reads false.

**Call relations**: agents_index uses it while shaping both agent rows and archived app rows.

*Call graph*: calls 1 internal fn (_visibility_flag).


##### `agents_status`  (lines 1491–1532)

```
async def agents_status(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a lightweight live status summary for each agent the member can see.

**Data flow**: It authenticates, reads aggregate turn statuses, optionally peeks latest activity for running turns, and returns activity, last-active time, and failure state.

**Call relations**: The frontend polls this beside the boot index instead of reloading the full agent list.

*Call graph*: calls 4 internal fn (agent_turn_statuses, latest_activity, _audience_for, _iso); 1 external calls (JSONResponse).


##### `_framed_length`  (lines 1535–1548)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Rejects whole-body form parses unless the request declares a safe Content-Length.

**Data flow**: It inspects transfer encoding and content length, returning length-required or too-large responses, or none when safe.

**Call relations**: Login, credential, preview, and multipart parsing routes call it before buffering form bodies.

*Call graph*: called by 6 (_parse_inbound, anthropic_code, fulfill_credential, open_session, preview, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 1551–1558)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses a request form and turns malformed parser errors into a client error response.

**Data flow**: It calls the request form parser and returns either FormData or a 400 response.

**Call relations**: Any route that accepts HTML form or multipart data uses this common wrapper.

*Call graph*: called by 6 (_parse_inbound, anthropic_code, fulfill_credential, open_session, preview, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 1561–1569)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a request body while enforcing a real byte limit.

**Data flow**: It streams chunks into memory, stops with 413 if the accumulated body exceeds the limit, and otherwise returns bytes.

**Call relations**: _parse_inbound and upload_start use it where declared Content-Length alone is not enough.

*Call graph*: called by 2 (_parse_inbound, upload_start); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 1572–1627)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...], tuple[tuple[str, str], ...]] | Response
```

**Purpose**: Parses the chat composer body into text, inline files, and already-uploaded file references.

**Data flow**: It accepts plain UTF-8 text or bounded multipart data, validates parts and file counts, and returns message text, upload files, and key/signature pairs.

**Call relations**: _chat_inbound calls it before validating upload grants, answer headers, stop headers, and final body size.

*Call graph*: calls 3 internal fn (_bounded_body, _form, _framed_length); called by 1 (_chat_inbound); 1 external calls (Response).


##### `_inbox_paths`  (lines 1630–1639)

```
def _inbox_paths(uploads: tuple[UploadFile, ...], keys: tuple[str, ...]) -> tuple[str, ...]
```

**Purpose**: Chooses safe workspace-relative filenames for chat attachments.

**Data flow**: It receives inline uploads and presigned keys, extracts leaf names, de-duplicates them, and returns web-inbox paths.

**Call relations**: _chat_inbound uses these paths before _deliver_uploads copies or attaches files.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (PurePosixPath, inbox_name).


##### `_deliver_uploads`  (lines 1642–1663)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], presigned_keys: tuple[str, ...], rels: tuple[str, ...]) -> tuple[str, ...]
```

**Purpose**: Makes chat attachments available inside the conversation workspace before the agent turn runs.

**Data flow**: It streams inline uploads into blob storage, combines them with presigned keys, delivers each to the conversation path, and returns blob keys.

**Call relations**: _admit_chat calls it immediately before admitting the message and then records member-file attachments.

*Call graph*: calls 3 internal fn (deliver_attachment, store_inbound_file, _upload_chunks); called by 1 (_admit_chat); 1 external calls (PurePosixPath).


##### `_files_note`  (lines 1666–1669)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Adds a machine-readable note naming saved attachment paths to the admitted message text.

**Data flow**: It receives text and paths and returns text plus an attachment note, or just the note when the message is files-only.

**Call relations**: _chat_inbound writes this note; transcript rendering later removes it for display.

*Call graph*: called by 1 (_chat_inbound).


##### `_member_attachments`  (lines 1672–1680)

```
def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]
```

**Purpose**: Splits a stored member message back into visible words and attachment paths.

**Data flow**: It looks for the attachment note at the end of the text and returns cleaned words plus parsed paths.

**Call relations**: _member_bubble uses it so the UI shows file cards instead of the internal note.

*Call graph*: called by 1 (_member_bubble).


##### `_member_bubble`  (lines 1683–1695)

```
def _member_bubble(said: str, attached: Mapping[str, dict[str, object]]) -> dict[str, object]
```

**Purpose**: Builds the chat UI object for one member message.

**Data flow**: It extracts words and attachment paths, attaches matching file payloads or fallback cards, and returns a user bubble dictionary.

**Call relations**: _TranscriptRenderer._member and _conversation_messages use it for committed and live/pending messages.

*Call graph*: calls 2 internal fn (_member_attachments, _note_card); called by 2 (_member, _conversation_messages); 1 external calls (PurePosixPath).


##### `_note_card`  (lines 1698–1709)

```
def _note_card(path: str) -> dict[str, object]
```

**Purpose**: Creates a fallback file card when a historical attachment has only a saved path note.

**Data flow**: It extracts the filename, guesses an image media type when possible, and returns a card without links.

**Call relations**: _member_bubble uses it for older messages whose attachment artifact rows are missing.

*Call graph*: called by 1 (_member_bubble); 2 external calls (PurePosixPath, raster_image_media_type).


##### `_upload_chunks`  (lines 1712–1714)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded file in fixed-size chunks.

**Data flow**: It repeatedly reads from the UploadFile until empty and yields each chunk.

**Call relations**: _deliver_uploads passes this iterator to the storage layer for inline attachment uploads.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_key`  (lines 1717–1722)

```
def _answer_key(conversation_id: UUID, turn_id: UUID, index: int) -> str
```

**Purpose**: Builds the idempotency key for an answer to a specific question in a turn.

**Data flow**: It combines conversation id, asking turn id, and question index into one stable string.

**Call relations**: _admit_chat uses it to admit answer messages; _asks uses the same format to match answers back to question cards.

*Call graph*: called by 2 (_admit_chat, _asks).


##### `_answer_headers`  (lines 1725–1737)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Parses optional headers that mark a chat message as an answer to a question.

**Data flow**: It reads the answer turn and question index headers, validates them, and returns a tuple, none, or a malformed-header response.

**Call relations**: _chat_inbound calls it after body validation so bad answer metadata opens no conversation.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 1740–1749)

```
def _stop_header(request: Request) -> UUID | None | Response
```

**Purpose**: Parses the optional header that asks to stop a running turn.

**Data flow**: It reads the stop-turn header, validates it as a UUID, and returns that id, none, or a bad-header response.

**Call relations**: _chat_inbound calls it before parsing the message semantics; chat later routes stop requests to _stop_chat.

*Call graph*: called by 1 (_chat_inbound); 2 external calls (Response, UUID).


##### `_chat_inbound`  (lines 1770–1796)

```
async def _chat_inbound(ctx: SurfaceContext, request: Request) -> _ChatInbound | Response
```

**Purpose**: Validates all inbound chat-send details before choosing or opening a conversation.

**Data flow**: It parses stop headers, body text and files, verifies presigned uploads, computes inbox paths, adds attachment notes, checks size, and parses answer headers.

**Call relations**: chat calls it first so malformed sends, missing uploads, or invalid stop/answer requests do not create side effects.

*Call graph*: calls 6 internal fn (verify_upload_grant, _answer_headers, _files_note, _inbox_paths, _parse_inbound, _stop_header); called by 1 (chat); 2 external calls (__init__, Response).


##### `_new_chat_target`  (lines 1799–1824)

```
async def _new_chat_target(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, inbound: _ChatInbound) -> _ChatTarget | Response
```

**Purpose**: Creates the target information for a message that starts a new chat.

**Data flow**: It checks the agent is in the member's audience, rejects answer/stop requests without an existing conversation, opens a conversation, and returns its id/title.

**Call relations**: _resolve_chat_target calls it when the query parameter says conversation=new.

*Call graph*: calls 2 internal fn (allows, _open_conversation); called by 1 (_resolve_chat_target); 3 external calls (__init__, Response, uuid4).


##### `_existing_chat_target`  (lines 1827–1858)

```
async def _existing_chat_target(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID, inbound: _ChatInbound) -> _ChatTarge
```

**Purpose**: Builds target information for a message sent to an existing conversation.

**Data flow**: It verifies the member may use the conversation and optionally prepares a cross-surface comment notice, then returns conversation id/title/comment.

**Call relations**: _resolve_chat_target calls it for UUID conversation parameters.

*Call graph*: calls 4 internal fn (allows, _comment_notice, _commentable, _member_chat); called by 1 (_resolve_chat_target); 2 external calls (__init__, Response).


##### `_resolve_chat_target`  (lines 1861–1882)

```
async def _resolve_chat_target(ctx: SurfaceContext, request: Request, audience: WebAudience, agent_id: UUID, member_id: UUID, email: str, inbound: _ChatInbound) -> _ChatTarget | Response
```

**Purpose**: Turns the chat request's conversation query parameter into a concrete chat target.

**Data flow**: It requires the parameter, distinguishes new from UUID ids, and delegates to the new or existing target helper.

**Call relations**: chat calls it after validating the inbound message and before stopping or admitting anything.

*Call graph*: calls 2 internal fn (_existing_chat_target, _new_chat_target); called by 1 (chat); 3 external calls (Response, web_extension, UUID).


##### `_stop_chat`  (lines 1885–1898)

```
async def _stop_chat(ctx: SurfaceContext, request: Request, conversation_id: UUID, turn_id: UUID) -> Response
```

**Purpose**: Stops a running turn in a conversation if the member is allowed to stop it.

**Data flow**: It authorizes the turn, asks core to stop it in the conversation, and returns whether it ended plus any founded replacement turn id.

**Call relations**: chat calls it for empty sends carrying the stop-turn header.

*Call graph*: calls 2 internal fn (stop_turn, _member_turn); called by 1 (chat); 2 external calls (JSONResponse, Response).


##### `_admit_chat`  (lines 1901–1942)

```
async def _admit_chat(ctx: SurfaceContext, request: Request, target: _ChatTarget, inbound: _ChatInbound, member_id: UUID, email: str) -> Response
```

**Purpose**: Admits a member message into a conversation and returns the turn or arrival it joined.

**Data flow**: It computes an answer idempotency key when needed, delivers uploads, admits the message with context and optional comment text, records member files, and returns JSON details.

**Call relations**: chat calls it for ordinary messages after target resolution.

*Call graph*: calls 7 internal fn (admit, admitted_body, attach_member_files, _answer_key, _chat_source, _deliver_uploads, _turn_context); called by 1 (chat); 1 external calls (JSONResponse).


##### `chat`  (lines 1945–1980)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Main POST route for sending a portal chat message or stopping a turn.

**Data flow**: It authenticates, checks the agent, parses inbound content, resolves the conversation target, and either stops a turn or admits the message.

**Call relations**: This is the browser chat transport; it coordinates _chat_inbound, _resolve_chat_target, _stop_chat, and _admit_chat.

*Call graph*: calls 6 internal fn (_admit_chat, _agent_param, _audience_for, _chat_inbound, _resolve_chat_target, _stop_chat); 1 external calls (Response).


##### `_rendered_text`  (lines 1983–1994)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Extracts human-visible text from a stored message.

**Data flow**: It reads either a plain string or text blocks, removes injected context from user messages, trims whitespace, and returns text.

**Call relations**: Transcript rendering and title generation call it before showing or summarizing messages.

*Call graph*: called by 3 (_assistant, _member, _title_excerpt).


##### `_append_activity`  (lines 1997–1998)

```
def _append_activity(events: list[dict[str, str]], text: str) -> None
```

**Purpose**: Adds one activity event to a transcript event list.

**Data flow**: It receives a list and text, appends a dictionary marked as activity, and mutates the list.

**Call relations**: _stored_activity callers use it while building assistant and subagent work timelines.

*Call graph*: called by 2 (_assistant, _subagent_activity).


##### `_stored_activity`  (lines 2001–2013)

```
def _stored_activity(block: ToolUseBlock, result: ToolResultBlock) -> str | None
```

**Purpose**: Chooses the readable activity label for a tool call and its stored result.

**Data flow**: It prefers explicit activity text, then user descriptions, special-cases skill loading, and falls back to the tool name or none.

**Call relations**: _TranscriptRenderer._assistant and _subagent_activity use it to turn tool records into UI events.

*Call graph*: called by 2 (_assistant, _subagent_activity).


##### `_subagent_activity`  (lines 2035–2059)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

**Purpose**: Builds the visible work timeline for a spawned subagent run.

**Data flow**: It scans transcript messages for activity-bearing tool results and assistant notes, converts them to events, and returns a bounded list.

**Call relations**: _subagent_nodes calls it after reading each spawned run's transcript.

*Call graph*: calls 2 internal fn (_append_activity, _stored_activity); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 2062–2072)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

**Purpose**: Tries to parse a subagent finish answer as a JSON object.

**Data flow**: It receives answer text, decodes JSON when possible, and returns a dictionary payload or none.

**Call relations**: _run_answer uses it to display structured run output as readable prose.

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 2075–2098)

```
def _payload_prose(value: JsonValue) -> str
```

**Purpose**: Turns a structured JSON value into readable text.

**Data flow**: It formats strings, booleans, numbers, lists, and objects into plain prose, preserving empty collections as none.

**Call relations**: _run_answer calls it when a finish payload has multiple fields or non-simple structure.

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 2101–2117)

```
def _run_answer(answer: str) -> str
```

**Purpose**: Returns the human-readable answer text for a subagent or profile run.

**Data flow**: It parses structured finish payloads, unwraps single prose fields, formats richer payloads, or returns raw text.

**Call relations**: _subagent_nodes and _TranscriptAids.render use it so run pages and nested run cards show the same answer.

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (render, _subagent_nodes).


##### `_subagent_nodes`  (lines 2120–2159)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

**Purpose**: Builds a nested tree of spawned subagent runs for transcript display.

**Data flow**: It receives descendant turns, names their profiles or agent targets, reads bounded transcripts for activity, formats outputs, and nests children under parents.

**Call relations**: _transcript_aids uses it for stored transcript rendering, and _events uses it when a live terminal frame completes.

*Call graph*: calls 4 internal fn (list_agents, read_transcript, _run_answer, _subagent_activity); called by 2 (_events, _transcript_aids); 2 external calls (__init__, gather).


##### `_ReplyState.note_answer`  (lines 2170–2176)

```
def note_answer(self) -> '_ReplyState'
```

**Purpose**: Moves a pending assistant answer into the event list as a note when later work shows it was not the final reply.

**Data flow**: It copies pending events, inserts the current answer if under the note limit, clears the answer, and returns a new state.

**Call relations**: _TranscriptRenderer._assistant and _TranscriptRenderer._member call it while deciding where assistant text belongs.

*Call graph*: called by 2 (_assistant, _member); 1 external calls (replace).


##### `_TranscriptRenderer.render`  (lines 2193–2209)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Converts stored engine messages into the list of chat bubbles and replies the portal displays.

**Data flow**: It precomputes activity results, walks messages in order, routes assistant and member messages through helpers, flushes the final reply, and returns rendered dictionaries.

**Call relations**: _rendered_messages creates the renderer and calls this as the main transcript projection step.

*Call graph*: calls 3 internal fn (_assistant, _flush, _member); 1 external calls (__init__).


##### `_TranscriptRenderer._assistant`  (lines 2211–2229)

```
def _assistant(self, message: Message, activity: Mapping[str, ToolResultBlock], state: _ReplyState) -> _ReplyState
```

**Purpose**: Accumulates assistant text and activity events for the current reply.

**Data flow**: It extracts visible text, stores it as the possible answer, scans tool calls for activity, and returns an updated reply state.

**Call relations**: render calls it for assistant messages before member messages or final flush close the reply.

*Call graph*: calls 4 internal fn (note_answer, _append_activity, _rendered_text, _stored_activity); called by 1 (render); 1 external calls (replace).


##### `_TranscriptRenderer._member`  (lines 2231–2261)

```
def _member(self, message: Message, state: _ReplyState, rendered: list[dict[str, object]]) -> _ReplyState
```

**Purpose**: Turns a member-authored stored message into a user bubble and closes any prior assistant reply.

**Data flow**: It ignores machine-only prompts, detects turn references, flushes reply state as needed, skips messages already shown in question cards, and appends a member bubble.

**Call relations**: render calls it for non-assistant messages; it relies on _member_bubble and _flush.

*Call graph*: calls 4 internal fn (note_answer, _flush, _member_bubble, _rendered_text); called by 1 (render); 2 external calls (replace, member_message_text).


##### `_TranscriptRenderer._flush`  (lines 2263–2300)

```
def _flush(self, state: _ReplyState, rendered: list[dict[str, object]], *, include_subagents: bool) -> _ReplyState
```

**Purpose**: Emits the current assistant reply if it has any visible content or attached cards.

**Data flow**: It gathers answer text, events, subagents, questions, files, apps, and connect controls for the closing turn, appends a reply, and clears state.

**Call relations**: render and _member call it whenever a reply boundary is reached.

*Call graph*: called by 2 (_member, render); 1 external calls (replace).


##### `_rendered_messages`  (lines 2303–2378)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

**Purpose**: Convenience wrapper that renders messages with all optional transcript decorations.

**Data flow**: It builds a _TranscriptRenderer with defaults for missing maps, asks it to render, and returns the result.

**Call relations**: _TranscriptAids.render calls this after gathering subagents, speakers, questions, files, apps, and connects.

*Call graph*: called by 1 (render); 1 external calls (__init__).


##### `_asks`  (lines 2397–2429)

```
def _asks(conversation_id: UUID, turns: tuple[Turn, ...], admitted: tuple[KeyedAdmission, ...]) -> _Asks
```

**Purpose**: Builds question cards and answer attribution for a conversation.

**Data flow**: It reads turns and keyed admissions, matches answers by _answer_key, marks older answered questions closed, and returns cards plus message refs already stated.

**Call relations**: _transcript_aids calls it so transcript rendering can put answers under the question that asked them.

*Call graph*: calls 1 internal fn (_answer_key); called by 1 (_transcript_aids); 2 external calls (__init__, member_message_text).


##### `_TranscriptAids.render`  (lines 2452–2471)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Renders messages using the pre-gathered transcript side data.

**Data flow**: It passes subagents, speakers, questions, files, apps, connects, attachments, and answer refs into _rendered_messages, then rewrites run replies if needed.

**Call relations**: _conversation_messages and _history_messages use _TranscriptAids so live tail and older pages render identically.

*Call graph*: calls 2 internal fn (_rendered_messages, _run_answer).


##### `_transcript_aids`  (lines 2474–2532)

```
async def _transcript_aids(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, agent_origin: frozenset[str], speakers: dict[str, str], asked: dict[str, str], opens: frozenset[UUI
```

**Purpose**: Collects all non-message data needed to render a conversation transcript.

**Data flow**: It reads turns, spawned turns, artifacts, keyed admissions, created apps, connect controls, speakers, asks, and attachment maps, then returns a _TranscriptAids object.

**Call relations**: _conversation_messages and _history_messages call it before rendering stored or compacted transcript windows.

*Call graph*: calls 9 internal fn (conversation_subagent_turns, keyed_admissions, list_conversation_artifacts, list_turns, _asks, _connect_controls, _created_apps, _file_payload, _subagent_nodes); called by 2 (_conversation_messages, _history_messages); 2 external calls (__init__, gather).


##### `_connect_controls`  (lines 2535–2575)

```
async def _connect_controls(ctx: SurfaceContext, conversation_id: UUID, turns: tuple[Turn, ...], viewer: UUID) -> dict[str, dict[str, object]]
```

**Purpose**: Builds connect-account controls that should appear under replies for this viewer.

**Data flow**: It scans terminal frames for connect requests owned by the viewer, shows a fresh connect control if open, or shows the landed account label if completed.

**Call relations**: _transcript_aids calls it so transcript loads match what the live stream showed.

*Call graph*: calls 4 internal fn (connect_available, held_accounts, _connect_control, _provider_label); called by 1 (_transcript_aids).


##### `_conversation_messages`  (lines 2578–2707)

```
async def _conversation_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], Turn | None, int]
```

**Purpose**: Projects a conversation into portal chat messages, including committed transcript, live turn prompt, and queued arrivals.

**Data flow**: It reads transcript, origins, speakers, compactions, latest turn detail, attachments, and queue rows, renders committed messages, appends live/pending member bubbles, and returns messages, live turn, and earlier-page marker.

**Call relations**: transcript and conversation_transcript call it for chat loads and read-only conversation views.

*Call graph*: calls 12 internal fn (agent_origin_refs, arrival_speakers, latest_turn, list_compactions, queued_arrivals, read_transcript, shared_artifacts, turn_detail, _file_payload, _member_bubble (+2 more)); called by 2 (conversation_transcript, transcript); 2 external calls (gather, member_message_text).


##### `_verified_earlier`  (lines 2710–2726)

```
async def _verified_earlier(ctx: SurfaceContext, conversation_id: UUID, indices: tuple[int, ...], messages: tuple[Message, ...]) -> int
```

**Purpose**: Finds the newest valid compaction page that actually sits above the current transcript window.

**Data flow**: It checks compaction records from newest to oldest and returns the index whose after-window matches the message prefix, or 0.

**Call relations**: _conversation_messages and _history_messages use it before advertising an earlier history cursor.

*Call graph*: calls 1 internal fn (read_compaction_after); called by 2 (_conversation_messages, _history_messages).


##### `_history_cursor`  (lines 2729–2731)

```
def _history_cursor(index: int, end: int | None=None) -> str
```

**Purpose**: Encodes a compacted-history position into an opaque cursor string.

**Data flow**: It combines compaction index and optional end position, base64-url encodes it, and strips padding.

**Call relations**: Transcript routes and history paging helpers use it to hand the browser a safe scroll-up token.

*Call graph*: called by 4 (fits, _history_messages, conversation_transcript, transcript); 1 external calls (urlsafe_b64encode).


##### `_history_position`  (lines 2734–2749)

```
def _history_position(cursor: str) -> tuple[int, int | None]
```

**Purpose**: Decodes and validates a compacted-history cursor.

**Data flow**: It base64-decodes the cursor, parses index and optional end position, checks shape and bounds, and returns numbers or raises ValueError.

**Call relations**: _history_messages calls it before reading a compaction page.

*Call graph*: called by 1 (_history_messages); 1 external calls (b64decode).


##### `_bounded_history_page`  (lines 2752–2774)

```
def _bounded_history_page(messages: list[dict[str, object]], index: int, end: int) -> tuple[list[dict[str, object]], int]
```

**Purpose**: Cuts a rendered history window into a page that fits message-count and byte-size limits.

**Data flow**: It starts near the requested end, checks response size, binary-searches if needed, and returns the chosen messages plus start index.

**Call relations**: _history_messages uses it after rendering an older compacted window.

*Call graph*: called by 1 (_history_messages).


##### `_bounded_history_page.fits`  (lines 2757–2762)

```
def fits(start: int) -> bool
```

**Purpose**: Tests whether a candidate history slice fits the JSON response byte limit.

**Data flow**: It builds the response payload for a slice, serializes it, and returns whether its body is small enough.

**Call relations**: _bounded_history_page uses it during initial check and binary search.

*Call graph*: calls 1 internal fn (_history_cursor); 1 external calls (JSONResponse).


##### `_history_messages`  (lines 2777–2828)

```
async def _history_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, cursor: str, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], str | None] | None
```

**Purpose**: Returns one earlier page from a compacted conversation transcript.

**Data flow**: It decodes the cursor, reads the compaction record, removes the kept overlap, renders the older window with transcript aids, bounds the page, and computes the next cursor.

**Call relations**: _conversation_history calls it when the browser scrolls above the current transcript tail.

*Call graph*: calls 8 internal fn (agent_origin_refs, arrival_speakers, read_compaction, _bounded_history_page, _history_cursor, _history_position, _transcript_aids, _verified_earlier); called by 1 (_conversation_history); 1 external calls (gather).


##### `transcript`  (lines 2831–2875)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Loads one member chat conversation for the portal chat pane.

**Data flow**: It authenticates, checks the agent and conversation, verifies chat access, renders messages, includes earlier-history cursor, and reports live turn or open credential handoffs.

**Call relations**: The frontend calls this on conversation load; live updates then come from stream.

*Call graph*: calls 7 internal fn (_agent_param, _audience_for, _conversation_messages, _history_cursor, _member_chat, _open_handoffs, _opens); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 2878–2891)

```
async def _open_handoffs(ctx: SurfaceContext, terminal: TerminalFrame, member_id: UUID) -> dict[str, object]
```

**Purpose**: Returns still-open handoffs from the newest committed terminal frame.

**Data flow**: It currently checks pending credential requests, renews prompts for the member, and returns them when any remain.

**Call relations**: transcript uses it so a page reload still shows credential prompts that live streaming previously showed.

*Call graph*: calls 1 internal fn (_pending_prompts); called by 1 (transcript).


##### `_connect_control`  (lines 2894–2898)

```
def _connect_control(ctx: SurfaceContext, provider: str, turn_id: UUID) -> dict[str, object]
```

**Purpose**: Builds the small UI object for pressing a connect-account handoff.

**Data flow**: It receives provider and turn id, adds the provider label, and returns provider, label, and turn id.

**Call relations**: _connect_controls and _events use it for stored and live connect controls.

*Call graph*: calls 1 internal fn (_provider_label); called by 2 (_connect_controls, _events).


##### `_provider_label`  (lines 2901–2912)

```
def _provider_label(ctx: SurfaceContext, provider: str) -> str
```

**Purpose**: Finds the display name for an account provider.

**Data flow**: It prefers the curated first-run provider catalog, otherwise asks the connect system when available, otherwise returns the provider slug.

**Call relations**: Connect controls and agent_setup use it so account names are consistent across the portal.

*Call graph*: calls 2 internal fn (connect_available, connect_label); called by 3 (_connect_control, _connect_controls, agent_setup).


##### `_provider_summary`  (lines 2915–2922)

```
def _provider_summary(provider: str) -> str
```

**Purpose**: Finds the short explanatory line for a curated account provider.

**Data flow**: It searches the first-run provider catalog and returns the provider summary or an empty string.

**Call relations**: agent_setup uses it when describing connector setup requirements.

*Call graph*: called by 1 (agent_setup).


##### `chats_index`  (lines 2925–2937)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Resolves a conversation permalink into either a chat rail row or a read-only conversation row.

**Data flow**: It authenticates, requires a conversation query parameter, and delegates resolution to _resolve_chat.

**Call relations**: The browser calls this for #/c/<id> links instead of listing every conversation here.

*Call graph*: calls 2 internal fn (_audience_for, _resolve_chat); 2 external calls (Response, web_extension).


##### `_resolve_chat`  (lines 2940–3028)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

**Purpose**: Finds what a permalinked conversation means for the current member.

**Data flow**: It parses the id, checks member chat access across chat agents, returns chat metadata for portal chats, commentable conversation metadata for other surfaces, or empty results.

**Call relations**: chats_index delegates to it; it shares gates with _member_chat and row shaping with _conversation_row.

*Call graph*: calls 9 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, allows, _commentable, _conversation_row, _iso, _member_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 3031–3044)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Common authorization gate for per-agent panel routes and intent submission.

**Data flow**: It authenticates, computes audience, parses the path agent id, checks the agent is visible to the member, and returns member/email/audience/agent id.

**Call relations**: Settings, setup, skills, conversations, actions, intents, homepage, and connections use this shared gate.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 11 (_readable_conversation, actions, agent_setup, community_skill, community_skills, connections, conversations, homepage, intents, settings (+1 more)); 1 external calls (Response).


##### `_iso`  (lines 3047–3048)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Formats optional datetimes for JSON responses.

**Data flow**: It returns none for none, otherwise calls isoformat on the datetime.

**Call relations**: Many response builders use it so timestamps are consistently encoded.

*Call graph*: called by 6 (_conversation_row, _memory_rows, _resolve_chat, _usage_payload, agents_status, object_detail); 1 external calls (isoformat).


##### `_window_param`  (lines 3051–3067)

```
def _window_param(request: Request) -> int | None | Response
```

**Purpose**: Parses the requested usage-report time window.

**Data flow**: It accepts named ranges or window_seconds, validates bounds, and returns seconds, none for all time, or an error response.

**Call relations**: workspace_usage calls it before reading member and workspace spend reports.

*Call graph*: called by 1 (workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 3070–3110)

```
def _usage_payload(report: MemberSpendReport | SpendReport) -> dict[str, object]
```

**Purpose**: Converts detailed usage reports into portal JSON.

**Data flow**: It reads token, cost, daily, execution, and model breakdown fields and returns nested dictionaries and lists.

**Call relations**: workspace_usage uses it for both member usage and admin workspace rollup usage.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_usage).


##### `skills`  (lines 3113–3137)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists skills available to a selected agent.

**Data flow**: It authorizes the panel, reads agent skills, and returns names, descriptions, origins, instructions, dependencies, and target agents.

**Call relations**: The skills panel uses this read; edits and installs happen through object or intent routes.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 3143–3147)

```
def _community_refusal(fault: Exception) -> Response
```

**Purpose**: Turns a community directory failure into a member-readable response.

**Data flow**: It uses the exception text as the body, returns status 502, and marks it with the refusal header.

**Call relations**: community_skills and community_skill use it when the external community service is unavailable.

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 3154–3170)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists or searches community skills for the selected agent.

**Data flow**: It authorizes the panel, validates minimum query length, asks the community directory, and returns skill summaries or a readable refusal.

**Call relations**: The community skills panel calls this before submitting an install intent.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 3173–3194)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches one community skill document for review.

**Data flow**: It authorizes, validates GitHub owner/repo and skill-name shapes, fetches the document, and returns it or not found/refusal.

**Call relations**: The skill detail view calls this before the member applies the skill through the intent lane.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 3197–3272)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reads recent or searched memory visible to the member across reachable agents.

**Data flow**: It authenticates, returns empty unavailable state if memory is off, lists recent memory by subject/kind/cursor or searches each reachable agent and deduplicates results.

**Call relations**: The workspace memory panel uses it and receives collection actions shaped by _action_payloads.

*Call graph*: calls 7 internal fn (object_actions, recent_memory, search_memory, decode, _action_payloads, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 3275–3285)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Formats memory matches for JSON.

**Data flow**: It converts each match into kind, text, reference, timestamp, and subject fields.

**Call relations**: workspace_memory uses it for both recent listings and search results.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `connections`  (lines 3288–3297)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connector accounts visible for one selected agent.

**Data flow**: It authorizes the panel, reads agent connections for the member with admin widening when allowed, and returns them.

**Call relations**: The agent connections panel calls this to show private and shared account edges.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 3300–3310)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connector accounts visible to the member across the workspace.

**Data flow**: It authenticates, reads connections, filters each connection's agent list to agents in the web audience, and returns the result.

**Call relations**: Workspace-level connector screens use this broader pool read.

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 3313–3319)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns GitHub coverage information visible to the member.

**Data flow**: It authenticates, asks core for coverage with admin scope when allowed, and returns the model dump.

**Call relations**: A GitHub-related portal panel uses this as its read endpoint.

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 3322–3353)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists conversations for one selected agent that the member may know about.

**Data flow**: It authorizes, reads one row more than the display limit with optional search, shapes rows, and returns whether more rows exist.

**Call relations**: The agent conversations panel uses this listing; individual transcript reads are gated separately.

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 3356–3360)

```
def _searched(request: Request) -> str | None
```

**Purpose**: Extracts a bounded search string from a request.

**Data flow**: It reads q, trims whitespace, cuts it to the maximum search length, and returns none when empty.

**Call relations**: conversations passes it to the core conversation listing.

*Call graph*: called by 1 (conversations).


##### `_conversation_row`  (lines 3363–3397)

```
def _conversation_row(entry: ListedConversation, member_id: UUID, agent: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Formats one conversation listing row for the portal.

**Data flow**: It combines conversation summary, agent info, source, speakers, timestamps, readability, disclosure, and commentability into a dictionary.

**Call relations**: conversations and _resolve_chat use it for panel rows and permalink resolution.

*Call graph*: calls 2 internal fn (_commentable, _iso); called by 2 (_resolve_chat, conversations).


##### `_readable_conversation`  (lines 3400–3420)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes a read-only conversation content request.

**Data flow**: It runs the panel gate, parses or receives the conversation id, asks core whether it is readable for this member/admin, and returns agent id, conversation id, and viewer info.

**Call relations**: conversation_transcript, _conversation_history, and _slot_target use it as their shared content gate.

*Call graph*: calls 3 internal fn (readable_conversation, _opens, _panel_gate); called by 3 (_conversation_history, _slot_target, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 3423–3441)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a read-only transcript for an authorized agent conversation.

**Data flow**: It routes cursor requests to history, otherwise authorizes the conversation, renders messages, adds earlier cursor if needed, and returns JSON.

**Call relations**: The conversations panel calls this for conversations that are not necessarily the member's own active chat.

*Call graph*: calls 4 internal fn (_conversation_history, _conversation_messages, _history_cursor, _readable_conversation); 1 external calls (JSONResponse).


##### `_member_chat_page`  (lines 3444–3472)

```
async def _member_chat_page(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes a history page request for a member's own chat conversation.

**Data flow**: It authenticates, parses agent and conversation ids, verifies chat access with _member_chat, and returns viewer info.

**Call relations**: _conversation_history falls back to this when the stricter read-only panel gate does not apply.

*Call graph*: calls 4 internal fn (_agent_param, _audience_for, _member_chat, _opens); called by 1 (_conversation_history); 4 external calls (__init__, Response, web_extension, UUID).


##### `_conversation_history`  (lines 3475–3496)

```
async def _conversation_history(ctx: SurfaceContext, request: Request, cursor: str) -> Response
```

**Purpose**: Returns one earlier transcript page for either a readable conversation or a member chat.

**Data flow**: It authorizes through _readable_conversation or _member_chat_page, reads the cursor page with _history_messages, and returns messages plus next cursor.

**Call relations**: conversation_transcript delegates cursor requests here.

*Call graph*: calls 3 internal fn (_history_messages, _member_chat_page, _readable_conversation); called by 1 (conversation_transcript); 2 external calls (JSONResponse, Response).


##### `_slot_target`  (lines 3516–3540)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

**Purpose**: Authorizes the conversation targeted by a conversation slot request.

**Data flow**: It handles ordinary conversation ids or subagent conversation ids under a root conversation, verifies readability, and returns a SlotTarget.

**Call relations**: conversation_slots and conversation_slot call it before summarizing or reading typed slot content.

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 3543–3559)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

**Purpose**: Builds the context object passed to a conversation slot provider.

**Data flow**: It reads the conversation audience and transcript, combines them with extension context, ids, messages, and public base URL, and returns context or none.

**Call relations**: conversation_slots and conversation_slot call it before provider-specific projection.

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 3562–3646)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

**Purpose**: Adds host-side projections or visible item lists needed by certain conversation slot types.

**Data flow**: It detects changes, artifacts, sites, or automations slots and reads the relevant workspace changes, artifacts, or member-visible objects into the context.

**Call relations**: conversation_slots and conversation_slot call it so providers receive already-authorized supporting data.

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 7 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit).


##### `_authorized_slot_payload`  (lines 3649–3693)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

**Purpose**: Filters a slot payload so it only includes items the viewer may open or see.

**Data flow**: It removes unauthorized sites, and for automations removes hidden rows or redacts content fields when the row is visible but content is not.

**Call relations**: conversation_slot applies it after the provider returns its payload.

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 3696–3740)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists which typed side panels are available for a conversation.

**Data flow**: It authorizes the target, builds shared slot context, asks each provider for a count, logs failed providers, and returns available slot summaries.

**Call relations**: The transcript side-panel UI calls this before opening an individual slot.

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 3743–3770)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reads one typed side-panel payload for a conversation.

**Data flow**: It authorizes, finds the requested provider, builds and projects context, reads the provider payload, verifies its type, filters authorization, and returns JSON.

**Call relations**: The browser calls this after conversation_slots says a slot exists.

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 3773–3776)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Retrieves the workspace-changes projection from a slot context.

**Data flow**: It checks the projection is a WorkspaceChanges object and returns it, otherwise raises an internal error.

**Call relations**: _read_changes and _summarize_changes use it for the built-in changes slot.

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 3779–3780)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Returns the full changes-slot payload.

**Data flow**: It receives a slot context and returns its validated WorkspaceChanges projection.

**Call relations**: CHANGES_SLOT uses this as its read function.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 3783–3784)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Returns the count for the changes slot, or none when empty.

**Data flow**: It counts changes in the projection and returns the count if nonzero.

**Call relations**: CHANGES_SLOT uses this as its summarize function for conversation_slots.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 3797–3800)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Retrieves the artifacts projection from a slot context.

**Data flow**: It checks the projection is an ArtifactsSlotPayload and returns it, otherwise raises an internal error.

**Call relations**: _read_artifacts and _summarize_artifacts use it for the built-in artifacts slot.

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 3803–3804)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Returns the full artifacts-slot payload.

**Data flow**: It receives a slot context and returns its validated artifacts projection.

**Call relations**: ARTIFACTS_SLOT uses this as its read function.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 3807–3809)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Returns the count for the artifacts slot, or none when empty.

**Data flow**: It counts artifacts in the projection and returns the count if nonzero.

**Call relations**: ARTIFACTS_SLOT uses this as its summarize function for conversation_slots.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `workspace_credentials`  (lines 3822–3834)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists workspace credential slots and whether they are filled, without exposing secret values.

**Data flow**: It authenticates, reads credential slots, attaches available collection actions, and returns JSON.

**Call relations**: The workspace credentials panel calls this; mutations go through declared actions.

*Call graph*: calls 4 internal fn (list_credential_slots, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 3837–3862)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists workspace members and whether the current reader can manage them.

**Data flow**: It authenticates, reads all members, includes id, email, admin, and seated state, adds member collection actions, and returns can_manage from audience admin status.

**Call relations**: The team panel uses this; action routes still enforce authority separately.

*Call graph*: calls 4 internal fn (list_members, object_actions, _action_payloads, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 3865–3876)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the member.

**Data flow**: It authenticates, reads member/admin-scoped sources, and returns their serialized rows.

**Call relations**: The workspace sources panel uses this to show private and shared source registrations.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `_connect_declared`  (lines 3890–3896)

```
def _connect_declared(ctx: SurfaceContext, surface: str, action: str) -> bool
```

**Purpose**: Checks whether this deployment actually declares the action needed to connect a surface.

**Data flow**: It looks through object actions for the surface instance and returns whether the connect action is present.

**Call relations**: workspace_surfaces uses it before offering Slack or iMessage connect buttons.

*Call graph*: calls 1 internal fn (object_actions); called by 1 (workspace_surfaces).


##### `workspace_surfaces`  (lines 3899–3950)

```
async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reports installed chat surfaces and connectable surface options for this member.

**Data flow**: It authenticates, reads installations and member surface links, filters installations by audience, builds Slack, iMessage, and terminal rows, and returns them.

**Call relations**: The surfaces/workspace topology screens use this to show what external clients are connected.

*Call graph*: calls 4 internal fn (list_installations, member_surfaces, _audience_for, _connect_declared); 3 external calls (__init__, JSONResponse, imessage_offered).


##### `workspace_first_run`  (lines 3979–4027)

```
async def workspace_first_run(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns setup data for the first-run onboarding screens.

**Data flow**: It authenticates, reads installations, model-key state, founding domain, connector install state, and available actions, then returns provider and setup choices.

**Call relations**: The first-run and connect pages use this shared catalog read.

*Call graph*: calls 7 internal fn (founding_domain, list_installations, member_holds_own_model_key, object_actions, object_kind, _action_payloads, _audience_for); 3 external calls (__init__, gather, JSONResponse).


##### `connector_catalog`  (lines 4030–4058)

```
async def connector_catalog(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists broker-provided account connectors available to connect.

**Data flow**: It authenticates, validates search text and cursor length, asks core for a limited catalog page, and returns provider tiles plus next cursor.

**Call relations**: The connector page calls this when browsing or searching installed broker providers.

*Call graph*: calls 2 internal fn (connector_catalog, _audience_for); 3 external calls (__init__, JSONResponse, Response).


##### `_held_providers`  (lines 4109–4117)

```
async def _held_providers(ctx: SurfaceContext, member_id: UUID, *, admin: bool) -> frozenset[str]
```

**Purpose**: Computes which providers the workspace/member already has connected for starter decisions.

**Data flow**: It reads connections and surface installations, collects provider names, adds Slack when installed, and returns a frozen set.

**Call relations**: workspace_starters calls it before turning ranked starter ideas into ready apps or unlocks.

*Call graph*: calls 2 internal fn (list_connections, list_installations); called by 1 (workspace_starters).


##### `fill_starters`  (lines 4120–4209)

```
def fill_starters(slate: Slate, held: frozenset[str], taken: frozenset[str], installed: tuple[StarterApp, ...]=()) -> tuple[tuple[StarterRow, ...], UnlockRow | None]
```

**Purpose**: Selects which starter rows and unlock row the start screen should show.

**Data flow**: It receives ranked ideas, held providers, installed app state, and taken app names; it fills app slots, chooses short-missing unlocks, and appends a check-in when present.

**Call relations**: workspace_starters calls it after loading or generating the member's slate.

*Call graph*: called by 1 (workspace_starters); 4 external calls (__init__, __init__, __init__, get).


##### `_solvent`  (lines 4212–4221)

```
async def _solvent() -> bool
```

**Purpose**: Checks whether the workspace has enough balance headroom to spend on starter generation.

**Data flow**: It opens an extension transaction, reads balance headroom, and returns true when unlimited or above reserve/grace threshold.

**Call relations**: workspace_starters passes this into StarterCache so a refusing workspace does not trigger model spending.

*Call graph*: called by 1 (workspace_starters); 2 external calls (read_headroom, web_extension).


##### `_recalled`  (lines 4224–4231)

```
async def _recalled(ctx: SurfaceContext, member_id: UUID) -> tuple[str, ...]
```

**Purpose**: Reads a bounded set of recent memory snippets for starter generation.

**Data flow**: It checks memory availability, reads recent memory for the member and shared subjects, truncates each text, and returns strings.

**Call relations**: workspace_starters gives these snippets to StarterCache to personalize suggestions.

*Call graph*: calls 1 internal fn (recent_memory); called by 1 (workspace_starters); 2 external calls (audience_subjects, conversation_audience).


##### `workspace_starters`  (lines 4234–4292)

```
async def workspace_starters(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns personalized starter suggestions for what the member can do next.

**Data flow**: It authenticates, reads installed app setup, recalls memory, checks solvency, loads or generates a slate, reads held providers, filters taken names, and returns starter/unlock rows.

**Call relations**: The start screen calls this lazily; fill_starters performs the final access-aware selection.

*Call graph*: calls 7 internal fn (agent_setup, _audience_for, _held_providers, _recalled, _setup_configured, _solvent, fill_starters); 5 external calls (__init__, __init__, gather, JSONResponse, web_extension).


##### `workspace_usage`  (lines 4295–4370)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns spending and token usage for the member, plus workspace rollup for admins.

**Data flow**: It authenticates, parses the time window, reads member spend, shapes usage and caps, and if admin adds workspace totals by dimension/member/agent/origin.

**Call relations**: The usage panel calls this; _window_param and _usage_payload keep parsing and formatting consistent.

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `connect_handoff`  (lines 4376–4400)

```
async def connect_handoff(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts the outbound provider-consent leg for one turn's connect request.

**Data flow**: It authorizes the member's turn, asks core for a fresh consent URL, redirects there, or returns a callback page if the request expired.

**Call relations**: Connect controls generated by transcripts and live streams point at this route.

*Call graph*: calls 2 internal fn (connect_url, _member_turn); 2 external calls (callback_page, RedirectResponse).


##### `_member_turn`  (lines 4403–4456)

```
async def _member_turn(ctx: SurfaceContext, request: Request, *, named_turn: UUID | None=None, allow_commentable: bool=False) -> tuple[UUID, UUID, str] | Response
```

**Purpose**: Authorizes access to a turn as the signed-in member.

**Data flow**: It authenticates, parses or receives a turn id, reads turn detail and owner, checks agent visibility or commentable conversation access, and returns member id, turn id, and email.

**Call relations**: _stop_chat, connect_handoff, and stream use it before touching a turn.

*Call graph*: calls 5 internal fn (turn_detail, turn_owner, _audience_for, _commentable, _member_chat); called by 3 (_stop_chat, connect_handoff, stream); 3 external calls (Response, web_extension, UUID).


##### `stream`  (lines 4459–4467)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens the Server-Sent Events stream for live updates from one turn.

**Data flow**: It authorizes the turn, reads the browser's last event id, and returns a streaming response over _events.

**Call relations**: The chat pane calls this after admission or reload to watch an agent reply live.

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 4470–4472)

```
def _event(name: str, payload: dict[str, object], cursor: str='') -> bytes
```

**Purpose**: Formats one named Server-Sent Events message.

**Data flow**: It receives an event name, payload, and optional cursor, JSON-encodes the payload, and returns bytes in SSE format.

**Call relations**: _events uses it for custom web events such as files, apps, subagents, and credentials.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 4475–4491)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest, member_id: UUID) -> dict[str, object] | None
```

**Purpose**: Builds credential prompts that are still awaiting values for this member.

**Data flow**: It renews the sealed credential request, checks each prompt's pending state, and returns reason, renewed seal, and prompt rows or none.

**Call relations**: _events and _open_handoffs use it for live and reload views of credential handoffs.

*Call graph*: calls 2 internal fn (credential_prompt_pending, renew_credential_request); called by 2 (_events, _open_handoffs).


##### `_file_payload`  (lines 4494–4510)

```
def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]
```

**Purpose**: Formats a shared artifact as the portal's file card.

**Data flow**: It reads artifact metadata and creates download and preview links using the surface context.

**Call relations**: _transcript_aids, _conversation_messages, and _events call it when showing shared or attached files.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 3 (_conversation_messages, _events, _transcript_aids).


##### `_opens`  (lines 4513–4514)

```
def _opens(audience: WebAudience) -> frozenset[UUID]
```

**Purpose**: Returns the set of agent ids the web audience may open.

**Data flow**: It extracts ids from the audience's agents and returns them as a frozen set.

**Call relations**: Transcript and slot gates use it when deciding whether created app cards should be visible.

*Call graph*: called by 4 (_events, _member_chat_page, _readable_conversation, transcript).


##### `_created_apps`  (lines 4517–4548)

```
async def _created_apps(ctx: SurfaceContext, created: Mapping[str, tuple[ObjectRef, ...]], opens: frozenset[UUID]) -> dict[str, list[dict[str, object]]]
```

**Purpose**: Builds application cards for agents created by turns, limited to apps the viewer can open.

**Data flow**: It gathers created agent object refs, resolves current agent rows, filters by the viewer's open set, and returns cards keyed by turn id.

**Call relations**: _transcript_aids uses it for stored transcripts and _events uses it for live terminal frames.

*Call graph*: calls 1 internal fn (list_agents); called by 2 (_events, _transcript_aids).


##### `_events`  (lines 4551–4606)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str, email: str) -> AsyncIterator[bytes]
```

**Purpose**: Converts core live frames for a turn into the browser's SSE stream.

**Data flow**: It tails core frames, emits file updates, subagent trees, connect controls, credential prompts, created app cards, and passes through mapped live frames.

**Call relations**: stream returns this async iterator to the browser.

*Call graph*: calls 13 internal fn (connect_available, conversation_subagent_turns, shared_artifacts, tail, turn_detail, _connect_control, _created_apps, _event, _file_payload, _opens (+3 more)); called by 1 (stream); 2 external calls (web_audience, web_extension).


##### `fulfill_credential`  (lines 4609–4639)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores one credential value entered privately by the member.

**Data flow**: It authenticates, bounds and parses the form, validates required fields and secret size, asks core to fulfill the sealed request, and returns stored or an error.

**Call relations**: Credential handoff forms submit here; the value never becomes chat text.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `_object_gate`  (lines 4642–4656)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

**Purpose**: Common gate for generic object index and detail pages.

**Data flow**: It authenticates, computes audience, reads the requested object kind declaration, and returns member id, audience, and kind or not found.

**Call relations**: object_index and object_detail call it before entering a kind-specific namespace.

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 4659–4669)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

**Purpose**: Selects and authorizes the agent namespace for an object read.

**Data flow**: It parses the agent query parameter, finds that agent in the web audience, and returns the agent or not found.

**Call relations**: object_index uses it for single-agent reads; object_detail always needs it.

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_action_payloads`  (lines 4672–4673)

```
def _action_payloads(views: tuple[ActionView, ...]) -> list[dict[str, object]]
```

**Purpose**: Serializes declared action views for portal JSON.

**Data flow**: It receives action view objects and returns their JSON-ready dictionaries without null fields.

**Call relations**: Workspace panels and action_views use it wherever actions are advertised.

*Call graph*: called by 5 (action_views, workspace_credentials, workspace_first_run, workspace_memory, workspace_team).


##### `_kind_payload`  (lines 4676–4683)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

**Purpose**: Builds shared metadata for an object kind page.

**Data flow**: It returns kind name, list fields, spec schema, and whether apply/delete intents exist for that kind.

**Call relations**: object_index and object_detail include this alongside rows or detail.

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 4686–4693)

```
def _filter_value(raw: str) -> JsonValue
```

**Purpose**: Parses one query-string filter value into the scalar type object rows may use.

**Data flow**: It tries JSON parsing so true, numbers, and null become typed values, falling back to the original string.

**Call relations**: object_index uses it when building ObjectListQuery filters from extra query parameters.

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_fanout_token`  (lines 4696–4701)

```
def _fanout_token(walking: dict[str, str]) -> str | None
```

**Purpose**: Encodes per-agent cursors for a multi-agent object listing.

**Data flow**: It receives a map of agent id strings to cursors, returns none if empty, otherwise JSON-encodes and hex-encodes it.

**Call relations**: object_index returns this as next_cursor when listing one kind across many agents.

*Call graph*: called by 1 (object_index); 1 external calls (dumps).


##### `_fanout_walks`  (lines 4704–4721)

```
def _fanout_walks(token: str) -> dict[UUID, str] | None
```

**Purpose**: Decodes a multi-agent object listing cursor.

**Data flow**: It hex-decodes and JSON-parses the token, validates nonempty string cursors under UUID agent ids, and returns a UUID-to-cursor map or none.

**Call relations**: object_index calls it when continuing a fanned-out object listing.

*Call graph*: called by 1 (object_index); 2 external calls (loads, UUID).


##### `_merged_rank`  (lines 4724–4737)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```

**Purpose**: Computes a sort key for merging object rows from multiple agents.

**Data flow**: It reads the requested order field, groups absent, boolean, numeric, and text values, and breaks ties by object name.

**Call relations**: object_index uses it after collecting per-agent pages in a fan-out read.


##### `object_index`  (lines 4740–4832)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists rows of a generic object kind visible to the signed-in member.

**Data flow**: It gates kind and audience, parses ordering, filters, search, cursor, and optional agent, reads rows from one or many agents, merges fan-out results, and returns rows plus next cursor.

**Call relations**: Generic object index pages use this for kinds such as artifacts, credentials, sites, or extension-defined objects.

*Call graph*: calls 7 internal fn (list_member_objects, _fanout_token, _fanout_walks, _filter_value, _kind_payload, _object_agent, _object_gate); 4 external calls (__init__, replace, JSONResponse, Response).


##### `object_detail`  (lines 4835–4882)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reads one generic object row and its detail for the member.

**Data flow**: It gates kind and agent, reads the named object, checks linked objects' openability, and returns summary, spec when visible, status fields, links, generation, and timestamps.

**Call relations**: Generic object detail pages call this after object_index rows are selected.

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 4885–4917)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Maps a core live frame into a standard SSE event.

**Data flow**: It receives a cursor and frame, chooses the event name by frame type, serializes the frame, and returns bytes.

**Call relations**: _events calls it for ordinary core frames after adding any web-specific side events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 4920–4925)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Submits a prepared intent for a selected agent.

**Data flow**: It authorizes the panel and passes context, request, agent, member, and email to the panel intent handler.

**Call relations**: The route table uses it for agent intent POSTs; actual intent parsing lives in ufo_ext_web.panels.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `actions`  (lines 4928–4946)

```
async def actions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Dispatches a presented object action for a selected agent.

**Data flow**: It authorizes, reads kind/action/name path parameters, and hands the request to submit_action with member and agent identity.

**Call relations**: Portal buttons that invoke declared actions post here; action authority is rechecked by the action machinery.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_action).


##### `action_views`  (lines 4949–4963)

```
async def action_views(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists actions declared for an object kind or one named object.

**Data flow**: It authenticates, verifies the kind exists, chooses collection or instance binding, serializes declared actions, and returns them.

**Call relations**: Panels use it to draw controls even when the target row itself is not read through the generic object page.

*Call graph*: calls 4 internal fn (object_actions, object_kind, _action_payloads, _audience_for); 2 external calls (JSONResponse, Response).


##### `_write_agent`  (lines 4970–4988)

```
def _write_agent(request: Request, audience: WebAudience, stated: object=None) -> AgentSummary | Response
```

**Purpose**: Chooses the agent namespace for a direct object write.

**Data flow**: It prefers a body-supplied agent id, then query agent id, otherwise the main agent, and verifies the chosen agent is in the audience.

**Call relations**: object_write calls it for both apply and delete writes.

*Call graph*: called by 1 (object_write); 2 external calls (Response, UUID).


##### `_direct_result`  (lines 4991–4995)

```
def _direct_result(frame: TerminalFrame, name: str) -> Response
```

**Purpose**: Turns a terminal frame from a direct object-write turn into a synchronous JSON result.

**Data flow**: It returns ok true with detail for done status, otherwise ok false with an error reason.

**Call relations**: object_write calls it when the prepared-intent turn reaches a terminal frame.

*Call graph*: called by 1 (object_write); 1 external calls (JSONResponse).


##### `object_write`  (lines 4998–5072)

```
async def object_write(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets an app frame create, update, or delete an object through the member's web session.

**Data flow**: It authenticates, validates kind and body/path, builds an object_apply or object_delete ToolIntent, admits it to an intent conversation, tails the turn until terminal/parked/timeout, and returns the result.

**Call relations**: Bridge clients use this direct write path; it relies on core turns for journaling and authority.

*Call graph*: calls 7 internal fn (admit, conversation_for, object_kind, tail, _audience_for, _direct_result, _write_agent); 7 external calls (__init__, timeout, dumps, conversation_audience, JSONResponse, json, Response).


##### `object_changes`  (lines 5078–5106)

```
async def object_changes(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the recent object-change journal for workspace admins.

**Data flow**: It authenticates, hides the route from non-admins, reads recent changes, and returns kind, name, verb, caller, agent, and timestamp.

**Call relations**: The admin audit panel uses this; object content itself remains behind each kind's own read gate.

*Call graph*: calls 2 internal fn (recent_object_changes, _audience_for); 2 external calls (JSONResponse, Response).


##### `settings`  (lines 5109–5121)

```
async def settings(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reads configuration for a selected agent.

**Data flow**: It authorizes the panel, finds the agent summary, computes whether the viewer may archive it, and delegates to agent_settings.

**Call relations**: The settings panel route is thin because detailed shaping lives in ufo_ext_web.panels.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_settings).


##### `agent_setup`  (lines 5124–5154)

```
async def agent_setup(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Reads setup requirements for a selected app or agent.

**Data flow**: It authorizes, reads setup state, adds friendly provider labels and summaries, checks whether the workspace has built its own page, and returns JSON.

**Call relations**: The setup screen calls this; _bound_page connects setup state with homepage availability.

*Call graph*: calls 5 internal fn (agent_setup, _bound_page, _panel_gate, _provider_label, _provider_summary); 1 external calls (JSONResponse).


##### `_bound_page`  (lines 5157–5184)

```
async def _bound_page(ctx: SurfaceContext, summary: AgentSummary, member_id: UUID) -> ObjectRow | None
```

**Purpose**: Finds the hosted site row currently bound as an agent's workspace-built homepage.

**Data flow**: It lists site objects for the agent with a homepage_agent filter and returns the first row carrying a site_url.

**Call relations**: agents_index, agent_setup, and homepage use it so setup and homepage state agree.

*Call graph*: calls 1 internal fn (list_member_objects); called by 3 (agent_setup, agents_index, homepage); 1 external calls (__init__).


##### `homepage`  (lines 5187–5206)

```
async def homepage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent's current homepage state.

**Data flow**: It authorizes, publishes app assets, reads any bound page, computes the homepage state, and returns JSON.

**Call relations**: The frontend polls this after boot to notice newly built homepages without reloading the whole agent index.

*Call graph*: calls 5 internal fn (_assets_published, _bound_page, _homepage_state, _panel_gate, apps); 1 external calls (JSONResponse).


##### `_homepage_state`  (lines 5209–5256)

```
def _homepage_state(ctx: SurfaceContext, summary: AgentSummary, bound: ObjectRow | None, admin: bool, member_id: UUID) -> dict[str, JsonValue]
```

**Purpose**: Builds the JSON state for an agent homepage, whether workspace-built, shipped, or absent.

**Data flow**: It checks bound hosted-site visibility, builds embedded URLs for bound pages, falls back to shipped app bundle URLs when available, and otherwise returns none.

**Call relations**: agents_index and homepage both call it after obtaining bound-page information.

*Call graph*: calls 1 internal fn (apps); called by 2 (agents_index, homepage); 3 external calls (shipped_app_slug, homepage_embed_url, shipped_homepage_url).


##### `_preview_start_page`  (lines 5265–5270)

```
def _preview_start_page(form: FormData) -> int
```

**Purpose**: Parses the first document page to render for attachment preview.

**Data flow**: It reads start_page from form data, returns at least 1, and defaults to 1 on invalid input.

**Call relations**: preview uses it before asking the preview service to render pages.

*Call graph*: called by 1 (preview); 1 external calls (get).


##### `_preview_pages`  (lines 5273–5278)

```
def _preview_pages(form: FormData) -> int
```

**Purpose**: Parses and clamps how many document pages to preview.

**Data flow**: It reads pages from form data, bounds it between 1 and the batch maximum, and defaults to the maximum on invalid input.

**Call relations**: preview uses it so a caller cannot request an unbounded render.

*Call graph*: called by 1 (preview); 1 external calls (get).


##### `preview`  (lines 5281–5327)

```
async def preview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders a temporary preview of an uploaded document for the composer.

**Data flow**: It authenticates, requires multipart data, bounds and parses the form, accepts the first supported file, asks the preview service to render pages, and returns base64 PNGs.

**Call relations**: The composer calls this before sending; it stores nothing and admits no turn.

*Call graph*: calls 6 internal fn (render_preview, _audience_for, _form, _framed_length, _preview_pages, _preview_start_page); 5 external calls (b64encode, PurePosixPath, JSONResponse, Response, get).


##### `upload_start`  (lines 5330–5367)

```
async def upload_start(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Creates a presigned direct-upload URL for one attachment.

**Data flow**: It authenticates, reads a bounded JSON body with filename, size, and checksum, validates size, asks core to mint an upload grant, and returns key, PUT URL, and signature.

**Call relations**: The composer calls this before uploading large attachments directly to blob storage; _chat_inbound later verifies the returned signature.

*Call graph*: calls 3 internal fn (mint_upload, _audience_for, _bounded_body); 3 external calls (loads, JSONResponse, Response).


### `extensions/web/ufo_ext_web/starters.py`

`domain_logic` · `request handling`

The start screen needs useful suggestions without making the member wait or wasting model calls. This file treats those suggestions like a short-lived menu: if a good menu was made recently, reuse it; if it is stale, show it for now and quietly try to make a better one. That means the screen does not go blank just because regeneration is happening.

The suggestions are called a “slate.” A slate records when it was made, which version of the ranking instructions made it, the ranked app ideas, and possibly one “check-in” question about unfinished work. The language model is given the member’s remembered work, existing applications, and a catalog of possible applications. It must return its answer through a tool call, which gives the code a structured shape to validate.

The cache has guardrails. It will not generate if there is no model, no remembered context, or the workspace cannot afford model work. It also uses a short “claim” record so two browser tabs do not pay for the same slate at the same time. If generation fails, it records a cooldown, like putting a sticky note saying “don’t retry this immediately.” Invalid rows from the model are dropped one by one instead of ruining the whole slate.

#### Function details

##### `Slate.fresh`  (lines 112–113)

```
def fresh(self, now: datetime) -> bool
```

**Purpose**: This checks whether a stored slate is still safe to reuse. It is fresh only if it is young enough and was made with the current version of the ranking instructions.

**Data flow**: It receives the current time, looks at the slate’s saved creation time and prompt digest, and compares them with the allowed time window and current instruction digest. It returns true when the slate can still be shown as current, or false when it should be regenerated.

**Call relations**: When StarterCache.read finds a stored slate, it asks Slate.fresh whether that slate can be returned immediately. If the answer is false, the read flow may still show the stale slate while trying to create a newer one.


##### `starters_key`  (lines 124–125)

```
def starters_key(member_id: UUID) -> str
```

**Purpose**: This creates the storage key for a member’s cached starter slate. It keeps each member’s start-screen suggestions separate in the shared store.

**Data flow**: It receives a member ID, turns that ID into the project’s standard member subject string, prefixes it with the starters namespace, and returns the finished key text.

**Call relations**: StarterCache._held uses this key to look up an existing slate. StarterCache.read uses the same key to save a newly generated slate after ranking succeeds.

*Call graph*: called by 2 (_held, read); 1 external calls (member_subject).


##### `claim_key`  (lines 128–129)

```
def claim_key(member_id: UUID) -> str
```

**Purpose**: This creates the storage key for the short-lived “someone is generating this slate” claim. The claim prevents duplicate model work when the same member’s screen is read from multiple places at once.

**Data flow**: It receives a member ID, converts it to the standard member subject string, adds the claim prefix, and returns the key used in the store.

**Call relations**: StarterCache._claim uses this key while trying to become the one reader allowed to regenerate. StarterCache.read deletes this key when generation ends, whether it succeeded or failed.

*Call graph*: called by 2 (_claim, read); 1 external calls (member_subject).


##### `cooldown_key`  (lines 132–133)

```
def cooldown_key(member_id: UUID) -> str
```

**Purpose**: This creates the storage key for a member’s recent generation failure record. That record stops the system from repeatedly hitting the same failing model path every time the screen refreshes.

**Data flow**: It receives a member ID, converts it into the standard member subject string, adds the cooldown prefix, and returns the key used to read or write the failure stamp.

**Call relations**: StarterCache._may_generate checks this key before allowing a new model call. StarterCache.read writes to this key when ranking fails so later reads back off for a while.

*Call graph*: called by 2 (_may_generate, read); 1 external calls (member_subject).


##### `_stamped`  (lines 136–145)

```
def _stamped(held: object, key: str) -> datetime | None
```

**Purpose**: This safely reads a timestamp from a small stored record. If the stored data is missing, old, malformed, or half-written, it treats it as absent instead of crashing.

**Data flow**: It receives any stored value and the name of the timestamp field to look for. If the value is a dictionary with a readable ISO-format date string, it returns a datetime; otherwise it returns nothing.

**Call relations**: StarterCache._may_generate uses this to read the last failure time from the cooldown record. StarterCache._claim uses it to read when an existing generation claim was made, so it can tell whether the claim is still alive or stale.

*Call graph*: called by 2 (_claim, _may_generate); 1 external calls (fromisoformat).


##### `StarterCache.read`  (lines 168–185)

```
async def read(self) -> Slate | None
```

**Purpose**: This is the main entry point for getting a member’s starter slate. It returns a fresh slate if possible, falls back to a stale one if needed, and tries to regenerate without making the screen worse for the member.

**Data flow**: It gets the current time, reads any stored slate, and returns it immediately if it is fresh. If regeneration is not allowed, it returns whatever was already stored. If regeneration is allowed, it asks the model to rank a new slate, stores it, and returns it. If anything goes wrong, it records a cooldown warning and returns the old slate instead of raising an error.

**Call relations**: This method ties the file together. It calls _held to load the cache, _may_generate to decide whether this read should regenerate, _rank to ask the model, and the key helpers to store results, remove claims, or mark failures. Other start-screen code would call this when it needs rows to show.

*Call graph*: calls 6 internal fn (_held, _may_generate, _rank, claim_key, cooldown_key, starters_key); 2 external calls (now, warn).


##### `StarterCache._held`  (lines 187–194)

```
async def _held(self) -> Slate | None
```

**Purpose**: This loads the currently stored slate for the member, if there is one and it still matches the expected data shape. Bad stored data is ignored rather than allowed to break the start screen.

**Data flow**: It builds the member’s starter-cache key, fetches the stored value, checks that it is a dictionary, and asks the Slate model to validate it. It returns a Slate object when validation succeeds, or nothing when the cache is missing or unusable.

**Call relations**: StarterCache.read calls this first so it knows whether there is anything safe to show. The method relies on starters_key to look in the right place in the shared store.

*Call graph*: calls 1 internal fn (starters_key); called by 1 (read).


##### `StarterCache._may_generate`  (lines 196–206)

```
async def _may_generate(self, now: datetime) -> bool
```

**Purpose**: This decides whether the current read is allowed to spend work creating a new slate. It protects the system from pointless or costly generation when the needed ingredients are missing, the workspace cannot pay, a recent failure is cooling down, or another reader is already doing the job.

**Data flow**: It receives the current time and checks the cache object’s model, recalled memory, and solvency flag. It then reads the cooldown stamp and compares it with the failure cooldown window. If those checks pass, it tries to claim the right to generate and returns whether the claim succeeded.

**Call relations**: StarterCache.read calls this before any model call. It uses cooldown_key and _stamped to avoid retrying too soon after failure, then hands off to _claim to coordinate with other simultaneous reads.

*Call graph*: calls 3 internal fn (_claim, _stamped, cooldown_key); called by 1 (read).


##### `StarterCache._claim`  (lines 208–225)

```
async def _claim(self, now: datetime) -> bool
```

**Purpose**: This tries to make this reader the only one allowed to regenerate the slate. It is the anti-duplicate-work lock, like putting a temporary reservation card on a shared desk.

**Data flow**: It writes a claim record with the current time if no claim exists. If a claim already exists, it reads that claim’s timestamp. A recent claim blocks this reader; an old claim can be replaced, but only if the stored value has not changed since it was read. It returns true when this reader owns the claim and false otherwise.

**Call relations**: StarterCache._may_generate calls this as the final gate before model generation. It uses claim_key to address the claim record and _stamped to understand whether an existing claim is still within its lease.

*Call graph*: calls 2 internal fn (_stamped, claim_key); called by 1 (_may_generate); 1 external calls (isoformat).


##### `StarterCache._rank`  (lines 227–254)

```
async def _rank(self, now: datetime) -> Slate
```

**Purpose**: This asks the language model to rank the best starter rows for the member. It packages the member’s memory, current applications, and available app catalog into a strict request and expects the model to answer through the configured tool format.

**Data flow**: It gathers recalled memory, existing agent names, and catalog entries, turns them into compact JSON, and sends them with the system instructions, token limit, and tool schema to the surface model. The model returns a message, which is passed to settle_slate to validate and turn into a Slate.

**Call relations**: StarterCache.read calls this only after cache and claim checks allow regeneration. After the model replies, _rank hands the raw reply to settle_slate so the rest of the code receives a cleaned, trusted slate rather than raw model output.

*Call graph*: calls 1 internal fn (settle_slate); called by 1 (read); 4 external calls (__init__, __init__, __init__, dumps).


##### `settle_slate`  (lines 257–290)

```
def settle_slate(reply: Message, generated_at: datetime) -> Slate
```

**Purpose**: This turns the model’s structured reply into a safe Slate object. It keeps valid ranked rows, drops invalid or duplicate ones, accepts a valid check-in if present, and rejects replies that did not use the required tool call at all.

**Data flow**: It receives a model message and the generation time. It searches the message for the expected record_slate tool call, reads its ranked entries, validates each row, keeps only rows that match known catalog unlocks and have not appeared before, validates the optional check-in, and returns a Slate stamped with the current prompt digest. If no tool call was recorded, it raises an error.

**Call relations**: StarterCache._rank calls this immediately after the model response arrives. Its result goes back to StarterCache.read, which stores and returns the slate; if settle_slate raises because the model did not follow the contract, read treats that as a generation failure and falls back safely.

*Call graph*: called by 1 (_rank); 1 external calls (__init__).


### Runtime and operator inspection
Runtime surface scaffolding and diagnostic views expose readable conversation timelines and trusted operator pages for inspecting workspaces and memory.

### `core/src/ufo/runtime/steps.py`

`domain_logic` · `diagnostic read / surface inspection`

A conversation turn may involve several hidden steps: the model speaks, asks for tools, tools answer, and workflow helper steps run in between. DBOS records those steps durably, meaning they survive after the run. This file rebuilds those raw records into `TurnStep` objects, which are easier for the rest of the system to show or inspect.

The important idea is that not every recorded workflow step is useful as a message. A model step can become an assistant message, including reasoning blocks, final text, tool calls, or partial output if the stream failed halfway through. A tool dispatch becomes a user-side tool result, because that is what the model saw next. Image data is not loaded back into memory; instead, the file adds a short note saying how many image attachments were omitted. Other workflow steps still appear in the timeline, but they do not rebuild a message window.

`DurableTurnSteps.read` first asks DBOS for the recorded steps for a workflow. It remembers tool call names from model outputs, so later tool results can be labeled with a human-friendly name. Then it creates numbered `TurnStep` entries with kind, name, original function name, timestamps, duration, and reconstructed messages. In short, this file is like a receipt formatter for a complex transaction: it takes machine records and turns them into a clear itemized story.

#### Function details

##### `_step_messages`  (lines 16–54)

```
def _step_messages(output: object) -> tuple[Message, ...]
```

**Purpose**: This helper rebuilds the message or messages that a recorded step contributed to the conversation view. It keeps useful model text and tool results, but skips workflow steps that do not correspond to something the model or user-side tool result would have seen.

**Data flow**: It receives one recorded step output, which may be a model stream result, a tool dispatch result, or something else. For a model result, it gathers reasoning blocks, text, and tool calls into an assistant message, or uses salvaged partial output if that is all that exists. For a tool result, it creates a tool-result message and adds a note instead of reloading omitted image attachments. For anything else, it returns no messages.

**Call relations**: This function is called by `DurableTurnSteps.read` while each recorded DBOS step is being turned into a `TurnStep`. It hands back the reconstructed message window that gets attached to that timeline entry.

*Call graph*: called by 1 (read); 3 external calls (__init__, __init__, __init__).


##### `DurableTurnSteps.read`  (lines 63–103)

```
async def read(self, workflow_id: str) -> tuple[TurnStep, ...]
```

**Purpose**: This method reads the durable DBOS record for one workflow and turns it into a clean sequence of `TurnStep` timeline entries. It is used when the system needs to show or inspect what happened during a turn after the fact.

**Data flow**: It takes a workflow ID and asks the DBOS client for that workflow's recorded steps. It first scans model outputs to connect tool call IDs with tool names. Then, for each recorded step, it decides whether it was a model step, a tool step, or a general workflow step; converts raw millisecond timestamps into normal datetime values; calculates duration when possible; asks `_step_messages` to rebuild any visible messages; and returns the finished steps as an immutable tuple.

**Call relations**: This is the main entry in the file. Higher-level diagnostic or surface-reading code calls it when it wants a trustworthy turn timeline. Inside the method, it delegates timestamp conversion to `DurableTurnSteps._timestamp`, message reconstruction to `_step_messages`, and then packages the results into `TurnStep` objects for callers to consume.

*Call graph*: calls 2 internal fn (_timestamp, _step_messages); 1 external calls (__init__).


##### `DurableTurnSteps._timestamp`  (lines 106–107)

```
def _timestamp(epoch_ms: int | None) -> datetime | None
```

**Purpose**: This small helper converts DBOS timestamp numbers into timezone-aware datetime objects. It also preserves missing timestamps as missing, instead of inventing a time.

**Data flow**: It receives either a millisecond count since the Unix epoch or `None`. If the value is present, it divides by 1000 to get seconds and converts it to a UTC datetime. If the value is `None`, it returns `None`.

**Call relations**: `DurableTurnSteps.read` calls this helper for each recorded step's start and completion times. Keeping the conversion in one place makes the timeline-building code easier to read and ensures both timestamps are interpreted the same way.

*Call graph*: called by 1 (read); 1 external calls (fromtimestamp).


### `core/src/ufo/runtime/surfaces/__init__.py`

`other` · `import time / package structure`

This is an empty package marker file. In Python projects, an `__init__.py` file tells Python that a folder is meant to be treated as an importable package, like a labeled drawer in a filing cabinet. Here, that drawer is `ufo.runtime.surfaces`.

Because the file is empty, it does not define any functions, classes, settings, or startup behavior. Its value is structural: it helps other code import modules that live under `core/src/ufo/runtime/surfaces/` using normal Python package paths. Without this file, depending on the Python version and packaging setup, imports from this folder could be less explicit or fail in environments that expect traditional package markers.

So this file matters not because it performs work, but because it gives the codebase a stable namespace. It says, “the files in this directory belong together as the runtime surfaces part of the system.”


### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the server-side doorway for the debugger web app. Think of it like a secure control-room window: operators can look across the fleet and drill into one workspace, but the code here only reads data and streams live updates. The actual permission check is done before these handlers run, through the shared operator workspace resolver. Once a request has a trusted operator session, the `SurfaceContext` gives every route a workspace-scoped view, so normal reads stay tied to the chosen workspace.

The file serves a built React app from `static/index.html`, then provides JSON routes under `api/` for everything the page displays. Those routes return fleet information, workspace metadata, conversation lists, transcripts, compaction records, workspace files, turn details, and turn steps. When the page wants to watch a live turn, the `stream` route opens a Server-Sent Events stream, which is a simple browser-friendly way for the server to keep sending events over one HTTP response.

Most handlers follow the same pattern: read an ID from the URL, reject bad IDs with a 404-style JSON error, ask `SurfaceContext` for the data, and return either JSON or a file stream. The `ROUTES` table at the bottom connects URL paths to these handler functions.

#### Function details

##### `app_page`  (lines 57–62)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger’s browser application. If the frontend has not been built yet, it fails clearly so the operator knows the missing setup step.

**Data flow**: It receives the current surface context and HTTP request, but does not need to read details from either one. It checks the already-loaded HTML file content; if present, it wraps that HTML as an HTTP response, and if missing, it raises an error explaining how to build the app.

**Call relations**: This is called when an operator opens the debugger root page with a GET request. It hands the browser the static app shell; after that, the browser calls the JSON API routes in this same file to fill the page with real data.

*Call graph*: 1 external calls (HTMLResponse).


##### `fleet`  (lines 65–69)

```
async def fleet(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the fleet-wide landing-page data: workspaces and recent threads across the deployment. This gives an operator a starting index before they drill into one workspace.

**Data flow**: It takes the request and context, creates a `FleetDirectory`, reads the fleet directory, converts that structured data into plain JSON-friendly values, and returns it as a JSON response.

**Call relations**: The frontend calls this route for the debugger’s fleet index. It relies on the operator-level authorization that has already happened before the handler, then hands the browser a broad read-only snapshot.

*Call graph*: 2 external calls (__init__, JSONResponse).


##### `workspace_meta`  (lines 72–85)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns small pieces of information about the currently selected workspace, such as its workspace ID, Slack team ID if connected, and Datadog site setting if configured.

**Data flow**: It reads the Slack installation value from the `SurfaceContext`. If that value uses the expected `team:` prefix, it strips the prefix to expose the Slack team ID. It also reads the `DD_SITE` environment variable, then returns all of this as JSON.

**Call relations**: The debugger app calls this when it needs workspace header or integration details. It asks `SurfaceContext` for installation information and then formats only the small metadata fields the frontend needs.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 88–90)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the conversations available in the current workspace. This lets the debugger show an operator what sessions can be inspected.

**Data flow**: It asks `SurfaceContext` for the workspace’s conversations, converts each conversation summary into JSON-friendly data, and returns the list.

**Call relations**: The frontend uses this route when showing the workspace’s conversation list. It delegates the actual workspace-scoped lookup to `SurfaceContext` and only packages the result for HTTP.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 93–98)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the turns inside one conversation. A turn is one unit of interaction or work inside a longer conversation.

**Data flow**: It reads `conversation_id` from the URL and uses `_uuid_param` to make sure it is a valid UUID, which is a standard unique identifier. If the ID is invalid, it returns a JSON error. Otherwise it asks `SurfaceContext` for the turns in that conversation and returns them as JSON.

**Call relations**: The debugger app calls this after an operator selects a conversation. This handler uses `_uuid_param` as its gatekeeper for URL IDs, then hands the valid ID to `SurfaceContext` for the real lookup.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 101–108)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full transcript for one conversation, if one exists. This lets an operator read the conversation in a human-friendly form.

**Data flow**: It pulls the conversation ID from the path, validates it with `_uuid_param`, and returns a JSON error if the ID is bad. With a valid ID, it asks `SurfaceContext` for the transcript. If no transcript exists, it returns an error; otherwise it serializes the transcript to JSON.

**Call relations**: The frontend calls this when showing the transcript view for a selected conversation. The handler sits between the URL and `SurfaceContext`, turning missing or invalid data into simple JSON errors.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 111–115)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is when older conversation messages are summarized or compressed so the system can keep working with a shorter context.

**Data flow**: It validates the `conversation_id` from the URL. If it is not a valid UUID, it returns a JSON error. Otherwise it asks `SurfaceContext` for that conversation’s compaction indexes or records and returns them as a JSON list.

**Call relations**: The debugger app calls this when an operator wants to inspect how a conversation was shortened over time. The route uses `_uuid_param` before passing the request to `SurfaceContext`.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 118–133)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one detailed compaction record, showing what messages existed before, what remained after, and the summary that replaced them. This is useful for debugging whether conversation memory was compressed correctly.

**Data flow**: It reads a conversation ID and compaction index from the URL. The conversation ID must be a valid UUID, and the index must be a number. If either check fails, or if no record is found, it returns a JSON error. Otherwise it turns the record’s before messages, after messages, and summary into JSON.

**Call relations**: The frontend calls this after choosing a specific compaction entry. It relies on `_uuid_param` for ID validation and `SurfaceContext.read_compaction` for the stored record, then reshapes the result into a clear JSON object.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 136–141)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a conversation’s workspace. This lets an operator see what files the system produced or used during that conversation.

**Data flow**: It validates the conversation ID from the URL. If the ID is invalid, it returns a JSON error. Otherwise it asks `SurfaceContext` for the workspace file list and returns each file entry as JSON.

**Call relations**: The debugger app calls this when showing the file browser for a conversation. Like the other conversation routes, it uses `_uuid_param` first and then delegates the workspace-scoped read to `SurfaceContext`.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 144–154)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file back to the browser. This is how the debugger lets an operator download or inspect a specific file.

**Data flow**: It validates the conversation ID and reads the requested file path from the URL. If the ID is invalid, the path is rejected, or no file exists, it returns a JSON error. If the file is found, it returns a streaming binary response, meaning the bytes can be sent gradually rather than loaded into one JSON object.

**Call relations**: The frontend calls this after an operator selects a file. The handler uses `_uuid_param` for the conversation ID, asks `SurfaceContext.read_workspace_file` for a safe readable stream, and hands that stream directly to the HTTP layer.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 157–164)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn. This gives the debugger the main record for a specific unit of work.

**Data flow**: It reads `turn_id` from the URL, validates it as a UUID, and returns a JSON error if invalid. With a valid ID, it asks `SurfaceContext` for the turn detail. If the turn does not exist, it returns an error; otherwise it returns the detail as JSON.

**Call relations**: The debugger app calls this when an operator opens a turn detail page. This route prepares and validates the ID, while `SurfaceContext` performs the workspace-scoped lookup.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `turn_steps`  (lines 167–174)

```
async def turn_steps(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the step-by-step records inside a turn. This helps an operator see how the system moved from start to finish during that turn.

**Data flow**: It validates the turn ID from the URL. If the ID is bad, or if no steps are found for that turn, it returns a JSON error. Otherwise it converts each step into JSON and returns the list.

**Call relations**: The frontend calls this when it needs a detailed timeline for a turn. The function uses `_uuid_param` before calling `SurfaceContext.turn_steps`, then packages the resulting steps for display.

*Call graph*: calls 2 internal fn (turn_steps, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 177–182)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn so the debugger can watch new activity arrive in real time. It also supports resuming after a dropped connection by reading the browser’s last seen event ID.

**Data flow**: It validates the turn ID, checks that the turn exists, and returns a JSON error if not. It reads the `Last-Event-ID` header, which tells the server where the browser left off. Then it returns a streaming response that will send events produced by `_events` as `text/event-stream` data.

**Call relations**: The frontend calls this while viewing a live turn. This handler performs the initial checks, then hands off to `_events`, which tails the underlying live frames and turns them into browser-readable Server-Sent Events.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 185–188)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Converts the system’s live turn feed into a stream of bytes suitable for an HTTP response. It is the bridge between internal live frames and the debugger page’s live event stream.

**Data flow**: It receives a context, a turn ID, and a cursor saying where to resume. It opens `ctx.tail`, which yields live frames with their cursors, and for each frame it calls `_sse` to format one event. It yields those formatted bytes one by one to the HTTP streaming response.

**Call relations**: `stream` calls this after validating the requested turn. `_events` stays connected to `SurfaceContext.tail` and uses `_sse` for the final event formatting before the bytes reach the browser.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 191–219)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as a Server-Sent Event, the simple text format browsers can read for live updates. It preserves the raw debug JSON rather than turning it into a polished user-facing message.

**Data flow**: It receives a cursor and one live frame. If the cursor is not empty, it writes it as the event ID so the browser can resume later. It then looks at the frame type, chooses an event name such as `terminal`, `reply`, `text`, or `cost`, serializes the frame to JSON, and returns the complete event as bytes.

**Call relations**: `_events` calls this for every frame it receives from the live tail. If a new kind of live frame appears and this function does not know how to name it, it raises an error instead of silently sending a confusing event.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 222–226)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID value from a URL path parameter. It keeps route handlers from treating malformed text as a real conversation or turn ID.

**Data flow**: It takes the request and the name of a path parameter. It tries to convert that parameter into a UUID object. If conversion works, it returns the UUID; if the text is not a valid UUID, it returns `None`.

**Call relations**: Most detail routes call this before reading conversation, file, compaction, or turn data. By returning `None` for bad IDs, it lets those routes produce consistent JSON 404-style errors instead of crashing.

*Call graph*: called by 9 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, turn_steps, workspace_file, workspace_files); 1 external calls (UUID).


### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `operator request handling`

This file is the doorway to a small “memory explorer” tool. Its job is to serve a web page and a JSON data endpoint so an operator can inspect what the system has remembered for one workspace. Without it, there would be no simple surface for checking the contents of the Memory extension’s durable store, which would make debugging recall behavior much harder.

The file defines a surface called `memory`. A surface is a web-facing part of an extension. The first route returns a static HTML page from `static/memory.html`; that page is the user interface. A second route accepts a session-binding request, using the shared operator login/session flow. A third route returns the actual memory records as JSON.

The important safety idea is workspace scoping. The request has already been checked and tied to a workspace before these reads happen. The code then opens the Memory extension’s own scoped store and asks for that workspace’s memory inventory. Think of it like giving an inspector access to one labeled filing cabinet: they can open drawers and read documents, but only inside that cabinet, and they cannot change anything here.

The JSON endpoint returns every memory item for the workspace, including shared and member-specific entries, current and superseded entries, and items that may or may not have been indexed yet.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function serves the Memory explorer’s HTML page to the operator. It is used when someone opens the surface in a browser.

**Data flow**: It receives the surface context and the incoming web request, but it does not need to inspect either one. It checks whether the static HTML file was successfully loaded when the module started. If the file is present, it wraps the HTML text in an HTTP response; if it is missing, it raises an error so the missing page is noticed immediately.

**Call relations**: The route table connects this function to a GET request at the surface root path. When the operator visits the Memory surface, this function is the piece that hands the browser the page shell; the page can then call the separate memories endpoint to fill in the data.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the workspace’s stored memory items as JSON. It gives the explorer page the data it needs to show what the recall system can draw from.

**Data flow**: It receives the current surface context, which includes the workspace identity, and the web request. It creates an extension context for the Memory extension, using a scoped store so database access stays inside the correct extension and workspace boundaries. It asks the memory store for the inventory of records for that workspace, converts each record into JSON-friendly data, and returns the list in a JSON HTTP response.

**Call relations**: The route table connects this function to GET requests at `api/memories`. After the HTML page is loaded, the browser can call this endpoint to fetch the actual memory list. Inside the function, it builds the extension access objects, then hands the read to `ufo_ext_memory.store.inventory`, which performs the store lookup; this function’s job is to turn that result into an API response.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).


### Conversation panels and hosted sites
Extension surfaces add in-conversation automation panels and public hosted-site routes with access checks, previews, framing, and visibility controls.

### `extensions/scheduled_tasks/ufo_ext_scheduled_tasks/conversation_slot.py`

`orchestration` · `conversation UI read/summarize`

A conversation can have scheduled tasks attached to it, such as reminders or recurring automations. This file is the bridge between the scheduled-task storage system and the conversation UI slot that shows those automations. Without it, the app might still store and run scheduled tasks, but the conversation would not have a clear, safe way to display them.

The file first creates a small helper, `_scheduler`, that turns the conversation’s extension context into a `ScheduleStore`, which is the object used to read scheduled-task records. Then `_conversation` asks that store for tasks belonging to the current conversation, limited to the names of items that are visible in the current context.

The main work happens in `_read`. It filters the stored tasks against the conversation’s visible items, checking that each task still matches the expected authorization generation. This is like checking both a guest’s name and their ticket number before letting them into an event. It then asks the scheduler for live inspection details, such as the next run time, last run time, last status, and last response. Before returning anything, it trims long text fields and hides task content when the visible item says the content should not be shown. If anything was omitted or shortened, it marks the result as `truncated` so the caller knows the view is incomplete.

Finally, the file registers `AUTOMATIONS_SLOT`, which tells the host app how to label, summarize, and read this conversation slot.

#### Function details

##### `_scheduler`  (lines 16–19)

```
def _scheduler(ctx: ConversationSlotContext) -> ScheduleStore
```

**Purpose**: This helper gets the scheduled-task storage interface for the current conversation-slot request. It also checks that the required extension context is present, because the schedule store cannot work without it.

**Data flow**: It receives a `ConversationSlotContext`, which should contain an extension context in `ctx.ext`. If that extension context is missing, it raises an error. If it is present, it builds and returns a `ScheduleStore` connected to that context.

**Call relations**: `_conversation` and `_read` call this when they need access to stored scheduled tasks. It hands them a `ScheduleStore`, which they then use to list tasks or inspect their current run information.

*Call graph*: called by 2 (_conversation, _read); 1 external calls (__init__).


##### `_conversation`  (lines 22–28)

```
async def _conversation(ctx: ConversationSlotContext) -> tuple[ScheduledTask, ...]
```

**Purpose**: This function fetches the scheduled tasks that belong to the current conversation and are tied to items currently visible to the caller. It keeps the read narrow so the slot only considers tasks relevant to this conversation view.

**Data flow**: It receives the conversation-slot context, collects the names from `ctx.visible_items`, and asks the schedule store to list tasks for `ctx.conversation_id` with those names. It requests one more than the maximum allowed so later code can tell whether there were too many results to show fully. It returns the matching scheduled-task records.

**Call relations**: `_read` calls `_conversation` as its first step in building the visible Automations payload. `_conversation` calls `_scheduler` to get the schedule store it needs for the database-style lookup.

*Call graph*: calls 1 internal fn (_scheduler); called by 1 (_read).


##### `_read`  (lines 31–90)

```
async def _read(ctx: ConversationSlotContext) -> AutomationsSlotPayload
```

**Purpose**: This is the main reader for the Automations conversation slot. It turns stored scheduled tasks into safe, display-ready automation summaries for the conversation UI.

**Data flow**: It receives a `ConversationSlotContext`. It gets a scheduler, loads candidate tasks through `_conversation`, and compares those tasks with `ctx.visible_items` to make sure each one is still authorized and matches the expected generation. It inspects the authorized tasks to learn timing and latest-run details. Then it builds `ConversationAutomation` objects, trimming long descriptions, schedules, statuses, and responses, and hiding content when `content_visible` is false. It returns an `AutomationsSlotPayload` containing the automation summaries and a `truncated` flag showing whether anything was left out or shortened.

**Call relations**: The registered `AUTOMATIONS_SLOT` uses `_read` when the host app wants the full Automations content for a conversation. `_read` calls `_scheduler` for inspection access and `_conversation` for the initial task list, then creates the payload objects that the rest of the app can display.

*Call graph*: calls 2 internal fn (_conversation, _scheduler); 2 external calls (__init__, __init__).


##### `_summarize`  (lines 93–95)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: This function gives a quick count for the Automations slot without reading all task details. It is useful when the UI only needs a small badge or summary, not the full list.

**Data flow**: It receives the conversation-slot context and counts the visible items, capped at the maximum number of automations the slot can show. If the count is zero, it returns `None`; otherwise it returns the count.

**Call relations**: The registered `AUTOMATIONS_SLOT` uses `_summarize` when the host app wants a lightweight summary. Unlike `_read`, it does not call the scheduler or inspect stored tasks; it only looks at the visible items already present in the context.


### `extensions/sites/ufo_ext_sites/surface.py`

`orchestration` · `request handling`

A hosted site link is not treated like a secret key that grants access by itself. It is more like an address on an envelope: it says which workspace, conversation, and site name to look up, but every visit still has to pass the front desk. This file verifies that address, finds the site, identifies the viewer from the `ufo_session` cookie when possible, and applies the site's visibility rules.

Public sites can be opened by anyone. Workspace-only sites require a signed-in workspace member. Private sites require the creator or a workspace admin. If the site is being used as an agent homepage, the agent's visibility rules take over instead. When access is denied, the file often returns the same plain 404 response as an unknown site, so the page cannot be used to discover what exists.

The file does not serve the site's app bytes directly. Instead, it builds a small outer HTML page with an iframe. The iframe points to a freshly minted ingress URL, which is a temporary doorway to the site's own origin. This keeps the hosted site separated from the portal session and from the outer page.

It also creates social sharing tags. Public sites may show their own name and preview card. Non-public sites get generic UFO metadata so private names are not leaked to chat unfurlers or crawlers.

#### Function details

##### `site_token`  (lines 174–183)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that names one hosted site. Other code uses this when it needs a stable address for a site.

**Data flow**: It receives a workspace ID, conversation ID, and site name. It puts those values into signed token claims for the sites surface. It returns the token string that can later be placed in a URL and verified.

**Call relations**: When `site_url` needs to build a public link, it asks `site_token` for the address part of that link. The actual signing is handed off to `mint_surface_token`, so callers do not need to know the token format.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 186–196)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the permanent public URL for a hosted site. It refuses to invent a link if the deployment has no public base URL configured.

**Data flow**: It receives the deployment's public base URL plus the site's workspace, conversation, and name. If the base URL is missing, it raises `SiteHostingUnconfigured`. Otherwise it creates a site token and returns the full `/surface/sites/...` link.

**Call relations**: This is the producer of normal hosted-site links. It relies on `site_token` to make the signed address, then adds that token to the shared frame route.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `shipped_homepage_url`  (lines 208–223)

```
def shipped_homepage_url(public_base_url: str | None, workspace_id: UUID, slug: str, digest: str) -> str | None
```

**Purpose**: Builds a stable URL for a deploy-wide shipped app bundle when it is opened inside the portal. Unlike a normal site, this names an app slug and bundle digest rather than a hosted-site row.

**Data flow**: It receives a public base URL, workspace ID, app slug, and digest. If there is no public base URL, it returns `None`. Otherwise it signs those values into a portal-embed token and returns the full frame URL.

**Call relations**: The token shape it creates is later understood by `shipped_address`, which lets `frame` recognize that the request is for a shipped app page rather than a user-created hosted site.

*Call graph*: 1 external calls (mint_surface_token).


##### `shipped_address`  (lines 226–240)

```
def shipped_address(token: str) -> ShippedAddress | None
```

**Purpose**: Reads and validates a token for a shipped app page. It returns a structured address only when the token really is for a portal-embedded shipped bundle.

**Data flow**: It receives a token string. It verifies the signature and checks that the portal-embed marker is present. If the workspace, slug, or digest is missing or malformed, it returns `None`; otherwise it returns a `ShippedAddress`.

**Call relations**: `resolve_workspace` uses this when a request is not a normal site token but may still name a workspace. `frame` uses it first so shipped app pages can be sent through `_shipped_frame` instead of looking for a hosted-site row.

*Call graph*: called by 2 (frame, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `site_card_url`  (lines 243–252)

```
def site_card_url(public_base_url: str | None, token: str, digest: str) -> str | None
```

**Purpose**: Builds the public URL for a site's share-preview image. This is the image that chat apps and social previews can fetch without being signed in.

**Data flow**: It receives the public base URL, the site token, and the preview image digest. If the base URL is missing, it returns `None`. Otherwise it returns a URL under the anonymous share-card route, ending with the digest and image extension.

**Call relations**: `frame` calls this only when it is safe to publish a site's own card: the site must be public and have a card hash. The returned URL is then placed into the page's Open Graph metadata by `_share_tags`.

*Call graph*: called by 1 (frame).


##### `site_address`  (lines 255–274)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Reads and validates a normal hosted-site token. It turns the signed token back into the workspace, conversation, site name, and whether it was minted for portal embedding.

**Data flow**: It receives a token string. It verifies the token signature and expected surface, checks the optional portal-embed flag, parses the UUIDs, and returns a `SiteAddress`. If anything is invalid or missing, it returns `None`.

**Call relations**: This is the main decoder for normal hosted-site links. `resolve_workspace`, `frame`, `_resolve`, and `homepage_embed_url` all use it before trusting anything from the URL.

*Call graph*: called by 4 (_resolve, frame, homepage_embed_url, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `homepage_embed_url`  (lines 277–292)

```
def homepage_embed_url(url: str) -> str
```

**Purpose**: Converts a normal hosted-site URL into the signed form used when the site is embedded as a portal homepage. It keeps the same site address but marks the token as portal-only.

**Data flow**: It receives a URL, splits off the final path segment as the token, and verifies that the URL looks like a hosted-site frame URL. It decodes the original token, then mints a new token with the same site information plus the portal-embed marker. It returns the rebuilt URL with that new token.

**Call relations**: It depends on `site_address` to prove that the input is really a hosted-site link. If the input cannot be verified, it raises an error instead of creating a misleading embed URL.

*Call graph*: calls 1 internal fn (site_address); 1 external calls (mint_surface_token).


##### `resolve_workspace`  (lines 295–305)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Finds which workspace a surface request belongs to before any site row is read. This is important because public visitors may not have a session cookie to identify their workspace.

**Data flow**: It reads the token from the request path. It first tries to decode it as a normal site address, then as a shipped app address. If either works, it returns the workspace ID. If neither works, it returns the same not-found response used for missing sites.

**Call relations**: The surface routing layer calls this early to choose the workspace context. It uses `site_address` and `shipped_address` as the two valid token shapes, and `_not_found` when the token proves nothing.

*Call graph*: calls 3 internal fn (_not_found, shipped_address, site_address).


##### `frame`  (lines 308–384)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a hosted-site link. It verifies the token, checks the site's visibility rules, and either renders the outer iframe page, redirects a portal iframe to ingress, or returns a safe denial.

**Data flow**: It receives the surface context and HTTP request. It reads the token, recognizes shipped app pages, resolves normal site rows, prepares safe sharing metadata, identifies the viewer from the session cookie, checks public/workspace/private/homepage rules, asks the context for an ingress URL, and returns an HTML page or redirect. It may also mint a CSRF token for the creator's visibility selector.

**Call relations**: This is the main GET handler for site links and deep links. It sends shipped app requests to `_shipped_frame`, uses `_viewer`, `_viewer_is_admin`, `_sites`, `site_address`, `site_card_url`, `_share_tags`, `_into_the_portal`, `_unconfigured_page`, and `_frame_page` to move from URL to final response.

*Call graph*: calls 18 internal fn (ingress_url, list_agents, _frame_page, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _page, _session_digest, _share_tags (+8 more)); 3 external calls (HTMLResponse, RedirectResponse, mint_surface_token).


##### `_shipped_frame`  (lines 387–426)

```
async def _shipped_frame(ctx: SurfaceContext, request: Request, shipped: ShippedAddress) -> Response
```

**Purpose**: Opens a deploy-wide shipped app bundle through the same frame route. It avoids site-row visibility checks because the bundle is public deploy code; the later ingress token controls access to data.

**Data flow**: It receives the context, request, and decoded shipped address. If the request did not arrive as a portal iframe, it finds the matching agent and redirects the viewer to that agent's portal screen. If it is inside the portal iframe, it builds the shipped app anchor, asks for an ingress URL using the slug and digest, and redirects there. If no ingress origin exists, it returns an explanatory page.

**Call relations**: `frame` calls this whenever `shipped_address` recognizes the token. It uses `_is_portal_iframe_request` to decide whether to send the user into the portal, `_into_the_portal` for that redirect, and `_framed_from` plus `ctx.ingress_url` for the actual iframe handoff.

*Call graph*: calls 8 internal fn (ingress_url, list_agents, _framed_from, _into_the_portal, _is_portal_iframe_request, _not_found, _share_tags, _unconfigured_page); called by 1 (frame); 5 external calls (HTMLResponse, RedirectResponse, serve_port, shipped_anchor, shipped_app_slug).


##### `_is_portal_iframe_request`  (lines 429–430)

```
def _is_portal_iframe_request(request: Request) -> bool
```

**Purpose**: Checks whether the browser says this request is loading inside an iframe. The code uses this to distinguish a cold link opened in a tab from a page being loaded by the portal.

**Data flow**: It reads the `sec-fetch-dest` request header. If the value is `iframe`, it returns `True`; otherwise it returns `False`.

**Call relations**: `frame`, `_shipped_frame`, and `_framed_from` use this small check before deciding whether to redirect to the portal or preserve iframe-specific information.

*Call graph*: called by 3 (_framed_from, _shipped_frame, frame).


##### `_framed_from`  (lines 433–436)

```
def _framed_from(request: Request) -> str | None
```

**Purpose**: Returns the referring page only for portal iframe loads. This lets ingress know what page framed it without trusting referer data from ordinary tab visits.

**Data flow**: It receives the request. If the request is not an iframe request, it returns `None`. If it is an iframe request, it returns the `referer` header value.

**Call relations**: `frame` and `_shipped_frame` pass this value to `ctx.ingress_url` when minting the temporary embedded address. It relies on `_is_portal_iframe_request` for the first decision.

*Call graph*: calls 1 internal fn (_is_portal_iframe_request); called by 2 (_shipped_frame, frame).


##### `_into_the_portal`  (lines 439–452)

```
def _into_the_portal(ctx: SurfaceContext, agent_id: UUID, share: str) -> Response
```

**Purpose**: Redirects a viewer to an agent's screen inside the member portal. This is used when an app page needs the portal's surrounding bridge in order to work.

**Data flow**: It receives the context, an agent ID, and already-built sharing tags. It asks the context for the portal URL pointing at that agent. If no portal is installed, it returns a simple HTML explanation. Otherwise it returns a 303 redirect to the portal.

**Call relations**: `frame` uses this for homepage-bound sites that were opened outside the correct portal iframe. `_shipped_frame` uses it for shipped app pages opened cold. It delegates page wrapping to `_page` when there is no portal.

*Call graph*: calls 2 internal fn (home_url, _page); called by 2 (_shipped_frame, frame); 2 external calls (HTMLResponse, RedirectResponse).


##### `_unconfigured_page`  (lines 455–463)

```
def _unconfigured_page(title: str, share: str) -> str
```

**Purpose**: Builds the HTML shown when site hosting or ingress is not configured. Instead of displaying a blank iframe, the user sees a clear explanation.

**Data flow**: It receives a title and sharing tags. It escapes the title for safe HTML, combines the basic styles with frame styles, and returns a complete page saying hosting is not configured.

**Call relations**: `frame` uses this when a normal or homepage site cannot get an ingress URL. `_shipped_frame` uses it for shipped app pages in the same situation. It relies on `_page` for the common document shell.

*Call graph*: calls 1 internal fn (_page); called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `share_card`  (lines 466–505)

```
async def share_card(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the preview image for a public site, and only while it is still safe to publish. This route is anonymous because link preview crawlers do not carry a user's session.

**Data flow**: It resolves the site from the token in the path. It refuses the request if the site does not exist, is homepage-bound, is not public, lacks a card blob, or the URL digest does not match the current row. If all checks pass, it reads the image bytes from blob storage and returns them with image headers and a short cache time.

**Call relations**: This is the GET handler for share-card URLs built by `site_card_url`. It uses `_resolve` to find the site and `_not_found` for every refusal, so callers cannot use this route to learn private details.

*Call graph*: calls 2 internal fn (_not_found, _resolve); 1 external calls (Response).


##### `set_visibility`  (lines 508–529)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets the creator of a site change whether it is private, workspace-visible, or public. It protects that action with both identity checks and a CSRF token, which is a signed value proving the form came from this user's own session.

**Data flow**: It resolves the site, identifies the viewer, and confirms the viewer is the creator. It refuses homepage-bound sites because their visibility follows the agent. It reads the submitted form, validates the CSRF token, parses the requested visibility level, updates the stored site row, and redirects back to the frame.

**Call relations**: This is the POST handler behind the visibility selector rendered by `_frame_page` and `_selector`. It uses `_viewer`, `_csrf_holds`, `_sites`, and the store's visibility parser before saving the change.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 532–536)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Looks up a hosted site row from the token in the request. It is a small shared helper for routes that need a site but do not need the full frame flow.

**Data flow**: It reads the token path parameter, decodes it with `site_address`, and returns `None` if the token is invalid. If valid, it opens the hosted-sites store for the current workspace and reads the row by conversation and site name.

**Call relations**: `share_card` and `set_visibility` call this before applying their own rules. It uses `_sites` so both routes read through the same workspace-scoped store wrapper.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (set_visibility, share_card).


##### `_sites`  (lines 539–540)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the store object used to read or change hosted-site records for the current workspace. It keeps store construction in one place.

**Data flow**: It receives the surface context. It takes the workspace ID and transaction factory from that context and returns a `HostedSites` store object.

**Call relations**: `frame`, `_resolve`, and `set_visibility` call this when they need to read a site row or update its visibility.

*Call graph*: called by 3 (_resolve, frame, set_visibility); 1 external calls (__init__).


##### `_viewer_is_admin`  (lines 543–548)

```
async def _viewer_is_admin(ctx: SurfaceContext, viewer: UUID | None) -> bool
```

**Purpose**: Checks whether the current viewer is a seated workspace admin. Admins are allowed to see private sites and private agent homepages even when they are not the creator or owner.

**Data flow**: It receives the context and an optional viewer ID. If there is no viewer, it returns `False`. Otherwise it opens a transaction, reads the workspace seats snapshot, and returns whether that viewer is present, seated, and marked admin.

**Call relations**: `frame` calls this only after it has identified a viewer and needs to decide whether private access should be allowed. The seat data comes from the `Seats` helper.

*Call graph*: calls 1 internal fn (transaction); called by 1 (frame); 1 external calls (__init__).


##### `_viewer`  (lines 551–564)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Identifies the workspace member behind the request's `ufo_session` cookie. If the cookie is absent, invalid, or does not belong to a member with access, it returns no viewer.

**Data flow**: It reads the session cookie, verifies the bearer token for this workspace, and gets an email identity. It links that email to a member if needed, then checks that the member still has workspace access. It returns the member ID or `None`.

**Call relations**: `frame` uses this to decide whether a visitor can see non-public sites. `set_visibility` uses it to prove that the poster is the site's creator before accepting a visibility change.

*Call graph*: calls 3 internal fn (link_member, linked_member, member_has_access); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 567–569)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility form token matches this browser session. This blocks another website from secretly submitting a visibility change on the creator's behalf.

**Data flow**: It receives the request and submitted token. It verifies the token signature, computes the digest of the current session cookie, and compares that digest with the token's CSRF claim. It returns `True` only when they match.

**Call relations**: `set_visibility` calls this after proving the viewer is the creator but before changing the stored visibility. It uses `_session_digest` so both token creation and checking bind to the same session value.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 572–576)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a one-way fingerprint of the current session cookie for CSRF protection. A one-way fingerprint means the code can compare sessions without putting the raw cookie into the CSRF token.

**Data flow**: It reads the `ufo_session` cookie from the request, uses an empty string if it is missing, hashes it with SHA-256, and returns the hexadecimal hash text.

**Call relations**: `frame` uses this when minting the creator's CSRF token for the visibility form. `_csrf_holds` uses it later to check that a submitted token belongs to the same session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 579–580)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard not-found response for missing, invalid, or hidden sites. Using the same body helps avoid revealing whether a site exists.

**Data flow**: It takes no input. It returns a plain-text HTTP 404 response with the shared `no such site` body.

**Call relations**: `resolve_workspace`, `frame`, `_shipped_frame`, `share_card`, and `set_visibility` all use this for both real misses and deliberate denials.

*Call graph*: called by 5 (_shipped_frame, frame, resolve_workspace, set_visibility, share_card); 1 external calls (PlainTextResponse).


##### `_page`  (lines 583–588)

```
def _page(title: str, style: str, body: str, share: str) -> str
```

**Purpose**: Builds the basic HTML document wrapper used by this surface. It keeps the document structure consistent across normal frames, error pages, and portal messages.

**Data flow**: It receives a title, CSS style text, body HTML, and sharing metadata. It returns one complete HTML string with charset, viewport, title, metadata, styles, and body.

**Call relations**: `frame`, `_frame_page`, `_into_the_portal`, and `_unconfigured_page` call this when they need to return an HTML page rather than a redirect or plain response.

*Call graph*: called by 4 (_frame_page, _into_the_portal, _unconfigured_page, frame).


##### `_share_tags`  (lines 591–625)

```
def _share_tags(name: str | None, canonical: str | None, card: str | None) -> str
```

**Purpose**: Builds the social preview metadata placed in the page head. It carefully names the site only when the caller has decided that information is public.

**Data flow**: It receives an optional site name, optional canonical URL, and optional site-card image URL. It escapes values for safe HTML, chooses either the site's card or the generic UFO card, and returns Open Graph and Twitter card tags.

**Call relations**: `frame` calls this after checking whether the site is public enough to publish its name and card. `_shipped_frame` calls it with no site-specific values, producing generic sharing metadata.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (escape).


##### `_frame_page`  (lines 628–667)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str, share: str) -> str
```

**Purpose**: Builds the outer page that surrounds a hosted site. It shows the site name, either a creator visibility selector or a viewer badge, and the sandboxed iframe that displays the actual site.

**Data flow**: It receives the site row, optional embedded ingress URL, the stable frame path, an optional CSRF token, and sharing tags. It chooses a form or badge, creates an iframe when ingress is available, or an unconfigured message when it is not. It returns the complete HTML page.

**Call relations**: `frame` calls this after all access checks pass for a normal hosted site. It uses `_selector` when the viewer is the creator and `_page` for the final document wrapper.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 670–680)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the small visibility form shown to a site's creator. The form lets the creator choose private, workspace, or public and submit the change safely.

**Data flow**: It receives the current visibility level, the frame path to post back to, and the CSRF token. It builds option tags with the current level selected, includes the hidden CSRF field, and returns the form HTML.

**Call relations**: `_frame_page` calls this only when `frame` has supplied a CSRF token, which happens for the creator's own signed-in session. The form posts to the `set_visibility` route.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).

## 📊 State Registers Touched

- `reg-persistence-handles` — The shared database and blob-storage connections used to read and save durable system data.
- `reg-feature-flags` — The shared on/off switches that let the service enable or disable behavior at runtime.
- `reg-extension-registry` — The discovered set of installed extensions and the capabilities each extension contributes.
- `reg-workspace-directory` — The shared record of workspaces, members, seats, admins, invitations, and onboarding status.
- `reg-member-session-auth` — The signed tokens and browser/session identity state that prove who is making a request.
- `reg-runtime-authority` — The current workspace, agent, and member identity under which work is allowed to act.
- `reg-agent-registry` — The saved agents, their owners, visibility, model choices, tool policies, and sandbox settings.
- `reg-conversation-transcripts` — The durable conversation history, compacted records, audiences, and readable timeline data.
- `reg-turn-queue-state` — The durable state of conversation turns, including pending, running, paused, cancelled, and finished work.
- `reg-live-update-streams` — The shared live progress channels that stream text, status, costs, and completion events to clients.
- `reg-surface-routing-state` — The saved routing state for web, Slack, iMessage, terminal, and other public conversation surfaces.
- `reg-inbound-delivery-ledger` — The durable deduplication and delivery records for inbound messages, writebacks, and mid-turn replies.
- `reg-object-store-and-journal` — The shared workspace object records and change history for agents, tasks, memories, sites, and related items.
- `reg-source-index` — The stored external sources, synced pages, permissions, indexing status, and retry/backoff state.
- `reg-memory-store` — The remembered facts and searchable memory chunks that can be retrieved or condensed later.
- `reg-artifact-blob-store` — The shared files, media blobs, previews, metadata, and signed-download records created by agent work.
- `reg-hosted-site-registry` — The saved hosted-site names, owners, visibility, ports, files, previews, and ingress routing state.
- `reg-scheduled-work-store` — The durable records for recurring tasks, delayed resumes, scheduled fires, and background job claims.
- `reg-notification-inbox` — The stored pending notifications and delivery state used to batch notices and wake conversations.
- `reg-monitor-objective-state` — The saved monitors, objectives, plans, steps, evidence, and blocks that survive across turns.
- `reg-build-metadata` — The product, package, version, and build identity exposed to CLI/admin surfaces, health checks, and telemetry.
- `reg-extension-state-store` — Generic per-workspace extension-owned durable key/value or configuration state not covered by a named core store.
- `reg-credential-request-state` — Pending and fulfilled credential-connection requests, OAuth/device-code callback context, and idempotency markers for credential fulfillment.
- `reg-workspace-change-log` — Durable per-conversation sandbox file-change snapshots and summaries used after tool execution and shown in workspace-change slots.
- `reg-transcript-access-audit` — Durable audit records of privileged/admin reads of private member transcripts for compliance and safety review.
- `reg-rate-limit-buckets` — Shared throttling counters, leases, and cooldown state for ingress, provider/model calls, connector actions, and background workers, separate from spend-cap accounting.
- `reg-proposal-review-state` — Durable reviewable-change proposals with source/target digests, creator, approval state, and publication lifecycle outside the self-improvement prompt-promotion loop.
