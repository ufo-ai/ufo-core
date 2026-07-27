# Reply streaming, writeback, artifact sharing, and final turn commit  `stage-15`

This stage is the system’s “delivery belt” while a conversation turn is running and when it finishes. As the model produces text, tool updates, costs, and final results, core/src/ufo/hub.py publishes those small live messages, called frames, to any screen that is watching. It also keeps recent frames so a reconnecting client can catch up. core/src/ufo/surfaces/hub_tail.py lets a client join late, replay what it missed, and know for sure when the turn is done by checking both the live hub and the database.

For setups with more than one server process, extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py sends the same live frames through Redis Streams, a shared message pipe. Its __init__.py just makes that extension importable.

When the answer is accepted, core/src/ufo/loop/transcript.py writes the transcript durably to blob storage and prevents older saves from replacing newer ones. Files produced by the turn become artifacts through core/src/ufo/artifacts.py. core/src/ufo/artifact_token.py creates safe, short-lived download passes, and core/src/ufo/surfaces/artifacts.py checks those passes before serving the private file bytes.

## Files in this stage

### Live turn streaming
These files publish, retain, and replay live turn frames so clients can follow or resume an in-progress reply stream.

### `core/src/ufo/surfaces/hub_tail.py`

`domain_logic` · `request handling`

A “turn” produces live frames, such as tokens or status updates, while it is running. A viewer may connect at an awkward time: after the turn started, after some frames were missed, or even after another process already finished the turn. This file makes that safe. It listens to the live hub, which is the fast in-memory broadcaster, and also polls the database, which is the slower but reliable record of truth. Think of it like watching a live sports game while also checking the official scoreboard: the live feed is immediate, but the scoreboard confirms the final result.

The main stream function starts two background tasks. One task copies frames from the hub into a shared queue. The other periodically checks whether the turn has reached a saved ending state. Before waiting for live frames, it also checks the database once, so an already-finished turn returns immediately.

The stream ends when it sees either a terminal frame, meaning the turn is truly done, or a parked frame, meaning the turn is paused because something like a spend cap or revoked seat prevents it from continuing. The small HubTailer class wraps this behavior so other surface code can request a tail without knowing about the hub details.

#### Function details

##### `tail_frames`  (lines 27–53)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the main live stream for one turn. It yields frames one by one until the turn either finishes or becomes parked, and it works whether the caller connects early, late, or after completion.

**Data flow**: It receives a hub, a turn ID, and an optional cursor, which is a bookmark for the last frame the caller already saw. It checks whether the hub can resume from that cursor; if not, it starts from the beginning of what the hub still remembers. It then starts one background path for live hub frames and another for database status checks, yields frames from their shared queue, and stops when a final or parked frame appears. When the caller is done, it cancels the background work so nothing is left running.

**Call relations**: HubTailer.tail calls this when a surface wants to watch a turn. Inside, it asks the hub whether the saved cursor is still usable, starts _pump to copy live hub messages, starts _poll_status to notice saved ending states, and calls turn_status_frame directly at the beginning so already-finished turns return without waiting.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, turn_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 56–63)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper copies live frames from the hub into the queue used by tail_frames. It is the fast path for updates that are happening right now.

**Data flow**: It receives the hub, the turn ID, the starting cursor, and the shared queue. As the hub produces each live frame with its cursor, this helper places that item into the queue for tail_frames to yield. If the hub subscription fails, it logs the failure instead of crashing the whole tailing flow.

**Call relations**: tail_frames starts this helper as a background task. It depends on Hub.subscribe for the actual live feed, then hands each received item back through the shared queue so tail_frames can present one combined stream to the caller.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 66–75)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper repeatedly checks the database for a saved end or parked state. It protects correctness when live hub messages are missed or when the caller attaches after the turn already changed state elsewhere.

**Data flow**: It receives a turn ID and the shared queue. Every short interval, it asks turn_status_frame whether the database now shows a terminal or parked frame. If it finds one, it puts that frame into the queue with no cursor and stops; otherwise it keeps waiting and checking. If something goes wrong during polling, it logs the error.

**Call relations**: tail_frames starts this beside the live hub pump. While _pump follows the live feed, _poll_status watches the durable database record and feeds any final or parked result back into the same queue, where tail_frames can end the stream.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `turn_status_frame`  (lines 78–107)

```
async def turn_status_frame(turn_id: UUID) -> LiveFrame | None
```

**Purpose**: This function reads the database and turns the stored state of a turn into the stream-ending frame, if there is one. It returns a terminal frame for completed turns, a parked notice for paused turns, or nothing while the turn is still queued or running.

