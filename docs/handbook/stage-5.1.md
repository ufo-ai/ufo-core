# Web portal and panel API ingress  `stage-5.1`

This stage is the front door for the web version of the system. It is part of the live user-facing loop: loading the browser app, checking who the user is, accepting chat messages, streaming answers back, and showing workspace panels.

The main web surface is the central reception desk. It serves the web app, verifies signed-in users, exposes chat routes, streams agent replies, and provides data for panels such as memory, files, usage, skills, and admin views. The audience rules act like the door policy: they decide which members may see or use which agents, and give admins tools to grant or remove access.

The panels bridge turns settings forms into the same normal action path used by chat, so changes to agents, members, credentials, and access do not need separate custom endpoints. The memory surface adds a read-only memory explorer for authorized operators. The community directory reader lets the app browse public skills, while guarding against slow, oversized, broken, or rate-limited responses. Together, these pieces make the web portal usable, controlled, and safe.

## Files in this stage

### Portal API ingress
The main web surface serves the browser app and exposes the authenticated routes for chat, streaming, workspace data, and portal panels.

### `extensions/web/ufo_ext_web/surface.py`

`orchestration` · `request handling, streaming, and scheduled background jobs`

This file is the bridge between a member using the browser and the core UFO system. It treats the browser like one “surface,” meaning one place where humans can talk to agents and inspect their work. First, it verifies the signed session cookie and turns the email in that cookie into a workspace member. Then it uses a web audience check to decide which agents and workspace records that member may see.

The file serves the built frontend files, including a careful fallback for assets from another deployed version so rolling upgrades do not break open browser tabs. It also defines the main chat path. A first message can open a conversation, file uploads are saved into that conversation’s workspace, and the message is admitted into the shared durable queue that agents consume. Live progress comes back through server-sent events, a simple browser streaming mechanism where the server sends named events over one long response.

Beyond chat, this file provides read-only portal panels: agents, conversations, transcripts, skills, connections, memory, artifacts, scheduled-run “radar,” usage, object lists/details, credentials, team, surfaces, and admin state. Most route functions follow the same pattern: authenticate, check audience, ask the privileged SurfaceContext for the real data, then return a small JSON shape for the frontend.

#### Function details

##### `load_assets`  (lines 195–205)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Loads the web app’s built static files from disk into memory, but only for file types this surface knows how to serve. This keeps accidental build leftovers, such as source maps, from being published unless explicitly allowed.

**Data flow**: It receives a directory path, scans the files inside it, reads allowed files as bytes, pairs each with its media type, and returns a lookup table keyed by request-friendly asset names.

**Call relations**: It runs when the module is imported to build the static asset table used later by static file responses.

*Call graph*: 1 external calls (glob).


##### `resolve_workspace`  (lines 218–257)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace an incoming web request belongs to before the route handler runs. It uses the signed bearer token from the session cookie, or from the one login form post that creates that cookie.

**Data flow**: It reads cookies, request method, content type, small form data when needed, and an optional chat target. It returns a workspace id, a redirect to sign-in, a refusal response, or None when the request cannot be scoped.

**Call relations**: The shared surface framework calls this as the early workspace gate. It uses _framed_length and _form before reading a login form, and _chat_target when preserving a conversation link through sign-in.

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 260–264)

```
def _chat_target(request: Request) -> UUID | None
```

**Purpose**: Safely reads a conversation id from the portal URL, if one is present. It rejects anything that is not a real UUID by returning no target.

**Data flow**: It reads the c query parameter, tries to parse it as a UUID, and returns that UUID or None.

**Call relations**: resolve_workspace uses it when redirecting an unsigned visitor to login so a valid target conversation can survive the sign-in trip.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 267–277)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Returns a response for a static frontend asset that exists in this build. It also supports browser revalidation through an ETag, which is a content fingerprint.

**Data flow**: It reads the requested path, looks it up in the in-memory asset table, and either returns None or passes the file bytes, type, and ETag to _asset_response.

**Call relations**: static_asset tries this first before falling back to the shared stored-asset path for mixed-version deploys.

*Call graph*: calls 1 internal fn (_asset_response); called by 1 (static_asset).


##### `_asset_response`  (lines 280–284)

```
def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response
```

**Purpose**: Builds the actual HTTP response for a static asset. It sends 304 Not Modified when the browser already has the same content.

**Data flow**: It receives the request, bytes, media type, and ETag. It compares the request’s if-none-match header, then returns either an empty 304 response or the asset body with cache headers.

**Call relations**: _static_response and _stored_asset both use it so local and shared-store assets behave the same way.

*Call graph*: called by 2 (_static_response, _stored_asset); 1 external calls (Response).


##### `_publish_assets`  (lines 295–299)

```
async def _publish_assets(blob: BlobStore) -> None
```

**Purpose**: Copies this build’s static assets into the shared blob store if they are not already there. This lets another server instance serve assets for a page created by this one.

**Data flow**: It reads the in-memory asset table, checks each key in the blob store, and writes missing asset bytes.

**Call relations**: _assets_published starts this work before portal_page serves the main HTML shell.

*Call graph*: calls 2 internal fn (exists, put); called by 1 (_assets_published).


##### `_assets_published`  (lines 302–314)

```
def _assets_published(blob: BlobStore) -> 'asyncio.Task[None]'
```

**Purpose**: Ensures this process starts exactly one asset-publishing task, and retries if the previous attempt failed. It is a small guard against duplicate work and broken deploy rollouts.

**Data flow**: It checks the module-level publish task, creates a new asyncio task when needed, stores it, and returns the task for callers to await.

**Call relations**: portal_page calls it before serving the browser shell so any assets named by that shell are available in shared storage.

*Call graph*: calls 1 internal fn (_publish_assets); called by 1 (portal_page); 1 external calls (create_task).


##### `_stored_asset`  (lines 317–339)

```
async def _stored_asset(blob: BlobStore, request: Request) -> Response
```

**Purpose**: Serves a static asset from the shared blob store when the current server build does not have it locally. This protects users during rolling deploys where their HTML came from a different version.

**Data flow**: It validates the requested asset name and type, checks an in-process cache, reads bytes from the blob store on a miss, stores a bounded cached copy, and returns a normal asset response or 404.

**Call relations**: static_asset calls it after _static_response cannot find a local file, and it reuses _asset_response to format the reply.

*Call graph*: calls 3 internal fn (exists, get, _asset_response); called by 1 (static_asset); 3 external calls (sha256, Path, Response).


##### `portal_page`  (lines 342–358)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main web portal HTML page to a signed-in request. It also fails clearly if the frontend has not been built.

**Data flow**: It checks that the built HTML exists, waits for assets to be published to shared storage, and returns the HTML with no-store caching.

**Call relations**: This is the GET route for the portal root. It relies on _assets_published so the assets referenced by the page are reachable.

*Call graph*: calls 1 internal fn (_assets_published); 1 external calls (HTMLResponse).


##### `_authenticate`  (lines 361–379)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

**Purpose**: Verifies the session cookie and resolves it to a workspace member. If the email is new but allowed, it links or creates the member record used by the rest of the portal.

**Data flow**: It reads the signed cookie, verifies it against the workspace, asks the context for the linked member or links one, and returns either member id plus email or a 401 response.

**Call relations**: _audience_for uses it for most portal routes, and fulfill_credential uses it directly for credential submission.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (_audience_for, fulfill_credential); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 382–388)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves JavaScript, CSS, fonts, and other declared static files for the portal. It only runs after the session has already been scoped.

**Data flow**: It receives a request, tries to serve the asset from this build, and if missing tries the shared blob store.

**Call relations**: This is the static asset route. It delegates to _static_response and _stored_asset.

*Call graph*: calls 2 internal fn (_static_response, _stored_asset).


##### `open_session`  (lines 391–416)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts the signed bearer token posted by the login flow, stores it as the browser session cookie, and redirects back into the portal. The token stays out of URLs.

**Data flow**: It checks the form size, parses the form, validates the token field’s basic shape, writes the session cookie, and returns a redirect or a 400-level response.

**Call relations**: This is the single POST route that opens a web session. It shares _framed_length and _form with other form-reading routes.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 419–423)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Reads the agent id from a route path and makes sure it is a valid UUID. Invalid ids become None so callers can answer not found.

**Data flow**: It reads request.path_params['agent_id'], parses it as a UUID, and returns the UUID or None.

**Call relations**: chat, transcript, and _panel_gate call it before checking whether the signed-in member may access that agent.

*Call graph*: called by 3 (_panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 426–427)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the private store key used for a web chat’s metadata row. That row binds a conversation to the web member and selected agent.

**Data flow**: It receives a conversation id and returns a string key under the chat store prefix.

**Call relations**: _open_conversation writes this key, _own_web_chat reads it, and chats_index bulk-reads these keys for rail rows.

*Call graph*: called by 3 (_open_conversation, _own_web_chat, chats_index).


##### `_chat_title`  (lines 436–454)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates a short human label for a chat from the first message or attached filenames. It trims neatly so the rail does not show awkward half-phrases.

**Data flow**: It receives message text and saved file paths, collapses whitespace, falls back to filenames when text is empty, cuts to the maximum title length, and returns the cleaned title.

