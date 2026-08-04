# Live Updates, Artifact Sharing, and Surface Writeback  `stage-17`

This stage runs during and just after an agent turn, when the system needs to show progress, share results, and let people download files. It is the “live display and handoff” layer. The main hub keeps a short stream of update frames, such as new text, tool activity, cost changes, and the final answer. If a screen disconnects and returns, it can replay recent frames instead of losing the thread. The hub tail adds a safety check against the database, so a late viewer can still learn whether the turn already finished and stop cleanly. When the system is spread across multiple server processes, the Redis hub carries the same live frames through Redis Streams, a shared message pipe, so watchers in other processes can follow along. For shared files, the artifact model represents files produced by a turn and allows listing, reading, copying back, or deleting them. The artifact download route is the guarded doorway: it serves the stored file only when the request includes a valid signed token.

## Files in this stage

### Live turn streaming
Live surfaces follow in-progress turn frames, replay recent updates for reconnects, and optionally share streams across processes through Redis.

### `core/src/ufo/surfaces/hub_tail.py`

`io_transport` · `request handling for live turn streaming`

A “turn” can emit live frames while it is running, like words appearing in a streaming chat response. The problem is that a subscriber may arrive after the turn has already started, or even after it has already finished somewhere else. Relying only on the live message hub would be fragile: a late subscriber might miss the final “done” signal. This file fixes that by watching two sources at once. One source is the hub, which streams live frames quickly. The other source is the durable database record, which is slower but authoritative, like checking the official scoreboard after listening to a radio broadcast. The main stream ends when it sees either a terminal frame, meaning the turn is finished, or a parked frame, meaning the turn is paused because of a spend cap or access issue. If a caller reconnects with the last cursor it saw, the code asks the hub whether it can resume from that point; otherwise it starts from the retained live history. The HubTailer class wraps this behavior so surface code can ask to “tail” a turn without knowing about the hub or the database polling details.

#### Function details

##### `tail_frames`  (lines 27–53)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the main async stream for following one turn’s live frames until the turn finishes or is parked. It protects callers from missed endings by combining fast live hub updates with slower but reliable database status checks.

**Data flow**: It receives a hub, a turn id, and optionally the last cursor the caller already saw. It checks whether the hub can resume from that cursor, starts one background task to read hub frames and another to poll the database, then yields frames as they arrive from a shared queue. Before waiting, it also checks the stored turn status once, so an already-finished or already-parked turn can end immediately. When a terminal or parked frame appears, it stops and cancels the background tasks.

**Call relations**: HubTailer.tail calls this when a surface wants to follow a turn. Inside, it asks Hub.covers whether a reconnect cursor is still usable, starts _pump to read Hub.subscribe, starts _poll_status to watch the database, and calls turn_status_frame to detect an already-known ending.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, turn_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 56–63)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper copies live frames from the hub into the shared queue used by tail_frames. It is the fast path for frames produced while the turn is actively running.

**Data flow**: It receives the hub, the turn id, the cursor to start from, and the queue where frames should go. It subscribes to the hub and, for every cursor-and-frame pair the hub emits, puts that pair into the queue. If the subscription fails, it logs the failure instead of crashing the whole tailing flow.

**Call relations**: tail_frames starts this helper as a background task. It depends on Hub.subscribe for the live stream, and it feeds the same queue that _poll_status also feeds, so tail_frames can consume both live updates and durable status results through one path.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 66–75)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper periodically checks the database for the turn’s final or parked state. It is the safety net that makes sure the stream ends correctly even if the live hub missed something.

**Data flow**: It receives a turn id and the shared frame queue. Once per polling interval, it calls turn_status_frame. If that returns a terminal or parked frame, it puts that frame into the queue with an empty cursor and stops. If anything goes wrong while polling, it logs the error.

**Call relations**: tail_frames starts this alongside _pump. While _pump listens to live hub messages, _poll_status checks the durable stored status. Whichever source first provides an ending frame lets tail_frames finish the stream.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `turn_status_frame`  (lines 78–107)

