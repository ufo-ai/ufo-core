# Live user surfaces  `stage-5.1`

This stage is the system’s set of live “front doors.” It is used during the main work loop, when real people are talking to agents, watching replies, or inspecting activity. Each surface adapts a different user interface into the same core conversation machinery.

The Slack surface checks that requests really came from Slack, then turns messages and button clicks into agent turns. It sends answers, files, and updates back into Slack threads. The web surface provides the browser portal, where members sign in, pick an agent, chat, watch live responses, read history, and access admin or spending pages when allowed. The terminal surface powers the ufo command-line client by accepting HTTP posts from the shell and streaming back simple display commands. The debugger surface is read-only: it serves a page, JSON data, and live event streams so operators can see what happened inside one workspace.

At the center, the core surface file is the trusted doorway. It gives these interfaces safe ways to identify members, create conversations, submit messages, read workspace views, and deliver replies.

## Files in this stage

### Debugger inspection surface
Read-only debugger routes expose workspace session state and live turn events for operator inspection.

### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the backend surface for a debugger UI. Think of it as a locked observation booth: an authorized operator can look at conversations, turns, transcripts, compaction records, files, and live activity for a single workspace, but this code does not edit that workspace. The workspace boundary is supplied by the surrounding surface system through SurfaceContext, so each read is already scoped to the workspace the operator is allowed to inspect.

At the front door, app_page serves a built React app from static/index.html. The browser app then asks the API routes in this file for data. Most handlers follow the same pattern: read an ID from the URL, check that it is a valid UUID, ask SurfaceContext for the matching record, and return either JSON or a 404-style JSON error. This keeps bad or missing IDs from leaking into lower-level code.

The file also supports downloading workspace files as raw bytes and following a live turn through server-sent events, often called SSE: a simple browser-friendly stream where the server sends named events over one open HTTP response. The _sse helper translates internal live frames, such as text updates or tool calls, into those stream events. The ROUTES table at the end connects URL paths to these handlers, including a POST route that stores the operator bearer token in an HTTP-only session cookie instead of putting it in a URL.

#### Function details

##### `app_page`  (lines 40–45)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger’s main web page. It is used when an operator opens the debugger in a browser.

**Data flow**: It receives the current surface context and HTTP request, checks whether the built HTML file was loaded when the module started, and returns that HTML as the response. If the frontend build is missing, it raises a clear error telling the developer to build the debugger app first.

**Call relations**: The route table sends the debugger’s root GET request here. This function hands the browser the shell of the app; after that, the browser uses the API handlers in this file to fill the page with workspace data.

*Call graph*: 1 external calls (HTMLResponse).


##### `workspace_meta`  (lines 48–55)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns basic information about the workspace being inspected, including its workspace ID and, when available, the linked Slack team. This gives the debugger UI enough context to label what the operator is looking at.

**Data flow**: It reads the workspace ID from SurfaceContext and asks the context for the Slack installation record. If that record looks like a Slack team identifier with the expected prefix, it strips the prefix and includes the team ID in the JSON response; otherwise it returns null for the Slack team.

**Call relations**: The route table sends the workspace metadata API request here. It relies on SurfaceContext.installation for the stored Slack link and then returns a small JSON object for the frontend.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 58–60)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of conversations in the current workspace. The debugger UI uses this as the starting list an operator can browse.

**Data flow**: It asks SurfaceContext for the workspace’s conversations, converts each returned entry into JSON-friendly data, and sends the list back as a JSON response.

**Call relations**: The route table sends the conversations API request here. It is a thin read-only bridge between the frontend and SurfaceContext.list_conversations.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 63–68)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns inside one conversation. A turn is one unit of interaction or work within a conversation.

**Data flow**: It reads the conversation_id from the URL and uses _uuid_param to make sure it is a valid UUID. If the ID is invalid, it returns a not-found JSON error; otherwise it asks SurfaceContext for that conversation’s turns, converts them to JSON-friendly data, and returns them.

**Call relations**: The route table sends requests for a conversation’s turns here. This handler uses _uuid_param for safe ID parsing, then hands the valid ID to SurfaceContext.list_turns.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 71–78)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the saved transcript for one conversation. This lets an operator read the conversation in a complete, structured form.

**Data flow**: It pulls conversation_id from the URL through _uuid_param. If the ID is invalid, it returns a not-found JSON error. If the ID is valid, it asks SurfaceContext for the transcript; a missing transcript also becomes a not-found JSON error, while a found transcript is converted to JSON and returned.

**Call relations**: The route table sends transcript requests here. It sits between the browser and SurfaceContext.read_transcript, adding URL validation and friendly error responses.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 81–85)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists the compaction records for a conversation. A compaction is where older conversation content has been summarized or shortened, and this endpoint helps an operator inspect when those changes happened.

**Data flow**: It reads and validates conversation_id using _uuid_param. If invalid, it returns a not-found JSON error. If valid, it asks SurfaceContext for the compaction list and returns that list as JSON.

**Call relations**: The route table sends compaction-list requests here. The function uses _uuid_param before calling SurfaceContext.list_compactions, so only well-formed conversation IDs reach the context layer.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 88–103)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns one detailed compaction record for a conversation. It shows what messages existed before, what remained after, and the summary that replaced or represented the compacted content.

**Data flow**: It reads conversation_id from the URL and validates it with _uuid_param, then reads the compaction index from the URL and checks that it is a number. Bad input returns a not-found JSON error. With valid input, it asks SurfaceContext for that exact compaction record; if found, it builds a JSON response containing the index, before messages, after messages, and summary.

**Call relations**: The route table sends requests for a specific compaction here. This handler depends on _uuid_param for the conversation ID and SurfaceContext.read_compaction for the stored record.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 106–111)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files associated with a conversation’s workspace area. This lets the debugger UI show what files are available to inspect or download for that conversation.

**Data flow**: It reads conversation_id from the URL using _uuid_param. If the ID is invalid, it returns a not-found JSON error. If valid, it asks SurfaceContext for the file list, converts each file entry to JSON-friendly data, and returns the list.

**Call relations**: The route table sends file-list requests here. It validates the conversation ID first, then forwards the read to SurfaceContext.list_workspace_files.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 114–124)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file back to the browser. This is used when an operator wants to inspect an actual file rather than just see its name in a list.

**Data flow**: It reads and validates conversation_id from the URL. It then asks SurfaceContext to open the requested file path. If the ID is bad, the path is rejected, or no file is found, it returns a not-found JSON error. If a file stream is available, it returns a streaming binary response so the bytes can be sent without loading everything into one JSON object.

**Call relations**: The route table sends file download requests here. The function uses _uuid_param for ID safety, SurfaceContext.read_workspace_file for the file stream, and StreamingResponse to send the bytes over HTTP.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 127–134)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information for one turn. This lets the debugger UI show the stored record for a specific unit of work.

**Data flow**: It reads turn_id from the URL through _uuid_param. If the ID is invalid, it returns a not-found JSON error. If valid, it asks SurfaceContext for the turn detail; missing data also returns not found, while found data is converted to JSON and returned.

**Call relations**: The route table sends turn-detail requests here. This handler validates the URL ID with _uuid_param, then gets the record through SurfaceContext.turn_detail.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 137–142)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger watch updates such as text, tool calls, cost ticks, or terminal output as they happen or as they are replayed from a cursor.

**Data flow**: It reads turn_id from the URL and validates it. It also checks that the turn exists before opening the stream. It reads the Last-Event-ID header, which is a browser-provided cursor used to resume after a dropped connection, and returns a server-sent event stream produced by _events.

**Call relations**: The route table sends live-stream requests here. It uses _uuid_param and SurfaceContext.turn_detail to reject bad turns early, then hands the real streaming work to _events and wraps that iterator in StreamingResponse.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 145–147)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the context’s live turn feed into bytes suitable for an HTTP streaming response. It is the small adapter between the system’s internal live frames and the debugger’s server-sent event output.

**Data flow**: It receives a SurfaceContext, a turn ID, and a cursor string. It asks SurfaceContext.tail for live frames from that point onward; for each cursor and frame it receives, it calls _sse to format the event and yields the resulting bytes.

**Call relations**: stream calls this when it has confirmed that a requested turn exists. _events then continuously reads from SurfaceContext.tail and delegates the exact event formatting to _sse.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 150–170)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one internal live frame as one server-sent event. This is what makes raw debugging updates understandable to a browser EventSource connection.

**Data flow**: It receives a cursor and a LiveFrame object. If the cursor is not empty, it adds it as the event ID so the browser can resume later. It then looks at the frame type, chooses an event name such as text, tool, cost, parked, terminal, or skill, serializes the frame to JSON, and returns the correctly formatted bytes. If a frame type is unknown, it raises an error instead of silently sending a misleading event.

**Call relations**: _events calls this for every frame it receives from SurfaceContext.tail. _sse is the final formatting step before bytes leave the server in the streaming response.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 173–177)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID value from a URL path parameter. It prevents handlers from treating malformed text as a real conversation or turn ID.

**Data flow**: It receives the HTTP request and the name of the path parameter to read. It tries to convert that string into a UUID object; if conversion succeeds, it returns the UUID, and if the string is not a valid UUID, it returns None.

**Call relations**: The conversation, file, turn, compaction, and stream handlers call this before asking SurfaceContext for data. It gives all those handlers the same simple rule: invalid IDs become not-found responses instead of lower-level failures.

*Call graph*: called by 8 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, workspace_file, workspace_files); 1 external calls (UUID).


### User channel surfaces
External Slack, terminal, and web interfaces translate member interactions into conversation turns and render replies back to users.

### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `startup, install, request handling, live turn updates, reply delivery`

This file makes Slack feel like a native place to talk to the agent. Without it, Slack events would be untrusted web traffic, messages would not be connected to the right conversation thread, Slack users could not be matched to workspace members, and the agent’s answers, questions, progress updates, and files would never return to Slack.

The main flow starts by checking Slack’s signature, which is like checking the wax seal on a letter. Then it decides which workspace the request belongs to, ignores messages the bot should not answer, and turns valid direct messages or mentions into admitted turns in the core system. If a message includes Slack files, the file bytes are streamed into the workspace instead of loaded all at once.

While a turn runs, background tasks update Slack’s thread status and, for long waits, post occasional progress messages. When the turn finishes, this file formats the final answer using Slack Block Kit, Slack’s structured message layout system. If the agent asks a question, simple choices become buttons; a click is later admitted as the next turn and the original button row is rewritten to show the chosen answer.

The file also covers installation. It supports Slack OAuth installs and bring-your-own Slack apps, stores the bot identity safely, and uploads shared artifacts back to Slack using Slack’s external upload flow.

#### Function details

##### `_env_signing_secret`  (lines 148–152)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from environment variables. This is the fallback secret used when a workspace does not have its own Slack app secret stored.

**Data flow**: It reads the process environment → looks for the Slack signing secret name → returns the secret text, or null if it is missing.

**Call relations**: Workspace-specific secret lookup functions call this when no per-workspace secret is available, so Slack request verification can still work for OAuth-installed workspaces.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 155–162)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use after the request has already been tied to a workspace. It prefers the workspace’s private credential slot and falls back to the deploy-wide secret.

**Data flow**: It receives a workspace context → asks the credential store for the Slack signing secret → if that slot is unset, reads the environment fallback → returns the secret or null.

**Call relations**: The event and interactivity handlers call this before verifying Slack’s signature, because every inbound Slack request must be proven before being trusted.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 165–173)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret during early workspace resolution, before a full workspace context exists. It safely handles unknown workspaces by returning no secret.

**Data flow**: It receives a shared auth helper and workspace id → tries to read that workspace’s Slack signing-secret credential → falls back to the environment secret if the slot is unset → returns null if the workspace is unknown.

**Call relations**: The workspace resolver uses this after extracting a Slack team id, so it can verify the raw request before binding the request to a tenant.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 176–180)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client id from the deploy environment. It fails loudly because OAuth install cannot work without it.

**Data flow**: It checks the environment → returns the client id if present → otherwise raises an error explaining that Slack authorization cannot run.

**Call relations**: The OAuth code exchange calls this when presenting this deploy’s Slack app identity to Slack.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 183–187)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret from the deploy environment. It fails loudly because the install flow cannot exchange OAuth codes without it.

**Data flow**: It checks the environment → returns the secret if present → otherwise raises an error explaining that Slack installation cannot run.

**Call relations**: The OAuth exchange uses this together with the client id to prove this server owns the Slack app.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 190–192)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the exact callback URL Slack should send users back to after they approve installation.

**Data flow**: It receives the public base URL of the deploy → trims any trailing slash → appends the Slack surface OAuth callback path → returns the full redirect URL.

**Call relations**: The OAuth callback uses this to exchange the code with the same redirect URL that was used in the original install link.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 195–207)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the “Add to Slack” link for the owner to click. The link names the app, requested permissions, callback URL, and sealed state that ties the install to the right workspace.

**Data flow**: It receives a client id, redirect URI, and sealed state → URL-encodes them with the Slack bot scopes → returns Slack’s authorization URL.

**Call relations**: Other setup code can use this helper to start the OAuth install path; it prepares the browser leg that later lands in oauth_callback.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 211–213)

```
def __init__(self, error: str)
```

**Purpose**: Creates a clear error object for Slack identity problems, such as a bad OAuth response or an invalid bot token.

**Data flow**: It receives an error string → stores it on the exception → initializes the normal runtime error message.

**Call relations**: Identity proving and OAuth exchange raise this when Slack’s response cannot be trusted or does not contain valid team and bot ids.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `identity_blob_key`  (lines 227–228)

