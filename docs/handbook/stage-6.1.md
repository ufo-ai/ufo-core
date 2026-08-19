# Live turn updates and terminal coordination  `stage-6.1`

This stage is shared behind-the-scenes support for live communication while a turn is running. A “turn” is one user request and the system’s answer process. Its job is to keep clients updated in real time, and to recover cleanly if a browser reconnects or if work is split across several server pods.

The in-memory hub is the fast local broadcaster. It sends live text, tool activity, cost changes, replies, and final status to any client watching the turn. It also keeps a short replay log, so a reconnecting client can resume from a saved cursor instead of starting over.

The hub tail adds a safety net. It listens to the live hub, but also checks the database more slowly to confirm whether the turn really finished or was parked. This prevents a client from missing the final state.

The Redis stream hub extends live updates across multiple server processes. Redis acts like a shared message lane for temporary frames. The Redis terminal layer does the same for terminal sessions, using streams for coordination and blob storage for larger chunks of data.

## Files in this stage

### Live turn streams
In-memory live turn streaming and tailing logic deliver updates to clients while protecting late or reconnecting viewers from missed final state.

### `core/src/ufo/surfaces/hub_tail.py`

`io_transport` · `request handling / live streaming`

A “turn” appears to be a unit of work or conversation that produces live frames over time. A client watching that turn needs updates as they happen, but live streams are fragile: the client may connect after the turn started, reconnect after missing something, or arrive after the turn already finished. This file solves that by using two paths at once, like watching both a live sports broadcast and the official scoreboard. The hub provides immediate live frames. The database provides the durable truth about whether the turn is already terminal, meaning finished, or parked, meaning paused because something such as a spend cap or seat permission blocks it.

The main generator, `tail_frames`, starts a hub subscription and a background poll of the stored turn state. Both feed one queue. The caller receives frames from that queue until a final frame appears. If the database already says the turn is done or parked, the generator returns that immediately. If the hub delivers the ending first, the stream stops there. If the hub misses it, the polling path eventually finds it. The cleanup is important: when the caller stops reading, the background tasks are cancelled so no hidden work keeps running.

#### Function details

##### `tail_frames`  (lines 28–59)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='') -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: This is the main streaming function. It yields live frames for one turn and stops only when the turn has finished or become parked, using both the live hub and the stored database state so late or reconnecting clients still get a correct ending.

**Data flow**: It receives a hub, a turn ID, and optionally the last cursor the caller saw. It checks whether the hub can resume from that cursor; if not, it starts from the beginning of what the hub still has. It then starts two background jobs: one copies hub messages into a queue, and one periodically checks the database for a final or parked state. Before waiting on the queue, it also checks the database once in case the turn already ended. It yields each frame to the caller and, when it sees a terminal or parked frame, it stops. When the generator closes, it cancels the background jobs.

**Call relations**: This function is the worker behind `HubTailer.tail`. It calls the hub to see whether a reconnect cursor is still usable, starts `_pump` for live hub messages, starts `_poll_status` for the durable fallback, and asks `turn_status_frame` for the official stored state. It hands frames back upward to whatever surface is sending them to the client.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, turn_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 62–69)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper copies live hub messages into the shared queue used by `tail_frames`. It is the fast path for updates that are still happening right now.

**Data flow**: It receives the hub, the turn ID, the cursor to start from, and the queue. It subscribes to the hub for that turn and puts each received cursor-and-frame pair into the queue. If the subscription fails, it records a log message instead of crashing the whole tailing flow.

**Call relations**: `tail_frames` starts this helper as a background task. `_pump` listens to `Hub.subscribe` and feeds results back to `tail_frames` through the queue. It does not decide when the stream is done; it simply passes along what the hub emits.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 72–81)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper is the safety net. It checks the database every so often to find out whether the turn has reached a final or parked state, even if the live hub does not deliver that information.

**Data flow**: It receives a turn ID and the shared queue. It waits for a fixed interval, asks `turn_status_frame` for the stored state, and repeats while the turn is still active. When it finds a terminal or parked frame, it puts that frame into the queue with an empty cursor and then stops. If something goes wrong, it writes a log entry.

**Call relations**: `tail_frames` starts this helper alongside `_pump`. While `_pump` listens to the live hub, `_poll_status` asks `turn_status_frame` for the durable database answer. Whichever path supplies the ending first causes `tail_frames` to finish the stream.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `turn_status_frame`  (lines 84–109)