**Data flow**: It receives a turn ID. It opens a workspace database transaction, reads the turn’s status, saved terminal data, workspace, speaker, behalf-of member, and admission source. If there is saved terminal data, it validates that data and wraps it as a Terminal live frame. If the turn is not parked, it returns nothing. If it is parked, it checks whether the relevant seat admission is now missing or revoked; in that case it returns a seat-revoked parked message. Otherwise it returns the normal spend-cap parked notice.

**Call relations**: tail_frames calls this once before waiting, and _poll_status calls it repeatedly afterward. It is the bridge from the durable database state into the live stream language used by the hub, producing Terminal or Parked frames that tell the outer stream when to stop.

*Call graph*: called by 2 (_poll_status, tail_frames); 8 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx, gate_member, seat_gate_absent).


##### `HubTailer.tail`  (lines 119–120)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This method gives other code a simple way to tail one turn without directly importing or coordinating the hub-tail functions. It is a small adapter around tail_frames.

**Data flow**: It receives a turn ID and an optional cursor from the caller. It uses the Hub stored inside the HubTailer instance and passes everything through to tail_frames. The result is an asynchronous stream of cursor-and-frame pairs for the caller to consume.

**Call relations**: Surface code can depend on HubTailer as a small injected object. When its tail method is called, it immediately delegates the real work to tail_frames, keeping the rest of the system separated from the hub-specific implementation.

*Call graph*: calls 1 internal fn (tail_frames).


### `core/src/ufo/hub.py`

`io_transport` · `cross-cutting live turn streaming`

This file is the project’s in-memory “newsstand” for live turn updates. As an agent works, it produces small frames: text chunks, cost totals, tool-start messages, skill-load messages, and final or paused states. The hub fans those frames out to whoever is watching, such as a command-line UI or web surface.

The important design choice is that publishing never waits for slow viewers. If a viewer’s queue is full, the oldest queued frame for that viewer is dropped. This protects the agent’s work from being slowed down by a stuck browser tab or terminal. At the same time, the hub keeps a bounded replay buffer, like a short security-camera loop. A reconnecting viewer can give the last cursor it saw, and the hub can replay newer frames if they are still in memory.

The `Hub` protocol describes the contract: publish a frame, subscribe from a cursor, and ask whether a cursor is still covered by the replay buffer. `InProcessHub` is the built-in implementation. It stores one `_TurnStream` per turn, guarded by a lock because publishers and subscribers may run on different event loops or threads. When a turn ends and nobody is watching, or when the last watcher leaves, the stream is removed so memory does not grow forever.

#### Function details

##### `Hub.publish`  (lines 75–75)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This is the interface promise for adding one live frame to a turn’s stream. Code that only knows it has a `Hub` can call this without caring whether the implementation is in-memory or shared across processes.

**Data flow**: It receives a turn ID and a frame, such as a text update or terminal result. An implementation appends that frame to the stream, sends it to current subscribers, and returns a cursor string that marks the frame’s position.

**Call relations**: The queue layer uses this contract when it needs to publish a failed terminal result. The concrete `InProcessHub.publish` provides the built-in behavior behind this promise.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 77–79)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the interface promise for watching a turn’s live frames. A caller may pass the last cursor it saw so it can receive only newer frames when reconnecting.

**Data flow**: It receives a turn ID and an optional cursor. An implementation first yields buffered frames newer than that cursor, then keeps yielding newly published frames as they arrive.

**Call relations**: The surface tailing code calls this when it starts pumping live frames to a user-facing surface. The concrete `InProcessHub.subscribe` supplies the in-process replay and live queue behavior.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 81–81)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for checking whether a reconnect can resume cleanly from a cursor. It answers whether the hub still has enough recent history to avoid a gap.

**Data flow**: It receives a turn ID and cursor. An implementation checks its retained buffer and returns true if the cursor is still within the replayable range, otherwise false.

**Call relations**: The surface tailing code asks this before deciding whether to resume from the live hub or fall back to another way of redrawing state. The concrete `InProcessHub.covers` performs the in-memory check.

*Call graph*: called by 1 (tail_frames).


##### `_offer`  (lines 84–87)

