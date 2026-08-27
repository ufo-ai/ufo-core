# Chat, Terminal, and Messaging Surfaces  `stage-6.2`

This stage is the set of “front doors” where people talk to the system through Slack, iMessage, or the command-line terminal. It sits around the main work loop: it accepts outside messages, turns them into the project’s common conversation format, then delivers the agent’s replies back to the same place.

The Slack surface is the busiest doorway. It receives Slack messages, button clicks, file shares, app installs, and thread changes, then sends replies and files back. Slack mentions are translated both ways: hidden Slack codes become readable names, and selected names become real Slack mentions when replying. Slack attribution adds a visible bot reference to connector-sent messages, while making sure that added mention is not later treated as a fresh request.

The iMessage surface does the same kind of translation for iMessage conversations. Its cloud bridge handles the lower-level connection to Spectrum’s service: credentials, network calls, attachments, incoming events, and provider-ready message objects.

The terminal surface exposes the system to the ufo command-line client using simple tab-separated text commands for messages, progress, files, prompts, and local terminal actions.

## Files in this stage

### iMessage Provider Bridge
Spectrum cloud transport and the iMessage surface translate between raw provider events and UFO conversations.

### `extensions/imessage/ufo_ext_imessage/cloud.py`

`io_transport` · `request handling and background message streaming`

This file lets the project use iMessage without talking to Apple directly. Spectrum provides the remote service that owns the iMessage line, and this code is the client for that service. Without it, the extension could not authenticate with Spectrum, send texts, upload or download attachments, or listen for incoming iMessages.

There are two kinds of network work here. First, normal HTTPS requests are used for project setup tasks, such as getting a short-lived shared-line token or registering a phone number. Second, gRPC is used for message traffic. gRPC is a network protocol where code calls remote service methods almost like local functions; here it is used to stream events and send message commands.

The central object is `SpectrumProject`. Think of it like a key ring and phone switchboard combined: it stores the project credentials, fetches and caches a temporary bearer token, opens secure channels to Spectrum, and exposes simple actions such as `send_text`, `subscribe`, and `download_attachment`. It also filters noisy message events so the rest of the extension only sees real inbound messages from other people, not spam, system messages, hidden stickers, or messages sent by this project itself.

One important detail is that token and HTTP-client state is kept per asyncio event loop. An event loop is the scheduler that runs asynchronous tasks. Keeping separate state avoids sharing async objects across loops, which can break in Python.

#### Function details

##### `SpectrumProject.installation_id`  (lines 91–92)

```
def installation_id(self) -> str
```

**Purpose**: This gives the project a stable identity string used by the provider layer. It labels this connection as a Spectrum project rather than, for example, a local device.

**Data flow**: It reads the stored `project_id` on the `SpectrumProject` object, prefixes it with `project:`, and returns that combined text. It does not contact the network or change anything.

**Call relations**: Other parts of the provider can ask the project for this identifier when they need a consistent name for the installed messaging backend. It stands alone and does not hand work to other functions.


##### `SpectrumProject.line`  (lines 94–115)

```
async def line(self) -> SpectrumLine
```

**Purpose**: This gets the temporary shared-line token needed to call Spectrum's iMessage services. It reuses a still-valid token when possible, and asks Spectrum for a fresh one when the old token is missing or close to expiring.

**Data flow**: It looks inside the current event loop's token state for a cached `SpectrumLine` and its expiry time. If the token is still safely valid, it returns it. Otherwise it sends a cloud request for a new token, checks that Spectrum's reply has the expected shape, stores the new token and expiry time, and returns the new `SpectrumLine`.

**Call relations**: Before message-related work can happen, `catch_up`, `subscribe`, `send_text`, `send_attachment`, and `download_attachment` call this function to get authorization. It relies on `_loop` to find the right per-loop state and `_request` to talk to Spectrum's HTTPS API.

*Call graph*: calls 2 internal fn (_loop, _request); called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 4 external calls (__init__, __init__, TypeAdapter, monotonic).


##### `SpectrumProject._loop`  (lines 117–133)

```
def _loop(self) -> SpectrumLoop
```

**Purpose**: This finds or creates the network state for the currently running asynchronous event loop. It prevents async objects such as locks and HTTP clients from being accidentally shared across event loops.

**Data flow**: It reads Python's current running event loop, checks a dictionary for existing `SpectrumLoop` state, and returns it if present. If this is the first loop, it reuses the project's original HTTP client, lock, and token state. If it is an additional loop, it creates a new HTTP client, lock, and token store just for that loop, saves them, and returns them.

**Call relations**: `line`, `_request`, and `invalidate` call this whenever they need loop-local state. It is the quiet safety layer underneath token caching and HTTP calls.

*Call graph*: called by 3 (_request, invalidate, line); 4 external calls (__init__, Lock, get_running_loop, AsyncClient).


##### `SpectrumProject.assign_line`  (lines 135–165)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: This makes sure a user's phone number is registered with Spectrum and returns the Spectrum phone line assigned to that user. It matters because a shared iMessage line cannot start a conversation with a phone number that has never messaged it first.

**Data flow**: It asks Spectrum for the project's current users, validates the reply, and searches for the requested phone number. If the user already exists, it returns the assigned phone number. If not, it sends a registration request with an idempotency key, validates the created user, and returns the newly assigned phone number.

**Call relations**: This function uses `_request` for the HTTPS calls and Pydantic validation to make sure Spectrum's replies match what the code expects. It is a setup step for connecting an outside phone number to the shared Spectrum line.

*Call graph*: calls 1 internal fn (_request); 2 external calls (__init__, TypeAdapter).


##### `SpectrumProject._request`  (lines 167–190)

```
async def _request(self, method: str, path: str, *, json: dict[str, str] | None=None, idempotency_key: str | None=None) -> object
```

**Purpose**: This is the common helper for Spectrum Cloud HTTPS API calls. It adds project authentication, optional duplicate-protection headers, checks for HTTP failures, and returns the JSON body.

**Data flow**: It receives an HTTP method, a path, optional JSON data, and an optional idempotency key. It builds the full Spectrum Cloud URL, authenticates with the project ID and secret, sends the request through the event loop's HTTP client, raises a project-specific error if Spectrum returns a bad HTTP status, and returns the decoded JSON response.

**Call relations**: `line` uses this to fetch shared-line tokens, and `assign_line` uses it to list or register users. It depends on `_loop` to choose the correct HTTP client for the current async loop.

*Call graph*: calls 1 internal fn (_loop); called by 2 (assign_line, line); 2 external calls (__init__, BasicAuth).


##### `SpectrumProject.channel`  (lines 192–193)

```
def channel(self) -> grpc.aio.Channel
```

**Purpose**: This opens a secure gRPC channel to Spectrum's iMessage service. A channel is the network connection used for streaming events and sending message commands.

**Data flow**: It uses Spectrum's fixed iMessage service address and creates a TLS-secured gRPC channel. The returned channel is later used inside `async with` blocks so it is closed after the operation.

**Call relations**: `catch_up`, `subscribe`, `send_text`, `send_attachment`, and `download_attachment` call this before using Spectrum's gRPC service stubs. It is the doorway from local code into Spectrum's message services.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 1 external calls (ssl_channel_credentials).


##### `SpectrumProject.invalidate`  (lines 195–198)

```
async def invalidate(self) -> None
```

**Purpose**: This forgets the cached shared-line token. It is useful when the token may no longer be trusted, so the next operation must fetch a fresh one.

**Data flow**: It gets the current loop's token state, takes the async lock so no other task edits it at the same time, and clears the stored token and expiry information. It returns nothing.

**Call relations**: Higher-level retry or error-handling code can call this after authentication trouble. It uses `_loop` to clear only the token cache tied to the current event loop.

*Call graph*: calls 1 internal fn (_loop).


##### `SpectrumProject.invalid_cursor`  (lines 200–204)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This recognizes one specific streaming error: Spectrum saying that an event cursor or sequence marker is invalid. A cursor is like a bookmark that tells the service where to resume reading events.

**Data flow**: It receives an exception, checks whether it is a gRPC error, and then checks whether the gRPC status code is `INVALID_ARGUMENT`. It returns `true` for that case and `false` otherwise.

**Call relations**: Code that consumes event streams can use this to decide whether it should reset its saved event position and catch up differently. It does not call Spectrum; it only classifies an error it was given.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.external_error`  (lines 206–207)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This tells the rest of the provider whether an exception came from the outside world: Spectrum gRPC, Spectrum Cloud, or HTTP networking. That helps separate remote-service failures from programming bugs.

**Data flow**: It receives an exception and checks whether it is a gRPC error, a `SpectrumCloudError`, or an HTTP client error. It returns a boolean answer and changes nothing.

**Call relations**: Higher-level error handling can call this when deciding what to retry, report, or treat as provider downtime. It is a classification helper for failures produced by the network functions in this file.


##### `SpectrumProject.error_code`  (lines 209–212)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This turns an exception into a short error label for logging or reporting. For gRPC errors, it uses the remote status code; for other errors, it uses the Python exception type name.

**Data flow**: It receives an exception. If it is a gRPC error, it asks the error for its status code name and returns that. Otherwise it returns the class name of the exception.

**Call relations**: Error-reporting code can use this after `external_error` or other exception handling. It gives callers a compact, consistent label without making them know the details of gRPC exceptions.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.catch_up`  (lines 214–235)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This reads older missed message events from Spectrum, starting after an optional saved sequence number. It lets the extension recover after downtime without losing messages.

**Data flow**: It first gets a valid shared-line token. It builds a catch-up request and includes `after_sequence` if one was provided. Then it opens a secure gRPC channel, sends the request with authorization metadata, and reads the stream of frames. Completion frames become `ProviderEvent` objects with the latest head sequence; message-change frames are filtered through `_inbound_message` and yielded as provider events.

**Call relations**: This is called when the provider needs to replay missed events before live streaming. It uses `line` for authorization, `channel` for the gRPC connection, `rpc_metadata` for request headers, and `_inbound_message` to convert Spectrum's raw message event into the extension's clean inbound-message shape.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 3 external calls (__init__, CatchUpEventsRequest, EventServiceStub).


##### `SpectrumProject.subscribe`  (lines 237–253)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This listens for new live iMessage events from Spectrum. It is the ongoing feed that lets the extension react when someone sends a message.

**Data flow**: It gets a valid token, creates an empty subscribe request, opens a secure gRPC channel, and starts Spectrum's live message-event stream. Once the stream is created, it sets the `ready` event so the caller knows listening has begun. For each incoming frame, it yields a `ProviderEvent`, including a cleaned inbound message only when the frame is a real received message.

**Call relations**: This is used during background message listening. It relies on `line`, `channel`, and `rpc_metadata` to connect safely, and calls `_inbound_message` to remove irrelevant or unsafe message changes before handing events to the provider.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 4 external calls (__init__, set, SubscribeMessageEventsRequest, MessageServiceStub).


##### `SpectrumProject.send_text`  (lines 255–268)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This sends a plain text iMessage into an existing Spectrum conversation. It returns Spectrum's message identifier so the caller can track what was sent.

**Data flow**: It receives a conversation ID, text, and idempotency key. It gets a valid shared-line token, builds a send-text gRPC request using the conversation and text, opens a secure channel, sends the request with authorization and idempotency metadata, and returns the GUID of the message Spectrum created.

**Call relations**: Higher-level provider code calls this when it needs to send a text reply. It uses `line` for the bearer token, `channel` for the gRPC connection, and `rpc_metadata` so Spectrum can authenticate the request and avoid duplicate sends.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (SendTextMessageRequest, MessageServiceStub).


##### `SpectrumProject.send_attachment`  (lines 270–297)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This sends a file attachment in an iMessage conversation. It first uploads the file to Spectrum, then sends a message that refers to that uploaded attachment.

**Data flow**: It receives a conversation ID, filename, file bytes, and idempotency key. It gets a valid token and opens a secure channel. First it uploads the attachment bytes with a separate upload idempotency key. Then it sends an attachment-message request that points to the uploaded attachment's GUID. It returns the GUID of the final message.

**Call relations**: Provider code calls this when sending images or other files. It uses `line`, `channel`, and `rpc_metadata` like `send_text`, but also talks to Spectrum's attachment service before handing the uploaded attachment to the message service.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 4 external calls (UploadAttachmentRequest, AttachmentServiceStub, SendAttachmentMessageRequest, MessageServiceStub).


##### `SpectrumProject.download_attachment`  (lines 299–309)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This downloads an attachment from Spectrum as a stream of byte chunks. Streaming means the file can be read piece by piece instead of loaded all at once.

**Data flow**: It receives an attachment ID, gets a valid token, opens a secure gRPC channel, and asks Spectrum's attachment service to download that attachment. As frames arrive from the stream, it yields only the primary file chunks as raw bytes.

**Call relations**: Higher-level code calls this when it needs the contents of an incoming attachment listed on an inbound message. It uses `line`, `channel`, and `rpc_metadata` to authenticate and connect to Spectrum's attachment service.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (DownloadAttachmentRequest, AttachmentServiceStub).


##### `spectrum_project`  (lines 313–327)

```
def spectrum_project() -> SpectrumProject
```

**Purpose**: This creates the shared `SpectrumProject` object from environment credentials. It is cached, so callers get the same project object instead of rebuilding clients and locks each time.

**Data flow**: It reads the Spectrum project ID and secret from deployment environment variables. If either is missing, it raises `ProviderNotConfigured` with instructions for what to set. If both exist, it creates an HTTP client, an async lock, an empty token cache, and returns a new `SpectrumProject`.

**Call relations**: This is the usual factory function other parts of the extension call to obtain the Spectrum connection. It prepares the object whose methods perform all later cloud and gRPC work.

*Call graph*: 5 external calls (__init__, __init__, Lock, AsyncClient, deploy_env).


##### `rpc_metadata`  (lines 330–334)

```
def rpc_metadata(token: str, idempotency_key: str | None=None) -> tuple[tuple[str, str], ...]
```

**Purpose**: This builds the small set of gRPC request headers Spectrum needs. It always includes bearer-token authorization and may include an idempotency key to prevent duplicate actions.

**Data flow**: It receives a token and optionally an idempotency key. It creates an authorization metadata pair using `Bearer <token>`, adds an `x-idempotency-key` pair if one was provided, and returns the metadata as an immutable tuple.

**Call relations**: `catch_up`, `subscribe`, `send_text`, `send_attachment`, and `download_attachment` call this right before making gRPC requests. It keeps authentication header formatting in one place.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe).


##### `_inbound_message`  (lines 337–375)

```
def _inbound_message(event: object) -> InboundMessage | None
```

**Purpose**: This turns Spectrum's raw message-change event into the extension's simpler `InboundMessage`, but only when the event is a real incoming user message. It filters out sent-by-me messages, system/service/spam/corrupt messages, hidden stickers, and empty messages.

**Data flow**: It receives a raw event object. If the object is not the expected message-change type, is not a received-message change, or was sent by this project, it returns nothing. It then checks the message flags, finds the sender address, collects visible non-sticker attachments, reads any text, and returns an `InboundMessage` containing the message ID, conversation ID, sender, text, attachments, and whether the chat looks direct. If there is no usable sender or content, it returns nothing.

**Call relations**: `catch_up` and `subscribe` call this while reading Spectrum event streams. It is the filter and translator between Spectrum's detailed protocol objects and the clean provider event model used by the rest of the extension.

*Call graph*: called by 2 (catch_up, subscribe); 2 external calls (__init__, __init__).


### `extensions/imessage/ufo_ext_imessage/surface.py`

`io_transport` · `main loop and message handling`

This file sits between two worlds: Apple-style messages coming from an iMessage provider, and UFO’s internal conversation system. Without it, a text sent to the shared iMessage line would never become a UFO turn, and UFO’s answers would never make it back to the chat.

The main class, ImessageSurface, keeps a long-running listener connected to the provider’s event stream. It remembers a cursor, which is like a bookmark in the message stream, so restarts do not replay old messages or skip new ones. If the provider disconnects, it waits briefly, reconnects, and catches up from the last known place.

The file also controls phone-number opt-in. When someone is proving that a phone number belongs to them, it checks a short code, confirms the address, and sends a contact card so the chat looks like a known contact. After that, normal direct messages or group messages are admitted into UFO conversations.

Attachments are treated carefully. Incoming files are downloaded only up to a size limit and saved into the workspace inbox. Outgoing files are sent through iMessage only if they fit; otherwise the final text can include a link instead. In short, this file is the adapter, gatekeeper, and safety buffer for iMessage traffic.