**Call relations**: _open_conversation uses it for brand-new chats, and summarize_chat_titles reuses it to clean model-written titles.

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_title_excerpt`  (lines 466–484)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

**Purpose**: Builds the short conversation excerpt used when asking the model to write a better chat title. It uses the first user and assistant messages only.

**Data flow**: It receives transcript messages, extracts rendered text, strips web/context wrapper text from the user side, limits each part, and returns joined text or an empty string.

**Call relations**: summarize_chat_titles calls it before asking the model for a title. It depends on _rendered_text and member_message_text to show real user words.

*Call graph*: calls 1 internal fn (_rendered_text); called by 1 (summarize_chat_titles); 1 external calls (member_message_text).


##### `summarize_chat_titles`  (lines 487–533)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

**Purpose**: Background job that gives conversations better titles after an opening exchange exists. This makes the chat rail easier to scan than using only the first raw message.

**Data flow**: It asks core for conversations awaiting titles, reads their transcripts, builds excerpts, optionally asks the model for a short title, cleans it, and records that the title attempt is done.

**Call relations**: A scheduled job calls this. It uses _title_excerpt and _chat_title, then writes results through the extension context.

*Call graph*: calls 4 internal fn (conversations_awaiting_title, summarized_conversation_title, _chat_title, _title_excerpt); 2 external calls (__init__, __init__).


##### `seed_homepages`  (lines 536–594)

```
async def seed_homepages(ctx: ExtensionContext, bucket: str | None=None) -> None
```

**Purpose**: Background job that asks each agent to build its first homepage once. It marks agents as seeded so the fleet is not repeatedly asked to do the same setup work.

**Data flow**: It reads workspace agents and existing markers, chooses an acting member, opens or reuses a conversation, invokes a scheduled prompt, checks cancellation, and writes a marker on accepted runs.

**Call relations**: A scheduled job calls this. It uses the extension context to open conversations and admit scheduled work on behalf of an owner or admin.

*Call graph*: calls 5 internal fn (earliest_seated_admin, invoke, open_conversation, turn_outcomes, workspace_agents); 1 external calls (now).


##### `_open_conversation`  (lines 597–630)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and writes the web-only ownership record that later gates access. It is careful about races so duplicate opens land on the winning conversation.

**Data flow**: It receives agent, member, email, queue key, text, and file paths. It writes a tentative chat row, asks core for the conversation, deletes the row if another conversation won, retitles new conversations, and returns id plus title.

**Call relations**: chat calls it when the request asks for a new conversation. It uses _chat_row_key, _chat_title, _own_web_chat, and _named.

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_web_chat); called by 1 (chat); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 633–640)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

**Purpose**: Looks up the current display title for one conversation. It avoids inventing a separate name in the web layer.

**Data flow**: It asks core for the one listed conversation matching agent, member, and conversation id, then returns its title or an empty string.

**Call relations**: _open_conversation and chat use it whenever they need to return the title that core’s listing owns.

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 2 (_open_conversation, chat).


##### `_own_web_chat`  (lines 643–655)

```
async def _own_web_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is this exact member’s web chat with this exact agent. This prevents one member or agent from opening another member’s web chat by guessing an id.

**Data flow**: It reads the chat row from the store, validates it as a ChatRecord, compares agent id and email, and returns the record or None.

**Call relations**: _member_chat builds on it, and _open_conversation uses it to verify a race-winning conversation has the expected web row.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 2 (_member_chat, _open_conversation).


##### `_member_chat`  (lines 658–679)

```
async def _member_chat(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID) -> ListedConversation | None
```

**Purpose**: Decides whether the current member may treat a conversation as a chat with the selected agent. It allows this surface’s own web chats and certain member-private extension conversations.

**Data flow**: It checks the web chat row, asks core for the listed conversation, and returns the listed conversation only when the record and audience rules match.

**Call relations**: chat and transcript use it to gate conversation ids. _resolve_chat and _member_turn also use it when resolving permalinks or stream access.

*Call graph*: calls 2 internal fn (list_agent_conversations, _own_web_chat); called by 4 (_member_turn, _resolve_chat, chat, transcript); 1 external calls (conversation_audience).


##### `_turn_context`  (lines 682–693)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

**Purpose**: Builds the small context object attached to a submitted turn: who sent it, where it came from, and optionally the browser’s timezone. Bad timezone names are ignored rather than blocking the message.

**Data flow**: It reads the timezone header, tries to create a TurnContext with sender/source/timezone, logs and drops the timezone if validation fails, and returns the context.

**Call relations**: chat calls it just before admitting a member message to the core queue.

*Call graph*: called by 1 (chat); 2 external calls (__init__, log).


##### `_chat_source`  (lines 696–704)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Creates the human-readable source string for a web chat message. When possible, it includes a portal link back to the conversation and the sender email.

**Data flow**: It receives the public base URL, conversation id, and email. It returns either a portal conversation URL plus email or a simpler web/email label.

**Call relations**: chat passes this into _turn_context so downstream work can say where the request came from.

*Call graph*: called by 1 (chat).


##### `_audience_for`  (lines 707–714)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Combines authentication with the web-specific permission set for this member. Most routes use it as their first step.

**Data flow**: It authenticates the request, then asks the web audience module which agents and admin powers this email has, returning member id, email, and audience or an error response.

**Call relations**: It is the common gate for agent lists, chat, panels, workspace pages, object reads, admin reads, and streaming ownership checks.

*Call graph*: calls 1 internal fn (_authenticate); called by 18 (_member_turn, _object_gate, _panel_gate, admin_index, agents_index, chat, chats_index, connection_pool, github_coverage, transcript (+8 more)); 2 external calls (web_audience, web_extension).


##### `agents_index`  (lines 717–757)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the portal’s boot data: the signed-in member, visible agents, subagent profiles, and the schema needed to create a new agent. This is usually the first API call the browser makes.

**Data flow**: It authenticates and builds the web audience, optionally reads web-audience grants for admins, formats agents and subagents, adds create-agent metadata, and returns JSON.

**Call relations**: This route depends on _audience_for, granted_emails, and agent_create_schema to populate the frontend’s initial agent screen.

*Call graph*: calls 1 internal fn (_audience_for); 4 external calls (JSONResponse, granted_emails, web_extension, agent_create_schema).


##### `_framed_length`  (lines 760–773)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Protects routes that must parse a whole form body by requiring an honest Content-Length under a limit. Chunked bodies are refused because their size cannot be trusted up front.

**Data flow**: It reads transfer-encoding and content-length headers, returns 411 or 413 when unsafe or too large, and returns None when parsing may continue.

**Call relations**: resolve_workspace, open_session, _parse_inbound, and fulfill_credential call it before form parsing.

*Call graph*: called by 4 (_parse_inbound, fulfill_credential, open_session, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 776–783)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses a request form and turns parser failures into a normal bad-request response. This keeps malformed client input from surfacing as an internal error.

**Data flow**: It calls request.form(), returns the parsed form, or returns a 400 response if form parsing fails.

**Call relations**: It is shared by login, chat multipart parsing, credential fulfillment, and workspace resolution from a posted token.

*Call graph*: called by 4 (_parse_inbound, fulfill_credential, open_session, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 786–794)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a non-form request body under a hard byte limit. It limits what is actually received, not just what the client claimed it would send.

**Data flow**: It streams chunks from the request, appends them until the cap is exceeded, and returns either the bytes or a 413 response.

**Call relations**: _parse_inbound uses it for plain-text chat messages.

*Call graph*: called by 1 (_parse_inbound); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 797–833)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...]] | Response
```

**Purpose**: Reads a chat submission as either plain text or multipart form with files. It rejects unsupported or unsafe body shapes.

**Data flow**: It inspects content type, reads and decodes plain text under a byte cap, or parses bounded multipart form data into message text and upload files, returning either that pair or an error response.

**Call relations**: chat calls it before deciding whether the request is a message, file upload, answer, or stop request.

*Call graph*: calls 3 internal fn (_bounded_body, _form, _framed_length); called by 1 (chat); 1 external calls (Response).


##### `_inbox_paths`  (lines 836–840)

```
def _inbox_paths(uploads: tuple[UploadFile, ...]) -> tuple[str, ...]
```

**Purpose**: Assigns safe workspace paths for uploaded files under the web inbox folder. It avoids filename collisions in one message.

**Data flow**: It receives upload objects, normalizes each filename through inbox_name, tracks names already used, and returns tupled workspace paths.

**Call relations**: chat uses these paths before _deliver_uploads saves the files and _files_note mentions them in the admitted message.

*Call graph*: called by 1 (chat); 1 external calls (inbox_name).


##### `_deliver_uploads`  (lines 843–852)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], paths: tuple[str, ...]) -> None
```

**Purpose**: Writes uploaded chat files into the conversation workspace before the agent turn starts. That way the agent can read the files from the paths named in the message.

**Data flow**: It receives uploads and matching paths, streams each upload in chunks, and asks the surface context to write each workspace file.

**Call relations**: chat calls it after opening or validating the conversation and before admitting the message.

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (chat).


##### `_files_note`  (lines 855–858)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Adds a plain note to the admitted message listing where attached files were saved. This tells the agent exactly which workspace paths to inspect.

**Data flow**: It receives the member’s text and saved paths, builds an attachment note, and returns either text plus note or just the note.

**Call relations**: chat uses it when uploads are present, before enforcing the inbound character limit.

*Call graph*: called by 1 (chat).


##### `_upload_chunks`  (lines 861–863)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded file in fixed-size chunks. This avoids loading large uploads all at once in this helper.

**Data flow**: It repeatedly reads chunks from an UploadFile until no more bytes remain, yielding each chunk.

