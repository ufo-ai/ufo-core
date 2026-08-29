# Live streams, downloads, and callback routes  `stage-6.3`

This stage is the system’s set of “open doors” while work is running or while a user is returning from another service. It is not the core thinking loop itself. Instead, it lets browsers, terminals, and external providers stay connected to that loop.

The live-stream pieces work like a news feed for one running turn. core/src/ufo/hub.py publishes text, status, costs, replies, and results, and keeps a short memory so clients can reconnect. extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py moves those updates through Redis Streams, a shared message pipe, so different servers can publish and read them. core/src/ufo/surfaces/hub_tail.py combines live messages with database checks, so late clients still learn when a turn ends or pauses. core/src/ufo/loop/steps.py turns internal workflow records into a readable timeline of model rounds, tool calls, and other steps.

The remaining routes help users get files or finish browser-based setup. core/src/ufo/surfaces/artifacts.py serves artifact downloads through signed links and can refresh expired ones for valid members. core/src/ufo/sdk/callback_page.py shows the final “done” page. core/src/ufo/surfaces/cli.py and extensions/pipedream/ufo_ext_pipedream/provider.py complete OAuth-style account connections, including Pipedream’s extra hosted setup step.

## Files in this stage

### Live turn streams
Live stream surfaces and transports keep clients updated on running turns, reconnects, terminal status, and readable step timelines.

### `core/src/ufo/surfaces/hub_tail.py`

`orchestration` · `request handling`

A “turn” is a unit of work whose progress can be streamed to a caller. The hard part is that a caller may arrive after the turn has already started, or the live in-memory hub may miss the final state because another process or event loop committed it. This file solves that by using two sources at once, like listening to both a walkie-talkie and checking the official notice board. The walkie-talkie is the hub subscription, which provides live frames as they happen. The notice board is the durable database state, which is checked once at the start and then polled until the turn is terminal or parked.

The main generator, `tail_frames`, feeds both sources into one queue. It yields frames to the caller until it sees a true ending: a terminal frame or a parked frame. “Parked” means paused rather than finished, often because billing, spending caps, or seat access stopped it. If the caller disconnects, the generator cancels the background work so no tasks are left running.

A key detail is that polling failures are treated as temporary. If one database read fails, the poll logs it and tries again later. This matters because the poll is the safety net that notices final states the live hub might not see.

#### Function details

##### `tail_frames`  (lines 34–66)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='', billing_url: str | None=None) -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: Streams all visible live frames for one turn and stops when the turn reaches a final or paused state. It is designed for clients that may connect late or reconnect with the last cursor they saw.

**Data flow**: It receives a hub, a turn id, an optional cursor called `since`, and an optional billing URL. It first checks whether the hub can resume from that cursor; if not, it starts from the beginning of the retained live history. It starts a background hub reader, checks the database for an already-finished or already-parked turn, and if needed starts a background poller. It yields each frame to the caller, then shuts down all background tasks when the stream ends or the caller closes it.

**Call relations**: This is the central flow used by `HubTailer.tail`. It asks the hub whether the reconnect cursor is still covered, starts `_pump` to receive live hub frames, uses `_read_status_frame` to catch an already-stored ending, and starts `_poll_status` so a later database ending is not missed.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, _read_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 69–78)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: Copies live frames from the hub subscription into the shared queue used by the streamer. It ignores purely internal arrival notifications so callers see meaningful turn frames.

**Data flow**: It receives the hub, the turn id, the starting cursor, and the queue. It subscribes to the hub and, for every frame it receives, filters out `ArrivalQueued` messages and puts the remaining frames into the queue with their cursor. If the subscription fails, it logs the problem instead of crashing the whole stream.

