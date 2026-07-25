# Surface routing and inbound event handling  `stage-6`

This stage is the system’s front door. It sits between people or external services and UFO’s core conversation engine during normal use. Each surface receives events in its own format, checks who is allowed in, and translates the event into a UFO conversation or message.

The core surface bridge is the common adapter. It helps surfaces identify users, create conversations, accept new messages, stream partial replies, fetch needed credentials, and deliver final replies back to durable places such as Slack. The web surface provides the browser chat page, live streamed answers, and recent workspace spending. The Slack surface verifies Slack requests, turns messages and button clicks into UFO turns, and sends responses, status updates, and files back to Slack. The terminal surface serves the `ufo` command-line client by turning conversation activity into simple text instructions a shell script can display. The debugger surface gives authorized operators read-only views into conversations, transcripts, files, and live events. The memory surface similarly gives operators a read-only page and API for inspecting stored workspace memories.

## Files in this stage

### Debugger inspection surface
Operator-facing debugger routes expose read-only workspace diagnostics, transcripts, files, and live turn events.

### `extensions/debugger/ufo_ext_debugger/surface.py`

`io_transport` · `request handling`

This file is the bridge between the debugger web app in the browser and the workspace data it is allowed to inspect. Think of it like a read-only control room window: the operator can look into one workspace’s sessions, but this file does not decide who is allowed in. That authorization has already happened before these handlers run, and the resulting SurfaceContext is already tied to one workspace.

The file serves a built React page from static/index.html. Once that page loads, it asks the api/ routes here for data. Those routes return plain JSON views of conversations, turns, transcripts, compaction records, and workspace files. Most routes follow the same pattern: read an ID from the URL, reject it with a 404 response if it is missing or invalid, ask SurfaceContext for the workspace-scoped data, then return that data in a browser-friendly form.

One route is slightly different: the live turn stream. It uses Server-Sent Events, a simple web streaming format where the server keeps sending named events over one HTTP response. This lets the debugger watch a turn unfold live, including text updates, tool calls, cost ticks, and terminal frames. The stream includes event IDs so the browser can resume after a dropped connection.

#### Function details

##### `app_page`  (lines 40–45)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the debugger’s main web page to the browser. If the frontend has not been built yet, it raises a clear error telling the developer what build step is missing.

**Data flow**: It receives the already-scoped surface context and the incoming request. It checks whether the prebuilt HTML file was loaded when the module started; if so, it wraps that HTML in an HTTP response. If the file is missing, nothing useful can be shown, so it stops with a runtime error.

**Call relations**: This is the GET handler for the surface root. It is the first page a browser receives, and after it returns the HTML page, that page calls the JSON and stream routes in this same file.

*Call graph*: 1 external calls (HTMLResponse).


##### `workspace_meta`  (lines 48–55)

```
async def workspace_meta(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns basic information about the workspace currently being inspected. It also translates a stored Slack installation marker into a plain Slack team ID when one is available.

**Data flow**: It reads the workspace ID from SurfaceContext and asks the context for the Slack installation value. If that value looks like a Slack team marker, it strips the internal prefix and returns the clean team ID; otherwise it returns null for the Slack team. The output is a JSON object for the browser.

**Call relations**: The debugger frontend calls this route when it needs to label the current workspace. It depends on SurfaceContext.installation for the workspace-scoped installation lookup, then hands the result back through JSONResponse.

*Call graph*: calls 1 internal fn (installation); 1 external calls (JSONResponse).


##### `conversations`  (lines 58–60)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of conversations visible in the current workspace. This gives the debugger page its top-level set of sessions to browse.

**Data flow**: It asks SurfaceContext for the workspace’s conversation list. Each conversation entry is converted into JSON-safe data, then all entries are returned as a JSON array.

**Call relations**: This route is called by the debugger frontend when it needs the conversation index. It delegates the actual workspace-scoped read to SurfaceContext.list_conversations and only formats the answer for HTTP.

*Call graph*: calls 1 internal fn (list_conversations); 1 external calls (JSONResponse).


##### `conversation_turns`  (lines 63–68)

```
async def conversation_turns(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the turns inside one conversation. A turn is one unit of interaction in a conversation, so this lets the debugger drill into the timeline.

**Data flow**: It reads conversation_id from the URL and tries to turn it into a valid UUID, which is a standard unique identifier. If that fails, it returns a 404 error. Otherwise it asks SurfaceContext for the turns in that conversation and returns them as JSON.

**Call relations**: The frontend calls this after the user selects a conversation. This function uses _uuid_param to validate the URL value, then relies on SurfaceContext.list_turns for the workspace-scoped data.

*Call graph*: calls 2 internal fn (list_turns, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_transcript`  (lines 71–78)

```
async def conversation_transcript(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the full transcript for one conversation, if a transcript exists. This gives the debugger a readable record of what happened in the session.

**Data flow**: It extracts and validates the conversation ID from the URL. If the ID is bad, it returns a 404 error. If the ID is valid, it asks SurfaceContext for the transcript; a missing transcript also becomes a 404 response. A found transcript is converted to JSON and returned.

**Call relations**: The frontend calls this when it needs the conversation’s message record. The helper _uuid_param protects the lookup from malformed IDs, and SurfaceContext.read_transcript provides the actual transcript data.

*Call graph*: calls 2 internal fn (read_transcript, _uuid_param); 1 external calls (JSONResponse).


##### `conversation_compactions`  (lines 81–85)

```
async def conversation_compactions(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of compaction records for a conversation. A compaction is when older conversation content is summarized or reduced, so this route helps an operator see where that happened.

**Data flow**: It validates the conversation ID from the URL. If the ID is invalid, it returns a 404 error. Otherwise it asks SurfaceContext for the compaction list, turns the result into a normal list, and returns it as JSON.

**Call relations**: The debugger frontend uses this when showing conversation history and summarization points. It shares the common ID-checking helper _uuid_param, then calls SurfaceContext.list_compactions for the workspace-scoped records.

*Call graph*: calls 2 internal fn (list_compactions, _uuid_param); 1 external calls (JSONResponse).


##### `compaction_record`  (lines 88–103)

```
async def compaction_record(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the details of one specific compaction record. It shows what messages existed before compaction, what remained after, and the summary that was created.

**Data flow**: It reads the conversation ID and compaction index from the URL. The conversation ID must be a valid UUID and the index must be digits only; otherwise it returns a 404 error. It then asks SurfaceContext for that compaction record. If found, it returns a JSON object containing the index, before messages, after messages, and summary.

**Call relations**: The frontend calls this after choosing a compaction entry to inspect. This function uses _uuid_param for safe conversation lookup, calls SurfaceContext.read_compaction for the record, and formats nested message objects into JSON.

*Call graph*: calls 2 internal fn (read_compaction, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_files`  (lines 106–111)

```
async def workspace_files(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files attached to or created within a conversation’s workspace area. This lets an operator see what files were available during that session.

**Data flow**: It validates the conversation ID from the URL. If invalid, it returns a 404 error. Otherwise it asks SurfaceContext for the file list for that conversation and returns each file entry as JSON.

**Call relations**: The debugger frontend calls this when it needs the file browser for a conversation. It uses _uuid_param to reject malformed IDs and SurfaceContext.list_workspace_files to read the scoped file list.

*Call graph*: calls 2 internal fn (list_workspace_files, _uuid_param); 1 external calls (JSONResponse).


##### `workspace_file`  (lines 114–124)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams the contents of one workspace file back to the browser. It is used when the debugger needs to download or display an individual file from a conversation.

**Data flow**: It validates the conversation ID and reads a file path from the URL. It asks SurfaceContext to open that file. If the ID is bad, the path is rejected, or no file exists, it returns a 404 error. If the file is found, it returns a streaming binary response instead of loading the whole file into memory at once.

**Call relations**: The frontend calls this after a user selects a specific file. The function uses _uuid_param for the conversation ID, SurfaceContext.read_workspace_file for safe file access, JSONResponse for failures, and StreamingResponse for successful file contents.

*Call graph*: calls 2 internal fn (read_workspace_file, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `turn`  (lines 127–134)

```
async def turn(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns detailed information about one turn. This is the route the debugger uses to inspect a single unit of work or interaction.

**Data flow**: It reads turn_id from the URL and converts it to a UUID. If that fails, it returns a 404 error. It then asks SurfaceContext for the turn detail; if no such turn exists, it returns 404. A found turn is converted into JSON and returned.

**Call relations**: The frontend calls this when opening a specific turn. This function relies on _uuid_param for ID validation and SurfaceContext.turn_detail for the workspace-scoped lookup.

*Call graph*: calls 2 internal fn (turn_detail, _uuid_param); 1 external calls (JSONResponse).


##### `stream`  (lines 137–142)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens a live event stream for one turn. This lets the debugger watch new frames arrive as the turn runs, rather than waiting for everything to finish.

**Data flow**: It validates the turn ID from the URL and confirms the turn exists. If not, it returns a 404 error. It also reads the Last-Event-ID request header, which tells the server where to resume if the browser previously disconnected. It returns a streaming response whose body is produced by _events.

**Call relations**: The frontend calls this when it wants live updates for a turn. This function checks the turn through SurfaceContext.turn_detail, then hands streaming work to _events and wraps it in StreamingResponse using the Server-Sent Events media type.

*Call graph*: calls 3 internal fn (turn_detail, _events, _uuid_param); 2 external calls (JSONResponse, StreamingResponse).


##### `_events`  (lines 145–147)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Turns the context’s live turn feed into bytes that can be sent over an HTTP stream. It is the small adapter between internal live frames and browser-readable Server-Sent Events.

**Data flow**: It receives the scoped context, a turn ID, and a resume cursor. It asks SurfaceContext.tail for live frames after that cursor. For each cursor-and-frame pair it receives, it passes the frame to _sse and yields the resulting bytes to the HTTP response.

**Call relations**: This helper is used only by stream. It sits in the middle of the live-update path: SurfaceContext.tail supplies raw live frames, _events loops over them, and _sse formats each one for the browser.

*Call graph*: calls 2 internal fn (tail, _sse); called by 1 (stream).


##### `_sse`  (lines 150–170)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Formats one live frame as one Server-Sent Event. It gives each frame a clear event name, such as text, tool, cost, or terminal, so the debugger can display it properly.

**Data flow**: It receives a cursor and a LiveFrame object. If the cursor is not empty, it writes it as the event ID so the browser can resume later. It then checks what kind of frame it is, serializes the frame to raw JSON, and returns the complete event as bytes. If it sees an unknown frame type, it raises an error instead of silently sending something misleading.

**Call relations**: _events calls this for every live frame it receives from SurfaceContext.tail. This function is the final formatting step before bytes leave the server through the streaming response.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_uuid_param`  (lines 173–177)

```
def _uuid_param(request: Request, name: str) -> UUID | None
```

**Purpose**: Safely reads a UUID value from the request path. It keeps route handlers from trying to use malformed IDs.

**Data flow**: It receives a request and the name of a path parameter. It reads that string from the request path and tries to convert it into a UUID object. If conversion works, it returns the UUID; if the string is not a valid UUID, it returns None.

**Call relations**: Most detail routes call this before looking up conversations or turns. By centralizing the check here, the route handlers can all follow the same pattern: invalid ID becomes None, and None becomes a simple 404 response.

*Call graph*: called by 8 (compaction_record, conversation_compactions, conversation_transcript, conversation_turns, stream, turn, workspace_file, workspace_files); 1 external calls (UUID).


### Conversation entry surfaces
Slack, terminal, and browser chat surfaces receive user input, translate it into UFO conversation activity, and return streamed or completed replies.

### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `installation, request handling, live turn status, reply delivery`

This file connects Slack to the core ufo system. Without it, Slack could not be installed, Slack messages could not safely enter ufo, and ufo answers could not appear back in the right Slack thread. It is both a security guard and a translator. First, it checks that incoming requests really came from Slack by validating Slack’s signature, like checking a sealed envelope before opening it. It then figures out which workspace the request belongs to, ignores bot messages and unrelated channel chatter, and admits only direct messages, mentions, or replies in threads where ufo is already participating. It can also fetch a small amount of earlier Slack context so a mid-thread mention makes sense to the agent. Attached Slack files are streamed into the workspace without loading the whole file into memory. While the agent works, this file updates Slack’s native thread status with messages like “Thinking…” or “Generating…”. When the turn finishes, it posts the final answer, renders questions as Slack buttons when possible, handles button clicks as follow-up turns, and uploads shared artifacts back to Slack. It also covers installation: either OAuth “Add to Slack” or a user-provided Slack app token.

#### Function details

##### `_env_signing_secret`  (lines 121–125)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from environment variables. This secret is used when a workspace does not have its own Slack signing secret stored.

**Data flow**: It reads the process environment → looks for the Slack signing secret name → returns the secret string, or nothing if it is unset.

**Call relations**: Workspace-level secret lookup falls back to this when checking incoming Slack requests before or during request handling.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 128–135)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use for the currently bound workspace. It prefers that workspace’s stored secret, then falls back to the deploy-wide secret.

**Data flow**: It receives a surface context → asks the credential store for the workspace Slack signing secret → returns it, or returns the environment secret if that slot is empty.

**Call relations**: The main Slack event route and interactive-button route call this before trusting any incoming request.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 138–146)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret for a workspace before the request has been fully bound to that workspace. This is needed while deciding whether an incoming Slack request is legitimate.

**Data flow**: It receives a shared authentication helper and workspace id → reads the workspace credential if possible → falls back to the environment secret → returns a secret or nothing.

**Call relations**: Workspace resolution calls this after using Slack’s team id to find a possible workspace, then verifies the raw request before binding it.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 149–153)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client id from the deploy environment. OAuth cannot start or finish without this id.

**Data flow**: It checks the environment → returns the configured client id → raises an error if missing.

**Call relations**: The OAuth code-exchange step uses this when asking Slack to turn an authorization code into a bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 156–160)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret from the deploy environment. This proves to Slack that this deploy owns the Slack app during installation.

**Data flow**: It checks the environment → returns the configured client secret → raises an error if missing.

**Call relations**: The OAuth exchange uses it together with the client id and returned code.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 163–165)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should redirect to after a user approves installation.

**Data flow**: It receives the public base URL of the deploy → trims any trailing slash → returns the fixed Slack OAuth callback path.

**Call relations**: The OAuth callback uses this same URL when exchanging Slack’s code, matching the URL that was used in the install link.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 168–180)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the “Add to Slack” link shown to an owner. The link includes the requested Slack permissions and a sealed state value that ties the install back to the right workspace.

**Data flow**: It receives a client id, redirect URI, and sealed state → URL-encodes those plus the Slack scopes → returns a Slack authorization URL.

**Call relations**: This is used by the Slack connection flow outside the main event path to start OAuth installation.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 184–186)

