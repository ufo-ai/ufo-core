# Authenticated Ingress and User Surfaces  `stage-6`

This stage is the system’s set of guarded front doors. It runs during normal use, whenever a request arrives from a browser, Slack, iMessage, the terminal, a shared artifact link, an OAuth return page, or a hosted site. Its first job is to check who is knocking, then connect that request to the right workspace, member, conversation, or operator view.

The Browser Web Portal Surface handles the main signed-in web app: chat, settings, admin pages, memory views, usage pages, and live screens. Chat, Terminal, and Messaging Surfaces adapt Slack, iMessage, and command-line messages into the system’s common conversation shape, then send replies back out. Artifact, OAuth, Site, and Debugger Routes are side doors for shared files, login handoffs, public hosted sites, and safe read-only inspection.

The shared surface bridge is the adapter layer between these outside surfaces and the core system. The operator support file adds stricter login rules for powerful internal tools and builds a fleet directory of workspaces and conversations. The package marker simply makes the surfaces code importable.

## Sub-stages

- [Browser Web Portal Surface](stage-6.1.md) `stage-6.1` — 7 files
- [Chat, Terminal, and Messaging Surfaces](stage-6.2.md) `stage-6.2` — 6 files
- [Artifact, OAuth, Site, and Debugger Routes](stage-6.3.md) `stage-6.3` — 5 files

## Files in this stage

### Surface Authentication Bridge
Shared operator login rules and external surface adapters authenticate incoming users, map them to workspace context, and expose the surfaces package for import.

### `core/src/ufo/ext/operator.py`

`domain_logic` · `request handling`

Operator tools, such as a debugger or memory explorer, need stronger access than normal user pages. This file answers two questions for those tools: “Is this request really from an operator?” and “Which workspace is the operator looking at?” It accepts a bearer token from the Authorization header, from a secure session cookie, or from the one form post that starts a session. It deliberately refuses query-string tokens, because URLs often end up in logs, browser history, and shared links.

Once a token is verified, the file checks that the user’s email belongs to the operator email domain. That domain check is the gate. After passing it, the operator can choose a workspace with `?ws=`, using either a workspace ID or a tenant domain. If no workspace is requested, the token’s own workspace is used.

The other major piece is `FleetDirectory`. Think of it like a front desk directory for the whole deployment. It lists every workspace, how many members and conversations it has, and the most recently active threads across the fleet. It reads broad identifiers first, then re-enters each workspace separately to read human-facing details under normal workspace security rules. It also ignores subagent child turns so internal background work does not crowd out real user-facing conversations.

#### Function details

##### `operator_claims`  (lines 45–63)

```
async def operator_claims(request: Request) -> tuple[str, str] | None
```

**Purpose**: This function tries to prove who the operator request belongs to. It looks for a valid bearer token in safe places only: first the Authorization header, then the operator session cookie, then the posted login form.

**Data flow**: It receives a web request. It reads the Authorization header and asks `_candidate_claims` whether the token is valid. If that fails, it tries the session cookie. If that also fails and the request is a POST, it reads the form body and tries the posted token. It returns a pair containing the claimed workspace and email address, or returns nothing if no valid token can be found.

**Call relations**: This is the first step used by `resolve_operator_workspace` when an operator-only page or API request arrives. It delegates the actual token checking to `_candidate_claims`, and it reads the request form only for the special POST that opens a session.

*Call graph*: calls 1 internal fn (_candidate_claims); called by 1 (resolve_operator_workspace); 1 external calls (form).


##### `_candidate_claims`  (lines 66–68)

```
def _candidate_claims(candidate: str) -> tuple[str, str] | None
```

**Purpose**: This small helper cleans up a possible token and verifies it. It exists so every token source is treated the same way.

**Data flow**: It receives a possible token string. It trims surrounding spaces. If the result is empty, it returns nothing. Otherwise it passes the token to `verified_claims`, which checks the signature and returns the trusted claims if the token is valid.

**Call relations**: `operator_claims` calls this helper for each possible token source: header, cookie, and posted form field. `_candidate_claims` hands the real verification work to `verified_claims`, keeping this file from storing or owning the signing secret.

*Call graph*: called by 1 (operator_claims); 1 external calls (verified_claims).


##### `resolve_operator_workspace`  (lines 71–110)

```
async def resolve_operator_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: This function decides which workspace an operator request is allowed to use. It verifies the operator identity, applies the operator email-domain gate, and turns the requested `?ws=` value into a workspace ID.

**Data flow**: It receives the request and the surface authentication context. It asks `operator_claims` for verified workspace-and-email claims. If no claims are found, a plain GET for an operator page is redirected to the shared login page; other requests are rejected by returning nothing. If claims exist, it checks the email domain. Then it reads `?ws=`: without it, it uses the workspace from the token; with it, it accepts a workspace UUID, or looks up a workspace by domain, or finally creates the standard UUID that would belong to that domain. The result is either a workspace ID, a redirect response, or nothing.

**Call relations**: Operator surfaces call this during request authentication to decide the workspace scope. It depends on `operator_claims` for identity, `email_domain` for the operator-domain check, and `workspace_by_domain` inside an owner-level database transaction when a domain must be resolved.

*Call graph*: calls 1 internal fn (operator_claims); 6 external calls (owner_tx, RedirectResponse, email_domain, workspace_by_domain, UUID, uuid5).


##### `bind_operator_session`  (lines 113–131)

```
async def bind_operator_session(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: This function turns a posted bearer token into a browser session cookie for operator tools. It lets an operator sign in once and then browse all shared operator surfaces without putting the token in the URL.

**Data flow**: It receives the surface context and the request. It reads the form body and looks for the `token` field. If the token is missing or blank, it returns a JSON error. If present, it creates a redirect back to the same URL and attaches an HTTP-only session cookie containing the token, using the surface’s secure-cookie setting. The browser receives the redirect and comes back carrying the cookie.

**Call relations**: This is used by the session-opening POST after the token has already been verified by the authentication resolver. It calls `Request.form` to read the submitted token, uses `RedirectResponse` or `JSONResponse` to build the reply, and uses `set_session_cookie` to store the session safely.

*Call graph*: 4 external calls (JSONResponse, RedirectResponse, form, set_session_cookie).


##### `FleetWorkspace._aware_utc`  (lines 149–150)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: This validator makes sure a workspace activity time has a time zone. If the database gives a plain timestamp, it treats it as UTC so later display and sorting do not become ambiguous.

**Data flow**: It receives the `last_turn_at` value while a `FleetWorkspace` model is being created. If the value is missing, it leaves it alone. If it already has a time zone, it leaves it alone. If it has no time zone, it returns a copy marked as UTC.

**Call relations**: Pydantic, the data validation library used by `FleetWorkspace`, calls this automatically when building the model. It uses `datetime.replace` only when it needs to attach the UTC time zone.

*Call graph*: 1 external calls (replace).


##### `FleetThread._aware_utc`  (lines 168–169)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: This validator makes sure a recent thread’s last-activity time has a time zone. That prevents a timestamp from being silently interpreted differently by different parts of the system.

**Data flow**: It receives the thread’s `last_turn_at` value while a `FleetThread` model is being created. If the timestamp already has a time zone, it is returned unchanged. If it does not, the function returns a UTC-marked version.

**Call relations**: Pydantic calls this automatically during `FleetThread` creation, including when `FleetDirectory.read` builds the list of recent threads. It calls `datetime.replace` to attach UTC when needed.

*Call graph*: 1 external calls (replace).


##### `FleetDirectory.read`  (lines 201–232)

```
async def read(self) -> FleetListing
```

**Purpose**: This is the main reader for the operator’s fleet directory. It returns the list of workspaces and the recent conversation threads an operator can click into.

**Data flow**: It starts by calling `_enumerate` to get broad activity facts: which workspaces exist and which conversations were recently active. It groups the wanted conversation IDs by workspace. Then, for each workspace, it temporarily scopes execution to that workspace and calls `_scoped` to fetch readable details such as domain, member count, conversation count, surface, queue key, and title. Finally it combines those pieces into a `FleetListing` containing `FleetWorkspace` and `FleetThread` objects.

**Call relations**: This is the public entry point of `FleetDirectory`. It coordinates the two-pass design: `_enumerate` performs the owner-level sweep, `ws` re-binds execution to one workspace at a time, and `_scoped` reads details under workspace-level rules. It then constructs the final listing models for operator surfaces to render.

*Call graph*: calls 2 internal fn (_enumerate, _scoped); 3 external calls (__init__, __init__, ws).


##### `FleetDirectory._enumerate`  (lines 234–272)

```
async def _enumerate(self) -> tuple[Sequence[sa.Row[Any]], Sequence[sa.Row[Any]]]
```

**Purpose**: This function performs the broad fleet-wide scan. It gathers only identifiers and ordering timestamps, enough to know what exists and what moved recently without reading workspace-specific text.

**Data flow**: It builds database queries for all workspaces and for the most recently active conversations, counting only root turns and skipping child turns from subagents. It runs those queries inside an owner-level database transaction. It returns two row lists: workspace activity rows and recent conversation rows.

**Call relations**: `FleetDirectory.read` calls this first. It uses SQLAlchemy to build the queries and `owner_tx` to run them with the kind of access intended for cross-workspace identifiers.

*Call graph*: called by 1 (read); 2 external calls (select, owner_tx).


##### `FleetDirectory._scoped`  (lines 274–322)

```
async def _scoped(self, workspace_id: UUID, last_turn_at: datetime | None, conversation_ids: Sequence[UUID]) -> tuple[FleetWorkspace, dict[UUID, sa.Row[Any]]]
```

**Purpose**: This function reads the human-visible details for one workspace in the fleet directory. It does so while scoped to that workspace, so the read follows the same boundary as normal workspace access.

**Data flow**: It receives a workspace ID, that workspace’s last activity time, and the conversation IDs that need details. Inside a workspace transaction, it finds the workspace domain, counts members, counts non-subagent conversations, and loads title and routing details for the requested conversations. It returns a `FleetWorkspace` summary plus a dictionary of opened conversation rows keyed by conversation ID.

**Call relations**: `FleetDirectory.read` calls this once per workspace after setting the current workspace with `ws`. It uses `workspace_tx` for the scoped database transaction, `workspace_domain` for the display domain, SQL queries for counts and conversation details, and `FleetWorkspace` to package the workspace summary.

*Call graph*: called by 1 (read); 4 external calls (__init__, select, workspace_tx, workspace_domain).


### `core/src/ufo/ext/surface.py`

`orchestration` · `cross-cutting`

A “surface” is any outside place where a member meets the agent: Slack, iMessage, the web portal, a terminal-like live channel, and similar integrations. This file is the doorway those surfaces use. It matters because surfaces are trusted in a special way: they can say who a member is and can put that member’s message onto the durable turn queue. Ordinary extensions are not allowed to do that.

The file provides three big sets of tools. First, it defines small data shapes used by surfaces, such as conversation summaries, shared files, spend reports, connector views, and turn details. These are the safe “view models” the portal and integrations render. Second, it defines `SurfaceContext`, the main object handed to a surface handler. Through it, a surface can link external identities to workspace members, create or find conversations, admit messages, stream live turn frames, stop turns, fetch transcripts, list agents, read workspace files, and mint download or sandbox links. Third, it runs background delivery loops for durable surfaces. Durable surfaces cannot rely on live in-memory events, so `WritebackPoller` and `MidTurnReplyPoller` repeatedly claim database rows, call the surface’s delivery functions, record success, and retry failures without sending two workers to do the same job.

An everyday analogy: this file is both the front desk and the mailroom. The front desk checks who someone is and routes their request to the right workspace conversation. The mailroom makes sure completed replies and attachments eventually reach external systems, even if a worker crashes or Slack briefly refuses a message.

#### Function details

##### `mint_marker`  (lines 196–206)

```
def mint_marker() -> str
```

**Purpose**: Creates a short random marker used to wrap one member message safely inside text sent to the model. The marker makes the wrapper unique, so a user cannot accidentally or deliberately close another message’s wrapper.

**Data flow**: It takes no input, asks the secure random-token library for a few bytes, and returns them as hexadecimal text.

**Call relations**: It is a small helper used when building fenced inbound messages. It hands a fresh marker to code that later calls `fence_member_message`.

*Call graph*: 1 external calls (token_hex).


##### `fence_member_message`  (lines 209–222)

```
def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str
```

**Purpose**: Builds the full text that represents one incoming member message, separating background context, the member’s own words, and attachment text. This gives the model a clear transcript without editing the member’s words.

**Data flow**: It receives a marker, ambient context text, the message body, and attachment text. It wraps the body and optional attachments in marker-named tags and returns one combined string.

**Call relations**: Surface ingest code uses this before admitting a member message. Later projection helpers, especially `member_message_text`, can pull the member’s own words back out.


##### `inbox_name`  (lines 225–254)

```
def inbox_name(raw: str, used: set[str]) -> str
```

**Purpose**: Turns an untrusted attachment filename into a safe filename for the workspace. It prevents path tricks, keeps useful extensions, caps length, and avoids duplicates within one batch.

**Data flow**: It receives a raw filename and a set of names already used. It strips path parts, replaces unsafe characters, trims the name, adds a number if needed, records the chosen name in `used`, and returns it.

**Call relations**: Surface upload and attachment-download paths rely on this shared rule so each integration treats dangerous filenames the same way.

*Call graph*: 1 external calls (contained_leaf).


##### `member_message_text`  (lines 257–271)

```
def member_message_text(inbound: str) -> str
```

**Purpose**: Extracts the member’s actual words from the larger inbound text that may include system context or surface wrappers. It is used when a UI wants to show what the person said, not the hidden prompt scaffolding.

**Data flow**: It receives inbound text, removes known engine context at the front and injected context at the end, then looks for the marker-wrapped member-message section. It returns that inner text when found, otherwise the cleaned input.

**Call relations**: `conversation_name` calls it to name new conversations from the member’s words rather than from Slack channel context or model-injected recall.

*Call graph*: called by 1 (conversation_name).


##### `MemberAdmitter.admit`  (lines 325–335)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, commen
```

**Purpose**: Defines the contract for admitting a member message into the turn system. Concrete implementations use it to create, resume, or join a turn as the named speaker.

**Data flow**: It accepts a conversation id, message body, optional idempotency key, context, speaker member id, optional prepared tool intent, and optional comment. It returns an `Admitted` result describing the turn and whether a run was opened.

**Call relations**: `SurfaceContext.admit` delegates to whatever concrete `MemberAdmitter` core injected, so surfaces never create turn rows directly.


##### `TurnTailer.tail`  (lines 349–351)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Defines how a live surface streams frames from a running turn. A frame is a live event, such as progress or a partial reply, that the member’s held connection can render.

**Data flow**: It receives a turn id and optional cursor, opens an async scoped stream, and yields cursor-frame pairs until the turn ends or the caller leaves the scope.

**Call relations**: `SurfaceContext.tail` exposes this injected tailer to web, CLI-like, debugger, and other live surfaces.


##### `TurnTailer.latest_activity`  (lines 353–353)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Defines a quick peek at what a running turn is currently doing without opening a stream. It helps status pages show live activity.

**Data flow**: It receives a turn id and returns the newest retained activity frame, or nothing if there is no retained activity.

**Call relations**: `SurfaceContext.latest_activity` passes this through to callers such as the web agents-status view.


##### `TurnStopper.stop`  (lines 363–363)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> 'Stopped'
```

**Purpose**: Defines how a surface asks core to stop a running turn. It also reports whether stopping founded a follow-up turn from a message that was already waiting.

**Data flow**: It receives workspace, conversation, and turn ids. It cancels or observes the turn and returns a `Stopped` result with the outcome.

**Call relations**: `SurfaceContext.stop_turn` delegates to this concrete stopper after the surface has already checked the acting member.


##### `TurnStepSource.read`  (lines 369–369)

```
async def read(self, workflow_id: str) -> tuple['TurnStep', ...]
```

**Purpose**: Defines how to read durable workflow steps for one turn attempt. These steps are used by debugging and inspection views.

**Data flow**: It receives a workflow id and returns an ordered tuple of `TurnStep` records.

**Call relations**: `SurfaceContext.turn_steps` uses this injected reader after confirming the turn belongs to the workspace.


##### `SurfaceModel.model`  (lines 399–399)

```
def model(self) -> str
```

**Purpose**: Names the model backing surface-side one-shot model calls. This lets the call be labelled and billed.

**Data flow**: It reads model identity from the concrete implementation and returns it as text.

**Call relations**: Surface routes access it through `SurfaceContext.model` when they need optional model help.


##### `SurfaceModel.turn`  (lines 401–401)

```
async def turn(self, request: ModelRequest) -> Message
```

**Purpose**: Defines a one-shot model call available to a surface route. It is for small surface-owned tasks where the route can still answer if the call fails.

**Data flow**: It receives a `ModelRequest`, sends it to the configured model, and returns a model `Message`.

**Call relations**: Concrete model access is injected into `SurfaceContext`; route code calls it only after checking that a model exists.


##### `shared_artifact_link`  (lines 486–497)

```
def shared_artifact_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary signed download link for a shared file. If public file delivery is not configured, it returns nothing so the surface can still show the filename.

**Data flow**: It receives a signing secret, public base URL, workspace id, and artifact. It computes an expiry, signs a download path, joins it to the public base, and returns the URL or `None`.

**Call relations**: `SurfaceContext.artifact_link` wraps this with the current workspace and configured secrets.

*Call graph*: called by 1 (artifact_link); 3 external calls (now, artifact_url_expiry, mint_artifact_url).


##### `shared_artifact_preview_link`  (lines 500–522)

```
def shared_artifact_preview_link(secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a signed preview-image link for an artifact when the artifact can safely be shown as a raster image. It avoids pretending a non-image file is an image.

**Data flow**: It receives signing settings, workspace id, and artifact. It chooses the preview blob if present, verifies the declared image type matches the key, and returns a preview URL or `None`.

**Call relations**: `SurfaceContext.artifact_preview_link` calls this for web file cards and conversation-slot projections.

*Call graph*: called by 1 (artifact_preview_link); 2 external calls (mint_image_preview_url, raster_image_media_type).


##### `_scheduled_runs_query`  (lines 525–561)

```
def _scheduled_runs_query(workspace_id: UUID, member_id: UUID, agent_id: UUID | None) -> sa.Select[Any]
```

**Purpose**: Builds the database query for scheduled turns a member is allowed to see. It keeps the permission logic in one place for the scheduled-run feed.

**Data flow**: It receives workspace id, member id, and optional agent id. It returns a SQL query limited to terminal scheduled turns in readable conversations, including failures and successful runs that shared files.

**Call relations**: `scheduled_runs` starts with this query and then adds optional filters, ordering, and result shaping.

*Call graph*: called by 1 (scheduled_runs); 3 external calls (or_, select, readable_audiences).


##### `scheduled_runs`  (lines 564–640)

```
async def scheduled_runs(workspace_id: UUID, member_id: UUID, *, limit: int, agent_id: UUID | None=None, turn_id: UUID | None=None, subjects: frozenset[str] | None=None) -> tuple[ScheduledRun, ...]
```

**Purpose**: Reads the newest completed scheduled runs visible to a member. This powers feeds that show what automated tasks fired, what they said, and which files they shared.

**Data flow**: It receives workspace and reader information plus filters. It queries matching turns, loads their shared artifacts, resolves conversation sources, and returns `ScheduledRun` objects.

**Call relations**: It calls `_scheduled_runs_query`, builds `SharedArtifact` rows, and asks `ConversationDirectory.sources` for links back to the originating conversation.

*Call graph*: calls 1 internal fn (_scheduled_runs_query); 6 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx).


##### `conversation_name`  (lines 689–694)

```
def conversation_name(inbound: str) -> str
```

**Purpose**: Derives a new conversation title from the member’s own opening words. It avoids using ambient channel context as the title.

**Data flow**: It receives inbound text, extracts the member-message portion, trims whitespace, caps the length, and returns the title text.

**Call relations**: Conversation-opening paths call this when naming a conversation consistently across surfaces.

*Call graph*: calls 1 internal fn (member_message_text).


##### `retitle_conversation`  (lines 697–714)

```
async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Updates a conversation title when a surface has a better name for it. Blank titles are ignored.

**Data flow**: It receives workspace id, conversation id, and title. It trims and caps the title, then updates the matching conversation row if the title is non-empty.

**Call relations**: `SurfaceContext.retitle_conversation` delegates here so direct and context-based title updates share the same rule.

*Call graph*: called by 1 (retitle_conversation); 2 external calls (update, workspace_tx).


##### `summarize_conversation_title`  (lines 717–737)

```
async def summarize_conversation_title(workspace_id: UUID, conversation_id: UUID, title: str) -> None
```

**Purpose**: Stores the title generated by an automatic titling job and records that summarization has been attempted. This prevents paying to summarize the same conversation repeatedly.

**Data flow**: It receives workspace id, conversation id, and proposed title. It writes the title if non-blank and always marks the row as summarized.

**Call relations**: Titling jobs call it after model summarization; listing views later read the title from the conversation row.

*Call graph*: 2 external calls (update, workspace_tx).


##### `AgentDetail._aware_utc`  (lines 810–811)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes agent update timestamps so they always include timezone information. This avoids ambiguous times in API responses.

**Data flow**: It receives a datetime. If it lacks timezone data, it marks it as UTC; otherwise it returns it unchanged.

**Call relations**: Pydantic calls it automatically while building `AgentDetail` values.

*Call graph*: 1 external calls (replace).


##### `ConnectionView._aware_utc`  (lines 859–860)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures connection timestamps are timezone-aware. This keeps the portal from receiving bare local-looking times.

**Data flow**: It receives a datetime and returns the same time with UTC added if no timezone is present.

**Call relations**: Pydantic runs it whenever `ConnectionView` is created.

*Call graph*: 1 external calls (replace).


##### `ConnectionPoolView._aware_utc`  (lines 886–887)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Ensures connection-pool timestamps include timezone information. It makes the listing safe to compare and display.

**Data flow**: It receives a datetime and returns it unchanged if aware, or tagged as UTC if naive.

**Call relations**: Pydantic applies it during `ConnectionPoolView` construction.

*Call graph*: 1 external calls (replace).


##### `_binding_fields`  (lines 946–974)

```
def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields
```

**Purpose**: Extracts the editable identity fields for a source binding from stored connector configuration. This lets portal actions submit a complete source identity instead of accidentally dropping hidden fields.

**Data flow**: It receives a backend name and config dictionary. It validates connector config; if valid, it returns binding name, stream, account, base URL, and backfill setting, otherwise `None` fields.

**Call relations**: `SurfaceContext.list_sources` calls it for each source row before building `SourceView`.

*Call graph*: called by 1 (list_sources); 2 external calls (model_validate, binding_name).


##### `SourceView._aware_utc`  (lines 1006–1007)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes the next-sync timestamp for source rows. It ensures UI code sees UTC-aware times.

**Data flow**: It receives a datetime and adds UTC if the timestamp has no timezone.

**Call relations**: Pydantic invokes it when `SourceView` objects are created.

*Call graph*: 1 external calls (replace).


##### `ConversationSummary._aware_utc`  (lines 1025–1028)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes conversation creation and last-turn timestamps. It also preserves missing last-turn values.

**Data flow**: It receives a datetime or `None`. It returns `None` unchanged, returns aware datetimes unchanged, and tags naive datetimes as UTC.

**Call relations**: Pydantic runs it for conversation summary objects built by directory and context listing methods.

*Call graph*: 1 external calls (replace).


##### `record_transcript_access`  (lines 1041–1102)

```
async def record_transcript_access(workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID) -> TranscriptAccess | None
```

**Purpose**: Records that an admin acknowledged reading another member’s private conversation. The record becomes the temporary permission gate and an audit trail.

**Data flow**: It receives workspace, conversation, agent, and reader ids. It verifies the conversation belongs to the agent and is another member’s private conversation, inserts an access row, logs the disclosure, and returns reader and subject emails or `None` if refused.

**Call relations**: Prepared-intent flows call it before serving private transcript content; `SurfaceContext.readable_conversation` later checks the recorded row.

*Call graph*: 9 external calls (__init__, now, insert, select, workspace_tx, log, audience_member, parse_audience, uuid4).


##### `ConversationDirectory.list`  (lines 1160–1284)

```
async def list(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'others'] | None=None, sea
```

**Purpose**: Lists conversations for one agent in the way the portal needs: newest first, permission-aware, and optionally filtered. It keeps all conversation listing behavior consistent.

**Data flow**: It receives agent, member, admin flag, limit, and optional filters. It queries conversation metadata, applies audience and search rules, fetches content-only extras for readable rows, and returns `ListedConversation` objects.

**Call relations**: It calls helper predicates for participation and search, then calls `sources` and `speakers` to enrich only conversations whose content the reader may see.

*Call graph*: calls 6 internal fn (_matches, _member_admitted, _others, _participated, sources, speakers); 8 external calls (__init__, __init__, select, workspace_tx, audience_member, conversation_audience, parse_audience, readable_audiences).


##### `ConversationDirectory.sources`  (lines 1286–1323)

```
async def sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]
```

**Purpose**: Finds the source link or origin reported by the first turn in each listed conversation. This lets a UI link back to the Slack thread or other surface origin.

**Data flow**: It receives conversation ids. It finds each conversation’s earliest turn, reads its stored context, extracts the source field, and returns a dictionary by conversation id.

**Call relations**: `ConversationDirectory.list`, `scheduled_runs`, and artifact listings use it to add origin links without duplicating the query.

*Call graph*: called by 1 (list); 4 external calls (model_validate, and_, select, workspace_tx).


##### `ConversationDirectory.speakers`  (lines 1325–1381)

```
async def speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]
```

**Purpose**: Finds the first few members who spoke in each conversation. It gives conversation lists human context without loading full transcripts.

**Data flow**: It receives conversation ids. It queries member turns, keeps each speaker’s first appearance, caps the count, and returns speaker email plus optional surface-reported sender name.

**Call relations**: `ConversationDirectory.list` calls it only for conversations whose content the reader may access.

*Call graph*: called by 1 (list); 4 external calls (__init__, model_validate, select, workspace_tx).


##### `ConversationDirectory._member_admitted`  (lines 1383–1396)

```
def _member_admitted(self) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition meaning a member’s own message ever opened a turn in the conversation. This separates real member conversations from machine-only lanes.