```
def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber’s queue without ever blocking. If the queue is already full, it makes room by dropping that subscriber’s oldest waiting frame.

**Data flow**: It receives a queue and one cursor/frame pair. If the queue has no space, it removes the oldest queued item, then inserts the new item; it returns nothing and only changes that queue.

**Call relations**: When `InProcessHub.publish` sends frames to subscribers, it schedules `_offer` on each subscriber’s event loop. This keeps publishing fast and lets slow subscribers lose old updates rather than slowing everyone down.


##### `InProcessHub.publish`  (lines 116–130)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This adds a new live frame for a turn, stores it briefly for replay, and sends it to all current subscribers. It is built so the publisher is never delayed by slow readers.

**Data flow**: It receives a turn ID and a live frame. Under a lock, it finds or creates that turn’s stream, increments the turn’s sequence number, turns that number into a cursor, stores the cursor/frame pair in the replay buffer, and copies the current subscriber list. If the frame ends or parks the turn and nobody is subscribed, it deletes the stream. After leaving the lock, it schedules delivery to each subscriber queue and returns the new cursor.

**Call relations**: This is the concrete implementation of `Hub.publish`. It creates `_TurnStream` state when the first frame for a turn arrives, uses `_offer` to deliver without blocking, and relies on subscriber queues registered by `InProcessHub.subscribe`.

*Call graph*: 2 external calls (__init__, deque).


##### `InProcessHub.subscribe`  (lines 132–162)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This lets a viewer follow one turn’s stream, starting with any recent frames it missed and then continuing with live frames. It is careful not to duplicate or skip frames during the handoff from replay to live delivery.

**Data flow**: It receives a turn ID and optional cursor. It creates a bounded queue for this subscriber, records the subscriber’s current event loop, finds or creates the turn stream, registers the subscriber, and snapshots all buffered frames newer than the cursor. It yields those replay frames first, then waits on the queue and yields each new live frame. When the caller stops listening, it removes the subscriber and deletes the turn stream if nobody else is watching.

**Call relations**: This is the concrete implementation of `Hub.subscribe`, used by surface code that pumps live updates to users. It shares turn state with `InProcessHub.publish`: publish appends frames and fans them to the queues that subscribe registered.

*Call graph*: 4 external calls (__init__, Queue, get_running_loop, deque).


##### `InProcessHub.covers`  (lines 164–172)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still reaches back far enough for a cursor. It helps a reconnecting surface decide whether it can resume smoothly or needs to redraw from durable state.

**Data flow**: It receives a turn ID and cursor. If the cursor is empty, or there is no stream or no buffered data for that turn, it returns false. Otherwise it compares the cursor with the oldest buffered cursor and returns true when the requested cursor is not older than what the buffer still holds.

**Call relations**: This is the concrete implementation of `Hub.covers`. Surface tailing code calls the hub’s cover check before choosing the resume path; this method answers using the replay buffer maintained by `InProcessHub.publish` and `InProcessHub.subscribe`.


### Artifact sharing
These files turn produced files into reusable artifacts and protect public downloads with signed short-lived tokens.

### `core/src/ufo/artifact_token.py`

`domain_logic` · `request handling`

This file is a small security gate for artifact downloads. An artifact is a stored file that the system is willing to share, but only through a signed token. Think of the token like a temporary claim ticket: it says which stored file may be picked up, what filename to suggest to the browser, and when the ticket expires.

The file defines the artifact storage prefix, the default token lifetime, and the download path used elsewhere. It also defines `ArtifactClaims`, the cleaned-up information the system trusts after a token has been checked.

The important protection is that the token is signed with a deployment secret. A signature is a tamper-evident seal: if someone changes the file key or expiry time inside the token, verification fails. The code also refuses tokens that point outside the artifact area, even if the signature is otherwise valid. That prevents a token from being used to fetch unrelated internal records, such as transcripts or maintenance data.

Without this file, shared file downloads would either need to be public forever or each caller would need to invent its own security checks. Here, token creation and token verification use the same payload shape and the same signing helper, so the producer and consumer agree on exactly what a valid artifact ticket means.

#### Function details

##### `mint_artifact_token`  (lines 35–39)

```
def mint_artifact_token(secret: str, blob_key: str, filename: str, expires_at: int) -> str
```

**Purpose**: Creates a signed download token for one artifact file. Callers use it when they want to give someone temporary permission to download a specific stored blob with a suggested filename.

**Data flow**: It receives the shared secret, the stored blob key, the filename to show to the downloader, and the expiry time. If the secret is missing, it stops with an artifact token error. Otherwise it packs the key, filename, and expiry into JSON bytes, signs those bytes with the secret, and returns the resulting token string.

**Call relations**: This is the token-making half of the flow. It relies on the shared token-signing helper to add the tamper-evident seal, and it raises the local artifact token error when the system is not configured safely. Later, the artifact download side can pass the returned token to `verify_artifact_token` to decide whether to serve the file.

*Call graph*: 3 external calls (__init__, dumps, sign_token).


##### `verify_artifact_token`  (lines 42–63)

```
def verify_artifact_token(token: str, secret: str, now: datetime) -> ArtifactClaims
```

**Purpose**: Checks that an artifact download token is genuine, still fresh, and only points inside the artifact storage area. It returns the trusted download details if the token passes all checks.

**Data flow**: It receives a token, the shared secret, and the current time. It first rejects a missing secret. Then it asks the token-signing helper to verify the signature and recover the JSON payload. It turns that payload into `ArtifactClaims`, checks that the blob key starts with the artifact prefix and does not contain a parent-directory escape like `..`, and compares the expiry time with the current timestamp. If anything is wrong, it raises an artifact token error; if everything is right, it returns the claims.

**Call relations**: This is the gatekeeper used when a download request arrives. It depends on the same signing helper used by `mint_artifact_token`, so a token minted by this module can be checked by this module. It also uses path parsing to defend the artifact namespace before the surrounding route serves any bytes.

*Call graph*: 6 external calls (__init__, __init__, timestamp, loads, PurePosixPath, verify_token).


### `core/src/ufo/artifacts.py`

`domain_logic` · `request handling`

An artifact is a file that a turn has shared out of the workspace, such as a report, image, or data file. This file defines how those shared files show up through the project’s object system. Without it, shared files would still exist in storage, but users and later turns would not have a clean way to find them, identify the newest version, copy them back into the workspace, or remove them.

The key idea is that an artifact is identified by two things together: the conversation that shared it and the filename. If the same conversation shares the same filename again, that is treated like a new version of the same object. If a different conversation shares the same filename, it becomes a different object. The visible object name combines a short conversation prefix with a safe filename slug, like putting files from the same meeting into the same labeled folder.

The main class, ArtifactObjects, provides the object actions. Listing groups stored share records into artifact objects. Getting returns the latest version’s details. Status creates a temporary download link and, when the file is not too large, copies the latest bytes back into the workspace so a later turn can reuse them. Creating and updating are deliberately refused, because artifacts must be made by sharing a real workspace file. Deleting removes all stored versions and their blobs.

#### Function details

##### `artifact_object_names`  (lines 65–85)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Builds the public object name for each artifact identity. It keeps files from different conversations distinct while making artifacts from the same conversation easy to recognize together.

**Data flow**: It receives pairs of conversation ID and filename. It turns each filename into a safe short slug, prefixes it with part of the conversation ID, checks whether any names collide, and adds a short digest only where needed. It returns a lookup from each original identity to its final object name.

**Call relations**: ArtifactObjects._groups uses this after reading share records from the database. The helper relies on _slug to clean filenames and _identity_digest only when two different identities would otherwise get the same visible name.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_groups); 1 external calls (Counter).


##### `_slug`  (lines 88–90)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short, safe piece of an object name. This prevents awkward characters, long names, or empty names from leaking into artifact names.

**Data flow**: It takes a filename, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims extra dashes, and cuts it to the configured length. If nothing usable remains, it returns a plain fallback word.

**Call relations**: artifact_object_names calls this for every filename while building the human-facing artifact names.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 93–95)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a stable fingerprint for an artifact identity. It is used only as a tie-breaker when two distinct artifacts would otherwise receive the same name.

**Data flow**: It takes a conversation ID and filename, combines them into text, and hashes that text with SHA-256, a standard one-way fingerprint method. It returns the hexadecimal hash string, from which callers use a short prefix.

**Call relations**: artifact_object_names calls this when duplicate generated names are detected, so the final names stay unique without becoming long in the common case.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 113–125)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Shows the artifacts available in the current workspace as object-list rows. It gives each row a name, a short summary, and searchable fields like filename and subject.

**Data flow**: It receives the tool context and a list query. It asks _groups for the current artifact groups, turns each group into an ObjectRow using the newest share for key display fields, then passes the rows through the object paging helper. It returns a page of artifact rows.

**Call relations**: This is called when the object system needs to list artifacts. It depends on _groups for the database grouping and _summary for the compact human-readable description before handing the rows to object_page.

*Call graph*: calls 2 internal fn (_groups, _summary); 2 external calls (__init__, object_page).


##### `ArtifactObjects.get`  (lines 127–146)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Returns the detailed description of one artifact, if it exists. The detail describes the latest version while also preserving when the first version was created.

**Data flow**: It receives a context and an artifact name. It uses _find to locate the matching version group; if none exists, it returns nothing. Otherwise it builds an ArtifactSpec from the newest share, records the first and latest timestamps, and adds a link back to the conversation that created it.

**Call relations**: The object system calls this when someone asks to inspect one artifact. It uses _find to resolve the public name, then packages the result into the standard object-detail shape used by the rest of the object system.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `ArtifactObjects.status`  (lines 148–167)

```
async def status(self, ctx: ToolContext, name: str) -> dict[str, JsonValue] | None
```

**Purpose**: Reports live status for one artifact and makes the latest version usable. It can create a temporary download URL and copy the file back into the workspace for reuse.

**Data flow**: It receives a context and artifact name. It finds the artifact group, looks at the newest share, optionally mints a time-limited download token if a secret is configured, and calls _materialize to copy the bytes into the workspace when allowed. It returns size, share time, turn ID, version count, download URL, and workspace path, or nothing if the artifact is not found.

**Call relations**: This runs as part of reading an object’s current status, especially during object_get. It calls _find to locate the artifact, mint_artifact_token to create a safe temporary link, and _materialize to write the file back into the sandbox workspace.

*Call graph*: calls 2 internal fn (_find, _materialize); 2 external calls (now, mint_artifact_token).


##### `ArtifactObjects.apply`  (lines 169–172)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None) -> None
```