```
def __init__(self, error: str)
```

**Purpose**: Creates a clear error object for Slack identity problems, such as bad OAuth responses or invalid bot tokens.

**Data flow**: It receives an error message → stores it on the exception → initializes the normal runtime error text.

**Call relations**: Identity proof and OAuth exchange raise this when Slack cannot prove the team and bot-user identity.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `identity_blob_key`  (lines 200–201)

```
def identity_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage key where this workspace’s Slack identity record is saved.

**Data flow**: It receives a workspace id → formats a stable blob-store path → returns that path.

**Call relations**: Identity reading, OAuth installation, and manifest-token identity proof all use this key so they agree on where the record lives.

*Call graph*: called by 3 (resolve, oauth_callback, read_identity).


##### `bot_token_fingerprint`  (lines 204–205)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack bot token. This lets the code tell whether an identity record belongs to the current token without storing or comparing the token itself.

**Data flow**: It receives a bot token → hashes it with SHA-256 → returns the hex fingerprint.

**Call relations**: Identity records are written and checked with this fingerprint during OAuth install, manifest proof, and identity reads.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 208–222)

```
async def read_identity(blob: BlobStore, workspace_id: UUID, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the saved Slack team and bot-user identity for a workspace, but only if it matches the current bot token.

**Data flow**: It receives a blob store, workspace id, and bot token → loads and validates the identity blob → compares its token fingerprint → returns the identity or nothing.

**Call relations**: Both the normal request path and the manifest-app resolver use this to avoid trusting stale identity data after a token rotation.

*Call graph*: calls 4 internal fn (exists, get, bot_token_fingerprint, identity_blob_key); called by 2 (resolve, _identity).


##### `_identity`  (lines 225–230)

```
async def _identity(ctx: SurfaceContext) -> SlackIdentity | None
```

**Purpose**: Gets the current workspace’s Slack identity, if Slack has been connected and proven.

**Data flow**: It receives the current surface context → reads the Slack bot token credential → reads the matching identity blob → returns the identity or nothing.

**Call relations**: Incoming events and interactive clicks call this after signature verification so they can recognize the right Slack team and bot user.

*Call graph*: calls 2 internal fn (credential, read_identity); called by 2 (ingest, interactive).


##### `SlackIdentityResolver.resolve`  (lines 244–252)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Proves and saves the Slack identity for a bring-your-own Slack app token. It avoids repeating the proof if a matching identity is already stored.

**Data flow**: It checks the identity blob → if valid, returns it → otherwise calls Slack to prove the token → writes the resulting identity → returns it.

**Call relations**: Background identity proof and setup flows use this when OAuth did not already provide the team and bot-user ids.

*Call graph*: calls 3 internal fn (_prove, identity_blob_key, read_identity).


##### `SlackIdentityResolver._prove`  (lines 254–279)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack’s auth.test API which team and bot user a bot token belongs to. This is the proof step for manually configured Slack apps.

**Data flow**: It sends the bot token to Slack → checks the HTTP and JSON response → validates team and user id shapes → returns a SlackIdentity.

**Call relations**: The resolver calls this only when no matching identity record already exists.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 285–298)

```
def _prove_identity_in_background(ctx: SurfaceContext) -> None
```

**Purpose**: Starts identity proof in the background when a workspace has credentials but no saved identity yet. This lets a later Slack retry succeed without blocking too long now.

**Data flow**: It receives a context → checks whether a proof task is already running for the workspace → creates one if needed → records it until completion.

**Call relations**: Event and interactive routes call this when identity is missing after request verification.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 2 (ingest, interactive); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 294–296)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished background identity-proof task from the in-process tracking table.

**Data flow**: It receives the completed task → checks it is still the tracked task for that workspace → deletes the tracking entry.

**Call relations**: It runs automatically as the cleanup callback for tasks created by _prove_identity_in_background.


##### `_run_identity_proof`  (lines 301–308)

```
async def _run_identity_proof(ctx: SurfaceContext) -> None
```

**Purpose**: Performs the background identity proof and logs failures instead of failing the Slack request that triggered it.

**Data flow**: It reads the bot token from credentials → runs SlackIdentityResolver → writes identity through the resolver → logs any identity or unexpected error.

**Call relations**: _prove_identity_in_background schedules this as an asynchronous task.

*Call graph*: calls 1 internal fn (credential); called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `url_verified_blob_key`  (lines 311–317)

```
def url_verified_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage key for the marker saying Slack successfully reached this deploy with a verified request.

**Data flow**: It receives a workspace id → formats the url-verified blob path → returns that path.

**Call relations**: _mark_url_verified uses it after signed Slack requests, especially to show manual Slack app setup as connected.

*Call graph*: called by 1 (_mark_url_verified).


##### `signing_secret_fingerprint`  (lines 320–323)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack signing secret. This detects secret rotation without saving the secret itself in the marker.

**Data flow**: It receives the signing secret → hashes it with SHA-256 → returns the hex fingerprint.

**Call relations**: _mark_url_verified stores this fingerprint so later setup checks know whether the current secret has really been verified.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 337–364)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s temporary OAuth code for a workspace bot token and identity information.

**Data flow**: It receives Slack’s code and redirect URI → posts them to Slack with the deploy client credentials → validates Slack’s response → returns bot token, team id, and bot-user id.

**Call relations**: oauth_callback calls this after the owner approves the Add to Slack flow.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `SlackConversationSearch.run`  (lines 443–457)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations by channel text or DM participants. This helps the agent find where a user wants to send or inspect something.

**Data flow**: It normalizes the query → lists conversations → resolves people for DMs and group DMs → filters conversations whose searchable text contains the query → returns matches plus a truncation flag.

**Call relations**: It coordinates the smaller listing, member-resolution, and conversation-shaping helpers inside SlackConversationSearch.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 459–478)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches pages of Slack conversations up to a fixed limit so a search cannot run forever.

**Data flow**: It starts with an empty cursor → requests Slack conversation pages → gathers channel objects → stops at no cursor or page limit → returns listed items and whether more pages existed.

**Call relations**: run calls this first, before resolving people and filtering matches.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 480–488)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list request.

**Data flow**: It receives a cursor → creates parameters for conversation types, archived filtering, and page size → adds the cursor if present → returns the parameter dictionary.

**Call relations**: _list calls this for each page request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 490–493)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a conversations.list response.

**Data flow**: It receives the parsed response → looks inside response metadata → returns the cursor string or an empty string.

**Call relations**: _list uses this after each page to decide whether to continue.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 495–523)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves the human labels for DM and group-DM participants so DMs can be searched by who is in them.

**Data flow**: It receives the listed conversations → collects member ids for DMs within a cap → looks up each user once → builds display labels → returns labels by conversation plus whether the cap was hit.

**Call relations**: run calls this after listing conversations; it uses _members, _kind, _slack_user, and _label.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 525–532)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as a public channel, private channel, group DM, or one-to-one DM.

**Data flow**: It reads Slack’s boolean fields from the raw object → chooses the matching kind → returns that kind string.

**Call relations**: Conversation conversion, member lookup, and people resolution all use this shared classification.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 534–548)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets member ids for a DM-like conversation. A one-to-one DM carries its user directly; a group DM needs a Slack API call.

**Data flow**: It receives a raw conversation and id → for one-to-one DM returns the embedded user → otherwise asks Slack for members → returns a tuple of user ids.

**Call relations**: _people calls this for each DM or group DM it chooses to resolve.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 550–555)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Turns a Slack user record into a readable search label.

**Data flow**: It receives an optional SlackUser and fallback user id → combines name and email when available → otherwise returns the best available value.

**Call relations**: _people uses this after calling _slack_user so search results show recognizable people.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 557–574)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts Slack’s raw conversation object into the smaller, safer SlackConversation model used by search.

**Data flow**: It checks the raw object and id → extracts name, purpose, topic, membership, kind, and people labels → returns a SlackConversation or nothing if unusable.

**Call relations**: run calls this for each listed raw conversation before applying the text query.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 576–578)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely extracts the text value from Slack’s nested purpose or topic fields.

**Data flow**: It receives a field object → if it is a dictionary, reads its value → returns that string or an empty string.

**Call relations**: _conversation uses it to avoid trusting Slack’s raw shape blindly.

*Call graph*: called by 1 (_conversation).


##### `verify_slack_signature`  (lines 685–700)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that an incoming request was signed by Slack with the expected secret and is recent enough to prevent replay attacks.

**Data flow**: It receives headers, raw body, signing secret, and optional clock time → validates timestamp and signature → returns nothing on success or raises an error on failure.

**Call relations**: Workspace resolution, event ingest, and interactive ingest all call this before accepting a Slack request.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 707–726)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw incoming Slack request body while enforcing a maximum size.

**Data flow**: It receives a request → reuses a cached body if present → otherwise streams chunks, counts bytes, rejects over-limit bodies, caches the bytes → returns the raw body.

**Call relations**: All Slack HTTP routes use this because signature verification must use the exact original bytes.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 729–738)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack’s URL verification handshake and extracts the challenge Slack expects back.

**Data flow**: It receives raw request bytes → parses JSON → checks for Slack’s url_verification type → returns the challenge string or nothing.

**Call relations**: Workspace resolution and ingest use this so Slack can verify an endpoint even before normal event handling.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 741–758)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Extracts a Slack team id from an incoming event or interactive payload before the workspace is bound.

**Data flow**: It receives raw request bytes → parses JSON or form-encoded interactive payload → finds and validates a team id → returns it or nothing.

**Call relations**: resolve_workspace uses this team id to look up which ufo workspace owns the Slack installation.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 761–762)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Builds the stable installation key used to bind a Slack team to a ufo workspace.

**Data flow**: It receives a Slack team id → prefixes it with a type marker → returns the installation id string.

**Call relations**: OAuth install writes this binding, and workspace resolution reads it for incoming Slack traffic.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 765–803)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace an incoming Slack request belongs to, or rejects it safely. This happens before the normal route gets a workspace context.

**Data flow**: It inspects the request → for OAuth callbacks, opens the sealed state → for Slack posts, reads the raw body, handles URL verification, extracts team id, looks up workspace, verifies signature → returns a workspace id, a challenge response, or nothing.

**Call relations**: This is the front gate for all Slack routes in a shared deployment with many workspaces.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 806–809)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state belongs to Slack OAuth installation.

**Data flow**: It receives decoded state claims → checks the payload marker and requested credential slot → returns true or false.

**Call relations**: OAuth callback and pre-route workspace resolution use it to avoid accepting unrelated sealed states.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 812–817)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the ufo conversation key for a Slack message. DMs are keyed by channel; channel conversations are keyed by channel plus thread root.

**Data flow**: It receives channel id, root timestamp, and whether this is a DM → returns either the channel id or channel:root timestamp.

**Call relations**: _to_inbound uses this to put all replies in the same Slack thread into the same ufo conversation.

*Call graph*: called by 1 (_to_inbound).


##### `slack_message_addressed`  (lines 820–827)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly asking ufo to respond.

**Data flow**: It receives the event, bot-user id, and DM flag → treats DMs and app_mention events as addressed → otherwise searches the text for the bot mention → returns true or false.

**Call relations**: _to_inbound uses this to ignore ordinary channel messages unless they are in an already active ufo thread.

*Call graph*: called by 1 (_to_inbound).


##### `slack_reply_body`  (lines 830–880)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool=False) -> bytes
```

