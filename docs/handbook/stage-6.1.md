# Messaging and chat surfaces  `stage-6.1`

This stage is the system’s set of doors for conversations. It sits around the main agent work loop: it receives messages from people, turns them into a common internal form, then carries the agent’s replies back out to the right place. The central piece, core surface support, gives outside channels controlled access to trusted actions such as finding users, starting conversations, accepting messages, streaming live output, and delivering final answers.

Each channel then has its own adapter. The Slack surface checks that requests really came from Slack, converts Slack threads into agent turns, and posts replies, files, forms, and status updates. Slack mention handling changes Slack’s hidden user codes into readable names for storage, then restores real mentions when sending replies. Slack attribution and hooks add “sent via this agent” footers and tidy old connection buttons.

The iMessage surface does the same bridge work for texts and attachments, while its cloud helper talks to Spectrum’s service to send, receive, and authenticate. The ufo terminal surface exposes conversations and live output as simple commands a shell client can display.

## Files in this stage

### Surface adapters
Channel-specific entrypoints translate Slack, iMessage, and terminal interactions into UFO conversations and route responses back through the shared surface bridge.

### `extensions/imessage/ufo_ext_imessage/surface.py`

`io_transport` · `main loop and message handling`

This file makes iMessage behave like one of UFO's communication surfaces, similar to an inbox or chat channel. Without it, UFO would not know how to receive iMessages, prove that a phone number belongs to a member, save incoming attachments, or send replies back to the same iMessage conversation.

The file has two main jobs. First, it listens to a message provider, which is the lower-level piece that talks to iMessage. It keeps a cursor, meaning a saved position in the message stream, so that if the process restarts or the provider disconnects it can continue without replaying every old message. It also catches up on missed messages before processing live ones.

Second, it decides what an incoming message means. If the phone number is still being verified, the message is treated as a proof code instead of a normal conversation turn. If the phone number is already confirmed, the message is admitted into the right UFO conversation. Direct chats go to that member; group chats go to a room-like audience. Attachments are downloaded only if they fit the size limit, then saved into the workspace.

For outgoing traffic, the file formats UFO's final answers, mid-turn replies, questions, account-connection prompts, and shared files so they can be sent back through iMessage.

#### Function details

##### `read_claim`  (lines 63–73)

```
def read_claim(stored: object) -> PendingClaim | None
```

**Purpose**: This reads a stored phone-number verification claim and checks that it has the expected shape. If the saved data is missing or malformed, it quietly treats it as unusable instead of stopping message processing.

**Data flow**: It receives an arbitrary stored value. If the value is empty, it returns nothing. If the value can be validated as a pending iMessage claim, it returns that claim. If validation fails, it records a log message and returns nothing.

**Call relations**: When a member is proving they own a phone number, ImessageSurface._prove asks this helper to read the saved claim. The helper shields that flow from bad stored data, so one corrupt claim does not block all incoming iMessages.

*Call graph*: called by 1 (_prove); 1 external calls (log).


##### `MessageStreamDisconnected.__init__`  (lines 87–90)

```
def __init__(self, cursor: int | None, error: Exception) -> None
```

**Purpose**: This creates a special error that means the live iMessage stream broke and should be reconnected. It remembers both the last known cursor and the original error.

**Data flow**: It receives the latest stream position, if known, and the error that interrupted the stream. It stores both on the exception object and uses the original error text as the human-readable message.

**Call relations**: ImessageSurface._consume_connected raises this when a provider-side problem happens while catching up, reading live frames, or processing provider-dependent work. ImessageSurface.listen catches it and uses the stored cursor to decide where to resume.

*Call graph*: called by 1 (_consume_connected).


##### `contact_card`  (lines 103–115)

```
def contact_card(assigned_phone_number: str) -> bytes
```

**Purpose**: This builds a small digital contact card for UFO's assigned phone line. Sending it helps the user save the line as a known contact, which can remove iMessage's junk-report warning.

**Data flow**: It receives the assigned phone number. It writes that number into a vCard, which is a standard contact-card text format, and returns the card as bytes ready to send as an attachment.

**Call relations**: ImessageSurface._send_contact_card calls this after a phone number is successfully connected. The generated bytes are passed to the provider as an attachment.

*Call graph*: called by 1 (_send_contact_card).


##### `claim_key`  (lines 118–119)

```
def claim_key(member_id: UUID, phone_number: str) -> str
```

**Purpose**: This makes the private storage key used for one member's claim on one phone number. It hashes the member and phone together so the raw pair is not used directly as the key.

**Data flow**: It receives a member ID and a phone number. It combines them, hashes the combined text, and prefixes the result with the iMessage claim namespace. The output is a stable string key for lookup or deletion.

**Call relations**: ImessageSurface._prove uses this when it needs to fetch or remove the pending verification claim from scoped storage.

*Call graph*: called by 1 (_prove); 1 external calls (sha256).


##### `queue_key`  (lines 122–123)

```
def queue_key(conversation_id: str, *, direct: bool) -> str
```

**Purpose**: This creates the saved routing label for an iMessage conversation. The label records both the conversation ID and whether it is a direct chat or a group chat.

**Data flow**: It receives an iMessage conversation ID and a direct-or-group flag. It turns them into a compact JSON string, which can later be stored on a UFO conversation and decoded again.

**Call relations**: ImessageSurface._admit_message uses this before asking UFO for the conversation that should receive an incoming message.

*Call graph*: called by 1 (_admit_message); 1 external calls (dumps).


##### `conversation_from_queue`  (lines 126–134)

```
def conversation_from_queue(queue: str) -> ConversationAddress
```

**Purpose**: This decodes a saved iMessage routing label back into a usable conversation address. It also rejects labels that do not match the expected direct-or-group shape.

**Data flow**: It receives a queue key string. It parses the JSON inside it, checks whether it names a direct or group conversation, and returns a ConversationAddress containing the ID and type. If the key is malformed, it raises an error.

**Call relations**: Outgoing paths use this helper before sending anything back to iMessage. ImessageSurface.post, ImessageSurface.speak, and ImessageSurface.attach all decode the stored queue key so they know which iMessage conversation to target.

*Call graph*: called by 3 (attach, post, speak); 2 external calls (__init__, loads).


##### `_attachment_content`  (lines 137–138)

```
async def _attachment_content(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: This wraps attachment bytes in the streaming shape expected by the workspace file writer. It is a tiny adapter between 'all bytes are already in memory' and 'please provide chunks over time.'

**Data flow**: It receives a bytes object. It yields that same bytes object as a single chunk and then finishes.

**Call relations**: ImessageSurface._downloaded_files calls this after downloading an iMessage attachment. The resulting async stream is handed to the workspace file-writing API.

*Call graph*: called by 1 (_downloaded_files).


##### `ImessageSurface.listen`  (lines 145–169)

```
async def listen(self, context: SurfaceListenerContext) -> None
```

**Purpose**: This is the long-running listener for incoming iMessages. It connects to the provider, resumes from the saved stream position, and keeps reconnecting if the provider stream drops.

**Data flow**: It receives a listener context that can store cursors and address messages. It creates the iMessage provider, reads the saved cursor for that provider installation, and repeatedly delegates connected-stream processing. On disconnection, it refreshes or clears the cursor as needed, invalidates the provider connection, logs the issue, waits briefly, and tries again.

**Call relations**: This is the top-level inbound loop for the surface. It calls ImessageSurface._consume_connected for each connected session and reacts to MessageStreamDisconnected when that session breaks.

*Call graph*: calls 3 internal fn (clear_cursor, cursor, _consume_connected); 3 external calls (Event, sleep, log).


##### `ImessageSurface._consume_connected`  (lines 171–216)

```
async def _consume_connected(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> None
```

**Purpose**: This runs one connected session with the iMessage provider. It catches up on missed messages, then processes live messages while watching for provider failures.

**Data flow**: It receives the listener context, provider, provider installation ID, and last cursor. It starts a background pump for live events, waits until the subscription is ready, optionally catches up from the old cursor, then reads live frames from a queue. New frames are processed and the cursor moves forward. Provider-side errors are converted into a reconnect signal.

**Call relations**: ImessageSurface.listen calls this for each connection attempt. It starts ImessageSurface._pump_live, uses ImessageSurface._catch_up when resuming, and hands each new event to ImessageSurface._process_event.

*Call graph*: calls 5 internal fn (external_error, _catch_up, _process_event, _pump_live, __init__); called by 1 (listen); 4 external calls (Event, Queue, create_task, gather).


##### `ImessageSurface._pump_live`  (lines 218–236)

```
async def _pump_live(self, provider: MessageProvider, ready: asyncio.Event, frames: asyncio.Queue[LiveFrame | LiveFailure]) -> None
```

**Purpose**: This copies live provider events into an internal queue. It separates the act of listening to the provider from the act of processing each message.

**Data flow**: It receives the provider, a readiness signal, and a queue. As the provider publishes frames, it wraps each frame and puts it into the queue. If the stream errors, it puts a failure item into the queue. If the stream ends normally, it still reports that as a failure because the listener expects a continuing stream.

**Call relations**: ImessageSurface._consume_connected starts this as a background task. The pump feeds frames and failures back to that method so message processing and reconnection decisions happen in one place.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (_consume_connected); 4 external calls (__init__, __init__, __init__, set).


##### `ImessageSurface._catch_up`  (lines 238–257)

```
async def _catch_up(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> int
```

**Purpose**: This processes messages that arrived while UFO was disconnected or behind. It advances the saved stream position after replaying the provider's catch-up feed.

**Data flow**: It receives the listener context, provider, installation ID, and old cursor. It asks the provider for events after that cursor, processes each real message event, tracks the newest sequence number seen, stores that final position, and returns it.

**Call relations**: ImessageSurface._consume_connected calls this before live processing when there is an existing cursor. It hands individual events to ImessageSurface._process_event just like the live path does.

*Call graph*: calls 3 internal fn (store_cursor, catch_up, _process_event); called by 1 (_consume_connected).


##### `ImessageSurface._process_event`  (lines 259–274)

```
async def _process_event(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, sequence: int, message: InboundMessage | None) -> None
```

**Purpose**: This processes one provider event and records that the event has been reached. Even messages from unclaimed phone numbers advance the cursor so they are not retried forever.

**Data flow**: It receives a possible inbound message plus its stream sequence number. If there is a message, it asks the listener context which workspace, if any, the sender belongs to. If a matching workspace exists, it admits the message there. Finally, it stores the stream cursor at this sequence.

**Call relations**: Both ImessageSurface._catch_up and ImessageSurface._consume_connected call this for individual events. It delegates actual message interpretation to ImessageSurface._admit_message.

*Call graph*: calls 3 internal fn (addressed, store_cursor, _admit_message); called by 2 (_catch_up, _consume_connected).


##### `ImessageSurface._admit_message`  (lines 276–320)

```
async def _admit_message(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage) -> None
```

**Purpose**: This decides whether an incoming iMessage becomes a UFO conversation turn. It filters out unclaimed senders, handles phone-number proof messages, routes direct and group chats, saves attachments, and admits the final text into UFO.

**Data flow**: It receives a workspace surface context, provider, and inbound message. It reads the sender's address claim. If the claim is still waiting for proof, it sends the message to the proof flow. If the sender is confirmed, it may ask whether a group-chat message deserves a reply, finds or creates the matching UFO conversation, downloads allowed attachments, wraps the member's message in UFO's message format, and admits it with an idempotency key so duplicates are ignored.

**Call relations**: ImessageSurface._process_event calls this after finding the workspace for the sender. It may call ImessageSurface._prove for verification, queue_key for routing, and ImessageSurface._downloaded_files for attachments before handing the message to the UFO conversation system.

*Call graph*: calls 7 internal fn (address_claim, admit, ambient_reply_wanted, conversation_for, _downloaded_files, _prove, queue_key); called by 1 (_process_event); 7 external calls (__init__, __init__, sha256, conversation_audience, room_audience, fence_member_message, mint_marker).


##### `ImessageSurface._prove`  (lines 322–361)

```
async def _prove(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage, claim: AddressClaim) -> None
```

**Purpose**: This handles the opt-in code flow that proves a member controls a phone number. These proof messages are not turned into UFO conversation turns.

**Data flow**: It receives the current context, provider, inbound message, and address claim. It ignores non-direct messages or claims that are not in proof mode. If the claim expired, it releases the phone number, deletes the stored claim, and tells the sender the code expired. If the sender sends an opt-out word, it releases and deletes the claim. Otherwise it reads the pending claim, normalizes the typed text, checks for the code, and either sends a 'wrong code' reply or confirms the address, sends a connected message and contact card, and deletes the pending claim.

**Call relations**: ImessageSurface._admit_message calls this when a sender's address claim still has an expiry time. It uses claim_key and read_claim to find the saved code, uses the provider to send replies, and calls ImessageSurface._send_contact_card after success.

*Call graph*: calls 6 internal fn (confirm_address, release_address, send_text, _send_contact_card, claim_key, read_claim); called by 1 (_admit_message); 2 external calls (__init__, now).


##### `ImessageSurface._send_contact_card`  (lines 363–384)

```
async def _send_contact_card(self, provider: MessageProvider, message: InboundMessage, assigned_phone_number: str) -> None
```

**Purpose**: This sends UFO's contact card to a newly connected iMessage chat. If the provider refuses the card for an external reason, the phone connection still stays confirmed.

**Data flow**: It receives the provider, the inbound proof message, and the assigned phone number. It builds the vCard bytes and asks the provider to send them as an attachment. If the provider reports an external send failure, it logs that failure instead of breaking the successful connection flow.

**Call relations**: ImessageSurface._prove calls this after the user enters the right opt-in code. It uses contact_card to create the attachment content and relies on the provider to send it.

*Call graph*: calls 4 internal fn (error_code, external_error, send_attachment, contact_card); called by 1 (_prove); 1 external calls (log).


##### `ImessageSurface._downloaded_files`  (lines 386–435)

```
async def _downloaded_files(self, ctx: SurfaceContext, provider: MessageProvider, conversation_id: UUID, attachments: tuple[MessageAttachment, ...]) -> str
```

**Purpose**: This downloads incoming iMessage attachments and saves the usable ones into the workspace. It also creates a short note explaining what was saved, skipped for size, or unavailable.

**Data flow**: It receives the workspace context, provider, UFO conversation ID, and attachment list. For each attachment, it chooses a safe inbox filename, skips it if the declared size is too large, streams the provider download into memory while enforcing the same limit, writes accepted files into the workspace inbox, and records status notes. It returns those notes as text to append to the admitted message.

**Call relations**: ImessageSurface._admit_message calls this when an incoming message has attachments. It uses the provider to download files, _attachment_content to pass bytes into the workspace writer, and the surface context to save files.

*Call graph*: calls 4 internal fn (write_workspace_file, download_attachment, external_error, _attachment_content); called by 1 (_admit_message); 1 external calls (inbox_name).


##### `ImessageSurface.post`  (lines 437–444)

```
async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: This sends the final UFO reply for a turn back to the correct iMessage conversation. It formats the final text before sending.

**Data flow**: It receives the surface context and a writeback, which is UFO's package of final reply information. It decodes the iMessage conversation from the writeback queue key, builds the terminal text, sends it through the provider with the turn ID as the send key, and returns the provider's message reference.

**Call relations**: The UFO surface system calls this when a turn is complete and needs to be delivered. It uses conversation_from_queue for routing and ImessageSurface._terminal_text for human-readable message formatting.

*Call graph*: calls 2 internal fn (_terminal_text, conversation_from_queue).


##### `ImessageSurface.speak`  (lines 446–450)

```
async def speak(self, _ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: This sends a mid-turn reply to iMessage before the full UFO turn has finished. It is used for interim messages such as progress updates.

**Data flow**: It receives a reply containing text, a queue key, and a reply ID. It decodes the target iMessage conversation, sends the reply text through the provider, and returns the provider's message reference.

**Call relations**: The broader surface system calls this when UFO wants to speak during an active turn. It uses conversation_from_queue to find the iMessage chat and then hands the text to the provider.

*Call graph*: calls 1 internal fn (conversation_from_queue).


##### `ImessageSurface.attach`  (lines 452–464)

```
async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None
```

**Purpose**: This uploads files produced by UFO back to the iMessage conversation. It only sends files that fit within the iMessage attachment size limit.

**Data flow**: It receives the context, writeback, and an existing reply reference. It decodes the target iMessage conversation, loops through the writeback artifacts, skips oversized files, reads each allowed artifact into bytes, and asks the provider to send it as an attachment.

**Call relations**: The surface system calls this after or alongside an outgoing reply when there are artifacts to share. It uses conversation_from_queue for routing and ImessageSurface._artifact_bytes to safely read each file before upload.

*Call graph*: calls 2 internal fn (_artifact_bytes, conversation_from_queue).


##### `ImessageSurface._terminal_text`  (lines 466–488)

```
async def _terminal_text(self, ctx: SurfaceContext, writeback: Writeback, *, direct: bool) -> str
```

**Purpose**: This builds the text for a completed UFO turn in a form that makes sense in iMessage. It combines the answer, any question, account-connection instructions, credential instructions, and links for files too large to attach.

**Data flow**: It receives the context, writeback, and whether the target chat is direct. It starts with the terminal answer and question text. It adds a connection URL for direct chats, or a note to continue in direct message for group chats. It adds a member-portal prompt for credential requests. It adds links or filenames for oversized artifacts. The output is one text message, or a fallback status line if there is no content.

**Call relations**: ImessageSurface.post calls this before sending the final iMessage text. It calls ImessageSurface._question_text for question formatting and asks the context for URLs and artifact links when needed.

*Call graph*: calls 4 internal fn (artifact_link, connect_url, home_url, _question_text); called by 1 (post).


##### `ImessageSurface._question_text`  (lines 490–501)

```
def _question_text(self, writeback: Writeback) -> str
```

**Purpose**: This formats a UFO question into plain text suitable for iMessage. It includes the title, each question, available choices, and any current answer.

**Data flow**: It receives a writeback. If there is no question, it returns an empty string. Otherwise it walks through the question items and builds a multi-line text block with labels, options, and chosen answers.

**Call relations**: ImessageSurface._terminal_text calls this while building the final outgoing message. It keeps question formatting separate from the rest of the terminal reply formatting.

*Call graph*: called by 1 (_terminal_text).


##### `ImessageSurface._artifact_bytes`  (lines 503–513)

```
async def _artifact_bytes(self, ctx: SurfaceContext, blob_key: str, size_bytes: int) -> bytes
```

**Purpose**: This reads a stored UFO artifact into memory so it can be uploaded to iMessage. It protects the provider by refusing files that are too large or whose size changes while being read.

**Data flow**: It receives the surface context, blob key, and expected byte size. It rejects the file immediately if the expected size is over the iMessage limit. Otherwise it streams bytes from blob storage, stops with an error if the limit is exceeded, checks that the final byte count matches the expected size, and returns the bytes.

**Call relations**: ImessageSurface.attach calls this for each artifact it wants to send. The returned bytes are passed directly to the iMessage provider's attachment-sending method.

*Call graph*: called by 1 (attach).


### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `request handling and turn delivery`

This file is the adapter between Slack and the core agent system. Without it, Slack could not safely talk to the agent: anyone could fake events, messages would not be tied to the right thread, files would not reach the workspace, and replies would not land where users expect them. The file first proves that an incoming request really came from Slack, using Slack’s signing secret. It then decides whether a message should become an agent turn: direct messages and @mentions always count, while ordinary thread chatter may be ignored unless the agent is already participating or a model says a reply is wanted. When a turn is admitted, the file gathers useful background from Slack, resolves the speaker to a workspace member when possible, downloads attached files, and stores the Slack thread information needed later. While the turn runs, it can keep Slack’s native “assistant is working” status fresh and, for very long runs, post occasional progress messages. When the turn finishes, this file formats the final answer for Slack, including interactive question forms, connection buttons, footer links, and shared files. It also supports installing Slack by OAuth or by a user-provided Slack app. In short, it is the whole Slack-facing surface: security guard, translator, thread map, file courier, and delivery driver.

#### Function details

##### `_env_signing_secret`  (lines 231–235)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from environment variables. This is the fallback secret used when a workspace has not stored its own Slack signing secret.

**Data flow**: It reads the process environment, looks for the Slack signing-secret variable, and returns the secret text or None if it is missing.

**Call relations**: Workspace-specific secret lookup functions call this when stored credentials are not available, so Slack request verification can still work for OAuth installs.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 238–245)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use for a Slack request after the workspace is already known. It prefers the workspace’s private credential and falls back to the deploy-wide environment secret.

**Data flow**: It receives a surface context, asks it for the Slack signing-secret credential, and if that slot is unset returns the environment secret instead.

**Call relations**: The event and interactivity routes call this before checking a Slack signature, so every inbound request is verified with the right tenant’s secret.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 248–256)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret while the system is still figuring out which workspace a Slack request belongs to. It is used before a full surface context exists.

**Data flow**: It receives shared authentication access and a workspace id, reads that workspace’s signing-secret credential, and falls back to the environment secret. Unknown workspaces or missing secrets become None.

**Call relations**: Workspace resolution calls this after extracting a Slack team id, so it can verify the raw request before binding the request to a workspace.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 259–263)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client id required to start or complete an OAuth install. It fails loudly if the deploy is not configured for Slack OAuth.

**Data flow**: It reads the client id from the environment and returns it. If absent, it raises an error instead of producing a broken install request.

**Call relations**: The OAuth token exchange calls this when presenting this app’s identity to Slack.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 266–270)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret required to exchange an OAuth code for a bot token. It stops installation if the deploy is missing this secret.

**Data flow**: It reads the secret from the environment and returns it, or raises an error if it is unset.

**Call relations**: The OAuth exchange uses this alongside the client id when asking Slack for the workspace bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 273–275)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should send the browser back to after an OAuth install. This must match what the Slack app is configured to allow.

**Data flow**: It receives the public base URL of the deploy, trims any trailing slash, appends the Slack surface OAuth path, and returns the full URL.

**Call relations**: The OAuth callback uses it to make sure the code exchange names the same redirect URL as the original authorization link.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 278–290)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Creates the “Add to Slack” URL an owner clicks to install the app. The URL includes the requested Slack permissions and a sealed state value tying the browser callback to the right workspace.

**Data flow**: It receives the Slack client id, redirect URL, and sealed state, encodes them as query parameters with the bot scopes, and returns Slack’s authorization URL.

**Call relations**: Other setup code can use this helper to generate the install link; the callback later validates the same state.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 294–296)

```
def __init__(self, error: str)
```

**Purpose**: Creates an error that explains why Slack identity proof or installation failed. It keeps Slack’s error text in a named field for callers to log or display.

**Data flow**: It receives an error string, stores it on the object, and initializes the normal runtime error message with the same text.

**Call relations**: Identity proof and OAuth exchange raise this when Slack responds with bad or unusable identity information.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `bot_token_fingerprint`  (lines 313–314)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Turns a Slack bot token into a safe fingerprint. This lets the system recognize whether a stored identity belongs to the current token without storing or comparing the token itself.

**Data flow**: It receives a bot token, hashes it with SHA-256, and returns the hexadecimal fingerprint.

**Call relations**: Identity reads, OAuth installation, and bring-your-own-app proof all use this to tie metadata to the exact token that proved it.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 317–329)

```
async def read_identity(blob: BlobStore, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack app identity for a workspace, but only if it matches the current bot token. This prevents stale team or bot ids from being used after a reinstall.

**Data flow**: It checks whether the identity blob exists, loads and validates it, compares its token fingerprint with the current bot token, and returns the identity or None.

**Call relations**: Inbound handling, identity resolution, and bring-your-own-app proof use this before trusting any stored Slack team or bot user id.

*Call graph*: calls 3 internal fn (exists, get, bot_token_fingerprint); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 332–338)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Returns the Slack bot user id for this workspace if Slack is installed and identity metadata is readable. This helps other extension code know which Slack user is the bot itself.

**Data flow**: It reads the bot token credential, reads the matching identity blob, and returns the bot user id or None if anything is missing.

**Call relations**: It is a small identity helper for code that only has a surface identity context, not the full Slack request flow.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_bot_token`  (lines 341–348)

```
async def _bot_token(ctx: SurfaceContext) -> str | None
```

**Purpose**: Reads the workspace’s Slack bot token if it has been installed. Missing tokens are treated as an incomplete install rather than a crash.

**Data flow**: It asks the surface context for the bot-token credential and returns the token, or None if the credential slot is unset.

**Call relations**: The event and interactivity routes call this before they can read from or write to Slack.

*Call graph*: calls 1 internal fn (credential); called by 2 (ingest, interactive).


##### `_identity`  (lines 351–355)

```
async def _identity(ctx: SurfaceContext, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the current Slack identity and mirrors the bot user id into the extension store when available. The mirror gives hook-time code a cheap way to know the bot’s Slack id.

**Data flow**: It receives a context and bot token, reads the matching identity, optionally writes the bot user id to the scoped store, and returns the identity or None.

**Call relations**: Inbound routes and mention mapping call this before trusting team ids or deciding which @mentions refer to the bot.

*Call graph*: calls 2 internal fn (_mirror_self_user_id, read_identity); called by 3 (_reply_mention_ids, ingest, interactive).


##### `_mirror_self_user_id`  (lines 361–377)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the verified Slack bot user id into the extension’s scoped store. This is best-effort because a footer or hook should not make Slack event handling fail.

**Data flow**: It receives a workspace id and bot user id, skips the write if this process already wrote the same value, otherwise writes it to the scoped store and updates an in-memory cache.

**Call relations**: Identity reads and OAuth installs call this so later hook code can find the bot user id without access to the identity blob.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 390–396)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Gets or proves the Slack identity for a bring-your-own Slack app install. It avoids calling Slack again when a matching identity is already stored.

**Data flow**: It reads the identity blob, returns it if valid, otherwise calls Slack to prove the token, stores the resulting identity, and returns it.

**Call relations**: Background identity proof uses this for workspaces that have a bot token but no stored identity yet.

*Call graph*: calls 2 internal fn (_prove, read_identity).


##### `SlackIdentityResolver._prove`  (lines 398–423)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack’s auth.test endpoint what team and bot user a pasted bot token belongs to. This is the proof step for non-OAuth installs.

**Data flow**: It sends the bot token to Slack, checks the response is successful and well formed, validates the team and bot user ids, and returns a SlackIdentity.

**Call relations**: The resolver calls this only when no valid identity is already stored.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 429–443)

```
def _prove_identity_in_background(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Starts a background task to prove Slack identity without blocking the current request. This lets Slack retry the event later after identity has been written.

**Data flow**: It receives a context and bot token, checks whether a proof task is already running for the workspace, starts one if not, and records it until completion.

**Call relations**: The install-incomplete response path calls this when a workspace has a token but no readable identity.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 1 (_identity_unavailable); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 439–441)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a completed identity-proof task from the in-memory task table. This keeps the table from growing forever.

**Data flow**: It receives the finished task, checks that it is still the task recorded for the workspace, and deletes that entry.

**Call relations**: It is registered as the completion callback for the task started by _prove_identity_in_background.


##### `_run_identity_proof`  (lines 446–452)

```
async def _run_identity_proof(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Runs the actual background identity proof and logs failures. It protects the request path from exceptions raised while proving the Slack token.

**Data flow**: It receives a context and bot token, constructs a SlackIdentityResolver, asks it to resolve identity, and logs any Slack-specific or unexpected error.

**Call relations**: It is the task body created by _prove_identity_in_background.

*Call graph*: called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `_identity_unavailable`  (lines 460–488)

```
def _identity_unavailable(ctx: SurfaceContext, bot_token: str | None) -> Response
```

**Purpose**: Builds the HTTP response used when Slack sent a valid request but the workspace cannot yet identify its Slack app. It also starts recovery when possible.

**Data flow**: If the bot token is missing, it logs a once-per-process warning and returns a no-retry service error. If the token exists, it starts background identity proof and asks Slack to retry.

**Call relations**: The event and interactivity routes call this when they cannot get both a bot token and identity record.

*Call graph*: calls 1 internal fn (_prove_identity_in_background); called by 2 (ingest, interactive); 2 external calls (Response, warn).


##### `signing_secret_fingerprint`  (lines 494–497)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack signing secret. This lets the system remember which secret verified a URL without storing the secret itself.

**Data flow**: It receives the signing secret, hashes it with SHA-256, and returns the hexadecimal fingerprint.

**Call relations**: _mark_url_verified uses this when recording that Slack successfully reached this deploy.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 517–550)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s temporary OAuth code for a workspace bot token and identity details. This is the central step that finishes an OAuth install.

**Data flow**: It sends the code, client id, client secret, and redirect URL to Slack, checks the response, validates required ids, and returns a SlackInstall record.

**Call relations**: oauth_callback calls this after validating the sealed install state.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `slack_app_dm_url`  (lines 553–558)

```
def slack_app_dm_url(app_id: str, team_id: str) -> str
```

**Purpose**: Builds a browser URL that opens the newly installed Slack app’s direct message. It gives the installer a friendly place to continue.

**Data flow**: It receives Slack app and team ids, encodes them into Slack’s app_redirect URL, and returns that URL.

**Call relations**: oauth_callback uses this on the success page when Slack supplied an app id.

*Call graph*: called by 1 (oauth_callback); 1 external calls (urlencode).


##### `SlackConversationSearch.run`  (lines 645–659)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations for channels or DMs matching a user’s query. It makes DMs searchable by resolving the people in them.

**Data flow**: It normalizes the query, lists conversations, resolves people for DMs and group DMs, converts raw Slack records into simple conversation records, filters by text match, and returns matches plus a truncation flag.

**Call relations**: This is the public method of SlackConversationSearch; it delegates listing, people resolution, and record conversion to its helper methods.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 661–680)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches a bounded set of Slack conversations from Slack’s paged list API. It stops after a fixed number of pages so a large workspace cannot make the search run forever.

