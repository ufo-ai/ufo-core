# Member chat surfaces  `stage-4.1`

This stage is the set of “front doors” where members talk to UFO. It sits in the main work loop: a person sends a message or clicks a form, the surface turns that channel-specific event into a common conversation turn for the agent system, then sends the agent’s answer back to the same place.

The web surface runs the browser portal. It signs people in, serves chat, streams live replies, and provides read-only pages for conversations, artifacts, settings, and admin views. The community file lets that portal browse the public skills.sh skill directory and read a selected skill’s SKILL.md. The panels file makes web forms active by converting submitted settings or actions into the same tool-style actions chat can run.

The iMessage surface receives texts and attachments, links accounts, avoids duplicate messages, and restores streams after reconnects. The Slack surface verifies Slack calls, converts messages and button/form clicks into turns, and posts replies, status, and files into the right thread. The command-line surface does the same for the ufo terminal chat screen, translating progress, files, prompts, and actions into plain text instructions.

## Files in this stage

### Web portal surface
Browser-facing modules authenticate members, serve chat and panels, expose community skills, and convert web form submissions into agent actions.

### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling`

Think of this file as the front desk for the web version of UFO. It checks who is at the door, decides which workspace and agents they are allowed to see, serves the portal page and its built JavaScript/CSS files, then answers all of the browser's API calls. A member signs in by posting a bearer token, which is a signed proof of their workspace and email; the file stores that token in a cookie and re-checks it on later requests. Chat is the most important flow: the browser sends text and optional files, this file saves attachments into the conversation workspace, opens or continues a conversation, and admits the message to the shared turn queue. The live answer is not polled; it is streamed back using SSE, server-sent events, which are one-way browser-friendly live updates. The file also turns raw stored transcripts into friendly chat bubbles, hiding internal prompt wrapping and showing tool work, spawned subagents, shared files, questions, and created apps in the right place. Around chat it provides panels for agents, conversations, skills, connections, memory, artifacts, usage, scheduled runs, object kinds, settings, homepages, and admin status. Most functions are careful gates: they return “not found” rather than leaking that a private thing exists.

#### Function details

##### `load_assets`  (lines 210–220)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Loads the already-built frontend files that this server is willing to serve. It only includes file types with known web media types, so accidental build leftovers are not published.

**Data flow**: It receives a directory path, scans the files in it, reads allowed files into memory, and returns a map from request name to file bytes and media type.

**Call relations**: It runs during module setup to build the static asset table used later by static file routes.

*Call graph*: 1 external calls (glob).


##### `resolve_workspace`  (lines 233–272)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace a web request belongs to before the request reaches a route handler. It reads the signed session cookie, or the posted login token for the one session-opening POST.

**Data flow**: It reads cookies, method, headers, form data, and sometimes the chat target query parameter. It returns a workspace id, a redirect to sign in, an error response, or nothing when the request is not admitted.

**Call relations**: The shared surface framework calls this first. It uses body-size and form helpers before later handlers separately authenticate the member email.

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 275–279)

```
def _chat_target(request: Request) -> UUID | None
```

**Purpose**: Extracts an optional conversation id from the login redirect query string. This lets a member land back on a specific chat after signing in.

**Data flow**: It reads the query parameter, tries to parse it as a UUID, and returns the UUID or nothing.

**Call relations**: It is used only by resolve_workspace when building a safe sign-in redirect.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 282–292)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Looks up a built portal asset from the in-memory asset table. It refuses unknown names instead of reading arbitrary files from disk.

**Data flow**: It reads the request path, finds the matching asset bytes and type if present, and returns a cache-aware response or nothing.

**Call relations**: static_asset tries this first, then falls back to the shared asset store for mixed-version deployments.

*Call graph*: calls 1 internal fn (_asset_response); called by 1 (static_asset).


##### `_asset_response`  (lines 295–299)

```
def _asset_response(request: Request, body: bytes, media_type: str, etag: str) -> Response
```

**Purpose**: Builds an HTTP response for one static asset with an ETag, a content fingerprint used for browser revalidation. It can answer 304 Not Modified when the browser already has the current bytes.

**Data flow**: It receives request headers, asset bytes, media type, and ETag. It returns either an empty 304 response or the asset body.

**Call relations**: Both local and stored asset serving use this helper so caching behaves the same.

*Call graph*: called by 2 (_static_response, _stored_asset); 1 external calls (Response).


##### `_publish_assets`  (lines 310–314)

```
async def _publish_assets(blob: BlobStore) -> None
```

**Purpose**: Copies this process's built frontend assets into the shared blob store. This helps old and new server pods serve each other's hashed asset files during a rollout.

**Data flow**: It reads the in-memory asset table, checks whether each blob key already exists, and writes missing bytes to shared storage.

**Call relations**: _assets_published starts this work before the portal page is served.

*Call graph*: calls 2 internal fn (exists, put); called by 1 (_assets_published).


##### `_assets_published`  (lines 317–329)

```
def _assets_published(blob: BlobStore) -> 'asyncio.Task[None]'
```

**Purpose**: Ensures asset publishing is started once per process and retried if it failed. It prevents the page from pointing at files that have not been made available yet.

**Data flow**: It reads a module-level task, creates a new async task when needed, stores it, and returns the task to await.

**Call relations**: portal_page waits on this before returning the browser shell.

*Call graph*: calls 1 internal fn (_publish_assets); called by 1 (portal_page); 1 external calls (create_task).


##### `_stored_asset`  (lines 332–354)

```
async def _stored_asset(blob: BlobStore, request: Request) -> Response
```

**Purpose**: Serves a static asset from shared storage when this server process does not have that build locally. This makes rolling upgrades safer.

**Data flow**: It validates the requested asset name and suffix, checks an in-process cache, reads bytes from the blob store if needed, caches them with an ETag, and returns the asset or 404.

**Call relations**: static_asset calls it after local lookup fails; it reuses _asset_response for the final HTTP response.

*Call graph*: calls 3 internal fn (exists, get, _asset_response); called by 1 (static_asset); 3 external calls (sha256, Path, Response).


##### `portal_page`  (lines 357–373)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main HTML document for the web portal. It also fails clearly if the frontend build has not been produced.

**Data flow**: It checks that the built HTML exists, waits for assets to be published, and returns the shell with no-store caching.

**Call relations**: This is the GET route for the portal root after workspace resolution has already allowed the request.

*Call graph*: calls 1 internal fn (_assets_published); 1 external calls (HTMLResponse).


##### `_authenticate`  (lines 376–394)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

**Purpose**: Turns a session cookie into the current member id and email. If the token is missing, expired, forged, or not linked to a member, it returns a clear 401 response.

**Data flow**: It reads the cookie, verifies it against the current workspace, links or creates the member row for the email, and returns member id plus email or an error response.

**Call relations**: _audience_for uses it for most routes, and fulfill_credential uses it directly for the private credential form.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (_audience_for, fulfill_credential); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 397–403)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves JavaScript, CSS, fonts, and other portal assets to an authenticated session. It first uses local built assets and then the shared store fallback.

**Data flow**: It takes the request path and returns a static file response or a not-found response from the fallback.

**Call relations**: This is the GET static route; it delegates all lookup details to _static_response and _stored_asset.

*Call graph*: calls 2 internal fn (_static_response, _stored_asset).


##### `open_session`  (lines 406–431)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts a posted bearer token and stores it as the browser session cookie. The token is sent in the form body, not in the URL, to avoid leaking it through browser history or logs.

**Data flow**: It checks form size, parses the form, validates the token shape, writes the cookie on a redirect response, and sends the browser back into the portal.

**Call relations**: This is the only POST that opens a web session; later routes verify the cookie again.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 434–438)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Parses the agent id from a route path. It returns nothing for malformed ids so callers can answer not found.

**Data flow**: It reads the path parameter, tries to convert it to a UUID, and returns the UUID or nothing.

**Call relations**: Chat, transcript, panel, and member-chat gates use it before checking audience permissions.

*Call graph*: called by 4 (_member_chat_page, _panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 441–442)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the private store key for this web surface's record of a conversation. That record binds a conversation to an agent and member email.

**Data flow**: It receives a conversation id and returns a stable string key under the chat prefix.

**Call relations**: Conversation creation, lookup, and rail listing all use this same key shape.

*Call graph*: called by 3 (_open_conversation, _own_web_chat, chats_index).


##### `_chat_title`  (lines 451–469)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates a short conversation title from the first message or, for file-only messages, from attached filenames. It trims at sensible word boundaries for the chat rail.

**Data flow**: It receives message text and attachment paths, collapses whitespace, falls back to filenames, shortens long text, and returns a label.

**Call relations**: _open_conversation uses it immediately, and summarize_chat_titles uses it to clean model-written titles.

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_title_excerpt`  (lines 481–499)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

**Purpose**: Builds the short opening excerpt used to ask a model for a better chat title. It uses the first user text and first assistant text when both exist.

**Data flow**: It receives transcript messages, extracts visible text, strips internal user context, limits length, and returns joined excerpt text or empty text.

**Call relations**: summarize_chat_titles calls it before requesting a model-generated title.

*Call graph*: calls 1 internal fn (_rendered_text); called by 1 (summarize_chat_titles); 1 external calls (member_message_text).


##### `summarize_chat_titles`  (lines 502–548)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

**Purpose**: Background job that names conversations from their opening exchange. It improves rail titles after the first agent reply exists.

**Data flow**: It asks core for untitled conversations, reads their transcripts, builds excerpts, asks the configured model for short titles when possible, cleans them, and records each conversation as processed.

**Call relations**: It is scheduled outside request handling and uses _title_excerpt and _chat_title as its title-preparation steps.

*Call graph*: calls 4 internal fn (conversations_awaiting_title, summarized_conversation_title, _chat_title, _title_excerpt); 2 external calls (__init__, __init__).


##### `seed_homepages`  (lines 551–611)

```
async def seed_homepages(ctx: ExtensionContext, bucket: str | None=None) -> None
```

**Purpose**: Background job that asks each agent to build its first homepage once. It gives new workspaces a useful Home tab without a human manually prompting every agent.

**Data flow**: It lists agents, skips already marked ones or ones missing required tools, chooses an acting member, opens a conversation, invokes a scheduled turn, checks cancellation, and writes a marker.

**Call relations**: It runs as a scheduled extension task and uses core conversation and invocation APIs rather than web request routes.

*Call graph*: calls 5 internal fn (earliest_seated_admin, invoke, open_conversation, turn_outcomes, workspace_agents); 1 external calls (now).


##### `_open_conversation`  (lines 614–647)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and stores the web-specific ownership record. It writes the record first so the conversation can later be gated back to the same member and agent.

**Data flow**: It receives agent, member, email, queue key, text, and paths. It mints an id, stores a ChatRecord, asks core for the conversation, cleans up if another id won, titles the conversation, and returns id plus title.

**Call relations**: chat calls it when the browser sends the first message for a new conversation.

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_web_chat); called by 1 (chat); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 650–657)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

**Purpose**: Reads the current title of one conversation as core lists it. This keeps responses aligned with the same title shown elsewhere.

**Data flow**: It receives agent, member, and conversation ids, asks core for that one listing row, and returns its title or an empty string.

**Call relations**: Both chat continuation and _open_conversation use it when they need the canonical title.

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 2 (_open_conversation, chat).


##### `_own_web_chat`  (lines 660–672)

```
async def _own_web_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is this member's own web chat with this agent. Anything else is treated as absent.

**Data flow**: It reads the stored chat row, validates it, compares agent id and email, and returns the ChatRecord or nothing.

**Call relations**: _member_chat builds on it, and _open_conversation uses it to verify a raced conversation already has a web row.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 2 (_member_chat, _open_conversation).


##### `_member_chat`  (lines 675–696)

```
async def _member_chat(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, conversation_id: UUID) -> ListedConversation | None
```

**Purpose**: Decides whether a conversation can be continued or read as this member's chat. It also allows certain member-private extension conversations that behave like chat.

**Data flow**: It checks the web chat row, reads the core listing for the member, and returns the listed conversation only if the conversation matches allowed chat shapes.

**Call relations**: Chat, transcript, permalink resolution, stream authorization, and member-chat page gates all depend on this check.

*Call graph*: calls 2 internal fn (list_agent_conversations, _own_web_chat); called by 5 (_member_chat_page, _member_turn, _resolve_chat, chat, transcript); 1 external calls (conversation_audience).


##### `_turn_context`  (lines 699–710)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

**Purpose**: Builds the extra context attached to a member's admitted message, including sender email, browser timezone, and source. Bad timezone names are dropped instead of rejecting the message.

**Data flow**: It reads a timezone header and the supplied email/source, validates the timezone through TurnContext construction, logs invalid values, and returns a TurnContext.

**Call relations**: chat passes this context into core admission for each normal message.

*Call graph*: called by 1 (chat); 2 external calls (__init__, log).


##### `_chat_source`  (lines 713–721)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Creates the human-readable source string for a web chat message. When a public portal URL exists, it includes a link back to the conversation.

**Data flow**: It receives public base URL, conversation id, and email, and returns either a portal conversation URL plus email or a plain web/email label.

**Call relations**: chat uses it inside _turn_context so created work can say where the request came from.

*Call graph*: called by 1 (chat).


##### `_audience_for`  (lines 724–731)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Combines authentication with web audience lookup. The audience says which agents and admin features this email may use.

**Data flow**: It authenticates the request, then asks the web audience layer for permissions, and returns member id, email, and audience or an error response.

**Call relations**: Most route handlers start here, either directly or through narrower gates like _panel_gate.

*Call graph*: calls 1 internal fn (_authenticate); called by 20 (_member_chat_page, _member_turn, _object_gate, _panel_gate, admin_index, agents_index, chat, chats_index, connection_pool, github_coverage (+10 more)); 2 external calls (web_audience, web_extension).


##### `agents_index`  (lines 734–767)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the signed-in member and the agents visible to them in the web portal. Admins also see web-audience grant lists.

**Data flow**: It authenticates and resolves audience, optionally reads grant emails, converts visible agents to JSON-friendly dictionaries, and returns them.

**Call relations**: This is the portal's first API read after loading; the frontend uses it to build the agent list and member state.

*Call graph*: calls 1 internal fn (_audience_for); 3 external calls (JSONResponse, granted_emails, web_extension).


##### `_framed_length`  (lines 770–783)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Rejects form requests that cannot be safely size-bounded before parsing. This prevents whole-body parsers from accepting unbounded or oversized uploads.

**Data flow**: It reads transfer and content-length headers, checks for chunked or missing length, compares against a limit, and returns an error response or nothing.

**Call relations**: Login, credential fulfillment, preview, multipart chat parsing, and workspace resolution use it before parsing forms.

*Call graph*: called by 5 (_parse_inbound, fulfill_credential, open_session, preview, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 786–793)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses a request form and turns malformed parser failures into a normal 400 response. This keeps bad client bodies from escaping as server errors.

**Data flow**: It awaits request.form(), catches form parser errors, and returns either form data or a bad-request response.

**Call relations**: All form-reading routes share this helper after their size checks.

*Call graph*: called by 5 (_parse_inbound, fulfill_credential, open_session, preview, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 796–804)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a non-form request body under a hard byte limit. The limit is enforced on bytes actually received, not just on headers.

**Data flow**: It streams chunks from the request, appends them until complete, and stops with 413 if the limit is exceeded.

**Call relations**: _parse_inbound uses it for plain text chat messages.

*Call graph*: called by 1 (_parse_inbound); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 807–843)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...]] | Response
```

**Purpose**: Reads the member's chat message and optional uploaded files. It accepts plain UTF-8 text or multipart form data, while refusing unsupported or unsafe shapes.

**Data flow**: It checks content type, reads plain bodies with a hard cap or multipart forms with declared length, extracts the message field and file uploads, and returns text plus upload objects or an error response.

**Call relations**: chat calls it before deciding whether to open, continue, answer, or stop a conversation.

*Call graph*: calls 3 internal fn (_bounded_body, _form, _framed_length); called by 1 (chat); 1 external calls (Response).


##### `_inbox_paths`  (lines 846–850)

```
def _inbox_paths(uploads: tuple[UploadFile, ...]) -> tuple[str, ...]
```

**Purpose**: Assigns safe workspace paths for uploaded chat files. Each file is placed directly under the web inbox directory with collision-safe names.

**Data flow**: It receives upload objects, tracks used names, sanitizes each filename through inbox_name, and returns workspace paths.

**Call relations**: chat uses these paths before saving uploads and adding the attachment note to the message.

*Call graph*: called by 1 (chat); 1 external calls (inbox_name).


##### `_deliver_uploads`  (lines 853–862)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], paths: tuple[str, ...]) -> None
```

**Purpose**: Saves chat attachments into the conversation workspace before the agent turn runs. This lets the agent see the files at the paths mentioned in the message.

**Data flow**: It receives uploads and target paths, streams each upload in chunks, and writes each file through the surface context.

**Call relations**: chat calls it after the conversation is authorized and before admitting the message.

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (chat).


##### `_files_note`  (lines 865–868)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Adds a hidden-in-practice note to the admitted message naming saved attachment paths. The agent can read the paths, while the UI later turns the note back into file cards.

**Data flow**: It receives text and paths, formats a note, and returns either text plus note or just the note for file-only messages.

**Call relations**: chat uses it before admission; _member_attachments later reverses it for display.

*Call graph*: called by 1 (chat).


##### `_member_attachments`  (lines 876–884)

```
def _member_attachments(said: str) -> tuple[str, tuple[str, ...]]
```

**Purpose**: Separates a displayed member message from the attachment note appended at admission time. This keeps the chat bubble from showing the internal saved-path sentence.

**Data flow**: It receives the admitted user text, searches for the file note at the end, and returns clean words plus extracted paths.

**Call relations**: _member_bubble uses it whenever transcript messages are rendered.

*Call graph*: called by 1 (_member_bubble).


##### `_attachment_preview`  (lines 887–895)

```
def _attachment_preview(agent_id: UUID, conversation_id: UUID, path: str) -> str | None
```

**Purpose**: Builds a same-origin preview URL for an attached image file. Non-image files get no preview URL and are shown as cards instead.

**Data flow**: It receives agent id, conversation id, and file path, checks whether the path looks like a raster image, quotes the path, and returns a route URL or nothing.

**Call relations**: It is passed as the attachment renderer into transcript rendering helpers.

*Call graph*: 2 external calls (raster_image_media_type, quote).


##### `_attachment_payload`  (lines 898–911)

```
def _attachment_payload(path: str, preview_url: str | None) -> dict[str, object]
```

**Purpose**: Builds the JSON description for one member-attached file in a chat bubble. It includes filename, media type, and optional preview link.

**Data flow**: It receives a path and preview URL, infers media type from image detection or suffix fallback, and returns a dictionary for the frontend.

**Call relations**: _member_bubble calls it for each extracted attachment path.

*Call graph*: called by 1 (_member_bubble); 2 external calls (PurePosixPath, raster_image_media_type).


##### `_member_bubble`  (lines 914–922)

```
def _member_bubble(said: str, attach: Attach | None) -> dict[str, object]
```

**Purpose**: Creates one user-side chat bubble from stored message text. It restores attachments as files rather than showing their internal note.

**Data flow**: It receives stored text and an optional preview-link function, splits words from paths, builds a user bubble, and adds file payloads when present.

**Call relations**: Transcript projection and live conversation reconstruction call it whenever member words should appear.

*Call graph*: calls 2 internal fn (_attachment_payload, _member_attachments); called by 2 (_conversation_messages, _rendered_messages).


##### `_upload_chunks`  (lines 925–927)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded file in fixed-size chunks. This avoids reading large attachments into memory all at once during workspace writes.

**Data flow**: It repeatedly reads bytes from the upload object and yields each non-empty chunk.

