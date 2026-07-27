# Slack, web, terminal, and operator routes  `stage-4.1`

This stage is the set of front doors where people reach the system from different places before their requests become normal conversation work. It sits at the edge of the main work loop: outside messages come in, are checked and reshaped, then passed inward to the shared conversation engine.

The Slack surface is the Slack doorway. It confirms that a request really came from Slack, accepts messages, installs, and button clicks, turns them into agent “turns” meaning one step in a conversation, and sends replies or generated files back to Slack.

The UFO terminal surface serves the command-line client. When a member posts a message from their shell, it starts a conversation turn and streams back small text instructions that the terminal can display.

The web surface does the same job for the browser chat. It lets a signed-in member send a message, watch the answer arrive live, and see recent spending. Together, these files act like adapters: each speaks its own outside language, then hands clean, standard conversation requests to the core system.

## Files in this stage

### User-Facing Conversation Routes
Ingress surfaces for Slack, terminal-client, and web chat requests that translate user interactions into core conversation turns and stream responses back through each channel.

### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `Slack install, inbound request handling, live turn feedback, reply delivery, file transfer`

This file is the adapter between Slack and the project’s core conversation engine. Without it, Slack could not safely talk to the agent: messages would not be verified, users could not be matched to workspace members, files would not move in or out, and replies would not land in the right Slack thread.

It does several jobs. First, it installs or connects a Slack app, either through OAuth or by using a workspace’s own Slack app token and signing secret. Then, for each incoming Slack event, it checks Slack’s signature so a forged web request cannot create a turn. It decides whether the agent was addressed, finds the right conversation thread, downloads any attached Slack files into the workspace, and admits the message as a turn for the core system to answer.

While the turn runs, it gives Slack users live feedback: a short thread status such as “Thinking…” or “Generating…”, plus occasional progress messages for very long turns. When the final answer is ready, it posts Slack-friendly message blocks, optional buttons for questions, private connection links, and shared artifacts. Large files become links instead of failed uploads. The file is like a bilingual receptionist: it checks IDs at the door, translates Slack-shaped messages into internal work, and translates the finished work back into Slack.

#### Function details

##### `_env_signing_secret`  (lines 138–142)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from the process environment. This is the fallback secret used when a workspace has not stored its own Slack signing secret.

**Data flow**: It reads one environment variable. If the variable is set and not empty, it returns that secret; otherwise it returns nothing.

**Call relations**: Workspace-specific secret lookups call this when they need the deploy-level fallback before verifying Slack requests.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 145–152)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use for a request after a workspace is already known. It prefers the workspace’s stored secret and falls back to the deploy-wide secret.

**Data flow**: It asks the surface context for the workspace credential named for Slack signing. If that slot is unset, it reads the environment fallback and returns that instead.

**Call relations**: The Slack event and interactive-button routes call this before checking request signatures.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 155–163)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret during early routing, before the request has been fully bound to a workspace context. It is used to prove that the untrusted Slack team hint really belongs to the selected workspace.

**Data flow**: It asks the shared authentication helper for the workspace’s Slack signing secret. If missing, it falls back to the deploy secret; if the workspace is unknown, it returns nothing.

**Call relations**: The workspace resolver uses this while deciding which workspace should receive an incoming Slack request.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 166–170)

```
def slack_client_id() -> str
```

**Purpose**: Returns the Slack OAuth client id for the deployed Slack app. If it is missing, it stops installation because OAuth cannot work without it.

**Data flow**: It reads the client id from the environment. A valid value is returned; a missing value becomes a runtime error.

**Call relations**: The OAuth token exchange calls this when presenting this app’s identity to Slack.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 173–177)

```
def slack_client_secret() -> str
```

**Purpose**: Returns the Slack OAuth client secret for the deployed Slack app. It prevents an install from continuing if the secret was not configured.

**Data flow**: It reads the client secret from the environment. A valid value is returned; a missing value raises an error.

**Call relations**: The OAuth exchange uses it together with the client id and Slack’s temporary authorization code.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 180–182)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the exact callback URL Slack should redirect to after an OAuth install. This must match the URL configured in Slack.

**Data flow**: It receives the public base URL of the deployment, trims any trailing slash, appends the Slack surface OAuth path, and returns the full URL.

**Call relations**: The OAuth callback uses this when exchanging Slack’s authorization code, matching the URL used during authorization.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 185–197)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Creates the “Add to Slack” link that an owner clicks to install the app. The link includes requested Slack permissions and a sealed state value tying the install to one workspace.

**Data flow**: It receives a client id, redirect URI, and state string, encodes them as URL query parameters with the needed Slack scopes, and returns Slack’s authorize URL.

**Call relations**: This helper is used by install setup code outside this file to start the OAuth flow that later returns to oauth_callback.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 201–203)

```
def __init__(self, error: str)
```

**Purpose**: Creates a Slack identity-related error while preserving Slack’s error text in a simple field. Callers use it when Slack token proof or OAuth identity data is bad.

**Data flow**: It receives an error string, stores it on the exception, and passes it to the normal runtime-error machinery.

**Call relations**: Identity proof and OAuth exchange raise this when Slack cannot prove the team and bot user ids.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `identity_blob_key`  (lines 217–218)

```
def identity_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage key for a workspace’s Slack identity record. This keeps each workspace’s Slack team and bot identity separate in shared blob storage.

**Data flow**: It receives a workspace id and formats it into a stable blob path. The returned string is used for reads and writes.

**Call relations**: Identity readers, identity proof, and OAuth install all use this so they agree on where the Slack identity lives.

*Call graph*: called by 3 (resolve, oauth_callback, read_identity).


##### `bot_token_fingerprint`  (lines 221–222)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Makes a non-reversible fingerprint of a Slack bot token. This lets the system detect when a stored identity belongs to an old token without storing the token in the identity record.

**Data flow**: It receives the bot token, hashes its bytes with SHA-256, and returns the hexadecimal hash string.

**Call relations**: Identity proof, identity reads, and OAuth install use it to bind stored team and bot ids to the current Slack token.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 225–239)

```
async def read_identity(blob: BlobStore, workspace_id: UUID, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack identity for a workspace, but only accepts it if it matches the current bot token. This avoids routing events using stale identity data after a reinstall.

**Data flow**: It builds the identity blob key, checks whether it exists, reads and parses it, compares the stored token fingerprint to the current token fingerprint, and returns the identity or nothing.

**Call relations**: The normal request path and the manifest-app identity resolver call this before deciding whether identity proof is still needed.

*Call graph*: calls 4 internal fn (exists, get, bot_token_fingerprint, identity_blob_key); called by 2 (resolve, _identity).


##### `_identity`  (lines 242–247)

```
async def _identity(ctx: SurfaceContext) -> SlackIdentity | None
```

**Purpose**: Gets the Slack identity for the current workspace if the workspace has a Slack bot token and a matching stored identity. It is the main request-time identity lookup.

**Data flow**: It asks the context for the Slack bot token. If the token slot is unset it returns nothing; otherwise it reads the identity record tied to that token.

**Call relations**: Inbound events and button clicks call this before trusting team ids or recognizing the bot’s own user id.

*Call graph*: calls 2 internal fn (credential, read_identity); called by 2 (ingest, interactive).


##### `SlackIdentityResolver.resolve`  (lines 261–269)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Ensures a bring-your-own Slack app has a proven identity record. It reuses a valid stored record or proves the token with Slack and saves the result.

**Data flow**: It reads any existing identity. If none matches the token, it asks Slack to prove the token, then writes the new identity JSON into blob storage and returns it.

**Call relations**: Background identity proof uses this when a workspace has credentials but no usable Slack identity record yet.

*Call graph*: calls 3 internal fn (_prove, identity_blob_key, read_identity).


##### `SlackIdentityResolver._prove`  (lines 271–296)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack what team and bot user a pasted bot token belongs to. This is the safety check for bring-your-own app installs.

**Data flow**: It sends the bot token to Slack’s auth.test API, checks the response is successful and well-shaped, validates the team and bot ids, and returns a SlackIdentity.

**Call relations**: SlackIdentityResolver.resolve calls this only when no current stored identity can be trusted.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 302–315)