```
def identity_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage path for a workspace’s Slack identity record. This keeps each workspace’s Slack metadata separate.

**Data flow**: It receives a workspace id → inserts it into a fixed blob-store path → returns that key string.

**Call relations**: Identity readers, resolvers, and the OAuth callback use this same key so they all read and write the same record.

*Call graph*: called by 3 (resolve, oauth_callback, read_identity).


##### `bot_token_fingerprint`  (lines 231–232)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a non-reversible fingerprint of a Slack bot token. This lets the code tell whether a stored identity belongs to the current token without storing the token in the identity record.

**Data flow**: It receives the bot token → hashes it with SHA-256 → returns the hex fingerprint.

**Call relations**: Identity reading, proving, and OAuth install use this fingerprint to reject stale identity records after a bot token changes.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 235–249)

```
async def read_identity(blob: BlobStore, workspace_id: UUID, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack team and bot-user identity for a workspace, but only if it matches the current bot token. This avoids routing Slack events using old app metadata.

**Data flow**: It receives blob storage, a workspace id, and a bot token → checks whether the identity blob exists → parses it → compares its token fingerprint → returns the identity or null.

**Call relations**: The identity resolver, request handlers, and self-user lookup call this before relying on Slack team or bot user ids.

*Call graph*: calls 4 internal fn (exists, get, bot_token_fingerprint, identity_blob_key); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 252–258)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Returns the Slack bot user id for this workspace, if the bot token and identity record are available.

**Data flow**: It receives an identity context → reads the Slack bot token credential → reads the matching identity blob → returns the bot user id or null.

**Call relations**: This is a small identity hook for code that needs to know which Slack user represents the bot itself.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_identity`  (lines 261–266)

```
async def _identity(ctx: SurfaceContext) -> SlackIdentity | None
```

**Purpose**: Loads the Slack identity for a bound workspace during request handling. It returns null when Slack is not fully configured yet.

**Data flow**: It receives the surface context → reads the bot token credential → reads the matching identity record → returns the identity or null.

**Call relations**: The event and interactivity handlers call this before interpreting Slack payloads, because team id and bot user id are needed to filter messages safely.

*Call graph*: calls 2 internal fn (credential, read_identity); called by 2 (ingest, interactive).


##### `SlackIdentityResolver.resolve`  (lines 280–288)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Gets or proves the Slack identity for a bring-your-own-app install. It avoids repeating Slack API calls when a valid identity is already stored.

**Data flow**: It checks the blob store for an existing matching identity → if found, returns it → otherwise asks Slack to prove the token → writes the identity blob → returns the new identity.

**Call relations**: Background identity repair and setup flows use this to derive the team and bot ids from a manually supplied bot token.

*Call graph*: calls 3 internal fn (_prove, identity_blob_key, read_identity).


##### `SlackIdentityResolver._prove`  (lines 290–315)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack’s auth.test endpoint what workspace and bot user a pasted bot token belongs to. It validates the shape of Slack’s answer before trusting it.

**Data flow**: It sends the bot token to Slack → parses Slack’s response → checks that Slack said ok and returned valid team and bot ids → returns a SlackIdentity or raises SlackIdentityError.

**Call relations**: SlackIdentityResolver.resolve calls this only when no usable identity record exists yet.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 321–334)

```
def _prove_identity_in_background(ctx: SurfaceContext) -> None
```

**Purpose**: Starts a one-at-a-time background repair job to prove Slack identity after an inbound request discovers it is missing. This lets a later Slack retry succeed without making the current request wait.

**Data flow**: It receives a context → checks whether this workspace already has a proof task → if not, creates one and records it → removes the record when the task finishes.

**Call relations**: The ingest and interactive handlers call this when credentials exist but the identity blob has not been written yet.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 2 (ingest, interactive); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 330–332)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Cleans up the in-memory record for a background identity proof task after it finishes.

**Data flow**: It receives the finished task → checks that it is still the tracked task for the workspace → removes it from the tracking dictionary.

**Call relations**: It is attached as a completion callback by _prove_identity_in_background so future repair attempts are not blocked by a finished task.


##### `_run_identity_proof`  (lines 337–344)

```
async def _run_identity_proof(ctx: SurfaceContext) -> None
```

**Purpose**: Runs the actual background identity proof and logs failures instead of crashing the request handler.

**Data flow**: It reads the Slack bot token from the context → constructs a SlackIdentityResolver → asks it to resolve and store identity → logs identity or unexpected errors.

**Call relations**: _prove_identity_in_background launches this as an asynchronous task.

*Call graph*: calls 1 internal fn (credential); called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `url_verified_blob_key`  (lines 347–353)

```
def url_verified_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage path for the marker that says Slack has successfully reached this workspace’s endpoint with a valid signature.

**Data flow**: It receives a workspace id → inserts it into a fixed blob-store path → returns the marker key.

**Call relations**: _mark_url_verified uses this when recording proof that Slack URL verification or signed events are working.

*Call graph*: called by 1 (_mark_url_verified).


##### `signing_secret_fingerprint`  (lines 356–359)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a safe fingerprint of the Slack signing secret. This lets the system detect whether a previous verification marker belongs to the current secret.

**Data flow**: It receives the signing secret → hashes it with SHA-256 → returns the hex fingerprint.

**Call relations**: _mark_url_verified stores this fingerprint in the verification marker so rotated secrets do not look connected until Slack proves the new one.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 373–400)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s temporary OAuth code for the workspace’s permanent bot token and identity details. It refuses malformed or failed Slack responses.

**Data flow**: It receives an OAuth code and redirect URI → sends them with the deploy’s client id and secret to Slack → validates the bot token, team id, and bot user id → returns a SlackInstall.

**Call relations**: oauth_callback calls this after the browser returns from Slack approval.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `SlackConversationSearch.run`  (lines 480–494)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations by channel details or DM participants. It gives the agent a bounded way to find where a user means to send or read something.

**Data flow**: It trims and lowers the query → lists Slack conversations → resolves people for DMs and group DMs → builds simplified conversation records → returns matches plus a truncated flag.

**Call relations**: It coordinates the private helper methods that page through Slack, resolve members, and turn raw Slack objects into searchable records.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 496–515)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches a bounded number of Slack conversation-list pages. The bound prevents a huge or misbehaving workspace from making the search run forever.

**Data flow**: It starts with an empty cursor → repeatedly calls Slack conversations.list with page parameters → collects channel objects → stops when no cursor remains or the page limit is reached → returns items and whether more pages existed.

**Call relations**: SlackConversationSearch.run calls this first, then passes the listed raw conversations to people resolution and matching.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 517–525)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list request.

**Data flow**: It receives a cursor string → creates parameters for conversation types, archived exclusion, and page size → includes the cursor when present → returns the parameter dictionary.

**Call relations**: SlackConversationSearch._list calls this for each page request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 527–530)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a conversations.list response.

**Data flow**: It receives a Slack response payload → looks inside response_metadata.next_cursor → returns that cursor if it is text, otherwise an empty string.

**Call relations**: SlackConversationSearch._list uses this after each page to decide whether to keep paging.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 532–560)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves the visible people in DMs and group DMs so they can be searched by human names or email addresses. It skips the bot itself.

**Data flow**: It receives an HTTP client and raw conversations → collects member ids for a bounded number of DM-like conversations → looks up each user once → converts users to labels → returns labels by conversation id and whether the cap was hit.

**Call relations**: SlackConversationSearch.run calls this after listing conversations; it uses _members, _label, _kind, and _slack_user to build the people text.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 562–569)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as a public channel, private channel, group DM, or one-to-one DM.

**Data flow**: It reads Slack boolean flags from the raw object → chooses the most specific kind → returns a short kind string.

**Call relations**: Conversation building, member lookup, and people resolution use this classification to treat DMs differently from channels.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 571–585)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets the member ids for one DM or group DM. For one-to-one DMs, Slack already includes the user; group DMs require a separate members call.

**Data flow**: It receives an HTTP client, raw conversation, and conversation id → for an IM returns the embedded user id → otherwise calls Slack conversations.members → returns valid member ids.

**Call relations**: SlackConversationSearch._people calls this while resolving searchable people labels.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 587–592)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Turns a Slack user record into readable search text. It prefers name plus email, then falls back to whichever field exists, then the raw user id.

**Data flow**: It receives a SlackUser or null and the Slack user id → chooses the best available display label → returns that label.

**Call relations**: SlackConversationSearch._people uses this after user lookup so DM searches can match human-friendly text.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 594–611)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts one raw Slack conversation object into the simplified model used for search results.

**Data flow**: It receives a raw object and resolved people labels → validates the object and id → extracts name, purpose, topic, kind, people, and membership flag → returns a SlackConversation or null.

**Call relations**: SlackConversationSearch.run calls this for each listed conversation before applying the query match.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 613–615)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely extracts Slack’s nested purpose or topic text field.

**Data flow**: It receives a possible nested object → reads its value field if present and textual → returns the text or an empty string.

**Call relations**: SlackConversationSearch._conversation uses this to flatten Slack’s purpose and topic objects.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 742–757)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a Slack request is fresh and really signed with the expected secret. This blocks forged or replayed requests.

**Data flow**: It receives headers, raw body bytes, a signing secret, and optionally the current time → validates timestamp and age → recomputes Slack’s HMAC signature → raises on mismatch or returns nothing on success.

**Call relations**: Workspace resolution, event ingest, and interactivity ingest call this before trusting Slack payload contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 764–783)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw request body with a size limit. The raw bytes are needed for signature checking, and the limit protects the server.

**Data flow**: It receives a request → returns cached bytes if already read → otherwise streams chunks, counting bytes → raises if too large → stores and returns the full body.

**Call relations**: Slack route handlers and workspace resolution call this before parsing or verifying a request.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 786–795)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack’s URL verification handshake and extracts the challenge string Slack expects back.

**Data flow**: It receives raw body bytes → tries to parse JSON → checks for type url_verification → returns the challenge string, an empty string, or null for normal events.

**Call relations**: Workspace resolution and ingest use this to answer Slack setup probes without admitting any turn.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 798–815)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Extracts a Slack team id from an untrusted body so the server can look up the possible workspace before verifying the signature.

**Data flow**: It receives raw bytes → tries JSON, then form-encoded interactive payload JSON → looks for team_id or team.id → validates the id pattern → returns the team id or null.

**Call relations**: resolve_workspace uses this hint to find the stored workspace and then verify the same raw body with that workspace’s secret.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 818–819)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Builds the stable installation key that maps a Slack team to a workspace.

**Data flow**: It receives a Slack team id → prefixes it with team: → returns the installation id string.

**Call relations**: The OAuth callback binds this id, and workspace resolution uses it to find the workspace for later Slack events.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 822–860)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace a Slack request belongs to before the main handler runs. It does this differently for OAuth callbacks, URL verification probes, and signed Slack events.

**Data flow**: It receives the raw request and shared auth helper → for GET callbacks opens sealed state and returns its workspace → for POSTs reads the raw body, handles URL verification, extracts the team hint, finds the workspace, verifies the signature, then returns the workspace id or a response/null.

**Call relations**: This is the pre-binding gate used by the surface framework so Slack routes are connected to the right workspace only after proof.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 863–866)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether sealed credential state belongs to the Slack OAuth install flow.

**Data flow**: It receives decoded credential-request claims → checks for the Slack install marker and the bot-token slot → returns true or false.

**Call relations**: resolve_workspace and oauth_callback use this to avoid accepting sealed state meant for another credential flow.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 869–874)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Creates the conversation key for Slack messages. Direct messages use the DM channel, while channel conversations use the thread root.

**Data flow**: It receives a channel id, root timestamp, and DM flag → returns the channel alone for DMs or channel:root timestamp for threaded channel messages.

**Call relations**: _to_inbound uses this key so all replies in the same Slack thread map to one core conversation.

*Call graph*: called by 1 (_to_inbound).


##### `slack_message_addressed`  (lines 877–884)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly asking the agent to respond. DMs always count; channels count when the bot is mentioned.

**Data flow**: It receives the event, bot user id, and DM flag → checks event type, DM status, and mention text → returns true or false.

**Call relations**: _to_inbound uses this to ignore ordinary channel chatter unless it is already part of a participating thread.

*Call graph*: called by 1 (_to_inbound).


