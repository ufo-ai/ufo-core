# Surface ingress, admission, and user-facing request handling  `stage-6`

This stage is the system’s front door. It is used when a person talks to UFO from Slack, a web browser, or the terminal, and when a scheduled or uploaded item needs to enter the same flow. Its job is to turn outside events into the core system’s standard pieces: members, conversations, messages, files, and agent turns.

The Slack surface translates Slack messages, buttons, file uploads, installs, and replies into UFO actions. The terminal surface receives HTTP messages from the shell client and streams simple text updates back. The web surface lets a signed-in browser send chat messages, watch live replies, and see workspace spending. The shared surface bridge gives these interfaces trusted ways to identify users, admit messages, read credentials, stream progress, and send final replies.

The admission code is the gatekeeper. It checks permission, avoids duplicate messages, links the message to the right conversation and agent, saves it safely, and queues work only when appropriate. The agents file exposes the workspace agent settings. The artifacts endpoint securely serves shared files using signed download tokens.

## Files in this stage

### External surface adapters
User-facing Slack, terminal, and web transports receive outside events and translate them into core surface interactions.

### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `request handling, install flow, live turn updates, reply delivery`

This file is the bridge between Slack and the rest of ufo. Without it, Slack messages could not safely enter the system, the agent could not answer in the right thread, buttons would not work, and files would not move between Slack and the workspace.

It does several jobs. First, it verifies that incoming HTTP requests really came from Slack by checking Slack’s signature, like checking the seal on an envelope before opening it. Then it decides which workspace the request belongs to, finds the installed Slack bot identity, and reduces Slack’s large event payload into a small internal “inbound message” shape. It admits that message as a ufo turn, optionally adding useful earlier Slack context and streaming any attached Slack files into the workspace.

While the turn runs, it starts background tasks that update Slack’s thread status and, for long turns, post progress updates so the user knows the agent is still working. When the core system finishes a turn, this file formats the answer for Slack, including question buttons, connect buttons, metadata footers, oversize artifact links, and uploaded files.

It also covers installation. OAuth installs and bring-your-own Slack app setups both end with a stored bot token and a stored identity record, so future Slack events can be routed and trusted.

#### Function details

##### `_env_signing_secret`  (lines 138–142)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from the process environment. This is the fallback secret used when a workspace has not stored its own Slack signing secret.

**Data flow**: It reads one environment variable. If the variable is present and not empty, it returns that string; otherwise it returns nothing.

**Call relations**: Workspace-specific secret lookup functions call this when their own credential lookup cannot provide a secret.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 145–152)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the Slack signing secret for a request after the workspace is already known. It prefers the workspace’s private credential, then falls back to the deploy-wide environment secret.

**Data flow**: It receives a surface context, asks it for the Slack signing-secret credential, and returns that value. If the credential slot is unset, it returns the environment secret instead.

**Call relations**: The event and interactivity request handlers call this before accepting Slack POST bodies, because signature checking must happen before trusting the payload.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 155–163)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret during early workspace resolution, before the normal workspace context is bound. It lets the shared Slack endpoint verify a request for the workspace hinted by Slack’s team id.

**Data flow**: It receives a shared auth helper and a workspace id. It tries to read that workspace’s stored signing secret, falls back to the environment secret if unset, and returns nothing if the workspace is unknown.

**Call relations**: Workspace resolution calls this after it maps a Slack team id to a workspace, so it can verify the raw request before handing control to that workspace.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 166–170)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client id required to build and complete Slack install flows. It fails loudly if the deploy is not configured for OAuth installs.

**Data flow**: It reads the client-id environment variable. A valid value is returned; a missing value becomes a runtime error.

**Call relations**: The OAuth code exchange uses this when asking Slack to trade the temporary install code for a bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 173–177)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret required to complete an OAuth install. This secret proves to Slack that the callback belongs to the configured app.

**Data flow**: It reads the client-secret environment variable. If absent, it raises an error instead of attempting a broken install.

**Call relations**: The OAuth exchange calls this alongside the client id when contacting Slack’s OAuth API.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 180–182)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the exact callback URL Slack should redirect to after an install. Slack requires this URL to match the app configuration.

**Data flow**: It receives the public base URL of the deploy, trims any trailing slash, appends the Slack surface OAuth path, and returns the full URL.

**Call relations**: The OAuth callback uses this same URL during code exchange, matching the URL used when the install link was made.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 185–197)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Creates the “Add to Slack” link for an owner to install the app. The link includes requested permissions and a sealed state value that ties the install back to one workspace.

**Data flow**: It receives a client id, redirect URL, and state string. It URL-encodes those with the Slack bot scopes and returns Slack’s authorization URL.

**Call relations**: Other install setup code can use this helper to send the owner to Slack; the later OAuth callback reads back the same state.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 201–203)

```
def __init__(self, error: str)
```

**Purpose**: Creates a clear error object for Slack identity problems, such as a bad token or malformed Slack response.

**Data flow**: It receives an error message, stores it on the exception, and initializes the normal runtime error text with the same message.

**Call relations**: Identity proving and OAuth exchange raise this when Slack cannot prove the team id and bot user id safely.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `identity_blob_key`  (lines 217–218)

```
def identity_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage path for a workspace’s Slack identity record. This keeps each workspace’s Slack identity separated inside the shared blob store.

**Data flow**: It receives a workspace id and formats it into a fixed blob path for that workspace’s Slack surface identity.

**Call relations**: Identity reads, identity writes, and OAuth callback storage all use this path so they agree on where the record lives.

*Call graph*: called by 3 (resolve, oauth_callback, read_identity).


##### `bot_token_fingerprint`  (lines 221–222)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a one-way fingerprint of a Slack bot token. This lets the system tell whether a stored identity belongs to the current token without storing the token in the identity record.

**Data flow**: It receives the bot token string, hashes it with SHA-256, and returns the hexadecimal hash text.

**Call relations**: Identity reading compares fingerprints, while identity proving and OAuth install writing stamp new identity records with the current token fingerprint.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 225–239)

```
async def read_identity(blob: BlobStore, workspace_id: UUID, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack team and bot-user identity for a workspace, but only if it matches the current bot token. This prevents stale identity data from routing events after a reinstall.

**Data flow**: It receives a blob store, workspace id, and bot token. It checks for the identity blob, parses it, compares its token fingerprint, and returns the identity or nothing.

**Call relations**: The normal identity lookup and the bring-your-own-app resolver call this before doing slower or riskier identity work.

*Call graph*: calls 4 internal fn (exists, get, bot_token_fingerprint, identity_blob_key); called by 2 (resolve, _identity).


##### `_identity`  (lines 242–247)

```
async def _identity(ctx: SurfaceContext) -> SlackIdentity | None
```

**Purpose**: Gets the current workspace’s Slack identity using its stored bot token. If Slack is not connected yet, it returns nothing.

**Data flow**: It asks the context for the Slack bot token. If present, it reads and validates the matching identity record from blob storage.

**Call relations**: Incoming events and button clicks call this before they trust Slack team ids or bot-user ids.

*Call graph*: calls 2 internal fn (credential, read_identity); called by 2 (ingest, interactive).


##### `SlackIdentityResolver.resolve`  (lines 261–269)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Ensures a bring-your-own Slack app has a stored identity record. It reuses a valid cached identity or proves the token with Slack and saves the result.

**Data flow**: It reads the existing identity. If missing or stale, it calls Slack to prove the token, writes the identity blob, and returns the identity.

**Call relations**: Background identity proof and setup paths use this so event handling can later route messages without calling Slack every time.

*Call graph*: calls 3 internal fn (_prove, identity_blob_key, read_identity).


##### `SlackIdentityResolver._prove`  (lines 271–296)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack what workspace and bot user a pasted bot token belongs to. This is the safety check for bring-your-own-app installs.

**Data flow**: It sends the token to Slack’s auth.test endpoint, checks that Slack says ok, validates the returned ids, and returns a Slack identity object.

**Call relations**: The resolver calls this only when no valid stored identity exists.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 302–315)

```
def _prove_identity_in_background(ctx: SurfaceContext) -> None
```

**Purpose**: Starts a background identity proof for a workspace that has credentials but no usable identity yet. It avoids making the current Slack request wait for the proof.

**Data flow**: It checks an in-process task table. If no proof is already running for the workspace, it creates one and registers cleanup when it finishes.

**Call relations**: Event and interactivity handlers call this when they cannot yet load identity, so a later Slack retry may succeed.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 2 (ingest, interactive); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 311–313)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished identity-proof task from the in-process tracking table. This allows a later request to retry proof if needed.

**Data flow**: It receives the completed task, checks it is still the current tracked task for that workspace, and deletes the table entry.

**Call relations**: It is attached as the completion callback for the task started by the background identity proof helper.


##### `_run_identity_proof`  (lines 318–325)

```
async def _run_identity_proof(ctx: SurfaceContext) -> None
```

**Purpose**: Performs the actual background identity proof and logs failures. It keeps a bad or unreachable Slack token from crashing request handling.

**Data flow**: It reads the workspace’s bot token, creates a resolver, asks it to resolve identity, and catches expected and unexpected errors.

**Call relations**: The background launcher schedules this task when ingest or interactivity finds missing identity.

*Call graph*: calls 1 internal fn (credential); called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `url_verified_blob_key`  (lines 328–334)

```
def url_verified_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage path for the marker that says Slack successfully reached this deploy with a valid signature. This is used as a connection health signal.

**Data flow**: It receives a workspace id and returns the fixed blob path for that workspace’s Slack URL verification marker.

**Call relations**: The URL-verification marker writer uses this path after a signed Slack request is accepted.

*Call graph*: called by 1 (_mark_url_verified).


##### `signing_secret_fingerprint`  (lines 337–340)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a one-way fingerprint of the signing secret used to verify Slack requests. This lets setup distinguish a current verification from an old one after secret rotation.

**Data flow**: It receives the signing secret string, hashes it with SHA-256, and returns the hash text.

**Call relations**: The URL-verification marker writer stores this fingerprint in the marker.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 354–381)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s temporary OAuth code for the workspace’s bot token and bot identity. This is the heart of the preferred Slack install path.

**Data flow**: It receives the code and redirect URL, sends them with app credentials to Slack, validates the returned token and ids, and returns a Slack install record.

**Call relations**: The OAuth callback calls this after it has verified the sealed install state.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `SlackConversationSearch.run`  (lines 461–475)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations visible to the bot and returns those matching a user’s query. It supports channels and direct messages by matching names, topics, purposes, and people.

**Data flow**: It lists conversations, resolves people for DMs, converts raw Slack records into smaller conversation objects, filters them by the query, and returns matches plus a truncation flag.

**Call relations**: It coordinates the helper methods that page Slack conversations, resolve members, and normalize each result.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 477–496)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches a bounded number of Slack conversation-list pages. The bound prevents a huge or malformed workspace from making the search run forever.

**Data flow**: It repeatedly sends Slack list requests with a cursor, gathers channel records, stops when no cursor remains or the page limit is reached, and reports whether it stopped early.

**Call relations**: The search runner calls this first to get the raw conversations it may later filter.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 498–506)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list request. It selects active channels and DMs and includes the cursor when continuing a page.

**Data flow**: It receives a cursor string, creates the request parameter dictionary, adds the cursor if present, and returns it.

**Call relations**: The listing helper calls this for each Slack page request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 508–511)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a list response. If Slack omits or malforms it, the search treats the list as finished.

**Data flow**: It receives the parsed Slack response, looks inside response metadata, and returns a cursor string or an empty string.

**Call relations**: The listing helper uses this after each page to decide whether to request another page.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 513–541)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves the human labels for DM and group-DM participants. This makes searches like “DM with Alice” possible even though DMs do not have channel names.

**Data flow**: It scans listed conversations for DMs, fetches member ids within a cap, looks up each user once, formats labels, and returns labels by conversation id plus a capped flag.

**Call relations**: The main search runner uses these labels when building searchable conversation objects.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 543–550)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies one raw Slack conversation as a public channel, private channel, group DM, or one-to-one DM.

**Data flow**: It reads Slack boolean flags from the raw conversation and returns a simple kind string.

**Call relations**: Conversation listing helpers use this classification when deciding whether to fetch members and when building final conversation objects.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 552–566)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets the member ids for a DM-style conversation. For a one-to-one DM it can read the user directly; for a group DM it asks Slack for members.

**Data flow**: It receives the Slack client, raw conversation, and conversation id. It returns a tuple of member id strings, or an empty tuple when unavailable.

**Call relations**: The people resolver calls this before looking up user display labels.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 568–573)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Chooses the best human-readable label for a Slack user. It prefers name plus email, then either one, then the raw Slack user id.

**Data flow**: It receives an optional Slack user record and a fallback user id. It returns one display string.

**Call relations**: The people resolver uses this to turn user lookups into searchable DM participant text.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 575–592)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Turns one raw Slack conversation object into the smaller shape used by search results. Invalid raw records are ignored.

**Data flow**: It checks the raw object and id, extracts name, kind, people, purpose, topic, and membership flag, and returns a SlackConversation or nothing.

**Call relations**: The main search runner calls this for each listed Slack record before applying the query filter.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 594–596)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely extracts Slack’s nested purpose or topic text field. Slack stores these as objects with a value field.

**Data flow**: It receives a field object, reads its value if the shape is right, and otherwise returns an empty string.

**Call relations**: Conversation normalization uses this for purpose and topic text.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 723–738)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a Slack HTTP request is authentic and recent. This protects the system from forged or replayed Slack events.

**Data flow**: It reads timestamp and signature headers, rejects missing or stale values, recomputes Slack’s HMAC signature from the raw body and secret, and raises if it does not match.

**Call relations**: Workspace resolution, event ingest, and button interactivity all call this before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 745–764)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw Slack request body with a size limit. The raw bytes are needed for signature verification, so they must be preserved exactly.

**Data flow**: It checks request state for cached bytes, otherwise streams chunks from the request, stops if the limit is exceeded, caches the bytes or overflow marker, and returns the bytes.

**Call relations**: All Slack request entry points use this before parsing the body or verifying the signature.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 767–776)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack’s URL verification handshake and extracts the challenge string Slack expects back.

**Data flow**: It parses the raw body as JSON, checks for type url_verification, and returns the challenge string or nothing.

**Call relations**: Workspace resolution and ingest use this to answer Slack setup probes without treating them as real events.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 779–796)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Extracts a Slack team id from an untrusted request body. This is only a hint used to find the possible workspace before signature verification.

**Data flow**: It tries JSON first and Slack form payload JSON second, looks for team_id or team.id, validates the id pattern, and returns it or nothing.

**Call relations**: Workspace resolution uses this hint to choose which stored signing secret should verify the request.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 799–800)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Turns a Slack team id into the installation key used by the core surface registry. This is how one Slack workspace maps to one ufo workspace.

**Data flow**: It receives the team id and prefixes it with a fixed label.

**Call relations**: OAuth installation binding and request workspace lookup both use this same key format.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 803–841)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace a Slack request belongs to before normal handling begins. It handles OAuth callbacks, signed Slack events, interactivity, and URL verification probes.

**Data flow**: It examines the request method and body, opens sealed OAuth state when present, echoes URL verification when possible, maps Slack team id to workspace, verifies the signature, and returns a workspace id, response, or nothing.

**Call relations**: This is the shared front-door resolver called before route handlers such as ingest, interactive, and OAuth callback run.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 844–847)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state belongs to a Slack OAuth install. This prevents a different credential flow from being accepted as a Slack install callback.

**Data flow**: It receives decoded credential claims and returns true only when the marker and requested slot match the Slack bot-token install flow.

**Call relations**: Workspace resolution and the OAuth callback both use this as a guard around sealed state.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 850–855)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the internal conversation key for a Slack message. DMs are keyed by channel, while channel threads are keyed by channel plus root timestamp.

**Data flow**: It receives the Slack channel, root timestamp, and DM flag, then returns either the channel id or a channel:timestamp key.

**Call relations**: Inbound event conversion uses this key before admitting messages into core conversations.

*Call graph*: called by 1 (_to_inbound).


##### `slack_message_addressed`  (lines 858–865)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly addressed to the agent. Direct messages always count; channel messages count when they are app mentions or contain the bot mention.

**Data flow**: It reads the event type, DM flag, bot user id, and text, then returns a boolean.

**Call relations**: Inbound conversion uses this to ignore ambient channel traffic unless it is part of an already active agent thread.

*Call graph*: called by 1 (_to_inbound).


##### `slack_reply_body`  (lines 868–918)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool=False) -> bytes
```