#### Function details

##### `read_claim`  (lines 63–73)

```
def read_claim(stored: object) -> PendingClaim | None
```

**Purpose**: Reads a stored phone-number claim and turns it into a PendingClaim object if it is valid. If the stored data is missing or malformed, it quietly treats it as no usable claim so one bad row does not block all incoming messages.

**Data flow**: It receives an arbitrary stored value. If the value is empty, it returns nothing. If the value matches the expected claim shape, it returns a PendingClaim; if validation fails, it writes a log note and returns nothing.

**Call relations**: ImessageSurface._prove calls this while checking whether a sender typed the correct opt-in code. It gives _prove a safe yes-or-no answer instead of letting bad stored data crash the message listener.

*Call graph*: called by 1 (_prove); 1 external calls (log).


##### `MessageStreamDisconnected.__init__`  (lines 87–90)

```
def __init__(self, cursor: int | None, error: Exception) -> None
```

**Purpose**: Packages a stream failure together with the last known cursor. This lets the listener know both what went wrong and where it may be able to resume.

**Data flow**: It receives a cursor and an error. It stores both on the exception object and sets the exception message to the original error text.

**Call relations**: ImessageSurface._consume_connected creates this when the provider stream ends or hits an outside/provider error. ImessageSurface.listen catches it and uses the saved cursor to decide how to reconnect.

*Call graph*: called by 1 (_consume_connected).


##### `contact_card`  (lines 103–115)

```
def contact_card(assigned_phone_number: str) -> bytes
```

**Purpose**: Builds a small digital contact card for the assigned iMessage phone number. The user can save it so the chat is recognized as a known contact, which helps remove Apple’s “Report Junk” banner.

**Data flow**: It receives a phone number. It places that number into a vCard text format and returns the finished card as bytes ready to send as an attachment.

**Call relations**: ImessageSurface._send_contact_card calls this after a phone number is successfully connected. The bytes it returns are handed to the message provider as an outgoing attachment.

*Call graph*: called by 1 (_send_contact_card).


##### `claim_key`  (lines 118–119)

```
def claim_key(member_id: UUID, phone_number: str) -> str
```

**Purpose**: Creates the storage key used for a member’s pending claim on a phone number. It hides the raw member-and-phone pair behind a hash, which is a one-way fingerprint.

**Data flow**: It receives a member ID and phone number. It combines them, hashes the result, adds a fixed prefix, and returns the final string key.

**Call relations**: ImessageSurface._prove uses this key to find and later delete the pending opt-in claim stored for that member and sender phone number.

*Call graph*: called by 1 (_prove); 1 external calls (sha256).


##### `queue_key`  (lines 122–123)

```
def queue_key(conversation_id: str, *, direct: bool) -> str
```

**Purpose**: Creates a compact identifier for an iMessage conversation queue. It records both the iMessage conversation ID and whether the chat is direct or group.

**Data flow**: It receives a conversation ID and a direct/group flag. It turns those two pieces into a small JSON string and returns it.

**Call relations**: ImessageSurface._admit_message uses this when asking UFO for the matching internal conversation. Later, post, speak, and attach decode this same key to know where to send replies.

*Call graph*: called by 1 (_admit_message); 1 external calls (dumps).


##### `conversation_from_queue`  (lines 126–134)

```
def conversation_from_queue(queue: str) -> ConversationAddress
```

**Purpose**: Turns a stored queue key back into a usable conversation address. It also rejects queue keys that do not match the expected direct-or-group shape.

**Data flow**: It receives a queue key string. It parses the JSON, checks that it contains a non-empty conversation ID tagged as direct or group, and returns a ConversationAddress. If the format is wrong, it raises an error.

**Call relations**: ImessageSurface.post, ImessageSurface.speak, and ImessageSurface.attach call this before sending anything out to iMessage. It translates UFO’s saved queue key back into the provider’s conversation ID.

*Call graph*: called by 3 (attach, post, speak); 2 external calls (__init__, loads).


##### `_attachment_content`  (lines 137–138)

```
async def _attachment_content(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps attachment bytes in an async stream, which is the format the workspace file writer expects. Think of it as putting a whole file into a one-piece conveyor belt.

**Data flow**: It receives bytes. When iterated, it yields those same bytes once and then ends.

**Call relations**: ImessageSurface._downloaded_files uses this after downloading an incoming attachment. It gives SurfaceContext.write_workspace_file the file content in the streaming shape it requires.

*Call graph*: called by 1 (_downloaded_files).


##### `ImessageSurface.listen`  (lines 145–169)

```
async def listen(self, context: SurfaceListenerContext) -> None
```

**Purpose**: Runs the long-lived iMessage listener. It connects to the provider, resumes from the saved stream position, and keeps reconnecting when provider-side failures happen.

**Data flow**: It receives a listener context that can store cursors and route addresses. It creates or obtains a provider, reads the saved cursor, calls the connected-consumer loop, and on disconnection updates or clears the cursor as needed before trying again.

**Call relations**: This is the top-level listening method for the surface. It calls ImessageSurface._consume_connected for each connected session, logs inactive or disconnected states, and uses the context cursor methods to preserve progress across reconnects.

*Call graph*: calls 3 internal fn (clear_cursor, cursor, _consume_connected); 3 external calls (Event, sleep, log).


##### `ImessageSurface._consume_connected`  (lines 171–216)

```
async def _consume_connected(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> None
```

**Purpose**: Consumes messages while the provider is connected. It starts live listening, catches up missed events first, filters out duplicate or old frames, and sends each new message onward.

**Data flow**: It receives the listener context, provider, installation ID, and optional cursor. It starts a background pump for live frames, optionally replays older missed frames, then processes new frames in order. It updates its cursor as each frame succeeds, or raises a disconnection wrapper when the provider fails.

**Call relations**: ImessageSurface.listen calls this for each connection attempt. It coordinates ImessageSurface._pump_live, ImessageSurface._catch_up, and ImessageSurface._process_event, and reports provider failures back through MessageStreamDisconnected.

*Call graph*: calls 5 internal fn (external_error, _catch_up, _process_event, _pump_live, __init__); called by 1 (listen); 4 external calls (Event, Queue, create_task, gather).


##### `ImessageSurface._pump_live`  (lines 218–236)

```
async def _pump_live(self, provider: MessageProvider, ready: asyncio.Event, frames: asyncio.Queue[LiveFrame | LiveFailure]) -> None
```

**Purpose**: Reads the provider’s live message stream in the background and places each event into a queue. This separates receiving messages from processing them, so the main loop can catch up and then consume live events cleanly.

**Data flow**: It receives a provider, a readiness signal, and a queue. It subscribes to the provider, wraps each incoming provider event in a LiveFrame, and puts it into the queue. If the stream fails or ends, it puts a LiveFailure instead and marks the stream as ready before exiting.

**Call relations**: ImessageSurface._consume_connected starts this as a background task. The queued frames or failures become the input that _consume_connected reads while deciding whether to process a message or reconnect.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (_consume_connected); 4 external calls (__init__, __init__, __init__, set).


##### `ImessageSurface._catch_up`  (lines 238–257)

```
async def _catch_up(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> int
```

**Purpose**: Replays missed provider events after a reconnect or restart. It is the safety net that prevents messages from being lost while the listener was offline.

**Data flow**: It receives a cursor and asks the provider for events after that point. For each real message frame, it sends the event to ImessageSurface._process_event. It tracks the newest stream position and stores that cursor before returning it.

**Call relations**: ImessageSurface._consume_connected calls this before accepting live frames when there is a saved cursor. It hands each catch-up message to ImessageSurface._process_event and records the final position through the listener context.

*Call graph*: calls 3 internal fn (store_cursor, catch_up, _process_event); called by 1 (_consume_connected).


##### `ImessageSurface._process_event`  (lines 259–274)

```
async def _process_event(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, sequence: int, message: InboundMessage | None) -> None
```

**Purpose**: Processes one provider event and records that the stream has advanced. A message from an unclaimed phone number is still counted as seen, but it is not delivered to any workspace.

**Data flow**: It receives a stream sequence number and an optional inbound message. If there is a message, it asks the context whether that sender maps to a workspace; if so, it passes the message to ImessageSurface._admit_message. In every case, it stores the cursor for the sequence afterward.

**Call relations**: Both ImessageSurface._catch_up and ImessageSurface._consume_connected call this for provider frames. It is the narrow bridge from provider events into workspace-specific surface logic.

*Call graph*: calls 3 internal fn (addressed, store_cursor, _admit_message); called by 2 (_catch_up, _consume_connected).


##### `ImessageSurface._admit_message`  (lines 276–320)

```
async def _admit_message(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage) -> None
```

**Purpose**: Decides whether an inbound iMessage should become a UFO conversation turn. It checks phone ownership, opt-in state, group-chat relevance, attachments, and the right audience before admitting the message.

**Data flow**: It receives a workspace surface context, provider, and inbound message. It reads the address claim for the sender. If the sender is still proving ownership, it routes to ImessageSurface._prove. Otherwise it may ignore duplicates or unwanted group chatter, find or create the right UFO conversation, download allowed attachments, wrap the member text, and admit the turn.

**Call relations**: ImessageSurface._process_event calls this only after the sender has been matched to a workspace. It may call ImessageSurface._prove for opt-in, queue_key to identify the conversation, ImessageSurface._downloaded_files for attachments, and finally the context’s admit method to create the UFO turn.

*Call graph*: calls 7 internal fn (address_claim, admit, ambient_reply_wanted, conversation_for, _downloaded_files, _prove, queue_key); called by 1 (_process_event); 7 external calls (__init__, __init__, sha256, conversation_audience, room_audience, fence_member_message, mint_marker).


##### `ImessageSurface._prove`  (lines 322–361)

```
async def _prove(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage, claim: AddressClaim) -> None
```

**Purpose**: Handles the phone-number opt-in proof step. It checks whether the sender’s message contains the expected code, expires old claims, respects opt-out words, and confirms the phone number when the code matches.

**Data flow**: It receives the context, provider, inbound message, and address claim. It ignores messages that cannot prove a direct pending claim. It reads the pending claim from scoped storage, compares the cleaned message text with the stored code, sends helpful texts for expired or wrong codes, and on success sends a confirmation, sends a contact card, confirms the address, and deletes the stored claim.

**Call relations**: ImessageSurface._admit_message calls this whenever an address claim still has an expiration time, meaning the sender is not fully proved yet. It uses claim_key and read_claim to find the pending claim, provider.send_text for user feedback, ImessageSurface._send_contact_card for the vCard, and context methods to confirm or release the address.

*Call graph*: calls 6 internal fn (confirm_address, release_address, send_text, _send_contact_card, claim_key, read_claim); called by 1 (_admit_message); 2 external calls (__init__, now).


##### `ImessageSurface._send_contact_card`  (lines 363–384)

```
async def _send_contact_card(self, provider: MessageProvider, message: InboundMessage, assigned_phone_number: str) -> None
```

**Purpose**: Sends the shared line’s contact card after a successful connection. If the provider refuses this attachment for an outside reason, it logs the problem but does not undo the phone connection.

**Data flow**: It receives a provider, the proving message, and the assigned phone number. It builds contact-card bytes, sends them as an attachment to the same conversation, and logs provider-side attachment failures that should not stop the stream.

**Call relations**: ImessageSurface._prove calls this after the opt-in code is accepted. It calls contact_card to build the attachment and then hands it to the provider’s send_attachment method.

*Call graph*: calls 4 internal fn (error_code, external_error, send_attachment, contact_card); called by 1 (_prove); 1 external calls (log).


##### `ImessageSurface._downloaded_files`  (lines 386–435)

```
async def _downloaded_files(self, ctx: SurfaceContext, provider: MessageProvider, conversation_id: UUID, attachments: tuple[MessageAttachment, ...]) -> str
```

**Purpose**: Downloads incoming iMessage attachments and saves the allowed ones into the workspace inbox. It also produces a human-readable note saying which files were saved, too large, or unavailable.

**Data flow**: It receives the workspace context, provider, UFO conversation ID, and attachment list. For each attachment it chooses a safe unique inbox name, skips files over the size limit, streams downloadable content into memory up to the limit, writes successful files into the workspace, and collects status notes. It returns those notes as text to append to the admitted message.

**Call relations**: ImessageSurface._admit_message calls this before admitting a message that has attachments. It uses the provider to download each attachment, _attachment_content to pass bytes to the workspace writer, and the context to save files.

*Call graph*: calls 4 internal fn (write_workspace_file, download_attachment, external_error, _attachment_content); called by 1 (_admit_message); 1 external calls (inbox_name).


##### `ImessageSurface.post`  (lines 437–444)

```
async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Sends the final UFO turn reply back to the matching iMessage chat. This is used when a turn has finished and there is a terminal response to deliver.

**Data flow**: It receives a surface context and writeback object. It decodes the saved queue key to find the iMessage conversation, builds the final text through ImessageSurface._terminal_text, sends that text through the provider, and returns the provider’s message reference.

**Call relations**: UFO’s surface system calls this when a completed turn needs to be written back. It depends on conversation_from_queue for the destination and ImessageSurface._terminal_text for text that fits iMessage’s plain-message format.

*Call graph*: calls 2 internal fn (_terminal_text, conversation_from_queue).


##### `ImessageSurface.speak`  (lines 446–450)

```
async def speak(self, _ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Sends an intermediate reply before the current UFO turn has fully ended. This lets the assistant say something mid-turn in the iMessage chat.

**Data flow**: It receives a mid-turn reply. It decodes the queue key, sends the reply text to that iMessage conversation through the provider, and returns the provider’s message reference.

**Call relations**: UFO’s surface system calls this for mid-turn messages. It uses conversation_from_queue to turn the saved queue key into the provider conversation ID before sending.

*Call graph*: calls 1 internal fn (conversation_from_queue).


##### `ImessageSurface.attach`  (lines 452–464)

```
async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None
```

**Purpose**: Uploads completed-turn artifacts as iMessage attachments when they fit the size limit. Oversized artifacts are skipped here because the final text can point to them by link instead.

**Data flow**: It receives the surface context, writeback, and an unused reply reference. It decodes the iMessage destination, loops over each artifact, skips files above the limit, reads each allowed artifact’s bytes, and sends it as an attachment through the provider.

**Call relations**: UFO’s surface system calls this after or alongside a final writeback when files should be shared. It uses conversation_from_queue for the destination and ImessageSurface._artifact_bytes to safely load each file before upload.

*Call graph*: calls 2 internal fn (_artifact_bytes, conversation_from_queue).


##### `ImessageSurface._terminal_text`  (lines 466–488)

```
async def _terminal_text(self, ctx: SurfaceContext, writeback: Writeback, *, direct: bool) -> str
```

**Purpose**: Builds the plain text that should be sent for a completed UFO turn. It combines the answer, any question prompts, connection instructions, credential instructions, and links for files too large to send through iMessage.

**Data flow**: It receives a context, writeback, and whether the chat is direct. It starts with the terminal response text and question text, adds a direct connect URL or direct-message instruction when account connection is requested, adds a home/member-portal hint for credential requests, and adds links or names for oversized artifacts. It returns the joined message, or a status fallback if there is no text.

**Call relations**: ImessageSurface.post calls this before sending the final iMessage. It calls ImessageSurface._question_text for question formatting and uses context URL helpers when the reply requires the user to continue elsewhere.

*Call graph*: calls 4 internal fn (artifact_link, connect_url, home_url, _question_text); called by 1 (post).


##### `ImessageSurface._question_text`  (lines 490–501)

```
def _question_text(self, writeback: Writeback) -> str
```

**Purpose**: Formats any question included in a terminal writeback into readable plain text. This makes structured prompts understandable inside a simple iMessage bubble.

**Data flow**: It receives the writeback. If there is no question, it returns an empty string. If there is one, it writes the title, each question, any answer options, and any current chosen answer into a newline-separated text block.

**Call relations**: ImessageSurface._terminal_text calls this while building the final outgoing text. It converts UFO’s structured question data into something iMessage can display without special UI.

*Call graph*: called by 1 (_terminal_text).


##### `ImessageSurface._artifact_bytes`  (lines 503–513)

```
async def _artifact_bytes(self, ctx: SurfaceContext, blob_key: str, size_bytes: int) -> bytes
```

**Purpose**: Reads an outgoing artifact from blob storage and verifies it is safe to send as an iMessage attachment. It protects the provider from files that are too large or whose size changed while being read.

**Data flow**: It receives a context, blob key, and expected size. It rejects the file immediately if the expected size is over the iMessage limit, streams the blob into memory while checking the limit, verifies the final byte count matches the expected size, and returns the bytes.

**Call relations**: ImessageSurface.attach calls this for each artifact it plans to upload. The returned bytes are passed directly to the provider’s send_attachment method.

*Call graph*: called by 1 (attach).


### Slack Message Surface
Slack helpers normalize attribution and mentions before the main surface ingests workspace activity and delivers replies.

### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `outbound Slack connector sends and inbound Slack message checks`

When this system sends a Slack message through a connector, the message is not rendered by the normal Slack-facing code. That means the usual place for adding a clear “this came from UFO” marker is skipped. This file fills that gap by adding an attribution footer to connector-sent Slack messages, but with the workspace’s actual bot user mentioned instead of just writing the product name. In Slack terms, a mention like `<@U123>` becomes a clickable reference to the bot.

The file has two matching halves. The outbound half decides whether a connector call is really sending a Slack message, then adds the bot-mention footer in the same shape used by the generic connector attribution system. It avoids adding a second footer if one is already present, like a mailroom stamp that should appear once, not every time the envelope is handled.

The inbound half protects the bot from confusing its own footer with a user’s request. Slack may report a message as mentioning the bot because the footer contains the bot mention. This file strips known attribution text before checking whether the message still mentions the bot. It also looks through Slack message blocks, not just the plain text field, because Slack messages can store visible text in nested block structures.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: This function answers a narrow question: is this connector call one that publishes a Slack message? It keeps the Slack extension in step with the connector tool’s own rules for deciding which calls should receive an attribution footer.

**Data flow**: It receives a connector provider name and a connector action slug. It lowercases the slug, then checks that the provider is Slack, that the slug looks like it is about messages, and that it includes a send-like verb. It returns true only when all of those clues match.

**Call relations**: This is the gatekeeper used before Slack attribution should be applied. By using the same provider and slug pattern as the connector attribution logic, it prevents one part of the system from adding a footer while another part thinks the call is unrelated.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: This function adds the bot-mention attribution footer to the arguments for a Slack send. Someone would use it right before dispatching a connector-sent Slack message, so readers can click through to the bot from the message footer.

**Data flow**: It receives the message arguments and the Slack bot user ID. It formats the standard attribution subject so it mentions that bot user, then passes the original arguments and that subject to the shared connector attribution helper. The result is a new set of arguments with the footer added, unless a suitable attribution is already there.

**Call relations**: This function is the Slack-specific wrapper around the shared connector attribution machinery. It asks `UFO_ATTRIBUTION_MENTION_SUBJECT.format` to build the right mention text, then hands everything to `attributed_arguments`, which does the actual message-shaping and duplicate-footer avoidance.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: This function checks whether a piece of Slack text truly mentions the bot, ignoring mentions that only appear inside this system’s own attribution footer. It helps stop the bot from treating its own sent messages as if a person had addressed it.

**Data flow**: It receives some Slack text and the bot user ID. First it removes known attribution footer text from the message. Then it looks for the Slack mention form for that bot user, such as `<@bot_user_id>`. It returns true if the mention remains after the footer is stripped away.

**Call relations**: This is used on the inbound side, when Slack text is being judged as addressed to the bot or not. It relies on the shared `attribution_stripped` helper so the same footer shape that was added on send is also recognized and ignored on receive.

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: This function collects every text-bearing part of a Slack message event where a bot mention might be hidden. It matters because Slack messages can keep visible text inside nested blocks, not only in the top-level `text` field.

**Data flow**: It receives a Slack event represented like a dictionary. It reads the top-level text field, then looks at the message blocks field and pulls out every nested string inside it. It returns all of those text snippets as a tuple, starting with the top-level text.

**Call relations**: This function prepares inbound message content for mention checks such as `addressing_mention`. To find text inside Slack’s nested block structure, it delegates the recursive searching work to `_nested_strings`.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: This helper walks through a nested Slack data structure and yields every string it can find. It is used because Slack block content can be buried inside dictionaries and lists several layers deep.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, it searches through each stored value. If it is a list, it searches through each item. Other kinds of values are ignored.

**Call relations**: This is the small search tool behind `message_bodies`. `message_bodies` gives it the Slack blocks from an event, and `_nested_strings` returns the scattered text pieces so the rest of the Slack extension can make an accurate mention decision.

*Call graph*: called by 1 (message_bodies).


### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message ingest and outbound reply sending`

Slack does not send every message exactly as a person sees it. A user mention may arrive as something like <@U123>, a channel as <#C456|general>, and a link as <https://example.com|docs>. Those forms are useful for Slack’s computers, but they are hard for people and language models to understand. This file is the translator at the boundary.

On the way in, render_markup rewrites Slack entities into readable text such as @Alex or #general, using a caller-provided map of Slack IDs to names. If a name cannot be safely found, it leaves the original code alone rather than guessing. Links are shown with both their label and URL when needed, so a reader can see the words and still know where the link points.

On the way out, mention_markup does a narrower job. If the agent writes @Alex, it can turn that back into <@U123> so Slack actually notifies Alex. It only does this for names the caller explicitly allows, skips code blocks and URLs, avoids ambiguous names, and limits how many mentions it will convert. Like a careful receptionist, it will only ring people it can identify with confidence.

The file also keeps Slack’s HTML-style escapes separate. Turning &lt; back into < is only safe for the original speaker’s own message, not for quoted bystanders.

#### Function details

##### `mentioned_users`  (lines 67–70)

```
def mentioned_users(text: str) -> frozenset[str]
```

*Call graph*: calls 1 internal fn (_mentioned).


##### `mentioned_channels`  (lines 73–75)

```
def mentioned_channels(text: str) -> frozenset[str]
```

*Call graph*: calls 1 internal fn (_mentioned).


##### `_mentioned`  (lines 78–83)

```
def _mentioned(text: str, kind: str) -> frozenset[str]
```

*Call graph*: called by 2 (mentioned_channels, mentioned_users).


##### `render_markup`  (lines 86–96)

```
def render_markup(text: str, names: Mapping[str, str]) -> str
```


##### `unescape`  (lines 99–108)

```
def unescape(text: str) -> str
```


##### `mention_key`  (lines 111–115)

```
def mention_key(name: str) -> str
```

*Call graph*: called by 2 (_mention_at, mention_index).


##### `mention_index`  (lines 118–132)

```
def mention_index(names: Mapping[str, str]) -> dict[str, str]
```

*Call graph*: calls 1 internal fn (mention_key).


##### `mention_markup`  (lines 135–166)

```
def mention_markup(text: str, ids: Mapping[str, str], limit: int=MENTION_MARKUP_MAX) -> str
```

*Call graph*: calls 1 internal fn (_mention_at); 1 external calls (finditer).


##### `_mention_at`  (lines 169–182)

```
def _mention_at(text: str, start: int, ids: Mapping[str, str]) -> tuple[int, str] | None
```

*Call graph*: calls 1 internal fn (mention_key); called by 1 (mention_markup); 2 external calls (islice, finditer).


##### `_entity`  (lines 185–201)

```
def _entity(match: re.Match[str], names: Mapping[str, str]) -> str
```


### `extensions/slack/ufo_ext_slack/surface.py`

`io_transport` · `startup, install, request handling, turn execution, delivery`

This file is the bridge between Slack and the core ufo system. Without it, Slack could not safely identify which workspace a request belongs to, decide whether a message is meant for the agent, save attached files, admit a turn, or post the finished answer back into the right Slack thread.

The file does several jobs. First, it verifies Slack requests using Slack's signing secret, which is like checking the wax seal on a letter before trusting it. It supports two install paths: OAuth, where Slack grants a bot token through an Add to Slack flow, and a manual app setup where the workspace owner supplies the token and secret.

For incoming messages, it works out whether the agent was addressed in a direct message or by mention, maps the Slack channel/thread to a ufo conversation, resolves the Slack user to a ufo member when possible, downloads any Slack files into the workspace, and gathers nearby Slack chatter as background context. Unmentioned replies in an existing thread are only admitted when they are part of the agent's conversation or when a separate ambient-reply decision says the agent should answer.

For outgoing work, it posts terminal replies, mid-turn replies, question forms, connection buttons, progress messages, live Slack status text, and uploaded artifacts. It also keeps careful delivery checkpoints so retries do not duplicate messages.

#### Function details

##### `_env_signing_secret`  (lines 228–232)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from the process environment. This is the fallback secret used when a workspace has not stored its own Slack signing secret.

**Data flow**: It reads the environment variable for Slack's signing secret. If it is present and non-empty, it returns the string; otherwise it returns nothing.

**Call relations**: Workspace-specific secret lookup calls this when no stored secret is available, both during normal request handling and during pre-routing workspace resolution.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 235–242)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret for a request after the workspace is already known. It prefers the workspace's private credential slot, then falls back to the deploy-wide environment secret.

**Data flow**: It receives a surface context, asks it for the Slack signing-secret credential, and returns that value. If the slot is unset, it returns the environment secret instead.

**Call relations**: The Slack event route and interactive-button route call this before verifying the request body.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 245–253)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret for a workspace before a request has been fully bound to that workspace. This lets the system verify a Slack request before trusting it.

