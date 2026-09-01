# Chat, Terminal, and External Messaging Surfaces  `stage-6.2`

This stage is the system’s set of doorways to the outside world. It is mostly shared, behind-the-scenes support for the main work loop: people type in Slack, iMessage, or a terminal, and this code turns those outside events into the system’s normal ideas of members, conversations, messages, files, mentions, and replies.

The core surface bridge is the trusted gatekeeper. It lets an external app prove who is speaking, open or find a conversation, stream an active response, and deliver the final answer safely. The Slack surface checks Slack requests, accepts messages, handles installs and buttons, and sends replies or files back. Slack mentions are cleaned up so stored messages are readable, then changed back into Slack’s special codes when notifications are needed. The iMessage cloud code talks to Spectrum Cloud, refreshes access, and converts remote events into simple messages; the iMessage surface maps those into UFO conversations and sends replies back. The UFO terminal surface turns server events into simple commands for a shell client. Redis terminal streaming lets terminal sessions and work running on different servers find each other and exchange control messages or larger data.

## Files in this stage

### Shared Surface Bridge
Defines the trusted runtime contract that external surfaces use to identify members, open conversations, admit messages, stream turns, and deliver replies.

### `core/src/ufo/runtime/ext/surface.py`

`orchestration` · `cross-cutting: request handling, live streaming, background delivery, and administration reads`

A “surface” is any outside doorway where a person talks to the system: a chat app, the browser portal, iMessage, or a future integration. This file is the controlled doorway from those surfaces into the core. It matters because surfaces need powers that ordinary extensions must not have, such as saying “this message came from this member,” creating or finding the right conversation, reading declared credentials, and delivering the agent’s reply back to the outside world.

The file covers two styles of surface. A live surface, such as the web app, keeps a connection open and streams turn events as they happen. A durable surface, such as Slack, cannot rely on a live stream, so the system records a delivery job and background pollers post the final reply and any mid-turn replies later. Think of it like a restaurant: live surfaces watch the kitchen; durable surfaces leave a ticket and the waiter brings food when it is ready.

It also contains many read views used by the portal: agents, conversations, files, connector accounts, usage, memory, credentials, and audit records. Most methods are careful to check workspace ownership or audience rules before returning content. Without this file, surface integrations would each reinvent sensitive identity, access, delivery, and retry rules, which would make security and reliability much harder to trust.

#### Function details

##### `is_silence_sentinel`  (lines 234–242)

```
def is_silence_sentinel(answer: str) -> bool
```

**Purpose**: Checks whether an agent’s final answer means “say nothing.” It is used to avoid posting an empty-looking reply that should count as delivered silence.

**Data flow**: It receives answer text, trims surrounding whitespace, compares the whole text to the known empty response or line-break forms, and returns true or false.

**Call relations**: Durable delivery code and surfaces can use this helper before posting a reply, so silence is treated as a real outcome rather than a message to send.


##### `mint_marker`  (lines 245–255)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to wrap one member message inside safe text tags. The marker makes it extremely unlikely that user text can accidentally mimic the wrapper.

**Data flow**: It takes no input, asks the secrets library for random bytes encoded as hex, and returns that marker string.

**Call relations**: It is paired with fence_member_message, which uses the marker to mark the boundary around a member’s own words.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 258–271)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the text that enters a turn from a surface message. It separates ambient context, the member’s words, and attachment text so later readers can tell them apart.

**Data flow**: It receives a marker, context text, message body, and attachment text, wraps the body and attachments in marker-named tags, and returns one combined inbound string.

**Call relations**: Surface ingesters use this before admission; member_message_text later reverses the member-message part for display.


##### `inbox_name`  (lines 274–303)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an unsafe attachment filename into a safe file name for the workspace. It prevents path tricks, overlong names, and duplicate names in one upload batch.

**Data flow**: It receives a raw name and a set of already-used names, keeps only a safe leaf filename, shortens it while preserving the suffix where possible, adds a number if needed, updates the used set, and returns the safe name.

**Call relations**: All surfaces should use this shared helper for inbound files, so Slack, browser uploads, and future surfaces follow the same safety rule.

*Call graph*: 1 external calls (contained_leaf).


##### `member_message_text`  (lines 306–320)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts the member’s own words from the stored inbound text. This is used when the UI wants to show what the person said, not the extra context wrapped around it.

**Data flow**: It receives inbound turn text, removes known engine context wrappers, looks for the marker-based member-message block, and returns either the extracted words or the original text.

**Call relations**: conversation_name calls this so conversation titles are based on the member’s message rather than hidden prompt context.

*Call graph*: called by 1 (conversation_name).


##### `MemberAdmitter.admit`  (lines 374–385)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Defines the capability for admitting a member’s message into the durable turn queue. A concrete implementation decides whether it starts a new turn or joins an existing one.

**Data flow**: It receives conversation id, message text, optional idempotency key, context, speaker, intent, comment, and runtime settings, then returns an Admitted result describing the turn created or joined.

**Call relations**: SurfaceContext.admit delegates to this protocol, keeping surface code independent from the queue implementation.


##### `TurnTailer.tail`  (lines 399–401)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how a live surface follows a turn’s live event frames until the turn ends. It is the read side of a live chat stream.

**Data flow**: It receives a turn id and optional cursor, opens an async scoped stream, and yields cursor-frame pairs until the stream is closed or the turn finishes.

**Call relations**: SurfaceContext.tail exposes this to web, debugger, and sample surfaces without letting them talk directly to the hub.


##### `TurnTailer.latest_activity`  (lines 403–403)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Defines a lightweight peek at what a turn is currently doing. It avoids opening a full stream.

**Data flow**: It receives a turn id, reads the newest retained activity if any, and returns that activity or null.

**Call relations**: SurfaceContext.latest_activity delegates here for status screens such as the web agent status view.


##### `TurnStopper.stop`  (lines 413–413)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines how a member-requested stop cancels a running turn. It also reports whether a pending follow-up turn was started.

**Data flow**: It receives workspace, conversation, and turn ids, attempts to stop that turn, and returns a Stopped result.

**Call relations**: SurfaceContext.stop_turn delegates to this so surfaces can offer stop buttons without knowing cancellation internals.


##### `TurnStepSource.read`  (lines 419–419)

```
async def read(self, workflow_id: str) -> tuple['TurnStep', ...]
```

**Purpose**: Defines how to read the recorded workflow steps for a turn. This powers debugging and detailed turn views.

**Data flow**: It receives a workflow id, reads recorded model/tool/workflow steps, and returns them as TurnStep records.

**Call relations**: SurfaceContext.turn_steps uses this protocol after first checking that the turn belongs to the workspace.


##### `SurfaceModel.model`  (lines 449–449)

```
def model(self) -> str
```

**Purpose**: Names the model available to surface routes for small synchronous model calls. It lets a route label and bill its own model use.

**Data flow**: It reads the configured model identifier and returns it as a string.

**Call relations**: SurfaceContext.model exposes this optional capability to surface routes that need model help while serving a request.


##### `SurfaceModel.turn`  (lines 451–451)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Runs one model request from a surface route. It is meant for bounded route-time work, not full conversation turns.

**Data flow**: It receives a ModelRequest, sends it to the configured model access layer, and returns the model Message result.

**Call relations**: Surface routes reach it through SurfaceContext.model when the deployment has wired this capability.


##### `shared_artifact_link`  (lines 536–547)

```
def shared_artifact_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a file shared by a turn. If public file delivery is not configured, it returns nothing.

**Data flow**: It receives signing secret, public base URL, workspace id, and artifact metadata, computes an expiry, signs an artifact path, and returns a full URL or null.

**Call relations**: SurfaceContext.artifact_link wraps this for surfaces that need to show links instead of uploading files directly.

*Call graph*: called by 1 (artifact_link); 3 external calls (now, artifact_url_expiry, mint_artifact_url).


##### `shared_artifact_preview_link`  (lines 550–572)

```
def shared_artifact_preview_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview-image link for a shared artifact when the artifact is safely previewable. It refuses mismatched or non-image preview data.

**Data flow**: It chooses either the artifact’s preview blob or original blob, verifies the declared raster image type and size, and returns a signed preview URL or null.

**Call relations**: SurfaceContext.artifact_preview_link delegates here for web previews and other visual surfaces.

*Call graph*: called by 1 (artifact_preview_link); 2 external calls (mint_image_preview_url, raster_image_media_type).


##### `_scheduled_runs_query`  (lines 575–611)

```
def _scheduled_runs_query(workspace_id: UUID, member_id: UUID, agent_id: UUID | None) -> sa.Select[Any]
```

**Purpose**: Builds the database query for completed scheduled turns a member is allowed to read. It is a reusable query fragment for scheduled-run feeds.

**Data flow**: It receives workspace id, member id, and optional agent id, creates a select statement filtered by scheduled admission, readable audiences, completion, and reporting value, and returns the query.

**Call relations**: scheduled_runs calls this first, then adds feed-specific filters and turns the rows into ScheduledRun objects.

*Call graph*: called by 1 (scheduled_runs); 3 external calls (or_, select, readable_audiences).


##### `scheduled_runs`  (lines 614–690)

```
async def scheduled_runs(workspace_id: UUID, member_id: UUID, *, limit: int, agent_id: UUID | None=None, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Lists recent completed turns that fired automatically, such as scheduled jobs, for conversations the member may read. It includes the reply text and shared files.

**Data flow**: It receives workspace/member ids plus filters, queries matching scheduled turns, reads their shared artifacts, looks up conversation sources, and returns ScheduledRun records.

**Call relations**: It uses _scheduled_runs_query for access-safe selection and ConversationDirectory.sources to add links back to the originating conversation.

*Call graph*: calls 1 internal fn (_scheduled_runs_query); 6 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx).


##### `conversation_name`  (lines 739–744)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Derives a conversation title from the member’s opening words. It deliberately ignores ambient context around the message.

**Data flow**: It receives inbound text, extracts the member-message portion, trims it, caps its length, and returns the title string.

**Call relations**: Conversation creation paths use this shared naming rule so different surfaces title conversations consistently.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 747–764)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Updates a conversation’s title when a surface has a better name for it. Blank names are ignored.

**Data flow**: It receives workspace id, conversation id, and title, trims and caps the title, then updates the matching conversation row if the title is nonblank.

**Call relations**: SurfaceContext.retitle_conversation is the workspace-bound wrapper that surface routes call.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `summarize_conversation_title`  (lines 767–787)

```
async def summarize_conversation_title(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores a model-generated summary title and marks that the title summary job has run. Even a blank summary marks the job complete.

**Data flow**: It receives workspace id, conversation id, and proposed title, caps it, then updates title when nonblank and sets the summarized flag.

**Call relations**: Background titling jobs use this to avoid repeatedly paying to summarize the same conversation.

*Call graph*: 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 862–863)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes an agent update time so it always has UTC timezone information. This avoids ambiguous timestamps in API responses.

**Data flow**: It receives a datetime, leaves it unchanged if timezone-aware, or returns a copy marked UTC.

**Call relations**: Pydantic calls this automatically when AgentDetail is built.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 911–912)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a connector connection timestamp to include UTC timezone information.

**Data flow**: It receives connected_at, keeps aware values as they are, or adds UTC to naive values.

**Call relations**: Pydantic runs it while building ConnectionView rows for the portal.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 938–939)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes a workspace connection-pool timestamp to UTC. This keeps connection listings consistent.

**Data flow**: It receives connected_at and returns a timezone-aware datetime.

**Call relations**: Pydantic applies it when SurfaceContext.list_connections creates ConnectionPoolView objects.

*Call graph*: 1 external calls (replace).


##### `_binding_fields`  (lines 998–1026)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts display and identity fields from a stored connector source configuration. It lets portal actions submit the same binding identity the object system expects.

**Data flow**: It receives backend name and raw config, validates it as ConnectorSourceConfig, then returns binding name, stream, account id, base URL, and backfill setting, or null fields if invalid.

**Call relations**: SurfaceContext.list_sources uses it when turning source rows into SourceView records.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 1058–1059)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures the next sync time for a source has UTC timezone information.

**Data flow**: It receives a datetime and returns the same value if aware, otherwise a UTC-marked copy.

**Call relations**: Pydantic calls it when SourceView objects are created for source listings.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 1077–1080)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation timestamps to UTC while allowing missing last-activity times.

**Data flow**: It receives a datetime or null, returns null unchanged, keeps aware times, or adds UTC to naive times.

**Call relations**: Pydantic applies it for ConversationSummary values used across conversation listings.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 1093–1154)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged reading another member’s private transcript. This audit row temporarily unlocks that transcript for the admin.

**Data flow**: It receives workspace, conversation, agent, and admin member ids, checks the conversation and subject, writes a transcript_access row, logs the disclosure, and returns the involved emails or null.

**Call relations**: readable_conversation later checks these records; portal prepared-intent flows call this before serving private content.

*Call graph*: 9 external calls (__init__, now, insert, select, workspace_tx, log, audience_member, parse_audience, uuid4).


##### `ConversationDirectory.list`  (lines 1212–1345)

```
async def list(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, portal: bool | None=None, conversation_id: UUID | None=None, participation: Literal['mine',
```

**Purpose**: Lists an agent’s conversations with access-aware metadata for the portal. It includes content details only for conversations the member may read.

**Data flow**: It receives agent/member ids and filters, builds a bounded conversation query, applies audience/search/participation rules, fetches sources and speakers for readable rows, and returns ListedConversation objects.

**Call relations**: SurfaceContext.list_agent_conversations delegates here; sources, speakers, and small predicate helpers support the main listing flow.

*Call graph*: calls 6 internal fn (_matches, _member_admitted, _others, _participated, sources, speakers); 10 external calls (__init__, __init__, not_, or_, select, workspace_tx, audience_member, conversation_audience, parse_audience, readable_audiences).


##### `ConversationDirectory.sources`  (lines 1347–1384)

```
async def sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the source link or origin string recorded on each conversation’s opening turn.

**Data flow**: It receives conversation ids, finds the earliest turn for each, parses its TurnContext, and returns a map from conversation id to source or null.

**Call relations**: ConversationDirectory.list and scheduled artifact/run views call this to link rows back to where they began.

*Call graph*: called by 1 (list); 4 external calls (model_validate, and_, select, workspace_tx).


##### `ConversationDirectory.speakers`  (lines 1386–1442)

```
async def speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Lists the first few members who spoke in each conversation. It gives the portal a compact “who was there” view.

**Data flow**: It receives conversation ids, finds each speaker’s first turn, parses sender display context, and returns speakers grouped by conversation.

**Call relations**: ConversationDirectory.list calls it only for conversations whose content the viewer may read.

*Call graph*: called by 1 (list); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `ConversationDirectory._member_admitted`  (lines 1444–1457)

```
def _member_admitted(self) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations that have at least one member-admitted turn. This separates human conversations from machine-only lanes.

**Data flow**: It reads the surrounding conversation row in a correlated database condition and returns true when a member admission exists.

**Call relations**: ConversationDirectory.list uses it when the caller asks for only member-admitted conversations.

*Call graph*: called by 1 (list); 2 external calls (literal, select).


##### `ConversationDirectory._spoken`  (lines 1459–1480)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for whether a member, or any member, has spoken in a conversation.

**Data flow**: It receives an optional member id, creates an exists condition over turn speaker rows, and returns that condition.

**Call relations**: _participated and _others use it to define conversation participation filters.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `ConversationDirectory._participated`  (lines 1482–1490)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for conversations the member participated in. Participation means the conversation belongs to them or they spoke in it.

**Data flow**: It receives a member id and returns an OR condition over conversation owner and spoken-turn existence.

**Call relations**: ConversationDirectory.list applies it for the “mine” participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 1 external calls (or_).


##### `ConversationDirectory._others`  (lines 1492–1504)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for conversations where other members spoke but this member did not participate.

**Data flow**: It receives a member id and returns a database condition excluding owned/spoken-by-this-member rows while requiring some member speech.

**Call relations**: ConversationDirectory.list applies it for the “others” participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 2 external calls (and_, not_).


##### `ConversationDirectory._matches`  (lines 1506–1535)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the search condition for conversation listings. It protects private content by only searching titles and speakers when the viewer may read them.

**Data flow**: It receives search text and member id, creates conditions over surface labels, owner email, readable titles, and readable speaker emails, and returns one OR condition.

**Call relations**: ConversationDirectory.list uses this before limiting results, so searching does not miss older matching conversations.

*Call graph*: called by 1 (list); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `LedgerEntry._aware_utc`  (lines 1549–1550)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes ledger entry timestamps to UTC.

**Data flow**: It receives created_at and returns it with timezone information if needed.

**Call relations**: Pydantic runs it when TurnDetail builds accounting rows.

*Call graph*: 1 external calls (replace).


##### `TurnStep._aware_utc`  (lines 1571–1574)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes optional workflow step timestamps to UTC.

**Data flow**: It receives a datetime or null, returns null unchanged, keeps aware times, or adds UTC.

**Call relations**: Pydantic applies it when TurnStep records are returned by a TurnStepSource.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 1630–1631)

```
def _fulfilled_marker_key(request_id: UUID, slot: str) -> str
```

**Purpose**: Builds the blob-store key that marks one private credential prompt as fulfilled.

**Data flow**: It receives a credential request id and slot name, formats them into a stable marker path, and returns that string.

**Call relations**: credential_prompt_pending checks this marker; fulfill_credential_request writes it after storing the value.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request).


##### `_credential_request_id`  (lines 1634–1637)

```
def _credential_request_id(state: CredentialRequestState, sealed: str) -> UUID
```

**Purpose**: Gets a stable id for a credential request, even for older sealed requests that do not carry one.

**Data flow**: It receives request state and sealed token text, returns the state’s id if present, otherwise hashes the sealed token into a UUID.

**Call relations**: Credential prompt checking, renewal, and fulfillment all use this to agree on the same request identity.

*Call graph*: called by 3 (credential_prompt_pending, fulfill_credential_request, renew_credential_request); 2 external calls (sha256, UUID).


##### `_main_agent`  (lines 1640–1653)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. This is the fallback agent for new surface conversations and installations.

**Data flow**: It receives a workspace id, queries the agent table for the main agent, returns its id, or raises if none exists.

**Call relations**: _bind_surface_installation and SurfaceContext._surface_agent use it when no explicit surface binding names an agent.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 1656–1695)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str, *, routes_ingress: bool) -> None
```

**Purpose**: Creates or updates the binding between a workspace and a surface’s external installation, such as a Slack team. It preserves the bound agent when rebinding.

**Data flow**: It receives workspace, surface, installation id, and routing mode, validates the id, finds the main agent for new rows, upserts the installation, and raises a conflict if another workspace owns it.

**Call relations**: SurfaceContext.bind_installation and SurfaceInstallationAccess.bind share this single writer.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.fleet_blob`  (lines 1769–1772)

```
def fleet_blob(self) -> FleetBlobStore
```

**Purpose**: Returns a deploy-wide blob-store view for shared fleet assets. It is separate from workspace-scoped blobs.

**Data flow**: It reads the backend from the workspace blob store and returns a FleetBlobStore using that backend.

**Call relations**: Surface code can use this when it needs static or shared assets rather than workspace data.

*Call graph*: 1 external calls (__init__).


##### `SurfaceContext.conversation_slots`  (lines 1775–1777)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Returns the extension-provided conversation slots registered at startup. These are extra panels or data areas tied to conversations.

**Data flow**: It reads the stored tuple of bound slots and returns it unchanged.

**Call relations**: Web surface conversation slot routes inspect this property before reading or summarizing a slot.


##### `SurfaceContext.read_conversation_slot`  (lines 1779–1784)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Runs one conversation slot read under the conversation’s agent identity. This keeps agent-scoped data access consistent.

**Data flow**: It receives a bound slot and slot context, temporarily binds the agent id, calls the provider’s read method, and returns its payload.

**Call relations**: The web surface calls this when rendering a specific conversation slot.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1786–1791)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Runs a slot’s summary operation under the conversation’s agent identity.

**Data flow**: It receives a bound slot and context, binds the agent, calls the provider’s summarize method, and returns the summary value or null.

**Call relations**: The web surface uses this while preparing conversation slot summaries.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.deploy_extensions`  (lines 1794–1797)

```
def deploy_extensions(self) -> tuple[DeployExtensionView, ...]
```

**Purpose**: Returns the extensions installed in this deployment for an administration view.

**Data flow**: It reads the boot-time extension summary tuple and returns it.

**Call relations**: Portal administration pages use this as read-only deployment status.


##### `SurfaceContext.runtime`  (lines 1800–1802)

```
def runtime(self) -> RuntimeIdentity | None
```

**Purpose**: Returns the runtime identity, if the composition root supplied one. This identifies the service and sandbox environment.

**Data flow**: It reads the stored runtime identity and returns it or null.

**Call relations**: Surfaces can display or report runtime identity without constructing it themselves.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1805–1808)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Reports whether this deployment allows sandbox public internet at all. Agent settings can only narrow this capability.

**Data flow**: It reads the stored boolean and returns it.

**Call relations**: Portal settings use it to explain the deployment-wide ceiling for agent internet access.


##### `SurfaceContext.deploy_skills`  (lines 1811–1816)

```
def deploy_skills(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Returns the deploy-provided skill index. This is the shared base set of skills available before member-authored skills are added.

**Data flow**: It asks the SkillRegistry for its index and returns the tuple of skill names and descriptions.

**Call relations**: Portal and spawn flows use this to show or compose the shared skill floor.


##### `SurfaceContext.system_skill_bundle`  (lines 1819–1821)

```
def system_skill_bundle(self) -> SystemSkillBundle
```

**Purpose**: Returns the immutable bundle of system skills loaded at startup.

**Data flow**: It reads and returns the stored SystemSkillBundle.

**Call relations**: Turn execution and surface reads can rely on this as the fixed deploy skill archive.


##### `SurfaceContext.models`  (lines 1824–1828)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Returns the model ids this deployment can run. It is the safe set shown in agent settings.

**Data flow**: It reads the stored tuple of model names and returns it.

**Call relations**: validate_runtime_config checks against this same set, keeping UI choices and runtime enforcement aligned.


##### `SurfaceContext.validate_runtime_config`  (lines 1830–1838)

```
def validate_runtime_config(self, runtime_config: TurnRuntimeConfig) -> None
```

**Purpose**: Rejects turn runtime settings that this deployment cannot support. It prevents unknown models or environment documents on deployments that disabled them.

**Data flow**: It receives a TurnRuntimeConfig, checks model membership and environment-document support, and either returns normally or raises ValueError.

**Call relations**: The UFO surface calls it while translating route input into runtime configuration.

*Call graph*: called by 1 (_runtime_config).


##### `SurfaceContext.store_environment_document`  (lines 1840–1846)

```
async def store_environment_document(self, body: bytes) -> str
```

**Purpose**: Stores an environment document in the workspace blob store and returns its digest. It is only available on deployments that allow this feature.

**Data flow**: It receives document bytes, verifies a storage hook exists, passes the workspace blob store and bytes to that hook, and returns the digest.

**Call relations**: The UFO surface uses it when a user uploads or saves environment configuration.

*Call graph*: called by 1 (store_environment).


##### `SurfaceContext.store_environment_file`  (lines 1848–1853)

```
async def store_environment_file(self, body: bytes) -> str
```

**Purpose**: Stores a file referenced by an environment document and returns its digest.

**Data flow**: It receives file bytes, checks that environment storage is enabled, calls the configured file storage hook, and returns the content digest.

**Call relations**: The UFO surface uses it for files attached to environment documents.

*Call graph*: called by 1 (store_environment_file).


##### `SurfaceContext.sandbox_sizes`  (lines 1856–1859)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Returns the sandbox sizes this deployment can provision. An empty list means there is no choice to offer.

**Data flow**: It reads the stored sandbox size tuple and returns it.

**Call relations**: Portal settings can use this to decide whether to show a sandbox size control.


##### `SurfaceContext.credential`  (lines 1861–1864)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential slot for a trusted surface. It never returns through a sandbox proxy.

**Data flow**: It receives a slot name, checks that a credential store exists, reads the slot for this workspace, and returns the secret value.

**Call relations**: Slack surface helpers use it for bot tokens, signing secrets, posting, attachment upload, and inbound conversion.

*Call graph*: called by 8 (_bot_token, _channel_origin, _ctx_signing_secret, _post_ephemeral, _to_inbound, attach, post, speak).


##### `SurfaceContext.put_member_credential`  (lines 1866–1872)

```
async def put_member_credential(self, member_id: UUID, slot: str, value: str) -> None
```

**Purpose**: Stores a member-specific credential value, such as a personal provider key. The caller has already authenticated the member.

**Data flow**: It receives member id, slot, and value, converts the slot to a member-scoped key, and writes it to the credential store.

**Call relations**: Web account-connection routes call this after provider sign-in or device polling succeeds.

*Call graph*: called by 2 (anthropic_code, openai_device_poll); 1 external calls (member_slot).


##### `SurfaceContext.member_credential_stored`  (lines 1874–1883)

```
async def member_credential_stored(self, member_id: UUID, slot: str) -> bool
```

**Purpose**: Checks whether a member has filled a particular personal credential slot. It does not reveal the value.

**Data flow**: It receives member id and slot, tries to read the member-scoped credential, and returns true if found, false if unset or no store exists.

**Call relations**: The web workspace accounts page uses this to show connected account state.

*Call graph*: called by 1 (workspace_accounts); 1 external calls (member_slot).


##### `SurfaceContext.clear_member_credential`  (lines 1885–1891)

```
async def clear_member_credential(self, member_id: UUID, slot: str) -> None
```

**Purpose**: Deletes one member’s personal credential for a slot. This lets the member disconnect or replace an account.

**Data flow**: It receives member id and slot, checks for a credential store, converts to member-scoped slot, and clears it.

**Call relations**: The web account disconnect route calls it for member-owned credentials.

*Call graph*: called by 1 (account_disconnect); 1 external calls (member_slot).


##### `SurfaceContext.member_holds_own_model_key`  (lines 1893–1906)

```
async def member_holds_own_model_key(self, member_id: UUID) -> bool
```

**Purpose**: Checks whether a member has any personal model-provider key recognized by the runtime. This helps decide whether personal model-powered features can run.

**Data flow**: It receives a member id, checks each routed model-key slot in the credential store, and returns true on the first filled slot.

**Call relations**: The web first-run page calls it so its prompts match the runtime’s actual capability gate.

*Call graph*: called by 1 (workspace_first_run); 1 external calls (member_slot).


##### `SurfaceContext.credential_prompt_pending`  (lines 1908–1935)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs a value for a slot. It prevents already-fulfilled prompts from showing again.

**Data flow**: It receives sealed request text and slot, opens and verifies the request, checks workspace and slot membership, looks for a fulfillment row or blob marker, and returns pending status.

**Call relations**: The web surface’s pending prompt builder calls this while rendering credential handoff prompts.

*Call graph*: calls 2 internal fn (_credential_request_id, _fulfilled_marker_key); called by 1 (_pending_prompts); 3 external calls (select, workspace_tx, open_credential_request).


##### `SurfaceContext.renew_credential_request`  (lines 1937–1966)

```
async def renew_credential_request(self, sealed: str, member_id: UUID) -> str | None
```

**Purpose**: Renews a still-valid credential request for the authenticated member, usually after a page reload.

**Data flow**: It receives sealed request and member id, verifies the request and age, confirms the member is still an admin, stamps request id and issue time if needed, and returns a new sealed token or null.

**Call relations**: The web surface uses it while rebuilding pending credential prompts.

*Call graph*: calls 1 internal fn (_credential_request_id); called by 1 (_pending_prompts); 5 external calls (now, workspace_tx, open_credential_request, seal_credential_request, member_is_admin).


##### `SurfaceContext.open_credential_authorization`  (lines 1968–1978)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential authorization handoff and returns its claims. It is used when a provider callback must recover which member and slot the flow belongs to.

**Data flow**: It receives sealed state, requires a credential store, verifies and decrypts the request, and returns CredentialRequestState or raises if invalid.

**Call relations**: Slack OAuth callback uses it to complete credential authorization.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 1980–2033)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Verifies and stores the value for a sealed credential request. It enforces workspace, member, declared-slot, and one-time prompt rules.

**Data flow**: It receives sealed request, slot, value, and member id, validates all claims, writes the credential using the declared merge rule, then writes a fulfillment marker for private prompts.

**Call relations**: Slack, UFO, and web credential fulfillment routes call this after collecting a secret.

*Call graph*: calls 2 internal fn (_credential_request_id, _fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 5 external calls (__init__, now, dumps, warn, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 2035–2043)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation identity to the current workspace.

**Data flow**: It receives an installation id and calls the shared installation upsert with routes_ingress enabled.

**Call relations**: Slack OAuth callback calls this after installation succeeds.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.address_claim`  (lines 2045–2070)

```
async def address_claim(self, address: str) -> AddressClaim | None
```

**Purpose**: Reads the current workspace’s claim on an addressed-surface address, such as a phone number.

**Data flow**: It receives an address, queries the surface_address row for this workspace and surface, normalizes expiry time, and returns AddressClaim or null.

**Call relations**: The iMessage surface uses it before admitting messages for claimed addresses.

*Call graph*: called by 1 (_admit_message); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_surface_claim`  (lines 2072–2106)

```
async def member_surface_claim(self, surface: str, member_id: UUID) -> AddressClaim | None
```

**Purpose**: Finds the best address claim a member holds for a given surface. Proved claims beat pending reservations.

**Data flow**: It receives surface name and member id, queries matching address claims ordered by strength and recency, normalizes expiry, and returns AddressClaim or null.

**Call relations**: The web portal uses it to show a member’s iMessage claim state.

*Call graph*: called by 1 (workspace_imessage_claim); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.confirm_address`  (lines 2108–2121)

```
async def confirm_address(self, address: str, proved_by: str) -> None
```

**Purpose**: Marks an address claim as proved by an inbound message. After this, the address stops expiring.

**Data flow**: It receives address and proof id, updates the matching surface_address row to clear expiry and store proved_by.

**Call relations**: The iMessage proof flow calls this when a sender proves they control the address.

*Call graph*: called by 1 (_prove); 2 external calls (update, workspace_tx).


##### `SurfaceContext.release_address`  (lines 2123–2132)

```
async def release_address(self, address: str) -> None
```

**Purpose**: Deletes an address claim so the address can be claimed again.

**Data flow**: It receives an address and deletes the matching row for this workspace and surface.

**Call relations**: The iMessage proof flow can call it to release a failed or unwanted claim.

*Call graph*: called by 1 (_prove); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.public_base_url`  (lines 2135–2138)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deployment’s public base URL, if configured.

**Data flow**: It reads the stored public base URL and returns it or null.

**Call relations**: Surface handlers use it when rendering callback URLs or links.


##### `SurfaceContext.cookie_secure`  (lines 2141–2145)

```
def cookie_secure(self) -> bool
```

**Purpose**: Decides whether session cookies should be marked Secure. Secure cookies are sent only over HTTPS.

**Data flow**: It parses the public base URL scheme and asks the HTTP helper whether that scheme requires secure cookies.

**Call relations**: Browser-facing surfaces use this when setting cookies.

*Call graph*: 2 external calls (cookie_secure, urlsplit).


##### `SurfaceContext.home_url`  (lines 2147–2157)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link into the browser portal, if this deployment has a public URL and home surface.

**Data flow**: It receives an optional fragment, combines the public base URL, home surface mount path, and fragment, and returns the URL or null.

**Call relations**: iMessage, Slack, and sites surfaces use it when they need to point a member to the portal.

*Call graph*: called by 3 (_terminal_text, _into_the_portal, _reply_with_oversize_links).


##### `SurfaceContext.shared_artifacts`  (lines 2159–2197)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Lists files shared by a turn in delivery order. Live surfaces use this to render downloads themselves.

**Data flow**: It receives a turn id, queries shared_artifact rows for this workspace and turn, and returns SharedArtifact objects.

**Call relations**: UFO and web surfaces call it when building shared file payloads or live event streams.

*Call graph*: called by 2 (shared_files, _events); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 2199–2207)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a shared artifact in this workspace.

**Data flow**: It receives artifact metadata and passes this context’s signing secret, base URL, and workspace id to shared_artifact_link.

**Call relations**: Slack, iMessage, UFO, and web surfaces call it when rendering shared files.

*Call graph*: calls 1 internal fn (shared_artifact_link); called by 5 (_terminal_text, _oversize_link_line, shared_files, _file_payload, _project_slot_context).


##### `SurfaceContext.artifact_preview_link`  (lines 2209–2219)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview image link for a shared artifact when safe and configured.

**Data flow**: It receives artifact metadata and delegates to shared_artifact_preview_link with this workspace’s signing settings.

**Call relations**: The web surface uses it for file cards and conversation slot projections.

*Call graph*: calls 1 internal fn (shared_artifact_preview_link); called by 2 (_file_payload, _project_slot_context).


##### `SurfaceContext.ingress_url`  (lines 2221–2267)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest: str | None=None) -> str | None
```

**Purpose**: Builds a signed browser URL to a sandbox-served app or port for a conversation. It returns nothing if ingress is not configured.

**Data flow**: It receives conversation id, port, entry path, and optional framing or shipped-app data, then delegates to the ingress URL signer.

**Call relations**: The sites surface uses it when serving frames and shipped app bundles.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (mint_ingress_view_url).


##### `SurfaceContext._identity_member`  (lines 2269–2282)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up which member an external surface identity is linked to. It is the shared private helper for identity resolution.

**Data flow**: It receives a surface name and external id, queries surface_identity in this workspace, and returns the member id or null.

**Call relations**: linked_member uses it for this surface; adopt_identity uses it to read a peer surface.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 2284–2285)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the workspace member linked to this surface’s external user id.

**Data flow**: It receives an external id, calls _identity_member for this context’s surface, and returns a member id or null.

**Call relations**: Sample, sites, Slack, UFO, and web authentication flows call it before admitting or serving user-specific requests.

*Call graph*: calls 1 internal fn (_identity_member); called by 7 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, _authenticated_member, _authenticate).


##### `SurfaceContext.member_has_access`  (lines 2287–2289)

```
async def member_has_access(self, member_id: UUID) -> bool
```

**Purpose**: Checks whether a member is currently admitted by the workspace seat rules.

**Data flow**: It receives member id, opens a workspace transaction, asks Seats.admits, and returns true or false.

**Call relations**: Sites, UFO, and web authentication flows use it after resolving identity.

