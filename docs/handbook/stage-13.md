# Live streaming, reply delivery, and artifact download  `stage-13`

This stage is the system’s live delivery layer. It runs during an agent’s turn and after it finishes, making sure people watching in Slack, the web app, or a terminal can see progress, receive the final answer, and download any shared files. It is like the broadcast booth for the work loop.

The central piece is the live message hub. It publishes updates such as generated text, tool activity, costs, and the final result while a turn is running. It also keeps a short history, so a screen that disconnects can resume from its last known point instead of losing the stream. The hub tail builds on this by combining those live messages with saved database state. That lets a late or reconnecting client catch up and still see a clean ending.

For larger deployments, the Redis stream hub moves the same live updates through Redis Streams, a shared message pipe that multiple server processes can read from. Finally, the artifacts route protects file downloads with signed tokens, so shared links can fetch the actual stored file only when the link is valid.

## Files in this stage

### Turn stream tailing
These files let clients follow an agent turn live, replay missed events after reconnecting, and finish with saved final state.

### `core/src/ufo/surfaces/hub_tail.py`

`orchestration` · `request handling`

A “turn” produces live frames, such as text updates and final status. The tricky part is timing: a caller may start watching before the turn ends, after it ends, or after missing some live messages. This file solves that by listening to two sources at once. One source is the hub, an in-memory broadcaster for live updates. The other is the database, which is the durable record of whether the turn has finished or is parked. “Parked” means the turn is paused rather than truly finished, for example because a spending cap was hit.

The main flow is like watching a race with both a live announcer and an official scoreboard. The hub gives quick live updates, while the database is the scoreboard that cannot be missed. `tail_frames` starts both a hub listener and a polling task. If a terminal or parked frame appears from either path, the stream ends. If the hub drops a frame because a queue is full, correctness is still protected because the database poll will eventually see the final or parked state.

The file also checks a special seat/admission case: if the speaker no longer has permission to continue, the parked message explains that the seat was revoked instead of showing the normal spend-cap message.

#### Function details

##### `tail_frames`  (lines 27–53)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Streams all live frames for one turn until the turn reaches a finished or parked state. It is used by callers that need a reliable live view, including callers that reconnect and provide the last cursor they saw.

**Data flow**: It receives a hub, a turn ID, and optionally a cursor, which is a saved position in the stream. First it checks whether the hub can still resume from that cursor; if not, it starts from the retained beginning. It creates a shared queue, starts one background task to read live hub frames, and another to poll the database for the turn’s durable status. It first checks the database immediately; if the turn is already done or parked, it yields that ending frame and stops. Otherwise it yields frames from the queue until a terminal or parked frame appears, then cancels the background tasks.

**Call relations**: This is the central routine used by `HubTailer.tail`. It calls `_pump` to bring in live hub messages, `_poll_status` to keep checking the saved turn state, and `turn_status_frame` to detect whether the stream should end. It also asks the hub whether a reconnect cursor is still covered before deciding where to resume.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, turn_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 56–63)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: Copies live frames from the hub subscription into the shared queue used by the main tailing loop. It is the live-announcer side of the system.

**Data flow**: It receives the hub, the turn ID, the starting cursor, and the queue. It subscribes to the hub for that turn, then places each incoming cursor-and-frame pair into the queue. If something goes wrong while listening, it records a log message and stops rather than crashing the caller directly.

**Call relations**: `tail_frames` starts this as a background task. `_pump` depends on the hub’s `subscribe` stream for live updates and feeds those updates back to `tail_frames` through the queue. Its log entry helps operators notice if the live path failed, while the polling path can still protect the final result.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 66–75)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: Periodically checks the database to see whether the turn has finished or become parked. This is the safety net that makes the stream reliable even if the live hub missed something.

**Data flow**: It receives the turn ID and the shared queue. Once per polling interval, it asks `turn_status_frame` whether the saved turn state now has an ending frame. If there is no ending state yet, it keeps waiting. When it finds a terminal or parked frame, it puts that frame into the queue with an empty cursor and exits. If polling fails, it logs the problem.

**Call relations**: `tail_frames` starts this alongside `_pump`. While `_pump` listens to live messages, `_poll_status` watches the durable stored state. When `_poll_status` finds the stream-ending state, it hands it back through the same queue so `tail_frames` can yield it and stop cleanly.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `turn_status_frame`  (lines 78–107)