```
async def turn_status_frame(turn_id: UUID) -> LiveFrame | None
```

**Purpose**: This function asks the database whether a turn has reached a stream-ending state. It returns a terminal frame for a completed turn, a parked frame for a paused turn, or nothing if the turn is still queued or running.

**Data flow**: It receives a turn id. It opens a workspace database transaction, reads the turn’s status, stored terminal data, workspace, speaker, behalf-of member, and admission source. If the turn has stored terminal data, it validates that data and wraps it as a Terminal frame. If the turn is not parked, it returns nothing. If the turn is parked, it checks whether the relevant member is still admitted by the seat rules; if access was revoked, it returns a Parked frame with the revoked-seat message. Otherwise it returns a Parked frame explaining that the turn is over a spend cap and will resume when the cap is raised.

**Call relations**: tail_frames calls this once before waiting, so already-ended turns finish immediately. _poll_status calls it repeatedly as the durable fallback. It relies on the schema tables, TerminalFrame validation, and seat-admission helpers to turn stored database state into the same kind of live frame the streaming surface expects.

*Call graph*: called by 2 (_poll_status, tail_frames); 8 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx, gate_member, seat_gate_absent).


##### `HubTailer.tail`  (lines 119–120)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This small wrapper exposes the tailing behavior as a method on a HubTailer object. It lets other surface code request a live turn stream without directly importing or wiring the lower-level tail_frames function.

**Data flow**: It receives a turn id and optional last-seen cursor. It uses the Hub stored on the HubTailer instance and passes everything to tail_frames. The result is an async iterator that yields cursor-and-frame pairs until the turn finishes or parks.

**Call relations**: Surface code calls this method through the injected HubTailer. The method simply hands off to tail_frames, which does the real work of combining hub subscription, database polling, and clean shutdown.

*Call graph*: calls 1 internal fn (tail_frames).


### `core/src/ufo/hub.py`

`io_transport` · `request handling and live turn streaming`

This file is the project’s in-memory “broadcast desk” for live turn updates. During a turn, many small frames may be produced: text chunks, cost ticks, tool calls, skill loads, and eventually a final terminal frame or a parked message. The hub lets publishers add those frames without waiting for slow readers, then fans them out to any subscribed surfaces such as a CLI or web view.

The important problem it solves is continuity. If a browser tab briefly disconnects, it can come back with a cursor, which is just a small marker saying “I last saw frame number N.” The hub can replay frames after that cursor, as long as they are still in its bounded ring buffer. Think of it like a short DVR buffer for a live broadcast.

The in-process implementation, `InProcessHub`, stores one stream per turn. Each stream has a numbered sequence, a replay buffer, and a list of live subscribers. A lock protects this shared state, because publishers and subscribers may run on different event loops or threads. If a subscriber’s queue is full, the oldest queued frame is dropped rather than blocking the publisher. That means live display can lose detail under pressure, but the agent’s work is never slowed down by a stuck screen. Finished streams are removed when nobody is listening, keeping memory bounded.

#### Function details

##### `Hub.publish`  (lines 75–75)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This is the interface promise for adding one live frame to a turn’s stream. Code that only knows it has a `Hub` can call this without caring whether the stream is local memory or some shared backend.

**Data flow**: A caller gives it a turn ID and a live frame, such as text, cost, tool activity, or a final result. The hub implementation records and broadcasts that frame, then returns a cursor that marks the frame’s position in the stream.

**Call relations**: This protocol method is used as the common contract for publishers. For example, `core/src/ufo/loop/queue._commit_failed_terminal` can publish a terminal failure frame through this interface, while the actual work is supplied by an implementation such as `InProcessHub.publish`.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 77–79)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the interface promise for following a turn’s live stream. A surface uses it to receive frames, optionally starting after a cursor it already saw.

**Data flow**: A caller gives it a turn ID and, optionally, a cursor. The hub implementation first yields any saved frames after that cursor, then keeps yielding new live frames as they arrive.