##### `slack_reply_body`  (lines 887–937)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool=False) -> bytes
```

**Purpose**: Builds the JSON body for a Slack chat.postMessage call. It chooses rich blocks when possible and falls back to plain text when size limits would be exceeded.

**Data flow**: It receives channel, optional thread, text, optional metadata, and optional action blocks → splits long text into Slack-sized pieces → adds buttons or footer metadata if needed → encodes JSON bytes or raises if too large.

**Call relations**: Final reply posting and long-progress posting both use this so Slack messages respect Slack’s formatting and size rules.

*Call graph*: called by 2 (_post, post); 1 external calls (dumps).


##### `_mrkdwn_section`  (lines 940–941)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a Slack markdown section block from text, trimming it to Slack’s section limit.

**Data flow**: It receives text → slices it to the section maximum → returns a Block Kit section dictionary.

**Call relations**: slack_ask_blocks uses this repeatedly when rendering agent questions.

*Call graph*: called by 1 (slack_ask_blocks).


##### `slack_ask_blocks`  (lines 944–996)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question as Slack blocks. Simple single-choice questions become buttons; richer questions are shown as text so the user can reply in the thread.

**Data flow**: It receives an AskUserInput or null → if null returns null → otherwise creates title and question sections → adds button rows only when options fit Slack’s button limits → returns block dictionaries.

**Call relations**: post calls this when a turn ends by asking the user something.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (post).


##### `slack_connect_blocks`  (lines 999–1020)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a private connection request as a Slack button. The button later lets the clicking member get a private authorization link.

**Data flow**: It receives a connect request and turn id → if no request returns null → otherwise builds one action block with a button value containing the turn id.

**Call relations**: post uses this when the terminal turn asks the user to connect an external provider.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1023–1027)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required string field from a Slack event or payload and fails clearly if it is missing.

**Data flow**: It receives a mapping and field name → checks that the value is a non-empty string → returns it or raises ValueError.

**Call relations**: _to_inbound and _to_click use this to avoid continuing with malformed Slack payloads.

*Call graph*: called by 2 (_to_click, _to_inbound).


##### `_inbound_files`  (lines 1030–1042)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable Slack file attachments from a message event, up to a safe maximum.

**Data flow**: It receives a Slack event → scans its files list → skips hidden or tombstoned items → keeps file name and private download URL when valid → returns InboundFile objects.

**Call relations**: _to_inbound uses this so ingest can later stream those files into the workspace before admitting the turn body.

*Call graph*: called by 1 (_to_inbound); 1 external calls (__init__).


##### `oauth_callback`  (lines 1045–1089)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the Slack OAuth install after the owner approves the app. It stores the bot token, binds the Slack team to the workspace, and records the bot identity.

**Data flow**: It receives the workspace context and browser request → validates state and code → exchanges the code with Slack → binds the team installation → stores the bot token and identity blob → returns an HTML success or error page.

**Call relations**: Slack redirects the owner here after authorization; it uses helpers for state checking, redirect URI creation, token exchange, identity fingerprinting, and install-page rendering.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _install_page, _is_install_state, bot_token_fingerprint, identity_blob_key, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 1 external calls (__init__).


##### `_install_page`  (lines 1092–1099)

```
def _install_page(message: str, status: int) -> Response
```

**Purpose**: Builds the small HTML page shown after Slack installation succeeds or fails.

**Data flow**: It receives a message and HTTP status → escapes the message for safe HTML → returns an HTML response.

**Call relations**: oauth_callback uses this for every browser-visible outcome.

*Call graph*: called by 1 (oauth_callback); 2 external calls (escape, Response).


##### `_mark_url_verified`  (lines 1105–1119)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this workspace using the current signing secret. This helps setup know the Slack app endpoint is connected.

**Data flow**: It receives context and signing secret → fingerprints the secret → skips if this process already wrote that fingerprint → writes a marker with fingerprint and time to blob storage → caches the fingerprint locally.

**Call relations**: ingest and interactive call this after successful signature verification; failures are logged but do not fail Slack’s request.

*Call graph*: calls 2 internal fn (signing_secret_fingerprint, url_verified_blob_key); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1122–1184)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Events API requests and turns valid Slack messages into agent turns. It is the main inbound message path.

**Data flow**: It reads and size-checks the raw body → verifies the Slack signature or answers URL verification → loads Slack identity → filters and converts the event to an inbound message → fetches sender and context → resolves the member → downloads files → admits the turn → starts status and progress tasks → returns ok.

**Call relations**: This function ties together most inbound helpers: request verification, event filtering, audience choice, ambient context, file streaming, member resolution, admission, and live feedback.

*Call graph*: calls 19 internal fn (admit, conversation_for, credential, _ambient_context, _ctx_signing_secret, _download_files, _files_note, _identity, _mark_url_verified, _prove_identity_in_background (+9 more)); 5 external calls (gather, loads, conversation_audience, JSONResponse, Response).


##### `_author_is_foreign`  (lines 1187–1194)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects whether a Slack message author comes from another organization in a shared Slack Connect channel. Such users are skipped because they cannot be safely resolved as local workspace members.

**Data flow**: It receives the event and local team id → compares source_team or user_team against the bound team → returns true only when the author team differs.

**Call relations**: _to_inbound calls this early to ignore external bystanders before admitting turns.

*Call graph*: called by 1 (_to_inbound).


##### `_room_audience`  (lines 1197–1227)

```
async def _room_audience(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> Audience | None
```

**Purpose**: Chooses who should be allowed to see a Slack conversation inside the core system. Public channels, private rooms, DMs, and externally shared channels have different privacy meanings.

**Data flow**: It receives context, payload, event, channel id, and whether an audience is already known → uses event fields and sometimes Slack channel info → returns an audience object, null for DM, or raises if the channel cannot be classified.

**Call relations**: _to_inbound calls this while building the inbound turn so the core conversation has the right disclosure boundary.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 3 external calls (conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1230–1268)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Converts a raw Slack event payload into the smaller trusted Inbound object, or ignores it. This is where message gating rules are applied.

**Data flow**: It receives context, payload, and Slack identity → validates event type, subtype, author, addressing, thread key, participation, audience, body, and files → returns Inbound or null.

**Call relations**: ingest calls this after signature and identity checks; it uses helpers for author filtering, thread keys, addressed checks, room audience, participating conversation, and attachments.

*Call graph*: calls 7 internal fn (_author_is_foreign, _inbound_files, _participating_conversation, _room_audience, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 1 external calls (__init__).


##### `_participating_conversation`  (lines 1271–1282)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has an admitted turn, meaning the agent is participating and unmentioned replies should be accepted.

**Data flow**: It receives context and queue key → finds the core conversation → checks whether it has a latest turn → returns the conversation id only if a turn exists.

**Call relations**: _to_inbound uses this to admit normal thread replies only after the agent has truly joined the thread.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1285–1315)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Looks up a Slack user’s display name, confirmed email, and timezone. It is best effort so slow Slack lookups do not block event acknowledgement for long.

**Data flow**: It receives bot token and Slack user id → calls Slack users.info with a short timeout → validates the response → keeps email only if Slack says it is confirmed → returns SlackUser or null.

**Call relations**: ingest uses it for sender context and member resolution; interactivity uses it when an answer click comes from an unlinked member; conversation search uses it for DM labels.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_people, ingest, interactive); 2 external calls (__init__, AsyncClient).


##### `_turn_context`  (lines 1318–1332)

```
def _turn_context(sender: SlackUser | None) -> TurnContext
```

**Purpose**: Builds the small sender context attached to an admitted turn. This gives the agent readable information like who spoke and their timezone.

**Data flow**: It receives a SlackUser or null → builds a sender label from name and email → tries to create a TurnContext with timezone → drops invalid timezones and returns context.

**Call relations**: ingest passes this context into core admission so the agent can see who the Slack speaker was.

*Call graph*: called by 1 (ingest); 1 external calls (__init__).


##### `_resolve_member`  (lines 1335–1353)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a workspace member when possible. Existing links win; otherwise a confirmed same-domain email can join or link the teammate.

**Data flow**: It receives context, Slack user id, DM flag, and sender info → checks existing linked member → if missing, uses confirmed email to join/link → returns a member id or null; in a DM with no user info it raises.

**Call relations**: ingest and interactive use this before admitting turns so the core knows who spoke when identity can be proven.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 2 (ingest, interactive); 1 external calls (__init__).


##### `_ambient_context`  (lines 1356–1403)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> str
```

**Purpose**: Fetches a short digest of earlier Slack messages when a thread or channel conversation starts. This gives the agent context that is not yet in the core transcript.

**Data flow**: It receives context, bot token, inbound message, and identity → skips DMs and already-participating conversations → chooses thread replies or recent channel history → calls Slack with a short timeout → returns a formatted digest or empty text.

**Call relations**: ingest prepends this digest to the first admitted turn in a Slack channel conversation.

*Call graph*: calls 2 internal fn (_ambient_digest, _slack_ok); called by 1 (ingest); 1 external calls (AsyncClient).


##### `_ambient_digest`  (lines 1406–1443)

```
def _ambient_digest(messages: list[object], bot_user_id: str, header: str) -> str
```

**Purpose**: Turns fetched Slack messages into a bounded plain-text context block. It keeps member messages, removes bot messages and direct mentions, and trims long histories sensibly.

**Data flow**: It receives raw messages, bot user id, and a header → filters valid human messages → formats timestamps and user mentions → sorts by time → trims to the digest character budget → returns header plus lines or empty text.

**Call relations**: _ambient_context calls this after Slack returns message history.

*Call graph*: called by 1 (_ambient_context); 1 external calls (fromtimestamp).


##### `_slack_download_host_ok`  (lines 1446–1448)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks that a private file URL belongs to Slack before sending the bot token to it.

**Data flow**: It receives a URL → parses the hostname → returns true only for slack.com or a Slack subdomain.

**Call relations**: _stream_download uses this as a safety check before downloading inbound files.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 1451–1469)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a Slack private file download in chunks, with host and size protections. This avoids holding a whole file in memory.

**Data flow**: It receives bot token and file URL → refuses non-Slack hosts → opens an authenticated streaming GET → yields chunks while counting bytes → raises if the file exceeds the inbound size limit.

**Call relations**: _download_files passes this stream directly to the workspace file writer.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 1481–1497)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack message attachments into the workspace before the agent turn runs. Oversized files are skipped rather than partially written.

**Data flow**: It receives context, conversation id, bot token, and inbound files → creates safe unique inbox names → streams each Slack file into workspace storage → records delivered and skipped names → returns DownloadedFiles.

**Call relations**: ingest calls this when an inbound Slack message includes files, then adds a note to the admitted turn.

*Call graph*: calls 3 internal fn (write_workspace_file, _inbox_name, _stream_download); called by 1 (ingest); 1 external calls (__init__).


##### `_inbox_name`  (lines 1500–1510)

```
def _inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Creates a safe, unique filename for an inbound Slack attachment inside the workspace inbox folder.

**Data flow**: It receives the raw filename and a set of already-used names → strips path parts and bad empty names → adds numeric suffixes until unused → records and returns the chosen name.

**Call relations**: _download_files uses this before writing each attachment.

*Call graph*: called by 1 (_download_files).


##### `_files_note`  (lines 1513–1524)

```
def _files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Builds the text note added to the user’s message telling the agent which files were saved or skipped.

**Data flow**: It receives DownloadedFiles → lists delivered workspace paths and oversized skipped Slack names → returns bracketed note text or an empty string.

**Call relations**: ingest appends this note to the admitted turn body after file download.

*Call graph*: called by 1 (ingest).


##### `ThreadStatus.run`  (lines 1561–1570)

```
async def run(self) -> None
```

**Purpose**: Runs live Slack thread-status updates for one turn from start to finish. It shows “Thinking…” first and clears the status at the end.

**Data flow**: It reads the bot token → opens an HTTP client → sets the initial status → follows live turn frames → finally clears the status even if following fails.

**Call relations**: _run_status calls this inside a background task created by _track_status.

*Call graph*: calls 2 internal fn (_follow, _set); called by 1 (_run_status); 1 external calls (AsyncClient).


##### `ThreadStatus._set`  (lines 1572–1615)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one status string to Slack’s assistant thread status API, but only if this turn is still the newest writer for the thread.

**Data flow**: It receives an HTTP client, bot token, and status text → checks the thread-writer guard → sends Slack the status and loading message → logs success or failure → returns whether Slack accepted it.

**Call relations**: ThreadStatus.run and ThreadStatus._follow use this for initial, refresh, changed, and clear status writes.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._follow`  (lines 1617–1658)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Tails live turn frames and translates them into short Slack status lines. It refreshes quiet statuses and stops on terminal or parked frames.

**Data flow**: It receives client, token, and last shown status → waits for tail frames or refresh timeouts → maps tool calls, skill loads, and text streaming to status text → rate-limits writes → cancels the pending tail read on exit.

**Call relations**: ThreadStatus.run calls this after the initial “Thinking…” write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_track_status`  (lines 1665–1687)

```
def _track_status(ctx: SurfaceContext, turn_id: UUID, queue_key: str, message_ts: str) -> None
```

**Purpose**: Starts one background Slack status task for an admitted turn. It also marks the newest turn as the only status writer for that Slack thread.

**Data flow**: It receives context, turn id, queue key, and message timestamp → skips if this turn already has a task → computes channel and thread timestamp → records writer → creates the task → removes tracking when done.

**Call relations**: ingest and interactive call this after admitting a Slack message or answer click.

*Call graph*: calls 1 internal fn (_run_status); called by 2 (ingest, interactive); 2 external calls (__init__, create_task).


##### `_track_status._untrack`  (lines 1682–1685)

```
def _untrack(_done: asyncio.Task[None]) -> None
```

**Purpose**: Cleans up status-task and thread-writer records when a status task ends.

**Data flow**: It receives the completed task → removes the turn from the task map → removes the thread writer only if it still points at that turn.

**Call relations**: _track_status attaches this callback to the background status task.


##### `_run_status`  (lines 1690–1702)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Runs a ThreadStatus task and logs if the whole follower dies unexpectedly.

**Data flow**: It receives a ThreadStatus → awaits its run method → if an exception escapes, logs the turn, channel, thread, and error.

**Call relations**: _track_status launches this wrapper so one failed status task does not crash the process.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 1714–1718)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the timing settings for long-running progress posts.

**Data flow**: It checks the base interval and cap after construction → raises if the base is not positive or the cap is smaller than the base → otherwise leaves the object usable.

**Call relations**: _track_progress constructs ProgressCadence before starting a ThreadProgress reporter.


##### `ProgressCadence.intervals`  (lines 1720–1732)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Produces the wait schedule for progress posts. The waits double by elapsed time until they reach a maximum cap.

**Data flow**: It starts at the base wait → yields each wait → adds it to elapsed time → chooses the next wait as elapsed time capped by the maximum → continues forever.

**Call relations**: ThreadProgress._follow consumes this iterator to know when the next interim update should be posted.


##### `TurnActivity.tool`  (lines 1752–1756)

```
def tool(self, tool: str, description: str) -> None
```

**Purpose**: Records that the turn started a tool call and updates the current activity shown in progress posts.

**Data flow**: It receives a tool slug and description → closes any streamed narration → sets the activity from the description or tool slug → increments that tool’s count.

**Call relations**: ThreadProgress._follow calls this when it sees a ToolCall frame.

*Call graph*: calls 1 internal fn (_close_narration).


##### `TurnActivity.skill`  (lines 1758–1760)

```
def skill(self, skill: str) -> None
```

**Purpose**: Records that the turn is loading a skill and updates the current activity text.

**Data flow**: It receives a skill name → closes any streamed narration → sets the activity to a loading-skill message.

**Call relations**: ThreadProgress._follow calls this when it sees a SkillLoad frame.

*Call graph*: calls 1 internal fn (_close_narration).


##### `TurnActivity.stream`  (lines 1762–1763)

```
def stream(self, text: str) -> None
```

**Purpose**: Records streamed text chunks so progress can say the answer is being written without quoting unfinished content.

