# Native communication and code-service integrations  `stage-13.2`

This stage is the system’s set of adapters for outside services that people already use: Slack, iMessage through Spectrum, and GitHub. It is shared behind-the-scenes support, used whenever the project needs to talk in a native chat app or access code on GitHub.

The Slack pieces work like a careful translator and installer. The mentions code changes Slack’s special hidden mention format into readable names, and safely turns approved names back into real Slack mentions when replying. The attribution and hooks code add a small footer to connector-sent Slack messages so people can see the bot was responsible, while making sure the bot does not mistake that footer for a new user message. The tools code guides an administrator through connecting a Slack workspace, setting up the Slack app, and searching Slack conversations.

The iMessage cloud bridge signs in to Spectrum, opens secure connections, sends messages, receives events, and turns Spectrum responses into objects the extension can use. The GitHub App code checks a workspace’s installation and creates short-lived credentials for coding tasks, falling back to a user’s stored GitHub token when needed.

## Files in this stage

### GitHub coding credentials
Validates workspace GitHub App installations and mints the credentials needed for coding workflows.

### `extensions/coding/ufo_ext_coding/github_app.py`

`domain_logic` · `credential lookup and token minting`

This file solves a trust problem around GitHub access. A workspace may have installed this product’s GitHub App, which lets the system ask GitHub for a temporary installation token. That token is safer than storing a long-lived secret, and it is limited to the permissions the organization approved when it installed the App.

The important detail is that the workspace does not store a plain GitHub installation id. It stores a sealed value, meaning an encrypted and authenticated binding that only the install callback could have created. This prevents someone from typing another organization’s installation id into a credential slot and tricking the system into minting tokens for the wrong organization.

The main class, GitHubAppTokens, reads that sealed installation binding, opens it, and asks GitHub for a one-hour token. It caches the token until shortly before it expires, so repeated work in the same conversation does not keep calling GitHub. It also avoids launching duplicate minting requests when several tasks need the same token at once.

GitHubAPIAuth wraps this for GitHub API use. It prefers the App token when an installation exists, but uses a stored personal token when there is no installation. If a sealed installation value is present but invalid, it fails instead of falling back, because using a different identity than the organization approved would be unsafe.

#### Function details

##### `_segment`  (lines 58–59)

```
def _segment(payload: dict[str, object]) -> bytes
```

**Purpose**: Turns one part of a JSON Web Token, or JWT, into the compact text form GitHub expects. A JWT is a signed short message used here to prove that this server really owns the GitHub App.

**Data flow**: It receives a small dictionary, converts it to compact JSON, encodes that text in URL-safe base64, and removes trailing padding characters. The result is a bytes value ready to be joined into a JWT.

**Call relations**: GitHubAppTokens._jwt uses this helper twice: once for the token header and once for the token body. It is a small formatting step before the private key signs the JWT.

*Call graph*: called by 1 (_jwt); 2 external calls (urlsafe_b64encode, dumps).


##### `GitHubAppTokens.bound`  (lines 83–95)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether a workspace has a GitHub App installation bound to it. It does not mint a token; it only answers whether the sealed installation value is present and valid.

**Data flow**: It takes a workspace id and credential store. It asks the store for the installation slot; if the slot is missing, it returns false. If a value exists, it tries to open the sealed installation binding for that workspace and slot, and returns true only if that succeeds.

**Call relations**: This is used when the system needs to know whether the GitHub App path applies before exporting credentials or choosing a fallback. It deliberately behaves like secret on invalid sealed values, so the system does not claim there is no App binding and then silently use a personal token instead.

*Call graph*: calls 1 internal fn (get); 1 external calls (open_installation).


##### `GitHubAppTokens.secret`  (lines 97–121)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Returns the actual GitHub installation token for a workspace, or returns nothing when the workspace has no App installation. This is the main entry point for getting a GitHub App credential.

**Data flow**: It receives a workspace id and credential store. It reads the sealed installation binding, opens it, and uses the workspace plus installation as the cache key. If a still-fresh token is cached, it returns that token. Otherwise it starts or waits for a shared minting task, then returns the newly minted token.

**Call relations**: Callers use this when they need a GitHub token for a workspace. It hands off the real minting work to GitHubAppTokens._mint, and it uses task sharing so that simultaneous requests for the same installation wait on one GitHub call instead of making several.

*Call graph*: calls 2 internal fn (get, _mint); 4 external calls (create_task, shield, time, open_installation).


##### `GitHubAppTokens._mint`  (lines 123–131)

```
async def _mint(self, key: tuple[UUID, str], installation: str) -> tuple[str, float]
```

**Purpose**: Performs one token mint for a specific workspace and installation, then records the result in the cache. It also cleans up the in-progress task marker when the mint finishes.

**Data flow**: It receives the cache key and installation id. It asks GitHubAppTokens._installation_token to get a token and expiry time from GitHub, stores that pair in the cache, and returns it. Whether the mint succeeds or fails, it removes its own pending-task entry if it is still the active one.

**Call relations**: GitHubAppTokens.secret creates this as an asynchronous task when no usable cached token exists. This function is the bridge between the public secret lookup and the lower-level GitHub API exchange.

*Call graph*: calls 1 internal fn (_installation_token); called by 1 (secret); 1 external calls (current_task).


##### `GitHubAppTokens._installation_token`  (lines 133–188)

```
async def _installation_token(self, installation: str) -> tuple[str, float]
```

**Purpose**: Talks to GitHub to exchange this App’s signed proof for an installation access token. It also checks that GitHub’s response is usable and records a warning if requested permissions are missing.

**Data flow**: It receives a GitHub installation id. It builds an authenticated request using GitHubAppTokens._jwt, optionally asks for specific permissions, and posts to GitHub’s installation-token endpoint. If GitHub cannot be reached, refuses the request, or returns a malformed response, it raises a credential-minting error. On success, it returns the token text and its expiry time.

**Call relations**: GitHubAppTokens._mint calls this when a fresh token is needed. This function is the only place in the file that directly talks to GitHub’s API, and it hands back the token information that higher-level code caches and returns.

*Call graph*: calls 1 internal fn (_jwt); called by 1 (_mint); 4 external calls (__init__, fromisoformat, AsyncClient, warn).


##### `GitHubAppTokens._jwt`  (lines 190–197)

```
def _jwt(self) -> str
```