**Call relations**: `core/src/ufo/surfaces/hub_tail._pump` calls this when it needs to feed live turn frames to a user-facing surface. Implementations such as `InProcessHub.subscribe` provide the replay-and-follow behavior behind the interface.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 81–81)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for asking whether a cursor is still safely replayable. It lets a surface decide whether it can resume smoothly or must refresh from durable state instead.

**Data flow**: A caller gives it a turn ID and cursor. The hub implementation checks whether its saved buffer still reaches far enough back, then returns true or false.

**Call relations**: `core/src/ufo/surfaces/hub_tail.tail_frames` calls this before tailing frames, so it knows whether a reconnect can continue without a gap. `InProcessHub.covers` is the local-memory version of that check.

*Call graph*: called by 1 (tail_frames).


##### `_offer`  (lines 84–87)

```
def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None
```

**Purpose**: This helper puts one frame into a subscriber’s queue without ever blocking. If the subscriber is already backed up, it drops the oldest waiting frame to make room.

**Data flow**: It receives a queue and a cursor-frame pair. If the queue is full, it removes one old queued item, then adds the new item; it returns nothing and only changes the queue.

**Call relations**: `InProcessHub.publish` schedules this helper on each subscriber’s event loop. That handoff matters because subscribers may be running on different loops or threads, so the actual queue write must happen safely on the subscriber side.


##### `InProcessHub.publish`  (lines 116–130)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This adds a new live frame to the in-memory stream for a turn and broadcasts it to current subscribers. It is designed so publishing never waits for slow subscribers.

**Data flow**: It receives a turn ID and a frame. Under a lock, it creates the turn stream if needed, increments the stream’s sequence number, stores the frame in the replay buffer, and takes a snapshot of current subscribers. After releasing the lock, it schedules delivery to each subscriber queue and returns the new cursor string.

**Call relations**: This is the concrete local implementation of `Hub.publish`. Publishing code can use the `Hub` interface, while this method does the real in-process fan-out; when queues need to receive the frame, it hands off to `_offer` through each subscriber’s event loop.

*Call graph*: 2 external calls (__init__, deque).


##### `InProcessHub.subscribe`  (lines 132–162)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This lets a caller watch a turn’s stream from a chosen point, first replaying saved frames and then waiting for new ones. It is the main path used by live surfaces to stay updated.

**Data flow**: It receives a turn ID and optional cursor. It creates a private queue for this subscriber, registers that queue under a lock, and copies any buffered frames newer than the cursor. It then yields the replay frames, followed by live frames taken from the queue. When the caller stops listening, it removes the subscriber and deletes the stream if nobody else is attached.

**Call relations**: This is the concrete local implementation of `Hub.subscribe`, used by the surface tailing code through the protocol. Its locking order is important: registration and replay snapshot happen together, so a frame is either included in replay or sent live, not both and not neither.

*Call graph*: 4 external calls (__init__, Queue, get_running_loop, deque).


##### `InProcessHub.covers`  (lines 164–172)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still contains enough history to resume from a cursor. It protects reconnecting clients from assuming they can replay frames that have already been discarded.

**Data flow**: It receives a turn ID and cursor. If there is no cursor, no stream, or no buffered frames, it returns false. Otherwise it compares the requested cursor with the earliest cursor still held and returns whether replay can cover that point.

**Call relations**: This is the concrete local implementation of `Hub.covers`. The surface tailing flow asks this before subscribing, so it can choose between smooth cursor replay and falling back to a more complete refresh path.


### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `request handling / live streaming`

This file is a live message hub built on Redis Streams, which are Redis lists designed for ordered event messages. Think of each turn as having its own temporary ticker tape. As work happens, the system writes small frame messages onto that tape. A browser-facing surface can then read forward from a cursor, replaying anything it missed and waiting for new messages.

The important design choice is that these live frames are not treated as permanent truth. They are short-lived updates. Redis keeps a trimmed stream with a maximum length and an expiry time, while the final durable answer is stored elsewhere. If an old live frame disappears, the user interface may need to redraw, but correctness is not lost.