**Call relations**: _deliver_uploads passes this async byte stream to the workspace file writer.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_headers`  (lines 866–878)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Parses headers used when a member answers a question the agent asked. Validating early prevents malformed answer clicks from opening or changing conversations.

**Data flow**: It reads answer turn and question index headers, returns None for a normal message, returns parsed turn UUID plus question index, or returns a 400 response.

**Call relations**: chat calls it before admitting an answer and uses the result to build an idempotency key.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 881–890)

```
def _stop_header(request: Request) -> UUID | None | Response
```

**Purpose**: Parses the header used when the member presses stop on a running turn. It makes sure the stop names a valid turn id.

**Data flow**: It reads the stop-turn header, returns None for a normal message, returns a UUID for a stop request, or returns a 400 response.

**Call relations**: chat calls it before reading or acting on the message body.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `chat`  (lines 893–990)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives one chat action from the browser: start a chat, continue a chat, answer a question, upload files, or stop a turn. It is the main write path for member messages in the web portal.

**Data flow**: It authenticates, checks agent and conversation access, parses stop/answer/body/uploads, opens or validates the conversation, saves uploads, admits the message or stops the turn, and returns JSON naming the turn and conversation outcome.

**Call relations**: This POST route coordinates many helpers: _audience_for, _agent_param, _parse_inbound, _open_conversation, _member_chat, _deliver_uploads, _turn_context, and core admission/stop calls.

*Call graph*: calls 16 internal fn (admit, admitted_body, stop_turn, _agent_param, _answer_headers, _audience_for, _chat_source, _deliver_uploads, _files_note, _inbox_paths (+6 more)); 5 external calls (JSONResponse, Response, web_extension, UUID, uuid4).


##### `_rendered_text`  (lines 993–1004)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Turns a stored model message into the plain text the portal should display. It strips hidden context wrappers from user messages.

**Data flow**: It receives a Message, extracts string content or text blocks, removes web/context tags for user messages, trims whitespace, and returns the visible text.

**Call relations**: _title_excerpt and _rendered_messages use it so titles and transcripts are based on the member-readable version of a message.

*Call graph*: called by 2 (_rendered_messages, _title_excerpt).


##### `_tool_event`  (lines 1007–1020)

```
def _tool_event(block: ToolUseBlock) -> dict[str, str]
```

**Purpose**: Converts a tool-use block into a small display event for the transcript. It hides internal request metadata that members should not see.

**Data flow**: It receives a tool-use block, copies it without the internal requested-by field, classifies it as a skill load or tool call, and returns a display dictionary.

**Call relations**: _subagent_activity and _rendered_messages call it while building visible work histories.

*Call graph*: called by 2 (_rendered_messages, _subagent_activity); 2 external calls (model_copy, tool_activity).


##### `_subagent_activity`  (lines 1042–1064)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

**Purpose**: Extracts the visible work a subagent did: notes, tools, and skills, in order. It only includes tool calls that actually produced activity.

**Data flow**: It receives subagent transcript messages, identifies active tool results, walks assistant blocks, converts notes and tool uses into display events, and returns a bounded list.

**Call relations**: _subagent_nodes calls it after reading each subagent conversation transcript.

*Call graph*: calls 1 internal fn (_tool_event); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 1067–1077)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

**Purpose**: Tries to decode a subagent’s final answer as a JSON object. Some subagents finish with structured data rather than plain prose.

**Data flow**: It receives an answer string, returns None if empty or not a JSON object, otherwise returns the decoded dictionary.

**Call relations**: _run_answer uses it before deciding how to display a run’s final output.

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 1080–1103)

```
def _payload_prose(value: JsonValue) -> str
```

**Purpose**: Turns structured JSON values into readable prose for the portal. Lists, objects, booleans, and numbers all get member-friendly text.

**Data flow**: It receives a JSON-like value, recursively renders strings, booleans, numbers, lists, and objects, and returns a text version.

**Call relations**: _run_answer uses it when a finish payload has multiple fields or nontrivial structure.

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 1106–1122)

```
def _run_answer(answer: str) -> str
```

**Purpose**: Produces the text shown as a subagent run’s answer. It hides unhelpful JSON wrapping when the payload is really just one prose field.

**Data flow**: It receives the terminal answer string, tries to decode it, returns raw text if not structured, returns the sole prose field when appropriate, or renders the whole payload as prose.

**Call relations**: _subagent_nodes uses it for subagent tree nodes, and _conversation_messages uses it for conversations made of subagent-profile runs.

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (_conversation_messages, _subagent_nodes).


##### `_subagent_nodes`  (lines 1125–1164)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

**Purpose**: Builds the nested tree of subagent runs spawned by a conversation or turn. This lets the UI show not just that a child ran, but what it did and what it answered.

**Data flow**: It receives turn records, finds spawned turns, names their profiles, reads bounded transcripts concurrently for activity, attaches children under parents, and returns a mapping from parent turn id to nodes.

**Call relations**: _conversation_messages uses it for settled transcripts, and _events uses it when a live turn finishes.

*Call graph*: calls 4 internal fn (list_agents, read_transcript, _run_answer, _subagent_activity); called by 2 (_conversation_messages, _events); 2 external calls (__init__, gather).


##### `_rendered_messages`  (lines 1167–1296)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

**Purpose**: Projects raw transcript messages into the chat bubbles and assistant replies the portal displays. It removes engine-only context and groups notes, tools, questions, files, and subagents under the right reply.

**Data flow**: It receives messages plus optional maps for subagents, speakers, questions, answers, and files. It walks the transcript, builds user bubbles and assistant replies, flushes replies at turn boundaries, and returns display dictionaries.

**Call relations**: _conversation_messages calls it as the central transcript rendering step.

*Call graph*: calls 2 internal fn (_rendered_text, _tool_event); called by 1 (_conversation_messages); 1 external calls (member_message_text).


##### `_rendered_messages.note_answer`  (lines 1222–1227)

```
def note_answer() -> None
```

**Purpose**: Moves a previously seen assistant answer into the list of work notes when later work shows it was not actually the final answer. This preserves narration in the right order.

**Data flow**: It reads the surrounding function’s current answer and pending event list, inserts the answer as a note when allowed, updates the note count, and clears the answer.

**Call relations**: It is an inner helper used only by _rendered_messages while grouping assistant messages.


##### `_rendered_messages.flush_reply`  (lines 1229–1250)

```
def flush_reply(include_subagents: bool) -> None
```

**Purpose**: Finishes the current assistant reply and appends it to the rendered transcript if it has anything visible. It attaches subagents, questions, and shared files when the reply belongs to a closing turn.

**Data flow**: It reads the surrounding pending answer/events and current turn id, gathers related data, appends one assistant reply dictionary if needed, and resets the buffers.

**Call relations**: It is an inner helper used only by _rendered_messages at user-message and turn boundaries.


##### `_conversation_messages`  (lines 1299–1442)

```
async def _conversation_messages(ctx: SurfaceContext, conversation_id: UUID, viewer: UUID) -> tuple[list[dict[str, object]], Turn | None]
```

**Purpose**: Builds the complete member-facing message list for one conversation, including settled transcript messages, the currently running turn, queued messages, shared files, questions, and subagent work.

**Data flow**: It reads transcript, speaker labels, origin markers, latest turn details, turns, artifacts, spawned subagents, and queued arrivals. It renders committed messages, appends live or waiting messages, and returns the message list plus latest turn if any.

**Call relations**: transcript and conversation_transcript call it. It relies on _rendered_messages, _subagent_nodes, _file_payload, and _run_answer.

*Call graph*: calls 13 internal fn (agent_origin_refs, arrival_speakers, conversation_subagent_turns, latest_turn, list_conversation_artifacts, list_turns, queued_arrivals, read_transcript, turn_detail, _file_payload (+3 more)); called by 2 (conversation_transcript, transcript); 2 external calls (gather, member_message_text).


##### `transcript`  (lines 1445–1475)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the chat transcript for one of the member’s own web conversations with an agent. If a turn is still running, it tells the browser which turn to stream.

**Data flow**: It authenticates, checks agent and conversation ownership, renders messages, and returns JSON with messages plus either a live turn id or pending handoffs from the latest terminal turn.

**Call relations**: This GET route uses _member_chat for access, _conversation_messages for projection, and _open_handoffs for credential prompts on reload.

*Call graph*: calls 5 internal fn (_agent_param, _audience_for, _conversation_messages, _member_chat, _open_handoffs); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 1478–1490)

```
async def _open_handoffs(ctx: SurfaceContext, turn_id: UUID, terminal: TerminalFrame) -> dict[str, object]
```

**Purpose**: Reports unfinished handoffs from a completed turn, currently credential prompts still waiting for member input. This lets reloads redraw prompts that were shown live.

**Data flow**: It receives a turn id and terminal frame, checks for a credential request, filters pending prompts, and returns a small handoff dictionary.

**Call relations**: transcript calls it when the latest turn has ended. It uses _pending_prompts for the per-slot pending check.

*Call graph*: calls 1 internal fn (_pending_prompts); called by 1 (transcript).


##### `chats_index`  (lines 1493–1556)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Builds the chat rail: the member’s own conversations and readable conversations from others across visible chat agents. It keeps private or rowless web conversations out of the rail.

**Data flow**: It authenticates, optionally resolves a requested permalink, lists conversations per visible agent and participation side, reads web chat records, formats rows, sorts by latest activity, and returns JSON.

**Call relations**: The browser calls this for the rail. It delegates permalink resolution to _resolve_chat and uses _chat_row_key and _iso while formatting rows.

*Call graph*: calls 5 internal fn (list_agent_conversations, _audience_for, _chat_row_key, _iso, _resolve_chat); 2 external calls (JSONResponse, web_extension).


##### `_resolve_chat`  (lines 1559–1628)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

**Purpose**: Resolves a conversation permalink into either a chat rail row or a read-only conversation projection. It lets the frontend open #/c/<id> links safely.

**Data flow**: It parses the requested id, checks whether it is one of the member’s chats, otherwise finds the owning agent and asks whether the member may see it, then returns JSON for a rail row, a conversation row, or no match.

**Call relations**: chats_index calls it when a conversation query is present. It uses _member_chat for own chats and _conversation_row for non-chat conversation projections.

*Call graph*: calls 7 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, _conversation_row, _iso, _member_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 1631–1644)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Common permission gate for routes that read or mutate data inside one agent’s panel. It makes sure the member is signed in and the path agent is in their web audience.

**Data flow**: It authenticates, parses the agent id, checks the audience, and returns member id, email, audience, and agent id or a 404/401 response.

**Call relations**: Skills, settings, conversations, connections, community skill pages, intents, usage, homepage, and readable-conversation checks call it first.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 10 (_readable_conversation, community_skill, community_skills, connections, conversations, homepage, intents, settings, skills, usage); 1 external calls (Response).


##### `_iso`  (lines 1647–1648)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Formats a datetime for JSON, while preserving None as null. It centralizes timestamp formatting.

**Data flow**: It receives a datetime or None and returns ISO-8601 text or None.

**Call relations**: Many response builders use it for dates in chat rows, conversations, memory, usage, artifacts, radar, and object details.

*Call graph*: called by 8 (_conversation_row, _memory_rows, _radar_run, _resolve_chat, _usage_payload, chats_index, object_detail, workspace_artifacts); 1 external calls (isoformat).


##### `_window_param`  (lines 1651–1667)

```
def _window_param(request: Request) -> int | None | Response
```

**Purpose**: Parses the usage time window requested by the browser. It accepts named ranges like 7d or a numeric number of seconds.

**Data flow**: It reads query parameters, validates the range or window_seconds value, and returns seconds, None for all time, or a 400 response.

**Call relations**: usage and workspace_usage use it before asking core for spending reports.

*Call graph*: called by 2 (usage, workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 1670–1710)

```
def _usage_payload(report: AgentSpendReport | MemberSpendReport | SpendReport) -> dict[str, object]
```

**Purpose**: Formats token and cost usage details into the JSON shape shared by agent, member, and workspace usage views.

**Data flow**: It receives a spend report, extracts selected-window totals, all-time totals, first-use date, daily lines, execution breakdown, and model breakdown, and returns a dictionary.

**Call relations**: usage and workspace_usage call it so all usage panels present the same nested usage data.

*Call graph*: calls 1 internal fn (_iso); called by 2 (usage, workspace_usage).


##### `skills`  (lines 1713–1733)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the skills available to the selected agent. These include the agent’s own skills and shared deployed skills.

**Data flow**: It gates the agent panel, asks core for the agent’s skills, formats each skill’s name, description, origin, and instructions, and returns JSON.

**Call relations**: This GET route uses _panel_gate before calling the surface context’s skill listing.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 1739–1743)

```
def _community_refusal(fault: Exception) -> Response
```

**Purpose**: Turns a community directory failure into a response the member can read directly. A special header tells the frontend the body is user-facing text.

**Data flow**: It receives an exception, converts it to text, and returns a 502 response with the refusal marker header.

**Call relations**: community_skills and community_skill call it when the external community directory is unavailable or errors.

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 1750–1766)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a page of community skill candidates for the selected agent, either popular results or search results. Applying one still goes through the intent system, not this read route.

**Data flow**: It gates the panel, validates a minimum search length, asks the community directory for listings, and returns skill JSON or a refusal response.

**Call relations**: This route uses _panel_gate and _community_refusal around COMMUNITY.listing.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 1769–1790)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches one community skill document so a member can inspect it before applying it. It validates the owner, repo, and skill name shapes before the lookup.

**Data flow**: It gates the panel, validates path segments, asks the community directory for the named skill, and returns the skill document, 404, or a refusal response.

**Call relations**: This route uses _panel_gate and _community_refusal around COMMUNITY.fetch.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 1793–1858)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows memory items visible to the member across agents they can reach. It supports both recent listings and text search.

**Data flow**: It authenticates, checks memory availability, computes audience subjects, then either lists recent memory with filters and cursor or searches each visible agent and deduplicates results, returning formatted rows.

**Call relations**: This workspace route uses _audience_for, ListingCursor decoding, SourceReader for per-agent searches, and _memory_rows for formatting.

*Call graph*: calls 5 internal fn (recent_memory, search_memory, decode, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 1861–1871)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Formats memory search or listing matches for the frontend. It includes the text, kind, optional object reference, subject, and creation time.

**Data flow**: It receives memory matches and returns a list of dictionaries with simple JSON values.

**Call relations**: workspace_memory calls it for both recent memory pages and search results.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `usage`  (lines 1874–1911)

```
async def usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns usage and cost information for the selected agent, including caps. It only exposes this for agents explicitly granted to the member or admin.