**Purpose**: Creates a short-lived signed JWT proving that this server controls the GitHub App private key. GitHub requires this proof before it will issue installation tokens.

**Data flow**: It reads the current time, builds a JWT header and body with the App id and expiration time, signs them with the App’s RSA private key, and combines the pieces into a single text token. The output is used as a bearer credential for the GitHub App itself.

**Call relations**: GitHubAppTokens._installation_token calls this just before asking GitHub for an installation token. It relies on _segment to format the header and body before signing them.

*Call graph*: calls 1 internal fn (_segment); called by 1 (_installation_token); 4 external calls (urlsafe_b64encode, PKCS1v15, SHA256, time).


##### `GitHubAPIAuth.bound`  (lines 207–214)

```
async def bound(self, workspace_id: UUID, store: CredentialStore) -> bool
```

**Purpose**: Checks whether the workspace has any usable GitHub API authentication available. It accepts either a GitHub App installation or a stored fallback token.

**Data flow**: It receives a workspace id and credential store. If App tokens are configured and the workspace has a bound installation, it returns true. Otherwise it checks the fallback slot in the credential store and returns true if a stored token exists, false if not.

**Call relations**: This method is used by code that needs to know whether GitHub API credentials can be supplied. It consults GitHubAppTokens.bound first so an App installation takes priority, then checks the fallback personal-token path.

*Call graph*: calls 1 internal fn (get).


##### `GitHubAPIAuth.secret`  (lines 216–223)

```
async def secret(self, workspace_id: UUID, store: CredentialStore) -> str | None
```

**Purpose**: Builds the Authorization header value used for GitHub API requests. It prefers a GitHub App installation token and falls back to a stored personal token only when there is no App token.

**Data flow**: It receives a workspace id and credential store. It first asks GitHubAppTokens.secret for an installation token if App support is configured. If that returns nothing, it tries to read the fallback token from the store. If it finds a token, it prefixes it with Bearer and returns the header value; if no token exists, it returns nothing.

**Call relations**: This is the practical API-auth companion to GitHubAppTokens.secret. Higher-level request code can ask it for one ready-to-use bearer string instead of caring whether the token came from the GitHub App or from the fallback credential slot.

*Call graph*: calls 1 internal fn (get).


##### `app_tokens`  (lines 226–245)

```
def app_tokens(installation_slot: str, permissions: tuple[tuple[str, str], ...] | None=GIT_INSTALLATION_PERMISSIONS) -> GitHubAppTokens
```

**Purpose**: Constructs a GitHubAppTokens object from deployment environment variables. It makes startup fail loudly if the GitHub App private key is missing or not the expected RSA key type.

**Data flow**: It receives the credential-slot name that stores the sealed installation binding, plus the permissions to request. It reads the GitHub App id and PEM-formatted private key from environment variables, parses the key, verifies its type, and returns a configured GitHubAppTokens instance.

**Call relations**: Configuration code calls this when wiring GitHub App support into the system. The returned GitHubAppTokens object is then used by GitHubAPIAuth and other credential flows to check bindings and mint tokens.

*Call graph*: 2 external calls (__init__, load_pem_private_key).


### iMessage cloud provider
Connects the iMessage extension to Spectrum’s cloud service for authentication, messaging, event intake, and response translation.

### `extensions/imessage/ufo_ext_imessage/cloud.py`

`io_transport` · `startup, request handling, and live event streaming`

This file lets the project talk to Spectrum Cloud as if it were a local iMessage provider. Without it, the extension would know what it wants to do, such as register a phone number or send a message, but it would have no practical way to ask Spectrum’s servers to do it.

It uses two kinds of network calls. Simple account-style actions, such as listing or creating users and getting a shared line token, go through HTTP requests. Live iMessage actions, such as creating chats, sending messages, streaming events, and moving attachments, go through gRPC, which is a fast remote-call system where code calls a server method almost like calling a local function.

The central object is `SpectrumProject`. It stores the project id and secret, keeps a reusable HTTP client, and caches a short-lived shared line token so every operation does not need to log in again. The token cache is protected by an async lock, which is a small “one-at-a-time” gate that prevents two tasks from refreshing the same token at once.

The file also translates incoming Spectrum message events into plain provider messages, filtering out messages that should not be surfaced, such as spam, system messages, messages from the current user, hidden attachments, and empty events. In short, it is both the network adapter and the safety filter for cloud-backed iMessage work.

#### Function details

##### `SpectrumProject.installation_id`  (lines 96–97)

```
def installation_id(self) -> str
```

**Purpose**: This property gives the project a stable installation name in the form `project:<id>`. Other parts of the provider can use it as a clear label for which Spectrum project is being used.

**Data flow**: It reads the stored project id from the `SpectrumProject` object and turns it into a single string. Nothing outside the object is changed.

**Call relations**: This is a lightweight identity helper on `SpectrumProject`. It does not call out to Spectrum; it simply gives callers a consistent name when they need to refer to this configured installation.


##### `SpectrumProject.line`  (lines 99–120)

```
async def line(self) -> SpectrumLine
```

**Purpose**: This gets the shared Spectrum iMessage line that all cloud iMessage operations need. It reuses a cached token when it is still fresh, and asks Spectrum for a new one when the old token is missing or close to expiring.

**Data flow**: It starts with the current event-loop state, including a token cache and a lock. If the cache contains a valid `SpectrumLine`, it returns that. Otherwise it sends an authenticated HTTP request to Spectrum, checks that the response has the expected shape, stores the new token and its expiry time, and returns the new line.

**Call relations**: Most cloud actions call this first because they need a bearer token before opening a gRPC request. It is used by phone registration, catch-up, subscription, text sending, attachment sending, and attachment downloading. Internally it asks `_loop` for safe per-loop state and `_request` for the HTTP call.

*Call graph*: calls 2 internal fn (_loop, _request); called by 6 (catch_up, download_attachment, register_phone, send_attachment, send_text, subscribe); 4 external calls (__init__, __init__, TypeAdapter, monotonic).


##### `SpectrumProject._loop`  (lines 122–138)

```
def _loop(self) -> SpectrumLoop
```

**Purpose**: This gives the current async event loop its own safe working state: an HTTP client, a lock, and a token cache. It exists because async objects such as locks and clients are tied to the event loop that uses them.