**Call relations**: _deliver_uploads passes this async byte stream to the workspace file writer.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_key`  (lines 930–935)

```
def _answer_key(conversation_id: UUID, turn_id: UUID, index: int) -> str
```

**Purpose**: Builds the idempotency key for an answer to a specific question asked by a turn. This makes double-clicks or retries join the same admitted answer.

**Data flow**: It receives conversation id, asking turn id, and question index, and returns one stable string key.

**Call relations**: chat uses it when admitting answers, and _asks uses it later to connect answers back to question cards.

*Call graph*: called by 2 (_asks, chat).


##### `_answer_headers`  (lines 938–950)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Reads headers that mark a chat message as an answer to a question. Malformed headers are rejected before any conversation changes happen.

**Data flow**: It reads the answer-turn and question-index headers, returns none for ordinary messages, or returns parsed UUID and index, or an error response.

**Call relations**: chat calls it before admission and before generating answer idempotency keys.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 953–962)

```
def _stop_header(request: Request) -> UUID | None | Response
```

**Purpose**: Reads the header that asks to stop a running turn. It distinguishes a stop action from a normal chat message.

**Data flow**: It reads the stop-turn header, returns none when absent, parses a UUID when present, or returns a bad-request response.

**Call relations**: chat uses it before reading message effects so malformed stop requests leave nothing behind.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `chat`  (lines 965–1062)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Admits one browser chat action: a new message, an answer to a question, or a stop request. It is the write path for web conversations.

**Data flow**: It authenticates, checks agent and conversation access, parses body and headers, saves uploads, opens or finds the conversation, stops a turn when requested, or admits the message and returns turn ids and title information.

**Call relations**: This route ties together most chat helpers and hands the final message to core admission; the stream route later tails the resulting turn.

*Call graph*: calls 17 internal fn (admit, admitted_body, stop_turn, _agent_param, _answer_headers, _answer_key, _audience_for, _chat_source, _deliver_uploads, _files_note (+7 more)); 5 external calls (JSONResponse, Response, web_extension, UUID, uuid4).


##### `_rendered_text`  (lines 1065–1076)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Extracts visible text from a stored message. For user messages, it strips internal context wrappers that were useful to the agent but should not appear in the UI.

**Data flow**: It receives a Message, reads string content or text blocks, removes known context tags for user messages, trims whitespace, and returns text.

**Call relations**: Transcript rendering and title excerpt generation both use it as their first text-cleaning step.

*Call graph*: called by 2 (_rendered_messages, _title_excerpt).


##### `_tool_event`  (lines 1079–1092)

```
def _tool_event(block: ToolUseBlock) -> dict[str, str]
```

**Purpose**: Turns a tool-use block into a compact UI event. It hides the internal requested-by field before deciding whether the block is a skill load or tool call.

**Data flow**: It receives a tool block, copies it without private input, classifies it, and returns a dictionary with kind, name, and descriptive text.

**Call relations**: Transcript and subagent activity renderers use it to show the work an agent performed.

*Call graph*: called by 2 (_rendered_messages, _subagent_activity); 2 external calls (model_copy, tool_activity).


##### `_subagent_activity`  (lines 1114–1136)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

**Purpose**: Summarizes the visible work done inside a spawned subagent run. It records notes and active tool calls in order.

**Data flow**: It receives subagent transcript messages, identifies tool uses that produced activity, collects assistant notes and tool events, limits the list, and returns event dictionaries.

**Call relations**: _subagent_nodes calls it after reading each child run's transcript.

*Call graph*: calls 1 internal fn (_tool_event); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 1139–1149)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

**Purpose**: Tries to decode a run's final answer as a JSON object. Runs can answer with structured data, but plain text is allowed too.

**Data flow**: It receives answer text, returns nothing for empty or non-object JSON, or returns the decoded object.

**Call relations**: _run_answer uses it to decide whether a structured finish payload needs human-friendly rendering.

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 1152–1175)

```
def _payload_prose(value: JsonValue) -> str
```

**Purpose**: Turns structured JSON-like values into readable prose for the portal. Lists, objects, booleans, numbers, and strings are all made displayable.

**Data flow**: It receives one value, recursively renders nested lists and objects, and returns a text representation, using “none” for empty collections.

**Call relations**: _run_answer uses it when a finish payload has multiple fields or non-simple content.

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 1178–1194)

```
def _run_answer(answer: str) -> str
```

**Purpose**: Returns the human-readable answer of a subagent or profile run. It hides JSON wrapping when the payload really contains prose.

**Data flow**: It receives raw answer text, tries to decode a finish payload, returns the single prose field when appropriate, or renders all fields as readable text.

**Call relations**: Subagent nodes and run-conversation transcript rendering use it so run outputs read naturally.

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (render, _subagent_nodes).


##### `_subagent_nodes`  (lines 1197–1236)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

**Purpose**: Builds a nested tree of subagent runs spawned by a conversation. Each node carries its target, output, work events, and children.

**Data flow**: It receives turn records, identifies child turns, names agent children when needed, reads bounded child transcripts concurrently, fills activity events, nests nodes by parent id, and returns them grouped by parent.

**Call relations**: Transcript aids use it for settled transcripts, and live events use it when a terminal frame arrives.

*Call graph*: calls 4 internal fn (list_agents, read_transcript, _run_answer, _subagent_activity); called by 2 (_events, _transcript_aids); 2 external calls (__init__, gather).


##### `_rendered_messages`  (lines 1239–1386)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

**Purpose**: Converts raw transcript messages into the chat shape the browser draws. It groups assistant notes, tool activity, subagents, questions, files, apps, speakers, and user bubbles into a clean timeline.

**Data flow**: It receives messages plus many side maps, walks messages in order, strips internal context, creates user bubbles and assistant replies, attaches related cards, and returns a list of UI dictionaries.

**Call relations**: _TranscriptAids.render is the public wrapper that supplies these side maps consistently.

*Call graph*: calls 3 internal fn (_member_bubble, _rendered_text, _tool_event); called by 1 (render); 1 external calls (member_message_text).


##### `_rendered_messages.note_answer`  (lines 1309–1314)

```
def note_answer() -> None
```

**Purpose**: Moves a provisional assistant answer into the list of work notes when later tool activity shows it was not the final reply. This keeps intermediate narration visible without confusing it with the answer.

**Data flow**: It reads the current pending answer inside _rendered_messages, inserts it into pending events if under the event limit, and clears the answer text.

**Call relations**: It is an inner helper used only while _rendered_messages walks assistant messages.


##### `_rendered_messages.flush_reply`  (lines 1316–1340)

```
def flush_reply(include_subagents: bool) -> None
```

**Purpose**: Emits the current assistant reply when the renderer reaches a boundary. It attaches any pending events, subagents, questions, files, or app cards that belong to that turn.

**Data flow**: It reads accumulated answer text and side data, appends a reply dictionary if there is anything to show, and resets the reply accumulator.

**Call relations**: It is an inner helper used only by _rendered_messages when user turns or the transcript end force a reply boundary.


##### `_asks`  (lines 1405–1437)

```
def _asks(conversation_id: UUID, turns: tuple[Turn, ...], admitted: tuple[KeyedAdmission, ...]) -> _Asks
```

**Purpose**: Builds the question cards for a conversation and marks answer messages already displayed inside those cards. This avoids showing the same answer twice.

**Data flow**: It receives conversation id, turns, and keyed admissions, matches admissions to question keys, creates card dictionaries, marks old unanswered asks as closed or hidden, and returns cards plus stated message refs.

**Call relations**: _transcript_aids calls it before rendering messages with question and answer context.

*Call graph*: calls 1 internal fn (_answer_key); called by 1 (_transcript_aids); 2 external calls (__init__, member_message_text).


##### `_TranscriptAids.render`  (lines 1459–1477)

```
def render(self, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Renders messages using the side information gathered for a transcript. For profile-run conversations, it also formats assistant answers as run outputs.

**Data flow**: It receives message tuples, passes stored aids into _rendered_messages, optionally rewrites assistant text through _run_answer, and returns UI messages.

**Call relations**: _conversation_messages and _history_messages create _TranscriptAids objects, then call this method for consistent rendering.

*Call graph*: calls 2 internal fn (_rendered_messages, _run_answer).


##### `_transcript_aids`  (lines 1480–1531)

```
async def _transcript_aids(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, agent_origin: frozenset[str], speakers: dict[str, str], asked: dict[str, str], opens: frozenset[UUI
```

**Purpose**: Collects all extra information needed to render a transcript consistently. This includes turns, subagents, shared files, questions, speakers, answers, and app cards.

**Data flow**: It concurrently reads turns, spawned runs, shared artifacts, and keyed admissions; builds maps for files, apps, speakers, asks, and attachment previews; and returns a _TranscriptAids bundle.

**Call relations**: _conversation_messages and _history_messages call it before rendering settled or compacted transcript windows.

*Call graph*: calls 8 internal fn (conversation_subagent_turns, keyed_admissions, list_conversation_artifacts, list_turns, _asks, _created_apps, _file_payload, _subagent_nodes); called by 2 (_conversation_messages, _history_messages); 3 external calls (__init__, gather, partial).


##### `_conversation_messages`  (lines 1534–1656)

```
async def _conversation_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], Turn | None, int]
```

**Purpose**: Builds the complete visible message list for a conversation's current tail. It combines the committed transcript with a still-running turn and queued messages that have not yet been written into the transcript.

**Data flow**: It reads transcript, origin refs, speakers, compactions, latest turn detail, and queued arrivals; renders committed messages; appends live prompt and waiting arrivals where appropriate; and returns messages, current turn, and earlier-page marker.

**Call relations**: The chat transcript route and read-only conversation transcript route both use this as the single projection path.

*Call graph*: calls 10 internal fn (agent_origin_refs, arrival_speakers, latest_turn, list_compactions, queued_arrivals, read_transcript, turn_detail, _member_bubble, _transcript_aids, _verified_earlier); called by 2 (conversation_transcript, transcript); 3 external calls (gather, partial, member_message_text).


##### `_verified_earlier`  (lines 1659–1675)

```
async def _verified_earlier(ctx: SurfaceContext, conversation_id: UUID, indices: tuple[int, ...], messages: tuple[Message, ...]) -> int
```

**Purpose**: Finds the compaction page that truly sits above the visible transcript window. It avoids offering older pages that would duplicate already-visible messages.

**Data flow**: It receives compaction indices and current messages, reads each candidate's after-window from newest to oldest, and returns the first matching index or zero.

**Call relations**: _conversation_messages and _history_messages use it to provide reliable upward pagination.

*Call graph*: calls 1 internal fn (read_compaction_after); called by 2 (_conversation_messages, _history_messages).


##### `_history_messages`  (lines 1678–1718)

```
async def _history_messages(ctx: SurfaceContext, agent_id: UUID, conversation_id: UUID, viewer: UUID, index: int, opens: frozenset[UUID]) -> tuple[list[dict[str, object]], int] | None
```

**Purpose**: Renders one older page of a compacted conversation. It removes the kept overlap so pages join cleanly with no repeated messages.

**Data flow**: It reads a compaction record, slices the before-window, gathers speaker/origin context, builds transcript aids, renders the page, finds the page above it, and returns both.

**Call relations**: conversation_history calls it when the browser scrolls upward in a long compacted transcript.

*Call graph*: calls 5 internal fn (agent_origin_refs, arrival_speakers, read_compaction, _transcript_aids, _verified_earlier); called by 1 (conversation_history); 1 external calls (gather).


##### `transcript`  (lines 1721–1757)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one of the member's own chat conversations as the browser should draw it on load. It also tells the page which live turn to stream or which credential prompt is still open.

**Data flow**: It authenticates, checks agent and conversation ownership, renders conversation messages, adds earlier-page and current-turn or handoff data, and returns JSON.

**Call relations**: This is the chat-specific transcript route; it relies on _member_chat and _conversation_messages.

*Call graph*: calls 6 internal fn (_agent_param, _audience_for, _conversation_messages, _member_chat, _open_handoffs, _opens); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 1760–1773)

```
async def _open_handoffs(ctx: SurfaceContext, turn_id: UUID, terminal: TerminalFrame) -> dict[str, object]
```

**Purpose**: Reports still-open handoffs from the newest committed turn, currently credential prompts. This lets a reload redraw prompts that are still awaiting input.

**Data flow**: It receives a turn id and terminal frame, checks for credential requests, filters pending prompts, and returns a handoff dictionary.

**Call relations**: transcript calls it when the latest turn is already terminal but still asks the member for credentials.

*Call graph*: calls 1 internal fn (_pending_prompts); called by 1 (transcript).


##### `chats_index`  (lines 1776–1847)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Builds the chat rail: conversations involving the member and readable conversations involving others. It keeps the member's own work from being pushed out by workspace-wide activity.

**Data flow**: It authenticates, optionally resolves a permalink, loops visible chat agents, reads member and other conversation pages, filters unreadable or rowless web chats, formats rows, sorts by last activity, and returns JSON.

**Call relations**: The frontend calls this for the rail; _resolve_chat handles direct conversation links.

*Call graph*: calls 5 internal fn (list_agent_conversations, _audience_for, _chat_row_key, _iso, _resolve_chat); 2 external calls (JSONResponse, web_extension).


##### `_resolve_chat`  (lines 1850–1919)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

**Purpose**: Resolves a single conversation id from a portal permalink. It returns a rail row for the member's own chat or a read-only conversation projection for other readable surfaces.

**Data flow**: It parses the id, searches visible chat agents for an own chat, checks latest turn timing, or falls back to conversation-agent lookup and listing authorization, then returns matching JSON.

**Call relations**: chats_index delegates here when the request asks about one conversation.

*Call graph*: calls 7 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, _conversation_row, _iso, _member_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 1922–1935)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Shared permission check for agent-specific panels and intent actions. It proves the member is signed in and that the path's agent is in their web audience.

**Data flow**: It authenticates, resolves audience, parses the agent id, checks audience access, and returns member id, email, audience, and agent id or a 404 response.

**Call relations**: Settings, skills, connections, conversations, homepage, intents, and community skill routes all start here.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 9 (_readable_conversation, community_skill, community_skills, connections, conversations, homepage, intents, settings, skills); 1 external calls (Response).


##### `_iso`  (lines 1938–1939)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Converts optional datetimes into JSON-friendly ISO strings. It leaves missing times as null.

**Data flow**: It receives a datetime or none and returns its ISO text or none.

**Call relations**: Many response builders use it for consistent timestamp formatting.

*Call graph*: called by 8 (_conversation_row, _memory_rows, _radar_run, _resolve_chat, _usage_payload, chats_index, object_detail, workspace_artifacts); 1 external calls (isoformat).


##### `_window_param`  (lines 1942–1958)

```
def _window_param(request: Request) -> int | None | Response
```

**Purpose**: Parses the usage time window requested by the browser. It accepts named ranges or a bounded number of seconds.

**Data flow**: It reads query parameters, validates range names or integer seconds, and returns seconds, all-time as none, or an error response.

**Call relations**: workspace_usage uses it before asking core for spend reports.

*Call graph*: called by 1 (workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 1961–2001)

```
def _usage_payload(report: MemberSpendReport | SpendReport) -> dict[str, object]
```

**Purpose**: Formats detailed usage accounting into JSON. It includes selected-window totals, all-time totals, daily history, and breakdowns.

**Data flow**: It receives a member or workspace spend report, reads nested usage fields, converts timestamps, and returns a dictionary.

**Call relations**: workspace_usage uses it for both the member's own usage and the admin workspace rollup.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_usage).


##### `skills`  (lines 2004–2024)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the skills loadable by one visible agent. These are the agent-specific and shared abilities the member can review.

**Data flow**: It passes through the panel gate, asks core for agent skills, formats each skill's name, description, origin, and instructions, and returns JSON.

**Call relations**: It is an agent panel route guarded by _panel_gate.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 2030–2034)

```
def _community_refusal(fault: Exception) -> Response
```

**Purpose**: Turns a community directory failure into a response meant to be shown to the member. A special header tells the frontend the body is user-facing text.

**Data flow**: It receives an exception, converts it to text, and returns a 502 response with the refusal marker header.

**Call relations**: Community listing and detail routes call it when the remote directory is unavailable.

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 2041–2057)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a page of community skills, either popular results or a search result. The member can inspect these before applying them through the intent path.

**Data flow**: It checks the panel gate, validates minimum search length, asks the community service for a listing, handles remote failures, and returns skill summaries.

**Call relations**: This read route feeds the skill browser; actual installation goes through intents.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 2060–2081)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches one community skill document for review. It validates the owner, repository, and skill name before contacting the directory.

**Data flow**: It gates the agent, validates path segments, fetches the skill, maps unavailable or missing results to appropriate responses, and returns the full document.

**Call relations**: The frontend calls it from the community skill panel before submitting an apply intent.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 2084–2149)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows or searches memory visible to the member across reachable agents. With no query it lists recent memory; with a query it searches each visible agent and deduplicates results.

**Data flow**: It authenticates, checks memory availability, builds audience subjects, validates filters and cursors for listing or runs per-agent searches, formats matches, and returns JSON.

**Call relations**: This workspace panel combines audience permissions with memory APIs and _memory_rows formatting.

*Call graph*: calls 5 internal fn (recent_memory, search_memory, decode, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 2152–2162)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Formats memory matches for the browser. It includes kind, text, optional object reference, creation time, and subject.

**Data flow**: It receives memory match objects, converts each field to simple JSON values, formats dates, and returns a list.

**Call relations**: workspace_memory uses it for both recent listings and search results.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `connections`  (lines 2165–2174)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connector accounts visible for a selected agent. It includes the member's own grants, shared grants, and extra visibility for admins.

**Data flow**: It gates the agent, asks core for agent connections using member/admin status, serializes entries, and returns JSON.

**Call relations**: This is the per-agent connections panel route.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 2177–2187)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists all connector accounts visible to the member, with agent lists trimmed to agents the web audience allows. It gives a workspace-level view without leaking hidden agents.

**Data flow**: It authenticates, reads all visible connections, filters each connection's agent edges by audience, serializes entries, and returns JSON.

**Call relations**: This workspace-level route uses _audience_for directly rather than the per-agent gate.

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 2190–2196)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns GitHub connection coverage for the member or admin. It helps the UI show what repositories or accounts are connected.

**Data flow**: It authenticates, asks core for GitHub coverage with admin status, serializes the model, and returns JSON.

**Call relations**: It is a small workspace read route built on _audience_for.

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 2199–2219)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists conversations for one selected agent that the member may see. Admins can see more rows, including private rows they may need to disclose before reading.

**Data flow**: It gates the agent, parses bounded search text, asks core for conversation listings, formats each row, and returns JSON.

**Call relations**: The agent conversations panel calls this; _conversation_row supplies the common row shape.

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 2222–2226)

```
def _searched(request: Request) -> str | None
```

**Purpose**: Extracts bounded search text for conversation listings. Empty search boxes become no search filter.

**Data flow**: It reads the q query parameter, trims it, cuts it to the maximum length, and returns text or none.

**Call relations**: conversations uses it before asking core for a searched listing.

*Call graph*: called by 1 (conversations).


##### `_conversation_row`  (lines 2229–2260)

```
def _conversation_row(entry: ListedConversation, agent: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Formats one conversation listing row. It avoids showing content-like fields when the row is not readable.

**Data flow**: It receives a listed conversation and optional agent summary, pulls surface, audience, speakers, timestamps, read/disclose flags, and returns a JSON-ready dictionary.

**Call relations**: conversations and _resolve_chat use it for panel and permalink responses.

*Call graph*: calls 1 internal fn (_iso); called by 2 (_resolve_chat, conversations).


##### `_readable_conversation`  (lines 2263–2283)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Shared gate for reading conversation content. It proves the agent is visible, the conversation id is valid, and core says this member may read it.

**Data flow**: It gates the panel, parses or accepts a conversation id, asks core for readability, and returns agent id, conversation id, and SlotViewer or a 404 response.

**Call relations**: Transcript, history, attachment, and slot routes use it as the main content gate.

*Call graph*: calls 3 internal fn (readable_conversation, _opens, _panel_gate); called by 4 (_slot_target, conversation_attachment, conversation_history, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 2286–2301)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a read-only transcript for an authorized conversation. This covers conversations not necessarily owned by the current web chat.

**Data flow**: It runs the readable-conversation gate, renders the conversation messages, adds an earlier-page marker when present, and returns JSON.

**Call relations**: It shares _conversation_messages with the chat transcript route so both screens display messages the same way.

*Call graph*: calls 2 internal fn (_conversation_messages, _readable_conversation); 1 external calls (JSONResponse).


##### `_member_chat_page`  (lines 2304–2326)

```
async def _member_chat_page(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Alternative gate for pages belonging to the member's own chat. It allows chat transcript history and attachments even when the normal panel gate is not the right shape.

**Data flow**: It authenticates, checks chat-agent access, parses the conversation id, verifies _member_chat, and returns a SlotViewer or 404.

**Call relations**: conversation_history and conversation_attachment try this after the general readable-conversation gate fails.

*Call graph*: calls 4 internal fn (_agent_param, _audience_for, _member_chat, _opens); called by 2 (conversation_attachment, conversation_history); 4 external calls (__init__, Response, web_extension, UUID).


##### `conversation_history`  (lines 2329–2354)

```
async def conversation_history(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one older page of a compacted conversation transcript. It is used when the reader scrolls upward.

**Data flow**: It authorizes through either normal conversation reading or member-chat reading, validates the page index, renders that history page, adds an earlier marker if any, and returns JSON.

**Call relations**: It delegates the actual page reconstruction to _history_messages.

*Call graph*: calls 3 internal fn (_history_messages, _member_chat_page, _readable_conversation); 2 external calls (JSONResponse, Response).


##### `_inbox_attachment`  (lines 2357–2368)

```
def _inbox_attachment(path: str) -> bool
```

**Purpose**: Checks whether a requested path is exactly one file saved by the web composer. This blocks access to arbitrary workspace files through the attachment preview route.

**Data flow**: It splits the path and returns true only for a simple filename directly under the web inbox directory.

**Call relations**: conversation_attachment uses it after also checking that the file is an image type.

*Call graph*: called by 1 (conversation_attachment).


##### `conversation_attachment`  (lines 2371–2411)

```
async def conversation_attachment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a safe inline preview of an image the member attached to a message. It never serves PDFs, SVGs, HTML-like files, or unrelated workspace paths.

**Data flow**: It authorizes the conversation, validates image type and inbox path, checks file existence and size, reads bytes from the workspace, validates the image content, and returns it with no-store headers.

**Call relations**: Attachment preview URLs produced by _attachment_preview point to this route.

*Call graph*: calls 5 internal fn (list_workspace_files, read_workspace_file, _inbox_attachment, _member_chat_page, _readable_conversation); 5 external calls (__init__, Response, raster_image_media_type, validated_image_preview, log).


##### `_slot_target`  (lines 2431–2455)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

**Purpose**: Finds the conversation target for typed conversation slots. It also supports reading a subagent conversation through an authorized root conversation.

**Data flow**: It reads optional root query data, authorizes the root or direct conversation, verifies spawned subagent relationships when needed, and returns a SlotTarget.

**Call relations**: conversation_slots and conversation_slot use it before building slot context.

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 2458–2474)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

**Purpose**: Builds the context object passed to a conversation slot provider. A slot provider is an extension hook that summarizes or reads a typed panel for a conversation.

**Data flow**: It reads the conversation audience and transcript, combines them with extension context, ids, messages, and public URL, and returns the slot context or nothing.

**Call relations**: Slot list and slot detail routes call it before projection and provider execution.

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 2477–2562)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

**Purpose**: Adds host-provided projections to slot context for built-in slot types such as changes, artifacts, sites, and automations. It also collects visibility information for object-backed cards.

**Data flow**: It inspects the extension and expected payload type, reads changes, artifacts, or member object rows as needed, builds projection or visible-item data, and returns an updated context.

**Call relations**: conversation_slots and conversation_slot call it before summarizing or reading a slot.

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 8 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit, urlunsplit).


##### `_authorized_slot_payload`  (lines 2565–2609)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

**Purpose**: Filters slot payloads so they only include sites or automations the viewer may open. For automations with hidden content, it keeps the row but removes sensitive details.

**Data flow**: It receives a payload and context visible-items list, filters or redacts entries, and returns the adjusted payload.

**Call relations**: conversation_slot applies it after a provider returns its typed payload.

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 2612–2656)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists which typed slots are available for an authorized conversation, with counts. A slot appears only if its provider can summarize something useful.

**Data flow**: It authorizes the target, builds shared slot context, loops configured slot providers, projects context, asks each provider for a count, logs failures, and returns slot metadata.

**Call relations**: The frontend uses this to decide which conversation side panels to show.

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 2659–2686)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full payload for one typed conversation slot. It verifies the provider returned the exact expected model type.

**Data flow**: It authorizes the target, finds the slot provider by id, builds and projects context, reads the payload, type-checks it, filters unauthorized content, and returns JSON.

**Call relations**: The browser calls it after selecting a slot listed by conversation_slots.

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 2689–2692)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Retrieves the workspace-changes projection from a slot context. It raises if the wrong projection was supplied.