**Data flow**: It gates the panel, checks the audience grant, parses the usage window, asks core for agent spend, formats totals, caps, dimensions, and usage detail, and returns JSON.

**Call relations**: This route uses _panel_gate, _window_param, and _usage_payload.

*Call graph*: calls 4 internal fn (agent_spend, _panel_gate, _usage_payload, _window_param); 2 external calls (JSONResponse, Response).


##### `connections`  (lines 1914–1923)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns connector accounts related to the selected agent that the member may see. This can include the member’s private grants, shared grants, and more for admins.

**Data flow**: It gates the panel, asks core for agent connections under member/admin rules, serializes each entry, and returns JSON.

**Call relations**: This route uses _panel_gate before calling the surface context’s connection listing.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 1926–1936)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the member’s broader connection pool and filters each connection’s agent list to agents visible in the web audience. This keeps hidden agents out of shared connection data.

**Data flow**: It authenticates, lists connections for the member/admin view, removes agent references outside the audience, serializes the entries, and returns JSON.

**Call relations**: The route uses _audience_for and core’s list_connections.

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 1939–1945)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns GitHub coverage information for the member or admin. This likely powers a panel showing which repositories or accounts are connected or indexed.

**Data flow**: It authenticates, asks core for GitHub coverage using member and admin status, serializes the model, and returns JSON.

**Call relations**: The route is a thin authenticated wrapper around the surface context’s github_coverage call.

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 1948–1968)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists conversations for the selected agent that this member may see. Admins may see broader rows, including rows they can disclose before reading.

**Data flow**: It gates the panel, reads an optional bounded search string, asks core for conversation rows, converts each row into portal JSON, and returns the list.

**Call relations**: This route uses _panel_gate, _searched, and _conversation_row.

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 1971–1975)

```
def _searched(request: Request) -> str | None
```

**Purpose**: Reads and bounds the conversation search text from the query string. Empty search becomes None.

**Data flow**: It trims the q parameter, cuts it to the maximum search length, and returns the text or None.

**Call relations**: conversations passes its result into core’s conversation listing.

*Call graph*: called by 1 (conversations).


##### `_conversation_row`  (lines 1978–2009)

```
def _conversation_row(entry: ListedConversation, agent: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Formats one listed conversation for the portal. It omits content-like fields when core says the row is not readable.

**Data flow**: It receives a ListedConversation and optional owning agent info, extracts ids, surface labels, audience, speakers, counts, dates, readability, and disclosure state, and returns a dictionary.

**Call relations**: conversations uses it for per-agent lists, and _resolve_chat uses it for permalink resolution to non-chat conversations.

*Call graph*: calls 1 internal fn (_iso); called by 2 (_resolve_chat, conversations).


##### `_readable_conversation`  (lines 2012–2032)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Common gate for reading conversation content. It makes unreadable, malformed, wrong-agent, or unauthorized conversations all look like not found.

**Data flow**: It gates the agent panel, parses or receives a conversation id, asks core whether this member/admin may read it, and returns agent id, conversation id, and SlotViewer or an error response.

**Call relations**: conversation_transcript and _slot_target use it before any transcript or slot content is read.

*Call graph*: calls 2 internal fn (readable_conversation, _panel_gate); called by 2 (_slot_target, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 2035–2045)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a read-only transcript for an authorized conversation. This is for panels and non-chat conversations, not continuing the chat.

**Data flow**: It verifies the conversation is readable, renders the messages with _conversation_messages, and returns them as JSON.

**Call relations**: This route uses _readable_conversation for access and shares the same rendering path as chat transcripts.

*Call graph*: calls 2 internal fn (_conversation_messages, _readable_conversation); 1 external calls (JSONResponse).


##### `_slot_target`  (lines 2062–2086)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

**Purpose**: Resolves which conversation a slot request should read, including subagent conversations reached through a root conversation. It prevents arbitrary child conversation reads.

**Data flow**: It reads the route conversation id and optional root query parameter, checks readability of the root or conversation, verifies child lineage when needed, and returns a SlotTarget or 404 response.

**Call relations**: conversation_slots and conversation_slot call it before building slot context.

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 2089–2105)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

**Purpose**: Builds the context object that a conversation slot provider needs. A slot is a typed add-on panel, such as changes or artifacts, shown beside a conversation.

**Data flow**: It reads the conversation audience and transcript, combines them with extension context, ids, messages, and public base URL, and returns a ConversationSlotContext or None.

**Call relations**: conversation_slots and conversation_slot call it before summarizing or reading slot content.

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 2108–2193)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

**Purpose**: Adds host-provided projections to slot context for built-in slot types. For example, it prepares workspace changes, artifacts, sites, or scheduled-task visibility before a slot reads.

**Data flow**: It receives slot context, extension name, expected payload type, root id, and viewer. Depending on the slot kind, it reads changes, artifacts, or visible objects, enriches the context, and returns it.

**Call relations**: conversation_slots and conversation_slot call it so each provider gets the extra data it needs under the same authorization rules.

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 8 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit, urlunsplit).


##### `_authorized_slot_payload`  (lines 2196–2240)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

**Purpose**: Filters slot payloads so they only include items the viewer is authorized to see. For automations, it can keep the row while hiding private details.

**Data flow**: It receives a payload and context with visible items, removes unauthorized sites or automations, redacts automation description/latest response when content is not visible, and returns the adjusted payload.

**Call relations**: conversation_slot calls it after reading a provider payload and before returning JSON.

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 2243–2287)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of available conversation slots with counts for one authorized conversation. Slots with errors are logged and skipped rather than breaking the whole panel.

**Data flow**: It resolves the target conversation, builds shared slot context, loops through registered slot providers, projects context for each, asks for a summary count, and returns slot metadata for non-empty slots.

**Call relations**: This route uses _slot_target, _slot_context, _project_slot_context, and core’s summarize_conversation_slot.

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 2290–2317)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full payload for one named conversation slot. It verifies both conversation access and slot id.

**Data flow**: It resolves the target, finds the provider, builds and projects slot context, reads the payload, checks it is the expected type, filters authorization-sensitive content, and returns JSON.

**Call relations**: This route uses _slot_target, _slot_context, _project_slot_context, and _authorized_slot_payload.

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 2320–2323)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Extracts the workspace-changes projection from a slot context and fails if the context was not prepared correctly.

**Data flow**: It receives a ConversationSlotContext, checks that its projection is WorkspaceChanges, and returns it or raises an error.

**Call relations**: _read_changes and _summarize_changes use it for the built-in changes slot provider.

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 2326–2327)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Reads the prepared changes payload for the built-in changes slot. It does not compute changes itself.

**Data flow**: It receives slot context and returns the WorkspaceChanges projection from _changes_projection.

**Call relations**: It is registered as the read function for CHANGES_SLOT.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 2330–2331)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts changes for the built-in changes slot and hides the slot when there are none.

**Data flow**: It receives slot context, gets the changes projection, returns the number of changes or None for zero.

**Call relations**: It is registered as the summarize function for CHANGES_SLOT.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 2344–2347)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Extracts the artifacts projection from a slot context and fails if the context was not prepared correctly.

**Data flow**: It receives a ConversationSlotContext, checks that its projection is ArtifactsSlotPayload, and returns it or raises an error.

**Call relations**: _read_artifacts and _summarize_artifacts use it for the built-in artifacts slot provider.

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 2350–2351)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Reads the prepared artifacts payload for the built-in artifacts slot. It relies on earlier projection work to gather the artifacts.

**Data flow**: It receives slot context and returns the ArtifactsSlotPayload projection from _artifacts_projection.

**Call relations**: It is registered as the read function for ARTIFACTS_SLOT.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 2354–2356)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts artifacts for the built-in artifacts slot and hides the slot when there are none.

**Data flow**: It receives slot context, gets the artifacts projection, counts artifacts, and returns the count or None for zero.

**Call relations**: It is registered as the summarize function for ARTIFACTS_SLOT.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `workspace_credentials`  (lines 2369–2377)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns credential slots that members can fill, without ever returning secret values. This powers the workspace credentials panel.

**Data flow**: It authenticates, asks core for declared credential slots and fill state, serializes them, and returns JSON.

**Call relations**: This workspace route uses _audience_for and core’s list_credential_slots.

*Call graph*: calls 2 internal fn (list_credential_slots, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 2380–2398)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace member roster, including admin and seat status, plus whether the current member can add people. It shows the same authority chat would use.

**Data flow**: It authenticates, reads the web audience admin flag, asks core for members, formats email/admin/seated rows, and returns JSON.

**Call relations**: This workspace route uses _audience_for and core’s list_members.

*Call graph*: calls 2 internal fn (list_members, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 2401–2412)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns source bindings visible to the member, such as connected knowledge sources. Admins can see more, while normal members see their own and shared sources.

**Data flow**: It authenticates, asks core for sources using member id and admin status, serializes each entry, and returns JSON.

**Call relations**: This workspace route uses _audience_for and core’s list_sources.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `workspace_surfaces`  (lines 2415–2426)

```
async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns installed surface bindings for agents visible to the member. This supports topology or integrations views without exposing hidden agents.