```
async def turn_status_frame(turn_id: UUID) -> LiveFrame | None
```

**Purpose**: This function translates the stored database status of a turn into the stream frame that should end the client’s view. It returns a finished frame, a parked message, or nothing if the turn is still running or waiting.

**Data flow**: It receives a turn ID. It opens a workspace database transaction and reads the turn’s status, saved terminal data, workspace, speaker, behalf-of member, and admission information. If no turn is found, it returns nothing. If terminal data is present, it validates that stored data and wraps it as a terminal live frame. If the turn is not parked, it returns nothing. If the turn is parked, it checks whether the relevant member is still admitted by the seat rules; if not, it returns a parked frame with the seat-revoked message. Otherwise it returns a parked frame explaining that the turn is over a spend cap and can resume later.

**Call relations**: Both `tail_frames` and `_poll_status` call this function. `tail_frames` uses it for the immediate first check, so an already-finished turn can end without waiting. `_poll_status` uses it repeatedly as the durable backup while the live stream is running.

*Call graph*: called by 2 (_poll_status, tail_frames); 7 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx, gate_member).


##### `HubTailer.tail`  (lines 121–124)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: This method provides the public tailing interface bound to a specific hub. It gives callers a clean async context they can use to read one turn’s frames and automatically close the underlying generator afterward.

**Data flow**: It receives a turn ID and optional cursor, then calls `tail_frames` with the stored hub. It wraps that generator in a closing context so that leaving the caller’s block shuts down the stream and its background work.

**Call relations**: This is the small adapter that other surface code calls instead of importing the hub-tail details directly. It delegates the real streaming work to `tail_frames` and uses `aclosing` so cleanup happens when the caller is done.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


### `core/src/ufo/hub.py`

`io_transport` · `main loop and live request handling`

A “hub” here is like a small radio tower for each running turn. Code that produces live events publishes frames into the hub, and surfaces such as a CLI or web page subscribe to hear them. The frames can be text chunks, cost meter updates, tool-call notices, skill-load notices, delivered replies, subagent progress, or an ending frame such as a terminal result or a parked message.

The important promise is that publishing never waits for slow readers. Each subscriber has a bounded queue. If that queue fills, the oldest unread frame is dropped for that subscriber, rather than blocking the running turn. At the same time, the hub keeps a bounded ring buffer, which is a fixed-size recent-history list, for each active turn. Subscribers reconnect with a cursor, which is just the frame number they last saw, and the hub replays newer buffered frames before sending fresh ones.

The in-process implementation uses a thread lock because publishers and subscribers may run on different asyncio event loops. It carefully registers subscribers and snapshots replay data under the same lock, so replayed frames and live frames do not overlap or leave gaps. Finished streams are removed when possible to avoid keeping memory forever, while parked turns preserve cursor numbering because they can resume later under the same turn id.

#### Function details

##### `Hub.publish`  (lines 151–151)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This is the interface promise for sending one live frame into a turn’s stream. Callers use it when something visible happens during a turn, such as text arriving, a tool starting, or the turn ending.

**Data flow**: It receives a turn id and a live frame. An implementation records and broadcasts that frame, then returns a cursor string that identifies the frame’s position in that turn’s stream.

**Call relations**: This protocol method is the shape that concrete hubs must follow. The queue code that commits a failed terminal state calls it so even failure can be reported through the same live stream.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 153–155)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the interface promise for reading a turn’s live stream. A surface uses it to catch up from an optional cursor and then keep receiving new frames.

**Data flow**: It receives a turn id and, optionally, the last cursor the reader already saw. It produces an asynchronous stream of cursor-and-frame pairs: first any buffered frames after that cursor, then newly published frames.

**Call relations**: The hub tailing code calls this when it needs to pump live frames toward a surface. Concrete implementations, such as InProcessHub.subscribe, provide the actual replay and live delivery behavior.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 157–157)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for checking whether a cursor can still be resumed without a gap. A surface uses it to decide whether it can safely reconnect from its cursor or must fall back to a fuller redraw or durable poll.

**Data flow**: It receives a turn id and cursor. An implementation checks its retained replay history and returns true if that cursor is still covered, or false if the needed history is gone or the cursor is empty.

**Call relations**: The live tailing code calls this before relying on replay. It lets the rest of the system treat different hub backends the same way, whether in-process or shared across processes.

*Call graph*: called by 1 (tail_frames).


##### `_offer`  (lines 160–163)

```
def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None
```

