# Reply delivery, live updates, panels, artifacts, and hosted outputs  `stage-19`

This stage is the return path from the system back to the person watching it. As a conversation turn runs, it turns internal work into visible updates, then makes finished results easy to open, share, and reuse. activity.py writes short human-friendly status messages, such as when a tool is about to run. hub_tail.py keeps the live update stream dependable, combining quick in-memory messages with database checks so late viewers still see the turn finish.

The other files deal with outputs that live beyond a single message. artifacts.py makes generated shared files show up as reusable workspace items. The sandbox ingress files protect hosted outputs: ingress_host.py creates valid web addresses for sandboxed sites, and ingress_token.py checks short-lived signed passes for opening a specific port. The Sites extension then presents these hosted results. conversation_slot.py fills the side panel with sites the viewer may access. objects.py makes sites manageable as workspace objects, including visibility changes and unhosting. surface.py serves the public site page, checks permission, and displays the site safely inside a protected frame.

## Files in this stage

### Member-facing turn updates
These files shape tool activity into readable status items and stream turn progress reliably to live viewers.

### `core/src/ufo/activity.py`

`domain_logic` · `during tool-call reporting`

When the system calls a tool, the raw request can contain a lot of internal detail. This file creates the simpler version shown to a member, like turning a kitchen order ticket into a short status message for a diner. Its main job is to take a ToolUseBlock, which is the system’s structured record of a tool call, and convert it into one of two activity objects from the hub layer.

There is one special case: if the tool name is load_skill, the activity is shown as a SkillLoad. The code looks for the requested skill name in the input and uses it only if it is really text; otherwise it falls back to an empty string. All other tools become a ToolCall activity. For those, the file builds a compact JSON preview of the tool input, trims it to 200 characters so the display cannot become too noisy, and includes a human-written user_description if one was provided as text.

Without this file, the user interface or event stream would either expose raw tool-call data directly or need to duplicate this display-shaping logic elsewhere.

#### Function details

##### `tool_activity`  (lines 12–25)

```
def tool_activity(call: ToolUseBlock) -> ToolCall | SkillLoad
```

**Purpose**: This function creates the single activity item that should be shown for a tool call. It hides internal clutter, gives skill-loading its own clearer shape, and limits long tool inputs to a short preview.

**Data flow**: It receives a ToolUseBlock containing a tool name and an input dictionary. If the name is load_skill, it reads the input field named name and returns a SkillLoad object with that skill name, or an empty name if the value is not text. Otherwise, it reads an optional user_description, turns the full input into compact JSON text, shortens that text if it is over 200 characters, and returns a ToolCall object containing the tool name, preview, and safe description.

**Call relations**: When another part of the system needs to show or publish what a bound tool call is doing, it calls tool_activity. Inside, this function uses json.dumps to make the input readable as compact text, then hands the final display-ready data to either SkillLoad.__init__ for the special skill-loading case or ToolCall.__init__ for ordinary tool calls.

*Call graph*: 3 external calls (__init__, __init__, dumps).


### `core/src/ufo/surfaces/hub_tail.py`

`orchestration` · `request handling / live streaming`

A surface is a way for users or clients to watch what is happening. This file is the bridge that lets a surface follow one turn as it produces live frames, like tokens or status updates. The tricky part is timing: a viewer may connect after the turn has started, after some frames have already passed, or even after the turn finished on another event loop. To make that safe, the code listens to two sources at once. First, it subscribes to the hub, which is the fast live message channel. Second, it repeatedly checks the durable stored turn state in the database. Think of it like watching a race both through a live camera and the official scoreboard: the camera is immediate, but the scoreboard is the final authority. If the live hub says the turn ended, the stream ends. If the database says the turn ended or became parked, that also ends the stream. A parked turn is not truly finished; it is paused, usually because of a spending cap or revoked seat access, so the file turns that stored state into a clear parked message for the viewer. The main public wrapper, HubTailer, exposes this as a scoped async stream, so when the caller stops listening, the background subscription and polling tasks are cancelled cleanly.

#### Function details

##### `tail_frames`  (lines 28–59)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='') -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: This is the main streaming loop for one turn. It yields live frames until the turn reaches a final state or becomes parked, and it still works when the caller connects late.

**Data flow**: It receives a hub, a turn id, and optionally the last cursor the caller already saw. It checks whether the hub can resume from that cursor; if not, it starts from the available retained stream. It then starts two background workers: one that listens to hub frames and one that polls the database for an ending state. Before waiting on live data, it checks the stored turn state once, so an already-finished turn returns immediately. It yields each frame it receives, and when a terminal or parked frame appears, it stops. When the generator is closed, it cancels the background workers so they do not keep running.

**Call relations**: HubTailer.tail creates this generator for callers that want to watch a turn. Inside, tail_frames asks the hub whether a resume cursor is still covered, starts _pump to bring in live hub messages, starts _poll_status to bring in durable ending information, and calls turn_status_frame both for the immediate first check and through the poller. It hands frames back to the surface layer one by one.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, turn_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 62–69)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper copies live frames from the hub subscription into the shared queue used by tail_frames. It is the fast path for updates that are happening right now.

**Data flow**: It receives the hub, the turn id, the cursor to start from, and a queue. It subscribes to the hub for that turn, reads each incoming item, and puts it into the queue. It does not return useful data; its job is to keep feeding the queue until the subscription ends or the task is cancelled. If something goes wrong, it records a log message rather than crashing the whole tail.

**Call relations**: tail_frames starts _pump as a background task while a caller is watching a turn. _pump relies on the hub subscription for live data and passes that data back through the queue. If it fails, _poll_status can still provide the important final or parked state from storage.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 72–81)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper checks the database once per second to see whether the turn has ended or become parked. It is the safety net that makes the stream reliable even if a live hub message is missed.

**Data flow**: It receives a turn id and the same queue used for live frames. It sleeps for a fixed interval, asks turn_status_frame whether the stored turn now has an ending or parked state, and, if so, puts that frame into the queue with an empty cursor. Then it stops. If the polling task has an error, it logs the problem.

**Call relations**: tail_frames starts _poll_status alongside _pump. While _pump listens to immediate hub messages, _poll_status keeps checking the durable stored state through turn_status_frame. Whichever source produces the ending frame first causes tail_frames to finish the stream.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `turn_status_frame`  (lines 84–109)

```
async def turn_status_frame(turn_id: UUID) -> LiveFrame | None
```

**Purpose**: This function translates the stored database state of a turn into the live frame that should end the viewer's stream. It returns a terminal frame for a finished turn, a parked frame for a paused turn, or nothing while the turn is still active.

**Data flow**: It receives a turn id. It opens a workspace database transaction and reads the turn's status, stored terminal frame, workspace, speaker, behalf-of member, and admission source. If no row exists, it returns nothing. If a terminal record is stored, it validates that record and wraps it as a Terminal frame. If the turn is not parked, it returns nothing. If the turn is parked, it checks whether the relevant member is still admitted to the workspace seats; if access was revoked, it returns a parked frame with that specific message. Otherwise it returns a general spending-cap parked notice.

