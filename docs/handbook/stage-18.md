# Live Updates, Cancellation, Replies, and Surface Delivery  `stage-18`

This stage is the system’s live “delivery desk” during and just after a conversation turn. While the assistant is working, core/src/ufo/hub.py sends small progress messages, called frames, to any open user interface and keeps a short memory so reconnecting clients can catch up. core/src/ufo/surfaces/hub_tail.py is the watcher for one turn: it combines that live feed with a database check so it knows whether the work is still running, paused, or finished.

If the system is spread across several server processes, extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py uses Redis Streams, a fast shared message log, to pass those live frames between them without saving every temporary update in the main database.

Stopping work is handled carefully. core/src/ufo/surfaces/stop.py checks the requested turn, asks cancellation to happen, alerts listeners, and may point the user to a follow-up turn. core/src/ufo/turns/cancellation.py performs the shared safe stop.

For what users see, core/src/ufo/loop/replies.py extracts private reply blocks from model output and hides control tags. core/src/ufo/turns/activity.py turns raw tool activity into safe, friendly status labels.

## Files in this stage

### Live Surface Controls
Client-facing surface endpoints let viewers follow a turn's live output and let members request a safe stop.

### `core/src/ufo/surfaces/hub_tail.py`

`orchestration` · `request handling / live stream`

A “turn” here is a unit of work whose progress is streamed to a caller, like watching words appear while an assistant is responding. The hard problem is that live messages can be missed: a client may connect after the turn started, reconnect after a network drop, or listen from a different event loop than the one that finished the turn. This file solves that by racing two sources into one stream. One source is the hub, which is the fast in-memory broadcaster for live frames. The other source is a repeated database poll, which is slower but durable and can see the final stored state even if the live message was missed.

The main generator, `tail_frames`, yields frames until it sees either a final result (`Terminal`) or a pause (`Parked`). The pause matters because a parked turn is not finished, but the live stream should still stop and tell the caller why it paused. The file also checks the current reason for a pause, such as a revoked seat, low balance, or spending cap, instead of relying on an old stored message that may no longer be true.

Think of it like following a delivery truck: the hub is the live GPS signal, while the database poll is calling the dispatch office. GPS is faster, but dispatch has the official answer if the signal was missed.

#### Function details

##### `tail_frames`  (lines 34–66)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='', billing_url: str | None=None) -> AsyncGenerator[tuple[str, LiveFrame]]
```

**Purpose**: Streams the live frames for one turn until the turn finishes or pauses. It protects callers from missed live messages by also checking the stored turn state.

**Data flow**: It receives a hub, a turn id, an optional last-seen cursor, and an optional billing URL. It first decides where the hub stream should resume, starts a background hub reader, checks the database once for an already-finished or already-parked turn, then starts a background poller if needed. It yields each frame to the caller and stops once a final or parked frame appears; when the caller is done, it cancels the background work.

**Call relations**: This is the main worker behind `HubTailer.tail`. It asks `Hub.covers` whether the old cursor can still be resumed, starts `_pump` to listen to `Hub.subscribe`, uses `_read_status_frame` for the durable database check, and starts `_poll_status` so a missed ending is still discovered.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, _read_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 69–78)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: Copies live frames from the hub into the shared queue used by `tail_frames`. It ignores arrival-notice frames that are not meaningful output for the caller.

**Data flow**: It receives the hub, the turn id, the resume cursor, and a queue. It subscribes to the hub, reads each incoming cursor and frame, skips `ArrivalQueued` notices, and puts the remaining frames into the queue. If the hub subscription fails, it logs the failure instead of crashing the whole tail.

**Call relations**: `tail_frames` starts this as a background task. `_pump` depends on `Hub.subscribe` for the in-memory live feed and hands usable frames back to `tail_frames` through the queue.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 81–93)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]], billing_url: str | None) -> None
```

**Purpose**: Repeatedly checks the durable turn state until it finds that the turn has finished or paused. This is the safety net for endings that the live hub feed did not deliver.

**Data flow**: It receives a turn id, the shared queue, and an optional billing URL. Every polling interval, it calls `_read_status_frame`; if no ending is found, it sleeps and tries again. If a terminal or parked frame is found, it puts that frame into the queue with an empty cursor and stops. If one poll fails, it logs the error and tries again later.

**Call relations**: `tail_frames` starts this after the initial database check shows the turn is still active. It relies on `_read_status_frame` for the actual database-backed status read and sends its result back into the same queue used by `_pump`.

