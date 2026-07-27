# User-facing surfaces receive and normalize inbound requests  `stage-4`

This stage is the system’s set of front desks after startup. It is where messages first arrive from Slack, the browser chat, the terminal client, operator tools, or debugger routes. Each front desk checks that the request is allowed, figures out who the outside user is, connects that user to the right workspace and conversation, and reshapes the message into the common form the core conversation engine understands.

The Slack, web, terminal, and operator routes are the visible doors. Slack verifies Slack requests and handles messages, installs, button clicks, replies, and files. The terminal route accepts command-line messages and streams simple updates back. The web route lets signed-in users send chat messages, receive live answers, and view recent spending.

The small surfaces package file only makes this folder importable. The important shared piece is the surface bridge in core/src/ufo/ext/surface.py. It gives these trusted doors controlled access to core abilities: finding people, opening conversations, admitting messages, streaming responses, and sending final results back out.

## Sub-stages

- [Slack, web, terminal, and operator routes](stage-4.1.md) `stage-4.1` — 3 files

## Files in this stage

### Surface Bridge Package
Defines the importable surfaces package and the core bridge that trusted external surfaces use to admit and deliver conversation events.

### `core/src/ufo/surfaces/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes the `ufo.surfaces` namespace available, so modules inside the `surfaces` folder can be imported using normal Python import paths. Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the workshop find the drawer reliably. Because the file is empty, it does not run setup code, expose shortcut imports, or change how the surface-related modules behave. If it were missing in environments that expect traditional Python packages, imports from this folder could fail or become less predictable.


### `core/src/ufo/ext/surface.py`

`orchestration` · `request handling and background writeback delivery`

A surface is the place where a human talks to UFO, such as Slack or a web page. This file defines the safe doorway that those surfaces use to reach into the core system. Without it, each surface would have to know too much about the database, blob storage, credentials, conversation ownership, and reply delivery rules, which would make identity and tenant isolation easy to get wrong.

The main piece is SurfaceContext. Think of it as a trusted service desk pass: it lets a surface prove who a person is, link that outside identity to a workspace member, create or find the right conversation, admit a message into the durable turn queue, read files and transcripts for debug views, and fetch workspace credentials when the surface declared it is allowed to do so.

The file supports two reply styles. Live surfaces keep a connection open and stream frames as the turn runs. Durable surfaces, like Slack, cannot rely on a live stream, so this file also includes WritebackPoller. The poller repeatedly looks for finished turns that need delivery, claims them so only one worker sends them, posts the reply, attaches files, records success, and retries failures with backoff.

The important theme is controlled privilege: surfaces are trusted, but every operation is still tied to a workspace and guarded by explicit lookup, ownership checks, and declared capabilities.

#### Function details

##### `MemberAdmitter.admit`  (lines 102–110)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: This protocol method describes the one core action a surface needs to submit a member’s message into a conversation. A concrete implementation turns the incoming text into a queued turn that the engine will run.

**Data flow**: It receives a conversation id, message text, optional duplicate-prevention key, optional turn context, and the speaking member id. It returns the id of the turn that was created or reused.

**Call relations**: SurfaceContext.admit delegates to this method whenever Slack, web, sample, or UFO surfaces accept a user message. The protocol keeps surfaces independent from the actual queue implementation.


##### `TurnTailer.tail`  (lines 119–119)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This protocol method describes how a live surface reads a turn’s live output frames. It lets the surface stream progress to a waiting user without touching the internal hub directly.

**Data flow**: It receives a turn id and an optional cursor saying where to resume. It yields cursor-and-frame pairs until the turn finishes or stops streaming.

**Call relations**: SurfaceContext.tail delegates to this method for live surfaces and debugger views. The concrete tailer owns the connection to the process hub.


##### `workspace_key`  (lines 135–143)

```
def workspace_key(conversation_id: UUID, rel: str) -> str
```

**Purpose**: This helper turns a user-facing relative file path into the internal blob-storage key for a conversation workspace file. It also blocks unsafe paths that try to escape the workspace folder.

**Data flow**: It receives a conversation id and a relative path. It cleans and validates the path, then returns a blob key under conversations/<id>/workspace/; if the path is absolute, empty, or contains parent-directory jumps, it raises an error.

**Call relations**: SurfaceContext.write_workspace_file uses it before saving uploaded files, and SurfaceContext.read_workspace_file uses it before reading them. It is the path safety gate for workspace file access.

*Call graph*: called by 2 (read_workspace_file, write_workspace_file); 1 external calls (PurePosixPath).


##### `ConversationSummary._aware_utc`  (lines 201–204)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This validator makes conversation timestamps consistently timezone-aware. That avoids confusing “local time versus UTC” behavior when summaries are serialized or displayed.