```
async def turn_status_frame(turn_id: UUID) -> LiveFrame | None
```

**Purpose**: Looks up the saved state of a turn and converts it into the frame that should end a stream, if one exists. It returns nothing while the turn is still queued or running.

**Data flow**: It receives a turn ID and opens a workspace database transaction. It reads the turn’s status, saved terminal frame, workspace, speaker information, and admission details. If the turn does not exist, it returns nothing. If a terminal frame is stored, it validates that saved data and returns a `Terminal` live frame. If the turn is not parked, it returns nothing. If the turn is parked, it checks whether the relevant seat or admission gate is now absent or revoked; in that case it returns a parked frame with a seat-revoked message. Otherwise it returns a parked frame saying the turn is over a spend cap and can resume when the cap is raised.

**Call relations**: Both `tail_frames` and `_poll_status` call this function. `tail_frames` uses it immediately so late subscribers can finish right away if the turn already ended. `_poll_status` uses it repeatedly as the durable fallback. Internally it relies on database access, terminal-frame validation, and seat/admission helpers to decide which final message is truthful.

*Call graph*: called by 2 (_poll_status, tail_frames); 8 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx, gate_member, seat_gate_absent).


##### `HubTailer.tail`  (lines 119–120)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Provides a small object-oriented wrapper around `tail_frames` for code that is given a `HubTailer` instead of importing the hub-tail module directly. It lets surface code ask for a turn stream through a simple method.

**Data flow**: It receives a turn ID and optional cursor. It uses the `Hub` stored on the `HubTailer` instance and passes everything to `tail_frames`. The result is the same asynchronous stream of cursor-and-frame pairs produced by `tail_frames`.

**Call relations**: Surface code calls this method when it needs to tail a turn. The method does not add new behavior; it hands the work to `tail_frames`, keeping the hub dependency tucked behind the `HubTailer` object.

*Call graph*: calls 1 internal fn (tail_frames).


### `core/src/ufo/hub.py`

`io_transport` · `request handling / live turn streaming`

This file solves the problem of showing live progress without making the agent wait for every viewer. Think of it like a small radio station for each turn: the agent broadcasts frames, and any surface that is listening receives them. If a listener is slow, the hub drops that listener’s oldest waiting frame rather than blocking the broadcast.

The frames are small updates such as streamed text, a final terminal frame, a spend update, a tool call announcement, or a skill-load announcement. Each published frame gets a simple increasing cursor, like ticket number 1, 2, 3. A subscriber can later say “start after ticket 27,” and the hub replays any still-remembered frames after that point before sending new live frames.

The main interface is `Hub`, which describes what any hub must do: publish, subscribe, and answer whether a cursor is still covered by its replay buffer. `InProcessHub` is the built-in version that works inside one running process. It uses a lock, which is a small gate that prevents two threads from changing the same shared state at the same time, because publishers and subscribers may run on different event loops. When a turn is finished and no one is listening, its stored stream is removed so memory does not grow forever.

#### Function details

##### `Hub.publish`  (lines 75–75)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This is the promised shape of a hub’s publish operation. Code uses it to send one live frame for a turn and get back the cursor assigned to that frame.

**Data flow**: It takes a turn ID and a live frame, such as text, cost, or final status. A real hub implementation stores or forwards that frame, assigns it a cursor, and returns that cursor to the caller.

**Call relations**: The queue code calls this when it needs to publish a failed terminal result. In this file, `InProcessHub.publish` is the concrete built-in version that does the actual fan-out work.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 77–79)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the promised shape of a hub’s subscribe operation. A surface uses it to receive frames for one turn, optionally starting after a cursor it has already seen.

**Data flow**: It takes a turn ID and an optional cursor. A real hub implementation first sends remembered frames after that cursor, then keeps yielding new live frames as they arrive.

**Call relations**: The hub tailing code calls this while pumping live frames to a surface. In this file, `InProcessHub.subscribe` is the concrete built-in version that combines replay with live delivery.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 81–81)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the promised shape of a check that tells whether the hub still has enough history to resume from a given cursor. It helps callers decide whether they can reconnect smoothly or must reload durable state instead.

**Data flow**: It takes a turn ID and a cursor. A real hub implementation checks its retained replay history and returns true if the cursor is still within the kept range, otherwise false.