**Data flow**: It receives a text chunk → appends it to the streaming buffer → returns nothing.

**Call relations**: ThreadProgress._follow calls this when it sees TextDelta frames.


##### `TurnActivity.checkpoint`  (lines 1765–1766)

```
def checkpoint(self) -> None
```

**Purpose**: Resets the per-interval tool tally after a progress post checkpoint.

**Data flow**: It clears the tool counter → leaves narration, activity, and streaming state intact.

**Call relations**: ThreadProgress._follow calls this after each scheduled progress post attempt.


##### `TurnActivity.current_step`  (lines 1768–1777)

```
def current_step(self) -> str
```

**Purpose**: Returns the best current activity to report. If text is streaming, it reports writing progress by character count rather than showing the text.

**Data flow**: It sums buffered streaming text length → if nonzero returns a writing message → otherwise returns the last activity string.

**Call relations**: TurnActivity.report calls this while building a progress message.

*Call graph*: called by 1 (report).


##### `TurnActivity._close_narration`  (lines 1779–1783)

```
def _close_narration(self) -> None
```

**Purpose**: Moves completed streamed text into the latest narration field when the turn switches from writing to another step.

**Data flow**: It joins and trims buffered streaming text → clears the buffer → if text exists, saves a limited version as narration.

**Call relations**: TurnActivity.tool and TurnActivity.skill call this before recording a new activity.

*Call graph*: called by 2 (skill, tool).


##### `TurnActivity.report`  (lines 1785–1812)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds one human-readable progress update from the activity seen so far, or skips posting if there has been no signal at all.

**Data flow**: It receives elapsed seconds → gets the current step → formats elapsed time → includes latest narration, current activity, and either tool tally, quiet note, or writing elapsed line → returns text or null.

**Call relations**: ThreadProgress._post calls this at each scheduled checkpoint.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 1839–1842)

```
async def run(self) -> None
```

**Purpose**: Runs interim progress reporting for one long turn.

**Data flow**: It reads the Slack bot token → opens an HTTP client → calls the frame-following loop.

**Call relations**: _run_progress calls this inside the background task created by _track_progress.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._follow`  (lines 1844–1878)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Tails live turn frames and posts progress on the cadence schedule until the turn finishes or parks.

**Data flow**: It starts a timer and first deadline → creates TurnActivity → waits for either a live frame or the deadline → updates activity from frames or posts a checkpoint → advances the deadline → cancels pending tail work on exit.

**Call relations**: ThreadProgress.run calls this; it hands each scheduled post to ThreadProgress._post.

*Call graph*: calls 1 internal fn (_post); called by 1 (run); 5 external calls (__init__, ensure_future, gather, wait, monotonic).


##### `ThreadProgress._post`  (lines 1880–1926)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float) -> None
```

**Purpose**: Posts one interim progress message into the Slack destination, if there is useful activity to say.

**Data flow**: It asks TurnActivity for report text → logs and returns if there is no signal → builds the Slack post body for the channel/thread → sends chat.postMessage → logs success or failure without stopping future checkpoints.

**Call relations**: ThreadProgress._follow calls this whenever the cadence deadline arrives.

*Call graph*: calls 3 internal fn (report, _slack_ok, slack_reply_body); called by 1 (_follow); 2 external calls (post, log).


##### `_track_progress`  (lines 1932–1951)

```
def _track_progress(ctx: SurfaceContext, turn_id: UUID, queue_key: str) -> None
```

**Purpose**: Starts one long-running progress reporter for a turn run. It is only called for the delivery that actually opened the run, preventing duplicate progress messages.

**Data flow**: It receives context, turn id, and queue key → skips if this process already has a reporter for the turn → creates ProgressCadence and ThreadProgress → starts the task → removes it when done.

**Call relations**: ingest and interactive call this only when admission reports that a run was opened.

*Call graph*: calls 1 internal fn (_run_progress); called by 2 (ingest, interactive); 3 external calls (__init__, __init__, create_task).


##### `_run_progress`  (lines 1954–1967)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Runs a ThreadProgress task and logs if the reporter has to abandon the turn.

**Data flow**: It receives ThreadProgress → awaits its run method → logs turn, queue key, and error if an exception escapes.

**Call relations**: _track_progress launches this wrapper as the background task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `interactive`  (lines 2001–2073)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack button clicks, including answer buttons and private connection buttons. It verifies the request, admits button answers as turns, and responds quickly enough for Slack.

**Data flow**: It reads and verifies the form payload → loads identity → converts the click → for connect clicks, posts a private ephemeral authorization response → for answer clicks, resolves the member, finds the conversation, admits the answer idempotently, starts status/progress, and schedules message rewrite → returns ok.

**Call relations**: This is the inbound path for Slack interactivity and uses helpers for signature checks, click parsing, member resolution, admission, progress tracking, rewrites, and ephemeral replies.

*Call graph*: calls 20 internal fn (admit, admitted_body, connect_url, conversation_for, credential, find_conversation, linked_member, _ctx_signing_secret, _ephemeral_in_background, _identity (+10 more)); 3 external calls (conversation_audience, JSONResponse, Response).


##### `_rewrite_in_background`  (lines 2079–2082)

```
def _rewrite_in_background(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Starts a background task to rewrite a Slack question message after a winning button answer.

**Data flow**: It receives bot token and AnswerClick → creates a task for _run_rewrite → stores it in a set so it is not garbage-collected → removes it when done.

**Call relations**: interactive calls this only when the admitted answer body matches the winning click.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 2085–2089)

```
async def _run_rewrite(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Runs the Slack message rewrite and logs failures without affecting the already-admitted answer.

**Data flow**: It receives bot token and click → calls _replace_buttons_with_answer → catches and logs any error.

**Call relations**: _rewrite_in_background launches this as a background task.

*Call graph*: calls 1 internal fn (_replace_buttons_with_answer); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 2092–2095)