**Data flow**: It receives a datetime or None. None passes through; a datetime that already has a timezone is kept, and one without a timezone is marked as UTC.

**Call relations**: It runs automatically when ConversationSummary objects are built in SurfaceContext.list_conversations. Callers get normalized timestamps without needing to remember this detail.

*Call graph*: 1 external calls (replace).


##### `LedgerEntry._aware_utc`  (lines 218–219)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes accounting timestamps consistently timezone-aware. It prevents ledger rows from carrying ambiguous times.

**Data flow**: It receives a datetime. If it has no timezone, the function marks it as UTC; otherwise it leaves it unchanged.

**Call relations**: It runs automatically when LedgerEntry objects are created in SurfaceContext.turn_detail. Debug and accounting views then receive predictable timestamp data.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 241–246)

```
def _fulfilled_marker_key(workspace_id: UUID, sealed: str, slot: str) -> str
```

**Purpose**: This helper builds the blob key for a small marker that says a particular credential prompt slot has already been answered. The marker is per sealed request and per slot, so one answered secret does not hide other unanswered secrets.

**Data flow**: It receives a workspace id, a sealed credential request string, and a slot name. It hashes the sealed request and returns a stable blob key for that slot’s fulfillment marker.

**Call relations**: SurfaceContext.credential_prompt_pending checks this key to decide whether to keep showing a prompt. SurfaceContext.fulfill_credential_request writes to this key after storing the secret.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_email_domain`  (lines 249–253)

```
def _email_domain(email: str) -> str
```

**Purpose**: This helper extracts the domain part of an email address in a cautious way. Badly shaped emails return an empty domain, so they cannot accidentally pass a domain check.

**Data flow**: It receives an email string. It trims and lowercases it, splits at the last @, and returns the domain only if both local name and domain are present.

**Call relations**: SurfaceContext.join_member uses it to decide whether a verified email belongs to the workspace’s domain. SurfaceContext.is_operator_workspace uses it to recognize the operator’s own workspace.

*Call graph*: called by 2 (is_operator_workspace, join_member).


##### `_earliest_agent`  (lines 256–271)

```
async def _earliest_agent(workspace_id: UUID) -> UUID
```

**Purpose**: This function finds the default agent for a workspace: the oldest agent row. It is used when no surface-specific agent binding exists.

**Data flow**: It receives a workspace id, reads the agent table inside the workspace boundary, and returns the first agent id by creation time and id. If the workspace has no agent, it raises an error because the workspace setup is broken.

**Call relations**: _bind_surface_installation uses it when creating a new surface binding, and SurfaceContext._surface_agent falls back to it for conversations without a binding.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 274–306)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str) -> None
```

**Purpose**: This function records that an external surface installation, such as a Slack team, belongs to a workspace. It gives later incoming shared requests a way to resolve which workspace they belong to.

**Data flow**: It receives a workspace id, surface name, and installation id. It validates the id, chooses the workspace’s default agent for new bindings, inserts or updates the binding, and raises SurfaceInstallationConflict if the same installation is already owned elsewhere.

**Call relations**: SurfaceContext.bind_installation calls it during a surface’s own setup flow, and SurfaceInstallationAccess.bind calls it for tools that register installations. It is the single write path for installation bindings.

