# External chat and terminal ingress  `stage-7.1`

This stage is the system’s front door for messages arriving from outside places, during the main work loop. Slack, iMessage, the UFO terminal client, web chat, and similar “surfaces” all speak different languages. These files translate them into the shared UFO conversation format, then translate replies back out again.

The core bridge in runtime/ext/surface.py is the gatekeeper. It lets trusted surfaces identify members, add new message turns, read conversation state, deliver replies, and show portal views. The Slack surface checks that requests really came from Slack, imports messages, files, forms, progress updates, and install flows, then sends UFO’s responses back. Slack mention utilities turn Slack’s hidden user codes into readable names and back again, while attribution utilities manage small bot footers without confusing them for real mentions.

The iMessage surface performs the same two-way conversion for texts, attachments, replies, files, and account prompts. Its cloud helper talks to Spectrum Cloud, the outside iMessage service. The terminal surface turns web requests into simple commands a shell script can follow, and the Redis terminal stream keeps terminal sessions working across separate server pods.

## Files in this stage

### Ingress surfaces and core bridge
External chat and terminal surfaces accept inbound activity, normalize it, and hand trusted turns and replies through the shared surface runtime bridge.

### `extensions/imessage/ufo_ext_imessage/surface.py`

`io_transport` · `main loop and message handling`

This is the bridge between an iMessage provider and the rest of UFO. Without it, UFO would not know how to listen to iMessage chats, remember where it left off in the message stream, prove that a phone number belongs to a member, or send replies back to the right chat.

The file has two main jobs. First, it runs a long-lived listener that reads provider events in order. It catches up from a saved cursor, then watches live messages. A cursor is just a saved position in the stream, like a bookmark in a book. Each message is admitted only after UFO finds a matching address claim for that sender.

Second, it formats traffic both ways. Incoming iMessage attachments are downloaded into a workspace inbox when they are small enough. Outgoing UFO replies become plain text messages, with links added for reports or large files. Small generated files can be sent as real iMessage attachments.

There is also an opt-in flow. When someone is proving a phone number, this file checks the code they typed, handles expired or cancelled claims, confirms the address, and sends a contact card for the UFO line. That contact card helps iMessage treat the conversation as known instead of suspicious.

#### Function details

##### `read_claim`  (lines 64–74)

```
def read_claim(stored: object) -> PendingClaim | None
```

**Purpose**: Reads a stored pending phone-number claim and turns it into a trusted shape the rest of the file can use. If the stored value is missing or malformed, it quietly treats it as no claim instead of blocking all incoming iMessages.

**Data flow**: It receives an unknown stored object. If the object is empty, it returns nothing. If it matches the expected pending-claim fields, it returns a PendingClaim. If validation fails, it records a log note and returns nothing.

**Call relations**: The proof step calls this when a sender is trying to confirm their phone number. It supplies the opt-in code and assigned line needed to finish the connection, but refuses unreadable stored data so the message listener can keep running.

*Call graph*: called by 1 (_prove); 1 external calls (log).


##### `MessageStreamDisconnected.__init__`  (lines 88–91)

```
def __init__(self, cursor: int | None, error: Exception) -> None
```

**Purpose**: Wraps a provider-stream failure together with the last known message position. This gives the listener enough information to reconnect without losing track of where it was.

**Data flow**: It receives a cursor and the original error. It stores both on the exception object and uses the error text as the exception message.

**Call relations**: The connected-stream loop creates this when catch-up, live reading, or event processing fails because of an outside provider problem. The outer listener catches it, decides where to resume, invalidates the provider, and waits before reconnecting.

*Call graph*: called by 1 (_consume_connected).


##### `contact_card`  (lines 104–116)

```
def contact_card(assigned_phone_number: str) -> bytes
```

**Purpose**: Builds a small digital contact card for the assigned UFO phone line. Members can save it so iMessage recognizes the sender and is less likely to show a junk-report banner.

**Data flow**: It receives the assigned phone number. It places that number into a vCard text format and returns the encoded bytes ready to upload as a file attachment.

**Call relations**: The contact-card sending step calls this after a phone number has been successfully proved. Its output is handed to the provider as the attachment body.

*Call graph*: called by 1 (_send_contact_card).


##### `claim_key`  (lines 119–120)

```
def claim_key(member_id: UUID, phone_number: str) -> str
```

**Purpose**: Creates a private storage key for one member and one phone number during the opt-in process. It hides the raw pair behind a hash so the key is stable but not plain to read.

**Data flow**: It receives a member ID and phone number. It combines them, hashes the combined text, adds the iMessage claim prefix, and returns the final store key.

**Call relations**: The proof step uses this key to find or delete the pending claim in the scoped store. That lets the code match an incoming verification text to the correct member and phone number.

*Call graph*: called by 1 (_prove); 1 external calls (sha256).


##### `queue_key`  (lines 123–124)

```
def queue_key(conversation_id: str, *, direct: bool) -> str
```

**Purpose**: Makes a compact, reversible label for an iMessage conversation. UFO uses this label to remember whether a conversation is a direct message or a group chat.

**Data flow**: It receives an iMessage conversation ID and a direct-or-group flag. It writes them as a small JSON array string and returns that string.

**Call relations**: When an incoming message is admitted, this function creates the queue key passed into UFO conversation lookup. Later, outbound functions decode that same kind of key to send replies to the correct iMessage chat.

*Call graph*: called by 1 (_admit_message); 1 external calls (dumps).


##### `conversation_from_queue`  (lines 127–135)

```
def conversation_from_queue(queue: str) -> ConversationAddress
```

**Purpose**: Turns a stored queue key back into the iMessage conversation address it represents. It protects outbound sending from malformed queue keys.

**Data flow**: It receives a queue-key string. It parses the JSON, checks that it describes either a direct or group conversation with a non-empty ID, and returns a ConversationAddress. If the key does not match that shape, it raises an error.

**Call relations**: Outgoing post, speak, and attach operations call this before contacting the provider. It tells them which iMessage conversation ID to use and, for final posts, whether direct-message-only actions are allowed.

*Call graph*: called by 3 (attach, post, speak); 2 external calls (__init__, loads).


##### `_attachment_content`  (lines 138–139)

```
async def _attachment_content(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps a block of attachment bytes as an async stream. This lets workspace file writing receive data in the streaming style it expects, even when all bytes are already in memory.

**Data flow**: It receives bytes. It yields those bytes once and then finishes.

**Call relations**: Attachment downloading calls this after it has fetched an iMessage attachment. The resulting stream is passed into the workspace file writer.

*Call graph*: called by 1 (_downloaded_files).


##### `ImessageSurface.listen`  (lines 146–170)

```
async def listen(self, context: SurfaceListenerContext) -> None
```

**Purpose**: Runs the iMessage listener forever. It connects to the provider, resumes from the saved stream position, and keeps trying again when the provider disconnects.

**Data flow**: It receives a listener context with public URL, cursor storage, and address routing tools. It creates a provider, reads the saved cursor, passes control to the connected consumer, and updates the cursor choice after failures. If no provider is configured, it logs that and waits indefinitely.

**Call relations**: This is the top-level listening loop for the surface. It repeatedly calls _consume_connected; when that reports a disconnection, listen clears bad cursors if needed, invalidates the provider, logs the problem, sleeps briefly, and starts over.

*Call graph*: calls 3 internal fn (clear_cursor, cursor, _consume_connected); 3 external calls (Event, sleep, log).


##### `ImessageSurface._consume_connected`  (lines 172–217)

```
async def _consume_connected(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> None
```

**Purpose**: Coordinates one healthy connection to the provider. It catches up missed messages, then processes live messages in order until the stream fails or ends.

**Data flow**: It receives the listener context, provider, installation ID, and starting cursor. It starts a background live-message pump, waits until the pump is ready, optionally catches up old events, then reads queued live frames. New events are processed and the in-memory cursor advances; external failures are wrapped as stream disconnections.

**Call relations**: listen calls this for each connection attempt. It starts _pump_live, may call _catch_up, and calls _process_event for each usable live message. If the provider has an outside error, it raises MessageStreamDisconnected so listen can reconnect cleanly.

*Call graph*: calls 5 internal fn (external_error, _catch_up, _process_event, _pump_live, __init__); called by 1 (listen); 4 external calls (Event, Queue, create_task, gather).


##### `ImessageSurface._pump_live`  (lines 219–237)

```
async def _pump_live(self, provider: MessageProvider, ready: asyncio.Event, frames: asyncio.Queue[LiveFrame | LiveFailure]) -> None
```

**Purpose**: Feeds live provider events into a queue that the main connected loop can read safely. It also turns provider failures into queue items instead of letting them vanish in a background task.

**Data flow**: It receives a provider, a readiness event, and a queue. As provider.subscribe produces frames, it wraps them as LiveFrame objects and puts them on the queue. If an error happens, it puts a LiveFailure. If the stream ends normally, it also reports that as a failure-like signal. It always marks readiness before leaving.

**Call relations**: _consume_connected starts this as a background task. The pump hands frames and failures back through the queue, while _consume_connected decides whether each item should be processed, skipped, or treated as a disconnection.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (_consume_connected); 4 external calls (__init__, __init__, __init__, set).


##### `ImessageSurface._catch_up`  (lines 239–258)

```
async def _catch_up(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> int
```

**Purpose**: Processes missed provider events between the saved cursor and the current head of the stream. This prevents messages from being lost during downtime or reconnects.

**Data flow**: It receives the context, provider, installation ID, and old cursor. It asks the provider for catch-up frames, processes each message-bearing frame, tracks the newest stream position it sees, saves that position, and returns it.

**Call relations**: _consume_connected calls this before it starts accepting live frames when a saved cursor exists. It hands each real event to _process_event, then stores the final cursor so future reconnects start in the right place.

*Call graph*: calls 3 internal fn (store_cursor, catch_up, _process_event); called by 1 (_consume_connected).


##### `ImessageSurface._process_event`  (lines 260–275)

```
async def _process_event(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, sequence: int, message: InboundMessage | None) -> None
```

**Purpose**: Turns one provider event into a possible UFO message admission, then records that the event position has been consumed. Messages from unclaimed phone numbers are skipped but still advance the stream.

**Data flow**: It receives a sequence number and maybe an inbound message. If there is a message, it asks the context whether the sender maps to a workspace. If so, it passes the message to _admit_message. Finally, it stores the sequence number as the new cursor.

**Call relations**: Both catch-up and live-consume paths call this for individual events. It is the checkpoint between raw provider events and workspace-aware message admission.

*Call graph*: calls 3 internal fn (addressed, store_cursor, _admit_message); called by 2 (_catch_up, _consume_connected).


##### `ImessageSurface._admit_message`  (lines 277–321)

```
async def _admit_message(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage) -> None
```

**Purpose**: Decides whether an inbound iMessage should become a UFO conversation turn. It checks phone ownership, proof status, group-chat relevance, attachments, and the correct audience before admitting the text.

**Data flow**: It receives a surface context, provider, and inbound message. It looks up the sender's address claim. If the claim is still being proved, it sends the message to _prove and stops. If already confirmed, it may build attachment notes, choose a direct-member or room audience, find or create the UFO conversation, save attachments, wrap the message text, and admit it as a member message.

**Call relations**: _process_event calls this after routing a sender to a workspace. This function may call _prove for verification messages, _downloaded_files for attachments, and queue_key plus conversation lookup before finally handing the message to UFO with ctx.admit.

*Call graph*: calls 7 internal fn (address_claim, admit, ambient_reply_wanted, conversation_for, _downloaded_files, _prove, queue_key); called by 1 (_process_event); 7 external calls (__init__, __init__, sha256, conversation_audience, room_audience, fence_member_message, mint_marker).


##### `ImessageSurface._prove`  (lines 323–362)

```
async def _prove(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage, claim: AddressClaim) -> None
```

**Purpose**: Handles texts sent during phone-number verification. It confirms the address when the member sends the right code, rejects expired or wrong codes with helpful replies, and honors opt-out words.

**Data flow**: It receives the context, provider, inbound message, and address claim. It ignores anything that is not a direct proof attempt. For expired claims, it releases the address, deletes the stored claim, and sends an expired-code reply. For opt-out text, it releases and deletes without replying. Otherwise it reads the pending claim, normalizes the typed text, compares the code, sends success or failure text, sends a contact card on success, confirms the address, and deletes the pending claim.

**Call relations**: _admit_message calls this when a sender still has an expiring claim. It uses claim_key and read_claim to find the pending record, sends provider texts for user feedback, calls _send_contact_card after success, and updates the surface context to either release or confirm the address.

*Call graph*: calls 6 internal fn (confirm_address, release_address, send_text, _send_contact_card, claim_key, read_claim); called by 1 (_admit_message); 2 external calls (__init__, now).


##### `ImessageSurface._send_contact_card`  (lines 364–385)

```
async def _send_contact_card(self, provider: MessageProvider, message: InboundMessage, assigned_phone_number: str) -> None
```

**Purpose**: Sends the UFO line's contact card after a phone number is connected. If the provider refuses the card for an outside reason, it logs the failure but does not undo the successful connection.

**Data flow**: It receives the provider, original inbound message, and assigned phone number. It builds vCard bytes, uploads them as an attachment to the same iMessage conversation, and uses the inbound message ID to make the send idempotent. If the upload fails because of a provider-side issue, it logs the provider error code.

**Call relations**: _prove calls this only after the verification code is accepted. It delegates byte creation to contact_card and message delivery to the provider.

*Call graph*: calls 4 internal fn (error_code, external_error, send_attachment, contact_card); called by 1 (_prove); 1 external calls (log).


##### `ImessageSurface._downloaded_files`  (lines 387–436)

```
async def _downloaded_files(self, ctx: SurfaceContext, provider: MessageProvider, conversation_id: UUID, attachments: tuple[MessageAttachment, ...]) -> str
```

**Purpose**: Downloads inbound iMessage attachments into the UFO workspace when they are small enough. It also produces a plain-text note explaining which files were saved, skipped as too large, or unavailable.

**Data flow**: It receives the context, provider, UFO conversation ID, and attachment list. For each attachment, it chooses a safe unique inbox filename, skips files above the size limit, streams download chunks into memory with a second size check, writes successful files under the iMessage inbox folder, and records status notes. It returns the notes as text.

**Call relations**: _admit_message calls this before admitting a message that has attachments. It uses the provider for downloads, the context for workspace file writing, and _attachment_content to present downloaded bytes as a stream.

*Call graph*: calls 4 internal fn (write_workspace_file, download_attachment, external_error, _attachment_content); called by 1 (_admit_message); 1 external calls (inbox_name).


##### `ImessageSurface.post`  (lines 438–445)

```
async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Sends the final UFO turn reply back to the matching iMessage chat. This is used when a turn has completed and there is a terminal response to deliver.

**Data flow**: It receives the surface context and a writeback object. It decodes the stored queue key to find the iMessage conversation, builds the final text, sends that text through the provider, and returns the provider's send reference.

**Call relations**: The surface runtime calls this for completed turn replies. It uses conversation_from_queue to find the destination and _terminal_text to convert UFO's rich terminal result into iMessage-friendly plain text.

*Call graph*: calls 2 internal fn (_terminal_text, conversation_from_queue).


##### `ImessageSurface.speak`  (lines 447–451)

```
async def speak(self, ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Sends a mid-turn reply to iMessage before the full UFO turn is finished. This lets the user see progress or intermediate output in the chat.

**Data flow**: It receives the context and a mid-turn reply. It decodes the queue key, sends the reply text to that iMessage conversation with the reply ID as the idempotency key, and returns the provider's send reference.

**Call relations**: The surface runtime calls this when UFO produces an early reply. It relies on conversation_from_queue for destination lookup and then hands the text directly to the provider.

*Call graph*: calls 1 internal fn (conversation_from_queue).


##### `ImessageSurface.attach`  (lines 453–466)

```
async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None
```

**Purpose**: Uploads small file artifacts from a completed UFO turn as iMessage attachments. Large files and non-file artifacts are left as links in the text reply instead.

**Data flow**: It receives the context, writeback, and an unused reply reference. It decodes the destination conversation, loops over artifacts, keeps only file artifacts within the size limit, reads each blob into bytes, and sends each one as an attachment with a stable per-artifact idempotency key.

**Call relations**: The surface runtime calls this after a final reply when artifacts may need sending. It uses conversation_from_queue to choose the chat and _artifact_bytes to safely read each workspace blob before provider upload.

*Call graph*: calls 2 internal fn (_artifact_bytes, conversation_from_queue).


##### `ImessageSurface._terminal_text`  (lines 468–498)

```
async def _terminal_text(self, ctx: SurfaceContext, writeback: Writeback, *, direct: bool) -> str
```

**Purpose**: Converts a rich UFO terminal result into a plain iMessage text body. It includes the main answer, questions, account-connection instructions, credential prompts, and links for artifacts that cannot be attached directly.

**Data flow**: It receives the context, writeback, and whether the target is a direct chat. It collects text parts from the terminal result, adds a connect URL for direct chats or a direct-message instruction for groups, adds a home/member-portal prompt for credential requests, and appends report or artifact links when needed. It joins the parts with blank lines, or falls back to a status message if there is no text.

**Call relations**: post calls this before sending a final iMessage. It calls _question_text for structured questions and asks the context for connect, home, report, or artifact URLs when the outgoing message needs links.

*Call graph*: calls 5 internal fn (artifact_link, connect_url, home_url, report_url, _question_text); called by 1 (post).


##### `ImessageSurface._question_text`  (lines 500–511)

```
def _question_text(self, writeback: Writeback) -> str
```

**Purpose**: Formats any question in a terminal reply as readable plain text. It preserves the question title, each prompt, available options, and any current answer.

**Data flow**: It receives a writeback. If there is no question, it returns an empty string. Otherwise it builds lines from the question title and question items, including option labels and chosen answers, then returns the joined text.

**Call relations**: _terminal_text calls this while building the final iMessage body. Its output becomes one section of the outgoing text if a question is present.

*Call graph*: called by 1 (_terminal_text).


##### `ImessageSurface._artifact_bytes`  (lines 513–523)

```
async def _artifact_bytes(self, ctx: SurfaceContext, blob_key: str, size_bytes: int) -> bytes
```

**Purpose**: Reads a workspace blob into memory for upload as an iMessage attachment, while enforcing the iMessage size limit and checking that the blob did not change size.

**Data flow**: It receives the context, blob key, and expected size. It rejects files that are already too large, streams chunks from blob storage into a byte buffer, rejects the file if the streamed data grows beyond the limit, verifies the final byte count matches the expected size, and returns the bytes.

**Call relations**: attach calls this for each small file artifact it wants to send through the provider. The checks here protect the provider upload path from oversized or inconsistent files.

*Call graph*: called by 1 (attach).


### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `install, request handling, turn execution, delivery`

This file is the whole Slack “surface,” meaning the adapter between Slack and the core ufo system. Without it, Slack could not safely talk to ufo: messages would not be verified, users could not be matched to members, files would not move in or out, and replies would not land in the right Slack thread.

It starts by handling installation. A workspace can connect through Slack OAuth, or by bringing its own Slack app credentials. The file stores the bot token, proves which Slack team and bot user it belongs to, and binds that Slack team to the correct ufo workspace.

For incoming Slack events, it checks Slack's signature, finds the workspace, ignores bot messages and irrelevant chatter, decides whether the agent was addressed, and turns the message into an admitted ufo turn. It also downloads Slack attachments into the workspace and adds recent Slack context when that context matters.

For outgoing answers, it formats text into Slack-safe chunks, renders questions as Slack forms, adds connect buttons, maps readable @names back to Slack mentions, posts exactly once even across retries, and uploads shared files. While a turn runs, it also keeps Slack's thread status fresh and posts occasional progress updates for long-running work. In short, this file is the translator, bouncer, courier, and status board for Slack.

#### Function details

##### `_env_signing_secret`  (lines 231–235)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from environment variables. This is the fallback secret used to prove Slack really sent a request.

**Data flow**: It reads the environment → looks for the Slack signing secret name → returns the secret string or nothing.

**Call relations**: Called when workspace-specific credentials are missing, so both workspace-bound and pre-binding verification can fall back to the deploy's Slack app secret.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 238–245)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret for an already-bound workspace request. It prefers the workspace's private credential, then falls back to the deploy secret.

**Data flow**: It receives a surface context → asks for the workspace credential → returns that secret, the environment secret, or nothing.

**Call relations**: Used by incoming event and interactive handlers before trusting a Slack request body.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 248–256)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret before the request has been bound to a workspace. This lets the router verify Slack events using the team id in the request.

**Data flow**: It receives shared auth access and a workspace id → tries that workspace's credential → falls back to the deploy secret → returns a usable secret or nothing.

**Call relations**: Used by workspace resolution so the system can verify the raw Slack request before choosing a tenant.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 259–263)

```
def slack_client_id() -> str
```

**Purpose**: Reads the Slack OAuth client id needed to build or complete an install flow.

**Data flow**: It reads the environment → returns the client id → raises an error if it is missing.

**Call relations**: Used during OAuth token exchange, where Slack requires the deploy app's client id.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 266–270)

```
def slack_client_secret() -> str
```

**Purpose**: Reads the Slack OAuth client secret needed to exchange an authorization code for a bot token.

**Data flow**: It reads the environment → returns the secret → raises an error if it is missing.

**Call relations**: Used only during the OAuth callback when completing installation.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 273–275)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the public callback URL Slack should redirect to after OAuth authorization.

**Data flow**: It receives the deploy's public base URL → appends the Slack OAuth route → returns a full redirect URL.

**Call relations**: Used by the OAuth callback so the exchange matches the URL used when authorization began.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 278–290)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the “Add to Slack” URL an owner clicks to install the app.

**Data flow**: It receives a client id, redirect URL, and sealed state → adds requested Slack scopes → returns a Slack authorization URL.

**Call relations**: Used by setup code outside this file to send owners into Slack's OAuth flow.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 294–296)

```
def __init__(self, error: str)
```

**Purpose**: Creates a clear error when Slack identity proof or OAuth install data is bad.

**Data flow**: It receives an error message → stores it on the exception → behaves like a runtime error.

**Call relations**: Raised by identity proving and OAuth exchange so callers can show or log install failures cleanly.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `bot_token_fingerprint`  (lines 313–314)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Makes a safe fingerprint of a Slack bot token. The fingerprint can be stored without storing the token itself.

**Data flow**: It receives a token → hashes it with SHA-256 → returns the hexadecimal fingerprint.

**Call relations**: Used when writing and reading Slack identity records to reject identities from old replaced tokens.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 317–329)

```
async def read_identity(blob: BlobStore, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack identity for a workspace, but only if it matches the current bot token.

**Data flow**: It receives blob storage and a bot token → loads and validates the identity blob → returns the identity or nothing.

**Call relations**: Used by install, identity resolution, event handling, and mention mapping whenever the bot user id or team id is needed.

*Call graph*: calls 3 internal fn (exists, get, bot_token_fingerprint); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 332–338)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Finds the Slack bot user's own id for the current workspace, if Slack is installed.

**Data flow**: It reads the bot token credential → reads the matching identity blob → returns the bot user id or nothing.

**Call relations**: Used through the identity seam so other extension code can know which Slack user is the bot.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_bot_token`  (lines 341–348)

```
async def _bot_token(ctx: SurfaceContext) -> str | None
```

**Purpose**: Safely reads the workspace's Slack bot token. Missing tokens are treated as an unfinished install, not as a crash.

**Data flow**: It asks the context for the bot token credential → returns it or nothing if the slot is unset.

**Call relations**: Used at the start of event and interactive handling before any Slack API calls can be made.

*Call graph*: calls 1 internal fn (credential); called by 2 (ingest, interactive).


##### `_identity`  (lines 351–355)

```
async def _identity(ctx: SurfaceContext, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the workspace Slack identity and mirrors the bot user id for hook-time use.

**Data flow**: It receives context and bot token → reads identity → if found, writes the bot user id to scoped storage → returns the identity.

**Call relations**: Used by inbound, interactive, and mention-resolution paths that need to know the installed team and bot user.

*Call graph*: calls 2 internal fn (_mirror_self_user_id, read_identity); called by 3 (_reply_mention_ids, ingest, interactive).


##### `_mirror_self_user_id`  (lines 361–377)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the bot's Slack user id into extension-scoped storage. This lets code running outside request handling still find it.

**Data flow**: It receives workspace id and bot user id → skips if already mirrored in this process → writes to scoped storage best-effort.

**Call relations**: Called after OAuth install and identity reads; failures are logged but do not block Slack request handling.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 390–396)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Gets or proves the Slack identity for a manually configured Slack app.

**Data flow**: It checks the stored identity → if missing, calls Slack auth.test → stores and returns the proved identity.

**Call relations**: Used when bring-your-own Slack app credentials need to be turned into team and bot-user metadata.

*Call graph*: calls 2 internal fn (_prove, read_identity).


##### `SlackIdentityResolver._prove`  (lines 398–423)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack to prove what team and bot user a bot token belongs to.

**Data flow**: It sends auth.test with the bot token → validates Slack's response and id shapes → returns a SlackIdentity or raises an identity error.

**Call relations**: Called by SlackIdentityResolver.resolve when no usable identity record already exists.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 429–443)

```
def _prove_identity_in_background(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Starts identity proof after an event finds a token but no identity record. It avoids doing slow install repair on the request path.

**Data flow**: It receives context and token → starts one background task per workspace → records the task until it ends.

**Call relations**: Called by _identity_unavailable so Slack can retry while proof runs.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 1 (_identity_unavailable); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 439–441)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished identity-proof task from the in-process tracking table.

**Data flow**: It receives the completed task → checks it is still the tracked one → deletes the workspace entry.

**Call relations**: Attached as a callback to the background identity proof task.


##### `_run_identity_proof`  (lines 446–452)

```
async def _run_identity_proof(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Runs the actual background identity proof and logs any failure.

**Data flow**: It receives context and token → resolves identity through SlackIdentityResolver → logs identity or unexpected errors.

**Call relations**: Created by _prove_identity_in_background when an event arrives for a not-yet-proved manual install.

*Call graph*: called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `_identity_unavailable`  (lines 460–488)

```
def _identity_unavailable(ctx: SurfaceContext, bot_token: str | None) -> Response
```

**Purpose**: Builds the HTTP response for a verified Slack request when the workspace cannot yet identify its bot.

**Data flow**: It receives context and maybe a bot token → if no token, warns and stops retries → if token exists, starts proof and asks Slack to retry.

**Call relations**: Used by event and interactive handlers when credentials or identity metadata are incomplete.

*Call graph*: calls 1 internal fn (_prove_identity_in_background); called by 2 (ingest, interactive); 2 external calls (Response, warn).


##### `signing_secret_fingerprint`  (lines 495–498)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a safe stored fingerprint of a Slack signing secret.

**Data flow**: It receives the secret → hashes it → returns a non-reversible string.

**Call relations**: Used to mark that URL verification happened with the currently valid secret.

*Call graph*: called by 2 (_mark_url_verified, verifying_fingerprint); 1 external calls (sha256).


##### `verifying_fingerprint`  (lines 501–508)

```
async def verifying_fingerprint(credentials: CredentialAccess) -> str | None
```

**Purpose**: Finds the fingerprint of the signing secret currently used by a workspace.

**Data flow**: It reads the signing-secret credential → fingerprints it → returns the fingerprint or nothing if no secret exists.

**Call relations**: Used by install_is_live to decide whether a previous verification marker is still valid.

*Call graph*: calls 2 internal fn (get, signing_secret_fingerprint); called by 1 (install_is_live).


##### `install_is_live`  (lines 511–525)

```
async def install_is_live(ext: ExtensionContext) -> bool
```

**Purpose**: Reports whether Slack is both reaching this deploy and able to receive replies from it.

**Data flow**: It checks current signing-secret proof, stored bot token, and mirrored verification marker → returns true only when all match.

**Call relations**: Used by Slack setup/status surfaces to tell owners whether Slack is connected.

*Call graph*: calls 1 internal fn (verifying_fingerprint).


##### `slack_oauth_exchange`  (lines 545–578)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Completes Slack OAuth by trading Slack's temporary code for a bot token and identity data.

**Data flow**: It receives a code and redirect URL → posts them to Slack with client credentials → validates the response → returns a SlackInstall.

**Call relations**: Called by oauth_callback after the owner returns from Slack authorization.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `slack_app_dm_url`  (lines 581–586)

```
def slack_app_dm_url(app_id: str, team_id: str) -> str
```

**Purpose**: Builds a browser URL that opens the installed Slack app's direct message.

**Data flow**: It receives app id and team id → encodes them into Slack's app redirect URL → returns the link.

**Call relations**: Used at the end of OAuth install to send the owner back to Slack.

*Call graph*: called by 1 (oauth_callback); 1 external calls (urlencode).


##### `SlackConversationSearch.run`  (lines 673–687)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations by channel text or DM participants.

**Data flow**: It lists conversations, resolves people for DMs, turns raw Slack rows into small conversation records, filters by query, and returns matches plus a truncation flag.

**Call relations**: This is the public search method; it coordinates the helper methods that list pages, read members, and shape results.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 689–708)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Reads a bounded set of Slack conversation-list pages.

**Data flow**: It repeatedly calls Slack conversations.list with a cursor → collects channel rows → returns rows and whether the page limit cut off more.

**Call relations**: Used by SlackConversationSearch.run before filtering search results.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 710–718)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds request parameters for one Slack conversations.list page.

**Data flow**: It receives an optional cursor → returns Slack API parameters with types, archive exclusion, page size, and cursor.

**Call relations**: Used only by _list to keep Slack paging requests consistent.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 720–723)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack's next-page cursor from a list response.

**Data flow**: It receives a Slack payload → looks inside response metadata → returns the cursor string or an empty string.

**Call relations**: Used by _list to decide whether another page should be fetched.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 725–753)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves who is in DMs and group DMs so searches can match people, not just channel names.

**Data flow**: It receives listed conversations → collects bounded member ids → looks up each person once → returns labels per conversation and whether it capped the work.

**Call relations**: Used by run after listing conversations and before building searchable records.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 755–762)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies one Slack conversation as public channel, private channel, group DM, or one-to-one DM.

**Data flow**: It reads Slack boolean fields → returns a simple kind string.

**Call relations**: Shared by conversation shaping, member lookup, and people resolution.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 764–778)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets member ids for one DM-style conversation.

**Data flow**: For one-to-one DMs it reads the user field → for group DMs it calls Slack members API → returns member ids.

**Call relations**: Used by _people when building searchable person labels.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 780–785)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Chooses a readable label for a Slack user.

**Data flow**: It receives resolved user facts and fallback id → returns name, email, both, or the id.

**Call relations**: Used by _people so DM search results can say who is in the conversation.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 787–804)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Turns one raw Slack conversation row into the smaller model this extension uses.

**Data flow**: It validates the row and id → extracts kind, name, purpose, topic, people, and membership → returns a SlackConversation or nothing.

**Call relations**: Used by run while preparing filterable search results.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 806–808)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Safely extracts a text value from Slack's nested purpose or topic object.

**Data flow**: It receives a possible dictionary → reads its value field → returns text or an empty string.

**Call relations**: Used by _conversation when shaping searchable channel metadata.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 989–1004)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a request really came from Slack and is not an old replay.

**Data flow**: It receives headers, raw body, signing secret, and optional clock → checks timestamp age and HMAC signature → raises if invalid.

**Call relations**: Used by workspace resolution, event ingest, and interactive handling before trusting Slack data.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 1011–1030)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw Slack request body with a size limit.

**Data flow**: It streams request chunks → stops if the body is too large → stores the raw bytes on request state → returns bytes.

**Call relations**: Used by all Slack POST routes so signature verification sees the exact original bytes.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 1033–1042)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Detects Slack's URL verification handshake and extracts the challenge string.

**Data flow**: It parses the raw JSON body → checks for type url_verification → returns the challenge or nothing.

**Call relations**: Used by request routing and ingest to answer Slack's endpoint setup probe.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 1045–1062)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Finds the Slack team id inside an event or interactive payload before the workspace is bound.

**Data flow**: It parses JSON or form payload → reads team_id or team.id → validates its shape → returns the team id or nothing.

**Call relations**: Used by resolve_workspace to choose the workspace whose secret should verify the request.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 1065–1066)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Turns a Slack team id into the installation key used by core.

**Data flow**: It receives a team id → prefixes it as a team installation id → returns the string.

**Call relations**: Used when binding OAuth installs and resolving incoming Slack requests.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 1069–1107)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace a Slack request belongs to, before normal request handling starts.

**Data flow**: It inspects GET OAuth state, URL verification, or POST team id → verifies signatures when needed → returns a workspace id, a direct response, or nothing.

**Call relations**: This is the shared routing gate for Slack OAuth, events, and interactivity.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 1110–1113)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state belongs to Slack OAuth installation.

**Data flow**: It receives decoded credential claims → checks the payload marker and requested slot → returns true or false.

**Call relations**: Used by resolve_workspace and oauth_callback to reject unrelated sealed states.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 1116–1121)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the ufo conversation key for a Slack thread.