**Call relations**: `tail_frames` starts this as a background task. `_pump` is the live side of the two-source design: it hands hub frames into the same queue that `_poll_status` uses for database-discovered ending frames.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 81–93)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]], billing_url: str | None) -> None
```

**Purpose**: Repeatedly checks the durable turn record until it finds that the turn has ended or parked. This is the safety net for cases where the live hub does not deliver the final state to this process.

**Data flow**: It receives the turn id, the shared frame queue, and the optional billing URL. Once per polling interval, it asks `_read_status_frame` for the current durable status. If no ending is present, it waits and tries again. If a terminal or parked frame appears, it puts that frame into the queue with an empty cursor and stops. If a read fails, it logs the error and keeps polling.

**Call relations**: `tail_frames` starts this after confirming the turn did not already have a stored ending. It depends on `_read_status_frame` for each database check and feeds its result back into the same queue as `_pump`, so the caller does not need to know which source found the ending.

*Call graph*: calls 1 internal fn (_read_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `_read_status_frame`  (lines 96–114)

```
async def _read_status_frame(turn_id: UUID, billing_url: str | None) -> LiveFrame | None
```

**Purpose**: Reads the durable status of a turn while carefully handling cancellation. It makes sure an in-progress status read is not abandoned halfway without a clear outcome.

**Data flow**: It receives a turn id and optional billing URL. It starts `turn_status_frame` as an asynchronous task and shields that task from immediate cancellation, meaning a caller asking to stop will be remembered but the database read is allowed to finish. It then returns the frame found by the read, returns `None` if there is no ending, or re-raises errors and cancellations in a controlled order.

**Call relations**: Both `tail_frames` and `_poll_status` use this wrapper instead of calling `turn_status_frame` directly. It protects the stream’s cleanup path from losing the result of the one read that may prove the turn has already ended or parked.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 2 (_poll_status, tail_frames); 2 external calls (ensure_future, shield).


##### `turn_status_frame`  (lines 117–174)

```
async def turn_status_frame(turn_id: UUID, billing_url: str | None=None) -> LiveFrame | None
```

**Purpose**: Turns the stored state of a turn into the stream frame that should end the live view. It returns a terminal frame for completed turns, a parked frame for paused turns, or nothing while the turn is still active.

**Data flow**: It receives a turn id and optional billing URL. It opens a workspace database transaction, reads the turn row, and checks its status. If the row has a stored terminal payload, it validates that payload and returns it as a `Terminal` frame. If the turn is not parked, it returns `None`. If it is parked, it checks likely reasons in a useful order: revoked seat access, insufficient balance, spending caps, and finally a generic pause message. The returned `Parked` message is computed fresh, not copied from an old stored string.

**Call relations**: `_read_status_frame` calls this whenever the streamer needs the durable truth. It reaches into billing, seat admission, spending-cap logic, and the turn and conversation tables so the live stream can explain why a turn is paused in words the user can act on.

*Call graph*: called by 1 (_read_status_frame); 11 external calls (__init__, __init__, __init__, __init__, model_validate, select, applicable_caps_absent, balance_refusal_message, read_headroom, workspace_tx (+1 more)).


##### `HubTailer.tail`  (lines 187–190)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Provides the public tailing interface bound to one hub instance. Callers use it to open a scoped stream of frames for a turn without knowing the details of the hub and database polling.

**Data flow**: It receives a turn id and optional resume cursor. It calls `tail_frames` with the stored hub and billing URL, then wraps the async generator in a closing context so leaving the caller’s block also closes the stream. The result is an asynchronous stream that yields cursor-and-frame pairs.

**Call relations**: This is the method other surface code is meant to call. It hands off the real work to `tail_frames`, while `aclosing` makes sure the background pump and poller are cleaned up when the caller is done.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


##### `HubTailer.latest_activity`  (lines 192–193)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Asks the hub for the most recent activity known for a turn. This lets a surface show or reason about the latest live activity without opening the full frame stream.

**Data flow**: It receives a turn id and forwards that request to the hub stored on the `HubTailer`. It returns the hub’s latest activity record, or `None` if the hub has no activity for that turn.

**Call relations**: This is a small convenience method on the same adapter as `tail`. Surface code can use it when it only needs the current activity snapshot, while `tail` is used when it needs the full ongoing stream.


### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `live turn streaming and subscription`

This file solves the problem of live fan-out across multiple running server processes. As an agent works, it produces small “frames” such as text deltas, tool activity, cost ticks, or a final terminal signal. Those frames need to reach whoever is watching, even if the publisher and watcher are in different processes. Redis Streams act like a short-lived message log: one stream is created per turn, publishers append frames, and subscribers read forward from a cursor, like following a bookmark in a notebook.

The important idea is that these live frames are useful but not the source of truth. If Redis trims or expires old frames, the final answer still lives elsewhere in the turn record. Losing a live frame may mean the viewer redraws from a safer place, not that the answer is lost.

The file also protects against a subtle async problem. An asyncio Redis client is tied to the event loop that created it, and this system has publishers and subscribers on different loops. So RedisStreamHub keeps a separate Redis client per running event loop instead of sharing one client everywhere.

Frames are converted to a simple JSON “wire” form before entering Redis, then rebuilt when read back. Subscribers first replay retained entries after their cursor, then wait for new ones. Timeouts while waiting are treated as normal quiet periods, not errors.

#### Function details

##### `frame_payload`  (lines 76–82)

```
def frame_payload(frame: HubFrame) -> dict[str, object]
```

**Purpose**: Turns one live frame into a plain JSON-friendly package with two parts: a kind label and the frame’s data. This is needed because Redis stores simple fields, not Python objects.

**Data flow**: It receives a HubFrame object, such as a text update or activity notice. It chooses the right kind name for that frame and asks the frame to produce JSON-safe data. For Activity frames, it uses a compatibility shape that looks like a tool activity. The result is a dictionary ready to be turned into JSON and written to Redis.

**Call relations**: RedisStreamHub.publish calls this just before adding a frame to a Redis Stream. It is the outbound translator: publish gives it an in-memory frame, and it hands back the small wire package that Redis can carry.

*Call graph*: called by 1 (publish); 2 external calls (__init__, model_dump).


##### `frame_from_payload`  (lines 85–95)

```
def frame_from_payload(payload: dict[str, object]) -> HubFrame
```

**Purpose**: Rebuilds a live frame from the JSON-style package read out of Redis. It is the inbound translator that lets subscribers receive normal HubFrame objects instead of raw stored data.

**Data flow**: It receives a dictionary containing a kind label and data. If the kind is an older-style tool or skill activity, it turns that into a readable Activity message. Otherwise it checks that the kind is known, validates the data against the matching frame type, and returns the reconstructed frame. If the kind is unknown, it raises an error instead of guessing.

**Call relations**: RedisStreamHub.subscribe calls this for each stream entry it yields to a watcher. RedisStreamHub.latest_activity also calls it when it finds an activity entry while scanning recent frames. In both cases, this function converts Redis wire data back into the project’s live-frame objects.

*Call graph*: called by 2 (latest_activity, subscribe); 2 external calls (__init__, cast).


##### `_stream_id`  (lines 98–100)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis Stream entry id into numbers so two ids can be compared safely. Redis ids look like a timestamp plus a sequence number, such as “12345-0”.

**Data flow**: It receives an entry id string. It splits the id into the millisecond timestamp part and the sequence part, then turns both into integers. It returns a pair of numbers that normal comparison can use.

**Call relations**: RedisStreamHub.covers uses this when deciding whether a saved cursor still points inside the retained Redis Stream. It helps answer: “Can we resume from this bookmark, or has Redis already trimmed past it?”

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 103–112)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from Redis’s XREAD response and checks that the response has the expected shape. This prevents the code from silently misreading Redis data if the protocol format is different than expected.

**Data flow**: It receives the raw result from a Redis stream read. If the result is empty, it returns an empty list. If the result is not the expected list shape, it raises a TypeError. Otherwise it pulls out and returns the list of entries for the stream.

**Call relations**: RedisStreamHub.subscribe calls this after each Redis XREAD. Subscribe then loops over the cleaned entry list, decodes each frame, and yields it to the caller.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 128–134)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the current asyncio event loop, creating one if needed. An event loop is the async task runner; Redis clients cannot safely be shared across different loops here.

**Data flow**: It looks up the currently running event loop. If this hub already has a Redis client for that loop, it returns it. If not, it creates a new Redis client from the configured URL, stores it under that loop, and returns the new client.

**Call relations**: Publish, subscribe, covers, and latest_activity all call this before talking to Redis. It is the shared doorway to Redis, but it keeps separate doorways for separate async loops so publisher and subscriber tasks do not trip over loop-bound connection state.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 136–137)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name for a specific turn. Each turn gets its own stream so its live frames stay separate from every other turn.

**Data flow**: It receives a turn UUID. It combines a fixed prefix with that UUID and returns the Redis key name to use for that turn’s stream.

**Call relations**: Publish, subscribe, covers, and latest_activity all call this before reading or writing Redis. It gives every operation the exact Redis stream key for the turn being discussed.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 139–146)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: Adds one live frame to the Redis Stream for a turn and returns the new stream cursor. Callers use this when they want watchers to see a new piece of live progress.

**Data flow**: It receives a turn id and a HubFrame. It builds the stream name, converts the frame into wire data, serializes that data as compact JSON, and appends it to Redis. In the same Redis pipeline, it also refreshes the stream’s expiry time. It returns the Redis entry id, which can be used later as a cursor.

**Call relations**: This is the sending side of the hub. It relies on _stream to choose the right Redis key, _client to get the right Redis connection for the current loop, and frame_payload to prepare the frame for storage. Subscribers later read the entry that publish wrote.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 148–172)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: Continuously reads live frames for a turn, starting after a given cursor, and yields them one by one. This is how a surface, such as a UI or API stream, follows a turn’s live output.

**Data flow**: It receives a turn id and an optional cursor. It starts reading from that cursor, or from the beginning if none is provided. It first tries a normal read for already-retained entries, then uses a blocking read that waits briefly for new ones. For every Redis entry found, it updates the cursor, decodes the stored JSON into a HubFrame, and yields the new cursor plus the frame. If Redis times out while waiting, it simply tries again.

**Call relations**: This is the receiving side of the hub. It uses _stream and _client to read the right Redis Stream, _stream_entries to normalize Redis responses, and frame_from_payload to turn stored data back into live frames. It pairs with publish: publish writes frames, and subscribe tails them.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 174–180)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether Redis still has enough stream history to resume from a given cursor. This helps decide whether a reconnecting watcher can continue smoothly or must redraw from a safer source.

**Data flow**: It receives a turn id and a cursor. If the cursor is empty, it returns false. Otherwise it reads the oldest retained entry in that turn’s Redis Stream. If the stream is gone or empty, it returns false. If the oldest retained entry is at or before the cursor, it returns true, meaning the cursor is still covered by the retained stream.

**Call relations**: This function uses _stream to find the turn’s Redis key, _client to inspect Redis, and _stream_id to compare Redis entry ids. It supports reconnect logic around subscribe by answering whether cursor-based replay is still safe.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


##### `RedisStreamHub.latest_activity`  (lines 182–214)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Finds the most recent activity-style frame for a turn, such as a tool call or skill load, without scanning the whole stream. This gives callers a quick answer to “what does this turn seem to be doing right now?”

**Data flow**: It receives a turn id. It reads recent stream entries newest-first, up to a fixed limit. For each entry, it parses the stored JSON and checks only the kind label first. If the entry is an activity kind, it decodes and returns that Activity. If the stream is empty, gone, or no activity appears within the recent limit, it returns None.

**Call relations**: This function uses _stream and _client to read recent Redis entries, then calls frame_from_payload only when it has found an activity entry worth decoding. Unlike subscribe, it is not trying to stream every frame; it is a bounded peek for status polling.

*Call graph*: calls 3 internal fn (_client, _stream, frame_from_payload); 2 external calls (loads, cast).


### `core/src/ufo/hub.py`

`io_transport` · `main loop / live stream delivery`

A running turn can produce many small updates: bits of text, tool activity, cost totals, delivered replies, and eventually a final frame or a parked message. This file defines the shapes of those updates and the "hub" that fans them out to anyone watching. Think of it like a train station announcement board with a short rewind buffer: people already watching hear announcements live, and someone who briefly steps away can replay the missed announcements if they still fit in the buffer.

The `Hub` protocol describes the contract: publish a frame, subscribe from a cursor, ask whether a cursor is still covered by the stored history, and peek at the latest activity. `InProcessHub` is the built-in version that works inside one Python process. It stores one stream per turn, protected by a lock so different asynchronous event loops and threads do not corrupt the same data.

Publishing never waits for slow subscribers. Each subscriber has a bounded queue; if it fills, the oldest waiting frame is dropped for that subscriber. The publisher keeps moving. The hub also keeps a bounded replay ring per turn. Terminal and parked frames end the live stream, and finished streams are cleaned up when nobody is listening. One special case matters: subagent activity is only mirrored onto an already-live root stream, so late background child activity does not accidentally recreate and pin an ended stream in memory.

#### Function details

##### `Hub.publish`  (lines 146–146)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This is the interface promise for adding one new live frame to a turn's stream. Code that has produced a new update uses it to broadcast that update and receive the cursor that marks its position.

**Data flow**: It takes a turn id and a frame, such as a text update or terminal result. An implementation appends that frame to the turn's stream, sends it to subscribers, and returns a cursor string that callers can store or pass back later.

**Call relations**: The protocol method is called by the queue code when it needs to commit a failed terminal update. In practice, concrete hubs such as `InProcessHub.publish` provide the real behavior behind this promise.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 148–148)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This is the interface promise for watching a turn's live stream. A caller can give the last cursor it saw, and the hub should replay newer stored frames before switching to live updates.

**Data flow**: It takes a turn id and an optional cursor. An implementation finds frames after that cursor, yields them in order, then keeps yielding new frames as they arrive.

**Call relations**: The hub-tail surface code calls this while pumping frames to a user-facing stream. Concrete implementations, especially `InProcessHub.subscribe`, decide how replay and live delivery are made seamless.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 150–150)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for checking whether a reconnect can safely resume from a cursor. It answers whether the hub still has enough history to avoid a gap.

**Data flow**: It takes a turn id and cursor. An implementation compares that cursor with the retained replay buffer and returns true if frames after that cursor can still be replayed, otherwise false.

**Call relations**: The tailing code calls this before deciding whether to resume from the live hub or fall back to a more durable redraw path. `InProcessHub.covers` is the local in-memory implementation.

*Call graph*: called by 1 (tail_frames).


##### `Hub.latest_activity`  (lines 152–152)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This is the interface promise for quickly asking what a running turn appears to be doing right now. It is meant for status reads that do not want to open a full subscription.

**Data flow**: It takes a turn id. An implementation searches recent retained frames for the newest activity summary and returns it, or returns nothing if there is no useful current activity.

**Call relations**: This method completes the hub contract alongside publishing, subscribing, and cursor checks. The in-process implementation provides the actual recent-frame search.


##### `_offer`  (lines 155–158)

```
def _offer(queue: asyncio.Queue[tuple[str, HubFrame]], item: tuple[str, HubFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber's queue without ever blocking the publisher. If the queue is already full, it makes room by discarding that subscriber's oldest queued frame.

**Data flow**: It receives a subscriber queue and one cursor/frame pair. If the queue has no space, it removes one old item, then immediately inserts the new item; it returns nothing and only changes the queue.

**Call relations**: `InProcessHub.publish` schedules this helper on each subscriber's event loop. That keeps cross-thread delivery safe while preserving the rule that publishing should not wait for a slow reader.


##### `InProcessHub._stream`  (lines 205–215)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This internal helper gets the live state object for one turn, creating it if needed. It is the place where a turn's replay buffer, subscriber list, and cursor count are first assembled.

**Data flow**: It receives a turn id and reads the hub's dictionaries while the caller holds the lock. If a stream already exists, it returns it; otherwise it creates a new `_TurnStream` with a bounded `deque` replay buffer and starts its sequence from the last remembered mark.

**Call relations**: `InProcessHub.publish` uses this when it needs somewhere to append a frame, and `InProcessHub.subscribe` uses it when a watcher attaches. Because it creates shared mutable state, callers are expected to use it only while the hub lock is held.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 217–236)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This adds a new frame to a turn's live stream, stores it for possible replay, and fans it out to current subscribers. It is designed so a slow or broken subscriber cannot slow down the running turn.

**Data flow**: It takes a turn id and frame. Under a lock, it finds or creates the stream, assigns the next cursor, stores the frame in the replay ring, snapshots the current subscribers, and marks the stream ended if the frame is terminal or parked. After releasing the lock, it schedules delivery of the frame to each subscriber queue and returns the cursor.

**Call relations**: It relies on `_stream` to get the per-turn state. It hands actual queue insertion to `_offer`, scheduled on each subscriber's own event loop, so publishing can be called even when subscribers live on different loops.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 238–265)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This lets a caller watch one turn from a given cursor onward. It first replays any retained missed frames, then waits for new live frames.

**Data flow**: It takes a turn id and optional cursor, creates a bounded queue for new frames, records the current event loop, and registers that queue as a subscriber while holding the lock. It snapshots buffered frames newer than the cursor, yields that replay, then yields fresh queue items forever until the caller stops; when the caller leaves, it unregisters the queue and may clean up the stream.

**Call relations**: It calls `_stream` to get or create the turn stream and uses `asyncio.Queue` plus the running event loop so `publish` can later deliver frames safely. It is the concrete behavior used behind the `Hub.subscribe` contract that surface tailing code consumes.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 267–275)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still reaches back far enough for a cursor. It helps a reconnecting viewer know whether it can continue smoothly or must redraw from durable state.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, the stream is missing, or no replay buffer remains, it returns false; otherwise it compares the requested cursor with the earliest retained cursor and returns whether the cursor is still within range.