**Data flow**: It repeatedly builds Slack list parameters, calls Slack, appends returned channels, follows the next cursor, and returns the gathered raw records plus whether it hit the page limit.

**Call relations**: SlackConversationSearch.run calls this before filtering conversations.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 682–690)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds query parameters for one conversations.list call. It includes the conversation kinds and optional page cursor.

**Data flow**: It receives a cursor string, creates a parameter dictionary with type filters, archive exclusion, and page size, and adds the cursor when present.

**Call relations**: The listing helper calls this for every page request to Slack.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 692–695)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a list response. A missing or malformed cursor means there are no more pages to read.

**Data flow**: It receives a Slack payload, looks inside response_metadata.next_cursor, and returns that string or an empty string.

**Call relations**: The listing helper uses this to decide whether to keep paging.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 697–725)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Finds readable labels for the people in DMs and group DMs. This lets searches like “DM with Alice” work even though Slack DMs do not have normal channel names.

**Data flow**: It scans listed conversations, gathers member ids for a bounded number of DMs, resolves each user once, formats labels, and returns labels by conversation id plus whether it capped the work.

**Call relations**: SlackConversationSearch.run calls this after listing conversations and before building searchable records.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 727–734)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as a public channel, private channel, group DM, or one-to-one DM. This gives later code a simple kind value.

**Data flow**: It reads Slack boolean flags from the raw record and returns the matching kind string.

**Call relations**: Conversation conversion, DM people resolution, and member lookup call this to choose the right behavior.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 736–750)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets the Slack user ids in a DM or group DM. One-to-one DMs can read the user directly; group DMs need Slack’s members endpoint.

**Data flow**: It receives the raw conversation and id, returns the direct user for one-to-one DMs, or calls Slack for group members and returns valid member ids.

**Call relations**: The people-resolution step calls this for each DM-like conversation it wants to label.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 752–757)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Formats a user lookup into a search-friendly label. It prefers name plus email when both are available.

**Data flow**: It receives a SlackUser or None and a fallback user id, then returns name, email, both, or the id.

**Call relations**: The people-resolution step uses this after reading user details from Slack.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 759–776)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts one raw Slack conversation into the simplified SlackConversation model used by search results. Badly shaped records are skipped.

**Data flow**: It validates the raw object and id, reads name, purpose, topic, kind, people labels, and membership flag, then returns a SlackConversation or None.

**Call relations**: SlackConversationSearch.run calls this for each listed record before applying the query filter.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 778–780)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely reads Slack’s nested purpose or topic text field. Missing or malformed data becomes an empty string.

**Data flow**: It receives a field object, reads its value key if it is a dictionary, and returns the string value or empty text.

**Call relations**: The conversation converter uses this for Slack’s purpose and topic fields.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 955–970)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a request body was really signed by Slack and is recent enough not to be a replay. This is the main security gate for Slack POST requests.

**Data flow**: It reads Slack timestamp and signature headers, checks the timestamp age, recomputes the expected HMAC signature from the body and signing secret, and raises an error if anything does not match.

**Call relations**: Workspace resolution, event ingest, and interactivity handling call this before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 977–996)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw Slack request body with a size limit. The raw bytes are needed because Slack signatures must be checked against the exact original body.

**Data flow**: It checks whether the body was already cached, otherwise streams chunks from the request, stops if the size limit is exceeded, caches the bytes, and returns them.

**Call relations**: Workspace resolution, event ingest, and interactivity handling all use this before parsing or verifying a request.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 999–1008)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack’s URL verification handshake and extracts the challenge text. This lets Slack confirm the endpoint is reachable.

**Data flow**: It parses the body as JSON, checks for type url_verification, and returns the challenge string or None for normal events.

**Call relations**: Workspace resolution and ingest use this to answer Slack setup probes without treating them as messages.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 1011–1028)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Extracts a trustworthy-looking Slack team id from an untrusted request body. It is only a hint used to find the right workspace before signature verification.

**Data flow**: It tries to parse the body as JSON or as a form-encoded interactive payload, reads team_id or team.id, validates the id shape, and returns it or None.

**Call relations**: resolve_workspace uses this hint to choose the candidate workspace whose signing secret will verify the request.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 1031–1032)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Turns a Slack team id into the installation key used by the core system. This is how one Slack workspace is bound to one ufo workspace.

**Data flow**: It receives a team id and returns it with a team: prefix.

**Call relations**: OAuth installation writes this binding, and workspace resolution reads it for incoming Slack events.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 1035–1073)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace a Slack request belongs to before the route runs. It handles OAuth callbacks, Slack URL verification, and signed Slack POSTs.

**Data flow**: For GET callbacks it opens the sealed state and returns the named workspace. For POSTs it reads the raw body, answers URL verification if possible, extracts the team hint, finds the bound workspace, verifies the signature, and returns the workspace id or a response/None.

**Call relations**: The hosting surface layer calls this before dispatching Slack routes, so each route runs in the correct tenant context.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 1076–1079)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential-authorization state belongs to Slack OAuth install. It prevents other credential flows from being accepted as Slack installs.

**Data flow**: It receives opened state claims and returns true only when the payload marker and requested slot match the Slack bot-token install.

**Call relations**: OAuth callback and workspace resolution use this when interpreting the browser state value.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 1082–1087)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the core conversation key for a Slack message. Channel conversations are keyed by channel plus thread root, while DMs are keyed by the DM channel.

**Data flow**: It receives the Slack channel id, root timestamp, and whether the channel is a DM, then returns either the channel id or channel:root timestamp.

**Call relations**: Inbound event parsing and interactive form parsing use this to find the core conversation that Slack message belongs to.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `slack_message_addressed`  (lines 1090–1108)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly asking the agent to respond. DMs always count; channel messages count when they mention the bot in real message content.

**Data flow**: It receives the Slack event, bot user id, and DM flag, reads all message bodies, checks for valid addressing mentions, and returns true or false.

**Call relations**: _to_inbound calls this while deciding whether a Slack event should be admitted, ignored, or sent to the ambient reply decision.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1111–1114)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts links in reply text so Slack previews can be disabled when there are too many. This keeps answers from being buried under many preview cards.

**Data flow**: It counts Markdown links, removes them, counts remaining bare URLs, and returns the total.

**Call relations**: slack_reply_body uses this before deciding whether to turn off Slack link unfurling.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `slack_reply_parts`  (lines 1117–1194)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long reply text into Slack-sized pieces without cutting awkwardly through Markdown blocks when possible. This lets long answers be delivered as several readable Slack messages.

**Data flow**: It receives text and a size limit, finds safe split points such as paragraph breaks while respecting code fences and tables, and returns a list of parts.

**Call relations**: Post, speak, and reply-body rendering use this whenever text may exceed Slack’s per-message limits.

*Call graph*: called by 3 (post, slack_reply_body, speak); 2 external calls (finditer, match).


##### `slack_reply_body`  (lines 1197–1264)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for one Slack chat.postMessage call. It can include rich Slack blocks, metadata for deduplication, form controls, buttons, and a footer.

**Data flow**: It receives channel, thread, text, optional metadata, delivery id, and block options, constructs a Slack message payload, checks size limits, and returns encoded JSON bytes.

**Call relations**: Final replies, mid-turn replies, and progress messages call this before posting to Slack.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 3 (_say, post, speak); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1267–1268)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a simple Slack Markdown section block with Slack’s section text limit applied. It is a small helper for form and context rendering.

**Data flow**: It receives text, truncates it to Slack’s section limit, and returns a Block Kit section dictionary.

**Call relations**: Ask-form rendering uses this for titles and prose fallback.

*Call graph*: called by 2 (_ask_prose, slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1271–1315)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question as a Slack form when Slack controls can represent it. If the question is too complex for controls, it renders readable prose instead.

**Data flow**: It receives an optional ask object, turns each question into an input block when possible, adds a submit button, or falls back to prose sections with a reply-in-thread hint.

**Call relations**: The final reply poster calls this when a turn ends by asking the user for input.

*Call graph*: calls 3 internal fn (_ask_control, _ask_prose, _mrkdwn_section); called by 1 (post).


##### `_ask_control`  (lines 1318–1370)

```
def _ask_control(index: int, ask: AskQuestion) -> dict[str, object] | None
```

**Purpose**: Builds one Slack input control for one question. It chooses radio buttons, checkboxes, or a text box depending on the question shape.

**Data flow**: It receives a question index and question, rejects unsupported attachments or oversized option groups, builds the Slack input block, and returns it or None.

**Call relations**: slack_ask_blocks calls this for each question before deciding whether the whole ask can be rendered as a form.

*Call graph*: calls 1 internal fn (_ask_option); called by 1 (slack_ask_blocks).


##### `_ask_option`  (lines 1373–1383)

```
def _ask_option(option: QuestionOption) -> dict[str, object]
```

**Purpose**: Converts one answer option into Slack’s option format. It preserves the option label and optional description within Slack’s limits.

**Data flow**: It receives a QuestionOption and returns a dictionary containing display text, submitted value, and optional description.

**Call relations**: _ask_control uses this when building radio-button and checkbox controls.

*Call graph*: called by 1 (_ask_control).


##### `_ask_prose`  (lines 1386–1394)

```
def _ask_prose(ask: AskQuestion) -> dict[str, object]
```

**Purpose**: Renders one question as plain Slack Markdown prose. This is used when a real Slack form control cannot safely represent the question.

**Data flow**: It receives a question, builds lines for the question, options, and multi-select note, and wraps them in a Markdown section block.

**Call relations**: slack_ask_blocks uses this fallback when any question cannot become an input control.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (slack_ask_blocks).


##### `slack_connect_blocks`  (lines 1397–1423)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Builds a Slack button for a connection request, such as connecting an outside provider account. The button carries the turn id so a later click can find the request.

**Data flow**: It receives an optional connect request and turn id, returns no blocks when there is no request, otherwise returns an actions block with one connect button.

**Call relations**: The final reply poster adds these blocks when the turn asked the user to connect an external account.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1426–1430)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required non-empty string field from a Slack payload. It fails early when Slack data is missing a field the rest of the code needs.

**Data flow**: It receives a mapping and field name, returns the string value if present and non-empty, or raises a ValueError.

**Call relations**: Inbound parsing and interactivity parsing use this for required Slack ids and timestamps.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `_inbound_files`  (lines 1433–1445)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts usable file attachments from a Slack message event. It ignores hidden or tombstoned files and caps how many files one message can bring in.

**Data flow**: It reads the event’s files list, picks valid private download URLs and names, creates InboundFile records, and returns them as a tuple.

**Call relations**: _to_inbound uses this from the event itself, and _declared_files uses it after fetching a message from Slack.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1448–1479)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Fetches a specific Slack message to discover file attachments that were not present in the original app_mention event. This covers Slack’s partial event shapes.

**Data flow**: It calls conversations.replies for the exact timestamp range, finds the message with the target timestamp, extracts inbound files, and returns them or an empty tuple on failure.

**Call relations**: _to_inbound calls this for app_mention events that may have files Slack did not include directly.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1487–1562)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes Slack OAuth installation after the owner approves the app. It stores the bot token, binds the Slack team to the workspace, records identity, and shows a success or error page.

**Data flow**: It reads error/state/code query parameters, validates the sealed state, exchanges the code with Slack, binds the installation, stores the credential and identity blob, mirrors the bot id, and returns a callback page.

**Call relations**: Slack redirects the browser here during OAuth install; it hands off to slack_oauth_exchange and core credential/install APIs.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, slack_app_dm_url, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 3 external calls (__init__, __init__, callback_page).


##### `_mark_url_verified`  (lines 1568–1582)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this deploy with a request verified by the current signing secret. This helps setup tools know the Slack app endpoint is live.

**Data flow**: It fingerprints the signing secret, skips if already recorded in this process, writes a timestamped marker blob, and caches the fingerprint in memory.

**Call relations**: Event and interactivity routes call this after a valid signed request or URL verification challenge.

*Call graph*: calls 1 internal fn (signing_secret_fingerprint); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1585–1629)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack event callbacks. It verifies the request, filters Slack events, turns relevant messages into agent turns, and quickly acknowledges Slack.

**Data flow**: It reads and verifies the raw body, handles URL verification, loads bot token and identity, ignores wrong-team or irrelevant events, converts valid messages to Inbound records, admits clear requests, or starts an ambient decision task.

**Call relations**: This is the main Slack events route; it coordinates signature verification, inbound parsing, admission, file download, and ambient reply decisions.

*Call graph*: calls 12 internal fn (_admit_inbound, _bot_token, _ctx_signing_secret, _decide_ambient_in_background, _folds_into_live_turn, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_inbound (+2 more)); 3 external calls (loads, JSONResponse, Response).


##### `_folds_into_live_turn`  (lines 1632–1671)

```
async def _folds_into_live_turn(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> bool
```

**Purpose**: Checks whether an unmentioned thread reply should be absorbed by a turn already running in that thread. Such messages should not be silenced by the ambient reply classifier.

**Data flow**: It receives an inbound message, finds an absorbing live turn, resolves the sender, checks whether the sender has a seat, logs the skip, and returns true only when admission would really fold it into the running turn.

**Call relations**: ingest calls this before deciding whether an unaddressed reply needs the ambient reply model.

*Call graph*: calls 4 internal fn (absorbing_turn, transaction, _resolve_member, _slack_user); called by 1 (ingest); 2 external calls (__init__, log).


##### `_admit_inbound`  (lines 1674–1724)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Performs all work needed to turn a Slack message into a core agent turn. It gathers context, downloads files, resolves the member, mirrors thread data, and admits the message.

**Data flow**: It receives an Inbound message, fetches sender info, ambient context, permalink, and mention names, resolves the member and conversation, downloads attachments, builds the fenced message body, calls core admission, anchors DM threads, and arms status followers if a run opened.

**Call relations**: ingest calls this for direct admissions, and the ambient decision task calls it when unaddressed thread chatter deserves a reply.

*Call graph*: calls 13 internal fn (admit, conversation_for, retitle_conversation, _ambient_context, _anchor_dm_thread, _arm_followers, _download_files, _mirror_thread, _resolve_member, _slack_permalink (+3 more)); called by 2 (_run_ambient_decision, ingest); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1730–1755)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts a background task to decide whether an unaddressed Slack thread reply deserves an agent turn. This keeps Slack’s three-second event acknowledgement fast.

**Data flow**: It keys the task by Slack message id, skips duplicates, starts the decision task, stores it, and removes it when done.

**Call relations**: ingest calls this after acknowledging a thread reply that is not directly addressed but may still need an answer.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1751–1753)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished ambient-decision task from the in-memory task table. This avoids holding completed tasks forever.

**Data flow**: It receives the finished task, checks it is still the recorded task for the message id, and deletes it.

**Call relations**: It is attached as the done callback for tasks created by _decide_ambient_in_background.


##### `_run_ambient_decision`  (lines 1758–1773)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the ambient reply decision and admits the message if the answer is yes. It logs failures because Slack has already been acknowledged and will not retry for this task.

**Data flow**: It asks whether a reply is wanted, calls admission when true, and logs any exception with the Slack queue key and timestamp.

**Call relations**: _decide_ambient_in_background creates this task after ingest returns success to Slack.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1776–1783)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages from users outside the installed Slack workspace in shared Slack Connect channels. Those authors are skipped because the app cannot safely serve or identify them.

**Data flow**: It compares source_team or user_team on the event with the installed team id and returns true when they differ.

**Call relations**: _to_inbound calls this before doing member resolution or admitting a message.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1799–1835)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines who should be allowed to see a Slack conversation inside the core system. Public channels, private rooms, DMs, and shared channels have different audience rules.

**Data flow**: It receives payload and event metadata, uses channel_type when enough, fetches channel info when needed, builds an audience and optional display label, or raises when it cannot safely decide.

**Call relations**: _to_inbound awaits this while converting Slack events into Inbound records.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1838–1888)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Converts a raw Slack event callback into the simplified Inbound message the rest of this file understands. It also drops messages the agent should ignore.

**Data flow**: It validates the event type, ignores bots and unsupported subtypes, checks author/team/addressing/thread rules, builds the conversation key, resolves channel audience and files, and returns an Inbound record or None.

**Call relations**: ingest calls this after verification and identity checks, before admission or ambient decision.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1891–1902)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has a real admitted turn in the core conversation. This prevents mere conversation rows from counting as participation too early.

**Data flow**: It looks up the conversation by queue key, checks that it has a latest turn, and returns the conversation id only when both exist.

**Call relations**: _to_inbound uses this to decide whether unaddressed thread replies may be considered part of an existing agent conversation.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1905–1937)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Reads basic Slack user details such as name, confirmed email, timezone, and team id. It is best-effort so Slack lookup problems do not usually block message handling.

**Data flow**: It calls users.info with the bot token, validates the response, keeps email only if Slack says it is confirmed, and returns a SlackUser or None.

**Call relations**: Admission, live-turn folding, interactivity, conversation search, and name resolution call this whenever they need user details.

*Call graph*: calls 1 internal fn (_slack_ok); called by 5 (_people, _name, _admit_inbound, _folds_into_live_turn, interactive); 2 external calls (__init__, AsyncClient).


##### `_conversation_members`  (lines 1951–1971)

```
async def _conversation_members(bot_token: str, channel: str) -> tuple[str, ...]
```

**Purpose**: Reads a bounded list of members in a Slack conversation. It is used to decide which names in an outgoing reply are safe to turn into Slack notifications.

**Data flow**: It calls Slack’s conversations.members endpoint with a fixed limit, returns valid user ids, and returns an empty tuple if Slack cannot be read.

**Call relations**: SlackNames.mention_ids calls this before resolving outbound @name mentions.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (mention_ids); 1 external calls (AsyncClient).


##### `SlackNames.of`  (lines 1992–2000)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack user and channel ids mentioned in text into readable names. This makes inbound Slack markup easier for the agent and users to understand.

**Data flow**: It scans texts for mentioned user and channel ids, adds explicitly supplied user ids, resolves them through cache and Slack, and returns id-to-name mappings.

**Call relations**: Admission and ambient digest building use this when rendering Slack messages into plain readable context.

*Call graph*: calls 1 internal fn (_resolved); 2 external calls (mentioned_channels, mentioned_users).


##### `SlackNames.mention_ids`  (lines 2002–2016)

```
async def mention_ids(self, channel: str, identity: SlackIdentity) -> dict[str, str]
```

**Purpose**: Builds the safe map from names in an outgoing reply to Slack mention ids for people in the current conversation. It avoids notifying outsiders or the bot itself.

**Data flow**: It reads the conversation roster, resolves names for members except the bot, keeps only users from the installed team, and returns a mention index.

**Call relations**: Reply mention mapping calls this before replacing agent-written @names with Slack notification markup.

*Call graph*: calls 2 internal fn (_resolved, _conversation_members); 1 external calls (mention_index).


##### `SlackNames._resolved`  (lines 2018–2026)

```
async def _resolved(self, wanted: Mapping[str, str]) -> dict[str, _NamedId]
```

**Purpose**: Combines cached name lookups with bounded fresh Slack lookups. This keeps common names fast while limiting how much one message can ask Slack for.

**Data flow**: It receives ids and their Slack lookup URLs, reads fresh-enough cached entries, fetches a limited set of missing names concurrently, stores fetched names, and returns the merged map.

**Call relations**: Both inbound name rendering and outbound mention mapping call this through SlackNames.of or mention_ids.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); called by 2 (mention_ids, of); 1 external calls (gather).


##### `SlackNames._remembered`  (lines 2028–2049)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, _NamedId]
```

**Purpose**: Reads fresh cached Slack names from the extension store. Stale or malformed cache rows are ignored.

**Data flow**: It receives ids, reads their store keys in one call, checks timestamp, name, and team fields, and returns usable cached _NamedId entries.

**Call relations**: SlackNames._resolved calls this before deciding which ids still need Slack API requests.

*Call graph*: called by 1 (_resolved); 3 external calls (__init__, __init__, now).


##### `SlackNames._name`  (lines 2051–2068)

```
async def _name(self, id_: str, url: str) -> _NamedId | None
```

**Purpose**: Fetches and sanitizes the display name for one Slack user or channel id. It also records the user’s team when resolving users.

**Data flow**: It chooses users.info or conversations.info, reads the raw name, removes forbidden Slack markup delimiters, collapses whitespace, truncates it, and returns a _NamedId or None.

**Call relations**: SlackNames._resolved calls this for ids missing from the cache.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (_resolved); 1 external calls (__init__).


##### `SlackNames._remember`  (lines 2070–2080)

```
async def _remember(self, names: Mapping[str, _NamedId]) -> None
```

**Purpose**: Writes newly resolved Slack names into the scoped cache. Failures are logged but do not stop message handling.

**Data flow**: It receives id-to-name entries, stamps them with the current time, and writes each to the extension store.

**Call relations**: SlackNames._resolved calls this after fresh Slack lookups.

*Call graph*: called by 1 (_resolved); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 2083–2103)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Gets Slack’s own permalink for a message. A real Slack permalink is safer than trying to build one from ids.

**Data flow**: It calls chat.getPermalink for a channel and timestamp, returns the permalink string, or returns None and logs if Slack cannot provide it.

**Call relations**: Admission and answer-submit handling use this to attach source links to turns.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, interactive); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 2106–2123)

```
def _turn_context(sender: SlackUser | None, source: str | None, question: str | None=None) -> TurnContext
```

**Purpose**: Builds the core turn context from Slack sender details, source link, and optional answered question. This gives the agent useful human context without trusting malformed timezones.

**Data flow**: It formats sender name/email when available, tries to create a TurnContext with timezone, and falls back without timezone if validation fails.

**Call relations**: Inbound admission and interactive answer admission call this when creating a turn.

*Call graph*: called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_resolve_member`  (lines 2126–2144)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Finds or creates the ufo member who sent a Slack message. It links existing Slack identities first, then uses Slack-confirmed email to join same-domain teammates.

**Data flow**: It checks for an existing linked member, requires user info and confirmed email when unlinked, raises for unresolved DMs that need identity, and returns a member id or None.

**Call relations**: Admission, live-turn folding, and form-submit handling call this before assigning a speaker to a turn.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 3 (_admit_inbound, _folds_into_live_turn, interactive); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 2147–2172)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether an unaddressed Slack thread message should start a new agent turn. This prevents the bot from intruding on ordinary member-to-member chatter.

**Data flow**: It fetches recent thread history, admits by default if no trustworthy history exists, otherwise asks the core ambient-reply decision and logs when the answer is no.

**Call relations**: The ambient background task calls this before deciding whether to call _admit_inbound.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 2175–2197)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Builds the recent Slack thread history used by the ambient reply decision. It includes member messages and the bot’s own messages so the model can judge context.

**Data flow**: It fetches the thread tail before the inbound message, converts usable messages into AmbientMessage entries, sorts them, and returns the latest bounded set.

**Call relations**: _ambient_reply_wanted calls this as the evidence for the reply/no-reply decision.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 2200–2246)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Fetches messages in a Slack thread before a given timestamp, walking pages so callers see the tail rather than only the thread beginning. It returns None when the read cannot be trusted.

**Data flow**: It calls conversations.replies page by page with a latest bound, accumulates messages, stops at the cursor end, and returns None on errors or page-limit overflow.

**Call relations**: Ambient history and unseen-tail context both call this to read Slack thread messages.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 2249–2269)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Converts one raw Slack message into an ambient-history entry if it is relevant and safely ordered before the inbound message.

**Data flow**: It validates user, timestamp, and text, drops other bots, marks whether the message is from this bot, and returns a sortable timestamp plus AmbientMessage.

**Call relations**: _ambient_history applies this to each fetched Slack message.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 2272–2327)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds background context that the core transcript does not already contain. This helps the agent understand surrounding Slack conversation without treating it as direct instructions.

**Data flow**: It decides which Slack messages to fetch based on DM/channel and whether the conversation already exists, reads recent channel or thread messages, resolves names, and returns a bounded ambient digest.

**Call relations**: _admit_inbound gathers this before admitting a Slack message as a turn.

*Call graph*: calls 4 internal fn (_digest_names, _slack_ok, _unseen_tail, ambient_digest); called by 1 (_admit_inbound); 1 external calls (AsyncClient).


##### `_digest_names`  (lines 2330–2339)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves author and mention ids for a batch of Slack messages used in an ambient digest. This makes the digest readable.

**Data flow**: It extracts text and author ids from message objects, asks SlackNames to resolve them, and returns an id-to-name map.

**Call relations**: Ambient context and unseen-tail digest building call this before rendering messages.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2342–2388)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Finds thread messages since the last admitted turn that the agent has not read because they founded no turn. It prevents the agent’s transcript from falling behind the Slack thread.

**Data flow**: It fetches the thread tail, sorts recent messages, walks backward until it finds a message already admitted, keeps the unseen ones, resolves names, and renders a digest.

**Call relations**: _ambient_context calls this for continuing Slack conversations.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2391–2459)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders fetched Slack messages into a safe, bounded context block for the agent. It keeps bystander words separate from the current speaker’s message.

**Data flow**: It filters unsupported messages, skips bot messages and direct bot mentions, formats timestamps and speaker names, collapses text, limits total size, and wraps the result in a marked ambient-context element.

**Call relations**: Ambient context and unseen-tail helpers call this after fetching Slack messages and resolving names.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2462–2464)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks whether a file download URL belongs to Slack. This prevents the bot token from being sent to an attacker-controlled host.

**Data flow**: It parses the URL hostname and returns true only for slack.com or Slack subdomains.

**Call relations**: _stream_download calls this before attaching the Authorization header to a download request.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2467–2485)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private Slack file download in chunks with a total size cap. It avoids loading the whole file into memory and refuses non-Slack hosts.

**Data flow**: It validates the host, opens an authenticated streaming GET request, yields chunks, tracks total bytes, and raises if the file exceeds the workspace write limit.

**Call relations**: _download_files passes this stream directly into the workspace file writer.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2497–2513)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads inbound Slack attachments into the workspace before the agent turn runs. Oversized files are skipped and reported instead of partially written.

**Data flow**: It assigns safe inbox filenames, streams each Slack file into the workspace, records delivered and skipped names, and returns a DownloadedFiles summary.

**Call relations**: _admit_inbound calls this when a Slack message has attached files.

*Call graph*: calls 2 internal fn (write_workspace_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2516–2525)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates the text note telling the agent which Slack files were saved and which were skipped. This lets the model know what attachments it can access.

**Data flow**: It receives a DownloadedFiles summary and returns one or two human-readable lines listing workspace paths and oversized skipped files.

**Call relations**: _admit_inbound adds this note to the fenced member message after downloading attachments.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2537–2542)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored Slack thread mirror into the current model shape. It supports older rows that stored only the queue key as a string.

**Data flow**: It receives stored JSON-like data, returns a MirroredThread built from a string row or validates a structured row.

**Call relations**: Hook followers and mid-turn comments use this when they need to recover Slack thread information from the scoped store.


##### `MirroredThread.anchor`  (lines 2544–2548)

```
def anchor(self) -> str | None
```

**Purpose**: Finds the Slack timestamp that status and progress messages should attach to. For channel threads it comes from the queue key; for DMs it comes from the stored message timestamp.

**Data flow**: It reads the root timestamp part of the queue key, falls back to message_ts, and returns a timestamp or None.

**Call relations**: Status tracking and progress posting use this to know which Slack thread can receive updates.

*Call graph*: called by 1 (_track_status).


##### `_thread_mirror_key`  (lines 2551–2552)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the scoped-store key for a conversation’s Slack thread mirror. This gives all hook-time code a consistent lookup key.