```
def _prove_identity_in_background(ctx: SurfaceContext) -> None
```

**Purpose**: Starts identity proof without blocking the Slack request that discovered the missing identity. It limits work to one background task per workspace.

**Data flow**: It checks an in-memory task table. If no proof is already running, it creates a task and records it until the task finishes.

**Call relations**: Inbound event and interactive routes call this when credentials exist but the Slack identity record is absent.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 2 (ingest, interactive); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 311–313)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished background identity-proof task from the in-memory tracker. This lets later requests retry proof if needed.

**Data flow**: It receives the completed task, checks that it is still the task recorded for the workspace, and deletes that tracker entry.

**Call relations**: It is attached as the completion callback for the task created by _prove_identity_in_background.


##### `_run_identity_proof`  (lines 318–325)

```
async def _run_identity_proof(ctx: SurfaceContext) -> None
```

**Purpose**: Performs the background identity proof and logs failures instead of letting them escape. This keeps Slack request handling resilient.

**Data flow**: It reads the bot token from the workspace, creates a SlackIdentityResolver, and asks it to resolve. Known identity errors and unexpected errors are logged.

**Call relations**: _prove_identity_in_background schedules this task when identity proof should happen asynchronously.

*Call graph*: calls 1 internal fn (credential); called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `url_verified_blob_key`  (lines 328–334)

```
def url_verified_blob_key(workspace_id: UUID) -> str
```

**Purpose**: Builds the storage key for the marker that says Slack successfully reached this deployment with a verified request. This is used as a connection-health signal.

**Data flow**: It receives a workspace id and returns the blob path for that workspace’s Slack URL verification marker.

**Call relations**: _mark_url_verified uses this when recording that a signed Slack request was accepted.

*Call graph*: called by 1 (_mark_url_verified).


##### `signing_secret_fingerprint`  (lines 337–340)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a non-reversible fingerprint of the signing secret used to verify Slack. This lets the system notice secret rotation without storing the secret in the marker.

**Data flow**: It receives the signing secret, hashes it with SHA-256, and returns the hash string.

**Call relations**: _mark_url_verified writes this fingerprint so setup checks know which secret was actually proven live.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 354–381)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s temporary OAuth code for a workspace bot token and identity details. It refuses malformed or failed Slack responses so broken installs are not saved.

**Data flow**: It sends the code, client id, client secret, and redirect URI to Slack. It validates the returned access token, team id, and bot user id, then returns a SlackInstall object.

**Call relations**: oauth_callback calls this after validating the sealed install state from the browser redirect.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `SlackConversationSearch.run`  (lines 461–475)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations for channels or DMs matching a user’s query. It returns matching conversations and warns if the scan was capped.

**Data flow**: It normalizes the query, lists conversations, resolves people for DMs, builds simplified conversation records, filters by searchable text, and returns matches plus a truncated flag.

**Call relations**: This is the public entry for SlackConversationSearch; it coordinates listing, people lookup, and conversation shaping.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 477–496)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches pages of Slack conversations up to a fixed limit. The limit prevents a huge or malformed workspace from making search run forever.

**Data flow**: It repeatedly calls Slack conversations.list with pagination parameters, collects channel objects, tracks the next cursor, and returns the list plus whether more pages remained.

**Call relations**: SlackConversationSearch.run calls this before filtering conversations by query.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 498–506)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list request. It includes conversation types, archive filtering, page size, and optional cursor.

**Data flow**: It receives the current cursor string and returns a dictionary of parameters. If the cursor is empty, no cursor field is included.

**Call relations**: SlackConversationSearch._list calls this for every page request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 508–511)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s pagination cursor from a list response. If Slack omits or malforms it, the search treats the page as the end.

**Data flow**: It reads response_metadata.next_cursor from the payload and returns it only if it is a string; otherwise it returns an empty string.

**Call relations**: SlackConversationSearch._list uses it to decide whether to fetch another page.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 513–541)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves the human labels for DM and group DM members so DMs can be searched by person. It avoids repeated user lookups and caps how many DMs it resolves.

**Data flow**: It scans listed conversations for DMs, reads their member ids, looks up each unique non-bot user, formats labels, and returns labels by conversation plus a capped flag.

**Call relations**: SlackConversationSearch.run calls this after listing conversations, and it uses _members, _slack_user, and _label to build searchable people text.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 543–550)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as public channel, private channel, group DM, or one-to-one DM. This simplifies later search logic.

**Data flow**: It reads Slack boolean fields such as is_im, is_mpim, and is_private, then returns one of the internal kind strings.

**Call relations**: Conversation shaping, member lookup, and people resolution call this whenever they need to treat DMs differently from channels.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 552–566)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets the member ids for a DM-style conversation. One-to-one DMs carry the user directly; group DMs need a Slack API call.

**Data flow**: It receives a raw conversation and id. For one-to-one DMs it returns the embedded user id; for group DMs it calls conversations.members and returns the listed member ids.

**Call relations**: SlackConversationSearch._people calls this while preparing person labels for searchable DMs.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 568–573)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Turns a Slack user lookup into a readable search label. It prefers name plus email when available, but always falls back to something identifiable.

**Data flow**: It receives an optional SlackUser and the raw Slack user id. It returns “name (email)”, name, email, or the user id.

**Call relations**: SlackConversationSearch._people uses it after each user lookup to build the text matched by the query.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 575–592)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts a raw Slack conversation object into the smaller internal SlackConversation model. Invalid or unusable raw entries are skipped.

**Data flow**: It checks that the raw value is a dictionary with a string id, extracts name, kind, purpose, topic, membership, and resolved people, then returns a SlackConversation.

**Call relations**: SlackConversationSearch.run calls this for each listed item before applying the query filter.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 594–596)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Extracts the text value from Slack’s nested purpose or topic field. Missing or malformed fields become empty text.

**Data flow**: It receives a field object, reads its value key if the object is a dictionary, and returns the value only when it is a string.

**Call relations**: SlackConversationSearch._conversation uses this to normalize Slack’s purpose and topic data.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 723–738)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a request really came from Slack and is recent enough to avoid replay attacks. A replay attack is someone resending an old valid request.

**Data flow**: It reads Slack timestamp and signature headers, checks the timestamp format and age, rebuilds Slack’s signed message from the raw body, computes the expected HMAC signature, and raises if anything does not match.

**Call relations**: Workspace resolution, event ingest, and interactive button handling call this before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 745–764)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw Slack request body with a size limit. The raw bytes are needed for signature verification, so they must not be altered by JSON parsing first.

**Data flow**: It checks whether the request body is already cached, otherwise streams chunks from the request, totals their size, rejects oversized bodies, caches the bytes, and returns them.

**Call relations**: All Slack routes that verify or inspect incoming requests use this helper.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 767–776)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack’s URL verification handshake and extracts the challenge string Slack expects back. This lets Slack confirm the endpoint exists.

**Data flow**: It parses the raw body as JSON, checks for type url_verification, and returns the challenge text or nothing for normal events.

**Call relations**: Workspace resolution and event ingest call this before treating a request as a real event.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 779–796)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Pulls a Slack team id from an incoming event or interactive payload before the workspace is bound. The id is only a hint until the signature is checked.

**Data flow**: It tries to parse the raw body as JSON, then as a form-encoded interactive payload. It extracts team_id or team.id and validates its shape before returning it.

**Call relations**: resolve_workspace uses this hint to find the possible workspace and then verifies the signature with that workspace’s secret.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 799–800)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Turns a Slack team id into the installation binding key used by the core surface system. This lets one Slack team map to one workspace.

**Data flow**: It receives a Slack team id and prefixes it with team: to produce a stable installation id.

**Call relations**: OAuth install writes this binding, and workspace resolution reads it for incoming Slack requests.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 803–841)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which workspace a Slack request belongs to before normal route handling begins. It also answers Slack’s unsigned URL verification challenge when no workspace can be bound yet.

**Data flow**: For OAuth GET callbacks, it opens the sealed state and returns the named workspace. For POSTs, it reads the raw body, handles URL verification, extracts the team hint, finds the bound workspace, verifies the signature, and returns the workspace id or a response/none.