```
def _ephemeral_in_background(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Starts a background task to send a private Slack response to a connect-button click.

**Data flow**: It receives context, ConnectClick, and text → creates a task for _post_ephemeral → stores it until completion.

**Call relations**: interactive calls this for connect clicks so the Slack acknowledgement can return immediately.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 2098–2123)

```
async def _post_ephemeral(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Sends a Slack ephemeral message, visible only to the clicking user, with the private connection result.

**Data flow**: It reads the bot token → posts chat.postEphemeral to the click’s channel and optional thread → includes the clicking Slack user id and text → logs failures.

**Call relations**: _ephemeral_in_background runs this after interactive handles a connect click.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_click`  (lines 2126–2181)

```
def _to_click(raw: bytes, identity: SlackIdentity) -> AnswerClick | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactive payload into either an answer click or connect click. Unknown or malformed actions are ignored.

**Data flow**: It receives raw form bytes and identity → decodes the payload field → checks block action type and team id → reads the first action, user, channel, and message → returns ConnectClick, AnswerClick, or null.

**Call relations**: interactive calls this after signature and identity checks to decide what kind of button was pressed.

*Call graph*: calls 2 internal fn (_dict_field, _string_field); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_dict_field`  (lines 2184–2188)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required nested object from a Slack payload and fails clearly if it is missing.

**Data flow**: It receives a mapping and field name → checks that the value is a dictionary → returns it or raises ValueError.

**Call relations**: _to_click uses this for required user, channel, and message objects.

*Call graph*: called by 1 (_to_click).


##### `_replace_buttons_with_answer`  (lines 2191–2233)

```
async def _replace_buttons_with_answer(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Updates the original Slack question message so the clicked button row becomes a confirmation of the chosen answer.

**Data flow**: It receives bot token and AnswerClick → copies the message blocks Slack already delivered → replaces the clicked block when possible or appends an answer block → sends chat.update with the same text and new blocks.

**Call relations**: _run_rewrite calls this in the background after interactive confirms the click won admission.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (_run_rewrite); 2 external calls (AsyncClient, dumps).


##### `_reply_text`  (lines 2236–2246)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a finished turn, including simple messages for failed, cancelled, or empty replies.

**Data flow**: It receives a Writeback → checks status → returns failure text, cancellation text or reason, normal reply text, or an empty-reply placeholder.

**Call relations**: _reply_with_oversize_links uses this as the base before adding credential or large-file notes.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 2249–2264)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Builds final reply text plus extra notes for credential requests and artifacts too large to upload to Slack.

**Data flow**: It receives context and Writeback → gets the base reply text → appends a terminal-instruction note for credential requests → appends download-link lines for oversized artifacts → returns final text.

**Call relations**: post calls this before formatting the Slack chat.postMessage body.

*Call graph*: calls 2 internal fn (_oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 2267–2270)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one too-large artifact as a Slack-readable bullet line with a download link when available.

**Data flow**: It receives context and artifact → asks context for an artifact link → builds linked or plain filename text with byte size → returns one line.

**Call relations**: _reply_with_oversize_links calls this for each artifact above Slack’s upload limit.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_debug_link`  (lines 2273–2285)

```
async def _debug_link(ctx: SurfaceContext, writeback: Writeback) -> str | None
```

**Purpose**: Builds an operator-only debug URL for the delivered turn when the deploy has a public base URL.

**Data flow**: It receives context and Writeback → returns null if no public URL or conversation is found → otherwise builds a URL containing workspace, conversation, and turn ids.

**Call relations**: post uses this only for operator workspaces and safe internal channels when adding accounting metadata.

*Call graph*: calls 1 internal fn (find_conversation); called by 1 (post).


##### `_channel_info`  (lines 2288–2302)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for a channel, best effort. It returns null if Slack cannot be reached or the response is not usable.

**Data flow**: It receives bot token and channel id → calls Slack conversations.info with a short timeout → returns the channel object or null, logging failures.

**Call relations**: _room_audience uses this to classify public/shared/private channels, and _channel_is_externally_shared uses it before adding operator-only metadata.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_channel_is_externally_shared, _room_audience); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 2305–2318)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack destination may include people outside the bound workspace. If the channel cannot be read, it treats it as external to be safe.

**Data flow**: It receives bot token and channel id → fetches channel info → if unavailable returns true → otherwise checks Slack shared-channel flags → returns true or false.

**Call relations**: post calls this before adding cost and debug metadata so outside guests do not see internal details.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (post).


##### `post`  (lines 2321–2377)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Posts the agent’s final reply to Slack and returns the Slack message reference. It also adds question buttons, connect buttons, safe metadata, and a retry path for Slack block-format rejection.

**Data flow**: It receives context and Writeback → parses the destination channel/thread → reads the bot token → builds reply text, action blocks, and optional operator metadata → sends chat.postMessage → retries once in a conservative format for invalid blocks → validates Slack’s timestamp → returns channel:ts.

**Call relations**: The core delivery poller calls this when a turn reaches a terminal writeback; attach may later upload files related to the same writeback.

*Call graph*: calls 9 internal fn (credential, is_operator_workspace, _channel_is_externally_shared, _chat_post, _debug_link, _reply_with_oversize_links, slack_ask_blocks, slack_connect_blocks, slack_reply_body); 2 external calls (__init__, AsyncClient).


##### `_chat_post`  (lines 2380–2420)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one Slack chat.postMessage request and returns the parsed Slack payload without requiring ok:true. This lets the caller handle recoverable Slack errors itself.

**Data flow**: It receives HTTP client, bot token, and JSON body bytes → posts to Slack → on HTTP error, extracts Slack error and retry-after if available → raises SurfaceDeliveryError → otherwise returns parsed JSON.

**Call relations**: post uses this for the initial final reply and the possible invalid_blocks retry.

*Call graph*: calls 1 internal fn (__init__); called by 1 (post); 1 external calls (post).


##### `attach`  (lines 2423–2442)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shared artifacts from the finished turn to Slack when they fit Slack’s upload size limit. Oversized files are already linked in the text reply.

**Data flow**: It receives context, Writeback, and reply reference → filters artifacts small enough for Slack upload → computes destination channel/thread → reads bot token → uploads all eligible artifacts concurrently → logs individual failures.

**Call relations**: The delivery system calls this after post; it delegates each file to _upload_artifact.

*Call graph*: calls 2 internal fn (credential, _upload_artifact); 1 external calls (gather).


##### `_upload_artifact`  (lines 2445–2491)

```
async def _upload_artifact(ctx: SurfaceContext, bot_token: str, channel: str, thread_ts: str | None, artifact: SharedArtifact) -> None
```

**Purpose**: Streams one shared artifact to Slack using Slack’s external upload process. The file bytes come from blob storage and are not buffered as a whole.

**Data flow**: It receives context, bot token, channel, optional thread, and artifact → reserves an upload URL from Slack with filename and length → streams blob bytes to that URL → completes the upload into the Slack channel/thread.

**Call relations**: attach starts one of these tasks for each uploadable artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 4 external calls (__init__, AsyncClient, Timeout, dumps).


##### `_slack_ok`  (lines 2494–2503)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Standardizes Slack API success checking for calls that must return ok:true.

**Data flow**: It awaits an HTTP request → raises for HTTP failure → parses JSON → if Slack did not return ok:true, builds a SlackApiError with Slack’s error details → otherwise returns the payload dictionary.

**Call relations**: Most Slack API helpers use this so malformed or failed Slack responses become consistent exceptions.

*Call graph*: called by 11 (_list, _members, _post, _set, _ambient_context, _channel_info, _post_ephemeral, _replace_buttons_with_answer, _slack_user, _upload_artifact (+1 more)); 1 external calls (__init__).


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The `ufo` shell client is deliberately simple: it sends a message to the server and reads back tab-separated command lines such as “show this text”, “ask for input”, “poll again”, or “exit”. This file is the translator between that plain terminal wire format and the richer conversation system behind it.

A request first proves who it is with a bearer token, which is a signed string saying which workspace and email it belongs to. The email is linked to a member account if possible. The path names a channel, so the same person can have separate terminal conversations.

If the request body contains a message, the file admits that message into the durable conversation queue. If the body is empty, it does not create a new turn; it resumes watching the latest turn instead. This matters because long answers may outlive one HTTP connection. The server holds the stream open for a little while, then tells the shell to poll again rather than letting the client time out mid-answer.

The file also supports private credential entry. When the model asks for a secret, the terminal receives a special `secret` directive. A later request can store that secret without putting it into the chat transcript.

#### Function details

##### `directive`  (lines 51–59)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one command line for the shell client to read. It makes sure tabs, newlines, and backslashes inside text cannot accidentally break the simple line-based protocol.

**Data flow**: It receives a command word, called a verb, plus any text fields. It escapes unsafe characters inside each field, joins everything with tabs, adds a newline, and returns the result as bytes ready to send over HTTP.

**Call relations**: This is the common packaging step used throughout the file. Higher-level functions decide what should happen on screen, then call `directive` to turn that decision into the exact bytes the terminal client understands.

*Call graph*: called by 6 (_answer, _fulfill_secret, _say_lines, channel, directives_for, stream_directives).


##### `resolve_workspace`  (lines 62–69)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Finds which workspace a request claims to belong to before the route itself runs. If the request has no usable bearer token, it returns nothing so the request can be rejected.

**Data flow**: It reads the `Authorization` header, checks that it uses the `Bearer` form, extracts the token, and asks the bearer-token code for the workspace claim. The output is a workspace UUID or `None`.

**Call relations**: The shared surface routing layer calls this as an early identification step. It delegates token reading to `workspace_claim`, while the later `channel` handler verifies the same token again for the member email.

*Call graph*: 1 external calls (workspace_claim).


##### `directives_for`  (lines 72–96)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Turns one live conversation event into one or more terminal commands. It is where model text, tool activity, costs, finished answers, and parked prompts become things the shell can display.

**Data flow**: It receives a live frame from the conversation system, plus context such as whether text has already streamed and which credential prompts still need answers. It chooses the right terminal directives: text deltas become `txt`, tool work becomes `note`, cost updates become `status`, finished turns go through `_answer`, and parked turns ask the user for input.

**Call relations**: `stream_directives` calls this for each frame it reads from the turn stream. `directives_for` uses `_activity` to describe tool calls, `_answer` to close a finished turn, and `directive` to encode simpler frames directly.

*Call graph*: calls 3 internal fn (_activity, _answer, directive); called by 1 (stream_directives).


##### `_activity`  (lines 99–101)

```
def _activity(frame: ToolCall) -> str
```

**Purpose**: Creates a short human-readable sentence for a tool call. This lets the terminal show that work is happening without exposing low-level tool details.

**Data flow**: It receives a tool-call frame, looks for a description or preview, and combines that with the tool name. The output is text like “running search: looking up prices” or just “running search”.

**Call relations**: `directives_for` calls this when it sees a tool-call frame, then wraps the returned sentence in a `note` directive for the shell.

*Call graph*: called by 1 (directives_for).


##### `_answer`  (lines 104–132)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Creates the final terminal commands for a completed, failed, or cancelled turn. It decides whether to show the answer, ask for missing secrets, prompt for the next message, or end the shell session.

**Data flow**: It receives a terminal frame, a flag saying whether the answer text has already streamed, any credential prompts still waiting, and an optional connection URL message. For a successful turn it may output answer lines, secret prompts, a connection instruction, and then an input prompt. For a failed turn it outputs an error-like message and prompts again. For a cancelled turn it says so and sends an exit command.

**Call relations**: `directives_for` hands terminal frames to `_answer` because they need special end-of-turn treatment. `_answer` uses `_say_lines` for normal text and `directive` for prompts, secret requests, and exit commands.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 135–136)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Splits a block of answer text into separate `say` commands. This keeps multi-line messages friendly for the shell’s line-based reader.

**Data flow**: It receives a text string, splits it into lines, turns each line into a `say` directive, and returns all those directive bytes. If the text has no split lines, it still returns one directive for the original text.

**Call relations**: `_answer` uses this when it needs to display final answer text or failure text that was not already streamed as smaller pieces.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 139–199)

```
async def stream_directives(frames: AsyncIterator[tuple[str, LiveFrame]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[[], Awaitable[str]] | None=
```

**Purpose**: Streams live conversation events to the terminal for a limited time. If the answer is not finished before the time limit, it tells the shell to reconnect and continue polling.

**Data flow**: It receives an async stream of live frames, a maximum hold time, and optional helpers for checking pending credential prompts and creating connection URLs. It reads frames until the turn ends, the source ends, or the deadline arrives. Each frame is converted into directive bytes and yielded to the HTTP response. If the stream did not reach a natural ending, it yields a `poll` directive.

**Call relations**: `channel` uses this as the body of its streaming HTTP response. Inside the loop it calls `_next` so end-of-stream is easy to handle, asks `directives_for` to render each frame, and uses `directive` itself when it must tell the client to poll again.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 2 external calls (get_running_loop, wait_for).


##### `_next`  (lines 202–208)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next item from an asynchronous frame stream, but turns “there is no next item” into `None`. This makes timeout handling simpler for the streaming loop.

**Data flow**: It receives an async iterator of cursor-and-frame pairs. It awaits the next pair and returns it, or catches the normal end-of-stream signal and returns `None` instead.

**Call relations**: `stream_directives` calls `_next` inside a timed wait. By hiding the low-level end-of-stream exception, `_next` lets the main streaming code treat “finished” as ordinary data.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 211–215)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Verifies that a request’s bearer token is valid for the current workspace and extracts the email identity from it. If the token is missing or invalid, it returns nothing.

**Data flow**: It reads the `Authorization` header, checks for a bearer token, and passes that token plus the workspace ID to `verify_token`. The result is the authenticated email address or `None`.

**Call relations**: `channel` calls this at the start of request handling. This separates the small token-checking step from the larger work of linking a member, finding a conversation, and streaming the response.

*Call graph*: called by 1 (channel); 1 external calls (verify_token).


##### `channel`  (lines 218–249)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles one POST from the terminal client for one named channel. It authenticates the user, stores a new message if there is one, or resumes the latest turn if the body is empty.

**Data flow**: It receives the surface context and HTTP request. It verifies the email, links or creates the member record, checks whether the request is actually a secret submission, finds the conversation for this email and channel, reads the body, and either admits a new turn or tails an existing one. The result is either a plain error/prompt response or a streaming text response of terminal directives.

**Call relations**: This is the route handler named in `ROUTES`. It calls `_authenticated_email` first, may hand secret submissions to `_fulfill_secret`, and otherwise uses the privileged `SurfaceContext` to link members, find conversations, admit messages, find latest turns, and tail live frames. It then hands that frame stream to `stream_directives` for rendering.

*Call graph*: calls 10 internal fn (admit, conversation_for, latest_turn, link_member, linked_member, tail, _authenticated_email, _fulfill_secret, directive, stream_directives); 5 external calls (partial, conversation_audience, PlainTextResponse, body, StreamingResponse).


##### `_fulfill_secret`  (lines 252–271)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores one private credential value entered by the terminal user. The secret is not admitted as a chat message, so it does not appear in the conversation transcript.

**Data flow**: It receives the context, request, member ID, and sealed credential request ID. It reads the target slot from a header and the secret value from the body, rejects empty or oversized values, then asks the surface context to fulfill the credential request. It returns a plain text directive saying whether the value was stored or why it was not.

**Call relations**: `channel` calls this when a request includes the secret header. `_fulfill_secret` relies on `SurfaceContext.fulfill_credential_request` to verify that the sealed request, member, slot, and timing are valid, and uses `directive` to send a simple status line back to the shell.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling`

This file is the bridge between a browser and the core UFO system. It does not run agents itself. Instead, it checks who the browser user is, checks which agents they are allowed to see, and then asks the core surface context to do the real work: create conversations, admit chat messages, read transcripts, list connections, and stream live turn updates.

The web session is based on a signed bearer token stored in a cookie. A bearer token is like a tamper-proof pass saying “this email belongs to this workspace until this time.” The portal first accepts that token through a POST form, stores it as the `ufo_session` cookie, and then verifies it on later requests. This keeps the token out of URLs, where it could leak through browser history or logs.

Most routes follow the same pattern: authenticate the cookie, build the member’s web audience, check that the requested agent is inside that audience, then return only the allowed view. Chat messages go into the shared durable queue used by other surfaces. Live replies are delivered through server-sent events, a browser-friendly stream of small events. Admin-only pages reuse the same gate but expose workspace-wide facts such as members, seats, installations, grants, and spending.

#### Function details

##### `resolve_workspace`  (lines 55–78)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace an incoming web request belongs to before the normal route handler runs. It reads the signed token from the session cookie, or from the login form for the one request that opens a session.

**Data flow**: It receives an HTTP request and surface auth context. It first tries to extract a workspace claim from the `ufo_session` cookie. If that fails on a POST request, it reads the submitted form token and tries that instead. If a plain GET asks for the portal page without a valid workspace yet, it returns the static portal HTML; otherwise it returns the workspace ID or `None` to reject the request.

**Call relations**: The shared surface fleet calls this before dispatching to routes in this file. It uses the bearer-token helper to read the workspace and may return the portal shell directly so an unauthenticated visitor can see the token form.

*Call graph*: 3 external calls (workspace_claim, HTMLResponse, form).


##### `_authenticate`  (lines 81–92)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | None
```

**Purpose**: Turns the browser session cookie into the actual member identity used by the web portal. It verifies the token for the current workspace and makes sure there is a linked member record for the email.

**Data flow**: It reads the session cookie from the request. If the cookie is missing or does not verify for the workspace, it returns `None`. If the token is valid, it finds or creates the member linked to the token’s email, then returns the member ID and email.

**Call relations**: _audience_for calls this at the start of nearly every protected route. It relies on the core surface context to look up or create the member, so later route handlers can make permission checks using a stable member ID.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 1 (_audience_for); 1 external calls (verify_token).


##### `portal_page`  (lines 95–98)

```
async def portal_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the single HTML page that makes up the web portal shell. The same page is used whether the visitor is already signed in or still needs to paste a token.

**Data flow**: It receives the request and returns the preloaded `portal.html` text as an HTML response. It does not inspect the user or change any state.

**Call relations**: This is the GET handler for the portal root. The browser-side page then calls API routes such as `agents_index` to decide whether to show the login form or the signed-in interface.

*Call graph*: 1 external calls (HTMLResponse).


##### `open_session`  (lines 101–119)

```
async def open_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Starts a browser session by accepting a submitted bearer token and storing it in a cookie. It redirects the browser back to the portal so later requests carry the cookie automatically.

**Data flow**: It reads the POSTed form field named `token`. If the field is missing or shaped in a way that cannot safely be written into a cookie, it returns a 400 JSON error. Otherwise it creates a redirect response and adds the token as the `ufo_session` cookie.

**Call relations**: This is the POST handler for the portal root. `resolve_workspace` may already have used the submitted token to scope the request, and later protected routes verify the cookie again through `_authenticate`.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


##### `_agent_param`  (lines 122–126)

```
def _agent_param(request: Request) -> UUID | None
```

**Purpose**: Safely reads an agent ID from the route path. It prevents malformed path text from being treated as a real agent.

**Data flow**: It looks at `request.path_params['agent_id']` and tries to parse it as a UUID, which is the standard unique identifier format used here. If parsing works, it returns the UUID; if not, it returns `None`.

**Call relations**: Agent-specific routes call this before doing permission checks. If it returns `None`, those routes answer as if the agent does not exist.

*Call graph*: called by 5 (chat, connections, credentials, sources, transcript); 1 external calls (UUID).


##### `_conversation_key`  (lines 129–130)

```
def _conversation_key(agent_id: UUID, email: str) -> str
```

**Purpose**: Builds the stable name used to find one member’s conversation with one agent. This keeps each person’s chat with each agent separate.

**Data flow**: It receives an agent ID and an email address. It combines them into a string shaped like `agent_id/email`, which the core conversation store can use as a lookup key.

**Call relations**: The chat route uses this key when creating or finding the conversation to send a new message to. The transcript route uses the same key later to find the matching history.

*Call graph*: called by 2 (chat, transcript).


##### `_audience_for`  (lines 133–140)

```
async def _audience_for(ctx: SurfaceContext, request: Request) -> tuple[UUID, str, WebAudience] | Response
```

**Purpose**: Performs the common sign-in and permission setup used by protected portal routes. It answers either the member, email, and allowed web audience, or an HTTP 401 response.

**Data flow**: It calls `_authenticate` to verify the session cookie. If authentication fails, it returns a response saying the session is missing or unknown. If authentication succeeds, it asks the web-audience code which agents and admin powers this email has, then returns that information with the member ID and email.

**Call relations**: Most route handlers in this file start here instead of repeating authentication code. The returned audience is then used by chat, transcript, list, stream, admin, and spend routes to decide what the user may see.

*Call graph*: calls 1 internal fn (_authenticate); called by 9 (admin_index, agents_index, chat, connections, credentials, sources, spend, stream, transcript); 3 external calls (Response, web_audience, web_extension).


##### `agents_index`  (lines 143–158)

```
async def agents_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the signed-in member’s basic portal starting data: who they are and which agents they can use. This is usually the first API call the browser makes after loading the page.

**Data flow**: It asks `_audience_for` to authenticate the request and load the user’s web audience. If that fails, it returns the failure response. Otherwise it turns the member email, admin flag, and allowed agents into JSON for the browser.

**Call relations**: The portal page calls this to decide whether the user is signed in and to fill the agent switcher. It depends on `_audience_for` so the list already reflects the user’s grants.

*Call graph*: calls 1 internal fn (_audience_for); 1 external calls (JSONResponse).


##### `chat`  (lines 161–178)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts a new chat message from the browser and submits it to the core conversation queue. It is the point where a web user’s typed text becomes an agent turn.

**Data flow**: It authenticates the user, parses the agent ID, checks the agent is allowed, reads the request body as the message, rejects empty or overly large messages, finds or creates the member’s conversation with that agent, and admits the message as a new turn. It returns the new turn ID as JSON.

**Call relations**: The browser calls this when the user sends a message. It uses `_agent_param`, `_conversation_key`, and the core surface context, then the browser can use the returned turn ID with `stream` to watch the answer.

*Call graph*: calls 5 internal fn (admit, conversation_for, _agent_param, _audience_for, _conversation_key); 4 external calls (conversation_audience, JSONResponse, body, Response).


##### `_rendered_text`  (lines 181–191)

```
def _rendered_text(message: Message) -> str
```

**Purpose**: Converts a stored message into the plain text the portal should display. It hides internal context wrapping and ignores non-text content such as tool traffic.

**Data flow**: It receives a message that may contain a simple string or structured content blocks. It extracts text, removes a leading `<context>...</context>` block from user messages, trims whitespace, and returns the cleaned text.

**Call relations**: The transcript route calls this for each recorded message. It keeps the browser transcript focused on the human-visible conversation rather than internal engine framing.

*Call graph*: called by 1 (transcript).


##### `transcript`  (lines 194–216)

```
async def transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the saved conversation history between the member and one agent. It gives the browser a text-only transcript to show when a chat is opened or refreshed.

**Data flow**: It authenticates the user, parses and checks the agent ID, builds the conversation key, and asks the core context for that conversation and its transcript. If no conversation or transcript exists, it returns an empty list. Otherwise it cleans each message with `_rendered_text` and returns role/text pairs as JSON.

**Call relations**: The browser calls this when loading an agent chat panel. It shares the same conversation key as `chat`, so the displayed history matches the conversation that new messages will enter.

*Call graph*: calls 6 internal fn (find_conversation, read_transcript, _agent_param, _audience_for, _conversation_key, _rendered_text); 2 external calls (JSONResponse, Response).


##### `connections`  (lines 219–231)

```
async def connections(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists connector accounts visible to the member for a selected agent. Connector accounts are external service connections the agent may use, such as linked tools or integrations.

**Data flow**: It authenticates the request, reads the agent ID, confirms the agent is in the user’s audience, and asks the core context for connections visible to that member. Admin users get the broader admin view. The result is converted to JSON.

**Call relations**: The browser uses this to fill the connections panel for an agent. It relies on `_audience_for` and `_agent_param` to avoid showing connections for agents the user cannot access.

*Call graph*: calls 3 internal fn (list_agent_connections, _agent_param, _audience_for); 2 external calls (JSONResponse, Response).


##### `credentials`  (lines 234–247)

```
async def credentials(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows which bring-your-own-key credential slots a member can fill, without ever returning secret values. It tells the portal about fill state, not the keys themselves.

**Data flow**: It authenticates the user, checks that the path’s agent is allowed, asks the core context for declared credential slots, and returns those slot records as JSON.

**Call relations**: The browser calls this for the credentials panel. The agent path is used for navigation and permission gating, while the actual slot list comes from the workspace’s declared credential setup.

*Call graph*: calls 3 internal fn (list_credential_slots, _agent_param, _audience_for); 2 external calls (JSONResponse, Response).


##### `sources`  (lines 250–262)

```
async def sources(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists source bindings visible to the member, such as registered content sources the system can index or use. Admins can see the wider workspace set.

**Data flow**: It authenticates the request, parses and checks the agent ID, then asks the core context for sources visible to the member, with an admin flag if appropriate. It serializes the source records into JSON.

**Call relations**: The browser uses this for the sources panel attached to an agent. The route keeps the same audience wall as the rest of the portal, so an out-of-audience agent reveals nothing.

*Call graph*: calls 3 internal fn (list_sources, _agent_param, _audience_for); 2 external calls (JSONResponse, Response).


##### `stream`  (lines 265–288)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live server-sent events stream for one agent turn. This lets the browser watch the answer arrive piece by piece instead of waiting for the whole turn to finish.

**Data flow**: It authenticates the member, parses the turn ID, checks that the turn exists, checks that it belongs to this member, and confirms its agent is still allowed by the member’s web audience. It reads the browser’s last seen event ID for reconnect support, then returns a streaming response powered by `_events`.

**Call relations**: After `chat` returns a turn ID, the browser calls this route to follow that turn. It hands the actual event generation to `_events` once ownership and audience checks have passed.

*Call graph*: calls 4 internal fn (turn_detail, turn_owner, _audience_for, _events); 3 external calls (Response, StreamingResponse, UUID).


##### `_events`  (lines 291–308)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Produces the bytes sent down the live event stream for a turn. It also turns a special connection request from the agent into a browser event containing a usable connection URL.

**Data flow**: It receives the core context, turn ID, member ID, and a resume cursor. It tails live frames from the core hub. For ordinary frames, it formats them with `_sse`. If a terminal frame contains a connection request, it asks the core for a connection URL and emits either a `connect` event or a `connect_error` event before continuing.

**Call relations**: Only `stream` calls this, after it has already checked the member may view the turn. `_events` then talks to the hub tail and uses `_sse` to package each frame for the browser.

*Call graph*: calls 3 internal fn (connect_url, tail, _sse); called by 1 (stream); 1 external calls (dumps).


##### `admin_index`  (lines 311–348)

```
async def admin_index(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the workspace administration snapshot for admins. It includes agents, their web grants and installations, members, and seat information.

**Data flow**: It authenticates the request and loads the web audience. If the user is not an admin, it returns a not-found response. If they are an admin, it gathers installations from the core context, web grants from the web extension store, and seat state from the seats system, then returns a JSON snapshot.

**Call relations**: The browser calls this for the admin view. It uses the same `_audience_for` gate as normal routes, but requires the admin flag before reading workspace-wide information.

*Call graph*: calls 2 internal fn (list_installations, _audience_for); 5 external calls (__init__, JSONResponse, Response, granted_emails, web_extension).


##### `spend`  (lines 351–365)

```
async def spend(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Shows an admin-only spending report for the workspace over a chosen time window. It mirrors the workspace-level financial rollup rather than a personal bill.

**Data flow**: It authenticates the user and checks for admin access. It reads `window_seconds` from the query string, falling back to the default one-day window, asks the core context for a spend rollup, turns that report into an HTML page with `_spend_page`, and returns it.

**Call relations**: This is the route behind the spend link shown to admins. It delegates the accounting math to the core context and only formats the resulting report for the browser.

*Call graph*: calls 3 internal fn (spend_rollup, _audience_for, _spend_page); 2 external calls (HTMLResponse, Response).


##### `_sse`  (lines 368–385)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as a server-sent event. Server-sent events are simple text chunks that browsers can receive as a continuous stream.

**Data flow**: It receives a cursor and a live frame. If the cursor is present, it writes it as the event ID so the browser can resume after a disconnect. It then chooses an event name based on the frame kind, serializes the frame as JSON, and returns the complete byte chunk.

**Call relations**: _events calls this for each frame it reads from the core tail. The browser receives these formatted chunks through the `stream` response.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_money`  (lines 388–389)

```
def _money(micro_usd: int) -> str
```

**Purpose**: Formats a stored micro-dollar amount as a readable dollar string. A micro-dollar is one millionth of a US dollar, used so accounting can stay precise with integers.

**Data flow**: It receives an integer number of micro-dollars, divides it by the constant number of micro-dollars per dollar, and returns a string such as `$0.123456`.

**Call relations**: The spend-page helpers call this whenever they need to display costs. It keeps all money formatting consistent in the admin spend page.

*Call graph*: called by 2 (_spend_page, _subject_rows).


##### `_subject_rows`  (lines 392–397)

```
def _subject_rows(subjects: tuple[SubjectTotal, ...]) -> str
```

**Purpose**: Builds HTML table rows for spending grouped by subject, such as by member or by agent. It also supplies a friendly `none` row when there is no spending.

**Data flow**: It receives a tuple of subject totals. For each one, it HTML-escapes the label to prevent it from being treated as page markup, formats the cost with `_money`, and joins the rows into one HTML string. If the tuple is empty, it returns a single placeholder row.

**Call relations**: _spend_page calls this for the member and agent spending tables. It depends on `_money` for cost display and `html.escape` for safe labels.

*Call graph*: calls 1 internal fn (_money); called by 1 (_spend_page); 1 external calls (escape).


##### `_spend_page`  (lines 400–420)

```
def _spend_page(report: SpendReport) -> str
```

**Purpose**: Turns a spend report into a small standalone HTML page. It presents total cost, cost by dimension, cost by member, and cost by agent.

**Data flow**: It receives a spend report from the accounting system. It builds table rows for dimensions directly, formats money with `_money`, escapes labels where needed, uses `_subject_rows` for member and agent tables, and returns one complete HTML document string.

**Call relations**: The `spend` route calls this after getting the rollup from the core context. This helper is only presentation: it does not calculate spending, it formats the already-computed report.

*Call graph*: calls 2 internal fn (_money, _subject_rows); called by 1 (spend); 1 external calls (escape).


### Shared surface gateway
The core surface extension provides the trusted orchestration boundary used by all live user-facing integrations.

### `core/src/ufo/ext/surface.py`

`orchestration` · `request handling and background writeback delivery`

A “surface” is the place where a person talks to the system: a Slack thread, a browser page, or a similar interface. This file gives those surfaces a special, trusted context called SurfaceContext. That context can do things ordinary extensions cannot do, such as saying “this external Slack user is this workspace member,” creating or finding the right conversation, putting a member’s message onto the turn queue, and reading workspace credential slots when needed.

The file supports two delivery styles. A live surface, like the web UI, keeps a connection open and streams the answer as frames arrive. A durable surface, like Slack, cannot rely on a live connection, so it records that a finished turn needs a writeback. The WritebackPoller later finds those finished turns, claims them so only one worker sends each one, posts the reply, uploads attachments, and records success or retry information.

The file also provides read-only views for administration and debugging: agents, connections, credential slots, sources, conversations, turns, transcripts, compactions, and sandbox files. A key safety theme runs through the file: every read and write is tied to the workspace, and checks are made before reading unscoped storage such as blobs.

#### Function details

##### `MemberAdmitter.admit`  (lines 122–130)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> Admitted
```

**Purpose**: This protocol method describes the core ability to accept a member’s message into a conversation. A surface uses it when a real user has spoken and the message should become work for the agent.

**Data flow**: It receives a conversation id, message text, optional duplicate-protection key, optional turn context, and the speaking member id. The real implementation records or merges the message and returns the turn id plus whether this call opened a new run.

**Call relations**: SurfaceContext.admit is the public surface-facing wrapper around this capability. Concrete core admission code implements the protocol, while Slack, web, sample, and UFO surfaces reach it through SurfaceContext.


##### `TurnTailer.tail`  (lines 139–139)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This protocol method describes how a live surface reads the stream of frames for one running turn. It is the read-side twin of admitting a message.

**Data flow**: It receives a turn id and an optional cursor saying where to resume. It yields cursor-and-frame pairs until the turn ends.

**Call relations**: SurfaceContext.tail delegates to this protocol. Live surfaces and debugger routes use it so they do not touch the internal event hub directly.


##### `ConnectionView._aware_utc`  (lines 229–230)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes sure connection timestamps include timezone information. If the database gives a plain timestamp, it treats it as UTC.

**Data flow**: It receives a datetime value. If the value already has a timezone, it returns it unchanged; otherwise it adds UTC.

**Call relations**: Pydantic calls this automatically when building ConnectionView objects, mainly from SurfaceContext.list_agent_connections.

*Call graph*: 1 external calls (replace).


##### `SourceView._aware_utc`  (lines 260–261)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes source sync timestamps timezone-aware. It prevents user interfaces from guessing the wrong time zone.

**Data flow**: It receives the next sync datetime. It returns the same value if it is already timezone-aware, or a UTC-marked version if not.

**Call relations**: Pydantic runs it while SurfaceContext.list_sources builds SourceView results.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 279–282)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This validator normalizes conversation timestamps to UTC when needed. It also safely allows a missing last-turn time.

**Data flow**: It receives a datetime or None. None stays None; timezone-aware values stay as they are; plain datetimes are marked as UTC.

**Call relations**: It runs automatically when SurfaceContext.list_conversations creates ConversationSummary objects.

*Call graph*: 1 external calls (replace).


##### `LedgerEntry._aware_utc`  (lines 296–297)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes accounting timestamps explicit about being UTC. That keeps cost records displayed consistently.

**Data flow**: It receives a datetime and returns either the original timezone-aware value or a UTC-marked version.

**Call relations**: It runs when SurfaceContext.turn_detail builds LedgerEntry objects for debugger and web views.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 310–315)

```
def _fulfilled_marker_key(workspace_id: UUID, sealed: str, slot: str) -> str
```

**Purpose**: This helper creates the blob-store key used to remember that one credential prompt slot has already been filled. It avoids showing the same prompt again after success.

**Data flow**: It receives a workspace id, a sealed credential request string, and a slot name. It hashes the sealed request and combines the pieces into a stable storage path.

**Call relations**: SurfaceContext.credential_prompt_pending reads this marker, and SurfaceContext.fulfill_credential_request writes it after storing the credential.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_email_domain`  (lines 318–322)

```
def _email_domain(email: str) -> str
```

**Purpose**: This helper extracts the domain part of an email address in a cautious way. Malformed addresses produce an empty domain, so they cannot accidentally pass a domain check.

**Data flow**: It receives an email string, trims and lowercases it, and returns the text after the final @ only if both sides are present.

**Call relations**: SurfaceContext.is_operator_workspace uses it to recognize the operator’s own workspace, and SurfaceContext.join_member uses it to decide whether a new email belongs to the workspace’s domain.

*Call graph*: called by 2 (is_operator_workspace, join_member).


##### `_main_agent`  (lines 325–338)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: This helper finds the main agent for a workspace. It is the fallback agent when a surface installation has not chosen a different one.

**Data flow**: It receives a workspace id, reads the agent table inside a workspace transaction, and returns the main agent id. If none exists, it raises an error because the workspace is incomplete.

**Call relations**: _bind_surface_installation uses it for new bindings, and SurfaceContext._surface_agent uses it when no surface-specific agent is configured.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 341–373)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str) -> None
```

**Purpose**: This helper records that an external surface installation, such as a Slack team, belongs to a workspace. Without this binding, shared incoming requests could not be routed to the right workspace.

**Data flow**: It receives a workspace id, surface name, and installation id. It inserts or updates the binding, defaulting new bindings to the main agent, and raises a conflict if that installation is already owned elsewhere.

**Call relations**: SurfaceContext.bind_installation calls it from a surface OAuth callback. SurfaceInstallationAccess.bind calls it when a tool registers an installation for a declared surface.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.credential`  (lines 399–402)

```
async def credential(self, slot: str) -> str
```

**Purpose**: This method lets a trusted surface read one configured credential slot for its workspace. It is used for secrets a surface needs to verify or talk to its provider.

**Data flow**: It receives a slot name. If this context has a credential store, it reads the value for the workspace and returns it; otherwise it raises an error.

**Call relations**: Slack surface code calls it for signing secrets, posting, identity checks, and ingest-related provider calls.

*Call graph*: called by 9 (_ctx_signing_secret, _identity, _post_ephemeral, _room_audience, _run_identity_proof, attach, ingest, interactive, post).


##### `SurfaceContext.credential_prompt_pending`  (lines 404–418)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: This method answers whether a specific credential prompt should still be shown. It keeps already-answered, expired, or invalid prompts from reappearing.

**Data flow**: It receives a sealed request and slot name. It opens and verifies the seal, checks that it belongs to this workspace and names the slot, then checks whether the fulfillment marker exists.

**Call relations**: Surface rendering code can call this before showing a prompt. It relies on _fulfilled_marker_key and the credential request opener.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 420–430)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: This method opens a sealed credential handoff and returns its claims. It is useful when a browser callback needs to know which workspace, member, and slot a credential authorization was for.

**Data flow**: It receives a sealed string, verifies it with the credential store’s signing/encryption key, and returns the decoded request state. If no credential store exists or the seal is invalid, it raises the appropriate error.

**Call relations**: Slack’s OAuth callback uses it before fulfilling a requested credential.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 432–457)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: This method stores a credential value in response to a sealed request. It checks that the right member is filling the right slot for the right workspace before writing anything.

**Data flow**: It receives the sealed request, slot, value, and member id. It verifies the seal, checks workspace, member, and slot, writes the credential, then writes a fulfillment marker blob.

**Call relations**: Slack OAuth callbacks and the built-in UFO surface use it to complete credential collection. It pairs with credential_prompt_pending, which later sees the marker.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 2 (oauth_callback, _fulfill_secret); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 459–465)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: This method lets a surface bind its external installation id to the current workspace. It is the surface-context wrapper for installation registration.

**Data flow**: It receives an installation id and passes the current workspace and surface name to the shared binding helper.

**Call relations**: Slack calls it during OAuth installation so later Slack requests can be resolved to the correct workspace.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.public_base_url`  (lines 468–471)

```
def public_base_url(self) -> str | None
```

**Purpose**: This property returns the deployment’s public base URL, if one is configured. Surfaces use it when they need to build callback or download links.

**Data flow**: It reads the stored public URL value from the context and returns it, or None when not configured.

**Call relations**: It is part of the information a surface can read from SurfaceContext; artifact_link also depends on the same configured base URL.


##### `SurfaceContext.artifact_link`  (lines 473–484)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: This method creates a temporary public download link for a shared artifact. It is used when a surface cannot upload the file directly but can show a link.

**Data flow**: It receives a SharedArtifact. If token signing and a public base URL are configured, it mints a time-limited token and returns a download URL; otherwise it returns None.

**Call relations**: Slack uses it for oversized attachment links. The generated link is verified later by the web artifact download route.

*Call graph*: called by 1 (_oversize_link_line); 2 external calls (now, mint_artifact_token).


##### `SurfaceContext._identity_member`  (lines 486–499)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: This private helper looks up which workspace member is linked to a surface-specific external user id. It is the common identity lookup used by several public methods.

**Data flow**: It receives a surface name and external id. It reads the surface_identity table for this workspace and returns the member id or None.

**Call relations**: SurfaceContext.linked_member uses it for the current surface, and SurfaceContext.adopt_identity uses it to copy identity from a peer surface.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 501–502)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: This method asks whether the current surface already knows an external user as a workspace member. It lets a surface avoid re-linking someone it has already seen.

**Data flow**: It receives an external id and returns the linked member id, or None if no link exists.

**Call relations**: Sample, Slack, UFO, and web surfaces call it during authentication or message intake.

*Call graph*: calls 1 internal fn (_identity_member); called by 6 (_surface_ingest, _surface_live_admit, _resolve_member, interactive, channel, _authenticate).


##### `SurfaceContext._workspace_email`  (lines 504–515)

```
async def _workspace_email(self) -> str | None
```

**Purpose**: This private helper reads the first member email for the workspace. That email’s domain is treated as the workspace’s trusted domain.

**Data flow**: It reads the earliest member row for the workspace and returns its email, or None if there are no members.

**Call relations**: SurfaceContext.is_operator_workspace and SurfaceContext.join_member use it for domain comparisons.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.is_operator_workspace`  (lines 517–527)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: This method tells whether the current workspace appears to be the system operator’s own workspace. It gates internal-only display details.

**Data flow**: It reads the workspace’s first member email, extracts its domain, and compares it to the configured operator domain.

**Call relations**: Slack post rendering calls it before showing operator-only accounting or debug information.

*Call graph*: calls 2 internal fn (_workspace_email, _email_domain); called by 1 (post).


##### `SurfaceContext.adopt_identity`  (lines 529–552)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: This method links the current surface’s external id to a member already known by another surface. It lets the same human keep one identity across surfaces.

**Data flow**: It receives a peer surface name and external id. It looks up the peer identity; if found, it inserts a matching identity for the current surface and returns the member id.

**Call relations**: The sample live surface uses it when one surface identity should follow another. If two requests race, the losing insert is logged and the same member is still returned.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 554–583)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: This method links an external surface user id to an existing workspace member by email. It does not create a new member.

**Data flow**: It receives an external id and email. It searches for a matching member email in this workspace, inserts the identity link if found, and returns the member id or None.

**Call relations**: SurfaceContext.join_member builds on it. Sample, UFO, and web surfaces also call it directly during authentication or channel setup.

*Call graph*: called by 4 (join_member, _surface_ingest, channel, _authenticate); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 585–602)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: This method links an external user to a member, creating the member first if their verified email is on the workspace’s own domain. It supports first-contact teammate onboarding.

**Data flow**: It receives an external id and verified email. It first tries link_member; if no member exists, it compares email domains, creates a member when allowed, and links again.

**Call relations**: Slack member resolution calls it because Slack can provide a channel-verified email. It uses _workspace_email, _email_domain, create_member, and link_member.

*Call graph*: calls 3 internal fn (_workspace_email, link_member, _email_domain); called by 1 (_resolve_member); 2 external calls (workspace_tx, create_member).


##### `SurfaceContext._conversation_lookup`  (lines 604–613)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: This private helper builds the database query for finding a conversation by the current surface’s queue key. The queue key is the surface’s own way of naming a thread or room.

**Data flow**: It receives a queue key and returns a SQL query that selects the matching conversation id, member id, and audience for this workspace and surface.

**Call relations**: SurfaceContext.find_conversation and SurfaceContext.conversation_for use the same lookup so they agree on conversation identity.

*Call graph*: called by 2 (conversation_for, find_conversation); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 615–621)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: This method finds an existing conversation without creating one. It is useful when a surface needs to know whether a thread is already participating.

**Data flow**: It receives a queue key, runs the shared lookup, and returns the conversation id or None.

**Call relations**: Slack, web, and debugger-style flows call it before admitting replies or showing transcript data.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 4 (_debug_link, _participating_conversation, interactive, transcript); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_for`  (lines 623–695)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None) -> UUID
```

**Purpose**: This method gets or creates the conversation for a surface queue key. It is the main way an incoming message finds the durable conversation it belongs to.

**Data flow**: It receives a queue key, audience, and optional agent id. It reuses an existing conversation when present, narrows the audience if the new information is more specific, or creates a new conversation bound to an agent.

**Call relations**: Sample, Slack, UFO, and web surfaces call it before admitting a message. It uses _surface_agent when no explicit agent is provided and protects against duplicate creation races.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat); 9 external calls (insert, select, update, audience_member, narrow_audience, parse_audience, workspace_tx, log, uuid4).


##### `SurfaceContext._surface_agent`  (lines 697–709)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: This private helper chooses which agent a surface conversation should use. It prefers the surface installation’s bound agent and falls back to the main agent.

**Data flow**: It reads the surface_installation table for this workspace and surface. If a bound agent exists it returns that id; otherwise it returns _main_agent.

**Call relations**: SurfaceContext.conversation_for calls it when creating a conversation without an explicitly chosen agent.

*Call graph*: calls 1 internal fn (_main_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.admit`  (lines 711–734)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> Admitted
```

**Purpose**: This method admits an inbound user message into an existing conversation. It is the surface-facing entry point for turning chat text into agent work.

**Data flow**: It receives the conversation id, message body, optional idempotency key, optional context, and speaker member id. It delegates to the injected MemberAdmitter and returns the admitted turn information.

**Call relations**: Sample, Slack, UFO, and web surfaces call it after resolving identity and conversation. The underlying admission layer decides whether to open a new run, merge, or deduplicate.

*Call graph*: called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat).


##### `SurfaceContext.connect_url`  (lines 736–742)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: This method starts a connector authorization flow for a turn and member. It returns a URL the member can use to connect an external account.

**Data flow**: It receives a turn id and member id. It loads the installed connect flow, creates a handoff, and returns the authorization URL; if connect is unavailable it raises a request error.

**Call relations**: Slack interactive actions and web event handling use it when an agent asks the user to connect a provider.

*Call graph*: called by 2 (interactive, _events); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.admitted_body`  (lines 744–769)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: This method checks what message body was actually admitted for an idempotency key. It helps surfaces resolve races between repeated clicks or deliveries.

**Data flow**: It receives an idempotency key. It first looks for a turn with that key, then for an inbound message row, and returns the stored body or None.

**Call relations**: Slack interactive handling uses it to confirm which answer won before changing the visible affordance.

*Call graph*: called by 1 (interactive); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 771–785)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: This method returns the member who owns the conversation containing a turn. Live surfaces use it as a permission check before streaming turn output.

**Data flow**: It receives a turn id, joins the turn to its conversation, and returns the conversation member id or None.

**Call relations**: The web stream route and sample live admit code call it before allowing a member to follow live frames.

*Call graph*: called by 2 (_surface_live_admit, stream); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 787–804)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: This method finds the newest turn in a conversation. It is useful when a surface reconnects or needs to resume around the most recent activity.