*Call graph*: calls 1 internal fn (_earliest_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.credential`  (lines 330–333)

```
async def credential(self, slot: str) -> str
```

**Purpose**: This method reads a declared workspace credential for a trusted surface. It is used for secrets such as Slack signing tokens that the surface needs in-process.

**Data flow**: It receives a credential slot name. If no credential store is configured, it raises an error; otherwise it reads the secret for this context’s workspace and returns it.

**Call relations**: Slack surface code calls it when verifying requests, posting messages, handling interactions, attaching files, and proving identity. The context keeps the read tied to the current workspace.

*Call graph*: called by 8 (_ctx_signing_secret, _identity, _post_ephemeral, _run_identity_proof, attach, ingest, interactive, post).


##### `SurfaceContext.credential_prompt_pending`  (lines 335–349)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: This method decides whether a user should still be shown a credential prompt for one slot. It prevents already answered, expired, tampered, or wrong-workspace prompts from reappearing.

**Data flow**: It receives a sealed request and slot name. It opens and verifies the sealed request, checks that it belongs to this workspace and names the slot, then returns true only if the fulfillment marker is not present.

**Call relations**: Surface renderers use this check before displaying credential prompts. It relies on _fulfilled_marker_key to look up the per-slot marker.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 351–361)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: This method opens a sealed credential handoff and returns the claims inside it. It is used when a browser callback needs to know which workspace, member, and slot the authorization belongs to.

**Data flow**: It receives a sealed string. If no credential store exists, it raises an error; otherwise it verifies and decrypts the sealed request and returns its state, or lets the credential error propagate.

**Call relations**: Slack’s OAuth callback uses it to recover the original credential request. The method delegates the actual seal verification to the credential subsystem.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 363–388)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: This method stores one requested credential value after proving that the right member is fulfilling the right sealed request. It is the safe write path from a surface prompt into the encrypted credential store.

**Data flow**: It receives a sealed request, slot, secret value, and member id. It verifies the seal, workspace, member, and slot, writes the value to the credential store, and writes a blob marker so that prompt is not shown again.

**Call relations**: Slack OAuth callbacks and UFO’s own surface use it after a user supplies or authorizes a secret. It uses _fulfilled_marker_key so prompt rendering and fulfillment agree.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 2 (oauth_callback, _fulfill_secret); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 390–396)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: This method lets a surface bind its external installation identity to the current workspace. For example, a Slack OAuth callback can record which Slack team maps to this workspace.

**Data flow**: It receives an installation id and passes the current workspace id and surface name to the shared binding function. The database is updated or a conflict is raised.

**Call relations**: Slack’s OAuth callback calls it after installation succeeds. The real database work is centralized in _bind_surface_installation.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.public_base_url`  (lines 399–402)

```
def public_base_url(self) -> str | None
```

**Purpose**: This property returns the deployment’s public base URL if one is configured. Surfaces use it to build callback or download links that external systems can reach.

**Data flow**: It reads the value stored in the context and returns it, or returns None when no public URL was configured.

**Call relations**: Surface code can read this alongside other context capabilities. SurfaceContext.artifact_link also depends on the same stored value.


##### `SurfaceContext.artifact_link`  (lines 404–415)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: This method creates a temporary public download link for a shared artifact when the surface cannot upload the file directly. If public links are not configured, it returns None so the surface can fall back to naming the file only.

**Data flow**: It receives a SharedArtifact. If both a token secret and public base URL exist, it creates an expiring signed token for the blob key and filename and returns a full download URL; otherwise it returns None.

**Call relations**: Slack uses it for oversized artifact messages. The generated token is understood by the web artifact download route elsewhere in the system.

*Call graph*: called by 1 (_oversize_link_line); 2 external calls (now, mint_artifact_token).


##### `SurfaceContext._identity_member`  (lines 417–430)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: This private method looks up which workspace member is linked to an external surface identity. It is the common read used by identity-linking helpers.

**Data flow**: It receives a surface name and external id. It queries the surface_identity table for this workspace and returns the member id, or None if there is no link.

**Call relations**: SurfaceContext.linked_member uses it for this surface, while SurfaceContext.adopt_identity uses it to read a peer surface’s identity before copying the link.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 432–433)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: This method checks whether this surface already knows an outside user as a workspace member. It lets a surface avoid creating duplicate identities on later messages.

**Data flow**: It receives an external id and asks _identity_member for this context’s surface. It returns the linked member id or None.

**Call relations**: Sample, Slack, UFO, and web surfaces call it during authentication or message ingest. It is the first, non-mutating identity lookup.

*Call graph*: calls 1 internal fn (_identity_member); called by 6 (_surface_ingest, _surface_live_admit, _resolve_member, interactive, channel, _authenticate).


##### `SurfaceContext._owner_email`  (lines 435–449)

```
async def _owner_email(self) -> str | None
```

**Purpose**: This private method reads the workspace owner’s email, defined here as the earliest member. That email’s domain is used as the workspace’s own domain.

**Data flow**: It reads the oldest member row for this workspace and returns its email, or None if no member exists.