**Call relations**: The surface router calls this shared resolver before handing requests to oauth_callback, ingest, or interactive.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 844–847)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state belongs to the Slack OAuth install flow. This prevents another credential request from being accepted as a Slack install.

**Data flow**: It reads the state claims and returns true only when the payload marker and requested slot match the Slack bot-token install shape.

**Call relations**: OAuth callback and workspace resolution both use it when interpreting sealed browser state.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 850–855)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the internal conversation key for a Slack message. DMs are keyed by channel, while channel conversations are keyed by channel plus thread root.

**Data flow**: It receives a channel id, root timestamp, and DM flag. It returns just the channel for DMs or channel:root_ts for channel threads.

**Call relations**: _to_inbound uses this key before finding or creating the matching core conversation.

*Call graph*: called by 1 (_to_inbound).


##### `slack_message_addressed`  (lines 858–865)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message directly asks the agent to respond. DMs always count; channel messages count when Slack reports an app mention or the text contains the bot mention.

**Data flow**: It reads the event type, DM flag, bot user id, and text. It returns true for direct address and false otherwise.

**Call relations**: _to_inbound uses this to admit new conversations only when the agent was addressed, while still allowing follow-up replies in active threads.

*Call graph*: called by 1 (_to_inbound).


##### `slack_reply_body`  (lines 868–918)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool=False) -> bytes
```

**Purpose**: Builds the JSON body for Slack chat.postMessage. It chooses between rich Slack blocks and plain text while staying under Slack’s size limits.

**Data flow**: It receives channel, optional thread, reply text, optional metadata, optional action blocks, and formatting options. It chunks text into Slack blocks when safe, appends actions and metadata, encodes JSON, and falls back or raises if too large.

**Call relations**: Final reply posting and long-turn progress messages call this before sending to Slack.

*Call graph*: called by 2 (_post, post); 1 external calls (dumps).


##### `_mrkdwn_section`  (lines 921–922)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates one Slack Block Kit section containing markdown text. It clips the text to Slack’s section size limit.

**Data flow**: It receives text and returns a dictionary shaped as a Slack section block with markdown content.

**Call relations**: slack_ask_blocks uses it to render question titles and option descriptions.

*Call graph*: called by 1 (slack_ask_blocks).


##### `slack_ask_blocks`  (lines 925–977)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question into Slack blocks, including answer buttons when the question is simple enough. More complex questions are shown as text so the user can answer in the thread.

**Data flow**: It receives an AskUserInput or nothing. It builds title and question sections, adds button rows for single-choice questions with limited options, and returns block dictionaries or nothing.

**Call relations**: post calls this when a turn ends by asking the user a question.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (post).


##### `slack_connect_blocks`  (lines 980–1001)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a Slack button for a private connection or authorization request. The button starts a separate secure handoff instead of putting secrets in chat.

**Data flow**: It receives a connect request and turn id. If no request exists it returns nothing; otherwise it returns an actions block with a connect button carrying the turn id.

**Call relations**: post uses this when a terminal turn asks a user to connect an external provider.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1004–1008)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required non-empty string field from Slack data. It fails early if Slack’s payload is missing something the code cannot safely continue without.

**Data flow**: It receives a mapping and field name, checks that the field is a non-empty string, and returns it or raises a ValueError.

**Call relations**: _to_inbound and _to_click use it while turning raw Slack payloads into internal objects.

*Call graph*: called by 2 (_to_click, _to_inbound).


##### `_inbound_files`  (lines 1011–1023)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts usable attached files from a Slack message. It skips hidden or tombstoned files and limits how many files can enter one turn.

**Data flow**: It reads the event’s files list, keeps up to the configured maximum with a name and private download URL, and returns InboundFile objects.

**Call relations**: _to_inbound calls this so ingest can later stream those files into the workspace.

*Call graph*: called by 1 (_to_inbound); 1 external calls (__init__).


##### `oauth_callback`  (lines 1026–1070)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the Slack OAuth install after the owner clicks “Add to Slack.” It stores the bot token, binds the Slack team to the workspace, and records the app identity.

**Data flow**: It reads error, state, and code from the browser request, validates sealed state, exchanges the code with Slack, binds the team installation, fulfills the bot-token credential request, writes identity metadata, and returns a small HTML status page.

**Call relations**: This is the OAuth route reached after resolve_workspace has identified the workspace from the sealed state.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _install_page, _is_install_state, bot_token_fingerprint, identity_blob_key, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 1 external calls (__init__).


##### `_install_page`  (lines 1073–1080)

```
def _install_page(message: str, status: int) -> Response
```

**Purpose**: Builds a simple HTML page telling the owner whether Slack install succeeded or failed. It escapes the message so it is safe to show in a browser.

**Data flow**: It receives display text and an HTTP status, escapes the text, wraps it in minimal HTML, and returns an HTTP response.

**Call relations**: oauth_callback uses it for every user-facing outcome of the install flow.

*Call graph*: called by 1 (oauth_callback); 2 external calls (escape, Response).


##### `_mark_url_verified`  (lines 1086–1100)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this workspace with a request signed by the current secret. This helps setup know the Slack request URL is truly connected.

**Data flow**: It fingerprints the signing secret, skips if this process already wrote the same fingerprint, writes a timestamped marker to blob storage, and updates the local cache if successful.

**Call relations**: Event ingest and interactive handling call this after signature verification.

*Call graph*: calls 2 internal fn (signing_secret_fingerprint, url_verified_blob_key); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1103–1165)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack event callbacks, especially messages. It verifies the request, decides whether to admit a turn, downloads files, and starts live feedback tasks.

**Data flow**: It reads the raw body, gets and checks the signing secret, handles URL verification, loads identity, converts the event to an Inbound message, fetches sender and context, resolves the member, downloads files, admits the turn, and starts status/progress tracking.

**Call relations**: This is the main Slack Events API route; it hands accepted user messages into the core conversation system and returns Slack’s quick acknowledgement.

*Call graph*: calls 19 internal fn (admit, conversation_for, credential, _ambient_context, _ctx_signing_secret, _download_files, _files_note, _identity, _mark_url_verified, _prove_identity_in_background (+9 more)); 4 external calls (gather, loads, JSONResponse, Response).


##### `_author_is_foreign`  (lines 1168–1175)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages from users in another Slack organization inside a shared Slack Connect channel. The app skips those users because it cannot safely resolve them as local members.

**Data flow**: It compares source_team or user_team from the event to the bound team id. If a different author team is present, it returns true.

**Call relations**: _to_inbound calls this before admitting any event as a user turn.

*Call graph*: called by 1 (_to_inbound).


##### `_to_inbound`  (lines 1178–1214)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Turns a raw Slack event payload into the smaller Inbound object used by admission. It also filters out events the agent should ignore.

**Data flow**: It checks event type, bot messages, subtypes, user id, foreign authors, addressing, threading, and participation. It builds queue keys, gathers file references, and returns Inbound or nothing.

**Call relations**: ingest calls this after signature and identity checks, before resolving users and creating turns.

*Call graph*: calls 6 internal fn (_author_is_foreign, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 1 external calls (__init__).


##### `_participating_conversation`  (lines 1217–1228)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether the agent is already participating in a Slack thread. A thread counts only after at least one turn has been admitted.

**Data flow**: It looks up the conversation by queue key. If found, it checks for a latest turn and returns the conversation id only when a turn exists.

**Call relations**: _to_inbound uses this to allow unmentioned replies only in threads where the agent has already joined.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1231–1261)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches basic Slack user information such as name, confirmed email, and timezone. It is best-effort so Slack lookup trouble does not stop most channel turns.

**Data flow**: It calls Slack users.info with the bot token, validates the payload, keeps email only if Slack says it is confirmed, and returns a SlackUser or nothing.

**Call relations**: Ingest uses it for sender context and member resolution; interactivity uses it when a button click comes from an unlinked member; conversation search uses it for DM labels.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_people, ingest, interactive); 2 external calls (__init__, AsyncClient).


##### `_turn_context`  (lines 1264–1278)

```
def _turn_context(sender: SlackUser | None) -> TurnContext
```

**Purpose**: Creates the context attached to an admitted turn, mainly who sent it and their timezone. Invalid timezones are dropped rather than failing the message.

**Data flow**: It receives optional SlackUser data, formats a sender label from name and email, tries to build a TurnContext with timezone, and falls back to sender-only context if validation fails.

**Call relations**: ingest passes this context into core turn admission.

*Call graph*: called by 1 (ingest); 1 external calls (__init__).


##### `_resolve_member`  (lines 1281–1299)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a workspace member when possible. Existing links win; otherwise a confirmed same-domain email can join or link the teammate.

**Data flow**: It asks for an existing linked member. If none exists, it uses the SlackUser email when available to join/link the member; missing user data in a DM raises because the speaker cannot be identified.

**Call relations**: ingest uses this for message speakers, and interactive uses it for answer button clicks.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 2 (ingest, interactive); 1 external calls (__init__).


##### `_ambient_context`  (lines 1302–1349)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> str
```