**Call relations**: This is the concrete implementation of the `Hub.covers` promise. The surface tailing flow uses that answer to choose between gapless hub replay and a fallback path.


##### `InProcessHub.latest_activity`  (lines 277–294)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This quickly finds the newest recent activity summary for a turn without opening a live subscription. It is meant for lightweight status checks, not for reconstructing a whole stream.

**Data flow**: It receives a turn id, looks up that turn's retained buffer under the lock, and scans backward through only a limited number of recent frames. If it finds an `Activity` frame, it returns it; if the stream is absent or recent frames contain no activity, it returns nothing.

**Call relations**: This is the concrete implementation of the `Hub.latest_activity` promise. It uses a bounded reverse scan so repeated status polling does not become expensive on long text-heavy streams.

*Call graph*: 1 external calls (islice).


### `core/src/ufo/loop/steps.py`

`domain_logic` · `trusted surface reads after or during workflow execution`

A workflow engine records many internal steps as it runs. Those raw records are useful to the engine, but they are not ideal for a user-facing or trusted boundary because they mix engine details with typed Python objects. This file acts like a translator: it reads the durable step history from DBOS, then reshapes it into `TurnStep` objects, which are the simpler form expected by the surface layer.

The main class, `DurableTurnSteps`, is given a `DBOSClient`, which is the connection to DBOS, the system that stores workflow execution history. When asked to read a workflow, it first fetches all recorded steps. It then makes one important first pass: whenever a model stream result contains tool calls, it remembers each tool call ID and its human-readable name. This matters because later tool-result steps may only point back to the call ID, so the file needs that small lookup table to label the tool step clearly.