**Data flow**: It reads the currently running asyncio event loop. If this project already has state for that loop, it returns it. If not, it creates state for that loop, either reusing the original client and token cache for the first loop or making a fresh client, lock, and cache for later loops.

**Call relations**: `line`, `_request`, and `invalidate` use this whenever they need loop-local state. It is the quiet plumbing that keeps token caching and HTTP access safe when the same project object is touched from more than one async loop.

*Call graph*: called by 3 (_request, invalidate, line); 4 external calls (__init__, Lock, get_running_loop, AsyncClient).


##### `SpectrumProject.register_phone`  (lines 140–189)

```
async def register_phone(self, phone_number: str, idempotency_key: str) -> RegisteredPhone
```

**Purpose**: This registers or finds a Spectrum user for a phone number, then creates a direct iMessage chat for that phone number. It returns the assigned Spectrum phone number and the conversation id needed for later sends.

**Data flow**: It takes a target phone number and an idempotency key, which is a repeat-safe label that helps the server avoid doing the same operation twice. It first asks Spectrum for existing users, creates the user if needed, gets a shared line token, then calls the chat service to create a direct iMessage chat. It returns a `RegisteredPhone` with the assigned sender number and conversation id, or raises a clear error if Spectrum rejects the operation or the target has not opted in.

**Call relations**: This is called when the provider needs to prepare a phone number for messaging. It relies on `_request` for HTTP user lookup and creation, `line` for authentication, `channel` for the secure gRPC connection, and `rpc_metadata` to attach the token and idempotency key to the remote call.

*Call graph*: calls 5 internal fn (_request, channel, line, rpc_metadata, __init__); 5 external calls (__init__, __init__, TypeAdapter, CreateChatRequest, ChatServiceStub).


##### `SpectrumProject._request`  (lines 191–214)

```
async def _request(self, method: str, path: str, *, json: dict[str, str] | None=None, idempotency_key: str | None=None) -> object
```

**Purpose**: This is the shared helper for Spectrum Cloud HTTP requests. It adds project authentication, optional repeat-safety headers, checks for HTTP errors, and returns the decoded JSON response.

**Data flow**: It receives an HTTP method, a path, optional JSON data, and an optional idempotency key. It builds the full Spectrum Cloud URL, sends the request with basic authentication using the project id and secret, raises a `SpectrumCloudError` if the server returns a bad HTTP status, and otherwise returns the response body as parsed JSON.

**Call relations**: `line` uses this to fetch shared line tokens, and `register_phone` uses it to list or create Spectrum users. It calls `_loop` so the request uses the HTTP client belonging to the current async loop.

*Call graph*: calls 1 internal fn (_loop); called by 2 (line, register_phone); 2 external calls (__init__, BasicAuth).


##### `SpectrumProject.channel`  (lines 216–217)

```
def channel(self) -> grpc.aio.Channel
```

**Purpose**: This opens a secure gRPC channel to Spectrum’s iMessage service. A gRPC channel is the network tunnel used to call remote service methods.

**Data flow**: It does not take any input beyond the project object. It creates and returns a secure channel pointed at Spectrum’s iMessage address, using SSL credentials so the connection is encrypted.

**Call relations**: Every gRPC-based action calls this before talking to Spectrum: registering chats, catching up events, subscribing to live messages, sending text, sending attachments, and downloading attachments. The caller then creates the specific service stub it needs on top of this channel.

*Call graph*: called by 6 (catch_up, download_attachment, register_phone, send_attachment, send_text, subscribe); 1 external calls (ssl_channel_credentials).


##### `SpectrumProject.invalidate`  (lines 219–222)

```
async def invalidate(self) -> None
```

**Purpose**: This clears the cached shared line token. It is useful after an authentication problem, because the next operation will be forced to fetch a fresh token.

**Data flow**: It gets the current loop’s token state, waits for the token lock, then empties the token cache. It returns nothing, but it changes the project’s in-memory authentication state.

**Call relations**: This is a maintenance hook for the provider around error recovery. It uses `_loop` for the correct loop-local cache and lock, then leaves future calls to `line` to rebuild the token state.

*Call graph*: calls 1 internal fn (_loop).


##### `SpectrumProject.invalid_cursor`  (lines 224–228)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: This answers whether an error means an event cursor is invalid. A cursor is a saved position in a stream, like a bookmark in a long list of events.

**Data flow**: It receives an exception and checks whether it is a gRPC error whose status code is `INVALID_ARGUMENT`. It returns `true` for that specific case and `false` otherwise.

**Call relations**: This is used by higher-level event-reading code to decide whether a saved stream position can no longer be used. It does not fix the problem itself; it classifies the error so the caller can choose a recovery path.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.external_error`  (lines 230–233)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: This tells the rest of the provider whether an exception came from outside systems, such as Spectrum, the network, or target opt-in rules. That helps callers separate expected service failures from bugs in local code.

**Data flow**: It receives an exception and checks its type against known external failure classes: gRPC errors, Spectrum cloud errors, opt-in failures, and HTTP client errors. It returns a boolean answer.

**Call relations**: Higher-level provider code can use this when logging, retrying, or reporting failures. This function does not call other helpers; it is a simple classifier for errors produced by the network methods in this file.


##### `SpectrumProject.error_code`  (lines 235–238)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: This turns an exception into a short, readable error label. It gives gRPC errors their official status name and uses the Python class name for other errors.

**Data flow**: It receives an exception. If it is a gRPC error, it reads the remote status code and returns that code’s name. Otherwise it returns the exception type name, such as `SpectrumCloudError`.

**Call relations**: This supports higher-level logging and reporting. It is usually paired with `external_error`, so callers can both recognize an outside failure and give it a compact label.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.catch_up`  (lines 240–261)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This reads missed iMessage events from Spectrum after a saved sequence number. It lets the provider recover messages that arrived while it was offline or disconnected.

**Data flow**: It takes an optional `after_sequence` bookmark. It gets a line token, opens a secure gRPC channel, asks Spectrum for catch-up events, then yields provider events one by one. Completion frames become events with a head sequence, and message-change frames are filtered and converted into inbound messages when appropriate.

**Call relations**: This is part of the event-reading path. It calls `line` for authentication, `channel` for the remote stream, `rpc_metadata` for request headers, and `_inbound_message` to turn raw Spectrum message changes into provider-friendly messages.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 3 external calls (__init__, CatchUpEventsRequest, EventServiceStub).