The file also solves a subtle async problem. Redis async clients are tied to the event loop that created them. An event loop is the scheduler that runs asynchronous tasks. This project may publish frames from one loop and subscribe from another, so `RedisStreamHub` keeps a separate Redis client per loop instead of sharing one unsafe client.

The main flow is simple: `publish` turns a live frame into JSON and appends it to a Redis stream; `subscribe` reads entries after a cursor and converts them back into frame objects; `covers` checks whether a saved cursor still points into data Redis has retained.

#### Function details

##### `frame_payload`  (lines 53–56)

```
def frame_payload(frame: LiveFrame) -> dict[str, object]
```

**Purpose**: This function turns one live frame object into a small plain-data package that can be sent through Redis. It adds a kind label, such as text delta or terminal frame, so the receiver knows which exact frame type to rebuild later.

**Data flow**: It receives a `LiveFrame` object. It looks up the frame's concrete type, asks the frame to dump its fields into JSON-friendly values, and returns a dictionary with `kind` and `data`. Nothing outside the function is changed.

**Call relations**: When `RedisStreamHub.publish` is about to write a frame to Redis, it calls this function first. The returned package is then converted to JSON text and stored in the stream.

*Call graph*: called by 1 (publish); 1 external calls (model_dump).


##### `frame_from_payload`  (lines 59–63)

```
def frame_from_payload(payload: dict[str, object]) -> LiveFrame
```

**Purpose**: This function rebuilds a live frame object from the plain-data package read out of Redis. It protects the reader by rejecting unknown frame kind labels instead of guessing.

**Data flow**: It receives a dictionary that should contain a `kind` label and frame `data`. It checks that the kind is a known string, chooses the matching frame model, validates the data, and returns a `LiveFrame` object. If the kind is not recognized, it raises an error.

**Call relations**: `RedisStreamHub.subscribe` calls this after reading and decoding JSON from Redis. This is the point where stored wire data becomes the original kind of live frame again.

*Call graph*: called by 1 (subscribe); 1 external calls (cast).


##### `_stream_id`  (lines 66–68)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: This helper turns a Redis stream entry id into numbers that can be compared safely. Redis ids look like `milliseconds-sequence`, and comparing the numeric parts tells which entry came first.

**Data flow**: It receives an entry id as text. It splits the text around the dash, converts the millisecond and sequence parts to integers, and returns them as a pair. It does not read or change any outside state.

**Call relations**: `RedisStreamHub.covers` uses this helper when deciding whether a saved cursor is still at or after the first retained Redis entry. That comparison tells whether replay can continue without a gap.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 71–80)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: This helper extracts the actual stream entries from Redis's `XREAD` response. It also checks that the response shape is the expected one, so the code fails clearly if Redis returns a different protocol format.

**Data flow**: It receives the raw batch returned by Redis. If the batch is empty, it returns an empty list. If the batch is not the expected list shape, it raises a type error. Otherwise, it returns the entries for the first stream in the response.

**Call relations**: `RedisStreamHub.subscribe` calls this after each Redis read. It turns Redis's nested response into a simple list that the subscribe loop can walk through and yield to callers.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 96–102)

```
def _client(self) -> Redis
```

**Purpose**: This method gives the hub a Redis client that is safe for the currently running async event loop. It avoids sharing one async Redis connection across loops, which can break because its internal futures belong to the loop that created it.

**Data flow**: It checks the current running event loop. If this hub already has a Redis client for that loop, it returns it. If not, it creates a new client from the hub's Redis URL, stores it under that loop, and returns the new client.

**Call relations**: `publish`, `subscribe`, and `covers` all call this before talking to Redis. It is the common doorway that keeps Redis access loop-local while letting the same hub object be used from different parts of the process.

*Call graph*: called by 3 (covers, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 104–105)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: This method builds the Redis stream name for a turn. It gives every turn its own stream so live frames for different turns do not mix together.

**Data flow**: It receives a turn UUID. It combines the fixed stream prefix with that UUID and returns the Redis key as text. It does not contact Redis or change state.

**Call relations**: `publish`, `subscribe`, and `covers` all call this before using Redis. It ensures all three operations agree on the exact stream key for the same turn.