**Data flow**: It receives slot context, checks the projection type, and returns the WorkspaceChanges object.

**Call relations**: The built-in changes slot read and summarize functions use it.

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 2695–2696)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Returns the changes payload for the built-in changes conversation slot.

**Data flow**: It receives slot context and returns the checked changes projection.

**Call relations**: It is registered as the read function for CHANGES_SLOT.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 2699–2700)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts changes for the built-in changes slot and hides the slot when there are none.

**Data flow**: It receives slot context, reads the changes projection, and returns the number of changes or none.

**Call relations**: It is registered as the summarize function for CHANGES_SLOT.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 2713–2716)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Retrieves the artifacts projection from a slot context. It protects the built-in artifacts slot from being called with the wrong prepared data.

**Data flow**: It receives slot context, checks projection type, and returns the ArtifactsSlotPayload.

**Call relations**: The built-in artifacts read and summarize functions use it.

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 2719–2720)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Returns the artifact payload for the built-in artifacts conversation slot.

**Data flow**: It receives slot context and returns the checked artifacts projection.

**Call relations**: It is registered as the read function for ARTIFACTS_SLOT.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 2723–2725)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Counts artifacts for the built-in artifacts slot and hides the slot when empty.

**Data flow**: It receives slot context, reads the artifacts projection, and returns the artifact count or none.

**Call relations**: It is registered as the summarize function for ARTIFACTS_SLOT.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `workspace_credentials`  (lines 2738–2746)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists workspace credential slots the member can fill, without exposing secret values. It shows fill state, not the stored credential itself.

**Data flow**: It authenticates, asks core for declared credential slots, serializes them, and returns JSON.

**Call relations**: The workspace credentials panel calls this read route; fulfillment uses fulfill_credential.

*Call graph*: calls 2 internal fn (list_credential_slots, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 2749–2767)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace roster and whether the current member can add people. It shows member admin and seat state.

**Data flow**: It authenticates, reads members from core, combines them with the current audience admin flag, and returns JSON.

**Call relations**: This workspace panel uses the same audience authority that chat and object reads rely on.

*Call graph*: calls 2 internal fn (list_members, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 2770–2781)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the member. These are workspace-level data sources, not per-agent rows.

**Data flow**: It authenticates, asks core for sources using member/admin status, serializes entries, and returns JSON.

**Call relations**: The workspace sources panel calls this route.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `workspace_surfaces`  (lines 2784–2795)

```
async def workspace_surfaces(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists installed surfaces connected to agents the member can see. Hidden agents' installations are filtered out.

**Data flow**: It authenticates, reads all installations, filters by web audience agent access, serializes entries, and returns JSON.

**Call relations**: The workspace topology or surfaces panel uses this route.

*Call graph*: calls 2 internal fn (list_installations, _audience_for); 1 external calls (JSONResponse).


##### `workspace_artifacts`  (lines 2798–2849)

```
async def workspace_artifacts(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a searchable, filterable page of files shared with the member. It includes signed links and optional preview links.

**Data flow**: It authenticates, validates cursor and filters, asks core for artifact page rows, formats file metadata, timestamps, links, origin, and cursors, and returns JSON.

**Call relations**: This workspace artifacts panel reuses core listing cursors and artifact link helpers.

*Call graph*: calls 6 internal fn (artifact_link, artifact_preview_link, list_artifacts, decode, _audience_for, _iso); 2 external calls (JSONResponse, Response).


##### `_radar_run`  (lines 2852–2879)

```
def _radar_run(ctx: SurfaceContext, run: ScheduledRun, task_names: Mapping[UUID, str]) -> dict[str, object]
```

**Purpose**: Formats one scheduled run for the radar feed. Successful runs show their artifacts; unsuccessful ones also show explanatory text.

**Data flow**: It receives a run and task-name map, derives the scheduled task id from the idempotency key when possible, formats timestamps, artifacts, links, and status, and returns a dictionary.

**Call relations**: workspace_radar uses it for every row in the scheduled-run feed.

*Call graph*: calls 3 internal fn (artifact_link, artifact_preview_link, _iso); called by 1 (workspace_radar); 1 external calls (scheduled_fire_task_id).


##### `_radar_task_names`  (lines 2882–2905)

```
async def _radar_task_names(ctx: SurfaceContext, audience: WebAudience, member_id: UUID, runs: tuple[ScheduledRun, ...]) -> dict[UUID, str]
```

**Purpose**: Looks up names for scheduled tasks that fired visible runs. Deleted or invisible tasks simply have no name.

**Data flow**: It receives runs, finds agents represented in them, lists scheduled_task objects for those agents, extracts ids and names, and returns an id-to-name map.

**Call relations**: workspace_radar calls it before formatting runs with _radar_run.

*Call graph*: calls 1 internal fn (list_member_objects); called by 1 (workspace_radar); 2 external calls (__init__, UUID).


##### `workspace_radar`  (lines 2908–2955)

```
async def workspace_radar(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a feed of scheduled work that ran on its own and reported into readable conversations. It is like an activity radar for automations.

**Data flow**: It authenticates, parses cursor and optional agent filter, chooses a page size that includes today's runs, reads scheduled runs, resolves task names, formats rows, and returns cursors.

**Call relations**: This workspace panel combines scheduling data, audience checks, and _radar_run formatting.

*Call graph*: calls 6 internal fn (count_scheduled_runs_since, list_scheduled_runs, decode, _audience_for, _radar_run, _radar_task_names); 4 external calls (now, JSONResponse, Response, UUID).


##### `workspace_usage`  (lines 2958–3033)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns usage and cost accounting for the current member, and for admins also the workspace rollup. It keeps non-admins from seeing other members' spend.

**Data flow**: It authenticates, parses the requested time window, reads member spend, formats caps and usage, optionally reads workspace rollup, and returns JSON.

**Call relations**: It uses _window_param and _usage_payload as its validation and formatting helpers.

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `_member_turn`  (lines 3036–3072)

```
async def _member_turn(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID, str] | Response
```

**Purpose**: Authorizes access to one live turn for streaming or stopping-related views. The turn must belong to the member and still be reachable through their web audience or own chat.

**Data flow**: It authenticates, parses turn id, checks owner, loads turn detail, verifies audience or chat access, and returns member id, turn id, and email or a refusal response.

**Call relations**: stream calls it before opening the server-sent events tail.

*Call graph*: calls 4 internal fn (turn_detail, turn_owner, _audience_for, _member_chat); called by 1 (stream); 3 external calls (Response, web_extension, UUID).


##### `stream`  (lines 3075–3083)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens the live SSE stream for a turn. SSE means server-sent events, a browser feature for receiving a sequence of updates over one HTTP response.

**Data flow**: It authorizes the turn, reads Last-Event-ID for resume support, and returns a streaming response driven by _events.

**Call relations**: The frontend connects here after chat admission returns a turn id.

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 3086–3087)

```
def _event(name: str, payload: dict[str, object]) -> bytes
```

**Purpose**: Formats a named server-sent event with JSON data. It is used for web-only live events that are not raw hub frames.

**Data flow**: It receives an event name and payload dictionary, JSON-encodes the payload, and returns SSE bytes.

**Call relations**: _events uses it for connect prompts, credentials, files, apps, and subagent summaries.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 3090–3103)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest) -> dict[str, object] | None
```

**Purpose**: Filters a credential request down to prompts that are still pending. This prevents already-filled prompts from reappearing after reconnect or reload.

**Data flow**: It receives a credential request, checks each prompt with core, and returns a reason, seal, and pending prompt list or none.

**Call relations**: _events uses it live, and _open_handoffs uses it for reload state.

*Call graph*: calls 1 internal fn (credential_prompt_pending); called by 2 (_events, _open_handoffs).


##### `_file_payload`  (lines 3106–3123)

```
def _file_payload(ctx: SurfaceContext, artifact: SharedArtifact) -> dict[str, object]
```

**Purpose**: Formats one shared artifact as a chat file card. Preview URLs are made same-origin so the portal's content security policy can load them safely.

**Data flow**: It receives an artifact, asks core for download and preview links, strips preview links down to path and query, and returns file metadata.

**Call relations**: Transcript aids and live events use it when turns share files.

*Call graph*: calls 2 internal fn (artifact_link, artifact_preview_link); called by 2 (_events, _transcript_aids); 2 external calls (urlsplit, urlunsplit).


##### `_opens`  (lines 3126–3127)

```
def _opens(audience: WebAudience) -> frozenset[UUID]
```

**Purpose**: Returns the set of agent ids the current web audience can open. This is used to decide whether app cards should be shown.

**Data flow**: It receives a WebAudience and returns a frozen set of visible agent UUIDs.

**Call relations**: Transcript, readable-conversation gates, member-chat pages, and live app creation checks use it.

*Call graph*: called by 4 (_events, _member_chat_page, _readable_conversation, transcript).


##### `_created_apps`  (lines 3130–3161)

```
async def _created_apps(ctx: SurfaceContext, created: Mapping[str, tuple[ObjectRef, ...]], opens: frozenset[UUID]) -> dict[str, list[dict[str, object]]]
```

**Purpose**: Builds cards for application agents created by turns, but only when the viewer may open those agents. This prevents readable conversations from advertising private apps.

**Data flow**: It receives created object refs and visible agent ids, lists agents, matches created agent names, filters by visibility, and returns cards keyed by turn id.

**Call relations**: Transcript aids and live terminal event processing use it to attach app cards.

*Call graph*: calls 1 internal fn (list_agents); called by 2 (_events, _transcript_aids).


##### `_events`  (lines 3164–3211)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str, email: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams live frames for a turn and enriches terminal frames with web-specific events. It sends subagent summaries, connection links, credential prompts, shared files, and created app cards.

**Data flow**: It opens the core tail from a cursor, loops frames, on terminal frames reads extra context and yields extra SSE events, then yields the raw frame converted by _sse.

**Call relations**: stream returns this async iterator as the body of the SSE response.

*Call graph*: calls 12 internal fn (connect_url, conversation_subagent_turns, shared_artifacts, tail, turn_detail, _created_apps, _event, _file_payload, _opens, _pending_prompts (+2 more)); called by 1 (stream); 2 external calls (web_audience, web_extension).


##### `fulfill_credential`  (lines 3214–3242)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores one credential value entered privately in the web UI. The value is not added to chat history or transcript.

**Data flow**: It authenticates, size-checks and parses the form, validates sealed token, slot, and value, checks secret size, asks core to fulfill the sealed request, and returns stored slot or an error.

**Call relations**: Credential prompts emitted by _events or transcript handoffs lead the browser to this POST route.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `admin_index`  (lines 3245–3297)

```
async def admin_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the administration snapshot for workspace admins. It includes agents, installations, web grants, members, seats, spend caps, and deploy settings.

**Data flow**: It authenticates, refuses non-admins as not found, reads installations, grants, seat snapshot, spend caps, and deploy metadata, formats everything, and returns JSON.

**Call relations**: This is the admin panel's main read route and intentionally performs no mutations.

*Call graph*: calls 3 internal fn (list_installations, spend_caps, _audience_for); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


##### `_object_gate`  (lines 3300–3314)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

**Purpose**: Shared gate for generic object index and detail pages. It authenticates, resolves web audience, and verifies that the requested object kind exists.

**Data flow**: It reads the kind path parameter, asks core for that kind's portal description, and returns member id, audience, and kind metadata or a 404 response.

**Call relations**: object_index and object_detail both start here.

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 3317–3327)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

**Purpose**: Resolves the agent namespace for an object read from the query string. Object rows live inside an agent's namespace, so this is required for detail reads and optional for index fanout.

**Data flow**: It parses the agent query parameter as a UUID, checks it against audience agents, and returns the agent summary or 404.

**Call relations**: object_index uses it for single-agent listings; object_detail always uses it.

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_kind_payload`  (lines 3330–3337)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

**Purpose**: Builds common metadata about an object kind for portal responses. It tells the frontend fields, schema, and whether apply/delete intents are supported.

**Data flow**: It receives a PortalKind and returns kind name, list fields, spec schema, and intent capability booleans.

**Call relations**: Both object index and detail responses include this payload.

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 3340–3347)

```
def _filter_value(raw: str) -> JsonValue
```

**Purpose**: Parses one query-string filter value into the type object rows may actually use. For example, true can become a boolean instead of the string “true”.

**Data flow**: It receives raw text, tries JSON decoding, and falls back to the original string on parse failure.

**Call relations**: object_index uses it when turning extra query parameters into exact filters.

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_merged_rank`  (lines 3350–3363)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```

**Purpose**: Computes a sort key for rows merged across several agents. It keeps missing values, booleans, numbers, and text in a stable order.

**Data flow**: It receives a row and field name, inspects the field value, and returns a tuple used for sorting with name as a tie-breaker.

**Call relations**: object_index uses it when no single agent is named and rows are fanned out.


##### `object_index`  (lines 3366–3436)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists objects of one kind visible to the member. It can read one agent namespace or fan out across all visible agents.

**Data flow**: It gates kind and audience, validates sort order and cursor rules, builds an ObjectListQuery from search, filters, sort, and cursor, asks each relevant agent for rows, merges and sorts fanout results, and returns JSON.

**Call relations**: This generic route powers object index pages for many object kinds.

*Call graph*: calls 5 internal fn (list_member_objects, _filter_value, _kind_payload, _object_agent, _object_gate); 3 external calls (__init__, JSONResponse, Response).


##### `object_detail`  (lines 3439–3485)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one object row and its details as the member may read them. Hidden specs become null, and unreadable rows look the same as missing rows.

**Data flow**: It gates kind and agent, asks core for the object, checks linked objects for whether they open, formats spec, status, links, and timestamps, and returns JSON or 404.

**Call relations**: This generic route powers object detail pages for many object kinds.

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 3488–3520)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Converts one live hub frame into server-sent event bytes. It includes an event id when a cursor is available so browsers can resume after disconnects.

**Data flow**: It receives a cursor and frame, picks an SSE event name based on frame type, serializes the frame JSON, and returns bytes.

**Call relations**: _events calls it for each raw live frame after adding any web-specific companion events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 3523–3528)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts prepared panel intents for one agent, such as applying or deleting supported objects. It is the mutation lane used by panels.

**Data flow**: It gates the panel, then passes context, request, agent id, member id, and email to the panel intent submitter.

**Call relations**: It delegates the actual intent behavior to ufo_ext_web.panels.submit_intent after _panel_gate succeeds.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `settings`  (lines 3531–3539)

```
async def settings(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent's settings view. It includes configuration visible to the member, with admin-only details controlled by audience admin status.

**Data flow**: It gates the panel and calls the panel settings helper with agent id and admin flag, returning that response.

**Call relations**: It delegates settings formatting to ufo_ext_web.panels.agent_settings.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_settings).


##### `homepage`  (lines 3542–3573)

```
async def homepage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent's bound homepage URL when the viewer is allowed to see it. Private agents only expose homepages to owner and admins.

**Data flow**: It gates the agent, checks visibility and ownership, lists site objects bound as that agent's homepage with admin read, finds a site URL, and returns set or none state.

**Call relations**: The Home tab calls this read route; homepage creation is seeded by seed_homepages or done through agent tools.

*Call graph*: calls 2 internal fn (list_member_objects, _panel_gate); 2 external calls (__init__, JSONResponse).


##### `preview`  (lines 3587–3618)