Next, it walks through each recorded step in order and decides what kind of step it is: a model round, a tool call result, or ordinary workflow work. It also converts millisecond timestamps into timezone-aware dates and computes a non-negative duration when both start and finish times are present. Without this file, readers of workflow history would have to understand DBOS’s raw internal records instead of seeing a clean, consistent turn timeline.

#### Function details

##### `DurableTurnSteps.read`  (lines 19–58)

```
async def read(self, workflow_id: str) -> tuple[TurnStep, ...]
```

**Purpose**: Reads the stored step history for one workflow and turns it into a clean ordered list of `TurnStep` objects. Someone would use this when they need to show or inspect what happened during a turn without exposing raw DBOS records.

**Data flow**: It takes a workflow ID and uses the stored DBOS client to fetch that workflow’s recorded steps. It first learns tool names from model stream outputs, then revisits every step to label it as a model step, tool step, or workflow step, convert timestamps, and calculate duration. It returns an immutable tuple of `TurnStep` records and does not change the workflow itself.

**Call relations**: This is the public reading path for the class. While building each surface step, it calls `_timestamp` to turn raw millisecond times into normal date-time values, and it creates `TurnStep` objects as the final boundary shape handed back to callers.

*Call graph*: calls 1 internal fn (_timestamp); 1 external calls (__init__).