**Purpose**: Builds the JSON body for a Slack chat.postMessage reply. It balances rich Slack blocks with Slack’s size limits and falls back to plain text when needed.

**Data flow**: It receives channel, thread, reply text, optional metadata, and optional action blocks. It creates block or text JSON, checks byte limits, and returns encoded bytes or raises for oversize text.

**Call relations**: Final reply posting and interim progress posting use this to send Slack-compatible message bodies.

*Call graph*: called by 2 (_post, post); 1 external calls (dumps).


##### `_mrkdwn_section`  (lines 921–922)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a Slack section block containing Markdown-style text. It trims the text to Slack’s section limit.

**Data flow**: It receives text and returns a small dictionary shaped as a Slack Block Kit section.

**Call relations**: Question rendering uses this repeatedly for titles, questions, and option lists.

*Call graph*: called by 1 (slack_ask_blocks).


##### `slack_ask_blocks`  (lines 925–977)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question as Slack Block Kit blocks. Simple single-choice questions become buttons; richer questions become readable text that users answer in the thread.

**Data flow**: It receives an optional ask-user object. If present, it builds title, question, option, and button blocks with bounded text and returns them; otherwise it returns nothing.

**Call relations**: Final reply posting calls this when a turn ends by asking the user something.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (post).


##### `slack_connect_blocks`  (lines 980–1001)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a connection request as a Slack button. The button lets a member open a private authorization flow without exposing secrets in Slack.

**Data flow**: It receives an optional connect request and turn id. If present, it returns an action block whose button value carries the turn id; otherwise it returns nothing.

**Call relations**: Final reply posting uses this when the turn asks the user to connect an outside service.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1004–1008)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required non-empty string field from a Slack payload. It gives clear errors when Slack data is missing where the code cannot continue.

**Data flow**: It receives a mapping and field name, returns the string value if valid, or raises a value error.

**Call relations**: Inbound event conversion and interactivity click parsing use this for required Slack ids and timestamps.

*Call graph*: called by 2 (_to_click, _to_inbound).


##### `_inbound_files`  (lines 1011–1023)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts usable file attachments from a Slack message event. It ignores hidden or tombstoned files and limits how many files one message can bring in.

**Data flow**: It reads the event’s files list, keeps up to the configured maximum with a name and private download URL, and returns inbound file records.

**Call relations**: Inbound conversion attaches these records so ingest can stream them into the workspace before admitting the turn.

*Call graph*: called by 1 (_to_inbound); 1 external calls (__init__).


##### `oauth_callback`  (lines 1026–1070)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the Slack OAuth install after the owner returns from Slack. It stores the bot token, binds the Slack team to the workspace, and writes the bot identity.

**Data flow**: It reads query parameters, validates sealed state, exchanges the code with Slack, binds the installation id, fulfills the credential request, writes identity metadata, and returns a simple HTML page.

**Call relations**: This is the route handler for Slack’s OAuth redirect and is paired with the earlier authorize URL.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _install_page, _is_install_state, bot_token_fingerprint, identity_blob_key, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 1 external calls (__init__).


##### `_install_page`  (lines 1073–1080)

```
def _install_page(message: str, status: int) -> Response
```

**Purpose**: Creates a small HTML page shown after an install attempt. It escapes the message so Slack or user-provided text cannot become HTML.

**Data flow**: It receives message text and an HTTP status, builds an HTML response, and returns it.

**Call relations**: The OAuth callback uses this for success, cancellation, validation failure, and Slack exchange failure pages.

*Call graph*: called by 1 (oauth_callback); 2 external calls (escape, Response).


##### `_mark_url_verified`  (lines 1086–1100)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this workspace with a currently valid signing secret. This helps setup know the Slack app’s request URL is truly connected.

**Data flow**: It fingerprints the secret, skips duplicate writes in the current process, writes a timestamped marker to blob storage, and logs but ignores write failures.

**Call relations**: Event ingest and interactivity call this after successful signature verification.

*Call graph*: calls 2 internal fn (signing_secret_fingerprint, url_verified_blob_key); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1103–1165)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Events API requests and turns eligible Slack messages into ufo turns. This is the main inbound-message path.

**Data flow**: It reads and verifies the raw request, handles URL verification, loads identity, filters and converts the event, fetches sender and context, resolves the member, downloads files, admits the turn, and starts live status and progress tasks.

**Call relations**: Slack calls this route for events; it hands accepted work to the core surface context and then returns Slack’s quick ok response.

*Call graph*: calls 19 internal fn (admit, conversation_for, credential, _ambient_context, _ctx_signing_secret, _download_files, _files_note, _identity, _mark_url_verified, _prove_identity_in_background (+9 more)); 4 external calls (gather, loads, JSONResponse, Response).


##### `_author_is_foreign`  (lines 1168–1175)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages from outside the connected Slack workspace in shared Slack Connect channels. Those external authors are skipped because the app cannot safely resolve them as workspace members.

**Data flow**: It compares source/user team fields on the event with the bound team id and returns true when they differ.

**Call relations**: Inbound conversion uses this before admitting any message.

*Call graph*: called by 1 (_to_inbound).


##### `_to_inbound`  (lines 1178–1214)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Reduces a Slack event payload to the small internal Inbound shape, or rejects it as irrelevant. This is where bot messages, unrelated channel chatter, and unsupported subtypes are filtered out.

**Data flow**: It reads the event, checks type, sender, team, DM/thread status, addressability, conversation participation, text, files, and ids, then returns an Inbound record or nothing.

**Call relations**: The ingest handler calls this after signature and identity checks, before doing member resolution or turn admission.

*Call graph*: calls 6 internal fn (_author_is_foreign, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 1 external calls (__init__).


##### `_participating_conversation`  (lines 1217–1228)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread is already an active ufo conversation. A thread counts only after it has at least one admitted turn.

**Data flow**: It looks up the conversation by queue key, then checks for a latest turn. It returns the conversation id only when both exist.

**Call relations**: Inbound conversion uses this to admit unmentioned replies only in threads where the agent is already participating.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1231–1261)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches a Slack user’s display name, confirmed email, and timezone. This enriches turn context and helps link first-time DM speakers to members.

**Data flow**: It calls Slack users.info with a short timeout, validates the response, keeps email only if Slack says it is confirmed, and returns a SlackUser or nothing.

**Call relations**: Ingest, interactivity, and conversation search use this when they need human user details.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_people, ingest, interactive); 2 external calls (__init__, AsyncClient).


##### `_turn_context`  (lines 1264–1278)

```
def _turn_context(sender: SlackUser | None) -> TurnContext
```

**Purpose**: Builds the sender context attached to an admitted turn. This lets the agent know who spoke and, when valid, what timezone Slack reports.

**Data flow**: It receives an optional SlackUser, formats name/email into a sender string, validates timezone through TurnContext, and drops invalid timezone values.

**Call relations**: Ingest passes this context into core turn admission.

*Call graph*: called by 1 (ingest); 1 external calls (__init__).


##### `_resolve_member`  (lines 1281–1299)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member when possible. It uses existing links first, then a Slack-confirmed email to link or join a teammate.

**Data flow**: It receives context, Slack user id, DM flag, and optional sender details. It returns a member id, returns nothing for unresolved channel speakers, or raises when a DM speaker cannot be resolved due to missing user info.

**Call relations**: Ingest and answer-button interactivity call this before admitting turns with a speaker.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 2 (ingest, interactive); 1 external calls (__init__).


##### `_ambient_context`  (lines 1302–1349)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> str
```

**Purpose**: Fetches earlier Slack messages that matter for the first turn in a conversation. This gives the agent context from a thread or recent channel messages before it was directly addressed.

**Data flow**: It decides whether context is needed, chooses Slack history or thread replies, fetches one bounded page with a short timeout, and returns a formatted digest or empty string.

**Call relations**: Ingest prepends this digest to the first admitted channel turn when appropriate.

*Call graph*: calls 2 internal fn (_ambient_digest, _slack_ok); called by 1 (ingest); 1 external calls (AsyncClient).


##### `_ambient_digest`  (lines 1352–1389)

```
def _ambient_digest(messages: list[object], bot_user_id: str, header: str) -> str
```

**Purpose**: Formats fetched Slack messages into a bounded text digest for the agent. It keeps useful member messages while dropping bot messages and messages that directly mentioned the bot.

**Data flow**: It filters and timestamps messages, sorts them, trims very long digests while preserving the oldest anchor and newest lines, and returns header plus lines.

**Call relations**: Ambient context fetching calls this after Slack returns message history.

*Call graph*: called by 1 (_ambient_context); 1 external calls (fromtimestamp).


##### `_slack_download_host_ok`  (lines 1392–1394)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks that a private file download URL belongs to Slack. This prevents the bot token from being sent to an attacker-controlled host.

**Data flow**: It parses the URL hostname and returns true only for slack.com or a Slack subdomain.

**Call relations**: The streaming download helper calls this before attaching the authorization header.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 1397–1415)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a Slack private file download into chunks without buffering the whole file. It also enforces host safety and a maximum file size.

**Data flow**: It validates the host, opens an authorized HTTP stream, yields chunks, counts bytes, and raises if the file exceeds the inbound cap.

**Call relations**: File downloading passes this stream directly to workspace file writing.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 1427–1443)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack message attachments into the workspace for the conversation. Oversize files are skipped rather than partially written.

**Data flow**: It assigns safe unique inbox filenames, streams each Slack file into the workspace, tracks delivered and skipped names, and returns that summary.

**Call relations**: Ingest calls this before admitting the turn so the agent can see saved attachment paths in the prompt body.

*Call graph*: calls 3 internal fn (write_workspace_file, _inbox_name, _stream_download); called by 1 (ingest); 1 external calls (__init__).


##### `_inbox_name`  (lines 1446–1456)

```
def _inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Creates a safe, unique filename for an inbound Slack attachment. It strips folder paths and avoids duplicate names within the same message.

**Data flow**: It receives the raw filename and a set of used names, normalizes the leaf name, appends numeric suffixes until unused, records it, and returns it.

**Call relations**: The file downloader uses this for every attachment it writes to the Slack inbox folder.

*Call graph*: called by 1 (_download_files).


##### `_files_note`  (lines 1459–1470)

```
def _files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates the short note appended to the turn body describing downloaded or skipped files. This tells the agent where files were saved and which ones were too large.

**Data flow**: It receives the downloaded-files summary, builds bracketed text clauses for delivered and skipped files, and returns empty text if there is nothing to report.

**Call relations**: Ingest appends this note to the user’s message before admitting the turn.

*Call graph*: called by 1 (ingest).


##### `ThreadStatus.run`  (lines 1497–1504)

```
async def run(self) -> None
```

**Purpose**: Runs the live Slack thread status updater for one turn. It starts with “Thinking…”, follows turn activity, and clears the status when finished.

**Data flow**: It reads the bot token, opens an HTTP client, writes the initial status, follows the turn tail, and always sends a clear status at the end.

**Call relations**: The status task wrapper calls this after a turn is admitted.

*Call graph*: calls 2 internal fn (_follow, _set); called by 1 (_run_status); 1 external calls (AsyncClient).


##### `ThreadStatus._set`  (lines 1506–1535)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> None
```