**Purpose**: Fetches recent Slack messages that came before a conversation-starting mention, so the agent can understand what it was pulled into. It avoids duplicating context once the thread is already a conversation.

**Data flow**: It decides whether to read recent channel history or thread replies, calls Slack with a short timeout, and passes returned messages to _ambient_digest. Failures return empty context.

**Call relations**: ingest runs this alongside the sender lookup before admitting a new conversation-starting turn.

*Call graph*: calls 2 internal fn (_ambient_digest, _slack_ok); called by 1 (ingest); 1 external calls (AsyncClient).


##### `_ambient_digest`  (lines 1352–1389)

```
def _ambient_digest(messages: list[object], bot_user_id: str, header: str) -> str
```

**Purpose**: Turns fetched Slack history into a bounded plain-text context digest. It keeps useful member messages and skips bot messages or messages that already addressed the bot.

**Data flow**: It filters and timestamps messages, clips each message, sorts them by time, trims the whole digest to a maximum size while preserving the anchor and newest lines, and returns text with a header.

**Call relations**: _ambient_context calls this after retrieving channel or thread history.

*Call graph*: called by 1 (_ambient_context); 1 external calls (fromtimestamp).


##### `_slack_download_host_ok`  (lines 1392–1394)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks that a private file download URL belongs to Slack before sending the bot token. This prevents leaking the token to another host.

**Data flow**: It parses the URL hostname, lowercases it, and returns true only for slack.com or a slack.com subdomain.

**Call relations**: _stream_download uses this guard before starting an authenticated file download.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 1397–1415)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams an inbound Slack file into the workspace without loading the whole file into memory. It also enforces a maximum download size.

**Data flow**: It verifies the host, opens an authenticated streaming GET request, yields chunks as they arrive, counts total bytes, and raises if the file exceeds the allowed size.

**Call relations**: _download_files passes this stream to the workspace file writer for each inbound attachment.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 1427–1443)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack message attachments into the workspace for the admitted conversation. Oversized files are skipped cleanly instead of writing partial files.

**Data flow**: It gives each file a safe unique inbox name, streams the download into the workspace, records delivered names and skipped Slack names, and returns a DownloadedFiles summary.

**Call relations**: ingest calls this before admitting the turn body note about available files.

*Call graph*: calls 3 internal fn (write_workspace_file, _inbox_name, _stream_download); called by 1 (ingest); 1 external calls (__init__).


##### `_inbox_name`  (lines 1446–1456)

```
def _inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Creates a safe, unique filename for a Slack attachment in the workspace inbox. It strips path parts so Slack-provided names cannot choose arbitrary paths.

**Data flow**: It takes the final path component, replaces empty or dangerous names with file, adds numeric suffixes until unused, records the chosen name, and returns it.

**Call relations**: _download_files uses it for every inbound file.

*Call graph*: called by 1 (_download_files).


##### `_files_note`  (lines 1459–1470)

```
def _files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Builds the text note added to the user’s turn explaining which Slack files were saved or skipped. This tells the model what file paths it can inspect.

**Data flow**: It receives delivered and skipped file names, formats workspace inbox paths and size-limit warnings, and returns an appended note or an empty string.

**Call relations**: ingest appends this note to the admitted message body after downloading attachments.

*Call graph*: called by 1 (ingest).


##### `ThreadStatus.run`  (lines 1497–1504)

```
async def run(self) -> None
```

**Purpose**: Runs live Slack thread status updates for one turn from start to finish. It sets an initial thinking status, follows turn events, and clears the status at the end.

**Data flow**: It reads the bot token, opens an HTTP client, writes “Thinking…”, follows the turn stream, and always sends a clear status in the end.

**Call relations**: _run_status calls this inside a background task started by _track_status.

*Call graph*: calls 2 internal fn (_follow, _set); called by 1 (_run_status); 1 external calls (AsyncClient).


##### `ThreadStatus._set`  (lines 1506–1535)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> None
```

**Purpose**: Writes one Slack assistant thread status, unless this turn is no longer the newest writer for that thread. This prevents an older turn from clearing or overwriting a newer turn’s status.

**Data flow**: It checks the thread-writer table, builds Slack’s status request body with optional loading message text, posts it to Slack, and logs the write.

**Call relations**: ThreadStatus.run and ThreadStatus._follow call this for initial, changing, refreshed, and cleared status text.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._follow`  (lines 1537–1572)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches live turn frames and converts them into short Slack status messages. It refreshes quiet statuses so Slack does not expire them.

**Data flow**: It tails the turn stream, waits for frames or refresh timeouts, maps tool calls, skill loads, and text deltas to status text, rate-limits rapid changes, and stops on terminal frames.