**Purpose**: Builds the JSON body for posting a Slack reply, including markdown blocks, buttons, and optional metadata when they fit Slack’s size limits.

**Data flow**: It receives channel, thread, text, metadata, and optional action blocks → builds a block-based Slack message when safe → falls back to plain text if needed → returns encoded JSON bytes or raises if too large.

**Call relations**: post uses this before calling Slack’s chat.postMessage API.

*Call graph*: called by 1 (post); 1 external calls (dumps).


##### `_mrkdwn_section`  (lines 883–884)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates one Slack markdown section block from text, trimmed to Slack’s section limit.

**Data flow**: It receives text → cuts it to the maximum allowed section size → returns a Block Kit section dictionary.

**Call relations**: slack_ask_blocks uses this while rendering questions for Slack.

*Call graph*: called by 1 (slack_ask_blocks).


##### `slack_ask_blocks`  (lines 887–939)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders a ufo question into Slack Block Kit blocks. Simple single-choice questions become buttons; richer questions become text instructions.

**Data flow**: It receives an AskUserInput or nothing → builds title, question sections, option text, and button rows when allowed → returns Slack blocks or nothing.

**Call relations**: post includes these blocks when a finished turn is asking the user for an answer.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (post).


##### `slack_connect_blocks`  (lines 942–963)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a private connection handoff as a Slack button. This lets a member open an authorization flow without putting secrets in chat.

**Data flow**: It receives a connect request and turn id → if no request, returns nothing → otherwise returns an actions block with a connect button carrying the turn id.

**Call relations**: post uses this when a turn ends by asking the user to connect an external provider.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 966–970)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Safely reads a required non-empty string field from a Slack payload.

**Data flow**: It receives a mapping and field name → checks that the value is a non-empty string → returns it or raises a clear error.

**Call relations**: _to_inbound and _to_click use this when converting untrusted Slack payloads into internal objects.

*Call graph*: called by 2 (_to_click, _to_inbound).


##### `_inbound_files`  (lines 973–985)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable file references from a Slack message event, ignoring hidden or unusable entries.

**Data flow**: It reads the event’s files list → checks up to the configured limit → keeps entries with a name and private download URL → returns InboundFile objects.

**Call relations**: _to_inbound includes these files so ingest can stream them into the workspace before admitting the turn.

*Call graph*: called by 1 (_to_inbound); 1 external calls (__init__).


##### `oauth_callback`  (lines 988–1032)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the Slack “Add to Slack” installation after the owner approves it in Slack.

**Data flow**: It reads query parameters → validates the sealed state and workspace → exchanges Slack’s code for a bot token → binds the Slack team to the workspace → stores the credential and identity → returns an HTML success or error page.

**Call relations**: This is the OAuth install landing route; later event handling depends on the token, team binding, and identity it writes.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _install_page, _is_install_state, bot_token_fingerprint, identity_blob_key, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 1 external calls (__init__).


##### `_install_page`  (lines 1035–1042)

```
def _install_page(message: str, status: int) -> Response
```

**Purpose**: Builds a small HTML page shown to the installer after OAuth succeeds or fails.

**Data flow**: It receives a message and HTTP status → escapes the message for safety → returns an HTML response.

**Call relations**: oauth_callback uses it for every visible install outcome.

*Call graph*: called by 1 (oauth_callback); 2 external calls (escape, Response).


##### `_mark_url_verified`  (lines 1048–1062)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully contacted this workspace using the current signing secret.

**Data flow**: It fingerprints the signing secret → skips work if this process already wrote that fingerprint → writes a marker blob with fingerprint and time → updates the in-memory cache.

**Call relations**: ingest and interactive call it after verified requests so setup tools can tell whether Slack is truly connected.

*Call graph*: calls 2 internal fn (signing_secret_fingerprint, url_verified_blob_key); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1065–1127)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack event callbacks: verifies them, filters them, downloads attachments, and admits valid messages as ufo turns.

**Data flow**: It reads the raw body → checks the signing secret and Slack signature → handles URL verification → loads identity → converts the event to an inbound message → fetches sender and context → resolves member and conversation → downloads files → admits a turn → starts live status tracking → returns Slack an ok response.

**Call relations**: This is the main Slack message intake route and the start of most Slack-originated conversations.

*Call graph*: calls 19 internal fn (admit, conversation_for, credential, default_agent, _ambient_context, _ctx_signing_secret, _download_files, _files_note, _identity, _mark_url_verified (+9 more)); 4 external calls (gather, loads, JSONResponse, Response).


##### `_author_is_foreign`  (lines 1130–1137)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages written by users from another Slack organization in shared Slack Connect channels.

**Data flow**: It reads source_team or user_team from the event → compares it with the installed team id → returns true if the author is external.

**Call relations**: _to_inbound uses this to skip external shared-channel users before trying to resolve them as workspace members.

*Call graph*: called by 1 (_to_inbound).


##### `_to_inbound`  (lines 1140–1175)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Turns a raw Slack event into the smaller Inbound object that ufo can admit, or decides to ignore it.

**Data flow**: It checks event type, subtype, author, bot/self messages, foreign authors, DM status, addressing, thread key, and files → may look up an existing participating conversation → returns Inbound or nothing.

**Call relations**: ingest calls this after signature and identity checks to decide whether a Slack event should become a ufo turn.

*Call graph*: calls 6 internal fn (_author_is_foreign, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 1 external calls (__init__).


##### `_participating_conversation`  (lines 1178–1189)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether ufo is already active in a Slack thread by requiring an existing conversation with at least one admitted turn.

**Data flow**: It receives a queue key → looks up the conversation → checks whether it has a latest turn → returns the conversation id only if both exist.

**Call relations**: _to_inbound uses this so unmentioned thread replies are accepted only after ufo has truly joined the thread.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1192–1222)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches basic Slack user details, such as display name, confirmed email, and timezone, on a best-effort basis.

**Data flow**: It receives a bot token and Slack user id → calls users.info → validates the response → returns a SlackUser or nothing if lookup fails.

**Call relations**: ingest uses it for sender context and member resolution; interactive uses it for unlinked button clickers; conversation search uses it for DM labels.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_people, ingest, interactive); 2 external calls (__init__, AsyncClient).


##### `_turn_context`  (lines 1225–1239)

```
def _turn_context(sender: SlackUser | None) -> TurnContext
```

**Purpose**: Builds the sender context attached to an admitted ufo turn. This helps the agent know who spoke and, if valid, their timezone.

**Data flow**: It receives a SlackUser or nothing → formats name/email → constructs TurnContext → drops invalid timezone values with a warning → returns the context.

**Call relations**: ingest passes this context into ctx.admit when creating a turn.

*Call graph*: called by 1 (ingest); 1 external calls (__init__).


##### `_resolve_member`  (lines 1242–1260)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Connects a Slack speaker to a ufo member when possible. It uses an existing link first, then a Slack-confirmed email to join or link the person.

**Data flow**: It receives context, Slack user id, DM flag, and sender info → checks existing linked member → if needed uses confirmed email to join member → returns member id or nothing; a DM without user lookup can raise an error.

**Call relations**: ingest and interactive use this so turns can be attributed to the right member.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 2 (ingest, interactive); 1 external calls (__init__).


##### `_ambient_context`  (lines 1263–1310)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> str
```

**Purpose**: Fetches a short digest of nearby Slack messages when a new Slack conversation starts, so the agent has context for a mention.

**Data flow**: It receives context, bot token, inbound message, and identity → skips DMs and already participating threads → fetches recent channel history or thread replies → passes messages to _ambient_digest → returns text to prepend to the user message.

**Call relations**: ingest calls this before admitting a conversation-starting Slack turn.

*Call graph*: calls 2 internal fn (_ambient_digest, _slack_ok); called by 1 (ingest); 1 external calls (AsyncClient).


##### `_ambient_digest`  (lines 1313–1350)

```
def _ambient_digest(messages: list[object], bot_user_id: str, header: str) -> str
```

**Purpose**: Turns fetched Slack messages into a bounded plain-text context block for the agent.

**Data flow**: It receives raw messages, bot-user id, and a header → filters out bots, invalid messages, and messages mentioning the bot → formats timestamped lines → trims to a maximum size while keeping the anchor and newest lines → returns the digest text.

**Call relations**: _ambient_context uses this after fetching Slack history.

*Call graph*: called by 1 (_ambient_context); 1 external calls (fromtimestamp).


##### `_slack_download_host_ok`  (lines 1353–1355)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks that a Slack file download URL belongs to Slack before sending the bot token to it.

**Data flow**: It receives a URL → parses the host → returns true only for slack.com or Slack subdomains.

**Call relations**: _stream_download calls this as a safety check before downloading private files.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 1358–1376)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private Slack file download in chunks, with host and size safeguards.

**Data flow**: It receives a bot token and URL → refuses non-Slack hosts → streams the response with authorization → yields chunks → raises if the total exceeds the inbound file limit.

**Call relations**: _download_files passes this stream directly into the workspace file writer, avoiding full-file buffering.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 1388–1404)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack message attachments into the ufo workspace before the agent runs.

**Data flow**: It receives context, conversation id, bot token, and inbound file list → chooses safe unique inbox names → streams each file into workspace storage → records delivered and too-large skipped files → returns a summary.

**Call relations**: ingest calls this after selecting a conversation and before admitting the turn body with a note about files.

*Call graph*: calls 3 internal fn (write_workspace_file, _inbox_name, _stream_download); called by 1 (ingest); 1 external calls (__init__).


##### `_inbox_name`  (lines 1407–1417)

```
def _inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Creates a safe, unique filename for a downloaded Slack attachment inside the Slack inbox folder.

**Data flow**: It receives the raw filename and a set of already-used names → strips path components and unsafe empty names → adds numeric suffixes until unique → records and returns the name.

**Call relations**: _download_files uses this for every inbound file.

*Call graph*: called by 1 (_download_files).


##### `_files_note`  (lines 1420–1431)

```
def _files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Builds the short note appended to the user’s turn telling the agent which Slack files were saved or skipped.

**Data flow**: It receives downloaded-file results → lists workspace paths for delivered files and names for skipped large files → returns bracketed note text or an empty string.

**Call relations**: ingest appends this to the admitted message body after downloading attachments.

*Call graph*: called by 1 (ingest).


##### `ThreadStatus.run`  (lines 1458–1465)

```
async def run(self) -> None
```

**Purpose**: Runs the live Slack thread status loop for one admitted turn.

**Data flow**: It reads the bot token → opens a Slack API client → sets “Thinking…” → follows live turn frames → always clears the status at the end.

**Call relations**: _run_status calls this inside a background task started by _track_status.

*Call graph*: calls 2 internal fn (_follow, _set); called by 1 (_run_status); 1 external calls (AsyncClient).


##### `ThreadStatus._set`  (lines 1467–1496)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> None
```

**Purpose**: Writes one status value to Slack’s assistant thread status API, unless this turn is no longer the newest writer for that thread.

**Data flow**: It checks the thread-writer table → builds the Slack status body, including loading text when non-empty → posts it to Slack → logs the write.

**Call relations**: ThreadStatus.run and _follow use this to show, refresh, and clear live status.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._follow`  (lines 1498–1533)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches the live frames from a running ufo turn and translates them into simple Slack status text.

**Data flow**: It tails the turn stream → maps tool calls, skill loads, and text generation to status messages → throttles rapid updates → refreshes quiet statuses → stops on terminal or parked frames.

**Call relations**: ThreadStatus.run calls this between the initial “Thinking…” status and the final clear.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_track_status`  (lines 1540–1562)

```
def _track_status(ctx: SurfaceContext, turn_id: UUID, queue_key: str, message_ts: str) -> None
```

