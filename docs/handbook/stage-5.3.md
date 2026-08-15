# Terminal and command-line surface  `stage-5.3`

This stage is the system’s doorway to command-line users. It sits on the outside edge of the main work loop, turning internal events into simple text that a shell program can show, and turning terminal replies back into actions the system understands.

The main piece is the UFO terminal surface. It exposes an HTTP endpoint for the command-line client and speaks a small text command protocol. Through it, the user can see chat messages, live progress updates, credential prompts, file links, and terminal actions in their own shell. Redis terminal support keeps this working even when the user’s connection is on one server pod and the backend work is on another, using Redis Streams for short control messages and separate blob storage for larger data. The sandbox terminal code lets the system run work through a user’s already-open terminal instead of connecting to a separate container. The CLI callback file finishes OAuth account linking after an external service sends the user back. The two __init__ files are simple package markers so these extensions can be imported.

## Files in this stage

### Remote terminal transport
Redis stream forwarding and terminal sandbox bridging let backend work communicate with a user's already-connected terminal across pods.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `cross-cutting request handling for terminal connect, operation send, reply, and cleanup`

In a single-process setup, a workflow can talk directly to the terminal connection that belongs to a conversation. In a shared fleet, that is no longer guaranteed: the browser or CLI may be connected to one pod while the workflow asking for terminal work runs on another. This file is the bridge between them. Think of Redis as the dispatch board and the blob store as the package shelf. Redis records who currently has the terminal, which operation is waiting, who has claimed it, and where the reply should appear. The blob store carries larger input or output bytes that should not be squeezed into Redis messages. The main class, RedisTerminals, publishes a short-lived “I am here” binding while a connection is held, sends terminal operations one at a time with a per-conversation lock, waits for replies with clear deadlines, and cleans up temporary keys afterward. It is careful about reconnects: a terminal operation is claimed atomically so two pods do not render and run the same command twice. If Redis, the client, or the terminal disappears, waits end with TerminalGone instead of leaving a workflow stuck forever.

#### Function details

##### `_text`  (lines 54–57)

```
def _text(value: bytes | str) -> str
```

**Purpose**: Turns a Redis field into normal Python text. Redis values may already be text or may still be bytes, so this gives the rest of the file one simple shape to work with.

**Data flow**: It receives one Redis value → leaves it alone if it is already a string, or decodes it if it is bytes → returns a string.

**Call relations**: It is the small translator used whenever this file reads Redis data, including operation decoding, reply decoding, binding reads, gate checks, and the Lua result parsing done through _pairs.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 60–65)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: Converts the flat field-and-value list returned by the Redis Lua script into a dictionary-like map. This makes the claimed operation easier to decode safely.

**Data flow**: It receives a list shaped like field, value, field, value → converts each item to text and groups neighboring items → returns a field map, or raises an error if the input is not a list.

**Call relations**: next_op calls this after the Lua script claims an operation. _pairs then relies on _text, and its result is handed to RedisTerminals._decode_op.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 68–71)

```
def _bind_payload(cwd: str, member_id: UUID | None) -> str
```

**Purpose**: Builds the JSON text that says where a terminal workspace is and which member owns it, if any. Redis stores this same payload for both live bindings and in-flight operations.

**Data flow**: It receives a current working directory and an optional member UUID → writes them into a small JSON object, using the UUID's hex text when present → returns JSON text.

**Call relations**: The heartbeat uses it to publish a live connection. _run_op uses it to pin the same binding while an operation is in progress.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 145–154)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts entries from the shape Redis returns for a stream read. It fails loudly if Redis replies in an unexpected format, which prevents silently reading the wrong thing.

**Data flow**: It receives the raw XREAD response → returns an empty list if there is no data, otherwise checks that the response is the expected list shape → returns the stream entries.

**Call relations**: _await_reply calls this each time it polls the reply stream, before passing the first reply entry to _decode_reply.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 181–198)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the current asynchronous event loop. This matters because asyncio Redis clients are tied to the loop that created them, like a tool that only works at one workbench.

**Data flow**: It reads the currently running event loop and the local client cache → reuses an existing Redis client for that loop or creates one with bounded socket timeouts → returns the client.

**Call relations**: Almost every Redis-facing method calls this before reading or writing keys and streams. It is the shared doorway to Redis for heartbeat, sending, receiving, staging, resolving, and cleanup.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 200–201)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores the live terminal binding for a conversation. This is the short-lived sign saying “a terminal for this conversation is currently connected.”

**Data flow**: It receives a conversation UUID → formats it into the term:bind Redis key name → returns that key string.

**Call relations**: _heartbeat writes this key repeatedly. _read_binding reads it first when trying to find a connected terminal.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 203–204)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key that stores a temporary binding while an operation is already running. This keeps the workspace discoverable after the held client stream has moved on.

**Data flow**: It receives a conversation UUID → formats it into the term:inflight Redis key name → returns that key string.

**Call relations**: _run_op writes this key before publishing an operation, _read_binding uses it as a fallback, and _clear_op deletes it after the operation ends.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 206–207)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name where terminal operations for one conversation are queued. A Redis Stream is an ordered log of messages that readers can wait on.

**Data flow**: It receives a conversation UUID → formats it into the term:op stream name → returns that stream name.

**Call relations**: _run_op adds new operations to this stream, next_op reads and claims them, and _clear_op removes finished entries.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 209–210)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: Builds the Redis Stream name where the reply for one operation will be written. Each operation gets its own reply stream so the sender can wait for exactly its answer.

**Data flow**: It receives an operation id → formats it into the term:reply stream name → returns that stream name.

**Call relations**: _await_reply waits on this stream, _deliver_reply writes to it, and _clear_op deletes it during cleanup.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 212–213)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key for the per-conversation lock. The lock makes sure only one terminal operation is sent to a conversation at a time.

**Data flow**: It receives a conversation UUID → formats it into the term:lock key name → returns that key string.

**Call relations**: send uses this key when it asks Redis for the conversation lock before posting a new operation.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 215–216)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key that marks an operation as already delivered to a held connection. This prevents a reconnecting or competing pod from running the same operation twice.

**Data flow**: It receives an operation id → formats it into the term:deliv key name → returns that key string.

**Call relations**: The Lua claim script creates keys with this prefix, and _clear_op removes the matching key when the operation is finished.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 218–219)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key for metadata about an in-flight operation. The metadata says which conversation and member the operation belongs to.

**Data flow**: It receives an operation id → formats it into the term:opmeta key name → returns that key string.

**Call relations**: _run_op writes this before publishing an operation. staged and _deliver_reply read it to check permissions, and _clear_op deletes it afterward.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 221–222)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for an operation's input body. This is used when the terminal operation needs byte content that is too large or unsuitable for the Redis control message.

**Data flow**: It receives an operation id → formats it into the term/op blob key → returns that blob key string.

**Call relations**: _run_op writes the body there, staged reads it for the terminal side, and _clear_op deletes it.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 224–225)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store key for a large operation reply. Small replies go through Redis, while larger replies are stored here and referenced from Redis.

**Data flow**: It receives an operation id → formats it into the term/reply blob key → returns that blob key string.