*Call graph*: called by 3 (_viewer, _authenticated_member, _authenticate); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.is_operator_workspace`  (lines 2291–2298)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the workspace belongs to the fleet operator. It gates operator-only debug renderings.

**Data flow**: It reads the workspace domain and compares it to the operator email domain.

**Call relations**: Surface renderers can call this before showing internal links or footers.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 2300–2323)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to the member already known by a peer surface. This lets one person keep the same member identity across surfaces.

**Data flow**: It receives peer surface and external id, finds the peer-linked member, inserts this surface identity if possible, logs races, and returns the member id or null.

**Call relations**: The sample live surface calls it when adopting identity from another surface.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 2325–2347)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing member found by email address.

**Data flow**: It receives external id and email, searches members case-insensitively, chooses the oldest match, and calls link_member_id, returning member id or null.

**Call relations**: join_member uses it first; several surface authentication flows call it after verifying an email.

*Call graph*: calls 1 internal fn (link_member_id); called by 5 (join_member, _surface_ingest, _viewer, _authenticated_member, _authenticate); 2 external calls (select, workspace_tx).


##### `SurfaceContext.link_member_id`  (lines 2349–2378)

```
async def link_member_id(self, external_id: str, member_id: UUID) -> UUID | None
```

**Purpose**: Links this surface’s external id to a specific existing member id.

**Data flow**: It receives external id and member id, verifies the member belongs to this workspace, inserts a surface_identity row, logs duplicate races, and returns the member id or null.

**Call relations**: link_member delegates here after resolving an email.

*Call graph*: called by 1 (link_member); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 2380–2397)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links an external id to a member by email, creating a new member when the verified email matches the workspace’s domain.

**Data flow**: It receives external id and email, tries linking an existing member, compares the email domain to the workspace domain, creates a member if allowed, and links again.

**Call relations**: Slack member resolution uses this when a channel has verified the user’s email.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 2399–2409)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the standard query for finding a conversation by this surface’s queue key.

**Data flow**: It receives a queue key and returns a select statement for conversation id, member id, audience, and surface label in this workspace and surface.

**Call relations**: conversation_for, find_conversation, and terminal_op_body use this shared lookup.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 2411–2417)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for a surface queue key without creating one.

**Data flow**: It receives a queue key, runs _conversation_lookup, and returns the conversation id or null.

**Call relations**: Slack and UFO routes call it when they must check an existing thread or workspace file lane.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 4 (_handle_answer_submit, _participating_conversation, workspace_file, workspace_listing); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 2419–2432)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds which agent a conversation is permanently bound to.

**Data flow**: It receives a conversation id, queries this workspace’s conversation row, and returns the agent id or null.

**Call relations**: The web surface uses it when resolving chat routes.

*Call graph*: called by 1 (_resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 2434–2437)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Workspace-bound wrapper for renaming a conversation.

**Data flow**: It receives conversation id and title, then calls the module-level retitle_conversation with this workspace id.

**Call relations**: Slack and web flows call it when a thread or portal action changes the conversation’s visible title.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 4 (_admit_inbound, submit_action, submit_intent, _open_conversation).


##### `SurfaceContext.conversation_for`  (lines 2439–2533)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation for a surface queue key. It also narrows audience safely and chooses the agent for new conversations.

**Data flow**: It receives queue key, audience, optional agent/conversation id, and label, reads any existing row, narrows or relabels it if needed, or inserts a new conversation with the selected agent.

**Call relations**: Most ingest and portal flows call this before admit; it relies on _conversation_lookup and _surface_agent.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, _handle_answer_submit, channel, workspace_upload, submit_action, submit_intent, _open_conversation (+1 more)); 9 external calls (insert, select, update, workspace_tx, log, audience_member, narrow_audience, parse_audience, uuid4).


##### `SurfaceContext._surface_agent`  (lines 2535–2547)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Finds the agent bound to this surface installation, falling back to the workspace main agent.

**Data flow**: It queries surface_installation for this workspace and surface; if no binding exists, it calls _main_agent.

**Call relations**: conversation_for uses it when creating a conversation without an explicit agent choice.

*Call graph*: calls 1 internal fn (_main_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 2549–2580)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Decides whether the agent should respond to an unaddressed ambient message. It fails open so likely user requests are not silently dropped.

**Data flow**: It receives the message and recent history, asks the ambient reply classifier with a timeout, logs the result, and returns true unless the classifier clearly says no reply.

**Call relations**: Slack and iMessage use it before admitting background channel chatter as a turn.

*Call graph*: called by 2 (_admit_message, _ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext.admit`  (lines 2582–2617)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, comment:
```

**Purpose**: Admits a surface message as a member into the turn system. It is the main privileged write path for human speech.

**Data flow**: It receives conversation id, body, idempotency key, context, speaker, optional intent/comment/runtime config, forwards them to the injected MemberAdmitter, and returns Admitted.

**Call relations**: Slack, iMessage, UFO, web, and sample surfaces call it after resolving identity and conversation.

*Call graph*: called by 11 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, _handle_answer_submit, _channel_message, _send, submit_action, submit_intent, _admit_chat (+1 more)).


##### `SurfaceContext.connect_url`  (lines 2619–2625)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates a provider connection authorization URL for a turn and member.

**Data flow**: It receives turn id and member id, loads the installed connect flow, raises a user-facing invalid-request error if unavailable, and returns the handoff URL.

**Call relations**: Slack, iMessage, and web connect controls use it when a terminal asks the member to connect an account.

*Call graph*: called by 3 (_terminal_text, _handle_connect_click, connect_handoff); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.held_accounts`  (lines 2627–2647)

```
async def held_accounts(self, owner_member_id: UUID) -> dict[str, str]
```

**Purpose**: Lists a member’s own connected accounts by provider, choosing the latest label per provider.

**Data flow**: It receives owner member id, queries connection rows, orders by update time, and returns a provider-to-label map.

**Call relations**: The web surface uses it to render connect controls.

*Call graph*: called by 1 (_connect_controls); 2 external calls (select, workspace_tx).


##### `SurfaceContext.connect_available`  (lines 2649–2656)

```
def connect_available(self) -> bool
```

**Purpose**: Reports whether the deployment has the connect flow configured.

**Data flow**: It tries to load the installed connect flow and returns false if it is unavailable, true otherwise.

**Call relations**: The web surface checks this before showing connect-related controls or labels.

*Call graph*: called by 3 (_connect_controls, _events, _provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connect_label`  (lines 2658–2660)

```
def connect_label(self, provider: str) -> str
```

**Purpose**: Returns the member-facing label for a connect provider.

**Data flow**: It receives a provider id, loads the installed connect flow, and asks it for the label.

**Call relations**: The web surface uses it when naming providers in UI.

*Call graph*: called by 1 (_provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connector_catalog`  (lines 2662–2664)

```
async def connector_catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads a page from the catalog of connector providers available in this deployment.

**Data flow**: It receives search text, limit, and cursor, forwards them to the connector registry, and returns a CatalogPage.

**Call relations**: The web connector catalog route calls this directly.

*Call graph*: called by 1 (connector_catalog).


##### `SurfaceContext.admitted_body`  (lines 2666–2691)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Finds the message body previously admitted under an idempotency key. This helps a surface resolve racing button clicks.

**Data flow**: It receives an idempotency key, first searches turns, then inbound_message rows, and returns the stored body or null.

**Call relations**: Slack and web admission flows use it to confirm which answer or chat message actually landed.

*Call graph*: called by 3 (_handle_answer_submit, _unseen_tail, _admit_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 2693–2707)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member who owns the conversation containing a turn. Live surfaces use this to gate tail access.

**Data flow**: It receives a turn id, joins turn to conversation in this workspace, and returns the conversation member id or null.

**Call relations**: Sample and web surfaces call it before letting a member stream a turn.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 2709–2715)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops a running turn in an already-authorized conversation.

**Data flow**: It receives conversation and turn ids, forwards workspace/conversation/turn to the injected TurnStopper, and returns Stopped.

**Call relations**: UFO and web stop routes call this when a member presses stop.

*Call graph*: called by 2 (_channel_stop, _stop_chat).


##### `SurfaceContext.retract_arrival`  (lines 2717–2735)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Retracts a pending message that has not yet been consumed by a turn, but only for the member who sent it.

**Data flow**: It receives conversation id, arrival id, and member id, deletes the matching unconsumed inbound_message row, and returns whether one row was deleted.

**Call relations**: The UFO surface unsend path calls it for queued member messages.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 2737–2754)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn is finished according to the database. Missing turns count as terminal.

**Data flow**: It receives a turn id, reads the turn status in this workspace, and returns true if absent or in a terminal status.

**Call relations**: The UFO surface uses it to avoid posting progress after the final answer has already been delivered.

*Call graph*: called by 1 (_channel_message); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 2756–2773)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Finds the newest turn in a conversation.

**Data flow**: It receives conversation id, queries turns ordered by sequence descending, and returns the latest turn id or null.

**Call relations**: Slack, UFO, and web surfaces use it to resume streams, resolve chat state, or attach follow-up actions.

*Call graph*: called by 6 (_participating_conversation, _channel_message, _channel_op_reply, _channel_stop, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.absorbing_turn`  (lines 2775–2819)

```
async def absorbing_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Checks whether a new message would fold into an existing live turn rather than start its own. It also checks spend and balance gates.

**Data flow**: It receives conversation id, finds the oldest non-terminal turn, rejects parked or gated turns, evaluates spend and balance, and returns the live turn id or null.

**Call relations**: Slack uses this before deciding whether ambient handling should treat a message as joining a live turn.

*Call graph*: called by 1 (_folds_into_live_turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.tail`  (lines 2821–2827)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of turn frames for surfaces that keep a connection to the member.

**Data flow**: It receives turn id and optional cursor, delegates to the injected TurnTailer, and returns an async context manager for frame iteration.

**Call relations**: Debugger, sample, web, and panel flows call it to stream live turn output.

*Call graph*: called by 6 (_events, _surface_frames, _intent_result, submit_action, _events, object_write).


##### `SurfaceContext.latest_activity`  (lines 2829–2833)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Peeks at the latest activity retained for a running turn.

**Data flow**: It receives a turn id, delegates to the injected TurnTailer, and returns Activity or null.

**Call relations**: The web agents status endpoint calls it after agent_turn_statuses reports a running turn.

*Call graph*: called by 1 (agents_status).


##### `SurfaceContext.spend_rollup`  (lines 2835–2838)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace-wide usage and cost data for a time window or all time.

**Data flow**: It receives optional window seconds, opens a workspace transaction, asks SpendRollup to read, and returns SpendReport.

**Call relations**: Sample and web usage views call it for workspace billing information.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 2840–2855)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded file into a conversation’s workspace before the turn runs. It enforces a maximum size while reading the stream.

**Data flow**: It receives conversation id, relative path, and byte chunks, accumulates chunks up to the limit, then writes the final bytes through the sandbox carrier.

**Call relations**: Slack, iMessage, sample, UFO, and web upload paths call it for inbound attachments.

*Call graph*: called by 5 (_downloaded_files, _surface_ingest, _download_files, workspace_upload, _deliver_uploads).


##### `SurfaceContext.render_preview`  (lines 2857–2885)

```
async def render_preview(self, kind: str, data: bytes) -> bytes | None
```

**Purpose**: Asks an external preview service to turn uploaded document bytes into a PNG thumbnail. It returns nothing if previewing is unavailable or fails.

**Data flow**: It receives file kind and bytes, posts them with rendering options to the preview service, checks for a PNG response, and returns PNG bytes or null.

**Call relations**: The web preview route calls it so compose screens can show file previews without storing them.

*Call graph*: called by 1 (preview); 2 external calls (AsyncClient, dumps).


##### `SurfaceContext.list_agents`  (lines 2887–2926)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists active agents in the workspace, main agent first. It supplies the portal’s agent chooser data.

**Data flow**: It queries non-archived agent rows, orders them, and returns AgentSummary objects with model, visibility, icon, owner, and purpose details.

**Call relations**: Sites and web surfaces use it for audience checks, app lists, frames, and subagent views.

*Call graph*: called by 5 (_shipped_frame, frame, web_audience, _created_apps, _subagent_nodes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_archived_agents`  (lines 2928–2959)

```
async def list_archived_agents(self) -> tuple[ArchivedAgent, ...]
```

**Purpose**: Lists archived agents so the portal can offer restore or history views.

**Data flow**: It queries archived agent rows, uses archived display names where available, orders newest first, and returns ArchivedAgent objects.

**Call relations**: The web agents index calls it alongside active agent listings.

*Call graph*: called by 1 (agents_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_extension_agent_ids`  (lines 2961–2976)

```
async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]
```

**Purpose**: Finds agents that have private extension conversations with a member.

**Data flow**: It receives member id, queries distinct agent ids from private extension conversations for that member, and returns a frozen set.

**Call relations**: The web audience calculation uses it to decide which extension-backed agents a member can see.

*Call graph*: called by 1 (web_audience); 3 external calls (select, workspace_tx, conversation_audience).


##### `SurfaceContext.agent_detail`  (lines 2978–3039)

```
async def agent_detail(self, agent_id: UUID, member_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads full settings for one agent, including prompt digest, bound surfaces, and outstanding setup needs.

**Data flow**: It receives agent and member ids, queries the agent row and surface bindings, asks pending_setup for this member, and returns AgentDetail or null.

**Call relations**: Web panel routes use it to render agent settings and complete agent specs.

*Call graph*: called by 2 (_complete_agent_spec, agent_settings); 5 external calls (__init__, select, workspace_tx, pending_setup, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 3041–3053)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for a registered object kind, such as list fields and form schema.

**Data flow**: It receives a kind name, looks it up in the object registry, and returns PortalKind or null.

**Call relations**: Web object and action routes call it before listing, writing, or showing action views.

*Call graph*: called by 3 (_object_gate, action_views, object_write); 1 external calls (__init__).


##### `SurfaceContext.object_actions`  (lines 3055–3070)

```
def object_actions(self, kind: str, binding: 'ActionBinding', *, name: str | None=None, generation: UUID | None=None) -> tuple[ActionView, ...]
```

**Purpose**: Builds the action controls the portal should show for an object target.

**Data flow**: It receives kind, action binding, optional name and generation, then delegates to presented_action_views and returns ActionView records.

**Call relations**: Web panels and administration pages call it to render available actions, while dispatch later rechecks authority.

*Call graph*: called by 7 (submit_action, action_views, admin_index, workspace_credentials, workspace_first_run, workspace_memory, workspace_team); 1 external calls (presented_action_views).


##### `SurfaceContext.frame_admits`  (lines 3072–3075)

```
def frame_admits(self, callable_id: str) -> bool
```

**Purpose**: Checks whether an embedded app frame may post a callable action.

**Data flow**: It receives a callable id and returns whether it is in the allowed frame-callable set.

**Call relations**: Web panel preparation and submission use it before accepting frame-originated actions.

*Call graph*: called by 2 (_prepare_panel_intent, submit_action).


##### `SurfaceContext.agent_skills`  (lines 3077–3116)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists deploy and member-authored skills available for an agent. Member skills that shadow deploy skills are skipped.

**Data flow**: It receives agent id, binds that agent, reads member skills, filters name collisions, and returns PortalSkill records for deploy and member skills.

**Call relations**: The web skills endpoint calls it to show the full loadable skill set.

*Call graph*: called by 1 (skills); 3 external calls (__init__, log, agent).


##### `SurfaceContext.model`  (lines 3119–3123)

```
def model(self) -> 'SurfaceModel | None'
```

**Purpose**: Returns the optional metered model access available to surface routes.

**Data flow**: It reads the stored SurfaceModel reference and returns it or null.

**Call relations**: Routes that need route-time model help gate on this property.


##### `SurfaceContext.memory_available`  (lines 3126–3130)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory search provider is installed.

**Data flow**: It checks whether the stored memory provider is non-null and returns a boolean.

**Call relations**: Web memory pages use it before calling search_memory or recent_memory.


##### `SurfaceContext.search_memory`  (lines 3132–3142)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches readable memory for a source reader and query set. It is an error to call when memory is unavailable.

**Data flow**: It receives a SourceReader and queries, checks the memory provider exists, forwards the search, and returns memory matches.

**Call relations**: The web workspace memory view calls it after checking memory_available.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 3144–3157)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items for readable subjects, optionally filtered by kind and paged by cursor.

**Data flow**: It receives subjects, limit, optional kinds and cursor, checks the memory provider, delegates to list_recent, and returns a listing page.

**Call relations**: The web memory page and recall helpers use it for browsing memory.

*Call graph*: called by 2 (_recalled, workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 3160–3165)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the memory item kinds that can be listed by the installed provider.

**Data flow**: It checks that memory exists, asks the provider for listable kinds, and returns them.

**Call relations**: Memory UI filters use this to offer the provider’s actual categories.


##### `SurfaceContext.agent_spend`  (lines 3167–3172)

```
async def agent_spend(self, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Reads usage and cap information for one agent.

**Data flow**: It receives agent id and optional time window, opens a workspace transaction, asks SpendRollup.read_agent, and returns AgentSpendReport.

**Call relations**: Agent billing or administration views can call it to show per-agent cost.

*Call graph*: 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.member_spend`  (lines 3174–3179)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads usage and cap information for one member.

**Data flow**: It receives member id and optional time window, opens a workspace transaction, asks SpendRollup.read_member, and returns MemberSpendReport.

**Call relations**: The web workspace usage page calls it for member-specific billing information.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 3181–3233)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to one agent that the viewer may see.

**Data flow**: It receives agent id, member id, and admin flag, queries connector grants and connections with visibility rules, and returns ConnectionView objects.

**Call relations**: The web connections page calls it for an agent’s connected accounts.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.list_connections`  (lines 3235–3317)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists the workspace’s connector accounts and which live agents use each one.

**Data flow**: It receives member id and admin flag, queries visible connections with live agent grants, groups agents per account, and returns ConnectionPoolView objects.

**Call relations**: The web connection pool and provider helpers call it.

*Call graph*: called by 2 (_held_providers, connection_pool); 7 external calls (__init__, __init__, and_, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.github_coverage`  (lines 3319–3367)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports which GitHub integration paths are present: API connection, git push credentials, and sources.

**Data flow**: It receives member id and admin flag, applies visibility rules, checks connection/source existence and credential slots, and returns GithubCoverageView.

**Call relations**: The web GitHub coverage and first-run pages call it to explain setup completeness.

*Call graph*: called by 3 (_held_providers, github_coverage, workspace_first_run); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.recent_object_changes`  (lines 3369–3408)

```
async def recent_object_changes(self, limit: int) -> tuple[ObjectChange, ...]
```

**Purpose**: Reads recent object change journal rows for admin audit views.

**Data flow**: It receives a limit, queries newest object_change rows in the workspace, normalizes timestamps, and returns ObjectChange records.

**Call relations**: The web object changes page calls it after performing its own admin gate.

*Call graph*: called by 1 (object_changes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversation_artifacts`  (lines 3410–3479)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists recent files shared by one conversation. Authorization is expected to be checked by the caller.

**Data flow**: It receives conversation id and limit, queries shared artifacts joined to turns/conversation/member, gets the conversation source, and returns ListedArtifact records.

**Call relations**: The web surface uses it for transcript aids and conversation slot context.

*Call graph*: called by 2 (_project_slot_context, _transcript_aids); 5 external calls (__init__, __init__, __init__, select, workspace_tx).


##### `SurfaceContext.agent_turn_statuses`  (lines 3481–3609)

```
async def agent_turn_statuses(self, agent_ids: Sequence[UUID], member_id: UUID) -> tuple[AgentTurnStatus, ...]
```

**Purpose**: Returns compact live/last-failed status for several agents, using only conversations the member may read.

**Data flow**: It receives agent ids and member id, queries live turns and latest readable activity per agent, preserves input order, and returns AgentTurnStatus records.

**Call relations**: The web agents_status route uses it, then may call latest_activity for running turns.

*Call graph*: called by 1 (agents_status); 5 external calls (__init__, case, select, workspace_tx, readable_audiences).


##### `SurfaceContext.agent_setup`  (lines 3611–3650)

```
async def agent_setup(self, agent_id: UUID, member_id: UUID) -> SetupState
```

**Purpose**: Computes what an agent still needs before it works, such as connections, credentials, or standing orders.

**Data flow**: It receives agent and member ids, defines an inner object-check helper, binds workspace context, asks setup_state, and returns SetupState.

**Call relations**: Web setup and starter pages call it; its inner armed helper reads object registry rows.

*Call graph*: called by 2 (agent_setup, workspace_starters); 2 external calls (setup_state, ws).


##### `SurfaceContext.agent_setup.armed`  (lines 3633–3647)

```
async def armed(kind: str, name: str | None) -> ArmedOrder
```

**Purpose**: Checks whether a declared standing order is already present for an agent setup step.

**Data flow**: It receives object kind and optional name, reads either that exact object or a page of objects, extracts schedule when present, and returns ArmedOrder.

**Call relations**: SurfaceContext.agent_setup passes this callback into setup_state so extension-owned order kinds can be checked through their own stores.

*Call graph*: calls 2 internal fn (list_member_objects, member_object); 2 external calls (__init__, __init__).


##### `SurfaceContext.list_member_objects`  (lines 3652–3678)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Lists a page of member-visible objects of one kind for an agent.

**Data flow**: It receives kind, agent, member, admin flag, and query, checks the registry and listability, binds agent scope, stamps supported fields, and returns an ObjectPage or null.

**Call relations**: Web object index routes and agent setup call it.

*Call graph*: called by 3 (armed, _bound_page, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 3680–3695)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one member-visible object detail by kind and name.

**Data flow**: It receives kind, name, agent, member, and admin flag, checks registry readability, binds agent scope, and returns the object or null.

**Call relations**: Web object detail routes and agent setup use it.

*Call graph*: called by 2 (armed, object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 3697–3719)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants or rows tied to a conversation for a member.

**Data flow**: It receives kind, agent id, conversation id, member id, admin flag, and limit, checks registry support, binds agent scope, and returns rows or null.

**Call relations**: The web surface uses it when projecting conversation slot context.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 3721–3752)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists member-fillable declared credential slots and whether each is filled, never the secret values.

**Data flow**: It reads filled slot names from the credential table, maps declared slots to object names, filters to member-fillable slots, and returns CredentialSlotView records.

**Call relations**: Web credential pages and intent-refusal UI call it.

*Call graph*: called by 2 (_intent_refusal, workspace_credentials); 4 external calls (__init__, select, workspace_tx, named_slots).


##### `SurfaceContext.workspace_domain`  (lines 3754–3760)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Returns the workspace’s domain used for domain-based joins, or null for personal-email workspaces.

**Data flow**: It opens a workspace transaction, asks the seats helper for the domain, and returns it.

**Call relations**: join_member and is_operator_workspace depend on this shared domain answer.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.list_members`  (lines 3762–3770)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Lists the workspace roster sorted by email.

**Data flow**: It opens a workspace transaction, gets a Seats snapshot, sorts member entries by email, and returns them.

**Call relations**: The web team page calls it after applying its route-level permissions.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 3772–3819)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists live source bindings visible to a member, including sync health and connector binding identity fields.

**Data flow**: It receives member id and admin flag, queries non-removed sources with visibility filters, projects binding fields from config, and returns SourceView objects.

**Call relations**: The web workspace sources page calls it; _binding_fields supplies connector-specific identity details.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.spend_caps`  (lines 3821–3870)

```
async def spend_caps(self) -> tuple[SpendCapView, ...]
```

**Purpose**: Lists all spend caps in the workspace with human-readable subject names.

**Data flow**: It queries spend_cap rows joined to agents or members, orders them, and returns SpendCapView objects.

**Call relations**: The web admin index calls it for billing governance.

*Call graph*: called by 1 (admin_index); 4 external calls (__init__, and_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 3872–3888)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations bound to this workspace and the agent each routes to.

**Data flow**: It queries surface_installation rows ordered by surface and returns InstallationSummary objects.

**Call relations**: Web administration, first-run, and surface setup pages call it.

*Call graph*: called by 4 (_held_providers, admin_index, workspace_first_run, workspace_surfaces); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 3890–3939)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists recent conversations across all surfaces for debug views.

**Data flow**: It builds an activity aggregate from turns, joins conversations and members, orders by newest activity, and returns ConversationSummary records.

**Call relations**: The debugger surface calls it to show workspace conversation metadata.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 3941–3964)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Workspace-bound wrapper for listing one agent’s conversations.

**Data flow**: It receives listing filters and delegates to ConversationDirectory.list with this workspace id.

**Call relations**: The web surface uses it for chat rails, named conversation resolution, and conversation pages.

*Call graph*: called by 4 (_member_chat, _named, _resolve_chat, conversations); 1 external calls (__init__).


##### `SurfaceContext.readable_conversation`  (lines 3966–4007)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Checks whether a member may read a conversation’s content. Admins need a recent recorded disclosure for another member’s private conversation.

**Data flow**: It receives conversation, agent, member, and admin flag, reads the conversation audience, checks normal readable audiences, then checks transcript_access when admin disclosure is possible.

**Call relations**: The web surface calls it before serving turns, transcripts, files, and subagent content.

*Call graph*: called by 1 (_readable_conversation); 6 external calls (now, select, workspace_tx, audience_member, parse_audience, readable_audiences).


##### `SurfaceContext.conversation_audience`  (lines 4009–4019)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience bound to a conversation on a specific agent.

**Data flow**: It receives conversation and agent ids, queries the audience string in this workspace, parses it, and returns an Audience or null.

**Call relations**: The web slot-context builder calls it when constructing authorized conversation context.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, workspace_tx, parse_audience).


##### `SurfaceContext.conversation_subagent_turns`  (lines 4021–4058)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists all subagent turns spawned beneath a conversation’s turns, including nested descendants.

**Data flow**: It receives conversation id and limit, builds a recursive descendant query, reads turn rows in breadth-first order, and converts them to Turn records.

**Call relations**: Web event and transcript-aid flows call it to nest subagent work under the parent conversation.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_events, _slot_target, _transcript_aids); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 4060–4076)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists a conversation’s recent turns in admission order.

**Data flow**: It receives conversation id and limit, queries newest turn rows by sequence, reverses them to oldest-first, and converts each to Turn.

**Call relations**: Debugger and web transcript views call it after checking conversation readability.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _transcript_aids); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 4078–4113)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Identifies transcript message references that came from machine-origin prompts rather than member prose.

**Data flow**: It receives conversation id, unions matching turn ids and inbound message ids for scheduled or spawn-result admissions, and returns them as strings.

**Call relations**: Web conversation-message builders use it to avoid rendering machine envelopes as member speech.

*Call graph*: called by 2 (_conversation_messages, _history_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 4115–4165)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn with its ledger rows and direct child subagent turns.

**Data flow**: It receives turn id, reads the turn row, child turn rows, and ledger entries in one transaction, and returns TurnDetail or null.

**Call relations**: Debugger and web routes call it for detailed turn pages, live events, and member-turn resolution.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.turn_steps`  (lines 4167–4180)

```
async def turn_steps(self, turn_id: UUID) -> tuple[TurnStep, ...] | None
```

**Purpose**: Reads recorded durable workflow steps for a turn, if the turn belongs to this workspace.

**Data flow**: It receives turn id, checks the workspace turn row and running attempt id, then asks the injected TurnStepSource for steps.

**Call relations**: The debugger turn_steps route calls it.

*Call graph*: called by 1 (turn_steps); 2 external calls (select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 4182–4223)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Lists admitted messages not yet visible in the written transcript. This keeps reloads from hiding messages admitted during a running turn.

**Data flow**: It receives conversation id and optional draining turn id, checks ownership, reads unconsumed or currently-drained inbound_message rows, and returns QueuedArrival records.

**Call relations**: The web conversation message projection calls it after reading turns.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 4225–4257)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Reads speaker attribution for member-admitted queued messages, including ones already drained into a running turn.

**Data flow**: It receives conversation id, checks ownership, reads member-admitted inbound_message contexts, parses sender and question fields, and returns SpokenArrival records.

**Call relations**: Web conversation and history projections use it to label folded messages correctly.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (_conversation_messages, _history_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.keyed_admissions`  (lines 4259–4295)

```
async def keyed_admissions(self, conversation_id: UUID) -> tuple[KeyedAdmission, ...]
```

**Purpose**: Lists all messages in a conversation admitted with idempotency keys. It helps projections recognize their own admitted messages.

**Data flow**: It receives conversation id, checks ownership, unions keyed turn rows and keyed inbound_message rows, and returns KeyedAdmission records.

**Call relations**: The web transcript-aids builder uses it beside transcript refs.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_transcript_aids); 4 external calls (__init__, select, union_all, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 4297–4308)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads the durable transcript blob for a conversation, after verifying the conversation belongs to this workspace.

**Data flow**: It receives conversation id, checks ownership, loads the transcript blob by key, decodes it, and returns Conversation or null if absent.

**Call relations**: Debugger and web surfaces call it for transcript, message, slot, and subagent projections.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 4 (conversation_transcript, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 4310–4321)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists persisted compaction record indices for a conversation. Compactions are saved summaries of earlier transcript windows.

**Data flow**: It receives conversation id, checks ownership, lists matching blob keys, extracts numeric indices, sorts them, and returns the tuple.

**Call relations**: Debugger and web message views call it to show or reason about transcript compactions.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_compactions, _conversation_messages).


##### `SurfaceContext.read_compaction`  (lines 4323–4329)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one full compaction record for a conversation.

**Data flow**: It receives conversation id and index, checks ownership, asks the transcript helper to read the record, and returns it or null.

**Call relations**: Debugger and web history-message flows call it when expanding compacted transcript history.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (compaction_record, _history_messages); 1 external calls (read_compaction_record).


##### `SurfaceContext.read_compaction_after`  (lines 4331–4339)

```
async def read_compaction_after(self, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the “after” message window of a compaction record.

**Data flow**: It receives conversation id and index, checks ownership, delegates to the transcript helper, and returns messages or null.

**Call relations**: The web verified-earlier check uses it to compare later history against compacted summaries.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_verified_earlier); 1 external calls (read_compaction_after).


##### `SurfaceContext.list_workspace_files`  (lines 4341–4347)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files currently in a conversation’s sandbox workspace.

**Data flow**: It receives conversation id, checks ownership, asks the sandbox manager for entries, and returns workspace files or an empty tuple.

**Call relations**: Debugger, UFO, and web file listing routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 3 (workspace_files, workspace_listing, conversation_attachment).


##### `SurfaceContext.conversation_changes`  (lines 4349–4355)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last recorded workspace file changes for a conversation.

**Data flow**: It receives conversation id, checks ownership, then returns recorded workspace changes or the no-change value.

**Call relations**: The web slot context projection uses it to show changed files.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 4357–4366)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one file from a conversation’s sandbox workspace.

**Data flow**: It receives conversation id and relative path, checks ownership, asks the sandbox manager for a byte stream, and returns the stream or null.

**Call relations**: Debugger, UFO, and web attachment routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 3 (workspace_file, workspace_file, conversation_attachment).


##### `SurfaceContext.terminal_connect`  (lines 4368–4374)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Registers a member’s held terminal connection for a conversation.

**Data flow**: It receives conversation id, working directory, optional member id, and runtime id, and records the terminal binding in the sandbox terminal manager.

**Call relations**: Terminal-capable surfaces call it when a client connection opens.


##### `SurfaceContext.terminal_disconnect`  (lines 4376–4377)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes a conversation’s terminal connection binding.

**Data flow**: It receives conversation id and tells the sandbox terminal manager to disconnect it.

**Call relations**: Terminal-capable surfaces pair this with terminal_connect when the client connection closes.


##### `SurfaceContext.claim_terminal`  (lines 4379–4385)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Claims a connected terminal for a fresh conversation before the turn opens a sandbox.

**Data flow**: It receives conversation id and working directory, asks the sandbox manager to claim an unbound terminal, and returns whether the claim succeeded.

**Call relations**: The UFO surface calls it when admitting messages or sends that should use the member’s terminal.

*Call graph*: called by 2 (_channel_message, _send).


##### `SurfaceContext.next_terminal_op`  (lines 4387–4394)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for or reads the next terminal operation requested by a turn.

**Data flow**: It receives conversation id and optional operation id to exclude, asks the terminal manager for the next op, and returns it.

**Call relations**: Terminal streaming routes use it to send run directives to the member’s client.


##### `SurfaceContext.terminal_resolve`  (lines 4396–4410)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Completes a pending terminal operation with the client’s reply or failure.

**Data flow**: It receives conversation id, operation id, reply bytes, optional failure text, and member id, forwards them to the terminal manager, and returns whether it resolved.

**Call relations**: The UFO channel operation reply route calls it.

*Call graph*: called by 1 (_channel_op_reply).


##### `SurfaceContext.terminal_op_body`  (lines 4412–4424)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads staged bytes for a pending terminal operation without creating a conversation.

**Data flow**: It receives queue key, operation id, and member id, looks up the conversation, then asks the terminal manager for staged bytes, returning bytes or null.

**Call relations**: The UFO op_body route calls it for clients that fetch operation payloads separately.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 4426–4439)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface.

**Data flow**: It receives a peer surface name, queries surface_installation, and returns the installation id or null.

**Call relations**: The debugger workspace metadata route uses it to show linked surface installation details.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 4442–4451)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Yields a raw workspace database transaction for trusted surface code that needs to read extension-owned tables.

**Data flow**: It opens workspace_tx, yields the connection inside an async context manager, commits on normal exit, and rolls back on errors.

**Call relations**: Sites and Slack surface code use it for surface-specific reads that do not have an ExtensionContext.

*Call graph*: called by 2 (_viewer_is_admin, _folds_into_live_turn); 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 4453–4463)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace.

**Data flow**: It receives conversation id, queries the conversation table for this workspace, and returns true if found.

**Call relations**: Transcript, compaction, queued-arrival, workspace-file, and related read methods call it before touching blob or sandbox data.

*Call graph*: called by 10 (arrival_speakers, conversation_changes, keyed_admissions, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_compaction_after, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 4465–4485)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the standard select statement for turn rows used by several detail and listing reads.

**Data flow**: It takes no input and returns a SQL select containing all fields needed to construct a Turn model.

**Call relations**: conversation_subagent_turns, list_turns, and turn_detail extend this query with their own filters.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 4487–4507)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database turn row into the typed Turn model.

**Data flow**: It receives a row, copies scalar fields, validates context and terminal JSON when present, and returns a Turn object.