**Purpose**: Writes one status value to Slack’s assistant thread status API. It only writes if this turn is still the newest writer for that thread.

**Data flow**: It checks the thread-writer table, builds the Slack status body, includes loading text when non-empty, sends it to Slack, and logs the write.

**Call relations**: The status runner and follower use this for initial, refreshed, changed, and clear status updates.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._follow`  (lines 1537–1572)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches live turn frames and converts them into short Slack status messages. It refreshes quiet statuses so Slack does not drop them.

**Data flow**: It tails the turn, waits for frames or refresh timeout, maps tool calls, skill loads, and text deltas to status text, rate-limits updates, and exits on terminal frames.

**Call relations**: ThreadStatus.run calls this between the initial “Thinking…” and final clear.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_track_status`  (lines 1579–1601)

```
def _track_status(ctx: SurfaceContext, turn_id: UUID, queue_key: str, message_ts: str) -> None
```

**Purpose**: Starts one live status task for an admitted turn. It deduplicates redeliveries and marks the newest turn as the only writer for its Slack thread.

**Data flow**: It derives the Slack channel and thread timestamp, records the writer, creates a ThreadStatus task, stores it by turn id, and registers cleanup.

**Call relations**: Message ingest and answer-button interactivity call this immediately after admitting a turn.

*Call graph*: calls 1 internal fn (_run_status); called by 2 (ingest, interactive); 2 external calls (__init__, create_task).


##### `_track_status._untrack`  (lines 1596–1599)

```
def _untrack(_done: asyncio.Task[None]) -> None
```

**Purpose**: Cleans up status-task tracking after a status task finishes. It also removes the thread-writer marker if this turn is still the current writer.

**Data flow**: It removes the turn id from the task table and conditionally deletes the writer entry for the Slack thread.

**Call relations**: It is attached as the done callback for tasks created by the status tracker.


##### `_run_status`  (lines 1604–1614)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Runs a ThreadStatus task with logging around failures. A status failure should not affect the durable turn reply.

**Data flow**: It awaits the status runner and catches any exception, logging turn, channel, thread, and error information.

**Call relations**: The status tracker schedules this wrapper as the background task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 1626–1630)

```
def __post_init__(self) -> None
```

**Purpose**: Validates that a progress-reporting schedule makes sense. The first interval must be positive, and the maximum interval cannot be smaller than it.

**Data flow**: It reads the configured base and cap seconds from the object and raises a value error if they are invalid.

**Call relations**: Progress task creation constructs this value before a long-running turn reporter starts.


##### `ProgressCadence.intervals`  (lines 1632–1644)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Produces the wait times between interim progress messages. The schedule doubles the elapsed wait until it reaches a cap.

**Data flow**: It starts with the base interval, yields it, accumulates elapsed time, then yields the smaller of elapsed time and the cap forever.

**Call relations**: ThreadProgress uses this iterator to decide when each checkpoint should post.


##### `TurnActivity.tool`  (lines 1661–1665)

```
def tool(self, tool: str, description: str) -> None
```

**Purpose**: Records that the turn has started or called a tool. It also closes any streamed narration that came before the tool call.

**Data flow**: It receives a tool name and description, saves completed streamed text as narration, sets the current activity, and increments that tool’s tally.

**Call relations**: ThreadProgress’s tail follower calls this when it sees a tool-call frame.

*Call graph*: calls 1 internal fn (_close_narration).


##### `TurnActivity.skill`  (lines 1667–1669)

```
def skill(self, skill: str) -> None
```

**Purpose**: Records that the turn is loading a skill. Like a tool call, it treats any preceding streamed text as completed narration.

**Data flow**: It receives a skill name, closes streamed narration, and sets the current activity to a loading message.

**Call relations**: ThreadProgress’s tail follower calls this when it sees a skill-load frame.

*Call graph*: calls 1 internal fn (_close_narration).


##### `TurnActivity.stream`  (lines 1671–1672)

```
def stream(self, text: str) -> None
```

**Purpose**: Records streamed text chunks from the turn. The text is not immediately shown in progress posts, but its size can be reported as writing activity.

**Data flow**: It receives a text chunk and appends it to the current streaming buffer.

**Call relations**: ThreadProgress’s tail follower calls this when it sees text-delta frames.


##### `TurnActivity.checkpoint`  (lines 1674–1675)

```
def checkpoint(self) -> None
```

**Purpose**: Resets the per-interval tool tally after a progress checkpoint. This makes the next post describe new tool activity since the last update.

**Data flow**: It clears the counter of tool calls while leaving narration, activity, and streaming state intact.

**Call relations**: ThreadProgress calls this after each attempted progress post.


##### `TurnActivity.current_step`  (lines 1677–1686)

```
def current_step(self) -> str
```

**Purpose**: Returns the best short description of what the turn is doing right now. Streaming text is reported as writing progress by size, not by quoting unfinished content.

**Data flow**: It counts buffered streamed characters. If any exist, it returns a writing message; otherwise it returns the latest tool or skill activity.

**Call relations**: TurnActivity.report uses this when building a progress message.

*Call graph*: called by 1 (report).


##### `TurnActivity._close_narration`  (lines 1688–1692)

```
def _close_narration(self) -> None
```

**Purpose**: Moves completed streamed text into the latest narration field. This captures prose the model finished before switching to a tool or skill step.

**Data flow**: It joins and trims the streaming buffer, clears it, and stores a length-limited narration if any text remains.

**Call relations**: Tool and skill activity recording call this before replacing the current step.

*Call graph*: called by 2 (skill, tool).


##### `TurnActivity.report`  (lines 1694–1721)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds the text for one interim progress post. It skips posts when the turn has produced no visible signal at all.

**Data flow**: It reads narration, current step, elapsed time, and tool counts, formats a short multi-line message, and returns text or nothing.

**Call relations**: ThreadProgress._post calls this at each checkpoint before deciding whether to send a Slack message.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 1748–1751)

```
async def run(self) -> None
```

**Purpose**: Runs the interim progress reporter for one turn. It posts only if the turn lasts long enough and has meaningful activity to report.

**Data flow**: It reads the bot token, opens an HTTP client, and delegates to the follower that watches turn frames and schedule deadlines.

**Call relations**: The progress task wrapper calls this after the tracker starts a reporter.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._follow`  (lines 1753–1787)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches live turn frames and progress deadlines at the same time. It updates activity state from frames and posts at scheduled checkpoints.

**Data flow**: It starts a timer, creates a TurnActivity, tails frames, waits until either a frame arrives or a deadline expires, posts progress on deadlines, and exits on terminal frames.

**Call relations**: ThreadProgress.run calls this as the main loop for long-turn updates.

*Call graph*: calls 1 internal fn (_post); called by 1 (run); 5 external calls (__init__, ensure_future, gather, wait, monotonic).


##### `ThreadProgress._post`  (lines 1789–1835)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float) -> None
```

**Purpose**: Sends one interim progress message to Slack if there is something useful to say. A failed post is contained so later checkpoints can still try.

**Data flow**: It asks TurnActivity for report text, skips and logs if empty, builds a Slack message body, posts it to the conversation destination, and logs success or failure.

**Call relations**: The progress follower calls this whenever the cadence reaches a checkpoint.

*Call graph*: calls 3 internal fn (report, _slack_ok, slack_reply_body); called by 1 (_follow); 2 external calls (post, log).


##### `_track_progress`  (lines 1841–1868)

```
def _track_progress(ctx: SurfaceContext, turn_id: UUID, queue_key: str) -> None
```

**Purpose**: Starts one interim progress reporter for an admitted turn. It is careful not to start duplicate reporters for Slack retries or mention twin deliveries.

**Data flow**: It checks the in-process task table, creates a ThreadProgress with the configured cadence, schedules it, stores the task, and removes it when done.

**Call relations**: Ingest and interactivity call this only for the delivery or click that should own progress messages.

*Call graph*: calls 1 internal fn (_run_progress); called by 2 (ingest, interactive); 3 external calls (__init__, __init__, create_task).


##### `_run_progress`  (lines 1871–1884)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Runs a ThreadProgress task and logs unrecoverable failures. Individual post failures are handled elsewhere, so reaching this wrapper means the reporter is abandoned.

**Data flow**: It awaits the progress runner and catches any exception, logging the turn, queue key, and error.

**Call relations**: The progress tracker schedules this wrapper as the background task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `interactive`  (lines 1918–1987)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack button clicks from question answers and connection requests. It verifies the request, admits answer clicks as new turns, and sends private connect links.

**Data flow**: It reads and verifies the form body, loads identity, parses the click, resolves the member, either posts an ephemeral connect response or admits an answer turn, starts live tracking, and schedules message rewrite when appropriate.

**Call relations**: Slack calls this route for Block Kit interactions; it hands accepted answers to core admission and uses background tasks for Slack updates.

*Call graph*: calls 20 internal fn (admit, admitted_body, connect_url, conversation_for, credential, find_conversation, linked_member, _ctx_signing_secret, _ephemeral_in_background, _identity (+10 more)); 2 external calls (JSONResponse, Response).


##### `_rewrite_in_background`  (lines 1993–1996)

```
def _rewrite_in_background(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Starts a background task to rewrite a question message after a winning answer click. This lets the HTTP acknowledgement return quickly to Slack.

**Data flow**: It receives the bot token and click, creates a rewrite task, tracks it in a set, and removes it when finished.

**Call relations**: The interactivity handler calls this only for the click body that won admission for that question.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 1999–2003)

```
async def _run_rewrite(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Runs the Slack message rewrite and logs failures. A failed rewrite does not undo the admitted answer turn.

**Data flow**: It calls the button-replacement helper and catches any exception, logging the affected message timestamp.

**Call relations**: The background rewrite launcher schedules this wrapper.

*Call graph*: calls 1 internal fn (_replace_buttons_with_answer); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 2006–2009)