**Purpose**: This helper puts a frame into a subscriber’s queue without ever blocking the publisher. If the queue is already full, it discards the oldest queued frame to make room.

**Data flow**: It receives a subscriber queue and one cursor-and-frame item. It checks whether the queue is full; if so, it removes one old item, then immediately inserts the new item.

**Call relations**: InProcessHub.publish schedules this helper on each subscriber’s event loop. That keeps cross-thread delivery safe while preserving the hub’s rule that a slow subscriber must not stall publishing.


##### `InProcessHub._stream`  (lines 210–220)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This internal helper finds the stored live-stream state for one turn, or creates it if it does not exist yet. It is the place where a turn gets its replay buffer, subscriber list, and cursor counter.

**Data flow**: It receives a turn id and reads the hub’s internal dictionary of active turn streams. If a stream is already present, it returns it; otherwise it creates a new stream with a fixed-size replay buffer and a cursor sequence continuing from any saved mark.

**Call relations**: InProcessHub.publish calls this when it needs somewhere to append a new frame. InProcessHub.subscribe calls it when a reader attaches, so there is a stream object to hold that subscriber and provide replay.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 222–241)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This sends one frame to all current subscribers of a turn and stores it in the turn’s replay buffer. It is designed so publishing is quick and does not wait for readers.

**Data flow**: It receives a turn id and frame. Under a lock, it gets or creates the turn stream, assigns the next cursor, stores the frame in the replay ring, and copies the current subscribers. After releasing the lock, it schedules delivery to each subscriber queue and returns the cursor. If the frame ends the stream, it marks or removes stored state as appropriate.

**Call relations**: This is the concrete implementation behind Hub.publish for the in-process backend. It uses InProcessHub._stream to access per-turn state and uses _offer indirectly on subscriber event loops so live delivery is safe even when publishers and subscribers run in different threads.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 243–270)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This lets a reader follow one turn’s live frames, starting with any buffered frames after its last cursor. It is the main path used by a CLI or web surface to show a running turn in real time.

**Data flow**: It receives a turn id and optional cursor. It creates a bounded queue for live frames, registers that queue as a subscriber, and snapshots all replay-buffer frames newer than the cursor. It yields the replay frames first, then waits on the queue and yields newly published frames until the reader stops. When the reader disconnects, it removes the subscriber and may clean up the stream.

**Call relations**: This is the concrete implementation behind Hub.subscribe. It is called by the surface tail pump, uses InProcessHub._stream to attach to the turn, and receives later frames because InProcessHub.publish fans them out to its registered queue.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 272–280)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still reaches far enough back for a given cursor. It helps a reconnecting surface know whether replay can be trusted to fill every gap.

**Data flow**: It receives a turn id and cursor string. If the cursor is empty, the stream is gone, or the replay buffer is empty, it returns false. Otherwise it compares the oldest retained cursor with the requested cursor and returns whether the requested point is still within the retained range.

**Call relations**: This is the concrete implementation behind Hub.covers. The tailing layer asks it before resuming from a cursor; its answer decides whether the client can continue from live replay or must recover state another way.


### Redis coordination backends
Redis Streams backends extend live answer and terminal coordination across multiple server pods.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `live request handling and reconnect streaming`

This file is a live message relay for a “turn,” meaning one running exchange or response. As an answer is being produced, small updates called frames are created: pieces of text, tool-call notices, cost updates, terminal markers, and similar events. Instead of storing these short-lived updates in the main database, this hub puts them into Redis Streams, which are ordered append-only message lists in Redis. Think of each turn as having its own scrolling receipt tape: publishers add new lines, and viewers read forward from the last line they saw.

The important reason this exists is scale. If one server process creates a live frame and another process is serving the user interface, Redis gives both processes a shared place to meet. Without this, live streaming would mostly work only inside one process, and reconnecting clients would have a harder time replaying recent updates.

The file also protects against a subtle async problem. Redis clients are tied to the event loop that created them; an event loop is the runner that schedules async work. This hub keeps a separate Redis client per event loop, so background workflow code and web-serving code do not accidentally share unsafe connection state.

Streams are trimmed and expire after idle time. That means old live frames can disappear, but that is acceptable because the final answer is stored elsewhere. If a reconnect cursor is too old, the caller can redraw from the durable turn state instead.

#### Function details

##### `frame_payload`  (lines 61–64)

```
def frame_payload(frame: LiveFrame) -> dict[str, object]
```