**Call relations**: SurfaceContext.join_member uses it to decide whether a new verified email can join the workspace. SurfaceContext.is_operator_workspace uses it to recognize the operator domain.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.is_operator_workspace`  (lines 451–459)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: This method tells whether the current workspace belongs to UFO’s operator organization. It gates operator-only display details so they do not appear in customer workspaces.

**Data flow**: It reads the owner email, extracts its domain, and compares it to the fixed operator domain. It returns false if there is no owner.

**Call relations**: Slack’s post rendering calls it before adding operator-only footer or debug information. It uses _owner_email and _email_domain.

*Call graph*: calls 2 internal fn (_owner_email, _email_domain); called by 1 (post).


##### `SurfaceContext.adopt_identity`  (lines 461–484)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: This method links the current surface’s external id to a member already known by another surface. It lets one human keep the same member identity across, for example, CLI and web.

**Data flow**: It receives a peer surface name and external id. It looks up the peer’s member link, inserts the same member link for this surface if found, tolerates a race where another request inserted it first, and returns the member id or None.

**Call relations**: The sample live admit flow calls it when adopting an identity. It builds on _identity_member and writes to surface_identity.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 486–515)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: This method links this surface’s external id to an existing workspace member with a matching email. It does not create a member if none exists.

**Data flow**: It receives an external id and email. It searches this workspace for a member with that email, inserts the surface identity link if found, tolerates duplicate-link races, and returns the member id or None.

**Call relations**: It is used directly by several surfaces and also by SurfaceContext.join_member. It is the simple “known email to known member” path.

*Call graph*: called by 4 (join_member, _surface_ingest, channel, _authenticate); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 517–534)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: This method links an external id to a member, creating a new member first if the verified email belongs to the workspace’s domain. It lets teammates join on first contact through a trusted surface.

**Data flow**: It first tries link_member. If no member exists, it compares the email domain with the owner’s domain; on a match it creates the member, then links and returns it. Foreign or malformed domains return None.

**Call relations**: Slack’s member resolver calls it after Slack has verified the user’s email. It depends on link_member, _owner_email, _email_domain, and create_member.

*Call graph*: calls 3 internal fn (_owner_email, link_member, _email_domain); called by 1 (_resolve_member); 2 external calls (workspace_tx, create_member).


##### `SurfaceContext._conversation_lookup`  (lines 536–541)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: This private helper builds the database query for finding a conversation by this surface’s queue key. A queue key is the surface’s own channel, thread, or session identifier.

**Data flow**: It receives a queue key and returns a query selecting the conversation id and member id for this workspace and surface.

**Call relations**: SurfaceContext.find_conversation and SurfaceContext.conversation_for both use this shared query so lookup rules stay identical.

*Call graph*: called by 2 (conversation_for, find_conversation); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 543–549)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: This method finds an existing conversation for a surface queue key without creating one. It is useful when a surface needs to check whether a thread is already participating.

**Data flow**: It receives a queue key, runs the shared conversation lookup, and returns the conversation id or None.

**Call relations**: Slack debug, participation, and interactive flows call it before deciding what a request may do. It uses _conversation_lookup inside a workspace transaction.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 3 (_debug_link, _participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_for`  (lines 551–594)

```
async def conversation_for(self, queue_key: str, member_id: UUID | None) -> UUID
```

**Purpose**: This method gets or creates the conversation for a surface queue key. It is the main way incoming messages are attached to the right durable conversation.

**Data flow**: It receives a queue key and optional member id. If the conversation exists, it may claim a previously memberless conversation for that member. If it does not exist, it chooses the surface’s agent, inserts a new conversation, handles creation races, and returns the conversation id.

**Call relations**: Sample, Slack, UFO, and web surfaces call it before admitting messages. It uses _conversation_lookup, _surface_agent, database inserts and updates, and race logging.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat); 5 external calls (insert, update, workspace_tx, log, uuid4).


##### `SurfaceContext._surface_agent`  (lines 596–608)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: This private method chooses which agent should run turns for this surface’s conversations. A surface installation can bind to a specific agent; otherwise the workspace’s earliest agent is used.

**Data flow**: It checks for a surface_installation row for this workspace and surface. If one exists, it returns its agent id; if not, it falls back to _earliest_agent.

**Call relations**: SurfaceContext.conversation_for calls it when creating a new conversation. This keeps agent selection out of individual surface implementations.

*Call graph*: calls 1 internal fn (_earliest_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.admit`  (lines 610–632)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: This method submits a user message into the engine as a turn in an existing conversation. It is the write side of a surface: after identity and conversation are resolved, the message enters the durable queue.

**Data flow**: It receives the conversation id, message body, optional idempotency key, optional turn context, and speaking member id. It forwards those values to the injected MemberAdmitter and returns the resulting turn id.

**Call relations**: Sample, Slack, UFO, and web surfaces call it after conversation_for. The actual admission and duplicate handling live behind the injected _admitter.

*Call graph*: called by 6 (_surface_ingest, _surface_live_admit, ingest, interactive, channel, chat).


##### `SurfaceContext.connect_url`  (lines 634–640)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: This method starts an installed-service connection handoff for a member from a terminal turn. It returns the URL the surface should send the user to.

**Data flow**: It receives a turn id and member id. It loads the configured connect flow, turns unavailable configuration into a connect-request error, and asks ConnectHandoff to authorize the handoff.

**Call relations**: Slack interactive handling and web event streaming call it when a terminal frame asks the user to connect an external provider.

*Call graph*: called by 2 (interactive, _events); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.admitted_body`  (lines 642–667)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: This method finds the message body that actually won for an idempotency key. It helps a surface update only the button or answer affordance whose click was accepted.

**Data flow**: It receives an idempotency key. It first looks for a completed turn with that key, then for an inbound queued message with that key, and returns the stored body or None.

**Call relations**: Slack interactive handling calls it after racing user interactions. It reads both turn and inbound_message tables to cover both admission states.

*Call graph*: called by 1 (interactive); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 669–683)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: This method returns the member who owns the conversation containing a turn. Live surfaces use it as an authorization check before letting someone stream that turn.