```
def _ephemeral_in_background(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Starts a background task to send a private Slack response for a connect button click. This keeps the interactivity acknowledgement fast.

**Data flow**: It receives context, click, and text, creates an ephemeral-post task, tracks it in a set, and removes it when done.

**Call relations**: The interactivity handler calls this after deciding what private connect message the clicking member should see.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 2012–2037)

```
async def _post_ephemeral(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Posts a private Slack message visible only to the member who clicked a connect button. It places the message in the relevant thread when possible.

**Data flow**: It reads the bot token, builds a chat.postEphemeral body with channel, user, text, and optional thread, sends it to Slack, and logs failures.

**Call relations**: The ephemeral background launcher schedules this after connect-click handling.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_click`  (lines 2040–2095)

```
def _to_click(raw: bytes, identity: SlackIdentity) -> AnswerClick | ConnectClick | None
```

**Purpose**: Parses a signed Slack interactivity payload into either an answer click or connect click. Unrecognized or wrong-team clicks are ignored.

**Data flow**: It decodes the form payload JSON, validates the action, extracts user, channel, message, thread, button value, and action id, then returns the appropriate click record or nothing.

**Call relations**: The interactivity handler calls this after signature and identity checks.

*Call graph*: calls 2 internal fn (_dict_field, _string_field); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_dict_field`  (lines 2098–2102)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required object field from a Slack payload. It makes malformed interactivity payloads fail clearly.

**Data flow**: It receives a mapping and field name, returns the nested mapping if valid, or raises a value error.

**Call relations**: Click parsing uses this for nested user, channel, and message data.

*Call graph*: called by 1 (_to_click).


##### `_replace_buttons_with_answer`  (lines 2105–2147)

```
async def _replace_buttons_with_answer(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Updates a Slack question message so the clicked button row becomes a small “answered by” line. It preserves the exact blocks Slack already accepted for everything else.

**Data flow**: It builds an answered context block, copies delivered blocks or makes a fallback block, replaces the clicked block when found, and sends chat.update to Slack.

**Call relations**: The rewrite task wrapper calls this after an answer click wins admission.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (_run_rewrite); 2 external calls (AsyncClient, dumps).


##### `_reply_text`  (lines 2150–2160)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the basic text to post for a completed, failed, or cancelled turn. It ensures Slack gets a visible outcome even when the agent produced no normal text.

**Data flow**: It reads the writeback status and text, returns failure text, cancellation text or reason, normal reply text, or an empty-reply placeholder.

**Call relations**: Reply preparation calls this before adding credential hints or oversize artifact links.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 2163–2178)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Builds the final reply text, including terminal credential guidance and links for artifacts too large to upload to Slack.

**Data flow**: It starts from the basic reply text, appends credential instructions when needed, finds oversize artifacts, formats download-link lines, and returns the combined text.

**Call relations**: The final post function calls this before building the Slack message body.

*Call graph*: calls 2 internal fn (_oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 2181–2184)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one too-large shared artifact as a readable link line. If no temporary link is available, it falls back to the filename.

**Data flow**: It receives context and artifact, asks the context for an artifact link, combines name and byte size, and returns a Markdown-style list item.

**Call relations**: Oversize-link reply preparation calls this for each artifact that cannot be uploaded inline.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_debug_link`  (lines 2187–2199)

```
async def _debug_link(ctx: SurfaceContext, writeback: Writeback) -> str | None
```

**Purpose**: Builds an operator-only debug URL for the delivered turn when the deploy has a public base URL. It does not decide who may open the debug page.

**Data flow**: It checks for a public base URL, finds the conversation for the writeback queue key, and returns a URL with workspace, conversation, and turn parameters or nothing.

**Call relations**: Final reply posting uses this when adding the operator metadata footer.

*Call graph*: calls 1 internal fn (find_conversation); called by 1 (post).


##### `_channel_is_externally_shared`  (lines 2202–2232)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel may include people outside the bound workspace. If it cannot prove the channel is internal, it treats it as shared.

**Data flow**: It calls Slack conversations.info, reads sharing flags from the channel object, and returns true on sharing flags or any lookup failure.

**Call relations**: Final reply posting uses this to avoid showing operator cost and debug metadata in shared channels.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (post); 1 external calls (AsyncClient).


##### `post`  (lines 2235–2291)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Posts the terminal agent reply to Slack and returns the Slack message reference. This is the durable outbound reply path.

**Data flow**: It reads the bot token, prepares reply text, action blocks, and optional metadata, posts to Slack, retries once with safer blocks for invalid-block errors, validates the timestamp, and returns channel:ts.

**Call relations**: The core delivery poller calls this when a turn has a writeback ready for Slack.

*Call graph*: calls 9 internal fn (credential, is_operator_workspace, _channel_is_externally_shared, _chat_post, _debug_link, _reply_with_oversize_links, slack_ask_blocks, slack_connect_blocks, slack_reply_body); 2 external calls (__init__, AsyncClient).


##### `_chat_post`  (lines 2294–2334)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request and returns Slack’s parsed response without requiring ok:true. This lets the caller handle recoverable Slack errors specially.

**Data flow**: It posts the prepared body to Slack, converts HTTP failures into delivery errors with retry-after when available, and returns the JSON payload.

**Call relations**: The final post function uses this for the first attempt and possible invalid-block retry.

*Call graph*: calls 1 internal fn (__init__); called by 1 (post); 1 external calls (post).


##### `attach`  (lines 2337–2356)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shareable artifacts from a completed turn into Slack when they fit Slack’s size cap. Oversize artifacts are handled as links in the reply text instead.

**Data flow**: It filters artifacts by size, finds the Slack destination from the queue key, reads the bot token, uploads eligible artifacts concurrently, and logs individual failures.

**Call relations**: The delivery flow can call this after posting the reply, using the returned reply reference as part of the delivery process.

*Call graph*: calls 2 internal fn (credential, _upload_artifact); 1 external calls (gather).


##### `_upload_artifact`  (lines 2359–2405)

```
async def _upload_artifact(ctx: SurfaceContext, bot_token: str, channel: str, thread_ts: str | None, artifact: SharedArtifact) -> None
```

**Purpose**: Performs Slack’s three-step external file upload for one artifact. The file bytes stream from blob storage rather than being loaded all at once.

**Data flow**: It reserves an upload URL with Slack, streams the artifact blob to that URL, then completes the upload into the Slack channel or thread with a title.

**Call relations**: The attachment uploader runs this concurrently for each eligible artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 4 external calls (__init__, AsyncClient, Timeout, dumps).


##### `_slack_ok`  (lines 2408–2414)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Normalizes Slack API calls that are expected to return ok:true. It turns Slack-level errors into exceptions with useful messages.

**Data flow**: It awaits an HTTP request, raises for HTTP errors, parses JSON, checks the ok field, returns the payload, or raises a SlackApiError.

**Call relations**: Most Slack API helpers use this so they do not each repeat the same ok:true checking.

*Call graph*: called by 11 (_list, _members, _post, _set, _ambient_context, _channel_is_externally_shared, _post_ephemeral, _replace_buttons_with_answer, _slack_user, _upload_artifact (+1 more)); 1 external calls (__init__).


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The `ufo` shell client is very small: it sends a message, then reads back plain text lines that tell it what to print, ask, store, or do next. This file creates those lines. Think of it as a translator between the richer server world and a simple terminal script.

A request must carry a bearer token, which is a signed proof of workspace and email. The workspace is used to route the request, and the email is used to find or create the member identity. Each terminal channel becomes a conversation key, so the same user can return to the same ongoing chat.

When the request body has text, the file admits that text as a new turn in the conversation. When the body is empty, it does not create a new turn; it just reconnects to the latest turn and keeps reading. That matters because answers can take longer than one HTTP request can safely stay open. The stream is held for a fixed time, then ends with a `poll` directive so the shell reconnects and continues without losing the answer.

The file also supports private credential entry. If the terminal sends a secret using special headers, the value is stored through the protected surface context instead of being added to the chat transcript.

#### Function details

##### `directive`  (lines 50–58)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one command line for the shell client to read. It makes sure special characters inside the command fields cannot accidentally break the line format.

**Data flow**: It takes a command name, called the verb, plus any text fields. It escapes tabs, newlines, and backslashes, removes carriage returns, joins everything with tabs, adds a newline, and returns the result as bytes ready to stream over HTTP.

**Call relations**: Most of this file uses this helper whenever it needs to speak to the shell. Higher-level functions decide what should happen, such as printing text or asking for input, and this function turns that decision into the exact wire format.

*Call graph*: called by 6 (_answer, _fulfill_secret, _say_lines, channel, directives_for, stream_directives).


##### `resolve_workspace`  (lines 61–68)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Finds which workspace a request belongs to before the main request handler runs. If the request does not have a usable bearer token, it returns nothing so the request can be rejected.

**Data flow**: It reads the `Authorization` header, checks that it starts with `Bearer`, extracts the token, and asks the bearer-token code for the workspace claim. The output is either a workspace UUID or `None`.

**Call relations**: The shared surface routing layer calls this early to scope the request. Later, the main channel handler verifies the same token again to identify the member email.

*Call graph*: 1 external calls (workspace_claim).


##### `directives_for`  (lines 71–95)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Converts one live server event into one or more terminal directives. This is where assistant progress, tool activity, costs, final answers, and pauses become things the shell can display.

**Data flow**: It receives a live frame from the conversation stream, plus context such as whether answer text was already streamed and whether credential prompts are still pending. Depending on the frame type, it emits bytes for `txt`, `note`, `status`, `say`, `ask`, `secret`, or similar directives.

**Call relations**: The streaming loop calls this for every frame it reads. For tool activity it asks `_activity` to write a human-readable note, and for final terminal frames it hands off to `_answer` because ending a turn has several special cases.

*Call graph*: calls 3 internal fn (_activity, _answer, directive); called by 1 (stream_directives).


##### `_activity`  (lines 98–100)

```
def _activity(frame: ToolCall) -> str
```

**Purpose**: Creates a short human-readable message for a tool call. It lets the terminal show that the assistant is doing work, not just sitting silently.

**Data flow**: It reads the tool name and either a description or preview from the tool-call frame. It returns text like `running search: looking up X`, or just `running search` if there is no extra detail.

**Call relations**: Only `directives_for` calls this, when it sees a tool-call frame. The returned text is then wrapped as a `note` directive for the shell.

*Call graph*: called by 1 (directives_for).


##### `_answer`  (lines 103–131)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Writes the final set of directives for a completed, failed, or cancelled turn. It decides whether to print the answer, ask for more input, request secrets, or end the terminal session.

**Data flow**: It takes a terminal frame and supporting context. For a successful turn, it may print answer lines, emit private secret prompts, show a connection URL message, and then ask for the next prompt. For a failed turn, it prints an error-like message and asks again. For a cancelled turn, it says `cancelled` and tells the client to exit.

**Call relations**: `directives_for` calls this when a live frame says the turn has reached its terminal state. `_answer` uses `_say_lines` to split normal text into printable lines and `directive` to encode each terminal command.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 134–135)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Turns a block of text into separate `say` directives. This keeps multi-line answers readable in the terminal.

**Data flow**: It receives a string, splits it into lines, and wraps each line as a `say` directive. If the text has no lines, it still produces one directive for the original text.

**Call relations**: `_answer` uses this when a final answer or failure message should be printed as normal terminal output. This helper relies on `directive` for the actual byte formatting.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 138–198)

```
async def stream_directives(frames: AsyncIterator[tuple[str, LiveFrame]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[[], Awaitable[str]] | None=
```

**Purpose**: Streams live conversation updates to the shell, but only keeps the HTTP request open for a safe amount of time. If the answer is not finished before the time limit, it tells the shell to poll again.

**Data flow**: It receives an async stream of live frames, a hold time, and optional callbacks for credential checks and connection URLs. It reads frames until the turn finishes, parks, the stream ends, or the deadline arrives. Each frame becomes directive bytes. If time runs out before a natural ending, it emits a `poll` directive.

**Call relations**: `channel` creates this stream after admitting or finding a turn. Inside the loop, it uses `_next` to safely get the next frame, asks credential and connection callbacks for extra information when needed, and uses `directives_for` to translate frames into terminal commands.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 2 external calls (get_running_loop, wait_for).


##### `_next`  (lines 201–207)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Fetches the next live frame from an async iterator, returning `None` when there are no more frames. It keeps normal end-of-stream behavior from being confused with timeout handling.

**Data flow**: It waits for the iterator’s next item. If an item exists, it returns the cursor and frame. If the iterator is exhausted, it catches that condition and returns `None` instead.

**Call relations**: `stream_directives` calls this inside a timed wait. This small wrapper lets the streaming loop treat `None` as a clean end, while timeouts remain a separate case.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 210–214)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Verifies the request token and extracts the member email. This is the gate that stops unauthenticated terminal requests from entering a conversation.

**Data flow**: It reads the `Authorization` header, checks for a bearer token, and verifies that token against the current workspace. If verification succeeds, it returns the email inside the token; otherwise it returns `None`.

**Call relations**: `channel` calls this at the start of request handling. If it returns `None`, `channel` immediately sends an unauthorized response and does not touch conversations or secrets.

*Call graph*: called by 1 (channel); 1 external calls (verify_token).


##### `channel`  (lines 217–248)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles one POST from the `ufo` shell for a specific channel. It authenticates the user, connects the request to the right conversation, optionally admits a new message, and returns a stream of terminal directives.

**Data flow**: It starts with the HTTP request and surface context. It verifies the email, links or creates the member, checks whether the request is actually a secret submission, finds the conversation for the email and channel, then reads the body. A non-empty body becomes a new turn after a size check; an empty body resumes the latest turn or asks for input if there is none. The output is either a plain error/ask response or a streaming response of directives.

**Call relations**: This is the route handler named in `ROUTES`, so the surface framework calls it for POSTs to a channel path. It relies on `_authenticated_email` for identity, `_fulfill_secret` for private credential submissions, and the `SurfaceContext` methods to link members, find conversations, admit turns, tail live frames, and build connection URLs.

*Call graph*: calls 10 internal fn (admit, conversation_for, latest_turn, link_member, linked_member, tail, _authenticated_email, _fulfill_secret, directive, stream_directives); 4 external calls (partial, PlainTextResponse, body, StreamingResponse).


##### `_fulfill_secret`  (lines 251–270)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores one private credential value sent by the shell. It deliberately does not create a chat message, so secrets do not appear in the conversation transcript.

**Data flow**: It reads the secret slot from a header and the secret value from the request body. It rejects empty or too-large values, then asks the protected surface context to fulfill the sealed credential request. The response is a simple `say` directive telling the shell whether the value was stored or why it was not.

**Call relations**: `channel` calls this when it sees the secret header. This function hands the actual verification and storage to `SurfaceContext.fulfill_credential_request`, and uses `directive` to format the result for the terminal.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling`

This file is the bridge between an ordinary web page and the core ufo agent system. Without it, a user could still maybe talk to the agent through other surfaces, but the browser chat page would not know how to prove who the user is, submit a message, receive live updates, or show cost information.

The flow starts with a signed bearer token, which is like a tamper-proof ticket saying which workspace and email the browser belongs to. The page can receive that token in the URL, save it in a cookie, and then use the cookie on later requests. Before any chat or spend request is accepted, the file checks the cookie and links the email to a member record in the workspace if needed.

When the user sends a message, the file checks that the message is present and not too large, finds the right conversation for that person, and admits a new “turn” into the shared durable queue. A turn is one user message plus the agent’s response work. The browser then opens a Server-Sent Events stream, a simple one-way live feed from server to browser, to receive text, tool activity, cost updates, account-connection links, and the final result. The spend page uses the same authentication and renders a small HTML report of recent costs.

#### Function details

##### `resolve_workspace`  (lines 38–45)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: This function tells the shared web fleet which workspace an incoming request belongs to. It does this before the route itself runs, using the signed token from either the session cookie or the URL.

**Data flow**: It receives the HTTP request and reads a token from the `ufo_session` cookie, or from the `token` query parameter if there is no cookie yet. It asks the bearer-token code to extract the workspace claim from that token. It returns the workspace ID if the token is usable, or `None` if there is no token to trust.

**Call relations**: The wider surface framework calls this early so the request can be routed under the right workspace. It delegates the actual token reading logic to `workspace_claim`, and later route handlers perform stricter authentication when they need the member email.

*Call graph*: 1 external calls (workspace_claim).


##### `_authenticate`  (lines 48–59)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | None
```

**Purpose**: This helper checks whether a request really belongs to a known web user in the current workspace. It turns the signed cookie into a member ID and email that the rest of the web handlers can safely use.

**Data flow**: It reads the session cookie from the request. If the cookie is missing or the token does not verify for the current workspace, it returns `None`. If the token is valid, it gets the email, looks for an already linked member, creates the link if needed, and returns the member ID together with the email.

**Call relations**: `chat`, `stream`, and `spend` all call this before doing anything private. It relies on the bearer-token verifier for proof of identity, then uses the surface context to find or create the member connection that the core system understands.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 3 (chat, spend, stream); 1 external calls (verify_token).


##### `chat_page`  (lines 62–69)