*Call graph*: called by 3 (covers, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 107–114)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This method appends one live frame to the Redis stream for a turn and returns the new cursor. Callers use it when fresh live output is produced and needs to be visible to subscribers in other processes or event loops.

**Data flow**: It receives a turn id and a live frame. It builds the turn's stream name, converts the frame into a JSON string, then uses a Redis pipeline to add the entry and refresh the stream's expiry time. It returns the Redis entry id, which acts as a cursor for resuming later.

**Call relations**: This is the write side of the hub. It calls `_stream` to choose the Redis key, `frame_payload` and `json.dumps` to create the stored message, and `_client` to get the correct Redis connection before appending the entry.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 116–140)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This method continuously reads live frames for one turn, starting after a given cursor. It lets a listener replay retained messages first and then wait for new ones without losing its place.

**Data flow**: It receives a turn id and an optional cursor. It turns the turn id into a stream key, starts reading after the cursor or from the beginning, and repeatedly asks Redis for entries. For each entry, it updates the cursor, parses the stored JSON, rebuilds the live frame, and yields the pair of new cursor and frame. Timeouts while waiting are treated as normal idleness, so the loop simply tries again.

**Call relations**: This is the read side of the hub. It uses `_client` and `_stream` to reach Redis, `_stream_entries` to simplify Redis read responses, `json.loads` to decode stored text, and `frame_from_payload` to rebuild the frame before yielding it to the caller.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 142–148)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This method checks whether Redis still has enough retained stream history for a subscriber to resume from a saved cursor. If it returns false, the caller should assume there may be a gap and redraw from a safer source.

**Data flow**: It receives a turn id and a cursor. If the cursor is empty, it returns false. Otherwise, it reads the first retained entry in the turn's Redis stream. If there is no retained entry, it returns false. If there is one, it compares that first entry id with the cursor and returns whether the cursor is still within the retained range.

**Call relations**: Reconnect logic can use this before subscribing from an old cursor. Internally it calls `_stream` to find the Redis key, `_client` to read Redis, and `_stream_id` to compare Redis entry ids in chronological order.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


### Artifact sharing
Shared turn files are represented as managed artifacts and served to users only through token-guarded download routes.

### `core/src/ufo/artifacts.py`

`domain_logic` · `request handling`

Artifacts are how this system treats files produced during a conversation as reusable workspace objects. Think of share_file as putting a finished document into a shared cabinet. This file defines how that cabinet is named, searched, opened, and emptied.

Each artifact is identified by two things: the conversation it came from and the filename. If the same filename is shared again in the same conversation, it becomes a newer version of the same artifact. If another conversation shares a file with the same name, it is a separate artifact. To make this visible to users, object names start with a short conversation prefix and then a cleaned-up filename.

The central class, ArtifactObjects, is the object-store surface for artifacts. It can list visible artifacts, get the latest version’s details, report status, copy small files back into the workspace, create a temporary download link, and delete all versions. It checks visibility so an agent only sees files from the right workspace, agent, and audience. It also protects against races: before copying or deleting, it confirms the artifact is still visible and unchanged enough to trust.

At the bottom, ARTIFACT_OBJECT registers this behavior with the larger object system, including user guidance and the allowed verbs.

#### Function details

##### `artifact_object_names`  (lines 67–87)

```
def artifact_object_names(shares: Iterable[tuple[UUID, str]]) -> dict[tuple[UUID, str], str]
```

**Purpose**: Builds stable, human-readable object names for shared artifact identities. It keeps artifacts from different conversations separate even when their filenames match.

**Data flow**: It receives pairs of conversation ID and filename. It turns each filename into a safe short slug, prefixes it with part of the conversation ID, checks whether any names still collide, and adds a short digest only for the colliding cases. It returns a mapping from each original identity to its final object name.

**Call relations**: ArtifactObjects._groups calls this after it has gathered database rows and grouped them by conversation and filename. This function relies on _slug for the readable filename part, _identity_digest for rare collision suffixes, and Counter to notice duplicate proposed names.