**Data flow**: It receives shared authentication access and a workspace id. It tries to read that workspace's Slack signing-secret credential, falls back to the environment secret, and returns nothing if the workspace is unknown.

**Call relations**: Workspace resolution uses this after extracting a Slack team hint, so it can verify the raw request before binding the request to a tenant.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 256–260)

```
def slack_client_id() -> str
```

**Purpose**: Returns the Slack OAuth client id for this deployment. It fails loudly if OAuth is configured incorrectly.

**Data flow**: It reads the client id environment variable. A present value comes out; a missing value raises an error.

**Call relations**: The OAuth token exchange uses this when trading Slack's temporary code for a bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 263–267)

```
def slack_client_secret() -> str
```

**Purpose**: Returns the Slack OAuth client secret for this deployment. It protects the install flow from silently continuing with missing configuration.

**Data flow**: It reads the client secret environment variable. A present value comes out; a missing value raises an error.

**Call relations**: The OAuth exchange calls this alongside the client id when proving this app's identity to Slack.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 270–272)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should redirect to after an OAuth install. This keeps the authorize step and callback exchange using the exact same URL.

**Data flow**: It takes the public base URL of the deployment, trims any trailing slash, appends the Slack surface OAuth path, and returns the full URL.

**Call relations**: The OAuth callback uses it before exchanging Slack's authorization code.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 275–287)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the Add to Slack link shown to an owner. The link asks Slack for the needed bot permissions and carries sealed state tying the install to one workspace.

**Data flow**: It takes a client id, redirect URL, and state token, encodes them with the required Slack scopes, and returns a Slack authorization URL.

**Call relations**: This is used by the install/connect path that sends a workspace owner to Slack to approve the app.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 291–293)

```
def __init__(self, error: str)
```

**Purpose**: Creates an error that carries Slack identity-proving failure text. It makes failures from Slack identity checks explicit and readable.

**Data flow**: It receives an error string, stores it on the exception, and initializes the normal runtime error text with the same value.

**Call relations**: Identity proof and OAuth exchange raise this when Slack returns bad, incomplete, or malformed identity information.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `bot_token_fingerprint`  (lines 310–311)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack bot token. The fingerprint lets the code tell whether stored identity data belongs to the current token without storing or comparing the secret token itself.

**Data flow**: It receives a bot token string, hashes it with SHA-256, and returns the hexadecimal hash.

**Call relations**: Identity reads, manual identity proof, and OAuth install all use this to tie a team/bot identity record to the token that proved it.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 314–326)

```
async def read_identity(blob: BlobStore, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the saved Slack workspace identity if it still matches the current bot token. It refuses stale identity data after a token changes.

**Data flow**: It checks the blob store for the identity record, parses it, compares its token fingerprint with the current bot token's fingerprint, and returns the identity or nothing.

**Call relations**: Both install validation and request handling call this before trusting team and bot-user ids.

*Call graph*: calls 3 internal fn (exists, get, bot_token_fingerprint); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 329–335)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Returns the Slack bot user id for this workspace when the bot token and identity record are available. Other extension code can use this to recognize the bot itself.

**Data flow**: It reads the bot token credential, loads the matching identity record, and returns the stored bot user id or nothing.

**Call relations**: This is an identity helper used by code that needs to know which Slack user id represents the app.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_bot_token`  (lines 338–345)

```
async def _bot_token(ctx: SurfaceContext) -> str | None
```

**Purpose**: Reads the workspace's Slack bot token if it has been installed. Missing tokens are treated as an incomplete install rather than a crash.

**Data flow**: It asks the surface context for the bot-token credential. If the slot is unset, it returns nothing.

**Call relations**: Inbound event and interactive routes call this before making any Slack API calls.

*Call graph*: calls 1 internal fn (credential); called by 2 (ingest, interactive).


##### `_identity`  (lines 348–352)

```
async def _identity(ctx: SurfaceContext, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Loads the Slack identity for the current token and mirrors the bot user id into the extension store. This keeps hook-time code able to find the bot id even when it cannot read blobs.

**Data flow**: It reads the identity from the blob store. If found, it writes the bot user id to the scoped store and returns the identity.

**Call relations**: Request handlers and mention-mapping code call this before trusting Slack team or bot-user information.

*Call graph*: calls 2 internal fn (_mirror_self_user_id, read_identity); called by 3 (_reply_mention_ids, ingest, interactive).


##### `_mirror_self_user_id`  (lines 358–374)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the verified Slack bot user id into a lightweight scoped store. This is best-effort because a missing footer helper should not break Slack event handling.

**Data flow**: It receives a workspace id and bot user id, skips the write if this process already wrote the same value, otherwise stores it and updates an in-memory cache.

**Call relations**: Identity loading and OAuth install call this after proving which Slack user the bot is.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 387–393)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Gets or proves the Slack identity for a manually configured Slack app. It avoids repeated network proof when a valid identity is already stored.

**Data flow**: It first tries to read an existing identity. If absent, it calls Slack to prove the token, writes the result to the blob store, and returns it.

**Call relations**: Background identity repair and manual Slack connect use this resolver to establish team and bot-user ids.

*Call graph*: calls 2 internal fn (_prove, read_identity).


##### `SlackIdentityResolver._prove`  (lines 395–420)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Asks Slack's auth.test API what workspace and bot user a bot token belongs to. This proves that a pasted token is real and tells ufo how to route future events.

**Data flow**: It sends the bot token to Slack, checks the JSON response, validates the team and user id shapes, and returns a SlackIdentity with the token fingerprint.

**Call relations**: The resolver calls this only when there is no valid cached identity.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 426–440)

```
def _prove_identity_in_background(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Starts a one-per-workspace background task to prove identity after an event arrives for a partially configured manual install. This lets Slack retry later instead of blocking the current request.

**Data flow**: It checks whether a proof task already exists for the workspace. If not, it starts one and registers cleanup when it finishes.

**Call relations**: The identity-unavailable response uses this when there is a bot token but no usable identity record.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 1 (_identity_unavailable); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 436–438)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished identity-proof task from the in-memory task table. This prevents old task entries from blocking future retries.

**Data flow**: It receives the completed task, checks that it is still the tracked task for the workspace, and deletes it if so.

**Call relations**: It is attached as the completion callback for the background identity-proof task.


##### `_run_identity_proof`  (lines 443–449)