**Data flow**: It reads the surrounding conversation row and returns a SQL `exists` condition over turns with member admission.

**Call relations**: `ConversationDirectory.list` uses it when the caller asks for member-admitted conversations only.

*Call graph*: called by 1 (list); 2 external calls (literal, select).


##### `ConversationDirectory._spoken`  (lines 1398–1419)

```
def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition meaning someone, or a specific member, spoke in the conversation. It is optimized as a per-conversation existence check.

**Data flow**: It receives an optional member id. It returns a SQL condition that checks for a matching speaker turn in the current conversation.

**Call relations**: `_participated` and `_others` compose this helper to define participation filters.

*Call graph*: called by 2 (_others, _participated); 2 external calls (literal, select).


##### `ConversationDirectory._participated`  (lines 1421–1429)

```
def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations a member participated in. A member counts if the conversation is bound to them or if they spoke in it.

**Data flow**: It receives a member id and returns a SQL OR condition over conversation ownership and spoken turns.

**Call relations**: `ConversationDirectory.list` uses it for the `mine` participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 1 external calls (or_).


##### `ConversationDirectory._others`  (lines 1431–1443)

```
def _others(self, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds a query condition for conversations where other members spoke and this member did not participate. It supports the portal’s “others” rail.

**Data flow**: It receives a member id and returns a SQL condition excluding conversations bound to or spoken in by that member while requiring some member speech.

**Call relations**: `ConversationDirectory.list` uses it for the `others` participation filter.

*Call graph*: calls 1 internal fn (_spoken); called by 1 (list); 2 external calls (and_, not_).


##### `ConversationDirectory._matches`  (lines 1445–1474)

```
def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the search condition for conversation lists. It carefully avoids leaking private conversation content through search results.

**Data flow**: It receives search text and member id. It matches visible metadata for all listed rows, and matches titles or speaker emails only when the member can read the content.

**Call relations**: `ConversationDirectory.list` applies it before limiting results so search finds the right row rather than filtering an already-cut page.

*Call graph*: called by 1 (list); 5 external calls (and_, literal, or_, select, readable_audiences).


##### `LedgerEntry._aware_utc`  (lines 1488–1489)

```
def _aware_utc(cls, value: datetime) -> datetime
```

**Purpose**: Normalizes accounting timestamps to UTC-aware datetimes. This helps billing and debugging displays sort correctly.

**Data flow**: It receives a datetime and returns it with UTC timezone if it was missing one.

**Call relations**: Pydantic invokes it when `LedgerEntry` values are built in `SurfaceContext.turn_detail`.

*Call graph*: 1 external calls (replace).


##### `TurnStep._aware_utc`  (lines 1505–1508)

```
def _aware_utc(cls, value: datetime | None) -> datetime | None
```

**Purpose**: Normalizes workflow step timestamps while allowing missing start or completion times. This keeps step timelines consistent.

**Data flow**: It receives a datetime or `None`. It returns `None`, an already-aware datetime, or the same time marked as UTC.

**Call relations**: Pydantic applies it to `TurnStep` records returned by `TurnStepSource` implementations.

*Call graph*: 1 external calls (replace).


##### `_fulfilled_marker_key`  (lines 1564–1569)

```
def _fulfilled_marker_key(sealed: str, slot: str) -> str
```

**Purpose**: Creates the blob-store key used to remember that one credential prompt slot was fulfilled. It lets one slot stop prompting while another slot in the same request still asks.

**Data flow**: It receives a sealed request string and slot name. It hashes the seal, combines it with the slot, and returns a stable marker path.

**Call relations**: `SurfaceContext.credential_prompt_pending` checks this marker, and `SurfaceContext.fulfill_credential_request` writes it after storing a credential.

*Call graph*: called by 2 (credential_prompt_pending, fulfill_credential_request); 1 external calls (sha256).


##### `_main_agent`  (lines 1572–1585)

```
async def _main_agent(workspace_id: UUID) -> UUID
```

**Purpose**: Finds the workspace’s main agent. Surfaces use it as the fallback agent when no installation binding names one.

**Data flow**: It receives a workspace id, queries the agent table for the main row, and returns its id. If none exists, it raises an error because the workspace is malformed.

**Call relations**: `_bind_surface_installation` and `SurfaceContext._surface_agent` call it when they need the default agent.

*Call graph*: called by 2 (_surface_agent, _bind_surface_installation); 2 external calls (select, workspace_tx).


##### `_bind_surface_installation`  (lines 1588–1627)

```
async def _bind_surface_installation(workspace_id: UUID, surface: str, installation_id: str, *, routes_ingress: bool) -> None
```