*Call graph*: calls 1 internal fn (_read_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `_read_status_frame`  (lines 96–114)

```
async def _read_status_frame(turn_id: UUID, billing_url: str | None) -> LiveFrame | None
```

**Purpose**: Reads the stored status of a turn while carefully preserving cancellation behavior. It is designed so a cancellation request does not leave the inner database read half-forgotten in an unclear state.

**Data flow**: It receives a turn id and optional billing URL. It starts `turn_status_frame` as its own async task, waits for that task while shielding it from immediate cancellation, then returns the resulting live frame or `None`. If cancellation happened while waiting, it raises the cancellation after the read has resolved in a controlled way.

**Call relations**: `tail_frames` uses this for the first immediate durable check, and `_poll_status` uses it for repeated checks. It hands off the real decision-making to `turn_status_frame`, adding careful async task behavior around that call.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 2 (_poll_status, tail_frames); 2 external calls (ensure_future, shield).


##### `turn_status_frame`  (lines 117–174)

```
async def turn_status_frame(turn_id: UUID, billing_url: str | None=None) -> LiveFrame | None
```

**Purpose**: Looks in the database and decides whether a turn should produce a stream-ending frame. It returns a final frame for completed turns, a pause frame for parked turns, or nothing for turns still waiting or running.

**Data flow**: It receives a turn id and optional billing URL. It opens a workspace database transaction, reads the turn row, and checks the stored terminal result first. If the turn is parked, it works out the current pause reason by checking seat access, billing balance, and spending caps. It returns a `Terminal`, a `Parked` message with the best current reason, or `None` if there is no stream-ending state yet.

**Call relations**: Only `_read_status_frame` calls this function. It reaches into the database with SQLAlchemy queries, converts stored terminal data through `TerminalFrame.model_validate`, asks `Seats` about membership access, reads billing headroom, and uses `SpendEvaluator` when spending caps may apply.

*Call graph*: called by 1 (_read_status_frame); 11 external calls (__init__, __init__, __init__, __init__, model_validate, select, applicable_caps_absent, balance_refusal_message, read_headroom, workspace_tx (+1 more)).


##### `HubTailer.tail`  (lines 187–190)

```
def tail(self, turn_id: UUID, since: str='') -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]
```

**Purpose**: Provides a clean context-managed way to stream one turn from the process hub. Callers use it without needing to know how the hub feed and database polling are combined.

**Data flow**: It receives a turn id and optional last-seen cursor. It calls `tail_frames` with this tailer’s hub and billing URL, then wraps the async generator in `aclosing` so leaving the caller’s block closes the generator and cancels its background tasks.

**Call relations**: This is the public method on `HubTailer` that callers use. It delegates the real streaming work to `tail_frames` and adds the lifetime wrapper that makes cleanup happen when the caller stops reading.

*Call graph*: calls 1 internal fn (tail_frames); 1 external calls (aclosing).


##### `HubTailer.latest_activity`  (lines 192–193)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Asks the hub for the latest known activity for a turn. This gives callers a quick snapshot of recent live state without opening the full frame stream.

**Data flow**: It receives a turn id, passes it to the hub, waits for the hub’s answer, and returns either an activity object or `None` if the hub has no activity to report.

**Call relations**: This method is a small forwarding point on `HubTailer`. It keeps callers talking to the tailer abstraction instead of importing or depending directly on the hub.


### `core/src/ufo/surfaces/stop.py`

`orchestration` · `request handling`

A “turn” is one running unit of conversation work. This file covers the case where a member presses stop while that work is still running. The important job is to make that stop feel immediate and correct, without damaging a turn that already finished by itself.

`MemberStop` acts like a careful traffic controller. First it looks in the database to confirm that the turn being stopped belongs to the conversation the member is allowed to affect. Without this check, someone could accidentally or maliciously stop the wrong conversation’s work.

Next it calls the shared cancellation routine. That routine is the durable source of truth for ending the turn, so this file does not invent a separate stop path. If the turn was already finished, the stop request becomes a harmless no-op and reports that nothing was newly ended.

If cancellation succeeds, the file asks admission logic whether there is already a pending member message that should be redispatched as a new turn. If so, it publishes an “absorbed” notice for that new turn, so the user interface does not wait forever for confirmation about the pending message. Finally it publishes the cancelled terminal event for the stopped turn, waking any live listeners immediately instead of making them wait until their next check.

#### Function details

##### `MemberStop.stop`  (lines 38–61)

```
async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped
```

**Purpose**: Stops one running turn for a member, but only if that turn belongs to the given conversation in the given workspace. It cancels the turn through the shared cancellation path, starts or identifies a follow-up turn if needed, and tells listeners what happened.

**Data flow**: It receives a workspace ID, conversation ID, and turn ID. It first reads the database to find which conversation owns that turn inside the workspace. If the owner does not match the requested conversation, it raises an error. If the owner matches, it asks the cancellation system to end the turn. If the turn was already ended, it returns a `Stopped` result saying `ended` is false. If cancellation produced a final cancellation frame, it asks admission to redispatch any pending follow-up work. When a new turn is founded, it publishes an absorbed-message notice for that new turn. It then publishes the cancelled terminal notice for the stopped turn and returns a `Stopped` result saying the turn ended, including the new founded turn ID if there is one.

**Call relations**: This method is the main workflow for a member stop request. It uses `workspace_tx` and `sqlalchemy.select` to verify ownership in the database, then hands the actual durable cancellation to `ufo.turns.cancellation.cancel_one_turn`. After that, it coordinates with admission redispatch and publishes hub messages using `Absorbed` and `Terminal`, so other parts of the system and live clients see the follow-up turn and the cancelled ending in the right order. It returns a `Stopped` object for the surface layer to report the result back to the member.

*Call graph*: 6 external calls (__init__, __init__, __init__, select, workspace_tx, cancel_one_turn).


### Live Hub Transport
The live hub and Redis extension publish, replay, and distribute turn progress frames across viewers and server processes.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `request handling and live streaming`

A running assistant turn can produce many short-lived updates: text chunks, tool activity, cost ticks, parking and resume notices, and final signals. This file sends those updates through Redis Streams, which are append-only message logs stored in Redis. Think of each turn as having its own scrolling ticker tape. Writers add frames to the tape, and readers can start from a saved position and keep reading forward.

This matters because live updates need to work across multiple server processes. Without this Redis-backed hub, a viewer connected to one server might miss frames produced by another. The permanent answer still lives elsewhere; these frames are for live display and replay, so losing an old trimmed frame only means the display may need to redraw, not that the system lost the actual result.

The file also translates between in-memory frame objects and a small JSON wire format. Redis stores only simple strings, so each frame is tagged with a kind, such as text delta or terminal, plus its data. The hub keeps one Redis client per asyncio event loop, because async Redis clients are tied to the loop that created them. That prevents subtle cross-thread event-loop errors when workflow code and web-serving code run on different loops.

#### Function details

##### `frame_payload`  (lines 76–82)

```
def frame_payload(frame: HubFrame) -> dict[str, object]
```

**Purpose**: Turns a live frame object into a plain dictionary that can be safely written as JSON. It adds a kind label so a later reader knows what type of frame to rebuild.

**Data flow**: It receives a HubFrame, such as a text update or activity notice. It inspects the frame type, converts the frame fields into JSON-friendly data, and returns a dictionary with a kind and data. Activity frames get a special Redis-facing shape so they remain compatible with the stream format.

**Call relations**: When RedisStreamHub.publish is about to write a frame to Redis, it asks this function to package the frame first. The packaged result is then converted to a JSON string and stored in the Redis Stream.

*Call graph*: called by 1 (publish); 2 external calls (__init__, model_dump).


##### `frame_from_payload`  (lines 85–95)

```
def frame_from_payload(payload: dict[str, object]) -> HubFrame
```

**Purpose**: Rebuilds a live frame object from the plain dictionary stored in Redis. This is what turns the wire format back into something the rest of the application understands.

**Data flow**: It receives a decoded JSON payload with a kind label and data. It checks the kind, validates the data against the matching frame model, and returns the reconstructed HubFrame. Older or alternate activity formats for tools and skills are translated into normal Activity messages.

**Call relations**: RedisStreamHub.subscribe uses this after reading frames from Redis so subscribers receive real frame objects. RedisStreamHub.latest_activity also uses it when it finds an activity-like entry while scanning recent stream entries.

*Call graph*: called by 2 (latest_activity, subscribe); 2 external calls (__init__, cast).


##### `_stream_id`  (lines 98–100)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis Stream entry id into numbers that can be compared reliably. Redis ids look like timestamp-plus-sequence strings, and this helper makes their ordering explicit.

**Data flow**: It receives an entry id string such as a millisecond time followed by a sequence number. It splits the string, converts both parts to integers, and returns them as a pair. That pair can then be compared with another pair to tell which entry came first.

**Call relations**: RedisStreamHub.covers uses this helper when deciding whether a subscriber's saved cursor still points to data that Redis has retained. It supports the resume-or-redraw decision.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 103–112)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from Redis's XREAD response and checks that the response has the expected shape. This prevents the code from silently misreading Redis data if the protocol response format changes.

**Data flow**: It receives the raw batch returned by Redis. If the batch is empty, it returns an empty list. If the batch is not the expected list-style response, it raises an error. Otherwise, it pulls out and returns the entries for the stream.

**Call relations**: RedisStreamHub.subscribe calls this after each Redis read. The subscribe loop then walks through the returned entries, decodes each stored frame, and yields it to the subscriber.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 128–134)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the currently running asyncio event loop. An asyncio event loop is the scheduler that runs async tasks; Redis clients are tied to the loop that created them, so sharing one across loops can break.