**Data flow**: It receives channel id, root timestamp, and whether it is a DM → returns channel-only for DMs or channel:root for channels.

**Call relations**: Used when converting Slack events and form submissions into ufo conversation keys.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `slack_message_addressed`  (lines 1124–1142)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly asking the agent to respond.

**Data flow**: It receives the event, bot user id, and DM flag → treats DMs as addressed → otherwise searches message bodies for real mentions while ignoring attribution-footers.

**Call relations**: Used by _to_inbound to separate direct requests from ambient thread chatter.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1145–1148)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts links in reply text so Slack previews can be controlled.

**Data flow**: It receives text → counts markdown links and bare URLs → returns the total.

**Call relations**: Used by slack_reply_body to turn off unfurls when many links would flood the thread.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `_slack_atomic_spans`  (lines 1151–1189)

```
def _slack_atomic_spans(text: str) -> list[tuple[int, int]]
```

**Purpose**: Finds blocks of text that should not be split, such as code fences and markdown tables.

**Data flow**: It scans text line by line → records start and end offsets for atomic spans → returns those spans.

**Call relations**: Used by slack_reply_parts before choosing where long Slack replies are cut.

*Call graph*: called by 1 (slack_reply_parts); 1 external calls (match).


##### `_slack_reply_cut`  (lines 1192–1212)

```
def _slack_reply_cut(text: str, start: int, limit: int, atomic_spans: list[tuple[int, int]]) -> int
```

**Purpose**: Chooses a safe cut point for one Slack message chunk.

**Data flow**: It receives text, start, size limit, and protected spans → looks for paragraph, line, sentence, or word boundaries → returns an offset.

**Call relations**: Used inside slack_reply_parts for each chunk of a long reply.

*Call graph*: called by 1 (slack_reply_parts); 1 external calls (finditer).


##### `slack_reply_parts`  (lines 1215–1232)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long reply text into Slack-sized pieces without breaking markdown when possible.

**Data flow**: It receives text and a limit → validates inputs → finds protected spans → returns ordered text parts.

**Call relations**: Used by reply body building, terminal posting, and mid-turn speaking.

*Call graph*: calls 2 internal fn (_slack_atomic_spans, _slack_reply_cut); called by 3 (post, slack_reply_body, speak).


##### `slack_reply_body`  (lines 1235–1304)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for a Slack chat.postMessage call.

**Data flow**: It receives channel, thread, text, metadata, optional blocks and actions → formats Slack Block Kit or plain fallback → returns encoded JSON bytes.

**Call relations**: Used whenever this file posts replies or progress messages to Slack.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 3 (_say, post, speak); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1307–1308)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a simple Slack markdown section block, trimmed to Slack's limit.

**Data flow**: It receives text → wraps it in a Block Kit section structure → returns the block dictionary.

**Call relations**: Used by ask rendering helpers for titles and prose fallback.

*Call graph*: called by 2 (_ask_prose, slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1311–1355)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders a ufo question as a Slack form when Slack can express it.

**Data flow**: It receives an optional ask → builds title, input controls, and submit button, or prose fallback → returns blocks or nothing.

**Call relations**: Used by post when a terminal turn asks the user for more information.

*Call graph*: calls 3 internal fn (_ask_control, _ask_prose, _mrkdwn_section); called by 1 (post).


##### `_ask_control`  (lines 1358–1410)

```
def _ask_control(index: int, ask: AskQuestion) -> dict[str, object] | None
```

**Purpose**: Builds the Slack input block for one question, or says it cannot be represented.

**Data flow**: It receives the question and index → chooses text box, radio buttons, or checkboxes → returns a block or nothing.

**Call relations**: Used by slack_ask_blocks while deciding whether the whole ask can be interactive.

*Call graph*: calls 1 internal fn (_ask_option); called by 1 (slack_ask_blocks).


##### `_ask_option`  (lines 1413–1423)

```
def _ask_option(option: QuestionOption) -> dict[str, object]
```

**Purpose**: Turns one answer option into Slack's option format.

**Data flow**: It receives a question option → copies label as text and value, adds a trimmed description if present → returns a dictionary.

**Call relations**: Used by _ask_control when building radio buttons or checkboxes.

*Call graph*: called by 1 (_ask_control).


##### `_ask_prose`  (lines 1426–1434)

```
def _ask_prose(ask: AskQuestion) -> dict[str, object]
```

**Purpose**: Renders one question as plain Slack-readable prose.

**Data flow**: It receives a question → writes its header, prompt, options, and multi-select note → returns a markdown section block.

**Call relations**: Used by slack_ask_blocks when a full interactive form is not possible.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (slack_ask_blocks).


##### `slack_connect_blocks`  (lines 1437–1463)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Builds a Slack button that lets a member complete a private connection request.

**Data flow**: It receives an optional connect request and turn id → returns an actions block with a Connect button, or nothing.

**Call relations**: Used by post when the terminal reply asks the member to authorize another provider.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1466–1470)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required non-empty string from a Slack payload.

**Data flow**: It receives a mapping and field name → returns the string → raises if missing or invalid.

**Call relations**: Used by event and interaction parsing where channel, timestamp, or user ids are mandatory.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `_inbound_files`  (lines 1473–1485)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable file references from a Slack message event.

**Data flow**: It receives an event → scans up to the configured file limit → skips hidden or tombstone files → returns names and private URLs.

**Call relations**: Used when parsing inbound events and when fetching files from app_mention events that omitted them.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1488–1519)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Looks up files attached to a specific Slack message when the event did not include them directly.

**Data flow**: It receives token, channel, timestamp, and optional root → queries the exact message range → extracts inbound files → returns them or empty.

**Call relations**: Used by _to_inbound for app_mention events where Slack may require a follow-up read.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1527–1602)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Finishes Slack OAuth installation after the owner approves the app.

**Data flow**: It validates state and code → exchanges the code → binds the Slack team → stores token and identity → returns a success or error page.

**Call relations**: Called by the Slack OAuth route after resolve_workspace binds the request to the installing workspace.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, slack_app_dm_url, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 3 external calls (__init__, __init__, callback_page).


##### `_mark_url_verified`  (lines 1608–1626)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this deploy with the current signing secret.

**Data flow**: It fingerprints the secret → writes a marker blob → mirrors the fingerprint to scoped storage best-effort.

**Call relations**: Called by event and interactive handlers after signature verification succeeds.

*Call graph*: calls 2 internal fn (mirror_url_verified, signing_secret_fingerprint); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `mirror_url_verified`  (lines 1629–1644)

```
async def mirror_url_verified(workspace_id: UUID, fingerprint: str) -> None
```

**Purpose**: Copies URL-verification proof into the storage area available to extension status checks.

**Data flow**: It receives workspace id and fingerprint → skips duplicate writes in-process → writes the fingerprint to scoped storage.

**Call relations**: Called by _mark_url_verified so install_is_live can read the proof later.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (__init__).


##### `ingest`  (lines 1647–1691)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Events API requests and admits member messages into ufo when appropriate.

**Data flow**: It reads and verifies the raw request → handles URL verification → checks install identity → parses an inbound message → admits it or starts ambient decision work → returns Slack an acknowledgement.

**Call relations**: This is the main inbound event route; it hands off to parsing, ambient gating, admission, file download, and follower arming helpers.

*Call graph*: calls 12 internal fn (_admit_inbound, _bot_token, _ctx_signing_secret, _decide_ambient_in_background, _folds_into_live_turn, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_inbound (+2 more)); 3 external calls (loads, JSONResponse, Response).


##### `_folds_into_live_turn`  (lines 1694–1732)

```
async def _folds_into_live_turn(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> bool
```

**Purpose**: Checks whether an unmentioned thread reply should be absorbed by an already-running turn.

**Data flow**: It receives an inbound message → finds a live absorbing turn → resolves the speaker and access → returns true only if admission would fold it in.

**Call relations**: Called by ingest before asking the ambient reply model, because live-turn messages must not be silently dropped.

*Call graph*: calls 4 internal fn (absorbing_turn, member_has_access, _resolve_member, _slack_user); called by 1 (ingest); 1 external calls (log).


##### `_admit_inbound`  (lines 1735–1786)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Turns a verified Slack message into a ufo turn.

**Data flow**: It gathers sender, context, permalink, and names → resolves member and conversation → downloads files → builds the admitted body → admits it → anchors DM threads and starts status followers.

**Call relations**: Called directly by ingest for clear requests and by ambient-decision work when the model says to reply.

*Call graph*: calls 14 internal fn (admit, attach_member_files, conversation_for, retitle_conversation, _ambient_context, _anchor_dm_thread, _arm_followers, _download_files, _mirror_thread, _resolve_member (+4 more)); called by 2 (_run_ambient_decision, ingest); 10 external calls (__init__, __init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1792–1817)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts a background task to decide whether unmentioned thread chatter deserves an agent reply.

**Data flow**: It receives the inbound message → starts one task per message id → keeps a strong reference until done.

**Call relations**: Called by ingest after acknowledging Slack so slow model decisions do not miss Slack's response deadline.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1813–1815)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a completed ambient-decision task from the tracking dictionary.

**Data flow**: It receives the finished task → verifies it is still current for that message id → deletes it.

**Call relations**: Attached to background ambient-decision tasks created by _decide_ambient_in_background.


##### `_run_ambient_decision`  (lines 1820–1835)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the ambient reply decision and admits the message if a reply is wanted.

**Data flow**: It asks whether a reply is wanted → if yes, calls admission → logs failures that happen after Slack was already acknowledged.

**Call relations**: Created by _decide_ambient_in_background and hands positive decisions to _admit_inbound.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1838–1845)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages written by users from another Slack organization in shared channels.

**Data flow**: It compares source/user team fields with the installed team id → returns true for external authors.

**Call relations**: Used by _to_inbound to skip Slack Connect bystanders before member resolution.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1861–1897)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines who should be allowed to see a Slack-origin conversation and what label to show for it.

**Data flow**: It reads event channel type and sometimes Slack channel info → returns an audience and optional label → raises if privacy cannot be known.

**Call relations**: Used by _to_inbound while shaping an admitted Slack message.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1900–1950)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Converts a raw Slack event payload into the smaller Inbound object used by admission.

**Data flow**: It filters unsupported events → checks speaker, addressing, thread, audience, files, and conversation participation → returns Inbound or nothing.

**Call relations**: Called by ingest after request verification and identity lookup.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1953–1964)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has an admitted ufo turn.

**Data flow**: It looks up the conversation by queue key → checks for a latest turn → returns the conversation id only if participation is real.

**Call relations**: Used by _to_inbound to decide whether unmentioned thread replies can belong to an existing conversation.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1967–1999)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches a Slack user's display facts, including confirmed email when available.

**Data flow**: It calls Slack users.info → validates the user/profile fields → returns name, email, timezone, and team id or nothing.

**Call relations**: Used by admission, member resolution checks, conversation search, answer submit handling, and name caching.

*Call graph*: calls 1 internal fn (_slack_ok); called by 5 (_people, _name, _admit_inbound, _folds_into_live_turn, _handle_answer_submit); 2 external calls (__init__, AsyncClient).


##### `_conversation_members`  (lines 2013–2033)

```
async def _conversation_members(bot_token: str, channel: str) -> tuple[str, ...]
```

**Purpose**: Reads a bounded first page of member ids for a Slack conversation.

**Data flow**: It calls Slack conversations.members → returns valid member id strings or an empty tuple on failure.

**Call relations**: Used by SlackNames.mention_ids to limit outbound @mention mapping to people already in the conversation.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (mention_ids); 1 external calls (AsyncClient).


##### `SlackNames.of`  (lines 2054–2062)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack user and channel ids mentioned in text into readable names.

**Data flow**: It scans texts and extra user ids → builds a lookup set → returns id-to-name mappings from cache or Slack.

**Call relations**: Used when admitting inbound messages and rendering ambient context so Slack markup becomes readable.

*Call graph*: calls 1 internal fn (_resolved); 2 external calls (mentioned_channels, mentioned_users).


##### `SlackNames.mention_ids`  (lines 2064–2078)

```
async def mention_ids(self, channel: str, identity: SlackIdentity) -> dict[str, str]
```

**Purpose**: Builds the safe map from written @names to Slack user ids for one conversation.

**Data flow**: It reads the conversation roster → resolves names → drops the bot and external-team users → returns a mention index.

**Call relations**: Used before posting replies so agent-written names can notify only people already in the Slack thread.

*Call graph*: calls 2 internal fn (_resolved, _conversation_members); 1 external calls (mention_index).


##### `SlackNames._resolved`  (lines 2080–2088)

```
async def _resolved(self, wanted: Mapping[str, str]) -> dict[str, _NamedId]
```

**Purpose**: Resolves requested Slack ids using the cache first and Slack API only for missing entries.

**Data flow**: It receives id-to-endpoint needs → reads remembered names → fetches a bounded missing set → stores fetched names → returns the merged map.

**Call relations**: Used by both inbound name rendering and outbound mention mapping.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); called by 2 (mention_ids, of); 1 external calls (gather).


##### `SlackNames._remembered`  (lines 2090–2111)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, _NamedId]
```

**Purpose**: Reads fresh cached Slack names from scoped storage.

**Data flow**: It receives ids → bulk-reads cache rows → drops malformed or stale rows → returns cached _NamedId entries.

**Call relations**: Used by _resolved before making Slack API calls.

*Call graph*: called by 1 (_resolved); 3 external calls (__init__, __init__, now).


##### `SlackNames._name`  (lines 2113–2130)

```
async def _name(self, id_: str, url: str) -> _NamedId | None
```

**Purpose**: Fetches and cleans one Slack user or channel name.

**Data flow**: It receives an id and API endpoint → calls the right Slack reader → strips unsafe delimiters and length → returns a _NamedId or nothing.

**Call relations**: Used by _resolved for cache misses.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (_resolved); 1 external calls (__init__).


##### `SlackNames._remember`  (lines 2132–2142)

```
async def _remember(self, names: Mapping[str, _NamedId]) -> None
```

**Purpose**: Writes freshly resolved Slack names into scoped storage.

**Data flow**: It receives id-to-name entries → stamps the current time → stores each cache row best-effort.

**Call relations**: Used by _resolved after Slack lookups complete.

*Call graph*: called by 1 (_resolved); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 2145–2165)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Asks Slack for the official link to a message.

**Data flow**: It receives token, channel, and timestamp → calls chat.getPermalink → returns the permalink or nothing.

**Call relations**: Used when admitting messages and answer submits so ufo context can point back to the Slack source.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 2168–2185)

```
def _turn_context(sender: SlackUser | None, source: str | None, question: str | None=None) -> TurnContext
```

**Purpose**: Builds the context metadata attached to an admitted turn.

**Data flow**: It receives sender info, source link, and optional question → formats sender and timezone → returns a TurnContext, dropping invalid timezones.

**Call relations**: Used by message admission and answer-submit admission.

*Call graph*: called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (__init__).


##### `_resolve_member`  (lines 2188–2206)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member, joining by confirmed email when needed.

**Data flow**: It checks existing linked member → if absent, uses Slack-confirmed email to join/link → returns member id or nothing; DMs fail loudly if user data is unavailable.

**Call relations**: Used before admitting messages, folding live replies, and admitting form answers.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 3 (_admit_inbound, _folds_into_live_turn, _handle_answer_submit); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 2209–2234)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether unmentioned Slack thread chatter should start a new agent turn.

**Data flow**: It reads recent thread history → if no trustworthy history, replies by default → otherwise asks core's ambient decision → returns true or false.

**Call relations**: Called by _run_ambient_decision after ingest has acknowledged Slack.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 2237–2259)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Builds the recent Slack-thread history used by the ambient reply decision.

**Data flow**: It fetches the thread tail → converts valid member and own bot messages to AmbientMessage entries → returns the newest bounded messages.

**Call relations**: Used only by _ambient_reply_wanted.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 2262–2308)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Fetches a Slack thread's messages before a given timestamp, walking pages so the tail is trustworthy.

**Data flow**: It calls conversations.replies with paging → collects messages until done or page limit → returns messages or nothing if the read is unreliable.

**Call relations**: Used for ambient decision history and unseen-message context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 2311–2331)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Converts one fetched Slack message into an ambient-history entry if it is relevant.

**Data flow**: It checks shape, user, timestamp, text, bot status, and ordering → returns timestamp plus AmbientMessage or nothing.

**Call relations**: Used by _ambient_history while filtering Slack's raw thread data.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 2334–2354)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds extra background text for a turn when Slack has relevant messages outside the ufo transcript.

**Data flow**: It receives an inbound message → returns no context for DMs → otherwise fetches founding or later/unseen context and concatenates it.

**Call relations**: Called during _admit_inbound before the member message is fenced and admitted.

*Call graph*: calls 3 internal fn (_founding_context, _later_channel_context, _unseen_tail); called by 1 (_admit_inbound); 1 external calls (gather).


##### `_founding_context`  (lines 2357–2402)

```
async def _founding_context(bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Gets background messages for the Slack message that first brings the agent into a channel conversation.

**Data flow**: It chooses thread replies or recent channel history → fetches one bounded page → renders it as an ambient digest.

**Call relations**: Used by _ambient_context for first admitted messages in channel conversations.

*Call graph*: calls 3 internal fn (_digest_names, _slack_ok, ambient_digest); called by 1 (_ambient_context); 1 external calls (AsyncClient).


##### `_later_channel_context`  (lines 2405–2456)

```
async def _later_channel_context(bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Gets nearby channel messages posted beside an already-running thread.

**Data flow**: It fetches channel history between root and current message → keeps the latest bounded items → renders an ambient digest.

**Call relations**: Used by _ambient_context for later turns in a channel thread.

*Call graph*: calls 4 internal fn (_digest_names, _latest_between, _slack_ok, ambient_digest); called by 1 (_ambient_context); 1 external calls (AsyncClient).


##### `_latest_between`  (lines 2459–2479)

```
def _latest_between(messages: Sequence[object], after_ts: str, before_ts: str) -> list[object]
```

**Purpose**: Keeps the newest messages inside a timestamp window.

**Data flow**: It receives raw Slack messages and timestamp bounds → parses timestamps → sorts valid entries → returns the newest limited messages oldest-first.

**Call relations**: Used by _later_channel_context after Slack history returns a page.

*Call graph*: called by 1 (_later_channel_context).


##### `_digest_names`  (lines 2482–2491)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves names needed to make an ambient digest readable.

**Data flow**: It gathers message text and author ids → asks SlackNames for names → returns an id-to-name map.

**Call relations**: Used before rendering founding, later-channel, and unseen-tail context.

*Call graph*: called by 3 (_founding_context, _later_channel_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2494–2540)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Finds recent thread messages that Slack users saw but no ufo turn transcript contains.

**Data flow**: It fetches the thread tail → walks backward until an admitted message is found → renders the skipped messages as ambient context.

**Call relations**: Used by _ambient_context for later admitted messages in an existing Slack thread.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2543–2611)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders Slack background messages into safe, bounded prompt context.

**Data flow**: It filters out bots, empty text, direct mentions, and own bot messages → names users and markup → trims old content if too long → returns a tagged context block.

**Call relations**: Used by all ambient-context fetchers before admission.

*Call graph*: called by 3 (_founding_context, _later_channel_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2614–2616)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks that a file download URL belongs to Slack before sending the bot token.

**Data flow**: It parses the URL hostname → accepts slack.com or Slack subdomains → returns true or false.

**Call relations**: Used by _stream_download as a safety gate before authenticated downloads.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2619–2637)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private Slack file download without loading the whole file into memory.

**Data flow**: It verifies the host → downloads with bot authorization in chunks → enforces total size → yields bytes or raises if too large.

**Call relations**: Used by _download_files when storing inbound Slack attachments.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2651–2672)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Copies Slack attachments into the ufo workspace as turn artifacts.

**Data flow**: It receives inbound file references → gives each a workspace-safe name → streams each into storage → delivers attachments and records skipped oversized files.

**Call relations**: Called by _admit_inbound before the message body is admitted.

*Call graph*: calls 3 internal fn (deliver_attachment, store_inbound_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2675–2684)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates a short note telling the agent which Slack files were saved or skipped.

**Data flow**: It receives download results → lists saved workspace paths and oversized skipped names → returns note text.

**Call relations**: Used by _admit_inbound as part of the fenced member message.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2696–2701)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored Slack-thread mirror row into a MirroredThread object.

**Data flow**: It receives a stored JSON value → accepts old string rows or model-shaped rows → returns a MirroredThread.

**Call relations**: Used when hook-time followers and mid-turn comments need to recover the Slack thread.


##### `MirroredThread.anchor`  (lines 2703–2707)

```
def anchor(self) -> str | None
```

**Purpose**: Finds the Slack timestamp that status and progress should attach to.

**Data flow**: It reads the root timestamp from the queue key or the stored DM message timestamp → returns an anchor or nothing.

**Call relations**: Used by status tracking and progress posting to choose the Slack thread parent.

*Call graph*: called by 1 (_track_status).


##### `_thread_mirror_key`  (lines 2710–2711)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the scoped-store key for a conversation's Slack thread mirror.

**Data flow**: It receives a conversation id → prefixes it with the Slack thread namespace → returns the key.

**Call relations**: Used when writing and reading durable Slack-thread mirror rows.

*Call graph*: called by 3 (_mirror_thread, follow_turn, speak).


##### `_mirror_thread`  (lines 2714–2722)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Stores which Slack thread belongs to a ufo conversation.

**Data flow**: It receives a conversation id and MirroredThread → serializes the thread → writes it to scoped storage.

**Call relations**: Called before admitting inbound messages and form answers so later hooks know where to post status.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, _handle_answer_submit); 2 external calls (__init__, model_dump).


##### `_dm_anchor_key`  (lines 2725–2731)

```
def _dm_anchor_key(turn_id: UUID, message_ref: UUID | None=None) -> str
```

**Purpose**: Builds the storage key that maps a DM turn or absorbed message to its Slack parent message.

**Data flow**: It receives a turn id and optional message ref → creates a base or nested DM-anchor key → returns it.

**Call relations**: Used for recording, reading, and later cleaning DM reply anchors.

*Call graph*: called by 3 (_anchor_dm_thread, _drop_turn_reply_records, _reply_thread).


##### `_anchor_dm_thread`  (lines 2734–2741)

```
async def _anchor_dm_thread(admitted: Admitted, message_ts: str) -> None
```

**Purpose**: Records which Slack DM message a ufo message should answer under.

**Data flow**: It receives admission result and Slack timestamp → stores the timestamp under the turn or arrival-specific DM anchor key.

**Call relations**: Called after admitting DM messages and DM form answers.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (__init__).


##### `_reply_thread`  (lines 2744–2759)

```
async def _reply_thread(queue_key: str, turn_id: UUID, message_ref: UUID | None=None) -> str | None
```

**Purpose**: Finds the Slack thread timestamp where a reply should be posted.

**Data flow**: It receives queue key, turn id, and optional message ref → uses channel root if present → otherwise reads DM anchors → returns thread timestamp or nothing.

**Call relations**: Used by terminal replies, mid-turn replies, and file attachment sharing.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 3 (attach, post, speak); 1 external calls (__init__).


##### `FollowerContext.workspace_id`  (lines 2770–2770)

```
def workspace_id(self) -> UUID
```

**Purpose**: Defines that follower code needs access to the current workspace id.

**Data flow**: A concrete context supplies this property → follower code reads it → uses it in thread identity and footer links.

**Call relations**: Implemented by _HookFollowerContext and already present on SurfaceContext-like objects.


##### `FollowerContext.public_base_url`  (lines 2773–2773)

```
def public_base_url(self) -> str | None
```

**Purpose**: Defines that follower code may need the deploy's public URL.

**Data flow**: A concrete context supplies a URL or nothing → footer code uses it to build web and debug links.

**Call relations**: Read by _slack_footer through contexts that satisfy FollowerContext.


##### `FollowerContext.credential`  (lines 2775–2775)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Defines how follower code reads credentials such as the Slack bot token.

**Data flow**: A concrete context receives a slot name → returns the secret value.

**Call relations**: Used by status and progress followers before calling Slack APIs.


##### `FollowerContext.tail`  (lines 2777–2779)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how follower code reads live frames from a running turn.

**Data flow**: A concrete context receives a turn id and cursor → returns an async stream of live frames.

**Call relations**: Used by ThreadStatus and ThreadProgress to react to turn activity.


##### `FollowerContext.turn_is_terminal`  (lines 2781–2781)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Defines how progress reporting checks whether a turn has already finished.

**Data flow**: A concrete context receives a turn id → returns whether it is terminal.

**Call relations**: Used by ThreadProgress before posting delayed progress updates.


##### `FollowerContext.is_operator_workspace`  (lines 2783–2783)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Defines how footer code knows whether operator-only details are allowed.

**Data flow**: A concrete context answers true or false → footer code includes or omits accounting/debug details.

**Call relations**: Called by _slack_footer.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2840–2841)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Identifies the Slack thread whose status this follower writes.

**Data flow**: It combines workspace id, channel id, and thread timestamp → returns a tuple key.

**Call relations**: Used by status tracking tables to ensure only the newest turn writes a thread's status.


##### `ThreadStatus.run`  (lines 2843–2862)

```
async def run(self) -> None
```

**Purpose**: Runs the lifecycle of one Slack thread status line for a turn.

**Data flow**: It reads the bot token → writes an initial “Thinking” status → follows live frames → clears status when finished or on failure.

**Call relations**: Started by _run_status after _track_status creates a follower task.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2864–2904)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one status line to Slack, if this turn still owns the thread status.

**Data flow**: It receives Slack client, token, and text → posts assistant.threads.setStatus → returns whether Slack accepted it.

**Call relations**: Used by run, _follow, and _clear for every status update.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2906–2915)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears Slack's thread status when no sibling turn still needs it.

**Data flow**: It checks other live status followers on the same thread → if none, writes an empty status.

**Call relations**: Called by ThreadStatus.run after following ends or after contained failures.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2917–2971)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Watches live turn frames and turns them into short Slack status text.

**Data flow**: It tails frames and blanking events → maps activity, absorbed messages, resume, and text streaming to status phrases → refreshes or updates Slack.

**Call relations**: Called by ThreadStatus.run after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2981–2988)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers when a progress post blanks Slack's native status.

**Data flow**: It receives workspace, channel, and thread timestamp → finds matching local status followers → sets their wake event.

**Call relations**: Called by ThreadProgress._say after posting into the same thread.

*Call graph*: called by 1 (_say).


##### `_track_status`  (lines 2991–3012)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts one Slack status follower task for a turn in this process.

**Data flow**: It receives context, turn id, and mirrored thread → finds an anchor → records writer ownership → starts _run_status unless already running.

**Call relations**: Called by _arm_followers from admission and hook-time execution.

*Call graph*: calls 2 internal fn (anchor, _run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 3015–3043)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Supervises a ThreadStatus task and repairs writer bookkeeping when it ends.

**Data flow**: It runs the status follower → logs fatal failures → removes task/status records → hands writer ownership back to an older live sibling or clears it.

**Call relations**: Created by _track_status.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 3055–3059)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the progress-report timing settings.

**Data flow**: It checks base and cap values after construction → raises if base is not positive or cap is below base.

**Call relations**: Runs automatically when _track_progress creates a ProgressCadence.


##### `ProgressCadence.intervals`  (lines 3061–3072)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Produces the wait intervals for long-running progress posts.

**Data flow**: It starts at the base wait → doubles by elapsed time until capped → yields intervals forever.

**Call relations**: Used by checkpoints_after to translate the schedule into elapsed-time checkpoints.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 3074–3083)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Finds future progress checkpoints after a turn has already been running for some time.

**Data flow**: It walks the interval schedule → skips checkpoints already reached → yields remaining elapsed-time marks.

**Call relations**: Used by ThreadProgress._follow so resumed reporters do not restart the timing ladder.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.update`  (lines 3096–3098)

```
def update(self, summary: str) -> None
```

**Purpose**: Records the latest completed activity description for progress messages.

**Data flow**: It receives a summary → clears streaming text → normalizes and trims the summary → stores it as current activity.

**Call relations**: Called by ThreadProgress._follow on activity and subagent activity frames.


##### `TurnActivity.stream`  (lines 3100–3101)

```
def stream(self, text: str) -> None
```

**Purpose**: Notes that reply text is currently streaming.

**Data flow**: It receives a text delta → appends it to the streaming list.

**Call relations**: Called by ThreadProgress._follow so progress can say the response is being prepared.


##### `TurnActivity.current_step`  (lines 3103–3107)

```
def current_step(self) -> str
```

**Purpose**: Chooses the member-facing step to report right now.

**Data flow**: It checks whether text is streaming → returns “Preparing the response” or the last activity summary.

**Call relations**: Used by TurnActivity.report.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 3109–3119)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds the text for one progress checkpoint.

**Data flow**: It gets the current step → if empty returns nothing → formats elapsed minutes or hours → returns a short progress line.

**Call relations**: Called by ThreadProgress._post when a checkpoint is due.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 3160–3163)

```
async def run(self) -> None
```

**Purpose**: Runs the long-turn progress reporter.

**Data flow**: It reads the bot token → creates a Slack client → follows turn frames and schedule.

**Call relations**: Started by _run_progress after _track_progress creates a reporter.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 3165–3168)

```
def _elapsed(self) -> float
```

**Purpose**: Measures how long the member has waited since the durable turn start.

**Data flow**: It compares current UTC time to started_at → returns elapsed seconds.

**Call relations**: Used throughout ThreadProgress._follow and resume posting.

*Call graph*: called by 2 (_follow, _post_resumed); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 3170–3223)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches live frames and posts progress when scheduled checkpoints arrive.

**Data flow**: It tracks activity, costs, resume notices, and deadlines → posts progress or resume notices when due → exits when the turn ends.

**Call relations**: Called by ThreadProgress.run.

*Call graph*: calls 3 internal fn (_elapsed, _post, _post_resumed); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 3225–3252)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one scheduled progress update if there is meaningful activity to report.

**Data flow**: It asks TurnActivity for report text → logs and skips if empty → otherwise sends it through _say.

**Call relations**: Called by _follow when a cadence checkpoint is reached.

*Call graph*: calls 2 internal fn (_say, report); called by 1 (_follow); 1 external calls (log).


##### `ThreadProgress._post_resumed`  (lines 3254–3265)

```
async def _post_resumed(self, client: httpx.AsyncClient, bot_token: str, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts a one-time notice that a turn resumed after a restart.

**Data flow**: It builds the resume notice → sends it through _say with current elapsed time and optional footer.

**Call relations**: Called by _follow after a grace period following a Resumed frame.

*Call graph*: calls 2 internal fn (_elapsed, _say); called by 1 (_follow).


##### `ThreadProgress._say`  (lines 3267–3307)

```
async def _say(self, client: httpx.AsyncClient, bot_token: str, text: str, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Sends a progress message into the Slack thread.

**Data flow**: It finds channel and thread anchor → optionally builds footer → posts Slack message → logs success or failure and restamps status.

**Call relations**: Used by both scheduled progress posts and resume notices.

*Call graph*: calls 4 internal fn (_footer, _restamp_thread_status, _slack_ok, slack_reply_body); called by 2 (_post, _post_resumed); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 3309–3326)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the footer for the first progress message.

**Data flow**: It formats cost and token data if available → delegates to _slack_footer → returns footer text or nothing.

**Call relations**: Called by _say when a progress post may be the turn's first Slack message.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_say).