**Purpose**: Starts one background Slack status task for an admitted turn and marks that turn as the current status writer for its Slack thread.

**Data flow**: It receives context, turn id, queue key, and message timestamp → skips duplicate turn tasks → derives channel and thread timestamp → records writer → creates the status task → registers cleanup.

**Call relations**: ingest and interactive call this after admitting a normal message or button-answer turn.

*Call graph*: calls 1 internal fn (_run_status); called by 2 (ingest, interactive); 2 external calls (__init__, create_task).


##### `_track_status._untrack`  (lines 1557–1560)

```
def _untrack(_done: asyncio.Task[None]) -> None
```

**Purpose**: Cleans up status tracking after a status task finishes.

**Data flow**: It receives the completed task → removes the turn from the task table → removes the thread-writer entry if this turn still owns it.

**Call relations**: It is attached as the done callback for tasks created by _track_status.


##### `_run_status`  (lines 1565–1575)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Runs a ThreadStatus task and logs any failure without crashing request handling.

**Data flow**: It receives a ThreadStatus → awaits its run method → catches unexpected errors → writes an observability log entry.

**Call relations**: _track_status schedules this in the background for each admitted turn.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `interactive`  (lines 1609–1675)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack button clicks, including answer buttons from questions and connect buttons for private authorization.

**Data flow**: It reads and verifies the raw Slack form payload → loads identity → converts the payload into a click object → for connect clicks, posts a private link → for answer clicks, resolves member and conversation, admits the answer turn, starts status, and schedules a message rewrite if this click won idempotency.

**Call relations**: This is the Slack interactivity route; it works with _to_click, _resolve_member, _track_status, _rewrite_in_background, and _ephemeral_in_background.

*Call graph*: calls 20 internal fn (admit, admitted_body, connect_url, conversation_for, credential, default_agent, find_conversation, linked_member, _ctx_signing_secret, _ephemeral_in_background (+10 more)); 2 external calls (JSONResponse, Response).


##### `_rewrite_in_background`  (lines 1681–1684)

```
def _rewrite_in_background(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Schedules the Slack message rewrite that replaces clicked answer buttons with the chosen answer.

**Data flow**: It receives the bot token and click details → creates an asynchronous rewrite task → tracks it until completion.

**Call relations**: interactive calls this only for the winning answer click.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 1687–1691)

```
async def _run_rewrite(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Performs the answer-button rewrite and logs any failure.

**Data flow**: It receives bot token and click details → calls _replace_buttons_with_answer → catches and logs errors.

**Call relations**: _rewrite_in_background schedules this so Slack can be acknowledged quickly.

*Call graph*: calls 1 internal fn (_replace_buttons_with_answer); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 1694–1697)

```
def _ephemeral_in_background(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Schedules a private Slack response to a connect-button click.

**Data flow**: It receives context, click details, and text → creates a background task to post an ephemeral message → tracks it until completion.

**Call relations**: interactive calls this for connect clicks so the HTTP acknowledgement is not delayed.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 1700–1725)

```
async def _post_ephemeral(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Posts a private Slack message visible only to the user who clicked a connect button.

**Data flow**: It reads the bot token → posts chat.postEphemeral with channel, user, text, and optional thread → logs any failure.

**Call relations**: _ephemeral_in_background runs this as a task after interactive decides what private text to show.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_click`  (lines 1728–1783)

```
def _to_click(raw: bytes, identity: SlackIdentity) -> AnswerClick | ConnectClick | None
```

**Purpose**: Converts Slack’s raw interactive form payload into an AnswerClick or ConnectClick, or ignores unsupported actions.

**Data flow**: It parses the form and JSON payload → checks the team matches the installed identity → reads action, user, channel, and message fields → returns a typed click object or nothing.

**Call relations**: interactive uses this after signature verification to understand what button was pressed.

*Call graph*: calls 2 internal fn (_dict_field, _string_field); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_dict_field`  (lines 1786–1790)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Safely reads a required dictionary field from an untrusted Slack payload.

**Data flow**: It receives a mapping and field name → checks that the value is a dictionary → returns it or raises an error.

**Call relations**: _to_click uses this for nested Slack fields like user, channel, and message.

*Call graph*: called by 1 (_to_click).


##### `_replace_buttons_with_answer`  (lines 1793–1835)

```
async def _replace_buttons_with_answer(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Updates the original Slack question message so the clicked button row becomes a small “answered by” line.

**Data flow**: It receives bot token and answer-click data → copies Slack’s delivered blocks → replaces the clicked block if found, otherwise appends an answer block → calls chat.update → returns nothing on success.

**Call relations**: _run_rewrite calls this after interactive admits the winning answer turn.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (_run_rewrite); 2 external calls (AsyncClient, dumps).


##### `_reply_text`  (lines 1838–1848)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text to post for a completed turn, including friendly fallback messages for failed, cancelled, or empty replies.

**Data flow**: It receives a Writeback → checks status and text → returns failure text, cancellation text, the agent reply, or an empty-reply placeholder.

**Call relations**: _reply_with_oversize_links uses this as the base Slack reply text.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 1851–1866)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds extra delivery information to the reply text, such as credential instructions and links for artifacts too large for Slack upload.

**Data flow**: It receives context and writeback → starts from _reply_text → appends terminal credential guidance if needed → appends download links for oversized artifacts → returns the final text.

**Call relations**: post uses this before building the Slack chat.postMessage body.

*Call graph*: calls 2 internal fn (_oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 1869–1872)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one too-large artifact as a Slack-visible link line when possible.

**Data flow**: It receives context and artifact → asks the context for a temporary artifact link → formats filename or markdown link plus byte size → returns one line of text.

**Call relations**: _reply_with_oversize_links calls this for each artifact that Slack cannot upload inline.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_debug_link`  (lines 1875–1887)

```
async def _debug_link(ctx: SurfaceContext, writeback: Writeback) -> str | None
```

**Purpose**: Builds an operator-only debug URL for the delivered turn when this deploy has a public base URL.

**Data flow**: It receives context and writeback → finds the conversation by queue key → combines base URL, workspace id, conversation id, and turn id → returns the URL or nothing.

**Call relations**: post adds this link to the metadata footer only for the operator workspace.

*Call graph*: calls 1 internal fn (find_conversation); called by 1 (post).


##### `post`  (lines 1890–1942)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Posts the finished ufo reply into the correct Slack channel or thread and returns Slack’s message reference.

**Data flow**: It splits the queue key into channel and thread → reads the bot token → builds reply text, action blocks, and optional operator metadata → posts to Slack → retries once with safer blocks if Slack rejects block formatting → returns channel:timestamp or raises a delivery error.

**Call relations**: The core delivery poller calls this to deliver terminal writebacks to Slack.

*Call graph*: calls 8 internal fn (credential, is_operator_workspace, _chat_post, _debug_link, _reply_with_oversize_links, slack_ask_blocks, slack_connect_blocks, slack_reply_body); 2 external calls (__init__, AsyncClient).


##### `_chat_post`  (lines 1945–1985)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request to Slack and returns the parsed response while preserving useful delivery errors.

**Data flow**: It receives an HTTP client, bot token, and JSON body → posts to Slack → on HTTP failure, extracts Slack error and retry-after if present → raises SurfaceDeliveryError → otherwise returns parsed JSON.

**Call relations**: post uses this so it can specially retry invalid block formatting before treating the delivery as failed.

*Call graph*: calls 1 internal fn (__init__); called by 1 (post); 1 external calls (post).


##### `attach`  (lines 1988–2007)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shareable artifacts from a completed turn into Slack, skipping files that are too large for Slack’s upload limit.

**Data flow**: It filters artifacts by size → derives channel and thread from the queue key → reads the bot token → uploads eligible artifacts concurrently → logs individual failures without failing the whole attachment step.

**Call relations**: The delivery flow calls this after posting the reply, while oversized files are represented as links by post.

*Call graph*: calls 2 internal fn (credential, _upload_artifact); 1 external calls (gather).


##### `_upload_artifact`  (lines 2010–2056)

```
async def _upload_artifact(ctx: SurfaceContext, bot_token: str, channel: str, thread_ts: str | None, artifact: SharedArtifact) -> None
```

**Purpose**: Streams one ufo artifact to Slack using Slack’s external upload flow.

**Data flow**: It reserves an upload URL from Slack with filename and size → streams bytes from the blob store to that URL → completes the upload into the channel or thread with a title → returns nothing on success.

**Call relations**: attach runs this for each uploadable artifact, often concurrently with other artifact uploads.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 4 external calls (__init__, AsyncClient, Timeout, dumps).


##### `_slack_ok`  (lines 2059–2065)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Shared helper for Slack API calls that must return ok:true.

**Data flow**: It awaits an HTTP request → raises for HTTP errors → parses JSON → checks Slack’s ok field → returns the payload or raises SlackApiError.

**Call relations**: Most Slack API helper functions use this so they all treat Slack errors consistently.

*Call graph*: called by 9 (_list, _members, _set, _ambient_context, _post_ephemeral, _replace_buttons_with_answer, _slack_user, _upload_artifact, slack_oauth_exchange); 1 external calls (__init__).


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The terminal client talks to the server by making POST requests. This file decides what each request means and what the client should show next. Think of it like a ticket window with a long-held phone line: the user sends a message, the server keeps the connection open while an answer is being produced, and it streams back short instruction lines such as “show this text,” “ask for input,” “show a status,” or “poll again soon.”

The file also checks that the caller is allowed in. It reads a bearer token, which is a signed proof carried in the HTTP Authorization header, and uses it to identify the workspace and member email. If the email is new, the surface links it to a member record so the conversation can continue across requests.

A channel path becomes a durable conversation key, so the same user and channel return to the same thread. If the request body contains text, that text is admitted as a new turn. If the body is empty, the server does not add a message; it simply resumes watching the latest turn. This matters because a reply can take longer than one held HTTP request. In that case the server sends a `poll` instruction and the shell reconnects.

The file also supports private credential entry. Secret values are sent with special headers and are stored through privileged context methods, not added to the chat transcript.

#### Function details

##### `directive`  (lines 50–58)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one instruction line for the terminal client. It makes sure tabs, newlines, and backslashes inside user-visible text cannot accidentally break the simple line format the shell reads.

**Data flow**: It receives a verb such as `txt`, `ask`, or `poll`, plus any text fields. It escapes unsafe characters inside each field, joins everything with tab characters, adds a newline, and returns the result as bytes ready to send over HTTP.

**Call relations**: This is the shared packaging step used whenever the file speaks to the shell client. Higher-level code such as `directives_for`, `_answer`, `_say_lines`, `stream_directives`, `channel`, and `_fulfill_secret` decide what should happen, then call `directive` to turn that decision into the exact wire format.

*Call graph*: called by 6 (_answer, _fulfill_secret, _say_lines, channel, directives_for, stream_directives).


##### `resolve_workspace`  (lines 61–68)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Finds which workspace a request belongs to before the main request handler runs. If the request has no usable bearer token, it returns nothing so the request can be rejected.

**Data flow**: It reads the Authorization header, checks that it looks like `Bearer <token>`, and passes the token to the bearer-token helper that extracts the workspace claim. The output is a workspace UUID, or `None` if the header is missing or malformed.

**Call relations**: The shared surface routing layer calls this early to scope the request to a workspace. It delegates the actual token reading to `ufo.sdk.bearer.workspace_claim`, while the later `channel` handler separately verifies the same token for the member email.

*Call graph*: 1 external calls (workspace_claim).


##### `directives_for`  (lines 71–95)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Translates one live conversation frame into one or more terminal instructions. A frame is a small event from the running turn, such as a piece of text, a tool call, a cost update, or the final answer.

**Data flow**: It receives a live frame, a flag saying whether answer text has already been streamed, and optional information about missing credentials or a connection URL message. It pattern-matches the frame type and returns directive bytes such as `txt`, `note`, `status`, `say`, `secret`, `ask`, or `exit`.

**Call relations**: `stream_directives` calls this for each frame it reads from the conversation tail. For simple frames it calls `directive` directly; for tool activity it asks `_activity` to make a human-readable note; for final terminal frames it hands off to `_answer` so turn-ending behavior stays in one place.

*Call graph*: calls 3 internal fn (_activity, _answer, directive); called by 1 (stream_directives).


##### `_activity`  (lines 98–100)

```
def _activity(frame: ToolCall) -> str
```

**Purpose**: Creates a short human-readable message for a tool call. This lets the terminal show that the assistant is doing something, not just silently waiting.

**Data flow**: It receives a tool-call frame and looks for a description or preview. It returns text like `running search: looking up ...`, or just `running search` if there is no extra detail.

**Call relations**: `directives_for` calls this when it sees a `ToolCall` frame, then wraps the returned text in a `note` directive for the terminal client.

*Call graph*: called by 1 (directives_for).


##### `_answer`  (lines 103–131)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Builds the final instructions for a completed, failed, or cancelled turn. This is where the terminal is told whether to show the final answer, ask for more input, request secrets, or exit.

**Data flow**: It receives a terminal frame, a flag saying whether the answer text was already streamed, any credential prompts that still need values, and an optional connection message. For a successful turn it may output answer lines, secret prompts, a connection message, and a new input prompt. For a failed turn it outputs an error-like message and prompts again. For a cancelled turn it tells the client to exit.

**Call relations**: `directives_for` hands terminal frames to `_answer`. `_answer` uses `_say_lines` when it needs to split final text into displayable lines, and uses `directive` to package prompts, secret requests, and exit instructions.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 134–135)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Turns a block of text into separate `say` instructions for the terminal. This keeps multi-line answers readable without letting embedded newlines disturb the wire format.