**Purpose**: Turns one live frame into a simple tagged dictionary that can be safely written to Redis as JSON. The tag says what kind of frame it is, so it can later be rebuilt as the same kind of object.

**Data flow**: It receives a live frame object. It looks up that frame’s type, adds a short kind name, and asks the frame to turn its fields into JSON-friendly values. It returns a dictionary with two parts: the kind and the frame data.

**Call relations**: When RedisStreamHub.publish is about to append a frame to Redis, it calls this function first. This function prepares the frame for json.dumps, which then turns it into the text form stored in the stream.

*Call graph*: called by 1 (publish); 1 external calls (model_dump).


##### `frame_from_payload`  (lines 67–71)

```
def frame_from_payload(payload: dict[str, object]) -> LiveFrame
```

**Purpose**: Rebuilds a live frame object from the tagged dictionary that was read back from Redis. It checks the kind tag so unknown or invalid frame types fail clearly instead of being misread.

**Data flow**: It receives a dictionary loaded from JSON. It reads the kind value, confirms it is a known frame kind, and uses the matching model class to validate and rebuild the frame data. It returns the reconstructed live frame.

**Call relations**: RedisStreamHub.subscribe calls this after reading a stored JSON frame from Redis. It converts the wire format back into the live-frame object that subscribers expect to receive.

*Call graph*: called by 1 (subscribe); 1 external calls (cast).


##### `_stream_id`  (lines 74–76)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis Stream entry id into two numbers that can be compared in time order. Redis ids look like a millisecond timestamp plus a sequence number, such as “123-0.”

**Data flow**: It receives a Redis entry id string. It splits the string around the dash, converts the millisecond part and sequence part into integers, and returns them as a pair. If the sequence part is missing, it treats it as zero.

**Call relations**: RedisStreamHub.covers uses this helper when deciding whether a saved cursor is still within the part of the stream Redis has kept. Comparing numeric pairs avoids fragile string comparisons.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 79–88)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from the shape returned by Redis xread. It also makes sure the response has the expected older Redis protocol shape, so the code does not silently read the wrong thing.

**Data flow**: It receives the raw batch returned by xread. If the batch is empty, it returns an empty list. If the batch is not the expected list form, it raises an error. Otherwise it pulls out and returns the entries for the stream.

**Call relations**: RedisStreamHub.subscribe calls this after each Redis xread. The subscribe loop then walks through the returned entries and turns each stored frame into a live frame for the caller.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 104–110)

```
def _client(self) -> Redis
```

**Purpose**: Gives the caller a Redis client that belongs to the currently running async event loop. This prevents one loop from reusing a Redis connection object created for another loop.

**Data flow**: It reads the current event loop and checks the hub’s internal dictionary of clients. If a client already exists for that loop, it returns it. If not, it creates a new Redis client from the configured URL, stores it for that loop, and returns it.

**Call relations**: Publishing, subscribing, and coverage checks all go through this method before talking to Redis. It is the safety valve that lets background workflow code and web-serving code use the same hub object without sharing loop-bound Redis state.

*Call graph*: called by 3 (covers, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 112–113)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name for one turn. This gives every turn its own separate live-update channel.

**Data flow**: It receives a turn id. It joins the fixed stream prefix with that id and returns the resulting Redis key name.

**Call relations**: RedisStreamHub.publish, RedisStreamHub.subscribe, and RedisStreamHub.covers all call this before using Redis. It keeps stream naming consistent, so writers and readers meet on the same Redis key.

*Call graph*: called by 3 (covers, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 115–122)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: Adds one live frame to the Redis Stream for a turn and returns the new stream cursor. Callers use this when they want viewers to see a new live update.

**Data flow**: It receives a turn id and a live frame. It builds the stream name, converts the frame into a JSON string, and uses a Redis pipeline, meaning a small batch of Redis commands sent together, to append the frame and refresh the stream’s expiry time. It returns the Redis entry id created by the append, which acts as the cursor for that frame.

**Call relations**: This is the writer side of the hub. It calls _stream to find the right Redis key, _client to get a safe Redis connection for the current event loop, and frame_payload to prepare the frame for storage. Subscribers later use the returned cursor, or a later one, to continue reading without repeating old frames.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 124–148)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Continuously reads live frames for one turn, starting after a cursor if one is provided. It supports both replaying retained frames and waiting for new frames as they arrive.