*Call graph*: calls 2 internal fn (_identity_digest, _slug); called by 1 (_groups); 1 external calls (Counter).


##### `_slug`  (lines 90–92)

```
def _slug(filename: str) -> str
```

**Purpose**: Turns a filename into a short safe text fragment for an artifact object name. This makes names easier to type, list, and compare.

**Data flow**: It takes a filename, lowercases it, replaces runs of non-letter-or-number characters with dashes, trims extra dashes, and limits the result to a fixed length. If nothing usable remains, it returns the fallback word “artifact”.

**Call relations**: artifact_object_names calls this while building the base name for each artifact. It is the small cleanup step that keeps object names tidy before collision handling happens.

*Call graph*: called by 1 (artifact_object_names).


##### `_identity_digest`  (lines 95–97)

```
def _identity_digest(identity: tuple[UUID, str]) -> str
```

**Purpose**: Creates a shortable fingerprint for a specific conversation-and-filename identity. It is used only when two different artifact identities would otherwise get the same object name.

**Data flow**: It receives a conversation ID and filename, joins them into one string, hashes that string with SHA-256, and returns the hexadecimal hash text. The caller later keeps only the needed prefix.

**Call relations**: artifact_object_names calls this when duplicate base names are found. It hands back the extra distinguishing suffix that prevents two different artifacts from sharing one name.

*Call graph*: called by 1 (artifact_object_names); 1 external calls (sha256).


##### `ArtifactObjects.list`  (lines 115–127)

```
async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage
```

**Purpose**: Returns a page of artifact objects the current context is allowed to see. It shows each artifact once, using its latest version for the summary fields.

**Data flow**: It reads the visible artifact groups through _groups. For each group, it builds an object row with the object name, a short summary, the filename, and the latest subject caption. It then passes those rows through the object paging helper and returns the requested page.

**Call relations**: The wider object system calls this when someone lists artifact objects. It delegates database gathering and naming to _groups, summary text to _summary, and final pagination to object_page.

*Call graph*: calls 2 internal fn (_groups, _summary); 2 external calls (__init__, object_page).


##### `ArtifactObjects.get`  (lines 129–148)

```
async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[ArtifactSpec] | None
```

**Purpose**: Fetches the main details for one named artifact without copying its bytes into the workspace. It describes the latest version and links it back to the conversation where it was created.

**Data flow**: It receives an object name and looks up matching shares with _find. If there is no match, it returns null. Otherwise it builds an ArtifactSpec from the latest share, sets created and updated times from the oldest and newest versions, adds a created_in link to the conversation, and returns an object detail record.

**Call relations**: The object system calls this for object_get-style detail retrieval. It asks _find to resolve the user-facing name, then packages the result into ObjectDetail, ObjectLink, and ObjectRef objects for the rest of the object API.

*Call graph*: calls 1 internal fn (_find); 4 external calls (__init__, __init__, __init__, __init__).


##### `ArtifactObjects.status`  (lines 150–192)

```
async def status(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> dict[str, JsonValue] | None
```

**Purpose**: Reports live status for an artifact and, when possible, makes the latest file usable again. For small enough files, it copies the bytes back into the conversation workspace; it can also issue a fresh temporary download link.

**Data flow**: It takes a context, object name, and optional expected generation. It finds the artifact, optionally reads the latest blob bytes if the file is under the materialization size limit, verifies in the database that the artifact is still visible, writes the file into an artifacts folder in the sandbox when bytes were loaded, mints a download URL when token signing is configured, and returns size, share time, turn ID, version count, download URL, and workspace path.

**Call relations**: The object seam calls status during object_get, which is why this is where the workspace copy happens. It uses _find to locate the artifact, _unchanged_visible to re-check permissions and consistency, workspace_tx for the database check, and mint_artifact_token to create the download link.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 4 external calls (__init__, now, mint_artifact_token, workspace_tx).


##### `ArtifactObjects.apply`  (lines 194–203)

```
async def apply(self, ctx: ToolContext, name: str, spec: ArtifactSpec, old: ArtifactSpec | None, *, expected_generation: UUID | None) -> None
```