##### `SpectrumProject.subscribe`  (lines 263–279)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: This subscribes to live iMessage events from Spectrum. It is the always-on stream used to receive new messages as they happen.

**Data flow**: It takes an asyncio event called `ready`. After getting a token and opening the message event stream, it sets `ready` so the caller knows the subscription is active. Then it yields provider events from the stream, including sequence numbers and filtered inbound messages when the frame contains a received message.

**Call relations**: This is used during live event handling. Like `catch_up`, it depends on `line`, `channel`, `rpc_metadata`, and `_inbound_message`; unlike catch-up, it also signals readiness to the caller once the remote subscription has started.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 4 external calls (__init__, set, SubscribeMessageEventsRequest, MessageServiceStub).


##### `SpectrumProject.send_text`  (lines 281–301)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: This sends a plain text iMessage through an existing Spectrum conversation. It returns the id of the message Spectrum created.

**Data flow**: It receives a conversation id, the text to send, and an idempotency key. It gets a line token, builds a send-text request, attaches authorization metadata, and calls Spectrum’s message service. If Spectrum says permission is denied, it converts that into a `TargetNotOptedIn` error; otherwise it returns the sent message guid.

**Call relations**: This is the text-sending path used by provider code after a conversation has been registered. It calls `line`, `channel`, and `rpc_metadata`, then hands the request to Spectrum’s message service.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (SendTextMessageRequest, MessageServiceStub).


##### `SpectrumProject.send_attachment`  (lines 303–330)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: This sends a file attachment in an existing Spectrum conversation. It first uploads the file, then sends a message that points to the uploaded attachment.

**Data flow**: It receives a conversation id, filename, raw file bytes, and an idempotency key. It gets a token and opens a secure channel, uploads the attachment bytes to Spectrum, then sends an attachment message using the uploaded attachment’s id. It returns the guid of the message that was sent.

**Call relations**: This is the attachment-sending path. It calls `line`, `channel`, and `rpc_metadata`, then uses Spectrum’s attachment service for the upload and Spectrum’s message service for the actual iMessage send.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 4 external calls (UploadAttachmentRequest, AttachmentServiceStub, SendAttachmentMessageRequest, MessageServiceStub).


##### `SpectrumProject.download_attachment`  (lines 332–342)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: This downloads an attachment from Spectrum in chunks. It returns an async stream of bytes so large files do not need to be loaded all at once.

**Data flow**: It receives an attachment id. It gets a line token, opens a secure gRPC channel, asks Spectrum to download that attachment, then yields each primary byte chunk as it arrives from the server.

**Call relations**: This is used when provider code needs the contents of an attachment from an inbound message. It relies on `line`, `channel`, and `rpc_metadata`, then reads from Spectrum’s attachment service stream.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (DownloadAttachmentRequest, AttachmentServiceStub).


##### `spectrum_project`  (lines 346–359)

```
def spectrum_project() -> SpectrumProject
```

**Purpose**: This creates the configured `SpectrumProject` singleton for the process. It reads the required project id and secret from environment variables and fails clearly if they are missing.

**Data flow**: It reads `SPECTRUM_PROJECT_ID` and `SPECTRUM_PROJECT_SECRET` from the environment. If either is absent, it raises `ProviderNotConfigured` with setup instructions. If both are present, it builds a `SpectrumProject` with an HTTP client, an async lock, and an empty token cache, and returns the cached project object on later calls.

**Call relations**: This is the setup doorway into the cloud provider. Other code calls it when it needs a ready-to-use Spectrum project, and the cache means the expensive shared client and token state are reused instead of recreated each time.

*Call graph*: 4 external calls (__init__, __init__, Lock, AsyncClient).


##### `rpc_metadata`  (lines 362–366)

```
def rpc_metadata(token: str, idempotency_key: str | None=None) -> tuple[tuple[str, str], ...]
```

**Purpose**: This builds the small set of headers sent with Spectrum gRPC calls. The headers carry the bearer token, and sometimes an idempotency key for safe retries.

**Data flow**: It receives a token and optionally an idempotency key. It always creates an authorization entry in the form `Bearer <token>`, adds an `x-idempotency-key` entry when provided, and returns the entries as an immutable tuple.

**Call relations**: All gRPC operations in this file call this before contacting Spectrum: registration, catch-up, live subscription, text sending, attachment sending, and attachment download. It is the common place that formats authentication metadata consistently.

*Call graph*: called by 6 (catch_up, download_attachment, register_phone, send_attachment, send_text, subscribe).


##### `_inbound_message`  (lines 369–407)

```
def _inbound_message(event: object) -> InboundMessage | None
```

**Purpose**: This converts a raw Spectrum message-change event into the provider’s clean inbound message shape. It also filters out events that should not be treated as user-visible incoming messages.

**Data flow**: It receives a raw event object. It first checks that the object is the expected message-change type and that it represents a received message, not a message sent by this account. It rejects system, spam, service, corrupt, empty, hidden, or sticker-only content. If a real sender and useful text or attachments remain, it returns an `InboundMessage`; otherwise it returns `None`.

**Call relations**: `catch_up` and `subscribe` call this for each message-change frame they receive from Spectrum. It acts like a sieve: raw stream events go in, and only meaningful incoming provider messages come out.

*Call graph*: called by 2 (catch_up, subscribe); 2 external calls (__init__, __init__).


### Slack message attribution
Adds safe bot attribution to outgoing Slack connector messages without letting those footers be mistaken for user input.

### `extensions/slack/ufo_ext_slack/attribution.py`

`domain_logic` · `Slack message sending and Slack event intake`

When this system sends a Slack message through a connector, that message is not rendered by the usual Slack-facing code. So this file supplies the missing “sent by this agent” footer itself. Instead of using a plain product name, it mentions the actual Slack bot user, so a human reader can click or contact the agent from the message.

The file also protects the inbound side. In Slack, a bot mention looks like text such as `<@BOTID>`. If the system adds that mention in a footer, Slack may later deliver the message as if the bot was mentioned. Without care, the agent could mistake its own attribution footer for a real user asking it something. This file avoids that by stripping known attribution text before checking whether a message truly addresses the bot.

