# Tool dispatch, sandboxed execution, artifacts, and files  `stage-10`

This stage is the system’s supervised “workshop” during the main conversation loop. When the model asks to do something, the tool registry acts like a catalog: it defines which tools exist, how they are shown to the model, and how the right one is found. The tool context is the rulebook handed to that tool. It says who the tool is acting for, which files and accounts it may use, how it can call subagents, and how it must report back.

The sandbox backends provide the safe workbench. They create a private workspace, run commands in local, terminal, Docker, or cloud containers, expose previews and ports, and block unsafe file paths that try to escape the workspace. The built-in tools are the everyday instruments: run shell commands, read and edit files, ask the user questions, collect credentials, manage checklists, and keep REPL sessions alive. Task journals remember long-running commands.

Artifacts are shared files the agent has deliberately produced for the user, with access checks. Activity messages turn raw tool calls into clear, short updates people can understand.

## Sub-stages

- [Sandbox backends and file safety](stage-10.1.md) `stage-10.1` — 11 files
- [Built-in interactive and stateful tools](stage-10.2.md) `stage-10.2` — 4 files

## Files in this stage

### Tool runtime surface
These files describe how tool actions are presented, how shared artifacts are managed, and how tools are contextualized and registered for safe execution.

### `core/src/ufo/turns/activity.py`

`domain_logic` · `during tool-call reporting`

When a tool enters dispatch, this file asks the background model for one short, goal-facing summary of that call. The request contains only the tool name and bounded arguments. The result is normalized to one line under 50 characters for every activity surface, including Slack.

Generation runs beside tool dispatch and has a bounded lifetime. A slow or failed summary produces no live label and never delays the tool or the main model loop.

#### Function details

##### `ActivitySummarizer.summarize`

```
async def summarize(self, call: ToolUseBlock) -> str | None
```

**Purpose**: Generates the current member-facing step without delaying dispatch.

**Data flow**: It receives one `ToolUseBlock`, bounds and serializes its arguments, sends that call alone to the background model, and returns a normalized line. Failure or timeout returns `None`.

**Call relations**: `TurnEngine` starts it in a detached task when a call enters dispatch, then publishes an `Activity` frame if the label finishes before the turn ends.


### `core/src/ufo/artifacts.py`

`domain_logic` · `request handling`

An artifact is a file produced during a conversation and shared with the outside world through `share_file`. This file is the read-and-delete side of that feature. It treats all shares with the same conversation and filename as versions of one object, so if a turn shares `report.txt` twice in the same conversation, users see one artifact whose latest version is current. The same filename from a different conversation is a different artifact.

The file gives each artifact a stable, readable name made from a short conversation id prefix plus a cleaned-up filename, like `3f2a9c1b-report-txt`. If two names would still collide, it adds a short fingerprint. This is like labeling boxes by both the room they came from and what is inside.

The `ArtifactObjects` class is the main object-store surface. It can list visible artifacts, return details for one artifact, report its status, and delete it. Status is especially important: it may copy the latest stored bytes back into the workspace under `artifacts/<name>/<filename>`, so a later turn can reuse a file made earlier. It can also create a temporary download link. Creating or updating artifacts is refused, because artifacts must come from sharing a real workspace file, not from direct object edits.

#### Function details

##### `artifact_object_names`  (lines 67–87)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Builds the public object name for each artifact identity, where an identity is a conversation plus a filename. It keeps artifacts from different conversations separate, even when the filenames match.

**Data flow**: It receives pairs of conversation id and filename. It turns each filename into a safe short slug, prefixes it with part of the conversation id, checks whether any generated names collide, and adds a short digest only for collisions. It returns a dictionary from each original identity to its final object name.

**Call relations**: ArtifactObjects._groups calls this after reading artifact rows from the database. This naming step lets later listing and lookup code talk about artifacts by one stable name instead of raw database fields.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_groups); 1 external calls (Counter).


##### `_slug`  (lines 90–92)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, safe name fragment suitable for an object name. It removes awkward punctuation and normalizes the text so names are easier to read and compare.

**Data flow**: It takes a filename string, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims extra dashes, and limits the length. If nothing usable remains, it returns the fallback word `artifact`.

**Call relations**: artifact_object_names uses this when creating the readable part of an artifact object name. It is the small cleaning step before collision checking happens.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 95–97)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a shortable fingerprint for an artifact identity. This is used only when two different artifacts would otherwise receive the same visible name.

**Data flow**: It takes a conversation id and filename, combines them into one string, and hashes that string with SHA-256, a standard one-way fingerprinting method. It returns the full hexadecimal hash, and callers take the needed prefix.

**Call relations**: artifact_object_names calls this when duplicate generated names are found. The digest lets the system keep names unique without making every normal name long and noisy.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 115–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible to the current tool turn. It is what the object system uses when an agent asks what shared files are available.