```
async def _run_identity_proof(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Runs the actual background identity proof and logs failures. It keeps failed proof attempts from crashing request handling.

**Data flow**: It receives a context and bot token, creates a resolver, asks it to resolve, and logs any expected or unexpected error.

**Call relations**: Background proof tasks created by _prove_identity_in_background run this.

*Call graph*: called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `_identity_unavailable`  (lines 457–485)

```
def _identity_unavailable(ctx: SurfaceContext, bot_token: str | None) -> Response
```

**Purpose**: Builds the HTTP response for a verified Slack request when the workspace cannot yet identify its Slack app. It either starts repair work or stops wasteful Slack retries when repair is impossible.

**Data flow**: If no bot token exists, it logs the incomplete install once and returns a no-retry service error. If a token exists, it starts background identity proof and returns a retryable service error.

**Call relations**: The event and interactive routes call this after token or identity lookup fails.

*Call graph*: calls 1 internal fn (_prove_identity_in_background); called by 2 (ingest, interactive); 2 external calls (Response, warn).


##### `signing_secret_fingerprint`  (lines 491–494)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a safe fingerprint of a Slack signing secret. This lets the code remember which secret successfully verified a URL without storing the secret itself.

**Data flow**: It receives a signing secret, hashes it with SHA-256, and returns the hexadecimal hash.

**Call relations**: URL verification marking uses this to tell current verification apart from a stale marker after a secret rotation.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (sha256).


##### `slack_oauth_exchange`  (lines 514–547)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack's short-lived OAuth code for the workspace bot token and identity details. This is the point where an approved install becomes usable credentials.

**Data flow**: It sends the code, redirect URL, client id, and client secret to Slack. It validates the returned token, team id, bot user id, and optional app id, then returns a SlackInstall.

**Call relations**: The OAuth callback calls this after validating the sealed install state.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `slack_app_dm_url`  (lines 550–555)

```
def slack_app_dm_url(app_id: str, team_id: str) -> str
```

**Purpose**: Builds a browser link that opens the installed app's direct message in Slack. This gives the owner an easy next step after installation.

**Data flow**: It receives the Slack app id and team id, encodes them into Slack's app_redirect URL, and returns the URL.

**Call relations**: The OAuth callback uses it for the success page when Slack supplied an app id.

*Call graph*: called by 1 (oauth_callback); 1 external calls (urlencode).


##### `SlackConversationSearch.run`  (lines 642–656)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations by channel details or DM participants. It gives the agent a bounded way to find a Slack destination by a human query.

**Data flow**: It normalizes the query, lists conversations, resolves people for DMs, converts raw Slack records into small conversation objects, filters by text match, and returns matches plus a truncation flag.

**Call relations**: It coordinates the private listing, people-resolution, and conversion helpers inside SlackConversationSearch.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 658–677)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches a bounded number of Slack conversation-list pages. It avoids scanning forever in huge workspaces.

**Data flow**: It repeatedly calls Slack with page parameters, collects channel records, follows cursors up to the page limit, and returns the records plus whether more pages remained.

**Call relations**: The search runner calls this before converting or filtering conversations.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 679–687)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversations.list call. It centralizes the search's page size, conversation types, and cursor handling.

**Data flow**: It receives a cursor string and returns a parameter dictionary, including the cursor only when non-empty.

**Call relations**: The conversation-list loop calls this for every Slack page request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 689–692)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack's next-page cursor from a response. A missing or malformed cursor means the listing is finished.

**Data flow**: It reads response_metadata.next_cursor from the payload and returns it if it is a string, otherwise an empty string.

**Call relations**: The list loop uses this after each Slack page to decide whether to keep paging.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 694–722)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves who is in DMs and group DMs so they can be searched by person. It caps the number of DMs it expands.

**Data flow**: It scans listed conversations, collects member ids for DMs, looks up user labels once per user, drops the bot itself, and returns labels by conversation plus whether the cap was hit.

**Call relations**: The search runner calls this after listing conversations and before building searchable SlackConversation objects.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 724–731)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as a public channel, private channel, group DM, or one-to-one DM.

**Data flow**: It reads Slack boolean flags from the raw record and returns a small kind string.

**Call relations**: Member lookup and conversation conversion use this to choose the right behavior.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 733–747)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets member ids for a DM or group DM. A one-to-one DM can read the user directly, while group DMs need a Slack API call.

**Data flow**: It receives the raw conversation and id. It returns the direct user for an IM or asks Slack for members of a group DM.

**Call relations**: People resolution calls this for each DM-like conversation it decides to expand.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 749–754)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Creates a readable search label for a Slack user. It prefers name plus email when both are known.

**Data flow**: It receives a SlackUser or nothing and the raw user id, then returns name/email/id text.

**Call relations**: People resolution uses this when building the searchable participant list for DMs.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 756–773)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Turns one raw Slack conversation record into the smaller model used by search results. Bad or incomplete records are ignored.

**Data flow**: It checks that the raw item is a dict with an id, reads name, people, purpose, topic, membership, and kind, then returns a SlackConversation.

**Call relations**: The search runner calls this for every listed record before applying the query filter.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 775–777)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Reads Slack's nested purpose or topic text field safely. Missing or malformed values become an empty string.

**Data flow**: It receives a field object, checks for a dict with a string value, and returns that value or an empty string.

**Call relations**: Conversation conversion uses this for the purpose and topic fields.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 952–967)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a Slack request is recent and genuinely signed by Slack. This is the main guard against forged requests.

**Data flow**: It reads timestamp and signature headers, rejects missing, invalid, or stale timestamps, computes the expected HMAC signature from the body and secret, and raises on mismatch.

**Call relations**: Workspace resolution, event ingest, and interactive ingest call this before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 974–993)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw request body while enforcing a size limit. The raw bytes are needed exactly as Slack signed them.

**Data flow**: It checks request state for a cached body or overflow marker. If missing, it streams chunks, totals their size, rejects oversized bodies, stores the bytes, and returns them.

**Call relations**: All Slack routes and workspace resolution use this before parsing or verifying Slack payloads.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 996–1005)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Extracts Slack's URL-verification challenge from a setup request. This lets Slack confirm the endpoint is reachable.

**Data flow**: It parses the body as JSON, checks for a url_verification type, and returns the challenge string or nothing.

**Call relations**: Workspace resolution and event ingest use this to answer Slack setup probes without treating them as real events.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 1008–1025)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Finds the Slack team id inside an untrusted event or interactive payload. It is only a hint until the signature is verified.

**Data flow**: It tries to parse JSON, then form-encoded interactive payload JSON, extracts team_id or team.id, validates its shape, and returns it or nothing.

**Call relations**: Workspace resolution uses this hint to choose which workspace's signing secret should verify the request.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 1028–1029)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Turns a Slack team id into the installation key used by ufo. This is how Slack workspaces are bound to ufo workspaces.

**Data flow**: It receives a team id and prefixes it with team:.

**Call relations**: OAuth install writes this binding, and request resolution reads it.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 1032–1070)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Determines which ufo workspace a Slack request belongs to, before the normal route context exists. It verifies signed Slack POSTs and accepts sealed OAuth callback state for GETs.

**Data flow**: For GET requests it opens sealed install state and returns its workspace. For POST requests it reads the raw body, handles URL verification, extracts a team id, finds the bound workspace, verifies the signature, and returns the workspace or a response.

**Call relations**: The surface routing layer calls this before dispatching to Slack event, interaction, or OAuth handlers.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 1073–1076)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether sealed credential state belongs to the Slack bot-token install flow. It prevents unrelated sealed state from authorizing a Slack install.

**Data flow**: It receives credential-state claims and returns true only when the payload marker and requested slot match Slack install expectations.

**Call relations**: OAuth callback and workspace resolution use this when interpreting browser callback state.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 1079–1084)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the ufo conversation key for a Slack message. Channel conversations are keyed by channel plus thread root, while DMs are keyed by the DM channel.

**Data flow**: It receives a Slack channel id, root timestamp, and DM flag. It returns just the channel for DMs or channel:root for channel threads.

**Call relations**: Inbound event conversion and interactive form conversion use this to find the right ufo conversation.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `slack_message_addressed`  (lines 1087–1105)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message is directly asking the agent to act. DMs always count; channels count when they mention the bot, except for attribution footer noise.

**Data flow**: It reads all message bodies, checks for real bot mentions, treats certain app_mention events as addressed, and returns a boolean.

**Call relations**: Inbound conversion uses this to choose between immediate admission, ambient decision, or ignoring the message.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1108–1111)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts Markdown and bare links in reply text. This helps decide whether Slack link previews should be disabled.

**Data flow**: It finds Markdown-style links, removes them, counts remaining bare URLs, and returns the total.

**Call relations**: Slack reply body construction uses this before posting messages.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `slack_reply_parts`  (lines 1114–1191)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long Slack replies into chunks that fit Slack limits while trying not to break code blocks or tables. This keeps large answers readable.

**Data flow**: It receives text and a size limit, identifies protected spans, cuts at paragraph, line, sentence, or word boundaries where possible, and returns ordered text parts.

**Call relations**: Terminal replies, mid-turn replies, and block-body construction use this before sending text to Slack.

*Call graph*: called by 3 (post, slack_reply_body, speak); 2 external calls (finditer, match).


##### `slack_reply_body`  (lines 1194–1261)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for a Slack chat.postMessage call. It can include text blocks, question or connect actions, metadata for deduplication, and a footer.

**Data flow**: It receives channel, thread, text, optional metadata, delivery id, block options, and actions. It builds a bounded Slack message body, disables excessive link previews, falls back to plain text if blocks are too large, and returns bytes.

**Call relations**: Reply posting, mid-turn speaking, and progress messages call this before sending to Slack.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 3 (_say, post, speak); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1264–1265)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a simple Slack Block Kit section containing Markdown text. It trims text to Slack's section limit.

**Data flow**: It receives text and returns a section block dictionary with mrkdwn text.

**Call relations**: Question rendering uses this for titles and prose fallback.

*Call graph*: called by 2 (_ask_prose, slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1268–1312)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders an agent question as Slack form blocks when possible. If Slack cannot represent the question safely, it renders readable prose instead.

**Data flow**: It receives an optional ask object. It builds a title, one control per question, and a submit button, or prose sections plus a reply-in-thread hint.

**Call relations**: Terminal reply posting appends these blocks when a turn ends by asking the user for input.

*Call graph*: calls 3 internal fn (_ask_control, _ask_prose, _mrkdwn_section); called by 1 (post).


##### `_ask_control`  (lines 1315–1367)

```
def _ask_control(index: int, ask: AskQuestion) -> dict[str, object] | None
```

**Purpose**: Builds one Slack input control for one question. It chooses text input, radio buttons, or checkboxes based on the question shape.

**Data flow**: It receives the question index and question data, rejects unsupported attachments or too-wide options, builds the matching Slack input block, and returns it or nothing.

**Call relations**: Ask block rendering calls this for each question.

*Call graph*: calls 1 internal fn (_ask_option); called by 1 (slack_ask_blocks).


##### `_ask_option`  (lines 1370–1380)

```
def _ask_option(option: QuestionOption) -> dict[str, object]
```

**Purpose**: Turns one answer option into Slack's option format. It includes the label and optional description.

**Data flow**: It receives a QuestionOption and returns a Slack option dictionary, trimming the description to Slack's limit.

**Call relations**: Question controls use this when building radio button and checkbox choices.

*Call graph*: called by 1 (_ask_control).


##### `_ask_prose`  (lines 1383–1391)

```
def _ask_prose(ask: AskQuestion) -> dict[str, object]
```

**Purpose**: Renders a question as plain readable Slack Markdown. This is used when Slack controls cannot express the question.

**Data flow**: It receives a question, writes the header, prompt, options, and multi-select note into lines, and wraps them in a Markdown section block.

**Call relations**: Ask block rendering uses this fallback when any question cannot become a form control.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (slack_ask_blocks).


##### `slack_connect_blocks`  (lines 1394–1420)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Builds a Slack button for a connection request, such as connecting another provider account. It lets the user continue sensitive authorization outside chat.

**Data flow**: It receives an optional connect request and turn id. If no request exists, it returns nothing; otherwise it returns an action block with a button carrying the turn id.

**Call relations**: Terminal reply posting adds these blocks beside the reply when the turn asks for a provider connection.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1423–1427)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required string field from a Slack payload. It gives a clear error when expected Slack data is missing.

**Data flow**: It receives a mapping and field name, checks that the value is a non-empty string, and returns it or raises.

**Call relations**: Inbound and interaction conversion use it for required Slack ids and timestamps.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `_inbound_files`  (lines 1430–1442)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts usable file attachments from a Slack message event. It skips hidden or tombstoned files and limits how many files one message can bring in.

**Data flow**: It reads the event's files array, keeps up to the configured maximum with a name and private download URL, and returns InboundFile records.

**Call relations**: Inbound conversion and attachment backfill use this before downloading files into the workspace.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1445–1476)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Looks up file attachments that Slack did not include directly in an app_mention event. It fetches the exact message from Slack and extracts its files.

**Data flow**: It receives token, channel, message timestamp, and optional thread root, fetches the bounded message range, finds the matching timestamp, and returns inbound files or none.

**Call relations**: Inbound conversion calls this for app_mention events that may have omitted file details.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1484–1559)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes the Add to Slack installation flow. It validates the sealed install state, exchanges Slack's code for a bot token, binds the Slack team, stores credentials, and shows a result page.

**Data flow**: It reads query parameters, handles user cancellation and bad state, builds the redirect URI, exchanges the code, binds the team to the workspace, stores the bot token and identity, mirrors the bot user id, and returns a callback page.

**Call relations**: Slack redirects the owner's browser here after OAuth authorization.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, slack_app_dm_url, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 3 external calls (__init__, __init__, callback_page).


##### `_mark_url_verified`  (lines 1565–1579)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached and signed a request to this deployment. Manual app setup can use this as proof that the Events URL works.

**Data flow**: It fingerprints the signing secret, skips duplicate writes in this process, stores a marker with the fingerprint and time, and remembers it in memory.

**Call relations**: Event and interactive routes call this after a request verifies successfully.

*Call graph*: calls 1 internal fn (signing_secret_fingerprint); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `ingest`  (lines 1582–1626)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Events API requests. It verifies the request, filters irrelevant messages, and starts or admits ufo turns from Slack messages.

**Data flow**: It reads the raw body, checks size and signature, answers setup challenges, loads bot token and identity, converts the payload to an inbound message, then either admits it, checks if it folds into a live turn, or starts an ambient decision task.

**Call relations**: This is the main event route Slack calls for messages and app mentions.

*Call graph*: calls 12 internal fn (_admit_inbound, _bot_token, _ctx_signing_secret, _decide_ambient_in_background, _folds_into_live_turn, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_inbound (+2 more)); 3 external calls (loads, JSONResponse, Response).


##### `_folds_into_live_turn`  (lines 1629–1668)

```
async def _folds_into_live_turn(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> bool
```

**Purpose**: Checks whether an unmentioned thread reply should be absorbed by an already running turn. This prevents a useful correction or stop message from being discarded by the ambient classifier.

**Data flow**: It receives inbound message data, checks for an existing conversation and absorbing turn, resolves the sender, checks seat permission, logs the skip, and returns true only when admission would actually fold the reply into the live turn.

**Call relations**: The event route calls this before sending an unaddressed reply to the ambient-reply decision.

*Call graph*: calls 4 internal fn (absorbing_turn, transaction, _resolve_member, _slack_user); called by 1 (ingest); 2 external calls (__init__, log).


##### `_admit_inbound`  (lines 1671–1721)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Turns a verified Slack message into a ufo conversation turn. It also saves files, adds context, links the thread, and starts live feedback when a run opens.

**Data flow**: It gathers sender info, ambient context, permalink, and mention names; resolves the member and audience; gets or creates the conversation; mirrors the Slack thread; downloads files; wraps the member text safely; admits the turn; anchors DM replies; and arms followers if a run started.

**Call relations**: The event route and ambient-decision task call this when a Slack message should become agent work.

*Call graph*: calls 13 internal fn (admit, conversation_for, retitle_conversation, _ambient_context, _anchor_dm_thread, _arm_followers, _download_files, _mirror_thread, _resolve_member, _slack_permalink (+3 more)); called by 2 (_run_ambient_decision, ingest); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1727–1752)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts a background decision for an unmentioned thread reply. This keeps Slack's required quick acknowledgement from waiting on model judgment.

**Data flow**: It uses the inbound message id as a task key, skips if a task already exists, creates a task, stores it strongly, and removes it when done.

**Call relations**: The event route calls this after acknowledging Slack when a message might or might not deserve an agent reply.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1748–1750)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Forgets a finished ambient-decision task. This keeps the in-memory task table from growing forever.

**Data flow**: It receives the completed task, checks it is still the tracked task for that message id, and deletes the entry.

**Call relations**: It is attached as a completion callback when an ambient decision task is created.


##### `_run_ambient_decision`  (lines 1755–1770)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the ambient-reply decision and admits the message if the agent should answer. It logs failures because Slack has already been acknowledged.

**Data flow**: It asks whether a reply is wanted. If yes, it admits the inbound message; if anything fails, it logs the dropped message details.

**Call relations**: Background ambient tasks execute this after the event route has returned success to Slack.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1773–1780)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages from users outside the installed Slack workspace in shared channels. Those users are not served by this workspace's app.

**Data flow**: It compares source/user team fields with the installed team id and returns true when they differ.

**Call relations**: Inbound conversion uses this early to ignore external Slack Connect bystanders.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1796–1832)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines who should be allowed to see a Slack conversation in ufo based on the channel kind. Public channels, private rooms, shared channels, and DMs get different audiences.

**Data flow**: It reads channel type and payload flags, sometimes fetches Slack channel info, builds an audience and optional label, and raises when it cannot decide safely.

**Call relations**: Inbound conversion calls this before admitting a channel message.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1835–1885)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Converts a raw Slack event payload into the smaller Inbound object ufo admission needs. It filters bots, unsupported subtypes, foreign authors, and messages not meant for the agent.

**Data flow**: It validates the event, checks sender and addressing, computes the thread key, finds existing participation, gathers audience and files, and returns an Inbound record or nothing.

**Call relations**: The event route calls this after verification and identity loading.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1888–1899)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Checks whether a Slack thread already has a real ufo turn. A bare conversation row is not enough to count as participation.

**Data flow**: It finds the conversation by queue key, then checks whether it has a latest turn. It returns the conversation id only when both exist.

**Call relations**: Inbound conversion uses this to decide whether unaddressed thread replies may belong to an existing agent conversation.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1902–1934)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Fetches readable Slack user information such as name, confirmed email, timezone, and team id. It is best-effort so a slow Slack lookup does not break normal ingest.

**Data flow**: It calls users.info with a short timeout, validates the response, keeps email only if Slack says it is confirmed, and returns a SlackUser or nothing.

**Call relations**: Admission, member resolution, ambient folding, interaction handling, and name caching use this.

*Call graph*: calls 1 internal fn (_slack_ok); called by 5 (_people, _name, _admit_inbound, _folds_into_live_turn, interactive); 2 external calls (__init__, AsyncClient).


##### `_conversation_members`  (lines 1948–1968)

```
async def _conversation_members(bot_token: str, channel: str) -> tuple[str, ...]
```

**Purpose**: Reads a bounded roster of Slack conversation members. It is used to safely map outbound @names only to people already in the conversation.

**Data flow**: It calls Slack's conversations.members endpoint with a limit, returns member ids on success, and returns an empty tuple on failure.

**Call relations**: SlackNames.mention_ids calls this before resolving mentionable names.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (mention_ids); 1 external calls (AsyncClient).


##### `SlackNames.of`  (lines 1989–1997)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack ids found in message text into readable names. This makes inbound and ambient context understandable instead of showing raw ids.

**Data flow**: It scans texts for mentioned users and channels, adds explicitly supplied user ids, resolves them through cache and Slack, and returns id-to-name mappings.

**Call relations**: Admission and ambient digest preparation use this before rendering Slack markup.

*Call graph*: calls 1 internal fn (_resolved); 2 external calls (mentioned_channels, mentioned_users).


##### `SlackNames.mention_ids`  (lines 1999–2013)

```
async def mention_ids(self, channel: str, identity: SlackIdentity) -> dict[str, str]
```

**Purpose**: Builds a safe map from human @names in an agent reply to Slack user mention ids. It only considers people in the current Slack conversation and same team.

**Data flow**: It reads the conversation roster, resolves member names, drops the bot and foreign-team users, and returns a mention index.

**Call relations**: Reply mention mapping calls this before posting agent-authored text.

*Call graph*: calls 2 internal fn (_resolved, _conversation_members); 1 external calls (mention_index).


##### `SlackNames._resolved`  (lines 2015–2023)

```
async def _resolved(self, wanted: Mapping[str, str]) -> dict[str, _NamedId]
```

**Purpose**: Combines cached name lookups with bounded fresh Slack lookups. This keeps name resolution fast and prevents one message from causing too many Slack calls.

**Data flow**: It reads remembered names, selects missing ids up to a limit, fetches names concurrently, remembers them, and returns the combined result.

**Call relations**: Both inbound name rendering and outbound mention mapping use this shared resolver.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); called by 2 (mention_ids, of); 1 external calls (gather).


##### `SlackNames._remembered`  (lines 2025–2046)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, _NamedId]
```

**Purpose**: Reads fresh-enough cached Slack names from the extension store. Stale or malformed cache rows are ignored.

**Data flow**: It receives ids, reads matching store keys in bulk, checks name, timestamp, and team fields, and returns valid cached _NamedId entries.

**Call relations**: The resolver calls this before deciding which ids need Slack API lookups.

*Call graph*: called by 1 (_resolved); 3 external calls (__init__, __init__, now).


##### `SlackNames._name`  (lines 2048–2065)

```
async def _name(self, id_: str, url: str) -> _NamedId | None
```

**Purpose**: Fetches and cleans the display name for one Slack user or channel id. It removes characters that would conflict with Slack mention markup.

**Data flow**: It calls users.info for users or conversations.info for channels, extracts the name and team, normalizes whitespace, strips forbidden brackets, trims length, and returns a _NamedId or nothing.

**Call relations**: The resolver calls this for cache misses.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (_resolved); 1 external calls (__init__).


##### `SlackNames._remember`  (lines 2067–2077)

```
async def _remember(self, names: Mapping[str, _NamedId]) -> None
```

**Purpose**: Writes resolved Slack names into the extension cache. Failures are logged but do not stop message handling.

**Data flow**: It receives id-to-name entries, stamps them with the current time, and stores each under its cache key.

**Call relations**: The resolver calls this after successful fresh name lookups.

*Call graph*: called by 1 (_resolved); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 2080–2100)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Fetches Slack's own permanent link to a message. This gives the agent and logs a reliable source link rather than trying to invent one.