**Data flow**: It receives a conversation id and returns the thread/ prefixed key.

**Call relations**: Thread mirroring writes this key, and follow_turn and speak read it later.

*Call graph*: called by 3 (_mirror_thread, follow_turn, speak).


##### `_mirror_thread`  (lines 2555–2563)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Stores the Slack thread information for a core conversation. Turn-execution hooks need this later because they do not have the original Slack request.

**Data flow**: It receives a conversation id and MirroredThread, builds the store key, serializes the thread, and writes it to the scoped store.

**Call relations**: Inbound admission and interactive answer admission call this before a turn can execute and arm followers.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, interactive); 2 external calls (__init__, model_dump).


##### `_dm_anchor_key`  (lines 2566–2572)

```
def _dm_anchor_key(turn_id: UUID, message_ref: UUID | None=None) -> str
```

**Purpose**: Builds the scoped-store key that maps a turn or absorbed message to the Slack DM message it should answer under. DMs need this because their queue key does not include a thread root.

**Data flow**: It receives a turn id and optional message reference, appends the message reference when it differs from the turn id, and returns the dm_anchor key.

**Call relations**: DM anchoring, reply-thread lookup, and cleanup all use this shared key format.

*Call graph*: called by 3 (_anchor_dm_thread, _drop_turn_reply_records, _reply_thread).


##### `_anchor_dm_thread`  (lines 2575–2582)

```
async def _anchor_dm_thread(admitted: Admitted, message_ts: str) -> None
```

**Purpose**: Records which Slack DM message a member’s message corresponds to. This lets later replies thread under the correct DM message.

**Data flow**: It receives the admission result and Slack message timestamp, builds the anchor key from the turn and arrival id, and writes the timestamp to the scoped store.

**Call relations**: Inbound admission and form-submit admission call this for DMs after admitting the message.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_reply_thread`  (lines 2585–2600)

```
async def _reply_thread(queue_key: str, turn_id: UUID, message_ref: UUID | None=None) -> str | None
```

**Purpose**: Finds the Slack thread timestamp a reply should use. In channels it is the queue key’s root; in DMs it is the stored anchor for the turn or message reference.

**Data flow**: It receives queue key, turn id, and optional message ref, returns the channel root timestamp if present, otherwise reads DM anchors and returns a timestamp or None.

**Call relations**: Final replies, mid-turn replies, and file attachment sharing call this before posting to Slack.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 3 (attach, post, speak); 1 external calls (__init__).


##### `FollowerContext.workspace_id`  (lines 2611–2611)

```
def workspace_id(self) -> UUID
```

**Purpose**: Defines that a follower context must expose the current workspace id. Followers use it to key status state and footer links correctly.

**Data flow**: A concrete context provides the workspace id as a UUID property.

**Call relations**: Thread status, progress, and footer code rely on this interface whether they are running from a surface request or a hook.


##### `FollowerContext.public_base_url`  (lines 2614–2614)

```
def public_base_url(self) -> str | None
```

**Purpose**: Defines that a follower context may expose the deploy’s public URL. Followers use it to build links back to the web UI when available.

**Data flow**: A concrete context returns a URL string or None.

**Call relations**: Footer generation reads this through the FollowerContext interface.


##### `FollowerContext.credential`  (lines 2616–2616)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Defines how followers read needed credentials such as the Slack bot token. This keeps follower code independent of whether it runs in a request or hook.

**Data flow**: A concrete context receives a credential slot name and returns the secret string asynchronously.

**Call relations**: Thread status, progress, and posting helpers call this to authenticate Slack API writes.


##### `FollowerContext.tail`  (lines 2618–2620)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how followers subscribe to live turn frames. These frames describe activity, text streaming, resumes, and terminal states.

**Data flow**: A concrete context receives a turn id and optional cursor and returns an async stream of live frames.

**Call relations**: ThreadStatus and ThreadProgress use this to follow a running turn.


##### `FollowerContext.turn_is_terminal`  (lines 2622–2622)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Defines how progress reporters can check durable turn completion. This avoids posting progress after the final reply has already landed.

**Data flow**: A concrete context receives a turn id and returns true when the turn is terminal.

**Call relations**: ThreadProgress uses this during checkpoint waits.


##### `FollowerContext.conversation_agent`  (lines 2624–2624)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Defines how follower code finds the agent assigned to a conversation. The footer needs this to link to agent configuration.

**Data flow**: A concrete context receives a conversation id and returns an agent id or None.

**Call relations**: ThreadProgress footer rendering calls this before building the standard Slack footer.


##### `FollowerContext.is_operator_workspace`  (lines 2626–2626)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Defines how footer code can tell whether internal operator-only details may be shown. This protects cost/debug details from normal or external Slack rooms.

**Data flow**: A concrete context returns a boolean asynchronously.

**Call relations**: _slack_footer calls this through the follower context interface.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2683–2684)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Returns the unique key for the Slack thread whose native status is being written. It combines workspace, channel, and thread timestamp.

**Data flow**: It reads fields from the ThreadStatus instance and returns a tuple key.

**Call relations**: Status writer tracking uses this property to decide which turn is allowed to write a thread’s status.


##### `ThreadStatus.run`  (lines 2686–2705)

```
async def run(self) -> None
```

**Purpose**: Runs the lifecycle of Slack’s native assistant status for one turn. It starts with “Thinking…”, follows live frames, and clears the status when done.

**Data flow**: It reads the bot token, creates an HTTP client, writes the initial status, follows frames to update status text, handles cancellation or errors, and clears the status at the end.

**Call relations**: _run_status calls this inside a protected background task.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2707–2747)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one status line to Slack if this turn is currently allowed to be the thread’s writer. It treats a rejected Slack write as a skipped update, not a fatal follower failure.

**Data flow**: It checks the thread-writer claim, builds the assistant.threads.setStatus request, posts it to Slack, logs success or failure, and returns whether Slack accepted it.

**Call relations**: ThreadStatus.run, _follow, and _clear call this for all status updates.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2749–2758)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears Slack’s native status when the thread has no other live status follower. It avoids erasing a sibling turn’s status.

**Data flow**: It checks other live ThreadStatus objects for the same thread and only sends an empty status when none remain.

**Call relations**: ThreadStatus.run calls this on normal completion and after follower errors.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2760–2814)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Consumes live turn frames and turns them into short Slack status messages. It also refreshes the current status before Slack’s timeout and restamps after progress posts blank it.

**Data flow**: It listens for either the next live frame, a blanking event, or a refresh timeout, maps frames such as activity, absorbed messages, resume, or text streaming to status text, rate-limits writes, and exits on terminal or parked frames.

**Call relations**: ThreadStatus.run calls this after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2824–2831)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers for a thread so they rewrite the current status after Slack clears it. Posting a message in the same thread can clear Slack’s assistant status.

**Data flow**: It receives workspace, channel, and thread timestamp, finds matching live ThreadStatus objects, and sets their blanked event.

**Call relations**: ThreadProgress._say calls this after a progress message posts in the turn’s thread.

*Call graph*: called by 1 (_say).


##### `_track_status`  (lines 2834–2855)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts one native Slack status follower for a turn in this process. It also records that the newest turn is the current writer for that Slack thread.

**Data flow**: It skips if the turn is already tracked, finds channel and thread anchor, logs if unanchored, creates ThreadStatus, records writer/status maps, and starts the background task.

**Call relations**: _arm_followers calls this whenever admission or a turn hook wants Slack live status.

*Call graph*: calls 2 internal fn (anchor, _run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 2858–2886)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Wraps a ThreadStatus task with logging and cleanup. It also hands the thread writer claim back to an older live turn when the current writer ends.

**Data flow**: It awaits status.run, logs task-level failure, removes task/status records, and updates or deletes the writer entry for the thread.

**Call relations**: _track_status creates this as the background task body.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 2898–2902)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the progress-reporting schedule. The first interval must be positive and the cap cannot be smaller than the base interval.

**Data flow**: It reads base_seconds and cap_seconds on the dataclass and raises ValueError for impossible schedules.

**Call relations**: _track_progress constructs ProgressCadence before starting a progress reporter.


##### `ProgressCadence.intervals`  (lines 2904–2915)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait intervals between progress posts. The waits double with elapsed time until they hit a maximum cap.

**Data flow**: It starts with the base interval, yields waits forever, tracks elapsed time, and caps later waits at cap_seconds.

**Call relations**: checkpoints_after uses this to turn intervals into absolute elapsed checkpoints.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 2917–2926)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Generates future progress checkpoints after a turn has already been running for some time. This lets resumed reporters continue the real schedule instead of starting over.

**Data flow**: It receives elapsed seconds, walks the interval schedule, accumulates checkpoint times, and yields only checkpoints later than the elapsed time.

**Call relations**: ThreadProgress._follow uses this to choose when to post progress.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.update`  (lines 2939–2941)

```
def update(self, summary: str) -> None
```

**Purpose**: Records the latest completed activity summary for a running turn. It clears any text-streaming marker because a new activity has taken over.

**Data flow**: It receives a summary, collapses whitespace, truncates it to the progress limit, stores it as activity, and clears streaming chunks.

**Call relations**: ThreadProgress._follow calls this for activity and subagent activity frames.


##### `TurnActivity.stream`  (lines 2943–2944)

```
def stream(self, text: str) -> None
```

**Purpose**: Notes that response text is currently streaming. The progress message will say the response is being prepared rather than exposing unfinished text.

**Data flow**: It receives a text chunk and appends it to the streaming list.

**Call relations**: ThreadProgress._follow calls this for TextDelta frames.


##### `TurnActivity.current_step`  (lines 2946–2950)

```
def current_step(self) -> str
```

**Purpose**: Returns the member-facing current step for progress reporting. Text streaming takes priority over the last tool/activity summary.

**Data flow**: It checks whether any text is streaming and returns the response-preparation label, otherwise returns the stored activity string.

**Call relations**: TurnActivity.report calls this when building a progress post.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 2952–2962)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds the text for one progress checkpoint, or skips the checkpoint if no useful signal exists. It tells the member what step the turn is in and how long it has been running.

**Data flow**: It receives elapsed seconds, gets the current step, formats elapsed minutes or hours, and returns the progress line or None.

**Call relations**: ThreadProgress._post calls this before deciding whether to post a checkpoint message.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 3003–3006)

```
async def run(self) -> None
```

**Purpose**: Runs the long-turn progress reporter for one turn. It authenticates to Slack and follows live frames until the turn ends.

**Data flow**: It reads the bot token, opens an HTTP client, and delegates the reporting loop to _follow.

**Call relations**: _run_progress calls this inside a protected background task.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 3008–3011)

```
def _elapsed(self) -> float
```

**Purpose**: Calculates how long the member has been waiting for the turn. It uses wall-clock time from the durable turn start so restarts do not reset the timer.

**Data flow**: It subtracts started_at from the current UTC time and returns seconds.

**Call relations**: ThreadProgress._follow and _post_resumed use this to schedule and label progress.

*Call graph*: called by 2 (_follow, _post_resumed); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 3013–3066)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Runs the main progress-reporting loop. It watches live frames, posts at scheduled checkpoints, and posts a delayed resume notice when a restarted turn continues.

**Data flow**: It creates checkpoint deadlines, tracks activity, cost, and resume attempts, waits for frames or deadlines, posts progress or resume notices when due, and exits on terminal or parked frames.

**Call relations**: ThreadProgress.run calls this after setting up Slack access.

*Call graph*: calls 3 internal fn (_elapsed, _post, _post_resumed); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 3068–3095)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one scheduled progress checkpoint if there is activity worth reporting. A checkpoint with no signal is logged and skipped.

**Data flow**: It asks TurnActivity for report text, logs and returns false if none, otherwise passes the text to _say with timing and footer information.

**Call relations**: ThreadProgress._follow calls this whenever a progress checkpoint is reached.

*Call graph*: calls 2 internal fn (_say, report); called by 1 (_follow); 1 external calls (log).


##### `ThreadProgress._post_resumed`  (lines 3097–3108)

```
async def _post_resumed(self, client: httpx.AsyncClient, bot_token: str, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts the special notice that a turn resumed after a service restart. It waits briefly so a turn that immediately finishes does not produce a confusing notice.

**Data flow**: It builds the fixed resume notice and sends it through _say using the current elapsed time.

**Call relations**: ThreadProgress._follow calls this when a resume grace period expires.

*Call graph*: calls 2 internal fn (_elapsed, _say); called by 1 (_follow).


##### `ThreadProgress._say`  (lines 3110–3150)

```
async def _say(self, client: httpx.AsyncClient, bot_token: str, text: str, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Sends one progress or resume message into the Slack thread. It can include the standard footer on the first delivered progress message.

**Data flow**: It finds channel and thread anchor, optionally builds footer metadata, builds the Slack reply body, posts it, logs success or failure, restamps native status if needed, and returns whether it landed.

**Call relations**: ThreadProgress._post and _post_resumed use this for the actual Slack write.

*Call graph*: calls 4 internal fn (_footer, _restamp_thread_status, _slack_ok, slack_reply_body); called by 2 (_post, _post_resumed); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 3152–3173)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the standard Slack footer for a running turn’s first progress post. Since the turn is not terminal yet, it only includes information already known from live cost ticks.

**Data flow**: It reads the conversation’s agent id, formats cost/token information if present, and calls _slack_footer to produce footer text.

**Call relations**: ThreadProgress._say calls this only when sending the first progress message.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_say).


##### `_track_progress`  (lines 3179–3206)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, thread: MirroredThread, started_at: datetime) -> None
```

**Purpose**: Starts one long-turn progress reporter for a turn in this process. It is armed from the turn’s own execution so the reporter sees that execution’s live frames.

**Data flow**: It skips if already tracked, creates a ThreadProgress with cadence and timestamps, starts the background task, and stores it by turn id.

**Call relations**: _arm_followers calls this when it knows the turn’s durable start time.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 4 external calls (__init__, __init__, create_task, now).


##### `_run_progress`  (lines 3209–3224)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Wraps a ThreadProgress task with logging and cleanup. Individual post failures are contained elsewhere; this catches task-level failures.

**Data flow**: It awaits progress.run, logs any exception as abandoned reporting, and removes the progress task from the tracking table.

**Call relations**: _track_progress creates this as the background task body.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 3238–3252)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts all Slack side-channel followers for a turn: native status immediately, and long-run progress when the durable start time is known. It centralizes follower setup for admission and turn hooks.

**Data flow**: It receives a follower context, followed turn, and mirrored thread, starts status tracking, and starts progress tracking only when started_at is present.

**Call relations**: Inbound admission, interactive answer admission, and follow_turn all call this.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, follow_turn, interactive).


##### `_HookFollowerContext.workspace_id`  (lines 3264–3265)

```
def workspace_id(self) -> UUID
```

**Purpose**: Adapts a hook context to expose the workspace id expected by follower code.

**Data flow**: It returns the workspace id from the wrapped extension context.

**Call relations**: Followers armed by follow_turn use this through the FollowerContext interface.


##### `_HookFollowerContext.public_base_url`  (lines 3268–3269)

```
def public_base_url(self) -> str | None
```

**Purpose**: Adapts a hook context to expose the deploy public URL for footer links.

**Data flow**: It returns public_base_url from the wrapped extension context.

**Call relations**: Progress footer generation uses this when followers run from a hook.


##### `_HookFollowerContext.credential`  (lines 3271–3272)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Lets follower code read credentials from a hook’s extension credential store. This adapts hook-time access to the same interface used by request-time followers.

**Data flow**: It receives a credential slot and returns the stored credential from ctx.ext.credentials.

**Call relations**: Thread status and progress tasks use this to read the Slack bot token when armed by follow_turn.


##### `_HookFollowerContext.tail`  (lines 3274–3277)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Lets follower code subscribe to live turn frames through a hook context.

**Data flow**: It receives a turn id and cursor and returns the extension context’s tail stream.

**Call relations**: ThreadStatus and ThreadProgress use this when armed from follow_turn.


##### `_HookFollowerContext.turn_is_terminal`  (lines 3279–3280)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets progress reporters check durable turn completion through a hook context.

**Data flow**: It receives a turn id and returns the extension context’s terminal-state check result.

**Call relations**: ThreadProgress uses this when a checkpoint fires while running from a hook.


##### `_HookFollowerContext.conversation_agent`  (lines 3282–3283)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Lets follower footer code find a conversation’s agent through a hook context.

**Data flow**: It receives a conversation id and returns the agent id from the extension context.

**Call relations**: ThreadProgress._footer uses this through the FollowerContext interface.


##### `_HookFollowerContext.is_operator_workspace`  (lines 3285–3286)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Lets footer code decide whether operator-only information may be shown when running from a hook.

**Data flow**: It returns the extension context’s operator-workspace check result.

**Call relations**: _slack_footer calls this through the follower context adapter.


##### `follow_turn`  (lines 3289–3331)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Hook handler that arms Slack status and progress followers from the turn’s own execution. This restores followers after restarts and ensures progress follows the process that owns the turn.

**Data flow**: It checks the hook has a normal turn, reads the mirrored Slack thread with a short timeout, adapts the hook context, arms followers with the turn id, conversation id, and created time, and returns no hook outcome.

**Call relations**: The manifest hook calls this on user_prompt_submit; it delegates follower setup to _arm_followers.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `interactive`  (lines 3378–3482)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack interactive payloads such as ask-form submits and connect-button clicks. It verifies Slack’s signature, admits form answers, rewrites question messages, or posts private click responses.

**Data flow**: It reads and verifies the raw form body, loads bot token and identity, parses the interaction, handles connect clicks with an ephemeral message, or admits submitted answers as a turn and schedules a message rewrite.

**Call relations**: This is the Slack interactivity route; it coordinates parsing, member resolution, admission, follower arming, private responses, and form rewrites.

*Call graph*: calls 23 internal fn (admit, admitted_body, connect_url, conversation_for, find_conversation, linked_member, _anchor_dm_thread, _arm_followers, _bot_token, _ctx_signing_secret (+13 more)); 8 external calls (__init__, __init__, gather, conversation_audience, JSONResponse, Response, fence_member_message, mint_marker).


##### `_rewrite_in_background`  (lines 3488–3491)

```
def _rewrite_in_background(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Starts a background task to rewrite a Slack ask message after an answer is admitted. This keeps the interactivity acknowledgement fast.

**Data flow**: It creates a task for _run_rewrite, stores it in the shared task set, and removes it when finished.

**Call relations**: interactive calls this after confirming the submitted answer body is the one admitted.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3494–3498)

```
async def _run_rewrite(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Runs the ask-message rewrite and logs failures. A failed rewrite should not undo the admitted answer.

**Data flow**: It calls _replace_controls_with_answers and catches/logs any exception with the Slack message timestamp.

**Call relations**: _rewrite_in_background creates this task.

*Call graph*: calls 1 internal fn (_replace_controls_with_answers); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3501–3506)

```
def _ephemeral_in_background(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Starts a background task to send a private Slack response visible only to one user. This is used for connect links and empty-submit warnings.

**Data flow**: It creates a task for _post_ephemeral, stores it in the shared task set, and removes it when done.

**Call relations**: interactive calls this when it needs to answer a click without posting to the whole thread.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3509–3536)

```
async def _post_ephemeral(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Posts a private Slack message to one user, optionally inside the clicked thread. It uses chat.postEphemeral rather than public thread messages.

**Data flow**: It reads the bot token, builds the ephemeral message payload with channel, user, text, and optional thread timestamp, posts to Slack, and logs failures.

**Call relations**: _ephemeral_in_background runs this as a task after interactive events.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_interaction`  (lines 3539–3601)

```
def _to_interaction(raw: bytes, identity: SlackIdentity) -> AnswerSubmit | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactive form body into either an answer submission, a connect click, or nothing this surface cares about.

**Data flow**: It decodes the form payload JSON, checks type and team id, reads the first action, builds a ConnectClick for connect buttons or an AnswerSubmit for ask submits, and ignores other actions.

**Call relations**: interactive calls this after request verification and identity loading.

*Call graph*: calls 4 internal fn (_dict_field, _string_field, _submitted_answers, slack_thread_key); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_submitted_answers`  (lines 3604–3630)

```
def _submitted_answers(blocks: tuple[Mapping[str, object], ...], state: object) -> tuple[SubmittedAnswer, ...]
```

**Purpose**: Extracts every answer from a Slack ask form submit. It pairs each input block’s question label with the value held in Slack’s state payload.

**Data flow**: It walks delivered message blocks, keeps ask input blocks, reads their held state values, converts each value to text, and returns SubmittedAnswer records in display order.

**Call relations**: _to_interaction calls this when parsing an ask submit action.

*Call graph*: calls 1 internal fn (_held_answer); called by 1 (_to_interaction); 1 external calls (__init__).


##### `_held_answer`  (lines 3633–3651)

```
def _held_answer(field: object) -> str
```

**Purpose**: Converts one Slack form control’s held value into plain answer text. It supports radio buttons, checkboxes, and text inputs.

**Data flow**: It receives a Slack state field, switches on its control type, extracts the selected option, selected options, or typed value, and returns text or empty string.

**Call relations**: _submitted_answers uses this for each ask input block.

*Call graph*: calls 1 internal fn (_option_value); called by 1 (_submitted_answers); 1 external calls (get).


##### `_option_value`  (lines 3654–3658)

```
def _option_value(option: object) -> str
```

**Purpose**: Safely reads the submitted value from one Slack option object. Missing or malformed options become empty text.

**Data flow**: It receives an option object, returns its string value field if present, otherwise empty text.

**Call relations**: _held_answer uses this for radio-button and checkbox selections.

*Call graph*: called by 1 (_held_answer).


##### `_dict_field`  (lines 3661–3665)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload. It gives parsing code a clear failure when Slack payload shape is wrong.

**Data flow**: It receives a mapping and field name, returns the nested dictionary, or raises ValueError if it is missing or not a dictionary.

**Call relations**: _to_interaction uses this for user, channel, and message payload sections.

*Call graph*: called by 1 (_to_interaction).


##### `_replace_controls_with_answers`  (lines 3668–3701)

```
async def _replace_controls_with_answers(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Rewrites a Slack question message so form controls become the submitted answers. This makes the thread show what was committed and prevents later edits to the same form.

**Data flow**: It receives a submitted answer record, copies delivered blocks, replaces ask input blocks with answer context lines, replaces the submit row with submitted-by text, and calls Slack chat.update.

**Call relations**: _run_rewrite calls this after interactive answer admission succeeds.

*Call graph*: calls 2 internal fn (_context_line, _rewrite_slack_message); called by 1 (_run_rewrite).


##### `connect_message_key`  (lines 3723–3726)

```
def connect_message_key(member_id: UUID, provider: str) -> str
```

**Purpose**: Builds the store key for remembering where a connect button was posted. The key is based on the requester member and provider.

**Data flow**: It receives a member id and provider name and returns the connect-message key string.

**Call relations**: _hold_connect_message uses this when saving the location of a posted connect button.

*Call graph*: called by 1 (_hold_connect_message).


##### `_hold_connect_message`  (lines 3729–3759)

```
async def _hold_connect_message(store: ScopedStore, request: ConnectRequest | None, posted: dict[str, object], channel: str, ts: str | None) -> None
```

**Purpose**: Records the Slack message that contains a connect button, but only if the posted body actually carried that button. This lets later connection completion rewrite the right message.

**Data flow**: It checks for a connect request, Slack timestamp, and connect action block, then stores channel, message timestamp, and optional thread timestamp under the requester/provider key.

**Call relations**: post calls this after Slack accepts a final reply that may contain a connect button.

*Call graph*: calls 3 internal fn (put, _is_connect_action, connect_message_key); called by 1 (post); 1 external calls (__init__).


##### `_is_connect_action`  (lines 3762–3769)

```
def _is_connect_action(block: Mapping[str, object]) -> bool
```

**Purpose**: Checks whether a Slack block contains this surface’s connect button. It is used to decide whether a message still has an active connection prompt.

**Data flow**: It receives a block, verifies it is an actions block, scans its elements, and returns true when any element has the connect action id.

**Call relations**: _hold_connect_message and settle_connect_message use this to recognize connect buttons.

*Call graph*: called by 2 (_hold_connect_message, settle_connect_message).


##### `_rewrite_slack_message`  (lines 3772–3789)

```
async def _rewrite_slack_message(bot_token: str, channel: str, ts: str, text: str, blocks: list[dict[str, object]]) -> None
```

**Purpose**: Updates an existing Slack bot message with new text and blocks. It is the shared low-level helper for ask-answer and connect-settled rewrites.

**Data flow**: It receives bot token, channel, timestamp, fallback text, and block list, posts chat.update to Slack, and raises if Slack reports failure.

**Call relations**: _replace_controls_with_answers and settle_connect_message call this after constructing replacement blocks.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_replace_controls_with_answers, settle_connect_message); 2 external calls (AsyncClient, dumps).


##### `_held_connect_message`  (lines 3792–3828)

```
async def _held_connect_message(bot_token: str, held: ConnectMessage) -> Mapping[str, object] | None
```

**Purpose**: Reads the current Slack message that holds a previously posted connect button. Reading the live message avoids overwriting later changes such as answered ask forms.

**Data flow**: It builds a bounded Slack history or replies query for the exact timestamp, fetches messages, and returns the matching message dictionary or None.

**Call relations**: settle_connect_message calls this before rewriting a connect button into a settled account line.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (settle_connect_message); 1 external calls (AsyncClient).


##### `settle_connect_message`  (lines 3831–3857)

```
async def settle_connect_message(bot_token: str, held: ConnectMessage, provider: str, account: str) -> None
```

**Purpose**: Rewrites a connect button message after the external account connection succeeds. The button is replaced with a line saying which account was connected.

**Data flow**: It reads the live Slack message, checks the connect button is still present, removes connect-action blocks, appends a settled context line, and updates the Slack message.

**Call relations**: Connection-completion hook code can call this using the stored ConnectMessage.

*Call graph*: calls 4 internal fn (_context_line, _held_connect_message, _is_connect_action, _rewrite_slack_message).


##### `_context_line`  (lines 3860–3864)

```
def _context_line(text: str) -> dict[str, object]
```

**Purpose**: Creates a small Slack context block containing Markdown text. It is used for status-like lines inside rewritten messages.

**Data flow**: It receives text, truncates it to Slack’s context limit, and returns a Block Kit context block.

**Call relations**: Ask-answer rewriting and connect-message settling use this for replacement lines.

*Call graph*: called by 2 (_replace_controls_with_answers, settle_connect_message).


##### `_reply_text`  (lines 3867–3877)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a terminal turn reply. It distinguishes normal answers, failed turns, cancelled turns, and empty successful replies.

**Data flow**: It receives a Writeback, checks terminal status, and returns the agent text or the appropriate surface-owned fallback line.

**Call relations**: _reply_with_oversize_links calls this before adding credential hints or large-file links.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 3880–3909)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds Slack-specific delivery extras to the final reply text: credential instructions and links for files too large to upload. This prevents important actions or artifacts from silently disappearing.

**Data flow**: It starts with _reply_text, appends a portal credential hint when needed, finds oversized artifacts, formats download-link lines, and returns the combined text.

**Call relations**: post calls this before splitting and sending the final Slack reply.

*Call graph*: calls 3 internal fn (home_url, _oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 3912–3915)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one oversized shared artifact as a Markdown list item. It uses a temporary portal link when available.

**Data flow**: It receives an artifact, asks the context for an artifact link, chooses linked or plain filename text, and returns a line with size in bytes.

**Call relations**: _reply_with_oversize_links calls this for each artifact too large for Slack upload.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_reply_mention_ids`  (lines 3918–3929)

```
async def _reply_mention_ids(ctx: SurfaceContext, bot_token: str, channel: str, text: str) -> dict[str, str]
```

**Purpose**: Builds the mention-id map for an outgoing reply only when the reply contains @ text. This avoids unnecessary Slack roster reads.

**Data flow**: It checks the text for @, reads the current Slack identity, and asks SlackNames for safe mention ids for the channel, returning an empty map when not possible.

**Call relations**: _reply_mentions_mapped calls this before pinning the mention map in delivery progress.

*Call graph*: calls 1 internal fn (_identity); called by 1 (_reply_mentions_mapped); 1 external calls (__init__).


##### `_channel_info`  (lines 3932–3946)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Reads Slack metadata for a channel, private channel, DM, or group DM. It is best-effort and returns None when Slack cannot provide usable info.

**Data flow**: It calls conversations.info with the bot token and channel id, validates the channel object, and returns it or None.

**Call relations**: Channel audience detection, external-share checks, and name resolution call this.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 3949–3962)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack destination may include people outside the bound workspace. If the check cannot read channel info, it fails closed by treating the channel as external.

**Data flow**: It reads channel info and returns true for Slack shared-channel flags or when info is unavailable.