**Call relations**: The surface tailing flow calls this before relying on replay. In this file, `InProcessHub.covers` is the built-in implementation that checks the in-memory replay ring.

*Call graph*: called by 1 (tail_frames).


##### `_offer`  (lines 84–87)

```
def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None
```

**Purpose**: This helper puts a frame into one subscriber’s queue without ever waiting. If that subscriber is already backed up, it removes the oldest queued frame to make room for the newest one.

**Data flow**: It receives a queue and a cursor-frame pair. If the queue is full, it discards one old item, then immediately inserts the new item; it returns nothing and only changes the queue.

**Call relations**: `InProcessHub.publish` schedules this helper on each subscriber’s event loop. That lets publishing stay fast and safe even when the subscriber is running on a different loop or thread.


##### `InProcessHub.publish`  (lines 116–130)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This sends one live frame to everyone currently subscribed to a turn and saves it in that turn’s replay buffer. It returns the new cursor so callers can remember how far the stream has advanced.

**Data flow**: It receives a turn ID and a live frame. Under a lock, it creates the turn stream if needed, increases the sequence number, stores the frame in a bounded replay buffer, and copies the current subscriber list. After releasing the lock, it schedules delivery to each subscriber queue and returns the cursor string. If the frame ends the stream and nobody is listening, it deletes the turn’s in-memory stream.

**Call relations**: This is the concrete implementation behind the `Hub.publish` contract. It creates `_TurnStream` records as needed, uses a bounded deque as the replay ring, and hands each subscriber delivery off to `_offer` so slow subscribers do not block the publisher.

*Call graph*: 2 external calls (__init__, deque).


##### `InProcessHub.subscribe`  (lines 132–162)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This lets a surface follow one turn’s live stream. It first replays remembered frames after the supplied cursor, then waits for and yields new frames as they are published.

**Data flow**: It receives a turn ID and optional cursor. It creates a bounded queue for this subscriber, records the subscriber’s current event loop, registers the subscriber under the lock, and snapshots buffered frames newer than the cursor. It yields the replayed frames first, then repeatedly yields new queue items. When the caller stops listening, it removes the subscriber and deletes the turn stream if no subscribers remain.

**Call relations**: This is the concrete implementation behind the `Hub.subscribe` contract used by the surface pumping code. It works with `InProcessHub.publish` through shared locked state: publish stores frames and fans them to registered queues, while subscribe registers a queue and reads both replayed and live frames without duplicating them.

*Call graph*: 4 external calls (__init__, Queue, get_running_loop, deque).


##### `InProcessHub.covers`  (lines 164–172)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This tells a caller whether a given cursor is still inside the in-memory replay history for a turn. It is a quick safety check before trying to resume a live stream without gaps.

**Data flow**: It receives a turn ID and cursor. If the cursor is empty, or the turn has no stored stream or buffer, it returns false. Otherwise it reads the oldest retained cursor under the lock and returns true when that oldest cursor is less than or equal to the requested cursor.

**Call relations**: This is the concrete implementation behind the `Hub.covers` contract used by the surface tailing flow. It does not deliver frames itself; it only tells the caller whether `subscribe` can likely replay from the requested point.


### Artifact downloads
This route serves stored artifact bytes only through valid signed download links.

### `core/src/ufo/surfaces/artifacts.py`

`io_transport` · `request handling`

This file solves a simple but important problem: shared files need to be downloadable, but not publicly open to anyone who can guess a URL. It creates one FastAPI route, meaning one web endpoint, for artifact downloads. A token in the request acts like a temporary claim ticket. Without that ticket, or if the ticket is invalid, expired, or points to a missing file, the route refuses to send any bytes.

When a request comes in, the route reads two things from the running app: the blob store, which is where file contents live, and the artifact token secret, which is the private key used to check that the token was really created by this deployment. It then verifies the token against the current time. If verification succeeds, the token reveals which stored blob should be downloaded and, optionally, what filename the user should see.

Before streaming, the route checks that the blob still exists. This matters because it avoids opening a download stream for a file that is gone. If a filename is present, it prepares a browser-friendly download header, including safe encoding for names with special characters. Finally, it returns a streaming response, so the file is sent in chunks rather than loaded fully into memory. That is like pouring from a large container through a hose instead of carrying the whole container at once.

#### Function details

##### `download`  (lines 26–53)

```
async def download(request: Request, token: str='') -> StreamingResponse
```

