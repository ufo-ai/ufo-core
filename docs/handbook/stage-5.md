# HTTP server startup, route mounting, and surface ingress  `stage-5`

This stage is where the running server opens its front doors. During startup it mounts the routes that people and outside services use, then during normal operation it checks each request, works out who is calling, and turns it into safe actions inside the system.

The web portal and panel API are the browser entrance. They serve the app, confirm the signed-in member, accept chat messages, stream replies, and provide panel data such as files, memory, usage, skills, and admin settings. Slack, shell, debugger, and OAuth routes are other entrances: Slack messages become conversation turns, the command-line client gets simple text events, operators can inspect read-only debug data, and OAuth callbacks finish account linking.

The shared bridge in `core/src/ufo/ext/surface.py` is like the reception desk behind all these doors. It helps trusted surfaces identify members, create conversations, submit messages, read portal data, and send replies back out. `core/src/ufo/ingress_serve.py` handles hosted sandbox websites by turning signed links into temporary browser sessions and forwarding web traffic safely. `surfaces/__init__.py` simply makes the surfaces folder importable.

## Sub-stages

- [Web portal and panel API ingress](stage-5.1.md) `stage-5.1` — 5 files
- [Slack, shell, debugger, and OAuth callback ingress](stage-5.2.md) `stage-5.2` — 7 files

## Files in this stage

### Surface ingress
Trusted user-facing surfaces and hosted site ingress are exposed through the core bridge, sandbox proxy doorway, and package namespace.

### `core/src/ufo/ext/surface.py`

`orchestration` · `request handling and background delivery`

A “surface” is any outside face of the product: a chat app, a browser UI, a CLI-like channel, or another extension that speaks for a user. This file defines the privileged doorway those surfaces use. That doorway matters because normal extensions are not allowed to claim who a human is or put words into the durable turn queue as that human. Without this file, Slack could not safely turn a Slack message into an agent turn, the web UI could not stream live turn frames, and finished replies could be lost before they reached the place where the user spoke.

The central piece is `SurfaceContext`. It is a bundle of trusted powers for one workspace and one surface. It can link external identities to members, create or find conversations, admit messages, stop turns, read transcripts and files, list agents and settings for the portal, mint temporary artifact links, and access credential handoffs.

The file also describes registered surfaces with `SurfaceSpec`: routes they expose, how a request finds its workspace, and whether they support durable reply delivery. For durable surfaces, background pollers claim completed replies from the database, post them to the surface, attach shared files, and retry safely when delivery fails. For live surfaces, the surface tails live frames instead. In short, this file is the controlled border crossing between outside user channels and the core turn engine.

#### Function details

##### `mint_marker`  (lines 191–201)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to wrap one member message. The marker makes the wrapper unique so text typed by a user cannot accidentally pretend to be the wrapper.

**Data flow**: It takes no input, asks the secrets library for random bytes written as hex text, and returns that marker string.

**Call relations**: Surfaces use the marker before calling `fence_member_message`, so each inbound member message gets its own private boundary.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 204–217)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the exact text that will be admitted as a member message, with separate sections for surrounding context, the member’s own words, and attachments. This keeps the model from confusing background context with what the member actually said.

**Data flow**: It receives a marker, ambient context text, message body, and attachment text. It wraps the member text and optional attachments in marker-named tags and returns one combined inbound string.

**Call relations**: It pairs with `member_message_text`, which later extracts the member’s actual words from this wrapped inbound text.


##### `inbox_name`  (lines 220–249)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an incoming attachment filename into a safe workspace filename. It prevents path tricks, unsafe characters, overlong names, and duplicate names in the same batch.

**Data flow**: It receives a raw filename and a set of names already used. It keeps only a safe leaf name, shortens it while preserving useful suffixes, adds a number if needed, updates the used set, and returns the safe name.

**Call relations**: Surface upload code calls this shared helper so every surface treats untrusted filenames the same way instead of inventing its own weaker rules.

*Call graph*: 1 external calls (contained_leaf).


##### `member_message_text`  (lines 252–266)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Pulls the member’s own words back out of the larger inbound prompt text. This is used when the system wants to display or title what the member said, not the extra context around it.

**Data flow**: It receives stored inbound text, strips known engine context wrappers, looks for the unique member-message wrapper, and returns either the inner member text or the original text if no wrapper exists.

**Call relations**: It is called by `conversation_name` when naming a new conversation from its opening message.

*Call graph*: called by 1 (conversation_name).


##### `MemberAdmitter.admit`  (lines 303–312)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Adm
```

**Purpose**: Defines the contract for admitting a member-spoken message into the durable turn system. Implementations use it to create or join a turn as a named speaker.

**Data flow**: It accepts a conversation id, message text, optional duplicate-protection key, turn context, speaker member id, and optional prepared tool intent. It returns an `Admitted` result telling which turn the message belongs to and whether a run opened.

**Call relations**: `SurfaceContext.admit` delegates to this protocol so surface code does not need to know the lower-level queue implementation.


##### `TurnTailer.tail`  (lines 324–326)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how a live surface reads a turn’s live frames while the turn runs. It is the read side that matches member admission on the write side.

**Data flow**: It receives a turn id and an optional cursor. It returns an async context that yields live frames with replay cursors until the turn ends.

**Call relations**: `SurfaceContext.tail` exposes this protocol to web, debugger, sample, and UFO surfaces that stream turn progress to a held connection.


##### `TurnStopper.stop`  (lines 336–336)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines how a member-requested stop cancels a running turn. It also reports whether a queued follow-up message started a new turn afterward.

**Data flow**: It receives workspace, conversation, and turn ids. It cancels the matching turn if possible and returns a `Stopped` result.

**Call relations**: `SurfaceContext.stop_turn` delegates to this protocol after a surface has already checked the acting member.


##### `_media_predicate`  (lines 368–384)

```
def _media_predicate(column: sa.ColumnElement[str], media: str) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database filter for artifact media categories like image, data, document, or other. This lets artifact listings filter files in user-friendly groups.

**Data flow**: It receives a database media-type column and a requested category. It returns a SQL condition that matches the category, or raises an error for an unknown category.

**Call relations**: `SurfaceContext.list_artifacts` uses it when the portal asks for only one media group.

*Call graph*: called by 1 (list_artifacts); 3 external calls (and_, not_, or_).


##### `conversation_name`  (lines 488–493)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Chooses the default title for a conversation from the member’s opening words. It avoids naming the conversation after ambient channel context.

**Data flow**: It receives inbound text, extracts the member message text, trims surrounding whitespace, caps the length, and returns the title.

**Call relations**: Conversation-creation paths use this naming rule so member-opened and agent-spawned conversations are named consistently.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 496–513)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Updates a conversation title when a surface has a better name for it. Blank titles are ignored so an existing title is not erased accidentally.

**Data flow**: It receives workspace id, conversation id, and title. It trims and caps the title, writes it to the matching conversation row, and returns nothing.

**Call relations**: `SurfaceContext.retitle_conversation` calls this shared helper for Slack and web flows that rename conversations.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `summarize_conversation_title`  (lines 516–536)

```
async def summarize_conversation_title(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores the result of an automatic title-summary job and records that the job has run. This prevents the same conversation from being summarized again and again.

**Data flow**: It receives workspace id, conversation id, and proposed title. It writes the nonblank title, or keeps the old one if blank, and marks the row as summarized.

**Call relations**: This is used by the conversation titling workflow rather than by surface request handlers directly.

*Call graph*: 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 586–587)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures an agent update timestamp includes timezone information. This keeps API responses consistent for clients.

**Data flow**: It receives a datetime, adds UTC if it has no timezone, and returns the normalized datetime.

**Call relations**: Pydantic calls it while creating `AgentDetail` objects returned by `SurfaceContext.agent_detail`.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 631–632)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures a connection timestamp is timezone-aware. This avoids ambiguous times in portal connection data.

**Data flow**: It receives a datetime, leaves it alone if it already has a timezone, or marks it as UTC if not.

**Call relations**: Pydantic runs it when `SurfaceContext.list_agent_connections` builds connection views.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 652–653)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes connection-pool timestamps to timezone-aware UTC values. This gives the portal stable time data.

**Data flow**: It receives a datetime and returns the same moment with UTC added if needed.

**Call relations**: It runs during creation of `ConnectionPoolView` records in `SurfaceContext.list_connections`.

*Call graph*: 1 external calls (replace).


##### `SubagentDetail.summary`  (lines 712–713)

```
def summary(self) -> SubagentSummary
```

**Purpose**: Creates the shorter listing form of a detailed subagent profile. This is useful when the UI needs a roster instead of full instructions.

**Data flow**: It reads the detail object’s name and model and returns a `SubagentSummary` with just those fields.

**Call relations**: It connects the detailed subagent data stored in `SurfaceContext.subagents` to simpler portal listings.

*Call graph*: 1 external calls (__init__).


##### `_binding_fields`  (lines 744–772)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts the identity fields for a connector-backed source. These fields let portal actions submit the exact source binding they mean to change.

**Data flow**: It receives a backend name and stored config. It tries to parse the config as a connector source, returns binding name and spec fields if valid, or returns all `None` fields if not.

**Call relations**: `SurfaceContext.list_sources` uses it while building each `SourceView`.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 798–799)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Makes a source sync timestamp timezone-aware. This keeps source health displays from mixing naive and aware dates.

**Data flow**: It receives a datetime and returns it unchanged or tagged as UTC.

**Call relations**: Pydantic calls it when `SourceView` objects are created in `SurfaceContext.list_sources`.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 817–820)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation timestamps for API output. It also safely allows `last_turn_at` to be missing.

**Data flow**: It receives a datetime or `None`; `None` stays `None`, and a naive datetime gets UTC attached.

**Call relations**: It runs for conversation summaries created by conversation listing methods.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 833–894)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged they are reading another member’s private transcript. This creates the temporary permission that lets the transcript be read and leaves an audit record.

**Data flow**: It receives workspace, conversation, agent, and reader member ids. It verifies the conversation belongs to that agent, finds the private subject member, writes an access row when disclosure is needed, logs the event, and returns the reader and subject emails.

**Call relations**: Portal prepared-intent flows call this before content routes rely on `SurfaceContext.readable_conversation` to allow the temporary read.

*Call graph*: 9 external calls (__init__, now, insert, select, audience_member, parse_audience, workspace_tx, log, uuid4).


##### `LedgerEntry._aware_utc`  (lines 955–956)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures ledger entry timestamps include UTC timezone information. This makes usage accounting rows easier to compare and display.

**Data flow**: It receives a datetime and returns a timezone-aware datetime.

**Call relations**: It is applied when `SurfaceContext.turn_detail` builds ledger entries.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 1001–1006)

```
def _fulfilled_marker_key(sealed: str, slot: str) -> str
```

**Purpose**: Builds the blob-store key that marks one credential prompt slot as fulfilled. This stops the same prompt from being shown again.

**Data flow**: It receives a sealed request string and slot name, hashes the seal, combines it with the slot, and returns a marker path.

**Call relations**: `SurfaceContext.credential_prompt_pending` checks this marker, and `SurfaceContext.fulfill_credential_request` writes it.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_main_agent`  (lines 1009–1022)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. It is the fallback agent when a surface installation has not chosen a specific one.

**Data flow**: It receives a workspace id, queries the agent table for the main agent, returns its id, or raises an error if none exists.