**Call relations**: _deliver_reply writes large replies there, _decode_reply reads them, and _clear_op removes them after the operation is done.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 227–240)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Records that this pod is currently holding a terminal connection for a conversation. It starts or shares a background heartbeat that keeps the Redis binding alive.

**Data flow**: It receives the conversation id, workspace directory, and optional member id → updates this process's local hold count under a thread lock → starts a heartbeat task if this is the first local connection.

**Call relations**: Called when a held terminal stream connects. It creates the _heartbeat task that publishes the binding for other pods to find.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 242–251)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Records that one local held terminal connection has ended. When the last local connection leaves, it stops refreshing the Redis binding.

**Data flow**: It receives a conversation id → decreases the local connection count under a thread lock → cancels the heartbeat and removes the local hold when the count reaches zero.

**Call relations**: This is the counterpart to connect. It does not delete the Redis key directly; _heartbeat simply stops, and Redis lets the binding expire naturally.


##### `RedisTerminals._heartbeat`  (lines 253–265)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: Keeps the live terminal binding fresh in Redis while this pod still holds the connection. It refreshes instead of deleting on exit so reconnects do not accidentally erase a newer pod's binding.

**Data flow**: It receives the conversation id, directory, and member id → repeatedly writes the binding payload to Redis with a time-to-live → sleeps between refreshes until cancelled.

**Call relations**: connect starts this as a background task. It uses _bind_payload, _bind_key, and _client to publish the connection's presence.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 267–276)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the terminal workspace known locally by this pod, without asking Redis. It is useful only on the pod that is actually holding the connection.

**Data flow**: It receives a conversation id → checks the local holds dictionary under a lock → returns a TerminalWorkspace if found, otherwise None.

**Call relations**: This is the local fast path. Cross-pod discovery uses arrived and _read_binding instead.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 278–291)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal binding to appear for a conversation. This covers normal reconnect gaps where the user's held stream drops and reconnects to a pod.

**Data flow**: It receives a conversation id and grace period → repeatedly asks _read_binding for the workspace until found or time runs out → returns the workspace or None.

**Call relations**: send calls this before trying to queue an operation, so a workflow does not fail just because it landed during a short client reconnect.

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 293–307)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Reads the conversation's terminal workspace from Redis. It checks both the live connection binding and the pinned in-flight binding.

**Data flow**: It receives a conversation id → reads the live binding key, then the in-flight key if needed → parses the JSON and returns a TerminalWorkspace, or None if no binding exists.

**Call relations**: arrived uses this during its wait loop. It relies on _client and the key-building helpers, and converts Redis text with _text.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 309–365)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one terminal operation to the connected terminal and waits for its reply. It turns missing terminals, timeouts, and Redis failures into TerminalGone so callers get one clear failure mode.

**Data flow**: It receives the conversation id, operation details, timeout, and optional body bytes → waits for a binding, creates an operation id, takes the conversation lock, runs the operation, and releases the lock → returns reply bytes or raises an appropriate terminal error.

**Call relations**: This is the main sender-side entry in the transport. It calls arrived first, uses _lock_key and _client for serialization, then delegates the actual posting and waiting to _run_op.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 5 external calls (__init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 367–413)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: Performs the work after send has acquired the one-at-a-time lock. It publishes the operation, stages any input body, waits for the reply, and then cleans up.

**Data flow**: It receives a conversation id, TerminalOp, optional body, workspace binding, and deadline → writes operation metadata and in-flight binding, stores body bytes if present, adds the operation to the Redis stream, waits for the reply → returns reply bytes and clears temporary state in all cases.

**Call relations**: send calls this inside its timeout. It uses _op_fields to build the stream message, _await_reply to wait for the answer, and _clear_op for teardown.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 415–423)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: Turns a TerminalOp object into the field map stored in the Redis operation stream. This is the compact control message read by the terminal side.

**Data flow**: It receives a TerminalOp → copies its id, kind, timeout, name, argument, and parameters into string fields → returns the Redis-ready dictionary.

**Call relations**: _run_op calls this just before adding the operation to the conversation's operation stream.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 425–433)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: Turns Redis stream fields back into a TerminalOp object. This lets the held terminal side work with the normal operation type instead of raw Redis fields.

**Data flow**: It receives a field map from Redis → converts values to text, fills missing optional fields with empty strings, parses the timeout → returns a TerminalOp.

**Call relations**: next_op calls this after _pairs has shaped the claimed Lua result into a field map.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 435–458)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: Waits for the reply to one operation until its deadline. It polls Redis in bounded blocks so it can wake up and fail on time instead of waiting forever.

**Data flow**: It receives an operation id, total deadline, and user-facing timeout → reads the operation's reply stream until an entry appears or time expires → returns decoded reply bytes or raises TerminalGone.

**Call relations**: _run_op calls this after publishing the operation. It uses _stream_entries to parse Redis replies and _decode_reply to interpret the reply payload.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 460–473)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: Interprets a reply message from Redis. It handles failed operations, small inline replies, and large replies stored in the blob store.

**Data flow**: It receives an operation id and reply fields → raises TerminalOpFailed if the reply says the operation failed, reads a blob if the reply points to one, or base64-decodes inline bytes → returns the reply bytes.

**Call relations**: _await_reply calls this after it receives a reply stream entry. For large replies it uses _reply_blob to find the stored body.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 475–506)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for and returns the next terminal operation that should be rendered to the connected client. It uses a Redis Lua script so reading and claiming happen as one indivisible action.

**Data flow**: It receives a conversation id and optionally an operation id to skip → asks Redis to scan, clean expired entries, and claim one unclaimed operation → returns a decoded TerminalOp when one is available, or keeps waiting in bounded Redis reads.

**Call relations**: This is the receiver-side counterpart to send. It reads the stream written by _run_op, uses _pairs and _decode_op for the claimed entry, and raises TerminalGone if the Redis rendezvous is unreachable.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 508–523)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Fetches the input body staged for an in-flight operation, if the requester is allowed to see it. This lets any pod serve the operation body from the shared blob store.

**Data flow**: It receives a conversation id, operation id, and optional member id → reads operation metadata from Redis and checks it with _gate_ok → returns the body bytes from the blob store, or None if missing, expired, mismatched, or unreadable.

**Call relations**: The terminal-serving side calls this after next_op when it needs the copy-in body. It depends on _opmeta_key for the gate and _body_blob for the stored bytes.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 525–540)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts a terminal reply and schedules it to be delivered through Redis. It returns immediately so the HTTP or stream route posting the reply is not held up by Redis I/O.

**Data flow**: It receives the conversation id, operation id, reply bytes, optional failure text, and optional member id → creates a background delivery task on the current loop → returns True right away.

**Call relations**: This is the reply-side entry point. It hands the actual Redis write to _deliver_reply through _spawn.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 542–571)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: Writes an operation reply to the correct Redis reply stream, after checking that the operation is still in flight and belongs to the right conversation/member. Large replies are placed in the blob store.

**Data flow**: It receives conversation and operation ids, reply bytes, optional failure text, and optional member id → reads metadata, verifies the gate, chooses failure, blob, or inline base64 form → writes the reply stream entry and sets an expiry.

**Call relations**: resolve schedules this in the background. It uses _gate_ok to reject stale or mismatched replies, _reply_blob for large reply storage, and _reply_stream for the sender's waiting stream.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 573–584)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a staged-body read or reply post is allowed for an operation. It fails closed: if the conversation or named member does not match, access is refused.