```
async def preview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders an uploaded file into a PNG preview for the composer before the member sends it. Nothing is stored and no turn is admitted.

**Data flow**: It authenticates, requires multipart form data, size-checks and parses the form, takes the first file, maps its suffix to a preview kind, asks core's preview renderer for PNG bytes, and returns the image or refusal.

**Call relations**: This route is independent of a specific agent and uses the same form helpers as chat uploads.

*Call graph*: calls 4 internal fn (render_preview, _audience_for, _form, _framed_length); 2 external calls (PurePosixPath, Response).


### `extensions/web/ufo_ext_web/community.py`

`io_transport` · `request handling`

The web app has a Community area where a user can browse skills made by others. This file is the bridge between that screen and the skills.sh website. Without it, the Community tab could not show popular skills, search the directory, or load the description and instructions shown before install.

It offers two main reads. The first read, `listing`, returns a small list of skills. If the user typed a search term, it calls the directory search API. If not, it reads the public leaderboard page and extracts the skill entries embedded there. The second read, `fetch`, downloads one skill’s document and pulls out its name, description, and instructions.

The file is careful not to overuse the remote service. Listings are cached for 15 minutes, and downloaded documents are kept for the life of the process. Think of it like keeping a flyer on the desk after you have already picked it up, instead of walking back to the noticeboard every time.

It also turns remote failures into clear user-facing messages. If the directory rate-limits the app, returns bad data, or sends too much data, the code raises `CommunityUnavailable` with a sentence that can be shown under a toast notification instead of silently showing an empty result.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: Turns an HTTP failure code from the skill directory into a clear `CommunityUnavailable` error. It gives a special, more helpful message for rate limiting, because that is something users may be able to wait out.

**Data flow**: It receives a numeric web response code. If the code means “too many requests,” it creates an error explaining the 60-per-hour limit; otherwise it creates an error that says which code the directory returned. The result is an exception object ready to be raised by the caller.

**Call relations**: The network-reading helpers call this when the skills.sh service answers with something other than success. `_search` uses it for search API failures, and `_body` uses it for streamed download failures, so the rest of the file gets one consistent kind of user-readable refusal.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: Returns the skill rows shown in the Community listing: either popular skills when there is no search text, or matching skills when the user searches. It also avoids repeat network calls by reusing a recent cached answer.

**Data flow**: It receives a search query string. First it checks the listing cache for the same query and returns it if it is still fresh. If not cached, it opens an HTTP client, asks either the search endpoint or the leaderboard reader for results, trims the list to the display limit, saves it in the cache, and returns the list of `CommunitySkill` records.

**Call relations**: This is the public entry for the Community browse view. It creates the temporary web client through `_client`, then hands the real directory read to `_search` when the user typed a query or `_popular` when they did not.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: Downloads and reads the full document for one community skill, so the install screen can show the skill’s description and instructions. It returns nothing when the downloaded skill file is missing or not in the expected format.

**Data flow**: It receives a source repository such as `owner/repo` and a skill name. It builds a cache key from both, returns the cached document if already fetched, otherwise downloads the directory’s JSON package for that skill. It looks for the `SKILL.md` file, parses that markdown document, stores the parsed result or `None` in the cache, and returns it.

**Call relations**: This is used after a user picks a skill and needs details beyond the listing row. It uses `_client` to make the web client, `_body` to safely download the response, and `_parse` to turn the markdown file into a `CommunityDocument`.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: Creates the temporary asynchronous web client used for calls to skills.sh. “Asynchronous” means it can wait for the network without blocking other work in the server.

**Data flow**: It receives a timeout value. It creates an `httpx.AsyncClient` with that timeout, optional test transport, and redirect-following enabled, then returns the client to be used inside an `async with` block.

**Call relations**: `listing` and `fetch` call this whenever they need to talk to the remote directory. Keeping client creation here makes tests easier, because tests can inject a fake transport instead of using the real network.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: Reads the skills.sh public leaderboard and turns it into a sorted list of popular community skills. This is used when the user opens the Community tab without typing a search.

**Data flow**: It receives an open HTTP client. It downloads the leaderboard page with a special header, scans the page text for embedded JSON-looking skill entries, converts each valid entry into a `CommunitySkill`, removes duplicates by source and name, and returns the skills sorted by install count from highest to lowest. If no valid listing is found, it raises a clear unavailable error.

**Call relations**: `listing` calls this for the default, no-query view. `_popular` relies on `_body` for the protected download and `_entry` to normalize each raw leaderboard entry into the app’s simple skill row format.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: Calls the skills.sh public search endpoint and returns matching skills sorted by popularity. This is used when the user narrows the Community list with search text.

**Data flow**: It receives an open HTTP client and the user’s query. It sends the query and result limit to the search API, rejects non-success responses with a readable error, converts each returned raw skill item into a `CommunitySkill`, drops invalid items, sorts the rest by install count, and returns them.

**Call relations**: `listing` calls this when a query is present. It delegates failure wording to `_refusal` and delegates raw item cleanup to `_entry`, so search results look like leaderboard results to the rest of the web app.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: Converts one raw skill entry from skills.sh into the app’s small, safe `CommunitySkill` shape. It filters out entries that are not useful or do not name a valid source repository.

**Data flow**: It receives an unknown object from a remote response. If it is not a dictionary-like record, or if it lacks a skill name or valid `owner/repo` source, it returns `None`. Otherwise it extracts the name, source, and install count, creates a `CommunitySkill`, and returns it.

**Call relations**: Both `_popular` and `_search` use this as their shared cleanup step. That means the listing screen gets the same kind of rows whether they came from the leaderboard page or the search API.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: Safely downloads a response body from the skill directory. It protects the server by refusing failed responses and by stopping if the remote service sends more data than this app is willing to accept.

**Data flow**: It receives an HTTP client, a URL, a maximum byte size, and optional headers. It streams the response in chunks, checks the status code before reading, counts the bytes as they arrive, rejects anything too large, joins the accepted chunks together, and returns the complete bytes.

**Call relations**: `_popular` uses this to read the leaderboard page, and `fetch` uses it to download one skill package. When the directory refuses a request, `_body` asks `_refusal` to create the user-readable error message.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: Reads a downloaded `SKILL.md` document and extracts the pieces the install screen needs: name, description, instructions, and the original document. It only accepts documents with valid front matter, which is the metadata block at the top of the markdown file.

**Data flow**: It receives the full markdown document as text. It checks for a top metadata section wrapped in `---`, parses that metadata as YAML, requires a name and description, strips the remaining markdown body into instructions, and returns a `CommunityDocument`. If any required part is missing or unreadable, it returns `None`.

**Call relations**: `fetch` calls this after it finds the `SKILL.md` file inside the downloaded skill package. `_parse` is the final step that turns the raw file text into the structured document the web route can show to the user.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).


### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The web portal has buttons and forms for things like changing an agent, adding a team member, connecting an account, requesting a credential, or setting billing refills. This file defines the allowed shape of those submissions and then routes them through the existing conversation-and-tool system instead of creating separate web-only edit endpoints. That matters because every change gets the same permissions, audit trail, ordering, and error behavior as a chat command.

The main idea is: a panel submits a small, typed “intent” — a prepared request to do one specific thing. The file checks that the intent is valid, turns it into a ToolIntent, admits it as a turn in a durable intent conversation, waits for the turn to finish, and returns a simple JSON answer to the browser. Like putting a request in a single-file queue, this keeps multiple panel changes for the same member and agent from overlapping in confusing ways.

It also protects sensitive or risky flows. Credential values are never sent through the panel; the panel can only request a private credential prompt. Account connections are created through a special handoff, not by storing a URL in the form response. Agent updates are checked against available models before they are admitted, so a bad model name does not poison future runs.

Finally, the settings endpoint returns a projection of the current agent configuration, available models, schema for editable fields, and admin-only web audience information.

#### Function details

##### `ApplyIntent.kinds`  (lines 67–71)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: This returns the complete set of object kinds that a panel is allowed to submit changes for. It is used so the portal and the validation rules stay tied to the same official list.

**Data flow**: It reads the declared allowed values from the ApplyIntent kind field → extracts those fixed choices → returns them as a frozen set of strings, meaning callers can read but not accidentally change the set.

**Call relations**: Other code can ask ApplyIntent for its accepted kinds instead of copying the list by hand. Internally it uses Python’s type information to read the Literal choices that already validate incoming panel submissions.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 74–76)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: This returns the object kinds that can be created or updated with an apply action. It leaves out kinds that have special-only flows, such as credentials and connections.

**Data flow**: It starts with all allowed ApplyIntent kinds → removes kinds that are delete-only or connect-only → returns the remaining kinds as the set that may use apply.

**Call relations**: The web surface calls this when deciding what controls to show for an object kind. That keeps the screen from offering an apply button for things this route would later reject.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 79–84)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: This returns the object kinds that can be deleted from a panel. It deliberately excludes kinds that are only connected, not deleted through this path.

**Data flow**: It starts with all allowed ApplyIntent kinds → removes connect-only kinds → returns the kinds for which delete is accepted.

**Call relations**: The web surface calls this while building object page data. The same rule that validates submissions also informs the user interface, so the portal does not draw dead or misleading delete controls.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 87–106)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: This is the safety check that makes sure each requested action matches the kind of object it is trying to change. For example, connecting is only for connections, and credentials can only be cleared here, not filled with a secret value.

**Data flow**: It receives a parsed ApplyIntent → checks combinations of verb, kind, spec, and create_only → either returns the same intent as valid or raises a validation error explaining the bad pairing.

**Call relations**: This runs automatically during Pydantic validation, before submit_intent accepts the request. It prevents invalid panel actions from reaching _tool_intent or being admitted as tool turns.


##### `_tool_intent`  (lines 194–301)

```
def _tool_intent(submitted: ApplyIntent | AddMemberIntent | AudienceIntent | CorrectionIntent | CredentialIntent | RefillIntent | TranscriptIntent, slot: CredentialSlotView | None) -> ToolIntent
```

**Purpose**: This converts a validated panel submission into the exact tool command the backend already knows how to run. It is the translator between “a web form was submitted” and “run this named tool with these inputs.”

**Data flow**: It receives one validated intent and, for credential requests, the matching credential slot description → chooses the correct tool name and builds the tool input dictionary → returns a ToolIntent ready to be admitted into a conversation turn. For object apply actions, it also converts the object kind, name, and spec into a YAML manifest, which is a readable text format for structured data.

**Call relations**: submit_intent calls this after validation and any needed lookups. The returned ToolIntent is then passed to the surface context’s admit method so the existing tool-running machinery performs the change.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 304–320)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: This turns the final result of an admitted tool turn into the small JSON response the browser expects. It reports success, failure, any user-facing message, and the turn id used for audit or follow-up.

**Data flow**: It receives a terminal frame from the turn and the turn’s UUID → checks whether the turn finished successfully, requested credentials, or failed → returns a JSON HTTP response with applied true or false and an appropriate message.

**Call relations**: submit_intent calls this when the turn stream produces a Terminal frame. This function is the final adapter between the internal conversation result and the panel’s synchronous submit response.

*Call graph*: called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `submit_intent`  (lines 323–443)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: This is the main request handler for panel form submissions. It accepts one prepared change from the web portal, validates it, queues it as an intent conversation turn, waits for the result, and answers the browser.

**Data flow**: It reads the HTTP request body → rejects bodies that are too large → parses the JSON into one of the allowed panel intent models → fills in missing required agent settings when a partial agent edit was submitted → checks model and sandbox options before creating a turn → looks up credential slot metadata when needed → converts the submission with _tool_intent → creates or finds the member’s intent conversation for this agent → admits the turn → watches the turn stream until it finishes, parks, or times out → returns a JSON response describing whether the change was applied. It changes backend state indirectly by admitting a tool intent that the conversation engine executes.

**Call relations**: This function is called by the web routing layer when a panel submits a mutation. It relies on SurfaceContext methods to read agent details, find credential slots, create the intent conversation, admit the turn, and tail the turn’s frames. It hands conversion work to _tool_intent and final response formatting to _outcome.

*Call graph*: calls 7 internal fn (admit, agent_detail, conversation_for, list_credential_slots, tail, _outcome, _tool_intent); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `_update_schema`  (lines 446–457)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: This builds the schema used by the settings page to know which agent fields can be edited in the normal settings form. A schema is a machine-readable description of fields and their expected shapes.

**Data flow**: It asks AgentSpec for its JSON schema → removes fields the settings form should not render there, such as prompt and icon, plus sandbox_size when this deployment does not support sandbox sizes → returns the trimmed schema.

**Call relations**: agent_settings calls this while building the settings response. The browser can then render controls from the same AgentSpec definition the backend uses, instead of relying on a separate hand-written field list.

*Call graph*: called by 1 (agent_settings); 1 external calls (model_json_schema).


##### `agent_settings`  (lines 460–500)

```
async def agent_settings(ctx: SurfaceContext, agent_id: UUID, *, admin: bool) -> Response
```

**Purpose**: This returns the data needed to display an agent’s settings page. It includes the current agent values, deployment capabilities, available models, editable-field schema, and, for admins, the web audience grants.

**Data flow**: It receives a surface context, an agent id, and whether the viewer is an admin → loads the agent detail → returns 404 text if there is no such agent → if admin, loads the granted web audience emails → builds a JSON response containing agent metadata, deployment sandbox internet setting, model choices, current editable spec values, the schema from _update_schema, and optional audience data.

**Call relations**: The web route for the settings page calls this when the browser needs current settings. It reads agent data through SurfaceContext, uses _update_schema to describe editable fields, and calls the audience helper functions only for admins so non-admin responses do not include that information.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


### Messaging app surfaces
iMessage and Slack adapters verify incoming events, admit them as UFO conversation turns, and route agent replies back to the originating chat or thread.

### `extensions/imessage/ufo_ext_imessage/surface.py`

`io_transport` · `main loop and message send/receive`

This file is the bridge between the UFO conversation system and an iMessage provider. Think of it like a mailroom clerk: it watches the incoming message belt, stamps each message with a position number, routes valid messages to the right conversation, saves attachments, and sends outgoing replies back through the correct iMessage thread.

The main class, ImessageSurface, has two sides. On the inbound side, listen keeps a long-running connection to the provider. It remembers a cursor, which is the last event position it safely processed, so after a restart it can catch up without repeating old messages. If the provider disconnects or says the cursor is no longer valid, the code logs the problem, resets what it must, and reconnects.

Before an iMessage can become a UFO turn, the sender must be linked to a UFO member. The file supports a confirmation flow: a pending claim is stored for a phone number, the user replies “yes” in the matching direct chat, and then the phone number is linked. It can also send a small contact card so the iMessage thread is recognized as a known contact.

On the outbound side, post, speak, and attach send final replies, mid-turn replies, and files. Large files are not uploaded directly; the user gets a link instead.

#### Function details

##### `MessageStreamDisconnected.__init__`  (lines 78–81)

```
def __init__(self, cursor: int | None, error: Exception) -> None
```

**Purpose**: This creates an error object that remembers where the iMessage stream stopped and what caused the stop. It lets the listener recover from a provider problem without forgetting the last known message position.

**Data flow**: It receives a cursor value and the original error. It stores both on the exception and turns the original error into the exception message. The result is an exception that carries enough information for reconnect logic.

**Call relations**: ImessageSurface._consume_connected raises this when live reading, catch-up, or event processing fails because of an outside provider problem. ImessageSurface.listen catches it and uses the saved cursor to decide where to resume.

*Call graph*: called by 1 (_consume_connected).


##### `contact_card`  (lines 94–106)

```
def contact_card(assigned_phone_number: str) -> bytes
```

**Purpose**: This builds a small vCard contact file for the assigned iMessage phone number. The user can save it as a contact named “ufo”, which helps remove iMessage’s “Report Junk” banner.

**Data flow**: It receives an assigned phone number. It formats that number into standard vCard text and returns the text as bytes, ready to send as an attachment.

**Call relations**: ImessageSurface._linked_member calls this after a successful account-link confirmation when there is an assigned phone number. The resulting bytes are handed to the provider as a contact-card attachment.

*Call graph*: called by 1 (_linked_member).


##### `phone_key`  (lines 109–110)

```
def phone_key(phone_number: str) -> str
```

**Purpose**: This turns a phone number into a safe private storage key for pending account-link claims. It uses a hash so the raw phone number is not directly used as the stored key.

**Data flow**: It receives a phone number string. It hashes the number with SHA-256, a one-way fingerprinting method, adds the claim prefix, and returns the storage key.

**Call relations**: ImessageSurface._linked_member uses this whenever it looks up, deletes, or clears a pending phone-number claim. It relies on this helper so the same phone number always maps to the same store key.

*Call graph*: called by 1 (_linked_member); 1 external calls (sha256).


##### `queue_key`  (lines 113–114)

```
def queue_key(conversation_id: str, *, direct: bool) -> str
```

**Purpose**: This creates the compact identifier UFO uses to remember which iMessage conversation a UFO conversation belongs to. It records both the provider conversation ID and whether it is a direct chat or group chat.

**Data flow**: It receives an iMessage conversation ID and a direct-or-group flag. It writes those two pieces into a small JSON string and returns it as the queue key.

**Call relations**: ImessageSurface._admit_message calls this before asking the UFO surface context for the matching conversation. Later, outgoing functions decode the same style of key with conversation_from_queue.

*Call graph*: called by 1 (_admit_message); 1 external calls (dumps).


##### `conversation_from_queue`  (lines 117–125)

```
def conversation_from_queue(queue: str) -> ConversationAddress
```

**Purpose**: This reverses queue_key: it reads a stored queue key and turns it back into an iMessage conversation address. It protects the send path from malformed queue keys by rejecting anything that is not the expected shape.

**Data flow**: It receives a JSON queue string. It parses the string, checks whether it describes a direct or group conversation with a non-empty ID, and returns a ConversationAddress. If the shape is wrong, it raises an error.

**Call relations**: ImessageSurface.post, ImessageSurface.speak, and ImessageSurface.attach call this before sending anything to iMessage. It tells them which provider conversation ID to use.

*Call graph*: called by 3 (attach, post, speak); 2 external calls (__init__, loads).


##### `_attachment_content`  (lines 128–129)

```
async def _attachment_content(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: This wraps already-downloaded attachment bytes as an asynchronous stream. The workspace file writer expects streamed chunks, so this helper presents one block of bytes in that shape.

**Data flow**: It receives bytes. It yields those bytes once and then ends. Nothing is changed elsewhere.

**Call relations**: ImessageSurface._downloaded_files calls this after downloading an iMessage attachment. The stream it returns is passed to the workspace file writer.

*Call graph*: called by 1 (_downloaded_files).


##### `ImessageSurface.listen`  (lines 136–160)

```
async def listen(self, context: SurfaceListenerContext) -> None
```

**Purpose**: This is the long-running inbound listener for iMessage. It keeps reading provider events, recovers from disconnections, and makes sure processing resumes from the last safely stored cursor.

**Data flow**: It receives a listener context that can open the right UFO workspace. It gets a provider, reads the saved cursor, consumes events, and on provider failures updates or clears the cursor as needed. It keeps running indefinitely unless cancelled.

**Call relations**: The surface framework calls this when the iMessage surface starts. It hands connected work to _consume_connected, uses _read_cursor and _clear_cursor around reconnects, logs stream problems, and sleeps briefly before trying again.

*Call graph*: calls 3 internal fn (_clear_cursor, _consume_connected, _read_cursor); 3 external calls (Event, sleep, log).


##### `ImessageSurface._consume_connected`  (lines 162–207)

```
async def _consume_connected(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> None
```

**Purpose**: This handles one connected session with the iMessage provider. It catches up on missed events first, then processes live events in order until the stream fails or ends.

**Data flow**: It receives the listener context, provider, installation ID, and starting cursor. It starts a background live-message pump, waits until live subscription is ready, catches up old events if needed, then reads live frames from a queue. It updates the in-memory cursor as messages are processed and raises a reconnect-friendly error on outside failures.

**Call relations**: ImessageSurface.listen calls this for each connection attempt. It starts _pump_live, calls _catch_up for durable backlog, calls _process_event for each usable event, and wraps provider-side failures in MessageStreamDisconnected.

*Call graph*: calls 5 internal fn (external_error, _catch_up, _process_event, _pump_live, __init__); called by 1 (listen); 4 external calls (Event, Queue, create_task, gather).


##### `ImessageSurface._pump_live`  (lines 209–227)

```
async def _pump_live(self, provider: MessageProvider, ready: asyncio.Event, frames: asyncio.Queue[LiveFrame | LiveFailure]) -> None
```

**Purpose**: This background task copies live provider events into an internal queue. It separates the act of staying subscribed from the act of processing each message.

**Data flow**: It receives a provider, a readiness event, and a queue. As provider.subscribe yields frames, it wraps them as LiveFrame objects and puts them on the queue. If the stream errors or naturally ends, it puts a LiveFailure into the queue instead.

**Call relations**: ImessageSurface._consume_connected starts this as a task. The pump feeds live frames back to _consume_connected, which decides whether to process them, ignore old ones, or reconnect.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (_consume_connected); 4 external calls (__init__, __init__, __init__, set).


##### `ImessageSurface._catch_up`  (lines 229–248)

```
async def _catch_up(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> int
```

**Purpose**: This replays missed provider events after a restart or reconnect. It moves from the saved cursor to the provider’s current head so the system does not skip messages.

**Data flow**: It receives a context, provider, installation ID, and cursor. It asks the provider for catch-up frames, processes message frames, tracks the newest sequence number, stores that final cursor, and returns it.

**Call relations**: ImessageSurface._consume_connected calls this before reading live frames when a cursor exists. It calls _process_event for each caught-up message and _store_cursor when catch-up is complete.

*Call graph*: calls 3 internal fn (catch_up, _process_event, _store_cursor); called by 1 (_consume_connected).


##### `ImessageSurface._process_event`  (lines 250–265)

```
async def _process_event(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, sequence: int, message: InboundMessage | None) -> None
```

**Purpose**: This processes one provider event inside the correct UFO workspace and records that the event position has been reached. If the event contains a message, it tries to admit that message into UFO.

**Data flow**: It receives the listener context, provider, installation ID, event sequence, and maybe an inbound message. It opens the installation workspace, admits the message if present, stores the cursor, and may delete a temporary confirmation receipt key.

**Call relations**: _catch_up and _consume_connected both call this for provider events. It delegates message-specific work to _admit_message and uses ScopedStore to persist the cursor and clean up confirmation state.

*Call graph*: calls 2 internal fn (workspace, _admit_message); called by 2 (_catch_up, _consume_connected); 1 external calls (__init__).


##### `ImessageSurface._admit_message`  (lines 267–309)

```
async def _admit_message(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage) -> str | None
```

**Purpose**: This decides whether an inbound iMessage should become a UFO conversation turn. It checks the sender’s member link, filters unwanted group chatter, saves attachments, and submits the message to the UFO conversation engine.

**Data flow**: It receives a surface context, provider, and inbound message. It finds or confirms the linked member, prepares audience and conversation information, downloads allowed attachments, wraps the text in UFO’s member-message format, and admits it with sender context and an idempotency key. It returns a confirmation receipt key only when the message completed linking and should not be admitted as normal chat.

**Call relations**: _process_event calls this whenever a provider event contains a message. It calls _linked_member first, may call _downloaded_files, uses queue_key to find the UFO conversation, and finally hands the message to SurfaceContext.admit.

*Call graph*: calls 6 internal fn (admit, ambient_reply_wanted, conversation_for, _downloaded_files, _linked_member, queue_key); called by 1 (_process_event); 7 external calls (__init__, __init__, sha256, conversation_audience, room_audience, fence_member_message, mint_marker).


##### `ImessageSurface._linked_member`  (lines 311–364)

```
async def _linked_member(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage) -> MemberLink | None
```

**Purpose**: This proves that an iMessage sender belongs to a UFO member before their messages are accepted. It also completes the “reply yes to connect” flow and sends the “Connected” confirmation back to the chat.

**Data flow**: It receives a surface context, provider, and inbound message. It checks existing linked-member data and pending claim records in scoped storage, validates that the confirmation reply is direct, from the right phone number, in the right conversation, and says “yes” with no attachments. If valid, it links the phone number, sends confirmation text and possibly a contact card, deletes temporary claim data, and returns the linked member ID plus a cleanup key.

**Call relations**: _admit_message calls this before accepting any inbound message. It uses phone_key to find pending claims, may call contact_card for the vCard attachment, calls the surface context to read or create the member link, and calls the provider to send confirmation messages.

*Call graph*: calls 6 internal fn (link_member_id, linked_member, send_attachment, send_text, contact_card, phone_key); called by 1 (_admit_message); 4 external calls (__init__, __init__, now, sha256).


##### `ImessageSurface._downloaded_files`  (lines 366–415)

```
async def _downloaded_files(self, ctx: SurfaceContext, provider: MessageProvider, conversation_id: UUID, attachments: tuple[MessageAttachment, ...]) -> str
```

**Purpose**: This downloads inbound iMessage attachments into the UFO workspace, while enforcing a size limit. It returns human-readable notes that are added to the admitted message so the assistant knows what files arrived or were skipped.

**Data flow**: It receives a surface context, provider, conversation ID, and attachments. For each attachment, it chooses a safe unique filename, skips files that are too large, downloads allowed files in chunks, saves completed files under the iMessage inbox directory, and records notes about delivered, too-large, or unavailable files. It returns those notes as text.

**Call relations**: _admit_message calls this when an inbound message has attachments. It uses the provider to download files, _attachment_content to pass bytes to the workspace writer, and inbox_name to avoid filename collisions.

*Call graph*: calls 4 internal fn (write_workspace_file, download_attachment, external_error, _attachment_content); called by 1 (_admit_message); 1 external calls (inbox_name).


##### `ImessageSurface._store_cursor`  (lines 417–422)

```
async def _store_cursor(self, context: SurfaceListenerContext, installation_id: str, cursor: int) -> None
```

**Purpose**: This saves the latest processed provider event position. It is what lets the listener resume later without rereading the whole stream.

**Data flow**: It receives a listener context, installation ID, and cursor number. It opens that installation’s workspace and writes the cursor into scoped storage if the workspace exists.

**Call relations**: _catch_up calls this after replaying missed events. ImessageSurface.listen and _consume_connected later rely on the stored cursor to know where to continue.

*Call graph*: calls 1 internal fn (workspace); called by 1 (_catch_up); 1 external calls (__init__).


##### `ImessageSurface._read_cursor`  (lines 424–431)

```
async def _read_cursor(self, context: SurfaceListenerContext, installation_id: str) -> int | None
```

**Purpose**: This reads the last saved provider event position for an iMessage installation. It ignores missing or invalid values so the stream can safely start from scratch when needed.

**Data flow**: It receives a listener context and installation ID. It opens the workspace, reads the stored cursor value, and returns it only if it is a non-negative integer; otherwise it returns None.

**Call relations**: ImessageSurface.listen calls this at startup and after disconnections. The value it returns is passed into _consume_connected or used to decide how to recover.

*Call graph*: calls 1 internal fn (workspace); called by 1 (listen); 1 external calls (__init__).


##### `ImessageSurface._clear_cursor`  (lines 433–436)

```
async def _clear_cursor(self, context: SurfaceListenerContext, installation_id: str) -> None
```

**Purpose**: This removes the saved provider cursor when the provider says the old cursor cannot be used anymore. Clearing it allows the next connection to restart from a valid point.

**Data flow**: It receives a listener context and installation ID. It opens the workspace and deletes the cursor entry from scoped storage if the workspace exists.

**Call relations**: ImessageSurface.listen calls this when a disconnection error means the stored cursor is invalid. After that, the next _consume_connected run starts without that cursor.

*Call graph*: calls 1 internal fn (workspace); called by 1 (listen); 1 external calls (__init__).


##### `ImessageSurface.post`  (lines 438–445)

```
async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: This sends the final reply for a completed UFO turn back to the original iMessage conversation. It is used when the assistant’s turn has ended and there is one terminal response to deliver.

**Data flow**: It receives a surface context and a writeback, which contains the target queue key, final text, requests, and artifacts. It decodes the iMessage conversation, builds the final message text, sends it through the provider, and returns the provider’s send reference.

**Call relations**: The surface framework calls this for terminal writebacks. It uses conversation_from_queue to find the chat and _terminal_text to format what should be sent before calling the provider.

*Call graph*: calls 2 internal fn (_terminal_text, conversation_from_queue).


##### `ImessageSurface.speak`  (lines 447–451)

```
async def speak(self, _ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: This sends a mid-turn reply to iMessage before the full UFO turn is finished. It is useful for quick progress updates or intermediate assistant messages.

**Data flow**: It receives a surface context placeholder and a mid-turn reply. It decodes the queue key to find the iMessage conversation, sends the reply text through the provider, and returns the provider’s send reference.

**Call relations**: The surface framework calls this when it has a MidTurnReply. It uses conversation_from_queue, then hands the text directly to the provider’s send_text method.

*Call graph*: calls 1 internal fn (conversation_from_queue).


##### `ImessageSurface.attach`  (lines 453–465)

```
async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None
```

**Purpose**: This uploads files produced by a UFO turn back into the iMessage chat, as long as each file fits iMessage’s configured attachment limit.

**Data flow**: It receives a surface context, a writeback with artifacts, and an unused reply reference. It decodes the target conversation, checks each artifact size, reads the bytes for allowed files, and sends each file as an iMessage attachment. Oversized files are skipped here because _terminal_text includes links for them.

**Call relations**: The surface framework calls this after or alongside a writeback that contains shared files. It uses conversation_from_queue to find the chat and _artifact_bytes to safely load each file before sending it through the provider.

*Call graph*: calls 2 internal fn (_artifact_bytes, conversation_from_queue).


##### `ImessageSurface._terminal_text`  (lines 467–489)

```
async def _terminal_text(self, ctx: SurfaceContext, writeback: Writeback, *, direct: bool) -> str
```

**Purpose**: This builds the final text message that should be sent to iMessage for a completed turn. It combines the assistant’s final text with questions, connection instructions, credential instructions, and links for files too large to upload.

**Data flow**: It receives the surface context, writeback, and whether the target is a direct chat. It gathers the terminal text, question text, optional connection or credential URLs, and large-artifact links, joins the non-empty parts with blank lines, and returns the finished message. If there is no text, it returns a status message instead.

**Call relations**: ImessageSurface.post calls this before sending the final iMessage. It calls _question_text for structured questions and asks the surface context for connect URLs, home URLs, and artifact links when needed.

*Call graph*: calls 4 internal fn (artifact_link, connect_url, home_url, _question_text); called by 1 (post).


##### `ImessageSurface._question_text`  (lines 491–502)

```
def _question_text(self, writeback: Writeback) -> str
```

**Purpose**: This turns a structured UFO question into plain text that can be sent over iMessage. It includes the question title, each prompt, available options, and any current answer.

**Data flow**: It receives a writeback. If there is no question, it returns an empty string. Otherwise it reads the question fields, builds readable lines, and returns them joined with newlines.

**Call relations**: _terminal_text calls this while composing the final iMessage text. Its output becomes one section of the message sent by post.

*Call graph*: called by 1 (_terminal_text).


##### `ImessageSurface._artifact_bytes`  (lines 504–514)

```
async def _artifact_bytes(self, ctx: SurfaceContext, blob_key: str, size_bytes: int) -> bytes
```

**Purpose**: This reads a stored UFO artifact into memory for iMessage upload, while checking that it is still the expected size and not over the provider limit. It prevents accidentally uploading a file that is too large or changed while being read.

**Data flow**: It receives a surface context, blob key, and expected size. It rejects oversized files immediately, streams the blob into a byte buffer, rejects it if it grows too large, verifies the final byte count, and returns the bytes.

**Call relations**: ImessageSurface.attach calls this for each artifact it plans to send. The returned bytes are passed to the provider’s send_attachment method.

*Call graph*: called by 1 (attach).


### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `request handling and turn delivery`

This file is the bridge between Slack and ufo’s core conversation system. Without it, Slack messages would not be trusted, mapped to the right workspace, admitted as member turns, or answered in the correct thread. It also prevents common Slack pitfalls: duplicate event delivery, replies landing in the wrong DM thread, oversized messages, unsafe file downloads, and Slack form submissions being treated more than once. The file first proves that an incoming request really came from Slack using Slack’s signing secret. It then identifies the installed workspace, the bot identity, the Slack channel or DM thread, and the member who spoke. If the agent was mentioned, or the message belongs to a thread where the agent is already participating, it admits the message into ufo. If an unmentioned thread reply might not need the agent, it asks an ambient-reply decision before creating a turn. While a turn runs, background followers keep Slack’s native “thinking” status fresh and post occasional progress messages for long waits. When the turn finishes, this file formats the final reply, optional question forms, connection buttons, accounting footer, and shared files. It also implements Slack install flows: OAuth for the deploy’s Slack app and a bring-your-own-app setup. In short, it is both the front desk and the mailroom for Slack: it checks IDs, routes messages, keeps people informed, and delivers the final package.

#### Function details

##### `_env_signing_secret`  (lines 227–231)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from the process environment. This is the fallback secret used when a workspace does not store its own Slack signing secret.

**Data flow**: It reads one environment variable. If it is set to a non-empty value, that value comes out; otherwise the result is None.

**Call relations**: _ctx_signing_secret and _auth_signing_secret call this when they cannot find a workspace-specific secret.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 234–241)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use after a workspace has already been selected. It prefers the workspace’s own stored credential and falls back to the deploy-wide environment secret.

**Data flow**: It receives a surface context, asks it for the Slack signing-secret credential, and if that slot is unset reads the environment fallback. It returns the secret string or None.

**Call relations**: ingest and interactive use this before trusting Slack event and form requests.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 244–252)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret while the system is still trying to decide which workspace a Slack request belongs to. It uses the shared authorization helper instead of a bound surface context.