##### `_track_progress`  (lines 3332–3359)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, thread: MirroredThread, started_at: datetime) -> None
```

**Purpose**: Starts one progress reporter for a turn in this process.

**Data flow**: It receives context, turn, conversation, thread, and start time → builds cadence and ThreadProgress → starts _run_progress unless already running.

**Call relations**: Called by _arm_followers only when durable started_at is known.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 4 external calls (__init__, __init__, create_task, now).


##### `_run_progress`  (lines 3362–3377)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Supervises a ThreadProgress task and logs if reporting is abandoned.

**Data flow**: It runs the reporter → logs unexpected failure → removes the task record when done.

**Call relations**: Created by _track_progress.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 3391–3405)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts the live Slack side-channel followers for a turn.

**Data flow**: It receives a follower context, turn facts, and mirrored thread → always tracks status → tracks progress only when start time is available.

**Call relations**: Called after admission, answer submission, and hook-time turn execution.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, _handle_answer_submit, follow_turn).


##### `_HookFollowerContext.workspace_id`  (lines 3417–3418)

```
def workspace_id(self) -> UUID
```

**Purpose**: Exposes the hook workspace id through the follower interface.

**Data flow**: It reads workspace_id from the wrapped extension context → returns it.

**Call relations**: Used by followers armed from follow_turn.


##### `_HookFollowerContext.public_base_url`  (lines 3421–3422)

```
def public_base_url(self) -> str | None
```

**Purpose**: Exposes the hook's public base URL through the follower interface.

**Data flow**: It reads public_base_url from the wrapped extension context → returns the URL or nothing.

**Call relations**: Used by footer-building code in hook-armed followers.


##### `_HookFollowerContext.credential`  (lines 3424–3425)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Lets hook-armed followers read extension credentials.

**Data flow**: It receives a slot name → asks the extension credential accessor → returns the stored value.

**Call relations**: Used by ThreadStatus and ThreadProgress when armed from follow_turn.


##### `_HookFollowerContext.tail`  (lines 3427–3430)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Lets hook-armed followers tail live frames for a turn.

**Data flow**: It receives a turn id and cursor → delegates to the extension context tail stream.

**Call relations**: Used by status and progress followers started by follow_turn.


##### `_HookFollowerContext.turn_is_terminal`  (lines 3432–3433)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets hook-armed progress reporters check whether a turn has finished.

**Data flow**: It receives a turn id → delegates to the extension context → returns terminal state.

**Call relations**: Used by ThreadProgress after checkpoint waits.


##### `_HookFollowerContext.is_operator_workspace`  (lines 3435–3436)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Lets hook-armed footer code know whether operator-only links are allowed.

**Data flow**: It delegates to the wrapped extension context → returns true or false.

**Call relations**: Used by _slack_footer through the FollowerContext interface.


##### `follow_turn`  (lines 3439–3481)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Arms Slack status and progress followers from inside the turn execution that owns the work.

**Data flow**: It checks the hook turn → reads the mirrored Slack thread with a short timeout → wraps the hook context → arms followers → always returns no blocking outcome.

**Call relations**: Called by the extension hook system on user_prompt_submit so resumed or claimed turns get live Slack feedback.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `_handle_connect_click`  (lines 3528–3542)

```
async def _handle_connect_click(ctx: SurfaceContext, interaction: ConnectClick, member_id: UUID | None) -> None
```

**Purpose**: Responds when a member clicks a Slack connect button.

**Data flow**: It receives interaction and optional member id → creates a private authorization URL or error text → posts an ephemeral Slack message.

**Call relations**: Called by interactive after parsing a ConnectClick.

*Call graph*: calls 2 internal fn (connect_url, _ephemeral_in_background); called by 1 (interactive).


##### `_handle_answer_submit`  (lines 3545–3599)

```
async def _handle_answer_submit(ctx: SurfaceContext, bot_token: str, interaction: AnswerSubmit, member_id: UUID | None) -> Response | None
```

**Purpose**: Admits a submitted Slack ask form as the next ufo turn.

**Data flow**: It finds the conversation → validates non-empty answers → resolves sender/member → builds answer text → admits idempotently → anchors/follows DM turns → queues message rewrite.

**Call relations**: Called by interactive after parsing an AnswerSubmit.

*Call graph*: calls 13 internal fn (admit, admitted_body, conversation_for, find_conversation, _anchor_dm_thread, _arm_followers, _ephemeral_in_background, _mirror_thread, _resolve_member, _rewrite_in_background (+3 more)); called by 1 (interactive); 7 external calls (__init__, __init__, gather, conversation_audience, JSONResponse, fence_member_message, mint_marker).


##### `interactive`  (lines 3602–3644)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Block Kit interactions such as form submits and connect-button clicks.

**Data flow**: It verifies the signed form payload → checks install identity → parses the interaction → routes to connect or answer handling → returns Slack an acknowledgement.

**Call relations**: This is the main Slack interactivity route.

*Call graph*: calls 11 internal fn (linked_member, _bot_token, _ctx_signing_secret, _handle_answer_submit, _handle_connect_click, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_interaction (+1 more)); 2 external calls (JSONResponse, Response).


##### `_rewrite_in_background`  (lines 3650–3653)

```
def _rewrite_in_background(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Starts a background task to rewrite an ask message after answers are admitted.

**Data flow**: It receives token and submit data → creates _run_rewrite task → tracks it until completion.

**Call relations**: Called by _handle_answer_submit so Slack is acknowledged quickly.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (_handle_answer_submit); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3656–3660)

```
async def _run_rewrite(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Runs the ask-message rewrite and logs failures.

**Data flow**: It receives token and submit → calls _replace_controls_with_answers → logs if Slack update fails.

**Call relations**: Created by _rewrite_in_background.

*Call graph*: calls 1 internal fn (_replace_controls_with_answers); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3663–3668)

```
def _ephemeral_in_background(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Starts a background task to send a private Slack-only message to one user.

**Data flow**: It receives context, channel, user, thread, and text → creates _post_ephemeral task → tracks it.

**Call relations**: Used by answer-submit validation and connect-click handling.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 2 (_handle_answer_submit, _handle_connect_click); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3671–3698)

```
async def _post_ephemeral(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Posts an ephemeral Slack message visible only to one member.

**Data flow**: It reads bot token → posts chat.postEphemeral with optional thread timestamp → logs failures.

**Call relations**: Created by _ephemeral_in_background.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_interaction`  (lines 3701–3763)

```
def _to_interaction(raw: bytes, identity: SlackIdentity) -> AnswerSubmit | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactive payload into a connect click or ask submit.

**Data flow**: It decodes form payload JSON → validates team, action, user, channel, and message → returns ConnectClick, AnswerSubmit, or nothing.

**Call relations**: Called by interactive after signature and identity checks.

*Call graph*: calls 4 internal fn (_dict_field, _string_field, _submitted_answers, slack_thread_key); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_submitted_answers`  (lines 3766–3792)

```
def _submitted_answers(blocks: tuple[Mapping[str, object], ...], state: object) -> tuple[SubmittedAnswer, ...]
```

**Purpose**: Reads all answers from a submitted Slack ask form.

**Data flow**: It scans input blocks from the original message → pairs each block with state.values → returns submitted question/answer rows.

**Call relations**: Used by _to_interaction when handling ask submit actions.

*Call graph*: calls 1 internal fn (_held_answer); called by 1 (_to_interaction); 1 external calls (__init__).


##### `_held_answer`  (lines 3795–3813)

```
def _held_answer(field: object) -> str
```

**Purpose**: Converts one Slack control state into plain answer text.

**Data flow**: It receives a state field → reads radio, checkbox, or text-input values → returns answer text or empty string.

**Call relations**: Used by _submitted_answers for each input block.

*Call graph*: calls 1 internal fn (_option_value); called by 1 (_submitted_answers); 1 external calls (get).


##### `_option_value`  (lines 3816–3820)

```
def _option_value(option: object) -> str
```

**Purpose**: Safely extracts the stored value from a Slack option.

**Data flow**: It receives a possible option object → returns its string value or empty string.

**Call relations**: Used by _held_answer for radio buttons and checkboxes.

*Call graph*: called by 1 (_held_answer).


##### `_dict_field`  (lines 3823–3827)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload.

**Data flow**: It receives a mapping and field name → returns the nested mapping → raises if missing or not a dictionary.

**Call relations**: Used by _to_interaction for user, channel, and message objects.

*Call graph*: called by 1 (_to_interaction).


##### `_replace_controls_with_answers`  (lines 3830–3863)

```
async def _replace_controls_with_answers(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Rewrites an ask message so controls become the submitted answers.

**Data flow**: It receives token and submit → copies original blocks, replacing inputs and submit button with context lines → calls Slack chat.update.

**Call relations**: Called by _run_rewrite after an answer submit wins admission.

*Call graph*: calls 2 internal fn (_context_line, _rewrite_slack_message); called by 1 (_run_rewrite).


##### `connect_message_key`  (lines 3885–3888)

```
def connect_message_key(member_id: UUID, provider: str) -> str
```

**Purpose**: Builds the storage key for remembering where a connect button was posted.

**Data flow**: It receives member id and provider → returns a namespaced key.

**Call relations**: Used by _hold_connect_message when a reply contains a connect button.

*Call graph*: called by 1 (_hold_connect_message).


##### `_hold_connect_message`  (lines 3891–3921)

```
async def _hold_connect_message(store: ScopedStore, request: ConnectRequest | None, posted: dict[str, object], channel: str, ts: str | None) -> None
```

**Purpose**: Remembers the Slack message that contains a connect button.

**Data flow**: It checks the posted blocks for a connect action → stores channel, timestamp, and thread under member/provider key.

**Call relations**: Called by post after Slack accepts a terminal reply with connect controls.

*Call graph*: calls 3 internal fn (put, _is_connect_action, connect_message_key); called by 1 (post); 1 external calls (__init__).


##### `_is_connect_action`  (lines 3924–3931)

```
def _is_connect_action(block: Mapping[str, object]) -> bool
```

**Purpose**: Detects whether a Slack block contains this extension's connect button.

**Data flow**: It receives a block → checks action elements for the connect action id → returns true or false.

**Call relations**: Used when storing and later settling connect-button messages.

*Call graph*: called by 2 (_hold_connect_message, settle_connect_message).


##### `_rewrite_slack_message`  (lines 3934–3951)

```
async def _rewrite_slack_message(bot_token: str, channel: str, ts: str, text: str, blocks: list[dict[str, object]]) -> None
```

**Purpose**: Updates a bot-authored Slack message in place.

**Data flow**: It receives token, channel, timestamp, text, and blocks → posts chat.update → raises on Slack API failure.

**Call relations**: Shared by ask-answer rewrites and connect-settled rewrites.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_replace_controls_with_answers, settle_connect_message); 2 external calls (AsyncClient, dumps).


##### `_held_connect_message`  (lines 3954–3990)

```
async def _held_connect_message(bot_token: str, held: ConnectMessage) -> Mapping[str, object] | None
```

**Purpose**: Reads the current Slack message that was remembered for a connect button.

**Data flow**: It receives token and saved message location → queries the exact channel or thread range → returns the matching message or nothing.

**Call relations**: Used by settle_connect_message before rewriting a landed connection.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (settle_connect_message); 1 external calls (AsyncClient).


##### `settle_connect_message`  (lines 3993–4019)

```
async def settle_connect_message(bot_token: str, held: ConnectMessage, provider: str, account: str) -> None
```

**Purpose**: Rewrites a connect button into a line showing the connected account.

**Data flow**: It reads the live Slack message → if the button is still present, removes it and appends a connected-account line → updates Slack.

**Call relations**: Called when a provider connection finishes, using the message saved earlier by _hold_connect_message.

*Call graph*: calls 4 internal fn (_context_line, _held_connect_message, _is_connect_action, _rewrite_slack_message).


##### `_context_line`  (lines 4022–4026)

```
def _context_line(text: str) -> dict[str, object]
```

**Purpose**: Builds a small Slack context block for status-like lines inside messages.

**Data flow**: It receives text → trims it to Slack's context limit → returns a Block Kit context block.

**Call relations**: Used by ask rewrites and connect-settled rewrites.

*Call graph*: called by 2 (_replace_controls_with_answers, settle_connect_message).


##### `_reply_text`  (lines 4029–4039)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a terminal turn reply.

**Data flow**: It receives writeback → returns failure text, cancellation reason/text, final answer, or a no-reply placeholder.

**Call relations**: Used by _reply_with_links before adding report, credential, or oversize-file links.

*Call graph*: called by 1 (_reply_with_links).


##### `_reply_with_links`  (lines 4042–4084)

```
async def _reply_with_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds useful links and credential guidance to a terminal reply.

**Data flow**: It starts with _reply_text → adds details links, credential-setting instructions, and oversize-file download links → returns final text.

**Call relations**: Called by post before Slack-specific chunking and block rendering.

*Call graph*: calls 4 internal fn (home_url, _details_link_line, _oversize_link_line, _reply_text); called by 1 (post).


##### `_details_link_line`  (lines 4087–4091)

```
async def _details_link_line(ctx: SurfaceContext, conversation_id: UUID, artifact: SharedArtifact) -> str
```

**Purpose**: Builds one markdown link line for a detailed report artifact.

**Data flow**: It asks for a report URL or artifact link → returns a labeled markdown link or filename.

**Call relations**: Used by _reply_with_links for artifacts whose role is details.

*Call graph*: calls 2 internal fn (artifact_link, report_url); called by 1 (_reply_with_links).


##### `_oversize_link_line`  (lines 4094–4097)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Builds one markdown line for a file too large to upload to Slack.

**Data flow**: It gets an artifact link if possible → formats filename/link and byte size → returns a bullet line.

**Call relations**: Used by _reply_with_links for oversized shared files.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_links).


##### `_reply_mention_ids`  (lines 4100–4111)

```
async def _reply_mention_ids(ctx: SurfaceContext, bot_token: str, channel: str, text: str) -> dict[str, str]
```

**Purpose**: Gets the safe Slack mention map for a reply, only when the text might contain @names.

**Data flow**: It checks for @ → reads identity → asks SlackNames for channel mention ids → returns a map or empty map.

**Call relations**: Used by _reply_mentions_mapped before posting terminal or mid-turn replies.

*Call graph*: calls 1 internal fn (_identity); called by 1 (_reply_mentions_mapped); 1 external calls (__init__).


##### `_channel_info`  (lines 4114–4128)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for one channel.

**Data flow**: It calls conversations.info with a short timeout → returns the channel object or nothing.

**Call relations**: Used for audience decisions, name resolution, and footer safety checks.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 4131–4144)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel crosses workspace boundaries.

**Data flow**: It reads channel info → returns true if unreadable or marked shared/external.

**Call relations**: Used by _slack_footer to avoid exposing operator-only details in shared channels.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 4147–4179)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the small footer added to Slack progress and terminal replies.

**Data flow**: It may build web, debug, and accounting links → suppresses operator details unless allowed and channel is internal → returns footer text or nothing.

**Call relations**: Used by ThreadProgress._footer and post.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 4201–4206)

```
def _slack_reply_progress_key(turn_id: UUID, reply_id: UUID | None=None) -> str
```

**Purpose**: Builds the storage key for exactly-once Slack reply progress.

**Data flow**: It receives a turn id and optional reply id → returns the terminal or span-specific progress key.

**Call relations**: Used by post, speak, and cleanup.

*Call graph*: called by 3 (_drop_turn_reply_records, post, speak).


##### `_slack_reply_progress`  (lines 4209–4222)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Loads or creates the delivery checkpoint for a Slack reply.

**Data flow**: It reads scoped storage → validates existing progress or creates an empty row atomically → returns progress plus stored value.

**Call relations**: Used by post and speak before sending reply parts.

*Call graph*: calls 2 internal fn (get, put_if); called by 2 (post, speak); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 4225–4234)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Atomically saves updated Slack reply delivery progress.

**Data flow**: It serializes progress → writes it only if the stored value still matches expectation → returns updated progress and encoded value.

**Call relations**: Used throughout post, speak, mention mapping, reconciliation, and delivery.

*Call graph*: calls 1 internal fn (put_if); called by 4 (_deliver_slack_reply, _reply_mentions_mapped, post, speak); 2 external calls (__init__, model_dump).


##### `_drop_turn_reply_records`  (lines 4237–4247)

```
async def _drop_turn_reply_records(store: ScopedStore, turn_id: UUID) -> None
```

**Purpose**: Deletes temporary Slack delivery and DM-anchor records for a finished turn.

**Data flow**: It lists keys under reply-progress and DM-anchor prefixes → deletes each one.

**Call relations**: Called after terminal delivery is settled, or when a silent reply posts nothing.

*Call graph*: calls 4 internal fn (delete, list, _dm_anchor_key, _slack_reply_progress_key); called by 2 (attach, post).


##### `_slack_reply_delivery`  (lines 4250–4265)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Checks whether a Slack message is one of this extension's previously attempted deliveries.

**Data flow**: It inspects message metadata and timestamp → returns the timestamp if the delivery id matches.

**Call relations**: Used by _reconcile_slack_reply to recover from uncertain post attempts.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 4268–4308)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Looks through recent Slack messages to see whether a pending delivery actually posted.

**Data flow**: It pages recent history or replies with metadata included → searches for the delivery id → returns the timestamp, nothing, or raises if page limit is exceeded.

**Call relations**: Used by post and speak before retrying a previously uncertain Slack post.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 2 (post, speak); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 4311–4351)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Posts one Slack reply body with exactly-once checkpointing.

**Data flow**: It skips already delivered ids → marks a delivery pending → posts to Slack → handles invalid_blocks specially → records accepted timestamp.

**Call relations**: Used by post and speak for each message part or fallback variant.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 2 (post, speak); 2 external calls (__init__, model_copy).


##### `_reply_mentions_mapped`  (lines 4354–4384)

```
async def _reply_mentions_mapped(ctx: SurfaceContext, bot_token: str, channel: str, text: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue) -> tuple[_SlackReplyPro
```

**Purpose**: Replaces safe written @names with Slack mention markup, using a pinned map.

**Data flow**: It reads an existing mention map from progress or resolves and checkpoints one → applies mention markup → returns updated progress and mapped text.

**Call relations**: Used by post and speak before splitting reply text.

*Call graph*: calls 2 internal fn (_checkpoint_slack_reply, _reply_mention_ids); called by 2 (post, speak); 2 external calls (model_copy, mention_markup).


##### `post`  (lines 4387–4588)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str | NothingDelivered
```

**Purpose**: Posts the terminal answer for a completed turn to Slack.

**Data flow**: It suppresses pure silence when allowed → finds reply thread → builds linked text, forms, buttons, footer, and chunks → posts exactly once with fallbacks → returns first message ref or nothing delivered.

**Call relations**: This is the main outbound terminal-delivery function; attach runs afterward when files need uploading.

*Call graph*: calls 17 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _drop_turn_reply_records, _hold_connect_message, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _reply_with_links (+7 more)); 7 external calls (__init__, __init__, __init__, AsyncClient, loads, log, is_silence_sentinel).


##### `speak`  (lines 4591–4688)

```
async def speak(ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Posts a mid-turn reply to Slack before the final answer.

**Data flow**: It finds the right thread, including per-message DM anchors → maps mentions → splits text → posts parts with delivery checkpoints → returns first message ref.

**Call relations**: Used for live replies from a running turn; shares exactly-once machinery with post.

*Call graph*: calls 12 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _slack_reply_progress, _slack_reply_progress_key, _thread_mirror_key (+2 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 4691–4731)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request and returns Slack's payload without forcing ok:true.

**Data flow**: It posts JSON bytes to Slack → converts HTTP errors, including rate-limit retry-after, into delivery errors → returns parsed JSON.

**Call relations**: Used by _deliver_slack_reply so callers can handle recoverable Slack invalid_blocks responses.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 4734–4740)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the timestamp from a successful Slack post response.

**Data flow**: It checks ok:true and a non-empty ts → returns the timestamp → raises SlackApiError otherwise.

**Call relations**: Used after reply posts in _deliver_slack_reply, post, and speak.

*Call graph*: called by 3 (_deliver_slack_reply, post, speak); 1 external calls (__init__).


##### `attach`  (lines 4743–4787)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shared turn files to Slack after the terminal reply is recorded.

**Data flow**: It cleans delivery records → filters uploadable files → reserves/upload files concurrently → shares successful uploads in batches into the reply thread.

**Call relations**: Called after post returns a reply reference; it handles files separately so text delivery stays durable.

*Call graph*: calls 6 internal fn (credential, _attachment_batches, _drop_turn_reply_records, _reply_thread, _share_uploaded_files, _upload_artifact); 4 external calls (__init__, gather, AsyncClient, Timeout).


##### `_attachment_batches`  (lines 4790–4794)

```
def _attachment_batches(files: Sequence[dict[str, str]]) -> Iterator[Sequence[dict[str, str]]]
```

**Purpose**: Splits uploaded Slack file ids into batches Slack will accept in one share call.

**Data flow**: It receives a sequence of file dictionaries → yields slices no larger than Slack's attach limit.

**Call relations**: Used by attach before completing external uploads.

*Call graph*: called by 1 (attach).


##### `_upload_artifact`  (lines 4797–4824)

```
async def _upload_artifact(ctx: SurfaceContext, client: httpx.AsyncClient, bot_token: str, artifact: SharedArtifact) -> str
```

**Purpose**: Streams one stored ufo artifact into Slack's external upload flow.

**Data flow**: It reserves an upload URL and file id → streams blob bytes to the URL → returns the Slack file id.

**Call relations**: Called concurrently by attach for each inline-shareable file.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (__init__, post).


##### `_share_uploaded_files`  (lines 4827–4851)

```
async def _share_uploaded_files(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, files: Sequence[dict[str, str]]) -> None
```

**Purpose**: Completes Slack external upload by sharing uploaded files into a channel or thread.

**Data flow**: It receives file ids, channel, and optional thread → posts files.completeUploadExternal → returns nothing on success.

**Call relations**: Used by attach after individual file uploads succeed.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (post, dumps).


##### `_slack_ok`  (lines 4854–4863)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Common helper for Slack API calls that must return ok:true.

**Data flow**: It awaits an HTTP response → raises for HTTP failure → parses JSON → raises SlackApiError for ok:false → returns the payload.

**Call relations**: Used across install, Slack reads, status writes, message updates, uploads, and searches.

*Call graph*: called by 19 (_list, _members, _say, _set, _channel_info, _conversation_members, _declared_files, _founding_context, _held_connect_message, _later_channel_context (+9 more)); 1 external calls (__init__).


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

The terminal client talks to the server with ordinary HTTP requests, but the conversation itself is live. This file is the translator and traffic controller between those two worlds. It authenticates the caller, finds or creates the right conversation, admits new messages, then streams back short tab-separated command lines such as “say this”, “show a prompt”, “run this terminal operation”, “poll again”, or “listen while idle”. Think of it like a train station announcer: the deeper system decides what is happening, but this file announces it in the exact language the terminal understands.

It also protects important boundaries. A bearer token proves the user’s email and workspace. Message size, secret size, runtime options, and read-only conversations are checked before anything reaches the core conversation engine. Long-running turns are streamed for a limited time; if the answer is not done yet, the client is told to reconnect with a cursor so it continues from the right place instead of printing old text again.

Besides chat, this file supports terminal-specific features: workspace file upload/download, terminal operation replies, environment documents, system skill bundles, conversation listing, and private credential entry. Without it, the `ufo` shell would have no stable protocol for rendering conversations or safely connecting a local terminal to server-driven agent work.

#### Function details

##### `terminal_runtime_id`  (lines 142–144)

```
def terminal_runtime_id(channel: str) -> str
```

**Purpose**: Creates a stable short identifier for the local runtime tied to one terminal channel. This lets reconnects for the same channel refer to the same terminal namespace.

**Data flow**: It receives a channel name, hashes it, and returns the first fixed-length part of that hash. Nothing else is changed.

**Call relations**: When a channel stream is bound to a terminal, `_ChannelStream._bound` asks this function for the runtime id before announcing the terminal connection to the surface context.

*Call graph*: called by 1 (_bound); 1 external calls (sha256).


##### `directive`  (lines 147–155)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one protocol line for the terminal client. It escapes tabs, newlines, and backslashes so the shell can safely split the line into fields.

**Data flow**: It receives a command word and optional text fields, cleans each field so it cannot break the line format, joins them with tabs, adds a newline, and returns bytes ready to stream.

**Call relations**: Almost every response-building path uses this as the common printer for terminal instructions, including live frame rendering, message acknowledgements, secret results, polling, and client update notices.

*Call graph*: called by 12 (_answer, _channel_message, _channel_op_reply, _channel_stop, _client_update, _fulfill_secret, _send, _stream_end_directives, _subagent_note, directives_for (+2 more)).


##### `shared_files`  (lines 169–180)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files an agent turn shared and turns them into terminal-friendly file records. It also adds public download links when the deployment can provide them.

**Data flow**: It receives the surface context and a turn id, reads shared artifacts for that turn, asks the context for each artifact’s link, and returns immutable `SharedFile` records.

**Call relations**: _ChannelStream.response` passes this into `stream_directives`, so file links are fetched when a terminal frame ends and the client needs to display completed shared files.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 183–190)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies which workspace an incoming request claims to belong to before the route runs. If the authorization header is missing or not a bearer token, it rejects by returning no workspace.

**Data flow**: It reads the request’s authorization header, extracts the bearer token, asks the bearer codec for the workspace claim, and returns a workspace id or `None`.

**Call relations**: This is the early workspace-identification hook for the surface. Later request handlers authenticate the member email separately using the same bearer-token family.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 196–265)

```
def history_directives(conversation: Conversation, agent_origin: frozenset[str]) -> tuple[bytes, ...]
```

**Purpose**: Turns past conversation messages into terminal lines for a fresh resume. It shows user messages as `you`, agent replies as `say`, and completed work steps as compact notes while avoiding duplicate replay of the latest reply.

**Data flow**: It receives a conversation transcript and a set of machine-origin message references, walks messages in order, extracts readable text, groups assistant work into replies and step counts, trims to a character budget, and returns directive bytes.

**Call relations**: _ChannelStream.response` uses this only when a resume starts without a cursor, so the client sees useful history before the live tail begins. It relies on `_history_text` and `_dispatched` to understand each transcript message.

*Call graph*: calls 3 internal fn (_dispatched, _history_text, directive); called by 1 (response); 2 external calls (member_message_ref, member_message_text).


##### `_dispatched`  (lines 268–275)

```
def _dispatched(message: Message, active: set[str]) -> int
```

**Purpose**: Counts how many tool calls in an assistant message actually became active work. Calls that were written but never dispatched are not shown as completed steps.

**Data flow**: It receives one message and a set of active tool-use ids, checks non-text content blocks, and returns the number of tool-use blocks whose ids appear in the active set.

**Call relations**: It is a helper for `history_directives`, which uses the count to turn older assistant work into “Completed step” notes.

*Call graph*: called by 1 (history_directives).


##### `_history_text`  (lines 278–281)

```
def _history_text(message: Message) -> str
```

**Purpose**: Extracts human-readable text from a stored message, whether it is plain text or a list of content blocks.

**Data flow**: It receives a message, returns the string directly if the message content is already text, or joins the text blocks if the content is structured.

**Call relations**: It is used by `history_directives` while rebuilding the terminal’s visible conversation history.

*Call graph*: called by 1 (history_directives).


##### `directives_for`  (lines 284–342)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True, runtime: RuntimeIde
```

**Purpose**: Maps one live event from the conversation engine into one or more terminal protocol lines. It is the main live renderer for text, status, activity notes, terminal endings, absorbed messages, resumes, and replies.

**Data flow**: It receives a live frame plus context such as whether text was already streamed, pending credential prompts, shared files, runtime identity, and comment display rules. It pattern-matches the frame type and returns the matching directive bytes.

**Call relations**: _render_stream_frame` calls this after it has gathered any extra information needed for terminal frames. It delegates special cases to `_answer` and `_subagent_note`.

*Call graph*: calls 3 internal fn (_answer, _subagent_note, directive); called by 1 (_render_stream_frame); 1 external calls (__init__).


##### `_subagent_note`  (lines 345–351)

```
def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]
```

**Purpose**: Creates a terminal note for activity happening inside a named subagent. It skips subagent start and finish frames when there is no activity text to show.

**Data flow**: It receives a subagent activity frame, chooses a label from the subagent name or profile, and returns a note directive if activity text exists; otherwise it returns no lines.

**Call relations**: `directives_for` calls this when it sees a subagent activity frame, keeping the main frame renderer simpler.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for).