Think of it like a letterhead: outgoing mail gets a return address, but the mailroom should not treat that printed return address as a new incoming request. The main pieces are small: one function recognizes connector calls that send Slack messages, one adds the bot-mention footer, one checks for real mentions after removing attribution, and one gathers all possible text from a Slack event, including nested block text where Slack may hide the footer.

#### Function details

##### `is_slack_send`  (lines 36–45)

```
def is_slack_send(provider: str, slug: str) -> bool
```

**Purpose**: This function decides whether a connector call is the kind that publishes a Slack message. It keeps this Slack-specific footer logic aligned with the connector tool’s own idea of which calls should be attributed.

**Data flow**: It receives a connector provider name and a connector action slug. It lowercases the slug, then checks that the provider is Slack, the slug refers to a message, and the slug contains one of the send-related words. It returns true only when all of those are true.

**Call relations**: This is the gatekeeper used before applying Slack attribution. Its job is to make sure the rest of this file only changes connector calls that really send Slack messages, so attribution is not added to unrelated connector actions.


##### `mention_attributed`  (lines 48–55)

```
def mention_attributed(arguments: dict[str, JsonValue], bot_user_id: str) -> dict[str, JsonValue]
```

**Purpose**: This function adds the attribution footer that mentions the bot user to outgoing Slack send arguments. It also avoids adding a second footer if the message is already marked.

**Data flow**: It receives the outgoing connector arguments and the Slack bot user ID. It builds the footer subject by placing that bot ID into the shared attribution mention template, then passes the arguments and subject to the connector attribution helper. The result is a new set of arguments with the footer appended where appropriate, or the original shape left effectively unchanged if attribution is already present.

**Call relations**: After a call has been recognized as a Slack send, this function prepares the actual outgoing payload. It relies on the connector package’s shared attribution builder so the footer format stays consistent with the rest of the connector system, while changing only the subject to be the Slack bot mention.

*Call graph*: 2 external calls (format, attributed_arguments).


##### `addressing_mention`  (lines 58–68)

```
def addressing_mention(text: str, bot_user_id: str) -> bool
```

**Purpose**: This function tells whether a piece of Slack text truly mentions the bot, ignoring mentions that appear only inside this system’s own attribution footer. It prevents the agent from treating its own “sent by” line as a user addressing it.

**Data flow**: It receives some text and the bot user ID. First it removes any known attribution footer text from the input. Then it looks for Slack’s bot mention form, `<@bot_user_id>`, in what remains. It returns true if that mention is still present, and false otherwise.

**Call relations**: Inbound Slack processing can use this when deciding whether a message is meant for the agent. It hands the text to the connector attribution stripper first, because the important question is not “does the bot ID appear anywhere?” but “does it appear outside our own footer?”

*Call graph*: 1 external calls (attribution_stripped).


##### `message_bodies`  (lines 71–77)

```
def message_bodies(event: Mapping[str, object]) -> tuple[str, ...]
```

**Purpose**: This function gathers every text string in a Slack message event where a bot mention might appear. It looks beyond the top-level message text because Slack messages can store visible text inside nested blocks and rich-text elements.

**Data flow**: It receives a Slack event represented like a dictionary. It reads the top-level `text` field, using an empty string if it is missing, then walks through the event’s `blocks` field to collect every nested string inside it. It returns all of those strings as a tuple.

**Call relations**: This function prepares inbound Slack message content for mention checks such as `addressing_mention`. To find text inside Slack’s nested block structure, it delegates the recursive walking to `_nested_strings`.

*Call graph*: calls 1 internal fn (_nested_strings).


##### `_nested_strings`  (lines 80–89)

```
def _nested_strings(value: object) -> Iterator[str]
```

**Purpose**: This helper walks through nested Slack message data and yields every string it can find. It is needed because Slack block data can be a mix of dictionaries, lists, and strings nested several levels deep.

**Data flow**: It receives any value. If the value is a string, it yields that string. If it is a mapping, it looks through all its values and repeats the same process. If it is a list, it repeats the process for each item. Other kinds of values are ignored.

**Call relations**: This is the low-level scanner used by `message_bodies`. `message_bodies` asks it to search the Slack `blocks` data so later mention-detection code can see text that is not present in the event’s simple top-level `text` field.

*Call graph*: called by 1 (message_bodies).


### `extensions/slack/ufo_ext_slack/hooks.py`

`orchestration` · `request handling`

This file is a small hook that runs just before an external tool is used. Its job is to notice one specific situation: the system is about to send a Slack message through the generic connector tool. The generic connector does not know the Slack bot user's ID, so it can only add a generic attribution footer. This Slack extension does know that ID, because the Slack surface stored it earlier after proving the workspace install. So this hook rewrites the message arguments before the connector sends them, adding a footer that mentions the actual bot user.

The important safety rule is that this hook must never become a reason a Slack message fails to send. A pre-tool hook is a gate: if it errors or takes too long, the tool call can be denied. Since the footer is only cosmetic, this file puts its store read inside a short timeout and treats every failure as harmless. If the bot ID cannot be read, is missing, or looks invalid, the hook returns no change. Then the connector continues normally and adds its own generic attribution.

In everyday terms, this file is like a mailroom clerk who tries to stamp an envelope with the right sender name. If the name lookup is slow or unavailable, the clerk does not hold the mail hostage; they just let it go with the default stamp.

#### Function details

##### `attribute_connector_send`  (lines 30–44)

```
async def attribute_connector_send(ctx: HookContext) -> HookOutcome
```

**Purpose**: This function runs before a tool call and checks whether the call is a Slack send through the connector. If so, and if the workspace's Slack bot user ID is available, it rewrites the tool input so the message text includes a footer mentioning that bot user.

**Data flow**: It receives a hook context containing the pending tool call. It checks whether the payload is a pre-tool-use event for the generic external connector and whether that connector call is specifically a Slack send. If not, it returns no change. If it is a Slack send, it asks for the mirrored Slack bot user ID, uses that ID to add or update the attribution footer in the call arguments, and returns a modified version of the tool input.

**Call relations**: This is the hook entry point for this file. When the pre-tool-use lifecycle reaches a connector call, it calls on _mirrored_self_user_id to safely fetch the Slack bot identity. If an identity is found, it hands the message arguments to the attribution helper that adds the mention, then returns ModifyInput so the connector tool receives the rewritten arguments instead of the original ones.

*Call graph*: calls 1 internal fn (_mirrored_self_user_id); 3 external calls (__init__, is_slack_send, mention_attributed).