**Call relations**: `_bind_surface_installation` and `SurfaceContext._surface_agent` call it when they need the default agent.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 1025–1057)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str) -> None
```

**Purpose**: Records which external surface installation belongs to a workspace. For example, it binds a Slack team identity to the workspace.

**Data flow**: It receives workspace id, surface name, and installation id. It inserts or updates the workspace’s binding, keeps or defaults the agent binding, and raises a conflict if another workspace already owns that installation.

**Call relations**: Both `SurfaceContext.bind_installation` and `SurfaceInstallationAccess.bind` use this single writer so installation binding rules stay consistent.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.fleet_blob`  (lines 1106–1109)

```
def fleet_blob(self) -> FleetBlobStore
```

**Purpose**: Returns a deploy-wide blob-store view for shared fleet assets. It uses the same backend as the workspace blob store but without workspace scoping.

**Data flow**: It reads the current workspace blob backend and returns a `FleetBlobStore` around it.

**Call relations**: Surface handlers can use this property when they need deploy-owned shared files rather than workspace files.

*Call graph*: 1 external calls (__init__).


##### `SurfaceContext.conversation_slots`  (lines 1112–1114)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Exposes extension-provided conversation slots that were fixed at startup. These slots let surfaces read structured conversation-side data.

**Data flow**: It returns the tuple of bound conversation slots stored on the context.

**Call relations**: Web surface code reads this list to render conversation slot views.


##### `SurfaceContext.read_conversation_slot`  (lines 1116–1121)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Reads one authorized conversation slot under the conversation’s agent identity. Binding the agent ensures the slot provider sees the right agent-scoped resources.

**Data flow**: It receives a bound slot and slot context, temporarily binds the agent id from the context, calls the provider’s read method, and returns the payload.

**Call relations**: The web surface calls it when serving a conversation slot route.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1123–1128)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Runs the summary operation for one conversation slot under the right agent identity. This lets a slot provide a compact count or summary for a conversation.

**Data flow**: It receives a bound slot and context, binds the agent id, asks the provider to summarize, and returns an integer summary or `None`.

**Call relations**: The web surface calls it while building its conversation slots display.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.deploy_extensions`  (lines 1131–1134)

```
def deploy_extensions(self) -> tuple[DeployExtensionView, ...]
```

**Purpose**: Returns the installed deploy extensions that the administration UI can display. This is startup-fixed status, not per-agent state.

**Data flow**: It returns the stored tuple of `DeployExtensionView` records.

**Call relations**: Portal administration pages read this property through the surface context.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1137–1140)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Reports whether the deploy’s installed extensions allow sandbox public internet at all. The portal uses it as the ceiling for per-agent internet settings.

**Data flow**: It returns the boolean stored on the context.

**Call relations**: Agent settings views combine this with each agent’s own narrower setting.


##### `SurfaceContext.subagents`  (lines 1143–1147)

```
def subagents(self) -> tuple[SubagentDetail, ...]
```

**Purpose**: Returns the deploy’s available subagent profiles. These are fixed profiles agents can spawn for specialized work.

**Data flow**: It returns the tuple of `SubagentDetail` objects stored on the context.

**Call relations**: The web portal lists these beside workspace agents and opens their detail pages.


##### `SurfaceContext.deploy_skills`  (lines 1150–1155)

```
def deploy_skills(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Returns the deploy-provided loadable skill index. This is the shared base set of skills available before member-authored skills are added.

**Data flow**: It asks the skill registry for its index and returns the name-description pairs.

**Call relations**: Portal pages and subagent prompts use this same registry-backed view.


##### `SurfaceContext.models`  (lines 1158–1162)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Lists the model ids this deploy supports. The portal uses this closed list when a member chooses an agent model.

**Data flow**: It returns the stored tuple of model names.

**Call relations**: Agent settings render this property as their model choices.


##### `SurfaceContext.sandbox_sizes`  (lines 1165–1168)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Lists the sandbox sizes this deploy can provision. If the list is empty, the portal can hide size selection.

**Data flow**: It returns the stored tuple of size names.

**Call relations**: Agent settings use it to decide whether to offer sandbox-size configuration.


##### `SurfaceContext.credential`  (lines 1170–1173)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential value for a surface. This is privileged because normal extensions do not read secrets directly in-process.

**Data flow**: It receives a slot name, checks that a credential store exists, fetches the slot for the current workspace, and returns the secret value.

**Call relations**: Slack surface helpers call it for signing secrets, posting tokens, identity checks, and delivery credentials.

*Call graph*: called by 11 (_channel_origin, _ctx_signing_secret, _identity, _post_ephemeral, _run_identity_proof, _to_inbound, attach, ingest, interactive, post (+1 more)).


##### `SurfaceContext.credential_prompt_pending`  (lines 1175–1189)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential prompt still needs a value for a specific slot. This prevents already-answered or invalid prompts from reappearing.

**Data flow**: It receives a sealed request and slot. It opens and validates the seal, checks workspace and slot membership, then returns true only if the fulfillment marker blob is absent.

**Call relations**: The web surface calls it while deciding which credential prompts to show.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 1 (_pending_prompts); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 1191–1201)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential handoff and returns its claims. This is used when a surface callback needs to know which workspace, member, and slot the browser flow was for.

**Data flow**: It receives a sealed string, requires a credential store, verifies the seal, and returns the decoded credential request state.

**Call relations**: Slack OAuth callback code uses it before fulfilling credential-backed setup flows.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 1203–1228)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Stores a credential value only if a sealed request proves the right member asked for the right slot in this workspace. This protects credential writes from forged callbacks.

**Data flow**: It receives a sealed request, slot, value, and member id. It validates workspace, member, and slot, writes the value to the encrypted store, writes a fulfilled marker blob, and returns nothing.

**Call relations**: Slack, UFO, and web surfaces call it when a member completes a credential prompt or OAuth handoff.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 1230–1236)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation id to the current workspace. A surface uses this during its own install or OAuth callback.

**Data flow**: It receives an installation id and passes the current workspace and surface name to the shared binding helper.

**Call relations**: Slack OAuth callback code calls it after verifying the installation.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.public_base_url`  (lines 1239–1242)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deploy’s public base URL, if configured. Surfaces use this when they need to render callback or deep links.

**Data flow**: It returns the stored base URL or `None`.

**Call relations**: Surface handlers read it while constructing provider-facing URLs.


##### `SurfaceContext.home_url`  (lines 1244–1254)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link into the deploy’s browser home surface. This lets a non-browser surface point a member to the web UI when needed.

**Data flow**: It receives an optional URL fragment. If both public base URL and home surface are configured, it returns `/surface/<home>` with the fragment; otherwise it returns `None`.

**Call relations**: Slack uses it when it must reply with links for actions it cannot show inline.

*Call graph*: called by 1 (_reply_with_oversize_links).


##### `SurfaceContext.shared_artifacts`  (lines 1256–1288)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Lists files shared by one turn in share order. Live surfaces use this to render download links, while durable delivery uses similar data for attachments.

**Data flow**: It receives a turn id, queries shared artifact rows for the workspace and turn, converts each row into `SharedArtifact`, and returns the tuple.

**Call relations**: UFO and web surfaces call it when displaying files from a turn.

*Call graph*: called by 2 (shared_files, _events); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 1290–1305)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed download link for a shared file. If public delivery is not configured, it returns no link instead of exposing an unsafe URL.

**Data flow**: It receives a `SharedArtifact`, checks token secret and public base URL, mints an expiring artifact path, and returns the full URL.

**Call relations**: Slack, UFO, and web surfaces call it when they need file links in messages or portal views.

*Call graph*: called by 6 (_oversize_link_line, shared_files, _file_payload, _project_slot_context, _radar_run, workspace_artifacts); 2 external calls (now, mint_artifact_url).


##### `SurfaceContext.artifact_preview_link`  (lines 1307–1340)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed inline preview link for an eligible image or rendered document preview. It refuses previews with wrong type, missing size, or excessive size.

**Data flow**: It receives a shared artifact, chooses either its preview blob or original image blob, validates media type and size, signs a preview grant, and returns a full URL or `None`.

**Call relations**: Web surface file rendering calls it to show safe thumbnails and previews.

*Call graph*: called by 4 (_file_payload, _project_slot_context, _radar_run, workspace_artifacts); 4 external calls (__init__, now, mint_artifact_url, raster_image_media_type).


##### `SurfaceContext.ingress_url`  (lines 1342–1371)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str) -> str | None
```

**Purpose**: Builds a signed browser URL for a sandbox port belonging to a conversation. This lets a user open a site running inside the sandbox without giving the surface the ingress secret.

**Data flow**: It receives conversation id, port, and entry path. It signs short-lived ingress claims, builds the stable per-conversation host label, quotes the path, and returns the URL or `None` if ingress is not configured.

**Call relations**: The sites extension calls it when rendering a hosted site frame.

*Call graph*: called by 1 (frame); 6 external calls (__init__, now, site_label, mint_ingress_token, quote, urlsplit).


##### `SurfaceContext._identity_member`  (lines 1373–1386)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up which workspace member is linked to an external user id on a named surface. It is the common identity lookup used by surface identity helpers.

**Data flow**: It receives a surface name and external id, queries the identity table in the workspace, and returns the member id or `None`.

**Call relations**: `linked_member` uses it for this surface; `adopt_identity` uses it for a peer surface.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 1388–1389)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the member already linked to this surface’s external id. It does not create a link.

**Data flow**: It receives an external id, delegates to `_identity_member` with the current surface, and returns a member id or `None`.

**Call relations**: Sample, sites, Slack, UFO, and web surfaces call it during authentication or message admission.

*Call graph*: calls 1 internal fn (_identity_member); called by 8 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, channel, op_body, _authenticate).


##### `SurfaceContext.is_operator_workspace`  (lines 1391–1398)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace belongs to the fleet operator. This gates operator-only rendering such as internal debugging details.

**Data flow**: It reads the workspace email domain and compares it to the operator domain constant, returning a boolean.

**Call relations**: Surface code can use it before showing operator-only links or footers.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 1400–1423)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to the same member known by another surface. This lets one human keep the same identity across surfaces.

**Data flow**: It receives a peer surface and external id, looks up the peer member, inserts this surface identity if found, logs a race if another writer won, and returns the member id or `None`.

**Call relations**: Sample live-admit flow calls it to bridge identities across surfaces.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 1425–1461)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing workspace member by email. It returns no link if the email is not already a member.

**Data flow**: It receives external id and email, finds the oldest matching member ignoring case, inserts a surface identity, tolerates duplicate races, and returns the member id or `None`.

**Call relations**: `join_member` builds on it, and several surfaces call it during sign-in or channel identity resolution.

*Call graph*: called by 5 (join_member, _surface_ingest, _viewer, channel, _authenticate); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 1463–1480)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links an external id to a member, creating the member first when the verified email belongs to the workspace’s own domain. This lets teammates join on first contact through trusted channels.

**Data flow**: It receives external id and email, tries `link_member`, compares the email domain to the workspace domain, creates a member if allowed, and links again.

**Call relations**: Slack member resolution calls it after Slack has verified the user’s email.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 1482–1492)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the database query for finding a conversation by this surface’s queue key. The queue key is the surface’s own stable thread or channel key.

**Data flow**: It receives a queue key and returns a SQL select for conversation id, member, audience, and label in this workspace and surface.

**Call relations**: `find_conversation`, `conversation_for`, and `terminal_op_body` reuse this exact lookup.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 1494–1500)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for this surface queue key without creating one. This is useful when ambient messages should only join known conversations.

**Data flow**: It receives a queue key, runs `_conversation_lookup`, and returns the conversation id or `None`.