##### `_answer`  (lines 354–424)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True, runtime: RuntimeIdentity
```

**Purpose**: Builds the final terminal lines for a completed, failed, or cancelled turn. It decides whether to show the final answer, file links, secret prompts, runtime details, an error message, a prompt, or an exit instruction.

**Data flow**: It receives a terminal frame and supporting information such as streamed status, credential prompts, files, and runtime identity. It inspects the terminal status and returns the correct closing directives.

**Call relations**: `directives_for` hands terminal frames to this function. The output determines what the shell does at the end of a turn: keep prompting, collect secrets, show files, or exit.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for); 1 external calls (__init__).


##### `_render_stream_frame`  (lines 435–484)

```
async def _render_stream_frame(frame: LiveFrame, streamed: bool, pending: Callable[[str, str], Awaitable[bool]] | None, connect: Callable[[], Awaitable[str]] | None, files: Callable[[], Awaitable[tupl
```

**Purpose**: Prepares and renders one live frame during streaming. For terminal frames, it first checks for pending secret prompts, connection links, and shared files so the final output is complete.

**Data flow**: It receives a frame, current stream state, optional lookup callbacks, and display flags. It gathers any needed extra data, calls `directives_for`, updates whether text has streamed, and returns a small rendered-frame summary.

**Call relations**: `stream_directives` calls this for each frame pulled from the live tail. It is the bridge between raw engine frames and the stream loop’s state machine.

*Call graph*: calls 1 internal fn (directives_for); called by 1 (stream_directives); 1 external calls (__init__).


##### `_stream_end_directives`  (lines 487–510)

```
async def _stream_end_directives(turn_id: UUID, rendered_cursor: str, terminated: bool, ran: bool, prompting: bool, moved_on: Callable[[], Awaitable[bool]] | None) -> tuple[bytes, ...]
```

**Purpose**: Decides what instruction to send when a held stream ends. It tells the client whether to poll soon, listen while idle, immediately resume a newer turn, or do nothing.

**Data flow**: It receives the current turn id, last rendered cursor, and booleans saying whether the turn ended, ran an operation, or left a prompt. It may call a `moved_on` check, then returns final cursor/poll/listen directives.

**Call relations**: `stream_directives` calls this after the main streaming loop finishes, so every reconnectable ending gives the client the right next step.

*Call graph*: calls 1 internal fn (directive); called by 1 (stream_directives).


##### `_cancel_stream_tasks`  (lines 513–520)

```
async def _cancel_stream_tasks(*tasks: asyncio.Task[Any] | None) -> None
```

**Purpose**: Safely cancels background tasks created while streaming. This prevents leftover waits for frames or terminal operations after the response is done.

**Data flow**: It receives optional asyncio tasks, cancels the ones that exist, awaits them, and suppresses expected cancellation or terminal-gone errors.

**Call relations**: `stream_directives` uses this in its cleanup block so races between frames and terminal operations do not leak tasks.

*Call graph*: called by 1 (stream_directives); 1 external calls (suppress).


##### `stream_directives`  (lines 523–649)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Streams a turn’s live events to the terminal for a limited hold time. It also races live conversation frames against pending terminal operations, so the client can pause streaming, run a local command, and reconnect with the result.

**Data flow**: It receives a tail of live frames, timing and callback options, turn identity, cursor information, and display flags. It repeatedly waits for either the next frame or terminal operation, yields directive bytes, tracks the last rendered cursor, and finally yields reconnect instructions when needed.

**Call relations**: _ChannelStream.response` uses this as the core response body for held chat streams. It calls `_next`, `_render_stream_frame`, `_cancel_stream_tasks`, and `_stream_end_directives` to keep the stream correct across timeouts, terminal endings, and tool-operation handoffs.

*Call graph*: calls 5 internal fn (_cancel_stream_tasks, _next, _render_stream_frame, _stream_end_directives, directive); called by 1 (response); 3 external calls (ensure_future, get_running_loop, wait).


##### `_next`  (lines 652–658)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next item from an async stream and turns end-of-stream into `None`. This makes the main stream loop easier to race with timeouts and other tasks.

**Data flow**: It receives an async iterator of cursor/frame pairs, awaits one item, and returns that item or `None` if the iterator is finished.

**Call relations**: `stream_directives` wraps frame reads with this helper before putting them into asyncio tasks.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 661–665)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Checks the bearer token and extracts the email it proves for a specific workspace. It is the low-level token verifier.

**Data flow**: It reads the authorization header, extracts a bearer token, verifies it against the workspace id, and returns an email or `None`.

**Call relations**: _authenticated_member` calls this first, then uses the email to find or link a member row.

*Call graph*: called by 1 (_authenticated_member); 1 external calls (verify_token).


##### `_authenticated_member`  (lines 668–681)

```
async def _authenticated_member(ctx: SurfaceContext, request: Request) -> tuple[str, UUID | None] | None
```

**Purpose**: Authenticates a request as an email and, when possible, links it to a workspace member. It rejects invalid tokens and members who no longer have access.

**Data flow**: It receives the surface context and request, verifies the email, looks up or creates the email-to-member link, checks member access if there is a member id, and returns `(email, member_id)` or `None`.

**Call relations**: All public handlers call this before doing useful work: chat channels, joined conversations, conversation lists, op-body reads, skill downloads, environment storage, and workspace file routes.

*Call graph*: calls 4 internal fn (link_member, linked_member, member_has_access, _authenticated_email); called by 10 (channel, conversation, conversations, op_body, store_environment, store_environment_file, system_skills, workspace_file, workspace_listing, workspace_upload).


##### `_utf8_header`  (lines 684–691)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Recovers a header value that the client meant as UTF-8 text, such as a filesystem path. This matters because HTTP libraries often decode header bytes using a different default encoding.

**Data flow**: It reads a named header, reverses the server’s Latin-1 byte mapping, decodes the original bytes as UTF-8, and returns a cleaned string.

**Call relations**: `channel` uses it for the current working directory, `conversation` uses it to reject sandbox claims on joined conversations, and `_channel_op_reply` uses it for terminal operation error text.

*Call graph*: called by 3 (_channel_op_reply, channel, conversation).


##### `_stale_client`  (lines 694–698)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Detects whether the shell script version is older than the version served by this deployment. Local development can leave the served version unset to disable this check.

**Data flow**: It reads the expected client version from the environment and compares it with the request’s script-version header. It returns true only when a served version exists and the client does not match.

**Call relations**: _serve` checks this before routing channel actions, so stale clients can be told to update instead of continuing with an incompatible protocol.

*Call graph*: called by 1 (_serve).


##### `_client_update`  (lines 701–702)

```
def _client_update() -> bytes
```

**Purpose**: Builds the small terminal response that tells the client to install an update and explains why.

**Data flow**: It creates an `install` directive followed by a `say` directive with the stale-client message, and returns the combined bytes.

**Call relations**: _channel_message` and `_channel_stop` use this when a request should push the terminal script update flow.

*Call graph*: calls 1 internal fn (directive); called by 2 (_channel_message, _channel_stop).


##### `_resumed_from`  (lines 705–712)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Extracts the resume cursor only if it belongs to the current turn. This prevents a cursor from an older turn from accidentally skipping frames in a newer turn.

**Data flow**: It reads the `since` header, splits it into turn id and cursor, compares the turn id with the current turn, and returns the cursor or an empty string.

**Call relations**: _ChannelStream.response` uses this before opening the live tail, so reconnects continue from the right frame.

*Call graph*: called by 1 (response).


##### `_turn_context`  (lines 715–727)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the context attached to a user-admitted turn, including who sent it and the client’s timezone when valid.

**Data flow**: It receives an email and request, reads the timezone header, tries to create a `TurnContext`, logs and drops the timezone if validation fails, and returns the context.

**Call relations**: _channel_message` and `_send` pass this context into the core admission call so the engine knows the sender and local time setting.

*Call graph*: called by 2 (_channel_message, _send); 2 external calls (__init__, log).


##### `_runtime_config`  (lines 730–748)

```
def _runtime_config(ctx: SurfaceContext, request: Request) -> TurnRuntimeConfig | None
```

**Purpose**: Reads optional per-turn runtime choices from request headers, such as model, internet access narrowing, and environment pinning. It validates these choices before the turn is admitted.

**Data flow**: It reads model, internet, and environment headers, rejects unsupported internet values, builds a runtime config if anything was supplied, asks the context to validate it, and returns the config or `None`.

**Call relations**: _channel_message` and `_send` call this before admitting a message, so bad runtime options are reported as request errors rather than reaching the engine.

*Call graph*: calls 1 internal fn (validate_runtime_config); called by 2 (_channel_message, _send); 1 external calls (__init__).


##### `_channel_op_reply`  (lines 785–806)

```
async def _channel_op_reply(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, op_id: str, stale: bool) -> Response | _ChannelTurn
```

**Purpose**: Accepts the terminal client’s reply to a server-requested local operation. This is how command output or an operation failure gets back to the running turn without becoming a chat message.

**Data flow**: It reads the request body as the operation reply, rejects over-large replies, reads an optional error header, resolves the operation through the context, then returns either an immediate response or a `_ChannelTurn` to resume streaming.

**Call relations**: _serve` calls this when the request carries the operation header. After resolving the op, the normal stream path can continue tailing the turn that asked for it.

*Call graph*: calls 4 internal fn (latest_turn, terminal_resolve, _utf8_header, directive); called by 1 (_serve); 3 external calls (__init__, PlainTextResponse, body).


##### `_channel_stop`  (lines 809–820)

```
async def _channel_stop(ctx: SurfaceContext, request: Request, conversation_id: UUID, stale: bool) -> Response | _ChannelTurn
```

**Purpose**: Processes a user stop request, such as pressing Escape. It stops the currently running turn without admitting any message text.

**Data flow**: It rejects requests with a body, finds the latest turn, asks the context to stop it if present, and returns either a prompt/update response or a `_ChannelTurn` for streaming the cancellation result.

**Call relations**: _serve` calls this when the stop header is present. Its result then either ends immediately or flows into `_ChannelStream.response`.

*Call graph*: calls 4 internal fn (latest_turn, stop_turn, _client_update, directive); called by 1 (_serve); 3 external calls (__init__, PlainTextResponse, body).


##### `_channel_message`  (lines 823–883)

```
async def _channel_message(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str, stale: bool, marked: bool, posting: _Posting) -> Response | _Chan
```

**Purpose**: Processes the normal held-stream chat request for a channel or joined conversation. It either resumes an existing turn when the body is empty, or admits a new user message when text is present.

**Data flow**: It reads and trims the request body. Empty bodies inspect the latest turn and decide whether to prompt, listen, update, or resume. Non-empty bodies check stale clients, posting permissions, size, runtime config, terminal workspace claim, and then admit the message, returning a `_ChannelTurn` with any note or sent acknowledgement.

**Call relations**: _serve` uses this as the default path when the request is not a send, unsend, op reply, or stop. It hands successful stream-worthy results to `_ChannelStream.response`.

*Call graph*: calls 9 internal fn (admit, claim_terminal, latest_turn, turn_is_terminal, _client_update, _read_only, _runtime_config, _turn_context, directive); called by 1 (_serve); 3 external calls (__init__, PlainTextResponse, body).


##### `_read_only`  (lines 886–887)

```
def _read_only(surface: str) -> str
```

**Purpose**: Creates the human-readable message shown when the terminal cannot post into a conversation. It names the surface where the user should reply instead.

**Data flow**: It receives a surface id, converts known ids into friendly names like Slack or the portal, formats the read-only message, and returns it.

**Call relations**: _channel_message` and `_send` call this when `_posting` says the joined conversation is readable but not postable from the terminal.

*Call graph*: called by 2 (_channel_message, _send).


##### `_ChannelStream.response`  (lines 900–938)

```
async def response(self) -> Response
```

**Purpose**: Builds the streaming HTTP response for a channel turn. It prepares resume history, connection links, secret-prompt checks, file lookups, terminal operation polling, and then returns a text stream of directives.

**Data flow**: It reads request headers and context state, computes the resume cursor, optionally loads transcript history, constructs the live directive generator, wraps it with `_bound`, and returns a `StreamingResponse`.

**Call relations**: _serve` creates a `_ChannelStream` and calls this after message, stop, or op handling returns a streamable turn. It coordinates `history_directives`, `stream_directives`, and `_bound`.

*Call graph*: calls 4 internal fn (_bound, _resumed_from, history_directives, stream_directives); 2 external calls (partial, StreamingResponse).


##### `_ChannelStream._moved_on`  (lines 940–946)

```
async def _moved_on(self) -> bool
```

**Purpose**: Checks whether the conversation has advanced to a newer non-terminal turn while this stream was ending. This tells the client to immediately continue rather than sit idle.

**Data flow**: It asks for the latest turn in the conversation, compares it with the stream’s current turn, checks whether the latest turn is still running, and returns a boolean.

**Call relations**: _ChannelStream.response` passes this as the `moved_on` callback to `stream_directives`, which uses it when deciding final poll/listen instructions.


##### `_ChannelStream._bound`  (lines 948–965)

```
async def _bound(self, history: tuple[bytes, ...], directives: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Wraps the directive stream with terminal connection setup and cleanup. It also sends the initial sent acknowledgement, replayed history, and workspace note before live lines.

**Data flow**: It may announce a terminal connection using the current directory and stable runtime id, yields any sent/history/note lines, forwards live directives, and finally disconnects the terminal if it connected one.

**Call relations**: _ChannelStream.response` uses this as the actual body iterator for the streaming response. It calls `terminal_runtime_id` when a local workspace is attached.

*Call graph*: calls 1 internal fn (terminal_runtime_id); called by 1 (response).


##### `channel`  (lines 968–1003)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles the main terminal channel POST endpoint. A channel is the user’s own terminal conversation, identified by their email plus the path channel name.

**Data flow**: It authenticates the request, optionally fulfills a secret, reads and validates the current working directory, finds or creates the member-scoped conversation, and delegates the rest to `_serve` with plain posting rules.

**Call relations**: This is one of the primary route handlers in `ROUTES`. It hands common chat behavior to `_serve` after doing channel-specific authentication, secret, directory, and conversation lookup work.

*Call graph*: calls 5 internal fn (conversation_for, _authenticated_member, _fulfill_secret, _serve, _utf8_header); 3 external calls (__init__, conversation_audience, PlainTextResponse).


##### `conversation`  (lines 1006–1038)

```
async def conversation(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles POSTs to a conversation joined by id rather than by terminal channel. It lets a member read and sometimes post into conversations from other surfaces, while preserving that conversation’s existing sandbox.

**Data flow**: It authenticates the member, rejects unlinked users, optionally fulfills a secret, refuses terminal sandbox and op claims, parses the conversation id, checks whether the member can join it, and delegates to `_serve` with the correct posting mode.

**Call relations**: This route handler uses `_joined_conversation` to enforce access and posting rules, then shares the same serving path as `channel`.

*Call graph*: calls 5 internal fn (_authenticated_member, _fulfill_secret, _joined_conversation, _serve, _utf8_header); 2 external calls (PlainTextResponse, UUID).


##### `_serve`  (lines 1041–1072)

```
async def _serve(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str, posting: _Posting) -> Response
```

**Purpose**: Routes an authenticated conversation request to the right action: immediate send, unsend, terminal operation reply, stop, or normal held message/resume.

**Data flow**: It checks whether the client is stale, inspects action headers, calls the matching helper, and either returns that helper’s direct response or wraps the resulting `_ChannelTurn` in a `_ChannelStream` response.

**Call relations**: Both `channel` and `conversation` use this as their common dispatcher after they have resolved which conversation is being addressed and what posting rules apply.

*Call graph*: calls 6 internal fn (_channel_message, _channel_op_reply, _channel_stop, _send, _stale_client, _unsend); called by 2 (channel, conversation); 2 external calls (__init__, PlainTextResponse).


##### `_reachable_agents`  (lines 1075–1086)

```
async def _reachable_agents(ctx: SurfaceContext, member_id: UUID) -> tuple[AgentSummary, ...]
```

**Purpose**: Finds the agents a member is allowed to reach from the terminal. This includes workspace-visible agents, agents they own, and agents opened to them by extension conversations.

**Data flow**: It reads extension-opened agent ids and the full agent list from the context, filters the agents by visibility, ownership, or opened status, and returns the reachable summaries.

**Call relations**: _joined_conversation` uses it to decide whether a conversation id is behind an accessible agent, and `conversations` uses it to build the member’s list.

*Call graph*: calls 2 internal fn (list_agents, member_extension_agent_ids); called by 2 (_joined_conversation, conversations).


##### `_joined_conversation`  (lines 1089–1108)

```
async def _joined_conversation(ctx: SurfaceContext, member_id: UUID, email: str, conversation_id: UUID) -> _Posting | None
```

**Purpose**: Checks whether a member may open a specific conversation by id and decides how their messages would enter it. It rejects inaccessible agents, unreadable conversations, and machine-only lanes.

**Data flow**: It receives the conversation id, looks up its agent, compares that agent with reachable agents, asks for a matching listed conversation that includes member-admitted turns, and returns a posting mode or `None`.

**Call relations**: The `conversation` route calls this before delegating to `_serve`, so joined conversations get the right plain/comment/read-only behavior.

*Call graph*: calls 4 internal fn (conversation_agent, list_agent_conversations, _posting, _reachable_agents); called by 1 (conversation).


##### `_posting`  (lines 1111–1118)

```
def _posting(entry: ListedConversation, member_id: UUID, email: str) -> _Posting
```

**Purpose**: Classifies whether the terminal can post normally, post as a comment, or only read a listed conversation. The choice depends on the conversation’s surface and audience.

**Data flow**: It receives a listed conversation, member id, and email, checks whether it belongs to the member’s own audience or a shared audience, examines the surface type, and returns `_Plain`, `_Comment`, or `_ReadOnly`.

**Call relations**: _joined_conversation` uses this to decide serving behavior, and `_conversation_row` uses it to mark whether a listed conversation is postable.

*Call graph*: calls 1 internal fn (_comment_author); called by 2 (_conversation_row, _joined_conversation); 4 external calls (__init__, __init__, __init__, conversation_audience).


##### `_comment_author`  (lines 1121–1127)

```
def _comment_author(entry: ListedConversation, email: str, own: bool) -> str
```

**Purpose**: Chooses the author label for a terminal comment inserted into another surface’s conversation. It uses “You” for the member’s own conversation and otherwise tries to show the friendly sender name.

**Data flow**: It receives a listed conversation, email, and ownership flag. It searches known speakers for the email, strips the email suffix from sender labels when present, and returns the best display name.

**Call relations**: _posting` calls this when it has decided that terminal messages should enter as comments.

*Call graph*: called by 1 (_posting).


##### `conversations`  (lines 1153–1185)

```
async def conversations(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the list of conversations the member can open from the terminal. The list is newest-first, bounded in size, and can be narrowed by a search query.

**Data flow**: It authenticates the member, rejects unlinked users, reads the optional search text, loops through reachable agents, lists conversations for each, converts titled entries into rows, sorts them, trims the result, and returns JSON.

**Call relations**: This GET route uses `_reachable_agents` and `_conversation_row` to produce the terminal’s conversation picker data.

*Call graph*: calls 4 internal fn (list_agent_conversations, _authenticated_member, _conversation_row, _reachable_agents); 3 external calls (__init__, PlainTextResponse, Response).


##### `_conversation_row`  (lines 1188–1215)

```
def _conversation_row(entry: ListedConversation, agent: AgentSummary, member_id: UUID, email: str) -> ConversationRow
```

**Purpose**: Converts one backend conversation listing into the compact JSON row the terminal needs. It includes title, surface, speaker, agent, time, whether posting is allowed, and channel name for own terminal conversations.

**Data flow**: It receives a listed conversation, agent summary, member id, and email. It computes ownership, speaker display, channel shortcut, postability, and timestamps, then returns a `ConversationRow` model.

**Call relations**: `conversations` calls this for each listed conversation that has a title. It uses `_posting` to decide whether the row should be marked postable.

*Call graph*: calls 1 internal fn (_posting); called by 1 (conversations); 2 external calls (__init__, conversation_audience).


##### `_send`  (lines 1218–1288)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str, posting: _Posting) -> Response
```

**Purpose**: Handles the fast “send without holding a stream” request. It admits a message and immediately returns an acknowledgement, relying on an already-held stream to show the consequences.

**Data flow**: It validates the send id, reads the message body, checks posting permissions, size, runtime options, and optional terminal workspace claim, then admits the message with an idempotency key and returns a `sent` directive plus any workspace note.

**Call relations**: _serve` calls this when the send header is present. It shares admission helpers with `_channel_message` but deliberately does not open a live stream.

*Call graph*: calls 6 internal fn (admit, claim_terminal, _read_only, _runtime_config, _turn_context, directive); called by 1 (_serve); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 1291–1313)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Retracts a queued message that the engine has not yet taken up. This supports taking back a pending row before it becomes part of a turn.

**Data flow**: It rejects requests with a body, requires a linked member, parses the arrival id, asks the context to retract it for that member and conversation, and returns success or a conflict message.

**Call relations**: _serve` calls this when the unsend header is present. Unlike message admission, it changes only pending arrival state and does not stream a turn.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (_serve); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 1316–1337)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores a privately entered credential value for a pending credential prompt. The secret body is never treated as a chat message or transcript content.

**Data flow**: It reads the requested slot and body value, checks that both are present and small enough, asks the context to fulfill the sealed credential request, and returns a terminal `say` directive reporting stored or not stored.

**Call relations**: Both `channel` and `conversation` call this before normal serving when the request carries the secret header.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 2 (channel, conversation); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 1340–1352)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lets the terminal client download the body for an in-flight operation, such as bytes that need to be written to a local file. It is a read-only projection of a pending terminal operation.

**Data flow**: It authenticates the member, rebuilds the member-scoped queue key from email and channel, asks the context for the operation body, and returns bytes or a not-found response.

**Call relations**: This GET route is used after a streamed `run` directive tells the client which operation id to execute.

*Call graph*: calls 2 internal fn (terminal_op_body, _authenticated_member); 2 external calls (PlainTextResponse, Response).


##### `system_skills`  (lines 1355–1367)

```
async def system_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the bundled system skills archive to authenticated terminal clients. It supports caching with an ETag so unchanged bundles do not need to be downloaded again.

**Data flow**: It authenticates the request, reads the current bundle digest and archive from the context, compares the request’s `if-none-match` header, and returns either 304 not modified or the zip archive.

**Call relations**: This route is listed in `ROUTES` for channel-specific skill downloads and shares the common authentication helper.

*Call graph*: calls 1 internal fn (_authenticated_member); 2 external calls (PlainTextResponse, Response).


##### `store_environment`  (lines 1370–1380)

```
async def store_environment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores an environment document and returns its digest. Later turns can refer to that digest to pin the environment they should run with.

**Data flow**: It authenticates the request, reads the body bytes, asks the context to store the document, and returns the digest or a validation error.

**Call relations**: This POST route is used before a turn when the client needs to upload environment metadata for `_runtime_config` to reference.

*Call graph*: calls 2 internal fn (store_environment_document, _authenticated_member); 2 external calls (PlainTextResponse, body).


##### `store_environment_file`  (lines 1383–1392)

```
async def store_environment_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores a file referenced by an environment document and returns its digest. Identical file bytes produce the same digest.

**Data flow**: It authenticates the request, reads the file bytes from the body, asks the context to store them, and returns the digest or a validation error.

**Call relations**: This POST route supports environment upload alongside `store_environment`, using the same authentication path.

*Call graph*: calls 2 internal fn (store_environment_file, _authenticated_member); 2 external calls (PlainTextResponse, body).


##### `workspace_file`  (lines 1395–1415)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Streams a file out of a terminal channel’s workspace. This is the download side of terminal file copy.

**Data flow**: It authenticates the user, rebuilds the channel queue key, finds the existing conversation, asks for the named workspace file stream, and returns the file bytes or a uniform not-found response.

**Call relations**: This GET route reads only from the workspace attached to the member’s own channel; it does not create conversations or admit messages.

*Call graph*: calls 3 internal fn (find_conversation, read_workspace_file, _authenticated_member); 2 external calls (PlainTextResponse, StreamingResponse).


##### `workspace_upload`  (lines 1418–1436)

```
async def workspace_upload(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Uploads one file into a terminal channel’s workspace. This lets a user stage files before the first agent turn so tools can see them.

**Data flow**: It authenticates the user, validates the path, gets or creates the channel conversation, streams the request body into the workspace writer, and returns no-content on success or a size/refusal error.

**Call relations**: This PUT route uses the same member-scoped conversation identity as `channel`, but unlike downloads it may create the conversation because staging files is intentional.

*Call graph*: calls 3 internal fn (conversation_for, write_workspace_file, _authenticated_member); 4 external calls (conversation_audience, PlainTextResponse, stream, Response).


##### `workspace_listing`  (lines 1439–1464)

```
async def workspace_listing(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files in a terminal channel’s workspace with size and modified time. The terminal uses this for sync checks during file copy operations.

**Data flow**: It authenticates the user, finds the channel conversation if it exists, reads workspace file entries or uses an empty list, converts them to JSON, and returns that JSON.

**Call relations**: This GET route is part of the workspace file-transfer group and shares channel identity with `workspace_file` and `workspace_upload`.

*Call graph*: calls 3 internal fn (find_conversation, list_workspace_files, _authenticated_member); 3 external calls (dumps, PlainTextResponse, Response).


### `core/src/ufo/runtime/ext/surface.py`

`orchestration` · `cross-cutting: request handling, background delivery, listener runtime`

A surface is the place where a person talks to the system: a Slack thread, a browser chat, an iMessage conversation, or a similar channel. This file defines the special doorway those surfaces use to reach the core. Without it, outside channels could not safely turn a human message into durable work for an agent, could not map an external user to a workspace member, and could not deliver the agent’s final answer back to the right place.

The file does several jobs. It defines small data shapes for things a surface needs to show, such as agents, conversations, files, credential prompts, connector accounts, and turn details. It defines `SurfaceContext`, the powerful object handed to a surface route after the workspace is known. That context is like a controlled service desk: the surface can ask for exactly the privileged operations it needs, such as “admit this member’s message,” “give me signed links for these files,” or “tail this running turn.”

It also defines two background pollers. Durable surfaces, such as Slack, cannot rely on a live browser connection, so completed turns are stored and later delivered by `WritebackPoller`. Mid-turn replies use a similar `MidTurnReplyPoller`. Both pollers claim work with leases so several server instances can run without double-posting most messages. Finally, listener support lets one fleet-wide stream, such as an iMessage event feed, be owned by only one server instance at a time.

#### Function details

##### `is_silence_sentinel`  (lines 271–279)

```
def is_silence_sentinel(answer: str) -> bool
```

**Purpose**: Checks whether an agent’s final answer means “say nothing.” This prevents surfaces from posting empty-looking placeholder replies.

**Data flow**: It receives answer text, trims surrounding whitespace, and compares the whole answer against the accepted empty response forms. It returns true only when the entire answer is silence.

**Call relations**: Delivery code can use this before posting a reply so a silent terminal frame becomes a delivered no-op rather than a visible message.


##### `mint_marker`  (lines 282–292)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to wrap one member message safely. The marker makes the wrapper hard for message text to fake accidentally or maliciously.

**Data flow**: It asks the secure random generator for a few bytes and returns them as hexadecimal text. Nothing else is changed.

**Call relations**: It supports the message-fencing helpers that separate ambient context, the member’s words, and attachments before the text reaches the model.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 295–308)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the exact inbound text that represents one member message. It keeps context, the member’s own words, and attachment text in separate tagged sections.

**Data flow**: It receives a marker, ambient context text, message body, and attachment text. It returns one combined string, adding an attachment section only when there is attachment content.

**Call relations**: Surface ingesters use this kind of fenced text before admitting messages, and later readers can recover just the member’s words from it.


##### `inbox_name`  (lines 311–340)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an untrusted filename from a browser, Slack, or another surface into a safe single filename for the workspace. It also prevents two files in the same batch from overwriting each other.

**Data flow**: It receives a raw filename and a set of already-used names. It drops path parts, replaces unsafe characters, preserves useful extensions where possible, adds a number if needed, updates the set, and returns the safe name.

**Call relations**: Upload and inbound-file storage call this before creating artifact keys, so every surface follows the same filename safety rule.

*Call graph*: called by 2 (mint_upload, store_inbound_file); 1 external calls (contained_leaf).


##### `member_message_text`  (lines 343–357)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts the member’s own words from a stored inbound message. This is for displays that should show what the person said, not the extra context the engine added.

**Data flow**: It receives an inbound transcript string, strips known engine context wrappers, looks for the per-message fenced member section, and returns either the extracted words or the original text.

**Call relations**: Conversation naming uses this so a title is based on the person’s message rather than surrounding ambient context.

*Call graph*: called by 1 (conversation_name).


##### `member_message_ref`  (lines 360–367)

```
def member_message_ref(inbound: str) -> str | None
```

**Purpose**: Reads the message reference that the engine put in an inbound message’s context header. That reference links displayed transcript text back to the turn or queued message that created it.

**Data flow**: It receives inbound text, checks whether it begins with a context block containing `message_ref`, and returns that value or null.

**Call relations**: Transcript projections can use this to distinguish member-admitted messages from machine-origin messages.


##### `MemberAdmitter.admit`  (lines 421–432)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Defines the contract for admitting a member’s message into the durable turn queue. Concrete runtime code implements the actual queue write.

**Data flow**: A caller provides conversation id, message text, optional idempotency key, context, speaker member id, and optional intent/comment/runtime settings. The implementation returns which turn the message belongs to and whether a new run opened.

**Call relations**: `SurfaceContext.admit` delegates to this protocol, so surfaces do not know the queue internals.


##### `TurnTailer.tail`  (lines 446–448)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how a live surface streams frames from a running turn. A frame is a live update such as progress or output.

**Data flow**: It receives a turn id and optional cursor, opens an async scoped stream, and yields cursor-frame pairs until the turn ends.

**Call relations**: `SurfaceContext.tail` exposes this to web/debug/live surfaces while hiding the hub implementation.


##### `TurnTailer.latest_activity`  (lines 450–450)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Defines how to peek at the newest retained activity for a turn without subscribing to the whole stream.

**Data flow**: It receives a turn id and returns the latest activity object, or null if none is retained.

**Call relations**: `SurfaceContext.latest_activity` uses this for status screens that poll what an agent is currently doing.


##### `TurnStopper.stop`  (lines 460–460)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines how to stop a running turn at a member’s request. It also reports whether a pending follow-up message started a new turn.

**Data flow**: It receives workspace, conversation, and turn ids. The implementation cancels or observes the turn and returns whether it ended and any newly founded turn id.

**Call relations**: `SurfaceContext.stop_turn` delegates to this so surfaces can offer a stop button without controlling workflow cancellation directly.


##### `TurnStepSource.read`  (lines 466–466)

```
async def read(self, workflow_id: str) -> tuple['TurnStep', ...]
```

**Purpose**: Defines how to read durable workflow steps for a turn. These steps are the recorded model/tool/workflow events behind a turn.

**Data flow**: It receives a workflow id and returns an ordered tuple of `TurnStep` records.

**Call relations**: `SurfaceContext.turn_steps` calls this after confirming the turn belongs to the workspace.


##### `SurfaceModel.model`  (lines 496–496)

```
def model(self) -> str
```

**Purpose**: Defines the readable model id for a model service exposed to surface routes. The id is used for display, attribution, and billing labels.

**Data flow**: A concrete model access object returns its configured model name. It does not change state.

**Call relations**: Surface routes that need a one-off model call can inspect this through `SurfaceContext.model`.


##### `SurfaceModel.turn`  (lines 498–498)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Defines a one-shot model call available to a surface route. It is for surface-side work that can fail gracefully while a member waits.

**Data flow**: It receives a model request and returns one model message. Billing and execution are handled by the concrete implementation.

**Call relations**: Surface extensions may call this through `SurfaceContext.model` when the deployment wired such access.


##### `_without_carried`  (lines 617–640)

```
def _without_carried(conversation: Conversation) -> Conversation
```

**Purpose**: Reduces workspace file links in assistant messages to their labels. This lets transcript readers see the answer text as the member did.

**Data flow**: It receives a `Conversation`, walks each message, strips marked artifact text from assistant text blocks, and returns a copied conversation with cleaned content.

**Call relations**: `SurfaceContext.read_transcript` calls this after decoding transcript bytes from blob storage.

*Call graph*: called by 1 (read_transcript); 2 external calls (model_copy, marked_artifacts).


##### `shared_artifact_link`  (lines 643–654)

```
def shared_artifact_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed download link for a shared file when public file delivery is configured. If links cannot be minted, it returns null.

**Data flow**: It receives signing secret, public base URL, workspace id, and artifact. It calculates an expiry, mints a signed artifact path, and returns a full URL.

**Call relations**: `SurfaceContext.artifact_link` wraps this for the current workspace.

*Call graph*: called by 1 (artifact_link); 3 external calls (now, artifact_url_expiry, mint_artifact_url).


##### `shared_artifact_preview_link`  (lines 657–679)

```
def shared_artifact_preview_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview-image link for an artifact when the artifact really has eligible raster image bytes. This avoids serving a document as if it were a safe inline image.

**Data flow**: It chooses either the artifact’s preview blob or the original blob, verifies the media type matches a raster image type, and returns a preview URL or null.

**Call relations**: `SurfaceContext.artifact_preview_link` wraps this for web projections and file cards.

*Call graph*: called by 1 (artifact_preview_link); 2 external calls (mint_image_preview_url, raster_image_media_type).


##### `_scheduled_runs_query`  (lines 682–718)

```
def _scheduled_runs_query(workspace_id: UUID, member_id: UUID, agent_id: UUID | None) -> sa.Select[Any]
```

**Purpose**: Builds the database query for scheduled turns a member is allowed to read. Scheduled turns are agent runs that fired on their own, such as cron-like tasks.

**Data flow**: It receives workspace id, member id, and optional agent id. It returns a SQL query filtered to terminal scheduled turns in readable conversations.

**Call relations**: `scheduled_runs` adds optional narrowing and turns the rows into member-facing records.

*Call graph*: called by 1 (scheduled_runs); 3 external calls (or_, select, readable_audiences).


##### `scheduled_runs`  (lines 721–799)

```
async def scheduled_runs(workspace_id: UUID, member_id: UUID, *, limit: int, agent_id: UUID | None=None, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Reads the newest completed scheduled runs visible to a member, including their final text and shared files. This powers feeds of automated agent output.

**Data flow**: It builds a query, applies optional filters, reads turn and artifact rows, resolves conversation sources, and returns `ScheduledRun` objects.

**Call relations**: It uses `_scheduled_runs_query` for the access-controlled base query and `ConversationDirectory.sources` for links back to the originating conversation.

*Call graph*: calls 1 internal fn (_scheduled_runs_query); 6 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx).


##### `conversation_name`  (lines 848–853)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Derives a conversation title from the message that opened it. It uses the member’s words, not ambient channel context.

**Data flow**: It receives inbound text, extracts the member-message portion, trims it, limits its length, and returns the title text.

**Call relations**: Conversation creation paths can use this to give new conversations a consistent initial title.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 856–873)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Updates a conversation’s title when a surface has a better name for it. Blank titles are ignored.

**Data flow**: It receives workspace id, conversation id, and title, trims and caps the title, and updates the matching conversation row if the result is nonempty.

**Call relations**: `SurfaceContext.retitle_conversation` calls this after binding the workspace.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `summarize_conversation_title`  (lines 876–896)

```
async def summarize_conversation_title(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores a model-generated title summary and marks that title summarization has already run. This prevents paying for the same title job repeatedly.

**Data flow**: It receives workspace id, conversation id, and suggested title. It writes the nonblank title or keeps the old title, and marks the row summarized.

**Call relations**: A background titling flow can call this after summarizing a conversation opening.

*Call graph*: 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 968–969)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures an agent detail timestamp has timezone information. This keeps API output consistent.

**Data flow**: It receives a datetime and returns it unchanged if timezone-aware, otherwise returns a UTC-marked copy.

**Call relations**: Pydantic runs this validator when building `AgentDetail` objects.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 1017–1018)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a connection timestamp is timezone-aware. This avoids ambiguous times in portal displays.

**Data flow**: It receives a datetime and returns either the original aware value or a UTC-marked copy.

**Call relations**: Pydantic applies this when `ConnectionView` records are created.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 1044–1045)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a connection-pool timestamp to include UTC when missing.

**Data flow**: It receives `connected_at`, adds UTC only if no timezone exists, and returns the result.

**Call relations**: It runs during `ConnectionPoolView` validation.

*Call graph*: 1 external calls (replace).


##### `_binding_fields`  (lines 1079–1107)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts the identity fields of a connector-backed source so the portal can submit accurate actions for that source. If the source config is not a connector config, fields are empty.

**Data flow**: It receives backend name and stored config, validates the config, and returns binding name, stream, account id, base URL, and backfill setting.

**Call relations**: `SurfaceContext.list_sources` includes these fields in each `SourceView`.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 1139–1140)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Makes the next-sync timestamp timezone-aware for source listings.

**Data flow**: It receives a datetime and returns a UTC-marked copy only when the input had no timezone.

**Call relations**: Pydantic runs it while constructing `SourceView`.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 1158–1161)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation timestamps, while allowing nullable last-activity time.

**Data flow**: It receives a datetime or null. Null stays null; naive datetimes are marked UTC; aware values pass through.

**Call relations**: Conversation list builders rely on this for consistent API records.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 1174–1235)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged reading another member’s private conversation. This creates the temporary grant used by transcript reads and leaves an audit trail.

**Data flow**: It receives workspace, conversation, agent, and reader member ids. It verifies the conversation is another member’s private conversation, writes a transcript-access row, logs the disclosure, and returns the involved emails or null if not applicable.

**Call relations**: Portal prepared intents call this before content routes are allowed to show another member’s private transcript.

*Call graph*: 9 external calls (__init__, now, insert, select, workspace_tx, log, audience_member, parse_audience, uuid4).


##### `ConversationDirectory.list`  (lines 1293–1426)

```
async def list(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, portal: bool | None=None, conversation_id: UUID | None=None, participation: Literal['mine',
```

**Purpose**: Lists an agent’s conversations for a member or admin, with readable content only where allowed. It is the shared implementation behind portal conversation views.

**Data flow**: It builds a query filtered by agent, audience, surface, participation, search, and limits. It then fetches opening sources and speakers only for readable rows and returns `ListedConversation` records.

**Call relations**: Surface methods delegate to this so every conversation listing follows the same access and ordering rules.

*Call graph*: calls 6 internal fn (_matches, _member_admitted, _others, _participated, sources, speakers); 10 external calls (__init__, __init__, not_, or_, select, workspace_tx, audience_member, conversation_audience, parse_audience, readable_audiences).


##### `ConversationDirectory.sources`  (lines 1428–1465)

```
async def sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the source link or source label from the opening turn of each listed conversation.

**Data flow**: It receives conversation ids, finds each conversation’s first turn, reads its stored context, and returns a map from conversation id to source string or null.

**Call relations**: `ConversationDirectory.list` and scheduled-run/artifact listings use this to show where a conversation began.

*Call graph*: called by 1 (list); 4 external calls (model_validate, and_, select, workspace_tx).


##### `ConversationDirectory.speakers`  (lines 1467–1523)

```
async def speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Finds the first few members who spoke in each conversation. This lets listings show who participated without loading full transcripts.

**Data flow**: It receives conversation ids, queries first speech per member in order, reads sender labels from turn context, and returns speakers grouped by conversation.

**Call relations**: `ConversationDirectory.list` calls it only for conversations whose content the viewer may read.

*Call graph*: called by 1 (list); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `ConversationDirectory._member_admitted`  (lines 1525–1538)

```
def _member_admitted(self) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition for conversations that ever had a member-admitted turn. This separates real user conversations from machine-only lanes.

**Data flow**: It creates an `exists` condition against turns in the current conversation with member admission source.

**Call relations**: `ConversationDirectory.list` adds this condition when callers ask for member-started conversations only.

*Call graph*: called by 1 (list); 2 external calls (literal, select).


##### `ConversationDirectory._spoken`  (lines 1540–1561)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition for whether any member, or one specific member, spoke in a conversation.

**Data flow**: It receives an optional member id and returns an efficient correlated existence check against turn speaker rows.

**Call relations**: Participation filters use this as their basic building block.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `ConversationDirectory._participated`  (lines 1563–1571)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition for conversations the member is part of. Being bound to the member or having spoken both count.

**Data flow**: It receives a member id and returns an OR condition combining conversation ownership and `_spoken`.

**Call relations**: `ConversationDirectory.list` uses it for the “mine” participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 1 external calls (or_).


##### `ConversationDirectory._others`  (lines 1573–1585)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL condition for readable conversations where someone else spoke and this member did not participate.

**Data flow**: It receives a member id and returns a condition excluding the member’s own participation while requiring some member speech.

**Call relations**: `ConversationDirectory.list` uses it for the “others” rail.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 2 external calls (and_, not_).


##### `ConversationDirectory._matches`  (lines 1587–1616)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a SQL search condition for conversation listings. It searches safe metadata broadly and private content only when the viewer may read it.

**Data flow**: It receives search text and member id, creates conditions over surface label, owner email, title, and speaker emails, and returns one combined SQL expression.

**Call relations**: `ConversationDirectory.list` applies this before limiting results so search finds the intended row.

*Call graph*: called by 1 (list); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `LedgerEntry._aware_utc`  (lines 1630–1631)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes ledger timestamps to timezone-aware UTC.

**Data flow**: It receives a datetime and returns either the original aware datetime or a UTC-marked copy.

**Call relations**: It runs when turn accounting rows are converted to `LedgerEntry` objects.

*Call graph*: 1 external calls (replace).


##### `TurnStep._aware_utc`  (lines 1652–1655)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes optional turn-step timestamps to timezone-aware UTC.

**Data flow**: It receives a datetime or null, leaves null and aware values alone, and marks naive values as UTC.

**Call relations**: It runs during `TurnStep` validation for debugger and trajectory views.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 1711–1712)

```
def _fulfilled_marker_key(request_id: UUID, slot: str) -> str
```

**Purpose**: Builds the blob-store key used to mark a private credential prompt as fulfilled. The marker prevents the same prompt from being fulfilled twice.

**Data flow**: It receives a request id and slot name and returns a stable string path under `credential_requests`.

**Call relations**: Credential prompt checks and fulfillment both use the same key so they agree on completion.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request).