**Call relations**: tail_frames calls this immediately when a viewer attaches, so already-finished turns can end without waiting. _poll_status calls it repeatedly as the durable fallback. It uses the database transaction helper, schema table definitions, terminal-frame validation, and seat admission checks to decide what kind of stream-ending frame should be shown.

*Call graph*: called by 2 (_poll_status, tail_frames); 7 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx, gate_member).


##### `HubTailer.tail`  (lines 121–124)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: This method exposes turn tailing as a clean async context manager. Callers use it to receive frames without needing to know about the hub, polling, or cleanup details.

**Data flow**: It receives a turn id and optional resume cursor. It calls tail_frames with the HubTailer object's hub and wraps the resulting async generator in a closing context. The caller gets an async iterator of cursor-and-frame pairs, and when the caller leaves the context, the generator is closed and its background tasks are cleaned up.

**Call relations**: Surface code calls HubTailer.tail when it needs to stream one turn. HubTailer.tail delegates the real work to tail_frames and uses aclosing so that the caller's context lifetime controls the lifetime of the hub subscription and polling task.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


### Reusable file artifacts
This group defines shared turn outputs as workspace artifacts that can be listed, inspected, reused, copied, or removed.

### `core/src/ufo/artifacts.py`

`domain_logic` · `request handling`

An artifact is a file that a tool or agent has deliberately shared with `share_file`. This file turns those shared-file records into a clean object interface: users can list them, get details, copy the latest version back into the workspace, and delete all stored versions. Without this layer, shared files would just be database rows and blobs, with no consistent names, visibility rules, or safe way to retrieve them later.

The key idea is that an artifact is identified by both the conversation that shared it and the filename. If the same conversation shares the same filename again, that becomes a newer version of the same artifact. If another conversation shares a file with the same name, it is a different artifact. The public object name combines a short conversation prefix with a cleaned-up filename, like putting a labeled tab on a folder so related files sort together.

The file also enforces boundaries. It only shows artifacts for the current workspace, selected agent, and allowed audience. `get` returns descriptive information. `status` is the special step that may copy the latest file bytes into the sandbox workspace and mint a temporary download link. `apply` refuses edits, because artifacts are created only by sharing files. `delete` removes every version’s database row and stored bytes.

#### Function details

##### `artifact_object_names`  (lines 67–87)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Builds stable, readable object names for shared files. It keeps files from different conversations separate even when their filenames match.

**Data flow**: It receives pairs of conversation ID and filename. It cleans each filename into a short slug, prefixes it with part of the conversation ID, then checks whether any names still collide. If two distinct files would get the same name, it adds a short digest so each final name is unique.

**Call relations**: ArtifactObjects._groups calls this after reading artifact rows from the database. The names it returns become the names used by list, get, status, and delete.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_groups); 1 external calls (Counter).


##### `_slug`  (lines 90–92)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a safe, short name fragment for use in an artifact object name. This makes names easier to read and less likely to contain awkward characters.

**Data flow**: It takes a filename, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims it to the configured length, and falls back to `artifact` if nothing usable remains.

**Call relations**: artifact_object_names uses this while building the main visible name for each artifact.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 95–97)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a shortable fingerprint for a specific conversation-and-filename identity. It is used only when normal readable names would collide.

**Data flow**: It takes a conversation ID and filename, joins them into one string, and runs SHA-256 over it. The caller can then use the beginning of that digest as a collision suffix.

**Call relations**: artifact_object_names calls this only for identities whose readable names are not unique.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 115–117)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects visible inside a tool turn. It is the normal object-listing view for files shared with the current agent and audience.

**Data flow**: It reads the allowed subjects from the tool context, asks _groups for the matching artifact groups, turns each group into a compact row, and wraps those rows into a paged result.

**Call relations**: This is called by the object system when a turn asks to list artifacts. It relies on _groups for database lookup and _row for the display shape, then hands the rows to object_page for filtering, ordering, and paging.

*Call graph*: calls 2 internal fn (_groups, _row); 1 external calls (object_page).


##### `ArtifactObjects.member_page`  (lines 119–133)

```
async def member_page(self, ext: ExtensionContext | None, *, member_id: UUID, admin: bool, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Lists artifact objects for a signed-in member outside an active tool turn, such as in a portal view. It uses the same visibility idea as turn-time listing, but derives the audience from the member.

**Data flow**: It receives the member ID, builds the subjects that member is allowed to read, fetches matching artifact groups, converts them to rows, and returns a paged object list. The admin flag is accepted but does not widen visibility here.

**Call relations**: The member-facing object surface calls this for page views. It uses conversation_audience and audience_subjects to decide what the member can see, then follows the same _groups, _row, and object_page path as ArtifactObjects.list.

*Call graph*: calls 2 internal fn (_groups, _row); 3 external calls (audience_subjects, conversation_audience, object_page).


##### `ArtifactObjects.get`  (lines 135–137)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Returns detailed metadata for one artifact visible during a tool turn. It does not copy file bytes into the workspace; that happens through status.

**Data flow**: It takes an object name and the current read subjects from the context. It searches for the matching artifact group; if none is found it returns null, otherwise it converts the newest version and history timestamps into an ObjectDetail.

**Call relations**: The object system calls this when a turn asks for a specific artifact. It delegates lookup to _find and formatting to _detail.

*Call graph*: calls 2 internal fn (_find, _detail).


##### `ArtifactObjects.member_detail`  (lines 139–153)

```
async def member_detail(self, ext: ExtensionContext | None, name: str, *, member_id: UUID, admin: bool) -> MemberObject[ArtifactSpec] | None
```

**Purpose**: Returns both the list row and detailed metadata for one member-visible artifact. It is the portal-style counterpart to get.

**Data flow**: It receives a member ID and artifact name, computes that member’s readable subjects, and searches for the artifact. If found, it returns a MemberObject containing the row summary and the detail; if not, it returns null.

**Call relations**: The member-facing object system calls this when showing one artifact. It uses the same audience helpers as member_page, then uses _find, _row, and _detail to assemble the response.

*Call graph*: calls 3 internal fn (_find, _detail, _row); 3 external calls (__init__, audience_subjects, conversation_audience).


##### `ArtifactObjects.status`  (lines 155–194)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Prepares the latest artifact version for practical use. It may copy the file into the sandbox workspace and may create a temporary download link.

**Data flow**: It receives an artifact name and the tool context. It finds the visible artifact, fetches its bytes from blob storage if the file is small enough, rechecks in the database that the conversation is still visible and unchanged, then writes the bytes to `artifacts/<name>/<filename>` in the sandbox when possible. If a signing secret is available, it also creates a time-limited download URL. The result reports size, share time, turn ID, version count, download URL, and workspace path.

**Call relations**: The object seam calls this as part of object_get when a turn needs the artifact’s live status. It uses _find for lookup, _unchanged_visible for the safety recheck, the blob store for bytes, the sandbox for writing a workspace copy, and mint_artifact_url for the temporary link.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 4 external calls (__init__, now, mint_artifact_url, workspace_tx).


##### `ArtifactObjects.apply`  (lines 196–205)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update artifacts through the object interface. Artifacts must come from `share_file`, not from direct object writes.

**Data flow**: It receives the proposed artifact spec and related context, but does not use them to change anything. It immediately raises a VerbNotSupported error with guidance explaining that files must be written in the workspace and shared.

**Call relations**: The object system calls apply for create or update operations. For this object kind, apply is a locked door: it stops the write path and points callers back to `share_file`.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 207–233)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact and all of its versions. This removes both the database records and the stored file bytes, so old download links stop working.

**Data flow**: It receives an artifact name, finds all versions visible to the current context, and opens a workspace database transaction. It locks and rechecks the current latest version to make sure the artifact did not change mid-delete, deletes the matching shared-artifact rows, verifies the expected number was removed, then deletes each version’s blob and preview blob from storage.

**Call relations**: The object system calls this for artifact deletion. It uses _find for the named group, _unchanged_visible for a safe visibility check, SQL deletion for the rows, and the blob store to remove the actual bytes afterward.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 235–242)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds a database check that confirms an artifact’s conversation is still the same visible conversation the caller is allowed to read. This protects status and delete from acting on stale or no-longer-visible data.

**Data flow**: It takes the tool context and the latest artifact row. It creates a SQL query that looks for the row’s conversation in the current workspace, current agent scope, same audience, and allowed read subjects. The output is the query, not the query result.

**Call relations**: ArtifactObjects.status and ArtifactObjects.delete run this query inside a transaction. Status uses it before copying bytes; delete uses it before removing rows and blobs.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._find`  (lines 244–248)