**Data flow**: It receives a text string, splits it into lines, and wraps each line with `directive("say", line)`. If the text has no lines, it still returns one `say` directive for the original text.

**Call relations**: `_answer` calls this when a final answer or failure message needs to be displayed as normal spoken output. `_say_lines` relies on `directive` for the actual escaping and byte formatting.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 138–198)

```
async def stream_directives(frames: AsyncIterator[tuple[str, LiveFrame]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[[], Awaitable[str]] | None=
```

**Purpose**: Streams terminal instructions from a live turn, but only for a limited hold time. If the answer is not done before the hold expires, it tells the shell to reconnect and continue polling.

**Data flow**: It receives an async stream of live frames, a maximum number of seconds to hold the HTTP response open, and optional callbacks for checking pending credential prompts and building a connection URL. It reads frames until the turn ends, the frame source ends, or time runs out. It yields directive bytes as soon as they are ready, and if the turn has not ended it yields a `poll` directive.

**Call relations**: `channel` uses this as the body of the streaming HTTP response. Inside the loop it uses `_next` so end-of-stream is easy to treat as `None`, calls `directives_for` to render each frame, and uses `directive` itself when it must send the final `poll`. It also calls the supplied pending/connect callbacks when terminal frames mention credentials or connection requests.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 2 external calls (get_running_loop, wait_for).


##### `_next`  (lines 201–207)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next live frame safely for the streaming loop. It converts the special async-iterator end signal into a normal `None` value.

**Data flow**: It receives an async iterator of cursor-and-frame pairs. It awaits the next item; if an item exists, it returns it, and if the iterator is finished, it returns `None` instead of raising the end-of-iteration exception.

**Call relations**: `stream_directives` calls this inside a timeout. This small wrapper keeps timeout handling simple because the stream loop only has to check whether the result is an item or `None`.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 210–214)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Verifies the request token and extracts the member email. This is the identity check for the actual terminal channel request.

**Data flow**: It reads the Authorization header, confirms it is a bearer token, and asks the bearer-token helper to verify the token for the current workspace. The result is the authenticated email address, or `None` if the header or token is not valid.

**Call relations**: `channel` calls this at the start of every channel request. It delegates cryptographic token verification to `ufo.sdk.bearer.verify_token`, keeping the request handler focused on conversation flow.

*Call graph*: called by 1 (channel); 1 external calls (verify_token).


##### `channel`  (lines 217–249)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles one terminal POST request for one conversation channel. It authenticates the user, accepts a new message or resumes an existing turn, and returns either a plain response or a streaming response of terminal instructions.

**Data flow**: It receives the privileged surface context and the HTTP request. It verifies the email, links or finds the member, checks whether the request is actually a secret submission, and otherwise builds a conversation key from the email and channel path. If the body is empty it resumes the latest turn; if the body has text it size-checks it and admits it as a new turn for the default agent. It then returns a streaming text response that tails the turn and emits directives.

**Call relations**: This is the main handler registered in `ROUTES`. It starts by calling `_authenticated_email`; if special secret headers are present it hands the request to `_fulfill_secret`. For normal chat flow it uses `SurfaceContext` methods to find or create the member link, find the conversation, get or admit the turn, tail live frames, and optionally prepare a connection URL. The live frame stream is then handed to `stream_directives`.

*Call graph*: calls 11 internal fn (admit, conversation_for, default_agent, latest_turn, link_member, linked_member, tail, _authenticated_email, _fulfill_secret, directive (+1 more)); 4 external calls (partial, PlainTextResponse, body, StreamingResponse).


##### `_fulfill_secret`  (lines 252–271)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores one private credential value sent by the terminal client. The secret is treated as a credential answer, not as a chat message, so it does not enter the conversation transcript.

**Data flow**: It receives the surface context, request, member id, and sealed credential-request token. It reads the credential slot from a header and the secret value from the body, rejects empty or too-large input, and asks the privileged context to fulfill the credential request. It returns a plain text directive saying whether the value was stored or why it was not.

**Call relations**: `channel` calls this when the request includes the secret header. `_fulfill_secret` uses `SurfaceContext.fulfill_credential_request` for the protected storage and validation step, catches invalid credential requests, and uses `directive` plus plain text responses to tell the shell what happened.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling`

This file is the web “front door” for chatting with the agent. Without it, a browser user would not have a self-contained page for sending messages, receiving live replies, or checking spend. It sits at the edge of the system: it speaks HTTP to the browser, but uses the core SurfaceContext for the important shared work, such as finding the right workspace, admitting a message into the durable queue, following live answer frames, and reading cost totals.

Authentication is cookie-based. A signed bearer token can arrive in the page URL first, then the page stores it as the ufo_session cookie for later requests. The workspace is read from that token before a route runs, and the user email is verified again when they actually chat, stream, or view spend. The email is linked to a member record the first time needed.

The chat flow is simple: the page posts the user’s text, the server checks the session and message size, then admits a new “turn” for the default agent. The browser then opens a Server-Sent Events stream, which is a one-way live feed from server to browser, like listening to a radio channel for that answer. The stream sends normal text, tool activity, cost updates, account-connection links, and final status. A separate spend route renders an HTML summary of recent costs.

#### Function details

##### `resolve_workspace`  (lines 38–45)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This function tells the shared surface system which workspace a web request belongs to. It reads the signed token from the session cookie, or from the URL during first landing, and extracts the workspace claim from it.

**Data flow**: It receives the incoming web request and the surface authentication object. It looks for a ufo_session cookie first, then a token query parameter. If it finds a token, it asks the bearer-token code to read the workspace ID from it; if there is no token, it returns nothing, which means the request cannot be scoped to a workspace.

**Call relations**: This is used before the route handler runs, so the fleet can decide whether the request belongs to a valid workspace. It delegates the actual token reading to workspace_claim rather than trusting raw request data.

*Call graph*: 1 external calls (workspace_claim).


##### `_authenticate`  (lines 48–59)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | None
```

**Purpose**: This helper verifies that a request really belongs to a known web user in the current workspace. It turns the signed session cookie into a member ID and email that the rest of the web handlers can trust.

**Data flow**: It receives the surface context and the incoming request. It reads the session cookie, verifies that the token is valid for the current workspace, then looks up a member linked to the token’s email. If none exists, it tries to create that link. It returns a pair of member ID and email, or returns nothing when authentication fails.

**Call relations**: The chat, stream, and spend handlers all call this first because each of those actions must be limited to an authenticated workspace member. It relies on verify_token for checking the bearer token and on SurfaceContext methods to find or create the matching member link.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 3 (chat, spend, stream); 1 external calls (verify_token).


##### `chat_page`  (lines 62–69)