**Data flow**: It receives a turn id, joins turn to conversation inside this workspace, and returns the conversation member id or None if the turn is not found.

**Call relations**: The sample live surface and web stream route call it before tailing frames. It helps prevent one member from watching another member’s turn.

*Call graph*: called by 2 (_surface_live_admit, stream); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 685–702)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: This method finds the newest turn in a conversation. It is useful when a surface reconnects or needs to know what turn is currently relevant.

**Data flow**: It receives a conversation id, reads the turn table ordered by descending sequence number, and returns the most recent turn id or None.

**Call relations**: Slack participation logic and the UFO channel use it when resuming or inspecting conversation activity.

*Call graph*: called by 2 (_participating_conversation, channel); 2 external calls (select, workspace_tx).


##### `SurfaceContext.tail`  (lines 704–707)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This method streams live frames for one turn through the injected tailer. It gives live surfaces a single safe way to read ongoing output.

**Data flow**: It receives a turn id and optional resume cursor, then returns the async stream produced by the tailer.

**Call relations**: Debugger, sample, UFO, and web event routes call it after their own access checks. It delegates to TurnTailer.tail.

*Call graph*: called by 4 (_events, _surface_frames, channel, _events).


##### `SurfaceContext.spend_rollup`  (lines 709–713)

```
async def spend_rollup(self, window_seconds: int) -> SpendReport
```

**Purpose**: This method reads recent workspace spending totals. A surface can use it to show a spend dashboard or usage indicator.

**Data flow**: It receives a time window in seconds, opens a workspace transaction, and returns a SpendReport computed by SpendRollup.

**Call relations**: The sample live admit flow and web spend route call it for user-facing spend views.

*Call graph*: called by 2 (_surface_live_admit, spend); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 715–721)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: This method saves a streamed file into a conversation’s workspace folder before a turn runs. The sandbox can then see the file as part of its working area.

**Data flow**: It receives a conversation id, relative path, and async byte chunks. It validates and converts the path with workspace_key, then streams the bytes into blob storage without buffering the whole file.

**Call relations**: Sample ingest and Slack file download code call it when users attach files. workspace_key provides the path safety check.

*Call graph*: calls 1 internal fn (workspace_key); called by 2 (_surface_ingest, _download_files).


##### `SurfaceContext.list_conversations`  (lines 723–772)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: This method returns a bounded list of workspace conversations for read-only views. It includes conversations from all surfaces, not just the current one.

**Data flow**: It receives an optional limit. It builds an activity summary from turns, joins conversations and member email, orders by newest activity, and returns ConversationSummary objects.

**Call relations**: The debugger surface calls it to show conversation lists. ConversationSummary normalizes timestamps as each item is created.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 774–790)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: This method returns the most recent turns in a conversation in chronological order. It gives debug views the durable history of a conversation.

**Data flow**: It receives a conversation id and optional limit. It reads the latest rows in reverse order for efficiency, converts each row into a Turn record, and returns them oldest-first.

**Call relations**: The debugger conversation-turns endpoint calls it. It relies on _turn_query for the selected columns and _turn_record for typed conversion.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 1 (conversation_turns); 1 external calls (workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 792–842)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: This method returns a detailed view of one turn, including its accounting rows and child subagent turns. It is meant for inspection and debugging.

**Data flow**: It receives a turn id. It reads the turn, its children, and ledger rows; if no turn exists, it returns None. Otherwise it packages everything into a TurnDetail object.

**Call relations**: Debugger stream and turn pages call it. It uses _turn_query, _turn_record, and LedgerEntry to assemble the view.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (stream, turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 844–855)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: This method reads the durable transcript for a conversation if the conversation belongs to this workspace. It protects tenant data before touching unscoped blob storage.

**Data flow**: It receives a conversation id. It first checks ownership, then reads the transcript blob, decodes it, and returns the Conversation; missing or foreign data returns None.

**Call relations**: The debugger transcript endpoint calls it. _owned_conversation is the guard before transcript_key and decode are used.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_transcript); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 857–868)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: This method lists saved compaction record numbers for a conversation. Compactions are stored summaries that replace older transcript windows.

**Data flow**: It receives a conversation id. After ownership is confirmed, it lists compaction blobs, extracts numeric indices from matching keys, sorts them, and returns the tuple.

