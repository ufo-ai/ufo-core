# Web portal surface  `stage-5.1`

This stage is the user-facing web doorway into UFO while the system is running. It serves the browser app and turns clicks, forms, and live updates into requests the rest of the system can understand.

The main piece is surface.py. It delivers the web page, checks the user’s signed session cookie, and exposes the routes the browser calls. A route is a web address for a specific action. These routes cover chat messages, transcripts, workspace and admin data, object access, and live streaming so the portal can show updates as they happen.

community.py connects the portal to the public skills.sh directory. It lets users browse and search community skills, then fetches the full text of one chosen skill carefully, without pulling more remote data than needed.

panels.py connects settings-page buttons and forms to the normal chat-based write path, so portal actions use the same machinery as chat commands. It also supplies agent overview data for settings screens. __init__.py simply makes this folder importable as a Python package.

## Files in this stage

### Portal package and surface
Defines the web package boundary and the main browser-facing portal routes for chat, transcripts, workspaces, administration, objects, and live streams.

### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling`

This file is the web front door into the UFO system. It solves the problem of turning browser actions into safe, scoped core operations: sign in, list available agents, send chat messages, upload files, watch live answer updates, read transcripts, browse workspace resources, and perform panel mutations. Without it, the web UI would have no trusted way to talk to the core system, and members could not be separated by workspace, agent access, or conversation ownership.

The file works like a guarded reception desk. First, `resolve_workspace` reads a signed bearer token from the session cookie, or from the one login form post, to decide which workspace a request belongs to. Then most API routes call shared gate helpers such as `_audience_for`, `_panel_gate`, `_readable_conversation`, or `_object_gate` to confirm who the member is and what they are allowed to see.

Once admitted, routes translate browser-shaped requests into core calls through `SurfaceContext`. Chat routes create or continue conversations, save uploaded files, admit turns to the durable queue, and return IDs the browser can follow. Transcript helpers turn internal model messages into readable chat bubbles. Streaming uses Server-Sent Events, a simple one-way browser stream, so live text, tool activity, credential prompts, files, and subagent results arrive as the agent works.

#### Function details

##### `load_assets`  (lines 179–189)

```
def load_assets(directory: Path) -> dict[str, tuple[bytes, str]]
```

**Purpose**: Loads the built frontend files that the web portal is allowed to serve. It only includes known file types, so accidental build leftovers are not published.

**Data flow**: It takes a static asset directory, scans the files inside it, reads allowed files as bytes, and returns a lookup table from browser request name to file content and media type.

**Call relations**: This runs when the module is loaded to build `STATIC_ASSETS`; later `_static_response` uses that table instead of reading arbitrary paths from disk.

*Call graph*: 1 external calls (glob).


##### `resolve_workspace`  (lines 202–241)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Decides which workspace an incoming web request belongs to before the route handler runs. It keeps login tokens out of URLs by accepting them only from a cookie or the login form body.

**Data flow**: It reads the session cookie first, may read a small URL-encoded form body for a posted token, verifies the workspace claim, and returns a workspace ID, a redirect/response, or no workspace.

**Call relations**: The shared surface fleet calls this as the first gate. It uses `_chat_target` to preserve a requested chat across login, `_framed_length` and `_form` to safely read the login form, and redirects unauthenticated portal page loads to the shared login page.

*Call graph*: calls 3 internal fn (_chat_target, _form, _framed_length); 2 external calls (workspace_claim, RedirectResponse).


##### `_chat_target`  (lines 244–248)

```
def _chat_target(request: Request) -> UUID | None
```

**Purpose**: Extracts a conversation ID from the portal query string when a login redirect should return the member to a specific chat.

**Data flow**: It reads the `c` query parameter, tries to parse it as a UUID, and returns either that UUID or nothing.

**Call relations**: Only `resolve_workspace` uses it, just before redirecting an unauthenticated browser to login.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (UUID).


##### `_static_response`  (lines 251–265)

```
def _static_response(request: Request) -> Response | None
```

**Purpose**: Builds the HTTP response for a built frontend asset such as JavaScript or CSS. It also supports browser cache revalidation with an ETag, which is a content fingerprint.

**Data flow**: It maps the request path to a preloaded asset, compares the browser’s `if-none-match` header with the stored hash, and returns either the file, a 304 not-modified response, or nothing.

**Call relations**: `static_asset` calls this after the normal session/workspace gate has already run.

*Call graph*: called by 1 (static_asset); 1 external calls (Response).


##### `portal_page`  (lines 268–283)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main HTML shell for the web portal. It fails clearly if the frontend app has not been built.

**Data flow**: It reads the preloaded `index.html` text and returns it as HTML with no-store caching, or raises an error naming the build command if the file is missing.

**Call relations**: This is the GET route for the portal root after `resolve_workspace` has scoped the request.

*Call graph*: 1 external calls (HTMLResponse).