**Data flow**: It receives an auth object and workspace id, tries to read that workspace’s Slack signing secret, falls back to the environment secret, and returns None if the workspace is unknown.

**Call relations**: resolve_workspace uses it after a Slack team id points to a possible workspace, but before the request is fully accepted.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 255–259)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client id for the deploy’s Slack app. It fails loudly if the deploy is not configured for OAuth install.

**Data flow**: It reads the SLACK_CLIENT_ID environment variable and returns it. If missing, it raises an error explaining that Slack authorization cannot work.

**Call relations**: slack_oauth_exchange uses it when exchanging Slack’s temporary authorization code for a bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 262–266)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret for the deploy’s Slack app. This secret is needed to complete an OAuth install.

**Data flow**: It reads the SLACK_CLIENT_SECRET environment variable and returns it. If missing, it raises an error explaining that Slack install cannot work.

**Call relations**: slack_oauth_exchange uses it together with the client id and authorization code.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 269–271)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should redirect to after an owner clicks Add to Slack. It keeps the callback path consistent across the install flow.

**Data flow**: It receives the public base URL for the deploy, trims any trailing slash, and appends the Slack surface OAuth path. The result is a full redirect URL.

**Call relations**: oauth_callback uses the same URL during the code exchange that was used when starting authorization.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 274–286)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the Add to Slack link. The link tells Slack which app, scopes, redirect URL, and sealed install state to use.

**Data flow**: It receives a client id, redirect URI, and state token, URL-encodes them with the required Slack bot scopes, and returns Slack’s authorization URL.

**Call relations**: This is the outward-facing partner to oauth_callback, which later receives the browser redirect from Slack.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 290–292)

```
def __init__(self, error: str)
```

**Purpose**: Creates an error that carries Slack identity-proving failure text in a predictable field. It is used when Slack cannot prove or return a usable bot identity.

**Data flow**: It receives an error string, stores it on the exception, and initializes the RuntimeError base class with the same text.

**Call relations**: SlackIdentityResolver._prove and slack_oauth_exchange raise this when Slack’s identity response is missing, malformed, or negative.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `bot_token_fingerprint`  (lines 309–310)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack bot token. This lets the system tell whether a stored identity belongs to the current token without storing or comparing the token itself.

**Data flow**: It receives the token text, hashes it with SHA-256, and returns the hexadecimal hash string.

**Call relations**: read_identity checks stored identity records with it, while install and proof paths write new records using it.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 313–325)

```
async def read_identity(blob: BlobStore, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack team and bot-user identity, but only if it matches the current bot token. This avoids using stale Slack ids after reinstalling or rotating a token.

**Data flow**: It checks whether the identity blob exists, parses it as a SlackIdentity, compares its token fingerprint to the provided token, and returns the identity or None.

**Call relations**: Identity lookup, self-user resolution, and manifest-app identity proof all call this before doing more work.

*Call graph*: calls 3 internal fn (exists, get, bot_token_fingerprint); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 328–334)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Finds the Slack bot user id for a workspace if the Slack token and identity record are available. This is a small identity helper for code that only needs to know the bot’s own Slack user.

**Data flow**: It reads the bot token credential, reads the matching identity blob, and returns the bot_user_id or None.

**Call relations**: It depends on read_identity and the identity context’s credential access rather than the full surface request path.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_identity`  (lines 337–345)

```
async def _identity(ctx: SurfaceContext) -> SlackIdentity | None
```

**Purpose**: Gets the installed Slack identity for the current workspace. When found, it also mirrors the bot user id into the extension store for hooks that cannot read blobs.

**Data flow**: It reads the bot token credential, validates the stored identity against that token, writes the bot user id mirror best-effort, and returns the identity or None.

**Call relations**: ingest, interactive, and reply mention mapping use this before acting as the Slack bot.

*Call graph*: calls 3 internal fn (credential, _mirror_self_user_id, read_identity); called by 3 (_reply_mention_ids, ingest, interactive).


##### `_mirror_self_user_id`  (lines 351–367)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the proven Slack bot user id into a lightweight extension store. This helps turn-time hooks recognize the bot even though they do not have access to the identity blob.

**Data flow**: It receives a workspace id and bot user id, skips work if this process already wrote the same value, otherwise writes it to the scoped store and updates a small in-memory cache.

**Call relations**: _identity and oauth_callback call this after they know the bot user id.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 380–386)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Returns the Slack identity for a bring-your-own-app install. It reuses an existing valid identity when possible, otherwise proves the token with Slack and stores the result.

**Data flow**: It reads the identity blob using the current bot token. If absent, it calls _prove, writes the new identity JSON to the blob store, and returns it.

**Call relations**: _run_identity_proof calls this in the background when an inbound event arrives before identity proof has been completed.

*Call graph*: calls 2 internal fn (_prove, read_identity).


##### `SlackIdentityResolver._prove`  (lines 388–413)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack which team and bot user a pasted bot token belongs to. This is the proof step for manifest-based Slack app setup.

**Data flow**: It sends auth.test to Slack using the bot token, checks that Slack returned ok, validates the team id and bot user id shapes, and returns a SlackIdentity with the token fingerprint.

**Call relations**: SlackIdentityResolver.resolve calls this only when no matching stored identity exists.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 419–432)

```
def _prove_identity_in_background(ctx: SurfaceContext) -> None
```

**Purpose**: Starts a one-per-workspace background task to prove Slack identity without blocking the current request. This helps recover when credentials were filled but slack_connect was not rerun.

**Data flow**: It checks an in-memory task map, creates an async task for _run_identity_proof if none is running, stores it, and attaches cleanup when it finishes.

**Call relations**: ingest and interactive use it when identity is missing but a request has otherwise reached the workspace.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 2 (ingest, interactive); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 428–430)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a completed identity-proof task from the in-memory task map. This allows later retries if proof failed or credentials changed.

**Data flow**: It receives the finished task and deletes the workspace entry only if the map still points to that same task.

**Call relations**: It is registered as the done callback by _prove_identity_in_background.


##### `_run_identity_proof`  (lines 435–442)

```
async def _run_identity_proof(ctx: SurfaceContext) -> None
```

**Purpose**: Performs the actual background identity proof for a Slack workspace. It logs failures instead of letting them break the request that spawned it.

**Data flow**: It reads the Slack bot token credential, constructs a SlackIdentityResolver, runs it, and logs SlackIdentityError or unexpected exceptions.

**Call relations**: _prove_identity_in_background creates this as the background task.

*Call graph*: calls 1 internal fn (credential); called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `signing_secret_fingerprint`  (lines 448–451)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack signing secret. The system stores this fingerprint to know which secret successfully verified Slack’s URL check.

**Data flow**: It receives the secret text, hashes it with SHA-256, and returns the hexadecimal hash.

**Call relations**: _mark_url_verified uses it before writing the URL verification marker.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 465–492)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s short-lived OAuth code for a real workspace bot token and bot identity. This is the critical step after an owner approves Add to Slack.

**Data flow**: It posts the code, client id, client secret, and redirect URI to Slack, checks Slack’s ok response, validates the token, team id, and bot user id, and returns a SlackInstall.

**Call relations**: oauth_callback calls it after validating the sealed install state.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `SlackConversationSearch.run`  (lines 579–593)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations visible to the bot and returns those matching a user query. It supports channels and DMs, including finding DMs by the people in them.

**Data flow**: It normalizes the query, lists conversations, resolves DM people labels, converts raw Slack rows into SlackConversation objects, filters by searchable text, and returns matches plus a truncation flag.

**Call relations**: It coordinates the helper methods _list, _people, and _conversation.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 595–614)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Reads a bounded number of pages from Slack’s conversations.list API. It protects the system from endlessly paging a huge or strange workspace.

**Data flow**: It repeatedly requests pages using _params, appends channel rows, follows _next_cursor, and returns the rows plus whether more pages remained.

**Call relations**: SlackConversationSearch.run calls it before resolving people and filtering matches.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 616–624)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one conversations.list page. It keeps paging and filtering settings in one place.

**Data flow**: It receives a cursor string, creates parameters for conversation types, archived exclusion, and page size, and includes the cursor only when present.

**Call relations**: SlackConversationSearch._list calls it for each Slack list request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 626–629)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a list response. If Slack does not provide a usable cursor, it treats the listing as complete.

**Data flow**: It reads response_metadata.next_cursor from the payload and returns it if it is a string, otherwise an empty string.

**Call relations**: SlackConversationSearch._list uses it after each page.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 631–659)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Builds readable people labels for DM and group-DM conversations. This lets searches like “DM with Alice” work even though Slack DMs do not have channel names.

**Data flow**: It selects a bounded number of DM conversations, gets member ids, looks up each user once, turns each user into a label, and returns a conversation-id-to-labels map plus whether it capped the work.

**Call relations**: SlackConversationSearch.run uses this data while turning raw rows into searchable conversations.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 661–668)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation row as a public channel, private channel, group DM, or one-to-one DM. This simplifies later search decisions.

**Data flow**: It reads Slack boolean flags from the row and returns one of the supported kind strings.

**Call relations**: _people, _members, and _conversation all use this shared classification.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 670–684)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets member ids for a DM-like conversation. A one-to-one DM can read the user directly; a group DM needs Slack’s members API.

**Data flow**: It receives a raw conversation row and id, returns the single user for an IM, or requests members from Slack for an MPIM and returns valid string ids.

**Call relations**: SlackConversationSearch._people calls it before resolving user labels.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 686–691)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Chooses a readable label for a Slack user in search results. It prefers a name plus email when available.

**Data flow**: It receives an optional SlackUser and fallback user id, then returns name/email, name, email, or the raw id.

**Call relations**: SlackConversationSearch._people uses it after _slack_user returns user details.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 693–710)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Turns one raw Slack conversation row into the smaller SlackConversation model used by search. Invalid rows are dropped.

**Data flow**: It checks that the row is a dict with an id, reads name, purpose, topic, kind, people labels, and membership, then returns a SlackConversation or None.

**Call relations**: SlackConversationSearch.run calls it for every listed row before applying the query.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 712–714)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely reads Slack’s nested purpose or topic text. Slack wraps these fields in objects, so this avoids assuming the shape is always correct.

**Data flow**: It receives a field object, returns field.value if it is a string, otherwise an empty string.

**Call relations**: SlackConversationSearch._conversation uses it for purpose and topic.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 891–906)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a request really came from Slack and is recent enough not to be a replay. This is the main safety gate for inbound Slack traffic.

**Data flow**: It reads Slack signature headers, checks the timestamp, recomputes the HMAC signature from the secret and raw body, and raises SlackSignatureError if anything does not match.

**Call relations**: resolve_workspace, ingest, and interactive call it before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 913–932)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw request body while enforcing a maximum size. The raw bytes are needed for Slack signature verification.

**Data flow**: It checks request state for a cached body or overflow marker, streams chunks from the request, stops if the size limit is exceeded, caches the bytes, and returns them.

**Call relations**: resolve_workspace, ingest, and interactive all use it before parsing or verifying Slack requests.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 935–944)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Recognizes Slack’s URL verification handshake and extracts the challenge text Slack expects back. This lets Slack confirm the endpoint during app setup.

**Data flow**: It parses JSON bytes, checks for type url_verification, and returns the challenge string, an empty challenge, or None for non-handshake bodies.

**Call relations**: resolve_workspace and ingest use it to answer Slack setup probes.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 947–964)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Pulls an untrusted Slack team id from a request body so the system can look up the possible workspace. It supports both event JSON and form-encoded interactivity payloads.

**Data flow**: It tries JSON first, then form payload JSON, reads team_id or team.id, validates the id shape, and returns it or None.

**Call relations**: resolve_workspace uses this hint before verifying the request with the workspace’s secret.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 967–968)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Builds the stable installation key used to bind a Slack team to a ufo workspace. It namespaces the team id as a Slack team.

**Data flow**: It receives a Slack team id and returns a string like team:T123.

**Call relations**: resolve_workspace uses this to look up bindings, and oauth_callback uses it when creating a binding.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 971–1009)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace a Slack route request belongs to. It handles OAuth callbacks, Slack URL verification probes, and signed event or interaction posts.

**Data flow**: It inspects the HTTP method and body, may open sealed OAuth state, may return a challenge response, may map Slack team id to workspace, verifies the signature with that workspace’s secret, and returns a workspace id, response, or None.

**Call relations**: This is the pre-binding resolver that runs before request handling reaches workspace-specific routes such as ingest or interactive.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 1012–1015)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state belongs to the Slack OAuth install flow. This prevents unrelated credential links from being accepted as Slack installs.

**Data flow**: It reads the state payload and requested credential slots and returns true only for the Slack install marker and bot-token slot.

**Call relations**: resolve_workspace and oauth_callback both use it when interpreting sealed install state.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 1018–1023)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Creates ufo’s conversation key for a Slack message. Channel conversations are keyed by channel plus thread root, while DMs are keyed by the DM channel.

**Data flow**: It receives channel id, root timestamp, and whether this is a DM. It returns the channel alone for DMs or channel:root_ts for channel threads.

**Call relations**: _to_inbound and _to_interaction use it to attach messages and form answers to the right conversation.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `slack_message_addressed`  (lines 1026–1044)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly addressed to the agent. DMs always count; channel messages count when they mention the bot in the message body rather than only in the bot’s own footer.

**Data flow**: It receives the Slack event, bot user id, and DM flag, reads all message bodies, checks addressing mentions, and returns true or false.

**Call relations**: _to_inbound uses this to decide whether a message can start or join a turn directly.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1047–1050)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts links in reply text so Slack link previews can be controlled. Too many previews can bury the actual answer.

**Data flow**: It counts Markdown-style links, removes them, then counts remaining bare URLs and returns the total.

**Call relations**: slack_reply_body uses this count to disable link unfurling when a reply contains many links.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `slack_reply_parts`  (lines 1053–1130)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long Slack replies into pieces that fit Slack’s message limits while trying not to break Markdown awkwardly. It prefers paragraph, line, sentence, and word boundaries.

**Data flow**: It receives reply text and a character limit, detects code fences and tables as atomic spans, chooses safe cut points until all text fits, and returns a list of parts.

**Call relations**: post, speak, and slack_reply_body use it before sending long text to Slack.

*Call graph*: called by 3 (post, slack_reply_body, speak); 2 external calls (finditer, match).


##### `slack_reply_body`  (lines 1133–1200)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for a Slack chat.postMessage call. It can include Markdown blocks, fallback text, buttons or forms, metadata, thread placement, and footer text.

**Data flow**: It receives channel, optional thread, text, optional metadata, delivery id, block options, and actions, then returns encoded JSON bytes within Slack’s size limits or raises if too large.

**Call relations**: post, speak, and ThreadProgress._say use it for actual Slack messages.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 3 (_say, post, speak); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1203–1204)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a Slack Block Kit section block containing Markdown-style text. It also trims to Slack’s section text limit.

**Data flow**: It receives text and returns a dict with type section and mrkdwn text.

**Call relations**: slack_ask_blocks and _ask_prose use it when rendering questions.

*Call graph*: called by 2 (_ask_prose, slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1207–1251)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders a terminal ask_user question as Slack blocks. When possible it creates an interactive form; otherwise it falls back to prose instructions.

**Data flow**: It receives an optional AskUserInput, builds controls for each question, adds a title and submit button if all controls are expressible, or returns prose blocks if not.

**Call relations**: post includes these blocks on the final reply when the turn ends by asking the user something.

*Call graph*: calls 3 internal fn (_ask_control, _ask_prose, _mrkdwn_section); called by 1 (post).


##### `_ask_control`  (lines 1254–1306)

```
def _ask_control(index: int, ask: AskQuestion) -> dict[str, object] | None
```

**Purpose**: Builds one Slack input block for one question. It chooses radio buttons, checkboxes, or a text box depending on the question.

**Data flow**: It receives the question index and AskQuestion, checks Slack limits and attachment support, builds the right input element with initial values or hints, and returns a block or None.

**Call relations**: slack_ask_blocks calls it for each question and switches to prose if any question cannot be represented.

*Call graph*: calls 1 internal fn (_ask_option); called by 1 (slack_ask_blocks).


##### `_ask_option`  (lines 1309–1319)

```
def _ask_option(option: QuestionOption) -> dict[str, object]
```

**Purpose**: Turns one answer option into Slack’s option format. It preserves the option label as the submitted value.

**Data flow**: It receives a QuestionOption, creates text and value fields, adds a trimmed description if present, and returns the option dict.

**Call relations**: _ask_control uses it when building radio-button and checkbox choices.

*Call graph*: called by 1 (_ask_control).


##### `_ask_prose`  (lines 1322–1330)

```
def _ask_prose(ask: AskQuestion) -> dict[str, object]
```

**Purpose**: Renders a question as plain Slack Markdown text when an interactive control is not suitable. This keeps the question answerable by replying in the thread.

**Data flow**: It receives an AskQuestion, builds lines for the header, question, options, and multi-select hint, and wraps them in a Markdown section block.

**Call relations**: slack_ask_blocks uses it for fallback form rendering.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (slack_ask_blocks).


##### `slack_connect_blocks`  (lines 1333–1354)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a Slack button for a terminal connection request. The button lets a member open a private authorization link instead of passing secrets through chat.

**Data flow**: It receives an optional ConnectRequest and turn id, returns None if there is no request, otherwise builds one action block with a button carrying the turn id.

**Call relations**: post includes these blocks when the final reply asks the member to connect an external provider.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1357–1361)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required string field from a Slack event or payload. It gives a clear error when Slack data is missing or malformed.

**Data flow**: It receives a mapping and field name, returns the non-empty string value, or raises ValueError.

**Call relations**: _to_inbound and _to_interaction use it for essential ids and timestamps.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `_inbound_files`  (lines 1364–1376)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable file references from a Slack message event. It ignores hidden or tombstoned files and caps how many files are considered.

**Data flow**: It reads the event’s files list, keeps valid name and private download URL pairs, creates InboundFile records, and returns them as a tuple.

**Call relations**: _to_inbound reads direct event files with it, and _declared_files reuses it after fetching a message from Slack.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1379–1410)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Fetches file attachments Slack did not include directly in an app_mention event. This repairs a Slack event shape where files may need a separate thread read.

