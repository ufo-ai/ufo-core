# Live Turn Streaming  `stage-7.2`

Live Turn Streaming is the system’s “watch it happen” layer during the main work loop, when an agent is producing a response. It sends small live updates, like new text, tool activity, cost changes, replies, and final status, to any user interface that is watching.

The core hub is the local message room for one running turn. Publishers drop updates into it, and readers can join, leave, and rejoin without blocking the work or missing the most recent events. The Redis stream hub extends this across multiple server processes. Redis Streams act like a shared conveyor belt, so one process can publish live frames and another can read them. These frames are temporary live signals, not the permanent final answer.

The hub tail ties everything together for a client watching one turn. It listens to the live hub, but also checks the database so late subscribers or reconnected terminal clients can see the correct ending, even if the turn completed, failed, or paused elsewhere.

## Files in this stage

### Turn Tail Orchestration
Client-facing tailing combines live hub frames with durable polling so late or reconnecting subscribers see a reliable stream ending.

### `core/src/ufo/runtime/surfaces/hub_tail.py`

`orchestration` · `request handling`

A “turn” is a unit of work that produces live frames, such as progress updates or final results. The problem this file solves is reliability: live updates may be held in memory, but the final state of a turn is stored durably in the database. If a caller starts listening late, or if another event loop commits the turn before the local hub sees it, relying only on the live hub could leave the caller waiting forever.

The file solves this by racing two sources into one stream. One source subscribes to the hub, which is like listening to a radio broadcast of live frames. The other source checks the database every second, like looking at the official scoreboard. Whichever first reports that the turn is finished or parked wins, and the stream stops cleanly.

A parked turn is not complete; it is paused because something blocks it, such as a revoked seat, low billing balance, or spending cap. When the database says a turn is parked, this file re-checks the current blocking reason instead of trusting an old stored message. That keeps the user-facing explanation accurate if conditions changed.

The HubTailer class wraps this behavior so surface code can ask for a turn stream without knowing the details of hubs, polling, cleanup, or billing messages.

#### Function details

##### `tail_frames`  (lines 34–66)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='', billing_url: str | None=None) -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: This is the main streaming function. It yields live frames for one turn until it sees either a final result or a parked pause, and it works whether the caller connects before, during, or after the turn finishes.

**Data flow**: It receives a hub, a turn ID, an optional last-seen cursor, and an optional billing URL. First it decides where to resume from by asking whether the hub still covers the supplied cursor. It starts a background hub subscription, checks the database once for an already-finished or already-parked turn, then starts a repeating database poll if needed. Frames from both paths flow into one queue, and the function yields them outward until a terminal or parked frame appears. When the caller stops listening, it cancels the background work so no hidden tasks are left running.

**Call relations**: HubTailer.tail calls this when a surface wants to stream one turn. Inside, it starts _pump to listen to the live hub and uses _read_status_frame immediately, then through _poll_status, to make sure the durable database ending is not missed.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, _read_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 69–78)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This background helper copies live frames from the hub subscription into the shared queue used by tail_frames. It ignores arrival-queue notices because those are not useful stream content for this tail.

**Data flow**: It receives the hub, the turn ID, the cursor to start from, and the output queue. It subscribes to the hub and, for each received frame, skips ArrivalQueued frames and puts the rest into the queue with their cursor. If the subscription fails, it logs the error instead of crashing the caller directly.