##### `_credential_request_id`  (lines 1715–1718)

```
def _credential_request_id(state: CredentialRequestState, sealed: str) -> UUID
```

**Purpose**: Gets the durable id for a credential request, even for older sealed requests that did not carry one. This keeps fulfillment tracking stable.

**Data flow**: It receives opened credential state and the sealed token text. It returns the explicit request id when present, otherwise hashes the sealed text into a UUID.

**Call relations**: Credential renewal, pending checks, and fulfillment call this to address the same request.

*Call graph*: called by 3 (credential_prompt_pending, fulfill_credential_request, renew_credential_request); 2 external calls (sha256, UUID).


##### `_main_agent`  (lines 1721–1734)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. It is the fallback agent when a surface has no specific binding.

**Data flow**: It receives a workspace id, reads the agent table for the main row, and returns its id or raises if the workspace is malformed.

**Call relations**: Surface installation binding and surface-agent lookup call this when they need the default agent.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 1737–1776)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str, *, routes_ingress: bool) -> None
```

**Purpose**: Creates or updates the binding between a workspace and a surface installation, such as a Slack team. New bindings start on the workspace’s main agent.

**Data flow**: It receives workspace id, surface name, installation id, and whether that installation routes ingress. It upserts the row and converts uniqueness conflicts into `SurfaceInstallationConflict`.

**Call relations**: Both surface OAuth callbacks and manifest-scoped tool access use this one writer so installation rules stay consistent.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.fleet_blob`  (lines 1851–1854)

```
def fleet_blob(self) -> FleetBlobStore
```

**Purpose**: Returns a deploy-wide blob-store view for shared fleet assets. This is separate from the workspace-scoped blob view.

**Data flow**: It reads the current workspace blob backend and wraps that backend in a `FleetBlobStore`.

**Call relations**: Surface code can use this when serving deployment assets rather than workspace files.

*Call graph*: 1 external calls (__init__).


##### `SurfaceContext.conversation_slots`  (lines 1857–1859)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Exposes the deployment’s registered conversation slots to a surface. Conversation slots are extension-provided pieces of conversation data.

**Data flow**: It returns the immutable tuple of bound slots already validated at startup.

**Call relations**: The web surface reads these to render slot-based conversation panels.


##### `SurfaceContext.read_conversation_slot`  (lines 1861–1866)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Runs a conversation slot’s read operation inside the conversation’s agent namespace. Binding the agent keeps agent-scoped reads consistent.

**Data flow**: It receives a bound slot and slot context, temporarily binds the agent id, awaits the provider read, and returns the payload.

**Call relations**: The web surface calls this when a user opens a slot view.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1868–1873)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Runs a slot provider’s summary operation inside the conversation’s agent namespace.

**Data flow**: It receives a bound slot and context, binds the agent id, calls the provider summary method, and returns an integer summary or null.

**Call relations**: The web surface uses this while building conversation slot summaries.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.runtime`  (lines 1876–1878)

```
def runtime(self) -> RuntimeIdentity | None
```

**Purpose**: Returns the runtime identity configured for this surface context. This identifies the service and sandbox runtime when available.

**Data flow**: It simply returns the stored runtime identity or null.

**Call relations**: Surface routes can display or branch on deployment runtime identity without constructing it themselves.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1881–1884)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Reports whether this deployment allows sandbox internet access at all. Agent settings can only narrow this deployment-wide ceiling.

**Data flow**: It returns the stored boolean flag.

**Call relations**: Portal screens use it when deciding whether to show or enable internet-access controls.


##### `SurfaceContext.system_skill_bundle`  (lines 1887–1889)

```
def system_skill_bundle(self) -> SystemSkillBundle
```

**Purpose**: Returns the immutable bundle of deployment-provided system skills. A terminal can cache this before running a turn.

**Data flow**: It returns the stored `SystemSkillBundle` object.

**Call relations**: Runtime and portal views use this shared bundle rather than rebuilding deploy skills per request.


##### `SurfaceContext.models`  (lines 1892–1896)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Returns the model ids this deployment can run. The list is used for model selection in surfaces.

**Data flow**: It returns the stored tuple of model names.

**Call relations**: Runtime-config validation and settings UI depend on this closed set.


##### `SurfaceContext.validate_runtime_config`  (lines 1898–1906)

```
def validate_runtime_config(self, runtime_config: TurnRuntimeConfig) -> None
```

**Purpose**: Refuses a requested turn runtime configuration the deployment cannot execute. This protects admission from unknown models or unsupported environment documents.

**Data flow**: It receives a runtime config, checks model membership and environment-document support, and raises `ValueError` on invalid settings.

**Call relations**: The built-in UFO surface calls this while preparing runtime settings for a turn.

*Call graph*: called by 1 (_runtime_config).


##### `SurfaceContext.store_environment_document`  (lines 1908–1914)

```
async def store_environment_document(self, body: bytes) -> str
```

**Purpose**: Stores an environment document for a future turn when the deployment supports that feature.

**Data flow**: It receives bytes, verifies a document-store function exists, calls it with the workspace blob store, and returns the content digest.

**Call relations**: The UFO surface uses this before admitting turns that pin an environment document.

*Call graph*: called by 1 (store_environment).


##### `SurfaceContext.store_environment_file`  (lines 1916–1921)

```
async def store_environment_file(self, body: bytes) -> str
```

**Purpose**: Stores a file referenced by an environment document. The returned digest can be pinned from the document.

**Data flow**: It receives file bytes, checks that environment storage is enabled, stores the bytes through the configured function, and returns a digest.

**Call relations**: The UFO surface calls this for uploaded environment support files.

*Call graph*: called by 1 (store_environment_file).


##### `SurfaceContext.sandbox_sizes`  (lines 1924–1927)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Returns the sandbox sizes this deployment can provision. An empty tuple means there is no user choice to show.

**Data flow**: It returns the stored tuple of size names.

**Call relations**: Agent settings screens use it to decide whether sandbox-size selection exists.


##### `SurfaceContext.credential`  (lines 1929–1932)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential slot for trusted surface code. The value is never exposed through ordinary scoped extensions.

**Data flow**: It receives a slot name, checks that a credential store is configured, and returns the decrypted value from that workspace.

**Call relations**: Slack surface code uses this for bot tokens, signing secrets, posting, and attachment operations.

*Call graph*: called by 8 (_bot_token, _channel_origin, _ctx_signing_secret, _post_ephemeral, _to_inbound, attach, post, speak).


##### `SurfaceContext.put_member_credential`  (lines 1934–1940)

```
async def put_member_credential(self, member_id: UUID, slot: str, value: str) -> None
```

**Purpose**: Stores a credential value that belongs to one member. This is used after a surface has already authenticated the member.

**Data flow**: It receives member id, slot, and value, converts the slot to a member-scoped slot, and writes the value to the credential store.

**Call relations**: Web account-connection flows call this after provider authorization succeeds.

*Call graph*: called by 2 (anthropic_code, openai_device_poll); 1 external calls (member_slot).


##### `SurfaceContext.member_credential_stored`  (lines 1942–1951)

```
async def member_credential_stored(self, member_id: UUID, slot: str) -> bool
```

**Purpose**: Checks whether a member has filled a particular personal credential slot. It returns only presence, never the secret.

**Data flow**: It receives member id and slot, tries to read the member-scoped credential, and returns true on success or false if missing or no store exists.

**Call relations**: Workspace account screens use this to show connected account state.

*Call graph*: called by 1 (workspace_accounts); 1 external calls (member_slot).


##### `SurfaceContext.clear_member_credential`  (lines 1953–1959)

```
async def clear_member_credential(self, member_id: UUID, slot: str) -> None
```

**Purpose**: Removes one member’s personal credential value. This lets them disconnect or replace an account.

**Data flow**: It receives member id and slot, converts to the member-scoped slot, and clears that value from the credential store.

**Call relations**: The web account-disconnect route calls this after authorizing the member.

*Call graph*: called by 1 (account_disconnect); 1 external calls (member_slot).


##### `SurfaceContext.member_holds_own_model_key`  (lines 1961–1974)

```
async def member_holds_own_model_key(self, member_id: UUID) -> bool
```

**Purpose**: Checks whether a member has supplied any personal model-provider key. This can unlock features that require the member’s own provider account.

**Data flow**: It iterates the configured member-routed slots, tries to read each member-scoped credential, and returns true at the first one found.

**Call relations**: First-run and capability gates call this so the UI and runtime agree on what counts as a personal model key.

*Call graph*: called by 1 (workspace_first_run); 1 external calls (member_slot).


##### `SurfaceContext.credential_prompt_pending`  (lines 1976–1999)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs a value for one slot. This prevents showing prompts that were already fulfilled.

**Data flow**: It opens the sealed request, verifies workspace and slot, derives the request id, checks the fulfillment table and blob marker, and returns whether no fulfillment exists.

**Call relations**: The web surface uses it when rendering pending credential prompts.

*Call graph*: calls 2 internal fn (_credential_request_id, _fulfilled_marker_key); called by 1 (_pending_prompts); 3 external calls (select, workspace_tx, open_credential_request).


##### `SurfaceContext.renew_credential_request`  (lines 2001–2027)

```
async def renew_credential_request(self, sealed: str, member_id: UUID) -> str | None
```

**Purpose**: Renews a sealed credential request for an authenticated member during a page reload. It keeps the handoff alive only within the allowed renewal window.

**Data flow**: It opens the seal with a renewal TTL, verifies workspace and member, checks the member is still an admin, adds stable request metadata, and returns a new sealed token or null.

**Call relations**: Pending-prompt rendering calls this so browser reloads do not immediately strand fresh credential prompts.

*Call graph*: calls 1 internal fn (_credential_request_id); called by 1 (_pending_prompts); 5 external calls (now, workspace_tx, open_credential_request, seal_credential_request, member_is_admin).


##### `SurfaceContext.open_credential_authorization`  (lines 2029–2037)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential authorization token after the workspace is already bound. This recovers the claims needed to finish provider OAuth.

**Data flow**: It checks that credentials are configured, verifies and decrypts the sealed token, and returns the request state or raises if invalid.

**Call relations**: Slack OAuth callbacks use this before fulfilling a requested credential.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 2039–2088)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Verifies and stores the value for one requested credential slot. It ensures the right member, workspace, slot, and declaration before writing any secret.

**Data flow**: It opens the sealed request, validates workspace/member/slot/declaration, checks duplicate private fulfillment markers, writes the credential with the slot merge rule, and records a marker when needed.

**Call relations**: Slack, UFO, and web credential flows call this after collecting a secret or provider token.

*Call graph*: calls 2 internal fn (_credential_request_id, _fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 5 external calls (__init__, now, dumps, warn, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 2090–2098)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation identity to the current workspace. For example, a Slack OAuth callback records which team belongs to the workspace.

**Data flow**: It receives an installation id and delegates to the shared installation-binding helper with ingress routing enabled.

**Call relations**: Slack calls this after OAuth install succeeds.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.address_claim`  (lines 2100–2125)

```
async def address_claim(self, address: str) -> AddressClaim | None
```

**Purpose**: Looks up this workspace’s claim on an address used by an addressed surface, such as a phone number. It tells whether the address is proved or still reserved.

**Data flow**: It receives an address, reads the matching surface-address row for this workspace, normalizes expiry time, and returns an `AddressClaim` or null.

**Call relations**: The iMessage surface checks this while deciding whether an incoming sender may be admitted.

*Call graph*: called by 1 (_admit_message); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.confirm_address`  (lines 2127–2140)

```
async def confirm_address(self, address: str, proved_by: str) -> None
```

**Purpose**: Marks an address claim as proved by a particular inbound message. After this, the reservation no longer expires.

**Data flow**: It receives address and proof id, updates the surface-address row to clear expiry and store the proof, and commits the change.

**Call relations**: The iMessage proof flow calls this once a sender proves ownership.

*Call graph*: called by 1 (_prove); 2 external calls (update, workspace_tx).


##### `SurfaceContext.release_address`  (lines 2142–2151)

```
async def release_address(self, address: str) -> None
```

**Purpose**: Drops this workspace’s claim on an addressed-surface address. The address can then be claimed again.

**Data flow**: It receives an address and deletes the matching surface-address row for this workspace and surface.

**Call relations**: The iMessage proof flow uses this when a claim should be abandoned.

*Call graph*: called by 1 (_prove); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.public_base_url`  (lines 2154–2157)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deployment’s public base URL if configured. Surfaces use it to build callback and portal links.

**Data flow**: It returns the stored URL string or null.

**Call relations**: Surface handlers read this instead of duplicating deployment URL configuration.


##### `SurfaceContext.cookie_secure`  (lines 2160–2164)

```
def cookie_secure(self) -> bool
```

**Purpose**: Decides whether cookies should be marked `Secure`, meaning browsers send them only over HTTPS. It is based on the deployment’s public URL scheme.

**Data flow**: It parses the public base URL scheme and returns the cookie-security decision.

**Call relations**: Browser surfaces use this when setting session cookies.

*Call graph*: 2 external calls (cookie_secure, urlsplit).


##### `SurfaceContext.home_url`  (lines 2166–2176)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link into the deployment’s browser portal, if one exists. Non-browser surfaces can use it when an action needs a web page.

**Data flow**: It receives an optional hash fragment, checks for public base URL and home surface, and returns a full `/surface/<home>` URL or null.

**Call relations**: Report links, Slack reply links, iMessage terminal text, and sites portal links call this.

*Call graph*: called by 4 (report_url, _terminal_text, _into_the_portal, _reply_with_links).


##### `SurfaceContext.report_url`  (lines 2178–2200)

```
async def report_url(self, conversation_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Builds a portal link that opens a conversation at a particular report artifact, when that conversation is portal-readable. The link itself grants no access.

**Data flow**: It builds a home URL, reads the conversation audience, rejects room or externally shared audiences, and returns the portal URL or null.

**Call relations**: Slack and iMessage delivery helpers call this when adding detailed-report links.

*Call graph*: calls 1 internal fn (home_url); called by 2 (_terminal_text, _details_link_line); 4 external calls (select, workspace_tx, audience_member, parse_audience).


##### `SurfaceContext.shared_artifacts`  (lines 2202–2246)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Reads the files associated with a turn in share order. Live surfaces use this to render downloads directly.

**Data flow**: It receives a turn id, queries shared-artifact rows for the workspace and turn, and returns `SharedArtifact` objects.

**Call relations**: Web and UFO surfaces call this when building conversation messages or shared-file views.

*Call graph*: called by 3 (shared_files, _conversation_messages, _events); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 2248–2256)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for one shared artifact in this workspace. It returns null if public artifact delivery is not configured.

**Data flow**: It passes the context’s signing secret, public URL, workspace id, and artifact to `shared_artifact_link` and returns the result.

**Call relations**: Slack, iMessage, web, and UFO surfaces call this when they need a member-facing file link.

*Call graph*: calls 1 internal fn (shared_artifact_link); called by 6 (_terminal_text, _details_link_line, _oversize_link_line, shared_files, _file_payload, _project_slot_context).


##### `SurfaceContext.artifact_preview_link`  (lines 2258–2268)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a safe signed preview-image link for an artifact when possible.

**Data flow**: It passes context signing and URL settings plus the artifact to `shared_artifact_preview_link` and returns a URL or null.

**Call relations**: The web surface uses this while building file payloads and project slot context.

*Call graph*: calls 1 internal fn (shared_artifact_preview_link); called by 2 (_file_payload, _project_slot_context).


##### `SurfaceContext.ingress_url`  (lines 2270–2316)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest: str | None=None) -> str | None
```

**Purpose**: Mints a signed browser URL to a sandbox port for a conversation. This lets a surface show a site running inside the sandbox without exposing raw sandbox authority.

**Data flow**: It receives conversation id, port, entry path, and optional frame/shipped-app data, and returns a signed ingress view URL or null if ingress is unconfigured.

**Call relations**: The sites surface calls this for hosted frames and shipped app bundles.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (mint_ingress_view_url).


##### `SurfaceContext._identity_member`  (lines 2318–2331)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Finds which member a surface-specific external user id is linked to. It is the shared lookup behind same-surface and peer-surface identity adoption.

**Data flow**: It receives a surface name and external id, queries surface identity rows in the workspace, and returns the member id or null.

**Call relations**: `linked_member` uses it for the current surface, and `adopt_identity` uses it for a peer surface.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 2333–2334)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the workspace member linked to this surface’s external user id.

**Data flow**: It receives an external id and delegates to `_identity_member` for the current surface.

**Call relations**: Sample, sites, Slack, UFO, and web surfaces use it during authentication or member resolution.

*Call graph*: calls 1 internal fn (_identity_member); called by 7 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, _authenticated_member, _authenticate).


##### `SurfaceContext.member_has_access`  (lines 2336–2338)

```
async def member_has_access(self, member_id: UUID) -> bool
```

**Purpose**: Checks whether a member is currently admitted to the workspace. This protects routes after identity is resolved.

**Data flow**: It receives a member id, reads the seat state through `Seats`, and returns whether that member authority is allowed.

**Call relations**: Web, sites, Slack, and UFO authentication paths call this before serving member data or admitting messages.

*Call graph*: called by 4 (_viewer, _folds_into_live_turn, _authenticated_member, _authenticate); 3 external calls (__init__, __init__, workspace_tx).


##### `SurfaceContext.is_operator_workspace`  (lines 2340–2347)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace is the fleet operator’s own workspace. This gates internal-only displays.

**Data flow**: It reads the workspace domain and compares it to the operator email domain constant.

**Call relations**: Surfaces can call this before showing operator diagnostics or accounting footers.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 2349–2372)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to the member already known by another surface. This lets the same human keep one member identity across surfaces.

**Data flow**: It looks up the peer surface identity, inserts a current-surface identity row if found, logs races, and returns the member id or null.

**Call relations**: The sample live admit path uses this to bridge identities between surfaces.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 2374–2396)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing workspace member by email. It does not create a member.

**Data flow**: It receives external id and email, finds the oldest case-insensitive member email match, and delegates to `link_member_id` or returns null.

**Call relations**: Authentication and join flows use this as the first step before optional domain-based member creation.

*Call graph*: calls 1 internal fn (link_member_id); called by 5 (join_member, _surface_ingest, _viewer, _authenticated_member, _authenticate); 2 external calls (select, workspace_tx).


##### `SurfaceContext.link_member_id`  (lines 2398–2427)

```
async def link_member_id(self, external_id: str, member_id: UUID) -> UUID | None
```

**Purpose**: Links this surface’s external id to a specific existing member. The caller is responsible for having proved that member.

**Data flow**: It verifies the member exists in the workspace, inserts a surface identity row, logs insert races, and returns the member id or null.

**Call relations**: `link_member` calls this after resolving email to member id.

*Call graph*: called by 1 (link_member); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 2429–2446)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links a surface user by email, creating a new member if the email domain matches the workspace’s own domain. This supports first-contact teammate joins on trusted channels.

**Data flow**: It tries `link_member`, compares email domain with workspace domain if no member exists, creates the member when allowed, and links again.

**Call relations**: Slack member resolution uses this when Slack has verified the sender’s email.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 2448–2458)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the standard query for finding a conversation by this surface’s queue key. A queue key is the surface’s own stable thread/channel identifier.

**Data flow**: It receives a queue key and returns a SQL select for the matching workspace, surface, and key.

**Call relations**: Conversation find/create and terminal op lookup share this exact query shape.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 2460–2466)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for this surface queue key without creating one. This is useful when a surface wants to know whether it is already participating.

**Data flow**: It receives a queue key, executes `_conversation_lookup`, and returns the conversation id or null.

**Call relations**: Slack and UFO routes call this before deciding how to treat incoming or file-related requests.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 4 (_handle_answer_submit, _participating_conversation, workspace_file, workspace_listing); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 2468–2481)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation is permanently bound to. This lets route handlers enforce the agent wall before reading content.

**Data flow**: It receives a conversation id, queries the workspace conversation row, and returns the agent id or null.

**Call relations**: UFO and web surfaces use it when resolving opaque conversation links.

*Call graph*: called by 2 (_joined_conversation, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 2483–2486)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation in this workspace through the shared title helper.

**Data flow**: It receives conversation id and title and calls the module-level retitle function with the context workspace id.

**Call relations**: Slack, web panel submits, and web conversation open paths use it when a better title is known.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 4 (_admit_inbound, submit_action, submit_intent, _open_conversation).


##### `SurfaceContext.conversation_for`  (lines 2488–2582)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation for a surface queue key, with audience and agent binding rules enforced. It is the usual way a surface turns an external thread into a core conversation.

**Data flow**: It receives queue key, audience, optional agent/conversation id, and label. It reuses an existing row, narrows audience or updates label when needed, or inserts a new conversation bound to the chosen agent.

**Call relations**: iMessage, sample, Slack, UFO, web chat, and panel routes call this before admitting messages or uploads.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, _handle_answer_submit, channel, workspace_upload, submit_action, submit_intent, _open_conversation (+1 more)); 9 external calls (insert, select, update, workspace_tx, log, audience_member, narrow_audience, parse_audience, uuid4).


##### `SurfaceContext._surface_agent`  (lines 2584–2596)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Finds the agent currently bound to this surface installation, falling back to the main agent.

**Data flow**: It queries surface installation for the current workspace and surface. If none exists, it returns `_main_agent`.

**Call relations**: `conversation_for` and ambient-reply fallback model selection call this.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (_surface_agent_model, conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 2598–2658)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Decides whether an unaddressed ambient message should start a turn. It errs toward admitting when the classifier fails, so user requests are less likely to be dropped.

**Data flow**: It receives the message and recent history, runs the ambient reply classifier with a timeout, handles spend refusals and fallback model selection, logs the outcome, and returns a boolean.

**Call relations**: Slack and iMessage surfaces call this before admitting ambient channel traffic.

*Call graph*: calls 1 internal fn (_surface_agent_model); called by 2 (_admit_message, _ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext._surface_agent_model`  (lines 2660–2670)

```
async def _surface_agent_model(self) -> str
```

**Purpose**: Reads the model configured on the agent bound to this surface. This supports fallback ambient classification.

**Data flow**: It resolves the surface agent id, reads that agent’s model from the database, and returns the model string.

**Call relations**: `ambient_reply_wanted` calls it when retrying with the surface agent’s own model.

*Call graph*: calls 1 internal fn (_surface_agent); called by 1 (ambient_reply_wanted); 2 external calls (select, workspace_tx).


##### `SurfaceContext.admit`  (lines 2672–2707)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, comment:
```

**Purpose**: Admits a member message, comment, or prepared intent into the core turn queue. This is the central privileged action a trusted surface can perform.

**Data flow**: It receives conversation id, body, idempotency key, context, speaker, and optional intent/comment/runtime settings, and delegates to the injected `MemberAdmitter`.

**Call relations**: All chat surfaces and panel-submit routes use this after resolving workspace, member, conversation, and access.

*Call graph*: called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, _handle_answer_submit, _channel_message, _send, submit_action, submit_intent, _admit_chat (+1 more)).


##### `SurfaceContext.connect_url`  (lines 2709–2715)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates a provider-connect authorization URL for a terminal connect request. It ties the handoff to the speaking member and turn.

**Data flow**: It receives turn id and member id, loads the installed connect flow, and returns an authorization URL or raises a request error if unavailable.

**Call relations**: Slack, iMessage, and web surfaces call this when a terminal asks the member to connect an account.

*Call graph*: called by 3 (_terminal_text, _handle_connect_click, connect_handoff); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.held_accounts`  (lines 2717–2737)

```
async def held_accounts(self, owner_member_id: UUID) -> dict[str, str]
```

**Purpose**: Lists the provider accounts owned by one member, keyed by provider. This lets UI controls name already connected accounts.

**Data flow**: It receives owner member id, reads matching connection rows ordered by update time, and returns provider-to-label mappings.

**Call relations**: The web surface uses it while building connect controls.

*Call graph*: called by 1 (_connect_controls); 2 external calls (select, workspace_tx).


##### `SurfaceContext.connect_available`  (lines 2739–2746)

```
def connect_available(self) -> bool
```

**Purpose**: Reports whether the deployment has the connect flow configured. If false, surfaces should not show connect buttons.

**Data flow**: It tries to load the installed connect flow and returns false if it is unavailable.

**Call relations**: The web surface uses this in controls, events, and provider-label rendering.

*Call graph*: called by 3 (_connect_controls, _events, _provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connect_label`  (lines 2748–2750)

```
def connect_label(self, provider: str) -> str
```

**Purpose**: Returns the human-facing label for a connect provider.

**Data flow**: It receives a provider id, reads the installed connect flow, and asks it for the provider label.

**Call relations**: The web surface calls this when rendering provider names.

*Call graph*: called by 1 (_provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connector_catalog`  (lines 2752–2754)

```
async def connector_catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads one page of the catalog of providers the deployment can connect to.

**Data flow**: It receives search query, limit, and cursor, delegates to the connector registry, and returns a catalog page.

**Call relations**: The web connector-catalog route exposes this to the portal.

*Call graph*: called by 1 (connector_catalog).


##### `SurfaceContext.admitted_body`  (lines 2756–2781)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Finds what message body was admitted under an idempotency key. This helps surfaces reconcile repeated button clicks or redeliveries.

**Data flow**: It checks first turn rows and then queued inbound-message rows for the key, returning the stored inbound/body or null.

**Call relations**: Slack and web surfaces call this to tell which attempted answer or admission actually won.

*Call graph*: called by 3 (_handle_answer_submit, _unseen_tail, _admit_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 2783–2797)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member who owns the conversation containing a turn. Live surfaces use this to stop one member from tailing another member’s turn.

**Data flow**: It receives a turn id, joins turn to conversation, and returns the conversation member id or null.

**Call relations**: Sample and web live-turn gates call this before streaming turn frames.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 2799–2805)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops a running turn in an already-authorized conversation. It returns enough information for the surface to switch to a follow-up turn if one starts.

**Data flow**: It receives conversation id and turn id, delegates to the injected stopper with workspace id, and returns a `Stopped` result.

**Call relations**: UFO and web stop routes call this for member stop actions.

*Call graph*: called by 2 (_channel_stop, _stop_chat).


##### `SurfaceContext.retract_arrival`  (lines 2807–2825)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Deletes a pending message that the member sent but no turn has consumed yet. It only retracts that member’s own unconsumed message.

**Data flow**: It receives conversation id, arrival id, and member id, deletes the matching unconsumed inbound row, and returns whether one row was removed.

**Call relations**: The UFO surface uses this for unsend behavior.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 2827–2844)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has reached a terminal state by reading the database. Missing turns count as terminal because there is nothing to report.

**Data flow**: It receives a turn id, reads its status in this workspace, and returns true for missing, done, failed, or cancelled.

**Call relations**: The UFO surface uses this to avoid posting progress updates after the final answer already landed.

*Call graph*: called by 1 (_channel_message); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 2846–2863)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the newest turn in a conversation. This helps surfaces resume or render the latest live state after reconnects.

**Data flow**: It receives conversation id, orders turns by sequence descending, and returns the newest turn id or null.

**Call relations**: Slack, UFO, and web surfaces call this when resolving conversations, channel messages, stops, and reloads.