**Call relations**: The debugger compactions endpoint calls it. It uses _owned_conversation to keep blob listing scoped to the workspace.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_compactions).


##### `SurfaceContext.read_compaction`  (lines 870–876)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: This method reads one saved compaction record for a conversation. It returns nothing if the conversation is outside this workspace or the record is missing.

**Data flow**: It receives a conversation id and compaction index. It checks ownership, then asks the transcript module to read that compaction record from blob storage.

**Call relations**: The debugger compaction-record endpoint calls it. The ownership check happens here before handing off to read_compaction_record.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (compaction_record); 1 external calls (read_compaction_record).


##### `SurfaceContext.list_workspace_files`  (lines 878–892)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: This method lists files stored in a conversation’s workspace folder. It shows relative paths rather than internal blob keys.

**Data flow**: It receives a conversation id. If the conversation is owned by this workspace, it lists blobs under the workspace prefix and returns WorkspaceFile objects with path, size, and modification time.

**Call relations**: The debugger workspace-files endpoint calls it. It uses _owned_conversation first and WorkspaceFile for the returned view.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_files); 1 external calls (__init__).


##### `SurfaceContext.read_workspace_file`  (lines 894–905)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: This method opens a stream for one workspace file if it belongs to this workspace and exists. It keeps file reads inside the conversation workspace subtree.

**Data flow**: It receives a conversation id and relative path. It checks conversation ownership, validates the path with workspace_key, verifies the blob exists, and returns a byte stream or None.

**Call relations**: The debugger workspace-file endpoint calls it. workspace_key prevents paths from escaping into other conversation data.

*Call graph*: calls 2 internal fn (_owned_conversation, workspace_key); called by 1 (workspace_file).


##### `SurfaceContext.installation`  (lines 907–920)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: This method reads the external installation id for a surface in this workspace. It helps views build links back to the external place where a conversation lives.

**Data flow**: It receives a peer surface name, queries the installation table in this workspace, and returns the installation id or None.

**Call relations**: The debugger workspace metadata endpoint calls it to enrich its read view.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 922–932)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: This private method checks whether a conversation id belongs to the current workspace. It is the guard used before reading unscoped blob data.

**Data flow**: It receives a conversation id, queries the conversation table for that id and workspace id, and returns true if a row exists.

**Call relations**: Transcript, compaction, and workspace-file read/list methods call it before accessing blob storage.

*Call graph*: called by 5 (list_compactions, list_workspace_files, read_compaction, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 934–952)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: This private helper builds the common query shape for reading turns. It keeps the selected columns consistent across list and detail views.

**Data flow**: It takes no input and returns a SQL select object containing the durable turn fields needed to reconstruct a Turn record.

**Call relations**: SurfaceContext.list_turns and SurfaceContext.turn_detail call it before adding their own filters and ordering.

*Call graph*: called by 2 (list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 954–972)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: This private helper converts a database row into a typed Turn object. It also parses stored context and terminal data back into their structured forms.

**Data flow**: It receives a row from _turn_query. It copies scalar fields, validates context and terminal JSON when present, and returns a Turn record.

**Call relations**: SurfaceContext.list_turns and SurfaceContext.turn_detail use it so their returned turns have the same shape.

*Call graph*: called by 2 (list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.bind`  (lines 993–998)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: This method lets a tool bind an installation only for a surface it declared in its manifest. That prevents a tool from registering arbitrary surface names.

**Data flow**: It receives a surface name and installation id. It checks the surface is declared, reads the current workspace id, and writes the binding through _bind_surface_installation.

**Call relations**: Tool code uses this scoped access object during configuration. The shared binding function performs the actual database update.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 1010–1020)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: This method resolves an external installation id to the workspace that owns it before a request is bound to any workspace. It is the first gate for shared surface ingress.

**Data flow**: It receives an installation id, searches owner-visible installation bindings for this surface, and returns the workspace id or None.

**Call relations**: Slack’s workspace resolver calls it when an incoming shared Slack request names a team or installation.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 1022–1034)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: This method tries to open a sealed credential authorization before the request has been bound to a workspace. It is useful for OAuth callbacks carrying sealed state.

**Data flow**: It receives a sealed string. If no credential store exists or verification fails, it returns None; otherwise it returns the credential request state.

**Call relations**: Slack’s workspace resolver calls it during install or authorization callbacks. It uses the same credential seal mechanism as SurfaceContext.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 1036–1052)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: This method reads a declared credential during pre-binding authentication. It lets a shared resolver verify a request for a specific workspace without granting broad secret access.

**Data flow**: It receives a workspace id and slot. It rejects undeclared slots, checks a credential store exists, confirms the workspace exists inside a workspace context, and returns the secret.

**Call relations**: Slack’s auth signing-secret helper calls it while resolving requests. It raises SurfaceWorkspaceUnknown if the sealed or supplied workspace is not served here.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceDeliveryError.__init__`  (lines 1066–1070)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: This exception constructor records a durable delivery failure and an optional provider-requested retry delay. It lets the poller respect a service’s Retry-After instruction.

**Data flow**: It receives an error message and optional retry-after seconds. It rejects negative retry delays, stores the retry value, and initializes the runtime error text.

**Call relations**: Slack posting code raises it for provider delivery problems. WritebackPoller._fail_or_retry later reads the retry delay from this exception.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 1109–1125)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database condition for writebacks that are ready to be processed. It includes finished turns whose delivery is pending or whose previous claim expired.