**Purpose**: Refuses attempts to create or update artifacts through the object interface. This protects the rule that artifacts must come from sharing an actual workspace file.

**Data flow**: It receives the proposed name, new spec, and old spec, but does not use them to change storage. It immediately raises a VerbNotSupported error explaining that share_file is the correct way to create an artifact.

**Call relations**: The object system may call this for create or update-style actions. Instead of handing off to storage, it stops the request and points callers toward the sharing flow.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 174–186)

```
async def delete(self, ctx: ToolContext, name: str) -> None
```

**Purpose**: Deletes an artifact and all of its stored versions. After this, the database records are gone and the stored file bytes are removed, so old download links no longer work.

**Data flow**: It receives a context and artifact name. It finds the artifact group, raises an error if no such artifact exists, deletes matching shared_artifact rows for the current workspace, then deletes each referenced blob from blob storage. It changes both the database and blob store and returns no value.

**Call relations**: The object system calls this when an artifact is deleted. It first uses _find to translate the public object name into stored versions, then uses a workspace database transaction and the blob service to remove both metadata and bytes.

*Call graph*: calls 1 internal fn (_find); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._materialize`  (lines 188–199)

```
async def _materialize(self, ctx: ToolContext, name: str, latest: sa.Row) -> str | None
```

**Purpose**: Copies the latest artifact file back into the workspace, when it is small enough. This is what lets a later turn reuse a file that an earlier turn produced.

**Data flow**: It receives the context, artifact name, and latest share row. If the file is larger than the configured limit, it returns nothing and does not copy it. Otherwise it fetches the bytes from blob storage, writes them into the sandbox under artifacts/<name>/<filename>, and returns that workspace path; if the blob is missing, it raises an error.

**Call relations**: ArtifactObjects.status calls this after finding the latest version. It is deliberately kept out of plain get and delete paths so simply inspecting metadata does not unexpectedly write files into the workspace.

*Call graph*: called by 1 (status).


##### `ArtifactObjects._find`  (lines 201–203)

```
async def _find(self, name: str) -> tuple[sa.Row, ...] | None
```

**Purpose**: Looks up one artifact by its public object name. It hides the work of rebuilding artifact groups and comparing the generated names.

**Data flow**: It receives an artifact name. It asks _groups for all current named artifact groups, filters to the matching name, and returns that group of share rows if present. If there is no match, it returns nothing.

**Call relations**: ArtifactObjects.get, ArtifactObjects.status, and ArtifactObjects.delete all call this before doing their specific work. It provides a shared name-to-records lookup so those public operations behave consistently.

*Call graph*: calls 1 internal fn (_groups); called by 3 (delete, get, status).


##### `ArtifactObjects._groups`  (lines 205–238)

```
async def _groups(self) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Reads all shared artifacts for the current workspace and groups them into object-like bundles. Each bundle represents one conversation-and-filename identity, with the newest share first.