##### `_mirrored_self_user_id`  (lines 47–61)

```
async def _mirrored_self_user_id(ctx: HookContext) -> str | None
```

**Purpose**: This helper tries to read the Slack bot user ID that was previously stored for the current workspace. It is deliberately cautious: if the read fails, takes too long, or returns something that does not look like a Slack bot user ID, it returns nothing.

**Data flow**: It receives the hook context, which gives access to the extension's scoped store. It tries, within a one-second timeout, to read the stored value under the known self-user-ID key. If the read succeeds and the value is a string matching the expected Slack bot user ID pattern, it returns that string. If anything goes wrong, it logs the kind of failure and returns None, leaving the caller to proceed without a Slack-specific footer.

**Call relations**: attribute_connector_send calls this helper only after it has identified a connector call as a Slack send. This helper does not contact Slack directly; it only reads the local extension store. Its result decides whether the higher-level hook can add the precise bot mention or must leave the message unchanged so the generic connector behavior can continue.

*Call graph*: called by 1 (attribute_connector_send); 3 external calls (timeout, match, log).


### Slack markup and tooling
Handles Slack mention conversion and provides guided administrator tools for connecting and searching Slack workspaces.

### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message ingest and reply sending`

Slack does not send messages exactly as people see them. A person mention may arrive as `<@U123>`, a channel as `<#C456|team-room>`, a broadcast as `<!here>`, and a labeled link as `<https://example.com|docs>`. If the system stored those raw forms, humans and language models would see IDs instead of names. This file is the translator at that border.

On the way in, `render_markup` rewrites known Slack entities into readable text such as `@Alex` or `#ufo-eng`. If a name cannot be safely found, it leaves the original Slack form alone rather than guessing. Links keep both the visible label and the URL when those differ, because a person sees the label but the agent may need the address.

On the way out, the problem is reversed. The agent writes readable text like `@Alex`, but Slack only sends a notification if it receives `<@U123>`. `mention_markup` changes only approved names into Slack mention codes. It avoids code blocks, URLs, existing angle-bracketed forms, email-like text, ambiguous names, broadcast words such as `here`, and too many mentions in one reply. This is like a careful receptionist: it will connect a call only when the name is clear and authorized, and otherwise leaves the message untouched.

#### Function details

##### `mentioned_users`  (lines 67–70)

```
def mentioned_users(text: str) -> frozenset[str]
```

**Purpose**: Finds the Slack user IDs that are explicitly mentioned in a raw Slack message. A caller can use those IDs to look up readable user names before rendering the message.

**Data flow**: It receives message text as Slack sent it. It asks the shared `_mentioned` helper to look only for user mention forms, then returns a frozen set of the user IDs it found. It does not change the text.

**Call relations**: This is the user-specific front door for mention scanning. When other code needs to know which people a Slack message refers to, it calls this function, which delegates the actual pattern search to `_mentioned` with the user marker.

*Call graph*: calls 1 internal fn (_mentioned).


##### `mentioned_channels`  (lines 73–75)

```
def mentioned_channels(text: str) -> frozenset[str]
```

**Purpose**: Finds the Slack channel IDs that are explicitly mentioned in a raw Slack message. A caller can use those IDs to look up channel names before showing or storing the message.

**Data flow**: It receives message text as Slack sent it. It asks `_mentioned` to look only for channel mention forms, then returns a frozen set of the channel IDs it found. The original text is left untouched.

**Call relations**: This is the channel-specific front door for mention scanning. When other code needs channel references from a Slack message, it calls this function, which reuses `_mentioned` with the channel marker.

*Call graph*: calls 1 internal fn (_mentioned).


##### `_mentioned`  (lines 78–83)

```
def _mentioned(text: str, kind: str) -> frozenset[str]
```

**Purpose**: Does the shared work of scanning Slack markup for mentioned IDs of one requested kind, such as users or channels. It exists so user and channel scanning follow the same rules.

**Data flow**: It receives raw Slack text and a kind marker, for example `@` for users or `#` for channels. It searches for Slack entity markup, keeps only entities of that kind with a non-empty ID, and returns those IDs as a frozen set.

**Call relations**: Both `mentioned_users` and `mentioned_channels` call this helper. They decide what kind of mention they want, and `_mentioned` performs the common search and filtering.

*Call graph*: called by 2 (mentioned_channels, mentioned_users).


##### `render_markup`  (lines 86–96)

```
def render_markup(text: str, names: Mapping[str, str]) -> str
```

**Purpose**: Turns Slack’s encoded message entities into the words a reader would expect to see. This is used when admitting incoming Slack text so stored conversations are readable by people and by the model.

**Data flow**: It receives raw Slack message text plus a mapping from Slack IDs to readable names. It walks through each Slack entity it recognizes and replaces it with a readable form, using the mapping, Slack’s label, or the original markup when no safe name exists. It returns the rewritten text and does not modify the mapping.

**Call relations**: This function sits on the inbound path. Other code gives it a Slack message and known names; it uses the file’s entity-rendering rules to produce the version that downstream readers, transcripts, titles, and model prompts should all see.


##### `unescape`  (lines 99–108)

```
def unescape(text: str) -> str
```

**Purpose**: Changes Slack’s escaped character strings, such as `&lt;`, back into the characters the original member typed. It is intentionally separate from mention rendering because unescaping someone else’s quoted words can be unsafe.

**Data flow**: It receives text containing Slack escape sequences. It replaces `&lt;`, `&gt;`, and `&amp;` with `<`, `>`, and `&`, then returns the changed text. It has no side effects.

**Call relations**: This helper is used only when the caller knows it is safe to restore the original member’s own characters. It is not part of `render_markup`, because inbound entity rendering may also be applied to text from bystanders whose escaped characters should remain protected.


##### `mention_key`  (lines 111–115)

```
def mention_key(name: str) -> str
```

**Purpose**: Creates a standard lookup form for a person’s name. It makes name matching forgiving about capitalization and extra spaces.

**Data flow**: It receives a name string. It collapses runs of whitespace into single spaces, changes the result to a case-insensitive form, and returns that normalized key.

**Call relations**: This is the shared name-normalizing rule for outbound mentions. `mention_index` uses it when building the approved name-to-ID table, and `_mention_at` uses it when checking whether text after an `@` matches one of those approved names.