**Call relations**: tail_frames starts this as a background task at the beginning of a tail. It depends on Hub.subscribe for the live feed and hands frames back to tail_frames through the queue.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 81–93)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]], billing_url: str | None) -> None
```

**Purpose**: This background helper repeatedly checks the durable turn state until it finds a final or parked frame. It is the safety net for cases where the live hub does not deliver the ending.

**Data flow**: It receives a turn ID, the shared frame queue, and an optional billing URL. Every second it asks _read_status_frame for the database-backed status. If a read fails, it logs the problem and tries again later. Once it finds a terminal or parked frame, it puts that frame into the queue with an empty cursor and then stops.

**Call relations**: tail_frames starts this only after an initial database check shows the turn is still active. It repeatedly calls _read_status_frame, and its result can end the stream even if _pump never receives the final live frame.

*Call graph*: calls 1 internal fn (_read_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `_read_status_frame`  (lines 96–114)

```
async def _read_status_frame(turn_id: UUID, billing_url: str | None) -> LiveFrame | None
```

**Purpose**: This helper reads the database-backed status frame while carefully preserving cancellation behavior. It protects the actual status read from being half-interrupted, then reports cancellation at a safe point.

**Data flow**: It starts turn_status_frame as its own asynchronous task. While that task is running, it waits for it through a shield, which means an outside cancellation does not immediately kill the database read. If cancellation happened, it remembers that, waits until the read has reached a result or error, and then either returns the frame, raises the read error, or re-raises the cancellation as appropriate.

**Call relations**: tail_frames uses this for the first immediate durable check, and _poll_status uses it for repeated checks. It hands off the real database and business-rule work to turn_status_frame.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 2 (_poll_status, tail_frames); 2 external calls (ensure_future, shield).


##### `turn_status_frame`  (lines 117–174)

```
async def turn_status_frame(turn_id: UUID, billing_url: str | None=None) -> LiveFrame | None
```

**Purpose**: This function turns the stored state of a turn into the stream-ending frame, if there is one. It returns a final Terminal frame for completed turns, a Parked frame with a current explanation for paused turns, or nothing while the turn is still active.

**Data flow**: It receives a turn ID and optional billing URL. It opens a workspace database transaction and reads the turn row, including status, stored terminal data, workspace, agent, conversation, and member information. If there is a stored terminal payload, it validates it and returns a Terminal frame. If the turn is not parked, it returns nothing. If it is parked, it checks possible current causes in order: seat access, billing balance, and spending caps. It returns a Parked frame with the matching user-facing message, or a generic paused message if no specific blocker is currently found.

**Call relations**: _read_status_frame is the only function in this file that calls it. It reaches into the database through workspace_tx, uses schema tables to read turn and conversation data, and consults seats and billing/spend helpers so the parked message matches the current reason the turn cannot resume.

*Call graph*: called by 1 (_read_status_frame); 11 external calls (__init__, __init__, __init__, __init__, model_validate, select, workspace_tx, applicable_caps_absent, balance_refusal_message, read_headroom (+1 more)).


##### `HubTailer.tail`  (lines 187–190)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: This method gives callers a clean context-managed way to stream one turn. The caller does not need to know how to start or stop the hub subscription and polling tasks.

**Data flow**: It receives a turn ID and optional cursor. It calls tail_frames with the HubTailer’s hub and billing URL, then wraps the async generator in a closing context. The result is an async stream that automatically cleans itself up when the caller exits the context.

**Call relations**: Surface code calls this method as the public entry into this file’s tailing behavior. It delegates the actual streaming work to tail_frames and uses aclosing so tail_frames gets closed properly even if the caller stops after the first ending frame.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


##### `HubTailer.latest_activity`  (lines 192–193)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This method asks the hub for the latest known activity for a turn. It is a lightweight way for a surface to check what the hub most recently observed.

**Data flow**: It receives a turn ID and forwards it to the bound hub. The hub returns the latest Activity if it has one, or nothing if it does not.

**Call relations**: Callers use this through HubTailer when they need recent activity information without opening a full frame stream. It simply passes the request to the hub’s latest_activity method.


### Live Stream Transports
Redis-backed and in-memory hubs publish, buffer, and fan out short-lived live turn updates without blocking publishers.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `request handling and live streaming`

This file is the Redis-backed version of a live update hub. Think of it like a shared noticeboard for each conversation turn: as the system produces small updates, such as text chunks, cost updates, activity messages, or terminal events, it posts them to a Redis Stream named for that turn. A user-facing surface can then read forward from its last bookmark and show the updates in order.

The important reason this exists is scale. An in-memory hub only works inside one running server process. Redis gives all server processes a shared place to exchange live frames, so work done by one process can be watched by another. The frames are intentionally temporary: Redis streams are trimmed and expire after being idle. If an old live frame disappears, the system can redraw or fall back to the durable final answer stored elsewhere.

The file also protects against a subtle async problem. Redis async clients are tied to the event loop that created them, and this project may publish from one event loop while subscribing from another. So RedisStreamHub keeps a separate Redis client per running event loop. Publishing converts a frame into a small JSON message, appends it to Redis, and returns Redis's stream entry id as a cursor. Subscribing uses that cursor to replay retained messages and then waits for new ones. A timeout while waiting is treated as normal idleness, not as an error.

#### Function details

##### `frame_payload`  (lines 78–84)

```
def frame_payload(frame: HubFrame) -> dict[str, object]
```

**Purpose**: Turns one live frame into a simple wire message that can be stored in Redis as JSON. It adds a kind label so the reader later knows what type of frame to rebuild.

**Data flow**: It receives a HubFrame object. If the frame is an Activity, it reshapes it into the older tool-activity wire format; otherwise it asks the frame to produce its JSON-ready fields. It returns a dictionary with two parts: the frame kind and the frame data.

**Call relations**: RedisStreamHub.publish calls this right before writing a frame to Redis. The result is then passed to JSON encoding so Redis stores a plain text representation rather than a Python object.

*Call graph*: called by 1 (publish); 2 external calls (__init__, model_dump).


##### `frame_from_payload`  (lines 87–97)

```
def frame_from_payload(payload: dict[str, object]) -> HubFrame
```

**Purpose**: Rebuilds a live frame object from the JSON-style message read out of Redis. It is the reverse of frame_payload, with extra support for legacy activity message shapes.

**Data flow**: It receives a dictionary containing a kind label and data. It checks the kind, validates the data against the matching frame model, and returns the correct HubFrame object. If the kind is unknown, it raises an error rather than guessing.

**Call relations**: RedisStreamHub.subscribe uses this for every stream entry it yields to a viewer. RedisStreamHub.latest_activity also uses it when it finds an activity-like entry and needs to return it as an Activity object.

*Call graph*: called by 2 (latest_activity, subscribe); 2 external calls (__init__, cast).


##### `_stream_id`  (lines 100–102)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis Stream entry id into numbers that can be compared safely. Redis ids look like a timestamp plus a sequence number, such as '1712345678901-0'.

**Data flow**: It receives a Redis entry id string. It splits it into the millisecond timestamp and sequence part, turns both into integers, and returns them as a pair. That pair can be compared with another pair to tell which id came first.

**Call relations**: RedisStreamHub.covers calls this when deciding whether a subscriber's saved cursor still points into the part of the stream Redis has kept.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 105–114)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual entries from Redis's XREAD response and checks that the response has the expected shape. This prevents the code from silently misreading an unexpected Redis protocol format.

**Data flow**: It receives the raw response from Redis XREAD. If the response is empty, it returns an empty list. If the response is not the expected list form, it raises a clear error. Otherwise it returns the stream entries inside the response.

**Call relations**: RedisStreamHub.subscribe calls this after each XREAD operation. It gives subscribe a clean list of entries to decode and yield.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 130–136)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the current async event loop, creating it on first use. This avoids sharing one async Redis client across event loops, which can break because its waiting tasks belong to the loop that created it.

**Data flow**: It looks at the currently running event loop. If this hub already has a Redis client for that loop, it returns it. If not, it creates a client from the configured Redis URL, stores it under that loop, and returns the new client.

**Call relations**: All Redis operations in RedisStreamHub go through this helper: publish, subscribe, covers, and latest_activity. It is the small gatekeeper that makes the hub safe to use from both workflow loops and server-facing loops.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 138–139)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream key for one turn. Each turn gets its own stream so live frames for different turns do not mix.

**Data flow**: It receives a turn UUID. It combines the fixed stream prefix with that UUID and returns the Redis key string.

**Call relations**: RedisStreamHub.publish, subscribe, covers, and latest_activity all call this before touching Redis. It makes sure every operation uses the same naming rule for the turn's stream.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 141–148)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: Adds one live frame to the Redis Stream for a turn and returns the Redis cursor for that new entry. A caller uses this when fresh live output needs to be visible to subscribers.

**Data flow**: It receives a turn id and a HubFrame. It builds the stream name, converts the frame to a JSON string, then sends Redis two commands in a pipeline: append the frame to the stream and refresh the stream's expiry time. Redis returns the new entry id, and this function returns it as a string.

**Call relations**: This is the publishing side of the hub. It relies on _stream for the Redis key, frame_payload for the message format, and _client for the correct Redis connection. Subscribers later use the returned entry ids as cursors.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 150–174)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: Watches a turn's Redis Stream and yields live frames in order, starting after a given cursor. It lets a surface replay missed retained frames and then continue receiving new ones.

**Data flow**: It receives a turn id and optionally a cursor. It starts from that cursor, reads batches from Redis, and if no immediate entries are available it waits briefly for new ones. For each entry found, it updates the cursor, decodes the stored JSON into a HubFrame, and yields the new cursor plus the frame. If Redis times out while waiting, it simply tries again.

**Call relations**: This is the subscriber side of the hub. It uses _stream to find the right Redis Stream, _client to read from Redis, _stream_entries to normalize Redis responses, and frame_from_payload to rebuild frames before handing them to the caller.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 176–182)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether Redis still has enough stream history to resume from a saved cursor without a gap. If the cursor points before the oldest retained entry, the caller should redraw or restart from the beginning instead of assuming it has a complete history.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, it returns false. Otherwise it reads the oldest remaining entry in that turn's stream. If the stream is gone or empty, it returns false. If the oldest retained id is less than or equal to the cursor, it returns true, meaning the cursor is still covered.

**Call relations**: Reconnect logic can call this before using subscribe with an old cursor. It uses _stream to locate the stream, _client to read from Redis, and _stream_id to compare Redis entry ids correctly.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


##### `RedisStreamHub.latest_activity`  (lines 184–216)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Looks for the most recent activity message in the tail end of a turn's stream. This is useful for showing what an agent appears to be doing now, without scanning an entire long stream.

**Data flow**: It receives a turn id. It reads recent stream entries newest-first, in bounded batches, up to a fixed maximum number of frames. For each entry, it decodes only enough JSON to check the kind. If it finds a tool activity or skill activity, it rebuilds and returns it as an Activity. If it reaches the limit, the stream ends, or no activity is found, it returns None.

**Call relations**: Status or polling code can call this to get a lightweight current-activity hint. It uses _stream and _client to read Redis, JSON decoding to inspect entries, and frame_from_payload to rebuild the one activity frame it returns.

*Call graph*: calls 3 internal fn (_client, _stream, frame_from_payload); 2 external calls (loads, cast).


### `core/src/ufo/runtime/hub.py`

`io_transport` · `live turn streaming`

A running turn produces many small events before it finishes. Some are visible, like text chunks, activity messages, replies, and final terminal frames. Others are internal signals, like a newly queued arrival. This file defines those event shapes and an in-memory hub that fans them out to any live surface watching the turn.

The main idea is like a notice board with a short memory. Each turn has a fixed-size recent-history buffer, also called a ring buffer because old entries fall off when it fills. Every published frame gets a cursor, which is just a bookmark number. When a surface reconnects, it can say “start after this cursor,” and the hub replays the saved frames after that point before sending new ones.

The hub is deliberately lossy for slow subscribers. If a subscriber’s private queue is full, its oldest waiting frame is dropped so publishing never blocks. That protects the running agent from being slowed down by a frozen browser or terminal.

The file also pays careful attention to cleanup. Finished streams are removed once no one is listening, so memory stays bounded. Parked turns are treated differently from fully terminal turns because they may resume under the same turn id. Subagent activity is only mirrored onto an already-live root stream; it does not recreate an ended stream.

#### Function details

##### `Hub.publish`  (lines 151–151)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This is the interface promise for adding one live frame to a turn’s stream. Code that only knows about the abstract hub can call this without caring whether the hub is in-process, Redis-backed, or something else.

**Data flow**: Input is a turn id and a frame, such as a text delta or terminal status. An implementation appends that frame to the stream, assigns it a cursor bookmark, sends it to listeners, and returns the cursor string.

**Call relations**: Failure-handling code in the runtime queue publishes terminal failure frames through this interface. The concrete in-memory version, InProcessHub.publish, supplies the actual behavior described by the interface.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 153–153)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This is the interface promise for watching a turn’s live stream. A caller can provide a cursor bookmark so it gets recent missed frames before receiving new ones.

**Data flow**: Input is a turn id and an optional cursor. An implementation returns an asynchronous stream of cursor-and-frame pairs: first replayed saved frames after the cursor, then newly published frames as they arrive.

**Call relations**: The hub tailing code used by live surfaces calls this when it needs to pump frames toward a terminal or web client. The in-memory implementation provides replay plus live delivery.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 155–155)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for asking whether a reconnect can safely resume from a given cursor. It helps a surface decide whether to replay from the hub or redraw from durable stored state instead.

**Data flow**: Input is a turn id and a cursor string. An implementation checks whether its retained recent-history buffer still reaches back far enough, then returns true or false.

**Call relations**: The live tailing path calls this before choosing a reconnect strategy. InProcessHub.covers is the in-memory answer to that question.

*Call graph*: called by 1 (tail_frames).


##### `Hub.latest_activity`  (lines 157–157)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This is the interface promise for asking what a running turn appears to be doing right now, without opening a full live subscription. It is useful for quick status views.

**Data flow**: Input is a turn id. An implementation looks at recent retained frames and returns the newest Activity frame if one is still meaningful, or None if it cannot find one.

**Call relations**: This method is part of the hub contract for status readers. The concrete in-memory version searches its recent buffer with a fixed limit so status polling stays cheap.


##### `_offer`  (lines 160–163)

```
def _offer(queue: asyncio.Queue[tuple[str, HubFrame]], item: tuple[str, HubFrame]) -> None
```

**Purpose**: This helper puts one frame into a subscriber’s queue without ever waiting. If that subscriber is already backed up, it drops the oldest queued frame to make room for the newer one.

**Data flow**: Input is a subscriber queue and a cursor-and-frame item. The function checks whether the queue is full; if so, it removes one old item, then inserts the new item. It returns nothing, but the queue has been updated.

**Call relations**: InProcessHub.publish schedules this helper onto each subscriber’s event loop. That indirection lets publishers running on one thread safely deliver frames to subscribers running on another.


##### `InProcessHub._stream`  (lines 210–220)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This internal helper gets the stored live-stream state for one turn, creating it if needed. It keeps the rest of the hub code from repeating the setup rules for a turn’s buffer, subscribers, and cursor number.

**Data flow**: Input is a turn id. The helper looks in the hub’s turn dictionary; if the stream already exists, it returns it. If not, it creates a new _TurnStream with a fixed-size replay buffer, no subscribers, and a cursor sequence starting from the remembered mark for that turn.

**Call relations**: Both InProcessHub.publish and InProcessHub.subscribe call this while holding the hub’s lock, meaning the shared state is opened or created in a safe, consistent way before frames are appended or subscribers are registered.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 222–241)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This adds one frame to a turn’s in-memory live stream and fans it out to current subscribers. It is built so the producer never waits for slow or disconnected readers.

**Data flow**: Input is a turn id and a hub frame. Under a lock, the function finds or creates the turn stream, assigns the next cursor, saves the frame in the replay buffer, remembers the subscribers to notify, and marks the stream ended if the frame is terminal or parked. After releasing the lock, it schedules delivery to each subscriber queue and returns the cursor. If the frame is subagent activity for a missing or already-ended root stream, it drops it and returns an empty cursor.

**Call relations**: This is the concrete implementation behind Hub.publish. It relies on _stream to get turn state and on _offer, scheduled on subscriber event loops, to deliver without blocking.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 243–270)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This lets a caller watch a turn from a cursor onward. It first replays saved frames that the caller has not seen, then waits for new live frames.

**Data flow**: Input is a turn id and optional cursor bookmark. The function creates a bounded subscriber queue tied to the current asynchronous event loop. Under the lock, it registers that queue and snapshots the replay frames newer than the cursor. It yields those replay frames first, then yields items from the live queue forever until the caller stops. When the caller leaves, it unregisters the queue and may delete the stream if it has ended or has no retained frames.

**Call relations**: This is the concrete implementation behind Hub.subscribe, used by live surface tailing code. It calls _stream so a turn has state before replay and live delivery begin.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 272–280)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still goes back far enough for a reconnecting reader’s cursor. It answers whether the reader can resume cleanly from the hub alone.

**Data flow**: Input is a turn id and cursor string. If the cursor is empty, or the turn has no stream or no buffered frames, it returns false. Otherwise it compares the oldest retained cursor with the requested cursor and returns true when the buffer still covers that point.

**Call relations**: This is the concrete implementation behind Hub.covers. The live tailing flow uses the answer to decide whether to continue from the cursor or fall back to another way of rebuilding state.


##### `InProcessHub.latest_activity`  (lines 282–299)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This gives a quick one-line view of what a turn is currently doing, without subscribing to the whole stream. It intentionally looks only at a limited number of recent frames so repeated status checks stay inexpensive.

**Data flow**: Input is a turn id. The function finds the turn’s retained buffer, then scans backward through only the most recent activity-peek window. If it finds an Activity frame, it returns it; if there is no stream or no recent Activity, it returns None.

**Call relations**: This is the concrete implementation behind Hub.latest_activity. It uses a bounded reverse scan so status readers can ask often without forcing the hub to search thousands of text frames every time.

*Call graph*: 1 external calls (islice).