**Data flow**: It requests the exact message from conversations.replies, searches for the matching timestamp, extracts files with _inbound_files, and returns an empty tuple on failure.

**Call relations**: _to_inbound calls it only when a mention event might have omitted file details.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1413–1458)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the Add to Slack OAuth install flow. It validates the sealed install link, exchanges Slack’s code, stores the bot token, binds the Slack team to the workspace, and records identity.

**Data flow**: It reads query parameters, validates state and workspace, exchanges the code, binds the team installation, fulfills the bot-token credential request, writes the identity blob, mirrors the bot id, and returns an HTML success or error page.

**Call relations**: This is the browser callback counterpart to slack_authorize_url and the setup path for OAuth-installed workspaces.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _install_page, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 1 external calls (__init__).


##### `_install_page`  (lines 1461–1468)

```
def _install_page(message: str, status: int) -> Response
```

**Purpose**: Builds a small HTML response for Slack install outcomes. It gives the owner a readable success or failure message.

**Data flow**: It receives message text and HTTP status, escapes the message, wraps it in minimal HTML, and returns a Response.

**Call relations**: oauth_callback uses it for every visible install result.

*Call graph*: called by 1 (oauth_callback); 2 external calls (escape, Response).


##### `_mark_url_verified`  (lines 1474–1488)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached and signed a request for this workspace. This helps manifest-app setup know the request URL is live for the current signing secret.

**Data flow**: It fingerprints the signing secret, skips if already written in this process, writes a JSON marker with fingerprint and time to the blob store, and caches the write.

**Call relations**: ingest and interactive call it after a signed Slack request verifies.

*Call graph*: calls 1 internal fn (signing_secret_fingerprint); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1491–1536)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Events API requests. It verifies Slack, filters irrelevant events, converts member messages into ufo turns, and starts ambient decisions when needed.

**Data flow**: It reads the raw body, finds a signing secret, verifies the signature or answers URL verification, loads identity, converts the payload to Inbound, admits direct or live-folding messages, or starts a background ambient decision, then returns Slack an acknowledgement.

**Call relations**: This is the main inbound event route and it drives _to_inbound, _admit_inbound, _folds_into_live_turn, and _decide_ambient_in_background.

*Call graph*: calls 12 internal fn (credential, _admit_inbound, _ctx_signing_secret, _decide_ambient_in_background, _folds_into_live_turn, _identity, _mark_url_verified, _prove_identity_in_background, _slack_request_body, _to_inbound (+2 more)); 3 external calls (loads, JSONResponse, Response).


##### `_folds_into_live_turn`  (lines 1539–1578)

```
async def _folds_into_live_turn(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> bool
```

**Purpose**: Checks whether an unmentioned Slack reply should be absorbed by an already running turn instead of being judged by the ambient-reply classifier. This protects corrections or stop messages sent while the agent is working.

**Data flow**: It receives an inbound message, checks for an absorbing turn, resolves the sender to a member, verifies the member has a seat, logs the skipped ambient gate, and returns true only when admission can really fold the message into the live turn.

**Call relations**: ingest calls this before deciding whether to admit immediately or run the ambient decision.

*Call graph*: calls 4 internal fn (absorbing_turn, transaction, _resolve_member, _slack_user); called by 1 (ingest); 2 external calls (__init__, log).


##### `_admit_inbound`  (lines 1581–1631)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Turns a vetted Slack message into a ufo conversation turn. It gathers sender details, context, files, conversation mapping, and thread mirrors before admitting the message.

**Data flow**: It receives context, token, inbound data, and identity, fetches Slack user, permalink, ambient context, and mention names, resolves the member and audience, creates or finds the conversation, mirrors the Slack thread, downloads files, fences the message body, admits it, anchors DM replies, and arms status followers if a run opened.

**Call relations**: ingest calls it for direct admissions, and _run_ambient_decision calls it when the model says an ambient reply is wanted.

*Call graph*: calls 13 internal fn (admit, conversation_for, retitle_conversation, _ambient_context, _anchor_dm_thread, _arm_followers, _download_files, _mirror_thread, _resolve_member, _slack_permalink (+3 more)); called by 2 (_run_ambient_decision, ingest); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1637–1662)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts a background task to decide whether an unmentioned thread reply should create a turn. It avoids making Slack wait for a model decision before acknowledging the event.

**Data flow**: It keys the task by message id, skips if already running, creates _run_ambient_decision, stores the task, and removes it when done.

**Call relations**: ingest calls it after acknowledging ambient traffic that may or may not need the agent.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1658–1660)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a completed ambient-decision task from the in-memory task map. This keeps the map from growing forever.

**Data flow**: It receives a completed task and deletes the message entry only if that task is still the current one.

**Call relations**: It is registered by _decide_ambient_in_background as the task done callback.


##### `_run_ambient_decision`  (lines 1665–1680)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the ambient-reply decision and admits the message only if the decision says the agent should answer. It logs failures because Slack has already been acknowledged.

**Data flow**: It calls _ambient_reply_wanted, then _admit_inbound when true, and logs any exception with the queue key and timestamp.

**Call relations**: _decide_ambient_in_background creates this task after ingest returns success to Slack.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1683–1690)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages written by users from another Slack organization in a shared channel. Those authors are skipped because this app serves the installed workspace’s members.

**Data flow**: It compares source_team or user_team on the event to the installed team id and returns true only when a different team is named.

**Call relations**: _to_inbound uses it early to drop foreign Slack Connect authors.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1706–1742)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines the audience and readable label for the Slack place a message came from. This controls who may see the conversation inside ufo.

**Data flow**: It receives request and event data, channel id, and whether an audience is already known, uses event hints or conversations.info, and returns a ChannelOrigin for DM, private room, public channel, or shared channel.

**Call relations**: _to_inbound calls it while building the Inbound object.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1745–1795)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Converts a raw Slack event payload into the smaller Inbound record used by admission. It filters bot messages, unsupported subtypes, unaddressed top-level chatter, and foreign authors.

**Data flow**: It reads event fields, determines DM status and addressed status, builds the queue key and message id, checks participating conversations, resolves channel origin and files, and returns Inbound or None.

**Call relations**: ingest calls it after signature and identity checks, before deciding admission.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1798–1809)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has an admitted ufo turn. A bare conversation row is not enough; the transcript must have begun.

**Data flow**: It looks up the conversation by queue key, then checks for a latest turn, and returns the conversation id only when both exist.

**Call relations**: _to_inbound uses this to decide whether unmentioned thread replies can be considered part of an active agent conversation.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1812–1844)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches basic Slack user information such as name, confirmed email, timezone, and team id. It is best-effort so Slack lookup failures do not usually block message admission.

**Data flow**: It calls users.info with the bot token, validates the response, ignores unconfirmed email addresses, and returns a SlackUser or None.

**Call relations**: It feeds member resolution, turn context, conversation search labels, name caching, and interaction handling.

*Call graph*: calls 1 internal fn (_slack_ok); called by 5 (_people, _name, _admit_inbound, _folds_into_live_turn, interactive); 2 external calls (__init__, AsyncClient).


##### `_conversation_members`  (lines 1858–1878)

```
async def _conversation_members(bot_token: str, channel: str) -> tuple[str, ...]
```

**Purpose**: Reads a bounded roster of members in a Slack conversation. This is used to safely map outbound @names only to people already in the Slack thread.

**Data flow**: It calls conversations.members with a fixed limit, returns string member ids, or returns an empty tuple on failure.

**Call relations**: SlackNames.mention_ids uses it before resolving names to notification ids.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (mention_ids); 1 external calls (AsyncClient).


##### `SlackNames.of`  (lines 1899–1907)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack user and channel ids mentioned in text into readable names. This makes inbound messages and ambient context understandable to the agent.

**Data flow**: It scans texts for mentioned users and channels, adds explicit user ids, asks _resolved for cached or fetched names, and returns an id-to-name map.

**Call relations**: _admit_inbound and _digest_names use this through SlackNames to render Slack markup.

*Call graph*: calls 1 internal fn (_resolved); 2 external calls (mentioned_channels, mentioned_users).


##### `SlackNames.mention_ids`  (lines 1909–1923)

```
async def mention_ids(self, channel: str, identity: SlackIdentity) -> dict[str, str]
```

**Purpose**: Builds the safe map from names in an agent reply to Slack mention ids that notify people. It only considers current conversation members from the installed team.

**Data flow**: It reads the channel roster, resolves member names, drops the bot and foreign-team users, and returns a mention index keyed by name-like forms.

**Call relations**: _reply_mention_ids calls it before outbound reply text is converted to Slack mention markup.

*Call graph*: calls 2 internal fn (_resolved, _conversation_members); 1 external calls (mention_index).


##### `SlackNames._resolved`  (lines 1925–1933)

```
async def _resolved(self, wanted: Mapping[str, str]) -> dict[str, _NamedId]
```

**Purpose**: Combines cached Slack names with a bounded set of fresh Slack lookups. This keeps mention rendering fast and avoids too many API calls.

**Data flow**: It reads remembered names, chooses missing ids up to a limit, gathers fresh _name lookups, stores fetched names, and returns known plus fetched entries.

**Call relations**: SlackNames.of and SlackNames.mention_ids both rely on this cache-and-fetch layer.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); called by 2 (mention_ids, of); 1 external calls (gather).


##### `SlackNames._remembered`  (lines 1935–1956)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, _NamedId]
```

**Purpose**: Reads fresh-enough cached names from the extension store. Old or malformed cache rows are ignored.

**Data flow**: It receives ids, reads their cache keys in bulk, checks name, timestamp, and team fields, applies the TTL cutoff, and returns valid _NamedId entries.

**Call relations**: SlackNames._resolved calls it before deciding which ids still need Slack API requests.

*Call graph*: called by 1 (_resolved); 3 external calls (__init__, __init__, now).


##### `SlackNames._name`  (lines 1958–1975)

```
async def _name(self, id_: str, url: str) -> _NamedId | None
```

**Purpose**: Fetches and cleans the readable name for one Slack user or channel id. It also records the user’s Slack team when available.

**Data flow**: It calls _slack_user for user ids or _channel_info for channel ids, takes the returned name, removes Slack delimiter characters, compresses whitespace, trims length, and returns _NamedId or None.

**Call relations**: SlackNames._resolved gathers this for cache misses.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (_resolved); 1 external calls (__init__).


##### `SlackNames._remember`  (lines 1977–1987)

```
async def _remember(self, names: Mapping[str, _NamedId]) -> None
```

**Purpose**: Writes newly resolved Slack names into the extension store. Each row includes the time and team so later reads can decide freshness and safety.

**Data flow**: It receives an id-to-_NamedId map, gets the current timestamp, and writes each cache row best-effort.

**Call relations**: SlackNames._resolved calls it after successful fresh lookups.

*Call graph*: called by 1 (_resolved); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 1990–2010)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Fetches Slack’s own permalink for a message. This gives the ufo turn a reliable source link instead of trying to build one by hand.

**Data flow**: It calls chat.getPermalink with channel and timestamp, returns the permalink string if present, or None on failure.

**Call relations**: _admit_inbound and interactive use it when building turn context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, interactive); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 2013–2030)

```
def _turn_context(sender: SlackUser | None, source: str | None, question: str | None=None) -> TurnContext
```

**Purpose**: Builds the context object attached to an admitted turn, including sender name, timezone, source link, and answered question when available.

**Data flow**: It receives optional SlackUser, source URL, and question text, formats the sender label, validates timezone through TurnContext, and falls back without timezone if invalid.

**Call relations**: _admit_inbound uses it for messages, and interactive uses it for submitted answers.

*Call graph*: called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_resolve_member`  (lines 2033–2051)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member. It first checks an existing link, then may join a same-domain teammate using Slack-confirmed email.

**Data flow**: It receives context, Slack user id, DM flag, and optional SlackUser, checks linked_member, uses confirmed email through join_member when possible, returns a member id or None, and raises for unresolved DMs when user lookup failed.

**Call relations**: _admit_inbound, _folds_into_live_turn, and interactive use it before admitting a member-authored turn.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 3 (_admit_inbound, _folds_into_live_turn, interactive); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 2054–2079)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether an unmentioned thread reply should create an agent turn. This avoids the agent interrupting members who are just talking to each other.

**Data flow**: It fetches recent ambient history, admits by default if history is unavailable, builds the current AmbientMessage, calls the core decision method, logs no-reply decisions, and returns the boolean.

**Call relations**: _run_ambient_decision calls this before admitting an ambient inbound.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 2082–2104)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Fetches the recent Slack thread messages used by the ambient-reply decision. It keeps both member messages and the agent’s own messages, because the decision needs to know whether the agent already answered.

**Data flow**: It reads the thread tail before the inbound timestamp, converts valid entries with _ambient_entry, sorts by timestamp, keeps the newest bounded set, and returns AmbientMessage objects.

**Call relations**: _ambient_reply_wanted calls it before asking core’s ambient decision.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 2107–2153)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Reads messages from a Slack thread before a given timestamp, walking pages so it reaches the tail rather than only the root of a long thread. It returns None when the read cannot be trusted.

**Data flow**: It calls conversations.replies page by page with latest set to the inbound timestamp, accumulates messages, stops on no cursor, and returns None if requests fail or the page cap is exceeded.

**Call relations**: _ambient_history and _unseen_tail use it for thread context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 2156–2176)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Turns one raw Slack message into an ambient-history entry if it is useful for the reply decision. It drops malformed, empty, future, and other-bot messages.

**Data flow**: It reads user, timestamp, text, and bot flags, marks whether the speaker is the agent, parses the timestamp, and returns a sortable pair or None.

**Call relations**: _ambient_history applies it to each item fetched by _thread_tail.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 2179–2234)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds a digest of Slack messages the agent should know about but that are not already in the ufo transcript. This fills gaps such as earlier thread discussion before a mention.

**Data flow**: It decides which Slack history to fetch based on DM, new thread, existing conversation, or channel context, gets messages, resolves names, and returns an ambient_digest string or an empty string.

**Call relations**: _admit_inbound includes this digest inside the fenced member message before admission.

*Call graph*: calls 4 internal fn (_digest_names, _slack_ok, _unseen_tail, ambient_digest); called by 1 (_admit_inbound); 1 external calls (AsyncClient).


##### `_digest_names`  (lines 2237–2246)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves names for authors and mentioned ids inside a set of Slack messages. This makes ambient digests readable.

**Data flow**: It extracts message text and author ids from raw Slack messages, asks SlackNames.of for names, and returns the id-to-name map.

**Call relations**: _ambient_context and _unseen_tail call it before rendering ambient_digest.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2249–2295)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Finds recent thread messages that were seen in Slack but never admitted as ufo turns. These are usually ambient replies the agent was told not to answer.

**Data flow**: It fetches the thread tail, sorts messages by timestamp, walks backward until it finds an admitted body or reaches a limit, then renders the unseen messages as an ambient digest.

**Call relations**: _ambient_context uses it for messages in an already participating Slack thread.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2298–2366)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders Slack messages as safe, bounded background context for the agent. It keeps bystander words separated from the speaking member’s actual request.

**Data flow**: It filters raw Slack messages, drops bot posts and messages that directly addressed the bot, formats timestamp, speaker, and rendered text, trims long digests while preserving the root and newest lines, and wraps the result in a marked context element.

**Call relations**: _ambient_context and _unseen_tail call it after fetching Slack history and resolving names.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2369–2371)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks whether a file download URL belongs to Slack. This prevents sending the bot token to an attacker-controlled host.

**Data flow**: It parses the URL hostname and returns true only for slack.com or a slack.com subdomain.

**Call relations**: _stream_download calls it before making an authenticated download request.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2374–2392)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private Slack file download into ufo without loading the whole file into memory. It also enforces a size limit and host safety check.

**Data flow**: It receives a bot token and URL, verifies the host, streams bytes from Slack with authorization, counts total bytes, raises if the file is too large, and yields chunks.

**Call relations**: _download_files passes this stream directly to the workspace file writer.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2404–2420)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Saves attached Slack files into the ufo workspace before the turn runs. Oversized files are skipped and reported instead of partially written.

**Data flow**: It receives inbound file records, creates unique inbox names, writes each stream to workspace storage, collects delivered and skipped names, and returns DownloadedFiles.

**Call relations**: _admit_inbound calls it when a Slack message includes files.

*Call graph*: calls 2 internal fn (write_workspace_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2423–2432)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Builds a short note telling the agent which Slack files were saved and which were too large. This note becomes part of the admitted message.

**Data flow**: It receives DownloadedFiles, formats workspace paths for delivered files and original names for skipped files, and returns joined text.

**Call relations**: _admit_inbound appends this note to the fenced member message after downloading attachments.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2444–2449)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored Slack thread mirror into a MirroredThread object. It supports both old string-only rows and newer structured rows.

**Data flow**: It receives stored JSON-like data, treats a plain string as a queue key, otherwise validates the structured row, and returns MirroredThread.

**Call relations**: follow_turn uses it when arming followers from a hook context.


##### `MirroredThread.anchor`  (lines 2451–2455)

```
def anchor(self) -> str | None
```

**Purpose**: Finds the Slack message timestamp that status and progress posts should attach to. In channels this is the thread root; in DMs it may be the member message timestamp.

**Data flow**: It reads the root timestamp from queue_key, falls back to message_ts, and returns a timestamp or None.

**Call relations**: _track_status and ThreadProgress use this to decide where Slack feedback should appear.

*Call graph*: called by 1 (_track_status).


##### `_thread_mirror_key`  (lines 2458–2459)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the extension-store key for a conversation’s Slack thread mirror.

**Data flow**: It receives a conversation id and returns the string key under the Slack thread prefix.

**Call relations**: _mirror_thread writes this key, and follow_turn reads it.

*Call graph*: called by 2 (_mirror_thread, follow_turn).


##### `_mirror_thread`  (lines 2462–2470)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Stores the Slack thread that belongs to a ufo conversation. This lets later turn hooks post status and progress even though they no longer have the original Slack request.

**Data flow**: It receives a conversation id and MirroredThread, serializes the thread, and writes it to the Slack scoped store.

**Call relations**: _admit_inbound and interactive call it before admitting a turn.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, interactive); 2 external calls (__init__, model_dump).


##### `_dm_anchor_key`  (lines 2473–2479)

```
def _dm_anchor_key(turn_id: UUID, message_ref: UUID | None=None) -> str
```

**Purpose**: Builds the store key that links a DM turn or absorbed DM message to the Slack message it should answer under. DMs need this because their queue key is only the channel.

**Data flow**: It receives a turn id and optional message ref, includes the ref only when it names an absorbed message, and returns the store key.

**Call relations**: _anchor_dm_thread writes these keys, _reply_thread reads them, and attach deletes them after delivery is durable.

*Call graph*: called by 3 (_anchor_dm_thread, _reply_thread, attach).


##### `_anchor_dm_thread`  (lines 2482–2489)

```
async def _anchor_dm_thread(admitted: Admitted, message_ts: str) -> None
```

**Purpose**: Records which Slack DM message a ufo turn or absorbed arrival should reply under. This keeps answers in the member’s DM thread instead of the DM top level.

**Data flow**: It receives the admission result and Slack message timestamp, computes the DM anchor key, and stores the timestamp.