**Data flow**: It receives a turn id and an optional cursor. It chooses the Redis stream for that turn and starts reading from the cursor, or from the beginning if no cursor is given. It first tries a non-blocking read for already-available entries, then waits briefly for new entries if none are ready. For each entry, it updates the cursor, decodes the stored JSON, rebuilds the live frame, and yields the cursor plus frame to the caller. Timeout while waiting is treated as normal idleness, so the loop simply tries again.

**Call relations**: This is the reader side of the hub. It uses _stream and _client to talk to the right Redis stream, _stream_entries to normalize Redis responses, and frame_from_payload to rebuild frames. It is designed for surfaces or clients that tail a live turn and need to keep going across short quiet periods.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 150–156)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether a saved cursor is still covered by the retained Redis Stream. This tells a reconnecting reader whether it can resume cleanly or needs to redraw from durable state.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, it returns false. Otherwise it asks Redis for the first retained entry in that turn’s stream. If the stream is empty, it returns false. If there is a first entry, it compares that entry id with the cursor and returns true when the cursor is not older than what Redis still has.

**Call relations**: Reconnect logic can call this before subscribing from an old cursor. The method uses _stream to locate the stream, _client to query Redis, and _stream_id to compare Redis ids in the same order Redis uses.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `request handling and cross-pod terminal coordination`

A terminal session is like a phone call between a workflow and a user’s machine. In a single process, both sides can meet in memory. In a fleet of pods, the workflow may be on one pod while the held browser or CLI connection is on another, so memory is no longer enough. This file provides that meeting place using Redis, a shared fast key-value service, and a blob store, a shared place for larger byte data.

RedisTerminals publishes where a conversation’s terminal is currently connected, keeps that binding alive with a heartbeat, queues terminal operations one at a time, and waits for replies. Redis Streams act like small ordered mailboxes: workflows add an operation, connection pods read the next operation, and replies are written back to a reply stream. Large request or reply bodies are stored separately in the blob store so Redis is not overloaded.

The code is careful about failure. Keys have expiry times, so abandoned work eventually cleans itself up. A per-conversation lock stops two operations from running at the same time. A Redis-side Lua script claims an operation atomically, meaning two reconnecting pods should not both deliver the same command. If Redis or the terminal disappears, callers get TerminalGone instead of waiting forever.

#### Function details

##### `_text`  (lines 54–57)

```
def _text(value: bytes | str) -> str
```

**Purpose**: Converts a Redis field value into normal Python text. Redis may return either bytes or strings depending on client settings, and the rest of the file wants one consistent shape.

**Data flow**: It receives one Redis value, checks whether it is already text, and otherwise decodes the bytes into text. The result is a string that JSON parsing, ID comparison, and field decoding can use safely.

**Call relations**: Small decoding steps throughout the file call this when they read Redis data. It is used before turning stream fields into TerminalOp objects, reading bindings, checking gates, and decoding replies.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 60–65)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: Turns Redis’s flat stream field list into a more useful dictionary. This matters because the Lua script returns fields as alternating name and value items.

**Data flow**: It receives a flat list like field, value, field, value. It converts each item to text and returns a map from each field name to its value; if the input is not a list, it fails loudly.

**Call relations**: RedisTerminals.next_op uses this after the Lua claim script returns an operation. _pairs calls _text so the later operation decoder sees plain strings.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 68–71)

```
def _bind_payload(cwd: str, member_id: UUID | None) -> str
```

**Purpose**: Builds the small JSON record that says where a terminal is and which member it belongs to. This is the shared form used for both live bindings and in-flight operation pins.

**Data flow**: It receives a current working directory and an optional member ID. It writes them into JSON text, using the member’s hexadecimal ID when present and null when not.

**Call relations**: The heartbeat uses it when advertising a connected terminal. _run_op also uses it to keep the binding visible while an operation is in progress.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 145–154)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual entries from a Redis stream read response. It protects the code from silently misreading an unexpected Redis response shape.

**Data flow**: It receives the raw result from Redis XREAD. If there is no data, it returns an empty list; if the shape is wrong, it raises an error; otherwise it returns the stream entries.

**Call relations**: _await_reply calls this after waiting on a reply stream. That keeps reply-reading code focused on the reply fields instead of Redis response formatting.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 181–198)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the current asyncio event loop. An asyncio event loop is the scheduler running async tasks, and Redis clients bind their pending work to the loop that created them.

**Data flow**: It reads the current event loop, looks up an existing Redis client for that loop, and creates one if needed with bounded socket timeouts. It returns the client and stores it for reuse.

