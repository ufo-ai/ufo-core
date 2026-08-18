# Live frame and terminal transport  `stage-7.1`

This stage is the live delivery system for a running turn. After a turn is accepted, the model and tools produce many small updates before the final answer is ready: partial text, tool progress, terminal output, cost changes, parked states, and completion messages. The hub code is the local message room for these updates. It lets user interfaces watch the turn as it happens and reconnect from a saved position, called a cursor, instead of losing their place.

The tail helper makes this reliable for late or unlucky listeners. It follows the live stream, but also checks the database so a client still learns that a turn finished or was parked even if the last live message was missed.

When the system runs across several server pods, the Redis hub version acts like a shared pipe between them, carrying the same live frame updates through Redis Streams. The Redis terminal transport does a similar job for terminal sessions: it lets a terminal connection and the worker that needs it find each other across pods, sending small control messages through Redis and larger byte data through blob storage.

## Files in this stage

### Live turn frame streams
Streaming clients receive reliable turn updates through the surface tailer, the in-process hub, and the Redis-backed frame hub.

### `core/src/ufo/surfaces/hub_tail.py`

`io_transport` · `request handling`

A “turn” appears to be a unit of work that produces live frames, such as tokens or status updates, while it runs. The tricky part is that a listener may arrive after the turn already started, or even after it ended somewhere else. This file solves that by listening to two sources at the same time: the live hub, which is fast, and the durable database state, which is authoritative.

Think of it like watching a train on a live camera while also checking the station board. The camera gives moment-by-moment movement, but the station board confirms whether the train has arrived or been held. If the live stream drops a frame, that may affect how smoothly the display updates, but it should not hide the final truth.

The main function, `tail_frames`, starts a hub subscription and a repeated status poll. It first checks whether the turn is already done or parked. If not, it yields frames as they arrive. The stream ends when it sees either a terminal frame, meaning the turn is finished, or a parked frame, meaning it is paused because of a limit such as a spending cap or revoked seat access.

`HubTailer` wraps this behavior in a small object that other surface code can receive without needing to know about the hub directly.

#### Function details

##### `tail_frames`  (lines 28–59)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='') -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: This is the main streaming helper. It yields live frames for one turn and makes sure the caller sees a proper ending, whether that ending comes from the live hub or from the stored database state.

**Data flow**: It receives a hub, a turn ID, and optionally the last cursor the caller saw before reconnecting. It checks whether the hub can safely resume from that cursor; if not, it starts from the retained beginning. It then starts two background tasks: one that reads live frames from the hub, and one that repeatedly checks the database for a finished or parked state. Frames from both paths go into one queue. The function yields each queued frame to the caller until it sees a terminal or parked frame, then stops. When the caller is done or the generator closes, it cancels the background work.

**Call relations**: A surface caller reaches this through `HubTailer.tail`. Inside, it asks the hub whether a reconnect cursor is still covered, starts `_pump` for live hub messages, starts `_poll_status` for durable status checks, and also calls `turn_status_frame` immediately in case the turn was already over before listening began.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, turn_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 62–69)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background task copies live frames from the hub subscription into the shared queue used by `tail_frames`. It is the fast path for updates while a turn is actively producing output.

**Data flow**: It receives the hub, the turn ID, the cursor to start from, and the queue where frames should be placed. It subscribes to the hub for that turn and, for every cursor-and-frame pair it receives, puts that pair into the queue. If the subscription fails, it logs the problem instead of crashing the whole tailing flow.

**Call relations**: `tail_frames` starts `_pump` when a listener begins tailing a turn. `_pump` relies on `Hub.subscribe` for the live stream and feeds results back to `tail_frames` through the queue, where they are mixed with status-poll results.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 72–81)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background task is the safety net. It repeatedly checks the database so the stream can still end correctly even if the live hub misses or cannot deliver the final frame.

**Data flow**: It receives a turn ID and the same queue used for outgoing frames. Once per polling interval, it asks `turn_status_frame` whether the turn is finished or parked. If there is no ending yet, it waits and tries again. If it finds a terminal or parked frame, it puts that frame into the queue with an empty cursor and then exits. If something goes wrong, it logs the error.

**Call relations**: `tail_frames` starts `_poll_status` alongside `_pump`. While `_pump` listens to the fast live source, `_poll_status` checks the durable source by calling `turn_status_frame`, giving `tail_frames` a reliable way to know when to stop.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `turn_status_frame`  (lines 84–109)

```
async def turn_status_frame(turn_id: UUID) -> LiveFrame | None
```

**Purpose**: This function translates the stored database state of a turn into the live frame that should end the stream. It returns a terminal frame when the turn is complete, a parked frame when the turn is paused, or nothing when the turn is still active.