**Purpose**: Rejects attempts to create or update artifacts through the object API. Artifacts must come from sharing a file, not from directly writing an object spec.

**Data flow**: It receives the usual apply inputs: context, name, desired spec, old spec, and optional generation. It does not use them to change storage. Instead, it raises an error telling the caller that share_file is the supported way to produce artifacts.

**Call relations**: The object system would call this for create or update verbs, but this artifact kind deliberately refuses those paths. It hands off only to VerbNotSupported to produce the clear failure.

*Call graph*: 1 external calls (__init__).


##### `ArtifactObjects.delete`  (lines 205–229)

```
async def delete(self, ctx: ToolContext, name: str, *, expected_generation: UUID | None) -> None
```

**Purpose**: Deletes an artifact completely, including every stored version and its blob bytes. This makes old download links stop working because the underlying stored file is gone.

**Data flow**: It receives the artifact name, finds all versions with _find, and fails if none exist. Inside a database transaction, it locks and re-checks that the latest version is still the visible object, deletes all matching shared_artifact rows for the current workspace, and verifies the number deleted matches the number expected. After the database rows are gone, it deletes each corresponding blob from blob storage.

**Call relations**: The object system calls this for the delete verb. It uses _find to resolve the artifact name, _unchanged_visible to guard against permission or visibility changes during deletion, workspace_tx and SQLAlchemy delete for the database work, and then calls the blob store to remove the actual bytes.

*Call graph*: calls 2 internal fn (_find, _unchanged_visible); 3 external calls (delete, workspace_tx, ws_current).


##### `ArtifactObjects._unchanged_visible`  (lines 231–238)

```
def _unchanged_visible(self, ctx: ToolContext, latest: sa.Row) -> sa.Select
```

**Purpose**: Builds the database check that proves a latest artifact share still belongs to the current workspace, selected agent, and readable audience. It is a safety gate before copying or deleting.

**Data flow**: It takes the current context and the latest share row. It creates a SQL select query that looks for the share’s conversation with matching workspace ID, conversation ID, selected agent ID, audience, and the context’s readable subjects. The output is the query object; callers execute it themselves.

**Call relations**: ArtifactObjects.status and ArtifactObjects.delete call this just before doing side effects. Status uses it before writing bytes into the workspace, and delete uses it with a row lock before removing database rows and blobs.

*Call graph*: called by 2 (delete, status); 3 external calls (select, object_agent_id, ws_current).


##### `ArtifactObjects._find`  (lines 240–242)

```
async def _find(self, ctx: ToolContext, name: str) -> tuple[sa.Row, ...] | None
```

**Purpose**: Looks up one artifact by its user-facing object name. It is the shared resolver used by get, status, and delete.

**Data flow**: It receives a context and name. It asks _groups for all visible named artifact groups, filters for the requested name, and returns that group’s ordered share rows if found. If nothing matches, it returns null.

**Call relations**: ArtifactObjects.get, ArtifactObjects.status, and ArtifactObjects.delete all call this when they need to turn a name from the user or object API into the actual stored versions. It depends on _groups for the database read, grouping, naming, and sorting work.

*Call graph*: calls 1 internal fn (_groups); called by 3 (delete, get, status).


##### `ArtifactObjects._groups`  (lines 244–285)

```
async def _groups(self, ctx: ToolContext) -> Sequence[tuple[str, tuple[sa.Row, ...]]]
```

**Purpose**: Collects all visible shared artifacts and groups their versions into object-shaped bundles. This is the main bridge from raw database rows to artifact objects.

**Data flow**: It opens a workspace database transaction, selects shared artifact rows joined to their turns and conversations, and filters them to the current workspace, selected agent, and readable audiences. It groups rows by conversation ID and filename, asks artifact_object_names to assign public object names, sorts each group so the newest version comes first, and returns the groups sorted by name.

**Call relations**: ArtifactObjects.list calls this to show all artifacts, and _find calls it to locate one artifact by name. It uses workspace_tx for database access, object_agent_id and ws_current for scoping, SQLAlchemy select to build the query, and artifact_object_names to make the names users see.