**Call relations**: ThreadStatus.run calls this after setting the initial status.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_track_status`  (lines 1579–1601)

```
def _track_status(ctx: SurfaceContext, turn_id: UUID, queue_key: str, message_ts: str) -> None
```

**Purpose**: Starts one background Slack status task for an admitted turn. It deduplicates repeated Slack deliveries and marks the newest turn as the only status writer for its thread.

**Data flow**: It derives channel and thread timestamp from the queue key, stores the turn as thread writer, creates a ThreadStatus task, records it by turn id, and installs cleanup.

**Call relations**: ingest and interactive call this immediately after admitting a turn.

*Call graph*: calls 1 internal fn (_run_status); called by 2 (ingest, interactive); 2 external calls (__init__, create_task).


##### `_track_status._untrack`  (lines 1596–1599)

```
def _untrack(_done: asyncio.Task[None]) -> None
```

**Purpose**: Cleans up the in-memory status tracking when a status task finishes. It also removes the thread-writer marker if this turn is still the current writer.

**Data flow**: It removes the turn’s task from the status table and conditionally deletes the writer entry for the thread.

**Call relations**: It is registered as the completion callback for the task created by _track_status.


##### `_run_status`  (lines 1604–1614)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Runs a ThreadStatus task and logs any failure. Status is helpful but not durable, so errors should not crash request handling.

**Data flow**: It awaits status.run. If anything raises, it logs the turn, channel, thread, and error.

**Call relations**: _track_status schedules this as the background task wrapper.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 1626–1630)

```
def __post_init__(self) -> None
```

**Purpose**: Validates that the progress-report schedule is sensible. The first interval must be positive, and the cap cannot be smaller than the first interval.

**Data flow**: It reads base_seconds and cap_seconds from the dataclass instance and raises ValueError if they are invalid.

**Call relations**: _track_progress creates ProgressCadence before starting a ThreadProgress task.


##### `ProgressCadence.intervals`  (lines 1632–1644)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait times for long-turn progress posts. The waits double with elapsed time until they reach a cap.

**Data flow**: It starts with the base interval, yields it, adds it to elapsed time, then yields the lesser of elapsed time and the cap forever.

**Call relations**: ThreadProgress._follow uses this generator to decide when the next progress checkpoint should happen.


##### `TurnActivity.tool`  (lines 1661–1665)

```
def tool(self, tool: str, description: str) -> None
```

**Purpose**: Records that the turn started a tool call. It closes any streamed narration first so progress messages can mention the last completed narration and current tool.

**Data flow**: It receives a tool name and description, stores a readable current activity, clips it to Slack’s limit, and increments that tool’s count.

**Call relations**: ThreadProgress._follow calls this when it sees a ToolCall frame.

*Call graph*: calls 1 internal fn (_close_narration).


##### `TurnActivity.skill`  (lines 1667–1669)

```
def skill(self, skill: str) -> None
```

**Purpose**: Records that the turn is loading a skill. A skill is a capability package the agent needs before continuing.

**Data flow**: It closes any streamed narration, formats a loading message for the skill, clips it, and stores it as the current activity.

**Call relations**: ThreadProgress._follow calls this when it sees a SkillLoad frame.

*Call graph*: calls 1 internal fn (_close_narration).


##### `TurnActivity.stream`  (lines 1671–1672)

```
def stream(self, text: str) -> None
```

**Purpose**: Records streamed text chunks from the turn. The text itself is not posted as progress, but its size can show that writing is happening.

**Data flow**: It receives a text chunk and appends it to the in-progress streaming buffer.

**Call relations**: ThreadProgress._follow calls this when it sees TextDelta frames.


##### `TurnActivity.checkpoint`  (lines 1674–1675)

```
def checkpoint(self) -> None
```

**Purpose**: Resets the per-interval tool tally after a progress post attempt. This makes the next progress message describe activity since the last checkpoint.

**Data flow**: It clears the counter of tool calls while keeping narration, current activity, and streaming state.

**Call relations**: ThreadProgress._follow calls this after each timed progress checkpoint.


##### `TurnActivity.current_step`  (lines 1677–1686)

```
def current_step(self) -> str
```

**Purpose**: Returns the best short description of what the turn is doing now. Streaming text outranks the last tool call because it means the agent is actively writing.

**Data flow**: It sums buffered streaming text length. If any text is in flight, it returns a writing-size message; otherwise it returns the stored activity.

**Call relations**: TurnActivity.report uses this to build the “Now” line in progress posts.

*Call graph*: called by 1 (report).


##### `TurnActivity._close_narration`  (lines 1688–1692)

```
def _close_narration(self) -> None
```

**Purpose**: Moves streamed text into the latest completed narration when the turn switches to a tool or skill. This avoids quoting unfinished or final-answer text as progress.

**Data flow**: It joins and trims buffered streaming text, clears the buffer, and if text exists stores a clipped version as narration.

**Call relations**: TurnActivity.tool and TurnActivity.skill call this before recording the new activity.

*Call graph*: called by 2 (skill, tool).


##### `TurnActivity.report`  (lines 1694–1721)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds the human-readable progress message for a checkpoint, or skips if there has been no signal. It summarizes narration, current step, elapsed time, and tool activity.

**Data flow**: It gathers current step, formats elapsed minutes or hours, adds narration and activity lines when present, adds either tool tallies or quiet/writing timing, and returns text or nothing.

**Call relations**: ThreadProgress._post calls this before deciding whether to send a progress message to Slack.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 1748–1751)

```
async def run(self) -> None
```

**Purpose**: Runs interim progress reporting for one long turn. It opens the Slack client and delegates the timing and tail-following loop.

**Data flow**: It reads the bot token, creates an HTTP client, and calls _follow with both.

**Call relations**: _run_progress calls this inside the background progress task.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._follow`  (lines 1753–1787)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches a turn and posts progress at scheduled checkpoints until the turn ends. It keeps collecting activity between posts.

**Data flow**: It starts a clock, sets the first deadline, tails turn frames, updates TurnActivity for tools, skills, and text, posts when deadlines pass, resets tool tallies, and exits on terminal frames.

**Call relations**: ThreadProgress.run calls this after preparing Slack access.

*Call graph*: calls 1 internal fn (_post); called by 1 (run); 5 external calls (__init__, ensure_future, gather, wait, monotonic).


##### `ThreadProgress._post`  (lines 1789–1835)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float) -> None
```

**Purpose**: Sends one long-turn progress update to Slack, if there is useful activity to report. A failed post is logged and does not stop later checkpoints.

**Data flow**: It asks TurnActivity for report text. If none exists it logs a skip; otherwise it builds a Slack message body for the channel/thread, posts it, and logs success or failure.

**Call relations**: ThreadProgress._follow calls this whenever a progress deadline is reached.

*Call graph*: calls 3 internal fn (report, _slack_ok, slack_reply_body); called by 1 (_follow); 2 external calls (post, log).


##### `_track_progress`  (lines 1841–1868)

```
def _track_progress(ctx: SurfaceContext, turn_id: UUID, queue_key: str) -> None
```

**Purpose**: Starts one background progress reporter for an admitted turn. Because progress posts are real messages, it is careful to avoid duplicates on retries or mention twin deliveries.

**Data flow**: It checks whether this process already has a reporter for the turn, creates ThreadProgress with the standard cadence, starts the background task, records it, and removes it when done.

**Call relations**: ingest and interactive call this only for the request that should own progress reporting.

*Call graph*: calls 1 internal fn (_run_progress); called by 2 (ingest, interactive); 3 external calls (__init__, __init__, create_task).


##### `_run_progress`  (lines 1871–1884)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Wraps a ThreadProgress task and logs fatal failures. Individual post failures are handled elsewhere; this catches setup or stream-level failure.

**Data flow**: It awaits progress.run and logs the turn, queue key, and error if the reporter cannot continue.

**Call relations**: _track_progress schedules this wrapper as the background progress task.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `interactive`  (lines 1918–1987)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack button clicks, including question answers and private connect buttons. It verifies the request, admits answer clicks as turns, and responds privately for connect clicks.

**Data flow**: It reads and verifies the raw form body, loads identity, parses the click, marks URL verification, resolves the member, then either creates an ephemeral connect link or admits an answer turn, starts status/progress, and schedules message rewriting.

**Call relations**: This is the Slack interactivity route; it feeds button answers back into the same conversation flow as normal messages.

*Call graph*: calls 20 internal fn (admit, admitted_body, connect_url, conversation_for, credential, find_conversation, linked_member, _ctx_signing_secret, _ephemeral_in_background, _identity (+10 more)); 2 external calls (JSONResponse, Response).


##### `_rewrite_in_background`  (lines 1993–1996)

```
def _rewrite_in_background(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Starts a background task to update an answered question message. This lets the interactive route acknowledge Slack quickly.

**Data flow**: It creates a task for _run_rewrite, stores it in a set so it is not lost, and removes it when complete.

**Call relations**: interactive calls this after confirming that this click’s answer was the one admitted.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 1999–2003)

```
async def _run_rewrite(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Runs the Slack message rewrite for an answer click and logs any failure. The user’s answer turn has already been admitted, so rewrite failure should not undo it.

**Data flow**: It calls _replace_buttons_with_answer with the bot token and click. If an error occurs, it logs the message timestamp and error.

**Call relations**: _rewrite_in_background schedules this helper as the background task.

*Call graph*: calls 1 internal fn (_replace_buttons_with_answer); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 2006–2009)

```
def _ephemeral_in_background(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Starts a background task to send a private Slack response for a connect button click. This keeps Slack’s interactivity acknowledgement fast.

**Data flow**: It creates a task for _post_ephemeral, stores it in the shared background task set, and removes it when complete.

**Call relations**: interactive calls this after preparing the private connect response text.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 2012–2037)

```
async def _post_ephemeral(ctx: SurfaceContext, click: ConnectClick, text: str) -> None
```

**Purpose**: Sends a Slack ephemeral message, visible only to the clicking user, with the result of a connect button click. This keeps authorization links private.

**Data flow**: It reads the bot token, posts chat.postEphemeral to the channel and optional thread, and logs any failure instead of raising.

**Call relations**: _ephemeral_in_background schedules this after interactive handles a ConnectClick.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_click`  (lines 2040–2095)

```
def _to_click(raw: bytes, identity: SlackIdentity) -> AnswerClick | ConnectClick | None
```

**Purpose**: Parses a signed Slack interactive payload into either an answer click or a connect click. It filters out unrelated or wrong-team actions.

**Data flow**: It decodes the form payload, parses JSON, checks block action shape and team id, reads user, channel, message, action id, and value, then returns ConnectClick, AnswerClick, or nothing.

**Call relations**: interactive calls this after verifying the Slack signature and loading identity.

*Call graph*: calls 2 internal fn (_dict_field, _string_field); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_dict_field`  (lines 2098–2102)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload. It protects later parsing from silently using malformed data.