**Call relations**: Nearly every Redis operation in this class goes through _client. This is what lets workflow-side and serve-side loops use Redis safely without sharing one loop-bound client incorrectly.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 200–201)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores the live terminal binding for a conversation. The key name is predictable so any pod can find the same binding.

**Data flow**: It receives a conversation ID and formats it into the live binding key string. Nothing else is changed.

**Call relations**: _heartbeat writes this key while a connection is held. _read_binding reads it when another pod wants to know whether the terminal is connected.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 203–204)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores a temporary binding while an operation is running. This keeps the terminal discoverable even after the held stream hands off work and disconnects.

**Data flow**: It receives a conversation ID and returns the matching in-flight key string. It does not contact Redis itself.

**Call relations**: _run_op writes this key before posting an operation, _read_binding falls back to it if no live binding exists, and _clear_op removes it during cleanup.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 206–207)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name where terminal operations for one conversation are queued. A stream is an ordered mailbox in Redis.

**Data flow**: It receives a conversation ID and returns the operation stream name for that conversation.

**Call relations**: _run_op adds new operations to this stream, next_op reads and claims them, and _clear_op deletes completed entries when possible.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 209–210)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: Builds the Redis Stream name where the reply for one operation is written. Each operation gets its own reply mailbox.

**Data flow**: It receives an operation ID and returns the reply stream name. It has no side effects.

**Call relations**: _await_reply waits on this stream, _deliver_reply writes to it, and _clear_op deletes it after the operation finishes.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 212–213)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key for the per-conversation lock. The lock is like a bathroom key: only one terminal operation for the conversation can hold it at once.

**Data flow**: It receives a conversation ID and returns the lock key string. It does not acquire the lock itself.

**Call relations**: send uses this key to create and acquire a Redis lock before posting an operation, so commands queue instead of running concurrently.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 215–216)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key used as a delivery marker for an operation. The marker shows that a connection pod has already claimed the operation for delivery.

**Data flow**: It receives an operation ID and returns the delivery-marker key string.

**Call relations**: The Lua script in next_op creates these marker keys directly using the same prefix. _clear_op later removes the marker during best-effort cleanup.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 218–219)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key that stores metadata for an operation, such as its conversation and member. This metadata is used as a safety check before serving staged input or accepting a reply.

**Data flow**: It receives an operation ID and returns the operation metadata key string.

**Call relations**: _run_op writes this metadata, staged and _deliver_reply read it to check permissions, and _clear_op deletes it after completion.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 221–222)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for an operation’s input body. Blob storage is used for byte payloads that should not be squeezed into Redis stream fields.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s staged body.

**Call relations**: _run_op writes the body there, staged reads it for the connection-side projection, and _clear_op deletes it afterward.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 224–225)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for a large operation reply. Small replies go in Redis directly, but large replies are stored as blobs.

**Data flow**: It receives an operation ID and returns the blob key for that operation’s reply body.

**Call relations**: _deliver_reply writes large replies there, _decode_reply reads them when the waiting sender sees a blob marker, and _clear_op deletes them.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 227–240)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Records that this pod currently holds a terminal connection for a conversation. It starts or shares a heartbeat so other pods can discover the connection through Redis.

**Data flow**: It receives the conversation ID, current working directory, and optional member ID. Under a thread lock, it creates a local hold if needed, starts the heartbeat task, and increments the number of active held connections.

**Call relations**: The serve-side held stream calls this when a terminal connection is established. It starts _heartbeat, which does the actual Redis publishing until disconnect reduces the count to zero.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 242–251)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Marks one local terminal connection as gone. When the last local connection for that conversation leaves, it stops the heartbeat.

**Data flow**: It receives a conversation ID, finds the local hold, decrements its connection count, and cancels the heartbeat task if there are no remaining connections. It intentionally does not delete the Redis binding.

**Call relations**: This pairs with connect. By cancelling the heartbeat instead of deleting the key, it lets Redis expiry bridge short reconnect gaps without one pod erasing another pod’s fresh binding.


##### `RedisTerminals._heartbeat`  (lines 253–265)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Keeps the live terminal binding fresh in Redis while this pod holds the connection. It is the periodic “I’m still here” signal.

**Data flow**: It builds the binding JSON and key, then repeatedly writes that key with a time-to-live and sleeps before refreshing again. Redis errors are ignored so a temporary Redis problem does not crash the task.