**Data flow**: It calls chat.getPermalink with channel and timestamp, returns the permalink string on success, and returns nothing on failure.

**Call relations**: Inbound admission and form-answer admission use this for turn context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, interactive); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 2103–2120)

```
def _turn_context(sender: SlackUser | None, source: str | None, question: str | None=None) -> TurnContext
```

**Purpose**: Builds the context object attached to a ufo turn. It includes sender label, timezone when valid, source link, and optionally the question being answered.

**Data flow**: It receives Slack user info, source link, and optional question. It formats a sender line, creates a TurnContext, and drops invalid timezone data if needed.

**Call relations**: Admission paths call this before handing a Slack-originated turn to core.

*Call graph*: called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_resolve_member`  (lines 2123–2141)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member. It uses an existing link first, then a Slack-confirmed email to join or link a member.

**Data flow**: It receives user id, DM flag, and Slack user details. It returns a linked member, joins by confirmed email when possible, returns nothing when unresolved, and raises for unresolved DMs when user info is unavailable.

**Call relations**: Inbound admission, ambient folding checks, and interactive answer admission use this.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 3 (_admit_inbound, _folds_into_live_turn, interactive); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 2144–2169)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether an unmentioned reply in an existing Slack thread should become a new agent turn. This avoids interrupting human side conversations.

**Data flow**: It fetches recent thread history, admits by default when history is unavailable, otherwise asks core's ambient decision with the incoming message and logs when the answer is no.

**Call relations**: The background ambient-decision runner calls this before admitting an unaddressed message.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 2172–2194)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Builds the recent Slack thread history used by the ambient-reply decision. It includes member messages and the agent's own messages so the decision has context.

**Data flow**: It fetches the thread tail, converts usable messages into AmbientMessage entries, sorts them by timestamp, and returns the newest bounded slice.

**Call relations**: Ambient reply gating calls this before asking core whether to answer.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 2197–2243)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Fetches messages in a Slack thread before a given message. It walks pages so long threads do not accidentally return only the beginning.

**Data flow**: It calls Slack conversations.replies with a latest bound, follows cursors up to a page limit, collects messages, and returns nothing if the fetch is untrusted or too large.

**Call relations**: Ambient history and unseen-tail context use this to inspect Slack thread contents.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 2246–2266)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Converts one fetched Slack message into an ambient-history entry when it is relevant. It drops malformed, empty, future, and other-bot messages.

**Data flow**: It reads user, timestamp, text, and bot flags, marks whether the speaker is the agent, converts the timestamp, and returns an ordered AmbientMessage pair or nothing.

**Call relations**: Ambient history calls this for each Slack message fetched from the thread tail.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 2269–2324)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds background context for an admitted channel turn that the ufo transcript would not otherwise contain. This helps the agent understand nearby Slack discussion without treating it as instructions.

**Data flow**: It chooses no context for DMs, unseen-tail context for existing conversations, channel history for new top-level mentions, or thread history for mid-thread mentions; then fetches Slack messages, resolves names, and renders a digest.

**Call relations**: Inbound admission gathers this alongside sender and permalink data.

*Call graph*: calls 4 internal fn (_digest_names, _slack_ok, _unseen_tail, ambient_digest); called by 1 (_admit_inbound); 1 external calls (AsyncClient).


##### `_digest_names`  (lines 2327–2336)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves names for authors and mentions inside a group of Slack messages. This makes ambient digests readable.

**Data flow**: It extracts message text and author ids from Slack message objects, asks SlackNames to resolve them, and returns the mapping.

**Call relations**: Ambient context and unseen-tail context call this before rendering a digest.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2339–2385)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds context for thread messages that were intentionally not admitted as turns since the agent's last turn. This keeps Slack-visible conversation and model-visible conversation from drifting apart.

**Data flow**: It fetches the thread tail, sorts recent messages, walks backward until it finds an admitted message, keeps the unseen ones, resolves names, and renders a digest.

**Call relations**: Ambient context uses this for messages in already-participating Slack conversations.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2388–2456)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders fetched Slack messages as safe, bounded background text. It keeps human context while preventing one person's words from masquerading as system tags or another speaker.

**Data flow**: It filters unsupported messages, bots, the bot's own messages, and direct bot mentions; formats timestamp, speaker, and rendered text; trims long digests while preserving the first and newest lines; and wraps the result in a marked context element.

**Call relations**: Ambient context and unseen-tail context call this after fetching messages and resolving names.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2459–2461)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks whether a file download URL belongs to Slack. This prevents sending the bot token to an attacker-controlled host.

**Data flow**: It parses the URL host and returns true only for slack.com or a slack.com subdomain.

**Call relations**: The Slack file download stream calls this before making an authenticated request.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2464–2482)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a Slack private file download into chunks with safety limits. It never loads the whole file into memory.

**Data flow**: It verifies the host, downloads with the bot token, yields chunks, tracks total bytes, and raises if the file exceeds the workspace write limit.

**Call relations**: File downloading passes this stream directly into workspace file writing.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2494–2510)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads inbound Slack files into the ufo workspace. Oversized files are skipped and reported rather than partially written.

**Data flow**: It chooses safe inbox filenames, streams each Slack file into the workspace, records delivered names and skipped Slack names, and returns a DownloadedFiles summary.

**Call relations**: Inbound admission calls this before building the member message body.

*Call graph*: calls 2 internal fn (write_workspace_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2513–2522)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates a short note telling the agent which Slack files were saved or skipped. This makes attachments visible in the turn text.

**Data flow**: It receives delivered and skipped file lists, formats workspace paths and size-limit notes, and returns a newline-separated string.

**Call relations**: Inbound admission appends this note when it fences the member message.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2534–2539)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored Slack thread mirror into a MirroredThread object. It accepts both old string rows and newer structured rows.

**Data flow**: It receives a stored JSON-like row and returns a MirroredThread built from the queue key alone or from validated structured data.

**Call relations**: Follower hooks and mid-turn comments use this when they need to recover the Slack thread for a ufo conversation.


##### `MirroredThread.anchor`  (lines 2541–2545)

```
def anchor(self) -> str | None
```

**Purpose**: Finds the Slack timestamp that status and progress should attach to. Channel threads use the root timestamp in the queue key; DMs use the separately stored message timestamp.

**Data flow**: It checks the queue key for a root timestamp, then the message_ts field, and returns the first usable value or nothing.

**Call relations**: Status tracking, progress posting, and comment handling use this to choose the Slack thread parent.

*Call graph*: called by 1 (_track_status).


##### `_thread_mirror_key`  (lines 2548–2549)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the store key for a conversation's Slack thread mirror. One conversation has one durable Slack-thread record.

**Data flow**: It receives a conversation id and returns the prefixed store key string.

**Call relations**: Thread mirroring, follower hooks, and DM comment handling use this key.

*Call graph*: called by 3 (_mirror_thread, follow_turn, speak).


##### `_mirror_thread`  (lines 2552–2560)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Stores which Slack thread belongs to a ufo conversation. This lets turn-execution hooks post status and progress even though they do not have the original Slack request.

**Data flow**: It receives a conversation id and MirroredThread, builds the store key, serializes the thread, and writes it to the scoped store.

**Call relations**: Inbound admission and interactive answer admission write this before turns execute.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, interactive); 2 external calls (__init__, model_dump).


##### `_dm_anchor_key`  (lines 2563–2569)

```
def _dm_anchor_key(turn_id: UUID, message_ref: UUID | None=None) -> str
```

**Purpose**: Builds the store key that links a DM turn or absorbed message to the Slack message it answers. DMs need this because their conversation key does not include a thread root.

**Data flow**: It receives a turn id and optional message reference, adds the reference only when it is distinct from the turn id, and returns a prefixed key.

**Call relations**: DM anchoring, reply-thread lookup, and cleanup during attachment use this.

*Call graph*: called by 3 (_anchor_dm_thread, _reply_thread, attach).


##### `_anchor_dm_thread`  (lines 2572–2579)

```
async def _anchor_dm_thread(admitted: Admitted, message_ts: str) -> None
```

**Purpose**: Records which Slack DM message a ufo turn or absorbed arrival should answer under. This keeps replies threaded beneath the member's DM message.

**Data flow**: It receives admission result data and a Slack timestamp, builds the anchor key from the turn and arrival id, and stores the timestamp.

**Call relations**: Inbound admission and interactive answer admission call this for DM conversations.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 2 (_admit_inbound, interactive); 1 external calls (__init__).


##### `_reply_thread`  (lines 2582–2597)

```
async def _reply_thread(queue_key: str, turn_id: UUID, message_ref: UUID | None=None) -> str | None
```

**Purpose**: Finds the Slack thread timestamp for a reply. It uses the channel thread root when available or a stored DM anchor otherwise.

**Data flow**: It receives queue key, turn id, and optional message reference, returns the root timestamp for channel threads, or reads the best DM anchor from the store.

**Call relations**: Terminal posting, mid-turn speaking, and file attachment sharing call this to post in the right Slack thread.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 3 (attach, post, speak); 1 external calls (__init__).


##### `FollowerContext.workspace_id`  (lines 2608–2608)

```
def workspace_id(self) -> UUID
```

**Purpose**: Defines that a follower context must expose the current workspace id. Followers need this to distinguish identical Slack channel/thread ids across workspaces.

**Data flow**: A concrete context returns a UUID workspace id.

**Call relations**: Thread status and progress helpers rely on this protocol property.


##### `FollowerContext.public_base_url`  (lines 2611–2611)

```
def public_base_url(self) -> str | None
```

**Purpose**: Defines that a follower context may expose the deployment's public URL. Followers use it when building web links in footers.

**Data flow**: A concrete context returns a URL string or nothing.

**Call relations**: Footer rendering reads this through the follower context.


##### `FollowerContext.credential`  (lines 2613–2613)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Defines how followers read credentials such as the Slack bot token. This lets both surface requests and hooks supply the same need.

**Data flow**: A concrete context receives a credential slot name and returns its secret string.

**Call relations**: Status, progress, and footer code call this through the protocol.


##### `FollowerContext.tail`  (lines 2615–2617)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how followers watch live turn frames. These frames are the stream of activity, text, cost, resume, and terminal signals.

**Data flow**: A concrete context receives a turn id and optional cursor, and returns an async stream context yielding live frames.

**Call relations**: ThreadStatus and ThreadProgress follow this stream during turn execution.


##### `FollowerContext.turn_is_terminal`  (lines 2619–2619)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Defines how a progress follower checks whether a turn has already finished durably. This avoids posting progress after the answer has landed.

**Data flow**: A concrete context receives a turn id and returns true or false.

**Call relations**: ThreadProgress uses this during timed checkpoints.


##### `FollowerContext.conversation_agent`  (lines 2621–2621)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Defines how footer code finds the agent assigned to a conversation. The agent id is needed for configuration links.

**Data flow**: A concrete context receives a conversation id and returns an agent id or nothing.

**Call relations**: Progress footer generation uses this before calling the shared Slack footer renderer.


##### `FollowerContext.is_operator_workspace`  (lines 2623–2623)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Defines how footer code decides whether operator-only debug and accounting details may be shown. This protects ordinary or shared Slack rooms from internal details.

**Data flow**: A concrete context returns a boolean.

**Call relations**: Slack footer rendering calls this before adding accounting and debug links.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2680–2681)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Returns the unique identity of the Slack thread whose status this task writes. It includes workspace, channel, and thread timestamp.

**Data flow**: It combines the context workspace id with the stored Slack channel and thread timestamp and returns a tuple.

**Call relations**: Status writer tracking and restamping use this to coordinate one visible status per thread.


##### `ThreadStatus.run`  (lines 2683–2702)

```
async def run(self) -> None
```

**Purpose**: Runs the live Slack status follower for one turn. It writes an initial Thinking status, follows live frames, and clears the status when appropriate.

**Data flow**: It reads the bot token, opens a Slack client, sets the initial status, follows the turn stream, handles cancellation or failure, and clears the thread status at the end.

**Call relations**: The status task wrapper calls this after _track_status starts a task.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2704–2744)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one status line to Slack's assistant thread status API. It checks that this turn is still the current writer for the thread.

**Data flow**: It receives a Slack client, bot token, and status text, builds the API body, posts it, logs success or failure, and returns whether Slack accepted it.

**Call relations**: The run, follow, and clear steps use this for every visible status update.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2746–2755)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears Slack's thread status when this turn ends, but only if no sibling turn is still active in the same thread.

**Data flow**: It checks other live status objects for the same thread. If none remain, it writes an empty status.

**Call relations**: ThreadStatus.run calls this after follow ends or after certain failures.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2757–2811)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Watches live turn frames and converts them into short Slack status text. It also refreshes quiet statuses before Slack expires them.

**Data flow**: It opens the live frame stream, waits for frames, restamp events, or refresh timeouts, maps activity/text/resume/absorbed frames to status strings, rate-limits updates, and stops on terminal or parked frames.

**Call relations**: ThreadStatus.run calls this between initial set and final clear.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2821–2828)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers after the app posts in the same thread. Slack clears status when a reply lands, so this asks followers to write it again immediately.

**Data flow**: It receives workspace, channel, and thread timestamp, finds matching live ThreadStatus objects, and sets their wake event.

**Call relations**: Progress posting calls this after a progress message lands in Slack.

*Call graph*: called by 1 (_say).


##### `_track_status`  (lines 2831–2852)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts one Slack status task for a turn in this process. It also makes the newest turn the writer for that Slack thread.

**Data flow**: It skips existing tasks, finds the channel and anchor timestamp, logs unanchored threads, creates a ThreadStatus, records writer state, and starts the async task.

**Call relations**: The shared follower arming function calls this for admitted and executing turns.

*Call graph*: calls 2 internal fn (anchor, _run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 2855–2883)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Wraps a ThreadStatus task with logging and cleanup. It also passes the thread writer claim back to an older still-running turn when needed.

**Data flow**: It awaits the status run, logs task-level failure, removes task/status records, and updates or deletes the thread writer entry.

**Call relations**: _track_status starts tasks that execute this wrapper.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 2895–2899)

```
def __post_init__(self) -> None
```

**Purpose**: Validates a progress-reporting schedule. It prevents nonsensical timing such as zero wait or a cap below the first interval.

**Data flow**: It checks base_seconds and cap_seconds after construction and raises if they are invalid.

**Call relations**: Progress tracking creates a cadence before starting a ThreadProgress task.


##### `ProgressCadence.intervals`  (lines 2901–2912)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait intervals for long-turn progress posts. The waits double by elapsed time until they reach a cap.

**Data flow**: It starts at the base interval, yields waits forever, tracks elapsed time, and caps later waits.

**Call relations**: checkpoints_after uses this to find future progress checkpoints.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 2914–2923)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Generates future elapsed-time checkpoints after a turn has already been running for some time. This lets resumed reporters continue the same schedule.

**Data flow**: It walks interval checkpoints from zero and yields only checkpoint times greater than the supplied elapsed seconds.

**Call relations**: ThreadProgress uses this when it begins following a turn.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.update`  (lines 2936–2938)