**Call relations**: _admit_inbound and interactive call it after admitting DM messages or form answers.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_reply_thread`  (lines 2492–2507)

```
async def _reply_thread(queue_key: str, turn_id: UUID, message_ref: UUID | None=None) -> str | None
```

**Purpose**: Finds the Slack thread timestamp for a reply. Channels use the root in the queue key; DMs use the stored anchor for the turn or message ref.

**Data flow**: It receives queue key, turn id, and optional message ref, returns the channel root if present, otherwise reads DM anchor keys and returns the timestamp or None.

**Call relations**: post, speak, and attach call it before placing Slack messages or files.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 3 (attach, post, speak); 1 external calls (__init__).


##### `FollowerContext.workspace_id`  (lines 2518–2518)

```
def workspace_id(self) -> UUID
```

**Purpose**: Defines that a follower context must expose the current workspace id. Followers need this to key status ownership and footer links.

**Data flow**: An implementing context returns a UUID for the workspace.

**Call relations**: ThreadStatus, ThreadProgress, and _slack_footer rely on this protocol property through concrete contexts.


##### `FollowerContext.public_base_url`  (lines 2521–2521)

```
def public_base_url(self) -> str | None
```

**Purpose**: Defines that a follower context can provide the deploy’s public URL when available. This enables web and debug links in Slack footers.

**Data flow**: An implementing context returns a URL string or None.

**Call relations**: _slack_footer reads it through either SurfaceContext or _HookFollowerContext.


##### `FollowerContext.credential`  (lines 2523–2523)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Defines how followers read credentials such as the Slack bot token. It abstracts over surface requests and hook executions.

**Data flow**: An implementing context receives a slot name and returns the credential string asynchronously.

**Call relations**: ThreadStatus, ThreadProgress, and helpers call it without caring whether they are running from an inbound request or a hook.


##### `FollowerContext.tail`  (lines 2525–2527)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how followers read live frames from a running turn. A live frame is a stream event such as a tool call, text delta, or terminal signal.

**Data flow**: An implementing context receives a turn id and cursor and returns an async context manager that yields cursor/frame pairs.

**Call relations**: ThreadStatus and ThreadProgress use this stream to update Slack while the turn runs.


##### `FollowerContext.turn_is_terminal`  (lines 2529–2529)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Defines how a follower can check whether a turn has already ended durably. This prevents progress posts from appearing after the final answer.

**Data flow**: An implementing context receives a turn id and returns true or false asynchronously.

**Call relations**: ThreadProgress._follow uses it when a checkpoint timer fires without receiving a live frame.


##### `FollowerContext.conversation_agent`  (lines 2531–2531)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Defines how a follower finds the agent associated with a conversation. Footers need the agent id to link to configuration.

**Data flow**: An implementing context receives a conversation id and returns the agent id or None.

**Call relations**: ThreadProgress._footer calls this before building its first progress footer.


##### `FollowerContext.is_operator_workspace`  (lines 2533–2533)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Defines how a follower knows whether it is in the operator workspace. Operator-only footers can show extra accounting and debug links.

**Data flow**: An implementing context returns a boolean asynchronously.

**Call relations**: _slack_footer calls it before deciding whether to include internal debug information.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2590–2591)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Returns the unique key for the Slack thread whose native status is being written. It includes workspace, channel, and thread timestamp.

**Data flow**: It reads the status object’s context workspace id, channel, and thread_ts and returns them as a tuple.

**Call relations**: _track_status, _run_status, _restamp_thread_status, and ThreadStatus._clear use this identity to coordinate writers.


##### `ThreadStatus.run`  (lines 2593–2612)

```
async def run(self) -> None
```

**Purpose**: Runs the live Slack status follower for one turn. It writes an initial “Thinking…” line, follows turn frames, and clears the status when appropriate.

**Data flow**: It reads the bot token, opens an HTTP client, sets the initial status, follows frames with _follow, handles cancellation or failure, and calls _clear at the end.

**Call relations**: _run_status wraps this method in a background task.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2614–2654)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one Slack assistant thread status line. It only writes if this turn is currently the chosen writer for the thread.

**Data flow**: It receives an HTTP client, bot token, and status text, builds Slack’s setStatus body, posts it, logs success or failure, and returns whether Slack accepted it.

**Call relations**: ThreadStatus.run, _follow, and _clear use it for all status writes.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2656–2665)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears Slack’s thread status when this turn ends, but only if no sibling turn in the same thread is still running. This avoids erasing another turn’s live status.

**Data flow**: It scans live statuses for the same thread, returns if another exists, otherwise writes an empty status with _set.

**Call relations**: ThreadStatus.run calls it after following finishes or after a contained failure.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2667–2727)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Reads live turn frames and translates them into short Slack status lines. It also refreshes the line before Slack’s timeout and restamps after progress posts blank it.

**Data flow**: It opens the turn tail, waits for frames, blank events, or refresh timeouts, maps tool, skill, absorbed, resumed, and text frames to status text, rate-limits updates, and exits on terminal or parked frames.

**Call relations**: ThreadStatus.run calls it after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2737–2744)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers for a Slack thread so they immediately write their current line again. This is needed because posting a message in the thread clears Slack’s native status.

**Data flow**: It receives workspace, channel, and thread timestamp, finds matching live ThreadStatus objects, and sets their blanked event.

**Call relations**: ThreadProgress._say calls it after a progress message lands in the same thread.

*Call graph*: called by 1 (_say).


##### `_track_status`  (lines 2747–2768)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts one status follower task for a turn in this process. It also marks the newest turn as the writer for the Slack thread.

**Data flow**: It receives context, turn id, and mirrored thread, skips if already tracked, finds the channel and anchor timestamp, creates ThreadStatus, updates writer maps, starts _run_status, and stores the task.

**Call relations**: _arm_followers calls it for both immediate admissions and turn-execution hooks.

*Call graph*: calls 2 internal fn (anchor, _run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 2771–2799)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Supervises a ThreadStatus task and cleans up status ownership when it ends. If a newer writer finishes, an older still-running turn can regain the writer claim.

**Data flow**: It awaits status.run, logs task-level failure, removes task and status maps, and either deletes or transfers the thread writer entry.

**Call relations**: _track_status creates this as the background task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 2811–2815)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the progress reporting schedule. It prevents nonsensical intervals such as zero wait time or a cap smaller than the base.

**Data flow**: It reads base_seconds and cap_seconds and raises ValueError if they are invalid.

**Call relations**: _track_progress constructs ProgressCadence before starting a progress reporter.


##### `ProgressCadence.intervals`  (lines 2817–2828)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait times between progress posts. The waits grow with elapsed time until they reach a maximum cap.

**Data flow**: It starts at the base interval, yields it, adds it to elapsed time, then yields the smaller of elapsed time and the cap forever.

**Call relations**: ProgressCadence.checkpoints_after uses this sequence to place future checkpoints.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 2830–2839)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Finds future progress checkpoint times after a turn has already been running for some time. This lets resumed reporters continue the real schedule instead of starting over.

**Data flow**: It receives elapsed seconds, walks intervals into cumulative checkpoint times, and yields only those greater than the elapsed value.

**Call relations**: ThreadProgress._follow uses it to schedule progress posts.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.tool`  (lines 2854–2859)

```
def tool(self, tool: str, description: str) -> None
```

**Purpose**: Records that the turn is currently doing a tool step. It turns internal tool names into readable words when no user-facing description exists.

**Data flow**: It clears streaming text, normalizes the description or tool slug, trims it, and stores it as the current activity.

**Call relations**: ThreadProgress._follow calls it when tool or subagent activity frames arrive.


##### `TurnActivity.skill`  (lines 2861–2863)

```
def skill(self, skill: str) -> None
```

**Purpose**: Records that the turn is loading a skill. This gives long-running progress posts a clear current step.

**Data flow**: It clears streaming text and stores a short “loading the skill” message.

**Call relations**: ThreadProgress._follow calls it for SkillLoad frames.


##### `TurnActivity.stream`  (lines 2865–2866)

```
def stream(self, text: str) -> None
```

**Purpose**: Records that response text has begun streaming. The actual text is not posted as progress because it may be unfinished final answer content.

**Data flow**: It receives a text delta and appends it to the streaming list.

**Call relations**: ThreadProgress._follow calls it for TextDelta frames, and current_step interprets streaming as response preparation.


##### `TurnActivity.current_step`  (lines 2868–2872)

```
def current_step(self) -> str
```

**Purpose**: Returns the best current progress description. Streaming response preparation takes priority over the last completed tool step.

**Data flow**: It checks whether any text deltas have been seen and returns a generic response-preparation phrase or the stored activity.

**Call relations**: TurnActivity.report uses it when building a checkpoint post.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 2874–2884)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds the text for one progress checkpoint. It returns nothing if the turn has produced no meaningful activity signal yet.

**Data flow**: It receives elapsed seconds, gets the current step, formats elapsed time in minutes or hours, and returns a short line or None.

**Call relations**: ThreadProgress._post calls it before trying to post an interim update.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 2925–2928)

```
async def run(self) -> None
```

**Purpose**: Runs the long-turn progress reporter for one turn. It opens the Slack client and delegates the follow loop.

**Data flow**: It reads the Slack bot token, creates an HTTP client, and calls _follow.

**Call relations**: _run_progress wraps this method in a background task.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 2930–2933)

```
def _elapsed(self) -> float
```

**Purpose**: Calculates how long the member has been waiting for this turn. It uses wall-clock time so the value survives process restarts.

**Data flow**: It subtracts the turn’s durable start time from the current UTC time and returns seconds.

**Call relations**: ThreadProgress._follow and _post_resumed use it when scheduling and wording progress posts.

*Call graph*: called by 2 (_follow, _post_resumed); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 2935–2991)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Follows live turn frames and posts progress at scheduled checkpoints. It also sends a delayed resume notice if the turn was picked up after a restart.

**Data flow**: It calculates the next checkpoint, tracks activity, cost ticks, and resume attempts, waits for frames or timers, skips posts after terminal state, and exits on terminal or parked frames.

**Call relations**: ThreadProgress.run calls it; it calls _post and _post_resumed when timers are due.

*Call graph*: calls 3 internal fn (_elapsed, _post, _post_resumed); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 2993–3020)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one scheduled progress update if there is useful activity to report. Empty checkpoints are logged and skipped.

**Data flow**: It asks TurnActivity for a report line, logs a skip if None, otherwise sends the line through _say and returns whether it landed.

**Call relations**: ThreadProgress._follow calls it when a progress checkpoint is reached.

*Call graph*: calls 2 internal fn (_say, report); called by 1 (_follow); 1 external calls (log).


##### `ThreadProgress._post_resumed`  (lines 3022–3033)

```
async def _post_resumed(self, client: httpx.AsyncClient, bot_token: str, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts the special message that tells the member a turn resumed after a service restart. It avoids leaving a long pause looking like a failure.

**Data flow**: It builds the fixed resume notice and sends it through _say using the current elapsed time and optional spend.

**Call relations**: ThreadProgress._follow calls it after the resume grace period.

*Call graph*: calls 2 internal fn (_elapsed, _say); called by 1 (_follow).


##### `ThreadProgress._say`  (lines 3035–3075)

```
async def _say(self, client: httpx.AsyncClient, bot_token: str, text: str, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Sends one progress or resume message to Slack. The first delivered progress message may include the standard footer.

**Data flow**: It finds the channel and thread anchor, optionally builds a footer, posts a Slack reply body, logs success or failure, restamps thread status if needed, and returns whether Slack accepted it.

**Call relations**: _post and _post_resumed use it for all progress side-channel messages.

*Call graph*: calls 4 internal fn (_footer, _restamp_thread_status, _slack_ok, slack_reply_body); called by 2 (_post, _post_resumed); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 3077–3098)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the footer for the first progress post. It includes web links and, when allowed, cost information seen so far.

**Data flow**: It asks for the conversation’s agent, formats spend if available, and delegates to _slack_footer; it returns None if no agent is known.

**Call relations**: ThreadProgress._say calls it only for the first possible progress message.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_say).


##### `_track_progress`  (lines 3104–3131)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, thread: MirroredThread, started_at: datetime) -> None
```

**Purpose**: Starts one progress reporter for a turn execution in this process. It is armed from the turn’s own execution so it follows the live frames that process publishes.

**Data flow**: It skips already tracked turn ids, builds ThreadProgress with cadence and timestamps, creates _run_progress as a task, and stores it.

**Call relations**: _arm_followers calls it when it knows the turn’s durable start time.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 4 external calls (__init__, __init__, create_task, now).


##### `_run_progress`  (lines 3134–3149)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Supervises a ThreadProgress task and cleans it up when done. Task-level failures are logged as abandoned progress reporting.

**Data flow**: It awaits progress.run, logs unexpected errors, and removes the task from the progress task map.

**Call relations**: _track_progress creates this background task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 3163–3177)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts the Slack live-feedback followers for a turn: status always, progress only when the durable start time is known. This gives both admission and execution hooks one shared arming path.

**Data flow**: It receives a follower context, FollowedTurn, and MirroredThread, calls _track_status, and calls _track_progress if started_at is present.

**Call relations**: _admit_inbound, interactive, and follow_turn all use it after opening or picking up a turn.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, follow_turn, interactive).


##### `_HookFollowerContext.workspace_id`  (lines 3189–3190)

```
def workspace_id(self) -> UUID
```

**Purpose**: Adapts a hook extension context to the follower protocol by exposing its workspace id.

**Data flow**: It returns ext.workspace_id from the wrapped ExtensionContext.

**Call relations**: follow_turn creates _HookFollowerContext so status and progress code can run from hooks.


##### `_HookFollowerContext.public_base_url`  (lines 3193–3194)

```
def public_base_url(self) -> str | None
```

**Purpose**: Adapts a hook extension context by exposing the public base URL used for Slack footers.

**Data flow**: It returns ext.public_base_url from the wrapped ExtensionContext.

**Call relations**: ThreadProgress and _slack_footer can use it through the follower protocol.


##### `_HookFollowerContext.credential`  (lines 3196–3197)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Lets follower code read extension credentials from a hook context. This is how hook-started followers get the Slack bot token.

**Data flow**: It receives a credential slot name and returns ext.credentials.get(slot).

**Call relations**: ThreadStatus and ThreadProgress call it through the FollowerContext interface.


##### `_HookFollowerContext.tail`  (lines 3199–3202)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Lets follower code tail live turn frames from a hook context.

**Data flow**: It receives a turn id and optional cursor and returns ext.tail(turn_id, since).

**Call relations**: ThreadStatus and ThreadProgress use it exactly like they use SurfaceContext.tail.


##### `_HookFollowerContext.turn_is_terminal`  (lines 3204–3205)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets hook-started progress reporting check whether a turn has durably ended.

**Data flow**: It receives a turn id and returns ext.turn_is_terminal(turn_id).

**Call relations**: ThreadProgress._follow uses it via the follower protocol.


##### `_HookFollowerContext.conversation_agent`  (lines 3207–3208)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Lets hook-started followers find the agent for a conversation.

**Data flow**: It receives a conversation id and returns ext.conversation_agent(conversation_id).

**Call relations**: ThreadProgress._footer uses it through the follower protocol.


##### `_HookFollowerContext.is_operator_workspace`  (lines 3210–3211)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Lets hook-started footer rendering know whether the workspace is the operator workspace.

**Data flow**: It returns ext.is_operator_workspace().

**Call relations**: _slack_footer uses it through the follower protocol.


##### `follow_turn`  (lines 3214–3256)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Hook handler that re-arms Slack status and progress followers from the turn’s own execution. This is what restores live feedback after a restart or resumed run.

**Data flow**: It ignores missing or subagent turns, reads the mirrored Slack thread with a short timeout, adapts the hook context, builds FollowedTurn with created_at, arms followers, and returns no hook outcome.

**Call relations**: The manifest hook calls this during user_prompt_submit; it then calls _arm_followers.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `interactive`  (lines 3303–3408)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack interactive payloads such as ask-form submit buttons and connect buttons. It verifies Slack, admits submitted answers, rewrites forms, and sends private link responses.

**Data flow**: It reads and verifies the raw payload, loads identity, parses the interaction, handles connect clicks with an ephemeral message, handles answer submits by finding the conversation, resolving the member, admitting the answer, anchoring DM replies, arming followers, and starting a form rewrite when this submit won.

**Call relations**: This is the main route for Slack Block Kit actions and it coordinates _to_interaction, admission, _rewrite_in_background, and _ephemeral_in_background.

*Call graph*: calls 23 internal fn (admit, admitted_body, connect_url, conversation_for, credential, find_conversation, linked_member, _anchor_dm_thread, _arm_followers, _ctx_signing_secret (+13 more)); 8 external calls (__init__, __init__, gather, conversation_audience, JSONResponse, Response, fence_member_message, mint_marker).


##### `_rewrite_in_background`  (lines 3414–3417)

```
def _rewrite_in_background(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Starts a background task to rewrite a submitted question message. This keeps Slack’s required acknowledgement fast.

**Data flow**: It receives a bot token and AnswerSubmit, creates _run_rewrite as a task, stores it in a task set, and removes it when finished.

**Call relations**: interactive calls it after confirming the submitted answer was the one admitted.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3420–3424)

```
async def _run_rewrite(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Runs the form rewrite and logs any failure. A failed rewrite does not undo the admitted answer.

**Data flow**: It calls _replace_controls_with_answers and logs a warning if an exception occurs.

**Call relations**: _rewrite_in_background creates this as a background task.

*Call graph*: calls 1 internal fn (_replace_controls_with_answers); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3427–3432)

```
def _ephemeral_in_background(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Starts a background task to send a private Slack message visible only to one user. This is used for connect links and empty form-submit warnings.

**Data flow**: It receives context, channel, user id, thread timestamp, and text, creates _post_ephemeral as a task, tracks it, and removes it when done.

**Call relations**: interactive calls it for connect clicks and invalid empty answer submits.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3435–3462)

```
async def _post_ephemeral(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Sends one Slack ephemeral message in the clicked thread. Ephemeral means only the target member can see it.

**Data flow**: It reads the bot token, posts chat.postEphemeral with channel, user, text, and optional thread_ts, and logs failures.

**Call relations**: _ephemeral_in_background launches it after interactive chooses a private response.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_interaction`  (lines 3465–3527)

```
def _to_interaction(raw: bytes, identity: SlackIdentity) -> AnswerSubmit | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactivity body into either an answer submission or a connect-button click. Other actions are ignored.

**Data flow**: It decodes the form payload JSON, checks team and action id, extracts user, channel, message, and thread fields, returns ConnectClick for connect buttons or AnswerSubmit for ask-submit buttons.

**Call relations**: interactive calls it after signature verification and identity lookup.

*Call graph*: calls 4 internal fn (_dict_field, _string_field, _submitted_answers, slack_thread_key); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_submitted_answers`  (lines 3530–3556)

```
def _submitted_answers(blocks: tuple[Mapping[str, object], ...], state: object) -> tuple[SubmittedAnswer, ...]
```

**Purpose**: Extracts all answers from a submitted Slack ask form. It pairs each rendered input block with the value held in Slack’s state payload.

**Data flow**: It receives delivered blocks and state, walks input blocks with ask-prefixed ids, reads each label and held value, converts the held value with _held_answer, and returns SubmittedAnswer records.

**Call relations**: _to_interaction calls it when parsing an ask submit action.

*Call graph*: calls 1 internal fn (_held_answer); called by 1 (_to_interaction); 1 external calls (__init__).


##### `_held_answer`  (lines 3559–3577)

```
def _held_answer(field: object) -> str
```

**Purpose**: Converts one Slack form control state into plain answer text. It supports radio buttons, checkboxes, and text input.

**Data flow**: It receives a field object, branches on the Slack control type, returns the selected option value, comma-joined checkbox values, trimmed typed text, or an empty string.

**Call relations**: _submitted_answers uses it for each input block.

*Call graph*: calls 1 internal fn (_option_value); called by 1 (_submitted_answers); 1 external calls (get).


##### `_option_value`  (lines 3580–3584)

```
def _option_value(option: object) -> str
```

**Purpose**: Reads the submitted value from a Slack option object. It returns an empty string for malformed options.

**Data flow**: It receives an option object, checks for a string value field, and returns that value or empty text.

**Call relations**: _held_answer uses it for radio-button and checkbox selections.

*Call graph*: called by 1 (_held_answer).


##### `_dict_field`  (lines 3587–3591)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload. It gives clear errors for malformed interactive payloads.

**Data flow**: It receives a mapping and field name, returns the nested dict when present, or raises ValueError.

**Call relations**: _to_interaction uses it for user, channel, and message sections.

*Call graph*: called by 1 (_to_interaction).


##### `_replace_controls_with_answers`  (lines 3594–3638)

```
async def _replace_controls_with_answers(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Updates a Slack question message after a successful submit, replacing form controls with submitted answer lines and who submitted them.

**Data flow**: It receives the bot token and AnswerSubmit, copies delivered blocks except input blocks and the submit block, replaces those with context lines, and calls chat.update with the original message text and new blocks.

**Call relations**: _run_rewrite calls it in the background after interactive admits the winning answer.

*Call graph*: calls 2 internal fn (_context_line, _slack_ok); called by 1 (_run_rewrite); 2 external calls (AsyncClient, dumps).


##### `_context_line`  (lines 3641–3645)

```
def _context_line(text: str) -> dict[str, object]
```

**Purpose**: Creates a small Slack context block containing Markdown text. It is used for compact status-like lines inside a message.

**Data flow**: It receives text, trims it to Slack’s context limit, and returns a Block Kit context dict.

**Call relations**: _replace_controls_with_answers uses it for answer summaries and submitted-by text.

*Call graph*: called by 1 (_replace_controls_with_answers).


##### `_reply_text`  (lines 3648–3658)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a terminal Slack reply. It ensures failed, cancelled, and empty successful turns still produce something visible.

**Data flow**: It receives a Writeback, checks terminal status, and returns failure text, cancellation reason or default, terminal text, or an empty-reply placeholder.

**Call relations**: _reply_with_oversize_links uses it as the base final reply text.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 3661–3690)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds Slack-specific extra lines to the final reply, such as credential instructions and links for artifacts too large to upload. This prevents important outputs from silently disappearing.

**Data flow**: It starts from _reply_text, appends credential-setting guidance when needed, finds oversized artifacts, formats TTL links for them, and returns the combined text.

**Call relations**: post calls it before mapping mentions and splitting the final reply.

*Call graph*: calls 3 internal fn (home_url, _oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 3693–3696)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one oversized shared artifact as a Markdown list item. It uses a downloadable link when one is available.

**Data flow**: It receives context and artifact, asks the context for an artifact link, builds linked or plain filename text, and includes the byte size.

**Call relations**: _reply_with_oversize_links calls it for each artifact that exceeds Slack’s upload cap.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_reply_mention_ids`  (lines 3699–3710)

```
async def _reply_mention_ids(ctx: SurfaceContext, bot_token: str, channel: str, text: str) -> dict[str, str]
```

**Purpose**: Builds the safe Slack mention map for an outgoing reply, but only if the reply contains @ text. This avoids unnecessary Slack roster reads.

**Data flow**: It receives context, bot token, channel, and text, returns an empty map if no @ exists or identity is missing, otherwise asks SlackNames.mention_ids.

**Call relations**: _reply_mentions_mapped calls it when preparing terminal and mid-turn replies.

*Call graph*: calls 1 internal fn (_identity); called by 1 (_reply_mentions_mapped); 1 external calls (__init__).


##### `_channel_info`  (lines 3713–3727)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for a channel or conversation. It is best-effort and returns None on failures.

**Data flow**: It calls conversations.info with the bot token and channel id, checks the response, and returns the channel object when it is a dict.

**Call relations**: _channel_origin, _channel_is_externally_shared, and SlackNames._name use it.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 3730–3743)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel crosses workspace boundaries. It fails closed by treating unreadable channel metadata as externally shared.

**Data flow**: It fetches channel info and returns true if metadata is missing or any Slack shared-channel flag is true.

**Call relations**: _slack_footer uses it to avoid exposing operator-only accounting or debug links in shared channels.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 3746–3781)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, agent_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the footer text placed under Slack progress and final replies. It can include web links, configuration links, accounting, and debug links depending on workspace and channel safety.

**Data flow**: It receives context, token, channel, conversation id, agent id, turn id, and accounting text, builds web links when possible, checks operator workspace and external sharing, and returns a trimmed footer or None.

**Call relations**: post and ThreadProgress._footer call it before sending Slack messages.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 3803–3808)

```
def _slack_reply_progress_key(turn_id: UUID, reply_id: UUID | None=None) -> str
```

**Purpose**: Builds the store key for tracking delivery progress of a Slack reply. Terminal replies are keyed by turn; mid-turn replies add the reply id.

**Data flow**: It receives a turn id and optional reply id and returns the progress key string.

**Call relations**: post, speak, and attach use it to read, write, and later clean delivery records.

*Call graph*: called by 3 (attach, post, speak).


##### `_slack_reply_progress`  (lines 3811–3824)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Reads or creates the delivery-progress record for a Slack reply. This record supports exactly-once delivery across retries.

**Data flow**: It reads the key from the scoped store, validates existing data if present, otherwise writes an empty progress object with compare-and-set and returns the progress plus stored value.

**Call relations**: post and speak call it before sending any reply parts.

*Call graph*: calls 2 internal fn (get, put_if); called by 2 (post, speak); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 3827–3836)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Atomically writes an updated Slack reply progress record. It refuses to overwrite if another delivery worker changed the record first.

**Data flow**: It serializes the progress object, writes it with expected previous value, and returns the new progress and encoded value or raises if the compare-and-set fails.

**Call relations**: post, speak, _deliver_slack_reply, and _reply_mentions_mapped use it between delivery steps.