```
async def _find(self, subjects: frozenset[str], name: str) -> tuple[sa.Row, ...] | None
```

**Purpose**: Finds the artifact group with a given visible object name. It is the shared lookup helper for get, status, member detail, and delete.

**Data flow**: It receives a set of readable subjects and an artifact name. It asks _groups for all visible named groups, searches for the requested name, and returns that group’s versions or null if no match exists.

**Call relations**: ArtifactObjects.get, member_detail, status, and delete call this whenever they need one named artifact. It centralizes name lookup so all those actions agree on what a name means.

*Call graph*: calls 1 internal fn (_groups); called by 4 (delete, get, member_detail, status).


##### `ArtifactObjects._groups`  (lines 250–292)

```
async def _groups(self, subjects: frozenset[str]) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Reads shared artifacts from the database and groups their versions into user-facing artifact objects. This is where raw rows become the object model’s idea of one artifact per conversation and filename.

**Data flow**: It receives the subjects the caller may read. It queries shared_artifact rows joined to their turns and conversations, limited to the current workspace, selected agent, and allowed audiences. It groups rows by conversation ID and filename, gives each group a stable object name, sorts versions newest first, and returns the groups sorted by name.

**Call relations**: ArtifactObjects.list and member_page call this to build pages, and _find calls it to search by name. It uses artifact_object_names to assign the names that the rest of the file depends on.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 3 (_find, list, member_page); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `_row`  (lines 295–306)

```
def _row(name: str, shares: tuple[sa.Row, ...]) -> ObjectRow
```

**Purpose**: Creates the compact list-row view for an artifact. This is the short version shown in listings.

**Data flow**: It receives an artifact name and all its version rows, looks at the newest version, builds a short summary, and fills fields such as filename, subject, conversation ID, and share time.

**Call relations**: ArtifactObjects.list, member_page, and member_detail call this when they need the list-style representation. It uses _summary for the human-readable one-line description.

*Call graph*: calls 1 internal fn (_summary); called by 3 (list, member_detail, member_page); 1 external calls (__init__).


##### `_detail`  (lines 309–325)

```
def _detail(shares: tuple[sa.Row, ...]) -> ObjectDetail[ArtifactSpec]
```

**Purpose**: Creates the detailed object view for an artifact. It describes the latest shared file and links it back to the conversation where it was created.

**Data flow**: It receives all version rows for one artifact. It uses the newest version for filename, media type, subject, and update time, uses the oldest version for creation time, and returns an ObjectDetail with a `created_in` link to the conversation.

**Call relations**: ArtifactObjects.get and member_detail call this after finding an artifact. It turns database rows into the structured detail expected by the object system.

*Call graph*: called by 2 (get, member_detail); 4 external calls (__init__, __init__, __init__, __init__).


##### `_summary`  (lines 328–334)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Builds a short, readable summary for an artifact listing. It gives a quick sense of what the file is, when it was shared, and whether it has multiple versions.

**Data flow**: It receives all version rows, reads the newest version’s filename, media type, size, and share date, adds a version count when there is more than one version, and trims the text to the maximum summary length.

**Call relations**: _row calls this while preparing list entries. The summary then appears wherever artifact rows are listed.

*Call graph*: called by 1 (_row).


### Hosted site access gates
These files create the signed hostnames and short-lived tokens that securely route browsers to a specific sandboxed site.

### `core/src/ufo/sandbox/ingress_host.py`

`domain_logic` · `request handling`

A browser treats different hostnames as different places, with separate cookies and storage. This file uses that fact to keep sandboxed sites separated from each other. It turns a conversation ID and a sandbox port into a short DNS label, like a compact address printed on an envelope. The label contains the conversation ID, the port, and a small signed fingerprint made with the deploy secret. The fingerprint is not the main permission check; cookies and tokens still decide who may enter. Its job is to stop random guessed hostnames from causing the server to even look up the wrong conversation.

The file also checks labels on the way back in. It decodes the base32 text, verifies that the spelling is the single canonical form, checks that the signature matches this deployment's secret, and then returns the conversation ID and port. The canonical spelling check matters because base32 has a few unused bits here: without this check, several different text labels could decode to the same bytes, and browsers would treat them as separate sites with separate storage. A custom SiteLabelError is raised when a label is malformed, forged, or non-canonical.

#### Function details

##### `site_label`  (lines 52–57)

```
def site_label(conversation_id: UUID, port: int) -> str
```

**Purpose**: Builds the hostname label for a specific conversation and sandbox port. It is used when the system needs a stable browser origin for that one hosted site.

**Data flow**: It receives a conversation UUID and a port number. It first rejects ports outside the normal TCP port range, then joins the UUID bytes and the two-byte port into one address. It signs that address, appends the signature, encodes the result as lowercase base32 text, and returns that text as the DNS label.

**Call relations**: When a sandbox site needs to be named, this function starts the process. It asks _signature to create the signed fingerprint for the address, then asks _encode to turn the raw bytes into DNS-friendly text.

*Call graph*: calls 2 internal fn (_encode, _signature).


##### `parse_site_label`  (lines 60–72)

```
def parse_site_label(label: str) -> tuple[UUID, int]
```

**Purpose**: Reads a DNS label back into the conversation ID and port it claims to name, but only if the label is valid for this deployment. It is the safety check before a hostname is trusted as pointing at a sandbox site.

**Data flow**: It receives a label string from a hostname. It decodes the base32 text, re-encodes it to make sure the spelling is the one accepted form, splits the bytes into address and signature, and compares the supplied signature with the one this server would create. If anything is wrong, it raises SiteLabelError; if everything is right, it returns the UUID and port.

**Call relations**: This function is used on incoming hostnames. It relies on _encode to catch alternate spellings and on _signature to recompute the expected fingerprint. It uses a constant-time comparison so signature checking does not leak useful timing clues, and it constructs the UUID only after the label passes the checks.

*Call graph*: calls 2 internal fn (_encode, _signature); 4 external calls (__init__, b32decode, compare_digest, UUID).


##### `_encode`  (lines 75–76)

```
def _encode(raw: bytes) -> str
```

**Purpose**: Turns raw label bytes into the compact lowercase base32 text used in DNS. It keeps the public spelling predictable and short by removing padding characters.

**Data flow**: It receives bytes. It base32-encodes them, converts the result to normal text, removes trailing '=' padding, lowercases it, and returns the label-safe string.

**Call relations**: site_label uses this when producing a new label. parse_site_label uses it again after decoding to confirm that the incoming label matches the one true spelling for those bytes.

*Call graph*: called by 2 (parse_site_label, site_label); 1 external calls (b32encode).


##### `_signature`  (lines 79–81)

```
def _signature(address: bytes) -> bytes
```

**Purpose**: Creates the short signed fingerprint that proves a label was minted with this deployment's secret. This helps reject guessed labels before they can reach deeper sandbox lookup logic.

**Data flow**: It receives the raw address bytes made from conversation ID plus port. It reads the ingress secret, combines it with a fixed label kind and the address using HMAC, which is a keyed hash used to prove authenticity, then returns the first four bytes of the result.

**Call relations**: site_label calls this while minting a label, and parse_site_label calls it again to check an incoming label. Both sides use the same secret and input bytes, so a genuine label produces the same short fingerprint.

*Call graph*: called by 2 (parse_site_label, site_label); 2 external calls (new, ingress_secret).


### `core/src/ufo/sandbox/ingress_token.py`

`domain_logic` · `request handling`

This file protects sandbox ingress, meaning browser access into a running sandbox service through a port. Instead of trusting a plain URL or cookie, it uses a signed token: a compact message whose contents cannot be changed without detection because it is protected by a shared deploy secret. The token says which workspace and conversation it belongs to, which port it allows, when it expires, and what kind of visit step it is for.

There are two separate token kinds. A “view” token is put into the link that opens the sandbox frame. A “session” token is later used as the browser’s cookie for that origin. The file deliberately refuses to let one kind stand in for the other. This is like having one ticket to enter the building lobby and a different badge for staying inside; copying the lobby ticket into the badge slot should not work.

The main data object is `IngressClaims`, which holds the verified facts a token grants. `mint_ingress_token` turns those facts into signed text. `verify_ingress_token` checks the signature, checks the kind, parses the workspace and conversation identifiers, rejects impossible ports, and rejects expired tokens. `ingress_secret` reads the shared secret from the environment and fails loudly if deployment forgot to provide it.

#### Function details

##### `mint_ingress_token`  (lines 50–62)

```
def mint_ingress_token(claims: IngressClaims, kind: IngressTokenKind) -> str
```

**Purpose**: This function makes a signed ingress token for one specific hop, either the initial view link or the later session cookie. Someone uses it when they already know the workspace, conversation, port, and expiry time that should be granted, and need a tamper-proof token to hand to a browser.

**Data flow**: It receives `IngressClaims` and a token kind. It turns those claims into a JSON message, reads the shared ingress secret from the environment through `ingress_secret`, signs the message with that secret, and returns the signed token text. Nothing is changed in place; the output is a new string that can later be checked by `verify_ingress_token`.

**Call relations**: When the system needs to issue an ingress link or cookie, it calls this function to seal the allowed facts into a token. This function depends on `ingress_secret` for the shared secret and hands the finished payload to the lower-level token signing helper, which does the cryptographic signing.

*Call graph*: calls 1 internal fn (ingress_secret); 2 external calls (dumps, sign_token).


##### `verify_ingress_token`  (lines 65–87)

```
def verify_ingress_token(token: str, now: datetime, kind: IngressTokenKind) -> IngressClaims
```

**Purpose**: This function checks whether an incoming ingress token is real, still fresh, meant for the expected hop, and safe to use. It returns the claims only when every check passes; otherwise it raises `IngressTokenError` so callers can deny access.

**Data flow**: It receives token text, the current time, and the token kind the caller expects. It reads the same shared secret through `ingress_secret`, verifies the token signature, decodes the JSON payload, checks that the kind matches, converts the workspace and conversation values into UUIDs, converts the port and expiry into numbers, rejects ports outside the normal TCP port range, and compares the expiry with `now`. If all of that succeeds, it returns an `IngressClaims` object; if anything is malformed, forged, wrong-kind, or expired, it raises an error instead.

**Call relations**: This is the receiving side of `mint_ingress_token`: the same fields that were signed during minting are unpacked and checked here. It calls the lower-level token verification helper to prove the token was not changed, uses `ingress_secret` to get the deployment secret, and builds `IngressClaims` only after the payload has passed the basic safety checks.

*Call graph*: calls 1 internal fn (ingress_secret); 6 external calls (__init__, __init__, timestamp, loads, verify_token, UUID).


##### `ingress_secret`  (lines 90–97)

```
def ingress_secret() -> str
```

**Purpose**: This function retrieves the shared secret used to sign and verify ingress tokens. It exists so both token creation and token checking use the same deployment-wide secret, and so a missing secret is treated as a serious configuration error.

**Data flow**: It reads the environment variable named by `UFO_TOKEN_SECRET_ENV`. If the value exists and is not empty, it returns that string. If the value is missing, it raises `RuntimeError`, which stops token minting or verification rather than letting the system appear healthy while all ingress requests fail.

**Call relations**: `mint_ingress_token` calls this before signing a token, and `verify_ingress_token` calls it before checking one. This makes the secret a shared foundation for both directions: tokens minted without this secret cannot be accepted, and tokens cannot be verified unless the deployment is configured correctly.

*Call graph*: called by 2 (mint_ingress_token, verify_ingress_token).


### Sites panels and surfaces
This group exposes hosted sites as conversation panel entries, workspace objects, and public web pages with visibility controls.

### `extensions/sites/ufo_ext_sites/conversation_slot.py`

`domain_logic` · `conversation view request handling`

This file connects hosted sites to the conversation view. A conversation may have site-like objects attached to it, but the UI should not blindly show every site in storage. First, it checks the conversation’s visible authorization items, which are like permission slips saying which site names and versions the viewer may access. Then it asks the hosted-sites store for matching sites in this workspace and conversation. It double-checks that each returned site still matches the expected name and generation, so an old or mismatched permission does not expose the wrong site.

For every authorized site, it builds a `ConversationSite`, which is the clean, UI-ready shape: name, public URL, visibility, timestamps, and the authorization information needed to prove why it is visible. It only returns up to `CONVERSATION_SITES_MAX` sites and marks the result as truncated if more were available. This is like showing the first page of a long list and saying “there are more.”

At the bottom, the file registers `SITES_SLOT`, a `ConversationSlotProvider`. That tells the larger system: this slot is called “Sites,” it uses a link icon, this is how to quickly summarize it, and this is how to fully read its content.

#### Function details

##### `_read`  (lines 13–47)

```
async def _read(ctx: ConversationSlotContext) -> SitesSlotPayload
```

**Purpose**: Builds the full “Sites” slot content for a conversation. It finds the hosted sites the current viewer is authorized to see, turns them into public-facing site records, and notes whether the list had to be cut short.

**Data flow**: It receives a conversation slot context, which includes the conversation ID, visible authorization items, workspace/store access, and the public base URL. It extracts site names and generations from the visible items, asks `HostedSites` for matching site rows, filters out anything whose authorization name or generation does not match, builds public URLs with `site_url`, wraps each allowed row as a `ConversationSite`, and returns a `SitesSlotPayload` containing those sites plus a `truncated` flag.

**Call relations**: The `SITES_SLOT` provider calls this when the system needs the full Sites content for a conversation. Inside, it relies on `site_name_from_object` and `site_object_name` to translate between authorization-object names and site names, uses `HostedSites` to read the stored site records, uses `site_url` to make links a user can open, and finally hands the finished list back as a `SitesSlotPayload`.

*Call graph*: 6 external calls (__init__, __init__, __init__, site_name_from_object, site_object_name, site_url).


##### `_summarize`  (lines 50–52)

```
async def _summarize(ctx: ConversationSlotContext) -> int | None
```

**Purpose**: Returns a quick count for the Sites slot without loading the full site details. This lets the surrounding interface show a compact hint, such as how many site-related items are visible.

**Data flow**: It receives the conversation slot context and looks only at the number of visible items already present there. It caps that number at `CONVERSATION_SITES_MAX`, returns the count if it is greater than zero, and returns `None` if there is nothing to show.

**Call relations**: The `SITES_SLOT` provider calls this when it only needs a lightweight summary instead of the full site list. Unlike `_read`, it does not go to storage or build URLs; it simply reports a bounded count based on what the context already knows.


### `extensions/sites/ufo_ext_sites/objects.py`

`domain_logic` · `request handling`

A deployed website is not just a running server port; it is something workspace members need to find, share, restrict, or remove. This file gives each hosted site a stable object identity, like putting a label on a package so the rest of the system can refer to it safely. The label combines the site name with a short digest of the conversation that created it, because two different conversations may both deploy a site with the same friendly name.

The file defines the allowed editable setting for a site: its visibility. Visibility means who can open the link: only the creator, the whole workspace, or the public. Creation is deliberately refused here, because a site can only exist after a deploy knows which sandbox port is serving it. Deleting a site unregisters it, so the link stops resolving.

The main class, `SiteObjects`, adapts the site registry to the workspace object API. It lists sites a member may see, returns details for one site, reports status such as URL and port, changes visibility with safety rules, and unregisters sites. It also enforces ownership rules: creators control their own sites, while admins may only make a site more private, not more public.

#### Function details

##### `site_object_name`  (lines 57–61)

```
def site_object_name(conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the object name used to refer to one hosted site. It keeps names unique by adding a short fingerprint of the conversation ID to the human site name.

**Data flow**: It receives a conversation ID and a site name. It turns the conversation ID into a SHA-256 hash, takes a short prefix, and appends it to the site name with a dash. The result is a stable name such as `dashboard-9f21c0a4e3b7`.

**Call relations**: This is the shared naming rule for the file. Listing, conversation grants, and reverse-name checks all use it so every part of the system agrees on what a site object is called.

*Call graph*: called by 3 (member_conversation_rows, _named, site_name_from_object); 1 external calls (sha256).


##### `site_name_from_object`  (lines 64–70)

```
def site_name_from_object(conversation_id: UUID, object_name: str) -> str | None
```

**Purpose**: Tries to recover the original site name from an object name, but only if that object name belongs to the given conversation. This prevents a name from being accepted just because it looks similar.

**Data flow**: It receives a conversation ID and an object name. It recomputes the expected conversation suffix, checks whether the object name ends with that suffix, removes it, and then rebuilds the full object name to verify it exactly. It returns the plain site name when valid, or `None` when the object name does not match.

**Call relations**: It relies on `site_object_name` as the source of truth for naming. This is useful wherever code needs to translate from the workspace object name back to the registered site name.

*Call graph*: calls 1 internal fn (site_object_name); 1 external calls (sha256).


##### `_named`  (lines 73–74)

```
def _named(sites: Iterable[HostedSite]) -> dict[str, HostedSite]
```

**Purpose**: Turns a collection of hosted site records into a lookup table keyed by their workspace object names. This makes later searches fast and consistent.

**Data flow**: It receives a list or other iterable of hosted site records. For each site, it computes the object name from the site’s conversation ID and site name. It returns a dictionary where each key is that object name and each value is the original hosted site record.

**Call relations**: `_member_rows` uses this when preparing list results, and `_find` uses it when looking up one site by object name. It delegates the actual naming rule to `site_object_name`.

*Call graph*: calls 1 internal fn (site_object_name); called by 2 (_find, _member_rows).


##### `_workspace`  (lines 77–80)

```
def _workspace(ext: ExtensionContext | None) -> ExtensionContext
```

**Purpose**: Ensures the code has an extension context, which is the object carrying workspace information and the current database transaction. Without it, this object kind cannot read the site registry.

**Data flow**: It receives an optional extension context. If the context is missing, it raises an error with a clear message. If present, it returns the same context unchanged.

**Call relations**: This is the safety gate used before touching workspace-scoped data. `_sites`, `_member_rows`, and `_status` call it when they need the workspace ID, public base URL, or transaction.

*Call graph*: called by 3 (_member_rows, _status, _sites).


##### `_sites`  (lines 83–85)

```
def _sites(ext: ExtensionContext | None) -> HostedSites
```

**Purpose**: Creates the site-registry helper for the current workspace. The registry is where deployed sites, ports, creators, and visibility settings are stored.

**Data flow**: It receives an optional extension context, verifies it through `_workspace`, then reads the workspace ID and transaction from that context. It returns a `HostedSites` store object scoped to that workspace and transaction.

**Call relations**: Most actions in `SiteObjects` go through this helper before reading or changing hosted-site records. It is the bridge from the object API to the underlying site store.

*Call graph*: calls 1 internal fn (_workspace); called by 5 (_apply_owned, _delete_owned, _find, _member_rows, member_conversation_rows); 1 external calls (__init__).


##### `_summary`  (lines 88–89)

```
def _summary(site: HostedSite) -> str
```

**Purpose**: Creates a short human-readable summary for a hosted site. It gives list views a quick description without requiring someone to open the full detail.

**Data flow**: It receives one hosted site record. It reads the site name, visibility, and sandbox port, then combines them into a compact text string. The output is used as the object row summary.

**Call relations**: `_member_rows` calls this while building the list of visible site objects. It keeps the wording of list summaries in one place.

*Call graph*: called by 1 (_member_rows).


##### `SiteObjects._admin_can_apply`  (lines 104–105)

```
def _admin_can_apply(self, old: SiteSpec, spec: SiteSpec) -> bool
```

**Purpose**: Defines the one visibility change a workspace admin is allowed to make on someone else’s site: making it private. Admins are not allowed to widen access.

**Data flow**: It receives the old site specification and the requested new specification. It checks whether the old visibility was not private and the new visibility is private. It returns `true` only for that narrowing change, otherwise `false`.

**Call relations**: This method plugs into the broader object permission system provided by the parent class. When an admin tries to apply a change, this rule tells the system whether the admin override is allowed.


##### `SiteObjects._member_rows`  (lines 107–141)

```
async def _member_rows(self, ext: ExtensionContext | None, *, member_id: UUID | None) -> tuple[OwnedRow[GeneratedObjectOwner], ...]
```

**Purpose**: Builds the rows shown when a member lists site objects in the workspace. Each row includes ownership, visibility, conversation, and, when possible, the hosted URL.

**Data flow**: It receives the extension context and the member ID of the viewer. It loads all hosted sites for the workspace, gives each one its object name, fetches creator email addresses, and builds an `OwnedRow` for each site. The returned rows include fields such as conversation ID, creation time, visibility, owner email, whether the site is mine, and the site URL if a public base URL exists.

**Call relations**: This is called by the object-listing flow inherited from `MemberReadableObjects`. It uses `_workspace` and `_sites` to reach workspace data, `_named` for stable object names, `_summary` for readable row text, `owner_emails` for creator display, and `site_url` to produce the link people can open.

*Call graph*: calls 4 internal fn (_named, _sites, _summary, _workspace); 4 external calls (__init__, __init__, owner_emails, site_url).


##### `SiteObjects.member_conversation_rows`  (lines 143–160)

```
async def member_conversation_rows(self, ext: ExtensionContext | None, conversation_id: UUID, *, member_id: UUID, admin: bool, limit: int) -> tuple[ConversationObjectGrant, ...]
```

**Purpose**: Reports which site objects are visible inside a specific conversation for a specific member. This lets chat or conversation tools know which hosted sites can be referenced there.

**Data flow**: It receives a conversation ID, member ID, admin flag, and limit. It ignores the admin flag, asks the site registry for visible sites in that conversation for that member, and turns each site into a `ConversationObjectGrant` with the site object name, generation, and visible-content marker. It returns those grants as a tuple.

**Call relations**: The conversation object flow calls this when it needs objects tied to one conversation. It asks `_sites` for the visibility-filtered records and uses `site_object_name` so the grants match the names used by normal site listings.

*Call graph*: calls 2 internal fn (_sites, site_object_name); 1 external calls (__init__).


##### `SiteObjects._member_object`  (lines 162–183)

```
async def _member_object(self, ext: ExtensionContext | None, name: str, owner: GeneratedObjectOwner, *, member_id: UUID | None) -> ObjectDetail[SiteSpec] | None
```

**Purpose**: Builds the detailed object view for one site. It shows the editable specification and links the site back to the conversation that created it.

**Data flow**: It receives the object name, owner information, and viewer member ID. It looks up the hosted site by name. If the site is gone, it returns `None`; otherwise it returns an `ObjectDetail` containing the current visibility, creation and update times, and a `created_in` link pointing to the conversation object.

**Call relations**: The object-get flow calls this after permissions have been considered. It uses `_find` to translate the object name into the hosted site record, then packages the result in the standard object detail shape.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `SiteObjects._status`  (lines 185–201)

```
async def _status(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> dict[str, JsonValue] | None
```

**Purpose**: Returns live status information for a hosted site, such as the port serving it and the URL people can open. This is more operational than the basic object spec.

**Data flow**: It receives a tool context and object name. It finds the site, and if absent returns `None`. If present, it reads the site name, sandbox port, creator member ID, workspace ID, conversation ID, and public base URL, then returns a dictionary containing those details and the generated site URL.

**Call relations**: Tooling calls this when it needs status for an object. It uses `_find` to locate the site, `_workspace` to get workspace information, and `site_url` to construct the externally reachable link.

*Call graph*: calls 2 internal fn (_find, _workspace); 1 external calls (site_url).


##### `SiteObjects._apply_owned`  (lines 203–220)

```
async def _apply_owned(self, ctx: ToolContext, name: str, spec: SiteSpec, old: SiteSpec | None, owner: GeneratedObjectOwner | None) -> None
```

**Purpose**: Applies an allowed change to a site object, which in practice means changing only its visibility. It refuses to create sites here because sites must be created by deployment.

**Data flow**: It receives the tool context, object name, requested site spec, previous spec, and owner. If there is no previous object or owner, it raises `VerbNotSupported` to explain that deployment is required. It then finds the current site, rejects the change if the site disappeared, blocks switching a non-public site to public, skips work if visibility is unchanged, and otherwise writes the new visibility to the site registry.

**Call relations**: The object-apply flow calls this after permission checks decide the caller may try a change. It uses `_find` to confirm the site still exists and `_sites` to save the new visibility. Its rules work together with `_admin_can_apply` and the class-level gates to keep sharing changes safe.

*Call graph*: calls 2 internal fn (_find, _sites); 1 external calls (__init__).


##### `SiteObjects._delete_owned`  (lines 222–226)

```
async def _delete_owned(self, ctx: ToolContext, name: str, owner: GeneratedObjectOwner) -> None
```

**Purpose**: Unhosts a site object by unregistering it from the site registry. After this, the hosted link stops resolving through this system.

**Data flow**: It receives the tool context, object name, and owner information. It finds the matching hosted site. If the site is already gone, it raises an error; otherwise it asks the site registry to unregister that conversation-and-name pair.

**Call relations**: The object-delete flow calls this when the caller is allowed to unhost the site. It uses `_find` to resolve the object name and `_sites` to perform the unregister operation.

*Call graph*: calls 2 internal fn (_find, _sites).


##### `SiteObjects._find`  (lines 228–229)

```
async def _find(self, ext: ExtensionContext | None, name: str) -> HostedSite | None
```

**Purpose**: Looks up one hosted site by its workspace object name. It is the small helper that turns the object API’s name back into the actual site record.

**Data flow**: It receives an extension context and an object name. It loads all hosted sites for the workspace, builds the name-to-site dictionary with `_named`, and returns the matching hosted site if present. If no site has that object name, it returns `None`.

**Call relations**: Detail, status, apply, and delete all call this before acting on a site. It depends on `_sites` to read the registry and `_named` to apply the same naming rule used everywhere else in the file.

*Call graph*: calls 2 internal fn (_named, _sites); called by 4 (_apply_owned, _delete_owned, _member_object, _status).


### `extensions/sites/ufo_ext_sites/surface.py`

`io_transport` · `request handling`

A hosted site link is meant to be shareable, but the link itself is not permission to enter. This file is the gatehouse for those links. It turns a signed site token into a site address, finds the right workspace and stored site record, checks the visitor’s session cookie, and applies the site’s visibility rule: public, workspace-only, or private to the creator.

If a visitor should not learn whether a private site exists, the file returns the same plain 404 response used for a bad link. If the site is not public and the browser is simply not signed in, it shows a small page with a sign-in link. This keeps private information hidden while still helping legitimate workspace members recover.

The actual site files are not served here. Instead, this file renders a surrounding page with an iframe. An iframe is like a window cut into the page: the hosted site loads from its own origin, while this frame keeps the app’s cookies and routes separate. The iframe is also sandboxed, meaning the embedded site gets useful abilities like scripts and forms, but not the power to navigate the whole browser tab away.

For creators, the frame includes a small visibility form. That form uses a CSRF token, which is a signed anti-forgery value tied to the creator’s current session, so another website cannot secretly submit changes on their behalf.

#### Function details

##### `site_token`  (lines 97–106)

```
def site_token(workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Creates the permanent signed token that names one hosted site. The token carries the workspace ID, conversation ID, and site name, so a shared public host can know what site is being requested before it reads any site data.

**Data flow**: It receives a workspace ID, conversation ID, and site name. It packages those values as claims and asks the surface-token system to sign them for the sites surface. It returns the resulting token string, which can be placed in a public site URL.

**Call relations**: When a full hosted link is being built, site_url calls this helper first. site_token hands off the actual signing work to mint_surface_token, keeping the rest of the file from needing to know the signing details.

*Call graph*: called by 1 (site_url); 1 external calls (mint_surface_token).


##### `site_url`  (lines 109–119)

```
def site_url(public_base_url: str | None, workspace_id: UUID, conversation_id: UUID, name: str) -> str
```

**Purpose**: Builds the full browser URL for a hosted site. It refuses to produce a fake or incomplete link if the deployment has no public base URL configured.

**Data flow**: It receives the deployment’s public base URL plus the site’s workspace ID, conversation ID, and name. If there is no base URL, it raises SiteHostingUnconfigured. Otherwise it creates a site token and appends it to the hosted-sites frame path, returning a complete URL string.

**Call relations**: This is the producer of shareable hosted-site links. It calls site_token to create the address token, then combines that token with the public frame route.

*Call graph*: calls 1 internal fn (site_token); 1 external calls (__init__).


##### `site_address`  (lines 122–133)

```
def site_address(token: str) -> SiteAddress | None
```

**Purpose**: Turns a site token back into the site address it claims to represent. If the token is forged, for the wrong surface, or missing required information, it returns nothing.

**Data flow**: It receives a token string. It verifies the signature and surface name, then reads the workspace, conversation, and site-name claims. It converts the two IDs into UUID values and returns a SiteAddress object; if anything is invalid or missing, it returns None.

**Call relations**: resolve_workspace uses this before database access to discover which workspace the request belongs to. _resolve uses it later to find the specific stored site. It relies on verify_surface_token for trust and UUID parsing for valid IDs.

*Call graph*: called by 2 (_resolve, resolve_workspace); 3 external calls (__init__, verify_surface_token, UUID).


##### `resolve_workspace`  (lines 136–141)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | Response | None
```

**Purpose**: Identifies the workspace for an incoming hosted-site request using only the site token in the URL. This matters because public visitors may have no session cookie, so the workspace cannot be learned from login state.

**Data flow**: It reads the token path parameter from the request. It asks site_address to verify and decode it. If the token is bad, it returns the file’s standard 404 response; otherwise it returns the workspace ID from the token.

**Call relations**: The surface framework calls this as the identify step for the sites surface. It uses site_address for decoding and _not_found when the request should look exactly like an unknown site.

*Call graph*: calls 2 internal fn (_not_found, site_address).


##### `frame`  (lines 144–168)

```
async def frame(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Renders the hosted-site frame page for a visitor. It is the main GET handler: it checks the link, checks the visitor, enforces visibility, and returns an HTML page containing the site iframe.

**Data flow**: It receives the surface context and HTTP request. It resolves the requested site, reads the viewer from the session cookie, and compares that viewer with the site’s visibility and creator. If access is denied, it returns either a sign-in page or a 404. If access is allowed, it mints a fresh embedded-site URL, optionally creates a CSRF token for the creator, and returns the full frame HTML.

**Call relations**: The GET routes in SITES_SURFACE call this when someone opens a site link or a deep link inside the site. It delegates site lookup to _resolve, visitor lookup to _viewer, iframe-page construction to _frame_page, and standard failure pages to _not_found or _page.

*Call graph*: calls 7 internal fn (ingress_url, _frame_page, _not_found, _page, _resolve, _session_digest, _viewer); 2 external calls (HTMLResponse, mint_surface_token).


##### `set_visibility`  (lines 171–191)

```
async def set_visibility(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Changes a site’s visibility setting, but only for the member who created the site. It also blocks forged form submissions with a CSRF check.

**Data flow**: It receives the surface context and a POST request. It resolves the site, verifies that the current viewer is the creator, reads the submitted form, checks the CSRF token, validates the requested visibility level, and rejects attempts to newly enable public sharing. If all checks pass, it updates the stored visibility and redirects back to the site frame.

**Call relations**: The POST visibility route in SITES_SURFACE calls this when the creator submits the selector form. It uses _resolve and _viewer for permission checks, _csrf_holds for form authenticity, _sites to write the change, and HTTP response helpers for errors or the final redirect.

*Call graph*: calls 5 internal fn (_csrf_holds, _not_found, _resolve, _sites, _viewer); 4 external calls (PlainTextResponse, RedirectResponse, form, visibility_level).


##### `_resolve`  (lines 194–198)

```
async def _resolve(ctx: SurfaceContext, request: Request) -> HostedSite | None
```

**Purpose**: Looks up the stored hosted-site record named by the URL token. It is a small shared helper for both viewing the frame and changing visibility.

**Data flow**: It reads the token from the request path and asks site_address to decode it. If the token is invalid, it returns None. If it is valid, it opens the hosted-sites store for the current workspace and reads the site by conversation ID and name, returning the HostedSite record or no record.

**Call relations**: frame calls this before showing a site, and set_visibility calls it before editing one. It combines site_address, which proves what the URL says, with _sites, which gives access to the stored site table.

*Call graph*: calls 2 internal fn (_sites, site_address); called by 2 (frame, set_visibility).


##### `_sites`  (lines 201–202)

```
def _sites(ctx: SurfaceContext) -> HostedSites
```

**Purpose**: Creates the storage helper used to read or update hosted-site records for the current workspace. It keeps store construction in one place.

**Data flow**: It receives the surface context. It takes the workspace ID and active transaction from that context and uses them to create a HostedSites store object. It returns that store object to the caller.

**Call relations**: _resolve uses this to read a site, and set_visibility uses it to save a new visibility setting. It is the bridge from request context to the hosted-sites database wrapper.

*Call graph*: called by 2 (_resolve, set_visibility); 1 external calls (__init__).


##### `_viewer`  (lines 205–215)

```
async def _viewer(ctx: SurfaceContext, request: Request) -> UUID | None
```

**Purpose**: Finds which workspace member, if any, is represented by the browser’s session cookie. It returns None when the visitor is not signed in or the cookie is not valid for this workspace.

**Data flow**: It reads the ufo_session cookie from the request. If there is no cookie, it returns None. If there is one, it verifies the bearer token, which is a signed login credential. When that token yields an email, it finds the linked member for that email or creates the link, then returns the member ID.

**Call relations**: frame uses this to decide whether the visitor may view the site and whether to show creator controls. set_visibility uses it to prove that the POST came from the site creator. It hands token checking to verify_token and member linking to the surface context.

*Call graph*: calls 2 internal fn (link_member, linked_member); called by 2 (frame, set_visibility); 1 external calls (verify_token).


##### `_csrf_holds`  (lines 218–220)

```
def _csrf_holds(request: Request, submitted: str) -> bool
```

**Purpose**: Checks whether a submitted visibility form token belongs to this browser session. This protects the creator from another website tricking their browser into changing a site’s visibility.

**Data flow**: It receives the request and the submitted CSRF token string. It verifies the token, reads its stored session digest, and compares that digest with a fresh digest of the current request’s session cookie. It returns true only when they match.

**Call relations**: set_visibility calls this after it has confirmed the viewer is the creator but before accepting the form. It uses _session_digest to tie the submitted token to the current session and verify_surface_token to confirm the token was signed by the app.

*Call graph*: calls 1 internal fn (_session_digest); called by 1 (set_visibility); 1 external calls (verify_surface_token).


##### `_session_digest`  (lines 223–227)

```
def _session_digest(request: Request) -> str
```

**Purpose**: Creates a safe fingerprint of the current session cookie for CSRF protection. The fingerprint lets the app compare sessions without putting the raw cookie value into the CSRF token.

**Data flow**: It reads the ufo_session cookie from the request, or an empty string if there is none. It hashes that value with SHA-256, which turns it into a fixed-length digest. It returns the digest as text.

**Call relations**: frame uses this when minting a CSRF token for the creator’s visibility form. _csrf_holds uses it again during form submission to confirm the token matches the current browser session.

*Call graph*: called by 2 (_csrf_holds, frame); 1 external calls (sha256).


##### `_not_found`  (lines 230–231)

```
def _not_found() -> Response
```

**Purpose**: Returns the standard not-found response for this surface. Using one shared response helps bad links and unauthorized private-site access look the same.

**Data flow**: It takes no input. It builds a plain-text HTTP response with the body 'no such site' and status code 404. It returns that response.

**Call relations**: resolve_workspace uses this for invalid tokens during workspace identification. frame and set_visibility use it when a site is missing or when the visitor should not be told that the site exists.

*Call graph*: called by 3 (frame, resolve_workspace, set_visibility); 1 external calls (PlainTextResponse).


##### `_page`  (lines 234–239)

```
def _page(title: str, style: str, body: str) -> str
```

**Purpose**: Wraps a title, CSS style, and HTML body into a complete minimal HTML page. It avoids repeating the same document boilerplate in each response.

**Data flow**: It receives a page title, a CSS style string, and an HTML body string. It places them into a small HTML document with character set and viewport metadata. It returns the full HTML string.

**Call relations**: frame calls this directly for the not-signed-in page. _frame_page calls it to wrap the hosted-site header and iframe into a full page.

*Call graph*: called by 2 (_frame_page, frame).


##### `_frame_page`  (lines 242–272)

```
def _frame_page(site: HostedSite, embedded: str | None, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the HTML page that surrounds the hosted site. It shows the site name, either creator visibility controls or a viewer badge, and the iframe where the site itself appears.

**Data flow**: It receives the hosted-site record, the freshly made embedded-site URL, the frame path for form submission, and an optional CSRF token. It chooses a visibility selector if the caller supplied a CSRF token, otherwise it shows a badge. It escapes user-controlled text before putting it into HTML, adds iframe safety settings when an embedded URL exists, and returns the complete page HTML.

**Call relations**: frame calls this after access has been granted and the embedded URL has been prepared. _frame_page calls _selector when creator controls are needed and _page to wrap the final HTML document.

*Call graph*: calls 2 internal fn (_page, _selector); called by 1 (frame); 1 external calls (escape).


##### `_selector`  (lines 275–289)

```
def _selector(current: Visibility, frame_path: str, csrf: str) -> str
```

**Purpose**: Builds the creator’s small visibility form. It lets the creator choose between allowed visibility levels and submit the change back to the frame route.

**Data flow**: It receives the current visibility level, the frame path to post to, and the CSRF token. It creates option entries for private and workspace visibility, and includes public only if the site is already public. It returns an HTML form string with a hidden CSRF field, a select box, and a Save button.

**Call relations**: _frame_page calls this only when the current viewer is the creator and has been given a CSRF token. The form it creates posts to the route served by set_visibility.

*Call graph*: called by 1 (_frame_page); 1 external calls (escape).

## 📊 State Registers Touched

- `reg-auth-tokens` — Signed passes that prove who a caller is or allow short-lived access to protected routes and links.
- `reg-object-catalog` — The shared catalog of manageable workspace object types and the rules for who may view or change them.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-live-hub` — The live stream state that lets browsers, terminals, and operators watch progress and reconnect without losing updates.
- `reg-scheduled-automation` — Future and repeating tasks, pauses, wakeups, and their last-run state for long-running automation.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-artifact-store` — Generated files, previews, blobs, and signed shared-artifact links that outlive a single message.
- `reg-hosted-site-state` — Hosted sandbox sites, their public addresses, generations, ports, visibility, and unhosting status.
- `reg-conversation-slots` — Side-panel data shown beside a conversation, such as tasks, sources, sites, automations, and workspace changes.
- `reg-human-interaction-requests` — Pending user questions, approval prompts, and credential-request prompts created by tools and resumed through surfaces.
- `reg-outbound-delivery-queue` — Pending outbound surface writebacks and retry state for messages or notifications sent back to external channels such as Slack.
- `reg-research-observation-log` — Saved web research source observations and evidence records tied to conversations for later citation and display.