**Call relations**: _slack_footer uses this before deciding whether to show operator-only accounting and debug links.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 3965–4000)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, agent_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the small footer shown under Slack replies and first progress posts. It can include web links, agent config, cost accounting, and debug links when safe.

**Data flow**: It receives context, Slack channel, conversation, agent, turn, and optional accounting text, builds web URLs when possible, hides operator details outside operator/private-safe contexts, and returns bounded footer text or None.

**Call relations**: Final reply posting and progress footer rendering call this.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 4022–4027)

```
def _slack_reply_progress_key(turn_id: UUID, reply_id: UUID | None=None) -> str
```

**Purpose**: Builds the scoped-store key that tracks delivery progress for a Slack reply. Terminal replies and mid-turn spans get separate keys under the same turn.

**Data flow**: It receives a turn id and optional reply/span id and returns the reply_progress key string.

**Call relations**: post, speak, and cleanup use this to read, update, or delete reply delivery records.

*Call graph*: called by 3 (_drop_turn_reply_records, post, speak).


##### `_slack_reply_progress`  (lines 4030–4043)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Reads or creates the durable delivery-progress record for a Slack reply. This record makes retries continue instead of duplicating messages.

**Data flow**: It reads the store key, validates an existing progress record, or atomically creates an empty one and returns both the model and raw stored value.

**Call relations**: post and speak call this before sending any Slack reply parts.

*Call graph*: calls 2 internal fn (get, put_if); called by 2 (post, speak); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 4046–4055)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Atomically writes an updated Slack reply progress checkpoint. If someone else changed the record, it raises so delivery can retry safely later.

**Data flow**: It serializes the progress model, writes it with compare-and-set against the expected stored value, and returns the new progress and encoded value.

**Call relations**: Reply delivery, mention mapping, post, and speak use this after each important delivery step.

*Call graph*: calls 1 internal fn (put_if); called by 4 (_deliver_slack_reply, _reply_mentions_mapped, post, speak); 2 external calls (__init__, model_dump).


##### `_drop_turn_reply_records`  (lines 4058–4068)

```
async def _drop_turn_reply_records(store: ScopedStore, turn_id: UUID) -> None
```

**Purpose**: Deletes temporary delivery records and DM anchors for a turn after final delivery is settled. This prevents old retry data from lingering forever.

**Data flow**: It lists store keys under the turn’s reply-progress and DM-anchor prefixes and deletes each one.

**Call relations**: attach calls this after core has recorded the final reply reference, and post calls it when a silent reply posts nothing.

*Call graph*: calls 4 internal fn (delete, list, _dm_anchor_key, _slack_reply_progress_key); called by 2 (attach, post).


##### `_slack_reply_delivery`  (lines 4071–4086)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Checks whether a Slack message has the delivery metadata id this surface uses for deduplication. If so, it returns the Slack timestamp.

**Data flow**: It receives a Slack message object, validates metadata event type and payload id, reads ts, and returns the timestamp or None.

**Call relations**: _reconcile_slack_reply uses this while scanning Slack for a possibly already-posted message.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 4089–4129)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Looks back through Slack history for a message whose post request may have succeeded even though the response was lost. This prevents duplicate retry posts.

**Data flow**: It pages through recent channel or thread messages with metadata included, searches for the pending delivery id, returns the timestamp if found, or None if not found within the bounded window.

**Call relations**: post and speak call this when their delivery progress record has a pending Slack post.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 2 (post, speak); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 4132–4172)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Posts one Slack reply body exactly once as far as this surface can tell. It checkpoints pending and delivered states around the Slack API call.

**Data flow**: It checks whether this delivery id was already recorded, records it as pending, posts to Slack, handles recoverable invalid_blocks responses, records the accepted timestamp, and returns updated progress plus Slack payload.

**Call relations**: post and speak use this for every message part and fallback attempt.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 2 (post, speak); 2 external calls (__init__, model_copy).


##### `_reply_mentions_mapped`  (lines 4175–4205)

```
async def _reply_mentions_mapped(ctx: SurfaceContext, bot_token: str, channel: str, text: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue) -> tuple[_SlackReplyPro
```

**Purpose**: Replaces safe agent-written @names with Slack mention markup, and pins the mapping in the delivery record. Pinning keeps retries deterministic.

**Data flow**: It checks whether mention mappings are already stored, otherwise resolves and checkpoints them, then returns the text with names replaced by Slack mention syntax.

**Call relations**: post and speak call this before splitting text into Slack message parts.

*Call graph*: calls 2 internal fn (_checkpoint_slack_reply, _reply_mention_ids); called by 2 (post, speak); 2 external calls (model_copy, mention_markup).


##### `post`  (lines 4208–4405)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str | NothingDelivered
```

**Purpose**: Delivers the final reply for a completed turn to Slack. It handles silence suppression, long-message splitting, forms, connect buttons, footers, mention mapping, retries, and Block Kit fallback.

**Data flow**: It reads the destination thread and bot token, loads delivery progress, suppresses pure silence when appropriate, maps mentions, builds actions and footer, posts each part with checkpoints and reconciliation, records connect-button locations, marks delivery complete, and returns the first Slack message ref.

**Call relations**: The core writeback poller calls this when a turn reaches terminal state; attach may run afterward for shared files.

*Call graph*: calls 17 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _drop_turn_reply_records, _hold_connect_message, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _reply_with_oversize_links (+7 more)); 7 external calls (__init__, __init__, __init__, AsyncClient, loads, log, is_silence_sentinel).


##### `speak`  (lines 4408–4505)

```
async def speak(ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Delivers a mid-turn reply to Slack before the final turn answer. It uses the same exactly-once delivery machinery but without final footers, forms, or files.

**Data flow**: It finds the correct Slack thread, reads bot token and delivery progress, maps mentions, reconciles any pending delivery, posts split message parts with checkpoints, marks complete, and returns the first Slack message ref.

**Call relations**: Core mid-turn delivery calls this when the agent speaks during a running turn.

*Call graph*: calls 12 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _slack_reply_progress, _slack_reply_progress_key, _thread_mirror_key (+2 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 4508–4548)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Posts one chat.postMessage request and returns Slack’s parsed payload without requiring ok:true. This lets callers handle recoverable Slack errors such as invalid_blocks themselves.

**Data flow**: It sends the encoded body to Slack, converts HTTP errors into SurfaceDeliveryError with retry-after information when available, and returns the JSON payload.

**Call relations**: _deliver_slack_reply uses this for all reply message posts.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 4551–4557)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the Slack timestamp from a successful post response. It raises a SlackApiError if Slack did not accept the post or omitted the timestamp.

**Data flow**: It receives a Slack payload, checks ok is true, reads ts, and returns it as a non-empty string.

**Call relations**: Reply delivery, final post, and mid-turn speak use this after Slack post attempts.

*Call graph*: called by 3 (_deliver_slack_reply, post, speak); 1 external calls (__init__).


##### `attach`  (lines 4560–4599)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads files shared by a completed turn to Slack after the reply message exists. Files too large for Slack are already linked in the reply text instead.

**Data flow**: It finds the reply thread, drops temporary reply records, filters uploadable artifacts, reserves and uploads files concurrently, groups them within Slack’s attachment limit, and shares each batch into the thread.

**Call relations**: The core delivery flow calls this after post has returned the terminal reply reference.

*Call graph*: calls 6 internal fn (credential, _attachment_batches, _drop_turn_reply_records, _reply_thread, _share_uploaded_files, _upload_artifact); 4 external calls (__init__, gather, AsyncClient, Timeout).


##### `_attachment_batches`  (lines 4602–4606)

```
def _attachment_batches(files: Sequence[dict[str, str]]) -> Iterator[Sequence[dict[str, str]]]
```

**Purpose**: Splits uploaded-file metadata into groups Slack will accept in one share call. This avoids Slack’s hidden maximum-file-per-message limit.

**Data flow**: It receives a sequence of file dictionaries and yields slices of at most the Slack attachment cap.

**Call relations**: attach calls this before sharing uploaded files into the Slack conversation.

*Call graph*: called by 1 (attach).


##### `_upload_artifact`  (lines 4609–4636)

```
async def _upload_artifact(ctx: SurfaceContext, client: httpx.AsyncClient, bot_token: str, artifact: SharedArtifact) -> str
```

**Purpose**: Uploads one shared artifact to Slack’s external file-upload endpoint. It streams bytes from the blob store rather than buffering the whole file.

**Data flow**: It reserves an upload URL and file id from Slack, streams the artifact blob to the upload URL, checks the upload succeeded, and returns the Slack file id.

**Call relations**: attach runs this concurrently for every uploadable artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (__init__, post).


##### `_share_uploaded_files`  (lines 4639–4663)

```
async def _share_uploaded_files(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, files: Sequence[dict[str, str]]) -> None
```

**Purpose**: Completes Slack’s external upload flow by sharing uploaded files into the channel or thread. One call can share a batch of files as one Slack message.

**Data flow**: It receives file ids and titles, channel, optional thread timestamp, builds the completeUploadExternal payload, and posts it to Slack.

**Call relations**: attach calls this for each batch after uploads have succeeded.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (post, dumps).


##### `_slack_ok`  (lines 4666–4675)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Common helper for Slack Web API calls that must return ok:true. It turns Slack API failures into SlackApiError with useful detail.

**Data flow**: It awaits an HTTP response, checks HTTP status, parses JSON, verifies ok is true, and returns the payload or raises an error naming Slack’s error.

**Call relations**: Most Slack API reads and writes in this file use this to keep success checking consistent.

*Call graph*: called by 18 (_list, _members, _say, _set, _ambient_context, _channel_info, _conversation_members, _declared_files, _held_connect_message, _post_ephemeral (+8 more)); 1 external calls (__init__).


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The `ufo` command-line client is intentionally simple: it sends HTTP requests and reads back lines of text. This file is the server side of that contract. It decides what each terminal screen should show and sends it as tab-separated “directives,” such as `say`, `txt`, `ask`, `file`, `run`, `poll`, or `listen`.

A normal request either sends a member’s message or reconnects to keep watching the latest turn. The file admits messages into the durable conversation queue, tails the live frames produced by the agent, and translates those frames into terminal-friendly lines. If the answer takes longer than the held HTTP request, the server tells the client to poll again instead of letting the connection time out. When a turn is finished, the server can tell the client to idle-listen, because conversations may wake up later without a new typed message.

It also covers side paths that matter in a real terminal: stopping a turn, sending a second message while the first is still running, retracting a queued message, fulfilling private credential prompts, downloading operation bodies, and fetching a bundle of system skills. Without this file, the shell client would have no reliable way to authenticate, resume streams, avoid duplicate output, run local terminal operations, or display the agent’s work as it happens.

#### Function details

##### `terminal_runtime_id`  (lines 109–111)

```
def terminal_runtime_id(channel: str) -> str
```

**Purpose**: Creates a stable local runtime identifier for one terminal conversation. This gives the same channel the same short namespace each time, which helps terminal-side work stay tied to the right conversation.

**Data flow**: It takes a channel name as text, hashes it with SHA-256, and returns the first fixed number of hexadecimal characters. The original channel name is not returned, only its stable shortened hash.

**Call relations**: When `channel.bound` connects a terminal to a conversation, it calls this helper to name the local runtime before registering the terminal connection with the surface context.

*Call graph*: called by 1 (bound); 1 external calls (sha256).


##### `directive`  (lines 114–122)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one line of the tiny protocol spoken to the shell client. It makes sure fields cannot accidentally break the line format by escaping tabs, newlines, and backslashes.

**Data flow**: It receives a command word and any number of text fields. It escapes unsafe characters in each field, joins everything with tabs, adds a newline, and returns bytes ready to send over HTTP.

**Call relations**: Almost every rendering path uses this helper. Higher-level functions such as `directives_for`, `_answer`, `_send`, `_fulfill_secret`, and `channel` decide what should happen; `directive` turns that decision into the exact bytes the shell can read.

*Call graph*: called by 10 (_answer, _client_update, _fulfill_secret, _say_lines, _send, _subagent_note, channel, directives_for, history_directives, stream_directives).


##### `shared_files`  (lines 136–147)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files an agent shared during a turn and prepares the information the terminal can show. It adds public download links when the deployment is configured to provide them.

**Data flow**: It receives the surface context and a turn id. It asks the context for the turn’s shared artifacts, converts each artifact into a `SharedFile` with filename, size, and URL, and returns them as an ordered tuple.

**Call relations**: `stream_directives` receives this function through `channel` and calls it only when a turn reaches a terminal frame, so the file list is complete before it is printed.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 150–157)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies which workspace an incoming request claims to belong to before the route handler runs. If the request does not carry a valid bearer-style authorization header, it rejects the request by returning nothing.

**Data flow**: It reads the `Authorization` header, checks that it is a Bearer token, extracts the token, and asks the bearer codec for the workspace claim. The result is a workspace UUID or `None`.

**Call relations**: This is the early identification hook for the surface. The route handlers later verify the same bearer token again for the member email, but this function scopes the request to a workspace first.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 163–222)

```
def history_directives(conversation: Conversation) -> tuple[bytes, ...]
```

**Purpose**: Turns past conversation messages into terminal lines for a fresh resume. It lets the client rebuild enough context without replaying the latest live turn twice.

**Data flow**: It receives a conversation transcript. It walks through old user and assistant messages, folds tool work into short notes, keeps the newest content within a character budget, and returns `you`, `note`, and `say` directive bytes.

**Call relations**: `channel` uses this when an empty reconnect has no `since` cursor. It relies on `_history_text` to extract readable text and `_dispatched` to count real tool steps before using `directive` to format the output.

*Call graph*: calls 3 internal fn (_dispatched, _history_text, directive); called by 1 (channel).


##### `_dispatched`  (lines 225–232)

```
def _dispatched(message: Message, active: set[str]) -> int
```

**Purpose**: Counts how many tool calls in an assistant message actually became live work. This avoids showing a “completed step” for a call that was written but never dispatched.

**Data flow**: It receives one message and a set of active tool-use ids. If the message has structured content, it counts tool-use blocks whose ids are in that active set; plain text messages count as zero.

**Call relations**: `history_directives` calls this while summarizing old assistant messages into terminal notes that match what the live session would have shown.

*Call graph*: called by 1 (history_directives).


##### `_history_text`  (lines 235–240)

```
def _history_text(message: Message) -> str
```

**Purpose**: Extracts the human-readable text from a stored message. It normalizes user messages so they display like the member originally typed them.

**Data flow**: It receives a message. If the content is plain text, it uses that directly; if the content is block-based, it joins the text blocks. For user messages it runs the result through `member_message_text`; for assistant messages it returns the raw text.

**Call relations**: `history_directives` uses this helper for each message before deciding whether to print it as `you`, `say`, or part of a summarized note.

*Call graph*: called by 1 (history_directives); 1 external calls (member_message_text).


##### `directives_for`  (lines 243–284)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True) -> tuple[bytes, ...
```

**Purpose**: Translates one live conversation frame into the directive lines the shell should render. It is the main dictionary between the system’s internal event types and the terminal protocol.

**Data flow**: It receives a live frame plus context such as whether text was already streamed, pending credential prompts, connection messages, shared files, and whether the client should exit on cancellation. It returns zero or more directive byte lines.

**Call relations**: `stream_directives` calls this for every frame it reads from the live tail. For special cases it hands off to `_subagent_note` or `_answer`; for simple frames it uses `directive` directly.

*Call graph*: calls 3 internal fn (_answer, _subagent_note, directive); called by 1 (stream_directives).


##### `_subagent_note`  (lines 287–293)

```
def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]
```

**Purpose**: Formats progress messages from a subagent as terminal notes. It keeps the parent agent’s start and finish narration from being duplicated.

**Data flow**: It receives a subagent activity frame. If the frame has activity text, it builds one `note` directive labeled with the subagent name or profile; otherwise it returns no lines.

**Call relations**: `directives_for` calls this only for subagent activity frames, so subagent progress appears in the same stream as ordinary agent notes.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for).


##### `_answer`  (lines 296–349)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True) -> tuple[bytes, ...]
```

**Purpose**: Builds the final terminal output for a completed, failed, or cancelled turn. It decides whether to show the answer, shared files, credential prompts, a normal prompt, or an exit instruction.

**Data flow**: It receives a terminal frame and surrounding details. For a successful turn it may print answer text, file links, secret prompts, a connection link, and then `ask`; for failure it prints a safe error and prompts; for cancellation it either prompts or exits depending on the situation.

**Call relations**: `directives_for` calls this whenever a live frame says the turn has reached its end. It uses `_say_lines` and `directive` to produce the concrete shell protocol lines.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 352–353)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Splits display text into one `say` directive per line. This keeps multi-line messages readable in the shell protocol.

**Data flow**: It receives a string, splits it into lines, and wraps each line with `directive("say", ...)`. If the string is empty, it still returns one `say` line for that empty text.

**Call relations**: `_answer` uses this when it needs to print final answer text, failure text, or cancellation reasons.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 356–513)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Keeps an HTTP response open while a turn is running and streams terminal directives as live frames arrive. It also knows when to stop the stream and tell the client to poll, listen, run a local operation, or resume from a cursor.

**Data flow**: It receives a live frame tail, timing limits, optional callbacks for credentials, connection links, shared files, terminal operations, and resume state. It reads frames until the turn ends, an operation must run, the hold time expires, or the tail ends, yielding directive bytes along the way and finally adding `since`, `poll`, or `listen` when needed.

**Call relations**: `channel` creates this stream for the chosen turn. Inside, it uses `_next` to safely read frames, `directives_for` to render them, and `directive` to create protocol lines for resume cursors and local terminal operations.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 4 external calls (ensure_future, get_running_loop, wait, suppress).


##### `_next`  (lines 516–522)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next item from an async frame stream and turns normal stream exhaustion into `None`. This makes the calling loop simpler and avoids treating the end of a stream like an error.

**Data flow**: It receives an asynchronous iterator of live frames. It awaits one item and returns it; if the iterator is finished, it returns `None`.

**Call relations**: `stream_directives` wraps frame reads in tasks so it can race them against timeouts and terminal operations. `_next` gives that task a clean result even when the frame stream is done.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 525–529)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Checks the request’s bearer token and extracts the authenticated email address. This is how the terminal request proves which member is speaking.

**Data flow**: It reads the `Authorization` header, verifies that it contains a Bearer token, and asks the bearer verifier to validate it for the current workspace. It returns an email string or `None`.

**Call relations**: `channel`, `op_body`, and `system_skills` call this at the start of request handling. If it returns `None`, those routes answer with an unauthorized response.

*Call graph*: called by 3 (channel, op_body, system_skills); 1 external calls (verify_token).


##### `_utf8_header`  (lines 532–539)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Recovers a header value that the shell sent as raw UTF-8 bytes, such as a working directory path. This matters because HTTP libraries often decode headers as ISO-8859-1 first.

**Data flow**: It reads a named header, trims it, re-encodes it as latin-1 to recover the original bytes, then decodes those bytes as UTF-8. Bad bytes are replaced rather than crashing the request.

**Call relations**: `channel` uses this for headers where non-ASCII text can appear, especially the terminal’s current working directory and operation error text.

*Call graph*: called by 1 (channel).


##### `_stale_client`  (lines 542–546)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Checks whether the shell script version making the request differs from the version the server wants to serve. This lets the server nudge old clients to update instead of speaking a protocol they may not understand.

**Data flow**: It reads the expected client version from the `UFO_CLIENT_VERSION` environment variable and compares it with the request’s `x-ufo-script` header. It returns true only when the server has a version set and the client does not match.

**Call relations**: `channel` consults this before admitting messages, resuming streams, stopping turns, or accepting operation replies, so outdated clients get an update path at safe boundaries.

*Call graph*: called by 1 (channel).


##### `_client_update`  (lines 549–550)

```
def _client_update() -> bytes
```

**Purpose**: Builds the small response that tells the shell to install an updated client and explains why. It is the friendly protocol version of “please upgrade.”

**Data flow**: It creates an `install` directive followed by a `say` directive with the stale-client message, then returns the combined bytes.

**Call relations**: `channel` returns this when `_stale_client` says the current request should not continue with the old script.

*Call graph*: calls 1 internal fn (directive); called by 1 (channel).


##### `_resumed_from`  (lines 553–560)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Decides which live-frame cursor can safely be used when a client reconnects. It prevents a cursor from an older turn from skipping the beginning of a newer turn.

**Data flow**: It reads the `x-ufo-since` header, splits it into the turn id and cursor, and compares the named turn with the current turn. It returns the cursor only when the turn ids match; otherwise it returns an empty cursor.

**Call relations**: `channel` calls this before opening the live tail. The result is passed into `ctx.tail` and `stream_directives` so replay starts from the right place.

*Call graph*: called by 1 (channel).


##### `_turn_context`  (lines 563–575)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the context attached to a newly admitted member message. It records who sent the message, where it came from, and, when valid, the member’s reported time zone.

**Data flow**: It receives the authenticated email and request. It reads the timezone header, tries to create a `TurnContext`, logs and drops the timezone if validation fails, and returns a context object either way.

**Call relations**: `channel` and `_send` call this just before admitting a message through the surface context, so the engine can attribute the turn correctly.

*Call graph*: called by 2 (_send, channel); 2 external calls (__init__, log).


##### `channel`  (lines 578–725)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles the main `ufo` terminal endpoint for one conversation channel. It authenticates the member, admits messages or control actions, and returns either a short plain-text response or a live streaming response.

**Data flow**: It reads authorization, headers, path parameters, and the request body. Depending on the headers, it may fulfill a secret, send a message, unsend a pending message, resolve a terminal operation, stop a turn, resume an existing turn, or admit a new message; then it streams directives for the relevant turn back to the shell.

**Call relations**: This is the central route in the file. It calls many small helpers for authentication, UTF-8 headers, stale-client checks, history rendering, sending, unsending, secret fulfillment, resume cursors, and streaming; it delegates persistent conversation work to `SurfaceContext`.

*Call graph*: calls 23 internal fn (admit, claim_terminal, conversation_for, latest_turn, link_member, linked_member, read_transcript, stop_turn, tail, terminal_resolve (+13 more)); 5 external calls (partial, conversation_audience, PlainTextResponse, body, StreamingResponse).


##### `channel.moved_on`  (lines 691–693)

```
async def moved_on() -> bool
```

**Purpose**: Checks whether the conversation has already advanced to a newer running turn after the current streamed turn ended. This helps the client jump forward without replaying old history.

**Data flow**: It asks the surface context for the latest turn in the conversation, compares it with the turn currently being streamed, and checks whether that latest turn is still non-terminal. It returns a boolean answer.

**Call relations**: `channel` passes this callback into `stream_directives`. When a terminal frame is reached, `stream_directives` calls it to decide whether to send an immediate `poll` for the newer turn instead of entering idle listening.


##### `channel.bound`  (lines 708–723)

```
async def bound() -> AsyncIterator[bytes]
```

**Purpose**: Wraps the live directive stream with terminal connection setup and cleanup. It tells the backend that this terminal is available for local operations while the HTTP stream is open.

**Data flow**: Before yielding data, it may compute a runtime id from the channel and connect the terminal using the current working directory. It then yields the sent acknowledgement, history, workspace note, and live directives; when the stream ends, it disconnects the terminal.

**Call relations**: `channel` returns this generator inside a `StreamingResponse`. It calls `terminal_runtime_id` during setup and surrounds the `stream_directives` output so local `run` operations have an active terminal rendezvous.

*Call graph*: calls 1 internal fn (terminal_runtime_id).


##### `_send`  (lines 728–778)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str) -> Response
```

**Purpose**: Admits one message quickly and returns only an acknowledgement, without holding open a live stream. This is used when the member sends another message while a separate request is already watching the conversation.

**Data flow**: It reads a required send id header, reads and validates the message body, optionally claims the terminal working directory, admits the message with an idempotency key, and returns a `sent` directive plus a workspace note if a new terminal claim was made.

**Call relations**: `channel` calls this when the request has `x-ufo-send`. It uses `_turn_context` and `directive`, while the actual effects of the message are later seen by the already-held stream.

*Call graph*: calls 4 internal fn (admit, claim_terminal, _turn_context, directive); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 781–803)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Retracts a queued message if the agent has not taken it up yet. This supports the terminal behavior where a user can pull back a pending row before it becomes part of a turn.

**Data flow**: It requires an empty body, checks that there is a member, parses the arrival id, and asks the surface context to retract that arrival for that member. It returns empty success if retracted or a conflict message if the message was already used or gone.

**Call relations**: `channel` calls this when the request carries the unsend header. It does not admit a new turn; it only asks the durable queue to remove a still-pending arrival.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 806–825)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores a privately entered credential value for a pending credential prompt. The value is not treated as a chat message and is not written into the conversation transcript.

**Data flow**: It reads the credential slot from a header and the secret value from the body, validates that both are present and not too large, then asks the surface context to fulfill the sealed credential request. It returns a `say` directive explaining whether the value was stored.

**Call relations**: `channel` calls this early when a request carries the secret header. It uses the privileged surface context to verify the sealed request and store the value safely.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 828–840)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the raw bytes associated with an in-flight terminal operation. This lets the shell download operation input, such as data to write into a temporary file.

**Data flow**: It authenticates the request, finds the linked member, builds the conversation queue key from email and channel, and asks the surface context for the operation body. It returns the bytes as an octet stream or a 404-style text response if no operation matches.

**Call relations**: This is the GET route for `{channel}/op/{op_id}`. It shares authentication with the main channel route through `_authenticated_email` but does not create messages or turns.

*Call graph*: calls 3 internal fn (linked_member, terminal_op_body, _authenticated_email); 2 external calls (PlainTextResponse, Response).


##### `system_skills`  (lines 843–855)

```
async def system_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the system skill bundle to an authenticated shell client. It uses normal HTTP caching headers so the client can avoid downloading the same zip archive repeatedly.

**Data flow**: It authenticates the request, reads the current skill bundle from the surface context, builds an ETag from the bundle digest, and compares it with `If-None-Match`. It returns either 304 not modified or the zip archive bytes.

**Call relations**: This is the GET route for `{channel}/skills`. It uses `_authenticated_email` for access control and otherwise acts as a small download endpoint for client-side support files.

*Call graph*: calls 1 internal fn (_authenticated_email); 2 external calls (PlainTextResponse, Response).


### `core/src/ufo/runtime/ext/surface.py`

`orchestration` · `request handling, background delivery, and cross-cutting surface access`

A surface is the place where a person meets the agent: a Slack thread, a browser chat, a phone message, or another front door. This file defines the privileged contract those front doors use. That matters because admitting a message as a real workspace member is sensitive: if this seam were wrong, a surface could speak as the wrong person, read another tenant’s data, or lose replies after a turn finishes.

The file has three broad jobs. First, it defines small data shapes used by surfaces, such as conversation summaries, shared file records, agent settings, connection views, and delivery payloads. Second, it defines SurfaceContext, the large trusted toolbox a surface receives after the workspace is known. Through it, a surface can resolve identities, create or find conversations, admit messages, read transcripts and files, create signed download links, manage credentials, and show portal administration data. Third, it runs background delivery machinery. Durable surfaces, like Slack, cannot rely on live in-memory events, so WritebackPoller and MidTurnReplyPoller repeatedly claim database rows, call the surface’s send functions, and mark delivery as complete or retry later.

A useful analogy is a staffed reception desk. The surface checks who came in, SurfaceContext gives the receptionist the authorized forms and keys, and the pollers make sure outgoing mail is posted even if the first carrier fails.

#### Function details

##### `is_silence_sentinel`  (lines 213–221)

```
def is_silence_sentinel(answer: str) -> bool
```

**Purpose**: Checks whether a final answer is intentionally empty, using the special empty-response markers the model may emit. Surfaces use this so they do not post a meaningless blank reply.

**Data flow**: Takes an answer string → trims it and compares the whole text against the accepted silence patterns → returns true only when the entire answer means “say nothing.”

**Call relations**: It is a shared helper for delivery code that needs to distinguish a real reply from a deliberate no-reply marker before posting to an external surface.


##### `mint_marker`  (lines 224–234)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to wrap one member message safely. The marker makes the wrapper unique so ordinary member text cannot accidentally close or imitate it.

**Data flow**: Takes no input → asks the secrets library for random hex bytes → returns that marker as text.

**Call relations**: It feeds the message-fencing flow: a surface can mint a marker and pass it to fence_member_message before admitting the message.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 237–250)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the exact text that represents one inbound member message, separating channel context, the member’s own words, and attachment text. This keeps the model from confusing background context with what the person actually said.

**Data flow**: Takes a marker, ambient context, message body, and attachment text → wraps the member body and optional attachments in marker-specific tags → returns one combined inbound transcript string.