*Call graph*: called by 6 (_participating_conversation, _channel_message, _channel_op_reply, _channel_stop, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.absorbing_turn`  (lines 2865–2914)

```
async def absorbing_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Predicts whether a new message would fold into an existing live turn instead of founding a new one. It is advisory; admission repeats the decision under lock.

**Data flow**: It reads the oldest nonterminal turn in the conversation, checks spend and balance gates, and returns the turn id only if admission would currently fold into it.

**Call relations**: Slack uses this before ambient classification so it does not drop messages that should join a live turn.

*Call graph*: called by 1 (_folds_into_live_turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.tail`  (lines 2916–2922)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of turn frames from the runtime hub. The caller gets updates until the turn ends.

**Data flow**: It receives a turn id and cursor and returns the injected tailer’s async context manager.

**Call relations**: Debugger, sample, web panels, and web event streams call this for live updates.

*Call graph*: called by 6 (_events, _surface_frames, _intent_result, submit_action, _events, object_write).


##### `SurfaceContext.latest_activity`  (lines 2924–2928)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Peeks at the newest retained activity for a turn without opening a stream.

**Data flow**: It receives a turn id and delegates to the injected tailer, returning an activity or null.

**Call relations**: The web agents-status route calls this for running agent status.

*Call graph*: called by 1 (agents_status).


##### `SurfaceContext.spend_rollup`  (lines 2930–2933)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace usage and cost information for a time window or all time.

**Data flow**: It receives an optional window in seconds, opens a workspace transaction, and returns the spend rollup report.

**Call relations**: Sample and web usage routes call this for workspace spend displays.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 2935–2950)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes streamed bytes into a conversation’s sandbox workspace before a turn runs. It enforces the workspace write size limit.

**Data flow**: It receives conversation id, relative path, and byte chunks, accumulates up to the size limit, and writes the file through the sandbox carrier.

**Call relations**: Attachment delivery and upload routes call this when bytes pass through the server process.

*Call graph*: called by 4 (deliver_attachment, _downloaded_files, _surface_ingest, workspace_upload).


##### `SurfaceContext.mint_upload`  (lines 2952–2974)

```
async def mint_upload(self, filename: str, size_bytes: int, checksum_sha256: str) -> UploadGrant | None
```

**Purpose**: Creates a direct-to-S3 upload grant for a browser attachment. The signed grant proves the later send is naming a key this deployment minted.

**Data flow**: It receives filename, size, and checksum. On S3 it creates a fresh artifact key, asks for a presigned PUT URL, signs the key, and returns an `UploadGrant`; on other stores it returns null.

**Call relations**: The web upload-start route calls this before the browser uploads a file.

*Call graph*: calls 2 internal fn (_upload_signature, inbox_name); called by 1 (upload_start); 2 external calls (__init__, uuid4).


##### `SurfaceContext.verify_upload_grant`  (lines 2976–2982)

```
def verify_upload_grant(self, blob_key: str, signature: str) -> bool
```

**Purpose**: Checks whether an upload key was signed by this workspace’s deployment. This prevents a send from claiming arbitrary artifact bytes.

**Data flow**: It receives blob key and signature, rebuilds the signed upload message, verifies the detached signature, and returns true or false.

**Call relations**: The web chat-inbound path calls this before accepting browser-uploaded artifact keys.

*Call graph*: calls 1 internal fn (_upload_message); called by 1 (_chat_inbound); 1 external calls (verify_detached).


##### `SurfaceContext._upload_signature`  (lines 2984–2985)

```
def _upload_signature(self, blob_key: str) -> str
```

**Purpose**: Signs an upload artifact key for this workspace. The signature is returned to the browser as proof of a valid upload grant.

**Data flow**: It receives a blob key, builds the upload message, signs it with the artifact token secret, and returns the signature text.

**Call relations**: `mint_upload` calls this while creating an `UploadGrant`.

*Call graph*: calls 1 internal fn (_upload_message); called by 1 (mint_upload); 1 external calls (sign_detached).


##### `SurfaceContext._upload_message`  (lines 2987–2988)

```
def _upload_message(self, blob_key: str) -> bytes
```

**Purpose**: Builds the exact bytes covered by an upload signature. Including workspace id prevents a grant from being reused across workspaces.

**Data flow**: It receives a blob key and returns encoded text containing the upload purpose, workspace id, and key.

**Call relations**: Both signing and verification use this so they agree on what was authorized.

*Call graph*: called by 2 (_upload_signature, verify_upload_grant).


##### `SurfaceContext.store_inbound_file`  (lines 2990–3008)

```
async def store_inbound_file(self, filename: str, chunks: AsyncIterator[bytes]) -> str
```

**Purpose**: Streams an inbound file through this process into artifact storage. This is for dev stores or provider downloads that cannot upload directly from the browser.

**Data flow**: It receives filename and byte chunks, wraps the stream with a size counter, stores under a fresh safe artifact key, and returns the key.

**Call relations**: Slack downloads and web inline upload delivery call this.

*Call graph*: calls 1 internal fn (inbox_name); called by 2 (_download_files, _deliver_uploads); 1 external calls (uuid4).


##### `SurfaceContext.store_inbound_file.measured`  (lines 2998–3004)

```
async def measured() -> AsyncIterator[bytes]
```

**Purpose**: Counts streamed inbound-file bytes while passing them through. It stops oversized files before they are fully stored.

**Data flow**: It reads each incoming chunk, adds its length to a running count, raises if the limit is exceeded, and yields the chunk onward.

**Call relations**: `store_inbound_file` passes this generator into blob storage so size checking happens during streaming.


##### `SurfaceContext.deliver_attachment`  (lines 3010–3042)

```
async def deliver_attachment(self, conversation_id: UUID, blob_key: str, rel: str) -> None
```

**Purpose**: Copies a stored attachment into a conversation’s sandbox workspace. On S3 it lets the sandbox fetch directly; otherwise the server streams the bytes.

**Data flow**: It receives conversation id, blob key, and target path. It either runs a sandbox probe with a presigned GET URL or calls `write_workspace_file` with a blob stream.

**Call relations**: Slack and web upload delivery call this after storing or verifying attachments.

*Call graph*: calls 1 internal fn (write_workspace_file); called by 2 (_download_files, _deliver_uploads); 4 external calls (PurePosixPath, quote, shell_path, workspace_path).


##### `SurfaceContext.attach_member_files`  (lines 3044–3069)

```
async def attach_member_files(self, turn_id: UUID, blob_keys: tuple[str, ...]) -> None
```

**Purpose**: Records member-attached files as shared artifacts of a turn and tries to draw document covers. The message admission itself is not failed by preview problems.

**Data flow**: It receives a turn id and artifact keys, records each attachment row, then attempts cover rendering within one shared time budget.

**Call relations**: Slack and web admission paths call this after the message carrying the files is admitted.

*Call graph*: calls 2 internal fn (_draw_attachment_cover, _record_attachment); called by 2 (_admit_inbound, _admit_chat); 1 external calls (monotonic).


##### `SurfaceContext._record_attachment`  (lines 3071–3099)

```
async def _record_attachment(self, turn_id: UUID, blob_key: str) -> None
```

**Purpose**: Writes one member-attached file into the shared-artifact table. This makes member uploads visible through the same file system as agent-shared files.

**Data flow**: It receives turn id and blob key, determines filename, size, and media type, then inserts the artifact row if it does not already exist.

**Call relations**: `attach_member_files` calls this for each file before preview rendering.

*Call graph*: called by 1 (attach_member_files); 5 external calls (now, PurePosixPath, workspace_tx, artifact_media_type, uuid4).


##### `SurfaceContext._draw_attachment_cover`  (lines 3101–3133)

```
async def _draw_attachment_cover(self, blob_key: str, deadline: float) -> None
```

**Purpose**: Attempts to render a preview cover for a document attachment. It skips unsupported files, images, unconfigured preview service, and expired budgets.

**Data flow**: It receives blob key and deadline, checks eligibility, calls the preview renderer, and updates the artifact row with preview blob metadata if successful.

**Call relations**: `attach_member_files` calls this after recording each attachment.

*Call graph*: called by 1 (attach_member_files); 10 external calls (now, AsyncClient, PurePosixPath, update, monotonic, workspace_tx, log, raster_image_media_type, get, render_document_cover).


##### `SurfaceContext.render_preview`  (lines 3135–3194)

```
async def render_preview(self, kind: str, data: bytes, start_page: int=1, pages: int=1) -> PreviewRender | None
```

**Purpose**: Renders one or more preview pages for an uploaded document without storing the result. This lets a composer show a preview before sending.

**Data flow**: It receives document kind, bytes, start page, and count, posts them to the preview service, accepts PNG or ZIP responses, unpacks pages, and returns `PreviewRender` or null.

**Call relations**: The web preview route calls this while a member is composing a message.

*Call graph*: called by 1 (preview); 5 external calls (__init__, AsyncClient, BytesIO, dumps, ZipFile).


##### `SurfaceContext.list_agents`  (lines 3196–3233)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists active agents in the workspace, main agent first. Surfaces use this when a member can choose or inspect agents.

**Data flow**: It queries non-archived agent rows, orders them, and returns `AgentSummary` records.

**Call relations**: Sites, UFO, web audience, created-apps, and subagent-node views call this.

*Call graph*: called by 6 (_shipped_frame, frame, _reachable_agents, web_audience, _created_apps, _subagent_nodes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_archived_agents`  (lines 3235–3270)

```
async def list_archived_agents(self) -> tuple[ArchivedAgent, ...]
```

**Purpose**: Lists archived agents/apps so a portal can show restore options.

**Data flow**: It queries archived agent rows, chooses archived display names where present, orders newest first, and returns `ArchivedAgent` records.

**Call relations**: The web agents-index route calls this.

*Call graph*: called by 1 (agents_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_extension_agent_ids`  (lines 3272–3287)

```
async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]
```

**Purpose**: Finds agents that have private extension conversations for a member. This helps compute which agents are reachable to that member.

**Data flow**: It receives member id, queries distinct agent ids from matching private extension conversations, and returns them as a frozen set.

**Call relations**: UFO reachable-agent and web audience logic call this.

*Call graph*: called by 2 (_reachable_agents, web_audience); 3 external calls (select, workspace_tx, conversation_audience).


##### `SurfaceContext.agent_detail`  (lines 3289–3347)

```
async def agent_detail(self, agent_id: UUID, member_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads one agent’s settings, prompt digest, and bound surfaces for a portal settings page.

**Data flow**: It receives agent and member ids, reads the agent row and installation surfaces, computes prompt digest, and returns `AgentDetail` or null.

**Call relations**: Web panel code calls this while completing and displaying agent settings.

*Call graph*: called by 2 (_complete_agent_spec, agent_settings); 4 external calls (__init__, select, workspace_tx, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 3349–3361)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for a registered object kind, such as listable fields and schema. It returns null when the deployment has no such kind.

**Data flow**: It receives kind name, looks up the bound kind, and returns a `PortalKind` with sorted list fields and schema.

**Call relations**: Web object gates, action views, object writes, and first-run pages use this.

*Call graph*: called by 4 (_object_gate, action_views, object_write, workspace_first_run); 1 external calls (__init__).


##### `SurfaceContext.object_actions`  (lines 3363–3378)

```
def object_actions(self, kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Builds the action controls the portal can show for an object target. Dispatch still rechecks permissions later.

**Data flow**: It receives kind, action binding, and optional object name/generation, and asks the object-view helper to produce `ActionView` records.

**Call relations**: Web panels and workspace pages call this when rendering buttons and prepared actions.

*Call graph*: called by 7 (submit_action, _connect_declared, action_views, workspace_credentials, workspace_first_run, workspace_memory, workspace_team); 1 external calls (presented_action_views).


##### `SurfaceContext.frame_admits`  (lines 3380–3383)

```
def frame_admits(self, callable_id: str) -> bool
```

**Purpose**: Checks whether an embedded app frame may post a particular callable. This is a declaration-based safety gate.

**Data flow**: It receives a callable id and returns whether that id is in the frame-admissible set.

**Call relations**: Web panel preparation and submit code call this before accepting frame-originated actions.

*Call graph*: called by 2 (_prepare_panel_intent, submit_action).


##### `SurfaceContext.agent_skills`  (lines 3385–3424)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists deploy-provided and member-authored skills available to an agent. Deploy skills win name conflicts.

**Data flow**: It binds the agent, loads member skills, drops member skills shadowed by deploy skills, and returns `PortalSkill` records for top-level deploy and accepted member skills.

**Call relations**: The web skills route calls this to render skill contents.

*Call graph*: called by 1 (skills); 3 external calls (__init__, log, agent).


##### `SurfaceContext.model`  (lines 3427–3431)

```
def model(self) -> 'SurfaceModel | None'
```

**Purpose**: Returns the optional metered model access available to surface routes.

**Data flow**: It returns the stored `SurfaceModel` object or null.

**Call relations**: Routes that need surface-side model work check this before calling a model.


##### `SurfaceContext.memory_available`  (lines 3434–3438)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory-search provider is installed. Surfaces should hide memory views when false.

**Data flow**: It returns whether the context holds a memory provider.

**Call relations**: Memory search, recent memory, and memory-kind calls require this gate.


##### `SurfaceContext.search_memory`  (lines 3440–3450)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches memory visible to a given reader. It uses the same reader shape as turn tools so portal and agent visibility match.

**Data flow**: It receives a source reader and queries, verifies a memory provider exists, delegates the search, and returns matches.

**Call relations**: The web workspace-memory page calls this after checking memory availability.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 3452–3465)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items visible to selected subjects. This is browsing, not semantic search.

**Data flow**: It receives subjects, limit, optional kinds, and cursor, checks provider presence, delegates to the provider, and returns a paged result.

**Call relations**: The web recalled-items and workspace-memory routes call this.

*Call graph*: called by 2 (_recalled, workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 3468–3473)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the memory item classes the installed provider can list. This powers filters in the portal.

**Data flow**: It checks that memory is installed and returns the provider’s listable kind names.

**Call relations**: Memory pages call this after `memory_available` says the provider exists.


##### `SurfaceContext.member_spend`  (lines 3475–3480)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads one member’s usage and cap report for a selected time window or all time.

**Data flow**: It receives member id and optional window, opens a transaction, and returns the member spend report from `SpendRollup`.

**Call relations**: The web workspace-usage route calls this for member-specific accounting.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 3482–3534)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to one agent that the viewer may see. Private grants are visible only to their owner unless the caller is admin.

**Data flow**: It receives agent id, member id, and admin flag, queries connector grants joined to connections and owners, applies visibility, and returns `ConnectionView` records.

**Call relations**: The web connections route calls this for an agent’s connection panel.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.list_connections`  (lines 3536–3615)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists visible connector accounts in the workspace and the live agents attached to each. It omits archived agents from the attached-agent list.

**Data flow**: It receives member id and admin flag, queries connections with optional grants and agents, groups rows by provider/account, and returns `ConnectionPoolView` records.

**Call relations**: The web connection pool and held-provider helpers call this.

*Call graph*: called by 2 (_held_providers, connection_pool); 7 external calls (__init__, __init__, and_, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.github_coverage`  (lines 3617–3654)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports whether the workspace has visible GitHub API connections and GitHub sources. This supports GitHub setup/status UI.

**Data flow**: It receives member id and admin flag, builds visibility predicates, checks existence of GitHub connection and source rows, and returns booleans.

**Call relations**: The web GitHub coverage route calls this.

*Call graph*: called by 1 (github_coverage); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.recent_object_changes`  (lines 3656–3695)

```
async def recent_object_changes(self, limit: int) -> tuple[ObjectChange, ...]
```

**Purpose**: Reads recent object-change audit rows for the workspace. The calling surface must admin-gate this operator-style record.

**Data flow**: It receives a limit, queries the object-change journal newest first, normalizes timestamps, and returns `ObjectChange` records.

**Call relations**: The web object-changes route calls this after its own authorization.

*Call graph*: called by 1 (object_changes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversation_artifacts`  (lines 3697–3775)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int, role: ArtifactRole | None=None) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists files shared in one conversation, optionally narrowed by artifact role. This powers conversation artifact shelves and transcript aids.

**Data flow**: It receives conversation id, limit, and optional role, queries artifact rows joined to turn/conversation/member, resolves conversation source, and returns `ListedArtifact` records.

**Call relations**: The web surface calls this when building project slot context and transcript support data.

*Call graph*: called by 2 (_project_slot_context, _transcript_aids); 5 external calls (__init__, __init__, __init__, select, workspace_tx).


##### `SurfaceContext.agent_turn_statuses`  (lines 3777–3905)

```
async def agent_turn_statuses(self, agent_ids: Sequence[UUID], member_id: UUID) -> tuple[AgentTurnStatus, ...]
```

**Purpose**: Builds a compact live-status summary for each requested agent as seen by one member. Private turns from other members are excluded.

**Data flow**: It receives agent ids and member id, queries readable live turns, newest activity time, and last terminal status, then returns one `AgentTurnStatus` per input id.

**Call relations**: The web agents-status route polls this for dashboard status.

*Call graph*: called by 1 (agents_status); 5 external calls (__init__, case, select, workspace_tx, readable_audiences).


##### `SurfaceContext.agent_setup`  (lines 3907–3946)

```
async def agent_setup(self, agent_id: UUID, member_id: UUID) -> SetupState
```

**Purpose**: Computes what an agent still needs before it can work, such as accounts, credentials, and standing orders. It is member-aware for private grants.

**Data flow**: It receives agent and member ids, defines an `armed` helper for standing-order objects, binds workspace context, and calls setup-state logic.

**Call relations**: Web agent-setup and workspace-starter routes call this.

*Call graph*: called by 2 (agent_setup, workspace_starters); 2 external calls (setup_state, ws).


##### `SurfaceContext.agent_setup.armed`  (lines 3929–3943)

```
async def armed(kind: str, name: str | None) -> ArmedOrder
```

**Purpose**: Checks whether a setup-required order object exists for an agent, and returns its schedule when available.

**Data flow**: It receives kind and optional name, reads either one object detail or a member object page, and returns an `ArmedOrder` describing presence and schedule.

**Call relations**: `SurfaceContext.agent_setup` passes this helper into the setup-state calculation.

*Call graph*: calls 2 internal fn (list_member_objects, member_object); 2 external calls (__init__, __init__).


##### `SurfaceContext.list_member_objects`  (lines 3948–3974)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Reads a paged list of objects of one kind as a signed-in member. The object kind’s own member-list gate decides what is visible.

**Data flow**: It receives kind, agent, member, admin flag, and query. It verifies the kind supports member listing, binds the agent, stamps supported fields into the query, and returns a page or null.

**Call relations**: Agent setup and web object-index helpers call this.

*Call graph*: called by 3 (armed, _bound_page, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 3976–3991)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one object detail as a signed-in member. Hidden and absent objects both return null.

**Data flow**: It receives kind, name, agent, member, and admin flag, verifies the kind supports member detail reads, binds the agent, and delegates to the store.

**Call relations**: Agent setup and web object-detail routes call this.

*Call graph*: called by 2 (armed, object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 3993–4015)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants attached to a conversation for a member, when the object kind supports that view.

**Data flow**: It receives kind, agent, conversation, member, admin flag, and limit, verifies support, binds the agent, and returns grant rows or null.

**Call relations**: The web project-slot context builder calls this.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 4017–4044)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists declared credential slots and whether each is filled, without exposing any secret value.

**Data flow**: It reads filled credential slot names from the workspace, maps declarations to object names, and returns sorted `CredentialSlotView` records.

**Call relations**: Web credential pages and panel refusal rendering call this.

*Call graph*: called by 2 (_intent_refusal, workspace_credentials); 4 external calls (__init__, select, workspace_tx, named_slots).


##### `SurfaceContext.workspace_domain`  (lines 4046–4052)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Reads the workspace’s trusted signup domain, if it has one. Personal-email workspaces return null.

**Data flow**: It opens a workspace transaction, asks the seats helper for the domain, and returns a string or null.

**Call relations**: Join-member, founding-domain, and operator-workspace checks call this.

*Call graph*: called by 3 (founding_domain, is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.founding_domain`  (lines 4054–4062)

```
async def founding_domain(self) -> str | None
```

**Purpose**: Returns the hosted-signup domain that founded this workspace, only when the workspace id matches the signup policy for that domain.

**Data flow**: It reads `workspace_domain`, verifies the domain maps back to this workspace id, and returns the domain or null.

**Call relations**: The web first-run page calls this to decide whether to offer a website.

*Call graph*: calls 1 internal fn (workspace_domain); called by 1 (workspace_first_run); 1 external calls (signup_workspace_id).


##### `SurfaceContext.list_members`  (lines 4064–4072)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Lists the workspace roster in stable email order. Admin-only changes are handled elsewhere.

**Data flow**: It reads the current seat snapshot and returns member entries sorted by email.

**Call relations**: The web workspace-team page calls this.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 4074–4121)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists live source bindings visible to a member or admin. Removed sources are excluded.

**Data flow**: It receives member id and admin flag, queries source rows with owner emails and health, applies visibility, expands connector binding fields, and returns `SourceView` records.

**Call relations**: The web workspace-sources page calls this.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 4123–4139)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations bound to this workspace and the agent each one routes to.

**Data flow**: It queries surface-installation rows for the workspace, orders by surface, and returns `InstallationSummary` records.

**Call relations**: The web first-run, held-provider, and workspace-surfaces views call this.

*Call graph*: called by 3 (_held_providers, workspace_first_run, workspace_surfaces); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_surfaces`  (lines 4141–4156)

```
async def member_surfaces(self, member_id: UUID) -> frozenset[str]
```

**Purpose**: Finds which surfaces can reach a member, either through linked identity or proved address. Unproved reservations do not count.

**Data flow**: It receives member id, unions surface identity and proved surface address rows, and returns a frozen set of surface names.

**Call relations**: The web workspace-surfaces page calls this for member reachability.

*Call graph*: called by 1 (workspace_surfaces); 3 external calls (select, union, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 4158–4207)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists recent conversations across the workspace for debugging. It is bounded and ordered by latest activity.

**Data flow**: It builds activity counts and last-turn timestamps, joins conversation and member rows, and returns `ConversationSummary` records.

**Call relations**: The debugger surface uses this for its conversation list.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 4209–4234)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Lists conversations for one agent by delegating to the shared conversation directory. It binds the operation to this workspace.

**Data flow**: It receives filters and passes them to `ConversationDirectory.list`, returning listed conversations.

**Call relations**: UFO and web conversation routes call this for agent-specific conversation lists and resolution.

*Call graph*: called by 6 (_joined_conversation, conversations, _member_chat, _named, _resolve_chat, conversations); 1 external calls (__init__).


##### `SurfaceContext.readable_conversation`  (lines 4236–4277)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Checks whether a member may read a conversation’s content. Admins need a recent recorded disclosure for another member’s private conversation.

**Data flow**: It receives conversation, agent, member, and admin flag, reads the conversation audience, compares readable audiences, checks disclosure rows when needed, and returns a boolean.

**Call relations**: The web surface uses this as the central gate before transcript and conversation content routes.

*Call graph*: called by 1 (_readable_conversation); 6 external calls (now, select, workspace_tx, audience_member, parse_audience, readable_audiences).


##### `SurfaceContext.conversation_audience`  (lines 4279–4289)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience bound to a conversation for a given agent. The audience describes who may read or participate.

**Data flow**: It receives conversation and agent ids, reads the stored audience string, parses it, and returns an `Audience` or null.

**Call relations**: The web slot-context builder calls this before projecting conversation data.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, workspace_tx, parse_audience).


##### `SurfaceContext.conversation_subagent_turns`  (lines 4291–4328)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Reads all subagent turns spawned under a conversation’s turns, transitively. This lets views nest subagent work under the parent turn.

**Data flow**: It builds a recursive query from the conversation’s root turns through parent-turn links, orders breadth-first, and returns `Turn` records.

**Call relations**: The web surface uses this for event streams, slot targets, and transcript aids.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_events, _slot_target, _transcript_aids); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 4330–4346)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists a conversation’s recent turns in admission order. It returns full durable turn rows.

**Data flow**: It queries the newest turns up to the limit in reverse order, then reverses them back to oldest-first `Turn` records.

**Call relations**: Debugger and web transcript-aid routes call this.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _transcript_aids); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 4348–4383)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Finds message references in a conversation that came from scheduled runs or subagent results rather than member words. This helps transcript displays avoid showing machine envelopes as user bubbles.

**Data flow**: It queries both turn ids and inbound-message ids matching machine-origin rules and returns them as strings.

**Call relations**: The web conversation-message and history-message builders call this.

*Call graph*: called by 2 (_conversation_messages, _history_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 4385–4435)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn, its accounting rows, and its immediate subagent child turns. It returns null if the turn is not in this workspace.

**Data flow**: It queries the turn row, child turns, and ledger entries, converts them into `TurnDetail`, and returns it.

**Call relations**: Debugger and web turn/event/conversation resolution paths call this.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.turn_steps`  (lines 4437–4450)

```
async def turn_steps(self, turn_id: UUID) -> tuple[TurnStep, ...] | None
```

**Purpose**: Reads durable workflow steps for a turn after verifying workspace ownership.

**Data flow**: It receives a turn id, reads its running attempt id, and delegates to the injected turn-step source using that attempt or the turn id.

**Call relations**: The debugger turn-steps route calls this.

*Call graph*: called by 1 (turn_steps); 2 external calls (select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 4452–4493)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Reads admitted messages whose text may not yet appear in the written transcript. This keeps a reload from hiding messages sent during a running turn.

**Data flow**: It verifies the conversation belongs to the workspace, queries unconsumed and optionally draining-turn-consumed inbound rows, and returns `QueuedArrival` records.

**Call relations**: The web conversation-message builder calls this while projecting live conversation state.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 4495–4527)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Reads speaker attribution for member-admitted queued messages, whether waiting or already drained. This labels folded messages in transcript projections.

**Data flow**: It verifies conversation ownership, reads inbound-message context and speaker ids, extracts sender/question from context, and returns `SpokenArrival` records.

**Call relations**: The web conversation and history message builders call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (_conversation_messages, _history_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.keyed_admissions`  (lines 4529–4565)

```
async def keyed_admissions(self, conversation_id: UUID) -> tuple[KeyedAdmission, ...]
```

**Purpose**: Lists all messages in a conversation admitted with idempotency keys. This lets projections recognize their own admissions later.

**Data flow**: It verifies conversation ownership, unions keyed turn rows and keyed inbound-message rows, and returns `KeyedAdmission` records.

**Call relations**: The web transcript-aids builder calls this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_transcript_aids); 4 external calls (__init__, select, union_all, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 4567–4580)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads and decodes a conversation’s durable transcript from blob storage. It returns null for foreign or missing transcripts.

**Data flow**: It verifies conversation ownership, loads transcript bytes by key, decodes them, reduces workspace file links to labels, and returns a `Conversation`.

**Call relations**: Debugger and web conversation, slot, and subagent views call this.

*Call graph*: calls 2 internal fn (_owned_conversation, _without_carried); called by 4 (conversation_transcript, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 4582–4593)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists the saved compaction indices for a conversation transcript. Compaction records explain how long histories were summarized.

**Data flow**: It verifies conversation ownership, lists blob keys under the compaction prefix, extracts numeric indices, and returns them sorted.

**Call relations**: Debugger and web conversation-message views call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_compactions, _conversation_messages).


##### `SurfaceContext.read_compaction`  (lines 4595–4601)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one full compaction record for a conversation. It returns null if the conversation is foreign or the record is absent.

**Data flow**: It verifies ownership and delegates to transcript compaction-record storage.

**Call relations**: Debugger and web history views call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (compaction_record, _history_messages); 1 external calls (read_compaction_record).


##### `SurfaceContext.read_compaction_after`  (lines 4603–4611)

```
async def read_compaction_after(self, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the post-compaction message window for one compaction. This is a lighter comparison view.

**Data flow**: It verifies ownership, reads the compaction-after data, and returns messages or null.

**Call relations**: The web verified-earlier flow calls this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_verified_earlier); 1 external calls (read_compaction_after).


##### `SurfaceContext.list_workspace_files`  (lines 4613–4619)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files currently in a conversation’s sandbox workspace. Foreign conversations return an empty list.

**Data flow**: It verifies conversation ownership and asks the sandbox carrier for workspace entries.

**Call relations**: Debugger and UFO workspace-listing routes call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_files, workspace_listing).


##### `SurfaceContext.conversation_changes`  (lines 4621–4627)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last recorded workspace file changes for a conversation. This summarizes what changed in checkouts after turns ran.

**Data flow**: It verifies ownership and returns recorded changes, or a no-change object for foreign conversations.

**Call relations**: The web project-slot context builder calls this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 4629–4638)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one file out of a conversation’s sandbox workspace. It returns null when the conversation or file is unavailable.

**Data flow**: It verifies conversation ownership and delegates the path read to the sandbox carrier.

**Call relations**: Debugger and UFO workspace-file routes call this.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_file, workspace_file).


##### `SurfaceContext.terminal_connect`  (lines 4640–4646)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Registers a live member terminal as available for a conversation’s sandbox work. It pairs with disconnect around a held client connection.

**Data flow**: It receives conversation id, current directory, member id, and runtime id, and records the terminal binding in the sandbox terminal registry.

**Call relations**: Live terminal surfaces call this when a client connects.


##### `SurfaceContext.terminal_disconnect`  (lines 4648–4649)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the live terminal binding for a conversation.

**Data flow**: It receives a conversation id and tells the sandbox terminal registry to disconnect it.

**Call relations**: Live terminal surfaces call this when the held connection closes.


##### `SurfaceContext.claim_terminal`  (lines 4651–4657)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Claims a connected terminal for a new conversation before its sandbox opens. This avoids silently provisioning a different workspace.

**Data flow**: It receives conversation id and current directory, attempts a compare-and-swap claim through the sandbox carrier, and returns whether this call made the claim.

**Call relations**: The UFO surface calls this before sending channel messages.

*Call graph*: called by 2 (_channel_message, _send).


##### `SurfaceContext.next_terminal_op`  (lines 4659–4666)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next terminal operation requested by a conversation’s turn. A terminal operation is a command-like request for the member’s live terminal.

**Data flow**: It receives conversation id and optional op id to exclude, and returns the next terminal operation from the terminal registry.

**Call relations**: Live terminal transports use this while streaming instructions to the client.


##### `SurfaceContext.terminal_resolve`  (lines 4668–4682)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Submits the client’s answer to an in-flight terminal operation. It is gated by the op id and member binding held by the terminal transport.

**Data flow**: It receives conversation id, op id, reply bytes, optional failure text, and member id, then asks the terminal registry to resolve the op and returns success.

**Call relations**: The UFO channel op-reply route calls this.

*Call graph*: called by 1 (_channel_op_reply).


##### `SurfaceContext.terminal_op_body`  (lines 4684–4696)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads staged bytes for an in-flight terminal operation without creating a conversation. This supports clients fetching a command body separately.

**Data flow**: It receives queue key, op id, and member id, finds the existing conversation, and asks the terminal registry for staged bytes or null.

**Call relations**: The UFO op-body route calls this.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 4698–4711)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface. This helps render links or metadata for conversations that live elsewhere.

**Data flow**: It receives a peer surface name, queries surface-installation rows, and returns the installation id or null.

**Call relations**: The debugger workspace metadata route calls this.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 4714–4723)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Provides a raw workspace database transaction to trusted surface extension code. The surface remains responsible for scoping its own queries to the workspace.

**Data flow**: It opens `workspace_tx`, yields the async connection, commits on normal exit, and rolls back on error.

**Call relations**: The sites surface uses this for its own extension tables when no ordinary extension context exists.

*Call graph*: called by 1 (_viewer_is_admin); 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 4725–4735)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace. This protects blob and sandbox reads that are not naturally tenant-scoped.

**Data flow**: It receives conversation id, queries the conversation table by workspace and id, and returns true if a row exists.

**Call relations**: Transcript, compaction, workspace-file, queued-arrival, and related read methods call this before reading deeper state.

*Call graph*: called by 10 (arrival_speakers, conversation_changes, keyed_admissions, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_compaction_after, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 4737–4757)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the standard SQL select for full turn rows. Centralizing it keeps all turn projections consistent.

**Data flow**: It returns a SQL select containing turn identity, status, inbound, context, terminal, subagent, and tracing fields.

**Call relations**: Turn list, subagent-turn list, and turn-detail methods extend this base query.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 4759–4779)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database row into a typed `Turn` record. It parses nested context and terminal JSON when present.

**Data flow**: It receives a SQL row, copies scalar fields, validates context and terminal objects, and returns a `Turn` instance.

**Call relations**: Turn-reading methods call this after executing `_turn_query`.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.reserve_address`  (lines 4811–4871)

```
async def reserve_address(self, surface: str, address: str, member_id: UUID, claim_expires_at: datetime) -> AddressClaimState
```

**Purpose**: Reserves an addressed-surface address, such as a phone number, for a workspace member until proof arrives. It reports whether the address was reserved, already linked, or taken.

**Data flow**: It validates that the surface is declared as addressed, reads current workspace context, upserts or checks the fleet-wide surface-address row, and returns an `AddressClaimState`.

**Call relations**: Surface installation tools use this before an addressed listener can route inbound traffic to the workspace.

*Call graph*: 7 external calls (__init__, now, and_, or_, select, owner_tx, ws_current).


##### `SurfaceInstallationAccess.installation`  (lines 4873–4886)

```
async def installation(self, surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for a manifest-declared surface. Undeclared surfaces are rejected.

**Data flow**: It validates the surface name, reads the current workspace id, queries the installation row, and returns the id or null.

**Call relations**: Tools use this manifest-scoped view instead of unrestricted surface installation access.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `SurfaceInstallationAccess.bind`  (lines 4888–4900)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Binds a declared surface installation to the current workspace. Addressed surfaces bind a shared installation that does not route ingress by installation id.

**Data flow**: It validates the surface declaration, reads the current workspace id, chooses `routes_ingress`, and delegates to `_bind_surface_installation`.

**Call relations**: Manifest-scoped tools call this to configure surface installations safely.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 4912–4923)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Resolves a shared surface request’s workspace from an installation id. Only ingress-routing installations are considered.

**Data flow**: It receives installation id, queries the owner database for a matching surface installation, and returns workspace id or null.

**Call relations**: Slack workspace resolution calls this before binding a request to workspace context.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.addressed_workspace`  (lines 4925–4938)

```
async def addressed_workspace(self, address: str) -> UUID | None
```

**Purpose**: Resolves a workspace from an addressed-surface address. The address is unique across the fleet.

**Data flow**: It receives an address, queries the owner database for the surface-address row, and returns the workspace id or null.

**Call relations**: Addressed listeners use this before admitting events for a sender address.

*Call graph*: 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 4940–4950)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential handoff before a workspace has been bound. This is needed for OAuth callbacks that carry workspace information inside sealed state.

**Data flow**: It receives a sealed token, returns opened credential request state if valid, or null if credentials are unavailable or the seal is invalid.

**Call relations**: Slack workspace resolution uses this while handling OAuth callback state.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 4952–4968)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential for a resolved workspace during pre-binding authentication. It refuses undeclared slots and unknown workspaces.

**Data flow**: It receives workspace id and slot, validates slot declaration, binds workspace context, verifies the workspace exists, and reads the credential value.

**Call relations**: Slack authentication uses this to read its signing secret while resolving requests.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceListenerContext.workspace`  (lines 5016–5024)