**Data flow**: It receives a conversation id, orders that conversation’s turns by sequence descending, and returns the latest turn id or None.

**Call relations**: Slack participation checks and the UFO channel flow call it to know what turn is current.

*Call graph*: called by 2 (_participating_conversation, channel); 2 external calls (select, workspace_tx).


##### `SurfaceContext.tail`  (lines 806–809)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This method streams live frames for a turn through the injected tailer. It lets live surfaces send progress and final output to a connected client.

**Data flow**: It receives a turn id and optional resume cursor, then returns the async stream from the tailer.

**Call relations**: Debugger, sample, UFO, and web event streams call it instead of reading the internal hub directly.

*Call graph*: called by 4 (_events, _surface_frames, channel, _events).


##### `SurfaceContext.spend_rollup`  (lines 811–815)

```
async def spend_rollup(self, window_seconds: int) -> SpendReport
```

**Purpose**: This method reads a summary of recent workspace spending. It supports surface pages that show usage or cost information.

**Data flow**: It receives a time window in seconds, opens a workspace transaction, and returns a SpendReport for that window.

**Call relations**: The web spend page and sample live surface use it for member-visible spend information.

*Call graph*: called by 2 (_surface_live_admit, spend); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 817–832)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This method writes an uploaded file into a conversation’s sandbox workspace before the agent runs. The sandbox is like the agent’s working folder.