**Data flow**: It opens a workspace database transaction, selects shared artifact rows joined with their conversation IDs, and filters to the current workspace. It groups rows by conversation ID and filename, generates stable object names for those groups, sorts each group newest-first, then returns the named groups sorted by name.

**Call relations**: ArtifactObjects.list uses this to build the artifact listing, and _find uses it to resolve a single name. It hands raw database records through artifact_object_names so the rest of the class can work with the same public names users see.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, list); 3 external calls (select, workspace_tx, ws_current).


##### `_summary`  (lines 241–247)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short, readable one-line summary for an artifact list row. It tells a user what the latest file is and whether there are multiple versions.

**Data flow**: It receives a group of share rows with the newest first. It reads the latest filename, media type, size, share date, and the number of versions, then formats those into a short text string capped at the configured summary length.

**Call relations**: ArtifactObjects.list calls this while turning artifact groups into rows for display. It keeps list output compact while still giving enough context to recognize a file.

*Call graph*: called by 1 (list).


### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file solves a simple but important problem: shared files need to be downloadable from places like the web app or Slack, but the system must not give file bytes to just anyone. It creates one always-available FastAPI route, meaning an HTTP endpoint, for artifact downloads. A caller must bring a token, which is like a time-limited claim ticket signed with this deployment's secret key. The route first checks that the token is present. Then it verifies that the token is real and still valid. If the token is missing, invalid, expired, or otherwise wrong, the caller gets an error instead of the file.