```
async def chat_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function serves the browser chat page. If the user arrived with a token in the URL, it stores that token in a secure session cookie so later chat requests can authenticate normally.

**Data flow**: It receives the surface context and the incoming request. It creates an HTML response containing the embedded chat page. If the request has a token query parameter, it adds that token as the ufo_session cookie on the response. The output is the finished HTML response sent to the browser.

**Call relations**: This is the landing route for the web surface. It uses HTMLResponse to send the page and set_session_cookie to prepare the browser for later calls to chat, stream, and spend.

*Call graph*: 2 external calls (HTMLResponse, set_session_cookie).


##### `chat`  (lines 72–85)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function accepts a user’s message from the web page and starts a new agent turn. It does not generate the answer itself; it puts the message into the shared agent admission path so the same backend can answer as it would for other surfaces.

**Data flow**: It receives the surface context and request. It first authenticates the session. Then it reads the raw request body as the message text, rejects empty or overly large messages, finds or creates the user’s conversation, gets the default agent, and admits the message as a new turn. It returns JSON containing the new turn ID, or an error response if validation fails.

**Call relations**: The browser calls this after the user presses Send. It calls _authenticate for identity, then uses SurfaceContext to find the conversation, choose the default agent, and admit the turn. The returned turn ID is what the browser uses next to open the live stream.

*Call graph*: calls 4 internal fn (admit, conversation_for, default_agent, _authenticate); 3 external calls (JSONResponse, body, Response).


##### `stream`  (lines 88–105)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens the live event stream for one agent turn. It makes sure the requester owns that turn before allowing the browser to watch its updates.

**Data flow**: It receives the surface context and request. It authenticates the user, parses the turn ID from the URL, checks that the turn exists, and checks that its owner matches the authenticated member. It also reads the Last-Event-ID header, which helps resume after a dropped connection. If everything is allowed, it returns a streaming response that sends live events as they arrive.

**Call relations**: The browser calls this immediately after chat returns a turn ID. It calls _authenticate to identify the user, turn_owner to enforce ownership, and then hands the actual event production to _events inside a StreamingResponse.

*Call graph*: calls 3 internal fn (turn_owner, _authenticate, _events); 3 external calls (Response, StreamingResponse, UUID).


##### `_events`  (lines 108–125)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: This async generator converts the core system’s live turn updates into browser-friendly event bytes. It also turns account-connection requests into a special event containing a URL the user can click.

**Data flow**: It receives the surface context, turn ID, member ID, and an optional cursor saying where to resume. It tails the core live frame stream. For each frame, it may first emit a connect or connect_error event if the terminal frame asks the user to connect an account. It then converts the frame itself into a Server-Sent Events message and yields bytes to the HTTP stream.

**Call relations**: stream calls this when it is ready to send live updates to the browser. It reads frames from SurfaceContext.tail, asks SurfaceContext.connect_url for connection links when needed, and uses _sse to format ordinary frames.

*Call graph*: calls 3 internal fn (connect_url, tail, _sse); called by 1 (stream); 1 external calls (dumps).


##### `spend`  (lines 128–136)

```
async def spend(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This route shows recent workspace spending in the browser. It lets any authenticated member see the same kind of rollup that the command-line spend view would show.

**Data flow**: It receives the surface context and request. It authenticates the session, reads the requested time window from the query string or uses the default one-day window, asks the core context for a spend rollup, and returns an HTML page built from that report. If authentication fails, it returns an unauthorized response.

**Call relations**: A browser calls this through the web surface’s spend route. It uses _authenticate to confirm membership, SurfaceContext.spend_rollup to fetch the accounting data, and _spend_page plus HTMLResponse to present it.

*Call graph*: calls 3 internal fn (spend_rollup, _authenticate, _spend_page); 2 external calls (HTMLResponse, Response).


##### `_sse`  (lines 139–156)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: This helper formats one live frame as a Server-Sent Events message. Server-Sent Events are a simple browser feature where the server sends named text events over one long-lived HTTP response.

**Data flow**: It receives a cursor and a live frame. If the cursor is present, it writes it as the event ID so the browser can later reconnect from that point. It inspects the frame type and chooses an event name such as terminal, parked, cost, tool, or skill, then serializes the frame to JSON bytes. The output is one complete SSE event block.

**Call relations**: _events calls this for every normal live frame coming from the core tail. The browser-side JavaScript listens for the event names produced here and updates the chat bubble, activity note, cost meter, or final status.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_money`  (lines 159–160)

```
def _money(micro_usd: int) -> str
```

**Purpose**: This small helper turns a stored cost amount into a human-readable dollar string. The system stores money in micro-dollars, meaning millionths of a dollar, to avoid rounding problems.

**Data flow**: It receives an integer number of micro-dollars. It divides by the constant number of micro-dollars per dollar and formats the result with six decimal places. The output is a string such as $0.000123.

**Call relations**: _spend_page and _subject_rows use this whenever they need to show costs on the spend page. It keeps all money formatting consistent.

*Call graph*: called by 2 (_spend_page, _subject_rows).


##### `_subject_rows`  (lines 163–168)

```
def _subject_rows(subjects: tuple[SubjectTotal, ...]) -> str
```

**Purpose**: This helper builds the HTML table rows for spend grouped by a subject, such as member or agent. It makes sure labels are escaped so they are shown as text rather than interpreted as HTML.

**Data flow**: It receives a tuple of subject totals. For each subject, it escapes the label, formats the cost with _money, and creates a table row. If there are no subjects, it returns a single row saying none.

**Call relations**: _spend_page calls this twice: once for member totals and once for agent totals. It relies on _money for consistent cost display and html.escape for safe HTML output.

*Call graph*: calls 1 internal fn (_money); called by 1 (_spend_page); 1 external calls (escape).


##### `_spend_page`  (lines 171–191)

```
def _spend_page(report: SpendReport) -> str
```

**Purpose**: This function turns a spend report into a complete HTML page. It gives an authenticated workspace member a simple cost summary for the chosen time window.

**Data flow**: It receives a SpendReport containing the window length, total cost, spending by dimension, spending by member, and spending by agent. It formats each section into HTML tables, escaping labels and formatting money along the way. The output is a full HTML document string ready to send to the browser.

**Call relations**: spend calls this after asking the core context for a spend rollup. It uses _money for totals and dimension costs, and _subject_rows for the member and agent tables.

*Call graph*: calls 2 internal fn (_money, _subject_rows); called by 1 (spend); 1 external calls (escape).


### Core surface bridge
The shared bridge connects trusted external surfaces to workspace conversations, credentials, live reply streams, and durable delivery.

### `core/src/ufo/ext/surface.py`

`orchestration` · `request handling and background writeback delivery`

A “surface” is a place where a human talks to UFO, such as Slack, the web app, or a sample live interface. This file is the seam between those outside-facing extensions and the protected core. It gives surfaces special powers that ordinary extensions do not have: they can say who a user is, create or find the right conversation, put a user’s message onto the turn queue, and read declared credentials inside the main process.

The file supports two styles of surface. A live surface keeps a browser or connection open and streams frames as the turn runs. A durable surface, like Slack, cannot rely on a live connection, so replies are saved as “writebacks” and a background poller later posts them back. The poller is careful: it claims work so two workers do not deliver the same row at the same time, renews that claim during slow network calls, records the posted reply reference before uploading attachments, and retries failures with backoff.

The file also includes safe helpers for workspace files, identity linking, credential handoffs, artifact download links, debug read views, and installation lookup. Without this file, surfaces would either need unsafe direct database access or would not be able to admit messages and deliver answers reliably.

#### Function details

##### `MemberAdmitter.admit`  (lines 102–111)

```
async def admit(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: This protocol describes the one operation needed to put a member’s message onto UFO’s durable turn queue. A surface uses it when an outside user sends a message that should become work for an agent.

**Data flow**: The caller provides a conversation, an agent, the message text, optional duplicate-protection information, optional turn context, and the member who spoke. The concrete implementation stores or joins the turn and returns the turn id.

**Call relations**: SurfaceContext.admit is the public surface-facing wrapper around this protocol. Actual surface routes call SurfaceContext.admit, which then hands the work to the injected MemberAdmitter implementation.


##### `TurnTailer.tail`  (lines 120–120)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This protocol describes how a live surface reads the stream of updates for one turn. It exists so live surfaces do not talk to the internal hub directly.

**Data flow**: The caller supplies a turn id and an optional cursor saying where to resume. The implementation yields pairs of replay cursor and live frame until the turn ends.

**Call relations**: SurfaceContext.tail exposes this capability to live surfaces. Web, sample, debugger, and UFO channel surfaces call through the context rather than reaching into the hub themselves.


##### `workspace_key`  (lines 136–144)

```
def workspace_key(conversation_id: UUID, rel: str) -> str
```

**Purpose**: This builds the safe blob-storage key for a file inside a conversation’s workspace folder. It prevents a surface-supplied path from escaping into another folder, like checking that a guest only writes inside their assigned drawer.

**Data flow**: It receives a conversation id and a relative file path. It cleans and checks the path, rejects absolute paths or paths containing '..', and returns the full blob key under conversations/<id>/workspace/.

**Call relations**: SurfaceContext.write_workspace_file and SurfaceContext.read_workspace_file call this before touching blob storage, so both upload and download use the same safety rule.

*Call graph*: called by 2 (read_workspace_file, write_workspace_file); 1 external calls (PurePosixPath).


##### `ConversationSummary._aware_utc`  (lines 202–205)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This makes conversation timestamps consistently timezone-aware. It prevents old or database-returned naive timestamps from being interpreted differently by callers.

**Data flow**: It receives a datetime or None. None stays None; a datetime with no timezone is treated as UTC; a datetime that already has a timezone is left alone.

**Call relations**: Pydantic calls this validator when ConversationSummary objects are created, especially from SurfaceContext.list_conversations.

*Call graph*: 1 external calls (replace).


##### `LedgerEntry._aware_utc`  (lines 219–220)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This makes accounting timestamps consistently timezone-aware. It keeps ledger entries from carrying ambiguous times.

**Data flow**: It receives a datetime. If the datetime has no timezone, it marks it as UTC; otherwise it returns it unchanged.

**Call relations**: Pydantic calls this validator when LedgerEntry objects are built inside SurfaceContext.turn_detail.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 242–247)

```
def _fulfilled_marker_key(workspace_id: UUID, sealed: str, slot: str) -> str
```

**Purpose**: This creates the blob key for a small marker saying that one requested credential slot has already been filled. The marker stops the same credential prompt from being shown again.

**Data flow**: It receives a workspace id, the sealed credential request, and a slot name. It hashes the sealed request, combines that digest with the slot, and returns a stable marker path in blob storage.

**Call relations**: SurfaceContext.credential_prompt_pending reads this marker, and SurfaceContext.fulfill_credential_request writes it after storing the credential.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_email_domain`  (lines 250–254)

```
def _email_domain(email: str) -> str
```

**Purpose**: This extracts the domain part of an email address in a cautious way. Malformed addresses produce an empty string so they cannot accidentally pass a domain check.

**Data flow**: It receives an email-like string, trims and lowercases it, and looks for the part after '@'. It returns the domain only if both local name and domain exist.

**Call relations**: SurfaceContext.join_member uses it to decide whether a verified email belongs to the workspace’s own domain. SurfaceContext.is_operator_workspace uses it to recognize the operator workspace.

*Call graph*: called by 2 (is_operator_workspace, join_member).


##### `_bind_surface_installation`  (lines 257–285)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str) -> None
```

**Purpose**: This records that an external surface installation, such as a Slack team id, belongs to a workspace. It is the shared write path used by both surface callbacks and tools.

**Data flow**: It receives a workspace id, surface name, and installation id. It validates that the installation id is not empty, then inserts or updates the binding in the database; if another workspace already owns that installation, it raises a conflict.

**Call relations**: SurfaceContext.bind_installation and SurfaceInstallationAccess.bind both call this helper so installation binding follows one consistent database rule.

*Call graph*: called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.credential`  (lines 309–312)

```
async def credential(self, slot: str) -> str
```

**Purpose**: This lets a trusted surface read a credential slot for its workspace. It is used for secrets the surface needs, such as Slack signing secrets and bot tokens.

**Data flow**: It takes a slot name. If this context has no credential store, it raises an error; otherwise it reads the slot for the current workspace and returns the secret value.

**Call relations**: Slack surface functions call this throughout request verification, posting, attachment upload, and identity checks.

*Call graph*: called by 8 (_ctx_signing_secret, _identity, _post_ephemeral, _run_identity_proof, attach, ingest, interactive, post).


##### `SurfaceContext.credential_prompt_pending`  (lines 314–326)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: This checks whether a credential prompt should still be shown to the user. It avoids repeating prompts that are expired, invalid, for another workspace, for another slot, or already answered.

**Data flow**: It receives a sealed credential request and a slot name. It opens and verifies the sealed request, checks workspace and slot membership, then looks for the fulfilled marker in blob storage and returns true only if the marker is absent.

**Call relations**: It uses _fulfilled_marker_key and open_credential_request. Surfaces can use it while rendering reconnects or pending handoffs.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 328–336)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: This opens a sealed credential handoff and returns the trusted claims inside it. A surface callback uses it after an OAuth-style browser redirect to know which workspace, member, and slot the authorization belongs to.

**Data flow**: It receives a sealed string. It requires a credential store, verifies the seal with that store’s key, and returns the decoded credential request state or raises if invalid.

**Call relations**: Slack’s OAuth callback calls this when finishing a credential authorization flow.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 338–361)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: This stores one requested credential value after proving the request is valid and the right member is fulfilling it. It prevents a user from filling someone else’s credential request or an unrelated slot.

**Data flow**: It receives the sealed request, slot, value, and member id. It opens the seal, checks workspace, member, and slot, stores the secret in the credential store, then writes a fulfilled marker to blob storage.

**Call relations**: Slack OAuth and the built-in UFO surface use this to complete credential prompts. It shares marker keys with credential_prompt_pending.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 2 (oauth_callback, _fulfill_secret); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 363–369)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: This binds the current surface’s external installation identity to the current workspace. A Slack OAuth install uses it to remember which workspace owns later Slack events.

**Data flow**: It receives an installation id and passes the current workspace and surface name to the shared binding helper. The database is updated or a conflict is raised.

**Call relations**: Slack’s OAuth callback calls this after installation. The actual database write is delegated to _bind_surface_installation.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.public_base_url`  (lines 372–375)

```
def public_base_url(self) -> str | None
```

**Purpose**: This exposes the deployment’s public base URL, if one is configured. Surfaces use it when they must build callback or download URLs visible outside the server.

**Data flow**: It reads the value stored in the context and returns either the URL string or None.

**Call relations**: It is a simple property used by surface code that needs to render externally reachable links.


##### `SurfaceContext.artifact_link`  (lines 377–388)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: This creates a temporary public download link for a shared artifact when a surface cannot upload the file directly. If public download links are not configured, it safely returns None.

**Data flow**: It receives a SharedArtifact. If both a token secret and public base URL exist, it creates an expiring signed token for the blob key and filename and returns a full download URL; otherwise it returns None.

**Call relations**: Slack’s oversize-file rendering uses this when it needs to mention a file by link instead of attaching it inline.

*Call graph*: called by 1 (_oversize_link_line); 2 external calls (now, mint_artifact_token).


##### `SurfaceContext.default_agent`  (lines 390–402)

```
async def default_agent(self) -> UUID
```

**Purpose**: This finds the workspace’s default agent id. Surface routes use it when a user message should go to the standard assistant rather than a specific agent.

**Data flow**: It queries the agent table for the current workspace and the default agent name. It returns the id or raises an error if the workspace is missing that required agent.

**Call relations**: Sample, Slack, web, and UFO surfaces call this before admitting a message into a conversation.

*Call graph*: called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext._identity_member`  (lines 404–417)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: This looks up which workspace member is already linked to a surface-specific external id. It is the low-level identity lookup used by higher-level linking methods.

**Data flow**: It receives a surface name and external id. It queries the surface_identity table for the current workspace and returns the member id if found, otherwise None.

**Call relations**: SurfaceContext.linked_member calls it for this surface. SurfaceContext.adopt_identity calls it for a peer surface before linking the same human here.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 419–420)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: This asks whether this surface already knows an external user id as a workspace member. It is a read-only identity check.

**Data flow**: It receives an external id and delegates to _identity_member using the current surface name. It returns a member id or None.

**Call relations**: Sample, Slack, UFO, and web surfaces call this while authenticating or resolving who is speaking.

*Call graph*: calls 1 internal fn (_identity_member); called by 6 (_surface_ingest, _surface_live_admit, _resolve_member, interactive, channel, _authenticate).


##### `SurfaceContext._owner_email`  (lines 422–436)

```
async def _owner_email(self) -> str | None
```

**Purpose**: This finds the email address of the earliest member in the workspace, treated as the workspace owner. The owner’s email domain is used as the workspace’s trusted domain.

**Data flow**: It queries members in creation order for the current workspace and returns the first email, or None if there are no members.

**Call relations**: SurfaceContext.join_member uses this for same-domain joining. SurfaceContext.is_operator_workspace uses it to identify the operator’s own workspace.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.is_operator_workspace`  (lines 438–446)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: This checks whether the current workspace belongs to the UFO operator. It gates internal-only renderings, such as extra Slack accounting details.

**Data flow**: It reads the owner email, extracts its domain, and compares it with the fixed operator domain. It returns false if no owner exists.

**Call relations**: Slack posting uses this to decide whether to show operator-only information.

*Call graph*: calls 2 internal fn (_owner_email, _email_domain); called by 1 (post).


##### `SurfaceContext.adopt_identity`  (lines 448–471)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: This links the current surface’s external id to a member already known by another surface. It lets the same human keep one member identity across places like CLI and web.

**Data flow**: It receives a peer surface name and external id. It looks up the peer identity, inserts a matching identity for the current surface if found, logs harmless races, and returns the member id or None.

**Call relations**: The sample live admit path calls this. Internally it relies on _identity_member and then writes surface_identity.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 473–502)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: This links an external surface user id to an existing workspace member by email. It is used when the surface has verified the user’s email but should not create a new member.