**Call relations**: connect starts this background task. Other pods later read the key through _read_binding, and disconnect cancels the task when the local hold ends.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 267–276)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns this pod’s local view of the terminal workspace, if this pod is the one holding it. It avoids Redis because the information is already in memory locally.

**Data flow**: It receives a conversation ID, looks in the local hold table under a lock, and returns a TerminalWorkspace with the directory and member ID if found. If this pod has no hold, it returns None.

**Call relations**: This is useful for local callers that only need the workspace on the connection-holding pod. Cross-pod send flow uses arrived instead, because arrived reads the Redis-published binding.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 278–291)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal binding to appear for a conversation. This covers normal reconnect gaps where the member’s client is between held streams.

**Data flow**: It receives a conversation ID and a grace period. It repeatedly calls _read_binding until it finds a workspace, the grace time runs out, or it sleeps and polls again.

**Call relations**: send calls arrived before creating an operation. If arrived finds no binding, send reports that no terminal is connected instead of posting work into nowhere.

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 293–307)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Reads the terminal binding from Redis and turns it into a TerminalWorkspace. It checks both the live connection key and the temporary in-flight key.

**Data flow**: It asks Redis for the live binding key; if missing, it asks for the in-flight binding key. If neither exists it returns None; otherwise it parses the JSON and returns the directory and optional member ID.

**Call relations**: arrived uses this while waiting for a terminal to appear. It relies on _bind_key and _inflight_key for the Redis names, and on _text to normalize the stored JSON.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 309–365)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one terminal operation to the connected terminal and waits for its answer. It is the main workflow-side entry for asking the user’s terminal to do something.

**Data flow**: It receives the conversation, operation details, timeout, and optional body bytes. It waits for a binding, creates a TerminalOp, acquires the per-conversation Redis lock, runs the operation, and returns reply bytes; if anything important is missing or unreachable, it raises TerminalGone.

**Call relations**: send begins by calling arrived, then uses _lock_key and _client to serialize operations. Once it owns the lock, it hands the real work to _run_op and releases the lock afterward.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 5 external calls (__init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 367–413)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: Performs the actual post-lock operation work: publish metadata, stage input bytes, add the operation to Redis, wait for the reply, and clean up. It assumes send has already made sure only one operation is active for the conversation.

**Data flow**: It receives a conversation, TerminalOp, optional body, known binding, and deadline. It writes metadata and an in-flight binding, optionally stores body bytes in the blob store, adds the operation to the Redis stream, waits for the reply, and finally clears operation state.

**Call relations**: send calls this after acquiring the lock. It calls _await_reply to wait for the terminal’s response and always calls _clear_op afterward to remove Redis keys and blobs as best it can.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 415–423)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: Converts a TerminalOp object into the field map stored in the Redis operation stream. This gives the connection pod all the command details it needs.

**Data flow**: It receives a TerminalOp and returns a dictionary containing its ID, kind, timeout, name, argument, and parameters as Redis-friendly values.

**Call relations**: _run_op calls this immediately before adding an operation to the conversation’s Redis stream. next_op later reads those same fields and reconstructs a TerminalOp.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 425–433)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: Rebuilds a TerminalOp from fields read out of Redis. This is the reverse of _op_fields.

**Data flow**: It receives a field dictionary from a stream entry, converts values to text, parses the timeout as a number, and returns a TerminalOp object.

**Call relations**: next_op calls this after it has claimed an operation. The returned TerminalOp is what the held terminal stream will render or execute for the client.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 435–458)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: Waits for the reply to one operation, but only until the operation’s deadline. This prevents a workflow from getting stuck forever if the terminal disappears.

**Data flow**: It receives an operation ID, total deadline, and user-facing timeout. It repeatedly reads the operation’s reply stream with short bounded waits, decodes the first reply entry, and returns reply bytes or raises TerminalGone on timeout or Redis failure.

**Call relations**: _run_op calls this after posting an operation. It uses _reply_stream to find the mailbox, _stream_entries to interpret Redis’s response, and _decode_reply to turn the reply fields into bytes or an error.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 460–473)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: Turns reply stream fields into the final bytes returned to the sender, or raises the right terminal error. It understands failed replies, inline replies, and large blob-backed replies.

**Data flow**: It receives an operation ID and reply fields. If there is a failure field, it raises TerminalOpFailed; if there is a blob marker, it reads the reply bytes from the blob store; otherwise it base64-decodes the inline bytes.