##### `_authenticate`  (lines 286–304)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | Response
```

**Purpose**: Turns the session cookie into a workspace member and email address. It also creates or links the member identity the first time that email appears.

**Data flow**: It reads the signed cookie, verifies it for the current workspace, looks up or creates the linked member row, and returns either `(member_id, email)` or a 401 response.

**Call relations**: _audience_for uses this for nearly all API routes, while `fulfill_credential` uses it directly because credential fulfillment only needs the member identity.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (_audience_for, fulfill_credential); 2 external calls (verify_token, Response).


##### `static_asset`  (lines 307–311)

```
async def static_asset(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves one frontend asset to an already scoped request. The assets contain no workspace data, but the route still sits behind the web session gate.

**Data flow**: It asks `_static_response` for the file response and falls back to a 404 if no known asset matches.

**Call relations**: This is the route behind `/surface/web/static/...`; it delegates all asset lookup and cache behavior to `_static_response`.

*Call graph*: calls 1 internal fn (_static_response); 1 external calls (Response).


##### `open_session`  (lines 314–339)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts the posted bearer token from the login card, stores it in the browser session cookie, and redirects into the portal.

**Data flow**: It checks the form body size, parses the form, validates that the token has a cookie-safe shape, sets the session cookie, and returns a redirect response.

**Call relations**: This is the one POST route that opens a web session. Later requests are checked again by `resolve_workspace` and `_authenticate`.

*Call graph*: calls 2 internal fn (_form, _framed_length); 3 external calls (JSONResponse, RedirectResponse, set_session_cookie).


##### `_agent_param`  (lines 342–346)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Reads the agent ID from a route path. It converts malformed path values into a simple missing-agent outcome.

**Data flow**: It takes the `agent_id` path parameter, tries to parse it as a UUID, and returns the UUID or nothing.

**Call relations**: Chat, transcript, and `_panel_gate` use it before checking whether the signed-in member’s web audience includes that agent.

*Call graph*: called by 3 (_panel_gate, chat, transcript); 1 external calls (UUID).


##### `_chat_row_key`  (lines 349–350)

```
def _chat_row_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage key for this web surface’s record of a conversation. That record binds a conversation to one agent and one member email.

**Data flow**: It takes a conversation UUID and returns a string key under the `chat/` prefix.

**Call relations**: Conversation opening, ownership checks, chat listing, and title summarization all use this same key shape so they agree on where web chat metadata lives.

*Call graph*: called by 4 (_open_conversation, _own_chat, chats_index, summarize_chat_titles).


##### `_chat_title`  (lines 359–377)

```
def _chat_title(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Creates a short rail title for a new chat from the first message or uploaded filenames. It trims at readable word boundaries instead of cutting awkwardly mid-thought.

**Data flow**: It receives message text and saved file paths, collapses whitespace, falls back to filenames if needed, trims to the title limit, and returns a clean display title.

**Call relations**: _open_conversation uses it for the first title, and `summarize_chat_titles` uses it again to clean model-generated titles.

*Call graph*: called by 2 (_open_conversation, summarize_chat_titles).


##### `_message_text`  (lines 389–392)

```
def _message_text(message: Message) -> str
```

**Purpose**: Gets the visible text out of a stored model message. Messages may be plain strings or structured blocks.

**Data flow**: It reads the message content; if it is a string it returns it, otherwise it joins the text blocks and ignores non-text blocks.

**Call relations**: _title_excerpt uses this to collect the first user and assistant text for automatic chat retitling.

*Call graph*: called by 1 (_title_excerpt).


##### `_title_excerpt`  (lines 395–408)

```
def _title_excerpt(messages: tuple[Message, ...]) -> str
```

**Purpose**: Builds the short opening exchange used to ask the model for a better chat title. It waits until an assistant reply exists so the title can reflect the first answer too.

**Data flow**: It receives stored messages, finds the first user text and first assistant text, trims each, joins them, and returns an excerpt or an empty string.

**Call relations**: `summarize_chat_titles` calls it while processing pending title jobs.

*Call graph*: calls 1 internal fn (_message_text); called by 1 (summarize_chat_titles).


##### `summarize_chat_titles`  (lines 411–446)

```
async def summarize_chat_titles(ctx: ExtensionContext) -> None
```

**Purpose**: Background job that replaces rough first-message chat titles with a short model-written title once the first answer has landed.

**Data flow**: It lists pending title markers, reads conversations and transcripts, builds an excerpt, asks the configured model for a title, retitles the conversation, and deletes the marker.

**Call relations**: It is scheduled outside request handling. It relies on `_chat_row_key`, `_title_excerpt`, and `_chat_title`, and writes the final title back through the extension context.

*Call graph*: calls 4 internal fn (retitle_conversation, _chat_row_key, _chat_title, _title_excerpt); 3 external calls (__init__, __init__, UUID).


##### `_open_conversation`  (lines 449–483)

```
async def _open_conversation(ctx: SurfaceContext, store: ScopedStore, agent_id: UUID, member_id: UUID, email: str, queue_key: str, text: str, paths: tuple[str, ...]) -> tuple[UUID, str]
```

**Purpose**: Creates a new web chat conversation and records which agent and member email own it. It writes the web chat record before creating the conversation so later gates can recognize it.

**Data flow**: It receives agent/member details, opening text, and upload paths; writes a chat record, creates or joins the core conversation, sets the title, marks it pending for retitling, and returns the conversation ID and title.

**Call relations**: `chat` calls this when the browser sends the first message for a new conversation. It uses `_own_chat` and `_named` if a queue-key race means another opener already won.

*Call graph*: calls 8 internal fn (delete, put, conversation_for, retitle_conversation, _chat_row_key, _chat_title, _named, _own_chat); called by 1 (chat); 3 external calls (__init__, conversation_audience, uuid4).


##### `_named`  (lines 486–493)

```
async def _named(ctx: SurfaceContext, agent_id: UUID, member_id: UUID, conversation_id: UUID) -> str
```

**Purpose**: Reads the current title for one conversation from the same listing source the UI uses elsewhere.

**Data flow**: It asks core for that specific agent conversation and returns its title, or an empty string if no row appears.

**Call relations**: Chat continuation, race recovery in `_open_conversation`, and permalink resolution use it so every screen names the conversation the same way.

*Call graph*: calls 1 internal fn (list_agent_conversations); called by 3 (_open_conversation, _resolve_chat, chat).


##### `_own_chat`  (lines 496–508)

```
async def _own_chat(store: ScopedStore, agent_id: UUID, email: str, conversation_id: UUID) -> ChatRecord | None
```

**Purpose**: Checks whether a conversation is this member’s own web chat with this agent. It prevents one member or agent from reading another’s web chat by guessing an ID.

**Data flow**: It reads the stored chat record by conversation ID, validates it, compares agent ID and email, and returns the record or nothing.

**Call relations**: `chat`, `transcript`, `_open_conversation`, and `_resolve_chat` call it before allowing web-chat continuation or display.

*Call graph*: calls 2 internal fn (get, _chat_row_key); called by 4 (_open_conversation, _resolve_chat, chat, transcript).


##### `_turn_context`  (lines 511–522)

```
def _turn_context(email: str, request: Request, source: str) -> TurnContext
```

**Purpose**: Builds the extra context attached to an admitted chat turn: who sent it, where it came from, and the browser’s timezone if valid.

**Data flow**: It reads the timezone header, validates it through `TurnContext`, logs and drops it if invalid, and returns a context object.

**Call relations**: `chat` passes this context into core admission so downstream tools and records know the human sender and source.

*Call graph*: called by 1 (chat); 2 external calls (__init__, log).


##### `_chat_source`  (lines 525–533)

```
def _chat_source(public_base_url: str | None, conversation_id: UUID, email: str) -> str
```

**Purpose**: Creates a human-readable source label for a web chat message. When possible, it includes a portal link back to the conversation.

**Data flow**: It takes the public base URL, conversation ID, and email, and returns either a full portal fragment link plus email or a fallback text label.

**Call relations**: `chat` uses this when admitting a message so anything the agent creates can say where the request came from.

*Call graph*: called by 1 (chat).


##### `_audience_for`  (lines 536–543)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Combines authentication with web-specific authorization. It answers not just who the member is, but which agents and admin powers the web portal should expose.

**Data flow**: It authenticates the request, then asks the web audience module for that email’s allowed agent set and admin flag, returning member ID, email, and audience or a response.

**Call relations**: Most top-level API routes call this directly or through narrower gates such as `_panel_gate`, `_object_gate`, `_subagent_gate`, and `_member_turn`.

*Call graph*: calls 1 internal fn (_authenticate); called by 18 (_member_turn, _object_gate, _panel_gate, _subagent_gate, admin_index, agents_index, chat, chats_index, connection_pool, github_coverage (+8 more)); 2 external calls (web_audience, web_extension).


##### `agents_index`  (lines 546–587)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the portal’s startup data: signed-in member details, visible agents, subagent profiles, and admin-only agent creation schema.

**Data flow**: It authenticates and builds the web audience, optionally reads grant lists for admins, serializes agent and subagent summaries, and returns JSON.

**Call relations**: The browser calls this early after loading the shell to know what navigation and creation options to draw.

*Call graph*: calls 1 internal fn (_audience_for); 4 external calls (JSONResponse, granted_emails, web_extension, agent_create_schema).


##### `_framed_length`  (lines 590–603)

```
def _framed_length(request: Request, limit: int) -> Response | None
```

**Purpose**: Protects whole-body form parsing by requiring a trustworthy Content-Length under a limit. This avoids reading an unbounded form into memory.

**Data flow**: It checks transfer encoding and content length headers, returning 411 for missing/unusable length, 413 for too large, or nothing when safe.

**Call relations**: Login, credential fulfillment, workspace resolving, and multipart chat parsing call this before parsing request bodies.

*Call graph*: called by 4 (_parse_inbound, fulfill_credential, open_session, resolve_workspace); 1 external calls (Response).


##### `_form`  (lines 606–613)

```
async def _form(request: Request) -> FormData | Response
```

**Purpose**: Parses a form request and turns malformed form data into a clear 400 response.

**Data flow**: It calls the request form parser and returns parsed form data, or catches parser errors and returns a bad-request response.

**Call relations**: Used by login, workspace resolution, chat upload parsing, and credential fulfillment whenever the browser sends form data.

*Call graph*: called by 4 (_parse_inbound, fulfill_credential, open_session, resolve_workspace); 2 external calls (form, Response).


##### `_bounded_body`  (lines 616–624)

```
async def _bounded_body(request: Request, limit: int) -> bytes | Response
```

**Purpose**: Reads a non-form request body with a hard byte limit based on actual bytes received, not just headers.

**Data flow**: It streams chunks from the request, accumulates them until complete, and stops with a 413 response if the limit is exceeded.

**Call relations**: _parse_inbound uses it for plain-text chat messages.

*Call graph*: called by 1 (_parse_inbound); 2 external calls (stream, Response).


##### `_parse_inbound`  (lines 627–663)

```
async def _parse_inbound(request: Request) -> tuple[str, tuple[UploadFile, ...]] | Response
```

**Purpose**: Reads the chat composer input, either plain text or multipart form data with files. It rejects unsupported or unsafe body shapes.

**Data flow**: It checks the content type, reads plain bodies under a byte cap with UTF-8 decoding, or parses bounded multipart data into message text and upload files, then returns text plus uploads or an error response.

**Call relations**: `chat` calls this before deciding whether to open a conversation, stop a turn, or admit a message.

*Call graph*: calls 3 internal fn (_bounded_body, _form, _framed_length); called by 1 (chat); 1 external calls (Response).


##### `_inbox_paths`  (lines 666–670)

```
def _inbox_paths(uploads: tuple[UploadFile, ...]) -> tuple[str, ...]
```

**Purpose**: Chooses safe, unique workspace filenames for uploaded chat files under the web inbox folder.

**Data flow**: It receives upload objects, sanitizes each filename through the shared inbox naming helper, avoids duplicates, and returns saved paths.

**Call relations**: `chat` uses the paths both to save files through `_deliver_uploads` and to mention them in the admitted message text.

*Call graph*: called by 1 (chat); 1 external calls (inbox_name).


##### `_deliver_uploads`  (lines 673–682)

```
async def _deliver_uploads(ctx: SurfaceContext, conversation_id: UUID, uploads: tuple[UploadFile, ...], paths: tuple[str, ...]) -> None
```

**Purpose**: Stores attached chat files into the conversation workspace before the agent turn runs.

**Data flow**: It pairs each upload with its chosen path and streams the upload bytes into core workspace storage.

**Call relations**: `chat` calls it just before admitting the message, and it reads bytes through `_upload_chunks`.

*Call graph*: calls 2 internal fn (write_workspace_file, _upload_chunks); called by 1 (chat).


##### `_files_note`  (lines 685–688)

```
def _files_note(text: str, paths: tuple[str, ...]) -> str
```

**Purpose**: Adds a clear note to the admitted chat text naming where uploaded files were saved.

**Data flow**: It receives the member’s text and saved paths, builds an attachment note, and appends it or returns it alone if there was no text.

**Call relations**: `chat` uses it so the agent can see the file locations inside the workspace.

*Call graph*: called by 1 (chat).


##### `_upload_chunks`  (lines 691–693)

```
async def _upload_chunks(upload: UploadFile) -> AsyncIterator[bytes]
```

**Purpose**: Streams an uploaded file in fixed-size pieces instead of reading the whole file at once.

**Data flow**: It repeatedly reads chunks from an upload until empty and yields each byte chunk.

**Call relations**: _deliver_uploads passes this async byte stream to core file writing.

*Call graph*: called by 1 (_deliver_uploads); 1 external calls (read).


##### `_answer_headers`  (lines 696–708)

```
def _answer_headers(request: Request) -> tuple[UUID, int] | None | Response
```

**Purpose**: Reads the special headers used when a member answers a question the agent asked. It validates them before any conversation mutation happens.

**Data flow**: It checks for the answer-turn header, parses the turn ID and question index, and returns the pair, nothing, or a bad-request response.

**Call relations**: `chat` calls this after basic message parsing and uses the result to build an idempotency key, so double-clicked answers join the same admission.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `_stop_header`  (lines 711–720)

```
def _stop_header(request: Request) -> UUID | None | Response
```

**Purpose**: Reads the special header used when a member presses stop on a running turn.

**Data flow**: It checks for the stop-turn header, parses it as a UUID, and returns the turn ID, nothing, or a bad-request response.

**Call relations**: `chat` calls this before accepting message content so a malformed stop request cannot create or alter a conversation.

*Call graph*: called by 1 (chat); 2 external calls (Response, UUID).


##### `chat`  (lines 723–818)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Main POST endpoint for sending a web chat message, answering an agent question, uploading files, opening a new conversation, or stopping a running turn.

**Data flow**: It authenticates the member, checks agent access, parses the body and special headers, opens or validates the conversation, saves uploads, admits the message to core or stops a turn, and returns IDs and status JSON.

**Call relations**: This is the web composer’s write path. It coordinates helpers for audience, conversation ownership, uploads, context, answer idempotency, and core admission.

*Call graph*: calls 16 internal fn (admit, admitted_body, stop_turn, _agent_param, _answer_headers, _audience_for, _chat_source, _deliver_uploads, _files_note, _inbox_paths (+6 more)); 5 external calls (JSONResponse, Response, web_extension, UUID, uuid4).


##### `_rendered_text`  (lines 821–832)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Turns an internal message into text suitable for the portal transcript. It strips hidden context wrappers from user messages.

**Data flow**: It reads string or block message content, joins visible text, removes internal context markers from user messages, trims whitespace, and returns display text.

**Call relations**: _rendered_messages uses this for every stored message before deciding whether to draw it.

*Call graph*: called by 1 (_rendered_messages).


##### `_tool_event`  (lines 835–848)

```
def _tool_event(block: ToolUseBlock) -> dict[str, str]
```

**Purpose**: Converts a tool-use block into a small UI event describing a tool call or skill load. It hides the internal requester marker before summarizing.

**Data flow**: It copies the block without the private `requested_by` input, asks the hub helper what kind of activity it represents, and returns a display dictionary.

**Call relations**: _rendered_messages and `_subagent_activity` use it so transcripts show useful work markers without exposing internal metadata.

*Call graph*: called by 2 (_rendered_messages, _subagent_activity); 2 external calls (model_copy, tool_activity).


##### `_subagent_activity`  (lines 865–887)

```
def _subagent_activity(messages: tuple[Message, ...]) -> list[dict[str, str]]
```

**Purpose**: Extracts the visible work done inside a subagent run: notes, tools, and skills in order.

**Data flow**: It scans messages for active tool results, then walks assistant block messages and emits note or tool event entries up to a limit.

**Call relations**: _subagent_nodes calls it after reading each child run’s transcript.

*Call graph*: calls 1 internal fn (_tool_event); called by 1 (_subagent_nodes).


##### `_finish_payload`  (lines 890–900)

```
def _finish_payload(answer: str) -> dict[str, JsonValue] | None
```

**Purpose**: Attempts to decode a subagent finish answer as a structured JSON object. If it is ordinary text, it leaves it alone.

**Data flow**: It receives an answer string, parses JSON, and returns a dictionary payload or nothing.

**Call relations**: _run_answer calls it before deciding how to render a run’s final answer.

*Call graph*: called by 1 (_run_answer); 1 external calls (loads).


##### `_payload_prose`  (lines 903–926)

```
def _payload_prose(value: JsonValue) -> str
```

**Purpose**: Turns structured JSON values into readable prose for the portal. This prevents schema-shaped answers from appearing as raw JSON.

**Data flow**: It receives a JSON value and recursively renders strings, numbers, booleans, lists, and objects into plain text.

**Call relations**: _run_answer uses it when a finish payload has multiple fields or nontrivial structure.

*Call graph*: called by 1 (_run_answer); 2 external calls (items, strip).


##### `_run_answer`  (lines 929–945)

```
def _run_answer(answer: str) -> str
```

**Purpose**: Renders a subagent run’s final answer in the form members should read. Single prose fields become plain prose; richer payloads become labeled text.

**Data flow**: It receives the raw terminal answer, decodes a possible payload, selects or formats the content, and returns display text.

**Call relations**: _subagent_nodes uses it for child run cards, and `_conversation_messages` uses it when rendering conversations that were themselves subagent runs.

*Call graph*: calls 2 internal fn (_finish_payload, _payload_prose); called by 2 (_conversation_messages, _subagent_nodes).


##### `_subagent_nodes`  (lines 948–980)

```
async def _subagent_nodes(ctx: SurfaceContext, turns: tuple[Turn, ...]) -> SubagentRuns
```

**Purpose**: Builds a nested tree of subagent runs spawned by turns in a conversation. Each child can include its own work and further children.

**Data flow**: It receives turn records, identifies spawned subagent turns, reads recent child transcripts concurrently, fills activity events, nests children under parents, and returns a mapping keyed by parent turn.

**Call relations**: _conversation_messages uses it for transcript cards, and `_events` uses it when a live turn finishes and needs to stream subagent results.

*Call graph*: calls 3 internal fn (read_transcript, _run_answer, _subagent_activity); called by 2 (_conversation_messages, _events); 2 external calls (__init__, gather).


##### `_rendered_messages`  (lines 983–1069)

```
def _rendered_messages(messages: tuple[Message, ...], subagents: SubagentRuns | None=None, turn_ids: frozenset[str]=frozenset(), agent_origin: frozenset[str]=frozenset(), speakers: Mapping[str, str] |
```

**Purpose**: Projects raw stored model messages into the chat bubbles, tool events, questions, speakers, and subagent cards the web portal displays.

**Data flow**: It receives messages plus optional subagent, turn, speaker, and question metadata; walks messages in order; groups assistant replies with events; and returns UI-ready message dictionaries.

**Call relations**: _conversation_messages is its caller and supplies the extra context needed to make transcripts consistent across chat, read-only views, and subagent pages.

*Call graph*: calls 2 internal fn (_rendered_text, _tool_event); called by 1 (_conversation_messages); 1 external calls (member_message_text).


##### `_rendered_messages.flush_reply`  (lines 1014–1030)

```
def flush_reply(include_subagents: bool) -> None
```

**Purpose**: Internal helper that closes the currently accumulated assistant reply and appends it to the rendered transcript when there is something to show.

**Data flow**: It reads the surrounding function’s pending assistant text, tool events, subagent runs, and question data, emits one assistant message dictionary, then clears the pending state.

**Call relations**: Only `_rendered_messages` uses it while switching between user turns and assistant replies.


##### `_conversation_messages`  (lines 1072–1195)

```
async def _conversation_messages(ctx: SurfaceContext, conversation_id: UUID, viewer: UUID) -> tuple[list[dict[str, object]], Turn | None]
```

**Purpose**: Builds the portal’s canonical rendered transcript for a conversation, including settled messages, running turns, queued member messages, speakers, questions, files, and subagent output.

**Data flow**: It reads the stored transcript, latest turn, turn details, spawned runs, queue rows, speakers, and agent-origin markers; renders committed messages; appends running and queued messages; and returns messages plus the latest turn.

**Call relations**: `transcript`, `conversation_transcript`, and `subagent_conversation` all call this so every portal view shows conversations the same way.

*Call graph*: calls 11 internal fn (agent_origin_refs, arrival_speakers, conversation_subagent_turns, latest_turn, list_turns, queued_arrivals, read_transcript, turn_detail, _rendered_messages, _run_answer (+1 more)); called by 3 (conversation_transcript, subagent_conversation, transcript); 2 external calls (gather, member_message_text).


##### `transcript`  (lines 1198–1225)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the rendered transcript for one of the member’s own web chats with a specific agent.

**Data flow**: It authenticates, checks agent access, parses the conversation ID, verifies ownership with `_own_chat`, renders messages, and includes the active turn ID or open handoffs when relevant.

**Call relations**: The live chat page calls this on load or reload before attaching to the stream route.

*Call graph*: calls 5 internal fn (_agent_param, _audience_for, _conversation_messages, _open_handoffs, _own_chat); 4 external calls (JSONResponse, Response, web_extension, UUID).


##### `_open_handoffs`  (lines 1228–1243)

```
async def _open_handoffs(ctx: SurfaceContext, turn_id: UUID, terminal: TerminalFrame) -> dict[str, object]
```

**Purpose**: Finds any unfinished handoffs from the newest committed turn, such as credential prompts or shared files, so reloads show the same affordances as the live stream.

**Data flow**: It inspects a terminal frame, asks for pending credential prompts if present, asks for turn files, and returns a small payload of available handoffs.

**Call relations**: `transcript` uses it when the latest turn has already ended but still leaves member-visible follow-up actions.

*Call graph*: calls 2 internal fn (_pending_prompts, _turn_files); called by 1 (transcript).


##### `chats_index`  (lines 1246–1308)

```
async def chats_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Builds the chat rail: the member’s own conversations and readable conversations from others across visible agents.

**Data flow**: It authenticates, optionally resolves a permalink request, lists conversations per agent and participation side, filters web chats through stored chat records, formats rows, sorts by recent activity, and returns JSON.

**Call relations**: The portal rail calls this often. It delegates permalink handling to `_resolve_chat` and uses `_chat_row_key` to distinguish this web surface’s conversations from other surfaces.

*Call graph*: calls 5 internal fn (list_agent_conversations, _audience_for, _chat_row_key, _iso, _resolve_chat); 2 external calls (JSONResponse, web_extension).


##### `_resolve_chat`  (lines 1311–1377)

```
async def _resolve_chat(ctx: SurfaceContext, store: ScopedStore, audience: WebAudience, member_id: UUID, email: str, requested: str) -> Response
```

**Purpose**: Resolves a `#/c/<id>` permalink into either a web chat rail row or a read-only conversation projection.

**Data flow**: It parses the requested ID, checks whether it is the member’s own web chat, otherwise finds the owning agent and asks whether the member may list/read it, then returns either chats, a conversation row, or an empty result.

**Call relations**: `chats_index` calls it when the browser asks for a specific conversation rather than the full rail.

*Call graph*: calls 8 internal fn (conversation_agent, latest_turn, list_agent_conversations, turn_detail, _conversation_row, _iso, _named, _own_chat); called by 1 (chats_index); 2 external calls (JSONResponse, UUID).


##### `_panel_gate`  (lines 1380–1393)

```
async def _panel_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience, UUID] | Response
```

**Purpose**: Shared gate for per-agent panel routes. It proves the session, computes the web audience, and validates that the path’s agent is visible to the member.

**Data flow**: It authenticates through `_audience_for`, parses the agent path parameter, checks audience access, and returns member ID, email, audience, and agent ID or a 404 response.

**Call relations**: Agent panels, intents, object-related conversation reads, skills, usage, connections, and overviews use this one helper so they refuse inaccessible agents consistently.

*Call graph*: calls 2 internal fn (_agent_param, _audience_for); called by 9 (_readable_conversation, community_skill, community_skills, connections, conversations, intents, overview, skills, usage); 1 external calls (Response).


##### `_iso`  (lines 1396–1397)

```
def _iso(moment: datetime | None) -> str | None
```

**Purpose**: Converts optional datetimes into JSON-friendly ISO strings.

**Data flow**: It receives a datetime or nothing and returns `None` or the datetime’s ISO-formatted string.

**Call relations**: Many response builders use it when serializing timestamps for chats, usage, objects, artifacts, memory, and radar runs.

*Call graph*: called by 8 (_conversation_row, _memory_rows, _radar_run, _resolve_chat, _usage_payload, chats_index, object_detail, workspace_artifacts); 1 external calls (isoformat).


##### `_window_param`  (lines 1400–1416)

```
def _window_param(request: Request) -> int | None | Response
```

**Purpose**: Parses the usage time window requested by the browser. It accepts named ranges like 7d or a bounded number of seconds.

**Data flow**: It reads query parameters, validates the named range or integer window, and returns seconds, all-time as `None`, or a bad-request response.

**Call relations**: `usage` and `workspace_usage` call it before asking core for spend reports.

*Call graph*: called by 2 (usage, workspace_usage); 1 external calls (Response).


##### `_usage_payload`  (lines 1419–1459)

```
def _usage_payload(report: AgentSpendReport | MemberSpendReport | SpendReport) -> dict[str, object]
```

**Purpose**: Formats common token and cost usage details for JSON responses.

**Data flow**: It receives an agent, member, or workspace spend report and converts selected/all-time totals, daily lines, execution lines, and model lines into dictionaries.

**Call relations**: Agent usage and workspace usage share this helper so their detailed usage sections have the same shape.

*Call graph*: calls 1 internal fn (_iso); called by 2 (usage, workspace_usage).


##### `skills`  (lines 1462–1482)

```
async def skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the skills loadable by the selected agent, including agent-specific and shared deploy skills.

**Data flow**: It passes through `_panel_gate`, asks core for the agent’s skills, and serializes name, description, origin, and instructions.

**Call relations**: This backs the agent skills panel in the portal.

*Call graph*: calls 2 internal fn (agent_skills, _panel_gate); 1 external calls (JSONResponse).


##### `_community_refusal`  (lines 1488–1492)

```
def _community_refusal(fault: Exception) -> Response
```

**Purpose**: Turns a community directory failure into a member-readable 502 response.

**Data flow**: It receives an exception, uses its text as the response body, marks it with a refusal header, and returns the response.

**Call relations**: Community skill listing and detail routes call it when the external community service is unavailable or errors.

*Call graph*: called by 2 (community_skill, community_skills); 1 external calls (Response).


##### `community_skills`  (lines 1499–1515)

```
async def community_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a page of community skill directory results for review in an agent panel.

**Data flow**: It gates the agent, validates the optional search query length, asks the community directory for listing results, and returns skill summaries or a readable service-error response.

**Call relations**: The browser uses this to discover candidate skills; actual installation still goes through the intent route.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, listing).


##### `community_skill`  (lines 1518–1539)

```
async def community_skill(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Fetches one community skill document so the member can review its description and instructions.

**Data flow**: It gates the agent, validates owner/repo/skill path shapes, fetches the document from the community directory, and returns JSON, 404, or a readable service-error response.

**Call relations**: This supports the detail view behind community skill search results.

*Call graph*: calls 2 internal fn (_community_refusal, _panel_gate); 3 external calls (JSONResponse, Response, fetch).


##### `workspace_memory`  (lines 1542–1607)

```
async def workspace_memory(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows or searches memory items reachable by the signed-in member across their visible agents.

**Data flow**: It authenticates, checks memory availability, either pages recent memory by subject/kind/cursor or searches each visible agent concurrently, deduplicates matches, formats rows, and returns JSON.

**Call relations**: The workspace memory page calls this. It uses `_memory_rows` for response shape and core memory readers for the actual data.

*Call graph*: calls 5 internal fn (recent_memory, search_memory, decode, _audience_for, _memory_rows); 6 external calls (__init__, gather, audience_subjects, conversation_audience, JSONResponse, Response).


##### `_memory_rows`  (lines 1610–1620)

```
def _memory_rows(found: tuple[MemoryMatch, ...]) -> list[dict[str, object]]
```

**Purpose**: Formats memory search or listing results for the web API.

**Data flow**: It receives memory matches and returns dictionaries with kind, text, optional reference, timestamp, and subject.

**Call relations**: Only `workspace_memory` uses it for both recent-memory and search responses.

*Call graph*: calls 1 internal fn (_iso); called by 1 (workspace_memory).


##### `usage`  (lines 1623–1660)

```
async def usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns usage and cap information for one selected agent, but only when the member has an explicit grant or is an admin.

**Data flow**: It gates the agent, checks the grant condition, parses the usage window, asks core for agent spend, formats totals, caps, dimensions, and detailed usage, and returns JSON.

**Call relations**: This backs the per-agent usage panel and shares parsing/formatting with workspace usage.

*Call graph*: calls 4 internal fn (agent_spend, _panel_gate, _usage_payload, _window_param); 2 external calls (JSONResponse, Response).


##### `connections`  (lines 1663–1672)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connector accounts visible for a selected agent, such as private member grants or agent-shared connections.

**Data flow**: It gates the agent, asks core for visible agent connections for the member/admin view, and returns serialized entries.

**Call relations**: The agent connections panel calls this route.

*Call graph*: calls 2 internal fn (list_agent_connections, _panel_gate); 1 external calls (JSONResponse).


##### `connection_pool`  (lines 1675–1685)

```
async def connection_pool(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the member’s visible connection pool across the workspace, trimming each connection’s agent list to agents the web audience can see.

**Data flow**: It authenticates, asks core for workspace connections, filters each entry’s agents by the audience, serializes the entries, and returns JSON.

**Call relations**: This supports workspace-level connection views rather than one agent’s connection panel.

*Call graph*: calls 2 internal fn (list_connections, _audience_for); 1 external calls (JSONResponse).


##### `github_coverage`  (lines 1688–1694)

```
async def github_coverage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns GitHub connection coverage information for the signed-in member or admin.

**Data flow**: It authenticates, asks core for GitHub coverage using the member ID and admin flag, and returns the model as JSON.

**Call relations**: This is a workspace-level read used by connection or source setup UI.

*Call graph*: calls 2 internal fn (github_coverage, _audience_for); 1 external calls (JSONResponse).


##### `conversations`  (lines 1697–1717)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists conversations visible to the member for one selected agent, with optional search.

**Data flow**: It gates the agent, reads a bounded search string, asks core for visible conversations, formats each row, and returns JSON.

**Call relations**: The agent conversations panel calls it, and `_conversation_row` provides the common row shape.

*Call graph*: calls 4 internal fn (list_agent_conversations, _conversation_row, _panel_gate, _searched); 1 external calls (JSONResponse).


##### `_searched`  (lines 1720–1724)

```
def _searched(request: Request) -> str | None
```

**Purpose**: Reads and bounds a search string from a listing request.

**Data flow**: It takes the `q` query parameter, trims it, cuts it to the maximum length, and returns the string or nothing.

**Call relations**: Agent and subagent conversation listing routes use it before asking core to search.

*Call graph*: called by 2 (conversations, subagent_conversations).


##### `_conversation_row`  (lines 1727–1758)

```
def _conversation_row(entry: ListedConversation, agent: dict[str, str] | None=None) -> dict[str, object]
```

**Purpose**: Formats one conversation listing row for portal panels and cross-agent views.

**Data flow**: It receives a listed conversation and optional agent info, then returns IDs, titles, source labels, speakers, timestamps, counts, and readability/disclosure flags.

**Call relations**: Conversation listing, chat permalink resolution, and subagent run formatting all call it to keep row shape consistent.

*Call graph*: calls 1 internal fn (_iso); called by 3 (_resolve_chat, _subagent_run_row, conversations).


##### `_readable_conversation`  (lines 1761–1781)

```
async def _readable_conversation(ctx: SurfaceContext, request: Request, conversation_id: UUID | None=None) -> tuple[UUID, UUID, 'SlotViewer'] | Response
```

**Purpose**: Authorizes a content read for one conversation under one agent. It hides all unreadable or mismatched cases behind the same 404-style response.

**Data flow**: It gates the agent, parses or receives a conversation ID, asks core whether the member/admin may read it, and returns agent ID, conversation ID, and viewer info or an error response.

**Call relations**: Read-only transcript and conversation slot routes use it as their shared content gate.

*Call graph*: calls 2 internal fn (readable_conversation, _panel_gate); called by 2 (_slot_target, conversation_transcript); 3 external calls (__init__, Response, UUID).


##### `conversation_transcript`  (lines 1784–1794)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a read-only rendered transcript for an authorized conversation, including conversations from other surfaces or disclosed admin reads.

**Data flow**: It authorizes the conversation with `_readable_conversation`, renders messages through `_conversation_messages`, and returns them as JSON.

**Call relations**: This is separate from the web-chat `transcript` route because it reads conversations rather than continuing them.

*Call graph*: calls 2 internal fn (_conversation_messages, _readable_conversation); 1 external calls (JSONResponse).


##### `_slot_target`  (lines 1811–1835)

```
async def _slot_target(ctx: SurfaceContext, request: Request) -> SlotTarget | Response
```

**Purpose**: Finds and authorizes the conversation whose typed slots are being read. It also supports reading a subagent child through an authorized root conversation.

**Data flow**: It parses root and conversation IDs, uses `_readable_conversation` for the root or direct conversation, verifies child-run membership when needed, and returns a `SlotTarget` or error response.

**Call relations**: Both `conversation_slots` and `conversation_slot` call it before preparing slot context.

*Call graph*: calls 2 internal fn (conversation_subagent_turns, _readable_conversation); called by 2 (conversation_slot, conversation_slots); 3 external calls (__init__, Response, UUID).


##### `_slot_context`  (lines 1838–1854)

```
async def _slot_context(ctx: SurfaceContext, target: SlotTarget, ext: ExtensionContext) -> ConversationSlotContext | None
```

**Purpose**: Builds the context object passed to a conversation slot provider. A slot provider is a plugin-like reader that returns typed extra content for a conversation.

**Data flow**: It reads the conversation audience and transcript, combines them with extension context, conversation ID, agent ID, and public URL, and returns a `ConversationSlotContext` or nothing.

**Call relations**: Slot list and slot detail routes call it before projecting host data and invoking providers.

*Call graph*: calls 2 internal fn (conversation_audience, read_transcript); called by 2 (conversation_slot, conversation_slots); 2 external calls (__init__, replace).


##### `_project_slot_context`  (lines 1857–1942)

```
async def _project_slot_context(ctx: SurfaceContext, slot_context: ConversationSlotContext, extension: str, content: type[BaseModel], root_conversation_id: UUID | None, viewer: SlotViewer) -> Conversa
```

**Purpose**: Adds host-prepared projections to slot context for special built-in slot types such as changes, artifacts, sites, and automations.

**Data flow**: It inspects the extension name and expected payload type, reads needed core data, builds projection or visible-item lists, and returns an updated slot context.

**Call relations**: Conversation slot summary and detail routes call this before asking providers to summarize or read payloads.

*Call graph*: calls 5 internal fn (artifact_link, artifact_preview_link, conversation_changes, list_conversation_artifacts, list_conversation_member_objects); called by 2 (conversation_slot, conversation_slots); 8 external calls (__init__, __init__, __init__, __init__, replace, raster_image_media_type, urlsplit, urlunsplit).


##### `_authorized_slot_payload`  (lines 1945–1989)

```
def _authorized_slot_payload(payload: ConversationSlotPayload, context: ConversationSlotContext) -> ConversationSlotPayload
```

**Purpose**: Filters a slot payload so it only includes items this viewer is authorized to see. For automations, it can hide sensitive details while still showing the row exists.

**Data flow**: It receives a payload and context visible-items list, removes unauthorized sites or automations, blanks hidden automation fields where needed, and returns the adjusted payload.

**Call relations**: `conversation_slot` applies this after reading a provider payload and before returning JSON.

*Call graph*: called by 1 (conversation_slot); 1 external calls (model_copy).


##### `conversation_slots`  (lines 1992–2036)

```
async def conversation_slots(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists which typed conversation slots are available for an authorized conversation and how many items each contains.

**Data flow**: It authorizes the target, builds shared slot context, loops over registered slot providers, projects context as needed, asks each provider for a summary count, logs failures, and returns visible slot summaries.

**Call relations**: The transcript UI calls this to know which extra panels, such as files or changes, to show for a conversation.

*Call graph*: calls 4 internal fn (summarize_conversation_slot, _project_slot_context, _slot_context, _slot_target); 4 external calls (replace, JSONResponse, Response, log).


##### `conversation_slot`  (lines 2039–2066)

```
async def conversation_slot(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full typed payload for one conversation slot.

**Data flow**: It authorizes the target, finds the requested provider, builds and projects context, reads the payload, checks it has the expected type, filters it for authorization, and returns JSON.

**Call relations**: The browser calls this after selecting one slot from `conversation_slots`.

*Call graph*: calls 5 internal fn (read_conversation_slot, _authorized_slot_payload, _project_slot_context, _slot_context, _slot_target); 2 external calls (JSONResponse, Response).


##### `_changes_projection`  (lines 2069–2072)

```
def _changes_projection(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Retrieves the prepared workspace-changes projection from slot context and fails if the wrong projection was supplied.

**Data flow**: It receives slot context, verifies the projection is `WorkspaceChanges`, and returns it.

**Call relations**: The built-in changes slot read and summarize callbacks use it.

*Call graph*: called by 2 (_read_changes, _summarize_changes).


##### `_read_changes`  (lines 2075–2076)

```
async def _read_changes(ctx: ConversationSlotContext) -> WorkspaceChanges
```

**Purpose**: Read callback for the built-in changes slot.

**Data flow**: It receives slot context and returns the workspace changes projection.

**Call relations**: It is registered in `CHANGES_SLOT` and is called through the conversation slot provider machinery.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_summarize_changes`  (lines 2079–2080)

```
async def _summarize_changes(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Summary callback for the built-in changes slot.

**Data flow**: It receives slot context, counts projected changes, and returns the count or nothing when there are none.

**Call relations**: It is registered in `CHANGES_SLOT` and used by `conversation_slots` through core slot summarization.

*Call graph*: calls 1 internal fn (_changes_projection).


##### `_artifacts_projection`  (lines 2093–2096)

```
def _artifacts_projection(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Retrieves the prepared artifacts projection from slot context and fails if it is missing or wrong.

**Data flow**: It receives slot context, verifies the projection is `ArtifactsSlotPayload`, and returns it.

**Call relations**: The built-in artifacts slot read and summarize callbacks use it.

*Call graph*: called by 2 (_read_artifacts, _summarize_artifacts).


##### `_read_artifacts`  (lines 2099–2100)

```
async def _read_artifacts(ctx: ConversationSlotContext) -> ArtifactsSlotPayload
```

**Purpose**: Read callback for the built-in artifacts slot.

**Data flow**: It receives slot context and returns the artifacts projection.

**Call relations**: It is registered in `ARTIFACTS_SLOT` and called through the conversation slot provider machinery.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_summarize_artifacts`  (lines 2103–2105)

```
async def _summarize_artifacts(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Summary callback for the built-in artifacts slot.

**Data flow**: It receives slot context, counts projected artifacts, and returns the count or nothing when there are none.

**Call relations**: It is registered in `ARTIFACTS_SLOT` and used when `conversation_slots` builds the slot list.

*Call graph*: calls 1 internal fn (_artifacts_projection).


##### `_subagent_gate`  (lines 2118–2132)

```
async def _subagent_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, SubagentDetail] | Response
```

**Purpose**: Shared gate for subagent profile pages. It authenticates the member and verifies that the named subagent profile exists in this deployment.

**Data flow**: It authenticates through `_audience_for`, looks up the profile by path name, and returns member ID, audience, and profile or a not-found response.

**Call relations**: All subagent overview, skills, listing, and transcript routes start here.

*Call graph*: calls 2 internal fn (subagent, _audience_for); called by 4 (subagent_conversation, subagent_conversations, subagent_overview, subagent_skills); 1 external calls (Response).


##### `_reachable_agents`  (lines 2135–2139)

```
def _reachable_agents(audience: WebAudience) -> frozenset[UUID]
```

**Purpose**: Returns the set of agent IDs the member’s web audience can reach.

**Data flow**: It receives a web audience and builds a frozen set of IDs from its agents.

**Call relations**: Subagent conversation listing and detail routes pass this set to core so subagent work is scoped to visible parent agents.

*Call graph*: called by 2 (subagent_conversation, subagent_conversations).


##### `subagent_overview`  (lines 2142–2150)

```
async def subagent_overview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns configuration details for one subagent profile, such as model, prompt, limits, and trust behavior.

**Data flow**: It gates the subagent profile and serializes the profile model to JSON.

**Call relations**: This backs the subagent overview page.

*Call graph*: calls 1 internal fn (_subagent_gate); 1 external calls (JSONResponse).


##### `subagent_skills`  (lines 2153–2168)

```
async def subagent_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists deploy-level skills that a subagent profile can load.

**Data flow**: It gates the profile, checks whether it loads skills, returns deploy skills when enabled, and includes the load flag.

**Call relations**: The subagent skills panel calls this; agent-authored skills are shown on the agent skills panel instead.

*Call graph*: calls 1 internal fn (_subagent_gate); 1 external calls (JSONResponse).


##### `subagent_conversations`  (lines 2171–2189)

```
async def subagent_conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists conversations where a specific subagent profile ran, limited to parent agents and audiences the viewer may reach.

**Data flow**: It gates the profile, builds reachable agent IDs, reads a search string, asks core for visible subagent runs, formats rows, and returns JSON.

**Call relations**: This powers the subagent run listing page and uses `_subagent_run_row` for row formatting.

*Call graph*: calls 5 internal fn (list_subagent_conversations, _reachable_agents, _searched, _subagent_gate, _subagent_run_row); 1 external calls (JSONResponse).


##### `_subagent_run_row`  (lines 2192–2196)

```
def _subagent_run_row(run: SubagentRun) -> dict[str, object]
```

**Purpose**: Formats one subagent run as a conversation row with the parent agent named.

**Data flow**: It receives a subagent run and delegates to `_conversation_row` with agent ID and name attached.

**Call relations**: Subagent run lists and single-run responses use it for consistent headers.

*Call graph*: calls 1 internal fn (_conversation_row); called by 2 (subagent_conversation, subagent_conversations).


##### `subagent_conversation`  (lines 2199–2228)

```
async def subagent_conversation(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one readable subagent run transcript and its run header.

**Data flow**: It gates the profile, parses conversation and optional root IDs, asks core whether the viewer may read that run, renders the conversation messages, and returns run metadata plus messages.

**Call relations**: This backs both direct subagent run pages and child-run cards opened from a parent transcript.

*Call graph*: calls 5 internal fn (readable_subagent_conversation, _conversation_messages, _reachable_agents, _subagent_gate, _subagent_run_row); 3 external calls (JSONResponse, Response, UUID).


##### `workspace_credentials`  (lines 2231–2239)

```
async def workspace_credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists workspace credential slots the member can fill, without ever returning secret values.

**Data flow**: It authenticates, asks core for declared credential slots and fill state, serializes them, and returns JSON.

**Call relations**: The workspace credentials panel calls this; actual secret submission goes through `fulfill_credential`.

*Call graph*: calls 2 internal fn (list_credential_slots, _audience_for); 1 external calls (JSONResponse).


##### `workspace_team`  (lines 2242–2260)

```
async def workspace_team(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace roster and whether the current member can add people.

**Data flow**: It authenticates, reads audience admin status, asks core for members, and returns email/admin/seat rows plus a `can_add` flag.

**Call relations**: The team workspace page calls this and relies on the same admin truth used by mutation gates.

*Call graph*: calls 2 internal fn (list_members, _audience_for); 1 external calls (JSONResponse).


##### `workspace_sources`  (lines 2263–2274)

```
async def workspace_sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the member, such as private and shared knowledge sources.

**Data flow**: It authenticates, asks core for sources visible to the member/admin view, serializes the entries, and returns JSON.

**Call relations**: The workspace sources panel calls this.

*Call graph*: calls 2 internal fn (list_sources, _audience_for); 1 external calls (JSONResponse).


##### `workspace_artifacts`  (lines 2277–2322)

```
async def workspace_artifacts(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns a searchable, filterable page of files shared with the member.

**Data flow**: It authenticates, validates cursor and media filter parameters, asks core for artifact rows, adds download and preview links, and returns rows plus pagination cursors.

**Call relations**: The workspace artifacts page uses this for browsing shared files.

*Call graph*: calls 6 internal fn (artifact_link, artifact_preview_link, list_artifacts, decode, _audience_for, _iso); 2 external calls (JSONResponse, Response).


##### `_radar_run`  (lines 2325–2352)

```
def _radar_run(ctx: SurfaceContext, run: ScheduledRun, task_names: Mapping[UUID, str]) -> dict[str, object]
```

**Purpose**: Formats one scheduled run for the workspace radar page.

**Data flow**: It receives a scheduled run and task-name lookup, derives the task ID when possible, adds timestamps, status, source text, artifacts, and links, and returns a dictionary.

**Call relations**: `workspace_radar` calls it for each run after `_radar_task_names` has found visible task names.

*Call graph*: calls 3 internal fn (artifact_link, artifact_preview_link, _iso); called by 1 (workspace_radar); 1 external calls (scheduled_fire_task_id).


##### `_radar_task_names`  (lines 2355–2378)

```
async def _radar_task_names(ctx: SurfaceContext, audience: WebAudience, member_id: UUID, runs: tuple[ScheduledRun, ...]) -> dict[UUID, str]
```

**Purpose**: Looks up names for scheduled tasks that produced visible scheduled runs.

**Data flow**: It receives visible runs, finds the agents involved, lists scheduled-task objects for those agents, and builds a map from task UUID to task name.

**Call relations**: `workspace_radar` uses this so run rows can show a task name when the task still exists and is visible.

*Call graph*: calls 1 internal fn (list_member_objects); called by 1 (workspace_radar); 2 external calls (__init__, UUID).


##### `workspace_radar`  (lines 2381–2416)

```
async def workspace_radar(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists scheduled or automatic runs visible to the member, optionally narrowed to one visible agent.

**Data flow**: It authenticates, validates cursor and agent filters, asks core for scheduled runs, resolves task names, formats rows, and returns pagination JSON.

**Call relations**: The workspace radar page calls this to show what ran without a direct chat click.

*Call graph*: calls 5 internal fn (list_scheduled_runs, decode, _audience_for, _radar_run, _radar_task_names); 3 external calls (JSONResponse, Response, UUID).


##### `workspace_usage`  (lines 2419–2494)

```
async def workspace_usage(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the member’s own usage and caps, and for admins also the workspace-wide spend rollup.

**Data flow**: It authenticates, parses the usage window, reads member spend, formats the common usage payload, and if admin reads and attaches workspace rollup breakdowns.

**Call relations**: This backs the workspace usage page and shares `_window_param` and `_usage_payload` with per-agent usage.

*Call graph*: calls 5 internal fn (member_spend, spend_rollup, _audience_for, _usage_payload, _window_param); 1 external calls (JSONResponse).


##### `_member_turn`  (lines 2497–2521)

```
async def _member_turn(ctx: SurfaceContext, request: Request) -> tuple[UUID, UUID] | Response
```

**Purpose**: Authorizes access to one live turn for streaming or stopping-related display. The turn must belong to the member and still be under a visible agent.

**Data flow**: It authenticates, parses the turn ID, checks core ownership, reads turn detail, checks audience access to the agent, and returns member ID plus turn ID or a refusal response.

**Call relations**: `stream` calls it before opening the live Server-Sent Events tail.

*Call graph*: calls 3 internal fn (turn_detail, turn_owner, _audience_for); called by 1 (stream); 2 external calls (Response, UUID).


##### `stream`  (lines 2524–2532)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens the live event stream for one turn. Server-Sent Events let the browser receive a sequence of updates over one HTTP response.

**Data flow**: It authorizes the turn, reads the browser’s last received event ID, and returns a streaming response driven by `_events`.

**Call relations**: The chat UI connects here after admission or transcript load to follow a running turn.

*Call graph*: calls 2 internal fn (_events, _member_turn); 1 external calls (StreamingResponse).


##### `_event`  (lines 2535–2536)

```
def _event(name: str, payload: dict[str, object]) -> bytes
```

**Purpose**: Builds a named Server-Sent Event from a small JSON payload.

**Data flow**: It receives an event name and dictionary, JSON-encodes the payload, and returns bytes in SSE format.

**Call relations**: _events uses it for extra web-specific events such as subagent cards, connection prompts, credential prompts, and files.

*Call graph*: called by 1 (_events); 1 external calls (dumps).


##### `_pending_prompts`  (lines 2539–2552)

```
async def _pending_prompts(ctx: SurfaceContext, request_: CredentialRequest) -> dict[str, object] | None
```

**Purpose**: Filters a credential request down to prompts that are still waiting for member input.

**Data flow**: It checks each prompt’s slot through core, keeps pending ones, and returns reason/seal/prompts or nothing if all are fulfilled or expired.

**Call relations**: _events uses it during live streaming, and `_open_handoffs` uses it after reload.

*Call graph*: calls 1 internal fn (credential_prompt_pending); called by 2 (_events, _open_handoffs).


##### `_turn_files`  (lines 2555–2564)

```
async def _turn_files(ctx: SurfaceContext, turn_id: UUID) -> list[dict[str, object]]
```

**Purpose**: Lists files shared by a completed turn with links the browser can open.

**Data flow**: It asks core for shared artifacts for the turn and returns filename, subject, size, and link for each.

**Call relations**: _events streams these when a turn ends, and `_open_handoffs` includes them on transcript reload.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); called by 2 (_events, _open_handoffs).


##### `_events`  (lines 2567–2602)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Produces the live SSE byte stream for a turn, adding web-specific side events around the core live frames.

**Data flow**: It tails core frames from a cursor, and when a terminal frame arrives it may read subagent results, connection URLs, credential prompts, and shared files before yielding the core frame in SSE format.

**Call relations**: `stream` returns this iterator to the browser. It uses `_sse` for core frames and `_event` for extra named portal events.

*Call graph*: calls 9 internal fn (connect_url, conversation_subagent_turns, tail, turn_detail, _event, _pending_prompts, _sse, _subagent_nodes, _turn_files); called by 1 (stream).


##### `fulfill_credential`  (lines 2605–2633)

```
async def fulfill_credential(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores one credential value entered privately by the member in response to a credential prompt.

**Data flow**: It authenticates, bounds and parses the form, validates sealed token, slot, and value, enforces a secret-size limit, asks core to fulfill the sealed request, and returns stored status or refusal.

**Call relations**: Credential prompts come from transcripts or live events; this route is the private write path that completes them without adding the secret to chat history.

*Call graph*: calls 4 internal fn (fulfill_credential_request, _authenticate, _form, _framed_length); 2 external calls (JSONResponse, Response).


##### `admin_index`  (lines 2636–2687)

```
async def admin_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the web administration dashboard data for workspace admins only.

**Data flow**: It authenticates, checks admin status, reads installations, web grants, seat snapshot, spend caps, deployment extension info, and agent details, then returns one dashboard JSON object.

**Call relations**: The admin page calls this; non-admins receive not-found so the page is effectively invisible.

*Call graph*: calls 3 internal fn (list_installations, spend_caps, _audience_for); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


##### `_object_gate`  (lines 2690–2704)

```
async def _object_gate(ctx: SurfaceContext, request: Request) -> tuple[UUID, WebAudience, PortalKind] | Response
```

**Purpose**: Shared gate for generic object index and detail pages. It authenticates and verifies that the requested object kind exists.

**Data flow**: It authenticates, reads the `kind` path parameter, asks core for the kind description, and returns member ID, audience, and kind metadata or a 404 response.

**Call relations**: `object_index` and `object_detail` both start here so unknown kinds and audience setup behave the same.

*Call graph*: calls 2 internal fn (object_kind, _audience_for); called by 2 (object_detail, object_index); 1 external calls (Response).


##### `_object_agent`  (lines 2707–2717)

```
def _object_agent(request: Request, audience: WebAudience) -> AgentSummary | Response
```

**Purpose**: Chooses the agent namespace for a generic object read and checks that the viewer can access that agent.

**Data flow**: It parses the `agent` query parameter as a UUID, finds the matching agent in the audience, and returns the agent summary or a 404 response.

**Call relations**: Object index uses it when scoped to one agent; object detail always uses it because one object detail lives in one agent namespace.

*Call graph*: called by 2 (object_detail, object_index); 2 external calls (Response, UUID).


##### `_kind_payload`  (lines 2720–2727)

```
def _kind_payload(kind: PortalKind) -> dict[str, object]
```

**Purpose**: Builds common metadata about an object kind for portal responses.

**Data flow**: It receives a kind description and returns kind name, list fields, spec schema, and whether apply/delete intents are supported.

**Call relations**: Object index and detail include this so the browser knows how to draw the kind and which panel actions are possible.

*Call graph*: calls 2 internal fn (applying_kinds, deleting_kinds); called by 2 (object_detail, object_index).


##### `_filter_value`  (lines 2730–2737)

```
def _filter_value(raw: str) -> JsonValue
```

**Purpose**: Parses one object-list filter value from the query string into the kind of scalar the object rows may store.

**Data flow**: It tries to JSON-decode the raw string, returning booleans or numbers when written that way, and falls back to the original string.

**Call relations**: Object index uses it for all non-reserved query parameters before building the object list query.

*Call graph*: called by 1 (object_index); 1 external calls (loads).


##### `_merged_rank`  (lines 2740–2753)

```
def _merged_rank(row: dict[str, object], order_by: str) -> tuple[int, float | str, str]
```

**Purpose**: Ranks object rows from multiple agents into one sorted order.

**Data flow**: It reads the requested order field from a row, classifies missing values, booleans, numbers, and text, and returns a sortable tuple with name as a tie-breaker.

**Call relations**: `object_index` uses it only when reading across all visible agents, because each agent returns its own ordered page.


##### `object_index`  (lines 2756–2826)

```
async def object_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists rows of any registered object kind visible to the member, either for one agent or fanned out across all visible agents.

**Data flow**: It gates kind and audience, parses sorting, cursor, agent, search, and filters, asks core to list member-visible objects for each target agent, merges rows when needed, and returns objects plus kind metadata.

**Call relations**: This is the generic index route behind `objects/{kind}` and feeds many portal object pages without hard-coding each kind.

*Call graph*: calls 5 internal fn (list_member_objects, _filter_value, _kind_payload, _object_agent, _object_gate); 3 external calls (__init__, JSONResponse, Response).


##### `object_detail`  (lines 2829–2875)

```
async def object_detail(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one visible object’s detail, including its spec when allowed, live fields, typed links, and timestamps.

**Data flow**: It gates kind and audience, resolves the agent namespace, asks core for the object, checks linked objects for openability, and returns detail JSON or not-found.

**Call relations**: This is the generic detail route behind `objects/{kind}/{name}` and uses `_kind_payload` so detail pages share index metadata.

*Call graph*: calls 5 internal fn (member_object, _iso, _kind_payload, _object_agent, _object_gate); 2 external calls (JSONResponse, Response).


##### `_sse`  (lines 2878–2899)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Converts one core live frame into Server-Sent Event bytes the browser can consume. It includes the cursor as the event ID when present.

**Data flow**: It receives a cursor and live frame, chooses an SSE event name based on the frame type, serializes the frame to JSON, and returns bytes.

**Call relations**: _events calls it for every core frame after adding any extra portal-side events.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `intents`  (lines 2902–2907)

```
async def intents(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Receives the portal panels’ mutation requests for an agent and passes them to the intent system.

**Data flow**: It gates the agent, extracts member ID, email, and agent ID, and delegates the request to `submit_intent`, returning that response.

**Call relations**: This is the shared web write path for panel actions such as applying or deleting supported object kinds.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (submit_intent).


##### `overview`  (lines 2910–2918)

```
async def overview(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the selected agent’s overview information, such as configuration and deploy limits, as visible to this member.

**Data flow**: It gates the agent, keeps the audience admin flag, and delegates to `agent_overview` for the actual response.

**Call relations**: The agent overview panel calls this route; admin-only details are controlled by the audience flag passed through.

*Call graph*: calls 1 internal fn (_panel_gate); 1 external calls (agent_overview).


### `extensions/web/ufo_ext_web/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this folder as `ufo_ext_web` and import the web extension pieces inside it. Think of it like a label on a drawer: the drawer may contain useful tools, but this label only tells Python that the drawer exists and can be opened by name. Nothing runs from this file, and there are no functions, settings, or side effects here. Without it, depending on the Python version and packaging setup, imports for this web extension package might fail or behave less predictably.


### Portal integrations
Connects the portal to external community-skill browsing and internal panel actions that feed back into the normal chat write path.

### `extensions/web/ufo_ext_web/community.py`

`io_transport` · `request handling`

The Skills tab needs to show community skills from skills.sh, but that directory does not provide all useful information in one simple place. This file hides those details behind a small service object called CommunitySkills. When the user opens the Community view with no search text, it reads the public leaderboard page and extracts skill entries from the page payload. When the user searches, it calls the directory’s search API. In both cases it returns only the basic facts the listing can safely show: skill name, source repository, and install count. Descriptions are not fetched for every row because the directory only publishes them on each skill’s own document, and reading those one by one would quickly hit rate limits. When the user chooses one skill for install review, fetch reads that skill’s SKILL.md document and parses its front matter, which is a metadata block at the top of the file. The file also protects the app from slow or oversized remote responses by using timeouts and byte limits. It keeps short-lived caches for listings and process-long caches for fetched documents, like keeping recently read pages on a desk instead of walking back to the library every time. If the directory refuses or returns something unexpected, it raises CommunityUnavailable with a sentence meant to be shown to the user rather than silently returning an empty result.

#### Function details

##### `_refusal`  (lines 43–49)

```
def _refusal(code: int) -> CommunityUnavailable
```

**Purpose**: Turns an HTTP failure code from the skills directory into a user-readable error. It gives a special explanation for rate limiting, where the remote service says this deployment has read too often.

**Data flow**: It receives a numeric response code from the directory. If the code is 429, it creates an error explaining the hourly read limit; otherwise it creates an error saying what code the directory returned. The output is a CommunityUnavailable exception ready to be raised.

**Call relations**: The lower-level HTTP readers call this when the directory does not answer successfully. _search uses it after a failed search request, and _body uses it after a failed streamed download, so callers get one consistent kind of failure message.

*Call graph*: called by 2 (_body, _search); 1 external calls (__init__).


##### `CommunitySkills.listing`  (lines 79–89)

```
async def listing(self, query: str) -> list[CommunitySkill]
```

**Purpose**: Returns the list of community skills to show in the Skills tab. With no search text it shows the popular leaderboard; with search text it asks the directory for matching skills.

**Data flow**: It receives a query string and first checks the listing cache for a still-fresh answer. If cached data is available, it returns that immediately. Otherwise it opens an HTTP client, either reads the popular listing or performs a search, trims the result to the display limit, stores it in the cache with the current time, and returns the list of CommunitySkill objects.

**Call relations**: This is the public listing path used by the web route for the Community narrowing. It creates a client through _client, then hands the actual remote read to _popular or _search depending on whether the user typed a query.

*Call graph*: calls 3 internal fn (_client, _popular, _search); 1 external calls (monotonic).


##### `CommunitySkills.fetch`  (lines 91–116)

```
async def fetch(self, source: str, name: str) -> CommunityDocument | None
```

**Purpose**: Fetches the full document for one community skill so the Install review can show its description and instructions. It avoids repeated network reads by remembering each document for the life of the process.

**Data flow**: It receives a source repository and skill name, combines them into a cache key, and returns a cached document if one is already known. If not, it splits the source into owner and repository, downloads the skill package metadata, reads the JSON response, looks for the SKILL.md file, parses that file, stores either the parsed document or None in the cache, and returns the result.

**Call relations**: This is called when the user opens a specific skill rather than browsing the list. It uses _client to make an HTTP client, _body to safely download the remote response, and _parse to turn the SKILL.md text into a CommunityDocument.

*Call graph*: calls 3 internal fn (_body, _client, _parse); 2 external calls (__init__, loads).


##### `CommunitySkills._client`  (lines 118–119)

```
def _client(self, timeout: float) -> httpx.AsyncClient
```

**Purpose**: Builds the HTTP client used for calls to skills.sh. It centralizes timeout, redirect, and test-transport setup so every remote read behaves the same way.

**Data flow**: It receives a timeout value. It creates an asynchronous HTTP client with that timeout, follows redirects, and uses the optional injected transport if tests supplied one. The result is a client object used inside an async context.

**Call relations**: listing and fetch call this before talking to the remote directory. In production it creates a normal network client; in tests it can use a fake transport so tests do not need the real skills.sh service.

*Call graph*: called by 2 (fetch, listing); 1 external calls (AsyncClient).


##### `CommunitySkills._popular`  (lines 121–136)

```
async def _popular(self, client: httpx.AsyncClient) -> list[CommunitySkill]
```

**Purpose**: Reads the directory’s public leaderboard and turns it into ranked community skill rows. This is used when the user opens the Community tab without typing a search.

**Data flow**: It downloads the leaderboard page payload, scans the text for small JSON-looking skill entries, decodes each entry, and passes each one through _entry to validate and normalize it. It removes duplicates by skill source and name, raises a clear error if nothing usable was found, then returns the skills sorted by install count from highest to lowest.

**Call relations**: listing calls this for the no-query path. It relies on _body for the safe page download and _entry for turning loose remote data into clean CommunitySkill objects.

*Call graph*: calls 2 internal fn (_body, _entry); called by 1 (listing); 2 external calls (__init__, loads).


##### `CommunitySkills._search`  (lines 138–147)

```
async def _search(self, client: httpx.AsyncClient, query: str) -> list[CommunitySkill]
```

**Purpose**: Asks the skills.sh search API for skills matching the user’s query. It prepares those remote results in the same shape as the popular listing.

**Data flow**: It receives an HTTP client and the search text. It sends a GET request with the query and result limit, checks for a successful response, reads the JSON list of skills, normalizes each entry with _entry, drops unusable entries, sorts the rest by install count, and returns the list.

**Call relations**: listing calls this when the user has typed a search term. If the remote API refuses the request, it hands the status code to _refusal so the caller receives the same user-facing error style as other directory reads.

*Call graph*: calls 2 internal fn (_entry, _refusal); called by 1 (listing); 1 external calls (get).


##### `CommunitySkills._entry`  (lines 149–156)

```
def _entry(self, entry: object) -> CommunitySkill | None
```

**Purpose**: Turns one raw skill entry from the directory into a clean CommunitySkill object, or rejects it if it is missing the required pieces. This keeps messy remote data from leaking into the rest of the web app.

**Data flow**: It receives one unknown object from the remote response. If it is not a dictionary-like record, it returns None. Otherwise it reads the skill name, source repository, and install count, checks that the source looks like an owner/repository pair, and returns a CommunitySkill object when the entry is valid.

**Call relations**: _popular and _search both use this as their shared cleanup step. That means the leaderboard path and search path produce the same kind of safe, predictable listing rows.

*Call graph*: called by 2 (_popular, _search); 1 external calls (__init__).


##### `CommunitySkills._body`  (lines 158–178)

```
async def _body(self, client: httpx.AsyncClient, url: str, cap: int, headers: dict[str, str] | None=None) -> bytes
```

**Purpose**: Downloads a remote response body safely. It protects the web app from failed responses and from unexpectedly huge replies.

**Data flow**: It receives an HTTP client, a URL, a maximum allowed byte count, and optional request headers. It streams the response in chunks, checks that the response succeeded, counts bytes as they arrive, stops with a clear error if the response is too large, and finally returns all chunks joined together as bytes.

**Call relations**: _popular uses this to read the leaderboard page, and fetch uses it to read a skill document download response. When the directory returns a bad status, it calls _refusal; when the body is too large, it raises CommunityUnavailable directly.

*Call graph*: calls 1 internal fn (_refusal); called by 2 (_popular, fetch); 2 external calls (__init__, stream).


##### `CommunitySkills._parse`  (lines 180–199)

```
def _parse(self, document: str) -> CommunityDocument | None
```

**Purpose**: Extracts a skill’s name, description, and instructions from the text of SKILL.md. It only accepts documents that have the expected metadata block at the top.

**Data flow**: It receives the full markdown document as text. It looks for front matter, which is a YAML metadata section between --- lines, parses that metadata, checks for a name and description, then returns a CommunityDocument containing the metadata, the remaining instruction text, and the original document. If anything is missing or unreadable, it returns None.

**Call relations**: fetch calls this after it finds the SKILL.md file in the downloaded skill package. This is the final step that turns a raw document from the directory into the structured information the Install review can display.

*Call graph*: called by 1 (fetch); 2 external calls (__init__, safe_load).


### `extensions/web/ufo_ext_web/panels.py`

`orchestration` · `request handling`

The portal lets people change things such as agents, members, credentials, connections, and access grants. This file makes sure those changes do not bypass the project’s main safety and audit path. Instead of creating separate web-only update routes, it turns each submitted panel form into a structured “tool intent,” then sends that intent into the member’s dedicated intent conversation for the selected agent. Think of it like a service desk ticket: the form does not directly edit the database; it creates a clear request, queues it in the right lane, waits for the official result, and returns that result to the browser.

The Pydantic models in this file describe exactly which form submissions are allowed. Pydantic is a validation library: it checks that incoming data has the expected shape before the system trusts it. For example, a credential value is never sent through this route; the panel can only request a private credential prompt. A connection can only use the connect action. Deletes must not carry extra object data.

The main request function, submit_intent, reads the submitted JSON, checks size and validity, fills in a few safe details, converts the request into a ToolIntent, submits it to the conversation engine, and waits for a final frame saying whether it worked. The file also builds JSON schemas for agent forms and returns an agent overview projection for the web UI.

#### Function details

##### `ApplyIntent.kinds`  (lines 66–70)

```
def kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the complete set of object kinds that a portal panel is allowed to submit through ApplyIntent. This keeps the UI and the server tied to one closed list instead of separate, drifting lists.

**Data flow**: It reads the declared allowed values from the ApplyIntent kind type annotation, turns them into a frozen set, and returns that set. Nothing outside the class is changed.

**Call relations**: This helper is the source of truth for the object-kind list. It uses Python’s type-inspection helper to read the Literal values rather than copying the names by hand.

*Call graph*: 1 external calls (get_args).


##### `ApplyIntent.applying_kinds`  (lines 73–75)

```
def applying_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that the portal may create or update with an apply action. Kinds that are only deletable or only connectable are deliberately left out.

**Data flow**: It starts with the full allowed kind set, removes credential and source_trigger because those can only be deleted here, removes connection because that is connect-only, and returns the remaining frozen set.

**Call relations**: The web surface code asks this when building per-kind panel data, so the page can show an apply control only where the intent route will actually accept one.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent.deleting_kinds`  (lines 78–83)

```
def deleting_kinds(cls) -> frozenset[str]
```

**Purpose**: Returns the object kinds that the portal may delete. Connection is excluded because connections are opened through a connect flow rather than deleted through this object lane.

**Data flow**: It starts with the full allowed kind set, removes connect-only kinds, and returns the remaining frozen set. It does not inspect any request body or change state.

**Call relations**: The web surface code uses this when deciding whether to show delete controls. That keeps the page’s controls aligned with the same rules enforced when a submission arrives.

*Call graph*: called by 1 (_kind_payload).


##### `ApplyIntent._verb_pairs_with_its_kind`  (lines 86–105)

```
def _verb_pairs_with_its_kind(self) -> 'ApplyIntent'
```

**Purpose**: Checks that an ApplyIntent uses only sensible verb-and-kind combinations. This prevents the web route from becoming a loose, general-purpose mutation endpoint.

**Data flow**: It receives an already parsed ApplyIntent, examines its verb, kind, spec, and create_only flag, and either returns the same intent unchanged or raises a validation error explaining the bad combination.

**Call relations**: Pydantic runs this after building an ApplyIntent from incoming JSON. If it raises an error, submit_intent treats the submission as malformed and no conversation turn is created.


##### `_tool_intent`  (lines 175–271)

```
def _tool_intent(submitted: ApplyIntent | AddMemberIntent | AudienceIntent | CorrectionIntent | CredentialIntent | TranscriptIntent, slot: CredentialSlotView | None) -> ToolIntent
```

**Purpose**: Translates a validated panel submission into the exact tool request that the conversation engine understands. This is the switchboard that maps friendly portal actions to chat tools such as object_apply, object_delete, add_member, or request_credentials.

**Data flow**: It receives one validated intent object and, when needed, a credential slot description. It chooses the matching tool name, builds the input payload, converts object apply data into a YAML manifest, and returns a ToolIntent ready to submit to the engine.

**Call relations**: submit_intent calls this after all web-side validation is complete. The returned ToolIntent is then admitted into the member’s intent conversation, so the normal agent/tool machinery performs the actual change.

*Call graph*: called by 1 (submit_intent); 2 external calls (__init__, safe_dump).


##### `_outcome`  (lines 274–290)

```
def _outcome(frame: TerminalFrame, turn_id: UUID) -> Response
```

**Purpose**: Turns the final result of an admitted intent turn into a JSON response for the browser. It gives successful saves, credential handoffs, and failures a consistent response shape.

**Data flow**: It receives a terminal frame from the engine and the turn id. If the frame says done, it returns applied=true, plus credential-request details when present. Otherwise it cleans up the error text a little and returns applied=false with a message and the turn id.

**Call relations**: submit_intent calls this when the conversation tail produces a terminal frame. It is the last translation step between the engine’s internal result and the web panel’s HTTP response.

*Call graph*: called by 1 (submit_intent); 1 external calls (JSONResponse).


##### `submit_intent`  (lines 293–410)

```
async def submit_intent(ctx: SurfaceContext, request: Request, agent_id: UUID, member_id: UUID, email: str) -> Response
```

**Purpose**: Accepts one panel form submission, validates it, sends it through the official conversation-based write path, waits for the result, and returns that result to the web client. This is the central write endpoint for portal panels.

**Data flow**: It reads the HTTP request body, rejects oversized or malformed JSON, validates the submitted intent, performs extra checks such as model availability and credential-slot existence, converts the request into a ToolIntent, finds or creates the member’s dedicated intent conversation for the agent, admits a new turn, watches that turn until it finishes, and returns a JSON response. It changes system state indirectly by submitting the turn; the actual mutation is done by the engine and tools.

**Call relations**: Web routes call this when a user submits a panel action. It asks SurfaceContext for agent details, credential slots, the correct conversation, turn admission, and the turn stream; it uses _tool_intent to prepare the engine request and _outcome to format the final terminal result. If the turn parks or times out, it returns a clear message instead of pretending the change finished.

*Call graph*: calls 7 internal fn (admit, agent_detail, conversation_for, list_credential_slots, tail, _outcome, _tool_intent); 5 external calls (timeout, loads, conversation_audience, JSONResponse, body).


##### `agent_create_schema`  (lines 413–424)

```
def agent_create_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the JSON schema used by the portal’s create-agent form. A JSON schema is a machine-readable description of fields, types, and required values that a form can render from.

**Data flow**: It starts from the AgentSpec schema, removes sandbox_size when this deployment does not offer sandbox size choices, adds prompt to the required fields, and returns the adjusted schema dictionary.

**Call relations**: The portal uses this schema when drawing the create form, so the form matches the same AgentSpec shape the backend accepts. It relies on AgentSpec’s own schema rather than maintaining a second hand-written field list.

*Call graph*: 1 external calls (model_json_schema).


##### `_update_schema`  (lines 427–435)

```
def _update_schema(sandbox_sizes: tuple[str, ...]) -> dict[str, JsonValue]
```

**Purpose**: Builds the JSON schema used by the portal’s agent settings form. It removes fields that are edited somewhere else or unavailable in this deployment.

**Data flow**: It starts from the AgentSpec schema, removes prompt because the overview shows prompt in its own control, also removes sandbox_size when the deployment has no sandbox size choices, and returns the adjusted schema.

**Call relations**: agent_overview calls this while preparing the settings-page data. The returned schema tells the frontend which agent configuration fields to render for updates.

*Call graph*: called by 1 (agent_overview); 1 external calls (model_json_schema).


##### `agent_overview`  (lines 438–474)

```
async def agent_overview(ctx: SurfaceContext, agent_id: UUID, *, admin: bool) -> Response
```

**Purpose**: Returns the data needed to show an agent’s overview and settings page in the web portal. It combines the agent’s current settings, deployment capabilities, available models, form schema, and optional admin-only audience information.

**Data flow**: It asks SurfaceContext for the agent detail. If no agent exists, it returns a 404 response. Otherwise it optionally loads granted web-audience emails for admins, builds the current editable spec, builds the matching update schema, and returns everything as JSON.

**Call relations**: Web request handling calls this when the portal needs to display an agent overview. It uses _update_schema for the form shape, SurfaceContext for live agent/deployment data, and the web audience store only when the requester is an admin.

*Call graph*: calls 2 internal fn (agent_detail, _update_schema); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).