```
async def chat_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function serves the browser chat page. If the user arrived with a token in the URL, it stores that token in a session cookie so later chat requests can authenticate without keeping the token in the URL.

**Data flow**: It receives the request, creates an HTML response containing the built-in chat page, and checks for a `token` query parameter. If a token is present, it adds a strict same-site cookie to the response. The browser receives the page and, possibly, the new cookie.

**Call relations**: This is the first route a browser commonly visits. It calls the HTTP response helpers to send HTML and set the cookie; later, `chat`, `stream`, and `spend` depend on that cookie being present.

*Call graph*: 2 external calls (HTMLResponse, set_session_cookie).


##### `chat`  (lines 72–84)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function accepts a user’s chat message and submits it to the shared agent work queue. It returns the new turn ID so the browser can open a live stream for that specific response.

**Data flow**: It first authenticates the request. Then it reads the request body as the user’s message, rejects an empty message, and rejects a message that is too large. For a valid message, it finds the user’s conversation, admits a new turn with the user as the speaker, and returns JSON containing the turn ID.

**Call relations**: The chat page’s JavaScript calls this when the user presses Send. This function uses `_authenticate` for identity, then hands the actual work to the core surface context through `conversation_for` and `admit`. The browser uses the returned turn ID to call `stream` next.

*Call graph*: calls 3 internal fn (admit, conversation_for, _authenticate); 3 external calls (JSONResponse, body, Response).


##### `stream`  (lines 87–104)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function opens the live event stream for one agent turn. It makes sure the requesting member owns the turn before sending any updates.

**Data flow**: It authenticates the request, parses the turn ID from the URL, and checks that the turn exists. It then asks who owns the turn and compares that owner to the authenticated member. If the checks pass, it reads the browser’s last seen event ID, if any, and returns a streaming response that will send events from that point onward.

**Call relations**: The browser calls this after `chat` returns a turn ID. This function calls `_events` to produce the stream body, and wraps it in a `StreamingResponse` using the `text/event-stream` format that browser `EventSource` understands.

*Call graph*: calls 3 internal fn (turn_owner, _authenticate, _events); 3 external calls (Response, StreamingResponse, UUID).


##### `_events`  (lines 107–124)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: This async generator converts the core system’s live turn frames into browser-ready stream events. It also notices special account-connection requests and turns them into a clickable connection URL event.

**Data flow**: It receives the surface context, turn ID, member ID, and a cursor telling where to resume. It tails the core event hub for that turn. For each frame, it may create a special `connect` or `connect_error` event, then converts the frame itself into a Server-Sent Events byte block. It yields those byte blocks one by one to the HTTP stream.

**Call relations**: `stream` calls this when a browser has permission to watch a turn. It reads live frames from `ctx.tail`, asks `ctx.connect_url` when the agent requests an external account connection, and uses `_sse` to format ordinary frames.

*Call graph*: calls 3 internal fn (connect_url, tail, _sse); called by 1 (stream); 1 external calls (dumps).


##### `spend`  (lines 127–135)

```
async def spend(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function shows an authenticated user the workspace’s recent spending. It is a small web version of the command-line spend report.

**Data flow**: It authenticates the request, reads an optional `window_seconds` query parameter, and asks the core context for a spending rollup over that time window. It turns the report into an HTML page and returns it to the browser. If authentication fails, it returns an unauthorized response.

**Call relations**: A browser requests this route when the user wants to inspect costs. It shares `_authenticate` with the chat routes, calls the core `spend_rollup` method for the actual accounting data, and delegates page building to `_spend_page`.

*Call graph*: calls 3 internal fn (spend_rollup, _authenticate, _spend_page); 2 external calls (HTMLResponse, Response).


##### `_sse`  (lines 138–155)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: This helper formats one live frame as a Server-Sent Events message. Server-Sent Events are a browser-friendly text format for a one-way live feed from server to page.

**Data flow**: It receives a cursor and one live frame. If the cursor is present, it writes it as the event ID so the browser can resume after a dropped connection. It then inspects the kind of frame and emits an event name such as `terminal`, `parked`, `cost`, `tool`, or `skill`, with the frame serialized as JSON bytes.

**Call relations**: `_events` calls this for every normal frame it receives from the core event tail. The browser-side JavaScript listens for these event names and updates the chat bubble, cost meter, activity note, or final status.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_money`  (lines 158–159)

```
def _money(micro_usd: int) -> str
```

**Purpose**: This helper turns an internal cost value into a readable dollar string. It exists so the spend page can consistently show money in normal dollars rather than tiny accounting units.

**Data flow**: It receives an integer number of micro-dollars, where one dollar is split into one million parts. It divides by the shared conversion constant and formats the result with a dollar sign and six decimal places. It returns that string.

**Call relations**: `_spend_page` and `_subject_rows` call this whenever they need to display a cost. It keeps money formatting in one place instead of repeating the conversion in each HTML row.

*Call graph*: called by 2 (_spend_page, _subject_rows).


##### `_subject_rows`  (lines 162–167)

```
def _subject_rows(subjects: tuple[SubjectTotal, ...]) -> str
```

**Purpose**: This helper builds the table rows for spending grouped by a subject, such as member or agent. It also safely escapes labels so a name cannot accidentally become HTML code.

**Data flow**: It receives a tuple of spending totals. For each total, it escapes the label, formats the cost with `_money`, and creates an HTML table row. If there are no totals, it returns a single row saying `none`.

**Call relations**: `_spend_page` uses this twice: once for spending by member and once for spending by agent. It relies on `_money` for readable cost text and `html.escape` for safe display.

*Call graph*: calls 1 internal fn (_money); called by 1 (_spend_page); 1 external calls (escape).


##### `_spend_page`  (lines 170–190)

```
def _spend_page(report: SpendReport) -> str
```

**Purpose**: This helper turns a spending report into a complete HTML page. It gives the browser a simple summary of total cost, cost by dimension, cost by member, and cost by agent.

**Data flow**: It receives a `SpendReport` from the core accounting system. It loops through the report’s dimension totals, escapes display text, formats costs with `_money`, and inserts the rows into an HTML document. It also asks `_subject_rows` to build the member and agent sections. The result is a string of HTML ready to send in a response.

**Call relations**: `spend` calls this after it obtains the rollup data from the core context. This function is the presentation layer for that report: it does not calculate the spending itself, but it decides how the numbers are shown to the user.

*Call graph*: calls 2 internal fn (_money, _subject_rows); called by 1 (spend); 1 external calls (escape).


### Workspace agent settings
Workspace members and owners inspect or adjust the shared agent settings that shape admitted user requests.

### `core/src/ufo/agents.py`

`domain_logic` · `request handling for workspace object reads and updates`

A workspace has one main agent, created when the workspace is initialized. This file defines how that agent is shown and changed through the system’s object interface. Think of it like a settings card for the agent: everyone can look at the card, but only the owner can edit certain fields.

The file separates two kinds of agent information. The model and public-internet permission live in the agent object’s editable spec. The system prompt does not; prompt changes go through a governed proposal process, so this file only shows the current prompt and a digest, which is a short fingerprint used to prove which prompt a proposal is based on.

The agent object is update-only. Creating another agent is refused because there is currently only one agent per workspace. Deleting it is also refused because the workspace depends on it. When the owner applies a change, the database row for the existing agent is updated, and the change takes effect on the next turn.

One small but important detail is the special model value `auto`. If the stored model says `auto`, read views report the concrete model the deployment actually chose, while the saved spec still keeps `auto`. That avoids accidentally turning a flexible setting into a fixed model during read-and-apply workflows.

#### Function details

##### `_effective_model`  (lines 40–45)

```
def _effective_model(ctx: ToolContext, stored: str) -> str
```

**Purpose**: This helper decides which model name should be shown to a reader. If the stored setting is the special `auto` value, it reports the real model currently being used instead of the word `auto`.

**Data flow**: It receives the current tool context and the model value stored in the database. If the stored value is `auto`, it reads the already-resolved model from the active agent in the context; otherwise it keeps the stored value. It returns the model name that should be displayed.

**Call relations**: The list and status views call this when they need to describe what model the agent is actually running on. It keeps display output honest without changing the saved spec.

*Call graph*: called by 2 (list, status).


##### `AgentObjects.list`  (lines 71–96)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: This returns a paged list of agent objects for the current workspace. In practice there is normally just one, but it still uses the shared object-list format used by the rest of the system.

**Data flow**: It opens a workspace database transaction, reads agent rows for the current workspace, and pulls each agent’s name, stored model, and internet permission. For each row it builds a short human-readable summary, using `_effective_model` to show the real model when the stored setting is `auto`. It returns an object page containing those rows and the caller’s list query settings.

**Call relations**: This is called when someone asks to list objects of kind `agent`. It talks to the workspace database, consults the current workspace id, formats each result as an object row, and hands the final page back to the object system.

*Call graph*: calls 1 internal fn (_effective_model); 5 external calls (__init__, select, workspace_tx, object_page, ws_current).


##### `AgentObjects.get`  (lines 98–111)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[AgentSpec] | None
```

**Purpose**: This fetches the editable specification for one named agent. It shows the fields that can be applied back later: the stored model setting and whether public internet is allowed.

**Data flow**: It receives a context and an agent name, then asks `_row` for the matching database row. If no row exists, it returns nothing. If a row exists, it builds an `AgentSpec` from the stored model and internet policy, wraps it with creation and update timestamps, and returns that detail object.

**Call relations**: This is used when the object system needs the agent’s spec, such as for `object_get` or before applying changes. It relies on `_row` for the database lookup, then packages the result in the standard object detail shape.

*Call graph*: calls 1 internal fn (_row); 2 external calls (__init__, __init__).


##### `AgentObjects.status`  (lines 113–121)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: This returns the read-only live status for one named agent. It includes the current system prompt, a digest of that prompt, and the concrete model the agent is running.

**Data flow**: It receives a context and an agent name, then uses `_row` to read the agent’s database record. If the agent is missing, it returns nothing. Otherwise it computes a prompt digest from the stored prompt, converts the stored model to its effective displayed model when needed, and returns those values in a simple dictionary.

**Call relations**: This is the read side of the prompt proposal flow: proposal code can compare the digest it knows with the digest shown here. It calls `_row` to get the stored values, `prompt_digest` to fingerprint the prompt, and `_effective_model` to avoid showing `auto` where a concrete model is expected.

*Call graph*: calls 2 internal fn (_row, _effective_model); 1 external calls (prompt_digest).


##### `AgentObjects.apply`  (lines 123–142)

```
async def apply(self, ctx: ToolContext, name: str, spec: AgentSpec, old: AgentSpec | None) -> None
```

**Purpose**: This updates the existing agent’s editable settings: its model setting and public-internet permission. It refuses attempts to create a new agent and refuses edits by non-owners.

**Data flow**: It receives the desired new spec and the old spec, if one exists. If there is no old spec, it treats the request as an attempted create and raises an error because only one pre-existing agent is allowed. It then checks whether the speaker is the workspace owner; if not, it raises an owner-required error. If the checks pass, it updates the current workspace’s matching agent row with the new model, internet permission, and update timestamp.

**Call relations**: This is called when someone applies an `agent` object change. It first enforces the object’s rules, then writes directly to the agent table inside a workspace transaction. It deliberately does not touch the prompt, because prompt edits belong to the governed proposal path.

*Call graph*: calls 1 internal fn (speaker_is_owner); 5 external calls (__init__, __init__, update, workspace_tx, ws_current).


##### `AgentObjects.delete`  (lines 144–145)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: This refuses deletion of the workspace agent. The workspace is designed to have exactly one agent, so removing it is not a supported operation.

**Data flow**: It receives the context and agent name, but does not look anything up or change anything. It immediately raises a not-supported error explaining that the agent cannot be deleted.

**Call relations**: This is called when the object system receives a delete request for an `agent`. Instead of handing work off to the database, it stops the request at the boundary with a clear error.

*Call graph*: 1 external calls (__init__).


##### `AgentObjects._row`  (lines 147–162)

```
async def _row(self, name: str) -> sa.Row | None
```

**Purpose**: This private helper reads one agent row from the database. It centralizes the lookup used by the read-oriented methods.

**Data flow**: It receives an agent name, opens a workspace database transaction, and selects the prompt, model, internet permission, creation time, and update time for that name in the current workspace. It returns the single matching row, or nothing if no such agent exists.

**Call relations**: The `get` and `status` methods call this so they do not each have to repeat the same database query. It is the small shared doorway from the agent object logic to the underlying agent table.

*Call graph*: called by 2 (get, status); 3 external calls (select, workspace_tx, ws_current).


### Core surface admission
The trusted core surface layer identifies users, records and queues allowed messages, streams turns, and serves signed artifact downloads.

### `core/src/ufo/ext/surface.py`

`orchestration` · `request handling and background writeback delivery`

A surface is an external doorway into UFO, such as Slack, the web UI, or a command-line channel. This file is the doorway’s rulebook and toolkit. It gives trusted surface code powers that ordinary extensions do not get: it can say who a user is, create or find a conversation, place a user message onto the durable turn queue, read surface credentials, and fetch conversation files and transcripts for debug views. Without this file, outside channels would either be too weak to start real work or too powerful without clear boundaries.

The main type is SurfaceContext. A route handler receives it and uses it like a guarded control panel: look up identity, admit a turn, stream live frames, upload workspace files, or fulfill a credential request. The file also describes durable surfaces, where UFO posts the final answer later, and live surfaces, where the user keeps a connection open and watches frames as they arrive.

The other major machine here is WritebackPoller. It is like a mail carrier for durable surfaces. It finds finished turns waiting to be delivered, claims them so two workers do not send the same reply at once, posts the answer, attaches files, records progress, and retries safely after failures.

#### Function details

##### `MemberAdmitter.admit`  (lines 102–110)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: This protocol method describes the one action a surface needs to start work: accept a member’s message into a conversation. It exists so SurfaceContext can depend on a small promise instead of knowing the full queue implementation.

**Data flow**: It receives a conversation id, message text, optional duplicate-protection key, optional turn context, and the speaking member id. The real implementation writes or joins a turn in the durable queue. It returns the id of the turn that will run or is already running.

**Call relations**: SurfaceContext.admit delegates to this method whenever Slack, web, sample, or other surface routes accept a user message.


##### `TurnTailer.tail`  (lines 119–119)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This protocol method describes how a live surface reads a turn’s live output frames. It keeps the surface away from the internal hub and exposes only a safe stream-like operation.

**Data flow**: It receives a turn id and an optional cursor saying where to resume. It yields pairs of replay cursor and live frame until the turn ends.

**Call relations**: SurfaceContext.tail forwards live-surface streaming requests to this method, and web, debugger, sample, and built-in UFO surfaces use it to show answers as they are produced.


##### `workspace_key`  (lines 135–143)

```
def workspace_key(conversation_id: UUID, rel: str) -> str
```

**Purpose**: This builds the safe blob-store key for a file inside one conversation’s workspace folder. It prevents a surface-supplied filename from escaping that folder.

**Data flow**: It receives a conversation id and a relative path. It cleans and checks the path, rejects absolute paths or paths containing '..', then returns a blob key under conversations/<conversation>/workspace/.

**Call relations**: SurfaceContext.write_workspace_file uses it before saving uploaded files, and SurfaceContext.read_workspace_file uses it before reading files back.

*Call graph*: called by 2 (read_workspace_file, write_workspace_file); 1 external calls (PurePosixPath).


##### `ConversationSummary._aware_utc`  (lines 201–204)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This normalizes conversation timestamps so they always carry UTC timezone information. That avoids confusion between timezone-aware and timezone-less dates.

**Data flow**: It receives a datetime or None. None stays None; a datetime without timezone is marked as UTC; a datetime with timezone is left unchanged.

**Call relations**: Pydantic calls this validator while building ConversationSummary objects for conversation listing views.

*Call graph*: 1 external calls (replace).


##### `LedgerEntry._aware_utc`  (lines 218–219)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This normalizes accounting timestamps to be timezone-aware UTC values. It keeps ledger entries consistent for display and API output.

**Data flow**: It receives a datetime. If the datetime has no timezone, it marks it as UTC; otherwise it returns it as-is.

**Call relations**: Pydantic calls this validator when SurfaceContext.turn_detail builds LedgerEntry rows.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 241–246)