*Call graph*: called by 2 (_mention_at, mention_index).


##### `mention_index`  (lines 118–132)

```
def mention_index(names: Mapping[str, str]) -> dict[str, str]
```

**Purpose**: Builds the safe lookup table used to turn readable `@Name` text into Slack notification markup. It deliberately refuses names that are ambiguous or look like Slack broadcast words.

**Data flow**: It receives a mapping from Slack user IDs to readable names. For each name, it builds a normalized key with `mention_key`, groups all IDs that claim the same key, drops empty names and broadcast words, and returns only keys that point to exactly one ID.

**Call relations**: This function prepares data for `mention_markup`. Before an outgoing reply can safely convert `@Alex` into `<@U123>`, callers build this index so only unique, vouched-for names are eligible.

*Call graph*: calls 1 internal fn (mention_key).


##### `mention_markup`  (lines 135–166)

```
def mention_markup(text: str, ids: Mapping[str, str], limit: int=MENTION_MARKUP_MAX) -> str
```

**Purpose**: Converts approved readable mentions in outgoing text, such as `@Alex`, into Slack’s `<@ID>` form so Slack will notify the person. It is careful to leave unrelated `@` signs alone, such as those in URLs, email addresses, code, or unapproved names.

**Data flow**: It receives outgoing text, a safe name-to-ID mapping, and a maximum number of mentions to convert. It first finds spans that must be skipped, then scans each `@`. For each possible mention, it checks that it is not inside a skipped span, not part of another word or address, not past the conversion limit, and that `_mention_at` can match an approved name. It returns a new string with only those safe mentions replaced by Slack mention codes.

**Call relations**: This is the main outbound rewrite step. Reply-sending code calls it after the agent has written human-readable text. During its scan it calls `_mention_at` to decide whether the words after a particular `@` name exactly one approved person.

*Call graph*: calls 1 internal fn (_mention_at); 1 external calls (finditer).


##### `_mention_at`  (lines 169–182)

```
def _mention_at(text: str, start: int, ids: Mapping[str, str]) -> tuple[int, str] | None
```

**Purpose**: Checks whether the text immediately after an `@` spells an approved person’s name, and if so where that name ends. It chooses the longest matching name so fuller names win over shorter partial matches.

**Data flow**: It receives the full text, the position just after an `@`, and the approved name-to-ID map. It looks only a limited distance and only on the same line, rejects names that start with whitespace, tries up to a few words, trims sentence-ending punctuation, normalizes each candidate with `mention_key`, and returns the end position plus the matching ID. If nothing matches, it returns nothing.

**Call relations**: `mention_markup` calls this helper each time it finds an `@` that might be a mention. `_mention_at` performs the focused name-matching work and hands back enough information for `mention_markup` to replace exactly the right slice of text.

*Call graph*: calls 1 internal fn (mention_key); called by 1 (mention_markup); 2 external calls (islice, finditer).


##### `_entity`  (lines 185–201)

```
def _entity(match: re.Match[str], names: Mapping[str, str]) -> str
```

**Purpose**: Converts one recognized Slack entity into readable text. It knows the different display rules for people, channels, broadcasts, and links.

**Data flow**: It receives one regular-expression match from Slack markup plus the known ID-to-name mapping. For a user or channel, it uses the known name or Slack-provided label when available; for a broadcast, it returns forms like `@here` only for known broadcast names; for a link, it returns either the URL alone or `label (URL)`. If it cannot safely name an entity, it preserves the original or closest honest form.

**Call relations**: This is the per-entity worker used by the inbound rendering flow. `render_markup` applies it to each matched Slack entity so the whole message becomes readable without inventing names.


### `extensions/slack/ufo_ext_slack/tools.py`

`orchestration` · `Slack setup and Slack tool request handling`

Slack needs several things before UFO can work inside it: a bot token, a signing secret, the Slack team identity, and proof that Slack can reach this UFO deployment over the public internet. This file provides tools that walk a user through that process safely.

There are two ways to connect Slack. The OAuth path is the simpler “Add to Slack” route, used when this UFO deployment already has its own Slack app configured. The manifest path is the “bring your own Slack app” route: UFO prints a ready-made Slack app manifest, the user creates the app in Slack, and the secrets are collected privately through credential slots instead of being typed into chat.

The main setup tool, `slack_connect`, works like a status checker and installer in one. It reports whether Slack is not configured, not installed, pending, or connected. “Pending” means UFO knows the Slack workspace identity, but Slack has not yet sent a verified request to this deployment. “Connected” means Slack has reached UFO and the request signature matched the saved signing secret.

The file also exposes `slack_app_manifest`, which prints the exact Slack app YAML to paste into Slack, and `slack_channels`, which searches channels and direct messages using the bot token. Search results are marked as untrusted because they come from Slack workspace content written by users.

#### Function details

##### `_events_url`  (lines 139–140)

```
def _events_url(public_base_url: str) -> str
```

**Purpose**: Builds the public web address Slack should call when it sends events to UFO. It takes the deployment’s public base URL and adds the Slack surface path.

**Data flow**: It receives a public base URL, removes any trailing slash, appends `/surface/slack`, and returns the finished URL as text. It does not read or change any stored state.

**Call relations**: The setup flow calls this when it needs to tell Slack where to send events. `slack_connect_handler` uses it for status responses, and `slack_manifest_handler` uses it when filling in the Slack app manifest.

*Call graph*: called by 2 (slack_connect_handler, slack_manifest_handler).


##### `_state`  (lines 143–145)

```
def _state(state: str, hint: str, events_url: str | None, **extra: object) -> ToolResult
```

**Purpose**: Packages a Slack setup status into the standard tool response format. It gives the agent and user a simple JSON message such as `not_configured`, `pending`, or `connected`, plus a human-readable hint.

**Data flow**: It receives a state name, a hint, an optional events URL, and any extra details. It turns those into a JSON string, wraps that string as text content, and returns it as a tool result.

**Call relations**: This is the shared response helper for the install flow. `slack_connect_handler`, `_oauth_link`, and `_derive_manifest_identity` use it whenever they need to report where setup stands or what the user should do next.

*Call graph*: called by 3 (_derive_manifest_identity, _oauth_link, slack_connect_handler); 3 external calls (__init__, __init__, dumps).


##### `slack_connect_handler`  (lines 148–189)