**Call relations**: Slack uses it to decide whether a thread is already participating.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 2 (_participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 1502–1515)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent permanently bound to a conversation. This lets a surface resolve a conversation link before reading or admitting content.

**Data flow**: It receives a conversation id, queries the workspace conversation row, and returns the agent id or `None`.

**Call relations**: The web surface calls it when resolving an opaque chat URL.

*Call graph*: called by 1 (_resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 1517–1520)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation in this workspace through the surface context. It is the instance-level wrapper around the shared title helper.

**Data flow**: It receives a conversation id and title, adds the current workspace id, and calls `retitle_conversation`.

**Call relations**: Slack and web opening/admission flows call it when they learn a better title.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 2 (_admit_inbound, _open_conversation).


##### `SurfaceContext.conversation_for`  (lines 1522–1616)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation for a surface queue key. It also narrows audience information over time and chooses the conversation’s agent when new.

**Data flow**: It receives queue key, audience, optional agent id, optional conversation id, and label. It returns an existing conversation after updating narrower audience or label, or inserts a new conversation bound to an agent and returns its id.

**Call relations**: Sample, Slack, UFO, and web surfaces call it before admitting messages or prepared intents.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 7 (_surface_ingest, _surface_live_admit, _admit_inbound, interactive, channel, submit_intent, _open_conversation); 9 external calls (insert, select, update, audience_member, narrow_audience, parse_audience, workspace_tx, log, uuid4).


##### `SurfaceContext._surface_agent`  (lines 1618–1630)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Finds the agent this surface installation is bound to, falling back to the main agent. New conversations use this when no explicit agent is supplied.

**Data flow**: It queries the surface installation row for the current workspace and surface. It returns the bound agent id or the workspace main agent id.

**Call relations**: `conversation_for` calls it while creating a conversation.

*Call graph*: calls 1 internal fn (_main_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 1632–1663)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Asks the ambient-reply classifier whether an unaddressed message should start a turn. It fails open so uncertain classifier failures do not silently drop a user’s possible request.

**Data flow**: It receives the current ambient message and recent history, runs the classifier with a timeout, logs the decision, and returns true unless the classifier confidently says no reply.

**Call relations**: Slack calls it before admitting unmentioned thread replies.

*Call graph*: called by 1 (_ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext.admit`  (lines 1665–1696)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Admitt
```

**Purpose**: Admits a surface message or prepared intent into the durable turn queue as a member. This is the main privileged write action surfaces use.

**Data flow**: It receives conversation id, body, optional idempotency key, context, speaker member id, and optional intent. It passes them to the injected admitter and returns the `Admitted` result.

**Call relations**: Sample, Slack, UFO, web chat, and panel intent flows call it after they have resolved the conversation and speaker.

*Call graph*: called by 8 (_surface_ingest, _surface_live_admit, _admit_inbound, interactive, _send, channel, submit_intent, chat).


##### `SurfaceContext.connect_url`  (lines 1698–1704)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates a URL for a member to authorize a terminal connect request from a turn. It reports a clear error when connect flow is unavailable.

**Data flow**: It receives turn and member ids, obtains the installed connect flow, authorizes a handoff for the workspace, and returns the URL.

**Call relations**: Slack interactive flows and web event streams call it when a terminal frame asks for external connection.

*Call graph*: called by 2 (interactive, _events); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.admitted_body`  (lines 1706–1731)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Finds the body that was stored for a given idempotency key. Surfaces use this to know which duplicate click or delivery actually won.

**Data flow**: It receives an idempotency key, first checks founding turn rows, then queued inbound message rows, and returns the body or `None`.

**Call relations**: Slack and web flows call it when reconciling answer buttons or repeated submissions.

*Call graph*: called by 3 (_unseen_tail, interactive, chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 1733–1747)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member who owns the conversation containing a turn. Live surfaces use this to prevent one member from tailing another member’s turn.

**Data flow**: It receives a turn id, joins turn to conversation, and returns the conversation member id or `None`.

**Call relations**: Sample and web surfaces call it before streaming turn frames to a member.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 1749–1755)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops a running turn in a conversation the surface has already authorized. It reports whether stopping created a follow-up turn.

**Data flow**: It receives conversation and turn ids, adds the workspace id, delegates to the injected stopper, and returns `Stopped`.

**Call relations**: UFO and web chat handlers call it when a member presses stop.

*Call graph*: called by 2 (channel, chat).


##### `SurfaceContext.retract_arrival`  (lines 1757–1775)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Deletes a queued member message that has not yet been consumed by a turn. This lets a member take back only their own still-waiting message.

**Data flow**: It receives conversation id, arrival id, and member id. It deletes the matching unconsumed inbound row and returns true only if one row was removed.

**Call relations**: The UFO surface calls it for its unsend behavior.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 1777–1794)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has already reached a final status. This helps side-channel status messages avoid posting progress after the final answer.

**Data flow**: It receives a turn id, reads the turn status, and returns true for missing turns or terminal statuses.

**Call relations**: It is available to surface-side reporters that need a database-grounded end check.

*Call graph*: 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 1796–1813)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the most recent turn in a conversation. Surfaces use it to resume a stream or re-render the current open handoffs after reload.

**Data flow**: It receives a conversation id, queries turns ordered by sequence descending, and returns the newest turn id or `None`.

**Call relations**: Slack, UFO, and web surfaces call it when resolving active or recently active conversations.

*Call graph*: called by 4 (_participating_conversation, channel, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.absorbing_turn`  (lines 1815–1850)

```
async def absorbing_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Predicts whether a newly admitted message would fold into an already-running turn. It is a hint, not final authority, because admission repeats the decision under lock.

**Data flow**: It receives a conversation id, finds the oldest nonterminal turn, ignores parked turns, checks spend permission, and returns that turn id only if folding would be allowed.

**Call relations**: Slack calls it before applying gates that matter only when founding a new turn.

*Call graph*: called by 1 (_folds_into_live_turn); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.tail`  (lines 1852–1858)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn. This is how live surfaces read progress without touching the hub directly.

**Data flow**: It receives a turn id and optional cursor, delegates to the injected tailer, and returns an async context manager yielding frames.

**Call relations**: Debugger, sample, UFO, web events, and panel submit flows use it to stream turn progress.

*Call graph*: called by 5 (_events, _surface_frames, channel, submit_intent, _events).


##### `SurfaceContext.spend_rollup`  (lines 1860–1863)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace-wide spending for a selected time window or all time. The portal uses it for usage views.

**Data flow**: It receives an optional window size, opens a workspace transaction, asks `SpendRollup` to read the report, and returns it.

**Call relations**: Sample and web usage endpoints call it.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 1865–1880)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded file into a conversation’s sandbox workspace before the turn runs. This lets the agent find the file with normal file tools.

**Data flow**: It receives conversation id, relative path, and byte chunks. It accumulates chunks up to a maximum size, raises if too large, and writes the bytes through the sandbox carrier.

**Call relations**: Sample, Slack download, and web upload delivery code call it before admitting or running turns.

*Call graph*: called by 3 (_surface_ingest, _download_files, _deliver_uploads).


##### `SurfaceContext.list_agents`  (lines 1882–1910)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists all agents in a workspace, with the main agent first. Surfaces use it when a member can choose or inspect agents.

**Data flow**: It queries agent rows for the workspace, orders them, converts each row to `AgentSummary`, and returns the tuple.

**Call relations**: Web audience and subagent-node code call it while building portal choices.

*Call graph*: called by 2 (web_audience, _subagent_nodes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_extension_agent_ids`  (lines 1912–1927)

```
async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]
```

**Purpose**: Finds agents that have private extension conversations for a member. This helps decide which extension-owned agents the member can see.

**Data flow**: It receives a member id, queries distinct agent ids from matching private extension conversations, and returns a frozen set.

**Call relations**: The web audience layer calls it while calculating member-visible agents.

*Call graph*: called by 1 (web_audience); 3 external calls (select, conversation_audience, workspace_tx).


##### `SurfaceContext.agent_detail`  (lines 1929–1981)

```
async def agent_detail(self, agent_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads one agent’s full settings for the portal. It includes prompt digest, bound surfaces, and any pending setup needs.

**Data flow**: It receives an agent id, queries the agent row and its surface installations, computes the prompt digest, looks up pending setup, and returns `AgentDetail` or `None`.

**Call relations**: Web agent settings and intent submission panels call it before showing or changing agent configuration.

*Call graph*: called by 2 (agent_settings, submit_intent); 5 external calls (__init__, select, pending_setup, workspace_tx, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 1983–1995)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for a registered object kind. This tells the UI which fields can be filtered or sorted and what schema to render.

**Data flow**: It receives a kind name, looks it up in the object registry, and returns a `PortalKind` or `None`.

**Call relations**: The web object gate calls it before serving object pages.

*Call graph*: called by 1 (_object_gate); 1 external calls (__init__).


##### `SurfaceContext.agent_skills`  (lines 1997–2015)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists the skills a selected agent can load, combining deploy-provided skills with that agent’s member-authored skills. This mirrors what a turn would see.

**Data flow**: It receives an agent id, binds that agent, merges skill registries, labels each top-level skill as deploy or member origin, and returns portal skill records.

**Call relations**: The web skills endpoint calls it for the agent skills page.

*Call graph*: called by 1 (skills); 2 external calls (__init__, agent).


##### `SurfaceContext.memory_available`  (lines 2018–2022)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory-search provider is installed. Surfaces use this to hide memory UI when the deploy cannot serve it.

**Data flow**: It checks whether the context has a memory provider and returns a boolean.

**Call relations**: Memory search and recent-memory methods require callers to gate on this first.


##### `SurfaceContext.search_memory`  (lines 2024–2034)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches memory items the reader is allowed to see. It uses the same reader shape as agent tools so portal and agent access rules match.

**Data flow**: It receives a source reader and query strings, verifies a memory provider exists, delegates the search, and returns matches.

**Call relations**: The web workspace memory endpoint calls it for query-based memory search.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 2036–2049)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items for readable subjects without a search query. This supports browsing memory by newest items.

**Data flow**: It receives subjects, limit, optional kinds, and cursor. It verifies a provider exists, asks it for a recent page, and returns that page.

**Call relations**: The web workspace memory endpoint calls it for memory browsing.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 2052–2057)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Lists the memory item classes the provider can browse. The portal uses this for filter choices.

**Data flow**: It checks that a memory provider exists and returns its listable kinds.

**Call relations**: Memory UI reads it after `memory_available` confirms the feature exists.


##### `SurfaceContext.agent_spend`  (lines 2059–2064)

```
async def agent_spend(self, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Reads usage and caps for one agent over a time window. This powers agent-level billing views.

**Data flow**: It receives an agent id and optional window, opens a transaction, asks `SpendRollup` for the agent report, and returns it.

**Call relations**: The web usage endpoint calls it.

*Call graph*: called by 1 (usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.member_spend`  (lines 2066–2071)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads usage and caps for one member over a time window. This powers member-level usage views.

**Data flow**: It receives a member id and optional window, opens a transaction, reads the member spend report, and returns it.

**Call relations**: The web workspace usage endpoint calls it.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 2073–2125)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to a specific agent that the viewer may see. It enforces member versus admin visibility in the query.

**Data flow**: It receives agent id, member id, and admin flag. It queries connector grants and connections, filters private rows for non-admins, and returns `ConnectionView` records.