```
def _fulfilled_marker_key(workspace_id: UUID, sealed: str, slot: str) -> str
```

**Purpose**: This creates the blob-store marker path that records one credential prompt as already answered. It lets the UI stop asking for a slot once that slot has been filled.

**Data flow**: It receives a workspace id, a sealed credential request string, and a slot name. It hashes the sealed request and returns a marker key scoped to the workspace and slot.

**Call relations**: SurfaceContext.credential_prompt_pending checks this marker, and SurfaceContext.fulfill_credential_request writes it after storing a credential.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_email_domain`  (lines 249–253)

```
def _email_domain(email: str) -> str
```

**Purpose**: This extracts the domain part of an email address in a safe, simple way. Malformed emails return an empty domain so they cannot accidentally pass a domain check.

**Data flow**: It receives an email string, trims and lowercases it, splits around the last '@', and returns the domain only if both local name and domain exist.

**Call relations**: SurfaceContext.join_member uses it to decide whether a verified email belongs to the workspace domain, and SurfaceContext.is_operator_workspace uses it to recognize the operator workspace.

*Call graph*: called by 2 (is_operator_workspace, join_member).


##### `_earliest_agent`  (lines 256–271)

```
async def _earliest_agent(workspace_id: UUID) -> UUID
```

**Purpose**: This finds the default agent for a workspace: the earliest created agent. It is the fallback when no surface installation has chosen a specific agent.

**Data flow**: It receives a workspace id, reads the agent table inside a workspace transaction, orders agents by creation time and id, and returns the first id. If none exists, it raises an error because the workspace is misconfigured.

**Call relations**: _bind_surface_installation uses it when creating a surface binding, and SurfaceContext._surface_agent uses it when a conversation has no bound surface agent.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 274–306)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str) -> None
```

**Purpose**: This records that an external installation, such as a Slack team, belongs to one workspace for one surface. It is the shared write path for installation binding.

**Data flow**: It receives a workspace id, surface name, and installation id. It rejects an empty installation id, finds the default agent, inserts or updates the workspace’s surface binding, and raises a conflict if that installation is already owned elsewhere.

**Call relations**: SurfaceContext.bind_installation calls it during a surface OAuth callback, and SurfaceInstallationAccess.bind calls it when a tool registers an installation.

*Call graph*: calls 1 internal fn (_earliest_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.credential`  (lines 330–333)

```
async def credential(self, slot: str) -> str
```

**Purpose**: This lets trusted surface code read one configured credential slot for its workspace. It is used for secrets such as Slack signing tokens.

**Data flow**: It receives a slot name. If no credential store is configured it raises an error; otherwise it reads the slot value for this context’s workspace and returns the secret string.

**Call relations**: Slack surface code calls it before verifying requests, posting messages, uploading attachments, and running identity checks.

*Call graph*: called by 8 (_ctx_signing_secret, _identity, _post_ephemeral, _run_identity_proof, attach, ingest, interactive, post).


##### `SurfaceContext.credential_prompt_pending`  (lines 335–349)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: This tells a surface whether a credential prompt should still be shown to the user. It avoids re-showing prompts that were fulfilled, expired, tampered with, or meant for another workspace.

**Data flow**: It receives a sealed request and slot name. It opens and verifies the sealed request, checks that it belongs to this workspace and names the slot, then checks whether the fulfillment marker exists. It returns true only if the slot still needs an answer.

**Call relations**: Surface renderers use this before showing credential collection UI, and it relies on _fulfilled_marker_key to find the per-slot marker.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 351–361)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: This opens a sealed credential handoff and returns the trusted claims inside it. It is used when a browser callback must know which workspace, member, and slot a credential authorization belongs to.

**Data flow**: It receives a sealed request string. If there is no credential store it raises an error; otherwise it verifies and decodes the request and returns its state, or lets the credential code raise if invalid.

**Call relations**: Slack’s OAuth callback uses this to recover the credential request that started the browser flow.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 363–388)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: This verifies and stores one requested credential value. It protects against a user filling a credential for the wrong workspace, wrong slot, or wrong member.

**Data flow**: It receives a sealed request, slot name, secret value, and member id. It opens the sealed request, checks workspace, member, and slot, writes the value to the credential store, then writes a marker saying that slot was fulfilled.

**Call relations**: Slack OAuth and the built-in UFO surface call this when a user completes a credential request; it uses _fulfilled_marker_key so future renders know not to ask again.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 2 (oauth_callback, _fulfill_secret); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 390–396)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: This binds the current surface’s external installation id to this workspace. For example, it records which Slack team maps to the workspace.

**Data flow**: It receives an installation id and passes this context’s workspace id and surface name to the shared binding helper. The database binding is inserted or replaced.

**Call relations**: Slack’s OAuth callback calls this after installation so future incoming Slack requests can resolve the workspace.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.public_base_url`  (lines 399–402)

```
def public_base_url(self) -> str | None
```

**Purpose**: This exposes the deployment’s public base URL, if configured. Surfaces need it when they must show callback URLs or public links.

**Data flow**: It reads the value stored in the SurfaceContext and returns either the URL string or None.

**Call relations**: Surface code can read this property when constructing external URLs, while artifact_link uses the same stored value internally.


##### `SurfaceContext.artifact_link`  (lines 404–415)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: This creates a temporary public download link for a shared file when a surface cannot upload the file directly. The link is signed and expires.

**Data flow**: It receives a SharedArtifact. If token signing or public URL configuration is missing, it returns None. Otherwise it creates an expiry time, mints a signed artifact token, and returns a full download URL.

**Call relations**: Slack uses this for oversized artifacts, turning a stored blob into a link the message can include.

*Call graph*: called by 1 (_oversize_link_line); 2 external calls (now, mint_artifact_token).


##### `SurfaceContext._identity_member`  (lines 417–430)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: This looks up which UFO member, if any, is linked to an external user id on a given surface. It is the shared identity lookup behind several public methods.

**Data flow**: It receives a surface name and external id. It reads the surface_identity table for this workspace and returns the linked member id or None.

**Call relations**: SurfaceContext.linked_member calls it for the current surface, and SurfaceContext.adopt_identity calls it for a peer surface.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 432–433)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: This asks whether the current surface already knows an external user id as a UFO member. It lets a surface avoid relinking or guessing identity.

**Data flow**: It receives an external id and passes the current surface name plus that id to _identity_member. It returns the member id or None.

**Call relations**: Sample, Slack, web, and UFO surfaces call it while authenticating or resolving a speaker.

*Call graph*: calls 1 internal fn (_identity_member); called by 6 (_surface_ingest, _surface_live_admit, _resolve_member, interactive, channel, _authenticate).


##### `SurfaceContext._owner_email`  (lines 435–449)

```
async def _owner_email(self) -> str | None
```

**Purpose**: This finds the workspace owner’s email address, defined here as the earliest member’s email. That email’s domain is used as the workspace’s own domain.

**Data flow**: It reads members for this workspace, orders by creation time and id, and returns the first email or None if no member exists.

**Call relations**: SurfaceContext.join_member uses it for same-domain joining, and SurfaceContext.is_operator_workspace uses it for operator-only rendering.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.is_operator_workspace`  (lines 451–459)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: This checks whether the current workspace is UFO’s own operator workspace. It gates internal-only display details so customer workspaces do not see them.

**Data flow**: It reads the owner email, extracts its domain, and compares it with the configured operator domain. It returns false if there is no owner.

**Call relations**: Slack’s posting code calls it before adding operator-only accounting or debug information.

*Call graph*: calls 2 internal fn (_owner_email, _email_domain); called by 1 (post).


##### `SurfaceContext.adopt_identity`  (lines 461–484)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: This links the current surface’s external user id to a member already known by another surface. It lets the same human keep one identity across channels.

**Data flow**: It receives a peer surface name and external id. It looks up the peer member, inserts a new identity link for the current surface if found, tolerates races, and returns the member id or None.

**Call relations**: The sample live surface calls this when it wants to reuse an identity from a peer surface.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 486–515)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: This links the current surface’s external id to an existing workspace member with a matching email address. It does not create a new member.

**Data flow**: It receives an external id and email. It finds a member with that email in this workspace, inserts a surface identity link if found, tolerates duplicate-insert races, and returns the member id or None.

**Call relations**: Several surfaces call this during login or ingest, and SurfaceContext.join_member uses it before trying same-domain member creation.

*Call graph*: called by 4 (join_member, _surface_ingest, channel, _authenticate); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 517–534)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: This links a verified email to a member, creating the member first if the email belongs to the workspace’s domain. It supports teammate self-join on first contact through a trusted channel.

**Data flow**: It receives an external id and verified email. It first tries link_member. If no member exists, it compares the email domain with the owner’s domain, creates a member for matching domains, then links and returns that member id.

**Call relations**: Slack’s member resolution calls this because Slack can assert that the channel verified the user’s email.

*Call graph*: calls 3 internal fn (_owner_email, link_member, _email_domain); called by 1 (_resolve_member); 2 external calls (workspace_tx, create_member).


##### `SurfaceContext._conversation_lookup`  (lines 536–541)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: This builds the database query for finding a conversation by this surface’s queue key. The queue key is the surface-specific address, such as a channel and thread.

**Data flow**: It receives a queue key and returns a SQL select statement scoped to this workspace and surface. It does not run the query itself.

**Call relations**: SurfaceContext.find_conversation and SurfaceContext.conversation_for both use this helper so they search conversations consistently.

*Call graph*: called by 2 (conversation_for, find_conversation); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 543–549)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: This checks whether a surface queue key already has a conversation without creating one. It is useful when a surface should only respond inside an existing conversation.

**Data flow**: It receives a queue key, runs the shared lookup query, and returns the conversation id or None.

**Call relations**: Slack uses it for participation checks, debug links, and interactive actions that should not create conversations by accident.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 3 (_debug_link, _participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_for`  (lines 551–594)

```
async def conversation_for(self, queue_key: str, member_id: UUID | None) -> UUID
```

**Purpose**: This gets or creates the conversation for a surface queue key. It is the normal path before admitting a user message.

**Data flow**: It receives a queue key and optional member id. If a conversation exists, it may claim an unowned conversation for the member. If not, it creates a new conversation with the surface’s agent and returns its id, rereading on creation races.

**Call relations**: Slack, web, sample, and built-in UFO surfaces call this before calling SurfaceContext.admit.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat); 5 external calls (insert, update, workspace_tx, log, uuid4).


##### `SurfaceContext._surface_agent`  (lines 596–608)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: This chooses which agent a new surface conversation should use. It prefers the agent bound to the surface installation and falls back to the workspace’s earliest agent.

**Data flow**: It reads the surface_installation binding for this workspace and surface. If a bound agent exists it returns that id; otherwise it calls _earliest_agent.

**Call relations**: SurfaceContext.conversation_for calls it when creating a new conversation.

*Call graph*: calls 1 internal fn (_earliest_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.admit`  (lines 610–632)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: This admits an inbound surface message as a turn in an existing conversation. It is the main write action that starts UFO work from an outside channel.

**Data flow**: It receives conversation id, body text, optional idempotency key, optional context, and speaker member id. It forwards those to the injected MemberAdmitter and returns the resulting turn id.

**Call relations**: Surface routes call this after resolving identity and conversation; the underlying admitter takes care of queueing, duplicate protection, and writeback registration.

*Call graph*: called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat).


##### `SurfaceContext.connect_url`  (lines 634–640)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: This opens a connection authorization URL for a terminal connect request. It lets a member continue a secure provider connection flow from a surface.

**Data flow**: It receives a turn id and member id. It loads the installed connect flow, raises a clear error if unavailable, and asks ConnectHandoff to authorize the workspace, turn, and member.

**Call relations**: Slack interactive handlers and web event handlers call this when a turn ends by asking the user to connect an external account.

*Call graph*: called by 2 (interactive, _events); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.admitted_body`  (lines 642–667)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: This finds the exact message body that won an idempotent admission race. It helps a surface update only the UI element whose submitted answer actually landed.

**Data flow**: It receives an idempotency key. It first looks for a turn admitted with that key; if absent it checks queued inbound messages. It returns the stored body or None.

**Call relations**: Slack interactive code calls it after button or form races so it can confirm which answer should be shown as accepted.

*Call graph*: called by 1 (interactive); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 669–683)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: This finds the member who owns the conversation containing a turn. Live surfaces use it as an access check before streaming a turn.