```
async def workspace(self, installation_id: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Temporarily binds a listener event to the workspace selected by an installation id. It yields null when no workspace owns that installation.

**Data flow**: It verifies this process still owns the listener, resolves workspace id through auth, enters workspace context, and yields a `SurfaceContext` or null.

**Call relations**: Persistent listeners use this around each provider event before reading or admitting anything.

*Call graph*: 1 external calls (ws).


##### `SurfaceListenerContext.addressed`  (lines 5027–5038)

```
async def addressed(self, address: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Temporarily binds a listener event to the workspace selected by a sender address. It is for surfaces where one deploy-owned provider serves all workspaces.

**Data flow**: It checks listener ownership, resolves the address to a workspace, enters workspace context, and yields a `SurfaceContext` or null.

**Call relations**: The iMessage listener uses this while processing each event.

*Call graph*: called by 1 (_process_event); 1 external calls (ws).


##### `SurfaceListenerContext.cursor`  (lines 5040–5055)

```
async def cursor(self, installation_id: str) -> int | None
```

**Purpose**: Reads the saved stream cursor for this listener and installation. A cursor marks where the provider stream was last processed.

**Data flow**: It receives installation id, reads the stored cursor row, and returns the sequence only if it belongs to the same installation.

**Call relations**: The iMessage listener calls this when starting or resuming its stream.

*Call graph*: called by 1 (listen); 2 external calls (select, owner_tx).


##### `SurfaceListenerContext.store_cursor`  (lines 5057–5080)

```
async def store_cursor(self, installation_id: str, sequence: int) -> None
```

**Purpose**: Stores the listener’s current stream position. The cursor belongs to the fleet-wide listener, not to any one workspace.

**Data flow**: It receives installation id and sequence, upserts the surface cursor row, and records the new position.

**Call relations**: The iMessage listener updates this while catching up and after processing events.

*Call graph*: called by 2 (_catch_up, _process_event); 1 external calls (owner_tx).


##### `SurfaceListenerContext.clear_cursor`  (lines 5082–5089)

```
async def clear_cursor(self) -> None
```

**Purpose**: Deletes the listener’s saved stream position. The next run starts from the provider’s current head.

**Data flow**: It deletes the cursor row for this surface from the owner database.

**Call relations**: The iMessage listener calls this when it needs to forget its stream position.

*Call graph*: called by 1 (listen); 2 external calls (delete, owner_tx).


##### `SurfaceListenerRunner.run`  (lines 5116–5155)

```
async def run(self) -> None
```

**Purpose**: Runs a persistent surface listener only while this server instance owns the fleet-wide listener lease. It restarts or parks according to failure type.

**Data flow**: It loops waiting for ownership, starts the listener and ownership watcher, reacts to whichever finishes first, logs failures, and cancels child tasks during cleanup.

**Call relations**: The application lifecycle can run this for each registered persistent listener.

*Call graph*: calls 2 internal fn (_wait_until_not_owned, _wait_until_owned); 9 external calls (__init__, CancelledError, create_task, ensure_future, gather, sleep, wait, emit_metric, log).


##### `SurfaceListenerRunner._wait_until_not_owned`  (lines 5157–5162)

```
async def _wait_until_not_owned(self) -> None
```

**Purpose**: Waits until this server instance no longer owns the listener lease.

**Data flow**: It polls ownership on a timer and returns once ownership is explicitly false.

**Call relations**: `run` uses this as the watcher task while a listener is active.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._wait_until_owned`  (lines 5164–5168)

```
async def _wait_until_owned(self) -> None
```

**Purpose**: Waits until this server instance owns the listener lease.

**Data flow**: It polls ownership on a timer and returns once ownership is true.

**Call relations**: `run` calls this before starting the listener.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._owned_on_tick`  (lines 5170–5179)

```
async def _owned_on_tick(self) -> bool | None
```

**Purpose**: Performs one safe ownership check and logs database errors instead of crashing the runner.

**Data flow**: It calls `_owns`, returns true/false on success, and returns null after logging SQL errors.

**Call relations**: Both ownership wait loops call this repeatedly.

*Call graph*: calls 1 internal fn (_owns); called by 2 (_wait_until_not_owned, _wait_until_owned); 1 external calls (log).


##### `SurfaceListenerRunner._owns`  (lines 5181–5199)

```
async def _owns(self) -> bool
```

**Purpose**: Checks or renews listener ownership while shielding the database claim from cancellation. This avoids leaving broken transactions behind.

**Data flow**: It starts `_claim`, shields it until done, remembers cancellation, then re-raises cancellation after the claim has safely completed or returns the claim result.

**Call relations**: `_owned_on_tick` calls this for every ownership poll.

*Call graph*: calls 1 internal fn (_claim); called by 1 (_owned_on_tick); 2 external calls (ensure_future, shield).


##### `SurfaceListenerRunner._claim`  (lines 5201–5241)

```
async def _claim(self) -> bool
```

**Purpose**: Attempts to acquire or renew the fleet-wide listener lease in the owner database. Only the current owner or an expired claim can update the row.

**Data flow**: It builds an expiry timestamp, upserts the listener-claim row with compare-and-swap conditions, and returns whether the stored token is this runner’s token.

**Call relations**: `_owns` calls this inside a cancellation-safe wrapper.

*Call graph*: called by 1 (_owns); 7 external calls (now, timedelta, and_, insert, insert, or_, owner_tx).


##### `SurfaceDeliveryError.__init__`  (lines 5248–5252)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that may carry provider retry timing. Negative retry times are refused.

**Data flow**: It receives a message and optional retry-after seconds, validates the retry value, stores it, and initializes the runtime error.

**Call relations**: Slack posting code can raise this so pollers schedule retry according to provider guidance.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 5315–5339)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the SQL condition for terminal turn writebacks that are ready to claim. It waits until all pending mid-turn replies for the turn have cleared.

**Data flow**: It receives the current time and returns a condition over turn status, mid-turn reply status, writeback status, and claim expiry.

**Call relations**: Workspace candidate selection and writeback claiming both use this same due rule.

*Call graph*: called by 2 (_claim, due); 3 external calls (and_, exists, or_).


##### `writeback_workspaces`  (lines 5342–5377)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating reader for workspace ids that have due terminal writebacks. This lets the poller scan bounded pages fairly.

**Data flow**: It keeps an internal cursor, defines a due-query builder, wraps it with owner-candidate reading, and returns an async candidate function.

**Call relations**: A `WritebackPoller` receives the returned candidate function to decide which workspaces to drain.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 5349–5363)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query for workspace ids with due terminal writebacks.

**Data flow**: It reads the current time, selects grouped workspace ids matching `_writeback_due`, applies the cursor if present, and limits the page.

**Call relations**: The candidate function created by `writeback_workspaces` asks this query builder for each owner-database read.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 5367–5375)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with due writebacks. It wraps around when it reaches the end.

**Data flow**: It calls the owner candidate reader, resets the cursor on an empty page after progress, updates the cursor to the last workspace id, and returns the ids.

**Call relations**: `WritebackPoller.run` and `WritebackPoller.drain` call this through their `candidates` dependency.


##### `_WritebackDeliveryFailed.__init__`  (lines 5385–5388)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a post or attach exception with the delivery phase that failed. This helps retry logging and error handling explain what went wrong.

**Data flow**: It receives phase and original exception, stores both, and sets a readable exception message.

**Call relations**: `WritebackPoller._deliver_claimed` raises this around surface post and attach failures.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 5411–5444)

```
async def run(self) -> None
```

**Purpose**: Continuously drains terminal writebacks for durable surfaces. It limits concurrent workspaces and cleans up tasks on shutdown.

**Data flow**: It tracks in-flight workspace drain tasks, asks for candidate workspaces, starts drains under a semaphore, logs completed task errors, sleeps, and repeats.

**Call relations**: The app starts this background loop when durable surfaces are installed.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 5446–5455)

```
async def drain(self) -> None
```

**Purpose**: Runs one bounded writeback drain pass, useful for tests or manual ticks.

**Data flow**: It gets candidate workspaces, drains them concurrently under a semaphore, collects exceptions, and raises an exception group if any drain failed.

**Call relations**: It shares `_drain_workspace` with the long-running `run` loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 5457–5475)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers a batch of terminal writebacks for one workspace. It renews each claim while external delivery is in progress.

**Data flow**: It enters workspace context, claims due rows, starts renewal tasks, delivers rows one by one, and cancels renewal tasks afterward.

**Call relations**: `run` and `drain` call this for each candidate workspace.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 5477–5510)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due writeback rows for this worker. A claim is a lease that prevents other workers from delivering the same row at the same time.

**Data flow**: It selects due writebacks for the workspace, updates them to claimed with worker id and expiry, and returns turn ids, reply refs, and last errors.

**Call relations**: `_drain_workspace` calls this before starting renewals and delivery.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 5512–5544)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed terminal writeback and logs the result. Failures are either claim loss or retry/fail state transitions.

**Data flow**: It records start time, calls `_deliver_with_lease`, catches claim loss or delivery failure, updates retry state when needed, and logs outcome.

**Call relations**: `_drain_workspace` calls this for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 5546–5575)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while the claim-renewal task stays healthy, then marks the writeback delivered. It stops renewal before the final delivered commit.

**Data flow**: It starts `_deliver_claimed`, waits for either delivery or renewal failure, cancels/awaits both tasks, and calls `_mark_delivered` after successful delivery.

**Call relations**: `_deliver` uses this to tie delivery success to a still-held lease.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 5577–5607)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Performs the actual surface delivery for a claimed terminal writeback. It posts the reply once, records the provider reference, then attaches files.

**Data flow**: It builds the writeback payload, finds the surface spec, skips missing delivery handlers, posts if no reply reference exists, records the reference, and calls attach.

**Call relations**: `_deliver_with_lease` calls this while a claim is being renewed.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 5609–5612)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps a writeback claim alive until cancelled. This protects long external deliveries from being picked up by another worker.

**Data flow**: It sleeps for the refresh interval, calls `_refresh_claim`, and repeats forever.

**Call relations**: `_drain_workspace` starts one renewal task per claimed writeback.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 5614–5630)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends the lease on a claimed writeback row if this worker still owns it. Losing the row raises a claim-lost error.

**Data flow**: It updates claim expiry where turn id, claimed status, and worker id match, and raises if no row was updated.

**Call relations**: `_renew_claim` calls this repeatedly during delivery.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 5632–5691)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the `Writeback` payload for a terminal turn. It includes the terminal frame, conversation queue key, agent id, and agent-shared artifacts.

**Data flow**: It reads turn/conversation data and non-member-attached artifact rows, validates the terminal frame, constructs `SharedArtifact` records, and returns the payload plus surface name.

**Call relations**: `_deliver_claimed` calls this before invoking the surface’s post or attach handlers.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 5693–5705)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Stores the provider’s reply reference after a successful post. This prevents re-posting after a crash before attachments complete.

**Data flow**: It receives turn id and reply reference, updates the claimed writeback row for this worker, and raises if the claim was lost.

**Call relations**: `_deliver_claimed` calls this immediately after `post` returns a message reference.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 5707–5724)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a claimed writeback as delivered and clears claim fields.

**Data flow**: It receives turn id, updates the writeback row from claimed to delivered for this worker, clears owner and expiry, and raises if no row changed.

**Call relations**: `_deliver_with_lease` calls this after posting and attachment work finishes or is intentionally skipped.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 5726–5775)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed writeback for retry or marks it permanently failed after the delivery window expires. Provider retry hints are honored but bounded.

**Data flow**: It receives turn id and wrapped delivery error, computes retry timing and last-error text, updates the writeback row if still claimed by this worker, and returns outcome details.

**Call relations**: `_deliver` calls this after post or attach failures.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `mid_turn_reply_workspaces`  (lines 5778–5809)

```
def mid_turn_reply_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating reader for workspace ids that have due mid-turn replies. These are replies sent before a turn reaches its terminal answer.

**Data flow**: It keeps a cursor, defines a due-query builder for mid-turn rows, wraps it with owner-candidate reading, and returns an async candidate function.

**Call relations**: A `MidTurnReplyPoller` receives this candidate function to find workspaces to drain.

*Call graph*: 1 external calls (owner_candidates).


##### `mid_turn_reply_workspaces.due`  (lines 5784–5795)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query for workspace ids with due mid-turn replies.

**Data flow**: It reads current time, selects grouped workspace ids matching `_mid_turn_reply_due`, applies the cursor if set, and limits the page.

**Call relations**: The candidate function created by `mid_turn_reply_workspaces` uses this for each owner-database read.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); 2 external calls (now, select).


##### `mid_turn_reply_workspaces.candidates`  (lines 5799–5807)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with due mid-turn replies. It wraps around after reaching the end.

**Data flow**: It calls the owner candidate reader, resets cursor when needed, updates cursor from the last id, and returns workspace ids.

**Call relations**: `MidTurnReplyPoller.drain` calls this through its `candidates` dependency.


##### `_mid_turn_reply_due`  (lines 5812–5825)

```
def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the SQL condition for mid-turn reply rows that are claimable. Pending rows and expired claims are due.

**Data flow**: It receives current time and returns a condition over mid-turn reply status and claim expiry.

**Call relations**: Mid-turn workspace selection and row claiming share this due rule.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `MidTurnReplyPoller.run`  (lines 5853–5859)

```
async def run(self) -> None
```

**Purpose**: Continuously delivers due mid-turn replies for durable surfaces. It logs drain failures and retries on the next tick.

**Data flow**: It loops forever, calls `drain`, logs any exception, sleeps for the poll interval, and repeats.

**Call relations**: The app starts this background loop when surfaces may emit mid-turn replies.

*Call graph*: calls 1 internal fn (drain); 2 external calls (sleep, log).


##### `MidTurnReplyPoller.drain`  (lines 5861–5879)

```
async def drain(self) -> None
```

**Purpose**: Claims and delivers due mid-turn replies for candidate workspaces in one pass.

**Data flow**: It iterates candidate workspaces, enters workspace context, claims rows, starts renewal tasks, delivers each row in order, and cancels renewals at the end.

**Call relations**: `run` calls this every poll interval.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 1 (run); 4 external calls (create_task, gather, log, ws).


##### `MidTurnReplyPoller._claim`  (lines 5881–5922)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due mid-turn reply rows for this worker, ordered so replies are delivered in model order.

**Data flow**: It selects claimable reply ids, updates them to claimed with worker id and expiry, returns reply details, and sorts them by creation/round/span.

**Call relations**: `drain` calls this before starting delivery and renewal tasks.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); called by 1 (drain); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `MidTurnReplyPoller._deliver`  (lines 5924–5949)

```
async def _deliver(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed mid-turn reply and records logging/retry outcome.

**Data flow**: It records start time, calls `_deliver_with_lease`, catches claim loss or other errors, updates retry/fail state for errors, and logs success or failure.

**Call relations**: `drain` calls this for each claimed mid-turn reply row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (drain); 2 external calls (now, log).


##### `MidTurnReplyPoller._deliver_with_lease`  (lines 5951–5972)

```
async def _deliver_with_lease(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs a mid-turn surface send while the claim-renewal task stays healthy, then marks the row delivered.

**Data flow**: It starts `_speak`, waits for either send completion or renewal failure, cancels/awaits tasks, and writes delivered state with the reply reference.

**Call relations**: `_deliver` uses this to ensure a row advances only while this worker still owns its claim.

*Call graph*: calls 2 internal fn (_mark_delivered, _speak); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `MidTurnReplyPoller._speak`  (lines 5974–6016)

```
async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None
```

**Purpose**: Sends one mid-turn reply through the surface’s `speak` handler, or skips it when no durable mid-turn delivery is configured. Existing reply references are reused.

**Data flow**: It checks for an existing reply ref, reads the turn’s conversation and surface, finds the surface spec, builds a `MidTurnReply`, and calls `speak` when available.

**Call relations**: `_deliver_with_lease` calls this as the external delivery step.

*Call graph*: called by 1 (_deliver_with_lease); 4 external calls (__init__, select, workspace_tx, log).


##### `MidTurnReplyPoller._renew_claim`  (lines 6018–6021)

```
async def _renew_claim(self, reply_id: UUID) -> None
```

**Purpose**: Keeps a claimed mid-turn reply lease alive until delivery finishes or the task is cancelled.

**Data flow**: It sleeps for the refresh interval, calls `_refresh_claim`, and repeats.

**Call relations**: `drain` starts one renewal task per claimed reply row.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (drain); 1 external calls (sleep).


##### `MidTurnReplyPoller._refresh_claim`  (lines 6023–6039)

```
async def _refresh_claim(self, reply_id: UUID) -> None
```

**Purpose**: Extends the lease on a claimed mid-turn reply if this worker still owns it.

**Data flow**: It updates the row’s claim expiry where id, claimed status, and worker id match, and raises claim-lost if no row was updated.

**Call relations**: `_renew_claim` calls this repeatedly during delivery.

*Call graph*: called by 1 (_renew_claim); 4 external calls (now, timedelta, update, workspace_tx).


##### `MidTurnReplyPoller._mark_delivered`  (lines 6041–6059)

```
async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Marks a mid-turn reply row as delivered and stores the provider reply reference if there is one.

**Data flow**: It receives reply id and optional reply ref, updates the claimed row for this worker to delivered, clears claim fields, and raises if the claim was lost.

**Call relations**: `_deliver_with_lease` calls this after `_speak` succeeds or is intentionally skipped.

*Call graph*: called by 1 (_deliver_with_lease); 2 external calls (update, workspace_tx).


##### `MidTurnReplyPoller._fail_or_retry`  (lines 6061–6108)

```
async def _fail_or_retry(self, reply_id: UUID, error: Exception) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed mid-turn reply for retry or marks it failed after it gets too old. A failed span stops blocking the terminal writeback forever.

**Data flow**: It receives reply id and error, computes bounded retry time and last-error text, updates the row if still claimed, and returns outcome, error text, and next attempt time.

**Call relations**: `_deliver` calls this after send failures.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### iMessage provider plumbing
The iMessage extension package and cloud provider layer connect UFO’s iMessage surface to Spectrum Cloud messaging events and delivery.

### `extensions/imessage/ufo_ext_imessage/__init__.py`

`other` · `import/package discovery`

This is an empty Python package marker file. In Python, a folder often needs an `__init__.py` file so the language treats that folder as an importable package, meaning code elsewhere can refer to it by name and load modules from inside it. Think of it like a label on a box: the label does not contain the tools, but it tells Python that the box is meant to be opened as part of the program.

For this project, the file helps identify `extensions/imessage/ufo_ext_imessage` as the package for the iMessage extension. The actual work of the extension, such as connecting to iMessage data or exposing extension features, would live in other files inside or alongside this package. If this file were missing in environments that require explicit package markers, imports for this extension could fail or behave differently.

Because the file is empty, it has no functions, classes, settings, or side effects. Its importance is structural rather than behavioral.


### `extensions/imessage/ufo_ext_imessage/cloud.py`

`io_transport` · `provider setup and message request handling`

This file is the cloud-backed iMessage bridge. The rest of the project wants a simple message provider: send a text, send an attachment, listen for incoming messages, and fetch missed events. Spectrum Cloud is the outside service that supplies those iMessage abilities, so this file is the adapter between the project’s plain internal interface and Spectrum’s HTTP and gRPC APIs. gRPC is a network calling system where code calls remote services using generated request and response classes.

At startup or provider selection time, the file checks for a Spectrum project ID and secret in the deploy environment. If they exist, it builds a cached SpectrumProject. If not, it may fall back to a local development line, or raise a clear “not configured” error.

SpectrumProject does the live work. It uses HTTP to ask Spectrum Cloud for a temporary shared-line token and caches that token until shortly before it expires. It uses gRPC channels to stream message events, send messages, upload or download attachments, and catch up after downtime. Think of it like a receptionist with a temporary badge: before entering any Spectrum service, it makes sure the badge is still valid.

The file also filters raw incoming message events. It ignores messages from the user, system messages, spam, corrupt messages, and empty messages, then returns only useful inbound messages with sender, text, attachments, and conversation details.

#### Function details

##### `SpectrumProject.installation_id`  (lines 94–95)

```
def installation_id(self) -> str
```

**Purpose**: This property gives the Spectrum-backed provider a stable local identifier. It is used to name this installation as a project-based iMessage connection.

**Data flow**: It reads the project ID stored on the SpectrumProject object, prefixes it with "project:", and returns that string. It does not contact the network or change any state.

**Call relations**: Other parts of the provider system can ask for this identity when they need a consistent name for the connected iMessage installation. It stands alone and does not hand work to other helpers.


##### `SpectrumProject.line`  (lines 97–118)

```
async def line(self) -> SpectrumLine
```

**Purpose**: This function gets the temporary Spectrum line token needed to call the iMessage gRPC services. It reuses a still-valid token when possible, and asks Spectrum Cloud for a fresh one when needed.

**Data flow**: It starts by getting the current event-loop-specific state, including a lock and cached token data. If the cached SpectrumLine exists and will not expire soon, it returns it. Otherwise it sends an HTTP request for a shared iMessage token, checks that Spectrum’s response has the expected shape, stores the new token and expiry time, and returns a SpectrumLine containing the shared line ID and bearer token.

**Call relations**: All message-facing operations call this first: catch_up, subscribe, send_text, send_attachment, and download_attachment. It relies on _loop to find safe local state for the current async loop, _request to talk to Spectrum Cloud, and pydantic validation to reject malformed responses before the token is used.

*Call graph*: calls 2 internal fn (_loop, _request); called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 4 external calls (__init__, __init__, TypeAdapter, monotonic).


##### `SpectrumProject._loop`  (lines 120–136)

```
def _loop(self) -> SpectrumLoop
```

**Purpose**: This helper gives each running asyncio event loop its own safe state for HTTP clients, locks, and token cache data. That matters because async clients and locks are tied to the loop they run on.

**Data flow**: It reads the currently running asyncio event loop and looks up existing SpectrumLoop state for it. If state already exists, it returns it. If not, it creates one: the first loop reuses the project’s initial client, lock, and token dictionary, while later loops get their own HTTP client, lock, and empty token cache. It stores the new state for future calls.

**Call relations**: line, _request, and invalidate call this whenever they need the correct client or lock for the current async context. It is the quiet plumbing that prevents one event loop from accidentally using objects created for another.

*Call graph*: called by 3 (_request, invalidate, line); 4 external calls (__init__, Lock, get_running_loop, AsyncClient).


##### `SpectrumProject.assign_line`  (lines 138–168)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This function makes sure a phone number is registered with Spectrum and returns the shared iMessage phone number assigned to it. It is used when the system needs to know which Spectrum line should communicate with a particular user.

**Data flow**: It first fetches the project’s existing Spectrum users by HTTP. If the requested phone number is already registered, it returns that user’s assigned shared number. If not, it posts a request to create a shared user for that phone number, using an idempotency key so a retry does not create duplicates, validates the response, and returns the newly assigned shared phone number.

**Call relations**: This is a higher-level cloud setup operation built on _request. It does not appear in the listed callers here, but it fits the provider’s lifecycle when a member phone number needs to be associated with a Spectrum shared line.

*Call graph*: calls 1 internal fn (_request); 2 external calls (__init__, TypeAdapter).


##### `SpectrumProject._request`  (lines 170–193)

```
async def _request(self, method: str, path: str, *, json: dict[str, str] | None=None, idempotency_key: str | None=None) -> object
```

**Purpose**: This is the file’s common HTTP request helper for Spectrum Cloud’s REST-style API. It centralizes authentication, timeouts, optional retry-safety headers, and error translation.

**Data flow**: It receives an HTTP method, a path, optional JSON data, and an optional idempotency key. It builds the full Spectrum Cloud URL, adds basic authentication using the project ID and secret, includes the idempotency header if provided, sends the request through the current loop’s HTTP client, and raises SpectrumCloudError if the server returns an HTTP error. On success, it returns the parsed JSON response.

**Call relations**: line uses it to get shared-line tokens, and assign_line uses it to list or register users. It calls _loop so the request uses the correct async HTTP client for the current event loop.

*Call graph*: calls 1 internal fn (_loop); called by 2 (assign_line, line); 2 external calls (__init__, BasicAuth).


##### `SpectrumProject.channel`  (lines 195–196)

```
def channel(self) -> grpc.aio.Channel
```

**Purpose**: This function opens a secure gRPC channel to Spectrum’s iMessage service. A channel is the network connection object used by generated gRPC clients to call remote methods.

**Data flow**: It creates and returns a secure channel pointed at Spectrum’s iMessage address, using SSL credentials so the connection is encrypted. It does not send a request by itself.

**Call relations**: catch_up, subscribe, send_text, send_attachment, and download_attachment each call this when they need to talk to Spectrum’s gRPC services. Those functions then build specific service stubs on top of the channel.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 1 external calls (ssl_channel_credentials).


##### `SpectrumProject.invalidate`  (lines 198–201)

```
async def invalidate(self) -> None
```

**Purpose**: This function clears the cached Spectrum line token. It is useful after an authentication problem, because the next operation will be forced to fetch a fresh token.

**Data flow**: It gets the current loop’s state, takes the async lock so no other task changes the token cache at the same time, and clears the token_state dictionary. It returns nothing.

**Call relations**: No caller is listed in this file’s call graph, but provider orchestration code can call it when a token should no longer be trusted. It uses _loop to clear the cache belonging to the current async loop.

*Call graph*: calls 1 internal fn (_loop).


##### `SpectrumProject.invalid_cursor`  (lines 203–207)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This function tells the rest of the system whether an error means an event cursor is invalid. A cursor is a saved position in an event stream, like a bookmark showing where to resume.

**Data flow**: It receives an exception. If the exception is a gRPC error and its status code is INVALID_ARGUMENT, it returns true. Otherwise it returns false.

**Call relations**: This is part of the provider error interface. Higher-level code can use it after catch_up or subscribe fails to decide whether the saved event position should be discarded or repaired.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.external_error`  (lines 209–210)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This function recognizes errors that came from outside services or network layers. It helps separate expected remote failures from bugs inside the application.

**Data flow**: It receives an exception and checks whether it is a gRPC error, a SpectrumCloudError, or an HTTPX network/HTTP client error. It returns true for those external error types and false for others.

**Call relations**: Higher-level provider code can use this after any Spectrum operation fails to decide whether to retry, report a remote outage, or treat the failure as an internal programming problem.


##### `SpectrumProject.error_code`  (lines 212–215)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This function converts an exception into a short error label suitable for logs or status reporting. It gives gRPC failures their official status name and other failures their Python class name.

**Data flow**: It receives an exception. If it is a gRPC error, it reads the gRPC status code and returns its name. For any other exception, it returns the exception type’s name as text.

**Call relations**: This supports error reporting around the provider’s network calls. It does not send work elsewhere except reading a gRPC error code when available.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.catch_up`  (lines 217–238)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This function reads missed iMessage events from Spectrum after a known sequence number. It lets the system recover messages that arrived while it was offline or behind.

**Data flow**: It first gets a valid line token, builds a catch-up request, and includes the saved sequence number if one was provided. It opens a secure gRPC channel, attaches authorization metadata, and reads frames from Spectrum’s catch-up stream. Completion frames become ProviderEvent objects with a head sequence, while message-change frames become ProviderEvent objects with a sequence and, when relevant, an inbound message extracted by _inbound_message.

**Call relations**: This is one of the main read paths for the provider. It depends on line for authentication, channel for the gRPC connection, rpc_metadata for request headers, and _inbound_message to turn raw Spectrum message-change events into the project’s cleaner InboundMessage shape.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 3 external calls (__init__, CatchUpEventsRequest, EventServiceStub).


##### `SpectrumProject.subscribe`  (lines 240–256)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This function opens a live stream of new iMessage events from Spectrum. It is how the application receives messages as they happen.

**Data flow**: It gets a valid line token, creates a subscription request, opens a secure gRPC channel, and starts Spectrum’s message event stream with authorization metadata. Once the stream is successfully created, it sets the provided ready event so the caller knows listening has begun. For each incoming frame, it yields a ProviderEvent containing the sequence number when present and an inbound message only if the raw event passes _inbound_message’s filters.

**Call relations**: This is the provider’s live-listening path. It calls line, channel, rpc_metadata, and _inbound_message, then yields events back to the higher-level message loop that consumes provider events.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 4 external calls (__init__, set, SubscribeMessageEventsRequest, MessageServiceStub).


##### `SpectrumProject.send_text`  (lines 258–271)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This function sends a plain text iMessage through Spectrum. It returns Spectrum’s message ID so the caller can track the sent message.

**Data flow**: It receives a conversation ID, message text, and idempotency key. It gets a valid line token, builds a send-text gRPC request with the conversation, text, and client message ID, opens a secure channel, sends the request with authorization and idempotency metadata, and returns the GUID of the message Spectrum created.

**Call relations**: This is the provider’s text-send path. It calls line before sending, channel to reach Spectrum, and rpc_metadata so Spectrum can authenticate the request and safely recognize retries.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (SendTextMessageRequest, MessageServiceStub).


##### `SpectrumProject.send_attachment`  (lines 273–300)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This function sends a file attachment in an iMessage conversation. It first uploads the file to Spectrum, then sends a message that refers to the uploaded attachment.

**Data flow**: It receives a conversation ID, filename, raw file bytes, and idempotency key. It gets a valid line token and opens a secure gRPC channel. First it uploads the attachment bytes with a separate upload idempotency key and receives an attachment GUID. Then it sends an attachment message to the conversation using that GUID and the original idempotency key. It returns the GUID of the sent message.

**Call relations**: This is the provider’s attachment-send path. It combines the attachment service and message service over the same channel, using line for authentication and rpc_metadata for bearer-token and retry-safety headers.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 4 external calls (UploadAttachmentRequest, AttachmentServiceStub, SendAttachmentMessageRequest, MessageServiceStub).


##### `SpectrumProject.download_attachment`  (lines 302–312)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This function downloads an attachment from Spectrum as a stream of byte chunks. Streaming matters because attachments can be large, so the caller can process pieces instead of loading everything at once here.

**Data flow**: It receives an attachment ID, gets a valid line token, opens a secure gRPC channel, and requests the attachment download with authorization metadata. As Spectrum sends frames back, it yields only the primary file chunks as bytes and ignores other frame types.

**Call relations**: This is the provider’s attachment-read path. It calls line, channel, and rpc_metadata, then hands byte chunks to whichever higher-level code is serving or storing the attachment.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (DownloadAttachmentRequest, AttachmentServiceStub).


##### `_spectrum_pair`  (lines 321–326)

```
def _spectrum_pair() -> tuple[str, str] | None
```

**Purpose**: This helper reads the Spectrum project ID and secret from the deployment environment. It answers the basic question: does this deploy have real Spectrum Cloud credentials?

**Data flow**: It asks deploy_env for the project ID and project secret. If either value is missing or empty, it returns None. If both are present, it returns them as a pair.

**Call relations**: spectrum_configured uses it for a yes-or-no availability check, and spectrum_project uses it when building the actual SpectrumProject. Keeping this in one helper prevents credential lookup rules from being repeated.

*Call graph*: called by 2 (spectrum_configured, spectrum_project); 1 external calls (deploy_env).


##### `spectrum_configured`  (lines 329–331)

```
def spectrum_configured() -> bool
```

**Purpose**: This function reports whether Spectrum Cloud credentials are available. It is a simple feature flag for the cloud-backed iMessage provider.

**Data flow**: It calls _spectrum_pair. If that helper returns a project ID and secret, this function returns true; otherwise it returns false.

**Call relations**: imessage_offered uses this to decide whether iMessage should be offered at all, and line_provider uses it to decide whether to return the Spectrum cloud provider.

*Call graph*: calls 1 internal fn (_spectrum_pair); called by 2 (imessage_offered, line_provider).


##### `imessage_offered`  (lines 334–337)

```
def imessage_offered(public_base_url: str | None) -> bool
```

**Purpose**: This function tells the rest of the application whether iMessage connection should be offered in the current deployment. It allows either real Spectrum credentials or a plain local development setup.

**Data flow**: It receives the public base URL for the deployment. It returns true if Spectrum credentials are configured. If not, it checks whether the base URL represents a plain-local development environment and returns that result.

**Call relations**: This is used by setup or UI-facing code that needs to decide whether to show iMessage as an available option. It combines spectrum_configured with the local-development check from plain_local.

*Call graph*: calls 1 internal fn (spectrum_configured); 1 external calls (plain_local).


##### `line_provider`  (lines 340–347)

```
def line_provider(public_base_url: str | None) -> MessageProvider
```

**Purpose**: This function chooses the actual iMessage provider object for this deployment. It returns SpectrumProject for real cloud credentials, LocalLine for plain local development, or a clear configuration error otherwise.

**Data flow**: It receives the public base URL. If Spectrum is configured, it returns the cached SpectrumProject. If not, but the deployment is plain-local, it creates and returns a LocalLine. If neither condition is true, it raises ProviderNotConfigured with instructions for the required environment variables.

**Call relations**: This is the main provider-selection entry for this file. It calls spectrum_configured and spectrum_project for the cloud path, plain_local and LocalLine for the development path, and ProviderNotConfigured for the refusal path.

*Call graph*: calls 2 internal fn (spectrum_configured, spectrum_project); 3 external calls (__init__, __init__, plain_local).


##### `spectrum_project`  (lines 351–362)

```
def spectrum_project() -> SpectrumProject
```

**Purpose**: This function builds and caches the SpectrumProject used by the deployment. Caching means the app reuses the same provider object instead of recreating HTTP clients and token state each time.

**Data flow**: It reads the Spectrum credential pair. If the pair is missing, it raises ProviderNotConfigured. If present, it creates a SpectrumProject with the project ID, project secret, an async HTTP client, an asyncio lock, and an empty token cache, then returns it. Because it is cached, later calls return the same object.

**Call relations**: line_provider calls this when it chooses the cloud-backed provider. Internally, the created SpectrumProject later supplies all send, receive, token, and attachment behavior.

*Call graph*: calls 1 internal fn (_spectrum_pair); called by 1 (line_provider); 4 external calls (__init__, __init__, Lock, AsyncClient).


##### `rpc_metadata`  (lines 365–369)

```
def rpc_metadata(token: str, idempotency_key: str | None=None) -> tuple[tuple[str, str], ...]
```

**Purpose**: This helper builds the small set of metadata headers sent with Spectrum gRPC calls. Metadata here works like request headers: it carries the bearer token and, when needed, a retry-safe idempotency key.

**Data flow**: It receives a token and optional idempotency key. It always creates an authorization entry using the bearer token. If an idempotency key is provided, it adds an x-idempotency-key entry. It returns the entries as an immutable tuple.

**Call relations**: catch_up, subscribe, send_text, send_attachment, and download_attachment all call this before making gRPC calls. It keeps authentication header formatting consistent across every Spectrum gRPC request.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe).