**Data flow**: It receives a mapping and field name, returns the nested dictionary if present, or raises ValueError if not.

**Call relations**: _to_click uses it while extracting user, channel, and message objects.

*Call graph*: called by 1 (_to_click).


##### `_replace_buttons_with_answer`  (lines 2105–2147)

```
async def _replace_buttons_with_answer(bot_token: str, click: AnswerClick) -> None
```

**Purpose**: Updates a Slack question message so the clicked button row becomes a visible answer note. It preserves the blocks Slack already accepted instead of re-rendering the whole message.

**Data flow**: It builds an answered context block, copies delivered blocks or creates a fallback block, replaces the clicked block when found or appends the answer, and calls Slack chat.update.

**Call relations**: _run_rewrite calls this after an answer click wins admission.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (_run_rewrite); 2 external calls (AsyncClient, dumps).


##### `_reply_text`  (lines 2150–2160)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a final turn reply. It gives clear messages for failed, cancelled, empty, and normal turns.

**Data flow**: It reads the writeback status and text. Failed turns become a warning, cancelled turns use the cancellation reason or a default, and successful empty turns get a placeholder.

**Call relations**: _reply_with_oversize_links calls this before adding credential hints or artifact links.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 2163–2178)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds extra delivery text to the final reply when needed, including terminal credential instructions and links for files too large for Slack upload.

**Data flow**: It starts with _reply_text, appends a secure terminal instruction for credential requests, finds artifacts over Slack’s upload cap, formats links for them, and returns the combined text.

**Call relations**: post calls this before building the Slack message body.

*Call graph*: calls 2 internal fn (_oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 2181–2184)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one oversized shared artifact as a readable Slack bullet. If the system can make a temporary artifact link, it uses it.

**Data flow**: It asks the context for an artifact link, combines the filename or markdown link with the byte size, and returns one line of text.

**Call relations**: _reply_with_oversize_links calls this for each artifact too large to upload directly.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_debug_link`  (lines 2187–2199)

```
async def _debug_link(ctx: SurfaceContext, writeback: Writeback) -> str | None
```

**Purpose**: Builds an operator-only debug URL for the delivered turn when the deployment has a public base URL. The link points to the debug surface’s view of the conversation.

**Data flow**: It checks for a public base URL, finds the conversation by writeback queue key, and formats a URL with workspace, conversation, and turn ids. Missing pieces return nothing.

**Call relations**: post includes this link in the metadata footer only for allowed operator workspace replies.

*Call graph*: calls 1 internal fn (find_conversation); called by 1 (post).


##### `_channel_is_externally_shared`  (lines 2202–2232)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack destination may include people outside the bound workspace. If it cannot prove the channel is internal, it treats it as shared.

**Data flow**: It calls Slack conversations.info, reads sharing flags from the channel object, and returns true for any external/shared flag or for lookup failures.

**Call relations**: post uses this before adding operator cost and debug metadata, so external audiences do not see internal details.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (post); 1 external calls (AsyncClient).


##### `post`  (lines 2235–2291)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Posts the final agent reply to Slack and returns Slack’s message reference. It also adds question/connect buttons, optional operator metadata, and a safe retry when Slack rejects rich blocks.

**Data flow**: It parses the queue key, reads the bot token, builds reply text and action blocks, maybe builds internal metadata, sends chat.postMessage, retries with conservative blocks or plain text for invalid_blocks, validates Slack’s response, and returns channel:ts.

**Call relations**: The core delivery poller calls this when a turn has a terminal writeback ready for Slack.

*Call graph*: calls 9 internal fn (credential, is_operator_workspace, _channel_is_externally_shared, _chat_post, _debug_link, _reply_with_oversize_links, slack_ask_blocks, slack_connect_blocks, slack_reply_body); 2 external calls (__init__, AsyncClient).


##### `_chat_post`  (lines 2294–2334)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one Slack chat.postMessage request and returns the parsed response without requiring Slack ok:true. This lets callers handle recoverable Slack errors themselves.

**Data flow**: It posts the prepared JSON body. HTTP errors become SurfaceDeliveryError, including retry-after seconds for rate limits when available; successful HTTP responses are parsed as JSON and returned.

**Call relations**: post uses this so it can retry invalid block formatting before treating delivery as failed.

*Call graph*: calls 1 internal fn (__init__); called by 1 (post); 1 external calls (post).


##### `attach`  (lines 2337–2356)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shared artifacts from a completed turn to Slack, skipping files that were already represented as oversized links. Upload failures are logged per file and do not block other files.

**Data flow**: It filters artifacts under Slack’s upload limit, finds the destination channel and thread, reads the bot token, starts uploads concurrently on the event loop, and logs exceptions for individual artifacts.

**Call relations**: The core delivery flow calls this after post when there are shared files to attach.

*Call graph*: calls 2 internal fn (credential, _upload_artifact); 1 external calls (gather).


##### `_upload_artifact`  (lines 2359–2405)

```
async def _upload_artifact(ctx: SurfaceContext, bot_token: str, channel: str, thread_ts: str | None, artifact: SharedArtifact) -> None
```

**Purpose**: Performs Slack’s three-step external file upload for one artifact. The file bytes stream from blob storage instead of being buffered into memory.

**Data flow**: It reserves an upload URL with filename and length, streams the artifact bytes to that URL, then completes the upload into the channel or thread with a title.

**Call relations**: attach runs this concurrently for each uploadable shared artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 4 external calls (__init__, AsyncClient, Timeout, dumps).


##### `_slack_ok`  (lines 2408–2414)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Awaits a Slack API request and enforces Slack’s standard ok:true response. It turns Slack error payloads into SlackApiError.

**Data flow**: It awaits the HTTP response, raises for HTTP failure, parses JSON, checks the ok field, and returns the payload dictionary when successful.

**Call relations**: Most Slack API helpers call this after making Slack requests so they can share one consistent success check.

*Call graph*: called by 11 (_list, _members, _post, _set, _ambient_context, _channel_is_externally_shared, _post_ephemeral, _replace_buttons_with_answer, _slack_user, _upload_artifact (+1 more)); 1 external calls (__init__).


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The terminal client is intentionally simple: it sends a message with `curl`, then reads tab-separated instruction lines such as “show this text”, “ask for input”, “poll again”, or “store this secret”. This file creates those instruction lines and decides when to send them.

A request first proves who it is with a bearer token, which is a signed piece of text used like a temporary pass. The workspace is identified before routing, and the member email is checked again inside the request handler. The email is linked to a member account if possible, then combined with the channel name to find the right conversation.

If the POST body contains a normal message, the file admits that message into the durable conversation queue. If the body is empty, it does not create a new turn; it simply resumes watching the latest turn. This matters because long answers may outlive one HTTP request. The stream is held open for about 85 seconds, then returns a `poll` instruction so the shell reconnects instead of timing out abruptly.

As live frames arrive from the core system, this file translates them into terminal-friendly directives: text deltas become streamed text, tool activity becomes notes, costs become status lines, final answers become prompts, and credential requests become private secret prompts.

#### Function details

##### `directive`  (lines 50–58)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one line of the mini protocol that the shell client reads. It makes sure tabs, newlines, and backslashes inside user-facing text cannot accidentally split or corrupt the line.

**Data flow**: It receives a command word, called the verb, plus any text fields. It escapes unsafe characters in each field, joins everything with tabs, adds a newline, and returns the result as bytes ready to send over HTTP.

**Call relations**: Almost every part of this file uses this as the final packaging step. Higher-level functions decide what should happen on screen, then call `directive` to turn that decision into the exact wire format the terminal client understands.

*Call graph*: called by 6 (_answer, _fulfill_secret, _say_lines, channel, directives_for, stream_directives).


##### `resolve_workspace`  (lines 61–68)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Figures out which workspace a request claims to belong to before the route handler runs. If the request does not carry a valid-looking bearer token, it rejects the request by returning nothing.

**Data flow**: It reads the `Authorization` header from the incoming request. If the header is not a bearer token, it returns `None`; otherwise it asks the bearer-token helper to extract the workspace claim and returns that workspace ID.

**Call relations**: The shared surface-routing layer calls this early to scope the request. Later, `channel` checks the same token for the member email, so the same signed token supplies both the workspace and the user identity.

*Call graph*: 1 external calls (workspace_claim).


##### `directives_for`  (lines 71–95)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Translates one live conversation event into the terminal instructions that should be shown to the user. It is the main adapter between the core conversation stream and the simple shell protocol.

**Data flow**: It receives a live frame, plus context about whether answer text has already been streamed and whether any credential prompts are still waiting. Depending on the frame type, it returns zero or more directive lines such as text, note, status, say, ask, secret, or exit.

**Call relations**: `stream_directives` calls this for each frame it receives from the conversation tail. When the frame represents tool activity it asks `_activity` for readable wording, and when the frame ends a turn it hands the work to `_answer`.

*Call graph*: calls 3 internal fn (_activity, _answer, directive); called by 1 (stream_directives).


##### `_activity`  (lines 98–100)

```
def _activity(frame: ToolCall) -> str
```

**Purpose**: Creates a short human-readable note for a tool call. This lets the terminal show what the assistant is doing instead of leaving the user staring at a blank screen.

**Data flow**: It receives a tool-call frame. It chooses the best available detail, either a description or preview, and returns a sentence like “running search: looking up...” or just “running search”.

**Call relations**: `directives_for` uses this only when it sees a tool-call frame. The returned sentence is then wrapped in a `note` directive for the shell client.

*Call graph*: called by 1 (directives_for).


##### `_answer`  (lines 103–131)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None) -> tuple[bytes, ...]
```