*Call graph*: calls 1 internal fn (artifact_object_names); called by 2 (_find, list); 4 external calls (select, workspace_tx, object_agent_id, ws_current).


##### `_summary`  (lines 288–294)

```
def _summary(shares: tuple[sa.Row, ...]) -> str
```

**Purpose**: Creates a short one-line description for an artifact listing. It gives enough information to recognize the latest file without opening it.

**Data flow**: It receives the ordered versions of one artifact, reads the latest row, and formats the filename, media type, byte size, share date, and version count when there is more than one version. It trims the text to the configured maximum length and returns it.

**Call relations**: ArtifactObjects.list calls this while building each ObjectRow. It is the presentation helper that turns a group of stored versions into readable list text.

*Call graph*: called by 1 (list).


### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file exists so shared files can be downloaded safely without exposing the whole file store to the public. Think of it like a pickup counter: having the right claim ticket lets you receive one specific package, but without that ticket you get nothing.

The route is always mounted in the core app, so download links keep working no matter which user-facing surface created them, such as a web chat surface or a Slack integration. When a request comes in, the code reads two app-wide pieces of state: the blob store, which is where file bytes live, and the artifact token secret, which is the private key used to prove that a download token was genuinely created by this deployment.

The route first rejects requests with no token. Then it verifies the token, including checking it against the current time so expired or invalid tokens fail. If the token is valid, it names the exact blob to fetch. The code checks that the blob still exists before starting the response, so a missing file becomes a clear 404 error.

For a valid file, it returns a streaming download. Streaming means the server sends the file in chunks instead of loading the whole thing into memory, which matters for large files and many simultaneous downloads. If the token includes a filename, the response also asks the browser to download using that name.

#### Function details

##### `download`  (lines 26–53)

```
async def download(request: Request, token: str='') -> StreamingResponse
```

**Purpose**: This is the download endpoint for artifact links. It checks that the caller has a real, unexpired token, confirms the requested stored file exists, and then streams the file back as a download.

**Data flow**: A web request comes in with an optional token in the query string. The function reads the blob store and artifact-token secret from the FastAPI app state, rejects missing or bad tokens, turns a good token into claims that include the blob key and optional filename, checks that the blob exists, builds any download filename header, and returns a streaming response that sends the blob bytes in chunks. It does not load the full file into memory.

**Call relations**: FastAPI calls this function when a request reaches the artifact download path. During the request, it asks datetime.datetime.now for the current time, passes the token and secret to ufo.artifact_token.verify_artifact_token, uses urllib.parse.quote when it needs a browser-safe filename, raises fastapi.HTTPException for missing, invalid, or missing-file cases, and finally hands the blob stream to fastapi.responses.StreamingResponse so the web server can send the file.

*Call graph*: 5 external calls (now, HTTPException, StreamingResponse, verify_artifact_token, quote).

## 📊 State Registers Touched

- `reg-identity-auth-state` — The current proof of who is calling, such as member identity, cookies, bearer tokens, operator sessions, and signed access tokens.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-live-update-hub` — The short-lived stream of progress updates, tool activity, costs, final answers, and Redis fan-out frames for live viewers.
- `reg-artifact-blob-store` — The shared file and blob store for generated artifacts, copied outputs, download records, and signed access links.
- `reg-surface-delivery-state` — The surface installation keys, outbound delivery/writeback queue, and acknowledgement state used to send completed replies back to external surfaces.
- `reg-todo-checklist-state` — The durable visible todo/checklist state that agents update during long-running work and reuse across turns.
- `reg-user-question-state` — The pending human-question/answer state created when an agent asks the user for information and later resumed when the surface delivers a reply.
- `reg-page-alert-subscription-state` — The saved routing/subscription state that decides which conversations or agents should be alerted when synced source pages change.
- `reg-redis-coordination-state` — The live Redis coordination backend state, including clients, stream/consumer metadata, and cross-process fan-out or coordination wiring.