**Data flow**: It receives a conversation id, relative path, and byte chunks. It accumulates the chunks up to a size limit, then writes the complete file through the sandbox carrier.

**Call relations**: Slack file download handling and the sample surface call it so files attached by a user are available to agent tools.

*Call graph*: called by 2 (_surface_ingest, _download_files).


##### `SurfaceContext.list_agents`  (lines 834–860)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: This method lists all agents in the workspace for a surface UI. It includes enough information for a user or admin to choose or inspect an agent.

**Data flow**: It reads agent rows for the workspace, ordered with the main agent first, and returns AgentSummary objects.

**Call relations**: The web audience picker calls it when showing available agents.

*Call graph*: called by 1 (web_audience); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 862–907)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: This method lists connector accounts available to one agent, filtered by what the requesting member may see. Admins see all; regular members see shared connections and their own.

**Data flow**: It receives an agent id, member id, and admin flag. It queries connector grants and owners, applies the visibility rule, and returns ConnectionView rows.

**Call relations**: The web connections page calls it to populate the connections panel.

*Call graph*: called by 1 (connections); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.list_credential_slots`  (lines 909–935)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: This method lists credential slots that members can fill, showing only whether each has a value. It never returns the secret value.

**Data flow**: It reads filled slot names from the credential table, compares them with declared member-fillable slots, and returns CredentialSlotView objects.

**Call relations**: The web credentials page calls it to show what credentials a member can add or rotate.

*Call graph*: called by 1 (credentials); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 937–978)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: This method lists live source bindings visible to a member. Sources are external data feeds, and the method hides private sources the member should not see.

**Data flow**: It receives a member id and admin flag. It queries non-removed sources, filters to shared or owned sources for non-admins, and returns SourceView objects.

**Call relations**: The web sources page calls it for the source list.

*Call graph*: called by 1 (sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 980–996)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: This method lists surface installations for the workspace and the agent each one is bound to. It supports workspace administration views.

**Data flow**: It reads surface_installation rows for the workspace, ordered by surface, and returns InstallationSummary objects.

**Call relations**: The web admin index calls it when displaying configured surfaces.

*Call graph*: called by 1 (admin_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 998–1047)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: This method lists recent workspace conversations across all surfaces. It is mainly for debugging and inspection.

**Data flow**: It receives an optional limit, computes turn counts and last activity, joins member email data, and returns ConversationSummary objects ordered by newest activity.

**Call relations**: The debugger surface calls it for the conversation browser.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 1049–1065)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: This method lists recent turns in one conversation in normal oldest-to-newest order. It gives debug views the durable record of each turn.

**Data flow**: It receives a conversation id and limit. It queries the newest rows up to the limit, reverses them into admission order, and converts each row to a Turn record.

**Call relations**: The debugger conversation-turns route calls it. It relies on _turn_query and _turn_record.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 1 (conversation_turns); 1 external calls (workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 1067–1117)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: This method returns a detailed view of one turn, including accounting rows and child turns spawned by subagents. It is useful for debugging how a turn behaved and what it cost.

**Data flow**: It receives a turn id, reads the main turn, its child turns, and its ledger rows, then returns a TurnDetail or None.

**Call relations**: Debugger and web stream/detail views call it. It uses _turn_query, _turn_record, and LedgerEntry construction.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (stream, turn, stream); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 1119–1130)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: This method reads the saved transcript for a conversation. It first checks ownership so a workspace cannot read another workspace’s blob data.

**Data flow**: It receives a conversation id. If the conversation belongs to this workspace, it loads the transcript blob and decodes it; missing or foreign data returns None.

**Call relations**: Debugger and web transcript routes call it. It depends on _owned_conversation before touching the blob store.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_transcript, transcript); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 1132–1143)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: This method lists saved transcript compaction record numbers for a conversation. Compaction records describe how old transcript parts were summarized.

**Data flow**: It receives a conversation id, verifies ownership, lists matching blob keys, extracts numeric indices, and returns them sorted.

**Call relations**: The debugger compactions route calls it, and read_compaction can then fetch a chosen record.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_compactions).


##### `SurfaceContext.read_compaction`  (lines 1145–1151)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: This method reads one saved compaction record for a conversation. It returns nothing if the conversation is not in this workspace or the record is missing.

**Data flow**: It receives a conversation id and compaction index. After an ownership check, it asks the transcript module to read the record from blob storage.

**Call relations**: The debugger compaction-record route calls it after list_compactions has shown available indices.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (compaction_record); 1 external calls (read_compaction_record).


##### `SurfaceContext.list_workspace_files`  (lines 1153–1159)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: This method lists files currently present in a conversation’s sandbox workspace. It is a safe way for debug tools to inspect the agent’s working folder.

**Data flow**: It receives a conversation id, verifies ownership, and asks the sandbox system for workspace file entries. Foreign conversations return an empty list.

**Call relations**: The debugger workspace-files route calls it. It uses _owned_conversation for the safety check.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_files).


##### `SurfaceContext.read_workspace_file`  (lines 1161–1170)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: This method streams one file out of a conversation’s sandbox workspace. It returns nothing if the file or conversation is not accessible.

**Data flow**: It receives a conversation id and relative file path. After checking ownership, it asks the sandbox to stream the file bytes.

**Call relations**: The debugger workspace-file route calls it. It shares the same ownership guard as the other sandbox read methods.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_file).


##### `SurfaceContext.installation`  (lines 1172–1185)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: This method reads the current workspace’s installation id for another surface. It helps views build links or metadata for conversations that live on a different surface.

**Data flow**: It receives a peer surface name, queries the installation table for this workspace, and returns the installation id or None.

**Call relations**: The debugger workspace metadata route calls it when displaying surface-related context.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 1187–1197)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: This private helper checks whether a conversation belongs to the current workspace. It is a guard before reading unscoped storage like blobs or live sandbox files.

**Data flow**: It receives a conversation id, queries the conversation table for this workspace, and returns true if a row exists.

**Call relations**: Transcript, compaction, and workspace-file read methods call it before reading outside the database.

*Call graph*: called by 5 (list_compactions, list_workspace_files, read_compaction, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 1199–1217)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: This private helper builds the standard select query for turn rows. It keeps turn listing and detail views using the same set of fields.

**Data flow**: It takes no input and returns a SQL select object containing the durable turn columns needed to reconstruct a Turn record.

**Call relations**: SurfaceContext.list_turns and SurfaceContext.turn_detail use it before converting rows with _turn_record.

*Call graph*: called by 2 (list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 1219–1237)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: This private helper converts a database row into a typed Turn object. It also parses stored context and terminal data back into structured records.

**Data flow**: It receives a SQL row, copies scalar fields, validates JSON-like context and terminal values when present, and returns a Turn.

**Call relations**: SurfaceContext.list_turns and SurfaceContext.turn_detail use it for every turn row they return.

*Call graph*: called by 2 (list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.bind`  (lines 1258–1263)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: This method lets a tool bind an installation only for surfaces it declared in its manifest. That prevents a tool from registering arbitrary surface names.