Once the token is trusted, the route checks whether the requested blob actually exists in the blob store. This avoids opening a download stream for a file that is gone. If the token includes a filename, the route adds a download header so the browser saves the file with a helpful name. Finally, it returns a streaming response. Streaming means the file is sent in small pieces rather than loaded all at once into memory. That matters for large files and many simultaneous downloads: the server behaves more like a pipe than a bucket.

#### Function details

##### `download`  (lines 26–53)

```
async def download(request: Request, token: str='') -> StreamingResponse
```

**Purpose**: This is the download endpoint for artifact files. It checks the caller's token, confirms the requested blob exists, and then streams the file back without loading the whole thing into memory.

**Data flow**: The function receives an HTTP request and an optional token string from the query. It reads the blob store and artifact-token secret from the application state, rejects the request if the token is missing, and asks the token verifier to decode and validate it using the current UTC time. From the verified token it gets the blob key and optional filename, checks that the blob exists, builds a safe download filename header when needed, and returns a streaming response whose body comes from the blob store. If anything is wrong, it returns an HTTP error instead of bytes.

**Call relations**: FastAPI calls this function when a client makes a GET request to the artifact download path. Inside the request flow, it uses `verify_artifact_token` to decide whether the link is allowed, `datetime.now` to check the token against the current time, and `quote` to safely encode filenames for browser download headers. At the end it hands the blob stream to FastAPI's `StreamingResponse`, which sends the file back to the client piece by piece.

*Call graph*: 5 external calls (now, HTTPException, StreamingResponse, verify_artifact_token, quote).


### Transcript writeback
This file durably commits and reads the accepted conversation transcript without allowing stale writes to replace newer versions.

### `core/src/ufo/loop/transcript.py`

`io_transport` · `turn completion and transcript repair publishing`

A conversation can be updated by different parts of the system, especially when a turn finishes or when a repair process republishes a final result. This file protects the transcript like a numbered notebook: each saved conversation has a sequence number, and only a version with a newer sequence number is allowed to replace what is already stored. That matters because without this guard, a late or repeated write could accidentally erase the true latest conversation state.

The main piece is the `Transcript` object. It knows two things: where the shared blob storage is, and which conversation it belongs to. A blob store is a simple place to save and load raw bytes by key, like a labeled drawer. The file uses `transcript_key` to choose the drawer label for this conversation, and `encode` / `decode` to turn the conversation between an in-memory object and stored bytes.

Reading is forgiving: if there is no saved transcript yet, it returns nothing instead of treating that as an error. Writing is cautious: it first reads the current saved transcript, compares sequence numbers, and only writes if the new one is truly later. The first write for a given sequence stays authoritative.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: This function loads the saved transcript for this conversation, if one exists. It gives callers a normal conversation object instead of raw stored bytes, and returns nothing when the transcript has not been created yet.

**Data flow**: It starts with the conversation ID stored on the `Transcript` object. It turns that ID into the storage key, asks the blob store for the saved bytes, and if the bytes are found, decodes them into a `Conversation`. If the blob store says the item is missing, the function returns `None` rather than raising an error.

**Call relations**: This is the read side of the transcript wrapper. `Transcript.write` calls it before saving, so it can compare the currently saved sequence number with the new one and avoid overwriting newer data.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: This function saves a conversation transcript only if it is newer than what is already stored. It is the safety gate that prevents stale or duplicate work from replacing the accepted transcript.

**Data flow**: It receives a `Conversation` to save. First it reads the currently stored transcript. If one already exists and its sequence number is the same as or greater than the incoming conversation's sequence number, it stops without changing storage. Otherwise, it encodes the incoming conversation into bytes and writes those bytes to the blob store under this conversation's transcript key.