**Call relations**: The web connections endpoint calls it for an agent’s connection panel.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.list_connections`  (lines 2127–2196)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists the workspace’s connector accounts as a pool, including which agents each is attached to. Visibility differs for admins and normal members.

**Data flow**: It receives member id and admin flag, queries connections and optional grants, groups rows by provider account, attaches agent summaries, and returns connection pool views.

**Call relations**: The web connection pool endpoint calls it.

*Call graph*: called by 1 (connection_pool); 6 external calls (__init__, __init__, or_, select, workspace_tx, account_object_name).


##### `SurfaceContext.github_coverage`  (lines 2198–2246)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports which GitHub integration pieces are present: API connection, git push credentials, and sources. This helps the portal show setup coverage.

**Data flow**: It receives member id and admin flag, checks visible GitHub connections and sources plus stored GitHub credential slots, and returns booleans.

**Call relations**: The web GitHub coverage endpoint calls it.

*Call graph*: called by 1 (github_coverage); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.list_artifacts`  (lines 2248–2360)

```
async def list_artifacts(self, member_id: UUID, *, admin: bool, limit: int, cursor: 'ListingCursor | None'=None, q: str | None=None, media: str | None=None, scope: str | None=None) -> 'ListingPage[Lis
```

**Purpose**: Returns a paged list of files shared by turns, respecting what the viewer can read. It supports search, media filtering, and created/shared scopes.

**Data flow**: It receives viewer, admin flag, limit, cursor, query text, media filter, and scope. It builds a filtered artifact query, pages it, adds conversation source links, and returns a listing page.

**Call relations**: The web workspace artifacts endpoint calls it; it uses `_media_predicate` and `_conversation_sources`.

*Call graph*: calls 2 internal fn (_conversation_sources, _media_predicate); called by 1 (workspace_artifacts); 6 external calls (or_, select, readable_audiences, workspace_tx, page_of, page_query).


##### `SurfaceContext.list_conversation_artifacts`  (lines 2362–2428)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists recent files shared in one conversation. Authorization is expected to be checked before calling.

**Data flow**: It receives a conversation id and limit, queries matching shared artifact rows newest first, reads the conversation source, and returns `ListedArtifact` records.

**Call relations**: Web conversation message and slot-context projection code call it.

*Call graph*: calls 1 internal fn (_conversation_sources); called by 2 (_conversation_messages, _project_slot_context); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.list_scheduled_runs`  (lines 2430–2514)

```
async def list_scheduled_runs(self, member_id: UUID, *, limit: int, cursor: 'ListingCursor | None'=None, agent_id: UUID | None=None) -> 'ListingPage[ScheduledRun]'
```

**Purpose**: Lists scheduled turns that finished and produced reportable output for the viewer. This powers a feed of automatic runs.

**Data flow**: It receives member id, limit, optional cursor, and optional agent id. It pages terminal scheduled turns the member can read, gathers shared files, adds source links, and returns scheduled run records.

**Call relations**: The web workspace radar endpoint calls it; it relies on `_scheduled_runs` for the shared query.

*Call graph*: calls 2 internal fn (_conversation_sources, _scheduled_runs); called by 1 (workspace_radar); 5 external calls (__init__, select, workspace_tx, page_of, page_query).


##### `SurfaceContext.count_scheduled_runs_since`  (lines 2516–2533)

```
async def count_scheduled_runs_since(self, member_id: UUID, since: datetime, *, agent_id: UUID | None=None) -> int
```

**Purpose**: Counts scheduled-run feed rows since a given time under the same visibility rules as the feed. This lets a UI size a newest page without reading all content.

**Data flow**: It receives member id, timestamp, and optional agent id, turns `_scheduled_runs` into a count query, applies the time bound, and returns the count.

**Call relations**: The web workspace radar endpoint calls it alongside `list_scheduled_runs`.

*Call graph*: calls 1 internal fn (_scheduled_runs); called by 1 (workspace_radar); 1 external calls (workspace_tx).


##### `SurfaceContext._scheduled_runs`  (lines 2535–2569)

```
def _scheduled_runs(self, member_id: UUID, agent_id: UUID | None) -> sa.Select[Any]
```

**Purpose**: Builds the base database query for readable scheduled-run rows. It centralizes the feed’s visibility and reportability rules.

**Data flow**: It receives member id and optional agent id, creates a SQL select for scheduled terminal turns with artifacts or failures, filters by readable audiences, and returns the query.

**Call relations**: `list_scheduled_runs` pages this query, and `count_scheduled_runs_since` counts it.

*Call graph*: called by 2 (count_scheduled_runs_since, list_scheduled_runs); 3 external calls (or_, select, readable_audiences).


##### `SurfaceContext.list_member_objects`  (lines 2571–2597)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Lists one registered object kind for a signed-in member. It lets each object store enforce its own visibility rules.

**Data flow**: It receives kind, agent, member, admin flag, and list query. It finds the bound kind, verifies it supports member listing, binds the agent, stamps supported fields onto the query, and returns a page or `None`.

**Call relations**: Web homepage, object index, and radar task-name code call it.

*Call graph*: called by 3 (_radar_task_names, homepage, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 2599–2614)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one registered object for a signed-in member. Hidden and missing objects both return `None`.

**Data flow**: It receives kind, object name, agent, member, and admin flag. It finds a readable bound kind, binds the agent, delegates to the store, and returns the object or `None`.

**Call relations**: The web object detail endpoint calls it.

*Call graph*: called by 1 (object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 2616–2638)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants or rows tied to one conversation for a member. This supports conversation-context portal displays.

**Data flow**: It receives kind, agent, conversation, member, admin flag, and limit. It checks that the kind supports conversation member listing, binds the agent, delegates, and returns rows or `None`.

**Call relations**: The web slot-context projection calls it.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 2640–2671)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists member-fillable credential slots and whether each is filled, without revealing values. This powers the workspace credentials panel.

**Data flow**: It reads filled credential slots from the database, maps declared slots to object names, filters to member-fillable declarations, and returns `CredentialSlotView` rows.

**Call relations**: Web credential pages and submit-intent panels call it.

*Call graph*: called by 2 (submit_intent, workspace_credentials); 4 external calls (__init__, select, named_slots, workspace_tx).


##### `SurfaceContext.workspace_domain`  (lines 2673–2679)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Returns the workspace’s own email domain. This is used for trusted join checks and operator detection.

**Data flow**: It opens a workspace transaction, asks the seats helper for the domain, and returns the domain or `None`.

**Call relations**: `join_member` and `is_operator_workspace` call it.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.list_members`  (lines 2681–2689)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Returns the workspace roster ordered by email. This gives the portal stable team rows.

**Data flow**: It snapshots seats for the workspace, sorts member entries by email, and returns them.

**Call relations**: The web workspace team endpoint calls it.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 2691–2736)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists active source bindings visible to a member. Admins see all; normal members see their own and shared sources.

**Data flow**: It receives member id and admin flag, queries non-removed sources with owner emails, filters by visibility, adds connector binding fields, and returns `SourceView` rows.

**Call relations**: The web workspace sources endpoint calls it, and it uses `_binding_fields` for connector-backed rows.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.spend_caps`  (lines 2738–2786)

```
async def spend_caps(self) -> tuple[SpendCapView, ...]
```

**Purpose**: Lists all spend caps in the workspace with readable subject names. This supports the billing administration view.

**Data flow**: It queries spend caps with optional joined agent or member names, orders them, and returns `SpendCapView` rows.

**Call relations**: The web admin index calls it.

*Call graph*: called by 1 (admin_index); 4 external calls (__init__, and_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 2788–2804)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations in the workspace and the agent each routes to. This supports surface and agent administration pages.

**Data flow**: It queries surface installation rows for the workspace, orders by surface name, and returns `InstallationSummary` records.

**Call relations**: The web admin index and workspace surfaces endpoint call it.

*Call graph*: called by 2 (admin_index, workspace_surfaces); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 2806–2855)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists recent workspace conversations across all surfaces for debugging. It includes turn counts and latest activity.

**Data flow**: It builds an activity summary from turns, joins conversations and members, limits by recent activity, and returns `ConversationSummary` rows.

**Call relations**: The debugger surface calls it for its conversation list.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 2857–2976)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Lists conversations for one agent as the portal sees them, with privacy-aware content fields. It separates metadata an admin may see from content that requires readable access.

**Data flow**: It receives filters such as surface, conversation id, participation, and search. It queries bounded conversations, applies visibility, then fetches sources and speakers only for readable rows before returning `ListedConversation` records.

**Call relations**: Many web chat and conversation routes call it; it uses helper predicates and conversation-source/speaker readers.

*Call graph*: calls 5 internal fn (_conversation_sources, _conversation_speakers, _matches, _others, _participated); called by 5 (_member_chat, _named, _resolve_chat, chats_index, conversations); 8 external calls (__init__, __init__, select, audience_member, conversation_audience, parse_audience, readable_audiences, workspace_tx).


##### `SurfaceContext._spoken`  (lines 2978–2999)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a database condition that tests whether a conversation has member speech. It can target one member or any member.

**Data flow**: It receives an optional member id and returns a correlated SQL `exists` condition over turns.

**Call relations**: `_participated` and `_others` use it to define conversation participation filters.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `SurfaceContext._participated`  (lines 3001–3009)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for conversations a member participated in. A conversation counts if it is bound to them or they spoke in it.

**Data flow**: It receives a member id and returns a SQL `or` condition combining conversation ownership and `_spoken(member_id)`.

**Call relations**: `list_agent_conversations` uses it when the portal asks for “mine”.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list_agent_conversations); 1 external calls (or_).


##### `SurfaceContext._others`  (lines 3011–3023)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a condition for readable conversations where other members spoke and this member did not participate. This supports “others” conversation rails.

**Data flow**: It receives a member id and returns a SQL condition requiring a different owner, no speech by this member, and some member speech.

**Call relations**: `list_agent_conversations` uses it when the portal asks for “others”.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list_agent_conversations); 2 external calls (and_, not_).


##### `SurfaceContext._matches`  (lines 3025–3054)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a search condition for conversation listings. It protects private content by searching titles and speakers only where the member can read them.

**Data flow**: It receives search text and member id, creates conditions over surface label, owner email, readable title, and readable speaker emails, and returns one SQL predicate.

**Call relations**: `list_agent_conversations` uses it before applying the listing limit.

*Call graph*: called by 1 (list_agent_conversations); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `SurfaceContext._conversation_sources`  (lines 3056–3091)

```
async def _conversation_sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Reads each listed conversation’s opening source reference. This is often a link back to where the conversation began, such as a Slack thread.

**Data flow**: It receives conversation ids, finds the earliest turn in each, parses its context, and returns a mapping from conversation id to source string or `None`.

**Call relations**: Conversation, artifact, conversation-artifact, and scheduled-run listings call it to add source links.

*Call graph*: called by 4 (list_agent_conversations, list_artifacts, list_conversation_artifacts, list_scheduled_runs); 4 external calls (model_validate, and_, select, workspace_tx).


##### `SurfaceContext._conversation_speakers`  (lines 3093–3149)