**Data flow**: It receives a turn id, joins turn to conversation in the database, and returns the conversation’s member id or None if no turn exists.

**Call relations**: Web streaming and sample live routes call it before allowing a user to tail turn frames.

*Call graph*: called by 2 (_surface_live_admit, stream); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 685–702)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: This finds the most recently admitted turn in a conversation. It is useful when a surface reconnects or needs to resume around the newest work.

**Data flow**: It receives a conversation id, reads turns for that conversation ordered by descending sequence, and returns the newest turn id or None.

**Call relations**: Slack participation logic and the built-in UFO surface call it when they need the latest turn tied to a conversation.

*Call graph*: called by 2 (_participating_conversation, channel); 2 external calls (select, workspace_tx).


##### `SurfaceContext.tail`  (lines 704–707)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This streams live frames for one turn through the injected tailer. It is the live surface’s read side.

**Data flow**: It receives a turn id and optional resume cursor. It returns the async iterator produced by the tailer, yielding cursor and frame pairs.

**Call relations**: Debugger, sample, web, and UFO surfaces call this in event-stream routes to show live progress.

*Call graph*: called by 4 (_events, _surface_frames, channel, _events).


##### `SurfaceContext.spend_rollup`  (lines 709–713)

```
async def spend_rollup(self, window_seconds: int) -> SpendReport
```

**Purpose**: This reads workspace spending over a recent time window. Surfaces use it for spend or accounting views.

**Data flow**: It receives a window length in seconds, opens a workspace transaction, asks SpendRollup to read totals, and returns a SpendReport.

**Call relations**: The web spend view and sample live surface call this to show usage totals.

*Call graph*: called by 2 (_surface_live_admit, spend); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 715–721)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This saves a streamed file into a conversation’s workspace folder before a turn runs. The sandbox can then see the file as part of its working directory.

**Data flow**: It receives a conversation id, relative path, and async byte chunks. It validates and builds the workspace blob key, then streams chunks into the blob store without buffering the whole file.

**Call relations**: Sample ingest and Slack file-download code call this when attaching files to a conversation.

*Call graph*: calls 1 internal fn (workspace_key); called by 2 (_surface_ingest, _download_files).


##### `SurfaceContext.list_conversations`  (lines 723–772)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: This lists recent conversations in the workspace across all surfaces. It powers debug or inspection views.

**Data flow**: It receives an optional limit, builds an activity summary from turns, joins conversations and members, orders by latest activity, and returns ConversationSummary objects.

**Call relations**: The debugger surface calls it to show a workspace conversation list.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 774–790)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: This lists recent turns for one conversation in admission order. It gives a durable history of what ran.

**Data flow**: It receives a conversation id and optional limit. It queries the newest matching rows, reverses them into oldest-first order, converts each row to a Turn record, and returns them.

**Call relations**: The debugger surface calls it when showing a conversation’s turns, using _turn_query and _turn_record for consistent turn shape.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 1 (conversation_turns); 1 external calls (workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 792–842)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: This returns a detailed view of one turn, including accounting rows and child turns spawned by subagents. It is meant for inspection and debugging.

**Data flow**: It receives a turn id. It reads the turn, its child turns, and its ledger entries from the database. If the turn is missing it returns None; otherwise it returns a TurnDetail object.

**Call relations**: Debugger stream and turn pages call it to explain what happened inside a turn.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (stream, turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 844–855)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: This reads a conversation’s stored transcript, but only after confirming the conversation belongs to this workspace. That protects the unscoped blob store from cross-workspace reads.

**Data flow**: It receives a conversation id. It checks ownership, reads the transcript blob if present, decodes it, and returns the Conversation object or None.

**Call relations**: The debugger transcript view calls it; it uses _owned_conversation before touching blob data.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_transcript); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 857–868)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: This lists saved transcript compaction record numbers for a conversation. Compactions are stored summaries that replace older transcript windows.

**Data flow**: It receives a conversation id. After ownership checking, it lists compaction blobs, extracts numeric indices from their keys, sorts them, and returns the numbers.

**Call relations**: The debugger surface calls it before offering individual compaction records to inspect.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_compactions).


##### `SurfaceContext.read_compaction`  (lines 870–876)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: This reads one stored compaction record for a conversation. It is guarded by workspace ownership.

**Data flow**: It receives a conversation id and compaction index. If the conversation is not owned by this workspace it returns None; otherwise it asks transcript storage to read the record.

**Call relations**: The debugger compaction endpoint calls it after a user selects a compaction index.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (compaction_record); 1 external calls (read_compaction_record).


##### `SurfaceContext.list_workspace_files`  (lines 878–892)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: This lists files in a conversation’s workspace folder. It shows sandbox working files without exposing raw blob keys.

**Data flow**: It receives a conversation id, confirms ownership, lists blobs under the workspace prefix, strips the prefix into relative paths, and returns WorkspaceFile objects.

**Call relations**: The debugger workspace-files view calls it to browse files created or uploaded for a conversation.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_files); 1 external calls (__init__).


##### `SurfaceContext.read_workspace_file`  (lines 894–905)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: This streams one workspace file back to a caller, while ensuring the path stays inside the conversation’s workspace folder. It avoids loading the whole file into memory.

**Data flow**: It receives a conversation id and relative path. It checks ownership, validates the path with workspace_key, confirms the blob exists, and returns a byte stream or None.

**Call relations**: The debugger file endpoint calls it when a user downloads or views a workspace file.

*Call graph*: calls 2 internal fn (_owned_conversation, workspace_key); called by 1 (workspace_file).


##### `SurfaceContext.installation`  (lines 907–920)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: This reads the installation id for another surface in the same workspace. It helps render links or metadata that point back to the surface where a conversation lives.

**Data flow**: It receives a peer surface name, looks up that surface’s installation row for this workspace, and returns the installation id or None.

**Call relations**: The debugger workspace metadata view calls it when presenting surface-related context.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 922–932)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: This checks whether a conversation id belongs to the current workspace. It is a small safety gate before reading unscoped blob data.

**Data flow**: It receives a conversation id, queries the conversation table scoped by workspace id, and returns true if a row exists.

**Call relations**: Transcript, compaction, and workspace-file read/list methods call it before using the blob store.

*Call graph*: called by 5 (list_compactions, list_workspace_files, read_compaction, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 934–952)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: This builds the standard database query for turn rows used by surface read views. It keeps list and detail views selecting the same fields.

**Data flow**: It takes no input beyond the context and returns a SQL select statement containing all fields needed to build a Turn record.

**Call relations**: SurfaceContext.list_turns and SurfaceContext.turn_detail add filters to this query before running it.

*Call graph*: called by 2 (list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 954–972)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: This converts a database row into the typed Turn object used elsewhere in the system. It also parses nested context and terminal data.

**Data flow**: It receives a SQL row. It copies scalar fields, validates JSON-like context and terminal fields into TurnContext and TerminalFrame objects when present, and returns a Turn.

**Call relations**: SurfaceContext.list_turns and SurfaceContext.turn_detail use it to turn raw database rows into durable records for readers.

*Call graph*: called by 2 (list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.bind`  (lines 993–998)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: This lets a tool bind an installation only for surfaces it declared in its manifest. It prevents a tool from registering arbitrary surface names.

**Data flow**: It receives a surface name and installation id. It checks the surface is declared, gets the ambient workspace id, and calls the shared installation binding helper.

**Call relations**: Tool code uses this manifest-scoped access object; the actual database work is done by _bind_surface_installation.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 1010–1020)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: This resolves which workspace owns a surface installation before any workspace context is bound. It is the pre-request gate for shared surface ingress.

**Data flow**: It receives an installation id, reads the owner-level surface installation table for this surface, and returns the workspace id or None.

**Call relations**: Slack’s workspace resolver calls it to map an incoming Slack installation to the right UFO workspace.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 1022–1034)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: This opens a sealed credential handoff before the request has been assigned to a workspace. It is useful for OAuth callbacks carrying sealed state.

**Data flow**: It receives a sealed string. If no credential store exists, or the seal is invalid, it returns None; otherwise it returns the decoded credential request state.

**Call relations**: Slack’s workspace resolver uses it during credential-related callback handling.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 1036–1052)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: This reads a declared credential slot for a specific workspace during pre-binding authentication. It lets a resolver verify an incoming request safely.

**Data flow**: It receives a workspace id and slot. It rejects undeclared slots, requires a credential store, verifies the workspace exists inside a workspace context, then returns the credential value.

**Call relations**: Slack authentication code calls it to read the signing secret for the workspace it is trying to verify.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceDeliveryError.__init__`  (lines 1066–1070)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: This creates an error that can carry a provider’s Retry-After delay. The writeback poller uses that delay instead of blindly retrying on a fixed schedule.

**Data flow**: It receives an error message and optional retry-after seconds. It rejects negative delays, stores the delay, and initializes the runtime error message.

**Call relations**: Slack posting code raises this when Slack asks UFO to slow down; WritebackPoller._fail_or_retry later reads the delay.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 1109–1125)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This builds the database condition for writebacks ready to be claimed. It includes finished turns that are pending or whose prior claim expired.

**Data flow**: It receives the current time and returns a SQL boolean expression. The expression checks terminal turn status, pending or claimed writeback status, and expired claim times.

**Call relations**: writeback_workspaces.due uses it to find workspaces with deliverable rows, and WritebackPoller._claim uses it to claim specific rows.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `writeback_workspaces`  (lines 1128–1163)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: This creates a rotating workspace candidate reader for the writeback poller. It keeps the poller scanning bounded pages instead of trying every workspace at once.

**Data flow**: It initializes a cursor and returns the nested candidates function. That function reads workspace ids with due writebacks and advances or wraps the cursor.

**Call relations**: A WritebackPoller receives this candidate reader and calls it from run or drain to decide which workspaces to process next.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 1135–1149)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This nested helper builds the query for the next page of workspaces with due writebacks. It applies the rotating cursor when present.

**Data flow**: It reads the current time, selects workspace ids from writeback rows joined to turns, filters by _writeback_due, groups and orders them, limits the page, and optionally starts after the cursor.

**Call relations**: owner_candidates wraps this query so writeback_workspaces.candidates can read workspace ids safely from the owner scope.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 1153–1161)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This nested async function returns the next batch of workspace ids for the poller. It wraps around to the beginning when it reaches the end.

**Data flow**: It calls the wrapped due reader. If no ids are found and a cursor exists, it clears the cursor and tries again. If ids are found, it stores the last one as the next cursor and returns the batch.

**Call relations**: WritebackPoller.run and WritebackPoller.drain call this function through the poller’s candidates field.


##### `_WritebackDeliveryFailed.__init__`  (lines 1171–1174)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: This wraps a delivery exception with the phase where it happened: posting the reply or attaching files. That helps logging and retry decisions explain the failure.

**Data flow**: It receives a phase name and original exception. It stores both and initializes the error message from the original exception.

**Call relations**: WritebackPoller._deliver_claimed creates this wrapper when a surface post or attach call fails, and WritebackPoller._deliver catches it.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 1197–1230)

```
async def run(self) -> None
```

**Purpose**: This is the continuous background loop that delivers completed durable-surface replies. It keeps a bounded set of workspace drain tasks in flight.

**Data flow**: It repeatedly cleans up finished tasks, asks for candidate workspaces, starts drain tasks for new workspaces under a concurrency limit, sleeps briefly, and cancels in-flight work on shutdown.

**Call relations**: The service runs this for durable writeback delivery; it hands actual workspace processing to _drain_workspace.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 1232–1241)

```
async def drain(self) -> None
```

**Purpose**: This performs one bounded writeback drain pass instead of running forever. It is useful for tests, commands, or controlled maintenance work.

**Data flow**: It asks for candidate workspaces, drains them concurrently under a semaphore, gathers results, and raises an ExceptionGroup if any workspace drain failed.

**Call relations**: It uses the same _drain_workspace path as the long-running run loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 1243–1261)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: This processes claimed writebacks for one workspace. It binds the workspace context, claims rows, keeps claims fresh, and delivers each row.

**Data flow**: It receives a workspace id and semaphore. Inside the concurrency gate and workspace context, it claims rows, starts renewal tasks for their leases, calls _deliver for each, then cancels renewal tasks when done.

**Call relations**: WritebackPoller.run and drain call it for each candidate workspace; it coordinates _claim, _renew_claim, and _deliver.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 1263–1296)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: This claims a batch of due writebacks for the current worker. A claim is a temporary lock recorded in the database so peers do not deliver the same row.

**Data flow**: It receives a workspace id, finds due writeback turn ids, updates them to claimed with this worker id and an expiry time, and returns their turn id, existing reply reference, and last error.

**Call relations**: _drain_workspace calls it before starting delivery and lease-renewal tasks.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 1298–1330)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This wraps one writeback delivery with timing, logging, and failure handling. It decides whether a failure should retry or become terminal.

**Data flow**: It receives workspace id, turn id, optional reply reference, and renewal task. It calls _deliver_with_lease, catches lost claims or delivery failures, updates retry state through _fail_or_retry when needed, and logs the outcome.

**Call relations**: _drain_workspace calls it for each claimed row; it delegates the exact delivery sequence to _deliver_with_lease.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 1332–1361)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This runs external delivery while the claim lease is being renewed. It makes sure delivery does not continue silently after the lease renewal stops.

**Data flow**: It starts _deliver_claimed and waits for either delivery or renewal to finish first. If renewal fails first, it stops delivery and raises. If delivery succeeds, it stops renewal, waits for cleanup, and marks the writeback delivered.