**Call relations**: Turn listing and detail methods call it after using _turn_query.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.reserve_address`  (lines 4539–4599)

```
async def reserve_address(self, surface: str, address: str, member_id: UUID, claim_expires_at: datetime) -> AddressClaimState
```

**Purpose**: Lets a tool reserve an addressed-surface address for a workspace member until they prove it.

**Data flow**: It receives surface, address, member, and expiry, checks the surface was declared as addressed, upserts or takes over eligible expired reservations, and returns RESERVED, LINKED, or TAKEN.

**Call relations**: Extension tools use this manifest-scoped access object rather than writing surface_address rows directly.

*Call graph*: 7 external calls (__init__, now, and_, or_, select, owner_tx, ws_current).


##### `SurfaceInstallationAccess.installation`  (lines 4601–4614)

```
async def installation(self, surface: str) -> str | None
```

**Purpose**: Reads the current workspace’s installation id for a declared surface.

**Data flow**: It receives a surface name, verifies it was declared, queries surface_installation under the current workspace, and returns the installation id or null.

**Call relations**: Tool handlers call this when they need to know whether their declared surface is installed.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `SurfaceInstallationAccess.bind`  (lines 4616–4628)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Binds a declared surface installation to the current workspace from a tool context.

**Data flow**: It receives surface and installation id, verifies declaration, determines whether ingress routes by installation, and calls the shared installation binding helper.

**Call relations**: Extension tools use this; surface OAuth callbacks use the parallel SurfaceContext.bind_installation path.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 4640–4651)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Resolves which workspace owns a shared surface installation id that routes ingress.

**Data flow**: It receives an installation id, queries owner-scoped surface_installation rows for this surface and routes_ingress, and returns workspace id or null.

**Call relations**: Slack workspace resolution calls it before binding a SurfaceContext.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.addressed_workspace`  (lines 4653–4666)

```
async def addressed_workspace(self, address: str) -> UUID | None
```

**Purpose**: Resolves which workspace owns an addressed-surface address.

**Data flow**: It receives an address, queries owner-scoped surface_address rows for this surface, and returns workspace id or null.

**Call relations**: SurfaceListenerContext.addressed uses it for fleet-wide addressed listeners such as iMessage.

*Call graph*: 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 4668–4680)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens sealed credential authorization before a workspace has been resolved. It returns null instead of raising for invalid seals.

**Data flow**: It receives sealed state, checks for a credential store, tries to open the request, and returns CredentialRequestState or null.

**Call relations**: Slack workspace resolution uses it during OAuth-style pre-binding handshakes.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 4682–4698)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential for a workspace during shared surface resolution.

**Data flow**: It receives workspace id and slot, verifies the slot was declared and store exists, checks the workspace exists inside workspace scope, then returns the credential value.

**Call relations**: Slack request authentication uses it to fetch the signing secret before a SurfaceContext is fully bound.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceListenerContext.workspace`  (lines 4745–4753)

```
async def workspace(self, installation_id: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Temporarily binds a listener event to the workspace that owns an installation id.

**Data flow**: It receives installation id, verifies this process still owns the listener lease, resolves workspace id, enters workspace scope, and yields SurfaceContext or null.

**Call relations**: Persistent surface listeners use this around each installation-routed provider event.

*Call graph*: 1 external calls (ws).


##### `SurfaceListenerContext.addressed`  (lines 4756–4767)

```
async def addressed(self, address: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Temporarily binds a listener event to the workspace that owns a sender address.

**Data flow**: It receives address, verifies listener ownership, resolves workspace by address, enters workspace scope, and yields SurfaceContext or null.

**Call relations**: The iMessage listener calls it while processing addressed incoming events.

*Call graph*: called by 1 (_process_event); 1 external calls (ws).


##### `SurfaceListenerContext.cursor`  (lines 4769–4784)

```
async def cursor(self, installation_id: str) -> int | None
```

**Purpose**: Reads the stored stream cursor for this listener and installation.

**Data flow**: It receives installation id, queries the fleet cursor row for this surface, returns the sequence if the installation matches, otherwise null.

**Call relations**: The iMessage listener calls it at startup to decide where to resume.

*Call graph*: called by 1 (listen); 2 external calls (select, owner_tx).


##### `SurfaceListenerContext.store_cursor`  (lines 4786–4809)

```
async def store_cursor(self, installation_id: str, sequence: int) -> None
```

**Purpose**: Stores the listener’s latest stream sequence for an installation.

**Data flow**: It receives installation id and sequence, upserts the fleet cursor row for this surface, and records the new position.

**Call relations**: The iMessage listener stores cursors after catching up and after processing events.

*Call graph*: called by 2 (_catch_up, _process_event); 1 external calls (owner_tx).


##### `SurfaceListenerContext.clear_cursor`  (lines 4811–4818)

```
async def clear_cursor(self) -> None
```

**Purpose**: Deletes this surface listener’s stored stream cursor.

**Data flow**: It takes no input, deletes the cursor row for this surface, and returns nothing.

**Call relations**: The iMessage listener calls it when it needs to restart from the provider’s head.

*Call graph*: called by 1 (listen); 2 external calls (delete, owner_tx).


##### `SurfaceListenerRunner.run`  (lines 4844–4882)

```
async def run(self) -> None
```

**Purpose**: Runs a persistent surface listener only while this process owns the fleet-wide lease. It restarts or parks according to failure type.

**Data flow**: It loops forever, waits for ownership, starts the listener and an ownership watcher, reacts to whichever finishes first, logs failures, sleeps or parks as needed, and cancels tasks on exit.

**Call relations**: It drives SurfaceListenerContext for listener extensions and uses the ownership helper methods to coordinate across replicas.

*Call graph*: calls 2 internal fn (_wait_until_not_owned, _wait_until_owned); 9 external calls (__init__, CancelledError, create_task, ensure_future, gather, sleep, wait, emit_metric, log).


##### `SurfaceListenerRunner._wait_until_not_owned`  (lines 4884–4889)

```
async def _wait_until_not_owned(self) -> None
```

**Purpose**: Waits until this process no longer owns the listener lease.

**Data flow**: It repeatedly checks ownership, returns when ownership is false, and sleeps between checks.

**Call relations**: SurfaceListenerRunner.run starts it alongside the listener to stop work when another process takes over.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._wait_until_owned`  (lines 4891–4895)

```
async def _wait_until_owned(self) -> None
```

**Purpose**: Waits until this process acquires the listener lease.

**Data flow**: It repeatedly checks ownership, returns when ownership is true, and sleeps between checks.

**Call relations**: SurfaceListenerRunner.run calls it before starting the listener.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._owned_on_tick`  (lines 4897–4906)

```
async def _owned_on_tick(self) -> bool | None
```

**Purpose**: Performs one ownership check and handles database errors without crashing the outer loop.

**Data flow**: It calls _owns, returns true/false, or logs SQL errors and returns null.

**Call relations**: Both wait loops call it on each polling tick.

*Call graph*: calls 1 internal fn (_owns); called by 2 (_wait_until_not_owned, _wait_until_owned); 1 external calls (log).


##### `SurfaceListenerRunner._owns`  (lines 4908–4926)

```
async def _owns(self) -> bool
```

**Purpose**: Safely runs the lease claim transaction even if the task is cancelled. This avoids leaving database locks behind.

**Data flow**: It starts _claim as a future, shields it until complete, remembers cancellation, re-raises cancellation after the claim finishes, and otherwise returns the claim result.

**Call relations**: _owned_on_tick calls it; _claim performs the actual database upsert.

*Call graph*: calls 1 internal fn (_claim); called by 1 (_owned_on_tick); 2 external calls (ensure_future, shield).


##### `SurfaceListenerRunner._claim`  (lines 4928–4968)

```
async def _claim(self) -> bool
```

**Purpose**: Attempts to acquire or refresh the fleet-wide listener lease for this surface.

**Data flow**: It computes a new expiry, upserts the surface_listener_claim row if expired or already owned by this instance/token, and returns whether the stored token is this runner’s token.

**Call relations**: _owns calls it as the protected database operation behind listener ownership.

*Call graph*: called by 1 (_owns); 7 external calls (now, timedelta, and_, insert, insert, or_, owner_tx).


##### `SurfaceDeliveryError.__init__`  (lines 4975–4979)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that can carry a provider-requested retry delay.

**Data flow**: It receives a message and optional retry-after seconds, rejects negative delays, stores the delay, and initializes RuntimeError.

**Call relations**: Slack posting code can raise it; writeback pollers read its retry_after_seconds when scheduling retries.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 5042–5066)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for terminal writebacks that are ready to claim.

**Data flow**: It receives the current time and returns a condition requiring terminal turn status, no pending mid-turn replies, and an expired or absent claim.

**Call relations**: writeback_workspaces.due and WritebackPoller._claim use it to find deliverable final replies.

*Call graph*: called by 2 (_claim, due); 3 external calls (and_, exists, or_).


##### `writeback_workspaces`  (lines 5069–5104)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating candidate reader for workspaces that have due terminal writebacks.

**Data flow**: It initializes a cursor, wraps an owner-scoped due query with owner_candidates, and returns an async candidates function.

**Call relations**: WritebackPoller receives this candidate function to decide which workspaces to drain.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 5076–5090)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one paged query for workspace ids with due terminal writebacks.

**Data flow**: It reads the current time, selects grouped workspace ids matching _writeback_due, applies cursor if set, orders and limits the page, and returns the query.

**Call relations**: The owner_candidates wrapper calls this inside writeback_workspaces.candidates.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 5094–5102)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with due terminal writebacks.

**Data flow**: It runs the due reader, wraps to the beginning when the cursor reaches the end, updates the cursor to the last workspace id, and returns ids.

**Call relations**: WritebackPoller.run and drain call this through the poller’s candidates field.


##### `_WritebackDeliveryFailed.__init__`  (lines 5112–5115)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a failed writeback phase with whether it failed during posting or attaching.

**Data flow**: It receives phase and original exception, stores both, and initializes RuntimeError with a readable message.

**Call relations**: WritebackPoller._deliver_claimed raises it so _deliver can apply common retry logic.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 5138–5171)

```
async def run(self) -> None
```

**Purpose**: Continuously drains terminal writebacks in the background, bounded by workspace concurrency.

**Data flow**: It maintains in-flight workspace tasks, asks candidates for more workspace ids, starts drain tasks under a semaphore, logs task failures, sleeps between polls, and cancels tasks during shutdown.

**Call relations**: The application starts this poller for durable surfaces; _drain_workspace does the per-workspace work.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 5173–5182)

```
async def drain(self) -> None
```

**Purpose**: Runs one drain pass for currently due terminal writeback workspaces. This is useful for tests or manual flushing.

**Data flow**: It gets candidate workspace ids, drains them concurrently with a semaphore, gathers results, and raises an ExceptionGroup if any drains failed.

**Call relations**: It shares _drain_workspace with the continuous run loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 5184–5202)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers a batch of terminal writebacks for one workspace.

**Data flow**: It receives workspace id and semaphore, enters workspace scope, claims rows, starts claim-renewal tasks, delivers each row, and cancels renewals afterward.

**Call relations**: run and drain call it; it coordinates _claim, _renew_claim, and _deliver.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 5204–5237)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due terminal writeback rows for this worker.

**Data flow**: It receives workspace id, selects due claimable rows, updates them to claimed with worker id and expiry, and returns their turn ids, reply refs, and last errors.

**Call relations**: _drain_workspace calls it before attempting external delivery.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 5239–5271)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed terminal writeback and records success, lost claim, or retry/failure.

**Data flow**: It receives workspace id, turn id, prior reply ref, and renewal task, times the attempt, calls _deliver_with_lease, handles claim loss and delivery failure, updates retry state, and logs outcome.

**Call relations**: _drain_workspace calls it for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 5273–5302)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while a claim-renewal task keeps the row leased, then marks it delivered.

**Data flow**: It starts _deliver_claimed and waits for either delivery or renewal failure, cancels leftovers, then calls _mark_delivered after delivery completes.

**Call relations**: _deliver calls it; _deliver_claimed performs provider calls and _mark_delivered commits final state.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 5304–5334)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Builds the writeback payload and calls the surface’s post and attach handlers.

**Data flow**: It receives workspace id, turn id, and optional reply ref, builds Writeback, finds the surface spec, skips if no delivery handlers, posts if no ref, records the ref, then attaches files.

**Call relations**: _deliver_with_lease calls it; failures are wrapped as _WritebackDeliveryFailed for retry handling.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 5336–5339)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps a claimed writeback row leased while external delivery is in progress.

**Data flow**: It receives turn id, sleeps for the refresh interval in a loop, and calls _refresh_claim each time.

**Call relations**: _drain_workspace starts one renewal task per claimed row.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 5341–5357)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim expiry on a writeback row.

**Data flow**: It receives turn id, updates the claimed row if still owned by this worker, and raises claim-lost if no row was updated.

**Call relations**: _renew_claim calls it; delivery aborts if renewal proves ownership was lost.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 5359–5411)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the Writeback object handed to a durable surface.

**Data flow**: It receives turn id, reads terminal frame, conversation queue key, surface, agent, and shared artifacts, validates the terminal frame, and returns Writeback plus surface name.

**Call relations**: _deliver_claimed calls it before selecting the surface delivery handlers.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 5413–5425)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Stores the provider’s reply reference after posting but before attachments.

**Data flow**: It receives turn id and reply ref, updates the claimed writeback row if still owned by this worker, and raises claim-lost if not.

**Call relations**: _deliver_claimed calls it so retries can attach to an already-posted message instead of posting again.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 5427–5444)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a terminal writeback as fully delivered and clears its claim.

**Data flow**: It receives turn id, updates the claimed row to delivered with no owner or expiry, and raises claim-lost if the worker no longer owns it.

**Call relations**: _deliver_with_lease calls it after post and attach work completes.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 5446–5495)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Updates a failed writeback for retry or final failure. Provider retry-after values are honored but bounded.

**Data flow**: It receives turn id and wrapped delivery error, computes retry time or age-out failure, truncates last error text, updates the row if still claimed by this worker, and returns outcome details.

**Call relations**: _deliver calls it when _deliver_with_lease reports a post or attach failure.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `mid_turn_reply_workspaces`  (lines 5498–5529)

```
def mid_turn_reply_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating candidate reader for workspaces with due mid-turn replies.

**Data flow**: It initializes a cursor, wraps a due query with owner_candidates, and returns an async candidates function.

**Call relations**: MidTurnReplyPoller uses this candidate function to find workspaces with replies to speak before terminal delivery.

*Call graph*: 1 external calls (owner_candidates).


##### `mid_turn_reply_workspaces.due`  (lines 5504–5515)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one paged query for workspace ids with due mid-turn replies.

**Data flow**: It reads the current time, selects grouped workspace ids matching _mid_turn_reply_due, applies cursor if present, orders and limits results, and returns the query.

**Call relations**: The owner_candidates wrapper calls it inside mid_turn_reply_workspaces.candidates.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); 2 external calls (now, select).


##### `mid_turn_reply_workspaces.candidates`  (lines 5519–5527)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with due mid-turn replies.

**Data flow**: It runs the due reader, wraps cursor to the beginning when needed, updates cursor to the last id returned, and returns workspace ids.

**Call relations**: MidTurnReplyPoller.drain calls it through the poller’s candidates field.


##### `_mid_turn_reply_due`  (lines 5532–5545)