**Call relations**: _await_reply calls this once a reply entry appears. _deliver_reply writes replies in the exact formats this function reads.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 475–506)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for and claims the next terminal operation for a conversation. It is used by the connection side that needs to know what command to show or run next.

**Data flow**: It receives a conversation ID and optionally an operation ID to skip. It runs a Redis Lua script that cleans expired entries, skips already delivered operations, marks one operation as claimed, and returns it; if none is ready, it waits for more stream data and tries again.

**Call relations**: This is the receiving counterpart to send and _run_op. It uses _op_stream to find the queue, _pairs and _decode_op to rebuild the TerminalOp, and converts Redis failures into TerminalGone so the held stream can end cleanly.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 508–523)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Fetches the staged input body for an in-flight operation, if the requester is allowed to see it. This lets any pod serve the bytes because the body is in the shared blob store.

**Data flow**: It receives the conversation ID, operation ID, and optional member ID. It reads operation metadata from Redis, checks that the conversation and member match, and then reads the body blob; if anything is missing, mismatched, or too slow, it returns None.

**Call relations**: The connection-side read path uses this after next_op exposes an operation with a staged body. It relies on _gate_ok for the safety check and _body_blob for the blob-store key.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 525–540)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts a terminal reply and schedules its delivery without making the HTTP or stream handler wait on Redis. It returns immediately while a background task writes the reply.

**Data flow**: It receives the conversation ID, operation ID, reply bytes, optional failure text, and optional member ID. It creates a background delivery task and returns True right away.

**Call relations**: This is the reply-side counterpart to _await_reply. It hands the real write to _deliver_reply through _spawn so the waiting send call can pick up the reply from Redis when it arrives.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 542–571)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: Writes an operation reply into the shared reply stream after checking that it belongs to the right conversation and member. It stores large replies in the blob store and small replies directly in Redis.

**Data flow**: It receives operation identity, reply bytes, optional failure text, and optional member ID. It reads metadata, rejects mismatches with a warning, chooses failure/blob/base64 fields, writes the reply stream entry, and sets an expiry on the stream.

**Call relations**: resolve schedules this in the background. _await_reply is waiting on the stream it writes, and _decode_reply later reads either the inline base64 data or the blob marker created here.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 573–584)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a staged-body read or reply post is allowed for a given operation. It fails closed, meaning missing or mismatched information denies access.

**Data flow**: It receives raw operation metadata, a conversation ID, and an optional member ID. It parses the metadata, verifies the conversation matches, and if a member was named, verifies that member matches too.

**Call relations**: staged uses this before serving copy-in bytes, and _deliver_reply uses it before accepting a reply. This keeps one conversation or member from accidentally touching another operation.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 586–589)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports no local in-flight operation for this Redis-backed transport. In a cross-pod setup, the operation being awaited may live on another pod, so a local answer would be misleading.

**Data flow**: It receives a conversation ID but does not inspect Redis or local state. It always returns None.

**Call relations**: This satisfies the broader terminal transport interface. Unlike an in-process transport, this file intentionally does not claim a partial local view of distributed work.


##### `RedisTerminals._clear_op`  (lines 591–612)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: Cleans up Redis keys, stream entries, and blob objects related to an operation after it finishes or times out. The system is safe even if cleanup misses something, because keys also expire.

**Data flow**: It receives the conversation ID, operation ID, and optional stream entry ID. It tries to delete the operation stream entry, metadata, delivery marker, in-flight binding, reply stream, staged body blob, and reply blob, suppressing cleanup errors.

**Call relations**: _run_op calls this in a final cleanup step no matter how the operation ends. The delivery-safety rules come from next_op’s claim marker and expiry windows; _clear_op is the tidy-up crew.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 614–621)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: Starts a background task and keeps a reference to it so it is not lost while running. It also arranges for failures to be logged.

**Data flow**: It receives a coroutine and an event loop. It wraps the coroutine with _logged, creates a task on the loop, stores it in the task set, and removes it from the set when done.

**Call relations**: resolve uses this to schedule _deliver_reply. _spawn connects quick request handling with reliable enough background delivery and logging.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 623–627)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: Runs a background coroutine and logs any exception it raises. This prevents fire-and-return delivery failures from disappearing silently.

**Data flow**: It receives a coroutine, awaits it, and if any exception occurs, sends a warning with the error text. It does not return useful data.

**Call relations**: _spawn wraps background delivery work with this. When _deliver_reply fails after resolve has already returned, _logged is the place that records the problem.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).