**Data flow**: It looks up the current event loop. If this hub already has a Redis client for that loop, it returns it. If not, it creates a new client from the configured Redis URL, stores it for that loop, and returns it.

**Call relations**: All Redis operations in this hub go through this method. Publishing, subscribing, coverage checks, and activity lookups each call it right before talking to Redis, so workflow-side and web-serving-side code get separate safe clients when needed.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 136–137)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name for a particular turn. It gives every turn its own stream so frames from different conversations or runs do not mix.

**Data flow**: It receives a turn UUID. It combines a fixed stream prefix with that UUID and returns the Redis key string used for that turn's live-frame stream.

**Call relations**: Every public hub operation calls this before reading or writing. RedisStreamHub.publish writes to the named stream, RedisStreamHub.subscribe reads from it, RedisStreamHub.covers checks its retained entries, and RedisStreamHub.latest_activity scans it.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 139–146)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: Adds one live frame to the Redis Stream for a turn and returns the new stream position. Publishers use this whenever the running turn has a new update for viewers.

**Data flow**: It receives a turn id and a frame. It chooses the turn's stream name, converts the frame into a JSON string, appends it to Redis, trims the stream to a maximum length, refreshes the stream's expiry time, and returns the Redis entry id as a cursor. The cursor is a bookmark readers can use later.