**Call relations**: It is the companion to mint_marker and member_message_text: surfaces create fenced inbound text here, and later projections can recover the member’s words.


##### `inbox_name`  (lines 253–282)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an untrusted attachment filename into a safe, unique workspace filename. It prevents path tricks, unsafe characters, overlong names, and same-batch overwrites.

**Data flow**: Takes a raw filename and a set of names already used → keeps only the safe leaf name, cleans characters, preserves useful extensions, adds a number if needed, and updates the used set → returns the safe name.

**Call relations**: Surface upload paths call this before writing attached files into a workspace, relying on contained_leaf to strip dangerous path parts.

*Call graph*: 1 external calls (contained_leaf).


##### `member_message_text`  (lines 285–299)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts the human member’s own words from the larger inbound text stored in a turn. This lets views show “what the member said” instead of the full prompt envelope.

**Data flow**: Takes stored inbound text → removes core context wrappers and searches for the unique member-message fence → returns the fenced message body, or the original text if no fence is present.

**Call relations**: conversation_name calls it when naming a new conversation from its opening message.

*Call graph*: called by 1 (conversation_name).


##### `MemberAdmitter.admit`  (lines 353–363)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Defines the interface for admitting a member message into the durable turn queue. Implementations use it when a trusted surface says a specific member spoke.

**Data flow**: Takes conversation id, message text, optional idempotency key, context, speaker, intent, and comment → records or folds the message according to admission rules → returns an Admitted result describing the turn and whether a run opened.

**Call relations**: SurfaceContext.admit delegates to this protocol so surface code never writes turn queue internals directly.


##### `TurnTailer.tail`  (lines 377–379)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how a live surface subscribes to frames from one running turn. A frame is a live update the agent publishes while it works.

**Data flow**: Takes a turn id and optional cursor → opens an async stream of cursor-plus-frame pairs → closes the subscription when the surrounding context exits.

**Call relations**: SurfaceContext.tail exposes this protocol to web, CLI-like, debugger, and sample surfaces that stream live responses.


##### `TurnTailer.latest_activity`  (lines 381–381)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Defines how to peek at the most recent live activity for a turn without subscribing. This supports status displays.

**Data flow**: Takes a turn id → reads the newest retained activity from the hub implementation → returns that activity or nothing.

**Call relations**: SurfaceContext.latest_activity delegates to it for web agent status polling.


##### `TurnStopper.stop`  (lines 391–391)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines how a member can stop a running turn in a specific workspace conversation. It also reports whether a pending follow-up message started a new turn.

**Data flow**: Takes workspace, conversation, and turn ids → cancels or observes the turn according to core rules → returns a Stopped record.

**Call relations**: SurfaceContext.stop_turn delegates to it when web or terminal-style surfaces receive a stop request.


##### `TurnStepSource.read`  (lines 397–397)

```
async def read(self, workflow_id: str) -> tuple['TurnStep', ...]
```

**Purpose**: Defines how a surface reads recorded workflow steps for a turn. These steps are useful for debugging what the durable runner did.

**Data flow**: Takes a workflow id → loads its recorded steps from the backing step store → returns TurnStep records.

**Call relations**: SurfaceContext.turn_steps uses this after confirming the turn belongs to the workspace.


##### `SurfaceModel.model`  (lines 427–427)

```
def model(self) -> str
```

**Purpose**: Names the model used by a surface for small request-time model calls. It lets those calls be billed and labeled consistently.

**Data flow**: Reads the configured model identity → returns it as text.

**Call relations**: Surface routes can inspect this through SurfaceContext.model when they need optional model-powered behavior.


##### `SurfaceModel.turn`  (lines 429–429)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Defines a one-shot model call available to surface routes. It is for immediate helper work where the route can still answer if the call fails.

**Data flow**: Takes a model request → sends it to the configured model access layer → returns the model’s message.

**Call relations**: SurfaceContext.model exposes this protocol to surfaces that have been wired with a surface model.


##### `shared_artifact_link`  (lines 514–525)

```
def shared_artifact_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed download link for a shared file. If public file delivery is not configured, it cleanly returns no link.

**Data flow**: Takes signing secret, public base URL, workspace id, and artifact metadata → computes an expiry and signed artifact path → returns the full URL or None.

**Call relations**: SurfaceContext.artifact_link calls it when web, Slack, iMessage, or UFO surfaces need a member-visible file link.

*Call graph*: called by 1 (artifact_link); 3 external calls (now, artifact_url_expiry, mint_artifact_url).


##### `shared_artifact_preview_link`  (lines 528–550)

```
def shared_artifact_preview_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview-image link for an artifact when the stored type and preview data are safe to serve inline. It avoids pretending a non-image is an image.

**Data flow**: Takes signing secret, public URL, workspace id, and artifact → chooses preview bytes or original bytes, checks raster image type and size → returns a preview URL or None.

**Call relations**: SurfaceContext.artifact_preview_link calls it for web file cards and conversation slot projections.

*Call graph*: called by 1 (artifact_preview_link); 2 external calls (mint_image_preview_url, raster_image_media_type).


##### `_scheduled_runs_query`  (lines 553–589)

```
def _scheduled_runs_query(workspace_id: UUID, member_id: UUID, agent_id: UUID | None) -> sa.Select[Any]
```

**Purpose**: Builds the database query for scheduled turns a member is allowed to see. It keeps feed reads inside the member’s readable conversation audiences.

**Data flow**: Takes workspace id, member id, and optional agent id → constructs a SQL query for completed scheduled turns with reportable output or failure → returns the query object.

**Call relations**: scheduled_runs uses this query builder before adding feed-specific filters like turn id or subjects.

*Call graph*: called by 1 (scheduled_runs); 3 external calls (or_, select, readable_audiences).


##### `scheduled_runs`  (lines 592–668)

```
async def scheduled_runs(workspace_id: UUID, member_id: UUID, *, limit: int, agent_id: UUID | None=None, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Lists recent completed turns that fired automatically, such as scheduled tasks, for conversations the reader may read. It includes their final text and shared files so a feed can render them directly.

**Data flow**: Takes workspace, member, limit, and optional narrowing filters → queries matching turns and artifacts, resolves conversation sources → returns ScheduledRun records newest first.

**Call relations**: It calls _scheduled_runs_query for the access-safe base query and ConversationDirectory.sources to attach links back to the originating conversation.

*Call graph*: calls 1 internal fn (_scheduled_runs_query); 6 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx).


##### `conversation_name`  (lines 717–722)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Chooses a conversation title from the member’s opening words. It ignores injected ambient context so the title reflects what the person asked.

**Data flow**: Takes inbound turn text → extracts member-only text and trims it to the title limit → returns the title string.

**Call relations**: It calls member_message_text and is used by admission paths that need a consistent default title.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 725–742)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation after a surface has a better title. Blank titles are ignored so an existing name is not erased accidentally.

**Data flow**: Takes workspace id, conversation id, and title → trims and bounds the title → updates the matching conversation row if the title is nonblank.

**Call relations**: SurfaceContext.retitle_conversation wraps this for surface callers.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `summarize_conversation_title`  (lines 745–765)

```
async def summarize_conversation_title(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores a model-generated title summary and marks that the title summary job has run. This prevents the same conversation from being summarized repeatedly.

**Data flow**: Takes workspace id, conversation id, and proposed title → trims it and updates title_summarized, keeping the old title if the summary is blank → writes the row.

**Call relations**: It is used by title-summary orchestration outside this file to settle a conversation’s generated title.

*Call graph*: 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 840–841)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures an agent detail timestamp has timezone information. This avoids ambiguous dates in API responses.

**Data flow**: Takes a datetime → leaves it alone if timezone-aware, otherwise marks it as UTC → returns the normalized datetime.

**Call relations**: Pydantic calls it automatically when AgentDetail is created by SurfaceContext.agent_detail.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 889–890)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a connection timestamp to timezone-aware UTC. It keeps connection views consistent for callers.

**Data flow**: Takes connected_at → adds UTC if missing → returns the normalized timestamp.

**Call relations**: Pydantic runs it when SurfaceContext.list_agent_connections creates ConnectionView records.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 916–917)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes the timestamp for a connection pool row. This protects clients from mixed naive and timezone-aware datetimes.

**Data flow**: Takes connected_at → adds UTC if needed → returns the adjusted datetime.

**Call relations**: Pydantic runs it during SurfaceContext.list_connections output construction.

*Call graph*: 1 external calls (replace).


##### `_binding_fields`  (lines 976–1004)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts the user-facing identity fields for a connector-backed source row. These fields let the portal submit later source actions without losing part of the original binding identity.

**Data flow**: Takes a backend name and stored config dictionary → validates it as connector source config, derives binding name and identity fields, or returns None fields if invalid → returns a typed field mapping.

**Call relations**: SurfaceContext.list_sources expands its result into SourceView rows.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 1036–1037)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a source’s next-sync time is timezone-aware. This makes portal scheduling displays reliable.

**Data flow**: Takes next_sync_at → adds UTC if the database value lacks timezone data → returns it.

**Call relations**: Pydantic calls it as SourceView records are built by SurfaceContext.list_sources.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 1055–1058)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation creation and last-activity timestamps. It also preserves None for conversations with no turns yet.

**Data flow**: Takes a datetime or None → returns None unchanged, leaves aware values alone, or marks naive values as UTC.

**Call relations**: Pydantic applies it when conversation listing functions construct ConversationSummary objects.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 1071–1132)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged reading another member’s private transcript. The record is both an audit trail and the temporary gate that allows the read.

**Data flow**: Takes workspace, conversation, agent, and reader member ids → verifies the conversation is a private conversation owned by another member, writes an access row, logs the disclosure → returns reader and subject emails, or None if not applicable.

**Call relations**: Portal prepared-intent flows call this before content routes rely on SurfaceContext.readable_conversation to allow the admin read.

*Call graph*: 9 external calls (__init__, now, insert, select, workspace_tx, log, audience_member, parse_audience, uuid4).


##### `ConversationDirectory.list`  (lines 1190–1314)

```
async def list(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'others'] | None=None, sea
```

**Purpose**: Lists an agent’s conversations for a member or admin, including only the content the reader may see. It supports filters for surface, participation, exact id, member-admitted conversations, and search.

**Data flow**: Takes agent/member ids and listing options → builds an access-aware conversation query, fetches readable sources and speakers for the page → returns ListedConversation records.

**Call relations**: SurfaceContext.list_agent_conversations delegates here; it calls helper predicates plus sources and speakers to fill each row.

*Call graph*: calls 6 internal fn (_matches, _member_admitted, _others, _participated, sources, speakers); 8 external calls (__init__, __init__, select, workspace_tx, audience_member, conversation_audience, parse_audience, readable_audiences).


##### `ConversationDirectory.sources`  (lines 1316–1353)

```
async def sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the source link or origin text from each listed conversation’s opening turn. This tells a portal where the conversation started.

**Data flow**: Takes conversation ids → finds the first turn in each conversation and parses its TurnContext → returns a map from conversation id to source or None.

**Call relations**: ConversationDirectory.list and scheduled_runs use it to attach origin links to conversation and run listings.

*Call graph*: called by 1 (list); 4 external calls (model_validate, and_, select, workspace_tx).


##### `ConversationDirectory.speakers`  (lines 1355–1411)

```
async def speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Finds the first few members who spoke in each listed conversation. It gives listings useful human context without loading every turn.

**Data flow**: Takes conversation ids → queries first appearances of speakers, reads sender labels from turn context → returns a map to ConversationSpeaker tuples.

**Call relations**: ConversationDirectory.list calls it only for conversations whose content the reader may read.

*Call graph*: called by 1 (list); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `ConversationDirectory._member_admitted`  (lines 1413–1426)

```
def _member_admitted(self) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations that have ever had a member-admitted turn. This separates real member conversations from machine-only lanes.

**Data flow**: Takes no runtime input besides the directory workspace → creates an EXISTS SQL condition tied to the current conversation row → returns that condition.

**Call relations**: ConversationDirectory.list uses it when the member_admitted filter is requested.

*Call graph*: called by 1 (list); 2 external calls (literal, select).


##### `ConversationDirectory._spoken`  (lines 1428–1449)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for whether a conversation contains member speech, optionally by a specific member. It is optimized as an indexed existence check.

**Data flow**: Takes an optional member id → creates an EXISTS condition for turns with that speaker or any speaker → returns the SQL condition.

**Call relations**: _participated and _others compose this helper to define conversation participation filters.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `ConversationDirectory._participated`  (lines 1451–1459)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for conversations a member belongs to or has spoken in. This makes sure replies in shared or other-origin conversations still count as participation.

**Data flow**: Takes member id → checks direct conversation ownership or a spoken turn by that member → returns a SQL OR condition.

**Call relations**: ConversationDirectory.list uses it for the participation='mine' filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 1 external calls (or_).


##### `ConversationDirectory._others`  (lines 1461–1473)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for conversations where some member spoke but this member did not participate. This powers “others” views without leaking content by itself.

**Data flow**: Takes member id → combines not-owned, not-spoken-by-member, and spoken-by-someone checks → returns a SQL condition.

**Call relations**: ConversationDirectory.list uses it for the participation='others' filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 2 external calls (and_, not_).


##### `ConversationDirectory._matches`  (lines 1475–1504)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a search condition for conversation listings while respecting content privacy. Metadata can be searched broadly, but titles and speakers are searched only when readable.

**Data flow**: Takes a search string and member id → creates SQL conditions over surface label, owner email, and readable-only title/speaker matches → returns the combined condition.

**Call relations**: ConversationDirectory.list applies it before limiting the page so search finds matching conversations rather than filtering a pre-cut page.

*Call graph*: called by 1 (list); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `LedgerEntry._aware_utc`  (lines 1518–1519)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes accounting timestamps to timezone-aware UTC. This keeps turn detail output consistent.

**Data flow**: Takes created_at → adds UTC if missing → returns the timestamp.

**Call relations**: Pydantic runs it when SurfaceContext.turn_detail builds LedgerEntry records.

*Call graph*: 1 external calls (replace).


##### `TurnStep._aware_utc`  (lines 1535–1538)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes workflow step start and completion timestamps. Missing timestamps stay missing.

**Data flow**: Takes a datetime or None → returns None, the original aware datetime, or the same time marked UTC.

**Call relations**: Pydantic applies it when TurnStepSource implementations return TurnStep data through SurfaceContext.turn_steps.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 1594–1599)

```
def _fulfilled_marker_key(sealed: str, slot: str) -> str
```

**Purpose**: Creates the blob-store marker key that says a credential prompt slot has been fulfilled. It is keyed by the sealed request and slot so separate prompts do not interfere.

**Data flow**: Takes a sealed request string and slot name → hashes the seal and combines it with the slot → returns a blob path.

**Call relations**: SurfaceContext.credential_prompt_pending checks for this marker, and fulfill_credential_request writes it after storing a value.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_main_agent`  (lines 1602–1615)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. It is the default agent when a surface has no explicit binding.

**Data flow**: Takes workspace id → queries the agent table for the main row → returns its id or raises if the workspace is malformed.

**Call relations**: _bind_surface_installation and SurfaceContext._surface_agent call it as the fallback agent resolver.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 1618–1657)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str, *, routes_ingress: bool) -> None
```

**Purpose**: Creates or updates the binding between a workspace and an external surface installation, such as a Slack team. It enforces that routing installations belong to only one workspace.

**Data flow**: Takes workspace id, surface name, installation id, and routing flag → chooses the workspace’s main agent for new bindings, upserts the installation row → writes the binding or raises SurfaceInstallationConflict.

**Call relations**: SurfaceContext.bind_installation and SurfaceInstallationAccess.bind both use this single writer so OAuth callbacks and tool actions behave the same.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.fleet_blob`  (lines 1728–1731)

```
def fleet_blob(self) -> FleetBlobStore
```

**Purpose**: Provides access to deploy-wide shared blob storage, not just workspace-scoped blobs. This is used for static assets shared across workspaces.

**Data flow**: Reads the current workspace blob backend → wraps it in FleetBlobStore → returns that fleet-level blob store.

**Call relations**: Surface routes can use this property when they need shared deploy assets rather than tenant data.

*Call graph*: 1 external calls (__init__).


##### `SurfaceContext.conversation_slots`  (lines 1734–1736)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Returns the extension-provided conversation slots available to this surface. Slots are pre-bound at startup.

**Data flow**: Reads the context’s stored slot tuple → returns it unchanged.

**Call relations**: Web surface code uses this to know which extra conversation panels or summaries can be shown.


##### `SurfaceContext.read_conversation_slot`  (lines 1738–1743)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Runs a conversation slot’s read operation inside the correct agent scope. This lets the slot provider see the agent namespace it expects.

**Data flow**: Takes a bound slot and slot context → temporarily binds the agent id from the context, calls the provider read → returns the slot payload.

**Call relations**: The web surface’s conversation_slot route calls it when rendering a slot for a conversation.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1745–1750)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Runs a conversation slot’s summary operation inside the correct agent scope. It lets slot summaries share the same namespace rules as slot reads.

**Data flow**: Takes a bound slot and slot context → binds the agent id, calls the provider summarize method → returns an integer summary marker or None.

**Call relations**: The web surface calls it while preparing conversation slot summaries.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.deploy_extensions`  (lines 1753–1756)

```
def deploy_extensions(self) -> tuple[DeployExtensionView, ...]
```

**Purpose**: Returns the extensions installed in this deployment for administration screens. It exposes deployment shape, not tenant secrets.

**Data flow**: Reads the precomputed extension view tuple → returns it.

**Call relations**: Portal administration views read this property from SurfaceContext.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1759–1762)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Reports whether any deployed extension requires sandbox public internet capability. Portal settings use it as the upper bound for per-agent internet access.

**Data flow**: Reads the stored boolean → returns it.

**Call relations**: Agent administration views combine this deploy-wide fact with each agent’s own setting.


##### `SurfaceContext.deploy_skills`  (lines 1765–1770)

```
def deploy_skills(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Returns the deploy-provided skill index available to agents. This is the shared skill floor before member-authored skills are added.

**Data flow**: Reads the skill registry → asks it for its index → returns pairs of skill names and descriptions.

**Call relations**: Surface routes use it to show what skills the deployment can load.


##### `SurfaceContext.system_skill_bundle`  (lines 1773–1775)

```
def system_skill_bundle(self) -> SystemSkillBundle
```

**Purpose**: Returns the immutable bundle of system skills loaded for the deployment. Runtime code can cache or display it without rebuilding.

**Data flow**: Reads the stored system skill bundle → returns it.

**Call relations**: It is exposed through SurfaceContext as part of the surface’s read-only deployment capabilities.


##### `SurfaceContext.models`  (lines 1778–1782)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Lists the model ids this deployment supports. Portal settings use it to offer valid model choices.

**Data flow**: Reads the stored tuple of model names → returns it.

**Call relations**: Surfaces read it when rendering agent model configuration.


##### `SurfaceContext.sandbox_sizes`  (lines 1785–1788)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Lists the sandbox sizes available from the configured carrier. If empty, the portal can hide size selection.

**Data flow**: Reads the stored sandbox size tuple → returns it.

**Call relations**: Agent settings views use it to decide whether sandbox size is configurable.


##### `SurfaceContext.credential`  (lines 1790–1793)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential value for trusted surface code. This is intentionally privileged and unavailable to ordinary scoped extensions.

**Data flow**: Takes a slot name → checks that a credential store exists, then reads the encrypted workspace value → returns the secret string or raises if unavailable.

**Call relations**: Slack surface helpers and delivery functions call it for bot tokens, signing secrets, and related provider credentials.

*Call graph*: called by 8 (_bot_token, _channel_origin, _ctx_signing_secret, _post_ephemeral, _to_inbound, attach, post, speak).


##### `SurfaceContext.credential_prompt_pending`  (lines 1795–1809)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs one slot filled. It prevents fulfilled, expired, or foreign prompts from being shown again.

**Data flow**: Takes sealed request and slot → opens and validates the seal, checks workspace and slot membership, then checks the blob marker → returns true only if still pending.

**Call relations**: The web surface uses it while deciding which credential prompts to render.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 1 (_pending_prompts); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 1811–1821)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens and verifies a sealed credential handoff after a provider callback. It recovers the workspace, member, and requested slots from the signed state.

**Data flow**: Takes a sealed string → requires a credential store and verifies the seal for the credential purpose → returns CredentialRequestState or raises on invalid state.

**Call relations**: Slack OAuth callback code calls it when completing a credential authorization.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 1823–1848)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Stores one credential value only if the sealed request proves the workspace, member, and slot match. It also marks that slot prompt as fulfilled.

**Data flow**: Takes seal, slot, value, and member id → validates the seal and member, writes the encrypted credential, writes a fulfilled marker blob → returns nothing or raises on invalid requests.

**Call relations**: Slack, web, and UFO surfaces call it after a member supplies a requested credential.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 1850–1858)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation id to the current workspace. It is used by surface-owned install flows.

**Data flow**: Takes installation id → passes workspace, current surface, and routes_ingress=true to the shared binding helper → writes or replaces the binding.

**Call relations**: Slack OAuth callback calls it after a successful install.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.address_claim`  (lines 1860–1885)

```
async def address_claim(self, address: str) -> AddressClaim | None
```

**Purpose**: Looks up this workspace’s claim on an addressed-surface address, such as a phone sender address. It tells whether the claim is pending proof or already proved.

**Data flow**: Takes an address → queries surface_address for this workspace and surface → returns AddressClaim with normalized expiry or None.

**Call relations**: The iMessage surface uses it while deciding how to admit or prove an incoming message.

*Call graph*: called by 1 (_admit_message); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_surface_claim`  (lines 1887–1921)

```
async def member_surface_claim(self, surface: str, member_id: UUID) -> AddressClaim | None
```

**Purpose**: Finds the strongest address claim a member has for a given surface. This lets the portal show the member’s own reservation without knowing the address.

**Data flow**: Takes surface and member id → queries claims for that member, preferring proved claims and recent reservations → returns AddressClaim or None.

**Call relations**: The web surface uses it for workspace iMessage claim status.

*Call graph*: called by 1 (workspace_imessage_claim); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.confirm_address`  (lines 1923–1936)

```
async def confirm_address(self, address: str, proved_by: str) -> None
```

**Purpose**: Marks an addressed-surface reservation as proved by a specific inbound message. After this, routing by that address is stable.

**Data flow**: Takes address and proof id → updates the matching surface_address row to clear expiry and store proved_by → returns nothing.

**Call relations**: The iMessage proof flow calls it once a sender proves control of the address.

*Call graph*: called by 1 (_prove); 2 external calls (update, workspace_tx).


##### `SurfaceContext.release_address`  (lines 1938–1947)

```
async def release_address(self, address: str) -> None
```

**Purpose**: Deletes this workspace’s claim on an addressed-surface address. This frees the address for a future claim.

**Data flow**: Takes address → deletes the matching row for this workspace and surface → returns nothing.

**Call relations**: The iMessage proof flow calls it when a claim should be released.

*Call graph*: called by 1 (_prove); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.public_base_url`  (lines 1950–1953)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deployment’s public base URL, if configured. Surfaces use it to build callback and portal links.

**Data flow**: Reads the stored public base URL → returns it or None.

**Call relations**: Surface route code reads this property when it must tell a provider or member where to go.


##### `SurfaceContext.cookie_secure`  (lines 1956–1960)

```
def cookie_secure(self) -> bool
```

**Purpose**: Reports whether cookies should be marked Secure for this deployment. It bases the decision on the configured public URL scheme.

**Data flow**: Takes no input → parses the public base URL scheme and applies the cookie security helper → returns a boolean.

**Call relations**: Browser-like surfaces use it when setting session cookies.

*Call graph*: 2 external calls (cookie_secure, urlsplit).


##### `SurfaceContext.home_url`  (lines 1962–1972)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link to the deployment’s browser home surface. Other surfaces use it when they need to send a member to the portal.

**Data flow**: Takes an optional URL fragment → combines public base URL, home surface name, and fragment if both are configured → returns a URL or None.

**Call relations**: iMessage, Slack, and sites surfaces call it for portal fallback links.

*Call graph*: called by 3 (_terminal_text, _into_the_portal, _reply_with_oversize_links).


##### `SurfaceContext.shared_artifacts`  (lines 1974–2012)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Lists files shared by a turn in delivery order. Live surfaces use this directly, while durable surfaces receive similar data through writeback.

**Data flow**: Takes a turn id → queries shared_artifact rows for this workspace and turn → returns SharedArtifact records.

**Call relations**: Web and UFO surfaces call it to show or list files attached to a turn.

*Call graph*: called by 2 (shared_files, _events); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 2014–2022)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a shared artifact in this workspace. It hides signing details from surface extensions.

**Data flow**: Takes a SharedArtifact → passes the context’s signing secret, public URL, and workspace id to shared_artifact_link → returns a URL or None.

**Call relations**: Slack, iMessage, web, and UFO surfaces call it when rendering shared files.

*Call graph*: calls 1 internal fn (shared_artifact_link); called by 5 (_terminal_text, _oversize_link_line, shared_files, _file_payload, _project_slot_context).


##### `SurfaceContext.artifact_preview_link`  (lines 2024–2034)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview-image link for a shared artifact when safe and configured. It is mainly for browser file cards.

**Data flow**: Takes a SharedArtifact → delegates to shared_artifact_preview_link with workspace signing settings → returns a preview URL or None.

**Call relations**: The web surface calls it for file payloads and conversation slot context.

*Call graph*: calls 1 internal fn (shared_artifact_preview_link); called by 2 (_file_payload, _project_slot_context).