**Data flow**: It receives an external id and email. It looks up a member in this workspace with that email, inserts the surface identity link if found, logs duplicate-link races, and returns the member id or None.

**Call relations**: SurfaceContext.join_member builds on this. Sample, UFO, and web surfaces also call it directly during identity setup.

*Call graph*: called by 4 (join_member, _surface_ingest, channel, _authenticate); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 504–521)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: This links a verified email to a member, creating the member if the email belongs to the workspace’s own domain. It lets teammates join on first contact through a trusted channel like Slack.

**Data flow**: It first tries link_member. If no member exists, it compares the email’s domain with the owner’s domain; for a match, it creates the member and links again. Foreign or malformed domains return None.

**Call relations**: Slack’s member-resolution flow calls this when Slack has verified a user email. It uses _owner_email, _email_domain, create_member, and link_member.

*Call graph*: calls 3 internal fn (_owner_email, link_member, _email_domain); called by 1 (_resolve_member); 2 external calls (workspace_tx, create_member).


##### `SurfaceContext._conversation_lookup`  (lines 523–528)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: This builds the database query for finding a conversation by the current surface’s queue key. A queue key is the surface’s own address for a chat place, such as a channel/thread.

**Data flow**: It receives a queue key and returns a SQL query scoped to the current workspace and surface. The query selects the conversation id and owning member id.

**Call relations**: SurfaceContext.find_conversation and SurfaceContext.conversation_for reuse this query so lookup and creation logic agree.

*Call graph*: called by 2 (conversation_for, find_conversation); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 530–536)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: This finds an existing conversation for a surface queue key without creating one. It is useful when a surface wants to check whether a thread is already participating.

**Data flow**: It receives a queue key, runs the shared conversation lookup, and returns the conversation id or None.

**Call relations**: Slack debug and interaction paths call this when they need to recognize an existing conversation without side effects.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 3 (_debug_link, _participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_for`  (lines 538–577)

```
async def conversation_for(self, queue_key: str, member_id: UUID | None) -> UUID
```

**Purpose**: This gets or creates the conversation for a surface queue key. It also lets a previously memberless conversation become owned once the speaker is later identified.

**Data flow**: It receives a queue key and optional member id. If a conversation exists, it may fill in the missing member id and returns the conversation id. If none exists, it creates one, and if another request won the race, it rereads the existing row.

**Call relations**: Sample, Slack, UFO, and web surfaces call this before admitting a turn. It uses _conversation_lookup and database insert/update operations.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat); 5 external calls (insert, update, workspace_tx, log, uuid4).


##### `SurfaceContext.admit`  (lines 579–602)

```
async def admit(self, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: This admits a user message into UFO’s turn queue and returns the turn id. It is the main write operation a surface performs after it has resolved a conversation and agent.

**Data flow**: It receives the conversation id, agent id, message body, optional idempotency key, optional context, and speaker member id. It passes all of that to the injected MemberAdmitter and returns the resulting turn id.

**Call relations**: Sample, Slack, UFO, and web surfaces call this when handling inbound chat or button interactions. Delivery details are handled by the core admission implementation.

*Call graph*: called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat).


##### `SurfaceContext.connect_url`  (lines 604–610)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: This starts a connect authorization flow for a terminal connect request as a specific member. It gives a surface a URL to send the user to.

**Data flow**: It receives a turn id and member id. It loads the installed connect flow, wraps it in a handoff helper, and returns an authorization URL; if connect is unavailable, it raises a request error.

**Call relations**: Slack interactions and web event rendering call this when a turn asks the user to connect an external service.

*Call graph*: called by 2 (interactive, _events); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.admitted_body`  (lines 612–637)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: This finds the message body that actually won an idempotent admission. It helps a surface know which of several repeated clicks or retries became the real answer.

**Data flow**: It receives an idempotency key. It first looks for a turn with that key and returns its inbound text; if not found, it looks for a queued inbound message with that key; if neither exists, it returns None.

**Call relations**: Slack interactive handling uses this after answer affordance races so only the winning answer changes the visible message.

*Call graph*: called by 1 (interactive); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 639–653)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: This returns the member who owns the conversation containing a turn. Live surfaces use it to prevent one member from streaming another member’s turn.

**Data flow**: It receives a turn id, joins the turn to its conversation inside the current workspace, and returns the conversation member id or None if the turn is unknown.

**Call relations**: The sample live admit path and the web stream route call this before allowing a live tail.

*Call graph*: called by 2 (_surface_live_admit, stream); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 655–672)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: This finds the newest turn in a conversation. It helps surfaces resume or inspect the most recent work in a thread.

**Data flow**: It receives a conversation id, queries turns in that conversation ordered by descending sequence, and returns the newest turn id or None.

**Call relations**: Slack participation logic and the UFO channel use this when they need the current turn for a known conversation.

*Call graph*: called by 2 (_participating_conversation, channel); 2 external calls (select, workspace_tx).


##### `SurfaceContext.tail`  (lines 674–677)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This exposes live frame streaming for one turn. A live surface uses it to send ongoing progress to the user over a held connection.

**Data flow**: It receives a turn id and optional resume cursor, then returns the async iterator produced by the injected TurnTailer.

**Call relations**: Debugger, sample, UFO, and web event streams call this. The real hub access stays behind the TurnTailer abstraction.

*Call graph*: called by 4 (_events, _surface_frames, channel, _events).


##### `SurfaceContext.spend_rollup`  (lines 679–683)

```
async def spend_rollup(self, window_seconds: int) -> SpendReport
```

**Purpose**: This reads recent workspace spending totals. Surfaces use it for spend or accounting views.

**Data flow**: It receives a time window in seconds, opens a workspace transaction, asks SpendRollup to read totals for this workspace, and returns a SpendReport.

**Call relations**: The sample live admit flow and web spend route call this for user-visible spending information.

*Call graph*: called by 2 (_surface_live_admit, spend); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 685–691)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This streams an uploaded or downloaded file into a conversation’s workspace folder before a turn runs. The sandbox can then see the file as part of its working directory.

**Data flow**: It receives a conversation id, relative path, and async byte chunks. It converts the path into a safe workspace blob key and streams the chunks into blob storage.

**Call relations**: Sample ingest and Slack file download code call this. It relies on workspace_key for path safety.

*Call graph*: calls 1 internal fn (workspace_key); called by 2 (_surface_ingest, _download_files).


##### `SurfaceContext.list_conversations`  (lines 693–742)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: This lists recent conversations in the workspace for read-only views such as a debugger. It includes conversations across all surfaces, not only the current one.

**Data flow**: It receives an optional limit. It queries conversations, joins member email and turn activity counts, orders by newest activity, and returns ConversationSummary objects.

**Call relations**: The debugger surface calls this to show a workspace conversation list.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 744–760)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: This lists the recent turns in one conversation in normal oldest-to-newest order. It gives debug views a durable record of what happened.

**Data flow**: It receives a conversation id and optional limit. It queries the newest matching rows, reverses them back into admission order, converts each row to a Turn record, and returns them.

**Call relations**: The debugger conversation-turns route calls this. It reuses _turn_query and _turn_record.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 1 (conversation_turns); 1 external calls (workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 762–812)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: This returns a detailed read view for one turn. It includes the turn, its accounting ledger rows, and any child turns created by subagents.

**Data flow**: It receives a turn id. It reads the turn, child turns, and ledger entries from the database; if the turn does not exist in this workspace, it returns None; otherwise it packages everything into TurnDetail.

**Call relations**: Debugger stream and turn routes call this. It reuses _turn_query, _turn_record, and LedgerEntry.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (stream, turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 814–825)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: This reads the durable transcript for a conversation if the conversation belongs to this workspace. The ownership check protects the unscoped blob store from leaking another tenant’s transcript.

**Data flow**: It receives a conversation id, checks ownership in the database, reads the transcript blob if present, decodes it, and returns a Conversation object or None.

**Call relations**: The debugger transcript route calls this. It uses _owned_conversation before transcript_key and decode.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_transcript); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 827–838)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: This lists the saved compaction record numbers for a conversation. Compaction records explain how old transcript windows were summarized.

**Data flow**: It receives a conversation id, checks ownership, lists blobs under that conversation’s compaction folder, extracts numeric indices, sorts them, and returns the numbers.

**Call relations**: The debugger compactions route calls this. It uses _owned_conversation as its access gate.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_compactions).


##### `SurfaceContext.read_compaction`  (lines 840–846)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: This reads one compaction record for a conversation, if allowed and present. It lets a debug view inspect how transcript history was compressed.

**Data flow**: It receives a conversation id and compaction index. It checks ownership, then asks the transcript helper to read that compaction record; if unauthorized or missing, it returns None.

**Call relations**: The debugger compaction-record route calls this. It combines _owned_conversation with read_compaction_record.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (compaction_record); 1 external calls (read_compaction_record).


##### `SurfaceContext.list_workspace_files`  (lines 848–862)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: This lists files in a conversation’s workspace folder. It shows the sandbox’s working files using safe relative paths instead of raw blob keys.

**Data flow**: It receives a conversation id, checks ownership, lists blobs under conversations/<id>/workspace/, strips that prefix, and returns WorkspaceFile records.

**Call relations**: The debugger workspace-files route calls this. It uses _owned_conversation before listing blob storage.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_files); 1 external calls (__init__).


##### `SurfaceContext.read_workspace_file`  (lines 864–875)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: This streams one file from a conversation’s workspace folder. It validates the path so callers cannot escape the workspace subtree.

**Data flow**: It receives a conversation id and relative path. It checks conversation ownership, converts the relative path into a safe blob key, checks that the blob exists, and returns a byte stream or None.

**Call relations**: The debugger workspace-file route calls this. It shares path validation with write_workspace_file through workspace_key.

*Call graph*: calls 2 internal fn (_owned_conversation, workspace_key); called by 1 (workspace_file).


##### `SurfaceContext.installation`  (lines 877–890)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: This reads a workspace’s installation id for another surface. Debug or metadata views use it to build links into the external surface where a conversation lives.

**Data flow**: It receives a peer surface name, queries the installation table for the current workspace and that surface, and returns the installation id or None.

**Call relations**: The debugger workspace metadata route calls this.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 892–902)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: This checks whether a conversation id belongs to the current workspace. It is a small but important guard before reading unscoped blobs.

**Data flow**: It receives a conversation id, queries the conversation table scoped to this workspace, and returns true if a row exists.

**Call relations**: Transcript, compaction, workspace file listing, and workspace file reading methods call this before touching blob storage.

*Call graph*: called by 5 (list_compactions, list_workspace_files, read_compaction, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 904–922)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: This builds the common SQL query shape for reading turn rows. It keeps list and detail views using the same selected fields.

**Data flow**: It takes no input beyond the context and returns a SQL select object containing all durable turn fields needed to rebuild a Turn record.

**Call relations**: SurfaceContext.list_turns and SurfaceContext.turn_detail call this before adding their own filters and ordering.

*Call graph*: called by 2 (list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 924–942)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: This converts a database row into the project’s typed Turn record. It also parses stored JSON fields into their richer model objects.

**Data flow**: It receives a database row. It copies scalar fields, validates context JSON into TurnContext when present, validates terminal JSON into TerminalFrame when present, and returns a Turn.

**Call relations**: SurfaceContext.list_turns and SurfaceContext.turn_detail use this to turn raw database rows into records that callers can consume.

*Call graph*: called by 2 (list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.bind`  (lines 963–968)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: This lets a tool bind an installation only for surfaces it declared in its manifest. It prevents a tool from secretly registering unrelated surface names.

**Data flow**: It receives a surface name and installation id. It checks the surface is declared, reads the current workspace id, and delegates the database write to _bind_surface_installation.

**Call relations**: Tool code uses this scoped access object. The actual binding rules are shared with SurfaceContext.bind_installation.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 980–990)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: This resolves an external installation id to the workspace that owns it before a request is bound to any workspace. It is the first gate for shared surface ingress.

**Data flow**: It receives an installation id, searches the owner-level installation table for the configured surface, and returns the workspace id or None.