##### `DurableTurnSteps._timestamp`  (lines 61–62)

```
def _timestamp(epoch_ms: int | None) -> datetime | None
```

**Purpose**: Converts a stored timestamp in milliseconds into a timezone-aware UTC `datetime`. It also safely preserves missing timestamps as `None`.

**Data flow**: It receives either an integer millisecond timestamp or no value. If there is no value, it returns `None`; otherwise it divides by 1000 to get seconds and asks Python’s date-time library to build a UTC time. Nothing outside the function is changed.

**Call relations**: `DurableTurnSteps.read` uses this helper whenever it copies start and completion times from DBOS records into `TurnStep` objects. Keeping the conversion here makes the main reading logic easier to follow and keeps timestamp handling consistent.

*Call graph*: called by 1 (read); 1 external calls (fromtimestamp).


### Artifact downloads
Signed artifact download routes serve shared files and refresh expired links for authorized workspace members.

### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file is the guarded doorway for shared file bytes. A shared artifact is not served just because someone knows its path; the URL must include a valid signature, like a tamper-proof stamp saying which file, workspace, expiry time, and optional image preview are allowed. Without this route, shared files from chat, web pages, or other surfaces would either be unreachable or would need a less safe delivery path.

The main route checks the signed URL first. If the signature is wrong, it refuses the request. If the signature is valid but expired, it tries one recovery path: if the browser has the normal portal session cookie and that user is a member of the workspace that owns the artifact, it redirects them to a freshly signed URL. This makes old links in team conversations “self-heal” for the right people, while staying dead for outsiders.