**Data flow**: It receives a surface name and installation id. It checks the declared set, reads the current workspace, and calls the shared installation binding helper.

**Call relations**: Tool code uses this registry-style access. It raises UndeclaredSurface before writing if the surface was not declared.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 1275–1285)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: This method resolves a shared surface installation id to the workspace that owns it. It is used before a request is bound to any workspace.

**Data flow**: It receives an installation id, queries the owner-level installation table for this surface, and returns the workspace id or None.

**Call relations**: Slack workspace resolution calls it during request authentication.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 1287–1299)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: This method opens a sealed credential handoff before the workspace has been resolved. It is useful for OAuth callbacks carrying sealed state.

**Data flow**: It receives a sealed string. If a credential store exists and the seal is valid, it returns the request state; otherwise it returns None.

**Call relations**: Slack resolve_workspace calls it while handling pre-binding callback flows.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 1301–1317)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: This method lets a shared resolver read a declared credential for a specific workspace. It checks both the declared slot and that the workspace exists.

**Data flow**: It receives a workspace id and slot name. It rejects undeclared slots, enters that workspace context, verifies the workspace row, and returns the credential value.

**Call relations**: Slack authentication uses it to read the signing secret before the request is fully handed to a SurfaceContext.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceDeliveryError.__init__`  (lines 1343–1347)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: This error records a delivery failure from an external provider and may include a provider-requested retry delay. It lets the poller respect rate limits.

**Data flow**: It receives an error message and optional retry-after seconds. It rejects negative delays, stores the retry value, and initializes the runtime error.

**Call relations**: Slack posting code raises it, and WritebackPoller._fail_or_retry reads it to decide when the next attempt should happen.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 1389–1405)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database condition for writebacks that are ready to be tried. It includes terminal turns that are pending or whose claim has expired.

**Data flow**: It receives the current time and returns a SQL boolean expression combining turn status and writeback claim timing.

**Call relations**: writeback_workspaces.due uses it to find workspaces with work, and WritebackPoller._claim uses it to claim rows inside one workspace.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `writeback_workspaces`  (lines 1408–1443)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: This function creates a rotating reader that finds workspaces with deliverable writebacks. It keeps the background poller from scanning everything at once.

**Data flow**: It initializes a cursor, defines a due-workspace query, wraps it in owner_candidates, and returns an async candidates function.

**Call relations**: WritebackPoller receives the returned candidates function and calls it from run or drain to decide which workspaces to process.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 1415–1429)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested helper builds one page of workspace ids that currently have due writebacks. It uses the rotating cursor to continue after the last page.

**Data flow**: It reads the current time, selects workspace ids from due writeback rows, groups and orders them, applies the batch limit, and optionally filters after the cursor.

**Call relations**: owner_candidates calls it when writeback_workspaces.candidates needs fresh workspace ids.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 1433–1441)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This nested async function returns the next batch of workspace ids with writeback work. If it reaches the end, it wraps around to the beginning.

**Data flow**: It calls the owner-level due reader, resets the cursor if needed, stores the last returned id as the new cursor, and returns the workspace ids.

**Call relations**: WritebackPoller.run and WritebackPoller.drain use it through the poller’s candidates field.


##### `_WritebackDeliveryFailed.__init__`  (lines 1451–1454)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: This private error wraps a failure that happened during either posting the reply or attaching files. It preserves which phase failed.

**Data flow**: It receives the phase name and original exception, stores both, and uses the exception text as the message.

**Call relations**: WritebackPoller._deliver_claimed raises it around surface post and attach calls. WritebackPoller._deliver catches it and decides whether to retry or fail.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 1477–1510)

```
async def run(self) -> None
```

**Purpose**: This is the continuous background loop for durable reply delivery. It repeatedly finds workspaces with due writebacks and starts bounded drain tasks for them.

**Data flow**: It creates a concurrency semaphore and an in-flight task map. On each loop it logs completed task errors, asks for more candidate workspaces when capacity allows, starts drain tasks, then sleeps briefly.

**Call relations**: The application’s background worker runs this method. It hands individual workspace work to _drain_workspace and cancels outstanding tasks during shutdown.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 1512–1521)

```
async def drain(self) -> None
```

**Purpose**: This method performs one finite drain pass instead of running forever. It is useful for tests, maintenance commands, or one-shot background work.

**Data flow**: It asks for candidate workspaces, drains them concurrently under the same limit as run, gathers results, and raises an ExceptionGroup if any workspace drain failed.

**Call relations**: It calls _drain_workspace for each candidate workspace, just like run does, but returns after that batch.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 1523–1541)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: This private method processes one workspace’s due writeback rows. It claims rows, keeps their leases alive, and delivers them one by one.

**Data flow**: It receives a workspace id and semaphore. Inside that workspace context, it claims rows, starts renewal tasks for each claim, delivers each row, then cancels renewals.

**Call relations**: WritebackPoller.run and drain call it. It coordinates _claim, _renew_claim, and _deliver.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 1543–1576)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: This private method claims a batch of due writeback rows for this worker. Claiming is like putting a temporary name tag on work so another worker does not do it at the same time.

**Data flow**: It receives a workspace id, finds due rows, updates them to claimed with this worker id and an expiry time, and returns their turn ids, reply refs, and last errors.

**Call relations**: _drain_workspace calls it before any external delivery starts. It uses _writeback_due to decide which rows are eligible.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 1578–1610)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This private method wraps delivery with logging and failure handling. It turns delivery exceptions into retry, failure, or claim-lost outcomes.

**Data flow**: It receives workspace id, turn id, existing reply reference, and renewal task. It times the attempt, calls _deliver_with_lease, logs success, or calls _fail_or_retry on delivery failure.

**Call relations**: _drain_workspace calls it for each claimed row. It delegates the actual surface calls downward and records outcome through _fail_or_retry.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 1612–1641)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This private method delivers a claimed writeback while making sure the claim stays valid. It stops the renewal task before marking the row delivered to avoid fighting its own refresher.

**Data flow**: It receives workspace id, turn id, reply ref, and renewal task. It runs actual delivery and renewal side by side, reacts if renewal fails first, then marks the writeback delivered after delivery succeeds.

**Call relations**: _deliver calls it. It calls _deliver_claimed for the external work and _mark_delivered for the final database state change.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 1643–1665)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: This private method performs the actual two-phase durable surface delivery. It posts the reply if needed, records the provider’s reply reference, then attaches files.

**Data flow**: It receives workspace id, turn id, and optional existing reply ref. It builds the Writeback, finds the surface spec, creates a SurfaceContext, calls post when no ref exists, records the ref, then calls attach.

**Call relations**: _deliver_with_lease calls it. It hands off to extension-provided surface post and attach functions, wrapping failures as _WritebackDeliveryFailed.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 1667–1670)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: This private loop keeps a claimed writeback from expiring while a slow external delivery is in progress. It is a lease refresher.

**Data flow**: It receives a turn id, sleeps for the refresh interval, and repeatedly calls _refresh_claim.

**Call relations**: _drain_workspace starts one renewal task per claimed row. _deliver_with_lease watches the task while delivery runs.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 1672–1688)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: This private method extends this worker’s claim on a writeback row. If the row no longer belongs to this worker, it reports that the claim was lost.

**Data flow**: It receives a turn id, updates the claim expiry only when status and worker id still match, and raises _WritebackClaimLost if exactly one row was not updated.

**Call relations**: _renew_claim calls it on a timer. _deliver_with_lease treats failure here as a reason to stop delivery progress.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 1690–1746)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: This private method builds the Writeback object that a surface knows how to render. It gathers the terminal answer, metadata, and shared artifacts for one turn.

**Data flow**: It receives a turn id, reads the turn’s terminal frame and conversation surface/key, reads artifact rows, validates the terminal data, and returns the Writeback plus surface name.

**Call relations**: _deliver_claimed calls it before invoking a surface’s post or attach functions.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 1748–1760)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: This private method saves the external provider’s reply reference after the post phase succeeds. That lets recovery attach files later without posting the same reply again in most cases.

**Data flow**: It receives a turn id and reply reference, updates the claimed writeback row for this worker, and raises claim-lost if the row no longer matches.

**Call relations**: _deliver_claimed calls it between post and attach.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 1762–1779)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: This private method marks a writeback as fully delivered. It clears the worker claim and expiry so the row is no longer retried.

**Data flow**: It receives a turn id, updates the claimed row to delivered for this worker, clears claim fields, and raises claim-lost if another worker or state change intervened.

**Call relations**: _deliver_with_lease calls it after _deliver_claimed finishes successfully and renewal has been stopped.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 1781–1830)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: This private method records a failed delivery attempt and decides whether to retry later or give up. It respects provider retry advice when available but never lets a row wait forever.

**Data flow**: It receives a turn id and wrapped delivery error. It computes the retry time or terminal failure based on age, stores a shortened error message, clears the claim, and returns the outcome, error text, and next attempt time.

**Call relations**: WritebackPoller._deliver calls it after post or attach failure. Future runs of _claim will pick up rows returned to pending once their retry time arrives.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).