**Data flow**: It reads the current context's allowed subjects, fetches matching artifact groups, turns each group into a display row, and passes those rows through paging and filtering. It returns an ObjectPage containing the visible artifact summaries.

**Call relations**: This is the turn-time listing entry for artifacts. It relies on _groups to collect versioned artifacts and on _row to make each one readable before handing the result to the shared object_page helper.

*Call graph*: calls 2 internal fn (_groups, _row); 1 external calls (object_page).


##### `ArtifactObjects.member_page`  (lines 119–133)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects for a signed-in member viewing them outside an active tool turn, such as in a portal. It uses the member's own audience scope rather than the tool context's read scope.

**Data flow**: It receives member information and a list query. It derives the audiences that member may read, fetches artifact groups for those audiences, converts them into rows, and returns a paged ObjectPage.

**Call relations**: This mirrors ArtifactObjects.list for portal-style reads. It calls the audience helpers to decide what the member can see, then uses the same _groups and _row path as normal artifact listing.

*Call graph*: calls 2 internal fn (_groups, _row); 3 external calls (audience_subjects, conversation_audience, object_page).


##### `ArtifactObjects.get`  (lines 135–137)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Returns the saved description of one artifact visible to the current tool turn. It does not copy file bytes into the workspace; that happens through status.

**Data flow**: It takes the current context and an artifact object name. It searches visible artifact groups for that name, returns None if not found, or converts the matching versions into an ObjectDetail with the latest filename, media type, caption, timestamps, and conversation link.

**Call relations**: The object system calls this when a turn asks for one artifact's details. It delegates name lookup to _find and formatting to _detail.

*Call graph*: calls 2 internal fn (_find, _detail).


##### `ArtifactObjects.member_detail`  (lines 139–153)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Returns both the list-row view and detail view for one artifact as seen by a signed-in member outside a turn. It deliberately does not copy bytes to a workspace or create a download link.

**Data flow**: It takes an artifact name and member information, derives the member's readable audiences, and searches for that artifact. If missing, it returns None. If found, it packages a row summary and detailed spec into a MemberObject.

**Call relations**: This is the portal-style counterpart to get. It uses the same _find, _row, and _detail helpers as the tool-facing methods, but starts from the member's audience rather than a ToolContext.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 155–199)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports practical runtime information for an artifact and, when safe, copies the latest file bytes back into the workspace so a later turn can reuse them. It can also mint a temporary download URL.

**Data flow**: It receives a context, artifact name, and optional expected generation. It finds the visible artifact, fetches its bytes from blob storage if the file is small enough, checks in the database that the conversation is still visible and unchanged, writes the bytes into the sandbox workspace if available, optionally creates a time-limited download link, and returns size, share time, turn id, version count, URL, and workspace path.

**Call relations**: This is called during object status/get flows when the system needs the artifact to become usable again in the workspace. It uses _find for lookup, _unchanged_visible for a safety check, the blob store for bytes, the sandbox for writing files, and mint_artifact_url for member downloads.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 5 external calls (__init__, now, mint_artifact_url, workspace_tx, ws_current).


##### `ArtifactObjects.apply`  (lines 201–210)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Refuses create or update attempts for artifacts. Artifacts can only be made by writing a file in the workspace and sharing it with `share_file`.

**Data flow**: It receives the proposed artifact spec and related context, but does not store or change anything. It immediately raises VerbNotSupported with guidance explaining that artifacts are shared, not directly edited.

**Call relations**: The object system would call this for create or update verbs. This artifact store stops that path and points users back to the proper producer, `share_file`.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 212–238)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact and every stored version of it. This removes both the database records and the blob-store bytes, so old download links stop working.

**Data flow**: It takes a context and artifact name, finds all visible versions of that artifact, and fails if none exist. Inside a database transaction, it locks and checks the latest visible conversation state, deletes all matching shared_artifact rows, and verifies the expected number were removed. After the database rows are gone, it deletes each version's blob data and any preview blob data from storage.

**Call relations**: The object system calls this for artifact deletion. It relies on _find to collect the versions and _unchanged_visible to guard against deleting an artifact that changed or became invisible during the operation.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 240–247)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database check that confirms the latest artifact version still belongs to the current workspace, selected agent, and readable audience. This protects status and delete from acting on stale or no-longer-visible data.

**Data flow**: It receives the current tool context and the latest artifact row. It produces a SQL select statement that looks for the owning conversation under the current workspace, current agent, same audience, and allowed read subjects. The function returns the query; callers execute it.