**Data flow**: It receives raw operation metadata, a conversation id, and optional member id → parses the metadata JSON → returns true only when the conversation matches and any supplied member matches the recorded member.

**Call relations**: staged uses this before returning copy-in bytes. _deliver_reply uses it before accepting a reply.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 586–589)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports that this cross-pod transport has no reliable local view of the operation currently awaited by another pod. It deliberately returns None rather than pretending to know partial state.

**Data flow**: It receives a conversation id → does not read Redis or local operation state → returns None.

**Call relations**: This satisfies the terminal transport interface for operator-style reads. The actual in-flight tracking for this Redis transport lives in Redis keys used by send, staged, and resolve.


##### `RedisTerminals._clear_op`  (lines 591–612)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: Best-effort cleanup for all temporary Redis keys, stream entries, and blob objects owned by an operation. Safety does not depend only on this cleanup because the keys also expire.

**Data flow**: It receives a conversation id, operation id, and optional stream entry id → tries to delete the operation stream entry, metadata, delivery marker, in-flight binding, reply stream, and both blob bodies → changes external Redis/blob state but returns no value.

**Call relations**: _run_op calls this in its finally block after the reply wait ends or fails. It uses the key and blob helpers to remove each piece of operation state.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 614–621)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: Starts a background coroutine and keeps a reference to it until it finishes. This prevents a fire-and-return reply delivery task from being forgotten or garbage-collected mid-flight.

**Data flow**: It receives a coroutine and an event loop → wraps the coroutine with _logged, creates an asyncio task, stores it in the task set, and removes it when done → returns no direct result.

**Call relations**: resolve calls this to launch _deliver_reply without blocking the caller. _spawn uses _logged so failures are reported.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 623–627)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: Runs a background coroutine and logs any exception it raises. This keeps reply-delivery failures visible instead of silently disappearing.

**Data flow**: It receives a coroutine → awaits it → if any exception occurs, writes a warning with the error text.

**Call relations**: _spawn wraps background delivery work with this helper. It is the final safety net for failures from _deliver_reply.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).


### `core/src/ufo/sandbox/terminal.py`

`io_transport` · `request handling and tool execution`

A normal sandbox can be reached directly, like calling a server. A member’s own terminal is different: the server cannot open a new connection to it. It must ask over the connection the terminal already holds, then wait for the terminal’s next request to bring back the answer. This file is the meeting point for those two halves.

The central idea is a per-conversation “slot.” A slot remembers whether a terminal is connected, what directory it is in, which operation is currently waiting, any staged file bytes, and who is waiting for a reply. Because the workflow that asks for work and the web route that receives terminal messages can run on different event loops and threads, access is protected by a lock, and wakeups are sent back onto the correct event loop.

The file also defines `TerminalCarrier`, the sandbox carrier for “run this on the member’s machine.” It rewrites logical `/workspace` paths to the real directory where the user launched the tool, sends exec/read/write/file operations to the terminal, parses replies, and reports failures in the same style as other sandbox carriers. Without this file, terminal-bound work would either be lost during reconnects, race between connections, or have no safe way to match a request with its eventual answer.

#### Function details

##### `TerminalTransport.connect`  (lines 214–214)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: This is the transport contract for saying that a terminal connection is now present for a conversation. Implementations use it to record where the terminal is standing and which member owns it.

**Data flow**: It receives a conversation id, a current working directory, and an optional member id. The implementation records that binding so later sandbox work can be sent to that terminal. It returns nothing; the change is in the transport’s stored state.

**Call relations**: Surface code calls this side of the contract when a member’s held terminal stream connects. Later calls such as `arrived`, `send`, and `next_op` rely on the connection information it publishes.


##### `TerminalTransport.disconnect`  (lines 216–216)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: This is the transport contract for saying that a terminal connection has gone away. It lets the transport stop advertising a terminal that is no longer reachable.

**Data flow**: It receives a conversation id. The implementation decreases or clears the recorded connection for that conversation. It returns nothing; future sends may fail or wait for a reconnect.

**Call relations**: Surface code uses this when the held terminal stream ends. The stored state it changes is later read by `arrived`, `send`, and related methods.


##### `TerminalTransport.workspace`  (lines 218–218)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: This is the transport contract for asking, without waiting, where a conversation’s terminal is currently bound. It is a quick lookup of the terminal’s workspace.

**Data flow**: It receives a conversation id and reads the transport’s current binding. It returns a `TerminalWorkspace` if a terminal is known, or `None` if not.

**Call relations**: Code that only needs a snapshot can use this instead of waiting for a terminal to arrive. Other transport methods use the same underlying idea when deciding whether terminal work can proceed.


##### `TerminalTransport.arrived`  (lines 220–220)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: This is the transport contract for waiting until a terminal is connected. It exists because brief disconnects are normal when the client is polling or holding a stream.

**Data flow**: It receives a conversation id and a grace period in seconds. The implementation either finds an existing terminal or waits up to that time for one to connect. It returns the terminal workspace or `None` if no terminal appears.

**Call relations**: `TerminalCarrier.create`, `TerminalCarrier.attach`, and `Terminals.send` depend on this behavior so they do not fail just because the terminal is between reconnects.


##### `TerminalTransport.send`  (lines 222–231)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: This is the transport contract for asking the terminal to perform one operation and waiting for its answer. It is the main request-and-reply path from the sandbox layer to the member’s terminal.

**Data flow**: It receives the conversation, operation kind, timeout, optional operation name, argument, JSON parameters, and optional staged bytes. The implementation publishes the operation to the terminal, waits for a matching reply, and returns reply bytes or raises an error.

**Call relations**: `TerminalCarrier` methods use this contract for exec, read, write, and file operations. The terminal-facing route pairs with it by calling `next_op`, `staged`, and `resolve`.


##### `TerminalTransport.next_op`  (lines 233–235)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: This is the transport contract for the connected terminal to ask, “what should I do next?” It delivers the next pending operation for that conversation.

**Data flow**: It receives a conversation id and optionally an operation id to skip. The implementation either returns an already-waiting operation or waits until a new one is sent. The result is a `TerminalOp` describing the work.

**Call relations**: The terminal stream calls this after connecting or after answering a previous operation. It is the receiving half of work created by `send`.


##### `TerminalTransport.staged`  (lines 237–239)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: This is the transport contract for fetching bytes that belong to an in-flight operation, such as the contents of a file being written. It keeps large bytes out of the small directive message.

**Data flow**: It receives a conversation id, operation id, and optional member id. The implementation checks that the requested operation is still active and belongs to the allowed member, then returns the bytes or `None`.

**Call relations**: The terminal client uses this after `next_op` tells it about a write-like operation. It complements `send`, which stores the bytes before the client fetches them.


##### `TerminalTransport.resolve`  (lines 241–248)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: This is the transport contract for delivering the terminal’s answer to an operation. It matches a reply to the waiting sender by operation id.

**Data flow**: It receives the conversation id, operation id, reply bytes, optional failure text, and optional member id. The implementation checks that this is the current operation and wakes the sender with either bytes or a terminal-operation failure. It returns `True` if the answer was accepted, otherwise `False`.