```
def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for mid-turn reply rows that can be claimed.

**Data flow**: It receives the current time and returns true for pending unclaimed rows or claimed rows whose claim expired.

**Call relations**: mid_turn_reply_workspaces.due and MidTurnReplyPoller._claim use it.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `MidTurnReplyPoller.run`  (lines 5573–5579)

```
async def run(self) -> None
```

**Purpose**: Continuously delivers due mid-turn replies in the background.

**Data flow**: It loops forever, calls drain, logs drain failures, and sleeps between polling passes.

**Call relations**: The application starts it for durable surfaces that support mid-turn speaking.

*Call graph*: calls 1 internal fn (drain); 2 external calls (sleep, log).


##### `MidTurnReplyPoller.drain`  (lines 5581–5599)

```
async def drain(self) -> None
```

**Purpose**: Claims and delivers due mid-turn replies for all candidate workspaces in one pass.

**Data flow**: It gets workspace ids, enters each workspace scope, claims reply rows, starts renewal tasks, delivers rows in order, and cancels renewals afterward.

**Call relations**: run calls it repeatedly; it coordinates _claim, _renew_claim, and _deliver.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 1 (run); 4 external calls (create_task, gather, log, ws).


##### `MidTurnReplyPoller._claim`  (lines 5601–5642)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due mid-turn reply rows for this worker.

**Data flow**: It receives workspace id, selects due rows ordered by creation and span order, updates them to claimed with expiry, and returns sorted claimed rows.

**Call relations**: drain calls it before attempting to speak replies.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); called by 1 (drain); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `MidTurnReplyPoller._deliver`  (lines 5644–5669)

```
async def _deliver(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed mid-turn reply and records success or retry/failure.

**Data flow**: It receives workspace id, reply row, and renewal task, times the attempt, calls _deliver_with_lease, handles claim loss or other errors, updates retry state, and logs outcome.

**Call relations**: drain calls it for each claimed reply row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (drain); 2 external calls (now, log).


##### `MidTurnReplyPoller._deliver_with_lease`  (lines 5671–5692)

```
async def _deliver_with_lease(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs a mid-turn speak operation while keeping the claim alive, then marks the row delivered.

**Data flow**: It starts _speak and waits for either speaking or renewal failure, cancels remaining tasks, then calls _mark_delivered with the returned reply reference.

**Call relations**: _deliver calls it; _speak handles the surface-specific send.

*Call graph*: calls 2 internal fn (_mark_delivered, _speak); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `MidTurnReplyPoller._speak`  (lines 5694–5736)

```
async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None
```

**Purpose**: Sends one mid-turn reply through the conversation’s durable surface, or skips if no speak handler exists.

**Data flow**: It receives workspace id and reply row, returns existing reply_ref if present, reads turn and conversation surface data, finds the surface spec, builds MidTurnReply, and calls spec.speak when available.

**Call relations**: _deliver_with_lease calls it; durable surfaces implement speak to post progress or comment notices.

*Call graph*: called by 1 (_deliver_with_lease); 4 external calls (__init__, select, workspace_tx, log).


##### `MidTurnReplyPoller._renew_claim`  (lines 5738–5741)

```
async def _renew_claim(self, reply_id: UUID) -> None
```

**Purpose**: Keeps a mid-turn reply claim leased while delivery waits or runs.

**Data flow**: It receives reply id, sleeps for the refresh interval in a loop, and calls _refresh_claim repeatedly.

**Call relations**: drain starts one renewal task per claimed mid-turn reply.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (drain); 1 external calls (sleep).


##### `MidTurnReplyPoller._refresh_claim`  (lines 5743–5759)

```
async def _refresh_claim(self, reply_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim expiry on a mid-turn reply row.

**Data flow**: It receives reply id, updates the row if still claimed by this worker, and raises claim-lost if no row was updated.

**Call relations**: _renew_claim calls it; _deliver_with_lease treats renewal failure as loss of ownership.

*Call graph*: called by 1 (_renew_claim); 4 external calls (now, timedelta, update, workspace_tx).


##### `MidTurnReplyPoller._mark_delivered`  (lines 5761–5779)

```
async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Marks a mid-turn reply as delivered and stores its provider reply reference when one exists.

**Data flow**: It receives reply id and optional reply ref, updates the claimed row to delivered, clears claim fields, and raises claim-lost if ownership changed.

**Call relations**: _deliver_with_lease calls it after _speak finishes.

*Call graph*: called by 1 (_deliver_with_lease); 2 external calls (update, workspace_tx).


##### `MidTurnReplyPoller._fail_or_retry`  (lines 5781–5828)

```
async def _fail_or_retry(self, reply_id: UUID, error: Exception) -> tuple[str, str, datetime | None]
```

**Purpose**: Updates a failed mid-turn reply for retry or final failure. Failed aged-out replies stop blocking the terminal writeback.

**Data flow**: It receives reply id and error, computes retry delay using provider retry-after when present, truncates error text, updates row status and claim expiry, and returns outcome details.

**Call relations**: _deliver calls it when speaking a mid-turn reply fails.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### iMessage Integration
Packages the iMessage extension, connects to Spectrum Cloud, and translates iMessage traffic into UFO conversations and replies.

### `extensions/imessage/ufo_ext_imessage/__init__.py`

`other` · `import time`

This is an empty Python package marker. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to this folder as `ufo_ext_imessage` and load modules from inside it. Think of it like a label on a drawer: the drawer may contain useful tools, but this label mainly tells Python, “this drawer is part of the system and can be opened by name.” Without this file, depending on the Python version and packaging setup, imports for the iMessage extension might fail or behave inconsistently. Because the file is empty, it does not run setup code, expose shortcuts, or change any data when imported.


### `extensions/imessage/ufo_ext_imessage/cloud.py`

`io_transport` · `cross-cutting: used during setup, message sending, event streaming, catch-up, and attachment transfer`

This file lets the rest of the iMessage extension talk to Spectrum without needing to know Spectrum’s HTTP and gRPC details. HTTP is used here for project-level tasks like getting a shared line token or registering a phone number. gRPC, a fast remote-call protocol, is used for live messaging tasks like subscribing to new messages, catching up on missed events, sending texts, uploading attachments, and downloading attachment data.

The central object is SpectrumProject. Think of it like a staffed front desk for one Spectrum project: it knows the project ID and secret, asks Spectrum for a temporary pass when needed, reuses that pass until it is close to expiring, and adds the pass to each remote request. It also keeps separate connection state for different asyncio event loops, because an asyncio event loop is the scheduler that runs async work, and sharing certain async objects across loops can break.

The file also validates Spectrum’s JSON replies with Pydantic models, so bad or unexpected cloud responses become clear SpectrumCloudError failures. Incoming raw Spectrum message-change events are filtered carefully: outgoing messages, spam, system messages, corrupt messages, and empty messages are ignored. Only real incoming user messages are converted into InboundMessage objects for the rest of the provider.

#### Function details

##### `SpectrumProject.installation_id`  (lines 91–92)

```
def installation_id(self) -> str
```

**Purpose**: Returns a stable name for this Spectrum project as an installation identifier. Other parts of the provider can use it to refer to this configured project without exposing the secret.

**Data flow**: It reads the project_id stored on the SpectrumProject and prefixes it with "project:". The result is a plain string; nothing else is changed.

**Call relations**: This is a small identity helper on SpectrumProject. It does not call out to the network or hand work to other helpers; it simply gives callers a consistent label for the current project.


##### `SpectrumProject.line`  (lines 94–115)

```
async def line(self) -> SpectrumLine
```

**Purpose**: Gets the shared Spectrum iMessage line that this project should use, including the temporary token needed to call Spectrum’s messaging service. It reuses a cached token when it is still safely valid, so the code does not ask Spectrum for a new pass on every message.

**Data flow**: It starts with the project’s loop-local token cache. If the cache contains a SpectrumLine whose expiry time is more than a minute away, it returns that. Otherwise it makes an authenticated HTTP request to Spectrum, validates the response, stores the new line and expiry time, and returns the fresh SpectrumLine.

**Call relations**: Message actions such as catch_up, subscribe, send_text, send_attachment, and download_attachment call this first because they all need a valid bearer token. To do its work, it asks _loop for safe per-event-loop state and _request for the cloud HTTP call.

*Call graph*: calls 2 internal fn (_loop, _request); called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 4 external calls (__init__, __init__, TypeAdapter, monotonic).


##### `SpectrumProject._loop`  (lines 117–133)

```
def _loop(self) -> SpectrumLoop
```

**Purpose**: Returns the Spectrum connection state that is safe to use from the currently running asyncio event loop. This avoids reusing async locks and HTTP clients in a loop where they do not belong.

**Data flow**: It reads the current running event loop and checks the project’s loop-to-state map. If state already exists for that loop, it returns it. If not, it creates a SpectrumLoop with an HTTP client, lock, and token cache, stores it in the map, and returns it.

**Call relations**: The token code in line, the HTTP helper _request, and invalidate all call this before touching loop-sensitive objects. It is the local traffic controller that makes sure async state is used in the right lane.

*Call graph*: called by 3 (_request, invalidate, line); 4 external calls (__init__, Lock, get_running_loop, AsyncClient).


##### `SpectrumProject.assign_line`  (lines 135–165)

```
async def assign_line(self, phone_number: str, idempotency_key: str) -> str
```

**Purpose**: Finds or creates the Spectrum user record for a phone number and returns the shared phone number assigned to that user. This matters because the shared iMessage line can only continue conversations that have been opened correctly.

**Data flow**: It takes a phone number and an idempotency key, which is a repeat-safe request ID. It first asks Spectrum for existing users and searches for the phone number. If found, it returns that user’s assigned phone number. If not found, it sends a create-user request, validates Spectrum’s reply, and returns the newly assigned phone number.

**Call relations**: This function uses _request for both the user-list lookup and the possible user-registration call. It stands apart from the live gRPC messaging path, but prepares the cloud-side user setup that messaging depends on.

*Call graph*: calls 1 internal fn (_request); 2 external calls (__init__, TypeAdapter).


##### `SpectrumProject._request`  (lines 167–190)

```
async def _request(self, method: str, path: str, *, json: dict[str, str] | None=None, idempotency_key: str | None=None) -> object
```

**Purpose**: Sends one authenticated HTTP request to Spectrum Cloud and returns the decoded JSON body. It turns HTTP failures into a project-specific SpectrumCloudError so callers can treat cloud failures consistently.

**Data flow**: It receives an HTTP method, a URL path, optional JSON body, and optional idempotency key. It builds headers, adds Basic Auth using the project ID and secret, sends the request with a timeout, checks that the status is successful, and returns response.json(). If the status is an error, it raises SpectrumCloudError.

**Call relations**: line uses this to fetch shared-line tokens, and assign_line uses it to list or create users. It relies on _loop to get the HTTP client for the active event loop.

*Call graph*: calls 1 internal fn (_loop); called by 2 (assign_line, line); 2 external calls (__init__, BasicAuth).


##### `SpectrumProject.channel`  (lines 192–193)

```
def channel(self) -> grpc.aio.Channel
```

**Purpose**: Opens a secure gRPC channel to Spectrum’s iMessage service. A gRPC channel is like a protected phone line used for structured remote service calls.

**Data flow**: It uses Spectrum’s fixed iMessage service address and SSL credentials, which provide encrypted transport. It returns a gRPC async channel; it does not send a request by itself.

**Call relations**: The streaming and sending methods call this when they need to speak to Spectrum’s gRPC services. Those methods then build service stubs on top of the channel to catch up, subscribe, send messages, or transfer attachments.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe); 1 external calls (ssl_channel_credentials).


##### `SpectrumProject.invalidate`  (lines 195–198)

```
async def invalidate(self) -> None
```

**Purpose**: Clears the cached Spectrum line token. Someone would use this when the current token should no longer be trusted or should be refreshed on the next operation.

**Data flow**: It gets the current loop’s token state, takes the async lock so no other task edits it at the same time, and empties the cache. It returns nothing; its effect is that the next call to line will fetch a new token.

**Call relations**: This uses _loop for safe loop-local state. It supports the same token lifecycle that line controls, but in the opposite direction: instead of filling the cache, it clears it.

*Call graph*: calls 1 internal fn (_loop).


##### `SpectrumProject.invalid_cursor`  (lines 200–204)

```
def invalid_cursor(self, error: Exception) -> bool
```

**Purpose**: Checks whether an error means the stored event cursor is invalid. A cursor is a saved position in the event stream, like a bookmark saying where catch-up should resume.

**Data flow**: It receives an exception. If the exception is a gRPC async RPC error and its status code is INVALID_ARGUMENT, it returns true; otherwise it returns false. It does not change anything.

**Call relations**: This is an error-classification helper for higher-level retry or recovery code. It interprets one specific gRPC failure as meaning the caller’s saved stream position cannot be used.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.external_error`  (lines 206–207)

```
def external_error(self, error: Exception) -> bool
```

**Purpose**: Checks whether an exception came from outside this program, such as Spectrum’s gRPC service, Spectrum Cloud HTTP, or the HTTP client itself. This helps callers distinguish outside-service trouble from local programming mistakes.

**Data flow**: It receives an exception and tests its type. It returns true for gRPC RPC errors, SpectrumCloudError, and httpx HTTP errors; otherwise it returns false. It does not mutate state.

**Call relations**: This helper fits into broader error handling around SpectrumProject operations. It gives callers a simple yes-or-no answer about whether a failure came from the external service path.


##### `SpectrumProject.error_code`  (lines 209–212)

```
def error_code(self, error: Exception) -> str
```

**Purpose**: Turns an exception into a short readable code for logging, metrics, or retry decisions. For gRPC errors it preserves the official gRPC status name; for other errors it uses the Python exception class name.

**Data flow**: It receives an exception. If it is a gRPC async RPC error, it reads error.code().name and returns that. Otherwise it returns the exception type’s name as a string.

**Call relations**: This is another error-reporting helper around cloud and gRPC operations. It complements invalid_cursor and external_error by producing a compact label after an operation fails.

*Call graph*: 1 external calls (code).


##### `SpectrumProject.catch_up`  (lines 214–235)

```
async def catch_up(self, after_sequence: int | None) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Reads missed Spectrum events after an optional saved sequence number and yields provider-friendly events. This lets the extension recover messages that arrived while it was offline or disconnected.

**Data flow**: It takes an optional after_sequence bookmark. It gets a valid line token, builds a catch-up request, sets the bookmark if one was provided, opens a secure gRPC channel, and reads streamed frames from Spectrum. Completion frames become ProviderEvent objects with a head sequence; message-change frames are filtered through _inbound_message and yielded with their sequence.

**Call relations**: This is one of the main consumers of line, channel, rpc_metadata, and _inbound_message. It hands the rest of the provider a clean async stream of ProviderEvent objects instead of raw Spectrum protocol frames.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 3 external calls (__init__, CatchUpEventsRequest, EventServiceStub).


##### `SpectrumProject.subscribe`  (lines 237–253)

```
async def subscribe(self, ready: asyncio.Event) -> AsyncIterator[ProviderEvent]
```

**Purpose**: Subscribes to live message events from Spectrum and yields them as provider events. It is the always-on listener for new incoming iMessages.

**Data flow**: It receives an asyncio.Event named ready, which is a signal object. It gets a valid line token, opens a secure gRPC channel, starts a message-event subscription, marks ready as soon as the stream is established, then converts each incoming frame into a ProviderEvent, using _inbound_message when the frame contains a message change.

**Call relations**: This is the live counterpart to catch_up. It uses the same shared helpers—line for authentication, channel for transport, rpc_metadata for headers, and _inbound_message for filtering—but it follows the ongoing subscription stream instead of a catch-up stream.

*Call graph*: calls 4 internal fn (channel, line, _inbound_message, rpc_metadata); 4 external calls (__init__, set, SubscribeMessageEventsRequest, MessageServiceStub).


##### `SpectrumProject.send_text`  (lines 255–268)

```
async def send_text(self, conversation_id: str, text: str, idempotency_key: str) -> str
```

**Purpose**: Sends one text message into an existing iMessage conversation through Spectrum. It returns Spectrum’s message identifier so the caller can track what was sent.

**Data flow**: It takes a conversation ID, text, and idempotency key. It gets a valid line token, builds a send-text gRPC request with the conversation, message text, and repeat-safe client message ID, sends it over a secure channel with authentication metadata, and returns the created message’s GUID.

**Call relations**: Before sending, it relies on line for a token and channel for the gRPC connection. It uses rpc_metadata to attach the bearer token and idempotency key, then hands the request to Spectrum’s MessageService.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (SendTextMessageRequest, MessageServiceStub).


##### `SpectrumProject.send_attachment`  (lines 270–297)

```
async def send_attachment(self, conversation_id: str, filename: str, data: bytes, idempotency_key: str) -> str
```

**Purpose**: Uploads a file to Spectrum and then sends it as an iMessage attachment in a conversation. It returns the resulting message identifier.

**Data flow**: It takes a conversation ID, filename, raw file bytes, and idempotency key. It gets a valid line token and opens one secure gRPC channel. First it uploads the attachment data and receives an attachment GUID. Then it sends an attachment message that points at that uploaded GUID. Finally it returns the sent message’s GUID.

**Call relations**: This combines two Spectrum services in order: AttachmentService for upload, then MessageService for sending. It uses line, channel, and rpc_metadata for the same authentication and repeat-safety pattern used by send_text.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 4 external calls (UploadAttachmentRequest, AttachmentServiceStub, SendAttachmentMessageRequest, MessageServiceStub).


##### `SpectrumProject.download_attachment`  (lines 299–309)

```
async def download_attachment(self, attachment_id: str) -> AsyncGenerator[bytes, None]
```

**Purpose**: Downloads an attachment from Spectrum as a stream of byte chunks. Streaming is useful because attachments may be large and should not have to be loaded all at once.

**Data flow**: It receives an attachment ID, gets a valid line token, opens a secure gRPC channel, and starts a download request. As Spectrum sends frames, it yields only the primary file chunks as bytes. It does not assemble the full file itself.

**Call relations**: This method uses line for authentication, channel for the gRPC connection, and rpc_metadata for request headers. Callers can consume the yielded chunks and decide whether to save, forward, or process the attachment data.

*Call graph*: calls 3 internal fn (channel, line, rpc_metadata); 2 external calls (DownloadAttachmentRequest, AttachmentServiceStub).


##### `spectrum_project`  (lines 313–327)

```
def spectrum_project() -> SpectrumProject
```

**Purpose**: Builds the configured SpectrumProject singleton for the extension. Singleton here means the same cached project object is reused instead of being rebuilt each time.

**Data flow**: It reads the project ID and project secret from deployment environment variables. If either is missing, it raises ProviderNotConfigured with setup instructions. If both exist, it creates an HTTP client, async lock, empty token cache, and SpectrumProject, then returns it; future calls return the cached object.

**Call relations**: This is the entry point other provider code uses when it needs access to Spectrum. It supplies the SpectrumProject instance whose methods perform token fetching, message sending, event listening, and attachment transfer.

*Call graph*: 5 external calls (__init__, __init__, Lock, AsyncClient, deploy_env).


##### `rpc_metadata`  (lines 330–334)

```
def rpc_metadata(token: str, idempotency_key: str | None=None) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the small set of gRPC metadata headers Spectrum expects, including the bearer token and sometimes an idempotency key. Metadata is extra request information sent alongside a gRPC call, similar to HTTP headers.

**Data flow**: It takes a token and optional idempotency key. It always creates an authorization entry in the form "Bearer <token>". If an idempotency key is provided, it adds that too. It returns the entries as an immutable tuple.

**Call relations**: The gRPC methods catch_up, subscribe, send_text, send_attachment, and download_attachment call this before contacting Spectrum. It keeps authentication header formatting in one place.

*Call graph*: called by 5 (catch_up, download_attachment, send_attachment, send_text, subscribe).


##### `_inbound_message`  (lines 337–375)

```
def _inbound_message(event: object) -> InboundMessage | None
```

**Purpose**: Converts a raw Spectrum message-change event into the extension’s simpler InboundMessage object, but only if it is a real incoming user message worth processing. It filters out messages that should not trigger provider behavior.

**Data flow**: It receives an event object and first checks that it is the expected Spectrum message-change type. It rejects anything that is not a received message, is from the current user, is a system/service/spam/corrupt message, has no sender, or has neither text nor visible non-sticker attachments. For accepted events, it gathers sender, text, conversation ID, visible attachments, and whether the chat looks direct, then returns an InboundMessage.

**Call relations**: catch_up and subscribe call this while translating Spectrum stream frames into ProviderEvent objects. It is the filter at the door: raw cloud events come in, and only clean inbound messages are passed onward.

*Call graph*: called by 2 (catch_up, subscribe); 2 external calls (__init__, __init__).


### `extensions/imessage/ufo_ext_imessage/surface.py`

`io_transport` · `cross-cutting: active while listening for iMessage events and while sending replies`

This file is the adapter between Apple iMessage, through a provider object, and UFO’s internal conversation system. Without it, phone messages would not become UFO turns, UFO replies would not reach iMessage, and users could not prove that a phone number belongs to them.

The main class, ImessageSurface, works like a bilingual receptionist. On one side it talks to a MessageProvider, which supplies iMessage events and can send texts or attachments. On the other side it talks to UFO’s surface context, which knows about members, conversations, files, and address claims.

Incoming messages are read from a durable stream with a cursor, which is a saved bookmark saying “we have processed up to here.” If the stream drops, the listener reconnects and catches up so messages are not silently skipped. Each message is checked against a claimed phone number. If the number is still being verified, the file checks the opt-in code and confirms or releases the claim. If the number is already confirmed, the message is admitted into the right UFO conversation.

The file also saves inbound attachments into the workspace when they are small enough, sends outbound replies, uploads outbound artifacts, and creates fallback links for files too large for iMessage. It also sends a small contact card so users can save the UFO line as a contact.

#### Function details

##### `read_claim`  (lines 63–73)

```
def read_claim(stored: object) -> PendingClaim | None
```

**Purpose**: Reads a stored phone-number verification claim and turns it into a trusted PendingClaim object. If the stored data is missing or malformed, it quietly rejects it so one bad saved row does not stop all incoming messages.

**Data flow**: It receives an unknown stored value. If the value is empty, it returns nothing. Otherwise it tries to validate the value as a pending iMessage claim; valid data comes out as a PendingClaim, while invalid data is logged and becomes None.

**Call relations**: ImessageSurface._prove calls this while checking whether an incoming text contains the expected opt-in code. If read_claim cannot make sense of the saved claim, _prove stops the verification path instead of admitting the message.

*Call graph*: called by 1 (_prove); 1 external calls (log).


##### `MessageStreamDisconnected.__init__`  (lines 87–90)

```
def __init__(self, cursor: int | None, error: Exception) -> None
```

**Purpose**: Creates an error object that says the live iMessage stream disconnected, while remembering where processing had reached. This lets the listener reconnect without losing its place.

**Data flow**: It receives the last known cursor and the original error. It stores both on the exception and sets the visible error message to the original error’s text.

**Call relations**: ImessageSurface._consume_connected raises this when the provider stream ends or an outside provider error interrupts catch-up or live processing. ImessageSurface.listen catches it, decides where to restart, and reconnects.

*Call graph*: called by 1 (_consume_connected).


##### `contact_card`  (lines 103–115)

```
def contact_card(assigned_phone_number: str) -> bytes
```

**Purpose**: Builds a small digital contact card for the assigned iMessage phone line. Users can save it so the chat looks like it comes from a known contact.

**Data flow**: It receives the assigned phone number. It writes that number into a vCard, which is a standard contact-card text format, and returns the card as bytes ready to send as an attachment.

**Call relations**: ImessageSurface._send_contact_card calls this after a user successfully proves their phone number. The resulting bytes are handed to the provider for sending.

*Call graph*: called by 1 (_send_contact_card).


##### `claim_key`  (lines 118–119)

```
def claim_key(member_id: UUID, phone_number: str) -> str
```

**Purpose**: Creates a private storage key for one member’s claim on one phone number. The key is hashed so the raw member-and-phone pair is not used directly as the storage name.

**Data flow**: It receives a member ID and a phone number. It combines them, hashes the combined text with SHA-256, and prefixes the result with the iMessage claim prefix.

**Call relations**: ImessageSurface._prove uses this to look up and delete the saved pending claim while a user is proving ownership of a phone number.

*Call graph*: called by 1 (_prove); 1 external calls (sha256).


##### `queue_key`  (lines 122–123)

```
def queue_key(conversation_id: str, *, direct: bool) -> str
```

**Purpose**: Creates the stable internal queue name for an iMessage conversation. It records both the conversation ID and whether the chat is direct or a group chat.

**Data flow**: It receives an iMessage conversation ID and a direct/group flag. It turns them into a compact JSON string such as a small labeled address that UFO can store and later decode.

**Call relations**: ImessageSurface._admit_message uses this when asking UFO to find or create the matching conversation for an incoming iMessage.

*Call graph*: called by 1 (_admit_message); 1 external calls (dumps).


##### `conversation_from_queue`  (lines 126–134)

```
def conversation_from_queue(queue: str) -> ConversationAddress
```

**Purpose**: Reads an internal queue key back into a usable iMessage conversation address. This is needed when UFO has a saved queue key and now needs to send something back to iMessage.

**Data flow**: It receives a JSON queue string. It parses the string, checks that it has the expected direct-or-group shape, and returns a ConversationAddress with the conversation ID and direct/group flag. Bad shapes raise an error.

**Call relations**: ImessageSurface.post, ImessageSurface.speak, and ImessageSurface.attach call this before sending text or files, so they know which iMessage chat to target.

*Call graph*: called by 3 (attach, post, speak); 2 external calls (__init__, loads).


##### `_attachment_content`  (lines 137–138)

```
async def _attachment_content(data: bytes) -> AsyncIterator[bytes]
```

**Purpose**: Wraps already-downloaded attachment bytes as a tiny asynchronous stream. UFO’s file-writing interface expects chunks over time, even when this file already has all the data.

**Data flow**: It receives bytes. It yields those same bytes once, so the caller can pass them to an async file writer.

**Call relations**: ImessageSurface._downloaded_files uses this after downloading an inbound iMessage attachment, when saving that attachment into the workspace.

*Call graph*: called by 1 (_downloaded_files).


##### `ImessageSurface.listen`  (lines 145–169)

```
async def listen(self, context: SurfaceListenerContext) -> None
```

**Purpose**: Runs the long-lived iMessage listener. It keeps reading provider events, remembers progress, and reconnects after provider-side stream failures.

**Data flow**: It receives a listener context from UFO. It creates or obtains the message provider, reads the saved cursor bookmark, enters the connected-consumption loop, and updates its restart cursor whenever the stream disconnects. If the provider is not configured, it logs that iMessage is inactive and waits forever.

**Call relations**: This is the top-level listening method for the surface. It calls ImessageSurface._consume_connected for each connected session, and when that session reports a disconnection it clears or reloads the cursor as needed before trying again.

*Call graph*: calls 3 internal fn (clear_cursor, cursor, _consume_connected); 3 external calls (Event, sleep, log).


##### `ImessageSurface._consume_connected`  (lines 171–216)

```
async def _consume_connected(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> None
```

**Purpose**: Processes one connected stretch of the iMessage event stream. It starts live listening first, catches up from the saved cursor, then admits new live events in order.

**Data flow**: It receives the UFO listener context, provider, installation ID, and last cursor. It starts a background pump for live events, waits until the live stream is ready, replays missed events if needed, then reads queued live frames. Each usable event is sent to _process_event and the cursor is advanced. Provider-side failures are wrapped as MessageStreamDisconnected.

**Call relations**: ImessageSurface.listen calls this for each connection attempt. It coordinates ImessageSurface._pump_live, ImessageSurface._catch_up, and ImessageSurface._process_event, and reports disconnects back to listen so reconnect logic can run.

*Call graph*: calls 5 internal fn (external_error, _catch_up, _process_event, _pump_live, __init__); called by 1 (listen); 4 external calls (Event, Queue, create_task, gather).


##### `ImessageSurface._pump_live`  (lines 218–236)

```
async def _pump_live(self, provider: MessageProvider, ready: asyncio.Event, frames: asyncio.Queue[LiveFrame | LiveFailure]) -> None
```

**Purpose**: Copies live provider events into an internal queue. This separates the provider’s live stream from the main processing loop, like one person taking messages at the door while another files them.

**Data flow**: It receives the provider, a readiness signal, and a queue. It subscribes to live events, wraps each event as a LiveFrame, and puts it in the queue. If the stream errors or ends, it puts a LiveFailure into the queue. It always marks the stream as ready before exiting.

**Call relations**: ImessageSurface._consume_connected starts this as a background task. _consume_connected then reads the queued frames and decides whether to process them or reconnect.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (_consume_connected); 4 external calls (__init__, __init__, __init__, set).


##### `ImessageSurface._catch_up`  (lines 238–257)

```
async def _catch_up(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, cursor: int | None) -> int
```

**Purpose**: Replays older provider events that happened after the saved cursor but before live listening was ready. This helps avoid a gap where messages could otherwise be missed.

**Data flow**: It receives the current cursor. It asks the provider for catch-up frames, processes message frames through _process_event, tracks the newest sequence number seen, stores that final cursor, and returns it.

**Call relations**: ImessageSurface._consume_connected calls this after the live pump is ready and before it starts handling live frames. It hands each catch-up event to ImessageSurface._process_event.

*Call graph*: calls 3 internal fn (store_cursor, catch_up, _process_event); called by 1 (_consume_connected).


##### `ImessageSurface._process_event`  (lines 259–274)

```
async def _process_event(self, context: SurfaceListenerContext, provider: MessageProvider, installation_id: str, sequence: int, message: InboundMessage | None) -> None
```

**Purpose**: Turns one provider event into workspace activity if it belongs to a known address, then saves the stream position. Unknown senders still advance the cursor so the listener does not get stuck.

**Data flow**: It receives a sequence number and possibly an inbound message. If there is a message, it asks the listener context whether that sender maps to a workspace. If so, it passes the message to _admit_message. In all cases, it stores the sequence as processed.

**Call relations**: Both ImessageSurface._catch_up and ImessageSurface._consume_connected use this for individual events. It is the handoff point from provider-stream processing into message admission.

*Call graph*: calls 3 internal fn (addressed, store_cursor, _admit_message); called by 2 (_catch_up, _consume_connected).


##### `ImessageSurface._admit_message`  (lines 276–320)

```
async def _admit_message(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage) -> None
```

**Purpose**: Decides whether an incoming iMessage should become a UFO conversation turn. It also routes verification messages to the proof flow instead of treating them as normal chat.

**Data flow**: It receives a surface context, provider, and inbound message. It checks whether the sender has an address claim. If the claim is still pending, it calls _prove. If the sender is confirmed, it may ask whether a group-message reply is wanted, finds or creates the right UFO conversation, downloads allowed attachments, wraps the message text safely, and admits the turn.

**Call relations**: ImessageSurface._process_event calls this when a message comes from a sender tied to a workspace. It may call ImessageSurface._prove for opt-in verification, ImessageSurface._downloaded_files for attachments, and queue_key when creating the conversation mapping.

*Call graph*: calls 7 internal fn (address_claim, admit, ambient_reply_wanted, conversation_for, _downloaded_files, _prove, queue_key); called by 1 (_process_event); 7 external calls (__init__, __init__, sha256, conversation_audience, room_audience, fence_member_message, mint_marker).


##### `ImessageSurface._prove`  (lines 322–361)

```
async def _prove(self, ctx: SurfaceContext, provider: MessageProvider, message: InboundMessage, claim: AddressClaim) -> None
```

**Purpose**: Checks an incoming direct message against a pending phone-number verification code. A successful proof connects the phone number to the member; an expired or cancelled proof releases the claim.

**Data flow**: It receives the message and its address claim. It ignores non-direct or non-pending cases. For pending direct messages, it checks expiry, opt-out words, and the saved claim record. If the typed text contains the expected code, it sends a connected message, sends a contact card, confirms the address, and deletes the pending claim. If the code is wrong or expired, it replies or releases as appropriate.

**Call relations**: ImessageSurface._admit_message calls this whenever a sender’s claim still has an expiration time. It uses claim_key and read_claim to find the pending code, calls ImessageSurface._send_contact_card after success, and uses the provider and surface context to notify, confirm, release, or clean up.

*Call graph*: calls 6 internal fn (confirm_address, release_address, send_text, _send_contact_card, claim_key, read_claim); called by 1 (_admit_message); 2 external calls (__init__, now).


##### `ImessageSurface._send_contact_card`  (lines 363–384)

```
async def _send_contact_card(self, provider: MessageProvider, message: InboundMessage, assigned_phone_number: str) -> None
```

**Purpose**: Sends the UFO phone line as a contact-card attachment after a successful connection. Failure to send this card is logged but does not undo the phone connection.

**Data flow**: It receives the provider, the proving message, and the assigned phone number. It builds contact-card bytes with contact_card and asks the provider to send them to the conversation. If the provider refuses for an outside/provider reason, it logs the refusal and continues.

**Call relations**: ImessageSurface._prove calls this after the user enters the correct opt-in code. It deliberately treats contact-card delivery as helpful but not essential, so _prove can still confirm the address.

*Call graph*: calls 4 internal fn (error_code, external_error, send_attachment, contact_card); called by 1 (_prove); 1 external calls (log).


##### `ImessageSurface._downloaded_files`  (lines 386–435)

```
async def _downloaded_files(self, ctx: SurfaceContext, provider: MessageProvider, conversation_id: UUID, attachments: tuple[MessageAttachment, ...]) -> str
```

**Purpose**: Downloads inbound iMessage attachments that are small enough and saves them into the UFO workspace. It returns a plain note that can be included with the admitted message.

**Data flow**: It receives the surface context, provider, target conversation ID, and attachment list. For each attachment, it chooses a safe workspace filename, skips files over the size limit, streams download data from the provider, rejects files that grow too large, saves successful files under the iMessage inbox folder, and records which files were saved, too large, or unavailable. It returns those notes as text.

**Call relations**: ImessageSurface._admit_message calls this before admitting a message with attachments. It uses the provider for downloads, the surface context for writing workspace files, and _attachment_content to pass saved bytes to the file writer.

*Call graph*: calls 4 internal fn (write_workspace_file, download_attachment, external_error, _attachment_content); called by 1 (_admit_message); 1 external calls (inbox_name).


##### `ImessageSurface.post`  (lines 437–444)

```
async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Sends the final answer for a completed UFO turn back to the matching iMessage chat. This is the normal end-of-turn reply path.

**Data flow**: It receives the surface context and a writeback, which contains the saved queue key and final response details. It decodes the queue key, builds the outgoing text with _terminal_text, sends that text through the provider, and returns the provider’s message reference.

**Call relations**: The UFO runtime calls this when a turn has finished and needs a terminal reply. It relies on conversation_from_queue to find the iMessage chat and ImessageSurface._terminal_text to format the response.

*Call graph*: calls 2 internal fn (_terminal_text, conversation_from_queue).


##### `ImessageSurface.speak`  (lines 446–450)

```
async def speak(self, _ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Sends a mid-turn reply to iMessage before the whole UFO turn has finished. This allows progress updates or interim messages to appear in the chat.

**Data flow**: It receives a mid-turn reply containing a queue key, text, and reply ID. It decodes the queue key to find the iMessage conversation, sends the text through the provider, and returns the provider’s message reference.

**Call relations**: The UFO runtime uses this when it wants to speak during an active turn. It uses conversation_from_queue in the same way as final posting, but it sends the reply text directly.

*Call graph*: calls 1 internal fn (conversation_from_queue).


##### `ImessageSurface.attach`  (lines 452–464)

```
async def attach(self, ctx: SurfaceContext, writeback: Writeback, _reply_ref: str) -> None
```

**Purpose**: Uploads final-turn artifacts as iMessage attachments when they fit within iMessage’s size limit. Files that are too large are skipped here because the text reply can include links instead.

**Data flow**: It receives the surface context, writeback, and an unused reply reference. It decodes the destination conversation, loops over the artifacts, ignores anything larger than the maximum size, reads each allowed artifact’s bytes, and sends it through the provider with a stable idempotency key.

**Call relations**: The UFO runtime calls this after or alongside a writeback when artifacts should be shared. It uses conversation_from_queue to find the chat and ImessageSurface._artifact_bytes to safely load each file before upload.

*Call graph*: calls 2 internal fn (_artifact_bytes, conversation_from_queue).


##### `ImessageSurface._terminal_text`  (lines 466–488)

```
async def _terminal_text(self, ctx: SurfaceContext, writeback: Writeback, *, direct: bool) -> str
```

**Purpose**: Builds the final text that should be sent to iMessage at the end of a turn. It combines the answer, any question prompt, connection instructions, credential instructions, and links for oversized files.

**Data flow**: It receives the surface context, writeback, and whether the target chat is direct. It starts with the terminal response text and question text. It adds a direct connect URL when appropriate, or tells group chats to continue in direct message. It adds a member-portal hint for credential requests and links or names for artifacts too large to attach. It returns the joined text, or a status fallback if there is no text.

**Call relations**: ImessageSurface.post calls this just before sending the final iMessage text. It calls ImessageSurface._question_text for question formatting and uses the surface context to create URLs or artifact links.

*Call graph*: calls 4 internal fn (artifact_link, connect_url, home_url, _question_text); called by 1 (post).


##### `ImessageSurface._question_text`  (lines 490–501)

```
def _question_text(self, writeback: Writeback) -> str
```

**Purpose**: Formats a UFO question into readable iMessage text. It includes the question title, each question line, options, and any current answer.

**Data flow**: It receives a writeback. If there is no question, it returns an empty string. Otherwise it walks through the question items and builds a multi-line prompt with option labels and chosen answers where present.

**Call relations**: ImessageSurface._terminal_text calls this while composing the final outgoing text. Its output becomes one section of the message sent by ImessageSurface.post.

*Call graph*: called by 1 (_terminal_text).


##### `ImessageSurface._artifact_bytes`  (lines 503–513)

```
async def _artifact_bytes(self, ctx: SurfaceContext, blob_key: str, size_bytes: int) -> bytes
```

**Purpose**: Reads an artifact from UFO’s blob storage into memory so it can be uploaded to iMessage. It protects the provider from files that are too large or whose size changed unexpectedly.

**Data flow**: It receives the surface context, blob key, and expected size. It rejects the file immediately if the expected size is over the iMessage limit. Otherwise it streams chunks from blob storage into memory, raising an error if the data grows past the limit or if the final byte count does not match the expected size. On success, it returns the file bytes.

**Call relations**: ImessageSurface.attach calls this for each outbound artifact that is small enough to send directly. The returned bytes are then handed to the provider as an iMessage attachment.

*Call graph*: called by 1 (attach).


### Slack Integration
Packages the Slack extension, handles Slack mention formatting, and bridges Slack requests, messages, files, installs, and interactions into UFO.

### `extensions/slack/ufo_ext_slack/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the drawer may contain useful tools, but the label is what lets the rest of the system find it by name. Here, the drawer is the Slack extension package, `ufo_ext_slack`. Because the file is empty, it does not run setup code, expose shortcuts, or change how the Slack extension works. Its value is structural: without it, some Python versions or tooling might not recognize this directory as a package, and imports that refer to `ufo_ext_slack` could fail or behave inconsistently.


### `extensions/slack/ufo_ext_slack/mentions.py`

`domain_logic` · `message ingest and outgoing reply preparation`

Slack does not send messages exactly as people see them. A person mention may arrive as something like `<@U123>`, a channel as `<#C456|general>`, and a link as `<https://example.com|docs>`. This file is the translator between Slack’s wire format and the words humans and the model should read.

On the way in, `render_markup` rewrites known Slack entities into readable forms such as `@Alex` or `#general`. If it cannot safely resolve an id to a name, it leaves the original Slack text alone rather than guessing. Links are shown with both label and URL when that is useful, because the label is what a person saw and the URL is what the agent can open.

On the way out, `mention_markup` does a narrower job. If the agent writes `@Alex`, this file can turn that back into `<@U123>` so Slack sends a notification. It only does this for names the caller explicitly provides, avoids ambiguous names, skips code blocks and URLs, and limits how many mentions can be converted. This prevents an innocent summary of a busy thread from accidentally paging many people.

A key safety detail is that Slack’s escaped characters, like `&lt;`, are not automatically unescaped during markup rendering. Unescaping is separate because another person’s quoted text should not be allowed to break surrounding message structure.

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

This file is the bridge between Slack and ufo’s core conversation system. Without it, Slack could not safely send messages into ufo, ufo could not know which Slack thread a conversation belongs to, and answers, progress updates, forms, and shared files would not appear back in Slack.

It does several jobs. First, it proves inbound requests are really from Slack using Slack’s signing secret, then finds the correct ufo workspace. It understands Slack events, direct messages, channel mentions, threaded replies, attached files, and Slack’s URL verification handshake. It decides when the agent was actually addressed, and for unmentioned thread chatter it asks the core “ambient reply” decision before starting a new turn.

Second, it records enough Slack thread information so later code, even code running after a restart, knows where to post status messages, interim progress, final replies, and file uploads. Think of this as leaving a forwarding address for every conversation.

Third, it renders Slack-specific output: Block Kit forms for questions, connect buttons, footers, safe message chunks, mention resolution, ephemeral private messages, and external file uploads. It also protects against duplicate delivery by checkpointing what has already been posted.

Finally, it supports installation through either OAuth or a bring-your-own Slack app, storing the bot token and identity Slack proves for the workspace.

#### Function details

##### `_env_signing_secret`  (lines 231–235)

```
def _env_signing_secret() -> str | None
```

**Purpose**: Reads the deploy-wide Slack signing secret from environment variables. This is the fallback secret used when a workspace has not stored its own Slack signing secret.

**Data flow**: It reads the process environment → looks for the Slack signing secret name → returns the secret text, or nothing if it is unset.

**Call relations**: Workspace-specific secret lookup helpers call this when their own credential lookup does not find a secret.

*Call graph*: called by 2 (_auth_signing_secret, _ctx_signing_secret).


##### `_ctx_signing_secret`  (lines 238–245)

```
async def _ctx_signing_secret(ctx: SurfaceContext) -> str | None
```

**Purpose**: Finds the signing secret to use for a request after a workspace has already been selected. This lets one workspace use its own Slack app while another uses the shared deploy app.

**Data flow**: It receives a surface context → tries to read the workspace credential slot → falls back to the environment secret → returns the secret or nothing.

**Call relations**: The event and interactivity routes call this before verifying Slack signatures.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 2 (ingest, interactive).


##### `_auth_signing_secret`  (lines 248–256)

```
async def _auth_signing_secret(auth: SurfaceAuth, workspace_id: UUID) -> str | None
```

**Purpose**: Finds the signing secret during the early phase before the request is fully bound to a workspace. It is used to decide whether an incoming Slack request is trustworthy enough to route.

**Data flow**: It receives shared surface authentication and a workspace id → tries that workspace’s credential slot → falls back to the environment secret → returns the secret, or nothing for unknown/unconfigured workspaces.

**Call relations**: Workspace resolution calls this after extracting a Slack team id and before accepting the request.

*Call graph*: calls 2 internal fn (credential, _env_signing_secret); called by 1 (resolve_workspace).


##### `slack_client_id`  (lines 259–263)

```
def slack_client_id() -> str
```

**Purpose**: Returns the Slack OAuth client id for the deploy’s Slack app. It fails loudly if the app is not configured, because OAuth cannot start without it.

**Data flow**: It reads the client id environment variable → returns it if present → raises an error if missing.

**Call relations**: The OAuth token exchange uses this when presenting the app identity to Slack.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_client_secret`  (lines 266–270)

```
def slack_client_secret() -> str
```

**Purpose**: Returns the Slack OAuth client secret for the deploy’s Slack app. This secret proves to Slack that the callback is from the real app owner.

**Data flow**: It reads the client secret environment variable → returns it if present → raises an error if missing.

**Call relations**: The OAuth exchange calls this together with the client id when trading a code for a bot token.

*Call graph*: called by 1 (slack_oauth_exchange).


##### `slack_oauth_redirect_uri`  (lines 273–275)

```
def slack_oauth_redirect_uri(public_base_url: str) -> str
```

**Purpose**: Builds the callback URL Slack should redirect to after a user approves installation. This keeps the redirect address consistent between the install link and token exchange.

**Data flow**: It receives the public base URL → trims any trailing slash → appends the Slack surface OAuth path → returns the full URL.

**Call relations**: The OAuth callback uses this to make sure the exchange uses the same redirect Slack originally saw.

*Call graph*: called by 1 (oauth_callback).


##### `slack_authorize_url`  (lines 278–290)

```
def slack_authorize_url(client_id: str, redirect_uri: str, state: str) -> str
```

**Purpose**: Builds the “Add to Slack” URL an owner clicks to install the app. It includes the requested Slack permissions and a sealed state value tying the install back to the right workspace.

**Data flow**: It receives the client id, redirect URL, and state token → URL-encodes them with the required bot scopes → returns a Slack authorization link.

**Call relations**: Install setup code can use this helper to start the OAuth flow; the callback later checks the same state.

*Call graph*: 1 external calls (urlencode).


##### `SlackIdentityError.__init__`  (lines 294–296)

```
def __init__(self, error: str)
```

**Purpose**: Creates an error that carries Slack identity or install failure text. It keeps the Slack error message easy to log or show in a controlled way.

**Data flow**: It receives an error string → stores it on the exception → initializes the runtime error with the same text.

**Call relations**: Identity proving and OAuth exchange raise this when Slack rejects a token or returns malformed identity data.

*Call graph*: called by 2 (_prove, slack_oauth_exchange).


##### `bot_token_fingerprint`  (lines 313–314)

```
def bot_token_fingerprint(bot_token: str) -> str
```

**Purpose**: Creates a one-way fingerprint of a Slack bot token. The system can tell whether stored identity data belongs to the current token without storing the token in the identity record.

**Data flow**: It receives a bot token → hashes it with SHA-256 → returns the hexadecimal fingerprint.

**Call relations**: Identity reads, OAuth callback, and manifest-token proving all use this to bind identity metadata to a particular token.

*Call graph*: called by 3 (_prove, oauth_callback, read_identity); 1 external calls (sha256).


##### `read_identity`  (lines 317–329)

```
async def read_identity(blob: BlobStore, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the stored Slack team and bot-user identity, but only if it matches the current bot token. This prevents old identity data from being used after a reinstall or token rotation.

**Data flow**: It receives a blob store and bot token → checks for the identity blob → parses it → compares the stored token fingerprint to the current token → returns the identity or nothing.

**Call relations**: Inbound handling, self-user resolution, and manifest identity proving all depend on this before trusting Slack team or bot ids.

*Call graph*: calls 3 internal fn (exists, get, bot_token_fingerprint); called by 3 (resolve, _identity, resolve_self_user_id).


##### `resolve_self_user_id`  (lines 332–338)

```
async def resolve_self_user_id(ctx: SurfaceIdentityContext) -> str | None
```

**Purpose**: Returns the Slack bot user id for this workspace if Slack is installed. Other parts of the extension can use this to know which Slack user represents the app itself.

**Data flow**: It receives an identity context → reads the bot token credential → reads the matching identity blob → returns the bot user id or nothing.

**Call relations**: This is a small identity lookup exposed through the surface identity path, built on the same stored identity used by request handling.

*Call graph*: calls 1 internal fn (read_identity); 1 external calls (credential).


##### `_bot_token`  (lines 341–348)

```
async def _bot_token(ctx: SurfaceContext) -> str | None
```

**Purpose**: Fetches the workspace’s Slack bot token, if one is stored. A missing token means ufo may receive Slack events but cannot answer them.

**Data flow**: It receives a surface context → reads the bot-token credential slot → returns the token or nothing if the slot is unset.

**Call relations**: The event and interactivity routes call this before any Slack API call that requires the bot.

*Call graph*: calls 1 internal fn (credential); called by 2 (ingest, interactive).


##### `_identity`  (lines 351–355)

```
async def _identity(ctx: SurfaceContext, bot_token: str) -> SlackIdentity | None
```

**Purpose**: Reads the Slack identity for the current token and mirrors the bot user id for hook-time readers. It is the trusted source for the Slack team and bot user.

**Data flow**: It receives context and bot token → reads matching identity metadata → if found, copies the bot user id into the scoped store → returns the identity or nothing.

**Call relations**: Inbound events, interactive actions, and outbound mention mapping call this before trusting Slack team or bot-user information.

*Call graph*: calls 2 internal fn (_mirror_self_user_id, read_identity); called by 3 (_reply_mention_ids, ingest, interactive).


##### `_mirror_self_user_id`  (lines 361–377)

```
async def _mirror_self_user_id(workspace_id: UUID, bot_user_id: str) -> None
```

**Purpose**: Copies the bot user id into a lightweight scoped store. This matters because some later hooks can read that store but cannot read the identity blob directly.

**Data flow**: It receives a workspace id and bot user id → skips work if this process already mirrored the same value → writes it to the extension store → updates an in-process cache.

**Call relations**: Identity loading and OAuth installation call this so turn-time hooks can recognize the bot user later.

*Call graph*: called by 2 (_identity, oauth_callback); 1 external calls (__init__).


##### `SlackIdentityResolver.resolve`  (lines 390–396)

```
async def resolve(self) -> SlackIdentity
```

**Purpose**: Finds or proves the Slack identity for a manually configured Slack app. It avoids repeating Slack API proof if a valid identity is already stored.

**Data flow**: It reads stored identity for the token → returns it if valid → otherwise calls Slack to prove the token → stores the resulting identity → returns it.

**Call relations**: Background identity repair and bring-your-own-app setup use this to turn a pasted bot token into trusted team and bot-user ids.

*Call graph*: calls 2 internal fn (_prove, read_identity).


##### `SlackIdentityResolver._prove`  (lines 398–423)

```
async def _prove(self) -> SlackIdentity
```

**Purpose**: Calls Slack’s auth.test endpoint to prove which team and bot user a token belongs to. It rejects malformed or unsuccessful Slack responses.

**Data flow**: It sends the bot token to Slack → reads Slack’s JSON response → validates team id and bot user id formats → returns a SlackIdentity with a token fingerprint.

**Call relations**: The resolver calls this only when no valid identity record already exists.

*Call graph*: calls 2 internal fn (__init__, bot_token_fingerprint); called by 1 (resolve); 3 external calls (__init__, AsyncClient, match).


##### `_prove_identity_in_background`  (lines 429–443)

```
def _prove_identity_in_background(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Starts a background identity proof for a workspace whose token exists but identity record is missing. This lets Slack retry the event after the identity has been repaired.

**Data flow**: It receives context and token → checks whether a proof task is already running for the workspace → creates one if not → records it until it finishes.

**Call relations**: The missing-identity response path calls this so inbound events can recover without blocking Slack’s immediate response.

*Call graph*: calls 1 internal fn (_run_identity_proof); called by 1 (_identity_unavailable); 1 external calls (create_task).


##### `_prove_identity_in_background._untrack`  (lines 439–441)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished background identity-proof task from the in-process task table. This prevents the table from keeping old task references forever.

**Data flow**: It receives the completed task → checks that it is still the task recorded for the workspace → deletes that entry.

**Call relations**: It is attached as a completion callback when the background proof task is created.


##### `_run_identity_proof`  (lines 446–452)

```
async def _run_identity_proof(ctx: SurfaceContext, bot_token: str) -> None
```

**Purpose**: Runs the actual background Slack identity proof and logs failures. It keeps identity repair from crashing the request handler.

**Data flow**: It receives context and token → constructs a resolver → asks it to resolve identity → logs Slack-specific or unexpected errors.

**Call relations**: The background task launcher creates this coroutine when identity is unavailable but a bot token exists.

*Call graph*: called by 1 (_prove_identity_in_background); 1 external calls (__init__).


##### `_identity_unavailable`  (lines 460–488)

```
def _identity_unavailable(ctx: SurfaceContext, bot_token: str | None) -> Response
```

**Purpose**: Builds the HTTP response for a verified Slack request when the workspace cannot currently prove its Slack identity. It either starts repair or tells Slack not to retry when no token exists.

**Data flow**: It receives context and maybe a bot token → if the token is missing, logs once and returns a no-retry 503 → if the token exists, starts background proof and returns a retryable 503.

**Call relations**: Event and interactivity routes use this when installation is incomplete or identity metadata is missing.

*Call graph*: calls 1 internal fn (_prove_identity_in_background); called by 2 (ingest, interactive); 2 external calls (Response, warn).


##### `signing_secret_fingerprint`  (lines 495–498)

```
def signing_secret_fingerprint(signing_secret: str) -> str
```

**Purpose**: Creates a one-way fingerprint of a Slack signing secret. This lets the system remember that Slack verified the current secret without storing the secret itself.

**Data flow**: It receives a signing secret → hashes it with SHA-256 → returns the hexadecimal fingerprint.

**Call relations**: URL verification marking and live-install checks use this to compare current and previously verified secrets.

*Call graph*: called by 2 (_mark_url_verified, verifying_fingerprint); 1 external calls (sha256).


##### `verifying_fingerprint`  (lines 501–508)

```
async def verifying_fingerprint(credentials: CredentialAccess) -> str | None
```

**Purpose**: Computes the fingerprint for the signing secret currently used by a workspace’s extension credentials. If no secret is stored there, there is no live per-workspace proof.

**Data flow**: It receives credential access → tries to read the Slack signing-secret slot → fingerprints it → returns the fingerprint or nothing.

**Call relations**: The install-status check calls this before deciding whether a stored URL verification marker still counts.

*Call graph*: calls 2 internal fn (get, signing_secret_fingerprint); called by 1 (install_is_live).


##### `install_is_live`  (lines 511–525)

```
async def install_is_live(ext: ExtensionContext) -> bool
```

**Purpose**: Answers whether Slack is fully connected for the current workspace. It requires both a verified request URL and a stored bot token.

**Data flow**: It receives an extension context → computes the current signing-secret fingerprint → checks that the bot token slot is stored → compares the scoped store’s verification marker → returns true or false.

**Call relations**: Slack setup/status tooling can call this to report whether the workspace is actually connected.

*Call graph*: calls 1 internal fn (verifying_fingerprint).


##### `slack_oauth_exchange`  (lines 545–578)

```
async def slack_oauth_exchange(code: str, redirect_uri: str) -> SlackInstall
```

**Purpose**: Trades Slack’s temporary OAuth code for a real bot token and identity details. It refuses to store anything if Slack’s response is missing required identity fields.

**Data flow**: It receives a code and redirect URL → sends client credentials and code to Slack → validates access token, team id, bot user id, and optional app id → returns a SlackInstall object.

**Call relations**: The OAuth callback calls this after checking the sealed install state.

*Call graph*: calls 4 internal fn (__init__, _slack_ok, slack_client_id, slack_client_secret); called by 1 (oauth_callback); 3 external calls (__init__, AsyncClient, match).


##### `slack_app_dm_url`  (lines 581–586)

```
def slack_app_dm_url(app_id: str, team_id: str) -> str
```

**Purpose**: Builds a browser URL that opens the installed Slack app’s direct message. This gives the installer a friendly place to continue after setup.

**Data flow**: It receives Slack app id and team id → URL-encodes them into Slack’s app_redirect endpoint → returns the link.

**Call relations**: The OAuth callback uses this on the success page when Slack supplied an app id.

*Call graph*: called by 1 (oauth_callback); 1 external calls (urlencode).


##### `SlackConversationSearch.run`  (lines 673–687)

```
async def run(self) -> SlackConversationMatches
```

**Purpose**: Searches Slack conversations for channels or DMs matching a user’s query. It makes DMs searchable by resolving the people in them.

**Data flow**: It receives the bot token, bot user id, and query stored on the object → lists conversations → resolves DM people → filters by case-insensitive text match → returns matches plus a truncation flag.

**Call relations**: This is the public entry point of SlackConversationSearch and coordinates its listing, people lookup, and conversion helpers.

*Call graph*: calls 3 internal fn (_conversation, _list, _people); 2 external calls (__init__, AsyncClient).


##### `SlackConversationSearch._list`  (lines 689–708)

```
async def _list(self, client: httpx.AsyncClient) -> tuple[list[object], bool]
```

**Purpose**: Fetches a bounded number of Slack conversation-list pages. The bound prevents a huge workspace or broken cursor from making the search run forever.

**Data flow**: It receives an HTTP client → repeatedly calls Slack conversations.list with cursor parameters → collects channel objects → returns the raw list and whether more pages remained.

**Call relations**: The search run calls this before turning raw Slack records into searchable conversation objects.

*Call graph*: calls 3 internal fn (_next_cursor, _params, _slack_ok); called by 1 (run); 1 external calls (get).


##### `SlackConversationSearch._params`  (lines 710–718)

```
def _params(self, cursor: str) -> dict[str, str]
```

**Purpose**: Builds the query parameters for one Slack conversation-list request. It includes conversation types, archive filtering, page size, and optional cursor.

**Data flow**: It receives a cursor string → creates a parameter dictionary → includes the cursor only when non-empty → returns it.

**Call relations**: The paged listing helper uses this for each Slack API request.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._next_cursor`  (lines 720–723)

```
def _next_cursor(self, payload: dict[str, object]) -> str
```

**Purpose**: Extracts Slack’s next-page cursor from a response. If Slack does not provide one, the listing is complete.

**Data flow**: It receives a Slack JSON payload → looks inside response metadata → returns the cursor string or an empty string.

**Call relations**: The conversation listing loop uses this to decide whether to fetch another page.

*Call graph*: called by 1 (_list).


##### `SlackConversationSearch._people`  (lines 725–753)

```
async def _people(self, client: httpx.AsyncClient, listed: list[object]) -> tuple[dict[str, tuple[str, ...]], bool]
```

**Purpose**: Resolves the people in direct messages and group DMs so searches can match names or emails. It deliberately caps how many DMs it resolves.

**Data flow**: It receives an HTTP client and raw conversation list → gathers member ids for DMs/group DMs → looks up each user once → builds people labels by conversation → returns labels and whether the cap was hit.

**Call relations**: The main search calls this after listing conversations and before filtering them.

*Call graph*: calls 4 internal fn (_kind, _label, _members, _slack_user); called by 1 (run).


##### `SlackConversationSearch._kind`  (lines 755–762)

```
def _kind(self, raw: dict[str, object]) -> SlackConversationKind
```

**Purpose**: Classifies a raw Slack conversation as a public channel, private channel, group DM, or 1:1 DM. This makes later code independent of Slack’s many boolean flags.

**Data flow**: It receives one raw conversation dictionary → checks Slack flags in priority order → returns a simple kind string.

**Call relations**: Conversation conversion, member lookup, and people resolution all use this classification.

*Call graph*: called by 3 (_conversation, _members, _people).


##### `SlackConversationSearch._members`  (lines 764–778)

```
async def _members(self, client: httpx.AsyncClient, raw: dict[str, object], convo_id: str) -> tuple[str, ...]
```

**Purpose**: Gets member ids for a DM or group DM. A 1:1 DM carries its user directly, while group DMs need a Slack members API call.

**Data flow**: It receives an HTTP client, raw conversation, and conversation id → returns the single DM user or calls Slack for group members → returns a tuple of user ids.

**Call relations**: People resolution calls this for each DM-like conversation it decides to resolve.

*Call graph*: calls 2 internal fn (_kind, _slack_ok); called by 1 (_people); 1 external calls (get).


##### `SlackConversationSearch._label`  (lines 780–785)

```
def _label(self, user: SlackUser | None, user_id: str) -> str
```

**Purpose**: Turns a Slack user record into a readable search label. It prefers name plus email when both are available.

**Data flow**: It receives an optional SlackUser and fallback user id → builds a label from name/email → returns the label or the raw id.

**Call relations**: People resolution uses this after user lookups to create searchable DM participant names.

*Call graph*: called by 1 (_people).


##### `SlackConversationSearch._conversation`  (lines 787–804)

```
def _conversation(self, raw: object, people: dict[str, tuple[str, ...]]) -> SlackConversation | None
```

**Purpose**: Converts one raw Slack conversation record into the smaller SlackConversation model used by search results. Invalid records are skipped.

**Data flow**: It receives a raw object and people labels → validates that the record has an id → extracts name, purpose, topic, kind, people, and membership → returns a SlackConversation or nothing.

**Call relations**: The main search calls this for every listed raw conversation before filtering by query.

*Call graph*: calls 2 internal fn (_kind, _nested_value); called by 1 (run); 1 external calls (__init__).


##### `SlackConversationSearch._nested_value`  (lines 806–808)

```
def _nested_value(self, field: object) -> str
```

**Purpose**: Extracts Slack’s nested purpose or topic text safely. Slack wraps these fields in objects rather than plain strings.

**Data flow**: It receives a field object → if it is a dictionary, reads its value → returns the string value or an empty string.

**Call relations**: Conversation conversion uses this for the purpose and topic fields.

*Call graph*: called by 1 (_conversation); 1 external calls (get).


##### `verify_slack_signature`  (lines 983–998)

```
def verify_slack_signature(headers: Mapping[str, str], body: bytes, signing_secret: str, now: float | None=None) -> None
```

**Purpose**: Checks that a request really came from Slack and is recent enough to avoid replay attacks. A replay attack is someone resending an old valid request.

**Data flow**: It receives headers, raw body, signing secret, and optional current time → checks timestamp and signature headers → recomputes Slack’s HMAC signature → raises on any mismatch, otherwise returns nothing.

**Call relations**: Workspace resolution, event ingest, and interactivity ingest all call this before trusting request contents.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 4 external calls (__init__, compare_digest, new, time).


##### `_slack_request_body`  (lines 1005–1024)

```
async def _slack_request_body(request: Request) -> bytes
```

**Purpose**: Reads and caches the raw request body while enforcing a maximum size. The raw bytes must stay unchanged because Slack signatures are checked over exact bytes.

**Data flow**: It receives a request → returns a previously cached body if present → otherwise streams chunks, counts bytes, rejects oversize bodies, caches the bytes, and returns them.

**Call relations**: All Slack HTTP routes and workspace resolution use this before parsing or verifying a request.

*Call graph*: called by 3 (ingest, interactive, resolve_workspace); 1 external calls (stream).


##### `url_verification_challenge`  (lines 1027–1036)

```
def url_verification_challenge(body: bytes) -> str | None
```

**Purpose**: Extracts Slack’s URL verification challenge from a setup request. Slack sends this to prove the server controls the configured endpoint.

**Data flow**: It receives raw bytes → tries to parse JSON → if the type is url_verification, returns the challenge string → otherwise returns nothing.

**Call relations**: Workspace resolution and event ingest use this to answer Slack setup probes quickly.

*Call graph*: called by 2 (ingest, resolve_workspace); 1 external calls (loads).


##### `slack_team_hint`  (lines 1039–1056)

```
def slack_team_hint(body: bytes) -> str | None
```

**Purpose**: Finds the Slack team id inside an untrusted request body. This is only a hint used to choose which workspace’s secret should verify the request.

**Data flow**: It receives raw bytes → parses JSON or form-encoded interactive payload → extracts team id → validates the id shape → returns the team id or nothing.

**Call relations**: Workspace resolution uses this before looking up the installation binding and verifying the signature.

*Call graph*: called by 1 (resolve_workspace); 3 external calls (loads, fullmatch, parse_qs).


##### `slack_installation_id`  (lines 1059–1060)

```
def slack_installation_id(team_id: str) -> str
```

**Purpose**: Turns a Slack team id into the installation key used by ufo. This is how a Slack workspace is bound to a ufo workspace.

**Data flow**: It receives a team id → prefixes it with team: → returns the installation id string.

**Call relations**: Workspace resolution and OAuth callback both use this exact key format.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `resolve_workspace`  (lines 1063–1101)

```
async def resolve_workspace(request: Request, auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Figures out which ufo workspace an incoming Slack route belongs to, before the normal route has a trusted workspace. It handles OAuth callbacks, events, interactive payloads, and URL verification probes.

**Data flow**: It receives a request and shared auth object → for GET callbacks, opens sealed state and returns its workspace → for POSTs, reads the body, handles URL verification, extracts team id, finds the workspace binding, verifies the signature, and returns the workspace id or a response/none.

**Call relations**: This is the pre-routing gate for Slack routes; it calls the body reader, team hint parser, signing-secret lookup, signature verifier, and installation-id helper.

*Call graph*: calls 9 internal fn (open_credential_authorization, workspace, _auth_signing_secret, _is_install_state, _slack_request_body, slack_installation_id, slack_team_hint, url_verification_challenge, verify_slack_signature); 1 external calls (JSONResponse).


##### `_is_install_state`  (lines 1104–1107)

```
def _is_install_state(claims: CredentialRequestState) -> bool
```

**Purpose**: Checks whether a sealed credential state belongs to Slack OAuth installation. It prevents a state token meant for another credential from being accepted here.

**Data flow**: It receives credential-state claims → checks the payload marker and requested slot → returns true only for the Slack bot-token install state.

**Call relations**: Workspace resolution and OAuth callback both use this before trusting the sealed state.

*Call graph*: called by 2 (oauth_callback, resolve_workspace).


##### `slack_thread_key`  (lines 1110–1115)

```
def slack_thread_key(channel: str, root_ts: str, is_dm: bool) -> str
```

**Purpose**: Builds the ufo conversation key for a Slack message. Channel conversations are keyed by channel plus thread root, while direct messages are keyed by the DM channel.

**Data flow**: It receives channel id, root timestamp, and DM flag → returns the channel alone for DMs or channel:root timestamp for channel threads.

**Call relations**: Inbound event parsing and interactive form parsing use this to attach Slack activity to the correct ufo conversation.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `slack_message_addressed`  (lines 1118–1136)

```
def slack_message_addressed(event: Mapping[str, object], bot_user_id: str, is_dm: bool) -> bool
```

**Purpose**: Decides whether a Slack message directly addresses the agent. Direct messages always do; channel messages usually need an @-mention that is not just the bot’s own footer.

**Data flow**: It receives a Slack event, bot user id, and DM flag → reads all message bodies → searches for real addressing mentions → returns true or false.

**Call relations**: Inbound conversion uses this to decide whether to admit the message immediately or treat it as ambient thread chatter.

*Call graph*: called by 1 (_to_inbound); 2 external calls (addressing_mention, message_bodies).


##### `_link_count`  (lines 1139–1142)

```
def _link_count(text: str) -> int
```

**Purpose**: Counts links in reply text so Slack link previews can be limited. Too many previews can bury the actual answer.

**Data flow**: It receives text → counts Markdown links → removes them → counts remaining bare URLs → returns the total.

**Call relations**: Slack reply body building uses this to disable unfurling when a message has many links.

*Call graph*: called by 1 (slack_reply_body); 2 external calls (findall, sub).


##### `_slack_atomic_spans`  (lines 1145–1183)

```
def _slack_atomic_spans(text: str) -> list[tuple[int, int]]
```

**Purpose**: Finds Markdown regions that should not be split in the middle, such as code fences and tables. This keeps long Slack replies readable after chunking.

**Data flow**: It receives text → scans line by line → records start and end positions for fenced code blocks and table-like blocks → returns the spans.

**Call relations**: Reply splitting calls this before choosing safe cut points.

*Call graph*: called by 1 (slack_reply_parts); 1 external calls (match).


##### `_slack_reply_cut`  (lines 1186–1206)

```
def _slack_reply_cut(text: str, start: int, limit: int, atomic_spans: list[tuple[int, int]]) -> int
```

**Purpose**: Chooses a safe place to cut one Slack reply chunk. It prefers paragraph, line, sentence, or word boundaries and avoids cutting inside protected Markdown spans.

**Data flow**: It receives text, start offset, size limit, and atomic spans → searches for the best boundary before the limit → returns the cut index.

**Call relations**: The reply splitter calls this repeatedly while breaking long text into Slack-sized parts.

*Call graph*: called by 1 (slack_reply_parts); 1 external calls (finditer).


##### `slack_reply_parts`  (lines 1209–1226)

```
def slack_reply_parts(text: str, limit: int=SLACK_MARKDOWN_TEXT_LIMIT) -> list[str]
```

**Purpose**: Splits long reply text into Slack-safe pieces. It respects Slack’s size limits while trying not to break Markdown awkwardly.

**Data flow**: It receives text and an optional limit → validates both → if needed, finds atomic spans and cuts text into chunks → returns the list of chunks.

**Call relations**: Final replies, mid-turn replies, and reply body construction all use this before posting to Slack.

*Call graph*: calls 2 internal fn (_slack_atomic_spans, _slack_reply_cut); called by 3 (post, slack_reply_body, speak).


##### `slack_reply_body`  (lines 1229–1296)

```
def slack_reply_body(channel: str, thread_ts: str | None, text: str, metadata: str | None, delivery_id: str | None=None, blocks: bool=True, actions: list[dict[str, object]] | None=None, sections: bool
```

**Purpose**: Builds the JSON body for one Slack message. It can include Markdown blocks, fallback plain text, action buttons/forms, metadata for duplicate detection, and footer text.

**Data flow**: It receives channel, thread, text, metadata, delivery id, and rendering options → validates sizes → builds Slack message JSON → may split block text → disables link previews for many links → returns encoded bytes.

**Call relations**: Progress posts, terminal replies, and mid-turn replies call this before sending chat.postMessage.

*Call graph*: calls 2 internal fn (_link_count, slack_reply_parts); called by 3 (_say, post, speak); 2 external calls (dumps, sub).


##### `_mrkdwn_section`  (lines 1299–1300)

```
def _mrkdwn_section(text: str) -> dict[str, object]
```

**Purpose**: Creates a simple Slack Block Kit section containing Markdown-style text. It trims the text to Slack’s section limit.

**Data flow**: It receives text → slices it to Slack’s allowed size → returns a section-block dictionary.

**Call relations**: Ask rendering helpers use this for titles and prose fallback.

*Call graph*: called by 2 (_ask_prose, slack_ask_blocks).


##### `slack_ask_blocks`  (lines 1303–1347)

```
def slack_ask_blocks(question: AskUserInput | None) -> list[dict[str, object]] | None
```

**Purpose**: Renders a ufo question as Slack form blocks when Slack can express it. If Slack cannot express one question safely, it renders the whole ask as prose and tells the user to reply in the thread.

**Data flow**: It receives an optional AskUserInput → returns nothing if there is no question → builds title, controls, and submit button → or builds prose fallback blocks.

**Call relations**: Terminal reply posting calls this when a turn ends by asking the user something.

*Call graph*: calls 3 internal fn (_ask_control, _ask_prose, _mrkdwn_section); called by 1 (post).


##### `_ask_control`  (lines 1350–1402)

```
def _ask_control(index: int, ask: AskQuestion) -> dict[str, object] | None
```

**Purpose**: Builds one Slack input control for one question. It chooses a text box, radio buttons, or checkboxes depending on the question shape.

**Data flow**: It receives the question index and question object → checks attachment and option limits → builds an input block with suitable element and defaults → returns the block or nothing if unsupported.

**Call relations**: Ask block rendering calls this for each question.

*Call graph*: calls 1 internal fn (_ask_option); called by 1 (slack_ask_blocks).


##### `_ask_option`  (lines 1405–1415)

```
def _ask_option(option: QuestionOption) -> dict[str, object]
```

**Purpose**: Turns one ufo answer option into a Slack choice option. It keeps the label as both display text and submitted value.

**Data flow**: It receives a QuestionOption → builds Slack option text and value → adds a trimmed description if present → returns the dictionary.

**Call relations**: Question controls use this when building radio button or checkbox options.

*Call graph*: called by 1 (_ask_control).


##### `_ask_prose`  (lines 1418–1426)

```
def _ask_prose(ask: AskQuestion) -> dict[str, object]
```

**Purpose**: Renders one question as readable prose for cases where Slack form controls are not suitable. This avoids silently dropping unsupported choices or attachments.

**Data flow**: It receives a question → builds lines for header, question, options, and multi-select note → wraps them in a Markdown section block.

**Call relations**: Ask rendering uses this when any control cannot be represented as a Slack form.

*Call graph*: calls 1 internal fn (_mrkdwn_section); called by 1 (slack_ask_blocks).


##### `slack_connect_blocks`  (lines 1429–1455)

```
def slack_connect_blocks(request: ConnectRequest | None, turn_id: UUID) -> list[dict[str, object]] | None
```

**Purpose**: Renders a Slack button that starts a private external-account connection flow. It lets a user authorize a provider without putting secrets in chat.

**Data flow**: It receives an optional connect request and turn id → returns nothing if there is no request → otherwise returns an action block with a connect button carrying the turn id.

**Call relations**: Terminal reply posting includes these blocks when the turn requests a connector authorization.

*Call graph*: called by 1 (post).


##### `_string_field`  (lines 1458–1462)

```
def _string_field(event: Mapping[str, object], field: str) -> str
```

**Purpose**: Reads a required string field from a Slack event or payload. It raises a clear error if Slack omitted something the code must have.

**Data flow**: It receives a mapping and field name → checks the value is a non-empty string → returns it or raises.

**Call relations**: Inbound and interaction parsers use this for required Slack ids and timestamps.

*Call graph*: called by 2 (_to_inbound, _to_interaction).


##### `_inbound_files`  (lines 1465–1477)

```
def _inbound_files(event: Mapping[str, object]) -> tuple[InboundFile, ...]
```

**Purpose**: Extracts downloadable file references from a Slack event. It skips hidden, tombstoned, malformed, and over-count files.

**Data flow**: It receives a Slack event → reads up to the inbound-file limit → chooses private download URLs and names → returns InboundFile objects.

**Call relations**: Inbound parsing uses this directly, and declared-file recovery uses it after fetching a message from Slack.

*Call graph*: called by 2 (_declared_files, _to_inbound); 1 external calls (__init__).


##### `_declared_files`  (lines 1480–1511)

```
async def _declared_files(bot_token: str, channel: str, ts: str, root_ts: str | None) -> tuple[InboundFile, ...]
```

**Purpose**: Fetches a Slack message back from its thread to discover files Slack did not include directly in the event body. This is a fallback for certain app_mention deliveries.

**Data flow**: It receives bot token, channel, message timestamp, and optional root timestamp → calls conversations.replies for exactly that message range → extracts files from the matching message → returns file references or an empty tuple.

**Call relations**: Inbound conversion calls this when the event type may have omitted file details.

*Call graph*: calls 2 internal fn (_inbound_files, _slack_ok); called by 1 (_to_inbound); 1 external calls (AsyncClient).


##### `oauth_callback`  (lines 1519–1594)

```
async def oauth_callback(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Completes Slack OAuth installation. It verifies the sealed install state, exchanges Slack’s code for a bot token, binds the Slack team to the ufo workspace, stores credentials and identity, and shows a success or error page.

**Data flow**: It receives context and browser request → handles Slack/user errors → validates state and workspace → exchanges code → binds installation → stores bot token and identity → mirrors bot id → returns a callback page response.

**Call relations**: This is the HTTP callback endpoint for the preferred “Add to Slack” install path.

*Call graph*: calls 10 internal fn (bind_installation, fulfill_credential_request, open_credential_authorization, _is_install_state, _mirror_self_user_id, bot_token_fingerprint, slack_app_dm_url, slack_installation_id, slack_oauth_exchange, slack_oauth_redirect_uri); 3 external calls (__init__, __init__, callback_page).


##### `_mark_url_verified`  (lines 1600–1618)

```
async def _mark_url_verified(ctx: SurfaceContext, signing_secret: str) -> None
```

**Purpose**: Records that Slack successfully reached this deployment with a request verified by the current signing secret. This is used to tell whether a manifest-app install is live.

**Data flow**: It receives context and signing secret → fingerprints the secret → skips if already recorded in this process → writes a marker blob → mirrors the fingerprint into the scoped store.

**Call relations**: Event and interactivity routes call this after a verified Slack request or URL challenge.

*Call graph*: calls 2 internal fn (mirror_url_verified, signing_secret_fingerprint); called by 2 (ingest, interactive); 2 external calls (dumps, time).


##### `mirror_url_verified`  (lines 1621–1636)

```
async def mirror_url_verified(workspace_id: UUID, fingerprint: str) -> None
```

**Purpose**: Copies a URL-verification fingerprint into the scoped store where extension status checks can read it. It is best-effort so a mirror failure does not break Slack event handling.

**Data flow**: It receives workspace id and fingerprint → skips if already mirrored → writes the value to the extension store → updates an in-process cache.

**Call relations**: The URL verification marker writer calls this after storing the durable marker.

*Call graph*: called by 1 (_mark_url_verified); 1 external calls (__init__).


##### `ingest`  (lines 1639–1683)

```
async def ingest(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack Events API requests. It verifies the request, checks installation identity, converts Slack events into inbound ufo messages, and either admits them or starts an ambient-reply decision.

**Data flow**: It receives context and request → reads body → verifies signature or answers URL verification → loads bot token and identity → filters wrong teams → converts to Inbound → admits addressed/files/live-turn messages or schedules ambient decision → returns a Slack acknowledgment.

**Call relations**: This is the main request handler for Slack message events and calls most inbound helpers.

*Call graph*: calls 12 internal fn (_admit_inbound, _bot_token, _ctx_signing_secret, _decide_ambient_in_background, _folds_into_live_turn, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_inbound (+2 more)); 3 external calls (loads, JSONResponse, Response).


##### `_folds_into_live_turn`  (lines 1686–1725)

```
async def _folds_into_live_turn(ctx: SurfaceContext, bot_token: str, inbound: Inbound) -> bool
```

**Purpose**: Checks whether an unmentioned Slack reply should be absorbed by a turn already running in that thread. If so, it bypasses the ambient classifier because no new turn is being founded.

**Data flow**: It receives context, bot token, and inbound message → checks for an existing conversation and absorbing turn → resolves the sender and seat permission → logs and returns true only when admission would fold into the live turn.

**Call relations**: The ingest route calls this before deciding whether an unaddressed reply needs model-based ambient gating.

*Call graph*: calls 4 internal fn (absorbing_turn, transaction, _resolve_member, _slack_user); called by 1 (ingest); 2 external calls (__init__, log).


##### `_admit_inbound`  (lines 1728–1778)

```
async def _admit_inbound(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Turns an accepted Slack message into a ufo turn. It gathers sender/source/context, downloads attached files, records Slack thread anchors, and asks core to admit the message.

**Data flow**: It receives context, bot token, inbound message, and identity → reads user, permalink, ambient context, and mention names → resolves member and conversation → mirrors the Slack thread → downloads files → builds fenced message body → admits it with an idempotency key → anchors DMs and starts followers if a run opened.

**Call relations**: Ingest calls this for directly admitted messages, and the ambient-decision task calls it when the decision says to reply.

*Call graph*: calls 13 internal fn (admit, conversation_for, retitle_conversation, _ambient_context, _anchor_dm_thread, _arm_followers, _download_files, _mirror_thread, _resolve_member, _slack_permalink (+3 more)); called by 2 (_run_ambient_decision, ingest); 9 external calls (__init__, __init__, __init__, gather, conversation_audience, fence_member_message, mint_marker, render_markup, unescape).


##### `_decide_ambient_in_background`  (lines 1784–1809)

```
def _decide_ambient_in_background(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Starts the ambient reply decision after Slack has already been acknowledged. This avoids missing Slack’s short response deadline while a model decides whether the agent should join unmentioned thread chatter.

**Data flow**: It receives context, token, inbound message, and identity → skips if a task for the message already exists → creates a background task → keeps it referenced until it completes.

**Call relations**: Ingest calls this for unaddressed thread replies that might or might not warrant a new turn.

*Call graph*: calls 1 internal fn (_run_ambient_decision); called by 1 (ingest); 1 external calls (create_task).


##### `_decide_ambient_in_background._untrack`  (lines 1805–1807)

```
def _untrack(done: asyncio.Task[None]) -> None
```

**Purpose**: Removes a finished ambient-decision task from the task table. This keeps only currently running decisions in memory.

**Data flow**: It receives a completed task → checks it is still the task for that message id → deletes the entry.

**Call relations**: It is registered as the completion callback for ambient decision tasks.


##### `_run_ambient_decision`  (lines 1812–1827)

```
async def _run_ambient_decision(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> None
```

**Purpose**: Runs the ambient decision and admits the message if the decision wants a reply. It logs failures because Slack will not retry after the event was acknowledged.

**Data flow**: It receives context, token, inbound, and identity → asks whether a reply is wanted → if yes, admits the inbound → logs any exception as a dropped post-ack message.

**Call relations**: Background ambient task creation runs this coroutine.

*Call graph*: calls 2 internal fn (_admit_inbound, _ambient_reply_wanted); called by 1 (_decide_ambient_in_background); 1 external calls (log).


##### `_author_is_foreign`  (lines 1830–1837)

```
def _author_is_foreign(event: Mapping[str, object], team_id: str) -> bool
```

**Purpose**: Detects messages from users outside the installed Slack workspace, such as in Slack Connect shared channels. Those users are not served as members here.

**Data flow**: It receives an event and bound team id → compares source/user team fields to the bound team → returns true only when the author belongs to another team.

**Call relations**: Inbound parsing uses this early to skip foreign authors before member resolution.

*Call graph*: called by 1 (_to_inbound).


##### `_channel_origin`  (lines 1853–1889)

```
async def _channel_origin(ctx: SurfaceContext, payload: Mapping[str, object], event: Mapping[str, object], channel: str, audience_known: bool) -> ChannelOrigin
```

**Purpose**: Determines the privacy audience and human label for the Slack channel a message came from. This protects private and shared-channel boundaries.

**Data flow**: It receives context, payload, event, channel id, and whether an audience is already known → uses event hints and, when needed, Slack channel info → returns an audience and optional label or raises if unknown.

**Call relations**: Inbound conversion calls this to decide who may see the resulting conversation.

*Call graph*: calls 2 internal fn (credential, _channel_info); called by 1 (_to_inbound); 4 external calls (__init__, conversation_audience, foreign_room_audience, room_audience).


##### `_to_inbound`  (lines 1892–1942)

```
async def _to_inbound(ctx: SurfaceContext, payload: Mapping[str, object], identity: SlackIdentity) -> Inbound | None
```

**Purpose**: Converts a raw Slack event payload into the compact Inbound object the rest of admission understands. It filters out bot messages, unsupported subtypes, irrelevant ambient messages, and foreign authors.

**Data flow**: It receives context, payload, and identity → validates event shape → checks sender and addressing → computes thread key → finds existing conversation if needed → resolves channel origin and files → returns Inbound or nothing.

**Call relations**: The event ingest route calls this after verifying the request and installation.

*Call graph*: calls 9 internal fn (credential, _author_is_foreign, _channel_origin, _declared_files, _inbound_files, _participating_conversation, _string_field, slack_message_addressed, slack_thread_key); called by 1 (ingest); 2 external calls (__init__, gather).


##### `_participating_conversation`  (lines 1945–1956)

```
async def _participating_conversation(ctx: SurfaceContext, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation only if it already has at least one admitted turn. This avoids treating a just-created but not-yet-admitted row as real thread participation.

**Data flow**: It receives context and queue key → finds a conversation id → checks for a latest turn → returns the id only when a turn exists.

**Call relations**: Inbound parsing uses this to decide whether unaddressed thread replies belong to a conversation the agent has joined.

*Call graph*: calls 2 internal fn (find_conversation, latest_turn); called by 1 (_to_inbound).


##### `_slack_user`  (lines 1959–1991)

```
async def _slack_user(bot_token: str, slack_user_id: str) -> SlackUser | None
```

**Purpose**: Looks up a Slack user’s display details and confirmed email. The confirmed email is important for linking Slack users to ufo members.

**Data flow**: It receives bot token and Slack user id → calls users.info → extracts name, confirmed email, timezone, and team id → returns SlackUser or nothing on failure.

**Call relations**: Admission, live-turn folding, ask submission, name resolution, and conversation search all use this user lookup.

*Call graph*: calls 1 internal fn (_slack_ok); called by 5 (_people, _name, _admit_inbound, _folds_into_live_turn, _handle_answer_submit); 2 external calls (__init__, AsyncClient).


##### `_conversation_members`  (lines 2005–2025)

```
async def _conversation_members(bot_token: str, channel: str) -> tuple[str, ...]
```

**Purpose**: Reads a bounded roster of members in a Slack conversation. It is used to safely map outbound @names only to people already in the thread.

**Data flow**: It receives bot token and channel id → calls conversations.members with a limit → returns member ids or an empty tuple on failure.

**Call relations**: SlackNames.mention_ids uses this before resolving names for outbound mention notifications.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (mention_ids); 1 external calls (AsyncClient).


##### `SlackNames.of`  (lines 2046–2054)

```
async def of(self, texts: Sequence[str], users: Sequence[str]=()) -> dict[str, str]
```

**Purpose**: Resolves Slack user and channel ids mentioned in text into readable names. This makes admitted messages and context digests understandable instead of showing raw Slack ids.

**Data flow**: It receives texts and optional user ids → collects mentioned user/channel ids → asks the resolver/cache for names → returns an id-to-name map.

**Call relations**: Admission and ambient digest building use this before rendering Slack markup into readable text.

*Call graph*: calls 1 internal fn (_resolved); 2 external calls (mentioned_channels, mentioned_users).


##### `SlackNames.mention_ids`  (lines 2056–2070)

```
async def mention_ids(self, channel: str, identity: SlackIdentity) -> dict[str, str]
```

**Purpose**: Builds a safe map from names in an agent reply to Slack mention ids. It only considers people in the conversation and excludes the bot and foreign-team members.

**Data flow**: It receives a channel and identity → reads the channel roster → resolves those user names → filters to the installed team → returns a mention index keyed by mentionable names.

**Call relations**: Outbound reply mapping calls this when reply text contains @ signs.

*Call graph*: calls 2 internal fn (_resolved, _conversation_members); 1 external calls (mention_index).


##### `SlackNames._resolved`  (lines 2072–2080)

```
async def _resolved(self, wanted: Mapping[str, str]) -> dict[str, _NamedId]
```

**Purpose**: Combines cached and freshly fetched Slack names. It limits the number of new Slack lookups per call so one message cannot cause a lookup storm.

**Data flow**: It receives wanted ids and their lookup URLs → reads fresh cache rows → fetches missing ids up to the limit → stores fetched names → returns the merged map.

**Call relations**: Both inbound name rendering and outbound mention mapping use this shared resolver.

*Call graph*: calls 3 internal fn (_name, _remember, _remembered); called by 2 (mention_ids, of); 1 external calls (gather).


##### `SlackNames._remembered`  (lines 2082–2103)

```
async def _remembered(self, ids: Sequence[str]) -> dict[str, _NamedId]
```

**Purpose**: Reads non-stale name cache entries from the scoped store. Cached names avoid repeated Slack API calls for common participants.

**Data flow**: It receives ids → reads their cache rows → validates name, team, and timestamp → returns still-fresh _NamedId entries.

**Call relations**: The name resolver calls this before making any Slack API requests.

*Call graph*: called by 1 (_resolved); 3 external calls (__init__, __init__, now).


##### `SlackNames._name`  (lines 2105–2122)

```
async def _name(self, id_: str, url: str) -> _NamedId | None
```

**Purpose**: Fetches and sanitizes the display name for one Slack user or channel id. It removes Slack delimiter characters and limits length.

**Data flow**: It receives an id and Slack API URL → calls user or channel lookup → extracts raw name and team when relevant → normalizes whitespace and length → returns _NamedId or nothing.

**Call relations**: The shared name resolver calls this for cache misses.

*Call graph*: calls 2 internal fn (_channel_info, _slack_user); called by 1 (_resolved); 1 external calls (__init__).


##### `SlackNames._remember`  (lines 2124–2134)

```
async def _remember(self, names: Mapping[str, _NamedId]) -> None
```

**Purpose**: Writes newly resolved names into the scoped cache. This lets later messages reuse the same Slack names without another network call.

**Data flow**: It receives id-to-name entries → records current timestamp → writes each row with name, time, and team → logs failures without stopping.

**Call relations**: The name resolver calls this after fresh lookups.

*Call graph*: called by 1 (_resolved); 2 external calls (__init__, now).


##### `_slack_permalink`  (lines 2137–2157)

```
async def _slack_permalink(bot_token: str, channel: str, ts: str) -> str | None
```

**Purpose**: Fetches Slack’s own permalink for a message. This gives ufo turns a source link that points to the exact Slack message.

**Data flow**: It receives bot token, channel, and timestamp → calls chat.getPermalink → returns the permalink string or nothing on failure.

**Call relations**: Inbound admission and ask-answer submission use this when building turn context.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (AsyncClient).


##### `_turn_context`  (lines 2160–2177)

```
def _turn_context(sender: SlackUser | None, source: str | None, question: str | None=None) -> TurnContext
```

**Purpose**: Builds the contextual metadata attached to a ufo turn, such as sender name, timezone, source link, and answered question. It drops invalid timezones rather than rejecting the message.

**Data flow**: It receives optional SlackUser, source URL, and optional question → formats sender identity → constructs TurnContext → if timezone validation fails, retries without timezone.

**Call relations**: Inbound admission and answer submission call this before admitting a turn.

*Call graph*: called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (__init__).


##### `_resolve_member`  (lines 2180–2198)

```
async def _resolve_member(ctx: SurfaceContext, slack_user_id: str, is_dm: bool, sender: SlackUser | None) -> UUID | None
```

**Purpose**: Maps a Slack user to a ufo member. It first uses an existing link, then joins or links by Slack-confirmed email when available.

**Data flow**: It receives context, Slack user id, DM flag, and optional sender details → returns an already linked member → or joins by confirmed email → or returns nothing; in a DM with no user lookup it raises.

**Call relations**: Admission, live-turn folding, and ask submission use this to decide who spoke.

*Call graph*: calls 2 internal fn (join_member, linked_member); called by 3 (_admit_inbound, _folds_into_live_turn, _handle_answer_submit); 1 external calls (__init__).


##### `_ambient_reply_wanted`  (lines 2201–2226)

```
async def _ambient_reply_wanted(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity) -> bool
```

**Purpose**: Asks whether an unaddressed thread reply should start a new agent turn. It uses recent Slack thread history so the decision sees what people were discussing.

**Data flow**: It receives context, token, inbound, and identity → fetches ambient history → if history is empty, admits by default → otherwise calls core’s ambient reply decision → logs and returns false when no reply is wanted.

**Call relations**: The background ambient-decision task calls this before admitting an unaddressed message.

*Call graph*: calls 2 internal fn (ambient_reply_wanted, _ambient_history); called by 1 (_run_ambient_decision); 2 external calls (__init__, log).


##### `_ambient_history`  (lines 2229–2251)

```
async def _ambient_history(bot_token: str, inbound: Inbound, identity: SlackIdentity) -> tuple[AmbientMessage, ...]
```

**Purpose**: Builds the recent thread history used by the ambient reply classifier. It reads Slack directly because dropped ambient messages are not in ufo’s transcript.

**Data flow**: It receives token, inbound, and identity → fetches thread tail before the inbound timestamp → converts valid member/own-agent messages into AmbientMessage objects → returns the newest bounded messages oldest-first.

**Call relations**: Ambient reply decision calls this as its evidence source.

*Call graph*: calls 2 internal fn (_ambient_entry, _thread_tail); called by 1 (_ambient_reply_wanted).


##### `_thread_tail`  (lines 2254–2300)

```
async def _thread_tail(bot_token: str, channel: str, root_ts: str, latest: str) -> tuple[object, ...] | None
```

**Purpose**: Fetches messages from a Slack thread before a given timestamp, walking pages so it does not accidentally return only the start of a long thread. If the page cap is exceeded, it returns nothing trusted.

**Data flow**: It receives token, channel, root timestamp, and latest timestamp → calls conversations.replies across pages → gathers messages → returns them or nothing on failure/overflow.

**Call relations**: Ambient history and unseen-tail context both use this Slack thread reader.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_ambient_history, _unseen_tail); 1 external calls (AsyncClient).


##### `_ambient_entry`  (lines 2303–2323)

```
def _ambient_entry(item: object, inbound: Inbound, identity: SlackIdentity) -> tuple[float, AmbientMessage] | None
```

**Purpose**: Converts one fetched Slack message into a classifier-friendly ambient message. It keeps member messages and the agent’s own messages, but drops other bots and invalid records.

**Data flow**: It receives a raw item, inbound message, and identity → validates user, timestamp, and text → marks whether it was from the bot → rejects messages at or after the inbound → returns timestamp plus AmbientMessage or nothing.

**Call relations**: Ambient history calls this for each item returned by the thread-tail fetch.

*Call graph*: called by 1 (_ambient_history); 1 external calls (__init__).


##### `_ambient_context`  (lines 2326–2381)

```
async def _ambient_context(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds background context that should accompany an admitted Slack turn but is not already in ufo’s transcript. Examples include earlier thread messages before a mention and recent channel chatter before a new thread.

**Data flow**: It receives context, token, inbound, identity, and marker → returns empty for DMs → for existing conversations reads unseen dropped replies → otherwise fetches channel or thread context → renders a bounded digest.

**Call relations**: Inbound admission gathers this alongside user and permalink data before building the admitted message body.

*Call graph*: calls 4 internal fn (_digest_names, _slack_ok, _unseen_tail, ambient_digest); called by 1 (_admit_inbound); 1 external calls (AsyncClient).


##### `_digest_names`  (lines 2384–2393)

```
async def _digest_names(bot_token: str, messages: Sequence[object]) -> dict[str, str]
```

**Purpose**: Resolves names for authors and mentions in messages that will be placed in an ambient digest. This makes background context readable.

**Data flow**: It receives bot token and raw messages → collects message texts and author ids → asks SlackNames for readable names → returns an id-to-name map.

**Call relations**: Ambient context and unseen-tail context call this before rendering digests.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 1 external calls (__init__).


##### `_unseen_tail`  (lines 2396–2442)

```
async def _unseen_tail(ctx: SurfaceContext, bot_token: str, inbound: Inbound, identity: SlackIdentity, marker: str) -> str
```

**Purpose**: Builds context for unaddressed thread replies that were previously ignored and therefore never entered the transcript. This keeps the agent’s view closer to the Slack thread users see.

**Data flow**: It receives context, token, inbound, identity, and marker → fetches the thread tail → walks backward until it finds an admitted message → digests the newer unseen messages → returns context text.

**Call relations**: Ambient context calls this when a conversation already exists.

*Call graph*: calls 4 internal fn (admitted_body, _digest_names, _thread_tail, ambient_digest); called by 1 (_ambient_context).


##### `ambient_digest`  (lines 2445–2513)

```
def ambient_digest(messages: list[object], bot_user_id: str, note: str, marker: str, names: Mapping[str, str]) -> str
```

**Purpose**: Renders Slack messages into a safe, bounded background-context block. It escapes by shape and drops addressed messages so another person’s words do not become fake instructions.

**Data flow**: It receives raw messages, bot user id, note, marker, and name map → filters to relevant member messages → formats timestamped speaker lines → trims to the digest limit with an omission marker → returns tagged context text or empty string.

**Call relations**: Ambient context and unseen-tail context call this after fetching Slack messages and resolving names.

*Call graph*: called by 2 (_ambient_context, _unseen_tail); 3 external calls (fromtimestamp, addressing_mention, render_markup).


##### `_slack_download_host_ok`  (lines 2516–2518)

```
def _slack_download_host_ok(url: str) -> bool
```

**Purpose**: Checks that a download URL belongs to Slack before attaching the bot token. This avoids leaking the token to another host.

**Data flow**: It receives a URL → parses its hostname → returns true only for slack.com or a Slack subdomain.

**Call relations**: The streaming file downloader calls this before making an authenticated request.

*Call graph*: called by 1 (_stream_download); 1 external calls (urlparse).


##### `_stream_download`  (lines 2521–2539)

```
async def _stream_download(bot_token: str, url: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a private Slack file download in chunks without buffering the whole file. It enforces host and size limits.

**Data flow**: It receives bot token and URL → verifies the host → sends an authenticated GET → yields chunks → raises if total bytes exceed the workspace write limit.

**Call relations**: Inbound file download passes this stream directly into the workspace file writer.

*Call graph*: calls 1 internal fn (_slack_download_host_ok); called by 1 (_download_files); 2 external calls (__init__, AsyncClient).


##### `_download_files`  (lines 2551–2567)

```
async def _download_files(ctx: SurfaceContext, conversation_id: UUID, bot_token: str, files: tuple[InboundFile, ...]) -> DownloadedFiles
```

**Purpose**: Downloads Slack-attached files into the ufo workspace so the agent can use them. Oversized files are skipped and reported instead of half-written.

**Data flow**: It receives context, conversation id, token, and file references → picks safe inbox names → writes each streamed download into the workspace → records delivered and skipped names → returns DownloadedFiles.

**Call relations**: Inbound admission calls this when a Slack message has attachments.

*Call graph*: calls 2 internal fn (write_workspace_file, _stream_download); called by 1 (_admit_inbound); 2 external calls (__init__, inbox_name).


##### `files_note`  (lines 2570–2579)

```
def files_note(downloaded: DownloadedFiles) -> str
```

**Purpose**: Creates a short text note telling the agent which Slack files were saved and which were skipped. This note becomes part of the admitted member message.

**Data flow**: It receives DownloadedFiles → formats delivered workspace paths and skipped Slack filenames → returns a newline-joined note.

**Call relations**: Inbound admission appends this to the fenced message body after downloading files.

*Call graph*: called by 1 (_admit_inbound).


##### `MirroredThread.read`  (lines 2591–2596)

```
def read(cls, row: JsonValue) -> 'MirroredThread'
```

**Purpose**: Reads a stored Slack-thread mirror row into a MirroredThread object. It accepts both the newer object shape and older string-only rows.

**Data flow**: It receives stored JSON-like data → if it is a string, treats it as the queue key → otherwise validates it as a model → returns a MirroredThread.

**Call relations**: Hook-time follower arming and mid-turn Slack comments use this when reading durable thread mirrors.


##### `MirroredThread.anchor`  (lines 2598–2602)

```
def anchor(self) -> str | None
```

**Purpose**: Finds the Slack message timestamp that status and progress should attach to. Channel threads use the root timestamp from the key; DMs may use a separately stored message timestamp.

**Data flow**: It reads the object’s queue key and message timestamp → returns the channel root, DM anchor, or nothing.

**Call relations**: Status tracking and progress posting use this to know whether Slack can address a thread.

*Call graph*: called by 1 (_track_status).


##### `_thread_mirror_key`  (lines 2605–2606)

```
def _thread_mirror_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the scoped-store key for a conversation’s Slack thread mirror. This gives hooks a stable place to find Slack thread information later.

**Data flow**: It receives a conversation id → prefixes it with the Slack thread namespace → returns the store key.

**Call relations**: Thread mirroring writes this key, while hook-time follower arming and some mid-turn replies read it.

*Call graph*: called by 3 (_mirror_thread, follow_turn, speak).


##### `_mirror_thread`  (lines 2609–2617)

```
async def _mirror_thread(conversation_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Stores the Slack thread associated with a ufo conversation. This is written before turn execution so later followers know where to post.

**Data flow**: It receives a conversation id and MirroredThread → builds the store key → serializes the thread → writes it into the Slack scoped store.

**Call relations**: Inbound admission and answer submission call this before admitting or resuming a turn.

*Call graph*: calls 1 internal fn (_thread_mirror_key); called by 2 (_admit_inbound, _handle_answer_submit); 2 external calls (__init__, model_dump).


##### `_dm_anchor_key`  (lines 2620–2626)

```
def _dm_anchor_key(turn_id: UUID, message_ref: UUID | None=None) -> str
```

**Purpose**: Builds the scoped-store key for a DM message anchor. Direct messages need this because the conversation key names only the DM channel, not each member message timestamp.

**Data flow**: It receives a turn id and optional message ref → builds a base key for the founding message or a nested key for an absorbed message → returns the key.

**Call relations**: DM anchoring, reply-thread lookup, and cleanup all use this key format.

*Call graph*: called by 3 (_anchor_dm_thread, _drop_turn_reply_records, _reply_thread).


##### `_anchor_dm_thread`  (lines 2629–2636)

```
async def _anchor_dm_thread(admitted: Admitted, message_ts: str) -> None
```

**Purpose**: Records which Slack DM message a ufo turn or absorbed message answers. This lets replies thread under the user’s original DM message.

**Data flow**: It receives an Admitted result and Slack message timestamp → builds the DM anchor key from turn and arrival ids → stores the timestamp.

**Call relations**: Inbound admission and answer submission call this for direct-message conversations.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 2 (_admit_inbound, _handle_answer_submit); 1 external calls (__init__).


##### `_reply_thread`  (lines 2639–2654)

```
async def _reply_thread(queue_key: str, turn_id: UUID, message_ref: UUID | None=None) -> str | None
```

**Purpose**: Finds the Slack thread timestamp where a reply should be posted. Channel replies use the root from the queue key; DM replies use stored anchors when available.

**Data flow**: It receives queue key, turn id, and optional message ref → returns the channel root if present → otherwise reads the specific or fallback DM anchor → returns a timestamp or nothing.

**Call relations**: Terminal replies, mid-turn replies, and file attachments call this before posting to Slack.

*Call graph*: calls 1 internal fn (_dm_anchor_key); called by 3 (attach, post, speak); 1 external calls (__init__).


##### `FollowerContext.workspace_id`  (lines 2665–2665)

```
def workspace_id(self) -> UUID
```

**Purpose**: Defines that a follower context must expose the current workspace id. Followers need this to key status ownership and footer links.

**Data flow**: A concrete context provides the UUID → followers read it → no data is changed.

**Call relations**: Thread status, progress, and footer helpers rely on this protocol property through either SurfaceContext or the hook adapter.


##### `FollowerContext.public_base_url`  (lines 2668–2668)

```
def public_base_url(self) -> str | None
```

**Purpose**: Defines that a follower context must expose the public base URL, if configured. This is needed to build web and debug links in Slack footers.

**Data flow**: A concrete context provides a URL string or nothing → footer code reads it → no data is changed.

**Call relations**: The shared Slack footer helper uses this through the follower context abstraction.


##### `FollowerContext.credential`  (lines 2670–2670)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Defines how followers read credentials such as the Slack bot token. Status and progress tasks need the token to call Slack.

**Data flow**: A concrete context receives a slot name → returns the secret value asynchronously → no local state is changed by the protocol itself.

**Call relations**: Thread status, progress, and ephemeral posting depend on context implementations of this method.


##### `FollowerContext.tail`  (lines 2672–2674)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how followers subscribe to live turn frames. A live frame is a small event saying what the turn is doing, such as tool activity or text streaming.

**Data flow**: A concrete context receives a turn id and optional cursor → returns an async stream of cursor/frame pairs → followers consume it.

**Call relations**: ThreadStatus and ThreadProgress use this to keep Slack updated while a turn runs.


##### `FollowerContext.turn_is_terminal`  (lines 2676–2676)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Defines how progress followers ask whether a turn has already finished durably. This prevents progress posts after the final reply is committed.

**Data flow**: A concrete context receives a turn id → returns true or false → no data is changed by the protocol itself.

**Call relations**: ThreadProgress checks this at reporting deadlines.


##### `FollowerContext.is_operator_workspace`  (lines 2678–2678)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Defines how footer code checks whether the workspace is an operator/internal workspace. Operator workspaces may show extra accounting and debug links.

**Data flow**: A concrete context returns a boolean → footer code decides what to include → no data is changed.

**Call relations**: The Slack footer helper calls this before adding operator-only details.

*Call graph*: called by 1 (_slack_footer).


##### `ThreadStatus.thread`  (lines 2735–2736)

```
def thread(self) -> tuple[UUID, str, str]
```

**Purpose**: Returns the unique identity of the Slack thread whose status is being updated. It combines workspace, channel, and thread timestamp.

**Data flow**: It reads the status object’s context workspace id, channel, and thread timestamp → returns them as a tuple.

**Call relations**: Status writer tracking uses this to prevent older turns from overwriting newer thread statuses.


##### `ThreadStatus.run`  (lines 2738–2757)

```
async def run(self) -> None
```

**Purpose**: Runs the live Slack thread-status follower for one turn. It posts “Thinking…” first, follows live frames, and clears status when the turn ends.

**Data flow**: It reads the bot token → opens an HTTP client → writes initial status → follows frames to update status → clears status unless cancelled or another turn still owns it.

**Call relations**: The status task wrapper calls this after _track_status creates a ThreadStatus.

*Call graph*: calls 3 internal fn (_clear, _follow, _set); called by 1 (_run_status); 2 external calls (AsyncClient, log).


##### `ThreadStatus._set`  (lines 2759–2799)

```
async def _set(self, client: httpx.AsyncClient, bot_token: str, status: str) -> bool
```

**Purpose**: Writes one status line to Slack’s assistant thread status API. It only writes if this turn is still the current writer for that Slack thread.

**Data flow**: It receives client, token, and status text → checks writer ownership → sends assistant.threads.setStatus → logs success or failure → returns whether Slack accepted it.

**Call relations**: ThreadStatus.run, _follow, and _clear all use this single status-write method.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_clear, _follow, run); 3 external calls (post, dumps, log).


##### `ThreadStatus._clear`  (lines 2801–2810)

```
async def _clear(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Clears the Slack status when a turn ends, but only if no sibling turn is still running in the same thread. This avoids erasing another turn’s live indicator.

**Data flow**: It receives client and token → scans live statuses in this process → if none share the thread, writes an empty status.

**Call relations**: ThreadStatus.run calls this after normal completion or errors.

*Call graph*: calls 1 internal fn (_set); called by 1 (run).


##### `ThreadStatus._follow`  (lines 2812–2866)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str, shown: str) -> None
```

**Purpose**: Consumes live turn frames and turns them into short Slack status text. It also refreshes the last shown line because Slack expires statuses after a short time.

**Data flow**: It receives client, token, and currently shown text → waits for frames, blanking events, or refresh timeout → maps activity/text/resume/absorbed frames to bounded status lines → writes changed lines → exits on terminal or parked frames.

**Call relations**: ThreadStatus.run calls this after the initial status write.

*Call graph*: calls 1 internal fn (_set); called by 1 (run); 4 external calls (ensure_future, gather, wait, monotonic).


##### `_restamp_thread_status`  (lines 2876–2883)

```
def _restamp_thread_status(workspace_id: UUID, channel: str, thread_ts: str) -> None
```

**Purpose**: Wakes status followers for a Slack thread after the bot posts a progress message there. Slack clears thread status on a reply, so the status must be stamped again.

**Data flow**: It receives workspace id, channel, and thread timestamp → finds matching live ThreadStatus objects → sets their wake-up event.

**Call relations**: ThreadProgress._say calls this after a progress post lands in a thread.

*Call graph*: called by 1 (_say).


##### `_track_status`  (lines 2886–2907)

```
def _track_status(ctx: FollowerContext, turn_id: UUID, thread: MirroredThread) -> None
```

**Purpose**: Starts one thread-status task for a turn in the current process. It makes the newest turn the writer for that Slack thread.

**Data flow**: It receives follower context, turn id, and mirrored thread → skips if already tracking the turn → finds channel and anchor timestamp → creates ThreadStatus and task → records writer/status/task tables.

**Call relations**: _arm_followers calls this from admission and hook-time turn execution.

*Call graph*: calls 2 internal fn (anchor, _run_status); called by 1 (_arm_followers); 3 external calls (__init__, create_task, log).


##### `_run_status`  (lines 2910–2938)

```
async def _run_status(status: ThreadStatus) -> None
```

**Purpose**: Wraps a ThreadStatus task with logging and cleanup. It also passes writer ownership back to an older live turn when the newest turn ends.

**Data flow**: It receives a ThreadStatus → runs it → logs task-level failures → removes task/status records → updates or deletes the thread writer entry.

**Call relations**: _track_status creates tasks that run this wrapper.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_status); 1 external calls (log).


##### `ProgressCadence.__post_init__`  (lines 2950–2954)

```
def __post_init__(self) -> None
```

**Purpose**: Validates the progress-report timing settings. The first interval must be positive, and the cap cannot be smaller than it.

**Data flow**: It reads base_seconds and cap_seconds → raises ValueError for invalid values → otherwise leaves the object unchanged.

**Call relations**: Progress tracking constructs this before starting a ThreadProgress reporter.


##### `ProgressCadence.intervals`  (lines 2956–2967)

```
def intervals(self) -> Iterator[float]
```

**Purpose**: Generates the wait intervals between progress posts. The wait doubles with elapsed time until it reaches the cap.

**Data flow**: It starts at base_seconds → yields each wait → updates elapsed time → yields capped waits forever.

**Call relations**: checkpoints_after uses this schedule to find future reporting times.

*Call graph*: called by 1 (checkpoints_after).


##### `ProgressCadence.checkpoints_after`  (lines 2969–2978)

```
def checkpoints_after(self, elapsed_seconds: float) -> Iterator[float]
```

**Purpose**: Finds future progress checkpoints after a turn has already been running for some time. This lets a restarted reporter continue the real schedule rather than starting over.

**Data flow**: It receives elapsed seconds → walks interval checkpoints → yields only checkpoints greater than the elapsed time.

**Call relations**: ThreadProgress._follow uses this to decide when to post next.

*Call graph*: calls 1 internal fn (intervals).


##### `TurnActivity.update`  (lines 2991–2993)

```
def update(self, summary: str) -> None
```

**Purpose**: Records the latest completed activity summary for progress messages. It clears any streaming text because a new completed step has arrived.

**Data flow**: It receives a summary → clears streaming fragments → normalizes whitespace and trims length → stores it as current activity.

**Call relations**: ThreadProgress._follow calls this for activity and subagent activity frames.


##### `TurnActivity.stream`  (lines 2995–2996)

```
def stream(self, text: str) -> None
```

**Purpose**: Records that text is currently streaming. Progress messages should say the response is being prepared rather than exposing partial text.

**Data flow**: It receives text → appends it to the streaming list → returns nothing.

**Call relations**: ThreadProgress._follow calls this for text-delta frames.


##### `TurnActivity.current_step`  (lines 2998–3002)

```
def current_step(self) -> str
```

**Purpose**: Returns the member-facing current step for progress reporting. Streaming text takes priority over the last completed activity.

**Data flow**: It reads the streaming list and activity string → returns “Preparing the response” if streaming exists, otherwise the last activity.

**Call relations**: TurnActivity.report calls this when building a progress line.

*Call graph*: called by 1 (report).


##### `TurnActivity.report`  (lines 3004–3014)

```
def report(self, elapsed_seconds: float) -> str | None
```

**Purpose**: Builds the text for one progress checkpoint, or skips it if no activity has happened. The message tells the user what step has been taking time.

**Data flow**: It receives elapsed seconds → gets current step → if empty returns nothing → formats elapsed minutes/hours → returns a short progress line.

**Call relations**: ThreadProgress._post calls this before posting a checkpoint to Slack.

*Call graph*: calls 1 internal fn (current_step); called by 1 (_post).


##### `ThreadProgress.run`  (lines 3055–3058)

```
async def run(self) -> None
```

**Purpose**: Runs the long-turn progress reporter for one Slack turn. It posts occasional interim messages when a turn takes a long time.

**Data flow**: It reads the bot token → opens an HTTP client → follows live frames and deadlines → returns when the follow loop ends.

**Call relations**: The progress task wrapper calls this after _track_progress creates a ThreadProgress.

*Call graph*: calls 1 internal fn (_follow); called by 1 (_run_progress); 1 external calls (AsyncClient).


##### `ThreadProgress._elapsed`  (lines 3060–3063)

```
def _elapsed(self) -> float
```

**Purpose**: Measures how long the user has been waiting for this turn. It uses wall-clock time from the durable turn start, so restarts do not reset the wait.

**Data flow**: It reads the current UTC time and the stored started_at time → subtracts them → returns elapsed seconds.

**Call relations**: The progress follow loop and resumed notice helper use this to schedule posts.

*Call graph*: called by 2 (_follow, _post_resumed); 1 external calls (now).


##### `ThreadProgress._follow`  (lines 3065–3118)

```
async def _follow(self, client: httpx.AsyncClient, bot_token: str) -> None
```

**Purpose**: Runs the progress reporting loop. It watches turn frames, waits for scheduled checkpoints, posts progress, and announces restarts after a grace period.

**Data flow**: It initializes activity, spend, schedule, and live frame task → waits for either frames or deadlines → updates activity/spend/resume state from frames → posts checkpoint or resume messages when due → exits on terminal, parked, or finished tail.

**Call relations**: ThreadProgress.run delegates the reporter’s main behavior to this method.

*Call graph*: calls 3 internal fn (_elapsed, _post, _post_resumed); called by 1 (run); 4 external calls (__init__, ensure_future, gather, wait).


##### `ThreadProgress._post`  (lines 3120–3147)

```
async def _post(self, client: httpx.AsyncClient, bot_token: str, activity: TurnActivity, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts one scheduled progress checkpoint if there is useful activity to report. It skips empty checkpoints rather than sending filler.

**Data flow**: It receives client, token, activity, elapsed seconds, spend, and first-message flag → asks activity for report text → logs skip if none → otherwise sends it through _say and returns whether it landed.

**Call relations**: The progress follow loop calls this when a schedule deadline arrives.

*Call graph*: calls 2 internal fn (_say, report); called by 1 (_follow); 1 external calls (log).


##### `ThreadProgress._post_resumed`  (lines 3149–3160)

```
async def _post_resumed(self, client: httpx.AsyncClient, bot_token: str, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Posts a one-time notice that a turn resumed after a service restart. It is delayed briefly so fast completions do not get an unnecessary warning.

**Data flow**: It receives client, token, spend, and first-message flag → computes elapsed time → sends the fixed resume notice through _say → returns whether it landed.

**Call relations**: The progress follow loop calls this after seeing a resume frame and waiting through the grace period.

*Call graph*: calls 2 internal fn (_elapsed, _say); called by 1 (_follow).


##### `ThreadProgress._say`  (lines 3162–3202)

```
async def _say(self, client: httpx.AsyncClient, bot_token: str, text: str, elapsed_seconds: float, spend: CostTick | None, first: bool) -> bool
```

**Purpose**: Sends one progress or resume message to Slack. The first successful progress message may include the standard footer.

**Data flow**: It receives client, token, text, elapsed time, spend, and first flag → finds channel and thread anchor → optionally builds footer → posts Slack message body → logs success/failure → restamps thread status if needed → returns whether it posted.

**Call relations**: Both checkpoint and resume posting go through this shared Slack-send method.

*Call graph*: calls 4 internal fn (_footer, _restamp_thread_status, _slack_ok, slack_reply_body); called by 2 (_post, _post_resumed); 2 external calls (post, log).


##### `ThreadProgress._footer`  (lines 3204–3221)

```
async def _footer(self, bot_token: str, channel: str, spend: CostTick | None) -> str | None
```

**Purpose**: Builds the footer for a progress message using whatever cost data is known so far. It may omit accounting because the turn is still running.

**Data flow**: It receives bot token, channel, and optional CostTick → formats accounting if present → calls the shared Slack footer builder → returns footer text or nothing.

**Call relations**: ThreadProgress._say calls this for the first progress message.

*Call graph*: calls 1 internal fn (_slack_footer); called by 1 (_say).


##### `_track_progress`  (lines 3227–3254)

```
def _track_progress(ctx: FollowerContext, turn_id: UUID, conversation_id: UUID, thread: MirroredThread, started_at: datetime) -> None
```

**Purpose**: Starts one progress reporter task for a turn in the current process. It only starts when the caller knows the turn’s durable start time.

**Data flow**: It receives follower context, turn id, conversation id, mirrored thread, and start time → skips if already tracking → builds cadence and ThreadProgress → creates and records the task.

**Call relations**: _arm_followers calls this from the turn-execution hook, not from plain admission.

*Call graph*: calls 1 internal fn (_run_progress); called by 1 (_arm_followers); 4 external calls (__init__, __init__, create_task, now).


##### `_run_progress`  (lines 3257–3272)

```
async def _run_progress(progress: ThreadProgress) -> None
```

**Purpose**: Wraps a ThreadProgress task with logging and cleanup. Single failed posts are handled inside the reporter, so errors here mean the reporter is abandoned.

**Data flow**: It receives ThreadProgress → runs it → logs task-level errors → removes the task record when done.

**Call relations**: _track_progress creates tasks that run this wrapper.

*Call graph*: calls 1 internal fn (run); called by 1 (_track_progress); 1 external calls (log).


##### `_arm_followers`  (lines 3286–3300)

```
def _arm_followers(ctx: FollowerContext, turn: FollowedTurn, thread: MirroredThread) -> None
```

**Purpose**: Starts the Slack live-feedback followers appropriate for a turn. Status can start immediately; progress starts only when the turn’s execution supplies a durable start time.

**Data flow**: It receives follower context, followed turn, and mirrored thread → always asks to track status → asks to track progress only if started_at is present.

**Call relations**: Inbound admission, answer submission, and the turn hook all converge here so follower setup stays consistent.

*Call graph*: calls 2 internal fn (_track_progress, _track_status); called by 3 (_admit_inbound, _handle_answer_submit, follow_turn).


##### `_HookFollowerContext.workspace_id`  (lines 3312–3313)

```
def workspace_id(self) -> UUID
```

**Purpose**: Exposes the hook context’s workspace id through the follower protocol. This lets hook-started followers behave like request-started followers.

**Data flow**: It reads workspace_id from the wrapped extension context → returns it.

**Call relations**: Followers use this property when _HookFollowerContext is passed into _arm_followers.


##### `_HookFollowerContext.public_base_url`  (lines 3316–3317)

```
def public_base_url(self) -> str | None
```

**Purpose**: Exposes the hook context’s public base URL through the follower protocol. This allows progress footers to include web links.

**Data flow**: It reads public_base_url from the extension context → returns the URL or nothing.

**Call relations**: The shared footer helper reads this through the follower context abstraction.


##### `_HookFollowerContext.credential`  (lines 3319–3320)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads credentials from a hook’s extension credential access. This is how hook-started followers get the Slack bot token.

**Data flow**: It receives a credential slot name → asks extension credentials for that slot → returns the secret.

**Call relations**: ThreadStatus and ThreadProgress call this when they run under a hook context.


##### `_HookFollowerContext.tail`  (lines 3322–3325)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Provides the hook context’s live turn-frame stream to followers. It adapts the extension context to the FollowerContext protocol.

**Data flow**: It receives turn id and optional cursor → calls the extension context’s tail method → returns the async frame stream context manager.

**Call relations**: Status and progress followers use this to watch live frames after follow_turn arms them.


##### `_HookFollowerContext.turn_is_terminal`  (lines 3327–3328)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Lets hook-started progress reporters ask whether a turn has finished. This prevents late progress posts after completion.

**Data flow**: It receives a turn id → calls the extension context’s terminal check → returns the boolean result.

**Call relations**: ThreadProgress uses this when running under hook ownership.


##### `_HookFollowerContext.is_operator_workspace`  (lines 3330–3331)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Exposes the hook context’s operator-workspace check. This controls whether debug/accounting footer details may appear.

**Data flow**: It asks the extension context whether the workspace is an operator workspace → returns the boolean.

**Call relations**: The Slack footer helper calls this through the FollowerContext interface.


##### `follow_turn`  (lines 3334–3376)

```
async def follow_turn(ctx: HookContext) -> HookOutcome
```

**Purpose**: Hook handler that arms Slack status and progress followers from the turn’s own execution. This is important after restarts because the process that resumes the turn reattaches live feedback.

**Data flow**: It receives hook context → ignores missing/subagent turns → reads the mirrored Slack thread with a short timeout → on success wraps the hook context and arms followers with the turn start time → always returns no hook outcome.

**Call relations**: The manifest hook calls this during user_prompt_submit; it feeds _arm_followers without letting live-feedback setup block the turn.

*Call graph*: calls 2 internal fn (_arm_followers, _thread_mirror_key); 4 external calls (__init__, __init__, timeout, log).


##### `_handle_connect_click`  (lines 3423–3437)

```
async def _handle_connect_click(ctx: SurfaceContext, interaction: ConnectClick, member_id: UUID | None) -> None
```

**Purpose**: Responds to a Slack connect button click with a private authorization link or an error. The response is ephemeral, meaning only the clicking user sees it.

**Data flow**: It receives context, click details, and optional member id → if no member or invalid request, prepares an explanatory message → otherwise asks core for a connect URL → schedules an ephemeral Slack post.

**Call relations**: The interactivity route calls this after parsing a ConnectClick.

*Call graph*: calls 2 internal fn (connect_url, _ephemeral_in_background); called by 1 (interactive).


##### `_handle_answer_submit`  (lines 3440–3494)

```
async def _handle_answer_submit(ctx: SurfaceContext, bot_token: str, interaction: AnswerSubmit, member_id: UUID | None) -> Response | None
```

**Purpose**: Handles a submitted Slack question form. It admits the submitted answers as the next ufo turn and schedules the Slack message rewrite that freezes the answers in place.

**Data flow**: It receives context, token, AnswerSubmit, and optional member id → finds the conversation → rejects empty submits with an ephemeral note → resolves sender/member/permalink → mirrors thread → admits answers with an idempotency key → anchors DMs and arms followers if needed → schedules rewrite only for the winning body.

**Call relations**: The interactivity route calls this after parsing an AnswerSubmit.

*Call graph*: calls 13 internal fn (admit, admitted_body, conversation_for, find_conversation, _anchor_dm_thread, _arm_followers, _ephemeral_in_background, _mirror_thread, _resolve_member, _rewrite_in_background (+3 more)); called by 1 (interactive); 7 external calls (__init__, __init__, gather, conversation_audience, JSONResponse, fence_member_message, mint_marker).


##### `interactive`  (lines 3497–3539)

```
async def interactive(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles Slack interactive payloads such as form submits and connect button clicks. It verifies the request, loads Slack identity, parses the interaction, and dispatches the appropriate action.

**Data flow**: It receives context and request → reads bounded raw body → verifies signature → loads bot token and identity → parses interaction → marks URL verified → finds linked member → handles connect click or answer submit → returns Slack acknowledgment.

**Call relations**: This is the main Slack interactivity HTTP route.

*Call graph*: calls 11 internal fn (linked_member, _bot_token, _ctx_signing_secret, _handle_answer_submit, _handle_connect_click, _identity, _identity_unavailable, _mark_url_verified, _slack_request_body, _to_interaction (+1 more)); 2 external calls (JSONResponse, Response).


##### `_rewrite_in_background`  (lines 3545–3548)

```
def _rewrite_in_background(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Starts a background task to rewrite a submitted question message. This lets the Slack interactivity route answer quickly.

**Data flow**: It receives bot token and submitted answer payload → creates a rewrite task → stores it in a task set → removes it when done.

**Call relations**: Answer submission calls this after core confirms that this submit won the idempotency race.

*Call graph*: calls 1 internal fn (_run_rewrite); called by 1 (_handle_answer_submit); 1 external calls (create_task).


##### `_run_rewrite`  (lines 3551–3555)

```
async def _run_rewrite(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Runs the Slack message rewrite for an answer submit and logs failures. A rewrite failure should not undo the admitted answer.

**Data flow**: It receives bot token and submit data → calls the control-replacement helper → logs any exception.

**Call relations**: The background rewrite launcher creates this coroutine.

*Call graph*: calls 1 internal fn (_replace_controls_with_answers); called by 1 (_rewrite_in_background).


##### `_ephemeral_in_background`  (lines 3558–3563)

```
def _ephemeral_in_background(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Starts a background task to post a private Slack message to one user. This keeps interactive requests within Slack’s short acknowledgment window.

**Data flow**: It receives context, channel, user id, thread timestamp, and text → creates an ephemeral-post task → tracks it until completion.

**Call relations**: Connect-click handling and empty-answer handling use this for private feedback.

*Call graph*: calls 1 internal fn (_post_ephemeral); called by 2 (_handle_answer_submit, _handle_connect_click); 1 external calls (create_task).


##### `_post_ephemeral`  (lines 3566–3593)

```
async def _post_ephemeral(ctx: SurfaceContext, channel: str, slack_user_id: str, thread_ts: str | None, text: str) -> None
```

**Purpose**: Posts a Slack ephemeral message visible only to one user, optionally inside a thread. This is used for private click feedback and validation errors.

**Data flow**: It receives context, channel, user id, thread timestamp, and text → reads bot token → calls chat.postEphemeral → logs failures.

**Call relations**: Background ephemeral task creation runs this coroutine.

*Call graph*: calls 2 internal fn (credential, _slack_ok); called by 1 (_ephemeral_in_background); 2 external calls (AsyncClient, dumps).


##### `_to_interaction`  (lines 3596–3658)

```
def _to_interaction(raw: bytes, identity: SlackIdentity) -> AnswerSubmit | ConnectClick | None
```

**Purpose**: Parses a verified Slack interactivity request into either an answer submit, a connect click, or nothing. It ignores selection changes and unsupported actions.

**Data flow**: It receives raw form bytes and identity → decodes the payload JSON → checks type and team → reads action, user, channel, and message → returns ConnectClick, AnswerSubmit, or nothing.

**Call relations**: The interactivity route calls this after signature verification and identity loading.

*Call graph*: calls 4 internal fn (_dict_field, _string_field, _submitted_answers, slack_thread_key); called by 1 (interactive); 5 external calls (__init__, __init__, loads, parse_qs, UUID).


##### `_submitted_answers`  (lines 3661–3687)

```
def _submitted_answers(blocks: tuple[Mapping[str, object], ...], state: object) -> tuple[SubmittedAnswer, ...]
```

**Purpose**: Extracts every answer from a submitted Slack form in the same order the questions were rendered. The questions come from the message blocks, and values come from Slack state.

**Data flow**: It receives message blocks and state object → iterates ask input blocks → reads each held control value → returns SubmittedAnswer objects.

**Call relations**: The interaction parser calls this when the action is the ask-submit button.

*Call graph*: calls 1 internal fn (_held_answer); called by 1 (_to_interaction); 1 external calls (__init__).


##### `_held_answer`  (lines 3690–3708)

```
def _held_answer(field: object) -> str
```

**Purpose**: Converts one Slack form control state into plain answer text. It supports radio buttons, checkboxes, and text boxes.

**Data flow**: It receives a control-state object → inspects its type → returns selected option value, comma-joined selected values, typed text, or an empty string.

**Call relations**: Submitted-answer extraction calls this for each input block.

*Call graph*: calls 1 internal fn (_option_value); called by 1 (_submitted_answers); 1 external calls (get).


##### `_option_value`  (lines 3711–3715)

```
def _option_value(option: object) -> str
```

**Purpose**: Reads the submitted value from a Slack option object. It returns an empty string for malformed options.

**Data flow**: It receives an option object → checks it is a dictionary → returns its string value field or empty string.

**Call relations**: Held-answer parsing uses this for radio button and checkbox selections.

*Call graph*: called by 1 (_held_answer).


##### `_dict_field`  (lines 3718–3722)

```
def _dict_field(payload: Mapping[str, object], field: str) -> Mapping[str, object]
```

**Purpose**: Reads a required dictionary field from a Slack payload. It raises a clear error when the payload shape is not what Slack actions require.

**Data flow**: It receives a mapping and field name → checks the field is a dictionary → returns it or raises.

**Call relations**: The interaction parser uses this for user, channel, and message objects.

*Call graph*: called by 1 (_to_interaction).


##### `_replace_controls_with_answers`  (lines 3725–3758)

```
async def _replace_controls_with_answers(bot_token: str, submit: AnswerSubmit) -> None
```

**Purpose**: Rewrites a Slack question message after a successful submit so controls become fixed answer lines. This shows what the agent actually received and prevents later edits.

**Data flow**: It receives bot token and submit data → replaces each input block with an answer context line → replaces the submit block with who submitted → leaves other blocks unchanged → calls Slack chat.update.

**Call relations**: The rewrite task calls this after answer admission wins.

*Call graph*: calls 2 internal fn (_context_line, _rewrite_slack_message); called by 1 (_run_rewrite).


##### `connect_message_key`  (lines 3780–3783)

```
def connect_message_key(member_id: UUID, provider: str) -> str
```

**Purpose**: Builds the store key for remembering where a connect button was posted. It is keyed by member and provider so the landing grant can find the latest button.

**Data flow**: It receives member id and provider name → formats them into a connect-message key → returns it.

**Call relations**: Connect-message holding uses this before storing button location.

*Call graph*: called by 1 (_hold_connect_message).


##### `_hold_connect_message`  (lines 3786–3816)

```
async def _hold_connect_message(store: ScopedStore, request: ConnectRequest | None, posted: dict[str, object], channel: str, ts: str | None) -> None
```

**Purpose**: Remembers where a Slack message with a connect button was posted. This lets a later successful connection rewrite the button into an account-confirmation line.

**Data flow**: It receives store, optional connect request, posted body, channel, and timestamp → returns if there was no real button or timestamp → stores channel, message timestamp, and thread timestamp.

**Call relations**: Terminal reply posting calls this after Slack accepts a message that may contain a connect button.

*Call graph*: calls 3 internal fn (put, _is_connect_action, connect_message_key); called by 1 (post); 1 external calls (__init__).


##### `_is_connect_action`  (lines 3819–3826)

```
def _is_connect_action(block: Mapping[str, object]) -> bool
```

**Purpose**: Checks whether a Slack block contains the connect button. This avoids rewriting messages that did not actually include the action.

**Data flow**: It receives a block → checks it is an actions block → scans elements for the connect action id → returns true or false.

**Call relations**: Connect-message storage and connection-settlement rewriting use this.

*Call graph*: called by 2 (_hold_connect_message, settle_connect_message).


##### `_rewrite_slack_message`  (lines 3829–3846)

```
async def _rewrite_slack_message(bot_token: str, channel: str, ts: str, text: str, blocks: list[dict[str, object]]) -> None
```

**Purpose**: Updates one Slack message’s text and blocks through chat.update. It is shared by question-answer rewrites and connect-button settlement.

**Data flow**: It receives bot token, channel, message timestamp, text, and blocks → sends chat.update JSON to Slack → raises through Slack response validation on failure.

**Call relations**: Answer-control replacement and connect settlement both call this common update helper.

*Call graph*: calls 1 internal fn (_slack_ok); called by 2 (_replace_controls_with_answers, settle_connect_message); 2 external calls (AsyncClient, dumps).


##### `_held_connect_message`  (lines 3849–3885)

```
async def _held_connect_message(bot_token: str, held: ConnectMessage) -> Mapping[str, object] | None
```

**Purpose**: Reads back the current Slack message that holds a connect button. Reading live blocks avoids overwriting other updates, such as a question form that was already submitted.

**Data flow**: It receives bot token and held message location → calls either conversation history or replies for the exact message → returns the matching message object or nothing.

**Call relations**: Connect settlement calls this before deciding how to rewrite the message.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (settle_connect_message); 1 external calls (AsyncClient).


##### `settle_connect_message`  (lines 3888–3914)

```
async def settle_connect_message(bot_token: str, held: ConnectMessage, provider: str, account: str) -> None
```

**Purpose**: Rewrites a connect button into a line showing which account was connected. It leaves the message alone if the button is already gone or the message cannot be found.

**Data flow**: It receives bot token, held message, provider, and account → reads the live Slack message → removes connect action blocks → appends a connected-account context line → updates the Slack message.

**Call relations**: Connection-completion hooks can call this after an external provider authorization succeeds.

*Call graph*: calls 4 internal fn (_context_line, _held_connect_message, _is_connect_action, _rewrite_slack_message).


##### `_context_line`  (lines 3917–3921)

```
def _context_line(text: str) -> dict[str, object]
```

**Purpose**: Creates a small Slack context block containing Markdown text. It is used for status-like lines inside messages, such as submitted answers or connected accounts.

**Data flow**: It receives text → trims it to Slack’s context limit → returns a context block dictionary.

**Call relations**: Answer rewrites and connect settlement use this to add compact explanatory lines.

*Call graph*: called by 2 (_replace_controls_with_answers, settle_connect_message).


##### `_reply_text`  (lines 3924–3934)

```
def _reply_text(writeback: Writeback) -> str
```

**Purpose**: Chooses the visible text for a terminal turn outcome. It handles successful, failed, cancelled, and empty replies.

**Data flow**: It receives a Writeback → inspects terminal status and text → returns failure text, cancellation reason/default, reply text, or an empty-reply placeholder.

**Call relations**: Reply-with-oversize-links calls this before adding credential or artifact notes.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `_reply_with_oversize_links`  (lines 3937–3966)

```
def _reply_with_oversize_links(ctx: SurfaceContext, writeback: Writeback) -> str
```

**Purpose**: Builds the final reply text plus extra Slack-only guidance for credential requests and files too large to upload. Oversized artifacts become portal links instead of disappearing.

**Data flow**: It receives context and writeback → starts with terminal reply text → appends credential-setting guidance if needed → appends Markdown links for over-cap artifacts → returns the combined text.

**Call relations**: Terminal reply posting calls this before mention mapping and message splitting.

*Call graph*: calls 3 internal fn (home_url, _oversize_link_line, _reply_text); called by 1 (post).


##### `_oversize_link_line`  (lines 3969–3972)

```
def _oversize_link_line(ctx: SurfaceContext, artifact: SharedArtifact) -> str
```

**Purpose**: Formats one oversized shared artifact as a Markdown list item. It uses a temporary portal link when one is available.

**Data flow**: It receives context and artifact → asks context for an artifact link → formats filename as a link or plain name with byte size → returns the line.

**Call relations**: Reply text building calls this for each artifact too large for Slack upload.

*Call graph*: calls 1 internal fn (artifact_link); called by 1 (_reply_with_oversize_links).


##### `_reply_mention_ids`  (lines 3975–3986)

```
async def _reply_mention_ids(ctx: SurfaceContext, bot_token: str, channel: str, text: str) -> dict[str, str]
```

**Purpose**: Builds the safe outbound mention map for a reply only when the text might contain @names. It avoids unnecessary Slack roster reads.

**Data flow**: It receives context, token, channel, and text → returns empty if no @ appears or identity is missing → otherwise resolves mention ids from SlackNames.

**Call relations**: Reply mention mapping calls this before replacing names with Slack mention markup.

*Call graph*: calls 1 internal fn (_identity); called by 1 (_reply_mentions_mapped); 1 external calls (__init__).


##### `_channel_info`  (lines 3989–4003)

```
async def _channel_info(bot_token: str, channel: str) -> Mapping[str, object] | None
```

**Purpose**: Fetches Slack metadata for a channel. It is best-effort and returns nothing on failure.

**Data flow**: It receives bot token and channel id → calls conversations.info → returns the channel object or nothing.

**Call relations**: Channel-origin decisions, external-sharing checks, and channel-name resolution use this.

*Call graph*: calls 1 internal fn (_slack_ok); called by 3 (_name, _channel_is_externally_shared, _channel_origin); 1 external calls (AsyncClient).


##### `_channel_is_externally_shared`  (lines 4006–4019)

```
async def _channel_is_externally_shared(bot_token: str, channel: str) -> bool
```

**Purpose**: Checks whether a Slack channel crosses workspace boundaries. If the channel cannot be read, it treats it as shared for safety.

**Data flow**: It receives bot token and channel id → fetches channel info → returns true if missing or any sharing flag is set, otherwise false.

**Call relations**: The footer helper uses this to suppress operator-only details in shared channels.

*Call graph*: calls 1 internal fn (_channel_info); called by 1 (_slack_footer).


##### `_slack_footer`  (lines 4022–4054)

```
async def _slack_footer(ctx: FollowerContext, bot_token: str, channel: str, conversation_id: UUID, turn_id: UUID, accounting: str | None) -> str | None
```

**Purpose**: Builds the small footer shown under Slack progress and final reply messages. It may include a web-chat link, accounting, and debug link depending on workspace and channel safety.

**Data flow**: It receives follower context, token, channel, conversation id, turn id, and optional accounting → builds web link if possible → if not operator or channel is shared, returns only safe web link → otherwise adds accounting/debug/web elements and returns trimmed text.

**Call relations**: Terminal reply posting and progress footer generation call this shared footer builder.

*Call graph*: calls 2 internal fn (is_operator_workspace, _channel_is_externally_shared); called by 2 (_footer, post).


##### `_slack_reply_progress_key`  (lines 4076–4081)

```
def _slack_reply_progress_key(turn_id: UUID, reply_id: UUID | None=None) -> str
```

**Purpose**: Builds the store key for delivery progress of a terminal or mid-turn Slack reply. This lets retries resume without posting duplicates.

**Data flow**: It receives a turn id and optional reply id → formats a base key for terminal reply or nested key for a span reply → returns it.

**Call relations**: Terminal reply posting, mid-turn speaking, and reply-record cleanup all use this key format.

*Call graph*: called by 3 (_drop_turn_reply_records, post, speak).


##### `_slack_reply_progress`  (lines 4084–4097)

```
async def _slack_reply_progress(store: ScopedStore, key: str) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Reads or creates the delivery-progress record for one Slack reply. The record tracks delivered parts, pending uncertain posts, completion, and mention mapping.

**Data flow**: It receives store and key → reads an existing record if present → otherwise creates an empty one with compare-and-set → returns the progress object and raw stored value.

**Call relations**: Terminal and mid-turn reply delivery call this before posting any Slack messages.

*Call graph*: calls 2 internal fn (get, put_if); called by 2 (post, speak); 2 external calls (__init__, __init__).


##### `_checkpoint_slack_reply`  (lines 4100–4109)

```
async def _checkpoint_slack_reply(store: ScopedStore, key: str, expected: JsonValue, progress: _SlackReplyProgress) -> tuple[_SlackReplyProgress, JsonValue]
```

**Purpose**: Atomically saves updated Slack reply delivery progress. Atomic means it only writes if the stored value is still the one the caller read.

**Data flow**: It receives store, key, expected stored value, and progress object → serializes progress → compare-and-sets the store row → returns updated progress and encoded value or raises if it changed.

**Call relations**: Reply delivery, mention mapping, terminal posting, and mid-turn posting use this to make retries safe.

*Call graph*: calls 1 internal fn (put_if); called by 4 (_deliver_slack_reply, _reply_mentions_mapped, post, speak); 2 external calls (__init__, model_dump).


##### `_drop_turn_reply_records`  (lines 4112–4122)

```
async def _drop_turn_reply_records(store: ScopedStore, turn_id: UUID) -> None
```

**Purpose**: Deletes temporary delivery records and DM anchors for a turn once delivery is settled. This prevents stale retry bookkeeping from accumulating.

**Data flow**: It receives a store and turn id → lists keys under reply-progress and DM-anchor prefixes → deletes each matching key.

**Call relations**: Attachment delivery and suppressed terminal replies call this after core no longer needs retry records.

*Call graph*: calls 4 internal fn (delete, list, _dm_anchor_key, _slack_reply_progress_key); called by 2 (attach, post).


##### `_slack_reply_delivery`  (lines 4125–4140)

```
def _slack_reply_delivery(message: object, delivery_id: str) -> str | None
```

**Purpose**: Checks whether a Slack message is the one previously attempted with a given delivery id. This supports recovery when Slack accepted a post but the response was lost.

**Data flow**: It receives a raw message and delivery id → inspects Slack metadata and timestamp → returns the timestamp if the metadata id matches, otherwise nothing.

**Call relations**: Reply reconciliation uses this while scanning recent Slack messages.

*Call graph*: called by 1 (_reconcile_slack_reply).


##### `_reconcile_slack_reply`  (lines 4143–4183)

```
async def _reconcile_slack_reply(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, delivery_id: str) -> str | None
```

**Purpose**: Searches recent Slack messages for a pending delivery id. This prevents duplicate posts after an uncertain chat.postMessage attempt.

**Data flow**: It receives client, token, channel, optional thread, and delivery id → scans recent history or replies with metadata included → returns matching timestamp, nothing, or raises if page limits are exceeded.

**Call relations**: Terminal and mid-turn reply posting call this before continuing after a recorded pending delivery.

*Call graph*: calls 2 internal fn (_slack_ok, _slack_reply_delivery); called by 2 (post, speak); 3 external calls (__init__, get, time).


##### `_deliver_slack_reply`  (lines 4186–4226)

```
async def _deliver_slack_reply(client: httpx.AsyncClient, bot_token: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue, delivery_id: str, body: bytes) -> tuple[_Sla
```

**Purpose**: Posts one Slack reply part with exactly-once bookkeeping. It records a pending id before sending and records the Slack timestamp after success.

**Data flow**: It receives client, token, store, progress key, progress, expected value, delivery id, and body → returns early if already delivered → checkpoints pending → posts to Slack → handles invalid_blocks specially → checkpoints delivered timestamp → returns updated progress and Slack payload.

**Call relations**: Terminal and mid-turn reply loops use this for every message part and fallback attempt.

*Call graph*: calls 3 internal fn (_chat_post, _checkpoint_slack_reply, _posted_message_ts); called by 2 (post, speak); 2 external calls (__init__, model_copy).


##### `_reply_mentions_mapped`  (lines 4229–4259)

```
async def _reply_mentions_mapped(ctx: SurfaceContext, bot_token: str, channel: str, text: str, store: ScopedStore, key: str, progress: _SlackReplyProgress, expected: JsonValue) -> tuple[_SlackReplyPro
```

**Purpose**: Replaces safe @names in reply text with Slack mention markup and pins the name-to-id map in the delivery record. Pinning keeps retries deterministic.

**Data flow**: It receives context, token, channel, text, store, key, progress, and expected value → uses existing pinned map or resolves one → checkpoints the map if new → returns updated progress, stored value, and mapped text.

**Call relations**: Terminal and mid-turn reply posting call this before splitting text into Slack-sized parts.

*Call graph*: calls 2 internal fn (_checkpoint_slack_reply, _reply_mention_ids); called by 2 (post, speak); 2 external calls (model_copy, mention_markup).


##### `post`  (lines 4262–4458)

```
async def post(ctx: SurfaceContext, writeback: Writeback) -> str | NothingDelivered
```

**Purpose**: Delivers a turn’s final Slack reply. It handles silence, message splitting, footers, question forms, connect buttons, duplicate-safe retries, invalid-block fallbacks, and delivery checkpointing.

**Data flow**: It receives context and writeback → may suppress true silence and clean records → finds reply thread and bot token → reads progress → reconciles pending delivery → maps mentions → builds actions/footer/text → posts each part with checkpoints and fallbacks → marks complete → returns first Slack message ref or NOTHING_DELIVERED.

**Call relations**: Core’s surface delivery pipeline calls this for terminal writebacks before attachments are uploaded.

*Call graph*: calls 17 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _drop_turn_reply_records, _hold_connect_message, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _reply_with_oversize_links (+7 more)); 7 external calls (__init__, __init__, __init__, AsyncClient, loads, log, is_silence_sentinel).


##### `speak`  (lines 4461–4558)

```
async def speak(ctx: SurfaceContext, reply: MidTurnReply) -> str
```

**Purpose**: Posts a mid-turn Slack reply before the turn ends. It is duplicate-safe and threads under the message it answers when possible.

**Data flow**: It receives context and mid-turn reply → finds channel, token, store, and thread anchor → reads delivery progress → reconciles pending posts → maps mentions → splits text → posts parts with checkpoints and fallback → marks complete → returns first message ref.

**Call relations**: Core calls this for mid-turn replies; final cleanup happens later when the terminal delivery completes.

*Call graph*: calls 12 internal fn (credential, _checkpoint_slack_reply, _deliver_slack_reply, _posted_message_ts, _reconcile_slack_reply, _reply_mentions_mapped, _reply_thread, _slack_reply_progress, _slack_reply_progress_key, _thread_mirror_key (+2 more)); 4 external calls (__init__, __init__, __init__, AsyncClient).


##### `_chat_post`  (lines 4561–4601)

```
async def _chat_post(client: httpx.AsyncClient, bot_token: str, body: bytes) -> Mapping[str, object]
```

**Purpose**: Sends one chat.postMessage request and returns Slack’s parsed JSON without forcing ok:true. This allows callers to handle recoverable Slack errors like invalid_blocks.

**Data flow**: It receives client, token, and encoded body → POSTs to Slack → converts HTTP errors, including rate limits, into SurfaceDeliveryError → returns JSON payload.

**Call relations**: The exactly-once reply delivery helper calls this for each attempted message.

*Call graph*: calls 1 internal fn (__init__); called by 1 (_deliver_slack_reply); 1 external calls (post).


##### `_posted_message_ts`  (lines 4604–4610)

```
def _posted_message_ts(payload: Mapping[str, object]) -> str
```

**Purpose**: Extracts the timestamp of a successfully posted Slack message. It raises if Slack did not report success or omitted the timestamp.

**Data flow**: It receives Slack payload → checks ok:true → reads non-empty ts → returns it or raises SlackApiError.

**Call relations**: Reply delivery, terminal posting, and mid-turn posting use this after chat.postMessage responses.

*Call graph*: called by 3 (_deliver_slack_reply, post, speak); 1 external calls (__init__).


##### `attach`  (lines 4613–4652)

```
async def attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None
```

**Purpose**: Uploads shared artifacts from a completed turn to Slack. It streams files to Slack’s external upload flow and shares successful uploads into the same thread as the reply.

**Data flow**: It receives context, writeback, and reply ref → finds thread → deletes temporary reply records → filters artifacts within Slack’s upload cap → uploads files concurrently → groups accepted file ids into Slack-sized batches → shares each batch to the channel/thread.

**Call relations**: Core calls this after the final reply has been posted and recorded.

*Call graph*: calls 6 internal fn (credential, _attachment_batches, _drop_turn_reply_records, _reply_thread, _share_uploaded_files, _upload_artifact); 4 external calls (__init__, gather, AsyncClient, Timeout).


##### `_attachment_batches`  (lines 4655–4659)

```
def _attachment_batches(files: Sequence[dict[str, str]]) -> Iterator[Sequence[dict[str, str]]]
```

**Purpose**: Splits uploaded file metadata into batches Slack will accept in one share call. This avoids Slack’s undocumented maximum-file error.

**Data flow**: It receives a sequence of file dictionaries → yields slices of at most the Slack attachment maximum.

**Call relations**: Attachment delivery uses this after uploading files and before completing shares.

*Call graph*: called by 1 (attach).


##### `_upload_artifact`  (lines 4662–4689)

```
async def _upload_artifact(ctx: SurfaceContext, client: httpx.AsyncClient, bot_token: str, artifact: SharedArtifact) -> str
```

**Purpose**: Performs the reservation and byte upload steps of Slack’s external file upload flow. It streams artifact bytes from the blob store rather than loading the whole file.

**Data flow**: It receives context, HTTP client, bot token, and artifact → asks Slack for an upload URL and file id → streams blob bytes to the URL → returns the file id.

**Call relations**: Attachment delivery runs this concurrently for each inline-sized shared artifact.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (__init__, post).


##### `_share_uploaded_files`  (lines 4692–4716)

```
async def _share_uploaded_files(client: httpx.AsyncClient, bot_token: str, channel: str, thread_ts: str | None, files: Sequence[dict[str, str]]) -> None
```

**Purpose**: Completes Slack’s external upload flow by sharing uploaded file ids into a channel or thread. This is the step that makes files visible in Slack.

**Data flow**: It receives client, token, channel, optional thread timestamp, and file metadata → sends files.completeUploadExternal → returns nothing if Slack accepts it.

**Call relations**: Attachment delivery calls this for each batch of uploaded files.

*Call graph*: calls 1 internal fn (_slack_ok); called by 1 (attach); 2 external calls (post, dumps).


##### `_slack_ok`  (lines 4719–4728)

```
async def _slack_ok(request: Awaitable[httpx.Response]) -> dict[str, object]
```

**Purpose**: Normalizes Slack API responses that use an ok:true/false field. It raises a SlackApiError when Slack says the request failed or the response is malformed for this use.

**Data flow**: It receives an awaitable HTTP response → awaits it → raises for HTTP errors → parses JSON → checks ok:true → returns the payload or raises with Slack’s error details.

**Call relations**: Most Slack API helpers use this so they do not each repeat Slack response checking.

*Call graph*: called by 18 (_list, _members, _say, _set, _ambient_context, _channel_info, _conversation_members, _declared_files, _held_connect_message, _post_ephemeral (+8 more)); 1 external calls (__init__).


### Terminal Surface Relay
Packages the UFO terminal extension, exposes server events as terminal commands, and uses Redis Streams to connect terminal sessions across pods.

### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import/package discovery`

This is an empty Python package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like putting a label on a folder so the rest of the system knows it can look inside for usable code.

Because this file contains no code, it does not define settings, start services, create objects, or change program behavior directly. Its value is structural: without it, some Python environments or tooling might not recognize `extensions/ufo/ufo_ext_ufo` as a package, which could make imports fail or make package discovery less reliable.

So this file matters mostly during loading and import time. It provides a clean package boundary for the UFO extension while leaving all actual work to other files in the package.


### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling and live response streaming`

The terminal client talks to the server through short HTTP requests, but a conversation with an agent is long-lived and can keep producing output after one request ends. This file bridges that gap. It authenticates the caller, finds or creates the right conversation for a terminal channel, accepts messages, stops, secret values, file uploads, and tool replies, then streams back plain text “directives” such as `say`, `txt`, `ask`, `run`, `poll`, and `listen`. A directive is like a small instruction card for the shell: print this text, ask for input, run this local operation, reconnect later, and so on.

The most important flow is `channel`. It checks the bearer token, decides what kind of request arrived, admits a message when needed, and then creates a `_ChannelStream` to follow the live turn. `stream_directives` watches the turn’s event feed for a limited time. If the answer is still running when the hold time ends, it sends a cursor and tells the client to poll again, so output is not lost or duplicated. If the turn finishes, it prompts or asks the client to listen for future background activity.

The file also supports terminal-side operations, credential prompts, environment bundles, shared files, and workspace file copy. Without it, the terminal client would have no safe way to resume streams, avoid duplicate output, run local tool requests, or keep a conversation tied to a user and workspace.

#### Function details

##### `terminal_runtime_id`  (lines 118–120)

```
def terminal_runtime_id(channel: str) -> str
```

**Purpose**: Creates a stable local runtime name for one terminal channel. This lets repeated connections for the same channel refer to the same terminal-side workspace identity.

**Data flow**: It takes a channel string, hashes it, and keeps the first fixed-length part of the hash. The result is a short deterministic identifier: the same channel always gives the same runtime id, and different channels are very unlikely to collide.

**Call relations**: _ChannelStream._bound uses this when a terminal connects, so the core can tie terminal operations to a repeatable runtime namespace for that channel.

*Call graph*: called by 1 (_bound); 1 external calls (sha256).


##### `directive`  (lines 123–131)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one line of the simple wire format that the shell client reads. It protects tabs, newlines, and backslashes inside fields so a message cannot accidentally break the command format.

**Data flow**: It receives a verb, such as `say` or `poll`, plus text fields. It escapes unsafe characters, joins everything with tabs, adds a newline, and returns bytes ready to send over HTTP.

**Call relations**: Most rendering functions use this as the final packaging step before bytes go to the terminal client, including answer rendering, send acknowledgements, secret replies, update notices, and stream control lines.

*Call graph*: called by 13 (_answer, _channel_message, _channel_op_reply, _channel_stop, _client_update, _fulfill_secret, _say_lines, _send, _stream_end_directives, _subagent_note (+3 more)).


##### `shared_files`  (lines 145–156)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files a turn shared and prepares the terminal-friendly details for each one. It adds download links when the deployment can provide them.

**Data flow**: It asks the surface context for artifacts belonging to a turn, then turns each artifact into a `SharedFile` with filename, size, and a public URL or an empty URL. It returns all files in share order.

**Call relations**: _ChannelStream.response passes this into the live stream renderer, which calls it at the end of a turn so file links are shown after the turn has finished producing files.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 159–166)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies which workspace an incoming request claims to belong to before the main handler runs. If the request does not carry a valid bearer-style authorization header, it refuses by returning no workspace.

**Data flow**: It reads the `Authorization` header, extracts the token after `Bearer`, and asks the bearer-token helper for the workspace claim. The output is a workspace UUID or `None`.

**Call relations**: This is the surface-level identification hook used before route handling. Later request handlers verify the same token again for the member email.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 172–231)

```
def history_directives(conversation: Conversation) -> tuple[bytes, ...]
```

**Purpose**: Turns an existing transcript into terminal lines for a fresh resume. It gives the user enough past context without replaying the current live answer twice.

**Data flow**: It reads the conversation messages, extracts user and assistant text, counts completed tool-like steps, keeps only the newest text within a character budget, and returns `you`, `say`, and `note` directives.

**Call relations**: _ChannelStream.response uses this when a client reconnects without a cursor and needs history before the live tail starts. It relies on _history_text and _dispatched to understand each message.

*Call graph*: calls 3 internal fn (_dispatched, _history_text, directive); called by 1 (response).


##### `_dispatched`  (lines 234–241)

```
def _dispatched(message: Message, active: set[str]) -> int
```

**Purpose**: Counts how many tool calls in an assistant message actually became live work. Calls that were written but never dispatched are ignored because the terminal never narrated them.

**Data flow**: It receives a message and a set of active tool-use ids. If the message is plain text, it returns zero; otherwise it counts matching tool-use blocks.

**Call relations**: history_directives uses this count to summarize past work as a note like “Completed 2 steps” when rebuilding terminal history.

*Call graph*: called by 1 (history_directives).


##### `_history_text`  (lines 244–249)

```
def _history_text(message: Message) -> str
```

**Purpose**: Extracts readable text from a stored message for history replay. For user messages, it also applies the same cleanup used for member message text elsewhere.

**Data flow**: It receives a message. If the content is already a string, it uses it; otherwise it joins the text blocks. For user messages it normalizes the text, then returns the final string.

**Call relations**: history_directives calls this for every transcript message before deciding whether to render it as user text, assistant text, or step summary.

*Call graph*: called by 1 (history_directives); 1 external calls (member_message_text).


##### `directives_for`  (lines 252–306)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True, runtime: RuntimeIde
```

**Purpose**: Converts one live server event into one or more terminal directives. This is the central translator between the agent’s internal event stream and the shell client’s small command language.

**Data flow**: It receives a live frame, state about whether text has already streamed, optional credential prompts, connection links, shared files, exit behavior, and runtime identity. It pattern-matches the frame type and returns the bytes the terminal should read next.

**Call relations**: _render_stream_frame calls this after it has gathered any extra information needed for terminal frames. It delegates terminal endings to _answer and subagent activity to _subagent_note.

*Call graph*: calls 3 internal fn (_answer, _subagent_note, directive); called by 1 (_render_stream_frame); 1 external calls (__init__).


##### `_subagent_note`  (lines 309–315)

```
def _subagent_note(frame: SubagentActivity) -> tuple[bytes, ...]
```

**Purpose**: Renders progress from a subagent as a note under that subagent’s name. It avoids printing start and end events that are already explained elsewhere.

**Data flow**: It takes a subagent activity frame, picks a label from the subagent name or profile, and if there is activity text, returns a `note` directive. If there is no activity text, it returns nothing.

**Call relations**: directives_for calls this when it sees a SubagentActivity frame, keeping the main frame translator simple.

*Call graph*: calls 1 internal fn (directive); called by 1 (directives_for).


##### `_answer`  (lines 318–388)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=(), exits: bool=True, runtime: RuntimeIdentity
```

**Purpose**: Builds the final terminal lines for a turn that has ended. It decides whether to print the answer, show files, ask for secrets, prompt again, or exit the client.

**Data flow**: It receives a terminal frame plus context such as whether text already streamed, pending credential prompts, shared files, connection messages, exit policy, and runtime data. It returns the final directives for done, failed, or cancelled outcomes.

**Call relations**: directives_for uses this for Terminal frames. It calls _say_lines for multi-line messages and directive for all client instructions.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for); 1 external calls (__init__).


##### `_say_lines`  (lines 391–392)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Splits text into separate `say` directives so the terminal can print each line cleanly. It still emits one line for empty text.

**Data flow**: It takes a text string, splits it into lines, wraps each line in a `say` directive, and returns the directive bytes.

**Call relations**: _answer uses this whenever a completed, failed, or cancelled turn needs normal spoken text sent to the terminal.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `_render_stream_frame`  (lines 403–450)

```
async def _render_stream_frame(frame: LiveFrame, streamed: bool, pending: Callable[[str, str], Awaitable[bool]] | None, connect: Callable[[], Awaitable[str]] | None, files: Callable[[], Awaitable[tupl
```

**Purpose**: Prepares all the extra information needed to render one live frame, then records what that rendering means for stream state. It knows when a frame ends a turn and whether the prompt is back.

**Data flow**: It receives one live frame and helper callbacks for credentials, connection URLs, shared files, and runtime data. For terminal frames it checks pending prompts, builds connection messages, reads shared files, then calls directives_for and returns lines plus flags.

**Call relations**: stream_directives calls this for each frame it pulls from the live tail. This function is the layer between raw frame reading and byte-by-byte streaming.

*Call graph*: calls 1 internal fn (directives_for); called by 1 (stream_directives); 1 external calls (__init__).


##### `_stream_end_directives`  (lines 453–476)

```
async def _stream_end_directives(turn_id: UUID, rendered_cursor: str, terminated: bool, ran: bool, prompting: bool, moved_on: Callable[[], Awaitable[bool]] | None) -> tuple[bytes, ...]
```

**Purpose**: Adds the reconnect instruction that belongs at the end of a stream. This is how the terminal knows whether to poll soon, listen while idle, or do nothing.

**Data flow**: It receives the turn id, last rendered cursor, and flags saying whether the stream ended, ran an operation, prompted, or the conversation moved to a newer turn. It returns `since`, `poll`, or `listen` directives as needed.

**Call relations**: stream_directives calls this after leaving the frame-reading loop, so every reconnectable ending carries the correct cursor.

*Call graph*: calls 1 internal fn (directive); called by 1 (stream_directives).


##### `_cancel_stream_tasks`  (lines 479–486)

```
async def _cancel_stream_tasks(*tasks: asyncio.Task[Any] | None) -> None
```

**Purpose**: Cleans up background tasks used while racing live frames against terminal operations. It prevents leftover tasks from continuing after the HTTP stream has ended.

**Data flow**: It receives optional asyncio tasks, cancels each one, then awaits them while ignoring expected cancellation or terminal-gone errors. Nothing is returned; the change is that the tasks are stopped.

**Call relations**: stream_directives calls this in its cleanup path after watching frames and operations.

*Call graph*: called by 1 (stream_directives); 1 external calls (suppress).


##### `stream_directives`  (lines 489–613)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Streams a turn’s live events to the terminal for a bounded amount of time. It is the heart of the long-polling design: hold the request while useful, then tell the client exactly how to resume.

**Data flow**: It receives a live-frame tail, a hold timeout, optional helpers for secrets, files, connection URLs, and terminal operations, plus turn and cursor data. It yields directive bytes as frames arrive, emits `run` when the local terminal must execute an operation, and ends with reconnect instructions when appropriate.

**Call relations**: _ChannelStream.response creates this generator for each held channel response. Internally it uses _next to read frames, _render_stream_frame to translate them, directive for operation instructions, _cancel_stream_tasks for cleanup, and _stream_end_directives for the final cursor and reconnect decision.

*Call graph*: calls 5 internal fn (_cancel_stream_tasks, _next, _render_stream_frame, _stream_end_directives, directive); called by 1 (response); 3 external calls (ensure_future, get_running_loop, wait).


##### `_next`  (lines 616–622)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next item from an async stream without letting the normal end-of-stream exception leak into the task machinery. It turns “no more frames” into `None`.

**Data flow**: It awaits the next `(cursor, frame)` pair from the frame iterator. If the iterator is finished, it returns `None`; otherwise it returns the pair.

**Call relations**: stream_directives wraps frame reads with this helper so it can race frame arrival against operation arrival and timeouts cleanly.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 625–629)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Verifies that a request’s bearer token proves an email address for the current workspace. It is a small authentication check, not a conversation lookup.

**Data flow**: It reads the authorization header, extracts the bearer token, and verifies it against the workspace id. It returns the email if verification succeeds, otherwise `None`.

**Call relations**: _authenticated_member calls this first, then links the proven email to a member row and checks access.

*Call graph*: called by 1 (_authenticated_member); 1 external calls (verify_token).


##### `_authenticated_member`  (lines 632–645)

```
async def _authenticated_member(ctx: SurfaceContext, request: Request) -> tuple[str, UUID | None] | None
```

**Purpose**: Authenticates the request and connects the proven email to a workspace member when possible. It allows an email with no member row to continue as an unlinked conversation, but rejects removed or unauthorized members.

**Data flow**: It verifies the email token, looks up or creates the linked member record, checks whether that member still has access, and returns `(email, member_id)` or `None`.

**Call relations**: All public request handlers call this before doing useful work, including channel traffic, operation body reads, environment uploads, system skills, and workspace file routes.

*Call graph*: calls 4 internal fn (link_member, linked_member, member_has_access, _authenticated_email); called by 8 (channel, op_body, store_environment, store_environment_file, system_skills, workspace_file, workspace_listing, workspace_upload).


##### `_utf8_header`  (lines 648–655)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Recovers a UTF-8 string from an HTTP header whose raw bytes may have been decoded by the server as Latin-1. This matters for paths and errors that can contain non-ASCII characters.

**Data flow**: It reads the named header, strips whitespace, re-encodes it as Latin-1 bytes, then decodes those bytes as UTF-8 with replacement for invalid data. The output is the intended string as best as possible.

**Call relations**: channel uses this for the current working directory header, and _channel_op_reply uses it for terminal operation error text.

*Call graph*: called by 2 (_channel_op_reply, channel).


##### `_stale_client`  (lines 658–662)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Checks whether the shell script version making the request is older than the version the server wants clients to run. This lets the server ask users to reinstall when protocol expectations change.

**Data flow**: It reads the served client version from the environment and compares it with the request’s `x-ufo-script` header. It returns true only when the server has a version set and the client does not match.

**Call relations**: channel uses this once per main channel request, and the request-specific helpers decide whether to refuse, update, or continue.

*Call graph*: called by 1 (channel).


##### `_client_update`  (lines 665–666)

```
def _client_update() -> bytes
```

**Purpose**: Builds the terminal instructions that tell the client to install the updated script and explain why. It is the friendly update notice for stale clients.

**Data flow**: It creates an `install` directive followed by a `say` directive with the stale-client message, then returns the combined bytes.

**Call relations**: _channel_message and _channel_stop use this when a stale client reaches a point where the server wants to push an update through the terminal protocol.

*Call graph*: calls 1 internal fn (directive); called by 2 (_channel_message, _channel_stop).


##### `_resumed_from`  (lines 669–676)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Reads the client’s resume cursor, but only trusts it if it belongs to the same turn being streamed. This prevents skipping early frames of a new turn by mistake.

**Data flow**: It parses the `x-ufo-since` header into a turn id and cursor. If the turn id matches the current turn, it returns the cursor; otherwise it returns an empty cursor.

**Call relations**: _ChannelStream.response uses this before opening the live tail so reconnects continue from the right frame.

*Call graph*: called by 1 (response).


##### `_turn_context`  (lines 679–691)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the context attached to a newly admitted user message. It records who sent it, where it came from, and optionally the user’s timezone.

**Data flow**: It receives the authenticated email and request headers. It creates a TurnContext with sender and source; if a timezone header is present and valid, it includes it, otherwise it logs and drops the bad timezone.

**Call relations**: _channel_message and _send call this just before admitting a message to the core conversation engine.

*Call graph*: called by 2 (_channel_message, _send); 2 external calls (__init__, log).


##### `_runtime_config`  (lines 694–712)

```
def _runtime_config(ctx: SurfaceContext, request: Request) -> TurnRuntimeConfig | None
```

**Purpose**: Reads optional per-turn runtime choices from request headers, such as model, internet access, or environment. It validates them before a turn is admitted.

**Data flow**: It reads model, internet, and environment headers. If none are set, it returns `None`; otherwise it builds a TurnRuntimeConfig, checks that internet is only narrowed to `off`, asks the context to validate it, and returns the config or raises an error.

**Call relations**: _channel_message and _send use this so member-supplied runtime choices are checked before calling the core admit function.

*Call graph*: calls 1 internal fn (validate_runtime_config); called by 2 (_channel_message, _send); 1 external calls (__init__).


##### `_channel_op_reply`  (lines 724–745)

```
async def _channel_op_reply(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, op_id: str, stale: bool) -> Response | _ChannelTurn
```

**Purpose**: Processes the terminal client’s reply to a server-requested local operation. This is how a command run on the member’s machine sends its result back to the running turn.

**Data flow**: It reads the request body as the operation reply, refuses oversized replies, reads any operation error header, and resolves the waiting operation in the surface context. It then either tells a stale client to poll or returns the current turn to resume streaming.

**Call relations**: channel calls this when the request carries the operation header. If it returns a _ChannelTurn, channel wraps it in _ChannelStream so the same turn can continue streaming.

*Call graph*: calls 4 internal fn (latest_turn, terminal_resolve, _utf8_header, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_channel_stop`  (lines 748–759)

```
async def _channel_stop(ctx: SurfaceContext, request: Request, conversation_id: UUID, stale: bool) -> Response | _ChannelTurn
```

**Purpose**: Processes a user stop request, such as pressing Escape in the terminal. It does not admit a new message; it asks the current running turn to stop.

**Data flow**: It rejects any body content, finds the latest turn, and if one exists tells the context to stop it. It returns either a prompt, an update notice for stale clients, or a _ChannelTurn for streaming the cancelled result.

**Call relations**: channel calls this when the stop header is present. A returned _ChannelTurn is streamed back through _ChannelStream.

*Call graph*: calls 4 internal fn (latest_turn, stop_turn, _client_update, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_channel_message`  (lines 762–812)

```
async def _channel_message(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str, stale: bool, marked: bool) -> Response | _ChannelTurn
```

**Purpose**: Handles the normal channel request: either a new user message or an empty reconnect. It decides whether to admit a turn, resume the latest turn, prompt, listen, or ask for a client update.

**Data flow**: It reads and trims the request body. Empty bodies look up the latest turn and handle resume/listen/update cases; non-empty bodies are size-checked, given runtime context, may claim a terminal workspace, and are admitted to the conversation. The result is either an HTTP response or a _ChannelTurn with optional note and send acknowledgement.

**Call relations**: channel uses this for the default path when the request is not a secret, send, unsend, operation reply, or stop. It calls _runtime_config, _turn_context, directive, and core context methods for admission and terminal claiming.

*Call graph*: calls 8 internal fn (admit, claim_terminal, latest_turn, turn_is_terminal, _client_update, _runtime_config, _turn_context, directive); called by 1 (channel); 3 external calls (__init__, PlainTextResponse, body).


##### `_ChannelStream.response`  (lines 825–861)

```
async def response(self) -> Response
```

**Purpose**: Builds the streaming HTTP response for one channel turn. It gathers resume history when needed, wires helper callbacks into the stream renderer, and returns a response the web server can send.

**Data flow**: It starts with request, conversation, member, current directory, listen marker, and turn information. It computes the resume cursor, optionally reads transcript history, creates a stream_directives generator, wraps it with _bound, and returns a StreamingResponse.

**Call relations**: channel creates a _ChannelStream after request-specific handling returns a _ChannelTurn. This method is the bridge from routing logic to the live directive stream.

*Call graph*: calls 4 internal fn (_bound, _resumed_from, history_directives, stream_directives); 2 external calls (partial, StreamingResponse).


##### `_ChannelStream._moved_on`  (lines 863–869)

```
async def _moved_on(self) -> bool
```

**Purpose**: Checks whether the conversation has advanced to a newer non-terminal turn after this stream’s turn. This tells the stream whether it should immediately poll into the next turn instead of idling.

**Data flow**: It asks for the latest turn in the conversation. It returns true only if there is a latest turn, it differs from the stream’s turn, and that newer turn is not terminal.

**Call relations**: _ChannelStream.response passes this as the `moved_on` callback to stream_directives, which uses it when deciding final reconnect instructions.


##### `_ChannelStream._bound`  (lines 871–888)

```
async def _bound(self, history: tuple[bytes, ...], directives: AsyncIterator[bytes]) -> AsyncIterator[bytes]
```

**Purpose**: Wraps the directive generator with terminal connect and disconnect bookkeeping. It also sends the acknowledgement, history, and workspace note before live frames.

**Data flow**: It optionally connects the terminal runtime when a current directory is present, then yields the sent acknowledgement, history lines, note line, and every live directive. In a final cleanup block, it disconnects the terminal if it had connected one.

**Call relations**: _ChannelStream.response uses this as the body iterator for StreamingResponse. It calls terminal_runtime_id when it needs a stable id for the terminal connection.

*Call graph*: calls 1 internal fn (terminal_runtime_id); called by 1 (response).


##### `channel`  (lines 891–948)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Main POST handler for a terminal conversation channel. It authenticates the user, routes the request type, and either returns a direct response or starts a held live stream.

**Data flow**: It verifies the member, handles secret fulfillment first, validates the current working directory, gets or creates the conversation, checks client version, then branches to send, unsend, operation reply, stop, or normal message handling. If the branch returns a turn, it streams that turn back through _ChannelStream.

**Call relations**: This is the central route for `{channel}` posts. It calls the smaller helpers that each handle one kind of terminal request, keeping authentication and conversation selection in one place.

*Call graph*: calls 10 internal fn (conversation_for, _authenticated_member, _channel_message, _channel_op_reply, _channel_stop, _fulfill_secret, _send, _stale_client, _unsend, _utf8_header); 3 external calls (__init__, conversation_audience, PlainTextResponse).


##### `_send`  (lines 951–1011)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str) -> Response
```

**Purpose**: Admits a message without holding open a stream. It is used when the user sends another message while an existing stream is already open to receive the consequences.

**Data flow**: It validates the send id, reads and checks the message body, validates runtime choices, optionally claims the terminal workspace, and admits the message with an idempotency key so retries do not duplicate it. It returns a `sent` acknowledgement and maybe a workspace note.

**Call relations**: channel calls this when the send header is present. The live effects are expected to appear on another held channel stream, not on this response.

*Call graph*: calls 5 internal fn (admit, claim_terminal, _runtime_config, _turn_context, directive); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 1014–1036)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Retracts a queued message that has not yet been taken up by a turn. This supports taking back a pending row before the agent has used it.

**Data flow**: It rejects any request body, requires a real member id, parses the arrival id, and asks the context to retract that arrival for this conversation and member. It returns empty success or a conflict message if the message was already used or gone.

**Call relations**: channel calls this when the unsend header is present. Unlike message admission, it does not create or stream a turn.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 1039–1060)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores one credential value entered privately by the user. The value is not treated as chat text and does not enter the transcript.

**Data flow**: It reads the credential slot header and secret body, rejects empty or oversized values, and asks the context to fulfill the sealed credential request. It returns a `say` directive explaining success or the reason it was not stored.

**Call relations**: channel calls this before normal conversation handling when the secret header is present. It uses directive so the terminal can show the result in its usual format.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 1063–1075)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the raw body for a pending terminal operation. This is the download side used when the terminal needs bytes for a requested local action.

**Data flow**: It authenticates the member, builds the member-scoped conversation key, and asks the context for the operation body by operation id. It returns the bytes as an octet-stream or a 404-style text response if not found.

**Call relations**: This is the GET handler for `{channel}/op/{op_id}`. It shares authentication with the main channel route but does not admit messages or create conversation state.

*Call graph*: calls 2 internal fn (terminal_op_body, _authenticated_member); 2 external calls (PlainTextResponse, Response).


##### `system_skills`  (lines 1078–1090)

```
async def system_skills(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Returns the server’s bundled system skills archive to an authenticated terminal client. It uses caching headers so unchanged bundles do not need to be downloaded again.

**Data flow**: It authenticates the request, reads the bundle digest and archive from the context, compares the client’s `if-none-match` header with the digest ETag, and returns either 304 Not Modified or the zip archive.

**Call relations**: This is the GET handler for `{channel}/skills`. It only serves data after _authenticated_member succeeds.

*Call graph*: calls 1 internal fn (_authenticated_member); 2 external calls (PlainTextResponse, Response).


##### `store_environment`  (lines 1093–1103)

```
async def store_environment(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores an environment document and returns its digest, which later turns can reference. A digest is a content-based identifier: the same content gets the same name.

**Data flow**: It authenticates the request, reads the full body, asks the context to store it as an environment document, and returns the digest as plain text. Invalid documents become a 400 response with the validation message.

**Call relations**: This is the POST handler for `environment/document`. It is separate from channel streaming but uses the same member authentication.

*Call graph*: calls 2 internal fn (store_environment_document, _authenticated_member); 2 external calls (PlainTextResponse, body).


##### `store_environment_file`  (lines 1106–1115)

```
async def store_environment_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Stores one file used by an environment document and returns its digest. This lets environment descriptions refer to exact file contents.

**Data flow**: It authenticates the request, reads the file bytes, asks the context to store them, and returns the resulting digest. If storage rejects the content, it returns a 400 response.

**Call relations**: This is the POST handler for `environment/file`. It pairs with store_environment for uploading environment definitions and their file dependencies.

*Call graph*: calls 2 internal fn (store_environment_file, _authenticated_member); 2 external calls (PlainTextResponse, body).


##### `workspace_file`  (lines 1118–1138)

```
async def workspace_file(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Downloads a file from the workspace tied to a terminal channel. This is part of the `ufo cp` file-copy behavior.

**Data flow**: It authenticates the member, finds the conversation for that member and channel, asks the context to read the requested path, and streams the file bytes if found. Missing conversations, missing sandboxes, invalid paths, and absent files all return “no such file”.

**Call relations**: This is the GET handler for `{channel}/file/{path}`. It reads existing workspace state but does not create a conversation.

*Call graph*: calls 3 internal fn (find_conversation, read_workspace_file, _authenticated_member); 2 external calls (PlainTextResponse, StreamingResponse).


##### `workspace_upload`  (lines 1141–1159)

```
async def workspace_upload(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Uploads a file into the workspace tied to a terminal channel. It can create the conversation first so files can be staged before the user sends the first message.

**Data flow**: It authenticates the member, validates the path, gets or creates the conversation, and streams the request body into the workspace writer. Success returns 204 No Content; a refused oversized or invalid write returns an error response.

**Call relations**: This is the PUT handler for `{channel}/file/{path}`. It uses conversation_audience when creating the conversation so access rules match the member.

*Call graph*: calls 3 internal fn (conversation_for, write_workspace_file, _authenticated_member); 4 external calls (conversation_audience, PlainTextResponse, stream, Response).


##### `workspace_listing`  (lines 1162–1187)

```
async def workspace_listing(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Lists files in a channel’s workspace with basic metadata. The terminal uses this to compare local and remote folders for copy or sync behavior.

**Data flow**: It authenticates the member, finds the conversation for the member and channel, lists files if the conversation exists, and returns JSON containing each path, size, and modification time. A channel with no conversation returns an empty list.

**Call relations**: This is the GET handler for `{channel}/files`. It is a read-only companion to workspace_file and workspace_upload.

*Call graph*: calls 3 internal fn (find_conversation, list_workspace_files, _authenticated_member); 3 external calls (dumps, PlainTextResponse, Response).


### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `cross-cutting terminal connection, operation dispatch, reply handling, and cleanup`

In a single-process system, a terminal request can talk directly to the open terminal connection. In a fleet of pods, that is no longer safe: the browser or client may be connected to one pod while the workflow that wants to run a command is on another. This file is the shared meeting point between them.

Redis acts like a reliable notice board. A connected terminal repeatedly publishes “I am here” with its current workspace. A workflow that wants to run something waits for that notice, takes a per-conversation lock so only one operation runs at a time, writes an operation into a Redis stream, and then waits for a reply stream. Large input or output bytes go through the blob store instead of Redis.

The file is careful about failure. Keys have time-to-live values so abandoned operations eventually disappear. Reply waits always have deadlines, so a workflow cannot hang forever. A small Lua script in Redis reads and claims pending operations in one indivisible step, which prevents the same terminal command from being delivered twice after reconnects. In everyday terms, this file is the dispatch desk, receipt book, and lost-property cleanup for remote terminal work across many pods.

#### Function details

##### `_text`  (lines 55–58)

```
def _text(value: bytes | str) -> str
```

**Purpose**: Turns a Redis field into normal Python text. Redis values may arrive as bytes or strings, and the rest of this file wants to read them consistently.

**Data flow**: It receives one Redis value. If it is already text, it returns it unchanged; if it is bytes, it decodes those bytes into text.

**Call relations**: It is used anywhere Redis data is interpreted, such as decoding operations, replies, bindings, and metadata. `_pairs` also uses it while rebuilding a Redis stream entry into a dictionary.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 61–66)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: Converts Redis’s flat field list into a normal field map. This is needed because the Lua claim script returns stream fields as alternating field and value items.

**Data flow**: It receives a flat list like field, value, field, value. It checks that the input is really a list, converts each item to text with `_text`, and returns a dictionary from field names to values.

**Call relations**: It is used by `RedisTerminals.next_op` after Redis’s Lua script has claimed an operation. It prepares the raw script result so `_decode_op` can turn it into a `TerminalOp`.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 69–74)

```
def _bind_payload(cwd: str, member_id: UUID | None, runtime_id: str) -> str
```

**Purpose**: Builds the small JSON record that says where a terminal is connected and which member it belongs to. This is the shared shape used both for live connections and pinned in-flight operations.

**Data flow**: It receives a working directory, an optional member ID, and a runtime ID. It writes them into a JSON string, using the member ID’s hex form when one exists.

**Call relations**: The heartbeat uses it to publish the live terminal binding. `_run_op` uses it to keep a copy of the binding alive while an operation is running.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 148–157)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual entries from a Redis `XREAD` response. It also checks the response shape so the code fails clearly if Redis returns something unexpected.

**Data flow**: It receives the batch returned by Redis. Empty batches become an empty list; valid Redis stream responses are unpacked to their entry list; unexpected shapes raise a type error.

**Call relations**: It is used by `_await_reply` while waiting for a terminal’s answer. It shields the reply-reading code from Redis response-format details.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 185–202)

```
def _client(self) -> Redis
```

**Purpose**: Provides the Redis client for the current asyncio event loop. This matters because async Redis clients are tied to the loop that created them.

**Data flow**: It reads the currently running event loop, checks whether this `RedisTerminals` object already has a Redis client for it, and creates one if not. The created client has socket timeouts so blocked reads cannot hang forever.

**Call relations**: Nearly every Redis operation in this class goes through `_client`. It is the common doorway used by sending, receiving, binding lookup, reply delivery, and cleanup.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 204–205)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores the live terminal binding for one conversation. This is where other pods look to see whether a terminal is currently connected.

**Data flow**: It receives a conversation ID and returns a string key containing that ID.

**Call relations**: The heartbeat writes this key, and `_read_binding` reads it when a sender is trying to find the terminal.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 207–208)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores a temporary binding while an operation is in progress. This keeps the terminal discoverable even after the held client stream has handed off the command.

**Data flow**: It receives a conversation ID and returns the Redis key for that conversation’s in-flight binding.

**Call relations**: `_run_op` writes this key before publishing an operation, `_read_binding` falls back to it when the live binding is absent, and `_clear_op` deletes it during cleanup.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 210–211)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name where terminal operations for a conversation are posted. A stream is an ordered Redis log that readers can wait on and replay from.

**Data flow**: It receives a conversation ID and returns the stream key for operations in that conversation.

**Call relations**: `_run_op` appends new operations to this stream, `next_op` reads and claims them, and `_clear_op` removes completed entries.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 213–214)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: Builds the Redis stream name where one operation’s reply is posted. Each operation gets its own reply stream so the sender can wait for exactly its answer.

**Data flow**: It receives an operation ID and returns the reply stream key for that operation.

**Call relations**: `_await_reply` waits on this stream, `_deliver_reply` writes to it, and `_clear_op` deletes it after the operation is done.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 216–217)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis lock key used to make terminal operations run one at a time per conversation. Without it, two sends could race into the same terminal.

**Data flow**: It receives a conversation ID and returns the lock key string for that conversation.

**Call relations**: `send` uses this key to acquire a Redis lock before it posts any operation.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 219–220)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key that marks an operation as already delivered to a terminal connection. This prevents a reconnecting stream from showing the same command twice.

**Data flow**: It receives an operation ID and returns the delivery-marker key for that operation.

**Call relations**: The Redis Lua script in `next_op` creates these marker keys directly using the same prefix. `_clear_op` later removes the marker key by calling this helper.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 222–223)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key for metadata about an in-flight operation. The metadata says which conversation and member the operation belongs to.

**Data flow**: It receives an operation ID and returns the metadata key for that operation.

**Call relations**: `_run_op` writes metadata, `staged` and `_deliver_reply` read it to check whether a request is allowed, and `_clear_op` deletes it.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 225–226)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for an operation’s input body. This is used when the command needs byte data that should not be stored directly in Redis.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s staged input.

**Call relations**: `_run_op` writes the body, `staged` reads it for the terminal side, and `_clear_op` deletes it afterward.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 228–229)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for a large operation reply. Small replies fit in Redis, but larger ones are stored as blobs.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s large reply.

**Call relations**: `_deliver_reply` writes large replies there, `_decode_reply` reads them, and `_clear_op` deletes them.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 231–253)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that this pod is currently holding a terminal connection for a conversation. It starts or shares a heartbeat task that keeps the Redis binding alive.

**Data flow**: It receives the conversation ID, working directory, optional member ID, and optional runtime ID. It updates local hold state under a thread lock, creates a `_Hold` if needed, starts `_heartbeat` for the first local connection, and increments the connection count.

**Call relations**: This is called when a held terminal stream starts on a serving pod. It launches `_heartbeat`, and later `disconnect` decreases the matching local hold count.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 255–264)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Marks one local terminal connection as gone. When the last local connection for a conversation leaves, it stops the heartbeat.

**Data flow**: It receives a conversation ID, looks up the local hold, decreases its connection count, and cancels the heartbeat task if no local connections remain. It does not delete the Redis binding directly.

**Call relations**: It is the counterpart to `connect`. By cancelling heartbeat instead of deleting the key, it avoids erasing a newer binding written by another pod during reconnect.


##### `RedisTerminals._heartbeat`  (lines 266–280)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Keeps a conversation’s live terminal binding fresh in Redis while this pod holds the connection. It refreshes the key before its time-to-live expires.

**Data flow**: It receives the conversation details, turns them into a binding payload, and repeatedly writes that payload to the binding key with an expiry. If Redis has a temporary error, it suppresses it and tries again after sleeping.

**Call relations**: `connect` starts this as a background task. It uses `_bind_payload`, `_bind_key`, and `_client`; `disconnect` cancels it when the last local connection is gone.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 282–295)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the terminal workspace held locally on this pod, if there is one. It is a quick local lookup with no Redis call.

**Data flow**: It receives a conversation ID, checks the local `_holds` map under a lock, and returns a `TerminalWorkspace` built from the hold. If this pod is not holding the connection, it returns `None`.

**Call relations**: This serves callers that need the workspace on the same pod as the held connection. Cross-pod senders use `arrived` and `_read_binding` instead.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 297–310)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal binding to appear in Redis. This covers normal reconnect gaps where the terminal exists but has not republished itself yet.

**Data flow**: It receives a conversation ID and grace period. Until the deadline, it repeatedly calls `_read_binding`; if a workspace appears it returns it, and if time runs out it returns `None`.

**Call relations**: `send` calls this before creating an operation. It is the bridge between “a terminal might be reconnecting” and “we can safely say no terminal is present.”

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 312–330)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Reads the terminal workspace from Redis. It first checks the live binding, then checks the in-flight binding kept alive for a running operation.

**Data flow**: It receives a conversation ID, reads the binding key, falls back to the in-flight key, parses the JSON if found, and returns a `TerminalWorkspace`. If neither key exists, it returns `None`.

**Call relations**: `arrived` uses this while waiting for a terminal. It relies on `_bind_key`, `_inflight_key`, `_client`, and `_text` to translate Redis data into the workspace object.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 332–388)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Asks the remote terminal for one operation and waits for the bytes of its reply. It enforces one operation at a time and turns Redis failures or timeouts into terminal-specific errors.

**Data flow**: It receives the conversation, operation details, timeout, and optional body bytes. It waits for a binding with `arrived`, creates a `TerminalOp`, acquires the conversation lock, runs `_run_op` within a deadline, and returns the reply bytes. If the terminal is absent, stuck, or Redis is unreachable, it raises `TerminalAbsent` or `TerminalGone`.

**Call relations**: This is the main sender-side entry in the class. It calls `_lock_key` to serialize work, then hands the locked operation to `_run_op`; the terminal side later sees that operation through `next_op` and answers through `resolve`.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 6 external calls (__init__, __init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 390–436)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: Performs the actual send after the per-conversation lock has been acquired. It publishes the operation, stages any input body, waits for the reply, and cleans up afterward.

**Data flow**: It receives the conversation ID, `TerminalOp`, optional body, bound workspace, and deadline. It writes operation metadata and an in-flight binding to Redis, stores the body blob if present, appends the operation to the Redis operation stream, waits through `_await_reply`, and finally calls `_clear_op`.

**Call relations**: `send` calls this only after locking. It uses helper key builders, `_op_fields`, blob storage, Redis stream writes, `_await_reply`, and cleanup so the full operation lifecycle is contained.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 438–446)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: Turns a `TerminalOp` into the field dictionary stored in Redis. Redis streams store simple fields rather than Python objects.

**Data flow**: It receives a `TerminalOp` and returns a dictionary containing its ID, kind, timeout, name, argument, and parameters as Redis-friendly values.

**Call relations**: `_run_op` calls this right before appending an operation to the operation stream.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 448–456)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: Turns Redis stream fields back into a `TerminalOp`. This is how the terminal side understands what the sender asked it to do.

**Data flow**: It receives a field map from Redis, reads and converts each field with `_text`, converts the timeout to an integer, and returns a new `TerminalOp`.

**Call relations**: `next_op` calls this after `_pairs` has converted the Lua script result into a field map.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 458–481)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: Waits for one operation’s reply stream until the operation deadline. It guarantees the sender eventually gets a reply or a clear failure.

**Data flow**: It receives an operation ID, total wait budget, and user-facing timeout. It repeatedly performs a bounded Redis stream read, checks for entries, and sends the first real reply to `_decode_reply`. If the deadline passes, it raises `TerminalGone`.

**Call relations**: `_run_op` calls this after publishing an operation. It uses `_reply_stream`, `_stream_entries`, `_client`, and `_decode_reply` to turn the terminal’s response into bytes.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 483–496)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: Interprets the reply fields for an operation. Replies may be failures, small inline byte strings, or pointers to larger blobs.

**Data flow**: It receives an operation ID and reply fields. If the reply says the operation failed, it raises `TerminalOpFailed`; if the reply points to a blob, it reads that blob; otherwise it base64-decodes the inline reply bytes.

**Call relations**: `_await_reply` calls this when Redis reports a reply entry. It uses `_reply_blob` for large replies and `_text` to read Redis fields safely.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 498–529)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next terminal operation that should be shown to the connected client. It also claims the operation so another held stream does not deliver it too.

**Data flow**: It receives a conversation ID and optionally an operation ID to skip. It runs a Redis Lua script that scans old operations, removes expired ones, skips the excluded one, and atomically claims the first available operation. If none is ready, it blocks briefly on the stream and tries again.

**Call relations**: This is the receiver-side counterpart to `send`. It reads from the stream written by `_run_op`, uses `_pairs` and `_decode_op` to return a `TerminalOp`, and raises `TerminalGone` if Redis becomes unreachable.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 531–546)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Fetches the input body staged for an in-flight operation, if the requester is allowed to see it. This lets any pod serve the operation body from the shared blob store.

**Data flow**: It receives the conversation ID, operation ID, and optional member ID. It reads operation metadata, checks it with `_gate_ok`, then reads the body blob. If metadata is missing, the gate fails, the blob is missing, or the read times out, it returns `None`.

**Call relations**: This is used on the terminal-serving side after `next_op` has delivered an operation that needs staged bytes. It relies on `_opmeta_key`, `_body_blob`, `_client`, and `_gate_ok`.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 548–563)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts a terminal reply and schedules its delivery without making the HTTP or stream handler wait on Redis. It returns immediately once delivery has been queued.

**Data flow**: It receives the conversation ID, operation ID, reply bytes, optional failure message, and optional member ID. It creates a background task for `_deliver_reply` on the current event loop and returns `True`.

**Call relations**: This is the reply-side public method. It hands the real Redis work to `_deliver_reply` through `_spawn`, while the sender continues waiting in `_await_reply`.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 565–594)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: Writes an operation reply to Redis after checking that it belongs to the right conversation and member. Large replies are stored in the blob store first.

**Data flow**: It receives the conversation, operation, reply bytes, optional failure text, and optional member. It reads operation metadata, validates it with `_gate_ok`, chooses failure fields, blob fields, or inline base64 fields, appends the reply to the reply stream, and sets the stream expiry.

**Call relations**: `resolve` schedules this in the background. It writes the stream that `_await_reply` is watching, and logs warnings if the operation is gone or the requester does not match.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 596–607)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a staged-body or reply request is allowed for this operation. It protects against sending data to the wrong conversation or wrong member.

**Data flow**: It receives raw metadata, a conversation ID, and optional member ID. It parses the JSON, compares the stored conversation to the requested one, and, when a member is named, requires it to match the stored member.

**Call relations**: `staged` uses this before serving operation input bytes, and `_deliver_reply` uses it before accepting a reply. It uses `_text` and UUID parsing to compare stored values safely.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 609–612)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports that this Redis-backed transport has no local in-flight operation view. In this cross-pod design, the operation may be waiting on another pod.

**Data flow**: It receives a conversation ID but does not look anything up. It always returns `None`.

**Call relations**: This matches the transport interface while making clear that local inspection is not meaningful here. The actual operation state lives in Redis and on the sender’s pod.


##### `RedisTerminals._clear_op`  (lines 614–635)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: Best-effort cleanup for an operation after it finishes or times out. It removes Redis entries, marker keys, reply streams, and blobs so old state does not pile up.

**Data flow**: It receives the conversation ID, operation ID, and optional Redis stream entry ID. It deletes the stream entry if known, deletes related Redis keys, and tries to delete the input and reply blobs with bounded waits. Errors are suppressed because expiry timers are the backup cleanup path.

**Call relations**: `_run_op` calls this in a `finally` block. It uses all the key-building helpers for operation metadata, delivery markers, in-flight binding, reply stream, and blob paths.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 637–644)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: Starts a background task and keeps a reference to it until it finishes. This prevents fire-and-forget reply delivery from being lost silently.

**Data flow**: It receives a coroutine and an event loop. It wraps the coroutine with `_logged`, schedules it as a task, stores the task in `_tasks`, and removes it from the set when done.

**Call relations**: `resolve` uses this to schedule `_deliver_reply`. `_logged` provides the error reporting for the task.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 646–650)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: Runs a background coroutine and logs any exception it raises. This makes failures in asynchronous reply delivery visible.

**Data flow**: It receives a coroutine, awaits it, and if any exception escapes, writes a warning with the error text.

**Call relations**: `_spawn` wraps background delivery work with this before creating the task. It is the last safety net for `_deliver_reply` failures.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).