**Call relations**: ArtifactObjects.status uses this before copying bytes, and ArtifactObjects.delete uses it before removing rows. It is the safety gate between an earlier name lookup and a later side effect.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._find`  (lines 249–253)

```
async def _find(self, subjects: frozenset[str], name: str) -> tuple[sa.Row, ...] | None
```

**Purpose**: Looks up one artifact by its generated object name within a set of readable subjects. It returns all versions of that artifact, newest first, or says it was not found.

**Data flow**: It receives a set of audience subjects and a name. It asks _groups for all visible artifact groups, scans for the matching generated name, and returns that group's shares if present. If no group has that name, it returns None.

**Call relations**: get, member_detail, status, and delete all call this when they need to move from a user-facing artifact name to the underlying share rows. It is the common lookup step for single-artifact operations.

*Call graph*: calls 1 internal fn (_groups); called by 4 (delete, get, member_detail, status).


##### `ArtifactObjects._groups`  (lines 255–297)

```
async def _groups(self, subjects: frozenset[str]) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Collects raw shared artifact rows from the database and groups them into versioned artifact objects. This is where database records become the object model used by listing, lookup, status, and delete.

**Data flow**: It receives the readable audience subjects. It queries the current workspace for shared_artifact rows joined to their turns and conversations, limited to the selected agent and allowed audiences. It groups rows by conversation id and filename, assigns each group a generated object name, sorts each group's versions newest first, then returns all groups sorted by name.

**Call relations**: list and member_page call this to build pages, and _find calls it to locate one named artifact. It calls artifact_object_names so each grouped identity receives the same naming rules.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 3 (_find, list, member_page); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `_row`  (lines 300–311)

```
def _row(name: str, shares: tuple[sa.Row, ...]) -> ObjectRow
```

**Purpose**: Turns a versioned artifact group into a compact row for lists. The row shows the latest version's key facts and a short summary.

**Data flow**: It receives an artifact name and its shares, with the newest share first. It reads the latest filename, caption, conversation id, and share time, creates a short summary from all versions, and returns an ObjectRow.

**Call relations**: ArtifactObjects.list and ArtifactObjects.member_page use this for list pages, and member_detail uses it when returning a portal detail that also includes the row view. It depends on _summary for the human-readable sentence.

*Call graph*: calls 1 internal fn (_summary); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 314–330)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Builds the detailed object view for an artifact. It describes the latest version and links the artifact back to the conversation where it was created.

**Data flow**: It receives all shares for one artifact, newest first. It creates an ArtifactSpec from the latest share's filename, media type, and caption; sets created time from the oldest version and updated time from the newest; and adds a `created_in` link to the conversation.

**Call relations**: ArtifactObjects.get and ArtifactObjects.member_detail call this after finding an artifact. It is the formatting step that turns share rows into the object detail shape expected by the rest of the object system.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 333–339)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short human-readable summary for an artifact list row. It includes what the file is, how large it is, when it was shared, and whether it has multiple versions.

**Data flow**: It receives all shares for one artifact, newest first. It reads the latest filename, media type, size, and date, adds a version count when there is more than one share, and trims the result to the maximum summary length.

**Call relations**: _row calls this while building list rows. It keeps the list display concise so callers do not have to assemble their own artifact descriptions.

*Call graph*: called by 1 (_row).


### `core/src/ufo/tools/context.py`

`domain_logic` · `active during each tool call and throughout a turn`

A tool in this system is not allowed to freely reach into the whole application. Instead, it receives a ToolContext, which is like a guest badge: it says where the tool may go, what it may read, and whose authority it is using. This file defines that badge and the small result types that tools return.

The context includes access to the sandbox for files and shell commands, a blob store for artifacts, the current turn and agent, the current audience, connected account grants, credential-request helpers, subagent controls, browser and search providers, loaded skills, and cleanup hooks. These pieces keep tool work tied to the right workspace, user, agent, and conversation.

The file also defines how tools safely delegate work to subagents, how background subagents can be checked or cancelled, and how per-turn resources are closed at the end. Permission-related helpers answer questions such as “who is this tool acting as?”, “what audience should this write belong to?”, “is the speaker an admin?”, and “which external account can this turn use?”

Without this file, tools would either need direct access to too much of the system, which risks leaks and privilege mistakes, or every tool would have to reimplement the same permission and cleanup rules.

#### Function details

##### `UnknownSubagentProfile.__init__`  (lines 91–96)