**Data flow**: It authenticates, lists all installations, filters them to audience-allowed agent ids, serializes entries, and returns JSON.

**Call relations**: This workspace route uses _audience_for and core’s list_installations.

*Call graph*: calls 2 internal fn (list_installations, _audience_for); 1 external calls (JSONResponse).


##### `workspace_artifacts`  (lines 2429–2480)

```
async def workspace_artifacts(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a searchable, filterable page of files shared with the member. It includes download and preview links when available.

**Data flow**: It authenticates, validates cursor and filters, asks core for artifact rows under member/admin rules, formats file metadata, links, origins, and paging cursors, and returns JSON.

**Call relations**: This route uses _audience_for, ListingCursor decoding, _iso, and context artifact link helpers.

*Call graph*: calls 6 internal fn (artifact_link, artifact_preview_link, list_artifacts, decode, _audience_for, _iso); 2 external calls (JSONResponse, Response).


##### `_radar_run`  (lines 2483–2510)

```
def _radar_run(ctx: SurfaceContext, run: ScheduledRun, task_names: Mapping[UUID, str]) -> dict[str, object]
```

**Purpose**: Formats one scheduled run for the workspace radar feed. It includes the task name when it can be resolved and file artifacts the run shared.

**Data flow**: It receives a ScheduledRun and task-name lookup, derives a task id from the idempotency key, formats ids, times, status, text, source, and artifact links, and returns a dictionary.

**Call relations**: workspace_radar calls it for every run in the page.

*Call graph*: calls 3 internal fn (artifact_link, artifact_preview_link, _iso); called by 1 (workspace_radar); 1 external calls (scheduled_fire_task_id).


##### `_radar_task_names`  (lines 2513–2536)

```
async def _radar_task_names(ctx: SurfaceContext, audience: WebAudience, member_id: UUID, runs: tuple[ScheduledRun, ...]) -> dict[UUID, str]
```

**Purpose**: Builds a lookup from scheduled task ids to task names for runs visible in the radar feed. Deleted or invisible tasks simply have no name.

**Data flow**: It receives runs, finds agents that fired them, lists scheduled-task objects for those visible agents, extracts ids from fields, and returns a UUID-to-name dictionary.

**Call relations**: workspace_radar calls it before formatting runs with _radar_run.

*Call graph*: calls 1 internal fn (list_member_objects); called by 1 (workspace_radar); 2 external calls (__init__, UUID).


##### `workspace_radar`  (lines 2539–2586)

```
async def workspace_radar(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the feed of scheduled work that ran on its own and reported into readable conversations. It is like an activity radar for automations.

**Data flow**: It authenticates, parses cursor and optional agent filter, chooses a page size that includes today’s runs on the newest page, reads scheduled runs, resolves task names, formats rows, and returns cursors.

**Call relations**: This workspace route uses _audience_for, _radar_task_names, and _radar_run.

*Call graph*: calls 6 internal fn (count_scheduled_runs_since, list_scheduled_runs, decode, _audience_for, _radar_run, _radar_task_names); 4 external calls (now, JSONResponse, Response, UUID).


##### `workspace_usage`  (lines 2589–2664)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the member’s own usage and cost data, and for admins also the workspace-wide rollup. This is the portal’s main spending view.

**Data flow**: It authenticates, parses the usage window, reads member spend, formats caps and usage, optionally reads workspace rollup for admins, and returns JSON.

**Call relations**: This route uses _audience_for, _window_param, and _usage_payload.

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `_member_turn`  (lines 2667–2703)

```
async def _member_turn(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID] | Response
```

**Purpose**: Checks whether the signed-in member may access a live turn. It protects stream and stop-related access from guessed turn ids.

**Data flow**: It authenticates, parses the turn id, checks the turn owner, reads turn detail, verifies the agent is still allowed or the web chat is still owned, and returns member id plus turn id or a refusal.

**Call relations**: stream calls it before opening the server-sent event stream.

*Call graph*: calls 4 internal fn (turn_detail, turn_owner, _audience_for, _member_chat); called by 1 (stream); 3 external calls (Response, web_extension, UUID).


##### `stream`  (lines 2706–2714)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens the live event stream for a turn. The browser uses this to receive tokens, tool activity, cost updates, terminal state, files, connection prompts, and credential prompts.

**Data flow**: It verifies turn access, reads the Last-Event-ID header for resume support, and returns a StreamingResponse over _events with text/event-stream media type.

**Call relations**: This GET route delegates authorization to _member_turn and event production to _events.

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 2717–2718)

```
def _event(name: str, payload: dict[str, object]) -> bytes
```

**Purpose**: Creates one named server-sent event from a JSON payload. Server-sent events are browser-readable chunks formatted as event and data lines.

**Data flow**: It receives an event name and payload dictionary, JSON-encodes the payload, and returns bytes in SSE format.

**Call relations**: _events uses it for extra portal-specific events such as subagents, connection URLs, credential prompts, and files.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 2721–2734)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest) -> dict[str, object] | None
```

**Purpose**: Filters a credential request down to prompts that are still waiting for values. This prevents fulfilled or expired prompts from reappearing.

**Data flow**: It receives a credential request, checks each prompt with core, builds prompt dictionaries for pending slots, and returns prompt data or None.

**Call relations**: _events uses it during live streaming, and _open_handoffs uses it when reloading a completed transcript.

*Call graph*: calls 1 internal fn (credential_prompt_pending); called by 2 (_events, _open_handoffs).


##### `_file_payload`  (lines 2737–2754)

```
def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]
```

**Purpose**: Formats one shared artifact as a file card for chat. Preview links are made same-origin when possible so the portal’s browser security policy can load them.

**Data flow**: It receives a shared artifact, asks core for download and preview links, strips the origin from preview URLs, and returns file metadata and links.

**Call relations**: _conversation_messages uses it for transcript file attachments, and _events uses it when a live turn shares files.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 2 (_conversation_messages, _events); 2 external calls (urlsplit, urlunsplit).


##### `_events`  (lines 2757–2794)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams live frames for a running turn and injects web-specific events when a terminal frame arrives. It is the live tail behind the chat UI.

**Data flow**: It tails core frames from a turn, and for terminal frames it may read subagent results, connection URLs, credential prompts, and shared artifacts. It yields those extra events, then yields the original frame as SSE bytes.

**Call relations**: stream returns this async iterator. It uses _event, _sse, _subagent_nodes, _pending_prompts, and _file_payload.

*Call graph*: calls 10 internal fn (connect_url, conversation_subagent_turns, shared_artifacts, tail, turn_detail, _event, _file_payload, _pending_prompts, _sse, _subagent_nodes); called by 1 (stream).


##### `fulfill_credential`  (lines 2797–2825)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores a credential value privately in response to a prompt. The value is not posted as a chat message and does not enter the transcript.

**Data flow**: It authenticates, bounds and parses the form, validates sealed request, slot, and value fields, enforces a secret size limit, calls core to fulfill the sealed request, and returns stored status or an error.

**Call relations**: This POST route uses _authenticate, _framed_length, and _form before calling core’s credential fulfillment.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `admin_index`  (lines 2828–2879)

```
async def admin_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace administration snapshot for admins only. It includes agents, installations, web grants, members, seats, spend caps, and deploy settings.

**Data flow**: It authenticates, checks the admin flag, reads installations, grants, seat snapshot, spend caps, and deploy metadata, formats them, and returns JSON or 404 for non-admins.

**Call relations**: This route uses _audience_for, web_extension storage, Seats, and several SurfaceContext reads.

*Call graph*: calls 3 internal fn (list_installations, spend_caps, _audience_for); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


##### `_object_gate`  (lines 2882–2896)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

**Purpose**: Common gate for generic object pages. It authenticates, gets the web audience, and verifies that the requested object kind exists in this deploy.

**Data flow**: It reads the kind path parameter, asks core for the kind description, and returns member id, audience, and kind metadata or an error response.

**Call relations**: object_index and object_detail call it before reading any object rows.

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 2899–2909)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

**Purpose**: Finds the one agent namespace requested for an object read. Object data is always read through an agent’s namespace.

**Data flow**: It parses the agent query parameter as a UUID, searches the audience’s agents, and returns the matching AgentSummary or a 404 response.