```
async def _conversation_speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Reads the first speakers in each listed conversation. It returns display sender text where the surface reported it.

**Data flow**: It receives conversation ids, finds each member’s first turn per conversation, limits speaker count, parses context, and returns a mapping to `ConversationSpeaker` tuples.

**Call relations**: `list_agent_conversations` calls it only for conversations whose content the viewer may read.

*Call graph*: called by 1 (list_agent_conversations); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `SurfaceContext.readable_conversation`  (lines 3151–3192)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Decides whether a member may read a conversation’s content. It allows own/shared conversations and temporary admin disclosures, but fails closed for inaccessible rooms or wrong agents.

**Data flow**: It receives conversation, agent, member, and admin flag. It reads the conversation audience, checks direct readable audiences, then checks recent transcript disclosure rows for qualifying admins.

**Call relations**: The web readable-conversation gate calls it before serving transcript, turns, files, or related content.

*Call graph*: called by 1 (_readable_conversation); 6 external calls (now, select, audience_member, parse_audience, readable_audiences, workspace_tx).


##### `SurfaceContext.conversation_audience`  (lines 3194–3204)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience bound to one conversation. This tells callers who the conversation is meant for.

**Data flow**: It receives conversation and agent ids, queries the matching row, parses the stored audience string, and returns an audience object or `None`.

**Call relations**: The web slot-context builder calls it while authorizing slot reads.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, parse_audience, workspace_tx).


##### `SurfaceContext.conversation_subagent_turns`  (lines 3206–3243)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists all subagent turns spawned below a conversation’s turns, including nested descendants. This lets a view show the tree of delegated work.

**Data flow**: It receives a conversation id and limit, builds a recursive query over parent turn ids, reads matching turn rows breadth-first, converts them to `Turn` records, and returns them.

**Call relations**: Web conversation messages, event streams, and slot-target code call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_conversation_messages, _events, _slot_target); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 3245–3261)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Lists a conversation’s turns in admission order, limited to the most recent set. This is the main durable turn history read.

**Data flow**: It receives a conversation id and limit, queries newest turns by sequence, reverses them into oldest-first order, converts rows to `Turn`, and returns them.

**Call relations**: Debugger and web conversation-message routes call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _conversation_messages); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 3263–3298)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Finds message references whose inbound text came from machine-origin events rather than member prose. This helps transcript views avoid rendering internal envelopes as user bubbles.

**Data flow**: It receives a conversation id, unions matching turn ids and inbound-message ids for scheduled fires and subagent results, and returns them as strings.

**Call relations**: The web conversation-message projection calls it while deciding how to display messages.

*Call graph*: called by 1 (_conversation_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 3300–3350)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn with its accounting rows and direct subagent children. This is the detailed inspection view for a turn.

**Data flow**: It receives a turn id, reads the turn row, child turn rows, and ledger rows, converts them to model objects, and returns `TurnDetail` or `None`.

**Call relations**: Debugger and web event/message/turn resolution routes call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 3352–3393)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Lists admitted messages not yet present in the written transcript. This lets a reloaded chat still show messages that are queued or folded into a running turn.

**Data flow**: It receives a conversation id and optional draining turn id, first checks workspace ownership, then reads unconsumed or currently-draining inbound rows and returns `QueuedArrival` records.

**Call relations**: The web conversation-message projection calls it after reading turns.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 3395–3427)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Reads attribution for member-admitted queued messages. This labels folded messages with who spoke them.

**Data flow**: It receives a conversation id, verifies ownership, reads member-admitted inbound message contexts and speaker ids, and returns `SpokenArrival` records.

**Call relations**: The web conversation-message projection reads these beside turn rows.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 3429–3440)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads the durable transcript blob for a conversation. It first verifies the conversation belongs to this workspace to prevent cross-tenant reads.

**Data flow**: It receives a conversation id, checks ownership, reads the transcript blob if present, decodes it, and returns a `Conversation` or `None`.

**Call relations**: Debugger, UFO, web messages, slot context, and subagent-node code call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 5 (conversation_transcript, channel, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 3442–3453)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists saved compaction record indices for a conversation. Compaction records explain transcript summarization steps.

**Data flow**: It receives a conversation id, checks ownership, lists matching blob keys, extracts numeric indices, sorts them, and returns the tuple.

**Call relations**: The debugger surface calls it for compaction listings.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (conversation_compactions).


##### `SurfaceContext.read_compaction`  (lines 3455–3461)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one saved compaction record for a conversation. It returns nothing if the conversation is foreign or the record is absent.

**Data flow**: It receives a conversation id and index, checks ownership, reads and parses that compaction record from blob storage, and returns it or `None`.

**Call relations**: The debugger surface calls it for a selected compaction record.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (compaction_record); 1 external calls (read_compaction_record).


##### `SurfaceContext.list_workspace_files`  (lines 3463–3469)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files currently in a conversation’s sandbox workspace. This shows the live file state without exposing foreign conversations.

**Data flow**: It receives a conversation id, checks ownership, asks the sandbox manager for entries, and returns them or an empty tuple.

**Call relations**: The debugger workspace-files endpoint calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_files).


##### `SurfaceContext.conversation_changes`  (lines 3471–3477)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last recorded workspace file changes for a conversation. This helps the portal explain what changed in a sandbox.

**Data flow**: It receives a conversation id, checks ownership, then returns recorded changes or `NOTHING_CHANGED`.

**Call relations**: The web slot-context projection calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 3479–3488)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one workspace file from a conversation sandbox. It refuses foreign conversations and missing files by returning `None`.

**Data flow**: It receives a conversation id and relative path, checks ownership, asks the sandbox manager for a byte stream, and returns that stream or `None`.

**Call relations**: The debugger workspace-file endpoint calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (workspace_file).


##### `SurfaceContext.terminal_connect`  (lines 3490–3494)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Registers that a member’s held surface connection is available as a terminal for a conversation. This lets sandbox terminal operations rendezvous with the client.

**Data flow**: It receives conversation id, current directory, and optional member id, and records the terminal connection in the sandbox terminal manager.

**Call relations**: UFO-style terminal surfaces pair this with `terminal_disconnect` around a connection’s lifetime.


##### `SurfaceContext.terminal_disconnect`  (lines 3496–3497)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the connected terminal for a conversation. It is the cleanup half of terminal connection tracking.

**Data flow**: It receives a conversation id and tells the sandbox terminal manager to disconnect it.

**Call relations**: Terminal-capable surfaces call it when the held connection closes.


##### `SurfaceContext.claim_terminal`  (lines 3499–3505)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Binds a fresh conversation to an already connected terminal if it has not been bound elsewhere. This prevents early sandbox opens from missing the member’s terminal.

**Data flow**: It receives conversation id and current directory, asks the sandbox manager to claim the terminal, and returns whether this call made the claim.

**Call relations**: The UFO surface calls it when sending or opening a channel.

*Call graph*: called by 2 (_send, channel).


##### `SurfaceContext.next_terminal_op`  (lines 3507–3514)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next operation a turn wants the member’s terminal to perform. It can skip an operation the same request just answered.

**Data flow**: It receives conversation id and optional excluded op id, delegates to the terminal manager, and returns the next terminal operation.

**Call relations**: Terminal-capable live surface streams use it while racing terminal operations against turn frames.


##### `SurfaceContext.terminal_resolve`  (lines 3516–3530)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Answers a pending terminal operation with the client’s reply or failure. It is gated by an unguessable op id and the member binding held by the terminal transport.

**Data flow**: It receives conversation id, op id, reply bytes, optional failure text, and member id. It passes them to the terminal manager and returns whether the op was resolved.

**Call relations**: The UFO surface calls it when a terminal client posts an operation result.

*Call graph*: called by 1 (channel).


##### `SurfaceContext.terminal_op_body`  (lines 3532–3544)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads the staged byte body for a pending terminal operation. It avoids creating a conversation while looking up the queue key.

**Data flow**: It receives queue key, op id, and member id, finds the existing conversation for this surface, then asks the terminal manager for the staged bytes.

**Call relations**: The UFO surface `op_body` route calls it for clients downloading operation bodies.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 3546–3559)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface. This helps views build links into the surface where a conversation lives.

**Data flow**: It receives a peer surface name, queries the installation table, and returns the installation id or `None`.

**Call relations**: The debugger workspace metadata route calls it.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 3562–3571)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Yields a raw workspace database transaction for surface extension tables. This is for trusted surface code that has no normal extension context.

**Data flow**: It opens a workspace transaction, yields the connection to the caller, commits on normal exit, and rolls back on error.

**Call relations**: Slack uses it in a flow that needs extension-owned rows while deciding live-turn folding.

*Call graph*: called by 1 (_folds_into_live_turn); 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 3573–3583)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to the current workspace. It is a small but important guard before reading unscoped blobs or sandbox state.

**Data flow**: It receives a conversation id, queries the conversation table for the current workspace, and returns a boolean.

**Call relations**: Transcript, compaction, workspace-file, arrival, and change readers call it before touching external stores.

*Call graph*: called by 8 (arrival_speakers, conversation_changes, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 3585–3604)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the common database select for durable turn rows. This keeps all turn readers projecting the same fields.

**Data flow**: It takes no input and returns a SQL select containing turn identity, status, context, terminal, subagent, and tracing fields.

**Call relations**: `conversation_subagent_turns`, `list_turns`, and `turn_detail` use it before converting rows.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 3606–3625)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts a database turn row into a typed `Turn` object. It parses stored JSON fields into their model types.

**Data flow**: It receives a SQL row, copies scalar fields, validates context and terminal JSON when present, and returns a `Turn` record.

**Call relations**: All main turn-reading methods call it after using `_turn_query`.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.bind`  (lines 3646–3651)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Lets a tool bind an installation only for surfaces its manifest declared. This prevents an extension from claiming arbitrary surface names.

**Data flow**: It receives surface name and installation id, checks the declared set, reads the ambient workspace id, and calls the shared binding helper.

**Call relations**: Tool code uses this manifest-scoped access object when registering installations.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 3663–3673)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Looks up which workspace owns a shared surface installation id before the request is bound to a workspace. This is the pre-routing identity lookup for shared surfaces.

**Data flow**: It receives an installation id, queries the owner-wide installation table for this surface, and returns the workspace id or `None`.

**Call relations**: Slack workspace resolution calls it for incoming shared requests.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 3675–3687)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential handoff before a workspace has been resolved. It returns `None` for missing store, tampered, or expired seals.

**Data flow**: It receives a sealed string, verifies it with the credential store if available, and returns request state or `None`.

**Call relations**: Slack workspace resolution uses it when OAuth state itself names the workspace.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 3689–3705)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential while resolving a shared surface request. It ensures the resolver only reads slots declared for that surface.

**Data flow**: It receives workspace id and slot name, checks the slot declaration and store, verifies the workspace exists under workspace scope, then returns the credential value.