```
def __init__(self, requested: str, registered: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when code asks for a subagent profile name that is not registered. The error includes both the bad name and the valid choices, so the caller or model has enough information to try again.

**Data flow**: It receives the requested profile name and the tuple of registered names. It formats those into a human-readable error message and stores both pieces of information on the exception object. The result is an exception ready to be raised and inspected.

**Call relations**: The subagent registry calls this when a lookup fails. Instead of letting a plain missing-key error escape, the registry hands back a specific explanation of which profile was unknown and what profiles exist.

*Call graph*: called by 1 (get).


##### `UnknownSpawnTarget.__init__`  (lines 103–110)

```
def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None
```

**Purpose**: Builds a clear error when a spawn request names something that is neither a known subagent profile nor a workspace agent. It tells the caller what names are actually spawnable.

**Data flow**: It receives the requested target name, the available profile names, and the available agent names. It combines them into an error message and stores them as fields. The output is an exception that carries enough context to diagnose the failed spawn.

**Call relations**: The subagent spawning resolver calls this when it cannot match a target. This helps a tool surface a retryable error instead of failing with an unclear lookup problem.

*Call graph*: called by 1 (_resolve).


##### `AmbiguousSpawnTarget.__init__`  (lines 117–122)

```
def __init__(self, requested: str) -> None
```

**Purpose**: Builds an error for the case where one bare spawn name could mean either a profile or an agent. It tells the caller to use an explicit prefix so the system does not guess wrong.

**Data flow**: It receives the ambiguous name. It creates an error message that suggests the two qualified forms, such as profile:name or agent:name, and stores the requested name. The result is an exception that explains how to fix the ambiguity.

**Call relations**: The subagent spawning resolver calls this when a target name exists in both namespaces. This keeps delegation safe by forcing the caller to say which kind of child it wants.

*Call graph*: called by 1 (_resolve).


##### `Spawn.__call__`  (lines 188–197)

```
async def __call__(self, target: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None, delivers_result: bool=False, name: str='', detach_on_arrival: bool=False) -> SpawnRes
```

**Purpose**: Describes the callable interface used to delegate a task to a child turn, either a subagent profile or another workspace agent. A tool uses this when it wants another specialized worker to do part of the job.

**Data flow**: It takes a target name, an input payload, and options such as whether to run in the background, how to deduplicate retries, and whether the child should deliver its own result. An implementation validates the payload, starts or reconnects to the child turn, waits if appropriate, and returns a SpawnResult describing the child and any finished output.

**Call relations**: This is a protocol, meaning it states the shape of the function that the runtime provides on ToolContext. Tool code calls ctx.spawn, while the actual subagent workflow supplies the implementation behind this interface.


##### `SubagentControl.result`  (lines 207–207)

```
async def result(self, turn_id: UUID) -> SpawnResult
```

**Purpose**: Describes how a tool can retrieve the final result of an already-spawned background subagent. It is used when the child was started earlier and the caller now wants its completed output.

**Data flow**: It takes a child turn id. An implementation looks up that child, checks its terminal state and validated output, and returns a SpawnResult. It does not create a new child; it reads the state of an existing one.

**Call relations**: This is part of the SubagentControl protocol placed on ToolContext. Tools use it through ctx.subagents, and the subagent lifecycle system provides the real behavior.


##### `SubagentControl.wait`  (lines 209–209)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: Describes how a tool can wait for one or more background subagents and get their current terminal statuses. This is useful when several child tasks were started and the parent wants to pause until they finish or report progress.

**Data flow**: It receives a tuple of child turn ids. An implementation waits according to the runtime’s rules, gathers each child’s status and final text if available, and returns a tuple of SubagentStatus objects.

**Call relations**: This protocol method is offered through ToolContext by the subagent workflow. Tool handlers call it when coordinating background child turns.


##### `SubagentControl.cancel`  (lines 211–211)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: Describes how a tool can stop a running background subagent. It gives the parent a way to end child work that is no longer needed.

**Data flow**: It takes a child turn id. An implementation finds that child, requests cancellation, and returns a SubagentStatus describing the child’s resulting state and message.

**Call relations**: This is another operation supplied by the subagent lifecycle system through ctx.subagents. Tools call it when they decide a background child should not continue.


##### `SubagentControl.message`  (lines 213–215)

```
async def message(self, turn_id: UUID, text: str, dedup_key: str, delivers_result: bool=False) -> SubagentStatus
```

**Purpose**: Describes how a tool can send a follow-up message to an already-spawned subagent. The deduplication key helps avoid sending the same follow-up twice after a retry.

**Data flow**: It receives a child turn id, message text, a deduplication key, and whether the child should deliver the result itself. An implementation admits that message to the child turn once and returns a SubagentStatus showing what happened.

**Call relations**: This protocol method is exposed on ToolContext when subagent control is available. It connects parent-tool decisions to a running child’s conversation.


##### `TurnCleanup.register`  (lines 229–230)

```
def register(self, aclose: Callable[[], Awaitable[None]]) -> None
```

**Purpose**: Adds an asynchronous cleanup action to be run when the turn ends. Tools use this after opening something that must be closed, such as a browser connection or hosted session.

**Data flow**: It receives an async close function and appends it to the cleanup list. Nothing is closed immediately. The change is stored in the TurnCleanup object for later.

**Call relations**: Tools that create per-turn resources call this during first use. Later, the turn loop drains the cleanup registry so resources do not leak after the turn finishes or fails.


##### `TurnCleanup.drain`  (lines 232–238)

```
async def drain(self) -> None
```

**Purpose**: Runs all registered cleanup actions, closing resources at the end of a turn. It keeps one failed cleanup from stopping the rest.

**Data flow**: It reads the stored close functions, removes them one by one in reverse order, and awaits each one. If a closer raises an error, it logs the failure and continues. Afterward, the cleanup list is empty.

**Call relations**: The turn loop calls this at turn end. It hands failures to the observability logger, so cleanup problems are visible without leaving later resources unclosed.

*Call graph*: 1 external calls (log).


##### `ToolContext.acting_member_id`  (lines 281–289)

```
def acting_member_id(self) -> UUID | None
```

**Purpose**: Figures out which workspace member’s authority this tool call is using. It prefers the live speaker, and falls back to the member the turn is acting on behalf of.

**Data flow**: It reads speaker_member_id and on_behalf_of_member_id from the context. If there is a current speaker, it returns that id; otherwise it returns the carried behalf-of id, which may also be absent.

**Call relations**: Other ToolContext helpers use this as the common answer to “who is this call for?” It feeds audience calculation and connector account selection.


##### `ToolContext.effective_audience`  (lines 292–302)

```
def effective_audience(self) -> Audience
```

**Purpose**: Decides what audience should own a write made by this tool. This prevents private or cross-organization conversation facts from being stamped into the wrong memory space.

**Data flow**: It reads the current audience and the acting member. If there is no acting member, or the conversation is not the workspace-shared audience, it returns the existing audience. If a member is acting in a shared workspace conversation, it returns that member’s conversation audience.

**Call relations**: When tools write information, this property supplies the safe audience label. It calls the audience helper that builds a member-specific conversation audience when that special shared-room rule applies.

*Call graph*: 1 external calls (conversation_audience).


##### `ToolContext.read_subjects`  (lines 305–314)

```
def read_subjects(self) -> frozenset[str]
```

**Purpose**: Computes the set of subjects this tool may read from: the conversation’s subjects plus the requester’s own private subject. This is a privacy boundary for memory and source lookups.

**Data flow**: It starts with the subjects implied by the current audience. If there is an acting member, it adds only that member’s private subject. It returns the combined set as an immutable frozenset.

**Call relations**: Source and memory readers use this to decide which stored pages or facts are visible. It relies on audience and member-subject helpers to translate people and audiences into readable subject labels.

*Call graph*: 2 external calls (audience_subjects, member_subject).


##### `ToolContext.source_reader`  (lines 316–326)

```
def source_reader(self) -> SourceReader
```

**Purpose**: Creates a SourceReader that says who is asking to read synced source pages. This packages the current agent, live speaker, and readable subjects into one object for source-related extensions.

**Data flow**: It reads the turn’s agent id, the current speaking member id, and the computed read_subjects. It builds and returns a SourceReader with those values. The context itself is not changed.

**Call relations**: Memory and source extensions call this when searching, listing, or fetching source-backed content. It hands them a consistent permission view rather than making each extension rebuild the same rules.

*Call graph*: called by 5 (memory_search_handler, get, list, _pages, get); 1 external calls (__init__).


##### `ToolContext.meter_images`  (lines 328–337)

```
async def meter_images(self, model: str, images: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated images against this turn’s accounting ledger. Image providers may know their own price, but core records the spend in the workspace’s official usage records.

**Data flow**: It receives the model name, image count, and cost in micro-dollars. It opens a workspace database transaction and writes an image-usage record tied to the workspace and turn. It returns nothing, but the ledger is updated.

**Call relations**: The OpenRouter image extension calls this after generating images. This function hands the actual database write to the accounting layer inside a workspace transaction.

*Call graph*: called by 1 (generate); 2 external calls (record_image_usage, workspace_tx).


##### `ToolContext.meter_videos`  (lines 339–347)

```
async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None
```

**Purpose**: Records the cost of generated videos against this turn’s accounting ledger. This keeps video generation charges attached to the same workspace and turn as other model spending.

**Data flow**: It receives the model name, video count, and cost in micro-dollars. It opens a workspace database transaction and writes a video-usage record for this workspace and turn. It returns nothing, but accounting state changes.

**Call relations**: The OpenRouter video extension calls this after generating videos. The function delegates the ledger write to the accounting helper inside a workspace transaction.

*Call graph*: called by 1 (generate); 2 external calls (record_video_usage, workspace_tx).


##### `ToolContext.speaker_is_admin`  (lines 349–359)

```
async def speaker_is_admin(self) -> bool
```

**Purpose**: Checks whether the live requesting speaker is a workspace administrator. It intentionally returns false when there is no live speaker, so background work cannot silently use admin power.

**Data flow**: It reads speaker_member_id. If no one is speaking, it returns false. Otherwise it opens a workspace transaction, asks the seats subsystem whether that member is an admin in this workspace, and returns the boolean answer.

**Call relations**: Many object and credential operations call this before allowing workspace-wide or sensitive actions. It centralizes the admin check so callers do not each interpret background authority differently.

*Call graph*: called by 20 (apply, delete, apply, delete, get, list, status, request_credentials_handler, _credential_authorization, connect_github (+10 more)); 2 external calls (workspace_tx, member_is_admin).


##### `ToolContext.agent_is_main`  (lines 361–372)

```
async def agent_is_main(self) -> bool
```

**Purpose**: Checks whether the current agent is the workspace’s main agent. Some features expose different powers or visibility depending on whether the agent is the main one.

**Data flow**: It opens a workspace transaction and queries the agent table for the current turn’s agent id and workspace id. It reads the stored is_main value and returns it as a boolean, returning false if no matching row is found.

**Call relations**: Member, workspace, and web audience operations call this when deciding what the current agent is allowed to see or change. It uses SQLAlchemy to build the database query.

*Call graph*: called by 6 (add, _visible_rows, apply, status, _grant, _revoke); 2 external calls (select, workspace_tx).


##### `ToolContext.agent_visibility`  (lines 374–386)

```
async def agent_visibility(self) -> AgentVisibility
```

**Purpose**: Reads whether the current agent is private or workspace-visible. It also guards against unexpected stored values.

**Data flow**: It opens a workspace transaction, queries the agent table for the current agent’s visibility, and reads the stored string. If the value is private or workspace, it returns it; otherwise it raises a runtime error because the database contains a value this code does not understand.

**Call relations**: The sites extension calls this when setting a homepage. This helper gives that extension the agent visibility without making it query the agent table itself.

*Call graph*: called by 1 (set_homepage); 2 external calls (select, workspace_tx).


##### `ToolContext.begin_credential_authorization`  (lines 388–390)

```
async def begin_credential_authorization(self, slot: str, payload: str) -> str
```

**Purpose**: Starts an authorization flow for an extension credential slot, such as asking an admin to approve storing a secret. It returns a sealed authorization token that can be shown or passed through safely.

**Data flow**: It receives a credential slot name and a payload. It first runs the shared credential-authorization checks, getting the credential request service and member id. Then it asks that service to create an authorization for this workspace, member, slot, and payload, and returns the sealed string.

**Call relations**: Coding and Slack extension connection flows call this when they need a member to authorize a credential. It relies on _credential_authorization to enforce the common safety checks before creating anything.

*Call graph*: calls 1 internal fn (_credential_authorization); called by 2 (connect_github, _oauth_link).


##### `ToolContext.open_credential_authorization`  (lines 392–394)

```
async def open_credential_authorization(self, slot: str, sealed: str) -> str
```

**Purpose**: Opens and validates a sealed credential authorization for a given slot. This lets a tool recover the authorized payload only if it matches the same workspace, member, and credential slot.

**Data flow**: It receives a slot name and sealed authorization string. It runs the shared authorization checks, then asks the credential request service to open the sealed value for this workspace, member, and slot. It returns the opened payload string.

**Call relations**: This is the read side of the credential authorization flow. It uses the same _credential_authorization gate as beginning and fulfilling authorization, so all three steps follow the same rules.

*Call graph*: calls 1 internal fn (_credential_authorization).


##### `ToolContext.fulfill_credential_authorization`  (lines 396–401)

```
async def fulfill_credential_authorization(self, slot: str, sealed: str, plaintext: str) -> None
```

**Purpose**: Completes a credential authorization by validating the sealed approval and storing the plaintext secret in the workspace. This is the point where the approved credential is actually saved.

**Data flow**: It receives a slot name, sealed authorization, and plaintext secret. It runs the shared checks, opens the sealed authorization to prove it is valid, then writes the plaintext into the current workspace’s credential store. It returns nothing, but the credential store changes.

**Call relations**: Credential setup flows call this after authorization has been granted. It uses _credential_authorization for safety and then uses the current workspace object to store the secret.

*Call graph*: calls 1 internal fn (_credential_authorization); 1 external calls (ws_current).


##### `ToolContext._credential_authorization`  (lines 403–412)

```
async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]
```

**Purpose**: Performs the common safety checks needed before any credential authorization action. It makes sure there is a live speaker, the extension declared the credential slot, credential storage is configured, and the speaker is an admin.

**Data flow**: It receives the slot name and reads speaker_member_id, extension metadata, configured credential request support, and admin status. If any requirement is missing, it raises a ValueError. If everything is valid, it returns the credential request service and the speaker’s member id.

**Call relations**: The begin, open, and fulfill credential authorization methods all call this first. It in turn calls speaker_is_admin, so admin-only credential storage has one shared gate.

*Call graph*: calls 1 internal fn (speaker_is_admin); called by 3 (begin_credential_authorization, fulfill_credential_authorization, open_credential_authorization).


##### `ToolContext.connector_account`  (lines 414–423)

```
async def connector_account(self, provider: str, account_id: str | None=None) -> str
```

**Purpose**: Returns the external connector account id that a connector tool is allowed to use. It is a convenience wrapper for callers that only need the broker’s account id, not the full connection details.

**Data flow**: It receives a provider name and optionally a desired account id. It asks connector_connection to resolve the exact permitted connection, then returns only that connection’s account_id. The context is not changed.

**Call relations**: Connector execution tools and sample connector code call this before making broker-side external tool calls. It delegates the permission and ambiguity checks to connector_connection.

*Call graph*: calls 1 internal fn (connector_connection); called by 2 (call_external_tool, _connector_execute).


##### `ToolContext.connector_connection`  (lines 425–462)

```
async def connector_connection(self, provider: str, account_id: str | None=None) -> ConnectorConnection
```

**Purpose**: Selects the exact connected account this turn may use for a provider. It enforces private-by-default account access and reports clear errors when no account, the wrong account, or too many accounts are available.

**Data flow**: It receives a provider name and optionally a requested account id. It gets private and shared grant tiers, then either finds the requested account among them or chooses one account from the preferred tier. It returns a ConnectorConnection containing the connection id, external account id, and owning member id, or raises a ValueError if selection is impossible.

**Call relations**: connector_account calls this when only the account id is needed, and source registration code calls it when it needs the full connection identity. It depends on _connector_account_tiers to separate private grants from shared grants before making a choice.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 2 (connector_account, _resolved_account); 1 external calls (__init__).


##### `ToolContext.connector_accounts`  (lines 464–472)

```
async def connector_accounts(self, provider: str) -> tuple[str, ...]
```

**Purpose**: Lists all connected account ids this turn may use for one provider. This lets tools show or validate the available choices without selecting one yet.

**Data flow**: It receives a provider name. It gets the private and shared grant tiers, combines their account ids, removes duplicates, sorts them, and returns them as a tuple. It does not modify grants or connections.

**Call relations**: Source registration code calls this when resolving which account should back a source. It uses _connector_account_tiers so the list follows the same access rules as connector_connection.

*Call graph*: calls 1 internal fn (_connector_account_tiers); called by 1 (_resolved_account).


##### `ToolContext._connector_account_tiers`  (lines 474–493)

```
async def _connector_account_tiers(self, provider: str) -> tuple[list[Grant], list[Grant]]
```

**Purpose**: Divides active connector grants into two groups: private grants owned by the acting member, and shared grants available to the agent. This is the core permission filter for connector account access.

**Data flow**: It receives a provider name and reads the configured grant store and acting member id. If grants are unavailable, it raises ConnectUnavailable. Otherwise it loads active grants, filters them by provider and sharing rules, sorts each group by account id, and returns the private and shared lists.

**Call relations**: connector_connection and connector_accounts both call this before selecting or listing accounts. By putting the filtering here, both higher-level methods use the same private-versus-shared access rule.

*Call graph*: called by 2 (connector_accounts, connector_connection); 1 external calls (__init__).


### `core/src/ufo/tools/registry.py`

`data_model` · `startup and tool dispatch`

The project lets a model call named tools, such as reading a page, searching, writing a file, or asking another service to do something. This file gives each tool a clear record: its name, its plain description, the shape of input it expects, and the function that actually runs it. It also stores important safety flags. For example, a tool marked untrusted may return text from the outside world, so the engine must not treat that text as instructions. A tool marked side_effecting can change something outside the model, like posting to an API or writing durable data, so the engine can attach an idempotency key, which is a repeat-safe label that helps avoid doing the same external action twice after a retry.

The file also builds the schema sent to the model client. A schema is a machine-readable description of what arguments a tool accepts. Every tool schema is extended with a reserved requested_by field, used to say which message explicitly authorized a member-specific action.

ToolRegistry is the fixed catalog the engine uses at runtime. When created, it refuses duplicate tool names and refuses tools that already define the reserved requested_by input. Later, the engine can ask for all schemas or look up one tool by name. Without this file, tool calls would be harder to validate, dispatch safely, and explain to the model.

#### Function details

##### `ToolDef.schema`  (lines 53–65)

```
def schema(self) -> ToolSchema
```

**Purpose**: Builds the public description of one tool that can be sent to the model client. It combines the tool’s name and description with the input shape expected by its Pydantic model, then adds the standard requested_by field used for authorization context.

**Data flow**: It starts with a ToolDef, reads its input model, and asks that model for a JSON-style input schema. It makes sure the schema has a properties section, adds the reserved requested_by property with its type and explanation, and returns a ToolSchema object containing the tool name, description, and completed input schema.

**Call relations**: This is the bridge from the internal tool definition to the wire format the model sees. It creates a ToolSchema object, and ToolRegistry.schemas uses it when the system needs to publish the whole catalog of callable tools.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 72–81)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a newly created registry is safe and unambiguous. It prevents two tools from having the same name, and it protects the reserved requested_by field from being reused by a tool’s own input model.