**Purpose**: This function serves a requested artifact file to the browser or client, but only after checking that the caller has a valid artifact token. It is used when someone follows a generated artifact link, such as a shared file link or a large attachment link.

**Data flow**: It receives the web request and an optional token string from the URL. It reads the blob store and token secret from the application state, rejects the request if the token is missing, checks the token against the current time, and uses the verified token to find the stored blob key and optional filename. If the blob does not exist, it returns a not-found error. If everything is valid, it builds download headers when needed and returns a streaming response that sends the blob contents in chunks.

**Call relations**: This function is called by FastAPI when an HTTP GET request reaches the artifact download path. During that request, it relies on the artifact-token verifier to decide whether the link is trustworthy, asks the blob store whether the file exists and for a stream of its contents, and hands the resulting stream to FastAPI’s streaming response machinery so the network layer can send the file without loading it all into memory.

*Call graph*: 5 external calls (now, HTTPException, StreamingResponse, verify_artifact_token, quote).


### Redis stream hub
This extension moves live turn updates through Redis Streams so streaming works across multiple server processes.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `live request streaming and reconnect replay`

This file is a live message hub. When a turn is producing an answer, it publishes small “frames” such as a text fragment, a tool call, or a final result into Redis, an external fast data store. A viewer can then subscribe to the same Redis stream and receive those frames in order.

The problem it solves is fan-out across processes. If one server process is doing the work and another server process is serving the browser connection, an in-memory queue would not be enough because the two processes cannot see each other’s memory. Redis acts like a shared noticeboard: publishers pin new frames to it, and subscribers read from it.

Each turn gets its own Redis Stream, like a separate timeline. Published frames are converted into a small JSON package with a kind label and the frame’s data. Subscribers read entries from a remembered cursor, so reconnecting clients can replay anything still kept in Redis. Old stream entries are trimmed and idle streams expire, because these frames are only for live display; the permanent final answer is stored elsewhere.

One important detail is that Redis clients are kept separately per asyncio event loop. An asyncio event loop is the engine that runs async tasks. Sharing one Redis client across different loops can break, so this file creates the right client for the loop currently using it.

#### Function details

##### `frame_payload`  (lines 53–56)

```
def frame_payload(frame: LiveFrame) -> dict[str, object]
```

**Purpose**: Turns one live frame into a simple wire-friendly package. It records both what kind of frame it is and the frame’s data, so another process can rebuild the same type later.

**Data flow**: It receives a LiveFrame object, such as a text delta or terminal frame. It looks up the frame’s kind name, asks the frame to turn its fields into JSON-safe data, and returns a dictionary containing the kind and data.

**Call relations**: When RedisStreamHub.publish is about to send a frame to Redis, it calls this function first. The result is then converted to JSON and written into the Redis Stream.

*Call graph*: called by 1 (publish); 1 external calls (model_dump).


##### `frame_from_payload`  (lines 59–63)

```
def frame_from_payload(payload: dict[str, object]) -> LiveFrame
```

**Purpose**: Rebuilds a live frame from the small package that was stored in Redis. It checks the kind label so unknown or invalid frame types fail clearly instead of being misread.

**Data flow**: It receives a dictionary read from JSON. It reads the kind, finds the matching frame model, validates the stored data against that model, and returns a LiveFrame object ready for the rest of the system to use.

**Call relations**: RedisStreamHub.subscribe calls this after reading and decoding a Redis entry. It is the bridge from stored stream data back into the live frame objects that subscribers expect.

*Call graph*: called by 1 (subscribe); 1 external calls (cast).


##### `_stream_id`  (lines 66–68)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis Stream entry id into two numbers that can be compared safely. Redis ids look like a timestamp plus a sequence number, such as `123-0`.

**Data flow**: It receives a stream entry id as text. It splits the id at the dash, converts the millisecond timestamp and sequence part into integers, and returns them as a pair.

**Call relations**: RedisStreamHub.covers uses this helper when deciding whether a subscriber’s cursor is still inside the retained part of the stream. Comparing numeric pairs avoids mistakes that plain string comparison could make.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 71–80)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from Redis’s `xread` response and checks that the response shape is the one this code expects. This prevents the code from quietly misreading a different Redis protocol format.

**Data flow**: It receives the raw batch returned by Redis. If the batch is empty, it returns an empty list. If the batch is not the expected list shape, it raises an error. Otherwise it pulls out and returns the entries for the stream.