##### `_inbound_message`  (lines 372–410)

```
def _inbound_message(event: object) -> InboundMessage | None
```

**Purpose**: This function turns a raw Spectrum message-change event into the project’s clean InboundMessage object, but only when the event is a real incoming user message. It filters out noise such as outgoing messages, system messages, spam, corrupt messages, stickers, hidden attachments, and empty content.

**Data flow**: It receives an arbitrary event object. If the object is not the expected Spectrum message-change type, or it is not a received message, it returns None. It checks message flags to reject messages the app should ignore. It finds the sender from the event actor or message sender, collects visible non-sticker attachments into MessageAttachment objects, reads the text if present, and returns None if there is no sender or no useful content. Otherwise it returns an InboundMessage with message ID, conversation ID, sender, text, attachments, and whether the chat appears direct.

**Call relations**: catch_up and subscribe call this for raw message_changed frames. It is the translation gate between Spectrum’s detailed event format and the simpler provider events that the rest of the application understands.

*Call graph*: called by 2 (catch_up, subscribe); 2 external calls (__init__, __init__).


### Terminal stream relay
Redis-backed terminal streaming keeps remote terminal sessions connected across separate user-facing and background worker pods.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `cross-pod terminal request handling`

In a single-process system, a workflow can talk directly to the terminal connection it is holding. In a fleet of pods, that assumption breaks: the workflow may run on one pod while the live terminal connection is held by another. This file is the meeting place between them. Think of Redis as the message desk in a hotel: one pod leaves a note saying “the terminal is here,” another pod leaves an operation request, and the connection pod picks it up and later drops off the reply.

Redis keys record which conversation has a live terminal, which operation is currently in progress, which stream carries operations, and which stream carries replies. The blob store carries larger bodies, such as data copied into the terminal or a large reply copied out. The code is careful about time limits. Every wait has a deadline, so a workflow is not left stuck forever if the terminal disappears or Redis becomes unreachable.

The main class, RedisTerminals, publishes terminal presence, waits for a terminal to arrive, sends one operation at a time using a Redis lock, lets the connection pod claim the next operation, and delivers replies back to the waiting workflow. It also uses short-lived keys and cleanup so old operations do not pile up or accidentally run twice after reconnects.

#### Function details

##### `_text`  (lines 55–58)

```
def _text(value: bytes | str) -> str
```

**Purpose**: Turns a Redis field into normal Python text. Redis may return either bytes or strings, and the rest of this file wants to treat both the same way.

**Data flow**: It receives one Redis value. If it is already text, it returns it unchanged; if it is bytes, it decodes the bytes into text. Nothing outside the return value is changed.

**Call relations**: This small helper is used wherever Redis data is read back, including operation decoding, reply decoding, binding reads, gate checks, and Lua-script results. It keeps those callers from each having to repeat the same bytes-versus-text check.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 61–66)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: Turns Redis’s flat list of alternating field names and values into a normal dictionary. This makes a Lua-script result easier for the Python code to read.

**Data flow**: It receives an object that should be a list like field, value, field, value. It checks that shape, converts each item to text through _text, and returns a field-to-value map. If Redis returns an unexpected shape, it raises an error instead of guessing.

**Call relations**: RedisTerminals.next_op uses this after the Redis Lua script claims an operation. _pairs prepares the raw stream fields so _decode_op can turn them into a TerminalOp.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 69–74)

```
def _bind_payload(cwd: str, member_id: UUID | None, runtime_id: str) -> str
```

**Purpose**: Builds the small JSON record that says where a terminal is and who it belongs to. This record is what other pods read to find the terminal workspace.

**Data flow**: It receives a current working directory, an optional member ID, and a runtime ID. It writes those into a JSON string, using the member ID’s hex form when present. The output is stored in Redis by callers.

**Call relations**: The heartbeat uses it to publish the live binding, and _run_op uses it to pin the same binding while an operation is in progress. That shared format lets readers treat a live terminal and an in-flight terminal the same way.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 148–157)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts entries from the Redis XREAD response format this code expects. It fails clearly if Redis returns a different shape, rather than silently reading the wrong thing.

**Data flow**: It receives an XREAD batch. Empty input becomes an empty list. Otherwise it verifies that the batch is the expected list form and returns the stream entries inside it.

**Call relations**: RedisTerminals._await_reply calls this after reading the reply stream. The helper shields reply waiting from Redis response-format details.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 185–202)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the currently running asyncio event loop. This matters because asyncio Redis clients are tied to the loop that created them.

**Data flow**: It looks up the current event loop, checks whether this RedisTerminals instance already has a client for that loop, and creates one from the Redis URL if needed. The returned client has socket timeouts so blocking reads cannot hang forever on a broken connection.

**Call relations**: Almost every Redis operation in this class goes through _client, including heartbeat publishing, binding reads, sending operations, waiting for replies, claiming work, staging data, delivering replies, and cleanup. It is the shared doorway from this transport into Redis.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 204–205)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores the live terminal binding for a conversation. The key is used as a short-lived “this terminal is currently connected” marker.

**Data flow**: It receives a conversation ID and returns a string key containing that ID. It does not contact Redis itself.

**Call relations**: The heartbeat writes this key, and _read_binding reads it before falling back to the in-flight binding. This keeps key naming consistent between writer and reader.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 207–208)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores a terminal binding while an operation is already running. This keeps the terminal discoverable even after the held stream has handed off work.

**Data flow**: It receives a conversation ID and returns the matching Redis key string. It does not change Redis by itself.

**Call relations**: _run_op writes this key, _read_binding reads it when the live binding is absent, and _clear_op removes it after the operation is done.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 210–211)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name that carries operation requests for one conversation. A Redis Stream is an append-only message log that readers can poll or replay by position.

**Data flow**: It receives a conversation ID and returns the operation stream name for that conversation. It has no side effects.

**Call relations**: _run_op appends operation requests to this stream, next_op reads and claims from it, and _clear_op deletes the completed entry.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 213–214)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: Builds the Redis Stream name that carries the reply for one operation. Each operation gets its own reply stream so the sender can wait for exactly its answer.

**Data flow**: It receives an operation ID and returns the reply stream key string. It does not read or write data.

**Call relations**: _await_reply reads this stream, _deliver_reply writes to it, and _clear_op deletes it during cleanup.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 216–217)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis lock key that prevents two terminal operations for the same conversation from running at the same time. The lock acts like a single-file line at a service counter.

**Data flow**: It receives a conversation ID and returns the matching lock key. It does not acquire the lock itself.

**Call relations**: RedisTerminals.send uses this key when it creates the Redis lock before posting an operation. That keeps later sends queued behind the operation already in progress.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 219–220)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key used as a delivery marker for an operation. The marker helps ensure a reconnecting stream does not render and run the same operation twice.

**Data flow**: It receives an operation ID and returns the delivery-marker key string. It has no side effects.

**Call relations**: The Redis Lua script in next_op creates delivery markers using the same prefix, and _clear_op removes the marker after completion. This helper is used during cleanup.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 222–223)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key that stores metadata about an in-flight operation. The metadata says which conversation and member the operation belongs to.

**Data flow**: It receives an operation ID and returns the metadata key string. It does not read or write the metadata itself.

**Call relations**: _run_op writes this key, staged and _deliver_reply read it to check whether a request is allowed, and _clear_op removes it afterward.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 225–226)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for an operation’s copied-in body. The blob store is used when raw bytes are too large or unsuitable for Redis stream fields.

**Data flow**: It receives an operation ID and returns the blob key for that operation body. It does not touch the blob store itself.

**Call relations**: _run_op writes the body to this key, staged reads it for the serving side, and _clear_op deletes it when the operation is finished.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 228–229)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for a large operation reply. Small replies are placed directly in Redis, but large replies are stored as blobs.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s reply. It has no side effects.

**Call relations**: _deliver_reply writes large replies to this key, _decode_reply reads them when the reply stream says a blob was used, and _clear_op deletes them afterward.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 231–253)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that this pod is currently holding a terminal connection for a conversation. It starts or shares a background heartbeat that keeps the Redis binding alive.

**Data flow**: It receives the conversation ID, workspace path, optional member ID, and optional runtime ID. Under a thread lock, it creates a local hold record if needed, starts _heartbeat on the current event loop, and increments the local connection count. It returns nothing.

**Call relations**: Connection-serving code calls this when a held terminal stream opens. It hands ongoing Redis publication to _heartbeat so other pods can discover the terminal.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 255–264)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Notes that one local terminal connection for a conversation has ended. When the last local connection leaves, it stops the heartbeat.

**Data flow**: It receives a conversation ID, finds the local hold under a lock, decrements its connection count, and cancels the heartbeat task if no connections remain. It does not delete the Redis binding directly; the binding expires naturally.

**Call relations**: Connection-serving code calls this when a held stream closes. Its choice to cancel rather than delete works with _heartbeat’s time-to-live refresh model and avoids erasing another pod’s newer binding.


##### `RedisTerminals._heartbeat`  (lines 266–280)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Keeps the live terminal binding fresh in Redis while this pod holds the connection. It is the repeating “still here” signal.

**Data flow**: It receives the conversation and workspace details, builds the JSON binding with _bind_payload, and repeatedly sets the binding key in Redis with an expiry time. Redis errors are ignored for that tick, and the loop sleeps before trying again.

**Call relations**: connect starts this task when a pod begins holding a terminal connection. It uses _bind_key and _client to publish the binding, and disconnect cancels the task when the last local connection ends.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 282–295)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the terminal workspace information if this exact pod is holding the conversation locally. It is a fast local lookup with no Redis call.

**Data flow**: It receives a conversation ID, checks the local holds map under a lock, and returns a TerminalWorkspace built from the hold record if present. If this pod has no local hold, it returns None.

**Call relations**: This is useful for local inspection of a held connection. Cross-pod discovery uses arrived and _read_binding instead, because other pods cannot see this in-memory map.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 297–310)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal binding to appear in Redis. This covers normal reconnect gaps where the member’s client is between held streams.

**Data flow**: It receives a conversation ID and a grace period in seconds. Until the deadline, it calls _read_binding; if a workspace appears it returns it, otherwise it sleeps briefly and tries again. If the grace period ends, it returns None.

**Call relations**: send calls arrived before attempting an operation. arrived delegates the actual Redis read to _read_binding and decides whether the terminal is present soon enough to use.

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 312–330)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Reads the terminal workspace binding from Redis. It checks both the live connection marker and the pinned in-flight marker.

**Data flow**: It receives a conversation ID, reads the live binding key from Redis, then the in-flight key if needed. If no value exists, it returns None. If a JSON binding exists, it parses the working directory, member ID, and runtime ID into a TerminalWorkspace.

**Call relations**: arrived calls this during its polling loop. It uses _bind_key, _inflight_key, _client, and _text so sends can find a terminal even while a long operation is already running.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 332–388)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one terminal operation and waits for its reply. It turns terminal absence, Redis trouble, and missed deadlines into clear terminal-specific errors.

**Data flow**: It receives the conversation, operation kind, timeout, optional names and parameters, and optional body bytes. It waits for a binding with arrived, creates a TerminalOp, acquires a per-conversation Redis lock, runs _run_op under a deadline, and returns the reply bytes. It releases the lock in the end.

**Call relations**: Workflow-side code uses send when it needs the member’s terminal to do something. send coordinates arrival checking, locking via _lock_key, the actual operation work in _run_op, and error translation for callers.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 6 external calls (__init__, __init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 390–436)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: Performs the actual send after the conversation lock has been acquired. It stages any input bytes, posts the operation to Redis, waits for the reply, and then cleans up.

**Data flow**: It receives the conversation, TerminalOp, optional body, bound workspace, and deadline. It writes operation metadata and an in-flight binding to Redis, optionally stores the body in the blob store, appends the operation to the Redis operation stream, waits through _await_reply, and finally calls _clear_op. The normal output is the reply bytes.

**Call relations**: send calls this only after locking. It uses the key helpers, _op_fields, the blob store, Redis XADD, _await_reply for the response path, and _clear_op for teardown.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 438–446)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: Converts a TerminalOp into the field map stored in the Redis operation stream. This is the compact message the connection pod will later read.

**Data flow**: It receives a TerminalOp and returns a dictionary with the operation ID, kind, timeout, name, argument, and parameters as Redis-storable values. It does not write to Redis itself.

**Call relations**: _run_op calls this immediately before appending to the operation stream. next_op later reads those fields and _decode_op turns them back into a TerminalOp.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 448–456)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: Turns Redis stream fields back into a TerminalOp object. This gives the connection pod a normal operation object to render or execute.

**Data flow**: It receives a dictionary of Redis fields, converts needed values to text with _text, parses the timeout as an integer, fills in missing optional fields with empty strings, and returns a TerminalOp.

**Call relations**: next_op calls this after claiming an operation and converting Lua output with _pairs. It is the receiving-side mirror of _op_fields.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 458–481)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: Waits for the reply stream entry for one operation, but only until the operation deadline. It prevents a workflow from waiting forever for a terminal that has gone away.

**Data flow**: It receives an operation ID, a deadline budget, and the user-visible timeout. It repeatedly XREADs the reply stream with a bounded block time, extracts entries with _stream_entries, and decodes the first reply with _decode_reply. If time runs out or Redis is unreachable, it raises TerminalGone.

**Call relations**: _run_op calls this after posting an operation. _deliver_reply is the counterpart that writes the stream entry this function is waiting to read.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 483–496)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: Interprets a reply stream entry and returns the reply bytes, or raises a clear operation failure. It understands both inline replies and blob-backed large replies.

**Data flow**: It receives an operation ID and reply fields. If the fields contain a failure message, it raises TerminalOpFailed. If they point to a blob, it reads the reply blob with a timeout; otherwise it base64-decodes the inline bytes and returns them.

**Call relations**: _await_reply calls this once it has a reply entry. _deliver_reply writes entries in exactly the formats this decoder expects.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 498–529)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for and claims the next operation for a conversation’s held terminal stream. Claiming means marking it so another reconnecting stream does not run the same operation again.

**Data flow**: It receives a conversation ID and optionally an operation ID to skip. It runs a Redis Lua script that scans old stream entries, removes expired ones, and claims the first unclaimed valid operation. If one is found, it decodes it and returns a TerminalOp; otherwise it waits briefly for new stream data and tries again.

**Call relations**: The connection-holding side calls next_op while waiting for work to render to the client. It reads from the stream that _run_op writes and uses _pairs, _text, and _decode_op to turn the claimed Redis entry into an operation.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 531–546)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Fetches the copied-in body for an in-flight operation, if the request is allowed to see it. This lets any pod serve the body bytes from the shared blob store.

**Data flow**: It receives the conversation ID, operation ID, and optional member ID. It reads the operation metadata from Redis, checks it with _gate_ok, and then reads the operation body blob with a timeout. If the metadata is missing, mismatched, or the blob cannot be read, it returns None.

**Call relations**: The serving side uses staged when it needs the bytes that _run_op stored for an operation. _gate_ok enforces that the request matches the conversation and member named by the original binding.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 548–563)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts a terminal operation reply and schedules delivery to Redis without making the HTTP or stream handler wait. It reports success immediately because the waiting sender has its own timeout.

**Data flow**: It receives the conversation ID, operation ID, reply bytes, optional failure text, and optional member ID. It creates a coroutine for _deliver_reply, schedules it on the current event loop through _spawn, and returns True right away.

**Call relations**: Reply-handling code calls resolve after the client has produced an answer. resolve hands the real Redis write to _deliver_reply and uses _spawn so failures are logged instead of disappearing.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 565–594)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: Writes the operation reply into Redis, after checking that it belongs to the right conversation and member. It stores large replies in the blob store and small replies directly in the reply stream.

**Data flow**: It receives the conversation ID, operation ID, reply bytes, optional failure message, and optional member ID. It reads operation metadata, rejects missing or mismatched replies with a warning, chooses failure, blob, or base64-inline fields, writes the reply stream entry, and sets an expiry on that stream.

**Call relations**: resolve schedules this in the background. _await_reply is waiting on the reply stream this function writes, and _decode_reply understands the field formats it creates.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 596–607)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a staged-body or reply request matches the operation’s recorded conversation and member. It fails closed, meaning uncertain or mismatched requests are refused.

**Data flow**: It receives raw metadata from Redis, a conversation ID, and an optional member ID from the requester. It parses the JSON metadata, compares the conversation ID, and if a member was named by the requester, requires it to match the recorded member. It returns True or False.

**Call relations**: staged uses this before exposing copied-in bytes, and _deliver_reply uses it before accepting a reply. It relies on _text to normalize the Redis metadata before JSON parsing.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 609–612)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports no local in-flight operation for this cross-pod transport. The operation being awaited may live on another pod, so a local answer would be misleading.

**Data flow**: It receives a conversation ID but does not look up Redis or local state. It always returns None.

**Call relations**: Operator or inspection code may call this through the terminal transport interface. This implementation deliberately avoids pretending it can see another pod’s local in-memory operation.


##### `RedisTerminals._clear_op`  (lines 614–635)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: Best-effort cleanup after an operation finishes or is abandoned. It removes Redis entries, markers, temporary streams, and blob-store objects tied to that operation.

**Data flow**: It receives the conversation ID, operation ID, and optional Redis stream entry ID. It deletes the operation stream entry if known, deletes metadata, delivery, in-flight, and reply keys, then tries to delete the body and reply blobs with time limits. Cleanup errors are suppressed so teardown does not hang the caller.

**Call relations**: _run_op calls this in a finally block after waiting for the reply. Safety does not depend only on this cleanup, because Redis expiries and delivery markers also limit stale state, but this keeps the system tidy when things finish normally.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 637–644)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: Starts a background task for fire-and-return reply delivery and keeps a reference to it. Keeping the reference prevents the task from being garbage-collected while still running.

**Data flow**: It receives a coroutine and an event loop. It wraps the coroutine with _logged, creates a task on the loop, stores the task in the instance’s task set, and arranges for it to remove itself from the set when done.

**Call relations**: resolve calls _spawn when it schedules _deliver_reply. _spawn hands execution to _logged so any exception from the delivery path becomes an observability warning.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 646–650)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: Runs a background coroutine and logs any exception it raises. This keeps background reply delivery failures visible.

**Data flow**: It receives a coroutine that should return nothing. It awaits it; if the coroutine raises any exception, it catches the error and emits a warning message. It does not return useful data.

**Call relations**: _spawn uses this wrapper for scheduled background work. In practice, it records failures from _deliver_reply so resolve can return immediately without silently losing delivery errors.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).


### Slack message utilities
Slack-specific helpers preserve readable conversation text while handling connector attribution and outbound mention formatting.

### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `Slack connector send and Slack event filtering`

When this system sends a Slack message through a connector, the message is not produced by the usual Slack rendering path. That means the connector needs its own way to mark, “this came from the UFO agent.” This file supplies that mark as a Slack mention of the installed bot user, so a person reading the message can click through to the agent.

The tricky part is that a Slack mention is also how people talk to the bot. If the system appends a footer like “sent by <@bot>,” Slack may report the message as mentioning the app. Without this file’s checks, the system could wrongly treat its own outbound message as a person asking the bot something, or hide it from summaries as bot-directed traffic.

The file solves both sides together. On the sending side, it detects connector calls that are Slack message sends and appends the standard attribution footer, unless one is already present. On the receiving side, it collects all text-like parts of a Slack event, including nested block text, and tests whether the bot was mentioned after removing any attribution footer. In other words, it treats the footer like a signature on a letter: useful for identifying the sender, but not part of the conversation addressed to the recipient.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: This function decides whether a connector call is specifically a Slack message send. It keeps this Slack-specific attribution logic aligned with the connector tool’s own idea of which calls should receive a footer.

**Data flow**: It receives a connector provider name and a tool slug. It lowercases the slug, then checks that the provider is Slack, the slug looks like it is about messages, and the slug contains one of the known send verbs. It returns true only when all of those signs point to a Slack message being published.

**Call relations**: Other Slack connector code can use this as the gate before adding attribution. It does not call other functions in this file; it simply mirrors the connector attribution rule so the sender and footer logic do not drift apart.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: This function returns connector arguments with a bot-mention attribution footer added. It is used so a Slack message sent through a connector visibly points back to the deployed bot user, unless the message already has such a footer.

**Data flow**: It receives the outgoing connector arguments and the Slack bot user ID. It formats the standard attribution subject with that bot ID, then passes the arguments and subject to the shared connector attribution helper. The result is a new or updated argument dictionary with the footer added in the expected shape, or the original content left alone if it was already attributed.

**Call relations**: This function hands off the detailed footer-building work to `ufo_ext_connectors.tools.attributed_arguments`, using `UFO_ATTRIBUTION_MENTION_SUBJECT.format` to create the bot mention text first. It is the Slack-specific wrapper around the shared connector attribution behavior.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: This function checks whether a piece of text really mentions the bot, ignoring mentions that appear only in this system’s own attribution footer. It helps distinguish a person talking to the bot from a system-written signature that happens to contain the bot’s Slack mention.

**Data flow**: It receives text and the bot user ID. It first removes any recognized attribution footer from the text, then searches the remaining text for Slack’s mention form for that bot, such as `<@U123>`. It returns true if the mention is still present after the footer is removed, and false otherwise.

**Call relations**: This function relies on `ufo_ext_connectors.tools.attribution_stripped` to remove attribution text before checking for the bot mention. It is used on the receiving or filtering side of Slack behavior, where the system must decide whether a message is actually addressed to the agent.

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: This function pulls out every text string in a Slack message event where a bot mention might be hiding. It looks beyond the top-level message text because Slack block messages can store visible text deep inside nested structures.

**Data flow**: It receives a Slack event-like mapping. It reads the top-level `text` value, defaulting to an empty string when missing, then reads the message `blocks` and asks `_nested_strings` to find all strings inside them. It returns one tuple containing the top-level text followed by all nested strings found in the blocks.

**Call relations**: This function calls `_nested_strings` to walk through Slack’s nested block data. Its output can be fed into mention-detection logic such as `addressing_mention`, so the system does not miss a mention that Slack placed inside a block rather than in plain message text.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: This helper walks through nested Slack data and yields every string it can find. It exists because Slack block content can be shaped like a tree of dictionaries and lists, not just one flat text field.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, it recursively checks each stored value. If it is a list, it recursively checks each item. Other kinds of values are ignored. The output is a stream of strings found anywhere inside the original value.

**Call relations**: `message_bodies` calls this helper when it needs to inspect the nested `blocks` part of a Slack event. `_nested_strings` does not know about Slack rules itself; it simply performs the careful walk through the data so the caller can collect possible message text.

*Call graph*: called by 1 (message_bodies).


### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message ingest and reply sending`

Slack does not send messages exactly as people see them. A user mention may arrive as something like `<@U0BG8632NDS>`, a channel as `<#C0271QK6M|ufo-eng>`, and a link as `<https://example.com|the docs>`. That is useful for Slack’s servers, but not for a human reader or a language model trying to understand a conversation. This file is the translator between Slack’s “wire format” and the words people expect to read.

On the way in, `render_markup` rewrites known Slack entities into plain-looking text such as `@Real Name`, `#ufo-eng`, `@here`, or `the docs (https://example.com)`. If it cannot safely resolve an ID to a name, it leaves the original code alone rather than inventing a name.

On the way out, `mention_markup` does a narrower job. If the agent writes `@Real Name`, and the caller has supplied a trusted one-person mapping for that name, it changes the text to Slack’s notification form, such as `<@U0BG8632NDS>`. It deliberately avoids code blocks, URLs, existing angle-bracket forms, ambiguous names, broadcast words like `@here`, and too many mentions in one message. This is like a careful mailroom clerk: it labels envelopes only when the address is trusted and unambiguous.

#### Function details

##### `mentioned_users`  (lines 67–70)

```
def mentioned_users(text: str) -> frozenset[str]
```

**Purpose**: Finds the Slack user IDs that are explicitly mentioned in a piece of Slack-formatted text. A caller can then look up those IDs in Slack to learn the people’s names.

**Data flow**: It receives a text string that may contain Slack mention codes. It asks the shared `_mentioned` scanner to look only for user-style mentions, marked with `@`. It returns a frozen set of user ID strings, with duplicates removed.

**Call relations**: This is the user-specific front door for mention discovery. When another part of the Slack extension needs to know which people a message refers to, it calls this function, which delegates the actual scanning work to `_mentioned`.

*Call graph*: calls 1 internal fn (_mentioned).


##### `mentioned_channels`  (lines 73–75)

```
def mentioned_channels(text: str) -> frozenset[str]
```

**Purpose**: Finds the Slack channel IDs that are explicitly mentioned in a piece of Slack-formatted text. A caller can then ask Slack for the channel names.

**Data flow**: It receives a text string that may contain Slack channel codes. It asks `_mentioned` to look only for channel-style mentions, marked with `#`. It returns a frozen set of channel ID strings, with duplicates removed.

**Call relations**: This is the channel-specific twin of `mentioned_users`. Code that needs to resolve channel references calls it, and it relies on `_mentioned` for the common parsing step.

*Call graph*: calls 1 internal fn (_mentioned).


##### `_mentioned`  (lines 78–83)

```
def _mentioned(text: str, kind: str) -> frozenset[str]
```

**Purpose**: Scans Slack-formatted text for entity codes of one requested kind, such as users or channels, and extracts their IDs. It exists so user and channel mention lookup share the same careful parsing rule.

**Data flow**: It receives the message text and a kind marker, such as `@` or `#`. It runs the Slack entity pattern across the text, keeps only matches whose kind marker is the requested one, and collects the non-empty ID part. It returns those IDs as a frozen set.

**Call relations**: `mentioned_users` and `mentioned_channels` both call this helper when they need IDs from Slack’s encoded message text. It does not resolve names itself; it only extracts the raw IDs so other code can look them up.

*Call graph*: called by 2 (mentioned_channels, mentioned_users).


##### `render_markup`  (lines 86–96)

```
def render_markup(text: str, names: Mapping[str, str]) -> str
```

**Purpose**: Turns Slack’s encoded message entities into the readable words a person or model should see. This is used when accepting incoming Slack text so the stored conversation is understandable.

**Data flow**: It receives Slack-formatted text and a mapping from Slack IDs to readable names. It searches for Slack entity shapes, such as user mentions, channel mentions, broadcasts, and links, and replaces each one with a readable form when it can. It returns a new text string; it does not change the name mapping.

**Call relations**: This function is the main inbound translation step. As a message enters the system, callers use it so downstream readers see `@Name` or `#channel` instead of opaque Slack IDs. For each entity it finds, it uses the file’s entity-rendering rules to decide whether to show a name, a label, a broadcast, a link, or the original encoded text.


##### `unescape`  (lines 99–108)

```
def unescape(text: str) -> str
```

**Purpose**: Changes Slack’s escaped versions of `&`, `<`, and `>` back into the characters the message author typed. It is kept separate from mention rendering because unescaping someone else’s quoted words can be unsafe.

**Data flow**: It receives a text string that may contain `&amp;`, `&lt;`, or `&gt;`. It replaces those exact escape sequences with `&`, `<`, and `>`. It returns the unescaped string and changes nothing outside that returned value.

**Call relations**: This function is used only when the caller knows it is safe to restore the original author’s own characters. It is intentionally not part of `render_markup`, because ambient or quoted messages from other people should often keep their escapes to prevent them from being mistaken for markup.


##### `mention_key`  (lines 111–115)

```
def mention_key(name: str) -> str
```

**Purpose**: Builds the normalized lookup key used to compare mention names. It makes small spelling differences, such as extra spaces or letter case, stop mattering.

**Data flow**: It receives a name string. It splits the name on whitespace, joins the pieces back with single spaces, and case-folds it, which is a strong form of lowercasing for text comparison. It returns that normalized key.

**Call relations**: `mention_index` calls this when building the trusted name-to-ID table, and `_mention_at` calls it when checking whether text after an `@` matches one of those names. This keeps both sides of the comparison using the same rules.

*Call graph*: called by 2 (_mention_at, mention_index).


##### `mention_index`  (lines 118–132)

```
def mention_index(names: Mapping[str, str]) -> dict[str, str]
```

**Purpose**: Turns a trusted ID-to-name list into a safe name-to-ID lookup table for outbound mentions. It refuses to map names that are blank, broadcast words, or shared by more than one ID.

**Data flow**: It receives a mapping from Slack IDs to readable names. For each name, it creates a normalized key with `mention_key`, groups all IDs that claim that same key, and keeps only keys claimed by exactly one ID and not equal to broadcast words such as `here`, `channel`, or `everyone`. It returns a dictionary from normalized name key to Slack ID.

**Call relations**: Callers prepare this index before using `mention_markup` to send a reply. The function calls `mention_key` so outbound matching uses the same name-cleaning rule as the later text scan. By removing ambiguous names up front, it prevents the send path from paging the wrong person.

*Call graph*: calls 1 internal fn (mention_key).


##### `mention_markup`  (lines 135–166)

```
def mention_markup(text: str, ids: Mapping[str, str], limit: int=MENTION_MARKUP_MAX) -> str
```

**Purpose**: Rewrites safe, allowed `@Name` text in an outgoing reply into Slack’s real notification form. It is cautious so the agent does not accidentally notify people mentioned only in quoted text, code, links, or a recap.

**Data flow**: It receives outgoing text, a trusted name-key-to-ID mapping, and a maximum number of mentions to rewrite. If there are no IDs, it returns the text unchanged. Otherwise it finds places that should be skipped, scans each `@`, ignores ones inside skipped spans or attached to things like email addresses, asks `_mention_at` whether a known name begins there, and replaces accepted names with `<@ID>` until the limit is reached. It returns the rewritten text.

**Call relations**: This is the main outbound translation step before a reply goes to Slack. It calls `_mention_at` to decide whether the text after an `@` matches a trusted person. Its regular-expression scan finds candidate `@` symbols, while the helper does the name matching.

*Call graph*: calls 1 internal fn (_mention_at); 1 external calls (finditer).


##### `_mention_at`  (lines 169–182)

```
def _mention_at(text: str, start: int, ids: Mapping[str, str]) -> tuple[int, str] | None
```

**Purpose**: Checks whether a known mention name starts at a specific position in the text, and prefers the longest matching name. This lets names with spaces work without guessing from a simple word boundary.

**Data flow**: It receives the full text, the position just after an `@`, and the trusted name-key-to-ID mapping. It looks only on the same line, limits how many characters and words it considers, strips common trailing sentence punctuation, normalizes each candidate with `mention_key`, and checks the mapping. If it finds a match, it returns where the name ends and the Slack ID; otherwise it returns nothing.

**Call relations**: `mention_markup` calls this after it finds a possible outbound `@` mention. This helper uses regular-expression word finding and `itertools.islice` to limit the search, then uses `mention_key` so its lookup matches the table built by `mention_index`.

*Call graph*: calls 1 internal fn (mention_key); called by 1 (mention_markup); 2 external calls (islice, finditer).


##### `_entity`  (lines 185–201)

```
def _entity(match: re.Match[str], names: Mapping[str, str]) -> str
```

**Purpose**: Decides how one matched Slack entity should appear as readable text. It covers user mentions, channel mentions, broadcast mentions, and links.

**Data flow**: It receives one regex match for a Slack entity and a mapping from IDs to readable names. For a user or channel, it uses the supplied name if available, otherwise Slack’s label if present, and otherwise leaves the original code alone. For broadcasts, it returns forms like `@here` only for recognized broadcast names. For links, it returns either the URL alone or `label (URL)` when the visible label differs from the URL.

**Call relations**: This is the per-entity decision maker used by the inbound rendering process. `render_markup` finds Slack entity shapes in a whole message, and this helper supplies the replacement text for each one.