**Purpose**: Creates or replaces the binding between a workspace and a surface installation, such as a Slack team. This is how incoming provider traffic later resolves to the right workspace.

**Data flow**: It receives workspace id, surface name, installation id, and whether the installation routes ingress. It validates the id, finds the main agent for new rows, upserts the binding, and converts uniqueness conflicts into `SurfaceInstallationConflict`.

**Call relations**: `SurfaceContext.bind_installation` uses it from surface OAuth callbacks, and `SurfaceInstallationAccess.bind` uses it from tool-driven installation flows.

*Call graph*: calls 1 internal fn (_main_agent); called by 2 (bind_installation, bind); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.fleet_blob`  (lines 1696–1699)

```
def fleet_blob(self) -> FleetBlobStore
```

**Purpose**: Returns a deploy-wide blob-store view for shared fleet assets. It is separate from the workspace-scoped blob view.

**Data flow**: It reads the backend from the workspace blob store and wraps it in a `FleetBlobStore`.

**Call relations**: Surface code can use this property when it needs static or shared assets rather than workspace data.

*Call graph*: 1 external calls (__init__).


##### `SurfaceContext.conversation_slots`  (lines 1702–1704)

```
def conversation_slots(self) -> tuple['BoundConversationSlot', ...]
```

**Purpose**: Returns the conversation-slot providers installed for this deploy. These slots are extension-provided extra panes or summaries tied to conversations.

**Data flow**: It reads the prevalidated tuple stored on the context and returns it unchanged.

**Call relations**: Web surface routes inspect this before calling the slot read and summarize methods.


##### `SurfaceContext.read_conversation_slot`  (lines 1706–1711)

```
async def read_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> 'ConversationSlotPayload'
```

**Purpose**: Runs one conversation-slot read under the conversation’s agent identity. Binding the agent makes provider code see the same namespace a turn would use.

**Data flow**: It receives a bound slot and slot context, enters the agent scope from the context, calls the provider’s read method, and returns its payload.

**Call relations**: The web surface’s conversation-slot route calls this after it has authorized the conversation.

*Call graph*: called by 1 (conversation_slot); 1 external calls (agent).


##### `SurfaceContext.summarize_conversation_slot`  (lines 1713–1718)

```
async def summarize_conversation_slot(self, bound: 'BoundConversationSlot', context: 'ConversationSlotContext') -> int | None
```

**Purpose**: Runs one conversation-slot summarization under the conversation’s agent identity. It lets slot providers produce compact counts or summaries for UI lists.

**Data flow**: It receives a bound slot and context, enters the relevant agent scope, calls the provider’s summarize method, and returns an integer summary or `None`.

**Call relations**: The web surface calls it while building conversation-slot displays.

*Call graph*: called by 1 (conversation_slots); 1 external calls (agent).


##### `SurfaceContext.deploy_extensions`  (lines 1721–1724)

```
def deploy_extensions(self) -> tuple[DeployExtensionView, ...]
```

**Purpose**: Returns the installed deploy extensions as administration-view data. It exposes names, versions, and sandbox-internet requirements, not secrets.

**Data flow**: It returns the tuple of `DeployExtensionView` objects stored on the context.

**Call relations**: Administration routes read it to show what the running deploy loaded.


##### `SurfaceContext.deploy_sandbox_internet`  (lines 1727–1730)

```
def deploy_sandbox_internet(self) -> bool
```

**Purpose**: Reports whether this deploy’s extensions allow sandbox public internet at all. Agent settings can only narrow this deploy-level ceiling.

**Data flow**: It returns the boolean configured on the context.

**Call relations**: Portal administration and agent detail views use it to explain internet-access options.


##### `SurfaceContext.deploy_skills`  (lines 1733–1738)

```
def deploy_skills(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Returns the deploy-provided skill index available to agents. It is the shared skill floor before member-authored skills are added.

**Data flow**: It asks the skill registry for its index and returns the resulting skill name/description pairs.

**Call relations**: Agent spawning and portal skill views use this deploy-level skill inventory.


##### `SurfaceContext.system_skill_bundle`  (lines 1741–1743)

```
def system_skill_bundle(self) -> SystemSkillBundle
```

**Purpose**: Returns the immutable bundle of system skills loaded at boot. A terminal can cache it before running a turn.

**Data flow**: It returns the stored `SystemSkillBundle` unchanged.

**Call relations**: Runtime code reads this through the context when preparing turn execution.


##### `SurfaceContext.models`  (lines 1746–1750)

```
def models(self) -> tuple[str, ...]
```

**Purpose**: Returns the model ids this deploy can serve. The portal uses this closed list when offering model choices.

**Data flow**: It returns the tuple of configured model names.

**Call relations**: Agent settings and surface-side model choices read this property.


##### `SurfaceContext.sandbox_sizes`  (lines 1753–1756)

```
def sandbox_sizes(self) -> tuple[str, ...]
```

**Purpose**: Returns the sandbox sizes supported by the deployment. An empty list means there is no user-facing size choice.

**Data flow**: It returns the stored tuple of size names.

**Call relations**: Agent settings use it to decide whether to show sandbox-size controls.


##### `SurfaceContext.credential`  (lines 1758–1761)

```
async def credential(self, slot: str) -> str
```

**Purpose**: Reads a workspace credential slot for trusted surface code. The value is returned in-process and is never exposed to ordinary scoped extensions.

**Data flow**: It receives a slot name, checks that a credential store exists, and returns the encrypted store’s value for this workspace and slot.

**Call relations**: Slack surface helpers and delivery functions call it for tokens and signing secrets.

*Call graph*: called by 8 (_bot_token, _channel_origin, _ctx_signing_secret, _post_ephemeral, _to_inbound, attach, post, speak).


##### `SurfaceContext.credential_prompt_pending`  (lines 1763–1777)

```
async def credential_prompt_pending(self, sealed: str, slot: str) -> bool
```

**Purpose**: Checks whether a sealed credential request still needs one slot filled. It prevents fulfilled, expired, or foreign prompts from being shown again.

**Data flow**: It receives a sealed request and slot. It opens and validates the seal, checks workspace and slot membership, looks for the fulfilled marker blob, and returns a boolean.

**Call relations**: The web surface calls it while deciding which credential prompts to render.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 1 (_pending_prompts); 1 external calls (open_credential_request).


##### `SurfaceContext.open_credential_authorization`  (lines 1779–1789)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState
```

**Purpose**: Opens a sealed credential handoff and returns its claims. Surface OAuth callbacks use this when there is no active turn to identify the member and slot.

**Data flow**: It receives a sealed string, verifies it with the credential store’s Fernet key and purpose, and returns the decoded request state or raises if invalid.

**Call relations**: Slack OAuth callback code calls it before fulfilling a credential slot.

*Call graph*: called by 1 (oauth_callback); 1 external calls (open_credential_request).


##### `SurfaceContext.fulfill_credential_request`  (lines 1791–1816)

```
async def fulfill_credential_request(self, sealed: str, slot: str, value: str, member_id: UUID | None) -> None
```

**Purpose**: Stores a credential value only if the sealed request, slot, workspace, and member all match. This protects credential handoffs from tampering or cross-member use.

**Data flow**: It receives seal, slot, value, and member id. It validates the seal, checks workspace and member, writes the credential, and writes the fulfilled marker blob.

**Call relations**: Slack, web, and internal UFO surfaces call it after a member completes a credential prompt or provider callback.

*Call graph*: calls 1 internal fn (_fulfilled_marker_key); called by 3 (oauth_callback, _fulfill_secret, fulfill_credential); 4 external calls (__init__, now, dumps, open_credential_request).


##### `SurfaceContext.bind_installation`  (lines 1818–1826)

```
async def bind_installation(self, installation_id: str) -> None
```

**Purpose**: Binds this surface’s external installation identity to the current workspace. It is commonly used after OAuth installation completes.

**Data flow**: It receives an installation id and calls the shared binding helper with this workspace and surface, marking it as ingress-routing.

**Call relations**: Slack’s OAuth callback calls this so future Slack events can resolve their workspace.

*Call graph*: calls 1 internal fn (_bind_surface_installation); called by 1 (oauth_callback).


##### `SurfaceContext.address_claim`  (lines 1828–1853)

```
async def address_claim(self, address: str) -> AddressClaim | None
```

**Purpose**: Reads this workspace’s claim on an addressed surface identity, such as a phone number. It tells whether the address is still only reserved or already proved.

**Data flow**: It receives an address, queries the surface-address table for this surface and workspace, normalizes expiry time, and returns an `AddressClaim` or `None`.

**Call relations**: The iMessage surface checks this before admitting messages from an addressed sender.

*Call graph*: called by 1 (_admit_message); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.confirm_address`  (lines 1855–1868)

```
async def confirm_address(self, address: str, proved_by: str) -> None
```

**Purpose**: Marks a reserved addressed-surface identity as proved. The proving inbound message becomes the proof id so replayed events do not also create turns.

**Data flow**: It receives an address and proof id, then updates the matching surface-address row to clear expiry and store the proof.

**Call relations**: The iMessage surface calls it when a sender proves ownership of an address.

*Call graph*: called by 1 (_prove); 2 external calls (update, workspace_tx).


##### `SurfaceContext.release_address`  (lines 1870–1879)

```
async def release_address(self, address: str) -> None
```

**Purpose**: Removes this workspace’s claim on an addressed-surface identity. After that, the address can be claimed again.

**Data flow**: It receives an address and deletes the matching surface-address row for this workspace and surface.

**Call relations**: The iMessage proof flow can call it when a claim should be dropped.

*Call graph*: called by 1 (_prove); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.public_base_url`  (lines 1882–1885)

```
def public_base_url(self) -> str | None
```

**Purpose**: Returns the deployment’s public base URL if one is configured. Surfaces use it to build callback and portal links.

**Data flow**: It returns the stored public base URL or `None`.

**Call relations**: Surface route and message-rendering code reads it when it needs an externally visible URL.


##### `SurfaceContext.cookie_secure`  (lines 1888–1892)

```
def cookie_secure(self) -> bool
```

**Purpose**: Decides whether session cookies should be marked Secure. Secure cookies are only appropriate when the public base uses HTTPS-like schemes.

**Data flow**: It parses the configured public base URL, passes the scheme to the cookie helper, and returns a boolean.

**Call relations**: Browser surfaces use this when setting session cookies.

*Call graph*: 2 external calls (cookie_secure, urlsplit).


##### `SurfaceContext.home_url`  (lines 1894–1904)

```
def home_url(self, fragment: str='') -> str | None
```

**Purpose**: Builds a link to the deployment’s browser home surface. Durable surfaces use it when they need to send a member to the portal for an action they cannot perform inline.

**Data flow**: It receives an optional fragment. If public base URL and home surface are configured, it returns `/surface/<home>` plus the fragment; otherwise it returns `None`.

**Call relations**: iMessage, Slack, and sites surfaces call it when rendering portal links.

*Call graph*: called by 3 (_terminal_text, _into_the_portal, _reply_with_oversize_links).


##### `SurfaceContext.shared_artifacts`  (lines 1906–1944)

```
async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]
```

**Purpose**: Reads the files shared by one turn. Live surfaces use this directly, while durable surfaces receive the same information through writeback.

**Data flow**: It receives a turn id, queries shared-artifact rows for this workspace and turn, and returns `SharedArtifact` objects in share order.

**Call relations**: Web and UFO surfaces call it to render file lists and links.

*Call graph*: called by 2 (shared_files, _events); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.artifact_link`  (lines 1946–1954)

```
def artifact_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a temporary download link for a shared artifact in this workspace. It hides signing details from surface code.

**Data flow**: It receives a `SharedArtifact`, combines it with context signing settings and workspace id, and returns a URL or `None`.

**Call relations**: It delegates to `shared_artifact_link`; Slack, iMessage, web, and UFO surfaces use it when rendering files.

*Call graph*: calls 1 internal fn (shared_artifact_link); called by 5 (_terminal_text, _oversize_link_line, shared_files, _file_payload, _project_slot_context).


##### `SurfaceContext.artifact_preview_link`  (lines 1956–1966)

```
def artifact_preview_link(self, artifact: SharedArtifact) -> str | None
```

**Purpose**: Creates a safe preview-image URL for an artifact when possible. It returns nothing if preview delivery is not configured or the file is not eligible.

**Data flow**: It receives a `SharedArtifact`, applies context signing settings and workspace id, and returns a signed preview URL or `None`.

**Call relations**: It delegates to `shared_artifact_preview_link`; web file-card and slot-context code uses it.

*Call graph*: calls 1 internal fn (shared_artifact_preview_link); called by 2 (_file_payload, _project_slot_context).


##### `SurfaceContext.ingress_url`  (lines 1968–2014)

```
def ingress_url(self, conversation_id: UUID, port: int, entry_path: str, *, framed_from: str | None=None, shipped_slug: str | None=None, shipped_digest: str | None=None) -> str | None
```

**Purpose**: Mints a signed browser URL into a conversation’s sandbox port. This lets surfaces expose sandbox-hosted sites without giving them deployment secrets.

**Data flow**: It receives conversation, port, entry path, and optional framing or shipped-app details. It passes them with workspace identity to the ingress URL signer and returns a URL or `None` if ingress is unavailable.

**Call relations**: The sites surface calls it when serving app frames and shipped app bundles.

*Call graph*: called by 2 (_shipped_frame, frame); 1 external calls (mint_ingress_view_url).


##### `SurfaceContext._identity_member`  (lines 2016–2029)

```
async def _identity_member(self, surface: str, external_id: str) -> UUID | None
```

**Purpose**: Looks up which member a surface-specific external id is linked to. It is the private shared lookup behind several identity methods.

**Data flow**: It receives a surface name and external id, queries the surface-identity table in this workspace, and returns a member id or `None`.

**Call relations**: `linked_member` uses it for this surface, while `adopt_identity` uses it for a peer surface.

*Call graph*: called by 2 (adopt_identity, linked_member); 2 external calls (select, workspace_tx).


##### `SurfaceContext.linked_member`  (lines 2031–2032)

```
async def linked_member(self, external_id: str) -> UUID | None
```

**Purpose**: Finds the member already linked to this surface’s external user id. It lets a surface recognize returning users.

**Data flow**: It receives an external id and returns the linked member id by calling `_identity_member` for the current surface.

**Call relations**: Sample, sites, Slack, UFO, and web surfaces call it during authentication or inbound-message resolution.

*Call graph*: calls 1 internal fn (_identity_member); called by 8 (_surface_ingest, _surface_live_admit, _viewer, _resolve_member, interactive, channel, op_body, _authenticate).


##### `SurfaceContext.is_operator_workspace`  (lines 2034–2041)

```
async def is_operator_workspace(self) -> bool
```

**Purpose**: Checks whether the current workspace is the deployment operator’s own workspace. This gates internal-only rendering such as debug footers.

**Data flow**: It reads the workspace email domain and compares it to the operator domain constant.

**Call relations**: Surface rendering code can call it before showing operator-only details.

*Call graph*: calls 1 internal fn (workspace_domain).


##### `SurfaceContext.adopt_identity`  (lines 2043–2066)

```
async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to the member already known by another surface. This lets the same human keep one member identity across surfaces.

**Data flow**: It receives a peer surface and external id, looks up the peer identity, inserts this surface identity if found, logs races, and returns the member id or `None`.

**Call relations**: Sample live-admit code calls it when one surface wants to reuse another surface’s established identity.

*Call graph*: calls 1 internal fn (_identity_member); called by 1 (_surface_live_admit); 3 external calls (insert, workspace_tx, log).