**Data flow**: It receives the registry after construction, reads all tool names, and looks for repeated names. It also inspects each tool’s input fields to see whether any tool has already claimed requested_by. If either problem is found, it raises an error immediately; otherwise, the registry remains usable and unchanged.

**Call relations**: This runs automatically when a ToolRegistry is created. It acts like a gatekeeper at setup time, so later dispatch code can trust that a tool name points to exactly one tool and that the authorization field can be added consistently.


##### `ToolRegistry.schemas`  (lines 83–84)

```
def schemas(self) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the model-facing schemas for every registered tool. This is used when the system needs to tell the model which tools exist and what arguments each one accepts.

**Data flow**: It reads the registry’s tuple of ToolDef objects, asks each one to produce its ToolSchema, and returns those schemas as a tuple. It does not change the registry.

**Call relations**: This function gathers the individual schemas produced by ToolDef.schema into one catalog. It is part of the setup or prompting path where the engine exposes available tools to the model.


##### `ToolRegistry.get`  (lines 86–90)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the registered tool definition with a given name. The engine uses this when the model asks to call a tool and the system must locate the matching handler function and safety settings.

**Data flow**: It takes a tool name as input, scans the registry’s tools one by one, and returns the ToolDef whose name matches. If no registered tool has that name, it raises a KeyError so the mistake is caught loudly instead of silently calling the wrong thing.

**Call relations**: During tool execution, core/src/ufo/loop/engine._dispatch_segments calls this lookup after seeing a requested tool name. The returned ToolDef gives the engine the handler to run and the flags, such as untrusted or side_effecting, that shape how the result or action is treated.

*Call graph*: called by 1 (_dispatch_segments).

## 📊 State Registers Touched

- `reg-effective-config` — The merged deployment settings that tell the process how to run, which services to use, and which safety options are enabled.
- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-session-auth` — The login sessions, signed tokens, protected links, callback state, and request identities proving who a visitor or service is.
- `reg-visibility-boundaries` — The saved rules for who may see each conversation, agent, transcript, source, memory, artifact, or workspace object.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-runtime-fleet` — The records of running server or worker instances, their heartbeats, listener claims, and cleanup ownership.
- `reg-live-stream-state` — The live update stream that broadcasts turn progress, tool activity, subagent activity, cancellations, and final replies to connected clients.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-tool-catalog` — The shared catalog of tools the model can call, including built-in tools, extension tools, connector tools, and their safety labels.
- `reg-tool-execution-context` — The per-turn but shared rulebook passed through tools, saying who the tool acts for, what files, accounts, sandboxes, and subagents it may use.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-egress-policy` — The network access rules and proxy authorization state that decide what sandboxed code may contact outside the system.
- `reg-file-blob-store` — The shared byte storage for uploads, generated files, previews, media, and other raw data, separated by workspace or deployment scope.
- `reg-artifact-registry` — The saved list of files deliberately shared with users, including ownership, access checks, preview metadata, and download links.
- `reg-browser-site-runtime` — The shared browser sessions, hosted preview servers, public site links, and ownership records used to browse, test, and publish websites.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-extension-object-slots` — The extension-owned object and conversation-panel data, such as artifacts, sources, tasks, sites, automations, and custom workspace objects.
- `reg-workspace-change-log` — The saved record of file changes made during a conversation, used to explain later what the agent changed in the workspace.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-skill-assets-state` — The discovered skill packages, dependency metadata, copied helper files, and per-agent skill asset state used when building prompts and executing skill-backed work.
- `reg-pending-human-interactions` — The durable pending questions, credential-collection prompts, setup requests, and checklist-style waits that tools create and surfaces later resolve.
- `reg-sandbox-image-cache` — The local or remote sandbox image/build cache and validation state used to choose, compare, and launch safe execution environments.
- `reg-connector-action-cache` — Dynamic connector/MCP action schemas, allowed-action listings, and runtime client/session caches reused when exposing and executing external-service actions.
- `reg-active-cancellation-handles` — Process-local abort tokens and cancellation handles that bridge durable cancel requests to currently running turns, tools, sandboxes, and child turns.
- `reg-turn-created-references` — Saved references or citations created by a turn so final replies, source panels, transcripts, and later turns can resolve cited material consistently.
- `reg-conversation-workspace-files` — The mutable per-conversation working file tree that tools, skills, document automation, site building, artifacts, and cleanup read or modify before changes are snapshotted or shared.