```
async def slack_connect_handler(ctx: ToolContext, args: SlackConnectInput) -> ToolResult
```

**Purpose**: Runs the main Slack connection workflow. A user can call it repeatedly before, during, and after setup, and it will either continue installation or report the current connection state.

**Data flow**: It reads the requested install method, the deployment’s public URL, saved Slack credentials, saved Slack identity data, and the verification marker in blob storage. If no identity exists, it either creates an OAuth install link or tries to prove identity from manifest-provided credentials. Once identity exists, it binds the Slack team to this UFO workspace, checks whether Slack has sent a verified request, and returns a JSON status result.

**Call relations**: This is the top-level handler behind the `slack_connect` tool. It delegates URL building to `_events_url`, OAuth setup to `_oauth_link`, manifest setup to `_derive_manifest_identity`, status formatting to `_state`, identity reading to the Slack surface helper, and final reachability checking to `_verified`.

*Call graph*: calls 5 internal fn (_derive_manifest_identity, _events_url, _oauth_link, _state, _verified); 2 external calls (read_identity, slack_installation_id).


##### `_oauth_link`  (lines 192–222)

```
async def _oauth_link(ctx: ToolContext, events_url: str | None) -> ToolResult
```

**Purpose**: Creates an “Add to Slack” link for the easy install path. It is used when this UFO deployment already has Slack app credentials configured.

**Data flow**: It checks environment variables to see whether the deployment has a Slack client ID and client secret. It checks that the speaker is an administrator, confirms there is a public base URL, asks the tool context to seal a short-lived credential authorization handoff, builds Slack’s authorization URL, and returns it inside a status response.

**Call relations**: `slack_connect_handler` calls this when no Slack identity has been saved yet and the user chose the OAuth path. It hands off to the tool context for credential authorization and to Slack surface helpers for the client ID, redirect URI, and final Slack authorization URL.

*Call graph*: calls 3 internal fn (begin_credential_authorization, speaker_is_admin, _state); called by 1 (slack_connect_handler); 3 external calls (slack_authorize_url, slack_client_id, slack_oauth_redirect_uri).


##### `_derive_manifest_identity`  (lines 225–259)

```
async def _derive_manifest_identity(ctx: ToolContext, events_url: str | None) -> SlackIdentity | ToolResult
```

**Purpose**: Completes the bring-your-own-Slack-app path after the user has privately supplied the bot token and signing secret. It proves which Slack workspace and bot those credentials belong to.

**Data flow**: It checks whether the required credential slots are filled. If any are missing, it returns a setup status explaining what to collect. If both are present, it reads the bot token, verifies that the speaker is an administrator, calls Slack identity resolution, and returns either a Slack identity object or a clear error status if Slack rejects the token or returns unusable identity data.

**Call relations**: `slack_connect_handler` calls this when the manifest install method is being used and no saved identity is available yet. It uses `_state` for user-facing setup messages and `_token_diagnosis` to turn Slack token errors into understandable advice.

*Call graph*: calls 3 internal fn (speaker_is_admin, _state, _token_diagnosis); called by 1 (slack_connect_handler); 1 external calls (__init__).


##### `_verified`  (lines 262–281)

```
async def _verified(ctx: ToolContext) -> bool
```

**Purpose**: Checks whether Slack has successfully contacted this UFO deployment using the current signing secret. This separates “we have credentials” from “Slack can actually reach and trust this server.”

**Data flow**: It looks for a saved URL-verification marker in blob storage. If the marker is missing, broken, or does not match the fingerprint of the current signing secret, it returns `false`. If the marker is valid and matches the current secret, it returns `true`.

**Call relations**: `slack_connect_handler` calls this near the end of setup. Its answer decides whether the user sees `pending` or `connected` after Slack identity has been proven.

*Call graph*: called by 1 (slack_connect_handler); 2 external calls (loads, signing_secret_fingerprint).


##### `slack_manifest_handler`  (lines 284–299)

```
async def slack_manifest_handler(ctx: ToolContext, args: SlackManifestInput) -> ToolResult
```

**Purpose**: Produces the ready-to-paste Slack app manifest for the manual setup path. This saves the user from hand-copying scopes, event names, and callback URLs.

**Data flow**: It receives the desired bot display name, checks that the name is short and plain enough for Slack, reads the deployment’s public base URL, builds the Slack events and interactivity URLs, fills them into the manifest template, and returns the manifest as text.

**Call relations**: This is the handler behind the `slack_app_manifest` tool. It uses `_events_url` to make the Slack request URL and returns a tool result directly to the conversation so the user can paste it into Slack’s app creation page.

*Call graph*: calls 1 internal fn (_events_url); 3 external calls (__init__, __init__, match).


##### `slack_channels_handler`  (lines 302–325)

```
async def slack_channels_handler(ctx: ToolContext, args: SlackChannelsInput) -> ToolResult
```

**Purpose**: Searches the connected Slack workspace for conversations the agent may need to use. It can find channels, group direct messages, and one-to-one direct messages by names, topics, purposes, or people.

**Data flow**: It reads the saved Slack bot token and saved Slack identity. If either is missing, it stops with an error telling the user to finish connection first. Then it runs a Slack conversation search with the bot token, bot user ID, and query text, turns the found conversations into JSON, and returns them as untrusted text because the content comes from Slack users.

**Call relations**: This is the handler behind the `slack_channels` runtime tool. After setup is complete, the agent calls it when it has a channel name or person rather than a Slack conversation ID. It relies on Slack surface identity reading and the `SlackConversationSearch` helper to do the actual Slack API paging and matching.

*Call graph*: 5 external calls (__init__, __init__, __init__, dumps, read_identity).


##### `_token_diagnosis`  (lines 328–334)

```
def _token_diagnosis(error: str) -> str
```

**Purpose**: Turns Slack authentication error codes into plain advice. It helps users understand whether they likely copied the wrong bot token or hit another Slack `auth.test` failure.

**Data flow**: It receives a Slack error string. If the error is one of the known token rejection cases, it returns a message telling the user to re-copy the Bot User OAuth Token. Otherwise, it returns a more general Slack authentication failure message including the error text.

**Call relations**: `_derive_manifest_identity` calls this when Slack rejects the bot token during the manifest setup path. The returned message is placed into the setup status response shown to the user.

*Call graph*: called by 1 (_derive_manifest_identity).