##### `SurfaceContext.ingress_url`  (lines 2036–2082)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest: str | None=None) -> str | None
```

**Purpose**: Creates a signed URL that opens a sandbox-hosted web app or port for a conversation. The URL grants temporary viewing access without exposing deploy secrets.

**Data flow**: Takes conversation id, port, entry path, and optional framing or shipped-app details → calls mint_ingress_view_url with workspace and ingress settings → returns a URL or None.

**Call relations**: The sites extension calls it when serving app frames or shipped app bundles.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (mint_ingress_view_url).


##### `SurfaceContext._identity_member`  (lines 2084–2097)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up which workspace member is linked to an external surface identity. It is the shared private helper for own-surface and peer-surface identity resolution.

**Data flow**: Takes surface name and external id → queries surface_identity within the workspace → returns member id or None.

**Call relations**: linked_member and adopt_identity call it before linking or using identities.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 2099–2100)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the member linked to this surface’s external user id. Surfaces use it to decide who an incoming request speaks as.

**Data flow**: Takes external id → delegates to _identity_member using this context’s surface → returns member id or None.

**Call relations**: Sample, Slack, web, sites, and UFO surfaces call it during authentication or message admission.

*Call graph*: calls 1 internal fn (_identity_member); called by 8 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, channel, op_body, _authenticate).


##### `SurfaceContext.is_operator_workspace`  (lines 2102–2109)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace belongs to the fleet operator. This gates operator-only rendering, not tenant capabilities.

**Data flow**: Takes no input → reads the workspace domain and compares it to the operator email domain → returns a boolean.

**Call relations**: Surface code can call it before showing internal debugging or accounting extras.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 2111–2134)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external identity to the same member already known by a peer surface. This lets one person keep the same member identity across surfaces.

**Data flow**: Takes peer surface and external id → finds the peer-linked member, inserts an identity row for this surface, tolerates insertion races → returns member id or None.

**Call relations**: The sample live-admit flow calls it to reuse an identity established elsewhere.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 2136–2158)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing workspace member found by email. If no member has that email, it leaves the identity unlinked.

**Data flow**: Takes external id and email → searches members case-insensitively, choosing the oldest match → calls link_member_id → returns member id or None.

**Call relations**: join_member builds on it, and several surfaces call it during authentication or first contact.

*Call graph*: calls 1 internal fn (link_member_id); called by 5 (join_member, _surface_ingest, _viewer, channel, _authenticate); 2 external calls (select, workspace_tx).


##### `SurfaceContext.link_member_id`  (lines 2160–2189)

```
async def link_member_id(self, external_id: str, member_id: UUID) -> UUID | None
```

**Purpose**: Links this surface’s external id to a specific member after the surface has independently proved that member. It avoids creating links to non-members.

**Data flow**: Takes external id and member id → verifies the member exists in the workspace, inserts a surface_identity row, logs races → returns member id or None.

**Call relations**: link_member calls it after resolving email to member id.

*Call graph*: called by 1 (link_member); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 2191–2208)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links an external id by email, creating a new workspace member only when the email domain matches the workspace’s own domain. This supports verified channel-based self-join.

**Data flow**: Takes external id and verified email → tries existing member link, checks workspace domain, creates a member if allowed, then links again → returns member id or None.

**Call relations**: Slack member resolution calls it when Slack has verified the user email.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 2210–2220)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the query for finding this surface’s conversation by queue key. A queue key is the surface’s stable external conversation/thread identifier.

**Data flow**: Takes queue key → constructs a SQL select for matching conversation fields in this workspace and surface → returns the query.

**Call relations**: conversation_for, find_conversation, and terminal_op_body use it to avoid duplicating lookup logic.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 2222–2228)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for this surface queue key without creating one. This is useful when a surface must know whether the agent is already participating.

**Data flow**: Takes queue key → runs _conversation_lookup → returns conversation id or None.

**Call relations**: Slack participation and interaction handling call it before deciding whether to admit traffic.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 2 (_participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 2230–2243)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation is permanently bound to. This lets surfaces gate conversation links against the right agent.

**Data flow**: Takes conversation id → queries the workspace conversation row → returns agent id or None.

**Call relations**: The web surface uses it when resolving chat routes.

*Call graph*: called by 1 (_resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 2245–2248)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation in this workspace through the shared title updater. It is the surface-facing method for title changes.

**Data flow**: Takes conversation id and title → passes workspace id and title to retitle_conversation → returns nothing.

**Call relations**: Slack and web panel flows call it after opening or updating conversations.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 4 (_admit_inbound, submit_action, submit_intent, _open_conversation).


##### `SurfaceContext.conversation_for`  (lines 2250–2344)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation for a surface queue key, narrowing audience when possible and binding new conversations to the correct agent. This is the main entry point for turning external threads into core conversations.

**Data flow**: Takes queue key, audience, optional agent/conversation id, and label → reads existing row, narrows audience or updates label if needed, or inserts a new row with a selected agent → returns the conversation id.

**Call relations**: iMessage, Slack, sample, UFO, web chat, and panel submission paths call it before admitting messages.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 10 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, interactive, channel, submit_action, submit_intent, _open_conversation, object_write); 9 external calls (insert, select, update, workspace_tx, log, audience_member, narrow_audience, parse_audience, uuid4).


##### `SurfaceContext._surface_agent`  (lines 2346–2358)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Chooses the default agent for new conversations on this surface. It uses the surface installation binding if present, otherwise the workspace main agent.

**Data flow**: Takes no input → queries surface_installation for this workspace and surface → returns the bound agent id or _main_agent result.

**Call relations**: conversation_for calls it when a caller did not explicitly choose an agent.

*Call graph*: calls 1 internal fn (_main_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 2360–2391)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Asks the ambient reply classifier whether an unaddressed message should become an agent turn. It fails open so uncertain classifier failures do not silently drop a possible request.

**Data flow**: Takes the current ambient message and recent history → runs the classifier with a timeout, logs decision or warning → returns false only for a clear no-reply decision.

**Call relations**: Slack and iMessage durable ingest paths call it before admitting ambient channel traffic.

*Call graph*: called by 2 (_admit_message, _ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext.admit`  (lines 2393–2426)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, comment:
```

**Purpose**: Admits an inbound message or prepared intent into the core turn system as a member. It is the privileged write path surfaces use after resolving identity and conversation.

**Data flow**: Takes conversation id, body, optional key/context, speaker id, intent, and comment → delegates to the injected MemberAdmitter → returns an Admitted result.

**Call relations**: All major surfaces call it when user messages, panel actions, or terminal sends should become turns.

*Call graph*: called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, interactive, _send, channel, submit_action, submit_intent, chat (+1 more)).


##### `SurfaceContext.connect_url`  (lines 2428–2434)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates a provider connection authorization URL for a turn and member. It translates missing connect infrastructure into a request-level invalid error.

**Data flow**: Takes turn id and member id → loads installed connect flow, authorizes a ConnectHandoff for workspace/turn/member → returns the URL.

**Call relations**: iMessage, Slack interactive controls, and web connect handoff routes call it.

*Call graph*: called by 3 (_terminal_text, interactive, connect_handoff); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.held_accounts`  (lines 2436–2456)

```
async def held_accounts(self, owner_member_id: UUID) -> dict[str, str]
```

**Purpose**: Lists the latest connected account labels held by one member, grouped by provider. This helps the portal display settled connect controls.

**Data flow**: Takes owner member id → queries that member’s connection rows ordered by update time → returns provider-to-label/account mapping.

**Call relations**: The web surface uses it when building connect controls.

*Call graph*: called by 1 (_connect_controls); 2 external calls (select, workspace_tx).


##### `SurfaceContext.connect_available`  (lines 2458–2465)

```
def connect_available(self) -> bool
```

**Purpose**: Reports whether this deployment has the connect flow configured at all. Surfaces use it to avoid showing dead connect buttons.

**Data flow**: Takes no input → tries to load the installed connect flow → returns false if unavailable, true otherwise.

**Call relations**: Web connect controls and provider labeling paths call it before rendering connect UI.

*Call graph*: called by 3 (_connect_controls, _events, _provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connect_label`  (lines 2467–2469)

```
def connect_label(self, provider: str) -> str
```

**Purpose**: Returns the member-facing label for a connect provider. This keeps provider names consistent with the connect flow.

**Data flow**: Takes provider id → asks the installed connect flow for its label → returns the label string.

**Call relations**: The web surface uses it when naming providers.

*Call graph*: called by 1 (_provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connector_catalog`  (lines 2471–2473)

```
async def connector_catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads a page of connectable provider catalog entries. It supports searching and cursor-based browsing.

**Data flow**: Takes query text, limit, and optional cursor → delegates to the connector registry catalog → returns a CatalogPage.

**Call relations**: The web connector_catalog route calls it.

*Call graph*: called by 1 (connector_catalog).


##### `SurfaceContext.admitted_body`  (lines 2475–2500)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Finds the body that was admitted under an idempotency key. This lets a surface confirm which duplicate click or retried delivery actually won.

**Data flow**: Takes idempotency key → searches turn founding rows first, then inbound_message rows → returns the stored body or None.

**Call relations**: Slack and web paths call it when reconciling interactive actions and unseen tails.

*Call graph*: called by 3 (_unseen_tail, interactive, chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 2502–2516)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member who owns the conversation containing a turn. Live surfaces use it to prevent one member from tailing another’s turn.

**Data flow**: Takes turn id → joins turn to conversation in this workspace → returns conversation member id or None.

**Call relations**: Sample and web live-turn gating call it before streaming frames.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 2518–2524)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops a running turn after the surface has authorized the acting member. It returns whether the stop did anything and whether a follow-up turn started.

**Data flow**: Takes conversation id and turn id → delegates to the injected TurnStopper with workspace id → returns Stopped.

**Call relations**: UFO and web chat surfaces call it for stop requests.

*Call graph*: called by 2 (channel, chat).


##### `SurfaceContext.retract_arrival`  (lines 2526–2544)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Deletes a pending member message that has not yet been consumed by a turn. It only retracts the requesting member’s own unconsumed message.

**Data flow**: Takes conversation id, arrival id, and member id → deletes a matching unconsumed inbound_message row → returns true if one row was deleted.

**Call relations**: The UFO surface uses it for unsend behavior.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 2546–2563)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has ended according to the durable database row. This avoids posting progress updates after the final answer has already been delivered.

**Data flow**: Takes turn id → reads its status in this workspace → returns true for missing or terminal turns, false otherwise.

**Call relations**: The UFO channel uses it around side-channel progress reporting.

*Call graph*: called by 1 (channel); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 2565–2582)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the most recent turn in a conversation. Reloading or reconnecting surfaces use it to resume the right turn or re-render pending handoffs.

**Data flow**: Takes conversation id → queries turns ordered by descending sequence → returns the newest turn id or None.

**Call relations**: Slack, UFO, and web surfaces call it when resuming conversations or resolving chats.

*Call graph*: called by 4 (_participating_conversation, channel, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.absorbing_turn`  (lines 2584–2628)

```
async def absorbing_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Predicts whether a new message would fold into an existing live turn rather than open a new one. It also checks spend and balance gates so the prediction matches admission rules.

**Data flow**: Takes conversation id → finds the oldest non-terminal turn, rejects parked turns, evaluates spend cap and balance gates → returns the absorbing turn id or None.

**Call relations**: Slack calls it before deciding whether an ambient reply should be classified or treated as part of a live turn.

*Call graph*: called by 1 (_folds_into_live_turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.tail`  (lines 2630–2636)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for one turn through the injected tailer. It gives live surfaces updates without direct hub access.

**Data flow**: Takes turn id and optional cursor → delegates to TurnTailer.tail → returns an async context manager yielding live frames.

**Call relations**: Debugger, sample, UFO, web, and panel flows call it to stream turn progress and results.

*Call graph*: called by 7 (_events, _surface_frames, channel, submit_action, submit_intent, _events, object_write).


##### `SurfaceContext.latest_activity`  (lines 2638–2642)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Peeks at the latest retained activity for a running turn. It is a lightweight status read.

**Data flow**: Takes turn id → delegates to TurnTailer.latest_activity → returns Activity or None.

**Call relations**: The web agents_status route calls it after agent_turn_statuses identifies running turns.

*Call graph*: called by 1 (agents_status).


##### `SurfaceContext.spend_rollup`  (lines 2644–2647)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace-wide spend for a time window or all time. It supports billing and usage views.

**Data flow**: Takes optional window seconds → opens a workspace transaction and asks SpendRollup for the report → returns SpendReport.

**Call relations**: Sample and web workspace usage views call it.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 2649–2664)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded file into a conversation’s workspace before a turn runs. It enforces a maximum size while reading the stream.

**Data flow**: Takes conversation id, relative path, and byte chunks → accumulates bytes up to the limit, then writes them through the sandbox carrier → returns nothing or raises if too large.

**Call relations**: iMessage, Slack, sample, and web upload paths call it for inbound attachments.

*Call graph*: called by 4 (_downloaded_files, _surface_ingest, _download_files, _deliver_uploads).


##### `SurfaceContext.render_preview`  (lines 2666–2694)

```
async def render_preview(self, kind: str, data: bytes) -> bytes | None
```

**Purpose**: Asks an external preview service to turn uploaded document bytes into a PNG thumbnail. If previewing is unavailable or rejected, the surface can still show a plain file card.

**Data flow**: Takes file kind and bytes → posts them with preview options and bearer token to the preview service → returns PNG bytes or None.

**Call relations**: The web preview route calls it for compose-time file previews.

*Call graph*: called by 1 (preview); 2 external calls (AsyncClient, dumps).


##### `SurfaceContext.list_agents`  (lines 2696–2735)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists active agents in the workspace, main agent first. Surface routes can then apply their own audience rules before showing them.

**Data flow**: Takes no input → queries non-archived agent rows ordered by main flag and name → returns AgentSummary records.

**Call relations**: Sites and web audience, app, and subagent views call it.

*Call graph*: called by 5 (_shipped_frame, frame, web_audience, _created_apps, _subagent_nodes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_archived_agents`  (lines 2737–2768)

```
async def list_archived_agents(self) -> tuple[ArchivedAgent, ...]
```

**Purpose**: Lists archived agents for restore or administration screens. Results are ordered most recently archived first.

**Data flow**: Takes no input → queries archived agent rows and display names → returns ArchivedAgent records.

**Call relations**: The web agents_index route calls it.

*Call graph*: called by 1 (agents_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_extension_agent_ids`  (lines 2770–2785)

```
async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]
```

**Purpose**: Finds agents that have private extension conversations for a member. This helps decide which extension-provided agents a member can see.

**Data flow**: Takes member id → queries distinct agent ids from matching private extension conversations → returns a frozenset of ids.

**Call relations**: The web audience calculation calls it.

*Call graph*: called by 1 (web_audience); 3 external calls (select, workspace_tx, conversation_audience).


##### `SurfaceContext.agent_detail`  (lines 2787–2848)

```
async def agent_detail(self, agent_id: UUID, member_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads one agent’s detailed settings, prompt digest, bound surfaces, and missing setup for a member. It returns nothing if the agent is not in the workspace.

**Data flow**: Takes agent id and member id → queries agent and surface installation rows, computes prompt digest and pending setup → returns AgentDetail or None.

**Call relations**: Web panel settings and intent submission flows call it.

*Call graph*: called by 2 (agent_settings, submit_intent); 5 external calls (__init__, select, workspace_tx, pending_setup, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 2850–2862)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for an object kind registered in this deployment. It tells the portal which list fields and schema are available.

**Data flow**: Takes kind name → looks up the bound kind and optional schema → returns PortalKind or None.

**Call relations**: Web object gates, action views, and object writes call it before rendering or submitting object UI.

*Call graph*: called by 3 (_object_gate, action_views, object_write); 1 external calls (__init__).


##### `SurfaceContext.object_actions`  (lines 2864–2879)

```
def object_actions(self, kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Builds the action controls the portal should show for a target object. It pre-binds target identity but leaves final permission checks to dispatch and handlers.

**Data flow**: Takes kind, action binding, and optional name/generation → calls presented_action_views with registered actions → returns ActionView records.

**Call relations**: Web panels and administration pages call it when drawing buttons and forms.

*Call graph*: called by 7 (submit_action, action_views, admin_index, workspace_credentials, workspace_first_run, workspace_memory, workspace_team); 1 external calls (presented_action_views).


##### `SurfaceContext.frame_admits`  (lines 2881–2884)

```
def frame_admits(self, callable_id: str) -> bool
```

**Purpose**: Checks whether an embedded app frame is allowed to post a callable action. This is a declaration-based safety gate.

**Data flow**: Takes a callable id → checks membership in the precomputed admissible set → returns a boolean.

**Call relations**: Web panel submit paths call it before accepting framed action or intent submissions.

*Call graph*: called by 2 (submit_action, submit_intent).


##### `SurfaceContext.agent_skills`  (lines 2886–2925)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists deploy and member-authored skills available to an agent, while refusing member skills that shadow deploy skills. This gives the portal the same skill composition a turn would load.

**Data flow**: Takes agent id → binds agent scope, materializes member skills, filters shadowed names, combines top-level deploy skills and member skills → returns PortalSkill records.

**Call relations**: The web skills route calls it.

*Call graph*: called by 1 (skills); 3 external calls (__init__, agent, log).


##### `SurfaceContext.model`  (lines 2928–2932)

```
def model(self) -> 'SurfaceModel | None'
```

**Purpose**: Returns the optional surface model access object. Surfaces can gate model-powered route behavior on whether this is present.

**Data flow**: Reads the stored model object → returns it or None.

**Call relations**: Surface handlers use this property directly when wired with request-time model access.


##### `SurfaceContext.memory_available`  (lines 2935–2939)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory-search provider is installed. It lets the portal hide memory features on deployments without memory.

**Data flow**: Checks whether the stored memory provider is not None → returns a boolean.

**Call relations**: Web memory routes use it before calling search_memory or recent_memory.


##### `SurfaceContext.search_memory`  (lines 2941–2951)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches memory items visible to a prepared reader. It deliberately fails if no memory provider is installed so callers must gate correctly.

**Data flow**: Takes a source reader and search queries → verifies memory provider exists, delegates search → returns memory matches.

**Call relations**: The web workspace_memory route calls it.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 2953–2966)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items for readable subjects, optionally filtered by kind and paged by cursor. It is the browsing counterpart to memory search.

**Data flow**: Takes subjects, limit, optional kinds, and cursor → verifies provider exists, delegates list_recent → returns a listing page.

**Call relations**: Web memory and recalled-item views call it.

*Call graph*: called by 2 (_recalled, workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 2969–2974)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Lists memory item classes the provider can show. This powers portal filters.

**Data flow**: Checks a memory provider is installed → calls its listable_kinds method → returns kind names.

**Call relations**: Memory UI code reads it after memory_available is true.


##### `SurfaceContext.agent_spend`  (lines 2976–2981)

```
async def agent_spend(self, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Reads usage and caps for one agent over a selected time window. It supports billing views.

**Data flow**: Takes agent id and optional window seconds → opens a workspace transaction and asks SpendRollup for agent data → returns AgentSpendReport.

**Call relations**: Surface administration routes can call it for per-agent usage.

*Call graph*: 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.member_spend`  (lines 2983–2988)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads usage and caps for one member over a selected time window. It lets members or admins see member-level spend.

**Data flow**: Takes member id and optional window seconds → reads via SpendRollup inside a workspace transaction → returns MemberSpendReport.

**Call relations**: The web workspace_usage route calls it.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 2990–3042)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to one agent that the viewer may see. Admins see all; members see shared accounts and their own private accounts.

**Data flow**: Takes agent id, member id, and admin flag → queries connector grants joined to connections and owners, applies visibility, builds ConnectionView records → returns them.

**Call relations**: The web connections route calls it.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, account_object_name, workspace_tx).


##### `SurfaceContext.list_connections`  (lines 3044–3126)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists workspace connections and the live agents attached to each. It hides private owner details from viewers who should not see them.

**Data flow**: Takes member id and admin flag → queries connections, owners, grants, and non-archived agents, groups agents per account → returns ConnectionPoolView records.

**Call relations**: Web provider and connection pool views call it.

*Call graph*: called by 2 (_held_providers, connection_pool); 7 external calls (__init__, __init__, and_, or_, select, account_object_name, workspace_tx).


##### `SurfaceContext.github_coverage`  (lines 3128–3176)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports which GitHub integration paths are configured: API connection, git push credentials, and sources. It respects viewer visibility for connections and sources.

**Data flow**: Takes member id and admin flag → checks for visible GitHub connections and sources plus filled GitHub credential slots → returns GithubCoverageView.

**Call relations**: Web first-run, held-provider, and GitHub coverage routes call it.

*Call graph*: called by 3 (_held_providers, github_coverage, workspace_first_run); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.recent_object_changes`  (lines 3178–3217)

```
async def recent_object_changes(self, limit: int) -> tuple[ObjectChange, ...]
```

**Purpose**: Reads the newest object-change audit rows for a workspace. It is meant for admin audit views.

**Data flow**: Takes a limit → queries object_change rows newest first → returns ObjectChange records with UTC-aware timestamps.

**Call relations**: The web object_changes route calls it after admin gating.

*Call graph*: called by 1 (object_changes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversation_artifacts`  (lines 3219–3288)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists recent shared files for one conversation. Authorization is expected to happen before this read.

**Data flow**: Takes conversation id and limit → queries shared artifacts joined to turns and conversation metadata, resolves the conversation source → returns ListedArtifact records.

**Call relations**: Web transcript-aid and slot-context builders call it.

*Call graph*: called by 2 (_project_slot_context, _transcript_aids); 5 external calls (__init__, __init__, __init__, select, workspace_tx).


##### `SurfaceContext.agent_turn_statuses`  (lines 3290–3418)

```
async def agent_turn_statuses(self, agent_ids: Sequence[UUID], member_id: UUID) -> tuple[AgentTurnStatus, ...]
```

**Purpose**: Summarizes each selected agent’s readable turn activity for a member. It reports current live state, latest activity time, and whether the newest finished turn failed.

**Data flow**: Takes agent ids and member id → computes readable audiences, queries live and latest terminal/readable turns, preserves input order → returns AgentTurnStatus records.

**Call relations**: The web agents_status route calls it, then may use latest_activity for running turns.

*Call graph*: called by 1 (agents_status); 5 external calls (__init__, case, select, workspace_tx, readable_audiences).


##### `SurfaceContext.agent_setup`  (lines 3420–3459)

```
async def agent_setup(self, agent_id: UUID, member_id: UUID) -> SetupState
```

**Purpose**: Computes what one agent still needs before it is ready, such as accounts, credentials, or standing orders. It reads account readiness from the member’s perspective.

**Data flow**: Takes agent id and member id → defines an armed-order reader, enters workspace scope, calls setup_state → returns SetupState.

**Call relations**: Web agent_setup and workspace_starters routes call it; its nested armed helper reads objects through SurfaceContext methods.

*Call graph*: called by 2 (agent_setup, workspace_starters); 2 external calls (setup_state, ws).


##### `SurfaceContext.agent_setup.armed`  (lines 3442–3456)

```
async def armed(kind: str, name: str | None) -> ArmedOrder
```

**Purpose**: Checks whether a setup-required standing order is present for an agent. It can check a named object directly or look for any rows of a kind.

**Data flow**: Takes object kind and optional name → reads member_object for named orders or list_member_objects for unnamed orders → returns ArmedOrder with held state and optional schedule.

**Call relations**: SurfaceContext.agent_setup passes it into setup_state so extension-declared setup can ask about object-backed orders.

*Call graph*: calls 2 internal fn (list_member_objects, member_object); 2 external calls (__init__, __init__).


##### `SurfaceContext.list_member_objects`  (lines 3461–3487)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Reads one page of member-visible objects for a kind and agent. It lets the object kind’s own store enforce visibility and supported query fields.

**Data flow**: Takes kind, agent id, member id, admin flag, and query → looks up a member-listable store, binds agent scope, stamps supported fields → returns an ObjectPage or None.

**Call relations**: Agent setup and web object index/bound-page routes call it.

*Call graph*: called by 3 (armed, _bound_page, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 3489–3504)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one member-visible object detail row. Hidden and absent rows both return None.

**Data flow**: Takes kind, name, agent id, member id, and admin flag → looks up a member-readable store, binds agent scope, asks for member_detail → returns MemberObject or None.

**Call relations**: Agent setup and web object_detail routes call it.

*Call graph*: called by 2 (armed, object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 3506–3528)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants or rows attached to a conversation for a member. It is used for conversation-specific portal context.

**Data flow**: Takes kind, agent id, conversation id, member id, admin flag, and limit → looks up a conversation-member-listable store, binds agent scope, delegates the read → returns rows or None.

**Call relations**: The web slot context builder calls it.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 3530–3561)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists member-fillable credential slots and whether each has a stored value, never the secret values. It keeps deploy-only slots out of member UI.

**Data flow**: Takes no input → reads filled credential slots, maps declared slots to object names, filters member-fillable declarations → returns CredentialSlotView records.

**Call relations**: Web credential pages and panel intent submission call it.

*Call graph*: called by 2 (submit_intent, workspace_credentials); 4 external calls (__init__, select, named_slots, workspace_tx).


##### `SurfaceContext.workspace_domain`  (lines 3563–3569)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Reads the workspace’s verified domain subject, if it has one. Personal-email workspaces return None.

**Data flow**: Takes no input → opens a workspace transaction and calls the seats workspace_domain helper → returns a domain string or None.

**Call relations**: is_operator_workspace and join_member use it for operator checks and verified-domain auto-join.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.list_members`  (lines 3571–3579)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Lists the workspace roster sorted by email. It supports team administration views.

**Data flow**: Takes no input → reads the Seats snapshot in a transaction, sorts members by email → returns SeatEntry records.

**Call relations**: The web workspace_team route calls it.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 3581–3628)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists live source bindings visible to the viewer. Admins see all; members see their own and shared sources.

**Data flow**: Takes member id and admin flag → queries non-removed sources with owner info, applies visibility, extracts connector binding fields → returns SourceView records.

**Call relations**: The web workspace_sources route calls it, and it uses _binding_fields for connector-backed rows.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.spend_caps`  (lines 3630–3679)

```
async def spend_caps(self) -> tuple[SpendCapView, ...]
```

**Purpose**: Lists all spend caps in the workspace with human-readable subjects. It supports billing administration.

**Data flow**: Takes no input → joins caps to agent or member names where applicable → returns SpendCapView records.

**Call relations**: The web admin_index route calls it.

*Call graph*: called by 1 (admin_index); 4 external calls (__init__, and_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 3681–3697)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations bound to this workspace and their agents. It is used by workspace surface and admin views.

**Data flow**: Takes no input → queries surface_installation rows ordered by surface → returns InstallationSummary records.

**Call relations**: Several web routes call it for first-run, surfaces, providers, and admin pages.

*Call graph*: called by 4 (_held_providers, admin_index, workspace_first_run, workspace_surfaces); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 3699–3748)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists recent conversations across all surfaces in the workspace for debug-style views. It includes turn counts and last turn time.

**Data flow**: Takes an optional limit → aggregates turn activity per conversation, joins member emails, orders by latest movement → returns ConversationSummary records.

**Call relations**: The debugger conversations route calls it.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 3750–3773)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Lists conversations for one agent by delegating to ConversationDirectory. It binds the directory to this workspace.

**Data flow**: Takes agent/member ids and filters → creates ConversationDirectory for the workspace and calls list → returns ListedConversation records.

**Call relations**: Web conversation resolution and listing routes call it.

*Call graph*: called by 4 (_member_chat, _named, _resolve_chat, conversations); 1 external calls (__init__).


##### `SurfaceContext.readable_conversation`  (lines 3775–3816)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Checks whether a member may read a conversation’s content. Admins need a recent disclosure record before reading another member’s private conversation.

**Data flow**: Takes conversation id, agent id, member id, and admin flag → verifies workspace and agent, checks readable audiences, then checks recent transcript_access if needed → returns a boolean.

**Call relations**: The web surface’s readable-conversation gate calls it before transcript and file reads.

*Call graph*: called by 1 (_readable_conversation); 6 external calls (now, select, workspace_tx, audience_member, parse_audience, readable_audiences).


##### `SurfaceContext.conversation_audience`  (lines 3818–3828)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience bound to a conversation for a given agent. It returns None if the conversation is not found.

**Data flow**: Takes conversation id and agent id → queries the conversation audience in this workspace → parses and returns Audience or None.

**Call relations**: The web slot context builder calls it.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, workspace_tx, parse_audience).


##### `SurfaceContext.conversation_subagent_turns`  (lines 3830–3867)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists all subagent turns spawned under a conversation’s turns, including nested descendants. It lets views show the work tree under a parent conversation.

**Data flow**: Takes conversation id and limit → builds a recursive descendant query from parent_turn_id, reads turn rows breadth-first → returns Turn records.

**Call relations**: Web events, slot target, and transcript aid paths call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_events, _slot_target, _transcript_aids); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 3869–3885)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists recent turns of a conversation in admission order. It returns full durable turn records.

**Data flow**: Takes conversation id and limit → queries newest rows by descending sequence, reverses them to oldest-first → returns Turn records.

**Call relations**: Debugger and web transcript-aid routes call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _transcript_aids); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 3887–3922)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Finds transcript message references that came from machine-origin admissions, such as scheduled fires or subagent results. This helps UI avoid rendering those envelopes as member speech.

**Data flow**: Takes conversation id → unions matching turn ids and inbound message ids based on admission source or spawn-result keys → returns string references.

**Call relations**: Web conversation and history message builders call it.

*Call graph*: called by 2 (_conversation_messages, _history_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 3924–3974)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn with its accounting rows and direct subagent children. It supports detailed debugger and transcript views.

**Data flow**: Takes turn id → reads the turn, child turns, and ledger rows in one transaction → returns TurnDetail or None.

**Call relations**: Debugger and web turn-resolution, event, and conversation-message paths call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.turn_steps`  (lines 3976–3989)

```
async def turn_steps(self, turn_id: UUID) -> tuple[TurnStep, ...] | None
```

**Purpose**: Reads durable workflow steps for one turn if the turn belongs to this workspace. It returns None for foreign or missing turns.

**Data flow**: Takes turn id → reads the turn’s running attempt id or falls back to the turn id → asks TurnStepSource for steps → returns TurnStep records or None.

**Call relations**: The debugger turn_steps route calls it.

*Call graph*: called by 1 (turn_steps); 2 external calls (select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 3991–4032)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Lists admitted messages that are not yet visible in the written transcript. This lets a reloaded chat show messages that are waiting or folded into a still-running turn.

**Data flow**: Takes conversation id and optional draining turn id → confirms conversation ownership, queries unconsumed or currently consumed rows → returns QueuedArrival records.

**Call relations**: The web conversation message projection calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 4034–4066)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Reads attribution for member-admitted inbound-message rows in a conversation. This labels folded messages that are referenced by queue-row id.

**Data flow**: Takes conversation id → confirms ownership, queries member-admitted inbound messages and parses context → returns SpokenArrival records.

**Call relations**: Web conversation and history message builders call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (_conversation_messages, _history_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.keyed_admissions`  (lines 4068–4104)

```
async def keyed_admissions(self, conversation_id: UUID) -> tuple[KeyedAdmission, ...]
```

**Purpose**: Lists all admissions in a conversation that used an idempotency key. This helps projections match stored transcript references back to surface submissions.

**Data flow**: Takes conversation id → confirms ownership, unions keyed turn rows and keyed inbound_message rows → returns KeyedAdmission records.

**Call relations**: The web transcript-aids builder calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_transcript_aids); 4 external calls (__init__, select, union_all, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 4106–4117)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads the durable transcript blob for a conversation owned by this workspace. It refuses foreign conversation ids before touching blob storage.