*Call graph*: calls 1 internal fn (put_if); called by 4 (_deliver_slack_reply, _reply_mentions_mapped, post, speak); 2 external calls (__init__, model_dump).


##### `_slack_reply_delivery`  (lines 3839–3854)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Recognizes a Slack message that carries this file’s delivery metadata. It helps recover from the uncertain window where Slack accepted a post but the caller did not record it.

**Data flow**: It receives a raw Slack message and delivery id, checks metadata event type and payload id, and returns the message timestamp when it matches.

**Call relations**: _reconcile_slack_reply applies it to messages fetched from Slack history or replies.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 3857–3897)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Searches recent Slack messages for a previously pending delivery id. This prevents duplicate posts after a lost response from Slack.

**Data flow**: It pages through recent thread or channel history with metadata included, checks each message with _slack_reply_delivery, returns the timestamp if found, None if not found, or raises if page limits are exceeded.

**Call relations**: post and speak call it before retrying a reply that had a pending delivery id.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 2 (post, speak); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 3900–3940)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Sends one Slack reply part with checkpointing around the uncertain network call. It records pending before posting and records the Slack timestamp after success.

**Data flow**: It checks whether this delivery id was already recorded, writes it as pending, posts with _chat_post, handles invalid_blocks as a recoverable result, records the delivered timestamp, and returns updated progress plus Slack payload.

**Call relations**: post and speak use it for every message part and fallback attempt.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 2 (post, speak); 2 external calls (__init__, model_copy).


##### `_reply_mentions_mapped`  (lines 3943–3973)

```
async def _reply_mentions_mapped(ctx: SurfaceContext, bot_token: str, channel: str, text: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue) -> tuple[_SlackReplyPro
```

**Purpose**: Converts readable @names in an agent reply into Slack mention markup using a pinned map. Pinning keeps retries split and send the same way.

**Data flow**: It receives reply text and progress state, reuses progress.mentions when present, otherwise resolves mention ids, checkpoints the map, applies mention_markup, and returns updated progress, stored value, and mapped text.

**Call relations**: post and speak call it before splitting and delivering reply text.

*Call graph*: calls 2 internal fn (_checkpoint_slack_reply, _reply_mention_ids); called by 2 (post, speak); 2 external calls (model_copy, mention_markup).


##### `post`  (lines 3976–4132)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Posts the terminal reply for a completed turn to Slack and returns the first Slack message reference. It handles splitting, footers, ask forms, connect buttons, retries, mention mapping, and Block Kit fallbacks.

**Data flow**: It finds the channel and reply thread, reads the bot token, loads delivery progress, reconciles pending deliveries, prepares text and actions, builds the footer, sends each part with checkpointing, falls back on invalid blocks, marks delivery complete, and returns channel:timestamp.

**Call relations**: Core delivery calls this when a turn finishes and needs its final Slack answer posted.

*Call graph*: calls 15 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _reply_with_oversize_links, _slack_footer, _slack_reply_progress (+5 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `speak`  (lines 4135–4221)

```
async def speak(ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Posts a mid-turn reply to Slack before the final answer. It is exactly-once like terminal posting but simpler: no footer, files, ask form, or connect button.

**Data flow**: It finds the channel and thread for the span’s message ref, reads the bot token, loads and reconciles delivery progress, maps mentions, splits text, posts parts with checkpointing and fallback, marks complete, and returns channel:timestamp.

**Call relations**: Core calls this for MidTurnReply objects produced while a turn is still running.

*Call graph*: calls 11 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _slack_reply_progress, _slack_reply_progress_key, slack_reply_body (+1 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 4224–4264)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Posts a prepared chat.postMessage body to Slack and returns the parsed JSON without requiring ok:true. This lets callers handle recoverable Slack errors such as invalid_blocks.

**Data flow**: It sends the HTTP request, translates HTTP failures into SurfaceDeliveryError with optional retry-after seconds, and returns the JSON payload for successful HTTP responses.

**Call relations**: _deliver_slack_reply uses it for reply part sends.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 4267–4273)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the Slack timestamp from a successful post response. It turns missing or negative Slack responses into SlackApiError.

**Data flow**: It receives a Slack payload, checks ok:true, reads ts as a non-empty string, and returns it or raises.

**Call relations**: _deliver_slack_reply, post, and speak use it after Slack message sends.

*Call graph*: called by 3 (_deliver_slack_reply, post, speak); 1 external calls (__init__).


##### `attach`  (lines 4276–4321)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads and shares files produced by a completed turn into the same Slack thread as the reply. It also cleans up temporary reply-delivery and DM-anchor records once core has stored the final reply reference.

**Data flow**: It reads the reply thread, deletes progress and anchor store records for the turn, filters artifacts that fit Slack’s upload cap, reserves upload URLs, streams blobs to Slack concurrently, batches uploaded file ids, and shares each batch into Slack.

**Call relations**: Core calls it after post has delivered the terminal reply; it uses _upload_artifact and _share_uploaded_files for Slack’s external upload flow.

*Call graph*: calls 7 internal fn (credential, _attachment_batches, _dm_anchor_key, _reply_thread, _share_uploaded_files, _slack_reply_progress_key, _upload_artifact); 4 external calls (__init__, gather, AsyncClient, Timeout).


##### `_attachment_batches`  (lines 4324–4328)

```
def _attachment_batches(files: Sequence[dict[str, str]]) -> Iterator[Sequence[dict[str, str]]]
```

**Purpose**: Splits uploaded Slack file records into batches small enough for Slack’s complete-upload call. This keeps one share message where possible.

**Data flow**: It receives a sequence of file dicts and yields slices of at most Slack’s attachment cap.

**Call relations**: attach calls it before sharing uploaded files.

*Call graph*: called by 1 (attach).


##### `_upload_artifact`  (lines 4331–4358)

```
async def _upload_artifact(ctx: SurfaceContext, client: httpx.AsyncClient, bot_token: str, artifact: SharedArtifact) -> str
```

**Purpose**: Performs the reservation and byte upload steps for one shared artifact. It streams the blob directly to Slack’s external upload URL.

**Data flow**: It asks Slack for an upload URL and file id using filename and length, validates the response, posts the blob stream to the upload URL, and returns the file id.

**Call relations**: attach runs this concurrently for all inline-sized artifacts.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (__init__, post).


##### `_share_uploaded_files`  (lines 4361–4385)

```
async def _share_uploaded_files(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, files: Sequence[dict[str, str]]) -> None
```

**Purpose**: Completes Slack’s external upload flow by sharing already uploaded files into a channel or thread. This is the step that makes the files visible in Slack.

**Data flow**: It receives file ids and titles, channel, optional thread timestamp, and bot token, then posts files.completeUploadExternal with those values.

**Call relations**: attach calls it for each batch after uploads finish.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (post, dumps).


##### `_slack_ok`  (lines 4388–4397)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Checks a Slack API response for both HTTP success and Slack’s ok:true flag. It raises a readable SlackApiError for Slack-level failures.

**Data flow**: It awaits an HTTP request, raises for bad HTTP status, parses JSON, returns the payload when ok is true, or raises with Slack’s error and messages.

**Call relations**: Most Slack API helpers call it so they share the same error handling.

*Call graph*: called by 17 (_list, _members, _say, _set, _ambient_context, _channel_info, _conversation_members, _declared_files, _post_ephemeral, _reconcile_slack_reply (+7 more)); 1 external calls (__init__).


### Command-line chat surface
The shell chat surface translates conversation progress, terminal actions, files, and credential prompts into text instructions for the CLI client.

### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The `ufo` shell client is deliberately simple: it sends HTTP requests and reads back lines of tab-separated commands such as “say this”, “ask for input”, “run this terminal operation”, or “poll again soon”. This file is the translator and traffic controller for that conversation. Without it, the command-line client would not know how to authenticate, send messages, resume a long answer, display live progress, collect secrets privately, or run requested terminal operations.

A typical request comes into `channel`. The request’s bearer token proves the member’s email and workspace. A message body may start a new turn in the conversation, while an empty body resumes watching the latest turn. Long-running answers are streamed for a limited time; if the answer is still going, the server sends a `poll` instruction so the shell reconnects rather than timing out.

The file also protects important boundaries. Secrets are stored through a special path and never become chat messages. Terminal operation replies are returned to the waiting turn, not added to the transcript. Shared files are converted into downloadable links only when a turn ends. The core idea is like a theater stage manager: the agent produces events backstage, and this file cues the terminal exactly what to show or do next.

#### Function details

##### `directive`  (lines 96–104)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one instruction line for the shell client. It makes sure tabs, newlines, and backslashes inside user-visible text cannot accidentally break the line format.

**Data flow**: It receives a command word and optional text fields. It escapes characters that would confuse the shell’s tab-separated reader, joins everything with tabs, adds a newline, and returns bytes ready to send over HTTP.

**Call relations**: Many parts of this file call this helper whenever they need to speak to the shell, including answer rendering, history rendering, live frame rendering, secret replies, sends, and channel setup notes.

*Call graph*: called by 9 (_answer, _fulfill_secret, _say_lines, _send, _subagent_note, channel, directives_for, history_directives, stream_directives).


##### `shared_files`  (lines 118–129)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files an agent shared during a turn and turns them into terminal-friendly file records. Each record includes a name, size, and, when possible, a public download link.

**Data flow**: It receives the surface context and a turn id. It asks the core system for that turn’s shared artifacts, asks the context to mint a usable link for each one, and returns a tuple of `SharedFile` values.

**Call relations**: The channel stream passes this function into `stream_directives`; when a terminal frame arrives, `stream_directives` asks for the files so `_answer` can emit `file` directives after the turn has ended.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 132–139)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies which workspace a request belongs to before the route handler runs. If the request has no valid bearer token shape, it refuses to identify a workspace.

**Data flow**: It reads the HTTP `Authorization` header. If it finds a bearer token, it asks the bearer codec for the workspace claim and returns that workspace id; otherwise it returns `None`.

**Call relations**: This is used by the wider surface routing layer as the early request scoping step. The route handler later verifies the same token again for the member email.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 145–204)

```
def history_directives(conversation: Conversation) -> tuple[bytes, ...]
```

**Purpose**: Rebuilds a readable version of the past conversation for a client that is resuming without a cursor. It avoids repeating the most recent assistant reply because live tailing will replay the latest turn’s frames.

**Data flow**: It receives a conversation transcript. It extracts member messages, assistant text, and completed tool-step counts, keeps the newest content within a character budget, and returns `you`, `say`, and `note` directive lines.

**Call relations**: The `channel` handler calls this when an empty request resumes a conversation from scratch. It relies on `_history_text` to get message text, `_dispatched` to count real tool work, and `directive` to format the output.

*Call graph*: calls 3 internal fn (_dispatched, _history_text, directive); called by 1 (channel).


##### `_dispatched`  (lines 207–214)

```
def _dispatched(message: Message, active: set[str]) -> int
```

**Purpose**: Counts how many tool calls in an assistant message actually became active work. This prevents the resume history from claiming a step happened when it was never dispatched.

**Data flow**: It receives one message and a set of active tool-use ids. If the message is plain text it returns zero; otherwise it counts matching tool-use blocks and returns that number.

**Call relations**: Only `history_directives` calls this while reconstructing past conversation notes. Its count becomes the “Completed N steps” note shown on resume.

*Call graph*: called by 1 (history_directives).


##### `_history_text`  (lines 217–222)

```
def _history_text(message: Message) -> str
```

**Purpose**: Extracts the text that should be shown from one transcript message. For member messages, it applies the same cleanup used elsewhere for member-written text.

**Data flow**: It receives a message. If the content is already a string, it uses it directly; otherwise it joins the text blocks. For user messages it normalizes the text through `member_message_text`, then returns the final string.

**Call relations**: This helper is used by `history_directives` so history rendering can treat plain messages and block-based messages in one consistent way.

*Call graph*: called by 1 (history_directives); 1 external calls (member_message_text).


##### `directives_for`  (lines 225–265)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=()) -> tuple[bytes, ...]
```

**Purpose**: Converts one live event from the agent into one or more shell instructions. This is the main translation point between internal agent progress and what the terminal displays.

**Data flow**: It receives a live frame plus context such as whether text has already streamed, pending credential prompts, a connection link, and shared files. It pattern-matches the frame type and returns the matching directive bytes, such as `txt`, `note`, `status`, `say`, `ask`, `file`, or `absorbed`.

**Call relations**: `stream_directives` calls this for each frame it reads from the live tail. It delegates details to `_activity`, `_subagent_note`, and `_answer`, and uses `directive` for the final wire format.

*Call graph*: calls 4 internal fn (_activity, _answer, _subagent_note, directive); called by 1 (stream_directives).


##### `_activity`  (lines 268–270)

```
def _activity(frame: ToolCall) -> str
```

**Purpose**: Turns a tool call into a short human-readable progress note. It tells the member which tool is running and includes a description or preview when available.

**Data flow**: It receives a tool-call frame. It chooses the best detail text, combines it with the tool name, and returns a plain string such as “running search: looking up docs”.

**Call relations**: `directives_for` calls this when it sees a normal tool-call frame, then wraps the returned text in a `note` directive.

*Call graph*: called by 1 (directives_for).


##### `_subagent_note`  (lines 273–283)

```
def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]
```

**Purpose**: Creates progress notes for work done by a subagent, meaning a helper agent working under the main one. It labels the note with the subagent’s name or profile so the user can tell who is acting.

**Data flow**: It receives a subagent activity frame. If the subagent is running a tool or loading a skill, it returns a `note` directive; if the frame is only a start or finish marker, it returns nothing.

**Call relations**: `directives_for` calls this for subagent activity frames. This keeps subagent progress readable without flooding the terminal with extra lifecycle noise.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for).


##### `_answer`  (lines 286–334)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=()) -> tuple[bytes, ...]
```

**Purpose**: Builds the final set of instructions when a turn reaches a terminal state such as done, failed, or cancelled. It decides whether to show an answer, prompt again, ask for secrets, show shared files, or exit.

**Data flow**: It receives the terminal frame, whether answer text already streamed, any pending credential prompts, an optional connection message, and shared files. It returns the final directive lines appropriate to the turn’s status.

**Call relations**: `directives_for` calls this when it receives a terminal live frame. `_answer` uses `_say_lines` and `directive` to produce the exact shell instructions that close or continue the session.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 337–338)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Splits a block of text into separate `say` lines for the terminal. This keeps multi-line messages framed as clean individual display instructions.

**Data flow**: It receives text. It splits it by line, or keeps one empty-ish line when needed, then returns a tuple of `say` directives.

**Call relations**: `_answer` uses this whenever final text needs to be displayed as normal spoken output rather than streamed token-by-token.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 341–478)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Streams live turn events to the shell for a limited time, then tells the client whether to reconnect. It is the heart of the long-polling behavior that lets a simple shell client follow a live agent turn.

**Data flow**: It receives a live-frame tail, a hold duration, callbacks for credential prompts, connection links, files, terminal operations, the current turn id, and an optional resume cursor. It waits for frames or terminal operations, yields directives as bytes, updates the cursor after rendered frames, and finally yields `poll` or `run` instructions when the client must come back.

**Call relations**: `channel` creates this stream for normal message, resume, stop, and op-reply requests. It calls `_next` to read frames safely, `directives_for` to render them, and `directive` for cursor, poll, and run instructions.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 4 external calls (ensure_future, get_running_loop, wait, suppress).


##### `_next`  (lines 481–487)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next live frame without letting the end-of-stream exception leak into the waiting logic. It turns “no more frames” into a normal `None` value.

**Data flow**: It receives an async iterator of live frames. It awaits the next item and returns it, or returns `None` if the iterator is exhausted.

**Call relations**: `stream_directives` wraps frame reads in tasks so it can race them against timeouts and terminal operations. `_next` makes that task result easy to handle.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 490–494)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Checks the bearer token on a request and returns the member email if the token is valid for this workspace. If authentication fails, it returns nothing.

**Data flow**: It reads the `Authorization` header, extracts the bearer token, and asks `verify_token` to validate it against the workspace id. The result is an email string or `None`.

**Call relations**: `channel` uses this before accepting chat or secret requests, and `op_body` uses it before serving terminal operation bytes.

*Call graph*: called by 2 (channel, op_body); 1 external calls (verify_token).


##### `_utf8_header`  (lines 497–504)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Recovers a header value that the shell sent as raw UTF-8, such as a current working directory with non-ASCII characters. This works around the way HTTP servers decode headers by default.

**Data flow**: It reads a named header, re-encodes it as Latin-1 to recover the original bytes, then decodes those bytes as UTF-8 with replacement for bad data. It returns the resulting string.

**Call relations**: `channel` uses this for headers whose value may contain real user text, especially the terminal working directory and operation error text.

*Call graph*: called by 1 (channel).


##### `_stale_client`  (lines 507–513)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Detects whether the shell script version in the request differs from the version this deployment serves. If it is stale, the server can tell the client to reinstall or update.

**Data flow**: It reads the served client version from the environment and compares it with the request’s `x-ufo-script` header. It returns true only when a served version is configured and the client does not match it.

**Call relations**: `channel` checks this on normal message or resume streams. When true, it prepends an `install` directive before the rest of the screen.

*Call graph*: called by 1 (channel).


##### `_resumed_from`  (lines 516–523)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Decides which live-frame cursor can safely be used when a client reconnects. It ignores cursors from a different turn so frames are not skipped by mistake.

**Data flow**: It reads the `x-ufo-since` header, separates the turn id from the cursor, and compares the named turn with the current turn. It returns the cursor only when they match; otherwise it returns an empty string.

**Call relations**: `channel` calls this just before opening the live tail. The returned cursor is passed to `ctx.tail` through `stream_directives` so reconnects resume at the right place.

*Call graph*: called by 1 (channel).


##### `_turn_context`  (lines 526–538)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the background information attached to an admitted member message. It records who sent it, where it came from, and, when valid, the member’s reported time zone.

**Data flow**: It receives the member email and request. It reads the timezone header, tries to build a `TurnContext`, logs and drops the timezone if it is invalid, and returns the context object.

**Call relations**: `channel` and `_send` call this right before admitting a message to the core system. The context then travels with the turn so the agent can treat time and source correctly.

*Call graph*: called by 2 (_send, channel); 2 external calls (__init__, log).


##### `channel`  (lines 541–660)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main POST endpoint for a `ufo` conversation channel. It authenticates the member, admits messages when present, resumes live streams when empty, processes stops, terminal replies, secret submissions, sends, and unsends.

**Data flow**: It receives the surface context and HTTP request. It authenticates the email, finds or creates the member and conversation, branches based on special headers, admits or resolves the right action, then returns either a plain response or a streaming response of directives.

**Call relations**: This is the central route named in `ROUTES`. It calls many context methods to talk to the core conversation system, delegates special cases to `_send`, `_unsend`, and `_fulfill_secret`, uses helpers for headers and resume cursors, and starts `stream_directives` for live output.

*Call graph*: calls 21 internal fn (admit, claim_terminal, conversation_for, latest_turn, link_member, linked_member, read_transcript, stop_turn, tail, terminal_resolve (+11 more)); 5 external calls (partial, conversation_audience, PlainTextResponse, body, StreamingResponse).


##### `channel.moved_on`  (lines 628–630)

```
async def moved_on() -> bool
```

**Purpose**: Checks whether the conversation has already advanced to a newer live turn after the current stream reaches a terminal frame. This helps the client reconnect to the newer turn instead of replaying old history.

**Data flow**: It reads the latest turn id for the conversation, compares it with the turn being streamed, and checks whether that latest turn is still not terminal. It returns a true-or-false answer.

**Call relations**: The outer `channel` function passes this callback into `stream_directives`. `stream_directives` calls it after a terminal frame to decide whether to append an immediate `poll`.


##### `channel.bound`  (lines 644–658)

```
async def bound() -> AsyncIterator[bytes]
```

**Purpose**: Wraps the outgoing directive stream with terminal connection setup and cleanup. It also prepends update notices, replayed history, and workspace notes before live turn output.

**Data flow**: It starts with the prepared stream and optional setup data from `channel`. If a working directory is present, it marks the terminal connected, yields update/history/note lines, yields live directives, and finally disconnects the terminal.

**Call relations**: `channel` gives this async generator to `StreamingResponse`. It is the final bridge between the route’s decisions and the bytes the shell actually receives.


##### `_send`  (lines 663–713)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str) -> Response
```

**Purpose**: Accepts a quick second message while another held stream is already open. It admits the message and immediately returns an acknowledgement instead of holding another stream.

**Data flow**: It reads a send id header, the request body, and optional working directory. It validates size and identity, may claim the terminal directory, admits the message with an idempotency key so retries do not duplicate it, and returns a `sent` directive plus any workspace note.

**Call relations**: `channel` calls this when the `x-ufo-send` header is present. The consequences of the admitted message are later seen by the already-open stream through `stream_directives`, especially via `absorbed` directives.

*Call graph*: calls 4 internal fn (admit, claim_terminal, _turn_context, directive); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 716–738)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Retracts a queued message if the agent has not taken it up yet. This is the server side of taking back a pending row before it becomes part of a turn.

**Data flow**: It verifies the request body is empty, checks that there is a member, parses the arrival id, and asks the context to retract that arrival for this conversation and member. It returns empty success, a validation error, or a conflict saying the message was already taken up.

**Call relations**: `channel` calls this when the unsend header is present. It does not admit anything to the transcript; it only asks the core queue to remove a pending arrival.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 741–760)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores a credential value that the member entered privately. The secret is not treated as a chat message and is not added to the conversation transcript.

**Data flow**: It reads the credential slot header and secret body, validates that both are present and small enough, and asks the context to fulfill the sealed credential request. It returns a `say` directive telling the shell whether the value was stored.

**Call relations**: `channel` calls this when the secret header is present. It hands the sensitive write to the privileged surface context and uses `directive` to produce a safe terminal response.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 763–775)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the raw bytes for a terminal operation that is already in flight. This lets the shell download operation input, such as data to write into a temporary file, without creating a chat message.

**Data flow**: It authenticates the request, finds the linked member, builds the conversation queue key from email and channel, and asks the context for the operation body. It returns the bytes as binary data or a plain error if unauthorized or missing.

**Call relations**: This is the GET route named in `ROUTES`. It uses `_authenticated_email` for access control and `terminal_op_body` on the context to retrieve the single operation payload.

*Call graph*: calls 3 internal fn (linked_member, terminal_op_body, _authenticated_email); 2 external calls (PlainTextResponse, Response).