**Data flow**: It receives the current time and returns a SQL boolean expression. The expression matches terminal turns with pending or claimed writeback rows that are not currently protected by an active claim.

**Call relations**: writeback_workspaces.due uses it to find workspaces with work, and WritebackPoller._claim uses it to claim individual rows.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `writeback_workspaces`  (lines 1128–1163)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: This function creates a rotating reader for workspace ids that have deliverable writebacks. It keeps the background poller from scanning or loading everything at once.

**Data flow**: It initializes a cursor and returns an async candidates function. The returned function reads a bounded page of workspace ids and advances or resets the cursor as needed.

**Call relations**: A WritebackPoller receives this candidates function and calls it from run or drain. The owner_candidates wrapper performs the owner-level read safely.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 1135–1149)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: This inner function builds the SQL query for the next page of workspaces with due writebacks. It is cursor-aware so repeated polling moves through the id space.

**Data flow**: It reads the current time and current cursor. It selects workspace ids from due writebacks joined to terminal turns, groups them, orders them, limits the page, and optionally filters past the cursor.

**Call relations**: writeback_workspaces passes it into owner_candidates, which calls it whenever the poller asks for candidates.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 1153–1161)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function returns the next batch of workspace ids that may need writeback delivery. It wraps cursor movement and wraparound behavior.

**Data flow**: It calls the owner-level due reader. If no rows are found after a cursor, it resets to the beginning and tries again; when rows are found, it stores the last id as the new cursor.

**Call relations**: WritebackPoller.run and WritebackPoller.drain call this function through the candidates field to decide which workspaces to drain.


##### `_WritebackDeliveryFailed.__init__`  (lines 1171–1174)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: This exception wraps a failure in one phase of durable delivery: posting the reply or attaching files. It preserves both the phase and the original error.

**Data flow**: It receives the phase name and exception. It stores both, and uses the original exception’s text or class name as its own message.

**Call relations**: WritebackPoller._deliver_claimed raises it around surface post and attach calls. WritebackPoller._deliver catches it and decides whether to retry or fail.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 1197–1230)

```
async def run(self) -> None
```

**Purpose**: This is the long-running background loop that delivers finished turns to durable surfaces. It continually finds workspaces with due writebacks and starts bounded drain tasks.

**Data flow**: It keeps a map of in-flight workspace tasks and a concurrency semaphore. Each loop cleans up completed tasks, asks for more candidate workspaces when capacity allows, starts drain tasks, logs failures, sleeps briefly, and cancels outstanding work on shutdown.

**Call relations**: The application starts this method for continuous durable delivery. It hands each workspace to _drain_workspace.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 1232–1241)

```
async def drain(self) -> None
```

**Purpose**: This method performs one bounded drain pass instead of running forever. It is useful for tests, maintenance, or command-style execution.

**Data flow**: It reads candidate workspace ids, creates a semaphore, drains all candidates concurrently within the limit, gathers results, and raises an ExceptionGroup if any workspace drain failed.

**Call relations**: It uses the same _drain_workspace path as run, but without the infinite polling loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 1243–1261)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: This private method processes one workspace’s claimed writebacks under the workspace context. It is the per-workspace unit of background delivery work.

**Data flow**: It receives a workspace id and semaphore. After entering the workspace context, it claims due rows, starts lease-renewal tasks for them, delivers each one, and cancels renewals when finished.

**Call relations**: WritebackPoller.run and drain call it. It coordinates _claim, _renew_claim, and _deliver.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 1263–1296)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: This private method claims a batch of due writebacks for this worker. Claiming is like putting a temporary name tag on work so another poller does not do the same job at the same time.

**Data flow**: It receives a workspace id, finds due writebacks, updates them to claimed with this worker id and an expiry time, and returns the turn id, any saved reply reference, and last error for each claimed row.

**Call relations**: _drain_workspace calls it before delivery. It uses _writeback_due so workspace selection and row claiming agree on what is ready.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 1298–1330)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This private method wraps delivery of one claimed writeback with logging and retry/failure handling. It turns delivery exceptions into database state changes.