**Call relations**: Slack authentication reads its signing secret through this method.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceDeliveryError.__init__`  (lines 3732–3736)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that may carry a provider-requested retry delay. The poller uses that delay instead of a fixed backoff when present.

**Data flow**: It receives an error message and optional nonnegative retry-after seconds, validates the delay, stores it, and initializes the runtime error.

**Call relations**: Slack posting code raises it when the provider reports retry timing; pollers inspect it in retry handling.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 3787–3811)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for terminal writebacks that are ready to deliver. It also waits for pending mid-turn replies so the final answer comes last.

**Data flow**: It receives the current time and returns a SQL condition covering terminal status, no pending mid-turn replies, and claimable or expired writeback rows.

**Call relations**: `writeback_workspaces.due` and `WritebackPoller._claim` use it to find deliverable terminal replies.

*Call graph*: called by 2 (_claim, due); 3 external calls (and_, exists, or_).


##### `writeback_workspaces`  (lines 3814–3849)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace candidate reader for terminal writebacks. This keeps the poller from scanning every workspace every tick.

**Data flow**: It initializes a cursor, defines a due-workspace query and candidate function, wraps the query with owner-scope candidate reading, and returns the candidate function.

**Call relations**: A `WritebackPoller` receives this function and calls it from `run` or `drain`.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 3821–3835)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one bounded query for workspaces that currently have due terminal writebacks. It advances from the current cursor when one exists.

**Data flow**: It reads the current time, selects workspace ids with due writebacks, groups and orders them, applies cursor and limit, and returns the SQL query.

**Call relations**: The enclosing `writeback_workspaces` passes it to `owner_candidates`.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 3839–3847)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next batch of workspace ids with deliverable writebacks, wrapping around when it reaches the end. This gives fair rotation across workspaces.

**Data flow**: It calls the owner-scoped due reader, resets the cursor if needed, updates the cursor to the last workspace returned, and returns the ids.

**Call relations**: `WritebackPoller.run` and `WritebackPoller.drain` call this candidate function.


##### `_WritebackDeliveryFailed.__init__`  (lines 3857–3860)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a delivery failure with the phase that failed, either posting the reply or attaching files. This lets retry logs and state updates say what went wrong.

**Data flow**: It receives a phase and original exception, stores both, and creates a readable runtime-error message.

**Call relations**: `WritebackPoller._deliver_claimed` raises it around surface `post` and `attach` failures.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 3883–3916)

```
async def run(self) -> None
```

**Purpose**: Runs the continuous background loop that delivers terminal replies for durable surfaces. It keeps several workspace drains in flight and cleans them up on shutdown.

**Data flow**: It repeatedly checks finished tasks, asks for candidate workspaces, starts drain tasks under concurrency limits, logs failures, sleeps between ticks, and cancels remaining work when exiting.

**Call relations**: This is the long-running poller loop; it calls `_drain_workspace` for each selected workspace.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 3918–3927)

```
async def drain(self) -> None
```

**Purpose**: Runs one bounded drain pass for currently due terminal writeback workspaces. It is useful for tests or one-shot maintenance.

**Data flow**: It asks for workspace candidates, drains them concurrently with a semaphore, gathers results, and raises an exception group if any drain failed.

**Call relations**: It shares `_drain_workspace` with the continuous `run` loop.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 3929–3947)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers a batch of terminal writebacks for one workspace. It also starts lease-renewal tasks while external delivery is in progress.

**Data flow**: It receives workspace id and semaphore, binds workspace scope, claims rows, starts renewal tasks, delivers each row, and cancels renewals at the end.

**Call relations**: `run` and `drain` call it; it calls `_claim`, `_renew_claim`, and `_deliver`.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 3949–3982)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due writeback rows for this worker. Claiming prevents another worker from delivering the same reply at the same time.

**Data flow**: It receives workspace id, finds due rows, updates them to claimed with worker id and expiry, and returns turn id, existing reply ref, and last error.

**Call relations**: `_drain_workspace` calls it before starting delivery and renewals.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 3984–4016)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Wraps one claimed terminal writeback delivery with logging and retry handling. It decides whether success, claim loss, or failure should be recorded.

**Data flow**: It receives workspace id, turn id, existing reply ref, and renewal task. It calls `_deliver_with_lease`, handles claim loss or delivery failure, updates retry/failure state when needed, and logs the outcome.

**Call relations**: `_drain_workspace` calls it for every claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 4018–4047)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while the claim lease is alive, then marks the row delivered. It stops the renewal task before the final database update to avoid racing itself.

**Data flow**: It receives workspace id, turn id, reply ref, and renewal task. It races delivery against renewal failure, cancels leftover tasks, then calls `_mark_delivered` on success.

**Call relations**: `_deliver` calls it; it calls `_deliver_claimed` and `_mark_delivered`.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 4049–4071)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Builds the writeback payload and calls the surface’s durable delivery functions. It records the reply reference before attaching files so recovery does not repost the main reply.

**Data flow**: It receives workspace id, turn id, and optional reply ref. It builds the writeback, finds the surface spec, posts if needed, records the reply ref, then calls attach.

**Call relations**: `_deliver_with_lease` calls it; it hands off to surface `post` and `attach` handlers from `SurfaceSpec`.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 4073–4076)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps a claimed writeback lease alive while delivery is still running. This protects long external API calls from being picked up by another worker.

**Data flow**: It receives a turn id, sleeps for the refresh interval in a loop, and calls `_refresh_claim` each time.

**Call relations**: `_drain_workspace` starts it as a background task for each claimed row.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 4078–4094)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends this worker’s claim expiry for one writeback row. If the row is no longer claimed by this worker, it signals claim loss.

**Data flow**: It receives a turn id, updates the claim expiry only when status and worker id still match, and raises claim-lost if no row was updated.

**Call relations**: `_renew_claim` calls it periodically during delivery.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 4096–4148)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the `Writeback` object a durable surface needs to render a final reply. It includes the terminal frame, queue key, agent, conversation, and shared files.

**Data flow**: It receives a turn id, reads the turn, conversation, and artifact rows, parses the terminal frame, creates `SharedArtifact` records, and returns the writeback plus surface name.

**Call relations**: `_deliver_claimed` calls it before invoking the surface delivery handlers.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 4150–4162)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Stores the external reply reference after the main reply is posted. This lets retries attach files without posting the same reply again.

**Data flow**: It receives a turn id and reply reference, updates the claimed writeback row if this worker still owns it, and raises claim-lost if not.

**Call relations**: `_deliver_claimed` calls it immediately after a successful surface `post`.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 4164–4181)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a terminal writeback as delivered and clears its claim. This is the final successful state transition.

**Data flow**: It receives a turn id, updates the claimed row to delivered with no owner or expiry, and raises claim-lost if this worker no longer owns it.

**Call relations**: `_deliver_with_lease` calls it after posting and attachment delivery complete.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 4183–4232)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed terminal writeback for retry, or marks it failed if it is too old. It respects provider retry delays but bounds them.

**Data flow**: It receives a turn id and wrapped delivery error, computes last-error text and retry time, updates the claimed row to pending or failed, clears the claim, and returns outcome details.

**Call relations**: `_deliver` calls it when `_deliver_with_lease` reports a post or attach failure.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `mid_turn_reply_workspaces`  (lines 4235–4266)

```
def mid_turn_reply_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace candidate reader for deliverable mid-turn replies. These are replies produced before the turn’s final answer.

**Data flow**: It initializes a cursor, defines a due-workspace query and candidate function, wraps the query with owner-scoped candidate reading, and returns the candidate function.

**Call relations**: A `MidTurnReplyPoller` receives this function and calls it from `run` or `drain`.

*Call graph*: 1 external calls (owner_candidates).


##### `mid_turn_reply_workspaces.due`  (lines 4241–4252)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one bounded query for workspaces with due mid-turn replies. It advances from the current cursor when set.

**Data flow**: It reads the current time, selects workspace ids with due mid-turn reply rows, groups and orders them, applies cursor and limit, and returns the query.

**Call relations**: The enclosing `mid_turn_reply_workspaces` passes it to `owner_candidates`.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); 2 external calls (now, select).


##### `mid_turn_reply_workspaces.candidates`  (lines 4256–4264)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next batch of workspace ids with deliverable mid-turn replies, wrapping around at the end. This spreads poller attention across workspaces.

**Data flow**: It calls the owner-scoped due reader, resets the cursor if needed, updates it from the last returned id, and returns the workspace ids.

**Call relations**: `MidTurnReplyPoller.drain` calls this candidate function.


##### `_mid_turn_reply_due`  (lines 4269–4282)

```
def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for mid-turn replies that can be claimed. A row is due if pending or if an old claim expired.

**Data flow**: It receives the current time and returns a SQL condition over status and claim expiry.

**Call relations**: `mid_turn_reply_workspaces.due` and `MidTurnReplyPoller._claim` use it.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `MidTurnReplyPoller.run`  (lines 4308–4314)

```
async def run(self) -> None
```

**Purpose**: Runs the continuous background loop for mid-turn reply delivery. It keeps trying even when one drain pass fails.

**Data flow**: It repeatedly calls `drain`, logs any failure, sleeps for the poll interval, and repeats forever.

**Call relations**: This is the long-running poller loop for rows handled by `MidTurnReplyPoller`.

*Call graph*: calls 1 internal fn (drain); 2 external calls (sleep, log).


##### `MidTurnReplyPoller.drain`  (lines 4316–4326)

```
async def drain(self) -> None
```

**Purpose**: Claims and delivers due mid-turn replies for candidate workspaces. It processes each workspace under workspace scope.

**Data flow**: It asks for workspace candidates, binds each workspace, claims rows, logs retries, and delivers each claimed reply.

**Call relations**: `run` calls it; it calls `_claim` and `_deliver`.

*Call graph*: calls 2 internal fn (_claim, _deliver); called by 1 (run); 2 external calls (log, ws).


##### `MidTurnReplyPoller._claim`  (lines 4328–4369)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due mid-turn replies for this worker in delivery order. Claiming reduces duplicate delivery across replicas.

**Data flow**: It receives workspace id, selects due reply ids ordered by creation and span order, updates them to claimed with expiry, returns row data, and sorts the result.

**Call relations**: `drain` calls it before delivering mid-turn replies.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); called by 1 (drain); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `MidTurnReplyPoller._deliver`  (lines 4371–4394)

```
async def _deliver(self, workspace_id: UUID, row: sa.Row) -> None
```

**Purpose**: Delivers one claimed mid-turn reply and records success or retry. It logs both successful deliveries and failures.

**Data flow**: It receives workspace id and a claimed row, calls `_speak`, sends failures to `_fail_or_retry`, or marks successful rows delivered with the returned reply reference.

**Call relations**: `drain` calls it for every claimed row; it uses `_speak`, `_mark_delivered`, and `_fail_or_retry`.

*Call graph*: calls 3 internal fn (_fail_or_retry, _mark_delivered, _speak); called by 1 (drain); 2 external calls (now, log).


##### `MidTurnReplyPoller._speak`  (lines 4396–4437)

```
async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None
```

**Purpose**: Calls the surface’s mid-turn reply sender, unless the reply was already posted or the surface has no sender. A stored reply reference avoids duplicate posts after a crash.

**Data flow**: It receives workspace id and a reply row. If a reply ref exists it returns it; otherwise it reads turn and conversation details, finds the surface spec, builds `MidTurnReply`, and calls `speak` when available.

**Call relations**: `_deliver` calls it to hand off the actual external send to `SurfaceSpec.speak`.

*Call graph*: called by 1 (_deliver); 4 external calls (__init__, select, workspace_tx, log).


##### `MidTurnReplyPoller._mark_delivered`  (lines 4439–4457)

```
async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Marks a mid-turn reply as delivered and stores its external reference. If the claim was lost, it only logs that fact.

**Data flow**: It receives reply id and optional reply ref, updates the claimed row to delivered, clears owner and expiry, and logs if no row matched.

**Call relations**: `_deliver` calls it after `_speak` succeeds or decides there is nothing to send.

*Call graph*: called by 1 (_deliver); 3 external calls (update, workspace_tx, log).


##### `MidTurnReplyPoller._fail_or_retry`  (lines 4459–4506)

```
async def _fail_or_retry(self, reply_id: UUID, error: Exception) -> tuple[str, str, datetime | None]
```

**Purpose**: Releases a failed mid-turn reply for retry, or marks it failed when too old. A failed span no longer blocks the terminal writeback.

**Data flow**: It receives reply id and exception, computes retry timing and last-error text, updates the row to pending or failed while clearing the claim, and returns outcome details.