**Call relations**: Terminal-facing routes call this after doing the work from `next_op`. It completes the `send` call that is waiting on the workflow side.


##### `TerminalTransport.in_flight`  (lines 250–250)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: This is the transport contract for inspecting the current operation, if one is waiting for an answer. It is useful for tests or operator views.

**Data flow**: It receives a conversation id and reads the transport’s current operation slot. It returns the active `TerminalOp` or `None`.

**Call relations**: This sits beside the normal request path as an observation hook. It does not advance or complete work.


##### `_wake`  (lines 253–262)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: This helper safely wakes an `asyncio` future from another thread. A future is an object representing a result that will arrive later, and it must be completed on the event loop that owns it.

**Data flow**: It receives a saved waiter: the future plus its event loop, and the answer to put into that future. It schedules a tiny callback on the right loop. The eventual result is that the waiting coroutine resumes with the answer.

**Call relations**: `Terminals.connect`, `Terminals.send`, and `Terminals.resolve` use this whenever one side of the terminal rendezvous needs to wake the other side. It is the bridge between the workflow loop and the web-serving loop.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 258–260)

```
def _set() -> None
```

**Purpose**: This inner callback completes the future if it has not already been completed. It is small but important because it runs on the future’s own event loop.

**Data flow**: It closes over the future and the answer supplied to `_wake`. When the loop runs it, it checks whether the future is still pending, then stores the answer. It returns nothing.

**Call relations**: `_wake` schedules this callback instead of touching the future directly from the wrong thread. That keeps cross-thread wakeups safe.