**Data flow**: Takes conversation id → checks conversation ownership, fetches transcript blob, decodes it → returns Conversation, None if absent or unauthorized.

**Call relations**: Debugger, UFO, and web conversation renderers call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 5 (conversation_transcript, channel, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 4119–4130)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists available transcript compaction record indices for a conversation. Compactions are stored as blobs.

**Data flow**: Takes conversation id → checks ownership, lists compaction blob keys, extracts numeric indices → returns sorted integers.

**Call relations**: Debugger and web conversation-message views call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_compactions, _conversation_messages).


##### `SurfaceContext.read_compaction`  (lines 4132–4138)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one stored transcript compaction record. It returns None when the conversation is not owned or the record is missing.

**Data flow**: Takes conversation id and index → checks ownership, delegates to read_compaction_record → returns CompactionRecord or None.

**Call relations**: Debugger and web history message paths call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (compaction_record, _history_messages); 1 external calls (read_compaction_record).


##### `SurfaceContext.read_compaction_after`  (lines 4140–4148)

```
async def read_compaction_after(self, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the post-compaction message window for one compaction. This is a lighter read for comparison and verification.

**Data flow**: Takes conversation id and index → checks ownership, delegates to transcript read_compaction_after → returns messages or None.

**Call relations**: The web verified-earlier flow calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_verified_earlier); 1 external calls (read_compaction_after).


##### `SurfaceContext.list_workspace_files`  (lines 4150–4156)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files currently in a conversation’s sandbox workspace. It returns empty for missing or foreign conversations.

**Data flow**: Takes conversation id → checks ownership, asks the sandbox carrier for entries → returns WorkspaceFile records.

**Call relations**: Debugger and web attachment routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_files, conversation_attachment).


##### `SurfaceContext.conversation_changes`  (lines 4158–4164)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last recorded workspace changes for a conversation’s checkouts. It returns “nothing changed” when the conversation is not in this workspace.

**Data flow**: Takes conversation id → checks ownership, reads recorded workspace changes → returns WorkspaceChanges.

**Call relations**: The web slot context projection calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 4166–4175)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams a file out of a conversation’s live sandbox workspace. It returns None if the conversation or file is unavailable.

**Data flow**: Takes conversation id and relative path → checks ownership, asks the sandbox to read the file stream → returns an async byte iterator or None.

**Call relations**: Debugger and web conversation attachment routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_file, conversation_attachment).


##### `SurfaceContext.terminal_connect`  (lines 4177–4183)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Registers a live terminal connection for a conversation. This lets the sandbox use the member’s terminal as the turn’s working environment.

**Data flow**: Takes conversation id, cwd, optional member id, and runtime id → records the terminal binding in the sandbox terminal registry → returns nothing.

**Call relations**: Terminal-capable surface transports pair it with terminal_disconnect around a held connection.


##### `SurfaceContext.terminal_disconnect`  (lines 4185–4186)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the live terminal binding for a conversation. It is the cleanup half of terminal_connect.

**Data flow**: Takes conversation id → tells the sandbox terminal registry to disconnect it → returns nothing.

**Call relations**: Terminal-capable transports call it when the held terminal connection closes.


##### `SurfaceContext.claim_terminal`  (lines 4188–4194)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Attempts to bind a fresh conversation to the connected terminal before the first sandbox open. It reports whether this call made the claim.

**Data flow**: Takes conversation id and cwd → asks the sandbox carrier to claim the terminal if no binding exists → returns true if claimed.

**Call relations**: The UFO surface calls it while sending or opening terminal-backed conversations.

*Call graph*: called by 2 (_send, channel).


##### `SurfaceContext.next_terminal_op`  (lines 4196–4203)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next operation a turn wants the member’s terminal to perform. It can skip the operation the same request just answered.

**Data flow**: Takes conversation id and optional excluded op id → delegates to the terminal registry → returns the next TerminalOp.

**Call relations**: Terminal-streaming surfaces use it while racing terminal operations with turn frames.


##### `SurfaceContext.terminal_resolve`  (lines 4205–4219)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Resolves an in-flight terminal operation with the client’s reply or failure. The terminal registry enforces single-use operation id and member binding.

**Data flow**: Takes conversation id, op id, reply bytes, optional failure text, and member id → delegates resolution to the terminal registry → returns whether it was accepted.

**Call relations**: The UFO channel calls it when a terminal client posts an operation result.

*Call graph*: called by 1 (channel).


##### `SurfaceContext.terminal_op_body`  (lines 4221–4233)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads staged bytes for an in-flight terminal operation without creating a conversation. This supports clients that fetch operation bodies separately.

**Data flow**: Takes queue key, op id, and member id → finds existing conversation by queue key, then asks terminal registry for staged bytes → returns bytes or None.

**Call relations**: The UFO op_body route calls it.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 4235–4248)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface. It helps views link back to the surface where a conversation lives.

**Data flow**: Takes peer surface name → queries surface_installation for this workspace → returns installation id or None.

**Call relations**: The debugger workspace_meta route calls it.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 4251–4260)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides a raw workspace-scoped database transaction to trusted surface code. The surface remains responsible for including workspace_id in its own SQL.

**Data flow**: Takes no input → opens workspace_tx, yields the connection, commits on success or rolls back on error → returns when the context exits.

**Call relations**: Sites and Slack surface code call it when they need extension-specific table reads outside a turn.

*Call graph*: called by 2 (_viewer_is_admin, _folds_into_live_turn); 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 4262–4272)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace. It is a guard before unscoped blob or sandbox reads.

**Data flow**: Takes conversation id → queries conversation by id and workspace id → returns true if found.

**Call relations**: Transcript, compaction, workspace-file, arrival, and keyed-admission reads call it before accessing stored content.

*Call graph*: called by 10 (arrival_speakers, conversation_changes, keyed_admissions, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_compaction_after, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 4274–4294)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the common SQL select for reading full turn rows. It keeps all turn projections using the same column set.

**Data flow**: Takes no input → constructs a select over the turn columns needed to build a Turn record → returns the query.

**Call relations**: conversation_subagent_turns, list_turns, and turn_detail call it.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 4296–4316)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database row into a typed Turn object. It parses embedded context and terminal JSON into their proper models.

**Data flow**: Takes a SQL row → copies scalar fields and validates optional context/terminal data → returns a Turn.

**Call relations**: Turn listing and detail methods call it after running _turn_query.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.reserve_address`  (lines 4348–4408)

```
async def reserve_address(self, surface: str, address: str, member_id: UUID, claim_expires_at: datetime) -> AddressClaimState
```

**Purpose**: Lets a manifest-declared addressed surface reserve an address for a workspace member until proof arrives. It reports whether the address was reserved, already linked, or taken.

**Data flow**: Takes surface, address, member id, and expiry → verifies the surface is declared as addressed, upserts or reads the fleet-wide address claim → returns AddressClaimState.

**Call relations**: Tools use this access object under workspace scope when implementing address-claim flows.

*Call graph*: 7 external calls (__init__, now, and_, or_, select, owner_tx, ws_current).


##### `SurfaceInstallationAccess.installation`  (lines 4410–4423)

```
async def installation(self, surface: str) -> str | None
```

**Purpose**: Reads the current workspace’s installation id for a declared surface. It refuses undeclared surface names.

**Data flow**: Takes surface name → checks declaration, queries surface_installation in current workspace → returns installation id or None.

**Call relations**: Tool code can call it through SurfaceInstallationAccess to inspect its own declared surfaces.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `SurfaceInstallationAccess.bind`  (lines 4425–4437)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Binds a declared surface installation for the current workspace. Addressed surfaces are marked differently because their shared installation does not route tenants.

**Data flow**: Takes surface name and installation id → checks declaration, reads current workspace, delegates to _bind_surface_installation with the right routing flag → returns nothing.

**Call relations**: Tool-based installation flows call it instead of touching installation tables directly.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 4449–4460)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Resolves a shared surface request’s workspace from an installation id before any workspace context is bound. Only ingress-routing installations count.

**Data flow**: Takes installation id → queries owner-scope surface_installation rows for this surface and routes_ingress=true → returns workspace id or None.

**Call relations**: Slack workspace resolution calls it during pre-binding request authentication.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.addressed_workspace`  (lines 4462–4475)

```
async def addressed_workspace(self, address: str) -> UUID | None
```

**Purpose**: Resolves a workspace by an addressed-surface address. This is for fleet-wide providers where the sender address selects the tenant.

**Data flow**: Takes address → queries owner-scope surface_address rows for this surface → returns workspace id or None.

**Call relations**: SurfaceListenerContext.addressed uses it before yielding a SurfaceContext.

*Call graph*: 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 4477–4489)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential request before a workspace has been bound. Invalid or expired seals return None instead of raising.

**Data flow**: Takes sealed state → verifies it with the credential store’s Fernet key if available → returns CredentialRequestState or None.

**Call relations**: Slack request resolution calls it for OAuth state handling.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 4491–4507)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential for a workspace during pre-binding surface authentication. It checks both declaration and workspace existence.

**Data flow**: Takes workspace id and slot → verifies slot is declared, enters workspace scope, confirms workspace exists, reads encrypted credential → returns the secret or raises.

**Call relations**: Slack auth signing-secret lookup calls it while resolving workspace requests.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceListenerContext.workspace`  (lines 4554–4562)

```
async def workspace(self, installation_id: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds a listener event to a workspace resolved by installation id. It also verifies this process still owns the fleet listener lease.

**Data flow**: Takes installation id → checks ownership, resolves workspace through SurfaceAuth, enters workspace scope if found → yields SurfaceContext or None.

**Call relations**: Persistent listener implementations use it when provider events include an installation identity.

*Call graph*: 1 external calls (ws).


##### `SurfaceListenerContext.addressed`  (lines 4565–4576)

```
async def addressed(self, address: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds a listener event to the workspace that owns a sender address. It is for addressed shared providers.

**Data flow**: Takes address → checks listener ownership, resolves workspace by address, enters workspace scope if found → yields SurfaceContext or None.

**Call relations**: The iMessage listener uses it while processing events.

*Call graph*: called by 1 (_process_event); 1 external calls (ws).


##### `SurfaceListenerContext.cursor`  (lines 4578–4593)

```
async def cursor(self, installation_id: str) -> int | None
```

**Purpose**: Reads the stored stream cursor for this surface listener. It returns None if the cursor belongs to a different installation.

**Data flow**: Takes installation id → queries owner-scope stream cursor row for the surface → returns the sequence or None.

**Call relations**: The iMessage listener calls it when starting or resuming its stream.

*Call graph*: called by 1 (listen); 2 external calls (select, owner_tx).


##### `SurfaceListenerContext.store_cursor`  (lines 4595–4618)

```
async def store_cursor(self, installation_id: str, sequence: int) -> None
```

**Purpose**: Stores the latest stream position for a persistent listener. The cursor is fleet-level because one stream may deliver events for many workspaces.

**Data flow**: Takes installation id and sequence → upserts the cursor row by surface → returns nothing.

**Call relations**: The iMessage listener calls it after catching up or processing events.

*Call graph*: called by 2 (_catch_up, _process_event); 1 external calls (owner_tx).


##### `SurfaceListenerContext.clear_cursor`  (lines 4620–4627)

```
async def clear_cursor(self) -> None
```

**Purpose**: Deletes the stored listener stream cursor. The next read will start from the provider’s current head.

**Data flow**: Takes no input → deletes the cursor row for this surface → returns nothing.

**Call relations**: The iMessage listener calls it when it needs to reset stream position.

*Call graph*: called by 1 (listen); 2 external calls (delete, owner_tx).


##### `SurfaceListenerRunner.run`  (lines 4653–4691)

```
async def run(self) -> None
```

**Purpose**: Runs a persistent surface listener only while this process owns the fleet-wide listener lease. It restarts or parks behavior depending on failure type.

**Data flow**: Takes no input → waits for ownership, starts listener and ownership monitor tasks, reacts to whichever finishes first, logs failures, cancels tasks on exit → loops forever.

**Call relations**: Core starts this runner for registered surface listeners; it creates SurfaceListenerContext for the listener.

*Call graph*: calls 2 internal fn (_wait_until_not_owned, _wait_until_owned); 9 external calls (__init__, CancelledError, create_task, ensure_future, gather, sleep, wait, emit_metric, log).


##### `SurfaceListenerRunner._wait_until_not_owned`  (lines 4693–4698)

```
async def _wait_until_not_owned(self) -> None
```

**Purpose**: Waits until this process no longer owns the listener lease. It polls at the configured interval.

**Data flow**: Takes no input → repeatedly calls _owned_on_tick and sleeps → returns when ownership is explicitly false.

**Call relations**: run starts it as the ownership monitor beside the listener task.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._wait_until_owned`  (lines 4700–4704)

```
async def _wait_until_owned(self) -> None
```

**Purpose**: Waits until this process acquires the listener lease. It keeps polling instead of running the listener prematurely.

**Data flow**: Takes no input → repeatedly calls _owned_on_tick and sleeps until true → returns when owned.

**Call relations**: run calls it before starting each listener instance.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._owned_on_tick`  (lines 4706–4715)

```
async def _owned_on_tick(self) -> bool | None
```

**Purpose**: Attempts one ownership check and logs database failures. A database error returns None so polling can continue.

**Data flow**: Takes no input → calls _owns, catches SQLAlchemy errors, logs them → returns true, false, or None.

**Call relations**: _wait_until_owned and _wait_until_not_owned call it on each poll.

*Call graph*: calls 1 internal fn (_owns); called by 2 (_wait_until_not_owned, _wait_until_owned); 1 external calls (log).


##### `SurfaceListenerRunner._owns`  (lines 4717–4735)

```
async def _owns(self) -> bool
```

**Purpose**: Runs the lease-claim transaction in a cancellation-safe way. It avoids leaving database transactions half-aborted if the task is cancelled.

**Data flow**: Takes no input → starts _claim as a task, shields it until done, remembers cancellation, then either re-raises cancellation or returns claim result.

**Call relations**: _owned_on_tick calls it for each ownership poll.

*Call graph*: calls 1 internal fn (_claim); called by 1 (_owned_on_tick); 2 external calls (ensure_future, shield).


##### `SurfaceListenerRunner._claim`  (lines 4737–4777)

```
async def _claim(self) -> bool
```

**Purpose**: Claims or refreshes the fleet-wide listener lease for this surface. It uses an expiry so another process can take over if this one stops.

**Data flow**: Takes no input → computes expiry, upserts surface_listener_claim if expired or owned by this runner token → returns whether the stored token is this runner’s token.

**Call relations**: _owns calls it inside the cancellation-safe wrapper.

*Call graph*: called by 1 (_owns); 7 external calls (now, timedelta, and_, insert, insert, or_, owner_tx).


##### `SurfaceDeliveryError.__init__`  (lines 4784–4788)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that can carry a provider-requested retry delay. Negative retry delays are rejected.

**Data flow**: Takes message and optional retry_after_seconds → validates the delay, stores it, initializes RuntimeError → produces an exception instance.

**Call relations**: Slack delivery code raises it, and writeback pollers inspect it to schedule retries.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 4851–4875)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the condition for terminal turn writebacks that are ready to be claimed. It waits until pending mid-turn replies have either delivered or failed.

**Data flow**: Takes current time → creates SQL conditions over turn status, mid-turn reply status, writeback status, and claim expiry → returns the condition.

**Call relations**: writeback_workspaces.due and WritebackPoller._claim use it to find deliverable terminal replies.

*Call graph*: called by 2 (_claim, due); 3 external calls (and_, exists, or_).


##### `writeback_workspaces`  (lines 4878–4913)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace candidate reader for terminal writebacks. It lets the poller drain a bounded page of workspaces fairly.

**Data flow**: Initializes an internal cursor and owner-scope query reader → returns an async candidates function that pages due workspace ids.

**Call relations**: WritebackPoller receives this function as its candidates source.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 4885–4899)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the current owner-scope query for workspace ids with due terminal writebacks. It applies the rotating cursor if set.

**Data flow**: Reads current time and cursor → selects grouped workspace ids whose writebacks satisfy _writeback_due → returns the SQL query.

**Call relations**: writeback_workspaces passes it into owner_candidates, and candidates indirectly executes it.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 4903–4911)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next page of workspace ids with due terminal writebacks, wrapping around when it reaches the end. This avoids starving later workspace ids.

**Data flow**: Takes no input → calls the owner candidate reader, resets cursor if needed, updates cursor to the last returned id → returns workspace ids.

**Call relations**: WritebackPoller.run and drain call the candidates function supplied by writeback_workspaces.


##### `_WritebackDeliveryFailed.__init__`  (lines 4921–4924)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a failed writeback phase with whether it happened during post or attach. This lets retry logging and state updates explain the failure.

**Data flow**: Takes phase and original exception → stores both and initializes RuntimeError with a readable message → produces the wrapper exception.

**Call relations**: WritebackPoller._deliver_claimed raises it when a surface post or attach call fails.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 4947–4980)

```
async def run(self) -> None
```

**Purpose**: Continuously drains terminal writebacks across workspaces with bounded concurrency. It is the long-running background loop for durable final replies.

**Data flow**: Takes no input → cleans finished workspace tasks, fetches due workspaces, starts drain tasks under a semaphore, sleeps between polls, cancels outstanding tasks on shutdown.

**Call relations**: Core runs it for registered durable surfaces; it hands workspace ids to _drain_workspace.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 4982–4991)

```
async def drain(self) -> None
```

**Purpose**: Runs one bounded drain pass instead of an infinite loop. This is useful for tests or one-shot maintenance.

**Data flow**: Takes no input → gets candidate workspaces, drains each with a semaphore, gathers results → returns nothing or raises an ExceptionGroup for failures.

**Call relations**: It calls _drain_workspace just like run, but only once.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 4993–5011)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers terminal writebacks for one workspace. It also starts lease-renewal tasks while external delivery is in progress.

**Data flow**: Takes workspace id and semaphore → enters workspace scope, claims rows, starts renewals, delivers each row, cancels renewals afterward → returns nothing.

**Call relations**: run and drain call it for each candidate workspace.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 5013–5046)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due terminal writebacks for this worker. The claim prevents other workers from delivering the same row at the same time.

**Data flow**: Takes workspace id → selects due rows using _writeback_due, updates them to claimed with worker id and expiry → returns claimed row data.

**Call relations**: _drain_workspace calls it before starting deliveries.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 5048–5080)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed terminal writeback and records success, claim loss, or retry/failure. It wraps timing and logging around the delivery.

**Data flow**: Takes workspace id, turn id, existing reply ref, and renewal task → calls _deliver_with_lease, catches claim loss or delivery failures, updates retry state if needed → returns nothing.

**Call relations**: _drain_workspace calls it for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 5082–5111)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while the claim-renewal task is healthy, then stops renewal before marking the row delivered. This avoids racing its own lease refresh.

**Data flow**: Takes workspace id, turn id, reply ref, and renewal task → races delivery against renewal failure, cancels leftovers, then calls _mark_delivered → returns nothing.

**Call relations**: _deliver calls it for the actual guarded delivery flow.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 5113–5143)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Builds the writeback payload and calls the surface’s post and attach handlers. It records the post reference before attachments so retries do not repost unnecessarily.

**Data flow**: Takes workspace id, turn id, and optional reply ref → builds payload, finds surface spec, posts if needed, records reply ref, attaches artifacts → returns nothing.

**Call relations**: _deliver_with_lease calls it; it uses _build and _record_ref and wraps post/attach failures in _WritebackDeliveryFailed.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 5145–5148)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps a claimed writeback lease alive while delivery may take a long time. It refreshes periodically forever until cancelled.

**Data flow**: Takes turn id → sleeps for the refresh interval and calls _refresh_claim in a loop → returns only if cancelled or refresh fails.

**Call relations**: _drain_workspace starts one renewal task per claimed row.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 5150–5166)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim expiry for a writeback row. If the row is no longer claimed by this worker, it reports claim loss.

**Data flow**: Takes turn id → updates claim_expires_at where status and claimed_by match → returns nothing or raises _WritebackClaimLost.

**Call relations**: _renew_claim calls it repeatedly.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 5168–5220)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the Writeback payload for a terminal turn, including conversation routing data and shared artifacts. This is what the surface delivery handler receives.

**Data flow**: Takes turn id → queries turn, conversation, and shared artifact rows → validates terminal frame and builds Writeback plus surface name → returns both.

**Call relations**: _deliver_claimed calls it before choosing the surface spec.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 5222–5234)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Stores the external reply reference returned by a surface post. This lets later retries attach files without reposting the text.

**Data flow**: Takes turn id and reply ref → updates the claimed writeback row owned by this worker → returns nothing or raises _WritebackClaimLost.

**Call relations**: _deliver_claimed calls it after a successful post.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 5236–5253)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a claimed terminal writeback as delivered and clears its claim. It uses a worker check so only the claimant can finish the row.

**Data flow**: Takes turn id → updates status to delivered where claimed by this worker → returns nothing or raises _WritebackClaimLost.

**Call relations**: _deliver_with_lease calls it after delivery and lease renewal have stopped.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 5255–5304)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed terminal writeback for retry or marks it permanently failed after it is too old. It honors provider Retry-After values within a maximum window.

**Data flow**: Takes turn id and wrapped delivery failure → computes last error and retry time, updates the row if still claimed by this worker → returns outcome, error text, and next attempt time.

**Call relations**: _deliver calls it when _deliver_with_lease reports post or attach failure.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `mid_turn_reply_workspaces`  (lines 5307–5338)

```
def mid_turn_reply_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace candidate reader for deliverable mid-turn replies. It is separate from terminal writebacks because these replies happen before the turn ends.

**Data flow**: Initializes an internal cursor and owner-scope query reader → returns an async candidates function that pages due workspace ids.

**Call relations**: MidTurnReplyPoller receives this candidates function.

*Call graph*: 1 external calls (owner_candidates).


##### `mid_turn_reply_workspaces.due`  (lines 5313–5324)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the owner-scope query for workspaces with due mid-turn replies. It applies cursor paging for fairness.

**Data flow**: Reads current time and cursor → selects grouped workspace ids whose mid_turn_reply rows satisfy _mid_turn_reply_due → returns the SQL query.

**Call relations**: mid_turn_reply_workspaces passes it to owner_candidates.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); 2 external calls (now, select).


##### `mid_turn_reply_workspaces.candidates`  (lines 5328–5336)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next page of workspace ids with due mid-turn replies, wrapping around when needed. This spreads work across workspaces.

**Data flow**: Takes no input → executes the owner candidate reader, resets cursor if the page is empty past the end, updates cursor → returns workspace ids.

**Call relations**: MidTurnReplyPoller.drain calls this candidates function.


##### `_mid_turn_reply_due`  (lines 5341–5354)

```
def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the condition for mid-turn reply rows that can be claimed. Pending rows and expired claims are eligible.

**Data flow**: Takes current time → creates SQL status and claim-expiry conditions → returns the combined condition.

**Call relations**: mid_turn_reply_workspaces.due and MidTurnReplyPoller._claim use it.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `MidTurnReplyPoller.run`  (lines 5380–5386)

```
async def run(self) -> None
```

**Purpose**: Continuously drains mid-turn replies in a background loop. It logs failures and keeps polling.

**Data flow**: Takes no input → calls drain, logs any exception, sleeps for the poll interval → repeats forever.

**Call relations**: Core runs it for surfaces that may deliver mid-turn spoken replies.

*Call graph*: calls 1 internal fn (drain); 2 external calls (sleep, log).


##### `MidTurnReplyPoller.drain`  (lines 5388–5398)

```
async def drain(self) -> None
```

**Purpose**: Runs one pass over workspaces with due mid-turn replies. It claims and delivers rows one workspace at a time.

**Data flow**: Takes no input → gets candidate workspaces, enters each workspace scope, claims rows, logs retries, delivers each row → returns nothing.

**Call relations**: run calls it repeatedly.

*Call graph*: calls 2 internal fn (_claim, _deliver); called by 1 (run); 2 external calls (log, ws).


##### `MidTurnReplyPoller._claim`  (lines 5400–5441)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due mid-turn reply rows for this worker, ordered by creation and span order. This prevents duplicate delivery by peer workers.

**Data flow**: Takes workspace id → selects due rows, updates them to claimed with worker id and expiry, returns row data sorted for delivery → returns claimed rows.

**Call relations**: drain calls it before delivering replies.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); called by 1 (drain); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `MidTurnReplyPoller._deliver`  (lines 5443–5466)

```
async def _deliver(self, workspace_id: UUID, row: sa.Row) -> None
```

**Purpose**: Delivers one claimed mid-turn reply and updates its state. Failures are retried or failed using the shared retry window.

**Data flow**: Takes workspace id and claimed row → calls _speak, on error calls _fail_or_retry and logs, otherwise calls _mark_delivered and logs timing → returns nothing.

**Call relations**: drain calls it for each claimed mid-turn reply.

*Call graph*: calls 3 internal fn (_fail_or_retry, _mark_delivered, _speak); called by 1 (drain); 2 external calls (now, log).


##### `MidTurnReplyPoller._speak`  (lines 5468–5510)

```
async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None
```

**Purpose**: Calls the surface’s mid-turn speak handler unless the reply was already posted or the surface has no speak handler. Existing reply refs prevent duplicate posts after a crash.

**Data flow**: Takes workspace id and reply row → reuses stored reply_ref if present, otherwise loads turn routing data, finds surface spec, calls speak with a MidTurnReply payload → returns reply ref or None.

**Call relations**: _deliver calls it before marking the row delivered.

*Call graph*: called by 1 (_deliver); 4 external calls (__init__, select, workspace_tx, log).


##### `MidTurnReplyPoller._mark_delivered`  (lines 5512–5530)

```
async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Marks a mid-turn reply row delivered and stores the external reply reference if any. If the claim was lost, it logs instead of raising.

**Data flow**: Takes reply id and optional reply ref → updates the claimed row owned by this worker to delivered and clears claim fields → returns nothing.

**Call relations**: _deliver calls it after _speak succeeds or determines there is nothing to send.

*Call graph*: called by 1 (_deliver); 3 external calls (update, workspace_tx, log).


##### `MidTurnReplyPoller._fail_or_retry`  (lines 5532–5579)

```
async def _fail_or_retry(self, reply_id: UUID, error: Exception) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed mid-turn reply for retry or marks it failed once it is too old. A failed span stops blocking the terminal writeback.

**Data flow**: Takes reply id and exception → computes retry delay and last error, updates the claimed row to pending or failed → returns outcome, error text, and next attempt time.

**Call relations**: _deliver calls it when _speak raises an exception.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### iMessage cloud transport
The iMessage cloud bridge handles Spectrum credentials, HTTP and gRPC communication, attachments, and inbound cloud event conversion.

### `extensions/imessage/ufo_ext_imessage/cloud.py`

`io_transport` · `config load, message sending, event streaming, attachment transfer`

This file lets the project use iMessage without talking to Apple’s systems directly. Spectrum provides the outside service, and this code is the adapter between the rest of the UFO provider and Spectrum’s APIs. Without it, the iMessage extension would not know how to authenticate, find its shared sending line, receive new messages, send replies, or download attachments.

There are two kinds of network calls here. Ordinary HTTP requests are used for setup tasks, such as getting a temporary shared-line token or registering a phone number. gRPC, a network protocol for calling remote service methods, is used for live message work: catching up on missed events, subscribing to new events, sending text, uploading attachments, and downloading attachment data.

The central object is `SpectrumProject`. It stores the project ID and secret, keeps a cached shared-line token, and opens secure channels to Spectrum. The token is refreshed before it expires, like renewing a visitor badge before it stops opening doors. The file also has small safety helpers that identify external errors and invalid cursors.

Incoming Spectrum message events are filtered carefully. Messages from the user, system messages, spam, corrupt messages, stickers, and empty events are ignored. Only real inbound text or visible attachments are converted into `InboundMessage` objects for the rest of the provider.

#### Function details

##### `SpectrumProject.installation_id`  (lines 91–92)

```
def installation_id(self) -> str
```

**Purpose**: This property gives the project a stable local identity string. Other parts of the provider can use it to refer to this Spectrum-backed installation without exposing the secret.

**Data flow**: It reads the project ID stored on the `SpectrumProject` object, prefixes it with `project:`, and returns that combined string. It does not contact Spectrum or change any state.

**Call relations**: This is a simple identity helper on `SpectrumProject`. It sits alongside the network methods, giving callers a consistent name for the installation when they need to label or store provider state.


##### `SpectrumProject.line`  (lines 94–115)

```
async def line(self) -> SpectrumLine
```

**Purpose**: This gets the temporary shared-line token needed to use Spectrum’s iMessage service. It reuses a valid cached token when possible and asks Spectrum for a new one when the old one is missing or close to expiring.