**Purpose**: Finishes a turn in the terminal view. It decides what the user should see after the assistant is done, failed, or cancelled.

**Data flow**: It receives a terminal frame, a flag saying whether the answer text was already streamed, any still-pending credential prompts, and an optional connection message. For a successful turn it may send the final answer, secret prompts, a connection URL message, and a new input prompt. For failure it sends the error text and prompts again. For cancellation it says cancelled and tells the client to exit.

**Call relations**: `directives_for` calls this when the live stream reaches a terminal frame. `_answer` uses `_say_lines` for normal displayed text and `directive` for prompt, secret, and exit instructions.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 134–135)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Turns a block of text into one `say` instruction per line. This keeps multi-line assistant output readable in the terminal protocol.

**Data flow**: It receives text, splits it into display lines, and wraps each line as a `say` directive. If there are no split lines, it still sends the original text as one line.

**Call relations**: `_answer` uses this when it needs to show a completed answer or an error message that was not already streamed as live text.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 138–198)

```
async def stream_directives(frames: AsyncIterator[tuple[str, LiveFrame]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[[], Awaitable[str]] | None=
```

**Purpose**: Keeps an HTTP response open while a conversation turn is still producing output, and streams terminal instructions as events arrive. If the turn takes too long, it asks the shell to reconnect instead of letting the client timeout cut the answer off.

**Data flow**: It receives an async stream of live frames, a maximum hold time, and optional callbacks for checking pending credential prompts and creating connection URLs. It waits for frames until a final frame arrives, the stream ends, or the deadline passes. It yields directive bytes as it goes; if no final frame arrived before the deadline, it yields a `poll` directive.

**Call relations**: `channel` uses this as the body of the streaming HTTP response. Inside the loop it uses `_next` so end-of-stream is clean, calls `directives_for` to translate each frame, and uses `directive` directly when it must tell the shell to poll again.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 2 external calls (get_running_loop, wait_for).


##### `_next`  (lines 201–207)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Safely gets the next item from an async frame stream. It converts the normal “stream is finished” signal into `None`, which is easier for the timeout loop to work with.

**Data flow**: It receives an async iterator of live frames. It awaits the next frame and returns it; if the iterator is exhausted, it returns `None` instead of letting the stop signal bubble outward.

**Call relations**: `stream_directives` calls this inside a timeout wrapper. This small helper keeps the main streaming loop focused on deadline and rendering behavior rather than async-iterator edge cases.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 210–214)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Checks the request’s bearer token and extracts the member email if it is valid for the current workspace. This is the request-level identity check for the terminal channel.

**Data flow**: It reads the `Authorization` header, verifies that it contains a bearer token, and passes that token plus the workspace ID to the token verifier. It returns the email from the verified token, or `None` if authentication fails.

**Call relations**: `channel` calls this at the start of every request. If it returns no email, `channel` immediately sends an unauthorized response and does not touch any conversation data.

*Call graph*: called by 1 (channel); 1 external calls (verify_token).


##### `channel`  (lines 217–248)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles one POST from the terminal client for a named channel. It authenticates the user, finds or creates the right conversation, admits a new message when present, or resumes an existing turn when the body is empty.

**Data flow**: It receives the privileged surface context and the HTTP request. It checks the bearer token, links the email to a member, optionally stores a submitted secret, builds a conversation key from the email and channel path, reads the request body, and either admits a new turn or finds the latest one. It returns plain text for simple errors or a streaming response of terminal directives for normal conversation output.

**Call relations**: This is the route handler registered at the bottom of the file. It relies on the core `SurfaceContext` for member linking, conversation lookup, message admission, turn lookup, live tailing, credential checks, and connection URLs. For the outgoing stream it hands the turn tail to `stream_directives`; for secret submissions it delegates to `_fulfill_secret`.

*Call graph*: calls 10 internal fn (admit, conversation_for, latest_turn, link_member, linked_member, tail, _authenticated_email, _fulfill_secret, directive, stream_directives); 4 external calls (partial, PlainTextResponse, body, StreamingResponse).


##### `_fulfill_secret`  (lines 251–270)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores one private credential value entered by the terminal user. It deliberately treats the body as a secret, not as a chat message, so the value does not appear in the conversation transcript.

**Data flow**: It receives the surface context, request, member ID, and sealed credential-request token. It reads the target slot from a header and the secret value from the body, rejects empty or oversized values, then asks the privileged context to verify and store the credential. It returns a plain text directive saying whether the value was stored.

**Call relations**: `channel` calls this when the request contains the secret header. It uses the core credential fulfillment method to do the security-sensitive validation and storage, and uses `directive` to format the result for the shell.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


### `extensions/web/ufo_ext_web/surface.py`

`io_transport` · `request handling`

This file turns the shared UFO agent system into a small web app. A user opens the chat page, usually with a signed token from setup. The page stores that token in a browser cookie, like a wristband proving which workspace and email the user belongs to. Later requests use that cookie instead of asking the user to sign in again.

When the browser sends a chat message, the file checks the cookie, turns the email into a known workspace member if needed, rejects empty or overly large messages, finds that member’s conversation, and admits the message into the same durable queue used by other surfaces. That matters because the web UI is not a separate agent path; it feeds the same core system as other entry points.

The response itself is not returned all at once. The browser opens a Server-Sent Events stream, which is a one-way live feed from the server to the browser. This stream tails the turn’s live frames from the hub: text chunks, tool activity, skill loading, cost updates, final status, and account-connection links. If the browser disconnects, the stream can resume from the last event id.

The file also renders a simple spend page showing recent cost totals by dimension, member, and agent. Most core work is delegated through SurfaceContext, keeping this file focused on web authentication, HTTP responses, HTML, and browser-friendly streaming.

#### Function details

##### `resolve_workspace`  (lines 38–45)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Finds which workspace a web request is trying to access before the main route runs. It reads the signed bearer token from either the session cookie or the URL query string and extracts the workspace claim from it.

**Data flow**: A browser request comes in, possibly carrying a `ufo_session` cookie or a `token` query parameter. The function picks the token, asks the bearer-token helper to read the workspace id inside it, and returns that workspace id. If there is no token, it returns nothing, which lets the shared surface layer reject the request.

**Call relations**: This is used by the shared fleet as the early workspace-identification step for the web surface. It only asks `workspace_claim` what workspace the token names; later route handlers perform the stricter email authentication through `_authenticate`.