**Call relations**: This is used when a completed turn, or a repair flow publishing a committed final state, needs to persist the transcript. It relies on `Transcript.read` to inspect the current saved state, then hands the actual byte conversion to `encode` and the storage location calculation to `transcript_key` before writing to the blob store.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### Redis stream hub
These files package and implement the Redis-backed live-frame hub for cross-process response streaming.

### `extensions/redis_hub/ufo_ext_redis_hub/__init__.py`

`other` · `import time`

This is an empty Python package initializer. In Python, a file named `__init__.py` is used to tell the interpreter, “this folder is a package you can import from.” Think of it like a label on a box: the label does not contain the tools, but it lets the rest of the system recognize the box and open it by name. Here, the package is for a Redis Hub extension, but this particular file does not define setup logic, shared constants, or shortcuts to other modules. Its main value is structural: without it, some Python environments or packaging tools may not treat `ufo_ext_redis_hub` as an importable package in the expected way. Nothing happens at runtime when this file is imported beyond Python creating the package namespace.


### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `request handling and live streaming`

When an assistant is producing an answer, the user interface needs small live updates: new text, tool calls, cost changes, parking messages, and final status. This file sends those updates through Redis Streams, which are like named append-only message logs. Each turn gets its own Redis stream, so a browser or surface can reconnect with a cursor and continue reading from where it left off.

The important design choice is that these frames are useful but not the source of truth. If Redis trims an old frame or the stream expires, correctness is not lost; the system can redraw from the saved final turn state. This keeps live streaming fast and scalable without turning every token into permanent database data.

Redis client objects are kept per asyncio event loop. An event loop is the scheduler that runs async work, and Redis clients are tied to the loop that created them. Sharing one client between the workflow loop and the serving loop would break in subtle ways, so this hub creates a separate client for each loop on first use.

Publishing turns a frame into a small JSON message and appends it to the stream. Subscribing repeatedly reads forward from a cursor, first replaying retained entries and then blocking briefly while waiting for new ones. A timeout while waiting is treated as normal idleness, not an error.

#### Function details

##### `frame_payload`  (lines 53–56)

```
def frame_payload(frame: LiveFrame) -> dict[str, object]
```

**Purpose**: This turns one live frame into a simple wire format that can be stored in Redis as JSON. It records both what kind of frame it is and the frame’s data, so the reader can rebuild the same kind later.

**Data flow**: It receives a live frame object, looks up the short kind name for that frame type, and asks the frame to turn its fields into JSON-friendly values. It returns a dictionary with a kind tag and a data block ready to be encoded as JSON.

**Call relations**: When RedisStreamHub.publish is about to append a frame to Redis, it calls this function first. The result is then serialized and stored as the stream entry payload.

*Call graph*: called by 1 (publish); 1 external calls (model_dump).


##### `frame_from_payload`  (lines 59–63)

```
def frame_from_payload(payload: dict[str, object]) -> LiveFrame
```

**Purpose**: This rebuilds a live frame object from the JSON-shaped payload read out of Redis. It also protects the reader by rejecting unknown frame kinds instead of guessing.

**Data flow**: It receives a dictionary that should contain a kind name and data. It checks that the kind is known, picks the matching frame model, validates the data, and returns the reconstructed live frame.

**Call relations**: RedisStreamHub.subscribe calls this after reading and decoding a Redis stream entry. That lets subscribers receive normal live frame objects rather than raw JSON dictionaries.

*Call graph*: called by 1 (subscribe); 1 external calls (cast).


##### `_stream_id`  (lines 66–68)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: This converts a Redis stream entry id into two numbers so ids can be compared safely. Redis ids look like a timestamp plus a sequence number, such as “12345-0”.

**Data flow**: It receives a Redis entry id string, splits it into the millisecond timestamp part and the sequence part, converts both to integers, and returns them as a pair. If the sequence part is missing, it treats it as zero.

**Call relations**: RedisStreamHub.covers uses this helper when deciding whether a saved cursor still points to an entry range that Redis has retained. Comparing the numeric pairs tells it which stream id came first.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 71–80)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: This extracts the actual stream entries from the shape returned by Redis xread. It deliberately fails loudly if Redis returns an unexpected response shape, because silently misreading stream data would be worse.

**Data flow**: It receives the raw xread response. If the response is empty, it returns an empty list. If the response is not the expected list form, it raises an error. Otherwise, it pulls out and returns the entries for the stream.

**Call relations**: RedisStreamHub.subscribe calls this after each Redis read. It turns the Redis library’s nested response into the simple list of entries that the subscribe loop can process.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 96–102)

```
def _client(self) -> Redis
```