**Call relations**: Slack’s workspace resolver calls this while authenticating incoming Slack requests.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 992–1002)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: This opens a sealed credential authorization before the request has been bound to a workspace. It is useful for OAuth callbacks whose browser state carries the workspace claim.

**Data flow**: It receives a sealed string. If no credential store exists or the seal is invalid, it returns None; otherwise it returns the decoded credential request state.

**Call relations**: Slack’s workspace resolver calls this during OAuth-style pre-binding flows.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 1004–1020)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: This reads a declared credential slot for a specific workspace during pre-binding authentication. It still checks that the workspace exists and that the slot was declared.

**Data flow**: It receives a workspace id and slot name. It rejects undeclared slots or missing credential storage, temporarily binds the workspace, verifies the workspace row exists, and returns the credential value.

**Call relations**: Slack authentication uses this to read its signing secret before normal SurfaceContext binding is available.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceDeliveryError.__init__`  (lines 1034–1038)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: This creates a delivery error that may include a provider-requested retry delay. It lets a surface tell the poller, for example, to wait because the external API rate-limited it.

**Data flow**: It receives an error message and optional retry-after seconds. It rejects negative retry delays, stores the retry hint, and initializes the runtime error message.

**Call relations**: Slack posting code raises this. WritebackPoller._fail_or_retry recognizes it and schedules the next attempt accordingly.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 1077–1093)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for writebacks that are ready to be delivered or reclaimed. It captures the rule for pending rows, expired claims, and terminal turns.

**Data flow**: It receives the current time and returns a SQL boolean expression. The expression matches terminal turns whose writeback is pending with no active claim, or claimed but whose claim expired.

**Call relations**: writeback_workspaces.due and WritebackPoller._claim both use this so workspace discovery and row claiming agree on what is ready.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `writeback_workspaces`  (lines 1096–1131)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: This creates a rotating candidate reader for workspaces that have deliverable writebacks. It keeps the background poller from scanning everything every time.

**Data flow**: It initializes a cursor and returns an async candidates function. That function reads a bounded page of workspace ids with due writebacks and advances or wraps the cursor.

**Call relations**: WritebackPoller receives this candidate reader and calls it from run or drain. Internally it uses owner_candidates around the nested due query.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 1103–1117)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested helper builds the owner-level query for the next page of workspaces with due writebacks. It is the query body used by the rotating candidate reader.

**Data flow**: It reads the current time and cursor, selects workspace ids with due writebacks, groups them, orders them, limits the page, and applies the cursor if present.

**Call relations**: writeback_workspaces passes this helper to owner_candidates, which turns it into an async read function used by candidates.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 1121–1129)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This nested async function returns the next batch of workspace ids for the poller. It wraps back to the beginning after reaching the end.

**Data flow**: It calls the owner read function. If no ids are found and the cursor was not at the start, it resets the cursor and tries again; when ids are found, it stores the last id as the new cursor.

**Call relations**: WritebackPoller.run and WritebackPoller.drain call this through the poller’s candidates field.


##### `_WritebackDeliveryFailed.__init__`  (lines 1139–1142)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: This wraps an exception from either the posting phase or attachment phase of durable delivery. It preserves which phase failed so retry logging and handling are clearer.

**Data flow**: It receives the phase name and original exception. It stores both and sets the error message to the exception text or exception class name.

**Call relations**: WritebackPoller._deliver_claimed creates this when a surface post or attach callback raises. WritebackPoller._deliver then catches it.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 1165–1198)

```
async def run(self) -> None
```

**Purpose**: This is the continuous background loop for durable surface delivery. It repeatedly finds workspaces with due writebacks and starts bounded drain tasks for them.

**Data flow**: It creates a concurrency semaphore and tracks in-flight workspace tasks. Each loop cleans finished tasks, asks for more candidate workspaces when there is room, starts drain tasks, sleeps briefly, and cancels outstanding work on shutdown.

**Call relations**: A background service calls this for normal operation. It delegates real per-workspace work to _drain_workspace and logs task failures.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 1200–1209)

```
async def drain(self) -> None
```

**Purpose**: This performs one finite drain pass instead of running forever. It is useful for tests, commands, or controlled maintenance runs.

**Data flow**: It reads one batch of candidate workspace ids, drains them concurrently under a semaphore, gathers results, and raises an ExceptionGroup if any workspace drain failed.

**Call relations**: It calls _drain_workspace just like run does, but returns after the current candidates are processed.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 1211–1229)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: This processes a batch of claimed writebacks for one workspace. It binds the workspace boundary, claims rows, keeps their claims alive, and delivers each one.

**Data flow**: It receives a workspace id and semaphore. Inside the semaphore and workspace context, it claims rows, starts renewal tasks for each claim, delivers rows one by one, then cancels and gathers renewal tasks.

**Call relations**: WritebackPoller.run and drain call this. It coordinates _claim, _renew_claim, and _deliver.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 1231–1264)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: This marks a small batch of due writeback rows as claimed by this worker. Claiming is the lock that stops other pollers from working the same delivery at the same time.

**Data flow**: It receives a workspace id, finds due writebacks for terminal turns in creation order, updates them to claimed with this worker id and an expiry time, and returns the claimed turn ids plus any existing reply reference and last error.

**Call relations**: _drain_workspace calls this before delivery. It uses _writeback_due so it matches the workspace candidate logic.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 1266–1298)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This wraps one writeback delivery with timing, logging, and retry handling. It is the outer safety layer around actual post-and-attach work.

**Data flow**: It receives workspace id, turn id, optional existing reply reference, and the renewal task. It tries delivery with a live lease; if the claim is lost it logs that, if delivery fails it updates retry or failure state, and if it succeeds it logs success.

**Call relations**: _drain_workspace calls this for each claimed row. It delegates active delivery to _deliver_with_lease and failure updates to _fail_or_retry.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 1300–1325)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This races actual delivery against the claim-renewal task. It ensures delivery only proceeds while this worker still owns the claim.

**Data flow**: It receives delivery identifiers and a renewal task. It starts _deliver_claimed, waits until either delivery or renewal ends, returns on successful delivery, or raises if renewal stopped or failed; finally it cancels leftover tasks.

**Call relations**: _deliver calls this. It coordinates _deliver_claimed with _renew_claim.

*Call graph*: calls 1 internal fn (_deliver_claimed); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 1327–1352)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: This performs the actual durable delivery for a claimed writeback. It builds the reply data, finds the surface, posts the reply if needed, uploads attachments, and marks the row delivered.

**Data flow**: It receives workspace id, turn id, and optional reply reference. It builds a Writeback object, looks up the surface spec, skips delivery if no durable handlers exist, creates a SurfaceContext, calls post when no reply reference exists, records that reference, calls attach, and marks delivered.

**Call relations**: _deliver_with_lease calls this. It calls _build, _record_ref, _mark_delivered, and wraps surface callback errors as _WritebackDeliveryFailed.

*Call graph*: calls 4 internal fn (_build, _mark_delivered, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 1354–1372)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: This keeps a claimed writeback lease alive while slow external delivery is happening. It is like periodically saying, “I am still working on this.”

**Data flow**: It receives a turn id. In a loop, it sleeps, extends the claim expiry for rows still claimed by this worker, and raises _WritebackClaimLost if the row is no longer owned by this worker.

**Call relations**: _drain_workspace starts one renewal task per claimed row. _deliver_with_lease watches that task while delivery runs.

*Call graph*: called by 1 (_drain_workspace); 6 external calls (__init__, sleep, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 1374–1430)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: This constructs the Writeback object that a durable surface receives. It gathers the final turn outcome, conversation routing key, surface name, and shared artifacts.

**Data flow**: It receives a turn id. It queries the turn’s terminal frame and conversation data, reads artifact rows, validates the terminal frame, converts artifact rows to SharedArtifact objects, and returns the Writeback plus surface name.

**Call relations**: _deliver_claimed calls this before choosing the surface and calling its post or attach handlers.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 1432–1444)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: This records the external reply reference returned after posting. Saving it before attachments lets recovery avoid reposting the main reply after a crash.

**Data flow**: It receives a turn id and reply reference. It updates the writeback row only if it is still claimed by this worker; if no row is updated, it raises _WritebackClaimLost.

**Call relations**: _deliver_claimed calls this after a successful surface post and before attachment upload.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 1446–1463)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: This marks a claimed writeback as fully delivered. It clears the claim so the row is no longer picked up for retry.

**Data flow**: It receives a turn id. It updates the writeback row to delivered only if this worker still owns the claim, clears claimant and expiry fields, and raises _WritebackClaimLost if ownership changed.

**Call relations**: _deliver_claimed calls this after successful post and attachment work, or when there is no registered durable delivery to perform.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 1465–1514)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: This decides what to do after a durable delivery failure: retry later or give up permanently. It honors a provider retry hint when present but does not allow rows to be delayed forever.

**Data flow**: It receives a turn id and wrapped delivery failure. It computes retry timing, truncates the stored error message, checks whether the writeback is too old, updates the row to pending or failed while clearing the claim, and returns the outcome, stored error, and next attempt time.

**Call relations**: _deliver calls this when _deliver_with_lease reports a _WritebackDeliveryFailed. It recognizes SurfaceDeliveryError retry hints from surface code.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### Memory inspection surface
The memory extension exposes a small operator web surface for viewing stored workspace memory records.

### `extensions/memory/ufo_ext_memory/surface.py`

`io_transport` · `request handling`

This file is the “front counter” for the memory explorer. The explorer is a read-only screen where an operator can inspect what the memory system has saved for a particular workspace. Without this file, the memory data might still exist, but there would be no simple operator web surface for viewing it.

It serves two things. First, it loads and returns a static HTML page from `static/memory.html`. That page is the browser interface. Second, it provides an API endpoint, `api/memories`, which returns the workspace’s memory records as JSON, a common web data format.

The important safety idea is workspace scoping. A workspace is like a labeled filing cabinet. When the operator session has already been checked and tied to a workspace, this file reads only from that cabinet. To do that, it creates an `ExtensionContext` for the memory extension and opens the extension’s own scoped store. That means it reads the memory extension’s table, not some unrelated core table, while still respecting the already-bound workspace boundary.

At the bottom, `ROUTES` connects web requests to the right behavior: show the page, bind the operator session, or return the memory list.

#### Function details

##### `app_page`  (lines 27–30)

```
async def app_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns the memory explorer web page to the browser. It is used when an operator visits the surface’s main page.

**Data flow**: It receives the current surface context and the incoming web request. It checks whether the HTML file was successfully loaded when the module started. If the file is missing, it raises an error so the problem is visible; otherwise it wraps the HTML text in an HTTP response and sends it back to the browser.

**Call relations**: This function is connected to the main `GET` route for the surface. When the browser asks for the memory explorer page, the routing table sends the request here, and this function hands back an `HTMLResponse` containing the already-loaded page.

*Call graph*: 1 external calls (HTMLResponse).


##### `memories`  (lines 33–42)

```
async def memories(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function returns every memory item for the currently bound workspace as JSON. It gives the browser page the data it needs to display what the memory system can recall from.

**Data flow**: It receives the surface context, which includes the workspace identity, and the web request. It builds a memory-extension context with a scoped store, then asks the memory store inventory function for records in that workspace. Each returned memory item is converted into JSON-friendly data, and the list is returned as a JSON HTTP response.

**Call relations**: This function is connected to the `GET api/memories` route. After the operator session has already established the workspace boundary, this function creates the extension-side transaction and calls `ufo_ext_memory.store.inventory` to fetch the records. It then hands the browser a `JSONResponse` so the page can render the memory inventory.

*Call graph*: 5 external calls (__init__, __init__, __init__, JSONResponse, inventory).

## 📊 State Registers Touched

- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-identity-and-session-tokens` — The identities, bearer tokens, gateway tokens, operator sessions, and other passes that prove who is allowed in.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-live-event-stream` — The live progress channel that lets clients attach, resume, and receive streamed turn updates.
- `reg-inbound-surface-state` — The stored inbound messages and surface delivery keys that connect Slack, web, terminal, and other fronts to conversations.
- `reg-artifact-blob-store` — The shared file and blob storage used for uploaded content, generated artifacts, and token-protected downloads.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-oauth-consent-flow-state` — Short-lived OAuth/provider consent attempt state linking redirects, callbacks, workspace/member identity, and provider account checks until a credential is finalized.
- `reg-page-alert-watch-state` — The stored watches/subscriptions and notification targets used to replay source page changes into chat alerts.
- `reg-action-proposal-state` — Durable non-governance proposals created by agents or tools for later user/operator review, approval, rejection, or commit.