```
def update(self, summary: str) -> None
```

**Purpose**: Records a completed activity summary as the current step for progress messages. It clears any streaming text state.

**Data flow**: It receives a summary string, normalizes whitespace, trims it, stores it as activity, and clears streaming chunks.

**Call relations**: ThreadProgress updates this when activity frames arrive.


##### `TurnActivity.stream`  (lines 2940–2941)

```
def stream(self, text: str) -> None
```

**Purpose**: Records that response text is currently streaming. Progress messages should say the response is being prepared rather than exposing partial text.

**Data flow**: It receives a text chunk and appends it to the streaming list.

**Call relations**: ThreadProgress calls this on text-delta frames.


##### `TurnActivity.current_step`  (lines 2943–2947)

```
def current_step(self) -> str
```

**Purpose**: Returns the member-facing current step. Streaming response preparation takes priority over the last completed tool activity.

**Data flow**: It checks whether any text is streaming. If yes, it returns the response-preparation label; otherwise it returns the stored activity.

**Call relations**: TurnActivity.report calls this when composing a progress post.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 2949–2959)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds one progress-message line for a checkpoint. It returns nothing if the turn has produced no meaningful signal yet.

**Data flow**: It gets the current step, formats elapsed minutes or hours, and returns a short progress line or nothing.

**Call relations**: ThreadProgress._post calls this before posting to Slack.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 3000–3003)

```
async def run(self) -> None
```

**Purpose**: Runs the long-turn progress reporter. It opens a Slack client and follows the turn until terminal or parked.

**Data flow**: It reads the bot token, creates a Slack client, and delegates to the follow loop.

**Call relations**: The progress task wrapper calls this after _track_progress starts a task.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 3005–3008)

```
def _elapsed(self) -> float
```

**Purpose**: Calculates how long the member has been waiting on the turn. It uses wall-clock time so restarts do not reset the wait.

**Data flow**: It subtracts the durable turn start time from the current UTC time and returns seconds.

**Call relations**: The progress follow loop and resumed notice code use this for scheduling and message text.

*Call graph*: called by 2 (_follow, _post_resumed); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 3010–3063)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Watches live turn frames and posts timed progress updates. It also posts a delayed restart/resume notice when useful.

**Data flow**: It sets up checkpoint timing, tracks activity and cost frames, waits for either frames or due times, posts progress or resume messages when due, and stops on terminal or parked frames.

**Call relations**: ThreadProgress.run delegates the reporter's main loop to this.

*Call graph*: calls 3 internal fn (_elapsed, _post, _post_resumed); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 3065–3092)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one scheduled progress checkpoint if there is useful activity to say. A silent checkpoint is skipped instead of filled with fluff.

**Data flow**: It asks TurnActivity for report text. If none, it logs a skip; otherwise it sends the text through _say and returns whether Slack accepted it.

**Call relations**: The follow loop calls this whenever the cadence reaches a checkpoint.

*Call graph*: calls 2 internal fn (_say, report); called by 1 (_follow); 1 external calls (log).


##### `ThreadProgress._post_resumed`  (lines 3094–3105)

```
async def _post_resumed(self, client: httpx.AsyncClient, bot_token: str, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts the one-line notice that work resumed after a restart. It is delayed briefly so fast-finishing resumed turns do not get a stale warning.

**Data flow**: It builds the fixed resume notice and sends it through _say with the current elapsed time and cost state.

**Call relations**: The follow loop calls this after seeing a resume frame and waiting through the grace period.

*Call graph*: calls 2 internal fn (_elapsed, _say); called by 1 (_follow).


##### `ThreadProgress._say`  (lines 3107–3147)

```
async def _say(self, client: httpx.AsyncClient, bot_token: str, text: str, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Sends one progress message to the Slack thread. The first successful progress message can include the standard footer.

**Data flow**: It finds channel and thread anchor, optionally builds footer metadata, creates a Slack reply body, posts it, logs success or failure, and restamps thread status when posting into a thread.

**Call relations**: Scheduled progress and resumed notices both send through this shared method.

*Call graph*: calls 4 internal fn (_footer, _restamp_thread_status, _slack_ok, slack_reply_body); called by 2 (_post, _post_resumed); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 3149–3170)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the footer for the first progress message when enough conversation information exists. It may include web links and partial cost information.

**Data flow**: It asks for the conversation's agent id, formats current cost if known, and delegates to the shared Slack footer builder.

**Call relations**: ThreadProgress._say calls this only for a first progress post.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_say).


##### `_track_progress`  (lines 3176–3203)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, thread: MirroredThread, started_at: datetime) -> None
```

**Purpose**: Starts one progress reporter task for a turn execution in this process. It uses the durable turn start time so the schedule survives restarts.

**Data flow**: It skips if already tracking the turn, creates a ThreadProgress with cadence and timestamps, starts its async task, and stores it.

**Call relations**: The shared follower arming function calls this only when the caller knows the turn's durable start time.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 4 external calls (__init__, __init__, create_task, now).


##### `_run_progress`  (lines 3206–3221)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Wraps a ThreadProgress task with logging and cleanup. Per-message Slack failures are handled inside the reporter; this catches task-level abandonment.

**Data flow**: It awaits the progress run, logs any exception, and removes the task from the in-memory table.

**Call relations**: _track_progress starts tasks that execute this wrapper.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 3235–3249)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts all live Slack side-channel followers that a turn should have. Status can start at admission; progress starts only when the turn execution has begun.

**Data flow**: It receives follower context, turn info, and mirrored Slack thread, starts status tracking, and starts progress tracking if a durable started_at is available.

**Call relations**: Inbound admission, interactive answer admission, and the turn-execution hook all converge here.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, follow_turn, interactive).


##### `_HookFollowerContext.workspace_id`  (lines 3261–3262)

```
def workspace_id(self) -> UUID
```

**Purpose**: Adapts a hook context to expose the workspace id required by follower code.

**Data flow**: It returns the workspace id from the wrapped extension context.

**Call relations**: Follower tasks use this adapter when armed from the turn-execution hook.


##### `_HookFollowerContext.public_base_url`  (lines 3265–3266)

```
def public_base_url(self) -> str | None
```

**Purpose**: Adapts a hook context to expose the deployment public URL for footer links.

**Data flow**: It returns the public base URL from the wrapped extension context.

**Call relations**: Footer generation reads this through the follower protocol.


##### `_HookFollowerContext.credential`  (lines 3268–3269)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Lets follower code read extension credentials from a hook context. This supplies the Slack bot token outside a surface request.

**Data flow**: It receives a credential slot and returns that slot's value from extension credentials.

**Call relations**: Status and progress tasks use this adapter when started by follow_turn.


##### `_HookFollowerContext.tail`  (lines 3271–3274)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Lets follower code tail live turn frames from a hook context.

**Data flow**: It receives a turn id and optional cursor and returns the extension context's live tail stream.

**Call relations**: ThreadStatus and ThreadProgress use this stream after follow_turn arms them.


##### `_HookFollowerContext.turn_is_terminal`  (lines 3276–3277)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets progress code check terminal state through a hook context.

**Data flow**: It receives a turn id and returns the extension context's terminal-state answer.

**Call relations**: ThreadProgress uses this to avoid posting late progress after a turn finishes.


##### `_HookFollowerContext.conversation_agent`  (lines 3279–3280)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Lets footer code find the agent for a conversation from a hook context.

**Data flow**: It receives a conversation id and returns the agent id or nothing from the extension context.

**Call relations**: Progress footer generation uses this adapter.


##### `_HookFollowerContext.is_operator_workspace`  (lines 3282–3283)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Lets footer code ask whether the current workspace is an operator workspace through a hook context.

**Data flow**: It returns the extension context's operator-workspace boolean.

**Call relations**: Shared Slack footer rendering uses this when followers were armed from a hook.


##### `follow_turn`  (lines 3286–3328)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Hook handler that arms Slack status and progress followers from the turn's own execution. This restores live feedback after restarts and avoids making feedback failure block the turn.

**Data flow**: It ignores missing or subagent turns, reads the mirrored Slack thread with a short timeout, wraps the hook context as a follower context, arms followers with the turn start time, and returns no blocking outcome.

**Call relations**: The manifest hook calls this when a user prompt starts executing.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `interactive`  (lines 3375–3479)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack interactive payloads such as question-form submits and connect-button clicks. It verifies the request, admits submitted answers, rewrites forms, or sends private authorization links.

**Data flow**: It reads and verifies the body, loads token and identity, parses the interaction, handles connect clicks with an ephemeral message, handles answer submits by finding the conversation, resolving the member, admitting an answer turn, anchoring DMs, arming followers, and scheduling a form rewrite.

**Call relations**: Slack calls this route when a user presses a Block Kit button.

*Call graph*: calls 23 internal fn (admit, admitted_body, connect_url, conversation_for, find_conversation, linked_member, _anchor_dm_thread, _arm_followers, _bot_token, _ctx_signing_secret (+13 more)); 8 external calls (__init__, __init__, gather, conversation_audience, JSONResponse, Response, fence_member_message, mint_marker).


##### `_rewrite_in_background`  (lines 3485–3488)

```
def _rewrite_in_background(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Schedules a Slack message rewrite after a form answer is accepted. It keeps the interactive acknowledgement fast.

**Data flow**: It creates a rewrite task, stores it in a set so it stays alive, and removes it when done.

**Call relations**: The interactive route calls this after confirming the submitted answer was the admitted one.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (interactive); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3491–3495)

```
async def _run_rewrite(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Runs the form-control rewrite and logs failures. A failed rewrite should not undo the admitted answer.

**Data flow**: It calls the replacement helper with the bot token and submit data, catching and logging any exception.

**Call relations**: Background rewrite tasks execute this.

*Call graph*: calls 1 internal fn (_replace_controls_with_answers); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3498–3503)

```
def _ephemeral_in_background(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Schedules a private Slack response visible only to one user. This is used for empty submits and connect-button instructions.

**Data flow**: It creates an ephemeral-post task, stores it strongly, and removes it when complete.

**Call relations**: The interactive route calls this when it needs to answer a click without posting to the whole thread.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 1 (interactive); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3506–3533)

```
async def _post_ephemeral(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Posts a Slack ephemeral message to one user, optionally inside a thread. Ephemeral means only that Slack user can see it.

**Data flow**: It reads the bot token, posts chat.postEphemeral with channel, user, text, and optional thread timestamp, and logs failures.

**Call relations**: Background ephemeral tasks execute this for interaction feedback.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_interaction`  (lines 3536–3598)

```
def _to_interaction(raw: bytes, identity: SlackIdentity) -> AnswerSubmit | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactive request into either an answer submit or a connect click. Other actions are ignored.

**Data flow**: It decodes the form payload JSON, validates team and action fields, extracts user/channel/message/thread data, converts connect values to UUIDs, or collects submitted answers and returns the matching interaction object.

**Call relations**: The interactive route calls this after signature verification and identity loading.

*Call graph*: calls 4 internal fn (_dict_field, _string_field, _submitted_answers, slack_thread_key); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_submitted_answers`  (lines 3601–3627)

```
def _submitted_answers(blocks: tuple[Mapping[str, object], ...], state: object) -> tuple[SubmittedAnswer, ...]
```

**Purpose**: Reads every question and held answer from a submitted Slack ask form. It uses the message's own blocks as the source of question order.

**Data flow**: It walks input blocks with the ask prefix, reads the matching held state value, converts it to text, and returns SubmittedAnswer records.

**Call relations**: _to_interaction calls this while parsing an ask submit.

*Call graph*: calls 1 internal fn (_held_answer); called by 1 (_to_interaction); 1 external calls (__init__).


##### `_held_answer`  (lines 3630–3648)

```
def _held_answer(field: object) -> str
```

**Purpose**: Converts one Slack control's state into plain answer text. It supports radio buttons, checkboxes, and text boxes.

**Data flow**: It inspects the control type, returns the selected option value, joins multiple selected values, trims typed text, or returns an empty string.

**Call relations**: Submitted-answer extraction calls this for each input block.

*Call graph*: calls 1 internal fn (_option_value); called by 1 (_submitted_answers); 1 external calls (get).


##### `_option_value`  (lines 3651–3655)

```
def _option_value(option: object) -> str
```

**Purpose**: Extracts the stored value from one Slack option. Missing or malformed options become an empty answer fragment.

**Data flow**: It receives an option object, checks for a string value field, and returns it or an empty string.

**Call relations**: _held_answer uses this for radio button and checkbox selections.

*Call graph*: called by 1 (_held_answer).