**Call relations**: `_deliver` calls it when `_speak` raises an error.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### `core/src/ufo/ingress_serve.py`

`entrypoint` · `startup and request handling`

A sandbox can run a web server, but viewers should not connect to it directly. This file is the guarded front desk. Each sandbox site gets its own signed hostname, so the browser treats it like a separate website with separate cookies and storage. When someone opens a special view link, the server checks that the link is valid for this exact site, then gives the browser a short-lived session cookie and redirects it to the site path.

After that, normal requests go through a proxy. The proxy reads the hostname to learn which conversation and port are being requested, checks the session cookie, looks up the live sandbox in the database, dials it through the selected carrier, and streams bytes between browser and sandbox without loading the whole response into memory.

The file also protects important boundaries. It strips or rewrites headers that could leak sessions, poison caches, break framing, or let a sandbox set cookies outside its own origin. WebSockets get the same authorization gate as HTTP, plus an origin check so one hosted site cannot secretly open a socket to another. Without this file, sandbox sites would either be unreachable from the browser or exposed with unsafe cross-site and caching behavior.

#### Function details

##### `IngressServe.app`  (lines 220–252)

```
def app(self) -> FastAPI
```

**Purpose**: Builds the FastAPI web application that receives all ingress traffic. It sets up separate routes for opening signed view links, proxying normal HTTP requests, and relaying WebSocket connections.

**Data flow**: It starts with the configured IngressServe object, creates an empty FastAPI application, attaches HTTP and WebSocket route patterns, and returns the ready-to-run application. The important before→after change is that plain methods on this class become public routes the server can call.

**Call relations**: During startup, run creates an IngressServe and hands the result of this method to uvicorn. The routes then lead traffic into _no_view_token, _open, _proxy, _no_socket_view, or _socket depending on the path and protocol.

*Call graph*: 1 external calls (FastAPI).


##### `IngressServe._no_view_token`  (lines 254–260)

```
async def _no_view_token(self, request: Request) -> Response
```

**Purpose**: Answers requests to the reserved view-link path when no token was supplied. It prevents an empty or malformed view URL from accidentally reaching sandbox code.

**Data flow**: It receives an HTTP request. If the method is not GET or HEAD, it returns a 405 response saying which methods are allowed; otherwise it returns a plain 403 message saying the link is not valid.

**Call relations**: IngressServe.app routes the bare view path here. This protects the reserved token exchange path before the catch-all proxy route can forward anything to a sandbox.

*Call graph*: 1 external calls (Response).


##### `IngressServe._open`  (lines 262–304)

```
async def _open(self, request: Request, view_path: str) -> Response
```

**Purpose**: Turns a valid signed view token into a short-lived session cookie for the exact site named by the current hostname. This is how a browser is allowed to enter a sandbox-hosted site.

**Data flow**: It receives the request and the path text after the reserved view prefix. It splits out the token, checks the request hostname with _site, verifies the token, confirms the token matches the hostname’s conversation and port, mints a session token, sets it as a cookie, and returns a redirect to the requested site path. Bad methods, bad hosts, expired tokens, wrong-site tokens, or suspicious paths become error responses.

**Call relations**: IngressServe.app sends view-link HTTP requests here. It depends on _site to identify the addressed site, uses token verification and minting helpers, and returns the session that later lets _dial_site authorize _proxy and _socket traffic.

*Call graph*: calls 1 internal fn (_site); 8 external calls (replace, now, RedirectResponse, Response, mint_ingress_token, verify_ingress_token, set_session_cookie, quote).


##### `IngressServe._site`  (lines 306–317)

```
def _site(self, request: HTTPConnection) -> tuple[UUID, int] | None
```

**Purpose**: Figures out which sandbox site a request is trying to reach by reading the request hostname. It returns the conversation ID and port encoded in the site label, or nothing if the hostname is not one of this ingress server’s sites.

**Data flow**: It receives an HTTP or WebSocket connection, reads the hostname, checks that it ends with this server’s base host, removes that suffix, and asks parse_site_label to decode the remaining signed label. A valid label becomes a conversation-and-port pair; an invalid or foreign hostname becomes None.

**Call relations**: _open uses this to make sure a view token is being opened on the right site origin. _dial_site uses it as the first gate before allowing either HTTP proxying or WebSocket relaying.

*Call graph*: called by 2 (_dial_site, _open); 1 external calls (parse_site_label).


##### `IngressServe._dial_site`  (lines 319–365)

```
async def _dial_site(self, connection: HTTPConnection) -> DialedSite | SiteRefusal
```

**Purpose**: Performs the shared authorization and lookup step before any traffic reaches a sandbox. It checks the hostname, validates the session cookie, finds the live sandbox handle, and asks the carrier how to connect to the requested port.

**Data flow**: It receives an HTTP or WebSocket connection. It extracts the addressed site with _site, verifies the session cookie, compares the cookie’s claims to the hostname, reads the stored sandbox handle with _stored_handle, converts that into a backend container handle, and asks the carrier to dial the sandbox port. Success returns a DialedSite with claims and connection target; failure returns a SiteRefusal with a status code and viewer-facing message.

**Call relations**: _proxy and _socket both call this before talking to sandbox code. It hands successful callers the target they need to build an upstream HTTP or WebSocket connection, and gives failed callers one consistent refusal to return.

*Call graph*: calls 2 internal fn (_site, _stored_handle); called by 2 (_proxy, _socket); 8 external calls (__init__, __init__, __init__, now, warn, verify_ingress_token, sandbox_handle_id, ws).


##### `IngressServe._proxy`  (lines 367–419)

```
async def _proxy(self, request: Request, path: str) -> Response
```

**Purpose**: Forwards an authorized HTTP request to the sandbox site and streams the sandbox’s response back to the browser. It also cleans request and response headers so the sandbox cannot steal ingress cookies, override security framing, or make protected content cacheable.

**Data flow**: It receives the browser request and path. It calls _dial_site; refusal becomes a plain error response. On success, it builds the upstream URL with _upstream_url, prepares safe headers with _upstream_headers, streams the request body if present, sends the request with the shared HTTP client, and returns a StreamingResponse over _body. Response headers are filtered: unsafe cache, framing, hop-by-hop, and cookie behaviors are removed or rewritten before reaching the browser.

**Call relations**: IngressServe.app routes all non-view HTTP paths here. It relies on _dial_site for access control, _upstream_url and _upstream_headers for the outbound request, _body for streaming cleanup, and _unframed_policy plus _confined_cookie for response safety.

*Call graph*: calls 6 internal fn (_body, _confined_cookie, _dial_site, _unframed_policy, _upstream_headers, _upstream_url); 7 external calls (stream, Response, StreamingResponse, Request, BackgroundTask, log_error, ws).


##### `IngressServe._upstream_url`  (lines 421–424)

```
def _upstream_url(self, scheme: str, host: str, path: str, query_string: bytes) -> str
```

**Purpose**: Builds the exact URL used to contact the sandbox server. It preserves the requested path and query string while choosing HTTP, HTTPS, WS, or WSS based on the dialed target.

**Data flow**: It receives a scheme, upstream host, path, and raw query string. It safely quotes the path, appends the query if one exists, and returns a complete URL string.

**Call relations**: _proxy uses this when forwarding normal HTTP requests. _socket uses the same helper when opening a WebSocket connection to the sandbox.

*Call graph*: called by 2 (_proxy, _socket); 1 external calls (quote).


##### `IngressServe._stored_handle`  (lines 426–436)

```
async def _stored_handle(self, workspace_id: UUID, conversation_id: UUID) -> str | None
```

**Purpose**: Looks up the saved sandbox handle for a conversation in the workspace database. This tells the ingress which running sandbox, if any, belongs to the requested conversation.

**Data flow**: It receives a workspace ID and conversation ID. Inside a workspace database transaction, it queries the conversation table for the sandbox handle. It returns that handle string when found, or None when the conversation row or handle is missing.

**Call relations**: _dial_site calls this after the viewer’s session cookie has been verified. The result decides whether _dial_site can create a SandboxHandle and ask the carrier to connect, or must refuse because the site is gone.

*Call graph*: called by 1 (_dial_site); 2 external calls (select, workspace_tx).


##### `IngressServe._upstream_headers`  (lines 438–472)

```
def _upstream_headers(self, request: HTTPConnection, dial_headers: Mapping[str, str]) -> list[tuple[str, str]]
```

**Purpose**: Creates the safe set of request headers that the sandbox server is allowed to see. It removes transport-only headers, the public Host header, WebSocket handshake internals, duplicated carrier headers, and the ingress session cookie.

**Data flow**: It receives the browser connection and extra headers supplied by the dial target. It walks through incoming headers, drops unsafe or inappropriate ones, removes the reserved ingress cookie from Cookie lines while keeping the site’s own cookies, and then appends the dial target’s headers. The output is a list of header name/value pairs for the upstream request.

**Call relations**: _proxy uses this before sending HTTP to the sandbox. _socket uses it before opening the upstream WebSocket connection, so both protocols expose the same safe header view to sandbox code.

*Call graph*: called by 2 (_proxy, _socket).


##### `IngressServe._unframed_policy`  (lines 474–486)

```
def _unframed_policy(self, policy: str) -> str
```

**Purpose**: Removes only the frame-control part from a site’s Content-Security-Policy header. This lets the ingress, not the sandbox, decide which app page may embed the site while preserving the site’s other browser protections.

**Data flow**: It receives one policy header as text, splits it into semicolon-separated directives, drops any directive named frame-ancestors, and joins the rest back together. If nothing remains, it returns an empty string so the caller can omit that header.