##### `Terminals.connect`  (lines 278–291)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None) -> None
```

**Purpose**: This records that a terminal connection has arrived for a conversation. It also wakes anyone who was waiting for the terminal to reconnect.

**Data flow**: It receives a conversation id, current directory, and optional member id. Under a lock, it creates or updates the conversation slot, increments the connection count, and collects any arrival waiters. After releasing the lock, it wakes those waiters.

**Call relations**: Surface routes call this when a member’s terminal stream opens. It feeds `arrived`, which is used by senders and by `TerminalCarrier.create` to tolerate normal reconnect gaps.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 293–300)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: This records that one terminal connection for a conversation has closed. If there is no pending operation and no connections left, it removes the slot.

**Data flow**: It receives a conversation id. Under the lock, it finds the slot, decreases its connection count, and deletes the slot only when it is idle and no connection remains. It returns nothing.

**Call relations**: Surface routes call this when a held stream ends. The careful deletion rule lets an operation survive a normal reconnect instead of being stranded.


##### `Terminals.workspace`  (lines 302–307)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: This gives an immediate snapshot of the terminal’s bound workspace. It does not wait for a reconnect.

**Data flow**: It receives a conversation id and reads the matching slot under the lock. If found, it returns a `TerminalWorkspace` containing the directory and member id; otherwise it returns `None`.

**Call relations**: This is the in-process implementation of the `TerminalTransport.workspace` contract. It is useful when callers only want to know the current binding.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 309–331)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: This waits briefly for a terminal to be connected. It prevents false failures during the normal moment when a client stream has ended and is about to reconnect.

**Data flow**: It receives a conversation id and grace period. It first checks for an existing slot; if none exists, it registers a future as an arrival waiter and waits until either `connect` wakes it or the deadline passes. It returns the workspace or `None`.

**Call relations**: `Terminals.send` calls this before sending work, and terminal carriers use the same contract when opening or attaching. If the wait times out, it asks `_drop_arrival` to remove its stale waiter.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 333–338)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: This removes an arrival waiter that no longer needs to wait. It keeps the arrival list from filling with timed-out futures.

**Data flow**: It receives a conversation id and a specific future. Under the lock, it filters that future out of the waiting list and deletes the list if it becomes empty. It returns nothing.

**Call relations**: `Terminals.arrived` calls this when its waiting period expires. It is cleanup for the reconnect-wait path.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 340–415)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: This sends one operation to a conversation’s terminal and waits for the matching answer. It is the heart of the in-process rendezvous.

**Data flow**: It receives operation details and optional staged bytes. It waits for a terminal to arrive, waits for the conversation’s one-operation-at-a-time turn, stores a new `TerminalOp` in the slot, wakes a terminal watcher if one is waiting, then waits for `resolve` to provide bytes or a failure. In all cases it clears the in-flight operation afterward and wakes queued senders.

**Call relations**: `TerminalCarrier` uses the transport’s `send` method for commands, file reads, writes, and file-system operations. On the other side, `next_op` delivers the operation and `resolve` completes it.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 5 external calls (__init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 417–448)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: This makes sure only one operation at a time is active for a conversation. It turns concurrent requests into an orderly queue instead of letting them collide.

**Data flow**: It receives a conversation id, event loop, and timeout. If the slot is free, it marks it busy and returns. If another operation is running, it waits on a queue ticket until released or until the allowed wait expires; on timeout it removes its ticket and raises `TerminalGone`.

**Call relations**: `Terminals.send` calls this before publishing an operation. When a send finishes, it wakes queued tickets so one of them can claim the next turn.

*Call graph*: called by 1 (send); 5 external calls (__init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 450–475)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: This lets the connected terminal receive the next operation it should run. It also avoids redelivering the operation that the same request just answered.

**Data flow**: It receives a conversation id and optionally an operation id to exclude. Under the lock, it returns an undelivered current operation if one is available; otherwise it records the caller as the current watcher and waits. When woken, it returns the `TerminalOp`.

**Call relations**: The terminal-facing stream calls this to pick up work created by `send`. If a new stream replaces an old one, this method ensures only the newest watcher receives the operation.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 477–489)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: This returns staged bytes for the current operation, such as file contents for a write. It only serves bytes while the exact operation is still in flight.

**Data flow**: It receives a conversation id, operation id, and optional member id. Under the lock, it checks that the slot, operation id, and member match. It returns the stored bytes or `None`.

**Call relations**: After `next_op` tells the terminal about an operation that needs extra bytes, the terminal can call this to fetch them. The bytes were placed there by `send`.


##### `Terminals.in_flight`  (lines 491–496)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: This reports the operation currently waiting for a terminal reply. It is a read-only peek into the rendezvous state.

**Data flow**: It receives a conversation id and reads the slot under the lock. It returns the active `TerminalOp` if present, otherwise `None`.

**Call relations**: This supports tests or inspection tools that need to see what the terminal side should answer. It does not wake or modify the normal send/resolve flow.


##### `Terminals.resolve`  (lines 498–527)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: This accepts the terminal’s answer to an operation and wakes the sender that is waiting for it. It rejects stale, duplicate, wrong-member, or wrong-operation replies.

**Data flow**: It receives the conversation id, operation id, reply bytes, optional failure text, and optional member id. Under the lock, it verifies that this reply matches the current unresolved operation. If accepted, it marks the operation resolved and wakes the waiting sender with either the bytes or a `TerminalOpFailed` object, then returns `True`; otherwise it returns `False`.

**Call relations**: Terminal-facing routes call this after running the operation from `next_op`. It completes the future created by `Terminals.send`.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 542–584)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This creates a sandbox handle for a terminal-bound workspace. It confirms that the connected terminal is the right directory and prepares proxy environment variables for commands run there.

**Data flow**: It receives a sandbox specification. It waits for the terminal, compares the terminal’s directory with the requested workspace, builds a proxy URL containing the run token, and returns a `SandboxHandle`. If no matching terminal is connected, it raises `TerminalGone`.

**Call relations**: The sandbox-opening flow calls this when the selected backend is the member’s terminal. Later carrier methods use the returned handle to run commands and file operations through the terminal transport.

*Call graph*: 3 external calls (__init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 586–599)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This tries to reattach to an already-bound terminal outside the main turn, for example for file browsing or off-turn writes. It does not wait; it only succeeds if the terminal is already known.

**Data flow**: It receives a sandbox specification. It asks the transport whether the terminal has arrived with zero grace time, checks that the directory matches the resume id, and returns a lightweight `SandboxHandle` or `None`.

**Call relations**: Off-turn features call this to reach the same terminal workspace. It uses the transport abstraction so the terminal may be held by another process in a shared deployment.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 601–614)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a command in the member’s terminal sandbox. It rewrites logical `/workspace` paths to the real directory before sending the command.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It finds the real workspace root, replaces `/workspace` occurrences in the command arguments with that root, and delegates to `_exec`. It returns an `ExecResult`.

**Call relations**: Higher-level sandbox code calls this when it wants to execute a command. `_exec` performs the actual terminal send and reply parsing.

*Call graph*: calls 2 internal fn (_exec, _root).


##### `TerminalCarrier._exec`  (lines 616–641)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This is the lower-level command runner for arguments that already use real host paths. It avoids doing the `/workspace` rewrite twice.

**Data flow**: It receives a handle, concrete command arguments, and timeout. It sends an exec operation with JSON parameters and environment variables, parses the JSON reply, decodes base64 stdout and stderr, and returns an `ExecResult`. A terminal-reported failure becomes a runtime error.

**Call relations**: `TerminalCarrier.exec` calls this for normal commands after path rewriting. `TerminalCarrier.file_op` also calls it to run precomputed workspace enumeration commands.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (exec, file_op); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 643–656)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This writes bytes into a file in the terminal-bound workspace. The file contents are staged separately from the operation directive.

**Data flow**: It receives a handle, logical path, and bytes. It maps the path into the real workspace, sends a write operation with the bytes as staged body, and returns nothing if successful. A terminal failure becomes an `OSError`.

**Call relations**: Sandbox file-copy code calls this when it needs to place a file on the member’s machine. The transport’s `send` stores the bytes, and the terminal later fetches them with `staged`.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 658–674)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This reads a file from the terminal-bound workspace. It presents the result as chunks so callers can consume it like other sandbox reads.

**Data flow**: It receives a handle and logical path. It maps the path into the real workspace, sends a read operation, and yields the reply bytes in fixed-size chunks. A missing file becomes `FileNotFoundError`; other terminal failures become `OSError`.

**Call relations**: Sandbox file-reading code calls this. It uses the same terminal request path as writes and execs, then adapts the single terminal reply into an async byte stream.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 676–725)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs a higher-level sandbox file-system operation, such as search, glob, or change detection, in the terminal workspace. For tree walks, it first creates the exact listing that the operation should read.

**Data flow**: It receives a handle, operation name, and parameters. It rewrites only parameters that are true workspace paths, optionally runs an enumeration command through `_exec`, then sends a file operation with JSON parameters. It parses the JSON reply, raises a value error if the reply reports an operation error, and returns the result object.

**Call relations**: Higher-level file tools call this instead of implementing terminal-specific file logic themselves. It delegates command-style enumeration to `_exec` and reply parsing to `_reply_object`.

*Call graph*: calls 4 internal fn (_exec, _reply_object, _root, _under_root); 2 external calls (dumps, quote).


##### `TerminalCarrier.dial`  (lines 727–731)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This rejects attempts to expose a port from a terminal-bound sandbox. A member’s local terminal does not provide the same external per-port address that a remote sandbox can.

**Data flow**: It receives a sandbox handle and port number, but does not use them to build a target. It raises `SandboxUnreachable` explaining that this carrier cannot be dialed by port.

**Call relations**: Code that expects all sandbox carriers to support dialing may call this through the common interface. For terminal sandboxes, it deliberately stops the flow and tells callers to use a remote carrier for that feature.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 734–742)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: This decodes one captured command stream, such as stdout or stderr, from a terminal exec reply. It treats missing or malformed data as a real protocol error rather than pretending the stream was empty.

**Data flow**: It receives a parsed reply object and a stream name. It looks for a base64 field like `stdout_b64`, decodes it into bytes, and returns those bytes. If the field is absent or invalid, it raises an error.

**Call relations**: `TerminalCarrier._exec` uses this after `_reply_object` parses the terminal’s JSON reply. It turns the client’s encoded stream fields into the bytes needed for `ExecResult`.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 745–752)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: This parses a terminal reply that should be a JSON object. It verifies the shape before the caller trusts it.

**Data flow**: It receives raw reply bytes and the operation name for error messages. It decodes the bytes as UTF-8, parses JSON, checks that the result is a dictionary-like object, and returns it. Bad JSON or the wrong shape becomes a runtime error.

**Call relations**: `TerminalCarrier._exec` uses this for exec replies, and `TerminalCarrier.file_op` uses it for file-operation replies. It is the shared gatekeeper for structured terminal responses.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 755–758)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: This returns the real host directory backing `/workspace` for a terminal sandbox. It also catches the invalid case where no such directory is recorded.

**Data flow**: It receives a sandbox handle. If the handle has a workspace host path, it returns that path; otherwise it raises an error explaining that terminal sandboxes must serve `/workspace` from the bound directory.

**Call relations**: `TerminalCarrier.exec`, `TerminalCarrier.file_op`, and `_client_path` call this before translating paths. It centralizes the assumption that terminal sandboxes always have a real local root.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 761–767)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: This maps a logical sandbox path under `/workspace` to the member’s real directory. It does the mapping only at the leading `/workspace`, not by replacing every matching word.

**Data flow**: It receives a sandbox handle and a path. It gets the real root with `_root`, then asks `_under_root` to convert the path if it starts under `/workspace`. It returns the converted path or the original path if it is outside `/workspace`.

**Call relations**: `TerminalCarrier.read` and `TerminalCarrier.write` use this before sending paths to the terminal. It protects paths like `/workspace/workspace/notes.md` from being rewritten incorrectly.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 770–775)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: This performs the actual anchored path rewrite from `/workspace/...` to `<real-root>/...`. Paths outside `/workspace` are left unchanged.

**Data flow**: It receives a real root path and a candidate path. It treats the candidate as a POSIX-style path, checks whether it is relative to `/workspace`, and if so joins the remaining part under the real root. It returns the rewritten or original string.

**Call relations**: `_client_path` uses this for read and write paths, and `TerminalCarrier.file_op` uses it for selected file-operation parameters. It is the shared low-level rule for path translation.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### CLI linking and package hooks
The CLI callback endpoint completes account linking while package markers make the REPL and UFO terminal extensions importable.

### `core/src/ufo/surfaces/cli.py`

`io_transport` · `request handling`

This file is a small HTTP surface for connecting outside accounts, such as a third-party service account, to the system. In OAuth, the user is sent away to a provider to approve access, and then the provider redirects the browser back with two important pieces: a state value and a code. The state is like a sealed claim ticket: it proves this callback belongs to a specific earlier chat request. The code is exchanged by the connection flow for the actual broker-owned account record.

The file creates a FastAPI router, which is a bundle of web routes, under the `/v1` prefix. It exposes `/v1/connect/callback` as the place providers should redirect to. When a request arrives, the endpoint first checks that the connection system is available. It then rejects the request if either required value is missing. Next it asks the installed connection flow to complete the handoff. If the sealed state is bad, the user gets a clear bad-request error. If the named provider is not installed, the request gets a not-found error.

On success, it returns a plain text message naming the connected provider and account, then instructs the user to return to chat. Without this file, browser-based account connection could start but would have nowhere to land after provider approval.

#### Function details

##### `connect_callback`  (lines 19–39)

```
async def connect_callback(state: str='', code: str='') -> PlainTextResponse
```

**Purpose**: This is the HTTP endpoint that finishes an OAuth account connection after the provider redirects the user's browser back. It checks the returned state and code, completes the connection flow, and returns a simple human-readable success message.

**Data flow**: The browser sends in `state` and `code` query values. The function asks for the installed connection flow, verifies that both values are present, and passes them to the flow's completion step. If something is wrong, it turns the problem into an HTTP error response; if everything works, it returns plain text saying which provider account was connected.

**Call relations**: FastAPI calls this function when a GET request reaches `/v1/connect/callback`. The function first calls `ufo.grants.installed_connect_flow` to find the connection machinery, raises FastAPI `HTTPException` errors when the callback cannot be completed, and uses `PlainTextResponse` to send the final success message back to the browser.

*Call graph*: 3 external calls (HTTPException, PlainTextResponse, installed_connect_flow).


### `extensions/repl/ufo_ext_repl/__init__.py`

`other` · `import/package discovery`

This file contains no code, but it still matters. In Python projects, a file named `__init__.py` tells Python that the folder should be treated as an importable package. You can think of it like putting a label on a drawer: the drawer may contain other useful files, and the label lets the rest of the system find them by name.

Here, the labeled drawer is `ufo_ext_repl`, which appears to be the package for a REPL extension. A REPL is an interactive prompt where a user can type commands and see results immediately. The actual REPL behavior lives elsewhere in the package, not in this file.

Because this file is empty, it does not set up state, load configuration, register commands, or run startup logic. Its job is simply to make imports work in environments that expect traditional Python packages. Without it, some tooling or older import mechanisms might not recognize this directory as a package, which could make the extension harder or impossible to load.


### `extensions/ufo/ufo_ext_ufo/__init__.py`

`other` · `import time`

In Python, an `__init__.py` file is like a label on a folder saying, “this folder is a package.” That matters because other parts of the project may want to import code from inside `extensions/ufo/ufo_ext_ufo` using normal Python import paths. This particular file is empty, so it does not set up defaults, expose helper functions, or run any startup behavior. Its value is structural: without it, some Python tooling or older Python import behavior might not recognize this directory as an importable package. Think of it like a blank cover page for a section of a binder. The useful pages are elsewhere, but the cover page tells readers and tools where the section begins.


### UFO shell surface
The UFO terminal surface exposes the HTTP-facing text command protocol for messages, progress, credentials, file links, terminal operations, and cleanup.

### `extensions/ufo/ufo_ext_ufo/surface.py`

`io_transport` · `request handling`

This file is the bridge between a member using the `ufo` shell command and the core UFO conversation engine. The shell client does not receive rich web pages or JSON objects. Instead, the server streams plain text lines, where each line starts with a verb like `say`, `txt`, `ask`, `run`, or `file`, followed by tab-separated fields. That is like a stage manager handing simple cue cards to a performer: print this text, ask for input, run this command, reconnect later.

A request is authenticated with a bearer token, which is a signed proof of the user’s email and workspace. The main `channel` route then finds or creates the user’s conversation for the named channel. If the request contains a message, it admits that message into the durable conversation queue. If the request is empty, it resumes watching the latest turn. If it is a secret, stop, send, unsend, or terminal-operation reply, it follows a special path.

The core job is streaming. `stream_directives` tails live frames from the running turn and converts them into directives the shell can read. It keeps the HTTP connection open for a limited time, then tells the client to poll again instead of letting `curl` time out. It also supports terminal operations: when the agent needs the member’s machine to run or fetch something, the stream emits a `run` directive and waits for the next request to bring back the result.

#### Function details

##### `directive`  (lines 91–99)

```
def directive(verb: str, *fields: str) -> bytes
```

**Purpose**: Builds one line of the tiny text protocol that the shell client understands. It safely escapes tabs, newlines, and backslashes so user text cannot accidentally break the line format.

**Data flow**: It receives a command word and any number of text fields. It cleans each field for safe one-line transport, joins everything with tabs, adds a newline, and returns bytes ready to send over HTTP.

**Call relations**: This is the common formatter used wherever the file needs to speak to the shell. Higher-level code such as `directives_for`, `_answer`, `_send`, `_fulfill_secret`, `history_directives`, `channel`, and `stream_directives` decide what should happen; `directive` turns that decision into the actual wire line.

*Call graph*: called by 8 (_answer, _fulfill_secret, _say_lines, _send, channel, directives_for, history_directives, stream_directives).


##### `shared_files`  (lines 113–124)

```
async def shared_files(ctx: SurfaceContext, turn_id: UUID) -> tuple[SharedFile, ...]
```

**Purpose**: Collects the files a completed turn shared and prepares them for display in the terminal. It adds a usable download link when the deployment is configured to provide one.

**Data flow**: It receives the surface context and a turn id. It asks the core context for that turn’s shared artifacts, turns each artifact into a `SharedFile` with filename, size, and public URL, and returns them as a tuple.

**Call relations**: The streaming path calls this near the end of a turn through `stream_directives`. It relies on `SurfaceContext.shared_artifacts` for the stored file records and `SurfaceContext.artifact_link` for the public link the shell can show.

*Call graph*: calls 2 internal fn (artifact_link, shared_artifacts); 1 external calls (__init__).


##### `resolve_workspace`  (lines 127–134)

```
async def resolve_workspace(request: Request, _auth: SurfaceAuth) -> UUID | None
```

**Purpose**: Identifies which workspace an incoming request claims to belong to before the route handler runs. If the request does not carry a valid-looking bearer authorization header, it rejects the request by returning no workspace.

**Data flow**: It reads the HTTP `Authorization` header, extracts the bearer token, and asks the token helper for the workspace claim. The result is a workspace UUID or `None`.

**Call relations**: This is used by the shared surface routing layer as the early workspace lookup. The later route code verifies the same token again for the user email, so workspace scoping and user identity come from the same signed token.

*Call graph*: 1 external calls (workspace_claim).


##### `history_directives`  (lines 140–160)

```
def history_directives(conversation: Conversation) -> tuple[bytes, ...]
```

**Purpose**: Turns earlier conversation messages into terminal lines for a client that reconnects without a live cursor. It gives the user enough recent context without replaying the current answer twice.

**Data flow**: It receives a conversation transcript. It extracts readable text from each message, drops trailing assistant replies that will be replayed from live frames, keeps the newest messages within a character budget, and returns `you` or `say` directive bytes.

**Call relations**: The `channel` route calls this when an empty request resumes a conversation from scratch. It uses `_history_text` to normalize message content and `directive` to create the terminal protocol lines.

*Call graph*: calls 2 internal fn (_history_text, directive); called by 1 (channel).


##### `_history_text`  (lines 163–168)

```
def _history_text(message) -> str
```

**Purpose**: Extracts plain display text from one saved conversation message. For user messages, it strips them down to the member-facing message text format.

**Data flow**: It receives a message that may contain either a plain string or structured text blocks. It joins only text blocks when needed, then returns cleaned user text for user messages or raw assistant text for assistant messages.

**Call relations**: This helper is used only by `history_directives`. It hides the difference between simple and structured message content so history rendering can treat everything as plain text.

*Call graph*: called by 1 (history_directives); 1 external calls (member_message_text).


##### `directives_for`  (lines 171–201)

```
def directives_for(frame: LiveFrame, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=()) -> tuple[bytes, ...]
```

**Purpose**: Converts one live engine frame into one or more shell directives. A frame is a small update from the running turn, such as new text, a tool starting, a cost update, or the final result.

**Data flow**: It receives a live frame plus context such as whether text was already streamed, pending credential prompts, a connection message, and shared files. It pattern-matches the frame type and returns the terminal lines that should be sent to the client.

**Call relations**: `stream_directives` calls this for each frame it reads from the turn. It delegates tool narration to `_activity`, final turn rendering to `_answer`, and line formatting to `directive`.

*Call graph*: calls 3 internal fn (_activity, _answer, directive); called by 1 (stream_directives).


##### `_activity`  (lines 204–206)

```
def _activity(frame: ToolCall) -> str
```

**Purpose**: Creates a short human-readable status line for a tool call. It tells the terminal what tool is running and, when available, what it is doing.

**Data flow**: It receives a tool-call frame. It chooses the best available detail text from the frame, combines it with the tool name, and returns a sentence like `running search: ...`.

**Call relations**: `directives_for` uses this when it sees a tool-call frame. The returned text is then wrapped into a `note` directive for the shell.

*Call graph*: called by 1 (directives_for).


##### `_answer`  (lines 209–257)

```
def _answer(terminal: Terminal, streamed: bool, collect: tuple[CredentialPrompt, ...]=(), connect_message: str | None=None, files: tuple[SharedFile, ...]=()) -> tuple[bytes, ...]
```

**Purpose**: Builds the closing directives for a completed, failed, or cancelled turn. This is where the terminal learns whether to show the final answer, ask for secrets, show shared files, prompt again, or exit.

**Data flow**: It receives a terminal frame and extra end-of-turn context: whether answer text was already streamed, which credential prompts still need answers, an optional connection URL message, and shared files. It returns the right sequence of `say`, `file`, `secret`, `ask`, or `exit` lines.

**Call relations**: `directives_for` calls this for terminal frames. `_answer` uses `_say_lines` for multi-line text and `directive` for all final protocol lines.

*Call graph*: calls 2 internal fn (_say_lines, directive); called by 1 (directives_for).


##### `_say_lines`  (lines 260–261)

```
def _say_lines(text: str) -> tuple[bytes, ...]
```

**Purpose**: Splits display text into separate `say` lines for the terminal. This keeps multi-line messages aligned with the line-based protocol.

**Data flow**: It receives a text string. It splits it on line breaks, or keeps one empty-looking line if needed, and returns one `say` directive per line.

**Call relations**: `_answer` calls this whenever final answer text, an error message, or a cancellation reason needs to be printed to the user.

*Call graph*: calls 1 internal fn (directive); called by 1 (_answer).


##### `stream_directives`  (lines 264–401)

```
async def stream_directives(tail: AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]], hold_seconds: float, pending: Callable[[str, str], Awaitable[bool]] | None=None, connect: Callable[
```

**Purpose**: Streams live turn updates to the shell while keeping the HTTP request open only for a safe amount of time. It also pauses the stream when the agent needs the member’s local terminal to perform an operation.

**Data flow**: It receives an async source of live frames, a hold timeout, callbacks for credential checks, connection URLs, shared files, terminal operations, cursor state, and move-on detection. It reads frames until the turn ends, an operation needs to run, the source ends, or time runs out; it yields protocol lines as bytes and may finish with `poll` so the client reconnects cleanly.

**Call relations**: `channel` creates this stream after admitting or resuming a turn. Inside the loop it uses `_next` to safely read frames, `directives_for` to render frames, and `directive` to emit cursor, polling, and local-run instructions.

*Call graph*: calls 3 internal fn (_next, directive, directives_for); called by 1 (channel); 4 external calls (ensure_future, get_running_loop, wait, suppress).


##### `_next`  (lines 404–410)

```
async def _next(frames: AsyncIterator[tuple[str, LiveFrame]]) -> tuple[str, LiveFrame] | None
```

**Purpose**: Reads the next item from an async frame stream without letting end-of-stream escape as an exception. This makes the streaming loop easier and safer to race against timeouts and terminal operations.

**Data flow**: It receives an async iterator of cursor-and-frame pairs. It awaits the next pair and returns it, or returns `None` if the iterator is finished.

**Call relations**: `stream_directives` wraps frame reads in tasks and calls `_next` inside those tasks. That lets the main loop treat a finished stream as ordinary data instead of special exception control flow.

*Call graph*: called by 1 (stream_directives).


##### `_authenticated_email`  (lines 413–417)

```
def _authenticated_email(request: Request, workspace_id: UUID) -> str | None
```

**Purpose**: Verifies the request’s bearer token and extracts the user email. If the authorization header is missing, malformed, or invalid for this workspace, it returns no email.

**Data flow**: It reads the `Authorization` header, extracts the bearer token, and passes it with the workspace id to the token verifier. The output is an email string or `None`.

**Call relations**: Both `channel` and `op_body` call this at the start of request handling. It is the gate that keeps unauthenticated users from sending messages, reading operation bodies, or fulfilling terminal work.

*Call graph*: called by 2 (channel, op_body); 1 external calls (verify_token).


##### `_utf8_header`  (lines 420–427)

```
def _utf8_header(request: Request, name: str) -> str
```

**Purpose**: Recovers a header value that the shell sent as UTF-8 bytes, such as a current working directory with non-ASCII characters. It works around the fact that HTTP header handling often treats bytes as ISO-8859-1 text.

**Data flow**: It reads the named header as text, re-encodes it as latin-1 bytes to recover the original byte values, then decodes those bytes as UTF-8. The result is the intended string, with replacement characters if decoding fails.

**Call relations**: `channel` uses this for headers like the current directory and operation error text. That keeps terminal paths and messages readable even when they contain non-English characters.

*Call graph*: called by 1 (channel).


##### `_stale_client`  (lines 430–436)

```
def _stale_client(request: Request) -> bool
```

**Purpose**: Checks whether the shell script version making the request is older or different from the version the server is currently serving. If so, the server can tell the client to reinstall or update.

**Data flow**: It reads the server’s expected client version from the `UFO_CLIENT_VERSION` environment variable and compares it with the request’s `x-ufo-script` header. It returns true only when the server has a version set and the client does not match it.

**Call relations**: `channel` calls this before normal message or resume streaming. When it returns true, `channel` prepends an `install` directive to the next ordinary stream.

*Call graph*: called by 1 (channel).


##### `_resumed_from`  (lines 439–446)

```
def _resumed_from(request: Request, turn_id: UUID) -> str
```

**Purpose**: Validates the client’s resume cursor for a specific turn. It prevents a cursor from an older turn from accidentally skipping frames in a newer turn.

**Data flow**: It reads the `x-ufo-since` header, splits it into a turn id and cursor, and compares the named turn with the current turn id. It returns the cursor only if the turn matches; otherwise it returns an empty cursor.

**Call relations**: `channel` calls this before opening the live tail. The resulting cursor is passed into `ctx.tail` and `stream_directives`, so reconnects continue from the right point.

*Call graph*: called by 1 (channel).


##### `_turn_context`  (lines 449–461)

```
def _turn_context(email: str, request: Request) -> TurnContext
```

**Purpose**: Builds the context attached to a newly admitted user message. It records who sent it, where it came from, and the user’s timezone when the client reports one.

**Data flow**: It receives the sender email and request. It reads the timezone header, creates a `TurnContext`, and if the timezone is invalid it logs that fact and falls back to a context without timezone.

**Call relations**: `channel` and `_send` call this just before admitting a message to the core conversation. The core then has member-friendly context for time-sensitive behavior and auditing.

*Call graph*: called by 2 (_send, channel); 2 external calls (__init__, log).


##### `channel`  (lines 464–583)

```
async def channel(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Handles the main POST endpoint used by the UFO shell client for a conversation channel. It authenticates the user, accepts messages or control requests, and returns either a short plain response or a live stream of directives.

**Data flow**: It receives the surface context and HTTP request. It verifies the user, links or finds the member, routes special cases such as secret fulfillment, sends, unsends, stops, and terminal-operation replies, admits new messages when present, resumes existing turns when the body is empty, prepares history and cursor state, and returns a streaming text response for the shell.

**Call relations**: This is the central request handler named in `ROUTES`. It calls many `SurfaceContext` methods to find conversations, admit turns, stop turns, resolve terminal operations, read transcripts, and tail live frames. It delegates focused work to `_fulfill_secret`, `_send`, `_unsend`, `_turn_context`, `_resumed_from`, `history_directives`, `shared_files`, and `stream_directives`.

*Call graph*: calls 21 internal fn (admit, claim_terminal, conversation_for, latest_turn, link_member, linked_member, read_transcript, stop_turn, tail, terminal_resolve (+11 more)); 5 external calls (partial, conversation_audience, PlainTextResponse, body, StreamingResponse).


##### `channel.moved_on`  (lines 551–553)

```
async def moved_on() -> bool
```

**Purpose**: Checks whether the conversation has advanced to a newer non-finished turn after the current stream’s turn ended. This helps the client reconnect to the right live work instead of replaying old history.

**Data flow**: It reads the latest turn id from the conversation, compares it with the turn currently being streamed, and asks whether the latest turn is terminal. It returns true when there is a different latest turn that is still live.

**Call relations**: `channel` passes this inner helper into `stream_directives`. At the end of a terminal frame, `stream_directives` uses it to decide whether to emit an immediate `poll` with cursor information.


##### `channel.bound`  (lines 567–581)

```
async def bound() -> AsyncIterator[bytes]
```

**Purpose**: Wraps the outgoing stream with terminal connection setup and cleanup. It also sends any update notice, replayed history, and workspace note before live turn updates.

**Data flow**: It connects the conversation to the member’s current directory when one was provided, then yields queued prefix lines followed by each live directive. When streaming stops for any reason, it disconnects the terminal binding.

**Call relations**: `channel` returns a `StreamingResponse` built from this inner async generator. It is the final layer around `stream_directives`, making sure local terminal availability is registered only while this HTTP stream is alive.


##### `_send`  (lines 586–636)

```
async def _send(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, email: str, cwd: str) -> Response
```

**Purpose**: Accepts a message in a quick, non-streaming request. This lets a member send another message while a previous streaming request is still open.

**Data flow**: It receives the context, request, conversation id, member id, email, and current directory. It validates the send id and message body, optionally claims the terminal workspace, admits the message with an idempotency key so retries do not duplicate it, and returns a `sent` acknowledgement with the turn id and arrival id.

**Call relations**: `channel` calls this when the request has the `x-ufo-send` header. It uses `_turn_context` for admission context, `SurfaceContext.admit` to place the message into the conversation, and `directive` to format the acknowledgement.

*Call graph*: calls 4 internal fn (admit, claim_terminal, _turn_context, directive); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_unsend`  (lines 639–661)

```
async def _unsend(ctx: SurfaceContext, request: Request, conversation_id: UUID, member_id: UUID | None, unsend: str) -> Response
```

**Purpose**: Tries to retract a queued message before the agent has taken it up. This supports the user experience of taking back a message that is still waiting.

**Data flow**: It receives the request, conversation id, member id, and arrival id text. It rejects requests with a body, requires a real member, parses the arrival id as a UUID, asks the core to retract it, and returns either empty success or a conflict message.

**Call relations**: `channel` calls this when the `x-ufo-unsend` header is present. The actual ownership and pending-status check is done by `SurfaceContext.retract_arrival`.

*Call graph*: calls 1 internal fn (retract_arrival); called by 1 (channel); 3 external calls (PlainTextResponse, body, UUID).


##### `_fulfill_secret`  (lines 664–683)

```
async def _fulfill_secret(ctx: SurfaceContext, request: Request, member_id: UUID | None, sealed: str) -> Response
```

**Purpose**: Stores a credential value that the user typed privately in response to a secret prompt. The value is not admitted as a chat message and does not enter the transcript.

**Data flow**: It receives the sealed credential request id from the header, reads the slot name and body value, validates that both are present and not too large, asks the core to fulfill the credential request, and returns a `say` line reporting success or failure.

**Call relations**: `channel` calls this before normal conversation routing when it sees the secret header. It relies on `SurfaceContext.fulfill_credential_request` to verify the sealed request and store the value, and uses `directive` for the terminal message.

*Call graph*: calls 2 internal fn (fulfill_credential_request, directive); called by 1 (channel); 2 external calls (PlainTextResponse, body).


##### `op_body`  (lines 686–698)

```
async def op_body(ctx: SurfaceContext, request: Request) -> Response
```

**Purpose**: Serves the raw bytes for an in-flight terminal operation. This is used when the shell needs to download the body of an operation, such as data to write into a temporary file.

**Data flow**: It authenticates the request, finds the linked member, builds the same queue key used for the channel, asks the core for the operation body by channel and operation id, and returns the bytes as an octet-stream or a 404 if missing.

**Call relations**: This is the GET handler named in `ROUTES`. It shares authentication with `channel` through `_authenticated_email` and reads operation data through `SurfaceContext.terminal_op_body`; it does not admit messages or change the transcript.

*Call graph*: calls 3 internal fn (linked_member, terminal_op_body, _authenticated_email); 2 external calls (PlainTextResponse, Response).