**Call relations**: RedisStreamHub.subscribe calls this after every Redis `xread`. It gives the subscribe loop a clean list of entries to turn into live frames.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 96–102)

```
def _client(self) -> Redis
```

**Purpose**: Returns a Redis client that is safe to use on the current async event loop. This matters because async Redis clients are tied to the loop that created them.

**Data flow**: It looks at the currently running asyncio event loop. If this hub already has a Redis client for that loop, it returns it. If not, it creates a new client from the configured Redis URL, stores it for that loop, and returns it.

**Call relations**: Publish, subscribe, and covers all call this before talking to Redis. It is the safeguard that lets background workflow code and web-serving code use the same hub object without sharing unsafe loop-bound connection state.

*Call graph*: called by 3 (covers, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 104–105)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name for a particular turn. This keeps all live frames for one turn in one predictable Redis location.

**Data flow**: It receives a turn id, adds the configured stream prefix, and returns a stream name string like a namespaced address.

**Call relations**: Publish, subscribe, and covers all call this when they need to write to, read from, or inspect the Redis Stream for a turn. It keeps naming consistent across the whole hub.

*Call graph*: called by 3 (covers, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 107–114)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: Adds one live frame to the Redis Stream for a turn and returns the new stream cursor. A caller uses this whenever it wants subscribers to see a new piece of live progress.

**Data flow**: It receives a turn id and a LiveFrame. It turns the turn id into a Redis Stream name, converts the frame into compact JSON, appends it to Redis, trims the stream to a maximum size, refreshes the stream’s expiry time, and returns the Redis entry id that was created.

**Call relations**: This is the publishing side of the hub. It calls _stream to find the destination, frame_payload to make the frame safe to send, json.dumps to serialize it, and _client to get the correct Redis connection. Subscribers later use the returned entry ids as cursors for replay.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 116–140)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Continuously reads live frames for a turn from Redis, starting after a given cursor. It lets a client replay retained frames and then keep waiting for new ones.

**Data flow**: It receives a turn id and an optional cursor. It chooses the Redis Stream, starts at the cursor or at the beginning, repeatedly asks Redis for new entries, waits briefly when there is nothing new, decodes each entry’s JSON, rebuilds the LiveFrame, updates the cursor, and yields the cursor plus frame to the caller.

**Call relations**: This is the receiving side of the hub. It uses _stream and _client to read from Redis, _stream_entries to normalize Redis responses, json.loads to decode stored JSON, and frame_from_payload to rebuild frame objects. If Redis read timeouts happen during blocking waits, it treats them as normal idle moments and simply keeps listening.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 142–148)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether a saved cursor is still covered by the Redis Stream. This tells a reconnecting subscriber whether it can resume without a gap or should redraw from another source.

**Data flow**: It receives a turn id and cursor. If the cursor is empty, it returns false. Otherwise it reads the first retained entry in the turn’s stream. If the stream has no entries, it returns false. If entries exist, it compares the first retained id with the cursor and returns true only when the cursor has not fallen behind the retained history.

**Call relations**: Reconnect logic can call this before subscribing from an old cursor. It uses _stream to find the stream, _client to inspect Redis, and _stream_id to compare Redis entry ids as ordered numbers.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).

## 📊 State Registers Touched

- `reg-auth-session` — The signed login and identity state that proves which member or operator is using the system.
- `reg-audience-policy` — The saved visibility rules that decide which people may see or use a conversation or agent.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-transcript-state` — The stored conversation transcript, including exact recent messages and compact summaries of older content.
- `reg-sandbox-session` — The saved or live sandbox workspace where an agent can run commands and keep files across tool calls.
- `reg-live-stream-hub` — The live stream of turn updates that lets clients watch progress and reconnect without losing recent events.
- `reg-artifact-storage` — The shared file and blob storage for generated artifacts, plus the signed download state used to protect them.
- `reg-surface-installations` — The saved Slack, web, terminal, and other surface bindings used to receive messages and send replies back.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-observability-context` — The shared logging, metrics, tracing, and trace-link state used to understand work across requests and subagents.
- `reg-surface-delivery-state` — Durable outbound reply/writeback state used to de-duplicate, track, retry, and complete delivery of responses to Slack, web, terminal, or other surfaces.