When the grant is valid, the route reads the file from the workspace blob store, which is the project’s file storage for a workspace. Normal downloads are streamed in chunks, so a large file does not have to be loaded into memory all at once. Image previews are stricter: they are only returned if the bytes match the preview claim’s expected image type and size. Successful file responses are briefly cacheable because the exact signed URL is already the permission token; refusals and redirects are never cached.

#### Function details

##### `download`  (lines 59–117)

```
async def download(request: Request, artifact_id: str, filename: str, exp: str='', sig: str='', preview: str='', workspace: Annotated[str, Query(alias='ws')]='') -> Response
```

**Purpose**: This is the HTTP endpoint that serves an artifact file or a validated image preview. It protects the bytes by requiring a signed URL, then streams the file only if the signature, workspace, and stored blob all line up.

**Data flow**: A browser or client sends a path containing the artifact id and filename, plus query values such as expiry time, signature, preview data, and workspace id. The function reads the blob store and signing secret from the application, verifies the URL claims, checks that the named blob exists in the claimed workspace, and then either returns a small validated preview or streams the full file as a download. If the URL is invalid, missing workspace scope, points to a missing blob, or asks for an invalid preview, it returns an error instead of bytes.

**Call relations**: FastAPI calls this function when a request matches the artifact download path. It relies on the artifact URL verifier to decide whether the request is allowed. If the URL has only expired, it hands the request to `_refreshed_for_member` to see whether a logged-in teammate can get a new link. For successful downloads it asks the media helper for the content type, may ask the image preview validator to safely produce preview bytes, and finally returns either a normal response or a streaming response.

*Call graph*: calls 1 internal fn (_refreshed_for_member); 9 external calls (now, HTTPException, Response, StreamingResponse, artifact_media_type, verify_artifact_url, validated_image_preview, ws, quote).


##### `_refreshed_for_member`  (lines 120–171)

```
async def _refreshed_for_member(request: Request, claims: ArtifactClaims, secret: str) -> RedirectResponse
```

**Purpose**: This function gives expired artifact links a safe second chance for logged-in members of the owning workspace. It does not serve the file itself; it creates a new signed URL and redirects the browser there.

**Data flow**: It receives the current request, the expired-but-authentic artifact claims, and the signing secret. It reads the session cookie, verifies who the user is, checks the database to confirm that the user is a member of the workspace and that the artifact belongs to that workspace, then mints a fresh artifact URL with a new expiry time. If any check fails, it raises the refusal response instead of creating a new grant.

**Call relations**: `download` calls this only after the artifact URL verifier says the link was genuine but expired. This function then uses the session verifier, workspace database transaction, artifact expiry helper, and URL minting helper to turn a stale link into a fresh redirect. When the browser is not signed in or is not allowed, it delegates the final response choice to `_refusal`.

*Call graph*: calls 1 internal fn (_refusal); called by 1 (download); 10 external calls (now, RedirectResponse, or_, select, verified_claims, workspace_tx, artifact_url_expiry, mint_artifact_url, ws, UUID).


##### `_refusal`  (lines 174–179)

```
def _refusal(request: Request) -> HTTPException
```

**Purpose**: This function builds the response used when an expired artifact link cannot be refreshed. For a normal web browser it points the user toward sign-in; for other clients it returns a plain forbidden error.

**Data flow**: It receives the request and looks at the Accept header to see whether the client appears to want an HTML page. If so, it quotes the current artifact URL and puts it into the login URL as a return target, then raises a redirect-style HTTP exception. Otherwise it raises a 403 forbidden HTTP exception with the expired-link message.

**Call relations**: `_refreshed_for_member` calls this whenever the user is missing, the session cannot be trusted, the workspace id is bad, the user is not a member, or the artifact is not owned by that workspace. It uses URL quoting so the original link can safely travel through the login page and come back after sign-in.

*Call graph*: called by 1 (_refreshed_for_member); 2 external calls (HTTPException, quote).


### Browser callback bridges
Callback pages and provider routes complete external connection flows and guide users back from browser-based OAuth or install steps.