##### `_dict_field`  (lines 3658–3662)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload. It provides clear errors for malformed interactive payloads.

**Data flow**: It receives a mapping and field name, returns the dict value, or raises when the field is missing or not a dict.

**Call relations**: Interaction parsing uses this for user, channel, and message sections.

*Call graph*: called by 1 (_to_interaction).


##### `_replace_controls_with_answers`  (lines 3665–3698)

```
async def _replace_controls_with_answers(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Rewrites a Slack question message after an answer is accepted. The form controls become fixed answer lines plus who submitted them.

**Data flow**: It maps submitted answers by block id, walks the original delivered blocks, replaces input controls with context lines, replaces the submit row with submitter text, preserves other blocks, and updates the Slack message.

**Call relations**: The background rewrite runner calls this after interactive answer admission succeeds.

*Call graph*: calls 2 internal fn (_context_line, _rewrite_slack_message); called by 1 (_run_rewrite).


##### `connect_message_key`  (lines 3720–3723)

```
def connect_message_key(member_id: UUID, provider: str) -> str
```

**Purpose**: Builds the store key for remembering where a connect button was posted. It is keyed by requester and provider.

**Data flow**: It receives a member id and provider name and returns a prefixed key string.

**Call relations**: Connect-message holding uses this so later connection-settled hooks can find the Slack message.

*Call graph*: called by 1 (_hold_connect_message).


##### `_hold_connect_message`  (lines 3726–3756)

```
async def _hold_connect_message(store: ScopedStore, request: ConnectRequest | None, posted: dict[str, object], channel: str, ts: str | None) -> None
```

**Purpose**: Stores the Slack location of a reply's connect button, but only if the posted message actually contains the button. This avoids rewriting messages that fell back to plain text.

**Data flow**: It checks the connect request, timestamp, and posted blocks, finds a connect action, then stores channel, message timestamp, and optional thread timestamp.

**Call relations**: Terminal reply posting calls this after Slack accepts a message with connect blocks.

*Call graph*: calls 3 internal fn (put, _is_connect_action, connect_message_key); called by 1 (post); 1 external calls (__init__).


##### `_is_connect_action`  (lines 3759–3766)

```
def _is_connect_action(block: Mapping[str, object]) -> bool
```

**Purpose**: Checks whether a Slack block contains the connect button action. It recognizes the button by action id.

**Data flow**: It receives a block, verifies it is an actions block, scans its elements, and returns true if any element has the connect action id.

**Call relations**: Connect-message storage and connect-message settling use this to find or remove buttons.

*Call graph*: called by 2 (_hold_connect_message, settle_connect_message).


##### `_rewrite_slack_message`  (lines 3769–3786)

```
async def _rewrite_slack_message(bot_token: str, channel: str, ts: str, text: str, blocks: list[dict[str, object]]) -> None
```

**Purpose**: Updates a bot-authored Slack message with new text and blocks. It is the shared helper for rewriting answered forms and settled connect buttons.

**Data flow**: It receives token, channel, timestamp, fallback text, and blocks, posts chat.update to Slack, and raises if Slack rejects it.

**Call relations**: Question rewrite and connect-settle rewrite both use this.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_replace_controls_with_answers, settle_connect_message); 2 external calls (AsyncClient, dumps).


##### `_held_connect_message`  (lines 3789–3825)

```
async def _held_connect_message(bot_token: str, held: ConnectMessage) -> Mapping[str, object] | None
```

**Purpose**: Reads the current Slack message that contains a stored connect button. It reads live Slack state so it does not overwrite newer form-answer rewrites.

**Data flow**: It builds a one-message history or replies query, fetches from Slack, searches for the exact timestamp, and returns the message object or nothing.

**Call relations**: Connect settling calls this before rewriting the button into an account line.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (settle_connect_message); 1 external calls (AsyncClient).


##### `settle_connect_message`  (lines 3828–3854)

```
async def settle_connect_message(bot_token: str, held: ConnectMessage, provider: str, account: str) -> None
```

**Purpose**: Rewrites a connect button into a line showing the connected account. This makes the Slack thread reflect that authorization is complete.

**Data flow**: It reads the live Slack message, checks that a connect button is still present, removes connect action blocks, appends a connected-account context line, and updates the message.

**Call relations**: Connection-completion logic calls this after an external provider authorization lands.

*Call graph*: calls 4 internal fn (_context_line, _held_connect_message, _is_connect_action, _rewrite_slack_message).


##### `_context_line`  (lines 3857–3861)

```
def _context_line(text: str) -> dict[str, object]
```

**Purpose**: Builds a small Slack context block containing Markdown text. Context blocks are used for submitted-answer and connected-account notes.

**Data flow**: It receives text, trims it to Slack's context limit, and returns a Block Kit context block.

**Call relations**: Question rewriting and connect settling use this when replacing controls with status text.

*Call graph*: called by 2 (_replace_controls_with_answers, settle_connect_message).


##### `_reply_text`  (lines 3864–3874)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the main text for a terminal turn reply. It turns failures, cancellations, empty replies, and normal answers into member-facing text.

**Data flow**: It reads the terminal status and text from the writeback and returns an error line, cancellation reason, actual answer, or empty-reply placeholder.

**Call relations**: Reply text preparation calls this before adding credential hints or oversize attachment links.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 3877–3906)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Adds extra lines to a terminal reply for credential requests and artifacts too large for Slack upload. This prevents important follow-up instructions or files from disappearing.

**Data flow**: It starts with terminal reply text, adds a credentials-page instruction when needed, finds oversized artifacts, adds download-link lines for them, and returns the combined text.

**Call relations**: Terminal Slack posting calls this before splitting and sending reply parts.

*Call graph*: calls 3 internal fn (home_url, _oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 3909–3912)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one too-large artifact as a Markdown link line when a portal download link is available. It falls back to the filename when no link exists.

**Data flow**: It receives an artifact, asks the context for a temporary artifact link, and returns a bullet with name/link and byte size.

**Call relations**: Oversize reply preparation calls this for each over-limit shared artifact.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_reply_mention_ids`  (lines 3915–3926)

```
async def _reply_mention_ids(ctx: SurfaceContext, bot_token: str, channel: str, text: str) -> dict[str, str]
```

**Purpose**: Builds the Slack mention map for an outgoing reply only when the text contains an at-sign. This avoids unnecessary roster reads.

**Data flow**: It checks the text, loads identity, resolves same-team conversation members through SlackNames, and returns a name-to-id map or empty map.

**Call relations**: Reply mention mapping calls this before replacing readable @names with Slack mention markup.

*Call graph*: calls 1 internal fn (_identity); called by 1 (_reply_mentions_mapped); 1 external calls (__init__).


##### `_channel_info`  (lines 3929–3943)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for a channel or conversation. It is best-effort and returns nothing on failure.

**Data flow**: It calls conversations.info with the bot token and channel id, checks the response, and returns the channel dict if present.

**Call relations**: Audience selection, name resolution, and shared-channel footer gating use this.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 3946–3959)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel crosses workspace boundaries. If channel info cannot be read, it assumes shared for safety.

**Data flow**: It fetches channel info, returns true on missing info, otherwise checks Slack's shared-channel flags.

**Call relations**: Footer rendering uses this to decide whether to hide operator-only accounting and debug links.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 3962–3997)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, agent_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the small footer under Slack replies and first progress posts. It can include web links, configuration links, accounting, and debug links when safe.

**Data flow**: It receives context, token, channel, conversation, agent, turn, and accounting text. It builds portal links, checks operator workspace and shared-channel safety, and returns a trimmed footer or nothing.

**Call relations**: Terminal reply posting and progress footer generation use this shared footer builder.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 4019–4024)

```
def _slack_reply_progress_key(turn_id: UUID, reply_id: UUID | None=None) -> str
```

**Purpose**: Builds the store key for a reply delivery checkpoint. Terminal replies use the turn id alone; mid-turn replies add the reply id.

**Data flow**: It receives a turn id and optional reply id and returns the prefixed progress key.

**Call relations**: Terminal posting, mid-turn speaking, and cleanup use this to track exactly-once delivery records.

*Call graph*: called by 3 (attach, post, speak).


##### `_slack_reply_progress`  (lines 4027–4040)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Reads or creates the durable delivery-progress record for a Slack reply. This record prevents duplicate posts across retries.

**Data flow**: It reads the store key, validates an existing record, or atomically creates an empty record and returns both the model and stored raw value.

**Call relations**: Terminal and mid-turn posting call this before sending any Slack message parts.

*Call graph*: calls 2 internal fn (get, put_if); called by 2 (post, speak); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 4043–4052)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Atomically writes a new reply-delivery checkpoint. It detects when another worker changed the record.

**Data flow**: It serializes the progress object and writes it only if the stored value still equals the expected value, returning the updated pair or raising on conflict.

**Call relations**: Delivery, mention mapping, terminal posting, and mid-turn posting call this between send steps.

*Call graph*: calls 1 internal fn (put_if); called by 4 (_deliver_slack_reply, _reply_mentions_mapped, post, speak); 2 external calls (__init__, model_dump).


##### `_slack_reply_delivery`  (lines 4055–4070)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Checks whether a Slack message carries the metadata for a specific delivery id. This helps reconcile uncertain sends.

**Data flow**: It receives a message object and delivery id, validates metadata event type and payload id, and returns the message timestamp when matched.

**Call relations**: Reply reconciliation calls this while scanning Slack history.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 4073–4113)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Looks for a message Slack may have accepted when the previous HTTP response was lost. This closes an important duplicate-post window.

**Data flow**: It scans recent channel or thread messages with metadata included, follows pages up to a limit, returns the matching timestamp if found, or raises if the scan exceeds its page limit.

**Call relations**: Terminal and mid-turn posting call this when a progress record shows a pending delivery id.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 2 (post, speak); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 4116–4156)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Sends one Slack reply body with delivery checkpointing. It records pending state before posting and delivery state after Slack returns a timestamp.

**Data flow**: It skips if the delivery id is already recorded, writes pending id, posts to Slack, treats invalid_blocks as recoverable without recording delivery, otherwise records the accepted timestamp and returns the payload.

**Call relations**: Terminal and mid-turn posting call this for each reply part and fallback attempt.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 2 (post, speak); 2 external calls (__init__, model_copy).


##### `_reply_mentions_mapped`  (lines 4159–4189)

```
async def _reply_mentions_mapped(ctx: SurfaceContext, bot_token: str, channel: str, text: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue) -> tuple[_SlackReplyPro
```

**Purpose**: Replaces readable @names in reply text with Slack mention markup using a pinned map. Pinning keeps retries and text splitting deterministic.

**Data flow**: It uses an existing mention map from the delivery record if present; otherwise it resolves one, checkpoints it, applies mention markup, and returns updated progress plus mapped text.

**Call relations**: Terminal and mid-turn posting call this before splitting reply text into parts.

*Call graph*: calls 2 internal fn (_checkpoint_slack_reply, _reply_mention_ids); called by 2 (post, speak); 2 external calls (model_copy, mention_markup).


##### `post`  (lines 4192–4365)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Posts the terminal Slack reply for a completed turn. It handles splitting, footers, questions, connect buttons, retries, block fallbacks, mention mapping, and exact-once delivery.

**Data flow**: It finds channel and thread, reads token and progress record, maps mentions, prepares text and actions, builds footer, reconciles pending sends, posts each part with checkpoints and fallbacks, stores connect-button locations, marks the reply complete, and returns the first Slack message reference.

**Call relations**: The core surface delivery poller calls this when a turn has a terminal writeback for Slack.

*Call graph*: calls 16 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _hold_connect_message, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _reply_with_oversize_links, _slack_footer (+6 more)); 5 external calls (__init__, __init__, __init__, AsyncClient, loads).


##### `speak`  (lines 4368–4464)