##### `SurfaceContext.link_member`  (lines 2068–2090)

```
async def link_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links this surface’s external id to an existing workspace member by email. If no member has that email, it leaves the identity unlinked.

**Data flow**: It receives external id and email, case-insensitively finds the oldest matching member, then calls `link_member_id` and returns the id or `None`.

**Call relations**: Authentication and ingest paths call it; `join_member` uses it before deciding whether to create a new member.

*Call graph*: calls 1 internal fn (link_member_id); called by 5 (join_member, _surface_ingest, _viewer, channel, _authenticate); 2 external calls (select, workspace_tx).


##### `SurfaceContext.link_member_id`  (lines 2092–2121)

```
async def link_member_id(self, external_id: str, member_id: UUID) -> UUID | None
```

**Purpose**: Links this surface’s external id to a specific existing member id. It is used after the surface has proved that member requested the link.

**Data flow**: It receives external id and member id, verifies the member belongs to the workspace, inserts a surface identity, logs duplicate-insert races, and returns the member id or `None`.

**Call relations**: `link_member` delegates to it after resolving email to member id.

*Call graph*: called by 1 (link_member); 4 external calls (insert, select, workspace_tx, log).


##### `SurfaceContext.join_member`  (lines 2123–2140)

```
async def join_member(self, external_id: str, email: str) -> UUID | None
```

**Purpose**: Links a surface identity by email, creating a new member when the email domain matches the workspace’s own domain. This supports first-contact teammate joining through verified channels.

**Data flow**: It receives external id and verified email. It first tries `link_member`; if absent, it compares email domain to workspace domain, creates a member if allowed, and links again.

**Call relations**: Slack member-resolution calls it when Slack has verified the user’s email.

*Call graph*: calls 2 internal fn (link_member, workspace_domain); called by 1 (_resolve_member); 3 external calls (workspace_tx, create_member, email_domain).


##### `SurfaceContext._conversation_lookup`  (lines 2142–2152)

```
def _conversation_lookup(self, queue_key: str) -> sa.Select
```

**Purpose**: Builds the common query for finding a conversation by this surface’s queue key. Queue keys are surface-defined identifiers such as channel/thread ids.

**Data flow**: It receives a queue key and returns a SQL select for the matching conversation row in this workspace and surface.

**Call relations**: `conversation_for`, `find_conversation`, and `terminal_op_body` reuse it so they resolve surface conversations consistently.

*Call graph*: called by 3 (conversation_for, find_conversation, terminal_op_body); 1 external calls (select).


##### `SurfaceContext.find_conversation`  (lines 2154–2160)

```
async def find_conversation(self, queue_key: str) -> UUID | None
```

**Purpose**: Finds an existing conversation for this surface queue key without creating one. This is useful when a surface needs to know whether the agent is already participating.

**Data flow**: It receives a queue key, runs `_conversation_lookup`, and returns the conversation id or `None`.

**Call relations**: Slack participation and interactive handlers call it before deciding whether to admit or route an action.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 2 (_participating_conversation, interactive); 1 external calls (workspace_tx).


##### `SurfaceContext.conversation_agent`  (lines 2162–2175)

```
async def conversation_agent(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the agent permanently bound to a conversation. It helps routes enforce the agent wall before reading content.

**Data flow**: It receives a conversation id, queries this workspace’s conversation table, and returns the agent id or `None`.

**Call relations**: The web surface calls it while resolving chat URLs and permissions.

*Call graph*: called by 1 (_resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.retitle_conversation`  (lines 2177–2180)

```
async def retitle_conversation(self, conversation_id: UUID, title: str) -> None
```

**Purpose**: Renames a conversation in this workspace through the context. It is the surface-facing wrapper around the shared title update helper.

**Data flow**: It receives a conversation id and title, then calls the module-level `retitle_conversation` with this workspace id.

**Call relations**: Slack and web flows call it when a conversation gains a better displayed name.

*Call graph*: calls 1 internal fn (retitle_conversation); called by 3 (_admit_inbound, submit_intent, _open_conversation).


##### `SurfaceContext.conversation_for`  (lines 2182–2276)

```
async def conversation_for(self, queue_key: str, audience: Audience, agent_id: UUID | None=None, conversation_id: UUID | None=None, label: str | None=None) -> UUID
```

**Purpose**: Gets or creates the conversation represented by a surface queue key. It also narrows the conversation’s audience when the surface learns more precise membership.

**Data flow**: It receives queue key, audience, optional agent, optional desired conversation id, and optional label. It finds an existing row, narrows audience or updates label if needed, or inserts a new conversation bound to an agent.

**Call relations**: All main surface ingest paths call it before admitting a turn, including Slack, iMessage, web, sample, and UFO surfaces.

*Call graph*: calls 2 internal fn (_conversation_lookup, _surface_agent); called by 9 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, interactive, channel, submit_intent, _open_conversation, object_write); 9 external calls (insert, select, update, workspace_tx, log, audience_member, narrow_audience, parse_audience, uuid4).


##### `SurfaceContext._surface_agent`  (lines 2278–2290)

```
async def _surface_agent(self) -> UUID
```

**Purpose**: Finds the agent a new conversation for this surface should use. It prefers the surface installation binding and falls back to the workspace’s main agent.

**Data flow**: It queries the surface-installation row for this workspace and surface. If found it returns that agent id; otherwise it calls `_main_agent`.

**Call relations**: `conversation_for` calls it when the caller did not explicitly choose an agent.

*Call graph*: calls 1 internal fn (_main_agent); called by 1 (conversation_for); 2 external calls (select, workspace_tx).


##### `SurfaceContext.ambient_reply_wanted`  (lines 2292–2323)

```
async def ambient_reply_wanted(self, message: AmbientMessage, history: tuple[AmbientMessage, ...]) -> bool
```

**Purpose**: Asks the ambient-reply classifier whether the agent should answer an unaddressed message. It fails open so uncertain classifier failures do not silently drop possible member requests.

**Data flow**: It receives the current ambient message and recent history, runs the classifier with a short timeout, logs the decision or warning, and returns true unless the classifier clearly says no reply.

**Call relations**: Slack and iMessage surfaces call it before admitting ambient channel traffic.

*Call graph*: called by 2 (_admit_message, _ambient_reply_wanted); 3 external calls (wait_for, log, warn).


##### `SurfaceContext.admit`  (lines 2325–2358)

```
async def admit(self, conversation_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None, comment:
```

**Purpose**: Admits a member message, comment, or prepared intent into the durable turn queue. It is the main surface write path into core.

**Data flow**: It receives conversation id, body, optional idempotency key, context, speaker id, optional intent, and optional comment. It forwards them to the injected `MemberAdmitter` and returns the resulting `Admitted` record.

**Call relations**: All message-ingest and portal-submit surfaces call it; the underlying admitter owns the database locking and turn-opening rules.

*Call graph*: called by 10 (_admit_message, _surface_ingest, _surface_live_admit, _admit_inbound, interactive, _send, channel, submit_intent, chat, object_write).


##### `SurfaceContext.connect_url`  (lines 2360–2366)

```
async def connect_url(self, turn_id: UUID, member_id: UUID) -> str
```

**Purpose**: Creates a member-specific provider-connect authorization URL for a terminal connect request. It converts missing connect configuration into a request error.

**Data flow**: It receives turn and member ids, obtains the installed connect flow, builds a handoff, and returns its authorization URL.

**Call relations**: Slack, iMessage, and web surfaces call it when rendering connect controls.

*Call graph*: called by 3 (_terminal_text, interactive, connect_handoff); 3 external calls (__init__, __init__, installed_connect_flow).


##### `SurfaceContext.held_accounts`  (lines 2368–2388)

```
async def held_accounts(self, owner_member_id: UUID) -> dict[str, str]
```

**Purpose**: Lists the latest connected account labels held by one member, grouped by provider. It helps UI controls show what the member has already connected.

**Data flow**: It receives owner member id, queries that member’s connections ordered by update time, and returns provider-to-label mapping.

**Call relations**: The web surface uses it while building connect controls.

*Call graph*: called by 1 (_connect_controls); 2 external calls (select, workspace_tx).


##### `SurfaceContext.connect_available`  (lines 2390–2397)

```
def connect_available(self) -> bool
```

**Purpose**: Reports whether the deployment has provider-connect machinery configured. It lets surfaces hide buttons that would lead nowhere.

**Data flow**: It tries to load the installed connect flow and returns false if it is unavailable, true otherwise.

**Call relations**: Web connect-control and event-rendering paths call it before showing provider actions.

*Call graph*: called by 3 (_connect_controls, _events, _provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connect_label`  (lines 2399–2401)

```
def connect_label(self, provider: str) -> str
```

**Purpose**: Returns the human-facing label for a connect provider. This keeps labels consistent with the configured connect flow.

**Data flow**: It receives a provider id, asks the installed connect flow for its label, and returns it.

**Call relations**: The web surface calls it when displaying provider names.

*Call graph*: called by 1 (_provider_label); 1 external calls (installed_connect_flow).


##### `SurfaceContext.connector_catalog`  (lines 2403–2405)

```
async def connector_catalog(self, query: str, limit: int, after: str | None) -> CatalogPage
```

**Purpose**: Reads one page of connectable providers from the connector registry. It supports search and pagination in the portal.

**Data flow**: It receives query text, limit, and cursor, passes them to the connector registry, and returns a catalog page.

**Call relations**: The web connector-catalog route calls it directly.

*Call graph*: called by 1 (connector_catalog).


##### `SurfaceContext.admitted_body`  (lines 2407–2432)

```
async def admitted_body(self, idempotency_key: str) -> str | None
```

**Purpose**: Finds the body that was admitted under an idempotency key. It lets a surface confirm which of several racing clicks or retries actually landed.

**Data flow**: It receives an idempotency key, checks turn rows first and inbound-message rows second, and returns the stored body or `None`.

**Call relations**: Slack and web handlers call it after interactive or chat submissions.

*Call graph*: called by 3 (_unseen_tail, interactive, chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.turn_owner`  (lines 2434–2448)

```
async def turn_owner(self, turn_id: UUID) -> UUID | None
```

**Purpose**: Finds the member who owns the conversation containing a turn. Live surfaces use this to stop one member from tailing another member’s turn.

**Data flow**: It receives a turn id, joins turn to conversation in this workspace, and returns the conversation member id or `None`.

**Call relations**: Sample and web live-turn code call it before opening a stream.

*Call graph*: called by 2 (_surface_live_admit, _member_turn); 2 external calls (select, workspace_tx).


##### `SurfaceContext.stop_turn`  (lines 2450–2456)

```
async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Asks core to stop a running turn in an authorized conversation. It reports whether the call actually ended the turn and whether a follow-up turn began.

**Data flow**: It receives conversation and turn ids, adds the context workspace id, delegates to the injected `TurnStopper`, and returns `Stopped`.

**Call relations**: UFO and web chat surfaces call it for user stop requests.

*Call graph*: called by 2 (channel, chat).


##### `SurfaceContext.retract_arrival`  (lines 2458–2476)

```
async def retract_arrival(self, conversation_id: UUID, arrival_id: UUID, member_id: UUID) -> bool
```

**Purpose**: Deletes a pending message that a member sent but no turn has consumed yet. It only retracts that member’s own unconsumed message.

**Data flow**: It receives conversation, arrival, and member ids. It deletes a matching unconsumed inbound-message row and returns true only if one row was removed.

**Call relations**: The UFO surface calls it for unsend behavior.

*Call graph*: called by 1 (_unsend); 2 external calls (delete, workspace_tx).


##### `SurfaceContext.turn_is_terminal`  (lines 2478–2495)

```
async def turn_is_terminal(self, turn_id: UUID) -> bool
```

**Purpose**: Checks whether a turn has ended by reading durable state rather than live hub state. This prevents progress notices from appearing after the final answer.

**Data flow**: It receives a turn id, reads its status, and returns true if missing or in a terminal status.

**Call relations**: The UFO surface calls it before posting side-channel updates.

*Call graph*: called by 1 (channel); 2 external calls (select, workspace_tx).


##### `SurfaceContext.latest_turn`  (lines 2497–2514)

```
async def latest_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Returns the most recently admitted turn in a conversation. This helps reconnecting surfaces resume the right stream or render the latest handoffs.

**Data flow**: It receives a conversation id, queries turns in descending sequence order, and returns the newest turn id or `None`.

**Call relations**: Slack, UFO, and web surfaces call it during conversation resolution and reloads.

*Call graph*: called by 4 (_participating_conversation, channel, _conversation_messages, _resolve_chat); 2 external calls (select, workspace_tx).


##### `SurfaceContext.absorbing_turn`  (lines 2516–2560)

```
async def absorbing_turn(self, conversation_id: UUID) -> UUID | None
```

**Purpose**: Predicts whether a new message would fold into an existing live turn. It includes spend and balance gates so the prediction matches admission behavior.

**Data flow**: It receives a conversation id, finds the oldest non-terminal turn, rejects parked turns, evaluates spend cap and balance, and returns the live turn id only if admission would be allowed.

**Call relations**: Slack calls it before deciding whether ambient reply classification applies.

*Call graph*: called by 1 (_folds_into_live_turn); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.tail`  (lines 2562–2568)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Opens a live stream of frames for a turn. It is the surface-facing route to the hub without exposing the hub directly.

**Data flow**: It receives a turn id and optional cursor, delegates to the injected tailer, and returns an async context manager that yields live frames.

**Call relations**: Debugger, sample, UFO, and web surfaces call it for server-sent events and live updates.

*Call graph*: called by 6 (_events, _surface_frames, channel, submit_intent, _events, object_write).


##### `SurfaceContext.latest_activity`  (lines 2570–2574)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Peeks at the newest retained live activity for a turn. It is cheaper than opening a full tail.

**Data flow**: It receives a turn id, delegates to the injected tailer, and returns an activity frame or `None`.

**Call relations**: The web agents-status route calls it after `agent_turn_statuses` identifies running turns.

*Call graph*: called by 1 (agents_status).


##### `SurfaceContext.spend_rollup`  (lines 2576–2579)

```
async def spend_rollup(self, window_seconds: int | None) -> SpendReport
```

**Purpose**: Reads workspace-wide usage and cost over a selected time window. This supports billing and usage views.

**Data flow**: It receives an optional window length, opens a workspace transaction, asks `SpendRollup` to read totals, and returns a spend report.

**Call relations**: Sample and web workspace-usage routes call it.

*Call graph*: called by 2 (_surface_live_admit, workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.write_workspace_file`  (lines 2581–2596)

```
async def write_workspace_file(self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]) -> None
```

**Purpose**: Writes an uploaded or downloaded file into a conversation’s workspace before the turn runs. It enforces a maximum size while reading the stream.

**Data flow**: It receives conversation id, relative path, and byte chunks. It accumulates chunks up to the limit, raises if too large, and writes the final bytes through the sandbox carrier.

**Call relations**: Slack, iMessage, sample, and web upload paths call it before admitting the message that references the file.

*Call graph*: called by 4 (_downloaded_files, _surface_ingest, _download_files, _deliver_uploads).


##### `SurfaceContext.render_preview`  (lines 2598–2626)

```
async def render_preview(self, kind: str, data: bytes) -> bytes | None
```

**Purpose**: Asks an external preview service to render an uploaded document into a PNG thumbnail. If previewing is unavailable or fails, the surface can still show a plain file card.

**Data flow**: It receives a kind and file bytes. If preview service settings exist, it posts the file and render request, checks for a PNG response, and returns image bytes or `None`.

**Call relations**: The web preview route calls it while a member is composing an upload.

*Call graph*: called by 1 (preview); 2 external calls (AsyncClient, dumps).


##### `SurfaceContext.list_agents`  (lines 2628–2667)

```
async def list_agents(self) -> tuple[AgentSummary, ...]
```

**Purpose**: Lists active agents in the workspace, main agent first. Surfaces use it for agent pickers and portal views.

**Data flow**: It queries non-archived agent rows for this workspace, orders them, and returns `AgentSummary` objects.

**Call relations**: Sites and web surfaces call it for frames, audience decisions, and app lists.

*Call graph*: called by 5 (_shipped_frame, frame, web_audience, _created_apps, _subagent_nodes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_archived_agents`  (lines 2669–2698)

```
async def list_archived_agents(self) -> tuple[ArchivedAgent, ...]
```

**Purpose**: Lists archived agents so the portal can offer restore or history views. It uses archived display names when present.

**Data flow**: It queries archived agent rows, orders most recently archived first, and returns `ArchivedAgent` objects.

**Call relations**: The web agents index calls it.

*Call graph*: called by 1 (agents_index); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.member_extension_agent_ids`  (lines 2700–2715)

```
async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]
```

**Purpose**: Finds agents that have private extension conversations for a member. This helps web audience logic decide what agents the member can see through extension activity.

**Data flow**: It receives a member id, queries distinct agent ids from private extension-surface conversations for that member, and returns a frozen set.

**Call relations**: The web audience helper calls it while computing visible agents.

*Call graph*: called by 1 (web_audience); 3 external calls (select, workspace_tx, conversation_audience).


##### `SurfaceContext.agent_detail`  (lines 2717–2778)

```
async def agent_detail(self, agent_id: UUID, member_id: UUID) -> AgentDetail | None
```

**Purpose**: Reads one agent’s full settings for the portal. It includes prompt digest, bound surfaces, and setup work still missing for the requesting member.

**Data flow**: It receives agent and member ids, queries the agent row and installation surfaces, reads pending setup, and returns `AgentDetail` or `None`.

**Call relations**: Web panel routes call it for agent settings and submit flows.

*Call graph*: called by 2 (agent_settings, submit_intent); 5 external calls (__init__, select, workspace_tx, pending_setup, prompt_digest).


##### `SurfaceContext.object_kind`  (lines 2780–2792)

```
def object_kind(self, kind: str) -> 'PortalKind | None'
```

**Purpose**: Returns portal metadata for a registered object kind. This tells the UI which fields can be filtered or ordered and which schema to render.

**Data flow**: It receives a kind name, looks it up in the bound object registry, and returns a `PortalKind` or `None`.

**Call relations**: Web object routes call it before listing, showing, or writing object records.

*Call graph*: called by 2 (_object_gate, object_write); 1 external calls (__init__).


##### `SurfaceContext.agent_skills`  (lines 2794–2833)

```
async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]
```

**Purpose**: Lists deploy-provided and member-authored skills available to an agent. Member skills that try to shadow deploy skills are skipped.

**Data flow**: It receives an agent id, reads member skills inside that agent scope, filters shadowed names, and returns `PortalSkill` objects for deploy then member skills.

**Call relations**: The web skills route calls it to render skill inventory.

*Call graph*: called by 1 (skills); 3 external calls (__init__, agent, log).


##### `SurfaceContext.model`  (lines 2836–2840)

```
def model(self) -> 'SurfaceModel | None'
```

**Purpose**: Returns the optional model access object available to surface routes. Surfaces must handle `None` because a deploy may omit it.

**Data flow**: It returns the stored `SurfaceModel` or `None`.

**Call relations**: Surface handlers use this property before making surface-side model calls.


##### `SurfaceContext.memory_available`  (lines 2843–2847)

```
def memory_available(self) -> bool
```

**Purpose**: Reports whether a memory-search provider is installed. UI code uses this to hide memory features on deployments without memory.

**Data flow**: It checks whether the context has a memory provider and returns a boolean.

**Call relations**: Web memory routes gate calls to `search_memory`, `recent_memory`, and memory metadata with this property.


##### `SurfaceContext.search_memory`  (lines 2849–2859)

```
async def search_memory(self, reader: 'SourceReader', queries: tuple[str, ...]) -> 'tuple[MemoryMatch, ...]'
```

**Purpose**: Searches memory items visible to a source reader. It uses the same reader shape as turn tools so portal and agent reads match.

**Data flow**: It receives a source reader and queries. It raises if no memory provider exists, otherwise delegates search and returns matches.

**Call relations**: The web workspace-memory route calls it after checking `memory_available`.

*Call graph*: called by 1 (workspace_memory).


##### `SurfaceContext.recent_memory`  (lines 2861–2874)

```
async def recent_memory(self, subjects: frozenset[str], limit: int, kinds: 'frozenset[str] | None'=None, cursor: 'ListingCursor | None'=None) -> 'ListingPage[MemoryMatch]'
```

**Purpose**: Lists recent memory items for readable subjects without a search query. It supports paged browsing and optional kind filtering.

**Data flow**: It receives subjects, limit, optional kinds, and optional cursor. It raises if memory is unavailable, otherwise delegates to the provider and returns a listing page.

**Call relations**: Web memory and recall views call it after gating on memory availability.

*Call graph*: called by 2 (_recalled, workspace_memory).


##### `SurfaceContext.memory_kinds`  (lines 2877–2882)

```
def memory_kinds(self) -> tuple[str, ...]
```

**Purpose**: Returns the memory item kinds that the installed provider can list. This supplies the portal’s filter choices.

**Data flow**: It raises if no memory provider exists, otherwise returns the provider’s listable kinds.

**Call relations**: Web memory UI reads it alongside recent-memory pages.


##### `SurfaceContext.memory_body_max_chars`  (lines 2885–2891)

```
def memory_body_max_chars(self) -> int
```

**Purpose**: Returns the maximum body length the memory provider stores. Forms use it to stop users before submission rather than after.

**Data flow**: It raises if memory is unavailable, otherwise returns the provider’s body-length limit.

**Call relations**: Portal memory forms read it when enforcing client-side limits.


##### `SurfaceContext.agent_spend`  (lines 2893–2898)

```
async def agent_spend(self, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport
```

**Purpose**: Reads usage and caps for one agent over a time window. It supports agent-level billing views.

**Data flow**: It receives agent id and optional window, opens a transaction, asks `SpendRollup` for the agent report, and returns it.

**Call relations**: Administration or agent usage surfaces can call it when showing per-agent costs.

*Call graph*: 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.member_spend`  (lines 2900–2905)

```
async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport
```

**Purpose**: Reads usage and caps for one member over a time window. It supports member-level billing views.

**Data flow**: It receives member id and optional window, opens a transaction, asks `SpendRollup` for the member report, and returns it.

**Call relations**: The web workspace-usage route calls it for the signed-in member’s usage.

*Call graph*: called by 1 (workspace_usage); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_agent_connections`  (lines 2907–2959)

```
async def list_agent_connections(self, agent_id: UUID, member_id: UUID, *, admin: bool) -> tuple[ConnectionView, ...]
```

**Purpose**: Lists connector accounts granted to one agent that the viewer may see. It respects private versus shared grants.

**Data flow**: It receives agent id, member id, and admin flag. It queries grants joined to connections and owners, filters visibility for non-admins, and returns `ConnectionView` objects.

**Call relations**: The web connections route calls it for an agent’s connection panel.

*Call graph*: called by 1 (connections); 5 external calls (__init__, or_, select, account_object_name, workspace_tx).


##### `SurfaceContext.list_connections`  (lines 2961–3043)

```
async def list_connections(self, member_id: UUID, *, admin: bool) -> tuple[ConnectionPoolView, ...]
```

**Purpose**: Lists workspace connection accounts and the live agents attached to each. It is the connection-library view.

**Data flow**: It receives member id and admin flag, queries connections with visible owners and non-archived attached agents, groups rows by account, and returns `ConnectionPoolView` objects.

**Call relations**: The web provider and connection-pool routes call it.

*Call graph*: called by 2 (_held_providers, connection_pool); 7 external calls (__init__, __init__, and_, or_, select, account_object_name, workspace_tx).


##### `SurfaceContext.github_coverage`  (lines 3045–3093)

```
async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView
```

**Purpose**: Reports whether GitHub is configured through API connections, git-push credentials, and sources. It helps onboarding show what remains to set up.

**Data flow**: It receives member id and admin flag, checks visible GitHub connections, visible GitHub sources, and filled GitHub credential slots, then returns booleans.

**Call relations**: Web first-run, held-provider, and GitHub-coverage routes call it.

*Call graph*: called by 3 (_held_providers, github_coverage, workspace_first_run); 6 external calls (__init__, exists, or_, select, true, workspace_tx).


##### `SurfaceContext.recent_object_changes`  (lines 3095–3134)

```
async def recent_object_changes(self, limit: int) -> tuple[ObjectChange, ...]
```

**Purpose**: Reads the newest object-change audit records in the workspace. It is meant for admin audit screens.

**Data flow**: It receives a limit, queries object-change rows newest first, normalizes timestamps, and returns `ObjectChange` records.

**Call relations**: The web object-changes route calls it after doing its own admin gate.

*Call graph*: called by 1 (object_changes); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversation_artifacts`  (lines 3136–3205)

```
async def list_conversation_artifacts(self, conversation_id: UUID, *, limit: int) -> tuple[ListedArtifact, ...]
```

**Purpose**: Lists the newest files shared in one conversation. Authorization is expected to happen before this read.

**Data flow**: It receives conversation id and limit, queries shared artifacts joined to turn and conversation metadata, resolves the conversation source, and returns `ListedArtifact` objects.

**Call relations**: Web transcript-aid and slot-context code calls it when building conversation file views.

*Call graph*: called by 2 (_project_slot_context, _transcript_aids); 5 external calls (__init__, __init__, __init__, select, workspace_tx).


##### `SurfaceContext.agent_turn_statuses`  (lines 3207–3335)

```
async def agent_turn_statuses(self, agent_ids: Sequence[UUID], member_id: UUID) -> tuple[AgentTurnStatus, ...]
```

**Purpose**: Summarizes live and recent turn status for several agents, limited to conversations the member can read. It powers polling status cards.

**Data flow**: It receives agent ids and member id. It finds each agent’s liveest non-terminal readable turn, newest readable activity time, and whether the latest terminal readable turn failed, then returns statuses in input order.

**Call relations**: The web agents-status route calls it, then may use `latest_activity` for running turns.

*Call graph*: called by 1 (agents_status); 5 external calls (__init__, case, select, workspace_tx, readable_audiences).


##### `SurfaceContext.agent_setup`  (lines 3337–3376)

```
async def agent_setup(self, agent_id: UUID, member_id: UUID) -> SetupState
```

**Purpose**: Computes what an agent still needs before it is ready, such as accounts, credentials, or standing orders. It is member-aware for private grants.

**Data flow**: It receives agent and member ids, defines an `armed` helper for extension-owned orders, enters workspace scope, and asks setup-state logic for the result.

**Call relations**: Web agent-setup and workspace-starters routes call it; its nested `armed` helper reads objects through this same context.

*Call graph*: called by 2 (agent_setup, workspace_starters); 2 external calls (setup_state, ws).


##### `SurfaceContext.agent_setup.armed`  (lines 3359–3373)

```
async def armed(kind: str, name: str | None) -> ArmedOrder
```

**Purpose**: Checks whether a required standing order exists for an agent setup step. It can check either one named object or whether any objects of a kind exist.

**Data flow**: It receives an object kind and optional name. It reads that exact object or a page of objects, extracts a schedule when present, and returns an `ArmedOrder`.

**Call relations**: `SurfaceContext.agent_setup` passes this helper into setup-state logic.

*Call graph*: calls 2 internal fn (list_member_objects, member_object); 2 external calls (__init__, __init__).


##### `SurfaceContext.list_member_objects`  (lines 3378–3404)

```
async def list_member_objects(self, kind: str, agent_id: UUID, member_id: UUID, *, admin: bool, query: 'ObjectListQuery') -> 'ObjectPage | None'
```

**Purpose**: Lists object records of one kind as a signed-in member. It uses the object kind’s own member-list visibility rules.

**Data flow**: It receives kind, agent, member, admin flag, and query. It checks registry support, stamps supported fields onto the query, enters agent scope, and returns an object page or `None`.

**Call relations**: Web object indexes and setup checks call it.

*Call graph*: called by 4 (armed, _has_own_page, _homepage_state, object_index); 2 external calls (replace, agent).


##### `SurfaceContext.member_object`  (lines 3406–3421)

```
async def member_object(self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool) -> 'MemberObject | None'
```

**Purpose**: Reads one object record as a signed-in member. Hidden and absent rows both return `None`.

**Data flow**: It receives kind, name, agent, member, and admin flag. It verifies the kind supports member detail reads, enters agent scope, and delegates to the store.

**Call relations**: Web object detail routes and setup checks call it.

*Call graph*: called by 2 (armed, object_detail); 1 external calls (agent).


##### `SurfaceContext.list_conversation_member_objects`  (lines 3423–3445)

```
async def list_conversation_member_objects(self, kind: str, agent_id: UUID, conversation_id: UUID, member_id: UUID, *, admin: bool, limit: int) -> tuple['ConversationObjectGrant', ...] | None
```

**Purpose**: Lists object grants related to one conversation for a member. It supports conversation-aware portal panels.

**Data flow**: It receives kind, agent, conversation, member, admin flag, and limit. It checks whether the kind supports conversation-member listing, enters agent scope, and returns grants or `None`.

**Call relations**: The web slot-context projection calls it when adding conversation object rows.

*Call graph*: called by 1 (_project_slot_context); 1 external calls (agent).


##### `SurfaceContext.list_credential_slots`  (lines 3447–3478)

```
async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]
```

**Purpose**: Lists member-fillable credential slots and whether each is filled, never the secret values. It hides deploy-written machinery slots.

**Data flow**: It queries filled credential slots, maps declared slots to object names, filters to member-fillable declarations, and returns `CredentialSlotView` records.

**Call relations**: Web credential pages and submit-intent panels call it.

*Call graph*: called by 2 (submit_intent, workspace_credentials); 4 external calls (__init__, select, named_slots, workspace_tx).


##### `SurfaceContext.workspace_domain`  (lines 3480–3486)

```
async def workspace_domain(self) -> str | None
```

**Purpose**: Returns the workspace’s own email domain. This is used for trusted first-contact joining and operator-workspace checks.

**Data flow**: It opens a workspace transaction, asks the seats helper for the workspace domain, and returns a domain string or `None`.

**Call relations**: `join_member` and `is_operator_workspace` call it.

*Call graph*: called by 2 (is_operator_workspace, join_member); 2 external calls (workspace_tx, workspace_domain).


##### `SurfaceContext.list_members`  (lines 3488–3496)

```
async def list_members(self) -> tuple[SeatEntry, ...]
```

**Purpose**: Returns the workspace roster sorted by email. It supports stable team-management displays.

**Data flow**: It reads a seats snapshot for this workspace and returns its member entries sorted by email.

**Call relations**: The web workspace-team route calls it.

*Call graph*: called by 1 (workspace_team); 2 external calls (__init__, workspace_tx).


##### `SurfaceContext.list_sources`  (lines 3498–3545)

```
async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]
```

**Purpose**: Lists live source bindings visible to the member. Removed sources are excluded, and private sources are visible only to their owner unless the reader is admin.

**Data flow**: It receives member id and admin flag, queries source rows with owner emails, filters visibility, projects connector binding fields, and returns `SourceView` objects.

**Call relations**: The web workspace-sources route calls it; it uses `_binding_fields` for connector-managed rows.

*Call graph*: calls 1 internal fn (_binding_fields); called by 1 (workspace_sources); 4 external calls (__init__, or_, select, workspace_tx).


##### `SurfaceContext.spend_caps`  (lines 3547–3596)

```
async def spend_caps(self) -> tuple[SpendCapView, ...]
```

**Purpose**: Lists spend caps configured for the workspace with readable subject names. It supports administration billing views.

**Data flow**: It queries spend-cap rows joined to agent and member names, orders them, and returns `SpendCapView` records.

**Call relations**: The web admin index calls it.

*Call graph*: called by 1 (admin_index); 4 external calls (__init__, and_, select, workspace_tx).


##### `SurfaceContext.list_installations`  (lines 3598–3614)

```
async def list_installations(self) -> tuple[InstallationSummary, ...]
```

**Purpose**: Lists surface installations bound to this workspace and the agent each routes to. It supports surface and provider administration pages.

**Data flow**: It queries surface-installation rows for this workspace, orders by surface, and returns `InstallationSummary` records.

**Call relations**: Several web administration and first-run routes call it.

*Call graph*: called by 4 (_held_providers, admin_index, workspace_first_run, workspace_surfaces); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_conversations`  (lines 3616–3665)

```
async def list_conversations(self, limit: int=LIST_CONVERSATIONS_LIMIT) -> tuple[ConversationSummary, ...]
```

**Purpose**: Lists recent conversations across all surfaces for this workspace. It is mainly a debug-oriented read.

**Data flow**: It builds an activity summary from turns, joins conversations and members, orders by latest activity, and returns `ConversationSummary` objects.

**Call relations**: The debugger surface calls it for its conversation list.

*Call graph*: called by 1 (conversations); 3 external calls (__init__, select, workspace_tx).


##### `SurfaceContext.list_agent_conversations`  (lines 3667–3690)

```
async def list_agent_conversations(self, agent_id: UUID, member_id: UUID, *, admin: bool, limit: int, surface: str | None=None, conversation_id: UUID | None=None, participation: Literal['mine', 'other
```

**Purpose**: Lists conversations for one agent through the shared `ConversationDirectory`. It is the context-bound wrapper for portal conversation views.

**Data flow**: It receives agent, member, admin flag, limit, and filters, creates a directory for this workspace, and returns its list result.

**Call relations**: Web chat-resolution and conversation routes call it.

*Call graph*: called by 4 (_member_chat, _named, _resolve_chat, conversations); 1 external calls (__init__).


##### `SurfaceContext.readable_conversation`  (lines 3692–3733)

```
async def readable_conversation(self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool=False) -> bool
```

**Purpose**: Decides whether a member may read a conversation’s content. Admins can read another member’s private conversation only after a recent disclosure record.

**Data flow**: It receives conversation, agent, member, and admin flag. It checks workspace and agent, audience membership, admin eligibility, and recent transcript-access rows, returning true or false.

**Call relations**: The web surface calls it before serving transcripts, files, subagent turns, or related content.

*Call graph*: called by 1 (_readable_conversation); 6 external calls (now, select, workspace_tx, audience_member, parse_audience, readable_audiences).


##### `SurfaceContext.conversation_audience`  (lines 3735–3745)

```
async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None
```

**Purpose**: Reads the audience attached to a conversation. It returns the parsed audience object rather than raw stored text.

**Data flow**: It receives conversation and agent ids, fetches the audience string for this workspace and agent, parses it, and returns it or `None`.

**Call relations**: The web slot-context builder calls it before constructing conversation-scoped reads.

*Call graph*: called by 1 (_slot_context); 3 external calls (select, workspace_tx, parse_audience).


##### `SurfaceContext.conversation_subagent_turns`  (lines 3747–3784)

```
async def conversation_subagent_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Reads all subagent turns spawned under a conversation’s turns, including nested descendants. This lets UIs display the work tree under the parent conversation.

**Data flow**: It receives conversation id and limit, builds a recursive query over parent-turn links, fetches turn rows breadth first, converts them to `Turn` records, and returns them.

**Call relations**: Web events, slot-target, and transcript-aid code call it after conversation authorization.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 3 (_events, _slot_target, _transcript_aids); 3 external calls (literal, select, workspace_tx).


##### `SurfaceContext.list_turns`  (lines 3786–3802)

```
async def list_turns(self, conversation_id: UUID, limit: int=LIST_TURNS_LIMIT) -> tuple[Turn, ...]
```

**Purpose**: Reads the recent turns of a conversation in admission order. It returns the durable turn records, including terminal state and context.

**Data flow**: It receives conversation id and limit, fetches the newest matching turn rows, reverses them to oldest-first order, and converts each row to `Turn`.

**Call relations**: Debugger and web transcript-aid routes call it.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 2 (conversation_turns, _transcript_aids); 1 external calls (workspace_tx).


##### `SurfaceContext.agent_origin_refs`  (lines 3804–3839)

```
async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]
```

**Purpose**: Identifies conversation message references that came from machine-origin prompts, such as scheduled fires or subagent results. Projection code can avoid rendering them as member speech.

**Data flow**: It receives a conversation id, unions matching turn ids and inbound-message ids based on admission source or spawn-result key, and returns them as strings.

**Call relations**: Web conversation-message and history rendering code call it.

*Call graph*: called by 2 (_conversation_messages, _history_messages); 4 external calls (or_, select, union_all, workspace_tx).


##### `SurfaceContext.turn_detail`  (lines 3841–3891)

```
async def turn_detail(self, turn_id: UUID) -> TurnDetail | None
```

**Purpose**: Reads one turn with its billing ledger and directly spawned child turns. It supports debugger and rich transcript views.

**Data flow**: It receives a turn id, fetches the turn row, child turn rows, and ledger rows, converts them into typed records, and returns `TurnDetail` or `None`.

**Call relations**: Debugger and web routes call it when resolving or displaying one turn.

*Call graph*: calls 2 internal fn (_turn_query, _turn_record); called by 6 (stream, turn, _conversation_messages, _events, _member_turn, _resolve_chat); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.turn_steps`  (lines 3893–3906)

```
async def turn_steps(self, turn_id: UUID) -> tuple[TurnStep, ...] | None
```

**Purpose**: Reads durable workflow steps for one turn if it belongs to this workspace. It helps debugger views explain how a turn ran.

**Data flow**: It receives a turn id, reads its running-attempt id or uses the turn id, then delegates to the injected turn-step source. Missing foreign turns return `None`.

**Call relations**: The debugger turn-steps route calls it.

*Call graph*: called by 1 (turn_steps); 2 external calls (select, workspace_tx).


##### `SurfaceContext.queued_arrivals`  (lines 3908–3949)

```
async def queued_arrivals(self, conversation_id: UUID, draining_turn_id: UUID | None) -> tuple[QueuedArrival, ...]
```

**Purpose**: Lists admitted messages that the written transcript does not yet contain. This lets a reloaded conversation still show messages waiting for or folded into a running turn.

**Data flow**: It receives conversation id and optional draining turn id, verifies ownership, queries unconsumed and optionally drained inbound rows, and returns `QueuedArrival` objects.

**Call relations**: The web conversation-message projection calls it alongside turn rows.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_conversation_messages); 5 external calls (__init__, false, or_, select, workspace_tx).


##### `SurfaceContext.arrival_speakers`  (lines 3951–3983)

```
async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]
```

**Purpose**: Reads attribution for member-admitted inbound-message rows. It labels folded messages with sender, question, and speaker id.

**Data flow**: It receives a conversation id, verifies ownership, queries member-admitted inbound messages, decodes context, and returns `SpokenArrival` records.

**Call relations**: Web conversation-message and history rendering call it to label queued or folded messages.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (_conversation_messages, _history_messages); 4 external calls (__init__, __init__, select, workspace_tx).


##### `SurfaceContext.keyed_admissions`  (lines 3985–4021)

```
async def keyed_admissions(self, conversation_id: UUID) -> tuple[KeyedAdmission, ...]
```

**Purpose**: Lists all messages in a conversation that used idempotency keys. This lets projections recognize which transcript messages correspond to surface submissions.

**Data flow**: It receives a conversation id, verifies ownership, unions keyed turn rows and keyed inbound-message rows, and returns `KeyedAdmission` records.

**Call relations**: Web transcript-aid code calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_transcript_aids); 4 external calls (__init__, select, union_all, workspace_tx).


##### `SurfaceContext.read_transcript`  (lines 4023–4034)

```
async def read_transcript(self, conversation_id: UUID) -> Conversation | None
```

**Purpose**: Reads a conversation’s durable transcript from blob storage. It first checks workspace ownership to avoid cross-tenant blob reads.

**Data flow**: It receives a conversation id, verifies the conversation belongs to this workspace, reads the transcript blob, decodes it, and returns a `Conversation` or `None` if absent.

**Call relations**: Debugger, UFO, and web surfaces call it when rendering conversation history or slot context.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 5 (conversation_transcript, channel, _conversation_messages, _slot_context, _subagent_nodes); 2 external calls (decode, transcript_key).


##### `SurfaceContext.list_compactions`  (lines 4036–4047)

```
async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]
```

**Purpose**: Lists saved transcript-compaction record indices for a conversation. Compaction records explain how long histories were summarized.

**Data flow**: It receives a conversation id, verifies ownership, lists matching blob keys, extracts numeric indices, sorts them, and returns them.

**Call relations**: Debugger and web conversation-message routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (conversation_compactions, _conversation_messages).


##### `SurfaceContext.read_compaction`  (lines 4049–4055)

```
async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Reads one full compaction record for a conversation. It returns nothing if the conversation is not owned here or the record is absent.

**Data flow**: It receives conversation id and compaction index, verifies ownership, delegates to the transcript helper, and returns a `CompactionRecord` or `None`.

**Call relations**: Debugger and web history routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (compaction_record, _history_messages); 1 external calls (read_compaction_record).


##### `SurfaceContext.read_compaction_after`  (lines 4057–4065)

```
async def read_compaction_after(self, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Reads only the post-compaction message window for one compaction record. This is a lighter read for comparing later history.

**Data flow**: It receives conversation id and index, verifies ownership, delegates to the transcript helper, and returns messages or `None`.

**Call relations**: The web verified-earlier helper calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_verified_earlier); 1 external calls (read_compaction_after).


##### `SurfaceContext.list_workspace_files`  (lines 4067–4073)

```
async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]
```

**Purpose**: Lists member-visible files currently in a conversation’s sandbox workspace. It returns empty for foreign or missing conversations.

**Data flow**: It receives a conversation id, verifies ownership, asks the sandbox carrier for workspace entries, and returns them.

**Call relations**: Debugger and web attachment routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_files, conversation_attachment).


##### `SurfaceContext.conversation_changes`  (lines 4075–4081)

```
async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last recorded workspace-file changes for a conversation. It summarizes what git saw after turns committed.

**Data flow**: It receives a conversation id, verifies ownership, and returns recorded changes or a no-change value.

**Call relations**: The web slot-context projection calls it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 1 (_project_slot_context); 1 external calls (recorded_workspace_changes).


##### `SurfaceContext.read_workspace_file`  (lines 4083–4092)

```
async def read_workspace_file(self, conversation_id: UUID, rel: str) -> AsyncIterator[bytes] | None
```

**Purpose**: Streams one file from a conversation’s sandbox workspace. It returns nothing if the conversation is not owned here or the file is absent.

**Data flow**: It receives conversation id and relative path, verifies ownership, and asks the sandbox carrier for an async byte stream.

**Call relations**: Debugger and web conversation-attachment routes call it.

*Call graph*: calls 1 internal fn (_owned_conversation); called by 2 (workspace_file, conversation_attachment).


##### `SurfaceContext.terminal_connect`  (lines 4094–4100)

```
def terminal_connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Publishes that a surface-held terminal connection is available for a conversation. This lets a turn use the member’s terminal rather than a default sandbox.

**Data flow**: It receives conversation id, working directory, optional member id, and runtime id, then records the terminal connection with the sandbox terminal registry.

**Call relations**: Terminal-capable surfaces call it when a held terminal connection opens.


##### `SurfaceContext.terminal_disconnect`  (lines 4102–4103)

```
def terminal_disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Removes the terminal connection for a conversation. It is the cleanup pair for `terminal_connect`.

**Data flow**: It receives a conversation id and tells the sandbox terminal registry to disconnect it.

**Call relations**: Terminal-capable surfaces call it when the held connection closes.


##### `SurfaceContext.claim_terminal`  (lines 4105–4111)

```
async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool
```

**Purpose**: Claims a connected terminal for a fresh conversation before a turn opens its sandbox. It avoids silently provisioning somewhere else during reconnect races.

**Data flow**: It receives conversation id and working directory, asks the sandbox carrier to claim an empty binding, and returns whether this call made the claim.

**Call relations**: The UFO terminal surface calls it when admitting or resuming terminal-backed work.

*Call graph*: called by 2 (_send, channel).


##### `SurfaceContext.next_terminal_op`  (lines 4113–4120)

```
async def next_terminal_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next operation a turn asks a member’s terminal to perform. It can exclude an operation just answered to avoid rendering it twice.

**Data flow**: It receives conversation id and optional excluded op id, delegates to the terminal registry, and returns a `TerminalOp`.

**Call relations**: Terminal streaming routes use it while racing terminal operations against normal turn frames.


##### `SurfaceContext.terminal_resolve`  (lines 4122–4136)

```
def terminal_resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> bool
```

**Purpose**: Answers an in-flight terminal operation with client results. The terminal registry enforces the single-use op id and member binding.

**Data flow**: It receives conversation id, op id, reply bytes, optional failure text, and member id, then returns whether the operation was resolved.

**Call relations**: The UFO terminal surface calls it when the member’s client posts an operation result.

*Call graph*: called by 1 (channel).


##### `SurfaceContext.terminal_op_body`  (lines 4138–4150)

```
async def terminal_op_body(self, queue_key: str, op_id: str, member_id: UUID | None) -> bytes | None
```

**Purpose**: Reads staged bytes for an in-flight terminal operation without creating a conversation. It is used when a client fetches a large operation body separately.

**Data flow**: It receives queue key, op id, and member id, resolves the existing conversation, then asks the terminal registry for staged bytes. It returns bytes or `None`.

**Call relations**: The UFO `op_body` route calls it.

*Call graph*: calls 1 internal fn (_conversation_lookup); called by 1 (op_body); 1 external calls (workspace_tx).


##### `SurfaceContext.installation`  (lines 4152–4165)

```
async def installation(self, peer_surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation id for another surface. It helps debug and portal views link to the surface where a conversation lives.

**Data flow**: It receives a peer surface name, queries surface-installation rows, and returns the installation id or `None`.

**Call relations**: The debugger workspace metadata route calls it.

*Call graph*: called by 1 (workspace_meta); 2 external calls (select, workspace_tx).


##### `SurfaceContext.transaction`  (lines 4168–4177)

```
async def transaction(self) -> AsyncIterator[AsyncConnection]
```

**Purpose**: Yields a raw workspace database transaction for trusted surface code that needs extension-owned tables outside a turn. The caller must still scope its own SQL by workspace.

**Data flow**: It opens a workspace transaction, yields the async connection, commits on normal exit, and rolls back on error.

**Call relations**: Sites and Slack surface helpers call it for surface-specific reads that cannot go through an `ExtensionContext`.

*Call graph*: called by 2 (_viewer_is_admin, _folds_into_live_turn); 1 external calls (workspace_tx).


##### `SurfaceContext._owned_conversation`  (lines 4179–4189)

```
async def _owned_conversation(self, conversation_id: UUID) -> bool
```

**Purpose**: Checks whether a conversation id belongs to this workspace. It is the common guard before reading blobs, sandbox state, or queued messages.

**Data flow**: It receives a conversation id, queries for a matching conversation row in this workspace, and returns true or false.

**Call relations**: Many transcript, compaction, file, arrival, and workspace-change reads call it before touching unscoped stores.

*Call graph*: called by 10 (arrival_speakers, conversation_changes, keyed_admissions, list_compactions, list_workspace_files, queued_arrivals, read_compaction, read_compaction_after, read_transcript, read_workspace_file); 2 external calls (select, workspace_tx).


##### `SurfaceContext._turn_query`  (lines 4191–4211)

```
def _turn_query(self) -> sa.Select
```

**Purpose**: Builds the standard SQL select for turn rows. It keeps all turn projections selecting the same fields.

**Data flow**: It takes no input and returns a SQL select with the durable turn columns needed to build a `Turn` record.

**Call relations**: `conversation_subagent_turns`, `list_turns`, and `turn_detail` use it before converting rows with `_turn_record`.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 1 external calls (select).


##### `SurfaceContext._turn_record`  (lines 4213–4233)

```
def _turn_record(self, row: sa.Row) -> Turn
```

**Purpose**: Converts one database row into the typed `Turn` data model. It also validates nested context and terminal payloads.

**Data flow**: It receives a SQL row, copies turn fields, parses JSON context and terminal data when present, and returns a `Turn` object.

**Call relations**: Turn-listing and detail methods call it after running `_turn_query`.

*Call graph*: called by 3 (conversation_subagent_turns, list_turns, turn_detail); 3 external calls (__init__, model_validate, model_validate).


##### `SurfaceInstallationAccess.reserve_address`  (lines 4265–4325)

```
async def reserve_address(self, surface: str, address: str, member_id: UUID, claim_expires_at: datetime) -> AddressClaimState
```

**Purpose**: Reserves an addressed-surface address for a workspace member until they prove it. It reports whether the address was reserved, already linked to them, or taken.

**Data flow**: It receives surface, address, member id, and expiry. It checks the surface is declared as addressed, upserts or takes over expired reservations in owner scope, then returns an `AddressClaimState`.

**Call relations**: Tool code uses this manifest-scoped access object when setting up addressed surfaces like iMessage.

*Call graph*: 7 external calls (__init__, now, and_, or_, select, owner_tx, ws_current).


##### `SurfaceInstallationAccess.installation`  (lines 4327–4340)

```
async def installation(self, surface: str) -> str | None
```

**Purpose**: Reads this workspace’s installation identity for a declared surface. It refuses surfaces the tool did not declare.

**Data flow**: It receives a surface name, validates declaration, queries the workspace installation row, and returns the installation id or `None`.

**Call relations**: Extension tools call it through their manifest-scoped installation access.

*Call graph*: 4 external calls (__init__, select, workspace_tx, ws_current).


##### `SurfaceInstallationAccess.bind`  (lines 4342–4354)

```
async def bind(self, surface: str, installation_id: str) -> None
```

**Purpose**: Binds a declared surface installation to the current workspace. Addressed surfaces are marked differently because their shared installation does not route tenants.

**Data flow**: It receives surface and installation id, validates declaration, reads current workspace scope, and calls `_bind_surface_installation` with the correct ingress-routing flag.

**Call relations**: Tool-driven installation flows call it instead of writing installation rows directly.

*Call graph*: calls 1 internal fn (_bind_surface_installation); 2 external calls (__init__, ws_current).


##### `SurfaceAuth.workspace`  (lines 4366–4377)

```
async def workspace(self, installation_id: str) -> UUID | None
```

**Purpose**: Resolves which workspace owns a shared surface installation id. It only returns installations that are allowed to route ingress.

**Data flow**: It receives an installation id, queries owner-scoped surface-installation rows for this surface and id, and returns a workspace id or `None`.

**Call relations**: Slack workspace-resolution code calls it before binding a request to a workspace.

*Call graph*: called by 1 (resolve_workspace); 2 external calls (select, owner_tx).


##### `SurfaceAuth.addressed_workspace`  (lines 4379–4392)

```
async def addressed_workspace(self, address: str) -> UUID | None
```

**Purpose**: Resolves which workspace owns an addressed-surface address. This is the tenant lookup for shared providers where the sender address, not an installation, routes traffic.

**Data flow**: It receives an address, queries owner-scoped surface-address rows for this surface, and returns a workspace id or `None`.

**Call relations**: Addressed listener contexts use it before admitting address-routed events.

*Call graph*: 2 external calls (select, owner_tx).


##### `SurfaceAuth.open_credential_authorization`  (lines 4394–4406)

```
def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None
```

**Purpose**: Opens a sealed credential handoff before any workspace has been resolved. It returns nothing for expired or tampered seals.

**Data flow**: It receives a sealed string, verifies it with the credential store if present, and returns decoded state or `None`.

**Call relations**: Slack request-resolution code calls it during pre-binding OAuth handshakes.

*Call graph*: called by 1 (resolve_workspace); 1 external calls (open_credential_request).


##### `SurfaceAuth.credential`  (lines 4408–4424)

```
async def credential(self, workspace_id: UUID, slot: str) -> str
```

**Purpose**: Reads a declared credential slot during pre-binding surface authentication. It first verifies that the workspace exists.

**Data flow**: It receives workspace id and slot, checks the slot declaration and credential store, enters workspace scope, verifies the workspace row, and returns the credential value.

**Call relations**: Slack authentication uses it to read signing secrets while resolving a request.

*Call graph*: called by 1 (_auth_signing_secret); 4 external calls (__init__, select, workspace_tx, ws).


##### `SurfaceListenerContext.workspace`  (lines 4460–4468)

```
async def workspace(self, installation_id: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds a persistent listener event to the workspace owning an installation id. It yields no context if the installation is unknown.

**Data flow**: It receives an installation id, verifies this process still owns the listener lease, resolves the workspace, enters workspace scope, and yields a `SurfaceContext` or `None`.

**Call relations**: Persistent surface listeners use it around each provider event before touching workspace data.

*Call graph*: 1 external calls (ws).


##### `SurfaceListenerContext.addressed`  (lines 4471–4482)

```
async def addressed(self, address: str) -> AsyncIterator[SurfaceContext | None]
```

**Purpose**: Binds a persistent listener event to the workspace owning an address. It is the addressed-surface version of workspace binding.

**Data flow**: It receives an address, checks listener ownership, resolves the addressed workspace, enters workspace scope, and yields a `SurfaceContext` or `None`.

**Call relations**: The iMessage listener calls it while processing address-routed events.

*Call graph*: called by 1 (_process_event); 1 external calls (ws).


##### `SurfaceListenerContext.cursor`  (lines 4484–4499)

```
async def cursor(self, installation_id: str) -> int | None
```

**Purpose**: Reads the saved provider-stream position for this listener. It ignores cursors from a different installation id.

**Data flow**: It receives installation id, queries the owner-scoped cursor row for this surface, and returns the sequence or `None`.

**Call relations**: The iMessage listener calls it when starting or catching up.

*Call graph*: called by 1 (listen); 2 external calls (select, owner_tx).


##### `SurfaceListenerContext.store_cursor`  (lines 4501–4524)

```
async def store_cursor(self, installation_id: str, sequence: int) -> None
```

**Purpose**: Stores the provider-stream position for this listener. The cursor belongs to the fleet listener, not to any one workspace.

**Data flow**: It receives installation id and sequence, upserts the cursor row for this surface, and records the latest sequence.

**Call relations**: The iMessage listener updates it after catching up or processing events.

*Call graph*: called by 2 (_catch_up, _process_event); 1 external calls (owner_tx).


##### `SurfaceListenerContext.clear_cursor`  (lines 4526–4533)

```
async def clear_cursor(self) -> None
```

**Purpose**: Deletes the saved stream cursor so the next listener start begins from the provider’s head. It is a reset operation.

**Data flow**: It takes no extra input and deletes the cursor row for this surface in owner scope.

**Call relations**: The iMessage listener calls it when it needs to forget old stream position.

*Call graph*: called by 1 (listen); 2 external calls (delete, owner_tx).


##### `SurfaceListenerRunner.run`  (lines 4559–4597)

```
async def run(self) -> None
```

**Purpose**: Runs one persistent surface listener only while this process owns the fleet-wide lease. It restarts on ownership changes and parks non-database listener failures.

**Data flow**: It waits until owned, starts the listener with a `SurfaceListenerContext`, races it against ownership loss, logs failures, sleeps or parks as appropriate, and cancels tasks during cleanup.

**Call relations**: Core process startup runs this for surfaces that declare a listener.

*Call graph*: calls 2 internal fn (_wait_until_not_owned, _wait_until_owned); 9 external calls (__init__, CancelledError, create_task, ensure_future, gather, sleep, wait, emit_metric, log).


##### `SurfaceListenerRunner._wait_until_not_owned`  (lines 4599–4604)

```
async def _wait_until_not_owned(self) -> None
```

**Purpose**: Waits until this process loses the listener lease. It polls the ownership claim repeatedly.

**Data flow**: It calls `_owned_on_tick` in a loop, sleeps between checks, and returns once ownership is definitively false.

**Call relations**: `run` uses it to stop or restart the listener when another process takes over.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._wait_until_owned`  (lines 4606–4610)

```
async def _wait_until_owned(self) -> None
```

**Purpose**: Waits until this process gains the listener lease. It polls until ownership is true.

**Data flow**: It repeatedly calls `_owned_on_tick`, sleeping between attempts, and returns when the claim succeeds.

**Call relations**: `run` calls it before starting the listener task.

*Call graph*: calls 1 internal fn (_owned_on_tick); called by 1 (run); 1 external calls (sleep).


##### `SurfaceListenerRunner._owned_on_tick`  (lines 4612–4621)

```
async def _owned_on_tick(self) -> bool | None
```

**Purpose**: Performs one safe ownership check for the listener lease. Database errors are logged and treated as unknown rather than fatal.

**Data flow**: It calls `_owns`; if a SQLAlchemy database error occurs, it logs and returns `None`.

**Call relations**: The wait loops call it on every ownership poll.

*Call graph*: calls 1 internal fn (_owns); called by 2 (_wait_until_not_owned, _wait_until_owned); 1 external calls (log).


##### `SurfaceListenerRunner._owns`  (lines 4623–4641)

```
async def _owns(self) -> bool
```

**Purpose**: Runs the lease-claim transaction in a cancellation-safe way. This prevents a cancelled task from leaving a database transaction half-open.

**Data flow**: It starts `_claim` as a task, shields it until complete while remembering cancellation, then re-raises cancellation or returns the claim result.

**Call relations**: `_owned_on_tick` calls it whenever ownership is polled.

*Call graph*: calls 1 internal fn (_claim); called by 1 (_owned_on_tick); 2 external calls (ensure_future, shield).


##### `SurfaceListenerRunner._claim`  (lines 4643–4683)

```
async def _claim(self) -> bool
```

**Purpose**: Claims or refreshes the database lease for one surface listener. Only the current owner or an expired claim can update the lease.

**Data flow**: It computes a new expiry, upserts the listener-claim row with this instance id and token, and returns true if the returned token matches this runner.

**Call relations**: `_owns` wraps it to protect the transaction from cancellation.

*Call graph*: called by 1 (_owns); 7 external calls (now, timedelta, and_, insert, insert, or_, owner_tx).


##### `SurfaceDeliveryError.__init__`  (lines 4690–4694)

```
def __init__(self, message: str, *, retry_after_seconds: int | None=None) -> None
```

**Purpose**: Creates a delivery error that may carry a provider retry delay. Pollers use that delay instead of a fixed backoff when present.

**Data flow**: It receives an error message and optional nonnegative retry-after seconds, validates the delay, stores it, and initializes the runtime error.

**Call relations**: Slack delivery code can raise it; writeback and mid-turn pollers inspect it during retry scheduling.

*Call graph*: called by 1 (_chat_post).


##### `_writeback_due`  (lines 4755–4779)

```
def _writeback_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for terminal writebacks that are ready to claim. It also waits until pending mid-turn replies for the same turn are done.

**Data flow**: It receives the current time and returns a SQL condition over turn, writeback, claim status, claim expiry, and mid-turn reply status.

**Call relations**: `writeback_workspaces.due` and `WritebackPoller._claim` use it to find eligible terminal deliveries.

*Call graph*: called by 2 (_claim, due); 3 external calls (and_, exists, or_).


##### `writeback_workspaces`  (lines 4782–4817)

```
def writeback_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace-candidate reader for terminal writebacks. It prevents one workspace from monopolizing the poller.

**Data flow**: It initializes a cursor and returns an async candidate function that pages workspace ids with due writebacks, wrapping the owner-scoped query helper.

**Call relations**: A `WritebackPoller` receives the returned candidate function and calls it in `run` or `drain`.

*Call graph*: 1 external calls (owner_candidates).


##### `writeback_workspaces.due`  (lines 4789–4803)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one page query for workspace ids that currently have due terminal writebacks. It applies the rotating cursor when set.

**Data flow**: It reads the current time, selects workspace ids from writebacks joined to turns, filters with `_writeback_due`, groups and orders them, and returns the SQL query.

**Call relations**: The owner-candidate wrapper inside `writeback_workspaces` executes it.

*Call graph*: calls 1 internal fn (_writeback_due); 2 external calls (now, select).


##### `writeback_workspaces.candidates`  (lines 4807–4815)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspace ids with due writebacks. It wraps around when the end is reached.

**Data flow**: It calls the owner-scoped due reader, resets the cursor if a page is empty after the cursor, updates the cursor to the last id, and returns workspace ids.

**Call relations**: `WritebackPoller.run` and `WritebackPoller.drain` call it through the poller’s `candidates` field.


##### `_WritebackDeliveryFailed.__init__`  (lines 4825–4828)

```
def __init__(self, phase: Literal['post', 'attach'], error: Exception) -> None
```

**Purpose**: Wraps a failed writeback delivery with the phase that failed: posting the reply or attaching files. This lets retry logs say what broke.

**Data flow**: It receives a phase and original exception, stores both, and initializes the error message from the exception.

**Call relations**: `WritebackPoller._deliver_claimed` raises it when a surface `post` or `attach` call fails.

*Call graph*: called by 1 (_deliver_claimed).


##### `WritebackPoller.run`  (lines 4851–4884)

```
async def run(self) -> None
```

**Purpose**: Continuously drains terminal writebacks across workspaces. It keeps a bounded number of workspace drain tasks in flight and logs failures.

**Data flow**: It creates a concurrency semaphore, polls candidate workspaces, starts drain tasks for new ones, cleans up finished tasks, sleeps between polls, and cancels outstanding tasks on shutdown.

**Call relations**: Core runs it as the background mailroom for durable surface final replies.

*Call graph*: calls 1 internal fn (_drain_workspace); 5 external calls (Semaphore, create_task, gather, sleep, log).


##### `WritebackPoller.drain`  (lines 4886–4895)

```
async def drain(self) -> None
```

**Purpose**: Runs one bounded drain pass instead of an infinite loop. It is useful for tests or manual flushes.

**Data flow**: It reads candidate workspace ids, drains them concurrently under a semaphore, gathers results, and raises an exception group if any workspace failed.

**Call relations**: It calls `_drain_workspace`, the same worker used by `run`.

*Call graph*: calls 1 internal fn (_drain_workspace); 2 external calls (Semaphore, gather).


##### `WritebackPoller._drain_workspace`  (lines 4897–4915)

```
async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None
```

**Purpose**: Claims and delivers due writebacks for one workspace. It also starts lease-renewal tasks while external delivery is in progress.

**Data flow**: It receives workspace id and semaphore, enters workspace scope, claims rows, starts renewal tasks, delivers each row, and cancels renewals afterward.

**Call relations**: `run` and `drain` call it for each candidate workspace.

*Call graph*: calls 3 internal fn (_claim, _deliver, _renew_claim); called by 2 (drain, run); 4 external calls (create_task, gather, log, ws).


##### `WritebackPoller._claim`  (lines 4917–4950)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due terminal writeback rows for this worker. Claiming stops other workers from delivering the same rows.

**Data flow**: It receives workspace id, finds due rows with `_writeback_due`, updates them to claimed with worker id and expiry, and returns turn id, reply ref, and last error.

**Call relations**: `_drain_workspace` calls it before starting delivery.

*Call graph*: calls 1 internal fn (_writeback_due); called by 1 (_drain_workspace); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `WritebackPoller._deliver`  (lines 4952–4984)

```
async def _deliver(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Delivers one claimed terminal writeback and logs the outcome. It turns delivery failures into retry or failure state changes.

**Data flow**: It receives workspace id, turn id, existing reply ref, and renewal task. It times delivery, calls `_deliver_with_lease`, catches claim loss or delivery failure, and logs success or retry/failure details.

**Call relations**: `_drain_workspace` calls it for each claimed row.

*Call graph*: calls 2 internal fn (_deliver_with_lease, _fail_or_retry); called by 1 (_drain_workspace); 2 external calls (now, log).


##### `WritebackPoller._deliver_with_lease`  (lines 4986–5015)

```
async def _deliver_with_lease(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None, renewal: asyncio.Task[None]) -> None
```

**Purpose**: Runs external delivery while the claim-renewal task stays healthy, then marks the row delivered. It stops renewal before the final commit to avoid locking against itself.

**Data flow**: It starts `_deliver_claimed`, races it against renewal failure, cancels leftover tasks, waits for cleanup, and calls `_mark_delivered` after successful delivery.

**Call relations**: `_deliver` calls it as the protected delivery core.

*Call graph*: calls 2 internal fn (_deliver_claimed, _mark_delivered); called by 1 (_deliver); 3 external calls (create_task, gather, wait).


##### `WritebackPoller._deliver_claimed`  (lines 5017–5039)

```
async def _deliver_claimed(self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Builds the writeback payload and calls the surface’s final reply delivery functions. It records the provider reply reference before uploading attachments.

**Data flow**: It receives workspace id, turn id, and optional reply ref. It builds `Writeback`, finds the surface spec, skips missing delivery hooks, posts if needed, records the ref, and calls attach.

**Call relations**: `_deliver_with_lease` calls it; failures are wrapped as `_WritebackDeliveryFailed`.

*Call graph*: calls 3 internal fn (_build, _record_ref, __init__); called by 1 (_deliver_with_lease); 1 external calls (log).


##### `WritebackPoller._renew_claim`  (lines 5041–5044)

```
async def _renew_claim(self, turn_id: UUID) -> None
```

**Purpose**: Keeps a claimed writeback lease alive during slow external delivery. It refreshes until cancelled.

**Data flow**: It receives a turn id, sleeps for the refresh interval in a loop, and calls `_refresh_claim` each time.

**Call relations**: `_drain_workspace` starts one renewal task per claimed row.

*Call graph*: calls 1 internal fn (_refresh_claim); called by 1 (_drain_workspace); 1 external calls (sleep).


##### `WritebackPoller._refresh_claim`  (lines 5046–5062)

```
async def _refresh_claim(self, turn_id: UUID) -> None
```

**Purpose**: Extends the claim expiry for a writeback row still owned by this worker. It raises if the worker no longer owns the claim.

**Data flow**: It receives a turn id, updates the matching claimed row with a later expiry, and raises `_WritebackClaimLost` if no row was updated.

**Call relations**: `_renew_claim` calls it repeatedly.

*Call graph*: called by 1 (_renew_claim); 5 external calls (__init__, now, timedelta, update, workspace_tx).


##### `WritebackPoller._build`  (lines 5064–5116)

```
async def _build(self, turn_id: UUID) -> tuple[Writeback, str]
```

**Purpose**: Builds the `Writeback` object for one terminal turn. It includes the terminal frame, conversation queue key, agent id, and shared artifacts.

**Data flow**: It receives a turn id, queries turn and conversation data plus artifact rows, validates the terminal frame, and returns the writeback payload with the surface name.

**Call relations**: `_deliver_claimed` calls it before dispatching to the surface spec.

*Call graph*: called by 1 (_deliver_claimed); 5 external calls (__init__, __init__, model_validate, select, workspace_tx).


##### `WritebackPoller._record_ref`  (lines 5118–5130)

```
async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None
```

**Purpose**: Stores the provider reply reference after the final reply has been posted. This prevents reposting if the worker crashes before attachments finish.

**Data flow**: It receives turn id and reply ref, updates the claimed writeback row owned by this worker, and raises claim-lost if no row changed.

**Call relations**: `_deliver_claimed` calls it between `post` and `attach`.

*Call graph*: called by 1 (_deliver_claimed); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._mark_delivered`  (lines 5132–5149)

```
async def _mark_delivered(self, turn_id: UUID) -> None
```

**Purpose**: Marks a claimed terminal writeback as delivered. It clears claim owner and expiry.

**Data flow**: It receives a turn id, updates the claimed row owned by this worker to delivered, and raises claim-lost if the update fails.

**Call relations**: `_deliver_with_lease` calls it after external delivery succeeds.

*Call graph*: called by 1 (_deliver_with_lease); 3 external calls (__init__, update, workspace_tx).


##### `WritebackPoller._fail_or_retry`  (lines 5151–5200)

```
async def _fail_or_retry(self, turn_id: UUID, error: _WritebackDeliveryFailed) -> tuple[str, str, datetime | None]
```

**Purpose**: Schedules a failed terminal writeback for retry or marks it permanently failed after it gets too old. It honors provider retry-after hints within a bounded window.

**Data flow**: It receives turn id and wrapped delivery error, computes retry timing and last-error text, updates the row if still claimed by this worker, and returns outcome, error text, and next attempt time.

**Call relations**: `_deliver` calls it when `_deliver_with_lease` reports a delivery failure.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


##### `mid_turn_reply_workspaces`  (lines 5203–5234)

```
def mid_turn_reply_workspaces() -> WorkspaceCandidates
```

**Purpose**: Creates a rotating workspace-candidate reader for deliverable mid-turn replies. These are replies sent before a turn reaches its final answer.

**Data flow**: It initializes a cursor and returns an async function that pages workspace ids with due mid-turn reply rows.

**Call relations**: `MidTurnReplyPoller` receives and calls the returned candidate function.

*Call graph*: 1 external calls (owner_candidates).


##### `mid_turn_reply_workspaces.due`  (lines 5209–5220)

```
def due() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds one query for workspace ids with due mid-turn replies. It applies the rotating cursor to page through workspaces.

**Data flow**: It reads the current time, selects grouped workspace ids from mid-turn replies matching `_mid_turn_reply_due`, orders and limits them, and returns the SQL query.

**Call relations**: The owner-candidate wrapper inside `mid_turn_reply_workspaces` executes it.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); 2 external calls (now, select).


##### `mid_turn_reply_workspaces.candidates`  (lines 5224–5232)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: Returns the next rotating page of workspaces with mid-turn replies to deliver. It wraps around after the last workspace.

**Data flow**: It calls the due reader, resets the cursor if necessary, stores the last workspace id as the new cursor, and returns the ids.

**Call relations**: `MidTurnReplyPoller.drain` calls it through the poller’s `candidates` field.


##### `_mid_turn_reply_due`  (lines 5237–5250)

```
def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition for claimable mid-turn replies. It includes pending rows and expired claims.

**Data flow**: It receives the current time and returns a SQL condition over reply status and claim expiry.

**Call relations**: `mid_turn_reply_workspaces.due` and `MidTurnReplyPoller._claim` use it.

*Call graph*: called by 2 (_claim, due); 2 external calls (and_, or_).


##### `MidTurnReplyPoller.run`  (lines 5276–5282)

```
async def run(self) -> None
```

**Purpose**: Continuously drains deliverable mid-turn replies. It logs drain failures and tries again on the next poll.

**Data flow**: It loops forever, calls `drain`, catches and logs errors, then sleeps for the poll interval.

**Call relations**: Core runs it as the background sender for early replies and cross-surface comment notices.

*Call graph*: calls 1 internal fn (drain); 2 external calls (sleep, log).


##### `MidTurnReplyPoller.drain`  (lines 5284–5294)

```
async def drain(self) -> None
```

**Purpose**: Runs one pass of mid-turn reply delivery across candidate workspaces. It processes claimed replies in order.

**Data flow**: It gets workspace ids from the candidate reader, enters each workspace scope, claims rows, logs retries, and calls `_deliver` for each row.

**Call relations**: `run` calls it repeatedly.

*Call graph*: calls 2 internal fn (_claim, _deliver); called by 1 (run); 2 external calls (log, ws).


##### `MidTurnReplyPoller._claim`  (lines 5296–5337)

```
async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]
```

**Purpose**: Claims a batch of due mid-turn reply rows for this worker. It orders them so surfaces receive replies in the model’s intended order.

**Data flow**: It receives workspace id, finds due rows using `_mid_turn_reply_due`, updates them to claimed with worker id and expiry, returns useful row fields, and sorts the claimed rows.

**Call relations**: `drain` calls it before delivering mid-turn replies.

*Call graph*: calls 1 internal fn (_mid_turn_reply_due); called by 1 (drain); 5 external calls (now, timedelta, select, update, workspace_tx).


##### `MidTurnReplyPoller._deliver`  (lines 5339–5362)

```
async def _deliver(self, workspace_id: UUID, row: sa.Row) -> None
```

**Purpose**: Delivers one claimed mid-turn reply and records the result. It schedules retry or failure on errors.

**Data flow**: It receives workspace id and a claimed row, calls `_speak`, handles failures with `_fail_or_retry`, marks success with `_mark_delivered`, and logs timing.

**Call relations**: `drain` calls it for every claimed reply row.

*Call graph*: calls 3 internal fn (_fail_or_retry, _mark_delivered, _speak); called by 1 (drain); 2 external calls (now, log).


##### `MidTurnReplyPoller._speak`  (lines 5364–5406)

```
async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None
```

**Purpose**: Calls the surface’s mid-turn delivery hook for one reply, unless it was already posted or the surface has no hook. Existing reply references prevent duplicate posts after crashes.

**Data flow**: It receives workspace id and reply row. It returns an existing reply ref if present, otherwise loads turn and conversation data, finds the surface spec, builds `MidTurnReply`, and calls `speak` when available.

**Call relations**: `_deliver` calls it as the actual external send step.

*Call graph*: called by 1 (_deliver); 4 external calls (__init__, select, workspace_tx, log).


##### `MidTurnReplyPoller._mark_delivered`  (lines 5408–5426)

```
async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None
```

**Purpose**: Marks a mid-turn reply row as delivered and stores its provider reply reference. If the claim was lost, it logs instead of raising.

**Data flow**: It receives reply id and optional reply ref, updates the claimed row owned by this worker to delivered, clears claim fields, and logs claim loss if no row changed.

**Call relations**: `_deliver` calls it after `_speak` succeeds or intentionally does nothing.

*Call graph*: called by 1 (_deliver); 3 external calls (update, workspace_tx, log).


##### `MidTurnReplyPoller._fail_or_retry`  (lines 5428–5475)

```
async def _fail_or_retry(self, reply_id: UUID, error: Exception) -> tuple[str, str, datetime | None]
```

**Purpose**: Schedules a failed mid-turn reply for retry or marks it failed after the delivery window expires. A failed mid-turn reply no longer blocks the terminal reply.

**Data flow**: It receives reply id and error, computes retry delay from retry-after or fixed backoff, writes status, expiry, and last error if still claimed, and returns outcome and next attempt time.

**Call relations**: `_deliver` calls it when `_speak` raises an error.

*Call graph*: called by 1 (_deliver); 5 external calls (now, timedelta, case, update, workspace_tx).


### `core/src/ufo/surfaces/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python projects, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That means code elsewhere can refer to things inside `core/src/ufo/surfaces` using normal import paths, such as `ufo.surfaces.some_module`.

There is no code here, so it does not create objects, run setup steps, or expose a public shortcut API. Its value is structural: it makes the folder part of the project’s module layout. Without it, depending on the Python version and packaging setup, imports involving `ufo.surfaces` might fail or behave differently.

A simple analogy is a blank label on a filing cabinet drawer. The label does not contain the documents, but it tells the filing system that this drawer exists and can be addressed by name.

## 📊 State Registers Touched

- `reg-extension-capability-registry` — The live catalog of everything enabled extensions add, such as tools, routes, jobs, credentials, hooks, and backends.
- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-workspace-directory` — The durable list of workspaces and their core ownership, admin, billing, and setup state.
- `reg-member-auth-principals` — The shared answer to who the current person or service is and what member identity they are acting as.
- `reg-agent-profiles` — The saved assistant definitions, including each agent's model, tools, visibility, setup needs, internet access, and identity.
- `reg-surface-routing` — The mapping from outside places like web, Slack, terminal, and iMessage to the right workspace, conversation, member, and agent.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-inbound-admission-queue` — The saved queue of incoming messages or intents waiting to become safe conversation turns.
- `reg-transcript-store` — The saved conversation history and compacted summaries that later turns, portals, and auditors read back.
- `reg-live-update-stream` — The temporary live feed of progress messages that open clients and other server processes can follow.
- `reg-billing-ledger` — The shared money and usage record for tokens, images, videos, sandbox use, egress, balances, caps, and exports.
- `reg-credential-vault` — The encrypted store of API keys, connected accounts, grants, and approvals that lets tools use outside services without exposing secrets.
- `reg-artifact-blob-store` — The shared file storage for generated artifacts, downloads, document previews, screenshots, and other saved output bytes.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-extension-data-store` — Durable extension-scoped key/value or JSON state used by installed extensions beyond their manifest capabilities and lockfile selection.
- `reg-reply-delivery-outbox` — Durable reply records for messages that must be delivered exactly once or retried safely, including mid-turn replies before final turn completion.
- `reg-hosted-site-store` — Saved hosted-site records, published bindings, homepage mappings, build metadata, and site preview state used by public routes and site tools.
- `reg-transcript-access-audit-log` — Durable audit trail of privacy-sensitive transcript reads, especially admin access to another member’s private conversation history.
- `reg-delivery-format-registry` — Shared per-surface reply and delivery-format rules used when constructing prompts and shaping delivered responses.
- `reg-workspace-membership-roster` — Durable workspace member records, roles/admin flags, invitations, inviter stamps, seating history, and member-local profile fields such as timezone or email lookup data.
- `reg-auth-and-oauth-flow-state` — Short-lived login and OAuth handoff state such as nonces, return targets, code-verifier data, pending claims, and callback correlation before it becomes an authenticated principal or stored credential.
- `reg-signed-token-keyring` — Shared signing secrets, key IDs, expiry rules, and validation parameters used to mint and verify login, public-route, artifact-download, and sandbox-access tokens.