### `core/src/ufo/sdk/callback_page.py`

`io_transport` · `request handling`

When someone connects a tool, installs an app, or finishes a consent step, they often leave the original place they were working, such as Slack or a command-line flow, and end up in a browser tab. This file creates the simple final page for that moment. Without it, the person could finish the provider step and be left staring at a blank or confusing browser response.

The page is intentionally tiny. It has a headline, an optional detail line, an optional link back to the conversation, and an optional small script that tries to close the tab after a short delay. The tab-closing script is only a convenience: browsers often refuse to close tabs that were not opened by a script, so the reliable fallback is the visible link.

The file also defines fixed text such as “You can close this page,” the path to the product logo, and a compact HTML template. The logo is loaded from the same server instead of being embedded directly, keeping the page small. User-facing text and link values are escaped before being inserted into the page, which means special characters are treated as text rather than accidentally becoming HTML. The result is returned as an HTML HTTP response ready for a route handler to send to the browser.

#### Function details

##### `callback_page`  (lines 70–90)

```
def callback_page(*, headline: str, detail: str='', link: PageLink | None=None, status: int=200, close: bool=False) -> HTMLResponse
```

**Purpose**: Builds the browser page shown at the end of a connection, install, or callback flow. A caller gives it the message to show, and it returns a complete HTML response that can be sent directly to the person’s browser.

**Data flow**: It receives a headline, optional detail text, an optional PageLink with a label and destination URL, an HTTP status code, and a flag saying whether the page should try to close itself. It safely escapes the headline, detail, link label, and link URL so they cannot accidentally become active HTML. It fills those safe values into the shared page template, adds link styling only when there is a link, adds the close script only when requested, and wraps the finished HTML in an HTMLResponse with the requested status code.

**Call relations**: Callback or install routes use this function when they need to answer the final browser return leg. Inside, it relies on html.escape to make inserted text safe for HTML, then hands the finished page body to ufo.sdk.http.HTMLResponse so the rest of the web stack can send it as a proper HTML reply.

*Call graph*: 2 external calls (escape, HTMLResponse).


### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

When someone connects an outside account, such as through an OAuth flow, the outside provider eventually redirects their browser back to this service with two pieces of information: a sealed state value and a temporary code. This file is the landing place for that redirect. Its main job is to finish the account connection safely, without needing the user to already have a normal web session open.

The `connect_callback` endpoint checks that the required values arrived, asks the installed connect system to verify the sealed state, exchanges the provider code, and records the grant. The sealed state is important: it is like a tamper-proof claim ticket that says which member, agent, and conversation started the connection. If anything is missing, invalid, unavailable, or points to a provider that is not installed, the endpoint returns a clear HTTP error instead of pretending the connection worked.

After a successful connection, the file returns a shared callback page. That page names the connected account and tells the user either that the conversation continues or simply that they can close the page. The second endpoint, `connect_logo`, serves the exact SVG logo used on that page. It lives here because this callback page may be reached without the normal frontend application or login session, so it needs a stable same-origin asset.

#### Function details

##### `connect_callback`  (lines 38–64)

```
async def connect_callback(state: str='', code: str='') -> HTMLResponse
```

**Purpose**: This is the browser return endpoint for a provider OAuth connection. It completes the connection by checking the sealed state, using the provider code, and then showing the user a simple success page.

**Data flow**: The browser sends in `state` and `code` query values. The function first gets the installed connect flow, then rejects the request if either value is missing. It passes the state and code to the connect flow, which verifies and records the connection. If that succeeds, it builds a friendly account name from the recorded provider label and account label, then returns an HTML page telling the user the account is connected and whether the conversation will continue.

**Call relations**: FastAPI calls this function when a request reaches `/v1/connect/callback`. The function asks `installed_connect_flow` for the object that knows how to finish the connection, relies on that flow to validate and complete the handoff, and finally hands the display work to `callback_page`. If the setup is unavailable or the incoming data is wrong, it raises `HTTPException` so FastAPI can turn the problem into an HTTP error response.

*Call graph*: 3 external calls (HTTPException, installed_connect_flow, callback_page).


##### `connect_logo`  (lines 68–76)

```
async def connect_logo() -> Response
```

**Purpose**: This serves the UFO SVG logo used by the callback page. It gives the page a stable logo URL even when the normal frontend build is not available.

**Data flow**: A browser requests the logo path. The function reads the SVG file from disk, wraps those bytes in an HTTP response with the image type `image/svg+xml`, and adds a long-lived cache header so browsers can keep using the same file without asking again.

**Call relations**: FastAPI calls this function when a request reaches `/v1/connect/logo.svg`. It does not call the frontend or any session-based service; it directly creates a `Response` containing the stored logo file because callback pages need this asset on their own.

*Call graph*: 1 external calls (Response).


### `extensions/pipedream/ufo_ext_pipedream/provider.py`

`io_transport` · `request handling during connector OAuth consent`