**Purpose**: This gives the hub a Redis client that is safe to use on the current async event loop. It avoids sharing Redis client state between different loops in the same process.

**Data flow**: It checks which event loop is currently running, looks for an existing Redis client for that loop, and returns it if found. If none exists yet, it creates one from the Redis URL, stores it for that loop, and returns it.

**Call relations**: Publishing, subscribing, and coverage checks all call this before talking to Redis. It is the small gatekeeper that keeps Redis network access tied to the right async scheduler.

*Call graph*: called by 3 (covers, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 104–105)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: This builds the Redis stream name for a specific turn. It gives every turn its own channel of live updates.

**Data flow**: It receives a turn id, combines it with the fixed stream prefix, and returns the Redis key string for that turn’s stream.

**Call relations**: RedisStreamHub.publish, RedisStreamHub.subscribe, and RedisStreamHub.covers all call this before accessing Redis. It keeps stream naming consistent across writers, readers, and reconnect checks.

*Call graph*: called by 3 (covers, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 107–114)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This appends one live frame to the Redis stream for a turn and returns the new cursor. A caller uses it whenever there is a fresh live update to show to connected surfaces.

**Data flow**: It receives a turn id and a live frame. It builds the stream name, converts the frame to a JSON string, appends it to Redis with a maximum retained length, refreshes the stream’s expiration time, and returns the Redis entry id for the new frame.

**Call relations**: This is the writing side of the hub. It relies on _stream for the correct Redis key, frame_payload and JSON encoding for the stored message, and _client for the Redis connection. Subscribers later use the returned entry id as a cursor for continuing from that point.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 116–140)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the reading side of the hub. It continuously yields live frames for a turn, starting after a cursor if one is provided, so a surface can replay missed updates and then wait for new ones.

**Data flow**: It receives a turn id and an optional cursor. It chooses the Redis stream, starts reading from the cursor or the beginning, repeatedly asks Redis for entries, waits briefly when there are none, decodes each stored JSON frame, updates the cursor, and yields the cursor plus the rebuilt live frame. Timeout while waiting simply causes it to try again.

**Call relations**: A live surface calls this to follow a turn. Inside the loop it uses _client to read from Redis, _stream to find the right stream, _stream_entries to normalize Redis responses, and frame_from_payload after JSON decoding to turn stored messages back into live frame objects.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 142–148)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This answers whether Redis still has enough retained stream history for a reconnecting reader to resume from a cursor without a gap. If not, the caller should redraw from a safer source instead of trusting the stream.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, it returns false. Otherwise it asks Redis for the first retained entry in that turn’s stream. If the stream is empty, it returns false. If there is an entry, it compares the first retained id with the cursor and returns true only when the cursor is still within the retained range.

**Call relations**: Reconnect logic can call this before using subscribe with an old cursor. It uses _stream to identify the stream, _client to query Redis, and _stream_id to compare Redis ids in chronological order.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).

## 📊 State Registers Touched

- `reg-surface-installation-binding` — The stored connection between outside channels like Slack, web chat, or terminal clients and an internal workspace conversation.
- `reg-inbound-message-dedup` — The durable inbox and duplicate-detection state for messages arriving from external surfaces.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-cancellation-state` — The shared stop signal and saved cancellation status for turns, child turns, and paused work.
- `reg-sandbox-workspace-handle` — The saved handle and lease for the safe workspace where a conversation can run commands and keep files.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-live-stream-hub` — The live stream of turn updates that clients can watch and replay after reconnecting.
- `reg-transcript-compaction-store` — The saved conversation transcript and compacted summaries used to rebuild context and inspect past turns.
- `reg-artifact-download-tokens` — The short-lived signed passes that let private files produced by a turn be downloaded safely.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
- `reg-surface-writeback-state` — Pending and completed outbound reply/writeback records used to deliver final turn results back to external surfaces without duplication.
- `reg-artifact-share-registry` — Durable artifact metadata linking produced files to workspaces, conversations, turns, blob keys, and sharing visibility.
- `reg-conversation-todo-goal-state` — Persistent per-conversation checklist, goal, or task-progress state maintained by todo/planning tools across turns.
- `reg-turn-context-token-budget` — The per-turn context-window and token-budget state used to compact history, construct model requests, and constrain model/tool work.
- `reg-site-preview-serving-state` — The extension-maintained mapping from generated site/app outputs to served preview routes or shareable live site handles.
- `reg-crypto-signing-encryption-keyring` — Stable secret key material used to sign/verify session, OAuth/state, artifact, and filesystem tokens and to encrypt/decrypt stored credentials.