**Call relations**: WritebackPoller._deliver calls it; it hands off the actual surface post and attach work to _deliver_claimed and closes success through _mark_delivered.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 1363–1385)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: This performs the actual surface delivery for a claimed writeback. It posts the reply if needed, records the provider’s reply reference, then attaches files.

**Data flow**: It receives workspace id, turn id, and optional existing reply reference. It builds the Writeback object, finds the matching surface spec, creates a SurfaceContext, calls post if no reply reference exists, records that reference, then calls attach.

**Call relations**: _deliver_with_lease calls it. If post or attach fails, it wraps the error as _WritebackDeliveryFailed so _deliver can retry correctly.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 1387–1405)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: This keeps a writeback claim alive while slow external delivery is in progress. It is like renewing a library hold before it expires.

**Data flow**: It receives a turn id, sleeps for the refresh interval, then extends the claim expiry only if the row is still claimed by this worker. If the update does not affect one row, it raises claim-lost.

**Call relations**: _drain_workspace starts one renewal task per claimed writeback, and _deliver_with_lease watches that task while delivery runs.

*Call graph*: called by 1 (_drain_workspace); 6 external calls (__init__, sleep, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 1407–1463)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: This turns stored terminal turn data into the Writeback object a surface knows how to render. It also gathers any files shared by the turn.

**Data flow**: It receives a turn id, reads the terminal frame plus conversation queue key and surface, reads shared artifact rows, validates the terminal frame, builds SharedArtifact objects, then returns the Writeback and surface name.

**Call relations**: _deliver_claimed calls it before choosing which surface post and attach handlers to invoke.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 1465–1477)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: This records the external reply reference returned after a successful post. Recording it lets recovery attach files later without posting the main reply again.

**Data flow**: It receives a turn id and reply reference, updates the claimed writeback row only if this worker still owns it, and raises claim-lost if not.

**Call relations**: _deliver_claimed calls it immediately after a successful surface post and before attachment upload.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 1479–1496)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: This marks a writeback as fully delivered and clears its claim. It is the successful final database transition.

**Data flow**: It receives a turn id, updates the row from claimed-by-this-worker to delivered, clears claim owner and expiry, and raises claim-lost if the worker no longer owns the row.

**Call relations**: _deliver_with_lease calls it only after post and attach have completed and renewal has been stopped.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 1498–1547)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: This releases a failed writeback for a later retry or marks it permanently failed if it is too old. It respects provider Retry-After guidance when available.

**Data flow**: It receives a turn id and wrapped delivery failure. It computes a retry time, truncates the saved error text, decides whether the writeback has aged out, updates the row accordingly, and returns the outcome, saved error, and next attempt time.

**Call relations**: WritebackPoller._deliver calls it after post or attach failure so the poller avoids hot-looping and eventually gives up on stale undeliverable rows.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### `core/src/ufo/surfaces/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. That matters because other parts of the project may want to refer to code inside `ufo.surfaces` using normal import paths, rather than treating the folder as loose files. Think of it like a label on a drawer: the label does not contain the tools, but it tells the system that the drawer exists and can be opened by name. Because the file is empty, it does not run setup code, expose shortcut names, or change how the surface-related modules behave. Its job is structural: it helps organize the project’s code into a clear namespace.


### `core/src/ufo/surfaces/admission.py`

`domain_logic` · `request handling and scheduled-task admission`

This file solves a boundary problem: many places can ask an agent to speak, such as a user-facing surface, an internal extension, or a scheduled task. Without one shared admission point, some callers could skip spending limits, create duplicate turns, switch a conversation to the wrong agent, or race each other and give two replies where one would do.

The main class, Admission, acts like a guarded reception desk. It locks the conversation row in the database so only one admission decision at a time can assign the next sequence number. It checks that the conversation is still bound to the expected agent. It checks whether the speaker has a valid seat when seat limits apply. It asks the spend evaluator whether the turn may run now, must be parked for later, or must be cancelled.

It also avoids duplicates. If the same idempotency key is seen again, it returns the already admitted turn instead of making another. If a conversation already has a live turn, the new message is usually folded into that turn’s inbound-message queue, so one later reply can answer everything that arrived. Durable surfaces also get a writeback row, which is a delivery reminder for returning the final reply.

Finally, when a turn is ready, this file enqueues it on DBOS, a durable workflow queue. If enqueueing fails after the database commit, it marks the dispatch as not enqueued so another retry can safely pick it up.

#### Function details

##### `Admission.admit_member`  (lines 87–106)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: This is the public path for a real member message arriving from a surface, such as a chat UI. It marks the admission as member-driven, so it can resume a pending one-time pause for that conversation if one exists.

**Data flow**: It receives the workspace, conversation, message text, optional speaker member, optional idempotency key, and optional context. It wraps the workspace and conversation into a pending-pause marker, then passes everything to the shared admission routine. The result is the UUID of the turn that accepted the message, whether that is a new turn, an existing live turn, or a resumed paused turn.

**Call relations**: Surface-facing code calls this when a member speaks. It immediately hands the work to Admission._admit, adding the pending-pause marker so the shared logic knows this message is allowed to consume a member pause.

*Call graph*: calls 1 internal fn (_admit); 1 external calls (__init__).


##### `Admission.invoke`  (lines 108–127)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: This is the public path for internal code to ask an agent to speak in an existing conversation. It requires the caller to name the agent it believes owns the conversation, so the shared admission logic can refuse if that binding is wrong.

**Data flow**: It receives the workspace, conversation, asserted agent, message text, and optional idempotency details. It sends those values to the shared admission routine without a speaker member and without a pending-pause marker. It returns the UUID of the turn that accepted the invocation.

**Call relations**: Internal jobs and extension workflows use this path. It calls Admission._admit, but because it does not pass a pending-pause marker, it cannot consume a member’s one-time pause.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission.invoke_scheduled`  (lines 129–172)

```
async def invoke_scheduled(self, workspace_id: UUID, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This admits a scheduled task firing as an inbound turn. It also formats repeated scheduled tasks with metadata about when the schedule fired, so the agent can see why the message arrived.

**Data flow**: It receives a workspace and a claimed scheduled task, plus an optional runtime instruction. It checks that the task has been claimed, rejects invalid one-time-pause instructions, builds an idempotency key from the task and scheduled fire time, and prepares the inbound message text. It then calls the shared admission routine. If another worker has already superseded this scheduled invocation, it returns null instead of a turn ID.

**Call relations**: The scheduler calls this after claiming a task. It delegates the real admission decision to Admission._admit. If _admit reports that the schedule claim is no longer valid, this method turns that internal signal into a harmless null result.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 174–692)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, p
```

**Purpose**: This is the central admission engine. It decides whether an inbound message should become a new queued turn, join an existing live turn, resume a pause, park for later, or finish immediately as cancelled.

**Data flow**: It starts with the workspace, conversation, optional asserted agent, message body, optional speaker, optional idempotency key, optional context, and markers that say whether this is member-driven or scheduled. Inside one database transaction, it locks the conversation, verifies the agent binding, verifies the speaker when present, checks scheduled-task claims, looks for duplicate idempotency keys, and decides whether the message should reuse an existing turn. If there is already a live turn, it may store the message in the inbound-message queue instead of creating a new turn. If a new turn is needed, it assigns the next conversation sequence number, checks seat access, checks spend limits, inserts the turn, and possibly inserts a writeback reminder. After the transaction commits, it enqueues the turn only if it is queued and first in line. It returns the UUID of the turn that accepted the message.

**Call relations**: Admission.admit_member, Admission.invoke, and Admission.invoke_scheduled all funnel into this method so every inbound path follows the same rules. When it decides a queued turn should run now, it calls Admission._enqueue to place the work on the durable DBOS queue. It also uses seat checks, spend checks, turn-context serialization, terminal-frame creation, and SQL database operations to make the admission decision atomically.

*Call graph*: calls 1 internal fn (_enqueue); called by 3 (admit_member, invoke, invoke_scheduled); 14 external calls (__init__, __init__, __init__, model_dump, model_validate, delete, exists, insert, select, update (+4 more)).


##### `Admission._enqueue`  (lines 694–734)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: This places an admitted queued turn onto the DBOS durable workflow queue. It also cleans up the database marker if enqueueing is cancelled or fails, so the system can safely try again later.

**Data flow**: It receives a workspace ID, conversation ID, turn ID, and optionally a workflow ID. It builds queue options, including the queue name, workflow name, workflow identity, conversation partition key, and app version, then asks DBOS to enqueue the turn. If the operation is cancelled or raises an error, it opens a database transaction and clears the turn’s dispatch-enqueued timestamp while the turn is still queued. On ordinary errors it also writes a log entry. It does not return a value.

**Call relations**: Admission._admit calls this after committing the database decision, but only for turns that should be dispatched now. This method is the bridge from the admission database record to the worker system that will actually run the turn.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 745–760)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: This is a workspace-bound helper for internal invocation. It lets jobs or extensions invoke a turn without repeatedly passing the workspace ID, while still going through the shared admission rules.

**Data flow**: It receives a conversation ID, agent ID, message, and optional idempotency key and context. It combines those with the stored workspace ID and forwards them to the underlying Admission.invoke method. It returns the UUID of the admitted turn.

**Call relations**: Internal jobs and extension workflows are given an AdmissionInvoker instead of the full Admission object. This method forwards to Admission.invoke, which then reaches the shared Admission._admit path.


##### `AdmissionInvoker.invoke_scheduled`  (lines 762–765)

```
async def invoke_scheduled(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: This is a workspace-bound helper for scheduled invocations. It lets scheduler-side code fire a scheduled task without separately carrying the workspace ID.

**Data flow**: It receives a scheduled task and optional runtime instruction. It combines them with the stored workspace ID and forwards them to Admission.invoke_scheduled. The result is either the admitted turn UUID or null if the scheduled firing was superseded.

**Call relations**: Scheduler-related code can use this narrowed capability. It hands off to Admission.invoke_scheduled, which prepares the scheduled message and then uses the shared admission engine.


##### `MemberAdmission.admit`  (lines 776–792)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: This is a workspace-bound helper for surface member messages. It gives surfaces a narrow way to admit member speech while ensuring those messages follow the member-admission rules.

**Data flow**: It receives a conversation ID, message, optional idempotency key, optional context, and a required speaker member ID value that may still be null if unresolved. It combines those with the stored workspace ID and forwards them to Admission.admit_member. It returns the UUID of the turn that accepted the message.

**Call relations**: User-facing surfaces receive this helper rather than the full Admission object. It forwards to Admission.admit_member, which marks the message as member-driven and then sends it through Admission._admit.


### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the guarded doorway for downloading files that the system has shared. A shared file may be linked from the web interface, Slack, or another surface, but the actual download route lives here so it is always available even if a particular chat or web surface is not installed.

The key idea is simple: no valid token means no file. The token is like a temporary claim ticket. It is checked using a secret stored on the running application. If the token is missing, invalid, expired, or points to a file that no longer exists, the route stops immediately with an error instead of sending any bytes.

When the token is valid, the route looks up the blob, meaning the stored file content, through the application's `BlobStore`. It then streams the file out in chunks using `StreamingResponse`. Streaming matters because the server does not load the whole file into memory at once. That keeps large downloads from overwhelming the single web process.

If the token includes a filename, the route adds a download header so the browser saves the file with that name. It also safely encodes unusual characters in the filename so downloads work across browsers.

#### Function details

##### `download`  (lines 26–53)

```
async def download(request: Request, token: str='') -> StreamingResponse
```

**Purpose**: This is the HTTP download action for artifact links. It checks the caller's token, confirms the requested stored file exists, and then streams the file back without loading it all into memory.

**Data flow**: A web request comes in with an optional `token` value. The function reads the blob store and artifact-token secret from the application state, rejects the request if the token is missing, verifies the token against the current time, checks that the named blob exists, builds a safe download filename header when one is present, and returns a streaming file response. If anything is wrong, it returns an HTTP error instead of file data.

**Call relations**: FastAPI calls this function when someone visits the artifact download path. Inside the request, it asks `verify_artifact_token` to prove the token is trustworthy, uses the blob store to find and stream the file, uses `quote` when a filename needs safe web encoding, and creates a `StreamingResponse` to send the bytes back to the client.

*Call graph*: 5 external calls (now, HTTPException, StreamingResponse, verify_artifact_token, quote).

## 📊 State Registers Touched

- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-auth-session` — The login and token state that proves who a user or client is across gateway, web, terminal, and admin requests.
- `reg-credential-store` — The encrypted secrets and credential slots used to let tools and connectors act for a workspace without exposing raw secrets.
- `reg-seat-entitlements` — The shared seat and access-limit state that decides which members may use the agent in a workspace.
- `reg-surface-installations` — The stored links between outside surfaces, workspaces, channels, conversations, and agents.
- `reg-inbound-message-queue` — The durable queue of incoming messages and uploads before they are admitted into conversation turns.
- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-turn-state` — The durable status of each unit of agent work, including whether it is waiting, running, paused, finished, failed, or cancelled.
- `reg-live-stream` — The live feed of turn updates, text chunks, tool events, costs, and final frames that clients and debuggers can watch.
- `reg-workspace-storage` — The shared file, blob, artifact, and mount state that stores workspace bytes and files shared back to users.
- `reg-scheduled-jobs` — The background job and scheduled-task state that records what should run later, what is claimed, and what repeats.
- `reg-accounting-ledger` — The usage, price, spend-cap, billing, export, and cost records used to track and limit money spent by workspaces and turns.
- `reg-outbound-delivery-queue` — The durable pending, sent, failed, and retry state for replies or notifications that must be delivered back to external surfaces such as Slack.
- `reg-turn-admission-context` — Durable per-turn requester/speaker/on-behalf-of, timezone, surface context, and authorization-link metadata used to attribute, resume, and safely handle work.
- `reg-agent-runtime-settings` — Persistent non-prompt agent configuration such as selected runtime profile, workflow/tool policy, internet-access setting, and conversation or surface agent bindings.