OAuth is the common “let this app access my account” flow used by services like Gmail. ufo expects that flow to start with a simple web address and later return with a code. Pipedream is different: before sending the user to its hosted connection page, ufo must first ask Pipedream for a temporary Connect token. This file fills that gap. It gives ufo a normal-looking provider object, but the first URL it returns points back to this extension’s own `/ext/pipedream/oauth` route. That route then does the slower Pipedream work and redirects the browser onward. Think of it like a hotel front desk: ufo sends the guest to the desk, the desk prepares the right keycard, then sends the guest to the correct room. The route also receives the browser after the user approves or fails the connection. On success, it finds the newest Pipedream account for this exact workspace and saved state, then sends that account id back to ufo core. The later exchange step checks that the account belongs to the expected Pipedream app before accepting it. This matters because overlapping browser flows must not accidentally attach the wrong user’s account or the wrong provider’s account.

#### Function details

##### `PipedreamOAuthProvider.authorize_url`  (lines 51–53)

```
def authorize_url(self, state: str, redirect_uri: str) -> str
```

**Purpose**: This starts the connection flow from ufo’s point of view. Instead of sending the browser straight to Pipedream, it sends the browser to this extension’s bridge route so the extension can first create the needed Pipedream token.

**Data flow**: It receives ufo’s sealed state value and the callback URL that ufo core wants the browser to return to. It extracts the callback’s web origin, packages the provider name, state, and callback into query parameters, and returns a bridge URL under `/ext/pipedream/oauth`. Nothing is stored or changed here; it only builds the next address for the browser.

**Call relations**: ufo’s connect registry calls this when it needs an authorization URL for a Pipedream-backed connector. It uses `_origin` to make sure the callback has a usable scheme and host, then hands the browser off to `oauth_route`, where the asynchronous Pipedream token work can happen.

*Call graph*: calls 1 internal fn (_origin); 1 external calls (urlencode).


##### `PipedreamOAuthProvider.exchange`  (lines 55–69)

```
async def exchange(self, code: str, _redirect_uri: str, workspace_id: UUID, state: str) -> OAuthAccount
```

**Purpose**: This finishes the connection after ufo core receives an account id from the bridge route. It verifies that the Pipedream account really belongs to this provider’s app before returning the account information ufo will bind to the grant.

**Data flow**: It receives the code value, which in this design is the Pipedream account id, plus the workspace id and state. It rebuilds the Pipedream external user id from the workspace and state, asks Pipedream for that exact connected account, checks that the account’s app matches this provider, and tries to fetch a friendly label. It returns an `OAuthAccount` containing the account id and optional label; if the app does not match, it raises an error instead of accepting the account.

**Call relations**: ufo core calls this after the browser has come back through `oauth_route` with a code. It relies on Pipedream client helpers to locate and verify the account, then hands a clean account record back to core so the grant can be recorded without exposing or storing the underlying token.

*Call graph*: 4 external calls (__init__, PipedreamError, connection_user_id, pipedream_client).


##### `oauth_route`  (lines 72–120)

```
async def oauth_route(ctx: ExtensionContext, request: Request) -> Response
```

**Purpose**: This is the browser-facing bridge for both halves of the Pipedream connection flow. It either starts the Pipedream consent page or receives the browser after consent and redirects back to ufo core.

**Data flow**: It reads query parameters from the incoming request: the provider, sealed state, callback URL, and optional outcome marker. If state or callback is missing, it returns a bad request response. If the provider is unknown, it returns a not found response. If Pipedream reports success, it finds the newest account for this workspace-and-state-specific external user and redirects to the original callback with the state and account id. If Pipedream reports failure, it returns a clear error page. If there is no outcome yet, it creates a Pipedream Connect token with success and error redirects pointing back to this same route, builds the hosted Pipedream Connect Link for the provider’s app, optionally adds a custom OAuth app id from the environment, and redirects the browser there.

**Call relations**: The flow reaches this route first from `PipedreamOAuthProvider.authorize_url`. During the start leg, it calls `_origin` to build safe return URLs and calls the Pipedream client to mint a Connect token. During the return leg, it again uses the Pipedream client to resolve the connected account, then sends the browser back to ufo core, which will later call `PipedreamOAuthProvider.exchange` to verify and bind the account.

*Call graph*: calls 1 internal fn (_origin); 5 external calls (Response, get, connection_user_id, pipedream_client, urlencode).


##### `_origin`  (lines 123–127)

```
def _origin(url: str) -> str
```

**Purpose**: This small helper extracts the scheme and host from a URL, such as turning `https://example.com/path` into `https://example.com`. It also rejects callback URLs that are not real HTTP or HTTPS web addresses.

**Data flow**: It receives a URL string, parses it, checks that it has an `http` or `https` scheme and a host name, and returns only the origin part. If the URL is missing those required pieces, it raises an error instead of producing an unsafe or unusable bridge address.

**Call relations**: `PipedreamOAuthProvider.authorize_url` uses this when building the first bridge URL, and `oauth_route` uses it when building Pipedream’s success and error redirect URLs. In both places, it protects the flow from being built around a malformed callback address.

*Call graph*: called by 2 (authorize_url, oauth_route); 1 external calls (urlparse).