**Data flow**: It receives a turn ID. It opens a workspace database transaction and reads the turn’s status, stored terminal data, workspace, speaker, behalf-of member, and admission source. If the turn is missing, still queued, or still running, it returns nothing. If the turn has stored terminal data, it validates that data and wraps it as a terminal live frame. If the turn is parked, it checks whether the relevant member is still admitted by the seat system. If seat access was revoked, it returns a parked frame with the revoked-seat message; otherwise it returns a general message saying the turn is parked because it is over a spending cap.

**Call relations**: `tail_frames` calls this once before streaming so late listeners can immediately get the already-known ending. `_poll_status` calls it repeatedly while streaming, so the live tail eventually learns about a terminal or parked state from the database even if the hub does not supply it.

*Call graph*: called by 2 (_poll_status, tail_frames); 7 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx, gate_member).


##### `HubTailer.tail`  (lines 121–124)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: This method exposes turn tailing as a clean, closeable stream for surface code. It hides the details of hub subscriptions, polling, and cleanup behind a simple call.

**Data flow**: It receives a turn ID and an optional reconnect cursor. It creates the `tail_frames` async generator for this instance’s hub and wraps it in an async closing context, so leaving the caller’s block closes the generator and triggers cleanup of its background tasks.

**Call relations**: Surface code calls `HubTailer.tail` when it wants to stream one turn. This method delegates the real work to `tail_frames` and uses `aclosing` so callers get dependable teardown without needing to know about the pump or polling tasks.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


### `core/src/ufo/hub.py`

`io_transport` · `cross-cutting during live turn streaming and reconnects`

A running agent turn produces many small updates before it finishes. This file is the in-process “hub” that fans those updates out to any live viewers, like a switchboard sending the same announcement to every listener. The updates are called live frames: they include text chunks, terminal frames, cost ticks, tool calls, skill loads, absorbed message notices, delivered replies, and subagent progress.

The important promise is that publishing never waits for a slow viewer. Each subscriber has a small queue. If that queue is full, the oldest waiting frame is dropped for that subscriber, so the agent can keep running. At the same time, each turn keeps a bounded replay buffer, like a short rewind tape. A client reconnects with its last cursor, and the hub replays frames after that cursor before sending new live frames.

The file defines a Hub protocol, which is the shared interface other code expects, and InProcessHub, the local implementation using memory, a thread lock, and asyncio queues. The lock matters because publishers and subscribers may run on different event-loop threads. Finished streams are cleaned up to avoid keeping memory forever, while parked streams keep enough state to resume safely under the same turn id.

#### Function details

##### `Hub.publish`  (lines 140–140)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This is the interface promise for adding one live frame to a turn’s stream. Code uses it when something worth showing happens, such as a failure terminal frame from the queue layer.

**Data flow**: It receives a turn id and a frame to send. An implementation appends that frame to the turn’s stream, gives it a cursor, sends it to subscribers, and returns the cursor as a string.

**Call relations**: The queue failure path calls this through the Hub interface when it needs to publish a terminal update. The actual work is done by a concrete hub such as InProcessHub.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 142–144)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the interface promise for watching a turn’s live stream. A caller may provide the last cursor it saw so it can receive only newer frames.

**Data flow**: It receives a turn id and an optional cursor. An implementation first yields saved frames newer than that cursor, then keeps yielding new frames as they arrive.

**Call relations**: The surface tailing code calls this through the Hub interface while pumping live updates to a user-facing surface. A concrete hub supplies the replay and live streaming behavior.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 146–146)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for checking whether a cursor can still be replayed without a gap. A reconnecting client uses this to decide whether it can resume smoothly or must redraw from durable stored state.

**Data flow**: It receives a turn id and a cursor. An implementation checks its retained replay data and returns true if the cursor is still inside the kept range, otherwise false.

**Call relations**: The tailing layer asks this before resuming from a cursor. A concrete hub, such as InProcessHub, answers based on its replay buffer.

*Call graph*: called by 1 (tail_frames).


##### `_offer`  (lines 149–152)