**Call relations**: This is the write side of the hub. It relies on _stream to find the right Redis key, frame_payload to serialize the frame, and _client to get the correct Redis connection for the current event loop.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 148–172)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: Streams live frames for one turn, starting after a saved cursor or from the beginning if no cursor is given. It lets a viewer catch up on retained updates and then wait for new ones.

**Data flow**: It receives a turn id and an optional cursor. It repeatedly reads entries from that turn's Redis Stream, first without blocking to catch up quickly and then with a short blocking wait when no entries are ready. Each Redis entry's JSON frame is decoded back into a HubFrame, and the function yields the new cursor plus the frame. Timeouts are treated as normal idle moments, so the loop simply tries again.

**Call relations**: This is the read side used by live surfaces. It uses _stream for the Redis key, _client for Redis access, _stream_entries to unpack Redis's response, and frame_from_payload to rebuild each frame before handing it to the caller.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 174–180)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether a saved cursor can still be replayed without a gap. This helps a reconnecting viewer know whether it can resume from its bookmark or should redraw from a more durable source.

**Data flow**: It receives a turn id and cursor. If there is no cursor, or the stream has no retained entries, it returns false. Otherwise, it compares the first retained Redis entry id with the cursor and returns true when the cursor is not older than what Redis still keeps.

**Call relations**: Reconnect logic can call this before subscribing. The method uses _stream to find the stream, _client to read its oldest entry, and _stream_id to compare Redis entry ids in chronological order.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


##### `RedisStreamHub.latest_activity`  (lines 182–214)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Finds the most recent activity-style update for a turn, such as a tool call or skill load, by looking only at a bounded number of recent stream entries. This gives status polling a cheap way to answer “what is it doing now?”

**Data flow**: It receives a turn id. It scans that turn's Redis Stream from newest to older entries, but stops after a fixed limit so it does not repeatedly inspect thousands of text-only frames. For each entry, it reads the JSON payload and checks the kind before decoding. If it finds a tool or skill activity, it returns it as an Activity; if not, it returns None.

**Call relations**: Status views or polling code can call this when they need a recent human-readable activity. It uses _stream and _client to read Redis, and frame_from_payload only for the matching activity entry it wants to return.

*Call graph*: calls 3 internal fn (_client, _stream, frame_from_payload); 2 external calls (loads, cast).


### `core/src/ufo/hub.py`

`io_transport` · `cross-cutting during live turn streaming`

A running agent turn produces many small updates: text chunks, tool-status messages, cost ticks, delivered replies, subagent progress, and finally a terminal result or a parked notice. This file defines the shapes of those live updates and an in-memory “hub” that fans them out to anyone watching the turn. Think of it like a train-station announcement board: publishers post announcements, and every connected display receives them.

The important problem is reconnection. A browser or command-line client may disconnect briefly, especially when handing control to a local tool. Instead of forcing it to redraw everything from permanent storage, the hub keeps a bounded ring buffer, meaning a fixed-size recent-history list that drops the oldest entries when full. Each frame gets a simple increasing cursor number. A subscriber can reconnect with its last cursor and receive only the frames after it.

The hub is deliberately non-blocking. If a subscriber is too slow and its queue fills up, the oldest queued frame for that subscriber is dropped rather than slowing the running turn. Publishing must stay fast. Shared state is protected by a lock because publishers and subscribers may run on different event loops, meaning separate asynchronous execution contexts. When a turn ends, the hub drops retained memory once it is safe to do so.

#### Function details

##### `Hub.publish`  (lines 146–146)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This is the interface promise for adding one live frame to a turn’s stream. A caller uses it when something new has happened and watchers should be told.

**Data flow**: It receives a turn id and a frame of live information. An implementation records and broadcasts that frame, then returns the cursor that marks where the frame landed in the stream.

**Call relations**: The queue code calls this interface when it needs to publish a failed terminal result. Concrete hub implementations, such as InProcessHub.publish, provide the actual storage and fan-out behavior behind this promise.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 148–148)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This is the interface promise for watching a turn’s live stream. A caller uses it to receive missed frames after a cursor and then keep receiving new frames as they are published.

**Data flow**: It receives a turn id and optionally the last cursor the caller already saw. It produces an asynchronous stream of cursor-and-frame pairs, first replaying eligible old frames and then yielding live ones.

**Call relations**: The surface tailing code calls this when it needs to pump hub frames to a user-facing stream. InProcessHub.subscribe is the in-memory implementation of the behavior.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 150–150)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for asking whether a cursor is still covered by the hub’s replay memory. A reconnecting surface uses this to decide whether it can resume smoothly or must fall back to rebuilding from durable state.