**Call relations**: object_index uses it for single-agent reads, and object_detail requires it for the named object.

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_kind_payload`  (lines 2912–2919)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

**Purpose**: Builds shared metadata about an object kind for the frontend. It tells the UI which fields exist and whether apply/delete intents are available.

**Data flow**: It receives a PortalKind and returns kind name, list fields, spec schema, and booleans derived from ApplyIntent capabilities.

**Call relations**: object_index and object_detail include this metadata in their JSON responses.

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 2922–2929)

```
def _filter_value(raw: str) -> JsonValue
```

**Purpose**: Parses one object-list filter value from the query string. It lets filters express booleans or numbers instead of only strings.

**Data flow**: It receives raw text, tries to JSON-decode it, and returns the decoded scalar or the original string if decoding fails.

**Call relations**: object_index uses it while building ObjectListQuery filters.

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_merged_rank`  (lines 2932–2945)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```

**Purpose**: Computes a sort key for rows merged from multiple agents. This keeps a fan-out object index sorted as one list instead of grouped by agent.

**Data flow**: It receives a row and field name, classifies the field as missing, boolean, number, or text, and returns a tuple used for sorting.

**Call relations**: object_index uses it after reading object pages across multiple agents.


##### `object_index`  (lines 2948–3018)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns rows for one generic object kind, either from one agent or fanned out across all visible agents. It powers reusable portal index pages for object kinds.

**Data flow**: It authenticates and validates the kind, parses sorting, cursor, search, agent, and filters, asks core to list objects per agent, formats rows with agent names, sorts fan-out results, and returns metadata plus rows.

**Call relations**: This route uses _object_gate, _object_agent, _filter_value, _merged_rank, and _kind_payload.

*Call graph*: calls 5 internal fn (list_member_objects, _filter_value, _kind_payload, _object_agent, _object_gate); 3 external calls (__init__, JSONResponse, Response).


##### `object_detail`  (lines 3021–3067)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the detail page for one generic object. It includes visible spec, live status fields, typed links, and timestamps.

**Data flow**: It authenticates, validates kind and agent, reads the named object, checks whether each linked object would open for this member, and returns JSON or 404.

**Call relations**: This route uses _object_gate, _object_agent, _kind_payload, and _iso around core’s member_object reads.

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 3070–3100)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Converts one live core frame into server-sent event bytes. It includes the frame cursor as an event id so reconnects can resume.

**Data flow**: It receives a cursor and LiveFrame, chooses the event name based on frame type, serializes the frame to JSON, and returns formatted SSE bytes.

**Call relations**: _events uses it for every core live frame after adding any portal-specific side events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 3103–3108)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Submits a prepared portal intent for the selected agent. Intents are the web panels’ shared mutation lane, rather than each panel writing directly.

**Data flow**: It gates the agent panel, then passes the request, agent id, member id, and email to submit_intent, returning that response.

**Call relations**: This route uses _panel_gate and hands off the real intent work to ufo_ext_web.panels.submit_intent.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `settings`  (lines 3111–3119)

```
async def settings(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent’s settings view, including configuration and admin-only grant details when allowed.

**Data flow**: It gates the agent panel, then calls agent_settings with the agent id and whether the viewer is admin, returning that response.

**Call relations**: This route uses _panel_gate and delegates formatting to ufo_ext_web.panels.agent_settings.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_settings).


##### `homepage`  (lines 3122–3147)

```
async def homepage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent’s homepage site URL if the member is allowed to see it. Private homepages remain hidden from other members, even admins, unless the site itself would open.

**Data flow**: It gates the panel, lists site objects filtered to the agent’s homepage binding, finds a row with a site URL, hides private non-owned rows, and returns either none or set with the URL.

**Call relations**: This route uses _panel_gate and core’s object listing for the site kind.

*Call graph*: calls 2 internal fn (list_member_objects, _panel_gate); 2 external calls (__init__, JSONResponse).


### Access-controlled data views
These files govern what users and operators may view, from workspace memory records to agent access rules and public community skill listings.

### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `operator request handling`

This file is the doorway for a small web surface: a read-only memory browser. Its job is to show an operator what the system has stored as “memory” for a selected workspace. Without it, operators would not have this built-in way to inspect what recall is drawing from, including both current and older memory records.

The file does two main things. First, it serves a ready-made HTML page from `static/memory.html`. That page is the visible explorer in the browser. Second, it provides a JSON endpoint that the page can call to fetch the actual memory records.

The important safety idea is workspace scoping. A workspace is the boundary around one group’s data. The surrounding operator session machinery checks who the operator is and binds the request to a workspace. Then this file opens the memory extension’s own scoped storage connection, so reads come from the extension’s `memory_item` table under that same workspace boundary. In plain terms, it is like opening the correct filing cabinet drawer after the receptionist has already checked which office you are allowed to inspect.

The routes at the bottom connect web requests to these actions: load the page, bind the operator session, and fetch the memory list as JSON.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the browser. It is used when an operator opens the surface itself, before the page asks for any memory data.

**Data flow**: It receives the surface context and the incoming web request, but it does not need to inspect them. It checks whether the HTML file was successfully loaded when this module started. If the file is present, it wraps that HTML text in an HTML web response; if it is missing, it raises an error so the problem is obvious instead of serving a broken page.

**Call relations**: This function is attached to the GET route for the surface root path. When that route is visited, it hands the browser a complete HTML page by creating an `HTMLResponse`.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns all memory records for the currently bound workspace as JSON. The memory explorer page uses it to fill the screen with the stored memories an operator is allowed to see.

**Data flow**: It receives the surface context, which includes the workspace id selected through the operator session, and the incoming request. It creates an extension-specific storage context for the memory extension, with no extra credentials declared. Through that context it asks the memory store inventory code for records in the current workspace. The returned memory objects are converted into plain JSON-friendly data and sent back as a JSON web response.

**Call relations**: This function is attached to the GET route `api/memories`, which the HTML explorer calls after loading. It builds the `ExtensionContext`, `ScopedStore`, and `CredentialAccess` needed to read the memory extension’s own table, then delegates the actual listing work to `ufo_ext_memory.store.inventory`. Finally, it wraps the resulting list in a `JSONResponse` for the browser.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).


### `extensions/web/ufo_ext_web/audience.py`

`domain_logic` · `request handling`

The web portal needs a clear answer to a sensitive question: when a person signs in, which agents are they allowed to reach? This file is the authority for that answer on the web surface. Think of it like the guest list at a building front desk: admins can enter every room, regular members can enter the main public room, and they may enter extra rooms only if their email is on that room’s list or if they own a private conversation there.

Access grants are stored as small rows in the web extension’s own storage area. Each row links one agent to one member email. The file can read all those rows for an admin view, or read only the rows for one email when building that member’s portal view.

The central result is a `WebAudience`, which says whether the member is an admin, which agents they can generally see, which extra private-conversation agents they may chat with, and which agents were granted explicitly. The difference matters: some screens should open only because an admin deliberately granted access, not merely because a member has a private conversation.

The file also defines three chat tools. Workspace admins can grant or revoke web access for the current agent, and they can acknowledge opening another member’s private transcript. These tools check that the speaker is a real workspace member, is an admin, and refers to an existing member email before changing anything.

#### Function details

##### `web_extension`  (lines 28–35)

```
def web_extension() -> ExtensionContext
```

**Purpose**: Creates the web extension’s own context so code handling the web surface can read and write the web audience store. This matters because web access grants live in the web extension’s private storage area, not in a global permission table.

**Data flow**: It takes no input. It builds a scoped store for the web extension and an empty credential access object, then wraps them in an extension context. The result is a ready-to-use handle for web audience storage and transactions.

**Call relations**: When a web surface handler needs to consult or change web audience rows, this helper provides the extension-shaped access point. Internally it puts together the scoped store, credential access, and extension context objects so callers do not have to repeat that setup.

*Call graph*: 3 external calls (__init__, __init__, __init__).


##### `_grant_key`  (lines 38–39)

```
def _grant_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Builds the storage key for one access grant: one agent plus one member email. It keeps keys consistent so granting, revoking, and listing all refer to the same stored row.

**Data flow**: It receives an agent ID and an email address. It trims spaces from the email, lowercases it, and combines it with the fixed audience prefix and agent ID. The output is a single string key that can be used in the extension store.

**Call relations**: The grant tool uses this key when writing an access row, and the revoke tool uses the same key when deleting that row. Because both paths call this helper, they agree on exactly where the grant is stored.

*Call graph*: called by 2 (_grant, _revoke).


##### `granted_emails`  (lines 42–49)

```
async def granted_emails(store: ScopedStore) -> dict[UUID, tuple[str, ...]]
```

**Purpose**: Reads every stored web access grant and groups them by agent. This is useful for an administration view that needs to show who has been explicitly allowed to reach each agent.

**Data flow**: It receives the web extension’s scoped store. It asks the store for all keys under the audience prefix, pulls the agent ID and email out of each key, groups emails under their agent IDs, sorts each agent’s email list, and returns a dictionary from agent ID to emails.

**Call relations**: This function reads the same rows that the grant and revoke tools write and delete. It depends on the store’s listing operation and converts the agent ID text found in keys back into UUID objects, which are standard unique identifiers.

*Call graph*: calls 1 internal fn (list); 1 external calls (UUID).


##### `_granted_agent_ids`  (lines 52–59)

```
async def _granted_agent_ids(store: ScopedStore, email: str) -> frozenset[UUID]
```

**Purpose**: Finds which agents have been explicitly granted to one email address. It is the lookup used when building a signed-in member’s web portal view.

**Data flow**: It receives the web extension’s scoped store and an email address. It normalizes the email by trimming and lowercasing it, scans all audience grant rows, keeps only rows whose stored email matches, converts their agent IDs into UUIDs, and returns those IDs as a frozen set.

**Call relations**: The `web_audience` function calls this after it has determined the member is not an admin. The returned agent IDs are then mixed with the main agent and member-owned agents to decide which agents appear in the portal.

*Call graph*: calls 1 internal fn (list); called by 1 (web_audience); 1 external calls (UUID).


##### `WebAudience.allows`  (lines 77–78)