```
def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber’s queue without ever blocking the publisher. If the subscriber is already backed up, it drops that subscriber’s oldest waiting frame to make room.

**Data flow**: It receives an asyncio queue and a cursor-frame pair. If the queue is full, it removes one old item, then immediately inserts the new item; it returns nothing and only changes the queue.

**Call relations**: InProcessHub.publish schedules this helper on each subscriber’s event loop. That lets publishing cross thread boundaries safely while preserving the rule that a slow subscriber must not slow down the producer.


##### `InProcessHub._stream`  (lines 199–209)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This private helper gets the stored live state for one turn, creating it if this is the first time the turn is being streamed. It centralizes how replay buffers and cursor counters are started.

**Data flow**: It takes a turn id and reads the hub’s internal dictionaries. If a stream already exists, it returns it; otherwise it creates a new _TurnStream with a bounded deque replay buffer, no subscribers, and a sequence number continuing from any saved mark.

**Call relations**: InProcessHub.publish calls this when it needs a place to append a new frame. InProcessHub.subscribe calls it when a viewer starts watching and needs both replay history and a live queue registered.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 211–230)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This adds one live frame to a turn, assigns it the next cursor, stores it for replay, and sends it to current subscribers. It is designed so the publisher never waits for slow viewers.

**Data flow**: It receives a turn id and a live frame. Under a lock, it finds or creates the turn stream, advances the cursor number, saves the frame in the replay ring, copies the current subscriber list, and marks terminal or parked streams as ended when appropriate. After releasing the lock, it schedules delivery of the frame to each subscriber queue and returns the new cursor.

**Call relations**: This is the concrete implementation behind Hub.publish. It relies on InProcessHub._stream to get per-turn state, and it hands each delivery to _offer on the subscriber’s own event loop so cross-thread streaming is safe.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 232–259)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This lets a client watch a turn from a given cursor onward. It first replays saved frames the client missed, then waits for new frames and yields them as they arrive.

**Data flow**: It receives a turn id and optional cursor. It creates a bounded queue tied to the current asyncio event loop, registers that queue as a subscriber under the hub lock, snapshots replay frames newer than the cursor, yields those replay frames, then repeatedly yields frames taken from the live queue. When the caller stops listening, it unregisters the queue and may delete finished stream state.

**Call relations**: This is the concrete implementation behind Hub.subscribe. It uses InProcessHub._stream to access or create the turn’s state, and it is consumed by the surface pumping code that tails live frames for a client.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 261–269)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This tells a reconnecting client whether the hub still has enough replay history to continue from a cursor without missing frames. If not, the client should fall back to redrawing from durable stored data.

**Data flow**: It receives a turn id and cursor string. If the cursor is empty, or there is no retained stream or replay buffer, it returns false. Otherwise it compares the cursor with the earliest cursor still in the buffer and returns whether replay can cover it.

**Call relations**: This is the concrete implementation behind Hub.covers. The tailing code calls it before deciding whether a cursor-based resume is safe.


### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `live turn streaming and reconnect handling`

A turn can produce many small live updates before its final answer is saved. This file sends those updates through Redis, a fast in-memory service, instead of storing every tiny update in the main database. That matters because live frames are useful for smooth display, but they are not the source of truth; if one is lost, the final durable answer can still be redrawn later.

The file uses one Redis Stream per turn. A Redis Stream is like an append-only notebook: publishers add entries at the end, and subscribers read forward from a bookmark called a cursor. `RedisStreamHub.publish` turns a live frame into a small JSON record, appends it to the turn’s stream, and returns the Redis entry id as the new cursor. `RedisStreamHub.subscribe` starts from a cursor, replays any retained entries after it, then waits for new ones. This lets a browser or surface reconnect and continue without seeing duplicates or missing frames, as long as Redis has not trimmed the old entries away.

A key detail is that async Redis clients are tied to the event loop that created them. This project can publish and subscribe from different event loops in the same process, so the hub keeps a separate Redis client per loop. That avoids subtle cross-thread async failures. Streams are capped and expire after idle time, so Redis is used as a temporary live buffer, not long-term storage.

#### Function details

##### `frame_payload`  (lines 59–62)

```
def frame_payload(frame: LiveFrame) -> dict[str, object]
```

**Purpose**: Converts one live frame into a simple wire-friendly shape: a kind label plus the frame’s data. This is what lets Redis carry many different frame types in one common format.

**Data flow**: It receives a `LiveFrame`, looks up which kind of frame it is, asks the frame to turn its fields into JSON-safe data, and returns a dictionary with `kind` and `data`. Nothing is stored here; it only prepares the frame for later JSON encoding.

**Call relations**: When `RedisStreamHub.publish` is about to append a frame to Redis, it calls this helper first. The returned payload is then passed to JSON encoding so Redis receives a compact string rather than a Python object.

*Call graph*: called by 1 (publish); 1 external calls (model_dump).


##### `frame_from_payload`  (lines 65–69)

```
def frame_from_payload(payload: dict[str, object]) -> LiveFrame
```

**Purpose**: Rebuilds the original live frame object from the payload that was read out of Redis. It also rejects unknown frame kind labels so bad or incompatible stream data fails clearly.

**Data flow**: It receives a dictionary containing a `kind` and `data`. It checks that the kind is a known live-frame type, validates the data against that type’s model, and returns the reconstructed `LiveFrame`. If the kind is missing or unknown, it raises an error instead of guessing.

**Call relations**: After `RedisStreamHub.subscribe` reads and JSON-decodes a Redis stream entry, it calls this helper to turn the plain payload back into the frame object that callers expect to receive.

*Call graph*: called by 1 (subscribe); 1 external calls (cast).


##### `_stream_id`  (lines 72–74)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Turns a Redis stream entry id into two numbers that can be compared safely. Redis ids look like `milliseconds-sequence`, so this helper makes chronological comparison straightforward.

**Data flow**: It receives an entry id string, splits it around the dash, converts the time part and sequence part to integers, and returns them as a pair. If the sequence part is absent, it treats it as zero.

**Call relations**: `RedisStreamHub.covers` uses this helper when deciding whether a saved cursor is still within the part of the stream Redis has retained. Comparing parsed numeric ids avoids mistakes that can happen with plain string comparison.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 77–86)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual Redis stream entries from an `xread` response. It also makes sure the response has the expected shape, so the code does not silently read the wrong thing.

**Data flow**: It receives a batch returned by Redis `xread`. If the batch is empty, it returns an empty list. If the batch is not the expected list form, it raises an error. Otherwise it pulls out and returns the entries for the stream.

**Call relations**: `RedisStreamHub.subscribe` calls this helper after each Redis read. This keeps the subscribe loop focused on cursor progress and frame reconstruction, while this helper deals with the Redis response packaging.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 102–108)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the currently running async event loop, creating it the first time that loop needs one. This prevents one loop from reusing a client that belongs to another loop.

**Data flow**: It reads the current async event loop, checks the hub’s internal client dictionary, and either returns the existing client for that loop or creates a new one from the Redis URL and stores it. The main visible output is a Redis client ready for reads and writes.

**Call relations**: `publish`, `subscribe`, and `covers` all call this before talking to Redis. It is the shared doorway to Redis, and its per-loop behavior is important because publishing work and browser-facing subscription work may run on different event loops.

*Call graph*: called by 3 (covers, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 110–111)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name for a particular turn. This gives every turn its own isolated live-update stream.

**Data flow**: It receives a turn id and combines it with the fixed stream prefix to produce a Redis key string. It does not contact Redis or change any state.

**Call relations**: `publish`, `subscribe`, and `covers` use this helper whenever they need to address the stream for a turn. It keeps the naming rule in one place so all operations point at the same Redis key.

*Call graph*: called by 3 (covers, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 113–120)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: Appends a live frame to the Redis stream for a turn and returns the new stream cursor. Callers use it when a running turn has something new to show, such as text, tool activity, or a final terminal update.

**Data flow**: It receives a turn id and a live frame. It builds the stream name, converts the frame into a JSON string, then sends Redis two commands in a pipeline: add the frame to the stream and refresh the stream’s expiration time. Redis returns the new entry id, and this method returns that id as a string cursor.

**Call relations**: This is the publishing side of the hub. It relies on `_stream` for the Redis key, `_client` for the correct loop-local Redis connection, and `frame_payload` plus JSON encoding for the stored form. Subscribers later use the returned Redis entry ids as bookmarks.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 122–146)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Streams live frames for one turn, starting after a given cursor and then waiting for new frames. It is what a UI-facing surface can use to replay recent updates and keep following the turn live.

**Data flow**: It receives a turn id and an optional cursor. It starts reading after that cursor, or from the beginning if none is given. Each loop first tries a non-blocking Redis read, then a blocking wait if nothing is ready. For each entry found, it updates the cursor, decodes the stored JSON, rebuilds the live frame, and yields the pair of new cursor and frame. Timeout while waiting is treated as normal idleness, so it simply tries again.

**Call relations**: This is the receiving side of the hub. It uses `_stream` to choose the turn’s Redis stream, `_client` for Redis access, `_stream_entries` to unpack Redis responses, and `frame_from_payload` to reconstruct frames. It pairs with `publish`: anything published to a turn’s stream can later be replayed or tailed here.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 148–154)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether Redis still retains enough stream history to resume from a cursor without a gap. If it returns false, the caller should assume the live stream has lost old entries and redraw from a more durable source instead.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, it immediately returns false. Otherwise it asks Redis for the first retained entry in that turn’s stream. If there is no retained entry, it returns false. If there is one, it parses both the first retained id and the cursor id and returns whether the first retained id is at or before the cursor.

**Call relations**: Reconnect logic can call this before using `subscribe` with an old cursor. It uses `_stream` to find the turn’s Redis key, `_client` to read Redis, and `_stream_id` to compare entry ids in their natural numeric order.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


### Redis terminal rendezvous
Terminal connections and remote workers exchange control messages and byte payloads across pods through Redis Streams and blob storage.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `cross-cutting terminal request handling`

In a single-process setup, a terminal request can be handed directly to the code holding the user’s terminal connection. In a shared fleet, that breaks: the open connection may live on one pod while the workflow asking the terminal to run a command lives on another. This file acts like a message desk between them. A connected terminal publishes “I am here” in Redis with a short-lived heartbeat. When a workflow wants to run an operation, it first waits for that binding, takes a per-conversation lock so only one operation runs at a time, stores any large input bytes in the blob store, then adds an operation message to a Redis stream. The pod holding the terminal reads the stream, claims one operation so reconnects do not run it twice, and later posts a reply to a separate reply stream. Small replies are stored directly in Redis; large replies go through the blob store. The code is careful about time limits: every wait has a deadline, Redis keys expire, and cleanup is best-effort. Without this file, terminal operations in a multi-pod deployment could be lost, duplicated, routed to the wrong member, or leave workflows stuck forever waiting for an answer.

#### Function details

##### `_text`  (lines 54–57)

```
def _text(value: bytes | str) -> str
```

**Purpose**: This helper turns a Redis field into ordinary Python text. Redis values may arrive as bytes or as strings, and the rest of the file wants one consistent shape.

**Data flow**: It receives one Redis value. If it is already text, it leaves it alone; if it is bytes, it decodes it into text. The result is always a string.

**Call relations**: It is used wherever Redis data is read back, such as decoding operation fields, replies, binding records, and gate metadata. It keeps those callers from repeating the same bytes-versus-text check.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 60–65)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: This converts the flat field list returned by the Redis Lua script into a normal field dictionary. It makes the script output easier for Python code to read.

**Data flow**: It receives a list shaped like field, value, field, value. It checks that the value really is a list, converts each item to text with _text, and groups neighboring items into key-value pairs. It returns a dictionary of stream fields.

**Call relations**: RedisTerminals.next_op uses this after the Lua claim script finds an operation. The result is then handed to RedisTerminals._decode_op to build a TerminalOp object.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 68–71)

```
def _bind_payload(cwd: str, member_id: UUID | None) -> str
```

**Purpose**: This builds the small JSON record that says where a terminal is currently rooted and which member it belongs to. Redis stores this record as the terminal’s public binding.

**Data flow**: It receives a current working directory and an optional member UUID. It turns them into a JSON string, using the member’s hex form when one exists. The returned string is ready to store in Redis.

**Call relations**: RedisTerminals._heartbeat uses it to publish the live connection binding, and RedisTerminals._run_op uses it to pin the same binding while an operation is in flight.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 145–154)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: This unwraps the Redis XREAD response format used by this transport. It also fails loudly if Redis returns an unexpected shape, which avoids silently misreading stream data.

**Data flow**: It receives the batch returned by Redis XREAD. If there is no batch, it returns an empty list. Otherwise it checks the response is the expected list form and extracts the stream entries from it.

**Call relations**: RedisTerminals._await_reply calls this after reading the reply stream. That keeps reply waiting focused on the reply itself, not on Redis response-format details.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 181–198)

```
def _client(self) -> Redis
```

**Purpose**: This returns the Redis client for the current asynchronous event loop. An event loop is the runner that schedules async work, and Redis clients must stay tied to the loop that created them.

**Data flow**: It looks up the currently running loop, checks whether this RedisTerminals instance already has a Redis client for it, and creates one if needed with bounded socket timeouts. It returns the loop-local Redis client and remembers it for later calls.

**Call relations**: Nearly every Redis operation in this class goes through this method. It is the shared doorway to Redis for sending operations, reading bindings, waiting for replies, delivering replies, and cleanup.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 200–201)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: This creates the Redis key name for a conversation’s live terminal binding. The key is like the address label for “which terminal is connected right now.”

**Data flow**: It receives a conversation UUID and formats it into the binding key string. Nothing external is changed.

**Call relations**: RedisTerminals._heartbeat writes this key while a terminal is connected, and RedisTerminals._read_binding checks it when a workflow wants to find the terminal.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 203–204)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: This creates the Redis key name for a binding pinned during an active operation. The pin lets other accessors know the terminal is still logically busy even after the held stream has returned to the client.

**Data flow**: It receives a conversation UUID and formats it into the in-flight binding key string. It returns that key name.

**Call relations**: RedisTerminals._run_op writes this key while an operation is active, RedisTerminals._read_binding reads it if the live binding is absent, and RedisTerminals._clear_op deletes it during cleanup.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 206–207)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: This creates the Redis stream name where terminal operations for one conversation are queued. A stream is Redis’s ordered message log.

**Data flow**: It receives a conversation UUID and returns the matching operation stream key. It does not touch Redis itself.

**Call relations**: RedisTerminals._run_op appends new operations to this stream, RedisTerminals.next_op reads and claims work from it, and RedisTerminals._clear_op removes completed entries.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 209–210)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: This creates the Redis stream name where one operation’s reply will be posted. Each operation gets its own reply stream so the sender can wait for exactly its own answer.

**Data flow**: It receives an operation ID and returns the reply stream key string. No data is read or written.

**Call relations**: RedisTerminals._await_reply reads this stream, RedisTerminals._deliver_reply writes to it, and RedisTerminals._clear_op deletes it when the operation is done.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 212–213)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: This creates the Redis key used as the per-conversation lock. The lock prevents two terminal operations from being sent to the same conversation at the same time.

**Data flow**: It receives a conversation UUID and returns the lock key string. It does not acquire the lock itself.

**Call relations**: RedisTerminals.send uses this key when asking Redis for a lock before it starts the actual operation.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 215–216)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: This creates the Redis key used as a delivery marker for an operation. The marker says “this operation has already been handed to a terminal stream,” which helps prevent duplicate execution.

**Data flow**: It receives an operation ID and returns the marker key string. The function only builds the name.

**Call relations**: RedisTerminals._clear_op uses this key during cleanup. The claiming logic in RedisTerminals.next_op uses the same key pattern through the Lua script’s configured prefix.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 218–219)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: This creates the Redis key for metadata about an operation, such as its conversation and member. That metadata is used as a safety check before serving staged input or accepting a reply.

**Data flow**: It receives an operation ID and returns the metadata key string. It does not read the metadata.

**Call relations**: RedisTerminals._run_op writes this metadata, RedisTerminals.staged and RedisTerminals._deliver_reply read it for gate checks, and RedisTerminals._clear_op removes it later.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 221–222)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: This creates the blob-store key for an operation’s input body. The blob store is used for larger byte payloads that should not ride directly in Redis stream fields.

**Data flow**: It receives an operation ID and returns the blob key for the staged input body. It only constructs the name.

**Call relations**: RedisTerminals._run_op writes the body under this key, RedisTerminals.staged reads it when serving the terminal-side request, and RedisTerminals._clear_op deletes it afterward.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 224–225)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: This creates the blob-store key for a large operation reply. Small replies go in Redis, but larger replies are stored separately and referenced from the reply stream.

**Data flow**: It receives an operation ID and returns the blob key for the reply bytes. It does not access the blob store by itself.

**Call relations**: RedisTerminals._deliver_reply writes large replies here, RedisTerminals._decode_reply reads them, and RedisTerminals._clear_op deletes them during cleanup.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 227–240)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: This records that the current pod is holding a terminal connection for a conversation. It starts or shares a background heartbeat that keeps the Redis binding alive.

**Data flow**: It receives the conversation ID, current working directory, and optional member ID. Under a thread lock, it creates or reuses a local hold record, starts a heartbeat task if this is the first local connection, and increments the local connection count. It returns nothing.

**Call relations**: A held terminal stream calls this when it begins serving a conversation. It creates the heartbeat task RedisTerminals._heartbeat, which is what other pods later discover through Redis.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 242–251)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: This marks one local terminal connection as gone. When the last local connection for a conversation leaves, it stops the heartbeat instead of deleting the Redis binding immediately.

**Data flow**: It receives a conversation ID, looks up the local hold under a thread lock, decreases its connection count, and cancels the heartbeat task if no local connections remain. The Redis key is left to expire naturally.

**Call relations**: It is the counterpart to RedisTerminals.connect. By canceling only the heartbeat, it avoids deleting a binding that may already have been refreshed by a reconnecting pod.


##### `RedisTerminals._heartbeat`  (lines 253–265)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: This background task keeps a terminal binding fresh in Redis while this pod holds the connection. Think of it as periodically stamping “still here” on the conversation’s terminal record.

**Data flow**: It receives the conversation ID, directory, and member ID. It builds the JSON binding once, then repeatedly writes it to the binding key with a time-to-live and sleeps before refreshing again. If Redis has a transient error, it suppresses it and tries again on the next loop.

**Call relations**: RedisTerminals.connect starts this task. RedisTerminals.disconnect cancels it when the last local connection leaves, after which Redis eventually expires the binding on its own.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 267–276)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: This answers the terminal workspace held by this pod, if this pod is the one with the connection. It is a fast local lookup with no Redis trip.

**Data flow**: It receives a conversation ID, checks the local hold table under a lock, and returns a TerminalWorkspace with the saved directory and member ID if found. If this pod has no local hold, it returns null.

**Call relations**: This is useful for local reads on the serving pod. Cross-pod send paths do not rely on it; RedisTerminals.arrived and RedisTerminals._read_binding read Redis instead.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 278–291)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: This waits briefly for a terminal binding to appear for a conversation. It covers normal reconnect gaps, where the client may be between held streams for a moment.

**Data flow**: It receives a conversation ID and a grace period in seconds. It repeatedly asks RedisTerminals._read_binding for the current workspace until one appears or the deadline passes, sleeping between polls. It returns a TerminalWorkspace or null.

**Call relations**: RedisTerminals.send calls this before creating an operation. If it returns null, send reports that no terminal is connected.

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 293–307)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: This reads the terminal binding from Redis. It checks both the live heartbeat key and the in-flight pin so a long-running operation still counts as occupying the terminal.

**Data flow**: It receives a conversation ID and uses the Redis client to read the live binding key. If that is absent, it reads the in-flight binding key. If neither exists, it returns null; otherwise it parses the JSON and returns a TerminalWorkspace.

**Call relations**: RedisTerminals.arrived uses this as its polling step. The keys it reads are written by RedisTerminals._heartbeat and RedisTerminals._run_op.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 309–365)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: This is the main sender-side operation: it asks a connected terminal to do work and waits for the reply. It turns missing terminals, Redis trouble, and timeouts into TerminalGone errors that callers can understand.

**Data flow**: It receives a conversation ID, operation details, timeout, and optional body bytes. It waits for a binding, creates a TerminalOp, acquires the conversation lock in Redis, and then runs the operation under a deadline. It returns the reply bytes or raises an error, and it releases the lock in a cleanup step.

**Call relations**: This is the high-level path used when a workflow needs terminal work done. It calls RedisTerminals.arrived to find the terminal, uses RedisTerminals._lock_key and RedisTerminals._client for the Redis lock, and delegates the actual posting and reply wait to RedisTerminals._run_op.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 5 external calls (__init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 367–413)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: This performs the actual Redis-and-blob-store work after the sender has the conversation lock. It publishes the operation, waits for its answer, and cleans up afterward.

**Data flow**: It receives the conversation, TerminalOp, optional body, bound workspace, and deadline. It writes operation metadata, pins the binding, stores input bytes if present, appends the operation to the Redis stream, sets stream expiry, then waits for the reply. Whether it succeeds or fails, it calls cleanup at the end.

**Call relations**: RedisTerminals.send calls this while holding the per-conversation lock. It hands the operation to terminal readers through the operation stream, waits through RedisTerminals._await_reply, and finishes with RedisTerminals._clear_op.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 415–423)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: This turns a TerminalOp object into Redis stream fields. It is the packing step before the operation is written to Redis.

**Data flow**: It receives a TerminalOp and returns a dictionary containing the operation ID, kind, timeout, name, argument, and parameters as stream-friendly values.

**Call relations**: RedisTerminals._run_op calls this just before adding an operation entry to the conversation’s operation stream.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 425–433)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: This turns Redis stream fields back into a TerminalOp object. It is the unpacking step used by the pod that will render or execute the terminal directive.

**Data flow**: It receives a field dictionary from Redis, converts values to text, parses the timeout as an integer, and fills missing optional fields with empty strings. It returns a TerminalOp.

**Call relations**: RedisTerminals.next_op calls this after claiming an operation from the stream. It mirrors RedisTerminals._op_fields on the receiving side.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 435–458)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: This waits for the reply to one operation, but only until its deadline. It repeatedly reads the operation’s reply stream and never blocks forever.

**Data flow**: It receives an operation ID, total deadline allowance, and the user-facing timeout. It computes how much time remains, performs bounded Redis XREAD calls, skips empty polls, and decodes the first reply entry it sees. It returns reply bytes or raises TerminalGone if the deadline or Redis fails.

**Call relations**: RedisTerminals._run_op calls this after posting an operation. It uses RedisTerminals._reply_stream to know where to listen, _stream_entries to unwrap Redis output, and RedisTerminals._decode_reply to interpret the reply.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 460–473)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: This interprets a reply record from Redis and returns the operation’s output bytes. It also turns terminal-side failures into TerminalOpFailed.

**Data flow**: It receives an operation ID and reply fields. If the fields contain a failure message, it raises TerminalOpFailed. If the fields point to a blob, it reads the large reply from the blob store; otherwise it base64-decodes the inline reply. The output is the reply bytes.

**Call relations**: RedisTerminals._await_reply calls this once a reply stream entry arrives. RedisTerminals._deliver_reply creates the reply records that this method understands.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 475–506)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: This is the terminal-side wait for the next operation to show to the connected client. It claims work atomically so reconnects or multiple held streams do not run the same operation twice.

**Data flow**: It receives a conversation ID and optionally an operation ID to skip. It runs a Redis Lua script that scans old operations, removes expired ones, skips excluded work, and marks one unclaimed operation as delivered. If one is found, it decodes and returns it; otherwise it blocks briefly for new stream entries and tries again.

**Call relations**: The pod holding the terminal calls this while waiting for work. It consumes entries written by RedisTerminals._run_op and uses RedisTerminals._decode_op to return a usable TerminalOp.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 508–523)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: This retrieves the input body staged for an in-flight operation, but only if the request matches the correct conversation and member. It prevents one conversation or member from reading another operation’s bytes.

**Data flow**: It receives a conversation ID, operation ID, and optional member ID. It reads the operation metadata from Redis, checks it with RedisTerminals._gate_ok, and if allowed fetches the body from the blob store. It returns the bytes or null if the gate fails, metadata is missing, or the blob cannot be read in time.

**Call relations**: The terminal-serving side uses this when it needs the body for an operation. The metadata and blob were written earlier by RedisTerminals._run_op.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 525–540)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: This accepts a terminal operation’s reply and schedules delivery to Redis without making the HTTP or stream handler wait for Redis. It reports success immediately because the original sender has its own timeout.

**Data flow**: It receives the conversation ID, operation ID, reply bytes, optional failure message, and optional member ID. It creates a background task to deliver the reply and returns true right away.

**Call relations**: A reply POST path calls this after the terminal client finishes an operation. It delegates to RedisTerminals._deliver_reply through RedisTerminals._spawn so the actual Redis write happens asynchronously.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 542–571)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: This writes an operation reply to Redis after checking that it belongs to the right conversation and member. It stores large replies in the blob store and small replies directly in the stream.

**Data flow**: It receives conversation and operation IDs, reply bytes, an optional failure string, and an optional member ID. It reads operation metadata, rejects missing or mismatched requests with a warning, then builds reply fields: failure text, a blob pointer for large bytes, or base64 text for small bytes. It appends those fields to the reply stream and sets an expiry.

**Call relations**: RedisTerminals.resolve schedules this in the background. RedisTerminals._await_reply later reads the stream entry it writes, and RedisTerminals._decode_reply understands the inline or blob format.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 573–584)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: This checks whether a staged-body or reply request is allowed for a particular operation. It is a small safety gate based on conversation and member identity.

**Data flow**: It receives raw metadata, a conversation ID, and an optional member ID. It parses the metadata JSON, verifies the conversation matches, then checks that a named member matches the member stored for the operation. It returns true only when the request is allowed.

**Call relations**: RedisTerminals.staged uses this before serving copied-in bytes, and RedisTerminals._deliver_reply uses it before accepting a reply. Both paths fail closed when metadata is missing or mismatched.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 586–589)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: This reports that this Redis-backed transport has no reliable local view of the currently running operation. In a cross-pod setup, the operation being awaited may live on another pod.

**Data flow**: It receives a conversation ID but does not read local or remote state. It always returns null.

**Call relations**: This keeps the transport interface compatible with local transports while being honest about what this distributed implementation can know.


##### `RedisTerminals._clear_op`  (lines 591–612)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: This performs best-effort cleanup after an operation finishes or times out. It removes Redis entries, marker keys, reply streams, and blob-store objects so old operation state does not pile up.

**Data flow**: It receives the conversation ID, operation ID, and optional Redis stream entry ID. It tries to delete the operation stream entry, metadata, delivery marker, in-flight binding, reply stream, input blob, and reply blob. Redis and blob deletion errors are suppressed or bounded by timeouts because expiration windows provide the backup cleanup path.

**Call relations**: RedisTerminals._run_op calls this in its final step. It is cleanup, not the main duplicate-execution protection; that protection comes from the claim marker used by RedisTerminals.next_op.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 614–621)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: This starts a background coroutine and keeps track of it so it is not lost while running. It also wraps it so failures get logged.

**Data flow**: It receives a coroutine and an event loop. It creates a task that runs RedisTerminals._logged, stores the task in a set, and arranges for the task to remove itself from the set when done.

**Call relations**: RedisTerminals.resolve uses this to schedule RedisTerminals._deliver_reply. It lets the reply route return immediately while still giving the delivery work a managed lifetime.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 623–627)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: This runs a background coroutine and logs any exception it raises. It prevents background delivery failures from disappearing silently.

**Data flow**: It receives a coroutine, awaits it, and if any exception escapes, writes a warning with the error text. It returns nothing.

**Call relations**: RedisTerminals._spawn wraps background tasks with this method. In this file, that mainly protects reply delivery scheduled by RedisTerminals.resolve.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).