**Call relations**: _proxy calls this while copying response headers from the sandbox. The proxy then adds its own frame-ancestors rule separately, keeping framing policy consistent for all hosted sites.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._confined_cookie`  (lines 488–515)

```
def _confined_cookie(self, header: str) -> str | None
```

**Purpose**: Rewrites or rejects one Set-Cookie header from a sandbox site so the cookie stays inside that one site origin. It prevents a sandbox from setting cookies for parent domains or using the reserved ingress cookie namespace.

**Data flow**: It receives a raw Set-Cookie header. It parses the cookie name, rejects nameless cookies, malformed cookies, and names starting with the reserved prefix, removes any Domain attribute, and returns the remaining cookie header. If the cookie is not allowed, it returns None.

**Call relations**: _proxy calls this for each Set-Cookie header in a sandbox response. Allowed cookies are appended to the browser response; rejected cookies are silently kept off the wire.

*Call graph*: called by 1 (_proxy).


##### `IngressServe._body`  (lines 517–527)

```
async def _body(self, upstream: httpx.Response) -> AsyncIterator[bytes]
```

**Purpose**: Streams raw response bytes from the sandbox to the browser and makes sure the upstream response is closed afterward. This avoids buffering large bodies and avoids leaking pooled connections when a stream ends early or fails.

**Data flow**: It receives an httpx upstream response. It yields each raw chunk as it arrives, and in a final cleanup step closes the upstream response no matter whether streaming finished normally or through an error.

**Call relations**: _proxy gives this iterator to StreamingResponse. The streaming response sends its yielded chunks to the viewer while this helper guarantees the upstream connection is released.

*Call graph*: called by 1 (_proxy); 2 external calls (aclose, aiter_raw).


##### `IngressServe._no_socket_view`  (lines 529–535)

```
async def _no_socket_view(self, websocket: WebSocket) -> None
```

**Purpose**: Rejects WebSocket attempts to the reserved view-token path. View tokens are meant to be exchanged over HTTP only, not forwarded to sandbox code through a socket path.

**Data flow**: It receives a WebSocket handshake and creates a refusal saying the link is not valid. It then passes that refusal to _refuse, which sends an HTTP-style denial response instead of accepting the socket.

**Call relations**: IngressServe.app routes WebSocket handshakes on the view path here. It uses _refuse so this denial behaves like other WebSocket authorization failures.

*Call graph*: calls 1 internal fn (_refuse); 1 external calls (__init__).


##### `IngressServe._socket`  (lines 537–588)

```
async def _socket(self, websocket: WebSocket, path: str) -> None
```

**Purpose**: Relays an authorized WebSocket connection between the browser and the sandbox site. This lets hosted apps use live reload, push updates, or custom bidirectional protocols safely.

**Data flow**: It receives the browser WebSocket and path. It first checks _same_origin, then calls _dial_site. If either gate fails, it refuses the handshake. On success, it builds the upstream WebSocket URL, forwards safe headers, offers the browser’s requested subprotocols to the sandbox, opens the upstream socket, accepts the browser socket with the sandbox’s chosen subprotocol, and runs _relay. If connection or relay fails, it logs the problem and closes or refuses with an appropriate message.

**Call relations**: IngressServe.app sends catch-all WebSocket traffic here. It coordinates _same_origin, _dial_site, _upstream_url, _upstream_headers, _refuse, _relay, and _end to make a single guarded tunnel.

*Call graph*: calls 7 internal fn (_dial_site, _end, _refuse, _relay, _same_origin, _upstream_headers, _upstream_url); 6 external calls (__init__, accept, log_error, ws, connect, Subprotocol).


##### `IngressServe._same_origin`  (lines 590–602)

```
def _same_origin(self, websocket: WebSocket) -> bool
```

**Purpose**: Checks that a WebSocket was opened by the same site hostname it is connecting to. This blocks one sandbox-hosted site from using the viewer’s cookie to open a socket into another site.

**Data flow**: It receives the WebSocket handshake, reads the Origin header, parses its hostname, and compares that hostname with the requested WebSocket URL hostname. It returns true only when an Origin exists and the hostnames match.

**Call relations**: _socket calls this before the normal session gate. HTTP requests do not use this check, but WebSockets need it because browser cross-origin protections are weaker for socket handshakes.

*Call graph*: called by 1 (_socket); 1 external calls (urlsplit).


##### `IngressServe._refuse`  (lines 604–611)

```
async def _refuse(self, websocket: WebSocket, refusal: SiteRefusal) -> None
```

**Purpose**: Rejects a WebSocket handshake with the same status code and plain message that an HTTP request would have received. This gives the viewer a clear denial instead of a vague socket close.

**Data flow**: It receives the WebSocket object and a SiteRefusal. It wraps the refusal message and status in a Response and sends it as a denial response without accepting the socket.

**Call relations**: _no_socket_view uses this for reserved view paths. _socket uses it whenever origin checks, authorization, dialing, or upstream connection setup fail before the browser socket is accepted.

*Call graph*: called by 2 (_no_socket_view, _socket); 2 external calls (send_denial_response, Response).


##### `IngressServe._relay`  (lines 613–630)

```
async def _relay(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Runs the two halves of a WebSocket tunnel at the same time: browser to sandbox and sandbox to browser. It stops the whole tunnel as soon as either side finishes or fails.

**Data flow**: It receives the accepted browser WebSocket and the connected upstream WebSocket. It starts _viewer_to_site and _site_to_viewer as concurrent tasks, waits for the first to finish, cancels the other, gathers cleanup results, and re-raises any real failure from the completed side.

**Call relations**: _socket calls this after both WebSocket connections are open. It delegates actual message copying to _viewer_to_site and _site_to_viewer, acting like the coordinator between the two one-way pipes.

*Call graph*: calls 2 internal fn (_site_to_viewer, _viewer_to_site); called by 1 (_socket); 3 external calls (create_task, gather, wait).


##### `IngressServe._viewer_to_site`  (lines 632–641)

```
async def _viewer_to_site(self, viewer: WebSocket, upstream: ClientConnection) -> None
```

**Purpose**: Copies WebSocket messages from the browser to the sandbox, preserving whether each message is text or binary. That matters because many WebSocket protocols treat text and bytes differently.

**Data flow**: It repeatedly receives messages from the browser WebSocket. A disconnect message ends the loop; otherwise it sends either the text payload or byte payload to the upstream sandbox connection.

**Call relations**: _relay starts this as one direction of the tunnel. It works alongside _site_to_viewer until either direction ends.

*Call graph*: called by 1 (_relay); 2 external calls (receive, send).


##### `IngressServe._site_to_viewer`  (lines 643–656)

```
async def _site_to_viewer(self, upstream: ClientConnection, viewer: WebSocket) -> None
```

**Purpose**: Copies WebSocket messages from the sandbox back to the browser and then closes the browser side with an appropriate close code. It preserves text versus binary messages on the way back.

**Data flow**: It reads messages from the upstream sandbox connection. Text messages are sent as text to the browser; byte messages are sent as bytes. When the upstream closes, it chooses a legal close code, substitutes a safe code for reserved values if needed, and calls _end with the close reason.

**Call relations**: _relay starts this as the sandbox-to-browser half of the tunnel. It calls _end when the sandbox side is done so the viewer sees a clear terminal close.

*Call graph*: calls 1 internal fn (_end); called by 1 (_relay); 3 external calls (suppress, send_bytes, send_text).


##### `IngressServe._end`  (lines 658–668)

```
async def _end(self, viewer: WebSocket, code: int, reason: str) -> None
```

**Purpose**: Closes the browser WebSocket while ignoring errors that only mean the viewer is already gone. It is a final cleanup step, not a place where new failures should hide the original problem.

**Data flow**: It receives the browser WebSocket, a close code, and a reason. It attempts to close the socket with those values and suppresses any exception raised during that final close attempt.

**Call relations**: _site_to_viewer calls this after the sandbox closes. _socket also calls it when relay work fails after the browser socket has already been accepted.

*Call graph*: called by 2 (_site_to_viewer, _socket); 2 external calls (suppress, close).


##### `ingress_base_host`  (lines 671–682)

```
def ingress_base_host(configured: str | None) -> str
```

**Purpose**: Extracts the wildcard base hostname used for all sandbox site origins from configuration. It refuses to start if that setting is missing or invalid, because the ingress would not know how to map hostnames to sites.

**Data flow**: It receives the configured ingress public URL or None. It parses the URL, returns the hostname when present, and raises a RuntimeError when no hostname can be found.

**Call relations**: run calls this during startup while building IngressServe. The returned host is later used by _site to decide whether an incoming hostname belongs to this ingress deployment.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `ingress_frame_ancestor`  (lines 685–693)

```
def ingress_frame_ancestor(configured: str | None) -> str
```

**Purpose**: Builds the browser framing source that hosted sites are allowed to be embedded by. In plain terms, it says which app origin is allowed to put these sandbox sites inside a frame.

**Data flow**: It receives the configured public app URL or None. If the URL has a scheme and hostname, it returns just the origin: scheme, host, and optional port. If not, it returns the strict value 'none', meaning no page may embed the site.

**Call relations**: run calls this during startup and stores the result on IngressServe. _proxy later uses that value when writing the Content-Security-Policy header on proxied responses.

*Call graph*: called by 1 (run); 1 external calls (urlsplit).


##### `upstream_client`  (lines 696–719)

```
def upstream_client() -> httpx.AsyncClient
```

**Purpose**: Creates the shared HTTP client used to contact sandbox web servers. It is deliberately cookie-blind so cookies from one sandbox site cannot be stored by the proxy and replayed to another.

**Data flow**: It takes no input. It builds an httpx AsyncClient with connection limits, timeouts, and a cookie jar whose policy accepts no domains. The result is a reusable client for upstream sandbox requests.

**Call relations**: run calls this once during startup and passes the client into IngressServe. _proxy uses that client for all forwarded HTTP requests.

*Call graph*: called by 1 (run); 4 external calls (CookieJar, DefaultCookiePolicy, AsyncClient, Limits).


##### `run`  (lines 722–743)

```
def run() -> None
```

**Purpose**: Starts the ingress server process. It loads configuration, prepares observability and database access, builds the ingress service, and hands its app to uvicorn to listen for traffic.

**Data flow**: It reads configuration and environment values, initializes logging/telemetry, loads extension manifests, initializes the database, checks database reachability, ensures the ingress signing secret is available, computes the base host and frame ancestor, selects a sandbox carrier, creates the HTTP client and IngressServe, logs startup, and starts uvicorn on the configured port.

**Call relations**: This is the top-level entry for the file. It wires together ingress_base_host, ingress_frame_ancestor, upstream_client, IngressServe.app, and external setup functions so request-time methods have everything they need.

*Call graph*: calls 3 internal fn (ingress_base_host, ingress_frame_ancestor, upstream_client); 12 external calls (__init__, run, load_config, init_db, verify_db_reachable, load_manifests, init_o11y, log, owner_dsn, ingress_secret (+2 more)).


### `core/src/ufo/surfaces/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to things inside this folder using names like `ufo.surfaces.some_module`. Think of it like putting a label on a drawer: the drawer may contain useful tools, but this label simply tells Python that the drawer exists and can be opened through the normal import system. Because the file is empty, it does not set up shared state, re-export helper functions, or run any startup code. Its value is structural: without it, some Python environments or tooling might not recognize `ufo.surfaces` as a package, which could make imports fail or make the project harder for editors, linters, and documentation tools to understand.

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-database-schema` — The durable database layout and migration version that every runtime component must agree on.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-auth-tokens` — The signed tickets and login tokens used to prove access to sessions, downloads, sandbox links, and hosted onboarding.
- `reg-connection-grants` — The saved outside-service account connections and the grants saying which agents may use them.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-live-delivery` — The live stream and delivery state for partial replies, tool updates, terminal output, final status, and missed messages.
- `reg-sandbox-state` — The per-conversation sandbox handle, size, filesystem environment, command execution state, and cleanup state.
- `reg-browser-sessions` — The browser or Chrome DevTools session state used when tools and hosted sandbox websites need a controlled browser.
- `reg-blob-artifacts` — The shared file and artifact storage for large bytes, generated files, previews, and signed downloads.
- `reg-portal-slots` — The safe display state for conversation panels such as sources, artifacts, tasks, sites, automations, and workspace changes.
- `reg-surface-ingress` — The shared records that connect external surfaces like web, Slack, shell, OAuth, and inbound messages to conversations and replies.
- `reg-billing-export` — The billing integration state for exported usage, member counts, Stripe setup, Metronome sync, and BYOK reporting.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-ephemeral-cache-bus` — The selected Redis/cache/pub-sub backend and its ephemeral keys, locks, and connection state used to coordinate live delivery, workers, and shared runtime services.
- `reg-hosted-site-state` — Durable hosted website records, publication metadata, permissions, and homepage-agent bindings used to build, serve, list, and remove sites.
- `reg-db-engine-pool` — The process-wide database engine, DSN binding, and connection pool from which per-request sessions and migration runners obtain connections.
- `reg-connector-link-state` — Pending external-account consent/OAuth linking state between generated approval links, callbacks, completion markers, and eventual saved connections.
- `reg-http-route-map` — The process-wide web application routing state, including mounted core routes, extension routes, middleware, static assets, and ingress handlers used to dispatch incoming requests.
- `reg-listing-cursors` — Opaque pagination and browsing cursor state used to resume stable listings across objects, pages, memory, artifacts, usage records, and portal panels.