```
def allows(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this member’s ordinary web audience includes a given agent. Code can use it as a simple yes-or-no gate before showing or opening an agent.

**Data flow**: It receives an agent ID. It checks that ID against the IDs in `self.agents`, the tuple of agents this audience can normally reach, and returns true if any match and false otherwise.

**Call relations**: This method is part of the `WebAudience` object produced by `web_audience`. Other web routes can call it when deciding whether a member may access an agent through the normal portal audience rules.


##### `WebAudience.allows_chat`  (lines 80–81)

```
def allows_chat(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether this member can chat with a given agent, including agents reachable only through member-private extension conversations. This is broader than ordinary visibility.

**Data flow**: It receives an agent ID. It checks that ID against `self.chat_agents`, which combines normal agents and private-conversation agents, then returns true if the agent is present.

**Call relations**: This method relies on the `chat_agents` property in the same class. It is meant for chat access decisions where a private extension conversation may allow interaction even if the agent is not part of the normal explicitly granted audience.


##### `WebAudience.granted`  (lines 83–84)

```
def granted(self, agent_id: UUID) -> bool
```

**Purpose**: Answers whether access to an agent counts as explicitly granted, with admins treated as granted for everything. This is used for stricter checks where merely owning or having a private conversation is not enough.

**Data flow**: It receives an agent ID. If the audience belongs to an admin, it immediately returns true; otherwise it checks whether the ID is in `self.granted_ids`. The result is a yes-or-no answer.

**Call relations**: This method is part of the audience object built by `web_audience`. It separates deliberate admin grants from looser chat reachability, which matters for sensitive portal panels.


##### `WebAudience.chat_agents`  (lines 87–88)

```
def chat_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Builds the full list of agents this member can chat with. It combines the normal web audience with agents available because of private extension conversations.

**Data flow**: It reads `self.agents` and `self.conversation_agents`. It returns a new tuple containing both groups, in that order, without changing the stored audience object.

**Call relations**: The `allows_chat` method uses this property when checking a single agent. The property reflects the split made by `web_audience` between normal portal agents and private-conversation-only agents.


##### `web_audience`  (lines 91–129)

```
async def web_audience(surface: SurfaceContext, extension: ExtensionContext, email: str) -> WebAudience
```

**Purpose**: Builds the complete web audience for one signed-in email address. This is the main decision point for what the portal should show and allow for that member.

**Data flow**: It receives a surface context, the web extension context, and an email. It normalizes the email, reads the workspace seat snapshot inside a transaction, checks whether the email belongs to an admin or known member, and asks the surface for all agents. If the member is an admin, it returns an audience containing every agent. Otherwise, it reads explicit grants for that email, asks which agents are tied to the member’s private extension conversations, and returns an audience containing the main agent, explicitly granted agents, member-owned agents, and separate private-conversation chat agents.

**Call relations**: This function pulls together several sources of truth: workspace membership from `Seats`, the agent list from the surface, explicit web grants from `_granted_agent_ids`, and private extension conversation ownership from the surface. It packages the result as a `WebAudience` so portal routes can ask simple questions instead of repeating the full decision process.

*Call graph*: calls 4 internal fn (transaction, list_agents, member_extension_agent_ids, _granted_agent_ids); 2 external calls (__init__, __init__).


##### `_refusal`  (lines 140–141)

```
def _refusal(text: str) -> ToolResult
```

**Purpose**: Creates a standard error result for a web access tool when the requested action is not allowed or cannot be completed. It keeps refusals consistent and readable for the user.

**Data flow**: It receives a message string. It wraps that message in text content, marks the tool result as an error, and returns it. It does not change storage or permissions.

**Call relations**: `_gate` uses this helper for failed permission and input checks, and `_read_private_transcript` uses it for failed transcript-opening checks. It hands back a `ToolResult` in the same shape that the tools return on success.

*Call graph*: called by 2 (_gate, _read_private_transcript); 2 external calls (__init__, __init__).


##### `_gate`  (lines 144–162)

```
async def _gate(ctx: ToolContext, extension: ExtensionContext, args: WebAccessInput) -> ToolResult | None
```

**Purpose**: Performs the shared safety checks before granting or revoking web access. It makes sure only a real speaking workspace admin can change access, and only for an email that belongs to an existing workspace member.

**Data flow**: It receives the tool context, the extension context, and the requested email input. It checks that there is a speaking member, asks whether that member is an admin, checks that the email is not blank, then reads the workspace seat snapshot inside a transaction to confirm the email belongs to a member. If anything fails, it returns an error tool result; if all checks pass, it returns `None`.

**Call relations**: Both `_grant` and `_revoke` call this before touching the store. `_gate` uses `_refusal` to produce user-facing error messages and reads membership through `Seats`, so the write functions can stay focused on adding or removing the grant.

*Call graph*: calls 3 internal fn (transaction, speaker_is_admin, _refusal); called by 2 (_grant, _revoke); 1 external calls (__init__).


##### `_grant`  (lines 165–187)

```
async def _grant(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that gives one workspace member web access to the current agent. It writes the grant row that later lets the portal include that agent for the member’s email.

**Data flow**: It receives the tool context and an input object containing the target email and user-facing description. It first requires an extension context, then runs `_gate`. If the request is refused, it returns that refusal. If the current agent is the main agent, it reports that no grant is needed because everyone can already reach it. Otherwise it writes a grant key for the current agent and email, records who granted it, and returns a success message.

**Call relations**: This function is registered as the handler for the `grant_web_access` tool. It relies on `_gate` for admin and membership checks, `_grant_key` for the exact store location, and the tool context for the current agent and speaker.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_revoke`  (lines 190–212)

```
async def _revoke(ctx: ToolContext, args: WebAccessInput) -> ToolResult
```

**Purpose**: Implements the admin tool that removes one member’s explicit web access to the current agent. It deletes the grant row so that future audience calculations no longer include that agent because of that email grant.

**Data flow**: It receives the tool context and an input object containing the target email and user-facing description. It requires an extension context, runs `_gate`, and stops if the request is refused. Then it deletes the stored grant key for the current agent and email. If the current agent is the main agent, it explains that the member still reaches it because the main agent is open to all members; otherwise it returns a normal revocation message.

**Call relations**: This function is registered as the handler for the `revoke_web_access` tool. Like `_grant`, it uses `_gate` for the shared checks and `_grant_key` so deletion targets the same row that granting would have written.

*Call graph*: calls 3 internal fn (agent_is_main, _gate, _grant_key); 2 external calls (__init__, __init__).


##### `_read_private_transcript`  (lines 226–253)

```
async def _read_private_transcript(ctx: ToolContext, args: PrivateTranscriptInput) -> ToolResult
```

**Purpose**: Records an admin’s acknowledgement before opening another member’s private conversation transcript in the web portal. It does not read the transcript itself; it writes the audit-style permission record that lets the portal open it.

**Data flow**: It receives the tool context and an input object containing the conversation ID and user-facing description. It checks that there is a speaking member and that the speaker is an admin. It then asks `record_transcript_access` to record that this admin opened this private conversation for the current agent. If there is nothing valid to acknowledge, it returns an error message. If the record is made, it returns a message naming whose private conversation was opened and saying that the access was recorded.

**Call relations**: This function is registered as the handler for the `read_private_transcript` tool. It uses `_refusal` for blocked cases and delegates the actual recording decision to `record_transcript_access`, which enforces whether the conversation ID belongs to a private transcript that can be acknowledged for this agent.

*Call graph*: calls 2 internal fn (speaker_is_admin, _refusal); 3 external calls (__init__, __init__, record_transcript_access).


### `extensions/web/ufo_ext_web/community.py`

`io_transport` · `request handling`

The web app has a Community skills area, but the skill data lives on an outside website, skills.sh. This file is the small gateway between the app and that website. It knows how to ask for the popular skills list, how to search by a user’s query, and how to fetch the full SKILL.md document for one skill when the user chooses it.

The file is careful because outside services are not always friendly or predictable. It sets time limits, refuses oversized responses, turns rate-limit errors into a readable message, and checks that returned data has the shape the app expects. If the directory gives a bad answer, the app reports a clear failure instead of pretending there are no skills.

It also caches results. A cache is like keeping a recently used menu on the counter instead of walking back to the restaurant every time. Listings are kept for 15 minutes, and fetched skill documents are kept for the life of the process. This reduces calls to skills.sh, which matters because some useful endpoints are rate limited.

The main public object is `COMMUNITY`, an instance of `CommunitySkills`. Routes elsewhere can ask it for a listing or for a single skill document without knowing the details of the skills.sh API.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: Turns an HTTP failure code from the skill directory into a `CommunityUnavailable` error with a message a user can understand. It gives a special explanation for rate limiting, because that is a common and recoverable failure.

**Data flow**: It receives a numeric response code from skills.sh. If the code is 429, meaning too many requests, it creates an error explaining the hourly limit; otherwise it creates an error saying which code the directory returned. The output is an exception object that callers raise.

**Call relations**: The lower-level network readers call this when skills.sh does not answer successfully. `_body` uses it for streamed downloads, and `_search` uses it for search requests, so both paths report failures in the same plain way.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: Returns the list of community skills to show in the Community tab. With an empty query it returns the popular leaderboard; with a query it returns search results.

**Data flow**: It receives the user’s search text. First it checks the in-memory listing cache; if the same query was fetched recently, it returns that saved list. Otherwise it opens an HTTP client, asks either the search endpoint or the popular leaderboard, trims the result to the display limit, stores it in the cache, and returns the list of `CommunitySkill` items.

**Call relations**: This is the main listing method other web code calls when a user opens or filters Community skills. It delegates network setup to `_client`, chooses `_popular` or `_search` depending on whether there is a query, and uses the cache to avoid repeated directory reads.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: Fetches and parses the full document for one community skill, usually when a user opens the install/review view. It returns enough detail to show the skill’s name, description, instructions, and original document.

**Data flow**: It receives a source repository like `owner/repo` and a skill name. It builds a cache key and returns a saved document if one is already known. If not, it downloads the skill package metadata, finds the `SKILL.md` file inside the returned file list, parses that markdown file, saves the parsed result or a missing result in the document cache, and returns it.

**Call relations**: This is the main single-skill read used after a user selects a listed skill. It uses `_client` to make the HTTP client, `_body` to safely download the directory response, and `_parse` to turn the skill markdown into a structured `CommunityDocument`. If the downloaded JSON cannot be read, it raises `CommunityUnavailable` so the route can show a clear error.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: Creates the HTTP client used to talk to skills.sh. It centralizes timeout, redirect-following, and optional test transport setup.

**Data flow**: It receives a timeout length in seconds. It returns an `httpx.AsyncClient` configured with that timeout, the optional injected transport, and redirect following turned on. It does not make a request by itself.

**Call relations**: `listing` and `fetch` call this before doing network work. In production it creates a normal client; in tests, the injected transport can replace real network calls with predictable fake responses.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: Reads the skills.sh public page payload and extracts the popular skills leaderboard. This is used when the user has not typed a search query.

**Data flow**: It receives an HTTP client. It downloads the leaderboard page with a special header, scans the response text for small JSON-looking skill entries, decodes each entry, converts valid entries into `CommunitySkill` objects, removes duplicates by source and name, sorts them by install count from highest to lowest, and returns the sorted list. If no valid listing is found, it raises a clear unavailable error.

**Call relations**: `listing` calls this for the default Community view. `_popular` relies on `_body` for safe downloading and `_entry` for validating each possible skill entry before it is shown to users.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: Calls the public skills.sh search endpoint for a user’s query. It returns only valid skill rows, sorted by install count.

**Data flow**: It receives an HTTP client and the query string. It sends the query and result limit to the search API, rejects non-success responses through `_refusal`, reads the returned JSON, converts each listed entry through `_entry`, drops invalid entries, sorts the rest by install count, and returns them.

**Call relations**: `listing` calls this whenever the user has entered a search term. It uses `_entry` so search results follow the same validation rules as the popular listing, and `_refusal` so search failures are reported like other directory failures.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: Turns one raw skill record from skills.sh into the app’s simple `CommunitySkill` shape. It filters out records that are missing a name or have an unsafe-looking source repository.

**Data flow**: It receives an unknown object from decoded JSON. If the object is not a dictionary, or if it lacks a usable skill name and `owner/repo` source, it returns `None`. Otherwise it creates and returns a `CommunitySkill` with the name, source, and install count.

**Call relations**: Both `_popular` and `_search` send raw directory entries here before showing them to users. This keeps the two listing paths consistent and prevents malformed directory data from leaking into the UI.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: Downloads a response body from skills.sh safely. It enforces successful status codes and size limits so the app does not read a huge or failed response as if it were normal data.

**Data flow**: It receives an HTTP client, a URL, a maximum byte count, and optional headers. It streams the response in chunks, checks that the status code is successful, counts how many bytes have arrived, stops with a clear error if the response is too large, and finally returns the combined bytes.

**Call relations**: `_popular` uses this to read the leaderboard page, and `fetch` uses it to read a skill download response. It calls `_refusal` for bad HTTP status codes and raises `CommunityUnavailable` itself when the body is larger than this app accepts.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: Reads a skill’s `SKILL.md` markdown document and extracts the structured information the app needs. It expects the document to start with YAML front matter, which is a small metadata block at the top of the file.

**Data flow**: It receives the raw markdown text. It looks for a front matter block, safely parses that metadata, checks for a name and description, then returns a `CommunityDocument` containing the name, description, remaining instructions text, and original document. If the document is missing the expected metadata or the metadata cannot be read, it returns `None`.

**Call relations**: `fetch` calls this after finding the `SKILL.md` file in the downloaded skill data. This keeps document interpretation separate from network fetching, so `fetch` can focus on finding the file and caching the result.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).


### Panel mutation bridge
The panel bridge turns settings-panel form submissions into the system’s standard chat-based action flow.

### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The web portal has many buttons and forms: save an agent, add a team member, connect an account, request a credential, grant web access, and so on. This file defines exactly what those forms are allowed to ask for, checks that each request makes sense, turns it into a tool request the engine already understands, and waits for the final result so the browser gets a clear answer.

The important idea is that panel changes do not bypass the normal conversation system. Instead, each submitted form becomes a prepared “intent,” meaning a structured request to run a named tool with exact input. That intent is admitted as its own turn in a durable intent conversation for that member and agent. This is like putting every office form into the same official inbox instead of letting people edit the filing cabinet directly. The turn becomes the audit trail: who asked, what they asked, and what happened.

The file also protects sensitive flows. A credential value is never sent through the panel itself; the panel can only request a private credential prompt. Account connections are started through the same connection handoff used by chat. Agent settings are checked against the models and sandbox options this deployment actually supports. Finally, the file exposes the settings projection used by the portal to render an agent’s current settings and form schema.

#### Function details

##### `ApplyIntent.kinds`  (lines 66–70)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the full set of object kinds that a panel is allowed to submit changes for. This keeps the user interface and the server’s accepted inputs tied to the same source of truth.

**Data flow**: It reads the allowed values from the `kind` field on `ApplyIntent` → turns those fixed allowed values into a frozen set → returns that set for other code to compare against or display from.

**Call relations**: This is the base helper for the more specific kind lists. Other code can ask it what object kinds the panel lane recognizes, instead of copying the list by hand and risking drift.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 73–75)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that can be created or updated through an `apply` intent. It excludes kinds that are only deleted or only connected.

**Data flow**: It starts with all allowed panel kinds → removes credential-like and trigger-like kinds that cannot be applied, plus connection kinds that must use `connect` → returns the remaining kinds.

**Call relations**: The web surface uses this when building object-page controls. It helps the page show an apply/create control only where the intent lane will actually accept one.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 78–83)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that can be deleted from the panel. Connection kinds are excluded because they use a connection flow rather than deletion from this route.

**Data flow**: It starts with all allowed panel kinds → removes the kinds reserved for connection-only behavior → returns the kinds for which a delete action may be offered.

**Call relations**: The web surface uses this to decide where to show delete controls. Because it uses the same rules as validation, the page is less likely to offer a button that the server will reject.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 86–105)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: Checks that a submitted object change uses a verb that is valid for that object kind. For example, credentials can only be deleted here, while connections must use `connect`.

**Data flow**: It receives a parsed `ApplyIntent` object → checks combinations such as verb, kind, spec, and `create_only` → either returns the same object as valid or raises a validation error explaining what is wrong.

**Call relations**: This runs automatically during Pydantic validation, before the intent is turned into a tool call. It is the gate that stops the panel route from becoming a general-purpose object editing endpoint.


##### `_tool_intent`  (lines 177–273)

```
def _tool_intent(submitted: ApplyIntent | AddMemberIntent | AudienceIntent | CorrectionIntent | CredentialIntent | TranscriptIntent, slot: CredentialSlotView | None) -> ToolIntent
```

**Purpose**: Converts a validated panel submission into the exact tool request that the engine knows how to run. It is the translation step from “form action” to “system action.”

**Data flow**: It takes one submitted intent, and sometimes credential slot details → chooses the right tool name and builds the tool input → returns a `ToolIntent` object. For object applies, it also formats the object kind, name, and spec into a YAML manifest, which is a readable structured text format.

**Call relations**: It is called by `submit_intent` after input validation and any extra lookups. Its output is passed into the conversation admission step so the engine can run the same tools that chat would run, without asking a model to reinterpret the request.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 276–292)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Turns the terminal result of an admitted intent turn into the JSON response sent back to the browser. It gives the portal a simple success or failure shape.

**Data flow**: It receives the final frame from the turn and the turn id → checks whether the turn finished successfully, requested credentials, or failed → returns a JSON response containing whether it was applied, a message, and the turn id.

**Call relations**: It is called by `submit_intent` when the intent conversation produces a terminal frame. This keeps response formatting separate from the longer request flow.

*Call graph*: called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `submit_intent`  (lines 295–412)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: Accepts one panel form submission, validates it, submits it as a conversation turn, waits for the result, and returns that result to the web client. This is the main write path for portal panel actions.

**Data flow**: It reads the request body → rejects bodies that are too large or malformed → validates the submitted intent → performs special checks such as known agent model names, available sandbox sizes, or existing credential slots → converts the submission into a tool intent → finds or creates the member’s intent conversation for the selected agent → admits the intent as a turn → watches the turn until it finishes, parks, or times out → returns a JSON response describing the outcome.

**Call relations**: This is the central coordinator in the file. It calls `_tool_intent` to build the tool request, uses the surface context to look up agents, credential slots, conversations, and turn frames, and calls `_outcome` when the engine reports a final result. It deliberately routes writes through the conversation system so panel changes are ordered, auditable, and consistent with chat actions.

*Call graph*: calls 7 internal fn (admit, agent_detail, conversation_for, list_credential_slots, tail, _outcome, _tool_intent); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `agent_create_schema`  (lines 415–427)

```
def agent_create_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the form schema used when creating a new agent from the portal. A schema is a machine-readable description of the fields a form should show and require.

**Data flow**: It starts from the `AgentSpec` JSON schema → removes fields that should not be edited in the create form, such as raw input/output schemas and sometimes sandbox size → marks `prompt` as required → returns the adjusted schema dictionary.

**Call relations**: The portal can use this returned schema to render a create-agent form that matches what the backend expects. It avoids maintaining a separate hand-written form description.

*Call graph*: 1 external calls (model_json_schema).


##### `_update_schema`  (lines 430–440)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the form schema used when editing an existing agent’s settings. It leaves out the prompt because the settings page shows the prompt in its own dedicated text area.

**Data flow**: It starts from the `AgentSpec` JSON schema → removes fields not meant for the settings form, including input/output schemas, prompt, and sandbox size when this deployment has no sandbox size choices → returns the trimmed schema.

**Call relations**: It is called by `agent_settings` when preparing the settings response. That lets the settings page render editable fields from the same schema the agent spec uses.

*Call graph*: called by 1 (agent_settings); 1 external calls (model_json_schema).


##### `agent_settings`  (lines 443–481)

```
async def agent_settings(ctx: SurfaceContext, agent_id: UUID, *, admin: bool) -> Response
```

**Purpose**: Returns the current settings view for one agent, including its prompt, selected model, deployment capabilities, editable schema, and optionally web audience information for admins.

**Data flow**: It receives the surface context, agent id, and whether the requester is an admin → loads the agent detail → returns a 404 response if the agent does not exist → if the requester is an admin, loads the granted web audience emails → builds a JSON response with agent details, deployment limits, available model ids, current editable spec values, the settings schema, and audience data.

**Call relations**: This is the read-side partner to `submit_intent`. While `submit_intent` sends changes through the conversation lane, `agent_settings` projects the current state needed to display the settings page. It calls `_update_schema` so the page knows which fields to render.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).