**Data flow**: It receives a turn id and a cursor string. It checks whether the retained replay history goes back far enough to include that cursor, and returns true or false.

**Call relations**: The hub-tail surface code calls this before deciding how to continue a stream after reconnect. InProcessHub.covers supplies the in-memory answer.

*Call graph*: called by 1 (tail_frames).


##### `Hub.latest_activity`  (lines 152–152)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This is the interface promise for asking what a running turn appears to be doing right now, without opening a full subscription. It is meant for quick status reads.

**Data flow**: It receives a turn id. An implementation looks through recent retained frames for the newest activity summary and returns it, or returns nothing if none is available.

**Call relations**: No direct caller is listed in the provided graph, but this method belongs to the hub interface so status-related code can ask for a lightweight one-frame view of current activity.


##### `_offer`  (lines 155–158)

```
def _offer(queue: asyncio.Queue[tuple[str, HubFrame]], item: tuple[str, HubFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber’s queue without ever making the publisher wait. If that subscriber is already too far behind, it drops the oldest queued frame to make room.

**Data flow**: It receives an asynchronous queue and a cursor-and-frame item. If the queue is full, it removes one old item, then adds the new item immediately; it returns nothing and only changes the queue contents.

**Call relations**: InProcessHub.publish schedules this helper on each subscriber’s event loop. This keeps cross-thread delivery safe while preserving the rule that publishing should not block on slow readers.


##### `InProcessHub._stream`  (lines 205–215)

```
def _stream(self, turn_id: UUID) -> _TurnStream
```

**Purpose**: This internal helper finds or creates the live stream state for one turn. It is where the hub makes sure each turn has a replay buffer, a subscriber list, and a cursor counter.

**Data flow**: It receives a turn id and reads the hub’s dictionaries while the lock is expected to be held. If a stream already exists, it returns it; otherwise it creates a new _TurnStream with a fixed-size deque replay buffer and starts its cursor sequence from the saved mark for that turn, if any.

**Call relations**: InProcessHub.publish uses this when it needs somewhere to append a new frame. InProcessHub.subscribe uses it when a watcher attaches, so the watcher can be registered and given a replay snapshot.

*Call graph*: called by 2 (publish, subscribe); 2 external calls (__init__, deque).


##### `InProcessHub.publish`  (lines 217–236)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: This adds a new live frame for a turn, stores it for possible replay, and sends it to all current subscribers. It is designed so a slow or disconnected subscriber cannot slow down the running turn.

**Data flow**: It receives a turn id and a frame. Under a lock, it gets the turn stream, assigns the next cursor, saves the frame in the replay ring, snapshots the current subscribers, and marks the stream ended if the frame is terminal or parked. After releasing the lock, it schedules delivery to each subscriber queue and returns the new cursor.

**Call relations**: This is the concrete implementation behind Hub.publish. It calls InProcessHub._stream to get the per-turn state, and it hands each outgoing item to _offer through the subscriber’s event loop so delivery is safe even when publisher and subscriber run on different loops.

*Call graph*: calls 1 internal fn (_stream).


##### `InProcessHub.subscribe`  (lines 238–265)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: This lets a client follow one turn’s stream. It first replays retained frames newer than the caller’s cursor, then waits for new frames as they are published.

**Data flow**: It receives a turn id and optional cursor. It creates a bounded queue for live frames, records the current event loop, registers that queue as a subscriber, and takes a snapshot of replayable frames after the cursor. It yields the replay snapshot, then yields items from the live queue until the subscriber stops; on exit it removes the subscriber and may delete the stream if it is no longer needed.

**Call relations**: This is the concrete implementation behind Hub.subscribe. Surface streaming code calls the interface to receive frames. The method uses InProcessHub._stream to attach to the right turn, and InProcessHub.publish later feeds its queue with live updates.

*Call graph*: calls 1 internal fn (_stream); 2 external calls (Queue, get_running_loop).


##### `InProcessHub.covers`  (lines 267–275)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This answers whether the hub still has enough replay history for a client’s cursor. It prevents a reconnecting surface from assuming it can resume smoothly when the needed old frames have already been dropped.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, or the stream or buffer is missing, it returns false. Otherwise it compares the oldest retained cursor with the requested cursor and returns whether the retained history reaches back far enough.

**Call relations**: This is the concrete implementation behind Hub.covers. The surface tailing flow asks this before deciding whether to resume from the hub replay or rely on a more complete redraw from durable storage.


##### `InProcessHub.latest_activity`  (lines 277–294)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: This gives a quick answer to “what is this turn doing right now?” by looking for the newest retained Activity frame. It avoids opening a live stream just to show a status line.

**Data flow**: It receives a turn id. Under the lock, it finds that turn’s retained buffer and scans backward through only a limited number of recent frames. If it finds an Activity frame, it returns it; if the turn has no stream or no recent activity frame, it returns nothing.

**Call relations**: This is the concrete implementation behind Hub.latest_activity. It uses a bounded reverse scan so repeated status checks do not waste time searching a large replay buffer when no useful activity is likely to be there.

*Call graph*: 1 external calls (islice).


### Visible Reply Shaping
Reply extraction and activity labeling prepare safe, user-facing updates while hiding internal markup and sensitive tool details.

### `core/src/ufo/loop/replies.py`

`domain_logic` · `main loop and live response streaming`

The system allows a model to mark part of its text as a reply to a specific message, using tags like `<reply-to message="..."> ... </reply-to>`. This file is the filter and reader for those tags. Without it, the raw tags could leak to people, reply text could be shown twice, or half-written tags could appear during live streaming.

There are two related jobs here. After a full round of model output is complete, `marked_replies` scans the whole text, finds every fully closed reply span, records the words inside it, and notes which message ID it was aimed at if the ID is valid. It also returns the round text with the reply markup removed, so the conversation record keeps the words but not the instructions.

During live streaming, text arrives in small chunks, and a tag can be split across chunk boundaries. `ReplyRedaction.feed` works like a careful curtain: it immediately lets through normal prose, but holds back anything that might be part of a reply tag or reply span until it knows what it is. Completed reply spans are withheld from the live stream because they will be delivered separately later. Broken or unfinished markup is dropped rather than shown.

#### Function details

##### `marked_replies`  (lines 42–51)

```
def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]
```

**Purpose**: This function reads a completed model response and pulls out the reply sections that were deliberately marked for a member. It also produces a cleaned version of the text with the reply tags removed, so the saved conversation does not contain markup.

**Data flow**: It takes the full text of a completed round. It searches for closed `<reply-to ...>` sections, strips any reply markup from the words inside, ignores empty replies, and turns each valid spoken section into a `MarkedReply` with a message reference when possible. It returns two things: the collected replies in order, and the original text with all reply markup removed.

**Call relations**: This is used after the model has finished speaking, when the system can safely inspect the whole output. For each marked span it asks `_named_message` to turn the written message name into a real UUID when possible, then creates `MarkedReply` records that later code can deliver as member-visible replies.

*Call graph*: calls 1 internal fn (_named_message); 1 external calls (__init__).


##### `_named_message`  (lines 54–58)

```
def _named_message(named: str) -> UUID | None
```

**Purpose**: This small helper checks whether the message name written in a reply tag is a valid UUID, which is a standard unique identifier. If it is not valid, the reply is still kept, but without a trusted message reference.

**Data flow**: It receives the raw text from the `message="..."` part of a reply tag. It trims extra spaces and tries to parse it as a UUID. If parsing works, it returns the UUID; if parsing fails, it returns `None`.

**Call relations**: `marked_replies` calls this while building each `MarkedReply`. Its job is to keep bad or misspelled message IDs from crashing the reply extraction process.

*Call graph*: called by 1 (marked_replies); 1 external calls (UUID).


##### `ReplyRedaction.feed`  (lines 77–99)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This method filters live model output one chunk at a time, so reply markup and reply-only text are not shown in the ordinary stream. It lets safe prose through immediately and holds back anything that might be a tag until more characters arrive.

**Data flow**: It receives the next chunk of streamed text and appends it to text already being held. If it is inside a reply span, it discards text until it finds the closing reply tag, keeping only a possible unfinished closer at the end. If it is outside a reply span, it publishes normal text before an opener, withholds the reply span, or publishes only the part that is definitely not the start of a tag. It returns the newly safe text to show now, and updates its internal `held` and `inside` state for the next chunk.

**Call relations**: This method is called repeatedly while a round is still being streamed. It relies on `_growing_suffix` when waiting for a possible closing tag and on `_settled_chars` when deciding how much ordinary-looking text is safe to release. Later, once the full round exists, `marked_replies` can extract the withheld reply spans for their proper delivery.

*Call graph*: calls 2 internal fn (_growing_suffix, _settled_chars); 1 external calls (find).


##### `_growing_suffix`  (lines 102–107)

```
def _growing_suffix(text: str, token: str) -> str
```

**Purpose**: This helper keeps only the end of some text that could still become a specific token when the next streamed chunk arrives. It prevents the filter from accidentally discarding the beginning of a closing tag split across chunks.

**Data flow**: It receives some current text and a target token, such as the reply closing tag. It checks each possible tail end of the text and finds the longest suffix that matches the start of the token. It returns that suffix, or an empty string if no tail could grow into the token.

**Call relations**: `ReplyRedaction.feed` uses this while it is inside a hidden reply span and has not yet seen the full closing tag. The helper lets `feed` throw away hidden reply content while still remembering enough characters to recognize the closer if it continues in the next chunk.

*Call graph*: called by 1 (feed).


##### `_settled_chars`  (lines 110–119)

```
def _settled_chars(text: str) -> int
```

**Purpose**: This helper decides how much currently held text is definitely ordinary prose and can be published now. It is careful around a trailing `<`, because that character might be the start of a reply tag split across chunks.

**Data flow**: It receives held text from the live stream. If there is no `<`, all of it is safe. If the last `<` and the characters after it could still become an opening or closing reply tag, it reports that only the text before that point is safe. Otherwise, it reports that all the text can be published.

**Call relations**: `ReplyRedaction.feed` calls this when it is outside a reply span and has not found a complete opener. The result tells `feed` what to release to the live stream now and what to keep for the next chunk in case a tag is still forming.

*Call graph*: called by 1 (feed).


### `core/src/ufo/turns/activity.py`

`domain_logic` · `during a user turn when tool activity is being summarized`

When the system uses a tool, the raw call can be technical or sensitive: it may include tool names, arguments, paths, URLs, or IDs. This file creates a safe, plain-language activity label for that tool call, so a user can see progress without seeing the machinery underneath. It is like replacing a kitchen’s detailed prep notes with a simple sign that says “Chopping vegetables.”

The main piece is ActivitySummarizer. It receives a tool call and, optionally, the user’s goal. It builds a small JSON payload containing a shortened version of the goal and a shortened rendering of the tool arguments. Then it asks a language model to produce only a 3-to-8-word label, using a strict prompt that says not to reveal tool names, commands, paths, URLs, IDs, secrets, or JSON.

The file also protects the rest of the system from delays and failures. The model request has a small token limit, and the call is wrapped in an 8-second timeout. If anything goes wrong, the file records a metric and a log entry, then returns nothing instead of breaking the turn. Finally, the model’s answer is cleaned up so it becomes one neat label with extra whitespace, bullets, quotes, and ending punctuation removed.

#### Function details

##### `ActivityModel.model`  (lines 31–31)

```
def model(self) -> str
```

**Purpose**: This is the agreed-upon way for an activity model object to reveal which model name it uses. ActivitySummarizer reads it when building the request sent to the language model.

**Data flow**: Before: an object that follows the ActivityModel shape has some model identifier inside it. The property exposes that identifier as text. After: the summarizer can place that model name into the ModelRequest.

**Call relations**: ActivitySummarizer.summarize relies on this property when it prepares the model request. The protocol does not implement the storage itself; it states what any compatible model object must provide.


##### `ActivityModel.complete`  (lines 33–33)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This is the agreed-upon method for asking a language model to complete a request and return text. In this file, it is used to turn a prepared prompt and payload into a short activity label.

**Data flow**: Before: the caller has a ModelRequest containing the prompt, user payload, token limit, and other options. The model object receives that request and produces a text completion. After: the caller gets the model’s raw text answer.

**Call relations**: ActivitySummarizer.summarize awaits this method after building the request. The protocol only defines the promise that such a method exists; the actual model implementation lives elsewhere.


##### `ActivitySummarizer.summarize`  (lines 42–69)

```
async def summarize(self, call: ToolUseBlock, goal: str='') -> str | None
```

**Purpose**: This function creates a member-friendly label for one tool call. It uses the user goal and the tool call details as private context, asks a model for a safe short summary, and returns the cleaned-up label.

**Data flow**: Before: it receives a ToolUseBlock, which includes the tool name and its input arguments, plus an optional goal string. It trims the goal, turns the arguments into bounded JSON text, builds a compact payload, and places that payload into a ModelRequest with a safety-focused instruction prompt. It then waits for the model response, but only up to the configured timeout. After: if the model answers successfully, the raw answer is normalized by activity_line and returned as a short label; if the request fails or times out, it records a metric and log entry and returns None.

**Call relations**: This is the main flow in the file. It calls _bounded_arguments so large or sensitive argument blobs are not sent unchecked, constructs Message and ModelRequest objects for the model call, uses asyncio.timeout so the system does not wait forever, and then passes the model’s answer to activity_line for cleanup. On errors, it hands information to emit_metric and log so operators can see that activity labeling failed without interrupting the larger user turn.

*Call graph*: calls 2 internal fn (_bounded_arguments, activity_line); 6 external calls (__init__, __init__, timeout, dumps, emit_metric, log).


##### `_bounded_arguments`  (lines 72–76)

```
def _bounded_arguments(arguments: dict[str, object]) -> str
```

**Purpose**: This helper turns a tool’s argument dictionary into compact JSON text and cuts it off if it is too long. It keeps the activity-summary request small and avoids sending an unlimited amount of tool input to the model.

**Data flow**: Before: it receives a dictionary of tool arguments. It serializes that dictionary into compact JSON text. If the result fits within the configured character limit, it returns it unchanged; if it is too long, it returns only the beginning plus an ellipsis. After: the caller has a short text version of the arguments suitable for the summarization prompt.

**Call relations**: ActivitySummarizer.summarize calls this while building the payload for the model. This helper does not decide what the summary should say; it only prepares the argument text safely and predictably before the model request is made.

*Call graph*: called by 1 (summarize); 1 external calls (dumps).


##### `activity_line`  (lines 79–82)

```
def activity_line(text: str) -> str | None
```

**Purpose**: This function cleans a model’s raw answer into one tidy label. It removes common formatting noise so the member sees a simple phrase instead of bullets, quotes, code ticks, or stray punctuation.

**Data flow**: Before: it receives the text returned by the model. It collapses repeated whitespace, trims leading bullet-like characters, removes wrapping quote or code characters, and strips ending punctuation such as periods or exclamation marks. After: it returns the cleaned label, or None if nothing meaningful remains.

**Call relations**: ActivitySummarizer.summarize calls this after the model completes. The model is asked to follow strict formatting rules, but this function is the final cleanup step in case the model includes extra formatting anyway.

*Call graph*: called by 1 (summarize); 1 external calls (sub).


### Cancellation Primitive
Shared cancellation logic stops active workflow execution before marking the turn cancelled in storage.

### `core/src/ufo/turns/cancellation.py`

`domain_logic` · `cancel handling and reconciliation`

A “turn” is a unit of work that may be running as a durable DBOS workflow, meaning DBOS keeps enough state to recover or continue it reliably. This file exists because cancelling a turn is easy to get wrong: if the database says “cancelled” before the workflow is actually cancelled, the system could believe work has stopped while it is still running. That would be like marking a kitchen order as void before telling the cook to stop making it.

The file’s single function, `cancel_one_turn`, is the common cancellation primitive used by different parts of the system. It cancels exactly one turn. It does not cancel child or descendant turns; another part of the system, the cancel reconciler, is responsible for walking that tree.

The flow is careful. First it reads the turn row from the database. If the turn does not exist, or if it has already finished in some final state, it does nothing. If the turn is still active, it asks DBOS to cancel the workflow for that turn. Only after that cancellation request is durably recorded does it update the turn row to the terminal status `cancelled`. It also preserves the list of objects the turn had already created, so callers can still learn what was produced before cancellation. Finally, it emits a metric so cancelled turns are counted consistently.

#### Function details

##### `cancel_one_turn`  (lines 23–83)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Cancels one turn safely and records that cancellation in the database only after the workflow has been told to stop. It returns the final cancellation frame if this call actually changed the turn to cancelled, or `None` if the turn was missing or had already finished.

**Data flow**: It takes a DBOS client and a turn ID. It first opens a database transaction and reads the turn’s current status, profile, and parent information. If the row is missing or already terminal, it returns `None`. Otherwise it asks DBOS to cancel the workflow named by that turn ID. Then it reopens a database transaction, reads the objects the turn already created, builds a `TerminalFrame` with status `cancelled`, and tries to update the row only if it is still non-terminal. If that update succeeds, it emits a cancellation metric and returns the frame; if another process finished the turn first, it returns `None`.

**Call relations**: This function is the shared cancellation step that higher-level cancel paths rely on. Inside its flow it uses `workspace_tx` to read and write the durable turn row, `DBOSClient.cancel_workflow_async` to stop the durable workflow, `ObjectRef.model_validate` and `TerminalFrame` to describe the cancelled result, and `emit_metric` with `turn_profile` to report that the turn ended by cancellation.

*Call graph*: 8 external calls (__init__, model_validate, cancel_workflow_async, select, update, workspace_tx, emit_metric, turn_profile).

## 📊 State Registers Touched

- `reg-database-session-workspace-scope` — The shared database access layer that keeps reads and writes inside the right workspace and transaction.
- `reg-surface-routing` — The mapping from outside places like web, Slack, terminal, and iMessage to the right workspace, conversation, member, and agent.
- `reg-conversation-records` — The durable conversation list, including titles, audience, surface labels, sandbox links, and visibility rules.
- `reg-inbound-admission-queue` — The saved queue of incoming messages or intents waiting to become safe conversation turns.
- `reg-turn-state` — The shared status record for each unit of agent work, including claiming, running, completion, failure, parent-child links, and billing markers.
- `reg-transcript-store` — The saved conversation history and compacted summaries that later turns, portals, and auditors read back.
- `reg-live-update-stream` — The temporary live feed of progress messages that open clients and other server processes can follow.
- `reg-cancellation-flags` — The shared stop signals and cleanup markers used to cancel turns, child work, sandboxes, and stuck jobs safely.
- `reg-runtime-instance-fleet` — The record of which server processes are alive and which shared listeners or jobs they currently own.
- `reg-schedule-monitor-store` — The saved recurring prompts, pauses, and outside-world watches that can wake conversations later.
- `reg-artifact-blob-store` — The shared file storage for generated artifacts, downloads, document previews, screenshots, and other saved output bytes.
- `reg-subagent-objectives` — The shared plan and delegation state for child agents, objectives, steps, evidence, attempts, and result delivery.
- `reg-observability-context` — The shared tracing, logging, metrics, health, and redaction context used to understand what happened safely.
- `reg-reply-delivery-outbox` — Durable reply records for messages that must be delivered exactly once or retried safely, including mid-turn replies before final turn completion.
- `reg-delivery-format-registry` — Shared per-surface reply and delivery-format rules used when constructing prompts and shaping delivered responses.
- `reg-sandbox-task-session-state` — Persistent/pollable state for long-running sandbox commands and REPL sessions that survive tool timeouts across tool calls.
- `reg-runtime-connection-pools` — Live pooled connections and reusable clients for shared services such as the database, Redis/live hub, blob storage, model providers, connector APIs, and sandbox/browser providers.