*Call graph*: 1 external calls (workspace_claim).


##### `_authenticate`  (lines 48–59)

```
async def _authenticate(ctx: SurfaceContext, request: Request) -> tuple[UUID, str] | None
```

**Purpose**: Checks that the request’s session cookie really belongs to a member of the current workspace. It also makes sure the email from the token is linked to a member record, creating that link the first time if needed.

**Data flow**: It receives the surface context and browser request. It reads the `ufo_session` cookie, verifies the token against the current workspace, then uses the verified email to look up or create a linked member. It returns the member id and email when all of that succeeds, or returns nothing if the cookie is missing, invalid, or cannot be linked.

**Call relations**: The chat, stream, and spend routes call this before doing anything private. It hands off token checking to `verify_token`, then uses `SurfaceContext.linked_member` and `SurfaceContext.link_member` so the rest of the web flow can work with the core system’s member ids.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 3 (chat, spend, stream); 1 external calls (verify_token).


##### `chat_page`  (lines 62–69)

```
async def chat_page(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the browser chat page. If the URL contains a token, it stores that token in the session cookie so future chat, stream, and spend requests can authenticate automatically.

**Data flow**: It receives a request and creates an HTML response containing the built-in chat page. If the request has a `token` query parameter, it adds a strict same-site cookie named `ufo_session` to the response. The browser receives the page and, when applicable, keeps the cookie for later requests.

**Call relations**: This is the landing route for the web surface. It uses `HTMLResponse` to send the page and `set_session_cookie` to bind the setup token into the browser session; later routes rely on `_authenticate` reading that cookie.

*Call graph*: 2 external calls (HTMLResponse, set_session_cookie).


##### `chat`  (lines 72–84)

```
async def chat(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Accepts one user message from the browser and starts an agent turn for it. It returns a turn id that the browser can use to open the live event stream.

**Data flow**: A POST request arrives with the message text in the body. The function authenticates the cookie, rejects missing login, empty text, or text longer than the configured limit, then finds the user’s conversation and admits the message to the core queue. The output is a JSON response containing the new turn id, or an HTTP error response if something is wrong.

**Call relations**: The chat page’s JavaScript calls this when the user presses Send. This function calls `_authenticate` first, then uses `SurfaceContext.conversation_for` and `SurfaceContext.admit` to hand the work to the shared agent system. The returned turn id is immediately used by the browser to call `stream`.

*Call graph*: calls 3 internal fn (admit, conversation_for, _authenticate); 3 external calls (JSONResponse, body, Response).


##### `stream`  (lines 87–104)

```
async def stream(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Opens the live server-to-browser feed for one agent turn. It verifies that the requesting member owns the turn before sending any events.

**Data flow**: It receives a request whose path contains a turn id. It authenticates the user, parses the turn id, checks that the turn exists, and confirms that the turn belongs to that member. If all checks pass, it returns a streaming response with the `text/event-stream` media type; otherwise it returns an appropriate error such as not found, unauthorized, or forbidden.

**Call relations**: The browser calls this after `chat` returns a turn id. This function protects the stream with `_authenticate` and `SurfaceContext.turn_owner`, then delegates the actual event production to `_events`, wrapping it in a `StreamingResponse` so the browser can receive updates as they happen.

*Call graph*: calls 3 internal fn (turn_owner, _authenticate, _events); 3 external calls (Response, StreamingResponse, UUID).


##### `_events`  (lines 107–124)

```
async def _events(ctx: SurfaceContext, turn_id: UUID, member_id: UUID, since: str) -> AsyncIterator[bytes]
```

**Purpose**: Produces the sequence of live events that the browser receives while an agent turn is running. It also turns special account-connection requests into browser-friendly link events.

**Data flow**: It takes a turn id, member id, and an optional resume cursor from the browser. It tails the core hub for live frames starting after that cursor. For each frame, it may create an extra `connect` or `connect_error` event if the terminal frame asks the user to connect an account, then it converts the original frame into Server-Sent Events bytes and yields them to the HTTP stream.

**Call relations**: `stream` calls this to fill the streaming response. It relies on `SurfaceContext.tail` for the live turn feed, calls `SurfaceContext.connect_url` when a connection link is needed, and passes ordinary frames to `_sse` for formatting.

*Call graph*: calls 3 internal fn (connect_url, tail, _sse); called by 1 (stream); 1 external calls (dumps).


##### `spend`  (lines 127–135)

```
async def spend(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves a browser page showing recent workspace spending for any authenticated member. By default it shows the last 24 hours, but the request can ask for a different time window.

**Data flow**: A request arrives, possibly with a `window_seconds` query parameter. The function authenticates the session cookie, chooses the requested window or the default, asks the core context for the spend rollup, turns that report into HTML, and returns it. If authentication fails, it returns a 401 response.

**Call relations**: This is the web route behind the spend view. It uses `_authenticate` to ensure the requester belongs to the workspace, `SurfaceContext.spend_rollup` to get the same totals used elsewhere, and `_spend_page` plus `HTMLResponse` to present them in the browser.

*Call graph*: calls 3 internal fn (spend_rollup, _authenticate, _spend_page); 2 external calls (HTMLResponse, Response).


##### `_sse`  (lines 138–155)

```
def _sse(cursor: str, frame: LiveFrame) -> bytes
```

**Purpose**: Converts one live frame from the core system into the exact byte format expected by Server-Sent Events. This is what lets the browser distinguish text, final results, costs, tool calls, parked turns, and skill-loading messages.

**Data flow**: It receives a cursor and a live frame. If there is a cursor, it writes it as the event id so the browser can resume later from the last seen event. It then inspects the frame type, serializes the frame as JSON, assigns a suitable event name when needed, and returns the finished bytes to send over the stream.

**Call relations**: `_events` calls this for every frame from the hub. The browser-side JavaScript listens for the event names produced here, such as `terminal`, `parked`, `cost`, `tool`, and `skill`, and updates the page accordingly.

*Call graph*: called by 1 (_events); 1 external calls (model_dump_json).


##### `_money`  (lines 158–159)

```
def _money(micro_usd: int) -> str
```

**Purpose**: Formats an internal cost value as a readable US dollar string. The system stores money in micro-dollars, so this helper turns tiny accounting units into text a person can read.

**Data flow**: It receives an integer number of micro-US-dollars. It divides by the number of micro-dollars in one dollar, formats the result with commas and six decimal places, and returns a string such as `$0.000123`.

**Call relations**: The spend-page helpers call this whenever they display a cost. `_subject_rows` uses it for member and agent totals, and `_spend_page` uses it for the overall total and dimension totals.

*Call graph*: called by 2 (_spend_page, _subject_rows).


##### `_subject_rows`  (lines 162–167)

```
def _subject_rows(subjects: tuple[SubjectTotal, ...]) -> str
```

**Purpose**: Builds the HTML table rows for spend grouped by a subject, such as members or agents. It escapes labels so names are displayed safely instead of being treated as raw HTML.

**Data flow**: It receives a tuple of subject totals. For each subject, it escapes the label, formats the cost with `_money`, and adds one table row. If there are no subjects, it returns a single row saying `none`.

**Call relations**: `_spend_page` calls this twice: once for member totals and once for agent totals. It uses `_money` for readable costs and `html.escape` to keep generated HTML safe.

*Call graph*: calls 1 internal fn (_money); called by 1 (_spend_page); 1 external calls (escape).


##### `_spend_page`  (lines 170–190)

```
def _spend_page(report: SpendReport) -> str
```

**Purpose**: Creates the full HTML page for the workspace spend report. It turns the raw spend rollup into a simple human-readable summary with totals by dimension, member, and agent.

**Data flow**: It receives a spend report from the core surface context. It builds rows for usage dimensions, formats all money amounts, escapes any labels that could appear in HTML, and returns one complete HTML document as a string.

**Call relations**: The `spend` route calls this after it gets a report from `SurfaceContext.spend_rollup`. This helper uses `_money` and `_subject_rows` so the route itself can stay focused on authentication and response delivery.

*Call graph*: calls 2 internal fn (_money, _subject_rows); called by 1 (spend); 1 external calls (escape).