**Data flow**: It receives workspace id, turn id, optional reply reference, and the renewal task. It tries delivery with a lease; if the claim is lost it logs that, if delivery fails it calls _fail_or_retry, and if it succeeds it logs elapsed time.

**Call relations**: _drain_workspace calls it for each claimed row. It delegates the actual work to _deliver_with_lease.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 1332–1361)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: This private method makes sure external delivery happens only while the worker still owns the claim. It also stops the renewal task before marking the row delivered.

**Data flow**: It receives workspace id, turn id, optional reply reference, and renewal task. It runs delivery and renewal together, reacts if renewal stops first, cancels leftover tasks, waits for cleanup, then marks the writeback delivered.

**Call relations**: _deliver calls it. It runs _deliver_claimed for the outside call and _mark_delivered for the final database commit.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 1363–1385)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: This private method performs the actual surface delivery for a claimed writeback. It builds the reply payload, chooses the surface, posts the reply if needed, then attaches files.

**Data flow**: It receives workspace id, turn id, and optional saved reply reference. It builds a Writeback, finds the surface spec, skips surfaces without durable delivery, creates a SurfaceContext, posts if no reply reference is recorded, records the reference, and calls attach.

**Call relations**: _deliver_with_lease calls it while the claim renewal is active. It calls the surface’s post and attach handlers and wraps their failures in _WritebackDeliveryFailed.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 1387–1405)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: This private method keeps a long delivery claim alive. It prevents another worker from taking over while a slow external provider call is still in progress.

**Data flow**: It receives a turn id. In a loop, it sleeps until refresh time, extends the claim expiry for rows still claimed by this worker, and raises _WritebackClaimLost if the row is no longer owned.

**Call relations**: _drain_workspace starts one renewal task per claimed writeback. _deliver_with_lease watches this task while delivery runs.

*Call graph*: called by 1 (_drain_workspace); 6 external calls (__init__, sleep, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 1407–1463)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: This private method builds the Writeback object that a durable surface knows how to render. It collects the terminal answer and any shared artifacts for one turn.

**Data flow**: It receives a turn id, reads the turn terminal frame, conversation queue key and surface name, and artifact rows. It validates the terminal frame, converts artifact rows to SharedArtifact objects, and returns the Writeback plus surface name.

**Call relations**: _deliver_claimed calls it before selecting and invoking the surface’s delivery handlers.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 1465–1477)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: This private method saves the durable reference returned by the surface after posting a reply. That lets recovery attach files later without reposting the main reply.

**Data flow**: It receives a turn id and reply reference. It updates the claimed writeback row only if this worker still owns it; if not exactly one row is updated, it raises _WritebackClaimLost.

**Call relations**: _deliver_claimed calls it after a successful post and before attachments. This is the checkpoint between the post and attach phases.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 1479–1496)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: This private method marks a writeback as successfully delivered. It clears the worker claim so the row is closed and no longer retried.

**Data flow**: It receives a turn id. It updates the writeback row from claimed to delivered only if this worker still owns it, clears claim fields, and raises _WritebackClaimLost if the update did not happen.

**Call relations**: _deliver_with_lease calls it after delivery completes and renewal has been stopped. It is the final success commit.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 1498–1547)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: This private method releases a failed writeback either for a later retry or as permanently failed if it is too old. It also respects provider retry timing when a SurfaceDeliveryError includes it.

**Data flow**: It receives a turn id and wrapped delivery error. It calculates retry timing, truncates the stored error message, updates the claimed row to pending with a future claim expiry or failed if past the age limit, and returns the outcome, stored error, and next attempt time.

**Call relations**: WritebackPoller._deliver calls it after _deliver_with_lease reports a post or attach failure. Its returned outcome drives the failure log.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).

## 📊 State Registers Touched

- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-member-session-auth` — The signed-in person’s identity and session proof used to decide who is making a request.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-inbound-message-dedup` — The durable inbox and duplicate-detection state for messages arriving from external surfaces.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-spend-ledger-billing` — The shared usage ledger, prices, caps, exports, and billing records used to track and limit spending.
- `reg-live-stream-hub` — The live stream of turn updates that clients can watch and replay after reconnecting.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
- `reg-row-level-security-context` — The database safety context that keeps each workspace’s rows separated even when code uses shared tables.
- `reg-surface-writeback-state` — Pending and completed outbound reply/writeback records used to deliver final turn results back to external surfaces without duplication.
- `reg-slack-connect-provisioning-state` — The hosted-control-plane state for creating, retrying, and inspecting customer Slack Connect channels during workspace onboarding.