**Data flow**: It first gets the per-event-loop state, then checks whether a cached `SpectrumLine` exists and still has enough time left before expiry. If so, it returns it. Otherwise it sends an HTTP request to Spectrum, validates the response, stores the new token and expiry time, and returns a `SpectrumLine` containing the line ID and token.

**Call relations**: Message operations such as `catch_up`, `subscribe`, `send_text`, `send_attachment`, and `download_attachment` call this before making gRPC requests. It relies on `_loop` for safe local state and `_request` to fetch the token from Spectrum.

*Call graph*: calls 2 internal fn (_loop, _request); called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 4 external calls (__init__, __init__, TypeAdapter, monotonic).


##### `SpectrumProject._loop`  (lines 117–133)

```
def _loop(self) -> SpectrumLoop
```

**Purpose**: This provides the HTTP client, lock, and token cache that are safe to use in the currently running async event loop. An event loop is the scheduler that runs asynchronous tasks.

**Data flow**: It looks up the current event loop and checks whether this `SpectrumProject` already has state for it. If yes, it returns that state. If not, it creates a new `SpectrumLoop` with an HTTP client, an async lock, and a token dictionary, stores it, and returns it.

**Call relations**: `line`, `_request`, and `invalidate` use this whenever they need token or HTTP state. Its job is to keep async resources tied to the loop they belong to, preventing one loop from accidentally reusing objects created for another loop.

*Call graph*: called by 3 (_request, invalidate, line); 4 external calls (__init__, Lock, get_running_loop, AsyncClient).


##### `SpectrumProject.assign_line`  (lines 135–165)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This makes sure a phone number is registered with Spectrum and returns the shared Spectrum phone number assigned to it. It avoids creating a duplicate registration if the phone number already exists.

**Data flow**: It asks Spectrum for the project’s current users, validates the returned list, and searches for the requested phone number. If it finds one, it returns that user’s assigned phone number. If not, it sends a create-user request with an idempotency key, validates the created user response, and returns the new assigned phone number.

**Call relations**: This method uses `_request` for the HTTP calls and Pydantic validation to make sure Spectrum’s responses have the expected shape. It is used during the flow where a member phone number needs to be linked to the shared iMessage line before messages can be sent reliably.

*Call graph*: calls 1 internal fn (_request); 2 external calls (__init__, TypeAdapter).


##### `SpectrumProject._request`  (lines 167–190)

```
async def _request(self, method: str, path: str, *, json: dict[str, str] | None=None, idempotency_key: str | None=None) -> object
```

**Purpose**: This is the shared helper for HTTP calls to Spectrum Cloud. It adds authentication, optional duplicate-protection headers, timeout settings, and consistent error reporting.

**Data flow**: It receives an HTTP method, a path, optional JSON data, and an optional idempotency key. It builds the full Spectrum URL, sends the request using the project ID and secret as Basic Auth credentials, checks for HTTP failure statuses, and returns the parsed JSON response. If Spectrum returns an error status, it raises `SpectrumCloudError`.

**Call relations**: `line` and `assign_line` call this for Spectrum Cloud REST requests. It uses `_loop` to get the correct HTTP client for the current async environment, so callers do not need to worry about client setup.

*Call graph*: calls 1 internal fn (_loop); called by 2 (assign_line, line); 2 external calls (__init__, BasicAuth).


##### `SpectrumProject.channel`  (lines 192–193)

```
def channel(self) -> grpc.aio.Channel
```

**Purpose**: This opens a secure gRPC channel to Spectrum’s iMessage endpoint. A secure channel is the encrypted connection used for remote service calls.

**Data flow**: It creates and returns a gRPC channel pointed at Spectrum’s iMessage address, using SSL credentials so the connection is encrypted. It does not send any message by itself.

**Call relations**: `catch_up`, `subscribe`, `send_text`, `send_attachment`, and `download_attachment` call this when they are ready to talk to Spectrum’s live iMessage services. Those methods then create service-specific stubs on top of the channel.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 1 external calls (ssl_channel_credentials).


##### `SpectrumProject.invalidate`  (lines 195–198)

```
async def invalidate(self) -> None
```

**Purpose**: This clears the cached shared-line token. It is useful when the caller knows the token should no longer be trusted, for example after an authentication failure.

**Data flow**: It gets the current loop’s token state, takes the async lock so no other task edits the cache at the same time, and empties the token dictionary. It returns nothing.

**Call relations**: This supports the same token cache used by `line`. After invalidation, the next call to `line` will have to fetch a fresh token from Spectrum.

*Call graph*: calls 1 internal fn (_loop).


##### `SpectrumProject.invalid_cursor`  (lines 200–204)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This tells whether an error means an event cursor is invalid. A cursor is a saved position in a stream of events, like a bookmark in a log.

**Data flow**: It receives an exception and checks whether it is a gRPC error with the `INVALID_ARGUMENT` status code. It returns `true` for that specific case and `false` otherwise.

**Call relations**: Code that reads event streams can use this to decide whether a saved event position should be discarded and rebuilt. It does not call the stream methods directly; it classifies errors they may raise.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.external_error`  (lines 206–207)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This tells whether an exception came from Spectrum or the network layer rather than from local program logic. That helps higher-level code decide what can be retried or reported as an outside service problem.

**Data flow**: It receives an exception and checks whether it is a gRPC error, a Spectrum cloud error, or an HTTPX network/HTTP error. It returns a boolean answer and does not change anything.

**Call relations**: This is an error-classification helper for callers around the cloud transport. It groups together the kinds of failures that can happen while talking to Spectrum through HTTP or gRPC.


##### `SpectrumProject.error_code`  (lines 209–212)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This turns an exception into a short error label. It gives gRPC errors their official status name and uses the Python exception class name for other errors.

**Data flow**: It receives an exception. If the exception is a gRPC error, it reads its status code and returns the code name. Otherwise it returns the exception type’s name as text.

**Call relations**: Higher-level logging, metrics, or retry code can use this to record failures in a consistent way. It complements `external_error` and `invalid_cursor`, which also interpret errors from Spectrum communication.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.catch_up`  (lines 214–235)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This reads missed message events from Spectrum after a saved sequence number. It is used to catch up after downtime or after restarting, so no inbound messages are skipped.

**Data flow**: It gets a valid line token, builds a catch-up request, optionally includes the last seen sequence number, and opens a secure gRPC stream. As frames arrive, it yields `ProviderEvent` objects. Completion frames become head-sequence events, and message-change frames are converted into inbound messages when `_inbound_message` decides they are real incoming user messages.

**Call relations**: This method depends on `line` for authentication, `channel` for the secure connection, `rpc_metadata` for request headers, and `_inbound_message` for filtering message payloads. It feeds normalized `ProviderEvent` objects back to the provider’s event-processing flow.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 3 external calls (__init__, CatchUpEventsRequest, EventServiceStub).


##### `SpectrumProject.subscribe`  (lines 237–253)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This listens for new live message events from Spectrum. It is the ongoing stream used while the provider is running.

**Data flow**: It gets a line token, creates a subscription request, opens a secure gRPC stream, and marks the supplied `ready` event once the subscription has started. For each incoming frame, it yields a `ProviderEvent` containing the sequence number if present and an inbound message if the frame contains a usable received-message event.

**Call relations**: Like `catch_up`, it uses `line`, `channel`, `rpc_metadata`, and `_inbound_message`. The `ready` event lets orchestration code know when the live listener is actually connected.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 4 external calls (__init__, set, SubscribeMessageEventsRequest, MessageServiceStub).


##### `SpectrumProject.send_text`  (lines 255–268)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This sends a plain text iMessage through Spectrum into an existing conversation. It returns Spectrum’s message ID for the sent message.

**Data flow**: It receives a conversation ID, text, and idempotency key. It gets a valid token, builds a send-text request with the conversation and client message ID, sends it over a secure gRPC channel with authentication metadata, and returns the GUID of the message Spectrum created.

**Call relations**: This is one of the outgoing message paths. It relies on `line` for a bearer token, `channel` for the gRPC connection, and `rpc_metadata` to attach authentication and duplicate-protection information.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (SendTextMessageRequest, MessageServiceStub).


##### `SpectrumProject.send_attachment`  (lines 270–297)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This sends a file attachment through Spectrum. It first uploads the file, then sends a message that points to the uploaded attachment.

**Data flow**: It receives a conversation ID, filename, raw file bytes, and idempotency key. It gets a token, opens a gRPC channel, uploads the attachment bytes with an upload-specific idempotency key, then sends an attachment message referencing the uploaded attachment GUID. It returns the GUID of the final message.

**Call relations**: This method combines the attachment service and message service in one flow. It uses `line`, `channel`, and `rpc_metadata`, and it carefully uses separate idempotency keys so retrying an upload or send does not accidentally create duplicates.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 4 external calls (UploadAttachmentRequest, AttachmentServiceStub, SendAttachmentMessageRequest, MessageServiceStub).


##### `SpectrumProject.download_attachment`  (lines 299–309)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This downloads an attachment from Spectrum in chunks. It is used when an inbound message has an attachment and the provider needs the actual bytes.

**Data flow**: It receives an attachment ID, gets a valid token, opens a secure gRPC stream, and asks Spectrum to download that attachment. As chunk frames arrive, it yields the primary byte chunks one by one. The caller can assemble or stream those bytes without loading everything at once here.

**Call relations**: This is the inbound attachment data path. It relies on `line` for authentication, `channel` for the secure connection, and `rpc_metadata` for the bearer-token header.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (DownloadAttachmentRequest, AttachmentServiceStub).


##### `spectrum_project`  (lines 313–327)

```
def spectrum_project() -> SpectrumProject
```

**Purpose**: This creates the shared `SpectrumProject` object from deployment environment settings. It also reports a clear configuration error if the required Spectrum credentials are missing.

**Data flow**: It reads the project ID and project secret from deployment environment variables. If either is missing, it raises `ProviderNotConfigured` with instructions. If both are present, it creates an HTTP client, an async lock, an empty token cache, and returns a `SpectrumProject`. Because it is cached, later calls reuse the same object.

**Call relations**: This is the setup doorway for the file. Other code can call it to get the configured Spectrum connection object, instead of rebuilding clients and rereading credentials each time.

*Call graph*: 5 external calls (__init__, __init__, Lock, AsyncClient, deploy_env).


##### `rpc_metadata`  (lines 330–334)

```
def rpc_metadata(token: str, idempotency_key: str | None=None) -> tuple[tuple[str, str], ...]
```

**Purpose**: This builds the small set of headers sent with gRPC calls to Spectrum. The headers carry the bearer token and, when needed, an idempotency key to make retries safe.

**Data flow**: It receives a token and optionally an idempotency key. It creates an `authorization` header using `Bearer <token>`, adds an `x-idempotency-key` header if one was provided, and returns the headers as a tuple.

**Call relations**: `catch_up`, `subscribe`, `send_text`, `send_attachment`, and `download_attachment` call this before making gRPC requests. It keeps authentication header formatting in one place.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe).


##### `_inbound_message`  (lines 337–375)

```
def _inbound_message(event: object) -> InboundMessage | None
```

**Purpose**: This turns a raw Spectrum message-change event into the provider’s simpler `InboundMessage` format, but only if it is a real incoming user message. It filters out noise such as outgoing messages, spam, system/service messages, corrupt messages, stickers, hidden attachments, and empty messages.

**Data flow**: It receives an event object and first checks that it is the expected Spectrum message-change type. It then confirms the change is a received message and not from the current user. It finds the sender address, gathers visible non-sticker attachments, reads the text if present, and returns an `InboundMessage` with IDs, sender, text, attachments, and whether the chat looks direct. If any required piece is missing or the event should be ignored, it returns `None`.

**Call relations**: `catch_up` and `subscribe` call this for each message-change frame from Spectrum. It acts as the filter and translator between Spectrum’s detailed event format and the provider’s clean internal event format.

*Call graph*: called by 2 (catch_up, subscribe); 2 external calls (__init__, __init__).


### Slack message semantics
Slack-specific helpers add attribution footers, run delivery hooks, clean connection UI, and convert mentions between readable text and Slack codes.

### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `Slack send and inbound message handling`

When this system sends a Slack message through a connector, that message may not pass through the usual Slack message renderer. So this file supplies the missing attribution footer: a short marker saying the message came through the product, but using a real Slack mention of this workspace’s bot user. That matters because a reader can click the mention and find the agent.

The file also solves a second, easy-to-miss problem. Slack may report any message containing a bot mention as an “app mention.” If the system later reads back a message it sent itself, the attribution footer could look like someone addressed the bot. This file prevents that by stripping known attribution footers before deciding whether a mention is meaningful.

Think of it like signing outgoing letters with a return address, then teaching the mailroom not to treat that return address as a new incoming request.

The main pieces work together simply: one function identifies connector calls that are Slack sends, one appends the bot-mention footer, one checks whether text mentions the bot after removing that footer, and one gathers all text hidden inside a Slack event so the mention check does not miss text stored in blocks or rich-text structures.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: This function decides whether a connector call is the kind of call that publishes a Slack message. It uses the same provider and name pattern as the connector attribution code, so both parts of the system agree about which calls should get a footer.

**Data flow**: It receives a connector provider name and a connector slug, which is the connector action name. It lowercases the slug, checks that the provider is Slack, checks that the slug refers to a message, and checks that it contains a send-style verb. It returns true only when all of those are true; otherwise it returns false and changes nothing.

**Call relations**: Other Slack connector code can use this as the gate before adding Slack-specific attribution. It does not call other project functions; it is the shared yes-or-no test that keeps footer decisions aligned with the connector tool’s own attribution rules.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: This function returns Slack send arguments with an attribution footer added at the end, using a mention of the actual bot user. If the message already has an attribution footer, it leaves the arguments alone so the footer is not stacked twice.

**Data flow**: It receives the outgoing Slack arguments and the Slack bot user ID. It formats the attribution subject so it becomes a Slack mention for that bot user, then passes the original arguments and that subject to the shared connector attribution helper. The result is a new or updated arguments dictionary containing the footer when needed.

**Call relations**: This sits between Slack-specific knowledge and the generic connector attribution helper. It uses UFO_ATTRIBUTION_MENTION_SUBJECT.format to build the bot mention, then hands the message shape work to attributed_arguments so this file does not duplicate the generic footer-building rules.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: This function answers a careful question: does this text really mention the bot, apart from any attribution footer this system wrote itself? It helps avoid treating the system’s own footer as if a user had spoken to the agent.

**Data flow**: It receives a text string and the bot user ID. First it removes known attribution text from the string. Then it looks for Slack’s bot mention form, such as <@bot_id>, in what remains. It returns true if the bot is still mentioned after footer text is removed, and false otherwise.

**Call relations**: Inbound Slack-message logic can call this when deciding whether a message is addressed to the agent. It delegates the footer-removal step to attribution_stripped, then performs the bot-mention check itself.

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: This function gathers every piece of human-readable text in a Slack message event where a bot mention might appear. It checks not just the main text field, but also text buried inside Slack blocks and nested rich-text structures.

**Data flow**: It receives a Slack event represented like a dictionary. It reads the event’s main text field, defaulting to an empty string if missing, then reads the blocks field and asks _nested_strings to pull out all strings inside it. It returns one tuple containing the main text followed by all nested strings it found.

**Call relations**: Mention-detection code can use this before calling addressing_mention, so it does not miss mentions hidden in Slack’s block-based message layout. It calls _nested_strings to do the recursive search through nested Slack data.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: This helper walks through nested Slack data and yields every string it can find. It exists because Slack message blocks can be made of dictionaries inside lists inside more dictionaries, and mentions can appear at any level.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, like a dictionary, it searches each stored value. If it is a list, it searches each item. Other kinds of values are ignored. It produces a stream of strings and does not modify the original data.

**Call relations**: message_bodies calls this when it needs to inspect Slack blocks. This helper performs the recursive digging, while message_bodies decides which parts of the Slack event should be searched.

*Call graph*: called by 1 (message_bodies).


### `extensions/slack/ufo_ext_slack/hooks.py`

`orchestration` · `event hooks during tool use and after connection recording`

This file is the Slack extension’s event listener. It steps in at two moments where the general connector system does not know enough Slack-specific information on its own. First, before an external tool sends a Slack message, it may rewrite the message text so the footer mentions the actual Slack bot user for this workspace. That matters because a generic footer is less clear to Slack users; the Slack extension is the part that knows which bot identity was proven during install. The code is careful not to block the send. The pre-send hook is a gate: if it fails or takes too long, the message could be denied. Since the footer is only cosmetic, this file gives its store lookup its own short timeout and treats any error as “do nothing.” In that case, the normal generic footer is left in place.

Second, after a user finishes authorizing an external provider account, the file updates the Slack thread where the original “connect” button was posted. Without this, the user might see a button inviting them to do something they already completed. The hook reads the saved Slack message reference, uses the bot token to update that message, and then deletes the saved reference. In short, this file keeps Slack conversations accurate without making successful account connection depend on Slack message cleanup.

#### Function details

##### `attribute_connector_send`  (lines 38–52)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook runs before an external connector tool is used. If the tool call is a Slack send, it tries to add a footer that mentions the correct Slack bot user, so people can see which bot sent or assisted with the message.

**Data flow**: It receives a hook context containing the pending tool call. It checks whether the call is specifically a Slack send. If not, it returns no change. If it is, it asks `_mirrored_self_user_id` for the bot user ID saved for this workspace. When a valid bot ID is available, it rewrites the tool call’s message arguments with `mention_attributed` and returns a `ModifyInput` outcome containing the updated tool input. If the bot ID is missing, it leaves the call untouched.

**Call relations**: The hook system calls this just before `call_external_tool` would run. It uses `is_slack_send` to decide whether this is the kind of tool call it cares about, delegates the safe store lookup to `_mirrored_self_user_id`, and uses `mention_attributed` to prepare the new message arguments before handing the modified input back to the hook system.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 55–69)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper safely reads the Slack bot user ID that the Slack surface previously saved for the current workspace. It is deliberately cautious: if the read fails, takes too long, or returns something that does not look like a bot user ID, it returns nothing instead of risking blocking a Slack send.

**Data flow**: It receives the hook context and reads the extension’s scoped store at the known self-user-ID key. The read is wrapped in a one-second timeout. If an exception happens, it logs a small diagnostic message and returns `None`. If the stored value is a string matching the expected Slack bot-user pattern, it returns that string. Otherwise it returns `None`.

**Call relations**: `attribute_connector_send` calls this whenever it is about to decorate a Slack send. This helper does not contact Slack directly; it only reads the mirrored value from the extension store. Its failure path is intentionally quiet from the user’s point of view, allowing the caller to continue with the original generic attribution.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


##### `settle_connect_button`  (lines 72–101)

```
async def settle_connect_button(ctx: HookContext) -> HookOutcome
```

**Purpose**: This hook runs after the system records that a user successfully connected an external account. It updates the old Slack “connect” button message so the conversation reflects that the account is now connected.

**Data flow**: It receives a hook context whose payload should describe the recorded connection: provider, account ID, optional account label, and owning member. It builds the store key for the saved Slack connect message and reads it. If no saved message exists, it does nothing. If one exists, it gets the Slack bot token, validates the saved message data as a `ConnectMessage`, updates the Slack message through `settle_connect_message`, and then deletes the saved store entry. If the payload is not a connection-recorded event, it raises an error because the hook was invoked in the wrong situation.

**Call relations**: The system calls this after a connection has already been recorded, so account authorization is not at risk if Slack cleanup fails. The function uses `connect_message_key` to find the saved button message, `ConnectMessage.model_validate` to turn stored data back into the expected message reference, and `settle_connect_message` to perform the Slack update.

*Call graph*: 3 external calls (model_validate, connect_message_key, settle_connect_message).


### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message ingest and reply sending`

Slack messages do not arrive exactly as people see them. A person mention may arrive as something like `<@U123>`, a channel as `<#C456|team>`, and a link as `<https://example.com|docs>`. This file is the translator between Slack's wire format and the plain words that humans and the model should read. On the way in, `render_markup` rewrites known Slack entities into text such as `@Alex` or `#team`, but it leaves unknown codes alone rather than guessing. It also keeps Slack's escaped characters, like `&lt;`, separate from entity rendering because unescaping someone else's words can be unsafe when those words are later embedded in prompts or markup. On the way out, the problem is reversed: the agent may write `@Alex`, but Slack will not notify Alex unless the text contains `<@U123>`. `mention_index` builds a safe name-to-id lookup, refusing names that are ambiguous or look like broadcast words such as `here`. `mention_markup` then rewrites only approved names, skips code blocks and URLs, and caps how many mentions it will convert so that quoting a crowd does not accidentally page everyone. In short, this file is a careful translator and safety guard between readable conversation text and Slack's notification syntax.

#### Function details

##### `mentioned_users`  (lines 67–70)

```
def mentioned_users(text: str) -> frozenset[str]
```

**Purpose**: Finds which Slack user IDs are explicitly mentioned in a raw Slack message. A caller can use those IDs to look up real names before turning the message into readable text.

**Data flow**: It receives the message text as Slack sent it. It asks the shared `_mentioned` scanner to look only for user-style mentions, then returns a frozen set of user IDs found in the text. It does not change the message.

**Call relations**: This is a small public-facing wrapper around `_mentioned`. Code that needs to resolve user mentions calls this instead of parsing Slack markup itself, and `_mentioned` does the common scanning work.

*Call graph*: calls 1 internal fn (_mentioned).


##### `mentioned_channels`  (lines 73–75)

```
def mentioned_channels(text: str) -> frozenset[str]
```

**Purpose**: Finds which Slack channel IDs are explicitly mentioned in a raw Slack message. A caller can use those IDs to ask Slack for channel names before showing or storing the message.

**Data flow**: It takes Slack message text as input. It delegates to `_mentioned`, telling it to collect only channel-style mentions, and returns a frozen set of channel IDs. The original text is left untouched.

**Call relations**: This mirrors `mentioned_users`, but for channels. Both wrappers rely on `_mentioned` so the file has one consistent way to recognize Slack entity syntax.

*Call graph*: calls 1 internal fn (_mentioned).


##### `_mentioned`  (lines 78–83)

```
def _mentioned(text: str, kind: str) -> frozenset[str]
```

**Purpose**: Does the shared work of scanning Slack markup and extracting IDs of one requested kind, such as users or channels. It exists so user and channel mention detection stay identical except for the marker they look for.

**Data flow**: It receives the message text and a kind marker, such as `@` for users or `#` for channels. It searches for Slack entity patterns, keeps only matches with the requested kind and a non-empty ID, and returns those IDs as a frozen set.

**Call relations**: `mentioned_users` and `mentioned_channels` call this when they need raw IDs from Slack text. It does not call outward to Slack or any other project code; it is the local scanner behind those two entry points.

*Call graph*: called by 2 (mentioned_channels, mentioned_users).


##### `render_markup`  (lines 86–96)

```
def render_markup(text: str, names: Mapping[str, str]) -> str
```

**Purpose**: Turns Slack's encoded entities into the words a reader expects to see. This makes stored messages, transcripts, titles, and model input readable instead of full of opaque Slack IDs.

**Data flow**: It receives raw Slack text plus a mapping from Slack IDs to known names. It walks through Slack entity-shaped pieces in the text and replaces each one with a readable form, such as `@Name`, `#channel`, `@here`, or a labeled link. Unknown mentions stay in their original encoded form, and Slack escape sequences like `&lt;` are deliberately not decoded here.

**Call relations**: This is the inbound rewrite step for message text. For each entity it finds, it uses the local entity-rendering logic in `_entity` to decide the safe readable replacement, while leaving ordinary text alone.


##### `unescape`  (lines 99–108)

```
def unescape(text: str) -> str
```

**Purpose**: Converts Slack's escaped `&amp;`, `&lt;`, and `&gt;` back into `&`, `<`, and `>` when it is safe to do so. It is meant for the original speaker's own message, not for quoting other people's text.

**Data flow**: It takes a text string that may contain Slack escape sequences. It replaces the three known escaped forms one by one and returns the decoded text. It does not look for mentions, links, users, or channels.

**Call relations**: This function is intentionally separate from `render_markup`. The wider system can choose to unescape only trusted first-person text, while leaving bystanders' quoted words escaped so they cannot accidentally break surrounding prompt or markup structure.


##### `mention_key`  (lines 111–115)

```
def mention_key(name: str) -> str
```

**Purpose**: Normalizes a person's displayed name into a stable lookup key for mention matching. It smooths over differences in capitalization and repeated spaces, so `@Alex Graveley` can match a stored name with unusual spacing or case.

**Data flow**: It receives a name string. It splits the name into words, joins them back with single spaces, lowercases it in a Unicode-aware way, and returns that normalized key. It does not decide whether the name is safe or unique.

**Call relations**: `mention_index` calls this while building the safe name-to-ID table, and `_mention_at` calls it while checking whether text after an `@` matches a known person. It is the common rule that keeps stored names and written names comparable.

*Call graph*: called by 2 (_mention_at, mention_index).


##### `mention_index`  (lines 118–132)

```
def mention_index(names: Mapping[str, str]) -> dict[str, str]
```

**Purpose**: Builds the safe lookup table used to turn readable `@Name` text back into Slack user mentions. It refuses to map names that are shared by more than one user or that match broadcast words like `here`, because guessing could notify the wrong person or trigger a mass alert.

**Data flow**: It receives a mapping from Slack user IDs to readable names. For each name, it creates a normalized key with `mention_key`, groups IDs that claim the same key, drops empty keys and broadcast words, and returns only keys that point to exactly one ID. The output maps normalized names to Slack IDs.

**Call relations**: This prepares the allowlist that `mention_markup` uses later on the send path. It calls `mention_key` so the index and the later matcher use the same idea of what counts as the same name.

*Call graph*: calls 1 internal fn (mention_key).


##### `mention_markup`  (lines 135–166)

```
def mention_markup(text: str, ids: Mapping[str, str], limit: int=MENTION_MARKUP_MAX) -> str
```

**Purpose**: Rewrites selected readable `@name` mentions in an outbound reply into Slack's notification format, such as `<@U123>`. It is deliberately cautious: it avoids URLs and code, ignores unknown names, and stops after a small limit to prevent accidental mass paging.

**Data flow**: It receives the reply text, a safe name-to-ID mapping, and an optional maximum number of mentions to convert. It first finds spans that should never be rewritten, such as code blocks, Markdown link targets, angle-bracketed links, and bare URLs. Then it scans each `@`, rejects ones that look like part of an email address or URL, asks `_mention_at` whether a known name starts there, and replaces approved names with Slack mention codes. The result is a new text string ready for Slack; the input mapping is not changed.

**Call relations**: This is used on the outbound send path after `mention_index` has built the safe mapping. It calls `_mention_at` to interpret the words after each `@`, and uses regular expression scanning to find candidate `@` signs and protected spans.

*Call graph*: calls 1 internal fn (_mention_at); 1 external calls (finditer).


##### `_mention_at`  (lines 169–182)

```
def _mention_at(text: str, start: int, ids: Mapping[str, str]) -> tuple[int, str] | None
```

**Purpose**: Checks whether the text immediately after an `@` begins with a known mentionable name, and chooses the longest valid name that matches. This matters because display names can contain spaces, so a simple one-word match would be unreliable.

**Data flow**: It receives the whole text, the position just after an `@`, and the safe name-to-ID mapping. It looks only at a short slice of the current line, rejects names that begin with whitespace, considers up to a few words, trims sentence-ending punctuation, normalizes each candidate with `mention_key`, and returns the end position plus the matching Slack ID if it finds one. If no approved name fits, it returns nothing.

**Call relations**: `mention_markup` calls this whenever it finds an `@` that might be a real outbound mention. `_mention_at` uses `mention_key` so candidate text is compared the same way names were stored in `mention_index`, and it uses regular expression word scanning to try possible name lengths.

*Call graph*: calls 1 internal fn (mention_key); called by 1 (mention_markup); 2 external calls (islice, finditer).


##### `_entity`  (lines 185–201)

```
def _entity(match: re.Match[str], names: Mapping[str, str]) -> str
```

**Purpose**: Converts one matched Slack entity into its readable form. It knows the difference between user mentions, channel mentions, broadcast mentions, and links, and chooses the safest text for each.

**Data flow**: It receives a regular expression match for one Slack entity plus a mapping from IDs to known names. For user and channel mentions, it prefers the known name, then Slack's label, and otherwise leaves the original code unchanged. For broadcasts, it renders recognized broadcast words as `@here`, `@channel`, or `@everyone`. For links, it returns either the URL alone or `label (URL)` when the label differs from the URL.

**Call relations**: This is the per-entity decision helper used by `render_markup` while rewriting inbound Slack text. `render_markup` finds the entity-shaped pieces; `_entity` decides what each one should become for a human reader.