```
async def speak(ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Posts a mid-turn Slack reply before the final answer. It is exact-once like terminal posting but has no footer, forms, connect buttons, or files.

**Data flow**: It finds the channel and thread for the reply or comment, reads token and progress record, maps mentions, reconciles pending sends, posts split parts with checkpoints and fallback on invalid blocks, marks complete, and returns the first Slack message reference.

**Call relations**: Core calls this when a running turn emits a mid-turn reply for Slack.

*Call graph*: calls 12 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _slack_reply_progress, _slack_reply_progress_key, _thread_mirror_key (+2 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 4467–4507)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Posts one chat.postMessage request and returns the parsed Slack payload without immediately rejecting Slack-level ok:false. This lets callers handle recoverable invalid_blocks responses.

**Data flow**: It sends the JSON body to Slack, turns HTTP errors into delivery errors with retry-after when available, and returns the response JSON.

**Call relations**: The delivery helper uses this for each Slack message send attempt.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 4510–4516)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the timestamp of a successfully posted Slack message. It raises a Slack API error when Slack did not accept the message.

**Data flow**: It checks ok:true, reads the ts field, returns it when non-empty, and raises otherwise.

**Call relations**: Delivery, terminal posting, and mid-turn posting use this after Slack responds.

*Call graph*: called by 3 (_deliver_slack_reply, post, speak); 1 external calls (__init__).


##### `attach`  (lines 4519–4564)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads and shares files produced by a completed turn. It also cleans up temporary delivery and DM-anchor records now that core has recorded the terminal reply reference.

**Data flow**: It finds the reply thread, deletes reply progress and DM anchor records for the turn, filters artifacts that fit Slack's upload cap, uploads them concurrently, groups accepted file ids into Slack-sized batches, and shares each batch in the thread.

**Call relations**: Core calls this after the terminal Slack reply has been durably attached.

*Call graph*: calls 7 internal fn (credential, _attachment_batches, _dm_anchor_key, _reply_thread, _share_uploaded_files, _slack_reply_progress_key, _upload_artifact); 4 external calls (__init__, gather, AsyncClient, Timeout).


##### `_attachment_batches`  (lines 4567–4571)

```
def _attachment_batches(files: Sequence[dict[str, str]]) -> Iterator[Sequence[dict[str, str]]]
```

**Purpose**: Splits uploaded file records into groups Slack will accept in one share message. This keeps large artifact sets deliverable.

**Data flow**: It receives a sequence of file dicts and yields slices no larger than Slack's attachment limit.

**Call relations**: File attachment sharing calls this after uploads finish.

*Call graph*: called by 1 (attach).


##### `_upload_artifact`  (lines 4574–4601)

```
async def _upload_artifact(ctx: SurfaceContext, client: httpx.AsyncClient, bot_token: str, artifact: SharedArtifact) -> str
```

**Purpose**: Performs the reserve-and-upload steps of Slack's external file upload flow. It streams artifact bytes from ufo's blob store rather than buffering them.

**Data flow**: It asks Slack for an upload URL and file id, validates both, posts the artifact byte stream to the upload URL, and returns the file id.

**Call relations**: attach runs this concurrently for every shareable artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (__init__, post).


##### `_share_uploaded_files`  (lines 4604–4628)

```
async def _share_uploaded_files(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, files: Sequence[dict[str, str]]) -> None
```

**Purpose**: Completes Slack's external upload flow by sharing already uploaded files into a channel or thread. One call can share several files together.

**Data flow**: It receives file ids and titles, channel, optional thread timestamp, and bot token, then posts files.completeUploadExternal to Slack.

**Call relations**: attach calls this for each batch of successfully uploaded artifacts.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (post, dumps).


##### `_slack_ok`  (lines 4631–4640)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Normalizes Slack Web API responses by requiring HTTP success and Slack ok:true. It raises a readable SlackApiError otherwise.

**Data flow**: It awaits an HTTP request, raises for HTTP errors, parses JSON, checks ok, includes Slack response messages when present, and returns the payload.

**Call relations**: Most Slack API helpers call this so they can work with trusted successful payloads.

*Call graph*: called by 18 (_list, _members, _say, _set, _ambient_context, _channel_info, _conversation_members, _declared_files, _held_connect_message, _post_ephemeral (+8 more)); 1 external calls (__init__).


### Terminal Surface
The UFO command-line surface exposes conversations, files, prompts, progress, and local operations as tab-separated terminal commands.

### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

This file is the bridge between the ufo backend and a user’s terminal. The shell client does not receive rich web objects. Instead, the server sends plain text “directives” such as “say this”, “show a prompt”, “run this local operation”, “poll again”, or “listen while idle”. Without this file, the command-line client would not know what to print, when to reconnect, how to send messages, how to provide secrets, or how to let the agent safely use the member’s local terminal workspace.

The main request handler is `channel`. A POST to a channel can mean several things: send a new message, reconnect to continue reading an existing turn, stop a running turn, answer a local terminal operation, provide a secret, retract a queued message, or do a fast one-shot send. The file checks the bearer token, finds or creates the member’s conversation, admits messages into the durable conversation queue, and streams back directives while the agent works.

The streaming logic is careful because terminal clients reconnect often. A stream is held open for a limited time; if the answer is not finished, the server sends a cursor with `poll`, so the client can resume without repeating old output. When a turn ends and the user is back at the prompt, the server sends `listen`, so background activity can wake the terminal later. Think of it like a walkie-talkie conversation with bookmarks: each reconnect says “continue after this last thing I heard.”

#### Function details

##### `terminal_runtime_id`  (lines 109–111)

```
def terminal_runtime_id(channel: str) -> str
```

**Purpose**: Creates a stable short identifier for the local terminal runtime tied to one channel. This lets the backend recognize the same terminal conversation across reconnects without exposing the raw channel name.

**Data flow**: It receives a channel string, hashes it, cuts the hash down to a fixed length, and returns that shortened hexadecimal text. Nothing else is changed.

**Call relations**: When `channel.bound` connects a terminal workspace, it asks this function for the runtime id to report to the backend. The id is then used as the local namespace for terminal operations in that conversation.

*Call graph*: called by 1 (bound); 1 external calls (sha256).


##### `directive`  (lines 114–122)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one line of the simple wire language that the shell client reads. It protects the line format by escaping tabs, newlines, and backslashes inside fields.

**Data flow**: It receives a command word and any number of text fields. It escapes characters that would confuse line splitting, joins everything with tabs, adds a newline, and returns bytes ready to send over HTTP.

**Call relations**: Almost every part of this file uses `directive` when it needs to tell the shell client something. Higher-level functions decide what should happen; this function turns that decision into the exact bytes sent to the client.

*Call graph*: called by 10 (_answer, _client_update, _fulfill_secret, _say_lines, _send, _subagent_note, channel, directives_for, history_directives, stream_directives).


##### `shared_files`  (lines 136–147)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files shared by a finished turn and prepares them for terminal display. It adds usable download links when the deployment has artifact links configured.

**Data flow**: It receives the surface context and a turn id. It asks the backend for artifacts shared by that turn, turns each artifact into a `SharedFile` with name, size, and link, and returns them as an ordered tuple.

**Call relations**: `stream_directives` receives this as a callback from `channel` and calls it only when a terminal frame is being rendered. The resulting file records are handed to `_answer`, which emits `file` directives.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 150–157)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Reads the workspace id claimed by an authorization bearer token. The routing layer can use this before running the actual handler to decide which workspace the request belongs to.

**Data flow**: It receives an HTTP request and authentication object. It looks for an `Authorization: Bearer ...` header, extracts the token, asks the bearer codec for the workspace claim, and returns that workspace id or `None` if the header is missing or invalid-looking.

**Call relations**: This is the surface’s workspace identifier hook. The route handler later verifies the same bearer token again for the member email, so workspace scoping and member identity come from the same signed token.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 163–222)

```
def history_directives(conversation: Conversation) -> tuple[bytes, ...]
```

**Purpose**: Turns an existing conversation transcript into terminal lines for a fresh resume. It shows recent user messages and completed agent replies, while avoiding repeating the latest live turn.

**Data flow**: It receives a `Conversation`. It walks through its messages, extracts readable text, counts completed work steps, keeps only the newest content within a character budget, and returns `you`, `note`, and `say` directive bytes.

**Call relations**: `channel` uses this when an empty reconnect has no cursor, meaning the terminal is rejoining and needs recent context. It relies on `_history_text`, `_dispatched`, and `directive` to shape the transcript into the same kind of lines the live stream uses.

*Call graph*: calls 3 internal fn (_dispatched, _history_text, directive); called by 1 (channel).


##### `_dispatched`  (lines 225–232)

```
def _dispatched(message: Message, active: set[str]) -> int
```

**Purpose**: Counts how many tool calls in an assistant message actually became live work. Calls that were written but never dispatched are not counted as visible progress.

**Data flow**: It receives one message and a set of active tool-use ids. If the message has structured blocks, it counts tool-use blocks whose ids are in the active set and returns that count; plain text messages return zero.

**Call relations**: `history_directives` uses this count to decide how many completed work steps to summarize as a `note`. It helps replay old history in a way that matches what the user would have seen live.

*Call graph*: called by 1 (history_directives).


##### `_history_text`  (lines 235–240)

```
def _history_text(message: Message) -> str
```

**Purpose**: Extracts the readable text from one stored message. It also cleans user messages into the form the member actually meant to send.

**Data flow**: It receives a message. If the content is plain text, it uses it directly; if the content is block-based, it joins the text blocks. For user messages it passes the result through `member_message_text`; for assistant messages it returns the raw assistant text.

**Call relations**: `history_directives` calls this for every message while rebuilding a readable terminal transcript.

*Call graph*: called by 1 (history_directives); 1 external calls (member_message_text).


##### `directives_for`  (lines 243–284)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True) -> tuple[bytes, ...
```

**Purpose**: Converts one live backend frame into the terminal directives the shell understands. A frame is a single event from the running turn, such as text, progress, cost, completion, cancellation, or a request for input.

**Data flow**: It receives a live frame plus context such as whether text has already streamed, pending credential prompts, connection text, shared files, and whether cancellation should exit. It pattern-matches the frame type and returns zero or more directive lines.

**Call relations**: `stream_directives` calls this for each frame read from the turn tail. It delegates terminal endings to `_answer`, subagent progress to `_subagent_note`, and uses `directive` for simple one-line events.

*Call graph*: calls 3 internal fn (_answer, _subagent_note, directive); called by 1 (stream_directives).


##### `_subagent_note`  (lines 287–293)

```
def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]
```

**Purpose**: Formats progress from a subagent as a terminal note. A subagent is a helper agent working under the main agent.

**Data flow**: It receives a subagent activity frame. If the frame has activity text, it chooses a label from the subagent name or profile, builds a note directive, and returns it; if there is no activity text, it returns no lines.

**Call relations**: `directives_for` calls this only for `SubagentActivity` frames. It keeps subagent progress visible without showing start and end events that are already described elsewhere.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for).


##### `_answer`  (lines 296–349)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True) -> tuple[bytes, ...]
```

**Purpose**: Builds the final terminal output for a completed, failed, or cancelled turn. It decides whether to show answer text, file links, secret prompts, connection links, a new prompt, or an exit command.

**Data flow**: It receives a terminal frame and extra information collected at the end of a turn. Depending on the terminal status, it emits `say`, `file`, `secret`, `ask`, or `exit` directives. It returns the complete tuple of lines that close that turn for the terminal.

**Call relations**: `directives_for` hands terminal frames to `_answer`. `_answer` uses `_say_lines` and `directive` to turn human-readable outcomes into the wire format sent by `stream_directives`.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 352–353)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Splits a block of text into separate terminal `say` lines. This keeps multi-line answers from breaking the directive format.

**Data flow**: It receives text, splits it into lines, turns each line into a `say` directive, and returns the directives as a tuple. If the text is empty, it still produces one `say` line for that empty value.

**Call relations**: `_answer` uses this whenever final text needs to be displayed as normal speech from the agent.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 356–513)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Runs the live streaming loop for one turn. It reads backend frames and terminal operation requests, converts them into directives, and stops at the right moment so the shell can reconnect safely.

**Data flow**: It receives an async frame tail, a hold timeout, callbacks for credentials, connection links, files, terminal operations, and cursor state. It waits for either the next frame or a terminal operation, emits directives as events arrive, remembers the last rendered cursor, and finally emits `poll` or `listen` when the stream should be resumed later.

**Call relations**: `channel` creates this stream after it has authenticated the request and found the turn. Inside the loop, it uses `_next` to read frames and `directives_for` to render them. It hands cursors back to the client so later requests can continue without duplicating or losing output.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 4 external calls (ensure_future, get_running_loop, wait, suppress).


##### `_next`  (lines 516–522)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Safely reads the next item from an async iterator. It turns the normal end of the iterator into `None` instead of letting an exception escape through a task.

**Data flow**: It receives an async iterator of live frames. It awaits the next item and returns it, or returns `None` if the iterator has ended.

**Call relations**: `stream_directives` wraps frame reads in tasks so it can race them against timeouts and terminal operations. `_next` makes that task result simple to handle.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 525–529)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Checks the request bearer token and extracts the member email if it is valid for the workspace. This is the main identity check for terminal requests.

**Data flow**: It receives the request and workspace id. It reads the authorization header, verifies the bearer token, and returns the email from the token or `None` if authentication fails.

**Call relations**: `channel`, `op_body`, and `system_skills` call this before serving protected terminal resources. If it returns `None`, those handlers reject the request as unauthorized.

*Call graph*: called by 3 (channel, op_body, system_skills); 1 external calls (verify_token).


##### `_utf8_header`  (lines 532–539)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Reads a header value that the shell sent as raw UTF-8 bytes, such as the current working directory. This avoids corrupting non-ASCII paths.

**Data flow**: It receives a request and header name. It retrieves the header text, reverses the HTTP server’s byte-to-text decoding, decodes it as UTF-8, and returns the recovered string.

**Call relations**: `channel` uses this for headers like the working directory and operation error text. It lets terminal paths and messages survive HTTP header encoding.

*Call graph*: called by 1 (channel).


##### `_stale_client`  (lines 542–546)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Detects whether the shell script version making the request differs from the version the server expects. This lets the server ask old clients to update.

**Data flow**: It receives a request. It reads the server’s expected client version from the environment and compares it with the request’s `x-ufo-script` header. It returns `True` only when the server has a version set and the client does not match.

**Call relations**: `channel` checks this before admitting messages or deciding how to respond to reconnects. If a client is stale, `channel` may return an update directive or an error instead of continuing normally.

*Call graph*: called by 1 (channel).


##### `_client_update`  (lines 549–550)

```
def _client_update() -> bytes
```

**Purpose**: Creates the small response that tells the shell to install an updated client and explains why. It is used when the server refuses to continue with an outdated script.

**Data flow**: It takes no input. It returns bytes containing an `install` directive followed by a `say` directive with the stale-client message.

**Call relations**: `channel` calls this in several stale-client branches when it can safely tell the terminal to update immediately.

*Call graph*: calls 1 internal fn (directive); called by 1 (channel).


##### `_resumed_from`  (lines 553–560)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Extracts the safe resume cursor from the client’s `since` header. It only trusts the cursor if it belongs to the same turn being streamed.

**Data flow**: It receives the request and the current turn id. It splits the `since` header into a named turn and cursor, returns the cursor if the named turn matches, and otherwise returns an empty cursor.

**Call relations**: `channel` calls this before opening the turn tail. This prevents a cursor from an older turn from accidentally skipping the beginning of a newer turn.

*Call graph*: called by 1 (channel).


##### `_turn_context`  (lines 563–575)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the extra context attached to a member’s admitted message, including who sent it, where it came from, and the member’s timezone if valid.

**Data flow**: It receives the member email and request. It reads the timezone header, tries to build a `TurnContext`, logs and drops the timezone if validation fails, and returns the resulting context.

**Call relations**: `channel` and `_send` call this before admitting a message. The backend then has sender and timezone information for the turn.

*Call graph*: called by 2 (_send, channel); 2 external calls (__init__, log).


##### `channel`  (lines 578–725)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the main ufo terminal channel endpoint. It authenticates the member, interprets the request type, admits or resumes work, and returns either a short response or a live stream of directives.

**Data flow**: It receives the surface context and HTTP request. It verifies identity, finds the conversation for the email and channel, handles special cases like secrets, sends, unsends, stops, operation replies, stale clients, and normal messages, then creates a streaming response that yields acknowledgements, history, workspace notes, and live turn directives.

**Call relations**: This is the center of the file. It calls many helper functions to decode headers, check authentication, build turn context, render history, and stream frames. It hands most backend work to `SurfaceContext`, and it hands live rendering to `stream_directives` through the nested `bound` generator.

*Call graph*: calls 23 internal fn (admit, claim_terminal, conversation_for, latest_turn, link_member, linked_member, read_transcript, stop_turn, tail, terminal_resolve (+13 more)); 5 external calls (partial, conversation_audience, PlainTextResponse, body, StreamingResponse).


##### `channel.moved_on`  (lines 691–693)

```
async def moved_on() -> bool
```

**Purpose**: Checks whether the conversation has advanced to a newer running turn while the current streamed turn was ending. This tells the stream whether to poll immediately instead of idling.

**Data flow**: It reads the latest turn id from the conversation. It returns `True` when there is a different latest turn and that newer turn is not terminal; otherwise it returns `False`.

**Call relations**: `channel` passes this callback into `stream_directives`. After a terminal frame, `stream_directives` calls it to decide whether to send `poll 0` so the client continues straight into the newer turn.


##### `channel.bound`  (lines 708–723)

```
async def bound() -> AsyncIterator[bytes]
```

**Purpose**: Wraps the outgoing directive stream with terminal connection setup and cleanup. It also sends any initial acknowledgement, replayed history, or workspace note before live output begins.

**Data flow**: It starts by connecting the terminal workspace if a current directory was supplied. Then it yields the sent acknowledgement, history lines, workspace note, and all lines from `stream_directives`. When the stream ends or is cancelled, it disconnects the terminal workspace.

**Call relations**: `channel` returns a `StreamingResponse` built from this generator. It calls `terminal_runtime_id` when connecting the terminal and ensures `SurfaceContext.terminal_disconnect` runs in the end.

*Call graph*: calls 1 internal fn (terminal_runtime_id).


##### `_send`  (lines 728–778)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str) -> Response
```

**Purpose**: Admits one message quickly and returns only an acknowledgement, without holding a streaming connection open. This is used when the user sends another message while an existing stream is already listening.

**Data flow**: It receives context, request, conversation id, member id, email, and working directory. It validates the send id and message body, optionally claims the terminal workspace, admits the message with an idempotency key, and returns a `sent` directive plus any workspace note.

**Call relations**: `channel` calls `_send` when the request has the send header. The existing held stream, not this request, will later deliver the resulting agent output.

*Call graph*: calls 4 internal fn (admit, claim_terminal, _turn_context, directive); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 781–803)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Attempts to retract a queued message before the agent has taken it up. This supports taking back a pending line from the terminal.

**Data flow**: It receives context, request, conversation id, member id, and the arrival id text. It rejects requests with a body, requires a real member, parses the arrival id, asks the backend to retract it, and returns empty success or a conflict message.

**Call relations**: `channel` calls `_unsend` when the unsend header is present. It does not admit a new turn; it only asks `SurfaceContext` to remove a still-pending arrival.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 806–825)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores a credential value that the user typed privately in response to a secret prompt. The value is not treated as a chat message and is not added to the transcript.

**Data flow**: It receives context, request, member id, and a sealed credential request token. It reads the slot and body value, checks size and emptiness, asks the backend to fulfill the credential request, and returns a `say` directive explaining whether it was stored.

**Call relations**: `channel` calls this early when the secret header is present. It hands the actual validation and secure storage to `SurfaceContext.fulfill_credential_request`.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 828–840)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the raw bytes for an in-flight terminal operation. For example, a local write operation may fetch its staged content through this endpoint.

**Data flow**: It receives context and request. It authenticates the email, finds the linked member and channel queue key, asks the backend for the operation body, and returns the bytes as binary content or a 404-style text response if missing.

**Call relations**: This is the GET route for `{channel}/op/{op_id}`. It protects operation bodies with the same bearer authentication used by `channel` and delegates lookup to the terminal operation store in `SurfaceContext`.

*Call graph*: calls 3 internal fn (linked_member, terminal_op_body, _authenticated_email); 2 external calls (PlainTextResponse, Response).


##### `system_skills`  (lines 843–855)

```
async def system_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the zipped bundle of system skills to an authenticated shell client. It supports normal HTTP caching with an ETag so unchanged bundles do not need to be downloaded again.

**Data flow**: It receives context and request. It authenticates the email, reads the current skill bundle and digest, compares the request’s `if-none-match` header, and returns either `304 Not Modified` or the zip archive with cache headers.

**Call relations**: This is the GET route for `{channel}/skills`. It uses `_authenticated_email` for access control and then serves `ctx.system_skill_bundle` directly to the client.

*Call graph*: calls 1 internal fn (_authenticated_email); 2 external calls (PlainTextResponse, Response).
