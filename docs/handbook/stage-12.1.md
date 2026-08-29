# Sandbox carrier implementations and terminal transports  `stage-12.1`

This stage is the system’s “workshop selector and wiring.” It sits behind the scenes when a conversation needs a place to run commands, edit files, serve a web port, or show a live terminal. The selector chooses the sandbox carrier, meaning the kind of workspace to use, and keeps old choices available so paused work can resume.

The local carrier runs commands in an ordinary folder on the host computer, useful for simple development without containers. The Docker carrier gives each conversation its own container, a more isolated mini-computer, and manages creating it, copying files, running commands, exposing ports, and cleaning up idle ones. The E2B carrier does the same kind of work on a remote cloud sandbox service. The member-terminal carrier lets a user’s own connected terminal act as the sandbox, with safe request-and-reply handling even if the browser connection blips. The Redis stream terminal adds a transport bridge for multi-pod deployments, passing small control messages through Redis Streams and larger terminal data through blob storage.

## Files in this stage

### Terminal relays
Member-terminal sandbox execution and Redis-backed stream relaying keep terminal sessions usable across reconnects and pods.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_terminal.py`

`io_transport` · `request handling and long-held terminal streams`

In a single-process setup, a workflow can talk directly to the terminal connection it is holding. In a fleet of pods, that assumption breaks: the user's long-held terminal stream may be on one pod while the workflow turn that wants to run a command is on another. This file is the meeting place between them.

It publishes a short-lived Redis binding that says, “this conversation currently has a terminal, here is its workspace.” A background heartbeat refreshes that binding while the client is connected. When a workflow wants to run an operation, it waits briefly for that binding, takes a per-conversation lock so only one operation runs at a time, stores any input bytes in the blob store, and writes an operation record to a Redis Stream. The connection pod reads that stream, claims one operation so reconnects do not run it twice, renders it to the client, and later posts a reply. Small replies go through Redis; large replies go through the blob store.

The file is careful about failure. Every wait has a deadline, Redis keys expire, and cleanup is best-effort. This matters because a broken socket, crashed pod, or lost client should turn into a clear “terminal gone” error, not leave a workflow stuck forever.

#### Function details

##### `_text`  (lines 55–58)

```
def _text(value: bytes | str) -> str
```

**Purpose**: Turns a Redis field into normal Python text. Redis may return either bytes or strings, and the rest of this file wants one predictable shape.

**Data flow**: It receives one Redis value. If it is already text, it returns it unchanged; if it is bytes, it decodes the bytes into text. Nothing else is changed.

**Call relations**: Many later steps use this as a small adapter before reading Redis data: operation decoding, reply decoding, binding reads, gate checks, and Lua-script results all pass through it so they do not care which Redis response type they got.

*Call graph*: called by 7 (_decode_op, _decode_reply, _gate_ok, _read_binding, _run_op, next_op, _pairs).


##### `_pairs`  (lines 61–66)

```
def _pairs(flat: object) -> _StreamFields
```

**Purpose**: Converts Redis's flat list of stream fields into a simple field-to-value map. This makes a stream entry easier to read after the Lua claim script returns it.

**Data flow**: It receives a list shaped like field, value, field, value. It converts each item to text and builds a dictionary from each field to its matching value. If the input is not a list, it raises an error instead of guessing.

**Call relations**: RedisTerminals.next_op uses this after the Lua script finds an operation. _pairs relies on _text so the later operation decoder receives clean text-like fields.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op).


##### `_bind_payload`  (lines 69–74)

```
def _bind_payload(cwd: str, member_id: UUID | None, runtime_id: str) -> str
```

**Purpose**: Builds the small JSON message that describes where a terminal is connected and who it belongs to. Both the live binding and the in-flight binding use this same shape.

**Data flow**: It receives a current working directory, an optional member ID, and a runtime ID. It turns them into a JSON string, using the member ID's hex form when present. The result is stored in Redis.

**Call relations**: RedisTerminals._heartbeat uses it to publish the live terminal binding. RedisTerminals._run_op uses it again to pin the same binding while an operation is in progress.

*Call graph*: called by 2 (_heartbeat, _run_op); 1 external calls (dumps).


##### `_stream_entries`  (lines 148–157)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts entries from the particular Redis XREAD response shape this code expects. It protects the code from silently misreading an unexpected Redis protocol response.

**Data flow**: It receives the raw result of an XREAD call. Empty results become an empty list. A normal response is unpacked to the stream entries. An unexpected response type raises an error.

**Call relations**: RedisTerminals._await_reply uses this while waiting for a reply stream. It is the small parsing step between Redis's wire-shaped response and the reply decoder.

*Call graph*: called by 1 (_await_reply).


##### `RedisTerminals._client`  (lines 185–202)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the current asyncio event loop. This matters because asyncio clients are tied to the loop that created them, and this transport is used from different loops.

**Data flow**: It reads the currently running event loop. If a Redis client already exists for that loop, it returns it. Otherwise it creates one from the configured Redis URL with explicit socket timeouts, stores it, and returns it.

**Call relations**: Almost every Redis operation goes through this method. It is used by binding reads, heartbeats, operation sending, reply waiting, reply delivery, cleanup, staging, and operation polling so all of them share the same per-loop connection discipline.

*Call graph*: called by 9 (_await_reply, _clear_op, _deliver_reply, _heartbeat, _read_binding, _run_op, next_op, send, staged); 2 external calls (get_running_loop, from_url).


##### `RedisTerminals._bind_key`  (lines 204–205)

```
def _bind_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key name for the live terminal binding of a conversation.

**Data flow**: It receives a conversation ID and formats it into the key string used for the heartbeat-published binding. It does not read or write Redis itself.

**Call relations**: RedisTerminals._heartbeat uses this key when refreshing liveness. RedisTerminals._read_binding checks the same key first when another pod wants to know whether a terminal is connected.

*Call graph*: called by 2 (_heartbeat, _read_binding).


##### `RedisTerminals._inflight_key`  (lines 207–208)

```
def _inflight_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis key name for the temporary binding kept alive while an operation is running.

**Data flow**: It receives a conversation ID and returns the matching in-flight key string. It only names the storage location.

**Call relations**: RedisTerminals._run_op writes this key so other accessors can still see the terminal during a long operation. RedisTerminals._read_binding falls back to it, and RedisTerminals._clear_op deletes it during cleanup.

*Call graph*: called by 3 (_clear_op, _read_binding, _run_op).


##### `RedisTerminals._op_stream`  (lines 210–211)

```
def _op_stream(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis Stream name that carries operations for one conversation.

**Data flow**: It receives a conversation ID and returns the stream key where operation records are written and read. It has no side effects.

**Call relations**: RedisTerminals._run_op writes operations to this stream, RedisTerminals.next_op reads and claims them for the held terminal stream, and RedisTerminals._clear_op removes a completed entry when possible.

*Call graph*: called by 3 (_clear_op, _run_op, next_op).


##### `RedisTerminals._reply_stream`  (lines 213–214)

```
def _reply_stream(self, op_id: str) -> str
```

**Purpose**: Builds the Redis Stream name where one operation's reply will appear.

**Data flow**: It receives an operation ID and returns the reply stream key for that operation. It only constructs the name.

**Call relations**: RedisTerminals._await_reply waits on this stream, RedisTerminals._deliver_reply writes to it, and RedisTerminals._clear_op removes it after the operation is done.

*Call graph*: called by 3 (_await_reply, _clear_op, _deliver_reply).


##### `RedisTerminals._lock_key`  (lines 216–217)

```
def _lock_key(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the Redis lock key that keeps a conversation to one terminal operation at a time.

**Data flow**: It receives a conversation ID and returns the lock key string. The actual locking happens elsewhere.

**Call relations**: RedisTerminals.send uses this key before posting an operation, so two workflows do not ask the same user terminal to run commands at the same time.

*Call graph*: called by 1 (send).


##### `RedisTerminals._deliv_key`  (lines 219–220)

```
def _deliv_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key for the delivery marker that says an operation has already been handed to a terminal stream.

**Data flow**: It receives an operation ID and returns the marker key. It does not set or delete the marker itself.

**Call relations**: The Lua script in RedisTerminals.next_op creates these markers to stop duplicate delivery. RedisTerminals._clear_op later deletes the marker during cleanup.

*Call graph*: called by 1 (_clear_op).


##### `RedisTerminals._opmeta_key`  (lines 222–223)

```
def _opmeta_key(self, op_id: str) -> str
```

**Purpose**: Builds the Redis key for operation metadata, which records the conversation and member allowed to use the operation.

**Data flow**: It receives an operation ID and returns the metadata key string. It only names the record.

**Call relations**: RedisTerminals._run_op writes metadata before posting an operation. RedisTerminals.staged and RedisTerminals._deliver_reply read it to check whether a copy-in or reply request is allowed, and RedisTerminals._clear_op removes it afterward.

*Call graph*: called by 4 (_clear_op, _deliver_reply, _run_op, staged).


##### `RedisTerminals._body_blob`  (lines 225–226)

```
def _body_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store path for input bytes attached to an operation.

**Data flow**: It receives an operation ID and returns the blob key where the operation body is stored. It does not touch the blob store itself.

**Call relations**: RedisTerminals._run_op stores copy-in bytes under this key. RedisTerminals.staged reads them for the terminal-side request, and RedisTerminals._clear_op deletes them when the operation ends.

*Call graph*: called by 3 (_clear_op, _run_op, staged).


##### `RedisTerminals._reply_blob`  (lines 228–229)

```
def _reply_blob(self, op_id: str) -> str
```

**Purpose**: Builds the blob-store path for a large reply body.

**Data flow**: It receives an operation ID and returns the blob key for that operation's reply bytes. It only formats the name.

**Call relations**: RedisTerminals._deliver_reply writes large replies under this key, RedisTerminals._decode_reply reads them when the sender sees a blob marker, and RedisTerminals._clear_op deletes them during cleanup.

*Call graph*: called by 3 (_clear_op, _decode_reply, _deliver_reply).


##### `RedisTerminals.connect`  (lines 231–253)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that this pod is currently holding a terminal connection for a conversation. It starts the heartbeat that publishes the terminal's workspace into Redis.

**Data flow**: It receives the conversation, current working directory, optional member ID, and optional runtime ID. Under a thread lock, it either creates a new local hold and starts a heartbeat task, or increments the count for an existing hold. It returns nothing but changes local state and starts background Redis refreshes.

**Call relations**: This is called by the held terminal stream when a client connects. It creates the _Hold record and launches RedisTerminals._heartbeat, which keeps other pods able to find the terminal.

*Call graph*: calls 1 internal fn (_heartbeat); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals.disconnect`  (lines 255–264)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Records that one held terminal connection has ended. When the last local holder leaves, it stops the heartbeat.

**Data flow**: It receives a conversation ID. Under a thread lock, it finds the local hold, decreases its connection count, and if none remain, cancels the heartbeat task and removes the hold. It deliberately does not delete the Redis binding.

**Call relations**: This is the counterpart to RedisTerminals.connect. By cancelling the heartbeat but leaving the key to expire naturally, it avoids deleting a fresh binding that another reconnecting pod may have just published.


##### `RedisTerminals._heartbeat`  (lines 266–280)

```
async def _heartbeat(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str) -> None
```

**Purpose**: Keeps the Redis binding alive while this pod holds a terminal connection. The binding is like a signpost saying where the terminal currently is.

**Data flow**: It receives the conversation and workspace details. It builds the JSON binding, then repeatedly writes it to Redis with a time-to-live and sleeps before refreshing it again. Redis errors are ignored for a single beat so the loop can try again.

**Call relations**: RedisTerminals.connect starts this as a background task. It uses _bind_payload to make the value, _bind_key to choose the Redis key, and _client to write it.

*Call graph*: calls 3 internal fn (_bind_key, _client, _bind_payload); called by 1 (connect); 2 external calls (sleep, suppress).


##### `RedisTerminals.workspace`  (lines 282–295)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the workspace for a terminal connection held on this same pod. It is a local, no-network lookup.

**Data flow**: It receives a conversation ID. Under a lock, it checks the local holds table. If found, it returns a TerminalWorkspace with the stored directory, member, and runtime; otherwise it returns None.

**Call relations**: This is useful on the pod that actually owns the held connection. Cross-pod send paths do not rely on it; they use RedisTerminals.arrived and RedisTerminals._read_binding instead.

*Call graph*: 1 external calls (__init__).


##### `RedisTerminals.arrived`  (lines 297–310)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits briefly for a terminal binding to appear in Redis. This covers normal reconnect gaps where the client is between held streams.

**Data flow**: It receives a conversation ID and a grace period. Until the deadline, it repeatedly asks _read_binding for a workspace. If one appears, it returns it; if time runs out, it returns None.

**Call relations**: RedisTerminals.send calls this before trying to run an operation. It delegates the actual Redis lookup to RedisTerminals._read_binding and sleeps between polls.

*Call graph*: calls 1 internal fn (_read_binding); called by 1 (send); 2 external calls (get_running_loop, sleep).


##### `RedisTerminals._read_binding`  (lines 312–330)

```
async def _read_binding(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Reads the terminal workspace from Redis, either from the live connection binding or from the temporary in-flight binding.

**Data flow**: It receives a conversation ID. It first reads the live binding key; if missing, it reads the in-flight key. If neither exists, it returns None. Otherwise it parses the JSON and returns a TerminalWorkspace.

**Call relations**: RedisTerminals.arrived calls this while waiting for a terminal. It uses _bind_key and _inflight_key to find the records, _client to read Redis, and _text plus JSON parsing to turn the stored payload back into a workspace.

*Call graph*: calls 4 internal fn (_bind_key, _client, _inflight_key, _text); called by 1 (arrived); 3 external calls (__init__, loads, UUID).


##### `RedisTerminals.send`  (lines 332–388)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Sends one operation to a conversation's terminal and waits for the answer. It is the main workflow-side entry point for running terminal work across pods.

**Data flow**: It receives the conversation, operation kind, timeout, optional names and parameters, and optional input bytes. It waits for a binding, creates an operation ID, takes the per-conversation Redis lock, runs the operation posting and reply wait, and returns reply bytes. If the terminal is absent, unreachable, busy too long, or silent past the deadline, it raises a terminal-specific error.

**Call relations**: This method ties together the sender side: it calls RedisTerminals.arrived, uses _lock_key and _client for the Redis lock, then hands the real operation work to RedisTerminals._run_op. It is balanced by RedisTerminals.next_op on the terminal-stream side and RedisTerminals.resolve on the reply side.

*Call graph*: calls 4 internal fn (_client, _lock_key, _run_op, arrived); 6 external calls (__init__, __init__, __init__, wait_for, suppress, uuid4).


##### `RedisTerminals._run_op`  (lines 390–436)

```
async def _run_op(self, conversation_id: UUID, op: TerminalOp, body: bytes | None, bound: TerminalWorkspace, deadline_s: float) -> bytes
```

**Purpose**: Performs the actual send after the conversation lock is held. It publishes the operation, stages any input body, waits for the reply, and cleans up.

**Data flow**: It receives the conversation, a TerminalOp, optional body bytes, the bound workspace, and the reply deadline. It writes operation metadata and an in-flight binding to Redis, stores the body in the blob store if present, adds an operation entry to the Redis Stream, waits for a reply, and finally clears operation state. The normal output is reply bytes.

**Call relations**: RedisTerminals.send calls this once it has the lock. It uses helper methods to name keys and format fields, calls RedisTerminals._await_reply for the answer, and always calls RedisTerminals._clear_op in the end.

*Call graph*: calls 10 internal fn (_await_reply, _body_blob, _clear_op, _client, _inflight_key, _op_fields, _op_stream, _opmeta_key, _bind_payload, _text); called by 1 (send); 2 external calls (wait_for, dumps).


##### `RedisTerminals._op_fields`  (lines 438–446)

```
def _op_fields(self, op: TerminalOp) -> dict[FieldT, EncodableT]
```

**Purpose**: Turns a TerminalOp into the field dictionary stored in the Redis operation stream.

**Data flow**: It receives a TerminalOp and returns a dictionary containing its ID, kind, timeout, name, argument, and parameters as Redis-encodable values. It does not write anything itself.

**Call relations**: RedisTerminals._run_op calls this just before adding the operation to Redis. RedisTerminals._decode_op performs the reverse conversion on the reader side.

*Call graph*: called by 1 (_run_op).


##### `RedisTerminals._decode_op`  (lines 448–456)

```
def _decode_op(self, fields: _StreamFields) -> TerminalOp
```

**Purpose**: Turns Redis stream fields back into a TerminalOp object for the terminal connection to render.

**Data flow**: It receives a field map from Redis. It reads and converts the operation ID, kind, timeout, and optional text fields, then returns a TerminalOp.

**Call relations**: RedisTerminals.next_op calls this after the Lua claim script has selected an operation and _pairs has shaped its fields. It is the counterpart to RedisTerminals._op_fields.

*Call graph*: calls 1 internal fn (_text); called by 1 (next_op); 2 external calls (__init__, get).


##### `RedisTerminals._await_reply`  (lines 458–481)

```
async def _await_reply(self, op_id: str, deadline_s: float, timeout_s: int) -> bytes
```

**Purpose**: Waits for the reply to one operation without waiting forever. It repeatedly blocks on the operation's reply stream only up to the remaining deadline.

**Data flow**: It receives an operation ID, a deadline budget, and the user-facing timeout. It reads from the reply stream until an entry appears or time runs out. When a reply entry arrives, it passes the fields to _decode_reply and returns the resulting bytes; on timeout or Redis failure it raises TerminalGone.

**Call relations**: RedisTerminals._run_op calls this after posting an operation. It uses _reply_stream to choose the stream, _stream_entries to parse Redis's response, and RedisTerminals._decode_reply to turn the reply record into bytes or an operation failure.

*Call graph*: calls 4 internal fn (_client, _decode_reply, _reply_stream, _stream_entries); called by 1 (_run_op); 2 external calls (__init__, get_running_loop).


##### `RedisTerminals._decode_reply`  (lines 483–496)

```
async def _decode_reply(self, op_id: str, fields: _StreamFields) -> bytes
```

**Purpose**: Interprets one reply record from Redis. It understands failed operations, small inline replies, and large replies stored in the blob store.

**Data flow**: It receives an operation ID and reply fields. If the fields contain a failure message, it raises TerminalOpFailed. If they point to a blob, it reads that blob and returns its bytes. Otherwise it base64-decodes the inline reply and returns the bytes.

**Call relations**: RedisTerminals._await_reply calls this when a reply stream entry arrives. It uses _reply_blob for large replies and _text to normalize Redis field values.

*Call graph*: calls 2 internal fn (_reply_blob, _text); called by 1 (_await_reply); 5 external calls (__init__, __init__, get, wait_for, b64decode).


##### `RedisTerminals.next_op`  (lines 498–529)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Waits for the next operation that should be delivered to the held terminal stream. It also claims the operation so reconnects or duplicate streams do not run it twice.

**Data flow**: It receives a conversation ID and optionally an operation ID to skip. It runs a Redis Lua script that scans the operation stream, removes expired entries, skips excluded or already-delivered operations, and marks one as delivered. If an operation is found, it returns a TerminalOp; otherwise it waits briefly for more stream data and tries again. Redis failures become TerminalGone.

**Call relations**: This is the terminal-stream side of RedisTerminals.send. It uses _op_stream for the stream name, _pairs and _decode_op to turn the claimed Redis fields into a TerminalOp, and _text to interpret script results.

*Call graph*: calls 5 internal fn (_client, _decode_op, _op_stream, _pairs, _text); 1 external calls (__init__).


##### `RedisTerminals.staged`  (lines 531–546)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Fetches input bytes that were staged for an in-flight operation. It only returns them if the request matches the right conversation and member.

**Data flow**: It receives a conversation ID, operation ID, and optional member ID. It reads the operation metadata from Redis, checks it with _gate_ok, and if allowed reads the body blob. Missing metadata, failed checks, missing blobs, or blob timeouts all result in None.

**Call relations**: This is used by the serving side when it needs the operation's copy-in body. It relies on metadata written by RedisTerminals._run_op and the same gate logic used by RedisTerminals._deliver_reply.

*Call graph*: calls 4 internal fn (_body_blob, _client, _gate_ok, _opmeta_key); 1 external calls (wait_for).


##### `RedisTerminals.resolve`  (lines 548–563)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts a terminal reply and schedules it to be delivered to Redis. It returns immediately so the HTTP reply route is not held up by Redis or blob-store work.

**Data flow**: It receives the conversation, operation ID, reply bytes, optional failure message, and optional member ID. It gets the running event loop, schedules _deliver_reply as a tracked background task, and returns True immediately.

**Call relations**: This is the reply-side public entry point. It hands actual delivery to RedisTerminals._deliver_reply through RedisTerminals._spawn, while RedisTerminals._await_reply on the sender side waits independently for the reply stream entry.

*Call graph*: calls 2 internal fn (_deliver_reply, _spawn); 1 external calls (get_running_loop).


##### `RedisTerminals._deliver_reply`  (lines 565–594)

```
async def _deliver_reply(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None, member_id: UUID | None) -> None
```

**Purpose**: Writes an operation reply so the waiting sender can receive it. It also refuses replies that do not match the operation's conversation or member.

**Data flow**: It receives conversation and operation IDs, reply bytes, an optional failure message, and an optional member ID. It reads operation metadata, checks the gate, then writes either a failure field, a small base64-encoded reply, or a blob marker after storing a large reply. It adds the result to the reply stream and sets the stream to expire.

**Call relations**: RedisTerminals.resolve schedules this in the background. It uses _gate_ok to protect the operation, _reply_blob for large data, _reply_stream for the Redis reply, and warn to log dropped replies.

*Call graph*: calls 5 internal fn (_client, _gate_ok, _opmeta_key, _reply_blob, _reply_stream); called by 1 (resolve); 3 external calls (wait_for, b64encode, warn).


##### `RedisTerminals._gate_ok`  (lines 596–607)

```
def _gate_ok(self, meta_raw: bytes | str, conversation_id: UUID, member_id: UUID | None) -> bool
```

**Purpose**: Checks whether a staged-body or reply request is allowed to touch an operation. It fails closed, meaning uncertain or mismatched requests are denied.

**Data flow**: It receives raw operation metadata, the requested conversation ID, and an optional member ID. It parses the metadata, compares the conversation, then compares the member when one was supplied. It returns True only when the request matches the recorded operation.

**Call relations**: RedisTerminals.staged uses this before serving copy-in bytes. RedisTerminals._deliver_reply uses it before accepting a reply. Both depend on metadata written by RedisTerminals._run_op.

*Call graph*: calls 1 internal fn (_text); called by 2 (_deliver_reply, staged); 2 external calls (loads, UUID).


##### `RedisTerminals.in_flight`  (lines 609–612)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports no local in-flight operation for this cross-pod transport. The operation may be waiting on another pod, so this object cannot safely expose a local view.

**Data flow**: It receives a conversation ID and always returns None. It does not read Redis or local state.

**Call relations**: This preserves the transport interface while making the limitation explicit. Other methods use Redis to coordinate real in-flight work instead of this local inspection hook.


##### `RedisTerminals._clear_op`  (lines 614–635)

```
async def _clear_op(self, conversation_id: UUID, op_id: str, entry_id: str | None) -> None
```

**Purpose**: Cleans up Redis keys, stream entries, and blob objects that belonged to an operation. The cleanup is best-effort because safety also comes from expirations and delivery markers.

**Data flow**: It receives a conversation ID, operation ID, and optional stream entry ID. It tries to delete the operation stream entry, operation metadata, delivery marker, in-flight binding, reply stream, input blob, and reply blob. Redis and blob-delete failures are suppressed or bounded so cleanup cannot hang for a long time.

**Call relations**: RedisTerminals._run_op calls this in a finally block after the reply wait ends or fails. It uses all the key-building helpers to remove the pieces created by _run_op, next_op, and _deliver_reply.

*Call graph*: calls 8 internal fn (_body_blob, _client, _deliv_key, _inflight_key, _op_stream, _opmeta_key, _reply_blob, _reply_stream); called by 1 (_run_op); 2 external calls (wait_for, suppress).


##### `RedisTerminals._spawn`  (lines 637–644)

```
def _spawn(self, coro: Coroutine[object, object, None], loop: asyncio.AbstractEventLoop) -> None
```

**Purpose**: Starts a background delivery task and keeps a reference to it until it finishes. This prevents a fire-and-return task from disappearing silently.

**Data flow**: It receives a coroutine and an event loop. It wraps the coroutine with _logged, creates a task on the loop, stores the task in a set, and arranges for the task to remove itself from the set when done.

**Call relations**: RedisTerminals.resolve calls this to run RedisTerminals._deliver_reply without blocking the request. It hands failures to RedisTerminals._logged so they are reported.

*Call graph*: calls 1 internal fn (_logged); called by 1 (resolve); 1 external calls (create_task).


##### `RedisTerminals._logged`  (lines 646–650)

```
async def _logged(self, coro: Coroutine[object, object, None]) -> None
```

**Purpose**: Runs a background coroutine and logs any exception it raises. It keeps background delivery failures visible instead of swallowing them.

**Data flow**: It receives a coroutine. It awaits it normally; if any exception escapes, it records a warning with the error text. It does not re-raise the exception.

**Call relations**: RedisTerminals._spawn wraps background reply deliveries with this function. That means failures from RedisTerminals._deliver_reply are logged through the observability warning system.

*Call graph*: called by 1 (_spawn); 1 external calls (warn).


### `core/src/ufo/sandbox/terminal.py`

`io_transport` · `request handling and tool execution`

Most sandboxes can be reached by opening a network connection to them. A member’s own terminal is different: the server cannot dial into it directly. Instead, the server sends a request down the terminal’s already-held connection, and the terminal replies on its next request back. This file is the rendezvous point for that back-and-forth.

The main idea is a per-conversation “slot,” like a ticket window. The server puts one operation in the slot, such as “run this command,” “read this file,” or “write these bytes.” The connected terminal asks for the next operation, performs it locally on the member’s machine, then resolves the slot with the result. Because the terminal connection may end and reconnect between steps, the state belongs to the conversation, not to one network connection.

The file also exposes a sandbox carrier, `TerminalCarrier`, that makes this terminal look like other sandbox backends. It rewrites `/workspace/...` paths to the real directory where the member launched the tool, sends operations to the terminal, decodes command replies, streams reads in chunks, and rejects features that do not make sense for a local terminal, such as dialing an exposed port.

A lock is used because the workflow and the terminal connection run on different event loops, often on different threads. Without this file, a tool call could get lost, be answered by the wrong reconnect, or leave the agent waiting forever.

#### Function details

##### `TerminalTransport.connect`  (lines 250–256)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Defines the interface for telling a terminal transport that a member’s terminal is now connected for a conversation. Implementations use this to remember where the terminal is and who is allowed to answer for it.

**Data flow**: The caller provides a conversation id, current working directory, optional member id, and optional runtime id. An implementation records that information as the live binding for that conversation. There is no returned value; the change is stored in the transport.

**Call relations**: Surface routes call this kind of method when a terminal connection arrives. `Terminals.connect` is the in-process implementation, while other deployments can provide a different backend with the same shape.


##### `TerminalTransport.disconnect`  (lines 258–258)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Defines the interface for telling the transport that a terminal connection has gone away. This lets the transport stop advertising a terminal that is no longer present.

**Data flow**: The caller gives a conversation id. An implementation decreases or removes the stored connection state for that conversation. It returns nothing.

**Call relations**: Surface routes use this when a held terminal stream closes. `Terminals.disconnect` implements the local version of this behavior.


##### `TerminalTransport.workspace`  (lines 260–260)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Defines the interface for checking which workspace a terminal is currently bound to. This is a quick lookup that does not wait for a reconnect.

**Data flow**: The input is a conversation id. The transport reads its stored state and returns the terminal’s workspace information, or returns nothing if there is no known terminal.

**Call relations**: This is part of the shared transport contract. The in-process implementation is `Terminals.workspace`; code that needs a wait-for-arrival behavior uses `arrived` instead.


##### `TerminalTransport.arrived`  (lines 262–262)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Defines the interface for waiting until a terminal is connected, up to a caller-chosen grace period. This matters because the client normally disconnects and reconnects around long holds.

**Data flow**: The caller supplies a conversation id and a number of seconds to wait. The transport either returns workspace information when a terminal appears, or returns nothing when the grace time runs out.

**Call relations**: `TerminalCarrier.create` and `TerminalCarrier.attach` use this behavior to find the bound terminal. `Terminals.send` also uses it before trying to deliver an operation.


##### `TerminalTransport.send`  (lines 264–273)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Defines the interface for asking the terminal to perform one operation and waiting for its reply. This is the core request-and-answer path for terminal-backed commands and file work.

**Data flow**: The caller gives a conversation id, operation kind, timeout, optional operation name, argument, JSON parameters, and optional bytes to stage. The transport delivers that operation to the terminal and returns the reply bytes, or raises an error if the terminal disappears or reports failure.

**Call relations**: `TerminalCarrier` methods use this for exec, read, write, file operations, and skill loading. `Terminals.send` is the local implementation that coordinates with `next_op` and `resolve`.


##### `TerminalTransport.next_op`  (lines 275–277)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Defines the interface the connected terminal uses to ask, “What should I do next?” It waits until the server has placed an operation for that conversation.

**Data flow**: The terminal provides a conversation id and may name an operation it just answered so it will not receive it again. The transport returns the next `TerminalOp` to run.

**Call relations**: The surface side of the terminal connection calls this while holding the client stream. In the local implementation, it pairs with `Terminals.send`.


##### `TerminalTransport.staged`  (lines 279–281)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Defines the interface for fetching bytes attached to an in-flight operation, such as the content for a file write. The bytes are kept out of the small directive message.

**Data flow**: The caller gives a conversation id, operation id, and optional member id. The transport returns the staged bytes only if that exact operation is still current and the member is allowed to read them.

**Call relations**: Terminal-side routes call this when the terminal needs the body for a write-like operation. `Terminals.staged` provides the in-memory version.


##### `TerminalTransport.resolve`  (lines 283–290)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Defines the interface for answering an operation that the terminal has completed. It records either a successful reply body or a terminal-side failure message.

**Data flow**: The caller provides the conversation id, operation id, reply bytes, optional failure text, and optional member id. The transport wakes the waiting sender if the answer matches the current operation, and returns whether the answer was accepted.

**Call relations**: Terminal reply routes use this after the client finishes an operation. `Terminals.resolve` is the local implementation, and it wakes `Terminals.send`.


##### `TerminalTransport.in_flight`  (lines 292–292)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Defines a way to inspect the operation currently waiting for a terminal reply. This is mainly useful for tests or operator visibility.

**Data flow**: The caller gives a conversation id. The transport returns the active `TerminalOp`, or nothing if no operation is waiting.

**Call relations**: This belongs to the transport interface so different backends can expose the same view. `Terminals.in_flight` implements it for the in-process state.


##### `_wake`  (lines 295–304)

```
def _wake(waiter: _Waiter, answer: object) -> None
```

**Purpose**: Safely wakes a waiting asynchronous task from another thread. It is needed because the terminal side and workflow side may run on different event loops.

**Data flow**: It receives a saved future and the event loop that owns it, plus the answer to deliver. It schedules a tiny setter function on that original loop, so the future is completed in the safe place.

**Call relations**: `Terminals.connect` uses it to wake tasks waiting for a terminal to arrive. `Terminals.send` uses it to wake a terminal stream waiting for work or queued senders waiting their turn. `Terminals.resolve` uses it to wake the sender waiting for the terminal’s answer.

*Call graph*: called by 3 (connect, resolve, send).


##### `_wake._set`  (lines 300–302)

```
def _set() -> None
```

**Purpose**: Completes the waiting future if it has not already been completed. This small inner function performs the actual wake-up on the future’s own event loop.

**Data flow**: It reads the future captured by `_wake`. If the future is still pending, it stores the answer as the future’s result; otherwise it leaves it alone.

**Call relations**: `_wake` schedules this function with the event loop’s thread-safe callback mechanism. It is not called directly by the rest of the file.


##### `Terminals.connect`  (lines 320–342)

```
def connect(self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str | None=None) -> None
```

**Purpose**: Records that a terminal connection is present for a conversation. It also wakes anyone who was waiting for the terminal to reconnect.

**Data flow**: It receives the conversation id, current directory, member id, and optional runtime id. Under a lock, it creates or updates the conversation slot, increments the connection count, and collects arrival waiters. After releasing the lock, it wakes those waiters.

**Call relations**: This is the concrete in-process version of `TerminalTransport.connect`. It calls `_wake` because waiting workflows may live on a different event loop.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `Terminals.disconnect`  (lines 344–351)

```
def disconnect(self, conversation_id: UUID) -> None
```

**Purpose**: Marks one terminal connection as closed and removes idle state when nothing is waiting. This prevents old terminal bindings from lingering forever.

**Data flow**: It receives a conversation id, finds the stored slot, and lowers the connection count. If no connection remains and no operation is waiting for a reply, it deletes the slot. It returns nothing.

**Call relations**: This is the in-process version of `TerminalTransport.disconnect`. It is used by the surface side when the held terminal stream ends.


##### `Terminals.workspace`  (lines 353–360)

```
def workspace(self, conversation_id: UUID) -> TerminalWorkspace | None
```

**Purpose**: Returns the currently known terminal workspace for a conversation without waiting. It is a snapshot of where the connected terminal says it is standing.

**Data flow**: It receives a conversation id and reads the slot under the lock. If a slot exists, it returns a `TerminalWorkspace` with the directory, member id, and runtime id; otherwise it returns nothing.

**Call relations**: This implements the quick lookup promised by `TerminalTransport.workspace`. Code that needs to tolerate reconnect gaps uses `Terminals.arrived` instead.

*Call graph*: 1 external calls (__init__).


##### `Terminals.arrived`  (lines 362–386)

```
async def arrived(self, conversation_id: UUID, grace_s: float) -> TerminalWorkspace | None
```

**Purpose**: Waits for a terminal to be connected, but only for a limited grace period. This avoids failing normal reconnect gaps while still giving up when the terminal is truly absent.

**Data flow**: It takes a conversation id and wait duration. It repeatedly checks for a slot; if none exists, it registers a future to be woken by `connect` and waits until either a connection arrives or time runs out. It returns workspace information or nothing.

**Call relations**: `Terminals.send` calls this before sending an operation. If the wait times out, it calls `_drop_arrival` to remove its own waiter from the arrival list.

*Call graph*: calls 1 internal fn (_drop_arrival); called by 1 (send); 3 external calls (__init__, get_running_loop, wait_for).


##### `Terminals._drop_arrival`  (lines 388–393)

```
def _drop_arrival(self, conversation_id: UUID, waiter: asyncio.Future[object]) -> None
```

**Purpose**: Removes a timed-out arrival waiter from the waiting list. This keeps abandoned waits from building up in memory.

**Data flow**: It receives a conversation id and the specific future that stopped waiting. Under the lock, it filters that future out of the stored waiters and deletes the list if it becomes empty.

**Call relations**: `Terminals.arrived` calls this when its grace period expires. It is an internal cleanup helper for arrival waits.

*Call graph*: called by 1 (arrived).


##### `Terminals.send`  (lines 395–470)

```
async def send(self, conversation_id: UUID, kind: str, timeout_s: int, name: str='', arg: str='', params: str='', body: bytes | None=None) -> bytes
```

**Purpose**: Places one operation for the terminal to run and waits for the terminal’s reply. It serializes operations so the terminal only receives one at a time.

**Data flow**: It receives operation details and optional staged bytes. It first waits for the terminal to be present, then takes the conversation’s single operation turn, stores a new `TerminalOp` in the slot, wakes any terminal watcher, and waits for `resolve` to provide bytes or failure. In all exit paths it clears the operation state and wakes queued senders.

**Call relations**: This is the core server-to-terminal path. `TerminalCarrier` methods call it to run commands and file work; it uses `arrived`, `_take_turn`, and `_wake`, and it is completed by `Terminals.resolve`.

*Call graph*: calls 3 internal fn (_take_turn, arrived, _wake); 6 external calls (__init__, __init__, __init__, get_running_loop, wait_for, uuid4).


##### `Terminals._take_turn`  (lines 472–503)

```
async def _take_turn(self, conversation_id: UUID, loop: asyncio.AbstractEventLoop, timeout_s: int) -> None
```

**Purpose**: Waits until the caller owns the conversation’s single operation slot. This prevents two commands from being sent to the same terminal at once.

**Data flow**: It receives the conversation id, the caller’s event loop, and the operation timeout. If the slot is free, it marks it busy and returns. If another operation is running, it queues a future and waits up to the allowed time; on timeout it removes its ticket and raises an error.

**Call relations**: `Terminals.send` calls this before installing an operation. When a send finishes, it wakes queued waiters so they can compete for the next turn.

*Call graph*: called by 1 (send); 6 external calls (__init__, __init__, create_future, time, wait_for, deque).


##### `Terminals.next_op`  (lines 505–530)

```
async def next_op(self, conversation_id: UUID, exclude_op_id: str | None=None) -> TerminalOp
```

**Purpose**: Lets the connected terminal wait for the next operation it should perform. It also avoids redelivering an operation that the same request just answered.

**Data flow**: It receives a conversation id and optionally an operation id to exclude. If an undelivered operation is already waiting, it marks it delivered and returns it. Otherwise it records the caller as the current watcher and waits until `send` wakes it with a new operation.

**Call relations**: The terminal-facing route calls this while keeping the client connection open. It pairs with `Terminals.send`, which either finds this watcher and wakes it or leaves an operation for a later watcher.

*Call graph*: 2 external calls (__init__, get_running_loop).


##### `Terminals.staged`  (lines 532–544)

```
async def staged(self, conversation_id: UUID, op_id: str, member_id: UUID | None=None) -> bytes | None
```

**Purpose**: Returns the staged byte body for the current operation, such as file content being copied into the terminal. It only serves bytes for the exact operation that is in flight.

**Data flow**: It receives a conversation id, operation id, and optional member id. Under the lock, it checks that the slot and operation match and that the member is allowed. It returns the body bytes or nothing.

**Call relations**: Terminal-side routes use this after receiving an operation that needs a separate body. It is the in-memory implementation of `TerminalTransport.staged`.


##### `Terminals.in_flight`  (lines 546–551)

```
def in_flight(self, conversation_id: UUID) -> TerminalOp | None
```

**Purpose**: Reports the operation currently waiting for a reply, if there is one. This gives tests or diagnostics a way to see what the terminal is being asked to do.

**Data flow**: It receives a conversation id, reads the slot under the lock, and returns the stored operation or nothing. It does not change state.

**Call relations**: This implements `TerminalTransport.in_flight` for the local transport. It sits beside the main send/next/resolve flow as an inspection hook.


##### `Terminals.resolve`  (lines 553–582)

```
def resolve(self, conversation_id: UUID, op_id: str, reply: bytes, failed: str | None=None, member_id: UUID | None=None) -> bool
```

**Purpose**: Accepts the terminal’s answer to the current operation and wakes the sender waiting for it. It rejects stale, duplicate, or unauthorized answers.

**Data flow**: It receives the conversation id, operation id, reply bytes, optional failure text, and optional member id. Under the lock, it verifies that the answer matches the active operation and has not already been resolved. Then it wakes the stored sender with either reply bytes or a `TerminalOpFailed` value, and returns true. If anything does not match, it returns false.

**Call relations**: Terminal reply routes call this after performing an operation. It uses `_wake` to resume `Terminals.send` on the sender’s own event loop.

*Call graph*: calls 1 internal fn (_wake); 1 external calls (__init__).


##### `TerminalCarrier.create`  (lines 598–644)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a sandbox handle for a terminal-bound workspace. It checks that the connected terminal is in the expected directory and prepares proxy environment variables for later commands.

**Data flow**: It receives a sandbox specification, waits briefly for the terminal to appear, compares the terminal directory with the requested workspace, builds a proxy URL containing the run token, and returns a `SandboxHandle`. If the terminal is absent or in the wrong directory, it raises an error.

**Call relations**: This is called when a turn wants to open the terminal-backed sandbox. Later methods such as `exec`, `read`, and `write` use the returned handle.

*Call graph*: 4 external calls (__init__, __init__, __init__, urlsplit).


##### `TerminalCarrier.attach`  (lines 646–660)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reattaches to an already-bound terminal for off-turn work, such as file browsing or background writes. It does not wait through a reconnect gap.

**Data flow**: It receives a sandbox specification, asks the transport whether the terminal is currently present, and checks that the bound directory matches the resume id. If it matches, it returns a lightweight `SandboxHandle`; otherwise it returns nothing.

**Call relations**: This supports work outside the main turn. It uses the transport’s arrival lookup so a shared backend can find a terminal even if this process did not hold the connection.

*Call graph*: 1 external calls (__init__).


##### `TerminalCarrier.exec`  (lines 662–676)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a command on the member’s machine through the terminal. It first rewrites logical workspace paths to the real directory on that machine.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It finds the real workspace root, converts the arguments with `host_argv`, then delegates to `_exec`. The output is an `ExecResult` with stdout, stderr, exit code, and possible timeout information.

**Call relations**: Higher-level sandbox users call this as they would on any sandbox carrier. It calls `_root` and then hands the resolved command to `TerminalCarrier._exec`.

*Call graph*: calls 2 internal fn (_exec, _root); 1 external calls (host_argv).


##### `TerminalCarrier.load_skills`  (lines 678–689)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asks the running client to load skill data under the terminal’s UFO home directory. Skills are sent as structured JSON rather than shell code.

**Data flow**: It receives a handle and a payload mapping. It serializes the payload to JSON, sends an `OP_SKILLS` request through the terminal transport, and returns an `ExecResult` whose stdout is the client’s reply. If the terminal reports failure, it raises a runtime error.

**Call relations**: This is another operation built on `TerminalTransport.send`. It fits beside command execution but uses a dedicated operation kind.

*Call graph*: 2 external calls (__init__, dumps).


##### `TerminalCarrier._exec`  (lines 691–726)

```
async def _exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs command arguments that already use the member machine’s real paths. It is the lower-level executor used when paths must not be rewritten again.

**Data flow**: It receives a handle, finalized arguments, and a timeout. It sends an exec operation with arguments and environment, parses the JSON reply, decodes base64 stdout and stderr, normalizes timeout reporting to a standard exit code, and returns an `ExecResult`.

**Call relations**: `TerminalCarrier.exec` calls this after path rewriting. `TerminalCarrier._enumerate` also calls it for shell snippets that list files before a file operation.

*Call graph*: calls 2 internal fn (_reply_object, _reply_stream); called by 2 (_enumerate, exec); 2 external calls (__init__, dumps).


##### `TerminalCarrier.write`  (lines 728–741)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Writes bytes into a file in the terminal workspace. The bytes are staged separately so large file content is not squeezed into the operation directive.

**Data flow**: It receives a handle, logical path, and content bytes. It rewrites the path to the real terminal path, sends a write operation with the bytes as staged body, and returns nothing on success. Terminal-reported failures become operating-system-style errors.

**Call relations**: Higher-level sandbox code uses this as the copy-in operation. It calls `_client_path` before sending through the transport.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.read`  (lines 743–759)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Reads a file from the terminal workspace and yields it in chunks. Missing files are reported consistently as `FileNotFoundError`.

**Data flow**: It receives a handle and logical path. It rewrites the path, sends a read operation, receives the full reply bytes, and yields them in fixed-size pieces. If the terminal reports a missing path, it raises `FileNotFoundError`; other failures become `OSError`.

**Call relations**: Sandbox file readers call this to copy data out. It uses `_client_path` and the terminal transport’s send-and-reply path.

*Call graph*: calls 1 internal fn (_client_path).


##### `TerminalCarrier.file_op`  (lines 761–849)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs one higher-level file-system operation, such as read, grep, glob, or changes, on the member’s machine. It rewrites workspace paths and sometimes prepares a file listing first so the server, not the client, decides exactly what is scanned.

**Data flow**: It receives a handle, operation name, and parameter dictionary. It maps known path parameters from `/workspace` to the real root, optionally reads and renders office documents, optionally runs an enumeration command for walking or change scans, then sends a file operation to the terminal. It parses the JSON reply and returns it, or raises a clear error if the reply reports failure.

**Call relations**: This is the main bridge from `ufo fs` style operations to the terminal client. It calls `_root`, `_under_root`, `_enumerate`, and `_reply_object`, and uses the transport for the final terminal operation.

*Call graph*: calls 4 internal fn (_enumerate, _reply_object, _root, _under_root); 2 external calls (dumps, PurePosixPath).


##### `TerminalCarrier._enumerate`  (lines 851–872)

```
async def _enumerate(self, handle: SandboxHandle, op: str, walk_root: str, program: str, arguments: tuple[str, ...]=()) -> None
```

**Purpose**: Runs a shell program that prepares a deterministic listing for file operations that need to walk or inspect the workspace. This keeps scanning rules consistent with the server’s expectations.

**Data flow**: It receives a handle, operation name, walk root, shell program text, and optional arguments. It builds a small `sh -c` command with the walk root in the environment, executes it through `_exec`, and checks the exit code. A bad listing command becomes a value error with useful text.

**Call relations**: `TerminalCarrier.file_op` calls this before grep, glob, or changes operations. It delegates the actual command run to `TerminalCarrier._exec`.

*Call graph*: calls 1 internal fn (_exec); called by 1 (file_op); 1 external calls (quote).


##### `TerminalCarrier.dial`  (lines 874–878)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Rejects attempts to expose or dial a port on a terminal-bound sandbox. A member’s local terminal does not provide the same external per-port host that remote sandboxes do.

**Data flow**: It receives a sandbox handle and a port number, but does not use them to create a connection target. It always raises `SandboxUnreachable` with an explanation.

**Call relations**: Sandbox users may call `dial` uniformly across carriers. This implementation makes the terminal carrier’s limitation explicit instead of pretending a network endpoint exists.

*Call graph*: 1 external calls (__init__).


##### `_reply_stream`  (lines 881–889)

```
def _reply_stream(result: dict[str, object], name: str) -> bytes
```

**Purpose**: Extracts and decodes one captured command stream, such as stdout or stderr, from an exec reply. It treats a missing stream field as a malformed reply, not as empty output.

**Data flow**: It receives the parsed reply dictionary and a stream name. It looks for a matching base64 field, decodes it into bytes, and returns those bytes. If the field is absent or invalid, it raises an error.

**Call relations**: `TerminalCarrier._exec` calls this for stdout and stderr after parsing the terminal’s JSON reply.

*Call graph*: called by 1 (_exec); 1 external calls (b64decode).


##### `_reply_object`  (lines 892–899)

```
def _reply_object(reply: bytes, op: str) -> dict[str, object]
```

**Purpose**: Parses a terminal reply as a JSON object. It protects callers from silently accepting replies in the wrong shape.

**Data flow**: It receives raw reply bytes and the operation name for error messages. It decodes the bytes as UTF-8, parses JSON, verifies that the result is a dictionary-like object, and returns it. Invalid JSON or a non-object reply raises a runtime error.

**Call relations**: `TerminalCarrier._exec` uses this for command replies. `TerminalCarrier.file_op` uses it for file-operation replies.

*Call graph*: called by 2 (_exec, file_op); 1 external calls (loads).


##### `_root`  (lines 902–905)

```
def _root(handle: SandboxHandle) -> str
```

**Purpose**: Returns the real host directory that backs `/workspace` for a terminal sandbox. It raises if that directory is unexpectedly missing from the handle.

**Data flow**: It receives a sandbox handle. If the handle has a workspace host path, it returns that string; otherwise it raises an error explaining that a terminal sandbox must be bound to a directory.

**Call relations**: `TerminalCarrier.exec`, `TerminalCarrier.file_op`, and `_client_path` call this before rewriting paths.

*Call graph*: called by 3 (exec, file_op, _client_path).


##### `_client_path`  (lines 908–914)

```
def _client_path(handle: SandboxHandle, path: str) -> str
```

**Purpose**: Converts a logical `/workspace/...` path into the member machine’s real workspace path. It strips only the leading workspace prefix, avoiding accidental replacement elsewhere in the path.

**Data flow**: It receives a handle and a path string. It gets the real root with `_root`, passes the root and path to `_under_root`, and returns the rewritten path.

**Call relations**: `TerminalCarrier.write` and `TerminalCarrier.read` use this before sending paths to the terminal client.

*Call graph*: calls 2 internal fn (_root, _under_root); called by 2 (read, write).


##### `_under_root`  (lines 917–922)

```
def _under_root(root: str, path: str) -> str
```

**Purpose**: Maps paths under `/workspace` to the real bound root while leaving other paths unchanged. This is the small path-rule helper used by terminal file operations.

**Data flow**: It receives the real root and a path. If the path is not under `/workspace`, it returns the original path. If it is exactly `/workspace`, it returns the root; otherwise it appends the relative part to the root and returns the result.

**Call relations**: `_client_path` uses this for read and write paths. `TerminalCarrier.file_op` uses it for selected file-operation parameters that represent workspace paths.

*Call graph*: called by 2 (file_op, _client_path); 1 external calls (PurePosixPath).


### Local carrier selection
The host-directory sandbox provides the simplest carrier, while the selector chooses and preserves configured backends.

### `core/src/ufo/sandbox/local.py`

`io_transport` · `cross-cutting`

This file is the lightweight version of the sandbox system. Instead of starting Docker or a cloud sandbox, it treats the conversation’s workspace as a real folder on the same machine and runs requested commands there. That makes development simple and fast, but it is not a security wall: a local subprocess is still a host process, so safety mostly comes from carefully checking paths before reading or writing files.

The file builds a small private runtime area. Think of it like a temporary tool belt: it contains a private home directory, a copy of the `ufo` command-line helper, skill files, and certificate material needed for network traffic. Commands get a deliberately built environment rather than inheriting the server’s environment, because the server may contain secrets. Network egress is still routed through the project’s proxy, so model keys, metering, and certificate handling behave like they would in a container.

`LocalCarrier` is the main piece. It can create or attach to a workspace, run commands, copy files in and out safely, load skills, run file operations through `ufo fs`, and translate an in-sandbox port to localhost. The important theme is “same behavior, less isolation”: useful for local development, but not a replacement for Docker or E2B when strong separation is needed.

#### Function details

##### `_provision_scratch`  (lines 71–95)

```
def _provision_scratch() -> Path
```

**Purpose**: Creates one temporary local runtime area for this Python process. It prepares a private home directory and, when available, copies in the `ufo` helper program so later commands can use it.

**Data flow**: It starts with no input other than the current process environment. It makes a temporary directory, adds `home` and `bin` folders, tries to find the client binary, copies it into `bin`, and marks it executable. It returns the path to this scratch area; if the binary is missing, it warns and still returns the scratch area.

**Call relations**: This is used as the default factory for `LocalCarrier._scratch`, so it runs when a local carrier is first built. Later methods rely on the returned directory for `HOME`, `UFO_HOME`, `PATH`, certificates, and skill storage.

*Call graph*: 4 external calls (Path, mkdtemp, warn, client_binary).


##### `LocalCarrier.ufo_home`  (lines 103–105)

```
def ufo_home(self) -> Path
```

**Purpose**: Gives the local carrier’s private `UFO_HOME` directory. This is where the local runtime stores UFO-specific files such as skills and per-conversation runtime data.

**Data flow**: It reads the carrier’s scratch directory and appends `home/.ufo`. It returns that path without creating or changing anything itself.

**Call relations**: Other methods use this property when seeding skills, loading skills, creating runtime directories, and attaching to existing conversations. It keeps all UFO-local state under the private scratch home instead of the real user’s home.


##### `LocalCarrier.seed_system_skills`  (lines 107–135)

```
def seed_system_skills(self, archive: bytes) -> None
```

**Purpose**: Installs built-in system skills from a zip archive into the local UFO home. It replaces old versions of those skills safely and records the manifest that describes them.

**Data flow**: It receives zip archive bytes. It opens the archive, reads `manifest.json`, validates that the manifest and skill names look correct, removes old top-level skill folders that are being replaced, writes each archive file under the skills directory, and finally writes the new system manifest. The result is changed files on disk; it returns nothing.

**Call relations**: This prepares skill files before they are requested by `LocalCarrier.load_skills`. It relies on `_system_manifest` to compare against the old manifest and on containment helpers so archive paths cannot write outside the skills directory.

*Call graph*: calls 1 internal fn (_system_manifest); 7 external calls (BytesIO, loads, Path, contained_file, contained_relative, contained_remove, ZipFile).


##### `LocalCarrier.load_skills`  (lines 137–147)

```
async def load_skills(self, handle: SandboxHandle, payload: Mapping[str, object]) -> ExecResult
```

**Purpose**: Asynchronously resolves which system and user skills should be available for a sandbox run. It wraps the real disk work and returns a command-like result with either JSON output or an error.

**Data flow**: It receives a sandbox handle and a payload describing requested system and user skills. It runs `_load_skills` in a worker thread, because file operations are blocking. On success it returns an `ExecResult` whose stdout is JSON containing skill root paths; on validation or disk errors it returns an `ExecResult` with exit code 1 and the error text.

**Call relations**: The surrounding sandbox system can call this like a carrier operation. It hands the detailed validation and installation work to `_load_skills`, then packages the answer in the same style as command execution results.

*Call graph*: 3 external calls (__init__, to_thread, dumps).


##### `LocalCarrier._load_skills`  (lines 149–203)

```
def _load_skills(self, payload: Mapping[str, object]) -> dict[str, str]
```

**Purpose**: Does the detailed work of checking, verifying, and installing skills. It makes sure requested skills match their declared digests, so the runtime does not silently use tampered or mismatched content.

**Data flow**: It receives a payload with `system` and `user` skill descriptions. It reads the current system manifest, checks requested system skills against that manifest, reads their files, recomputes their digest, and records valid roots. For user skills, it validates names, decodes base64 file contents, checks the digest, installs the files, and records their roots. It returns a dictionary from skill name to local directory path.

**Call relations**: `LocalCarrier.load_skills` calls this in a background thread. This method coordinates `_system_manifest`, `_read_skill_files`, `_skill_digest`, `_validate_user_skill_name`, and `_install_user_skill` to turn a requested skill list into real directories.

*Call graph*: calls 5 internal fn (_install_user_skill, _read_skill_files, _skill_digest, _system_manifest, _validate_user_skill_name); 2 external calls (urlsafe_b64decode, contained_relative).


##### `LocalCarrier._system_manifest`  (lines 206–214)

```
def _system_manifest(root: Path) -> Mapping[str, object]
```

**Purpose**: Reads the saved manifest for installed system skills. If no manifest exists yet, it treats the system as having no installed skills.

**Data flow**: It receives the skills root directory. It safely opens `.system-manifest.json` under that root; if it is absent, it returns `{"skills": {}}`. If present, it parses the JSON and checks that the result is a mapping-like object before returning it.

**Call relations**: `seed_system_skills` uses this to know what old skills may need removal. `_load_skills` uses it to verify requested system skills against what has actually been installed.

*Call graph*: called by 2 (_load_skills, seed_system_skills); 2 external calls (load, contained_file).


##### `LocalCarrier._read_skill_files`  (lines 217–224)

```
def _read_skill_files(root: Path, name: str, files: list[str]) -> list[tuple[str, bytes]]
```

**Purpose**: Reads the files that belong to an installed system skill. It gathers their bytes so the code can verify the skill’s digest.

**Data flow**: It receives the skills root, the skill name, and a list of file paths from the manifest. For each file, it builds a contained path under the skill directory, opens it safely, reads its bytes, and adds `(path, bytes)` to a list. It returns that list.

**Call relations**: `_load_skills` calls this while checking a requested system skill. The returned contents are immediately passed into `_skill_digest` to confirm the installed files match the expected digest.

*Call graph*: called by 1 (_load_skills); 2 external calls (contained_file, contained_relative).


##### `LocalCarrier._install_user_skill`  (lines 227–233)

```
def _install_user_skill(root: Path, name: str, files: list[tuple[str, bytes]]) -> None
```

**Purpose**: Writes a verified user-provided skill into the local skills directory. It first removes any previous copy of that skill so the new version is clean.

**Data flow**: It receives the skills root, the skill name, and a list of file paths with bytes. It removes the destination folder under the skills root, then writes each file safely, creating parent directories as needed. It changes the filesystem and returns nothing.

**Call relations**: `_load_skills` calls this only after the user skill name and digest have been validated. It uses containment checks so user-supplied file paths cannot escape the skills area.

*Call graph*: called by 1 (_load_skills); 3 external calls (contained_file, contained_relative, contained_remove).


##### `LocalCarrier._validate_user_skill_name`  (lines 236–239)

```
def _validate_user_skill_name(name: str, root: Path) -> None
```

**Purpose**: Checks that a user skill name is safe and simple. It prevents names that would escape the skills directory, create nested top-level names, or hide as dot-prefixed entries.

**Data flow**: It receives a proposed skill name and the skills root. It converts the name into a contained path, looks at the relative path parts, and raises an error if the name is not exactly one normal top-level directory. If the name is valid, it returns nothing.

**Call relations**: `_load_skills` calls this before accepting a user skill. This is an early guard before decoding and writing user-provided files.

*Call graph*: called by 1 (_load_skills); 2 external calls (Path, contained_relative).


##### `LocalCarrier._skill_digest`  (lines 242–247)

```
def _skill_digest(files: list[tuple[str, bytes]]) -> str
```

**Purpose**: Computes the fingerprint used to prove a skill’s file list and contents are exactly what was declared. A digest is like a tamper-evident seal for the skill.

**Data flow**: It receives a sorted list of `(path, bytes)` entries. For each entry, it hashes the path and the content and feeds both into one overall SHA-256 hash. It returns a string such as `sha256:...`.

**Call relations**: `_load_skills` uses this for both system and user skills. A skill is accepted only when this computed digest matches the digest supplied by the manifest or payload.

*Call graph*: called by 1 (_load_skills); 1 external calls (sha256).


##### `LocalCarrier.create`  (lines 249–283)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a local sandbox handle for a conversation. It prepares the workspace and runtime folders, writes the proxy certificate, and builds the environment commands should use.

**Data flow**: It receives a `SandboxSpec` containing paths, conversation ID, proxy details, run token, and extra environment values. It creates the host workspace directory and per-conversation runtime directory, writes the proxy certificate into scratch space, builds proxy and model-key environment variables, and returns a `SandboxHandle` describing the ready local sandbox.

**Call relations**: The larger sandbox system calls this when starting a local sandbox for a run. It uses `_base_env` as the safe foundation, then adds proxy, certificate, sentinel model-key, and spec-provided environment values.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier._base_env`  (lines 285–308)

```
def _base_env(self) -> dict[str, str]
```

**Purpose**: Builds the minimal environment that every local command should receive. It avoids leaking the server’s secrets and disables host Git behaviors that could hang or prompt unexpectedly.

**Data flow**: It reads only a small allow-list from the host environment, such as locale and temporary-directory settings. It then adds private `HOME`, `UFO_HOME`, a `PATH` containing the local `ufo` helper and Python directory, and Git settings that avoid system/global host config, credential prompts, and terminal prompts. It returns a fresh dictionary of environment variables.

**Call relations**: `create` uses this as the base for command environments with proxy access. `attach` uses it for read-only access to an existing local workspace. All local subprocess behavior depends on this environment boundary.

*Call graph*: called by 2 (attach, create); 1 external calls (Path).


##### `LocalCarrier.attach`  (lines 310–324)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Reconnects to an existing local workspace without creating it. This is useful for browsing or reading a conversation that may already have files.

**Data flow**: It receives a `SandboxSpec`. It checks whether the workspace host path is an existing directory. If not, it returns `None`; if yes, it returns a `SandboxHandle` pointing at that directory with the local runtime path and base environment.

**Call relations**: The sandbox system calls this when it wants to access an existing local sandbox rather than create one. It uses `_base_env`, but unlike `create` it does not create the workspace or add proxy credentials.

*Call graph*: calls 1 internal fn (_base_env); 3 external calls (__init__, to_thread, Path).


##### `LocalCarrier.exec`  (lines 326–375)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs one command as a subprocess in the local workspace. It translates logical `/workspace` arguments into real host paths and enforces a timeout.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It finds the workspace root, rewrites command arguments to host paths, starts the subprocess in the workspace with the handle’s environment, and collects stdout and stderr. If the command times out or the operation is cancelled, it kills the whole process group. It returns an `ExecResult` with output, error text, exit code, and timeout information when relevant.

**Call relations**: This is the local carrier’s command runner. It relies on `_root` to find the host workspace, `host_argv` to rewrite paths, and `_kill_process_group` to clean up commands that do not finish normally.

*Call graph*: calls 2 internal fn (_kill_process_group, _root); 4 external calls (__init__, create_subprocess_exec, wait_for, host_argv).


##### `LocalCarrier.write`  (lines 377–400)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into the sandbox workspace. It runs the actual disk write off the async event loop so the server can keep doing other work.

**Data flow**: It receives a handle, a sandbox path, and bytes to write. It sends the blocking write operation to a worker thread by calling `_write_contained`. It returns nothing after the file has been written or raises an error if the path is unsafe or the write fails.

**Call relations**: The broader system calls this when a file needs to be delivered into the local sandbox. `_write_contained` does the guarded filesystem work.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._write_contained`  (lines 402–405)

```
def _write_contained(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Performs the safe, contained file write for `LocalCarrier.write`. It makes sure the target path belongs to an allowed sandbox root before replacing the file contents.

**Data flow**: It receives a handle, path, and content bytes. It resolves the path through `_contained_name`, opens the target using containment checks, and replaces the file with the given bytes while preserving or applying the intended write mode. It changes the file on disk and returns nothing.

**Call relations**: `LocalCarrier.write` calls this in a background thread. It depends on `_contained_name` to decide whether the path belongs to the workspace or runtime area, then delegates low-level safe writing to the containment helper.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.read`  (lines 407–418)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams bytes out of the local sandbox workspace. It reads in chunks so large files do not need to be loaded into memory all at once.

**Data flow**: It receives a handle and a sandbox path. It opens a safe file source in a worker thread, repeatedly reads chunks up to the configured chunk size, yields each chunk to the caller, and closes the source at the end. Its output is an asynchronous stream of bytes.

**Call relations**: The larger system calls this when it needs to copy a file out of the sandbox. It uses `_contained_source` to open the file safely before streaming begins.

*Call graph*: 1 external calls (to_thread).


##### `LocalCarrier._contained_source`  (lines 420–430)

```
def _contained_source(self, handle: SandboxHandle, path: str) -> BufferedReader
```

**Purpose**: Safely opens a file for reading from an allowed sandbox root. It refuses missing paths and paths that containment rules reject.

**Data flow**: It receives a handle and path. It resolves the path with `_contained_name`, opens the target through the containment helper, checks that it exists, and returns a byte-reading file object. If containment reports a missing path, it turns that into a normal `FileNotFoundError`.

**Call relations**: `LocalCarrier.read` calls this before streaming file contents. The returned open file is then read chunk by chunk outside this helper.

*Call graph*: calls 1 internal fn (_contained_name); 1 external calls (contained_file).


##### `LocalCarrier.file_op`  (lines 432–437)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a higher-level file operation through the `ufo fs` helper command. This lets the local carrier support the same file-tool interface as other sandbox carriers.

**Data flow**: It receives a handle, an operation name, and operation parameters. It passes those to `ufo_fs_file_op`, which invokes the file operation using the local carrier’s command path and workspace. It returns the resulting dictionary from that helper.

**Call relations**: The sandbox file-tool layer calls this for operations beyond simple raw read and write. It hands the work to the shared `ufo_fs_file_op` helper so local behavior matches other carriers.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `LocalCarrier.dial`  (lines 439–445)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Translates a sandbox port into a host address for the local carrier. Because local commands share the host network, the target is simply localhost on the same port.

**Data flow**: It receives a handle and a port number. It builds and returns a `DialTarget` with host `127.0.0.1:<port>` and TLS disabled. It does not inspect or change the workspace.

**Call relations**: The surrounding system calls this when it wants to connect to a server started inside the local sandbox. Unlike container carriers, there is no per-conversation network namespace here, so port conflicts can happen between conversations.

*Call graph*: 1 external calls (__init__).


##### `_kill_process_group`  (lines 448–453)

```
async def _kill_process_group(process: asyncio.subprocess.Process) -> None
```

**Purpose**: Force-kills a command and its child processes. This prevents a timed-out or cancelled command from leaving runaway subprocesses behind.

**Data flow**: It receives an asyncio subprocess object. It sends `SIGKILL` to the process group using the child process ID; if the process is already gone, it quietly returns. Otherwise it waits until the process has exited.

**Call relations**: `LocalCarrier.exec` calls this when a command times out or the exec operation is interrupted. It is deliberately aimed at the whole process group, not just the direct child.

*Call graph*: called by 1 (exec); 2 external calls (wait, killpg).


##### `_root`  (lines 456–459)

```
def _root(handle: SandboxHandle) -> Path
```

**Purpose**: Returns the host directory that backs the sandbox workspace. It also catches the invalid case where a local sandbox has no host workspace path.

**Data flow**: It receives a `SandboxHandle`. If `workspace_host_path` is missing, it raises an error explaining that the local carrier needs a host directory. Otherwise it converts the path string into a `Path` and returns it.

**Call relations**: `LocalCarrier.exec` uses this to choose the subprocess working directory. `_contained_name` uses it when resolving paths under the logical workspace root.

*Call graph*: called by 2 (exec, _contained_name); 1 external calls (Path).


##### `_contained_name`  (lines 462–468)

```
def _contained_name(handle: SandboxHandle, path: str) -> tuple[PurePosixPath, Path]
```

**Purpose**: Decides whether a requested sandbox path is inside an allowed local root and converts it into a relative path plus the real host root. It is the path gatekeeper for local reads and writes.

**Data flow**: It receives a handle and a path string. It treats the string as a POSIX-style path, then checks whether it is under `/workspace` or under the handle’s runtime root. If so, it returns the relative path and the matching host root. If not, it raises an error because the path is outside the sandbox roots.

**Call relations**: `_write_contained` and `_contained_source` call this before touching the filesystem. It uses `_root` when a path belongs to the workspace, so all local file access is anchored to the handle’s host workspace directory.

*Call graph*: calls 1 internal fn (_root); called by 2 (_contained_source, _write_contained); 2 external calls (Path, PurePosixPath).


### `core/src/ufo/sandbox/select.py`

`orchestration` · `startup / sandbox setup`

A sandbox needs somewhere to run: locally, in Docker, in E2B, or in another backend supplied by an extension. This file is the place where those choices are turned from names in the configuration into real carrier objects the rest of the system can use. Think of it like assigning both the main parking garage for new cars and a list of older garages where already-parked cars may still need to be found.

It starts with the built-in `local` carrier, then adds carriers advertised by extension manifests. If two carriers claim the same backend name, it stops immediately, because otherwise the same configuration name could mean two different things. It also checks the `resume_backends` list, which is for old backends that may still contain saved sandbox handles. A resume backend cannot repeat the current default backend, and the list cannot contain duplicates.

For each selected backend, the file builds exactly one carrier instance and records its `CarrierSpec`, which describes where it came from and how to create it. Remote, off-cluster carriers get an extra safety check: they must have a public HTTPS proxy URL configured. Without that, a remote sandbox could not safely route outbound traffic through the system’s credential-injecting and metered proxy.

#### Function details

##### `select_carriers`  (lines 26–50)

```
def select_carriers(config: Config, manifests: tuple[Manifest, ...]) -> DeployCarriers
```

**Purpose**: Chooses all sandbox carriers needed for this deploy: the default carrier for new sandboxes and any extra carriers needed only to resume existing sandboxes. It also rejects ambiguous or unsafe configuration early, before the system starts using sandboxes.

**Data flow**: It receives the main configuration and the extension manifests. It begins with the built-in `local` backend, adds every carrier declared by extensions, checks for duplicate names and invalid resume settings, then asks `_built` to create the default carrier and each resume carrier. It returns a `DeployCarriers` object containing the ready-to-use default carrier, its specification, and a mapping of resume backend names to their carrier/spec pairs.

**Call relations**: This is the main selection routine. `select_carrier` calls it when only the default backend is needed. During its work, it calls `_built` for each configured backend name so that name lookup, construction, and remote-backend safety checks happen in one shared place.

*Call graph*: calls 1 internal fn (_built); called by 1 (select_carrier); 2 external calls (__init__, __init__).


##### `select_carrier`  (lines 53–56)

```
def select_carrier(config: Config, manifests: tuple[Manifest, ...]) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Provides a simpler interface for code that only cares about the default sandbox backend. It hides the resume-backend details and returns just the carrier used for new sandboxes plus its specification.

**Data flow**: It receives the same configuration and manifests as `select_carriers`. It calls `select_carriers`, takes the default carrier and its spec from the returned bundle, and returns only those two values. It does not change the configuration or build anything directly itself.

**Call relations**: This is a convenience wrapper around `select_carriers`. It depends on the full selection flow so the same validation rules apply even when a caller only asks for the default carrier.

*Call graph*: calls 1 internal fn (select_carriers).


##### `_built`  (lines 59–79)

```
def _built(specs: dict[str, CarrierSpec], config: Config, name: str) -> tuple[Carrier, CarrierSpec]
```

**Purpose**: Looks up one backend name, verifies it is allowed and safely configured, and creates the actual carrier object. It is the final gate before a configured backend becomes something the process can use.

**Data flow**: It receives the dictionary of known carrier specs, the configuration, and one backend name. It finds the matching spec; if none exists, it raises a clear registration error. If the backend is remote, it reads `proxy_public_url` from the sandbox configuration and requires it to be a valid HTTPS URL. After those checks pass, it calls the carrier factory from the spec and returns the new carrier together with its spec.

**Call relations**: `select_carriers` calls this once for the default backend and once for each resume backend. `_built` uses `urlparse` to inspect remote proxy URLs, and raises `NotRegisteredError` when the configured backend name does not match any built-in or extension-provided carrier.

*Call graph*: called by 1 (select_carriers); 2 external calls (__init__, urlparse).


### Isolated sandbox carriers
Docker and E2B carriers provide containerized or cloud-hosted sandbox implementations with command, file, port, and lifecycle support.

### `extensions/docker/ufo_ext_docker.py`

`io_transport` · `sandbox startup, command execution, file access, idle reclaim`

This file is the Docker version of UFO's sandbox “carrier,” meaning the piece that carries commands into an isolated workspace. Think of it like assigning each conversation its own workshop: the tools run inside that workshop, the project files are mounted at `/workspace`, and outside network access must pass through a guarded proxy. Without this file, a deployment configured for Docker sandboxes could not start containers, execute user tools safely inside them, or clean up Docker networks when conversations go quiet.

The main class, `DockerCarrier`, creates or reconnects to a container named from the conversation ID. If the container was stopped to save memory and network space, it can be started again. Each command is run with fresh proxy settings for the current turn, so an old container never keeps an old access token. The proxy also keeps real API keys out of the container by replacing a harmless sentinel value only when traffic leaves through the proxy.

The file also reads and writes files through `docker exec`, not by directly touching the host mount, so callers see the filesystem exactly as the sandbox sees it. It tracks recent activity and in-flight commands so idle containers can be stopped without interrupting active work. Stopping frees scarce Docker bridge networks, while leaving the workspace and container state available for later revival.

#### Function details

##### `_docker`  (lines 79–93)

```
async def _docker(*argv: str, stdin: bytes=b'', timeout_s: int=60) -> tuple[int, bytes, bytes]
```

**Purpose**: Runs the Docker command-line program asynchronously and returns its exit code plus captured output. It gives the rest of the file one consistent way to talk to Docker and to recognize Docker commands that exceeded a deadline.

**Data flow**: It receives Docker arguments, optional bytes for standard input, and a timeout. It starts `docker ...`, feeds the input, waits for completion, and collects standard output and standard error. It returns a three-part result: numeric code, output bytes, and error bytes; if the timeout expires, it kills the process and returns a special timeout code.

**Call relations**: Almost every Docker action in this file goes through this helper. Creation, inspection, network setup, command execution, certificate installation, and cleanup all call `_docker` so they do not each have to repeat subprocess and timeout logic.

*Call graph*: called by 13 (_death_report, _ensure_network, _ensure_runtime_root, _exec_with, _held_id, _install_ca, _reclaim_idle, _release, _revive, _running_id (+3 more)); 2 external calls (create_subprocess_exec, wait_for).


##### `DockerCarrier.create`  (lines 106–219)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: Creates a Docker sandbox for a conversation, or reconnects to an existing one. It also prepares the per-command proxy environment that makes network use attributable to the current turn without storing secrets inside the container.

**Data flow**: It receives a sandbox specification containing the conversation ID, image, workspace path, proxy details, run token, and environment variables. It first reclaims old idle containers, then looks for a running or stopped container with the conversation's deterministic name. It installs the proxy certificate, ensures the runtime directory exists, and returns a `SandboxHandle`; if no usable container exists, it creates the Docker network and starts a new long-lived container.

**Call relations**: This is the main opening path used by the sandbox system when a conversation needs Docker. It calls the lookup helpers to find existing containers, `_revive` to restart stopped ones, `_ensure_network` before fresh creation, `_install_ca` so HTTPS through the proxy works, and `_ensure_runtime_root` so UFO runtime files have the right place inside the sandbox.

*Call graph*: calls 9 internal fn (_ensure_network, _ensure_runtime_root, _install_ca, _network_name, _reclaim_idle, _revive, _running_id, _stopped_id, _docker); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier.attach`  (lines 221–250)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: Looks for an already-existing container for a conversation and returns a handle to it if possible. Unlike `create`, it does not make a new container; it is a quiet “can I reconnect?” operation.

**Data flow**: It receives the same kind of sandbox specification used for creation. It checks whether the named container is running, tries to revive it if it is stopped, and verifies the runtime directory. It returns a `SandboxHandle` when the container is usable, or `None` when there is no usable existing sandbox.

**Call relations**: This supports read or resume flows that should not create fresh sandboxes. It uses `_running_id` and `_stopped_id` to find the container, `_revive` if reclaim previously stopped it, and `_ensure_runtime_root` before handing the handle back.

*Call graph*: calls 4 internal fn (_ensure_runtime_root, _revive, _running_id, _stopped_id); 2 external calls (__init__, sandbox_runtime_root).


##### `DockerCarrier._reclaim_idle`  (lines 252–320)

```
async def _reclaim_idle(self, opening: UUID) -> None
```

**Purpose**: Stops containers and removes Docker networks for conversations that have been idle long enough. This saves memory and scarce Docker bridge network ranges while keeping the stopped container and workspace available for later restart.

**Data flow**: It receives the conversation currently being opened and marks it as recently touched. It scans Docker for UFO containers and networks, records any that this process did not already know about, and chooses entries that are old and have no active command. For each stale conversation, it finds the held container ID, removes the touch record as a reservation, calls `_release`, and restores the record if cleanup failed.

**Call relations**: `create` calls this before opening a sandbox, so cleanup happens opportunistically rather than on a timer. It asks Docker for live resources through `_docker`, uses `_held_id` to identify what must be stopped, and calls `_release` under a per-conversation lock so cleanup cannot collide with revival.

*Call graph*: calls 3 internal fn (_held_id, _release, _docker); called by 1 (create); 1 external calls (UUID).


##### `DockerCarrier.exec`  (lines 322–332)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a normal command inside the sandbox with the current turn's egress proxy environment. This is how user-level tools execute while all outbound network traffic is routed through the policy-checking proxy.

**Data flow**: It receives a sandbox handle, the command arguments, and a timeout. It turns the handle's proxy and API-sentinel environment into Docker `--env` options, then passes everything to `_exec_with`. It returns an `ExecResult` containing text output, text error output, exit code, and timeout information.

**Call relations**: This is the public command-running path used by higher-level sandbox code. It is a thin wrapper around `_exec_with`, adding the environment needed for metered and filtered network access.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier.exec_skill`  (lines 334–338)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: Runs a skill-related command inside the container as the root user. It is used for server-carried setup or synchronization tasks that need elevated permissions.

**Data flow**: It receives a sandbox handle, command arguments, and a timeout. It adds Docker's root-user option and delegates the actual execution to `_exec_with`. The result is the same `ExecResult` shape used for normal commands.

**Call relations**: This is the privileged sibling of `exec`. It shares the retry, timeout, and activity-tracking behavior in `_exec_with`, but supplies different Docker options because skill loading needs root access.

*Call graph*: calls 1 internal fn (_exec_with).


##### `DockerCarrier._exec_with`  (lines 340–379)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, options: tuple[str, ...]) -> ExecResult
```

**Purpose**: Performs the shared work of running a Docker `exec` command, including activity tracking, timeout conversion, and one retry if the container had been stopped. It protects active commands from idle reclaim by marking them in flight.

**Data flow**: It receives a handle, command arguments, a timeout, and extra Docker options such as environment variables or user selection. It increments the in-flight count, marks the conversation as touched, runs `docker exec`, and if Docker says the container is not running, tries `_revive` and runs the command again. It returns an `ExecResult` and always decrements the in-flight count afterward.

**Call relations**: Both `exec` and `exec_skill` call this helper. It relies on `_docker` for the actual Docker command and `_revive` for recovery when idle reclaim stopped the container just before use.

*Call graph*: calls 2 internal fn (_revive, _docker); called by 2 (exec, exec_skill); 1 external calls (__init__).


##### `DockerCarrier.write`  (lines 381–399)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: Copies bytes into a file inside the sandbox. It uses a guarded in-container copy program so untrusted paths cannot escape through unsafe shell behavior such as symlink tricks.

**Data flow**: It receives a sandbox handle, destination path, and file content bytes. It marks the conversation active, starts the write through `_write_started`, and if Docker reports the container is stopped, revives it and retries. If the write still fails, it raises an `OSError`; otherwise it changes the file inside the container.

**Call relations**: Higher-level file upload or attachment paths use this method to put content into the sandbox. It delegates the actual Docker invocation to `_write_started` and uses `_revive` for the same stopped-container recovery used by command execution.

*Call graph*: calls 2 internal fn (_revive, _write_started).


##### `DockerCarrier._write_started`  (lines 401–419)

```
async def _write_started(self, handle: SandboxHandle, path: str, content: bytes) -> tuple[int, bytes]
```

**Purpose**: Starts one concrete file-copy attempt inside the container. It sends file bytes through standard input instead of putting them on the command line, which is safer for arbitrary content.

**Data flow**: It receives the handle, path, and bytes to write. It decides whether the destination should be rooted in the conversation runtime area or the normal workspace, then runs Python inside the container with UFO's copy-in program and streams the content as input. It returns Docker's exit code and error bytes for the caller to interpret.

**Call relations**: `write` calls this for the first attempt and any retry after revival. It uses `_docker` to run the in-container Python helper and uses `sandbox_runtime_root` plus `PurePosixPath` to choose the correct safe root.

*Call graph*: calls 1 internal fn (_docker); called by 1 (write); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `DockerCarrier.read`  (lines 421–455)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: Streams a file out of the sandbox in chunks. It reads through the container, not directly from the host mount, so the caller sees exactly what sandbox processes would see.

**Data flow**: It receives a handle and a path. It marks the conversation active, starts a `cat` stream through `_read_started`, yields chunks to the caller, and then checks whether the command failed. If the container was stopped before producing data, it revives and retries; filesystem errors become matching `OSError`s, while unusual command deaths become `RuntimeError`s with diagnostic detail.

**Call relations**: This is the public file-read path for Docker sandboxes. It uses `_read_started` for the streaming subprocess, `_revive` for stopped-container recovery, and `_death_report` when `cat` dies without explaining why.

*Call graph*: calls 3 internal fn (_death_report, _read_started, _revive).


##### `DockerCarrier._read_started`  (lines 457–499)

```
def _read_started(self, handle: SandboxHandle, path: str) -> tuple[AsyncGenerator[bytes], list[tuple[int, str]]]
```

**Purpose**: Prepares one file-read attempt and a place to record whether that attempt failed. It separates one attempt from retry logic so `read` can safely start over after reviving a stopped container.

**Data flow**: It receives a handle and path. It creates an empty failure list and returns an asynchronous byte generator plus that list. As the generator is consumed, the list remains empty on success or receives the exit code and error text after the stream ends unsuccessfully.

**Call relations**: `read` calls this to begin a stream. The nested `stream` function does the actual Docker subprocess work, while `read` consumes the stream and interprets the failure list afterward.

*Call graph*: called by 1 (read).


##### `DockerCarrier._read_started.stream`  (lines 472–497)

```
async def stream() -> AsyncGenerator[bytes]
```

**Purpose**: Runs `docker exec cat <path>` and yields the file bytes as they arrive. It also makes sure an abandoned read does not leave a Docker exec process running in the background.

**Data flow**: It starts a Docker exec process with pipes for standard output and standard error. It reads standard output in fixed-size chunks and yields each chunk. After output ends, it reads error text and waits for the process; if the exit code is non-zero it records the failure, and if the generator is closed early it kills and reaps the process.

**Call relations**: This nested generator is returned by `_read_started` and consumed by `read`. It calls `asyncio.create_subprocess_exec` directly because streaming needs more careful pipe control than the simpler `_docker` helper provides.

*Call graph*: 1 external calls (create_subprocess_exec).


##### `DockerCarrier._death_report`  (lines 501–519)

```
async def _death_report(self, handle: SandboxHandle) -> str
```

**Purpose**: Collects Docker's view of a container when a read command dies without useful error text. This gives later debugging a factual clue, such as whether the container exited or was killed for memory use.

**Data flow**: It receives a sandbox handle, runs `docker inspect` on the container, and asks for status, exit code, and out-of-memory information. It returns a short text fragment describing either the inspected container state or the fact that inspection itself failed.

**Call relations**: `read` calls this only for confusing read failures where `cat` exited non-zero but gave no stderr. It uses `_docker` to ask Docker for the container state.

*Call graph*: calls 1 internal fn (_docker); called by 1 (read).


##### `DockerCarrier.file_op`  (lines 521–527)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: Runs a structured UFO filesystem operation inside the Docker sandbox. This is used for higher-level file actions that are more than a raw byte read or write.

**Data flow**: It receives a handle, an operation name, and operation parameters. It passes the carrier, handle, operation, and parameters to `ufo_fs_file_op`, which runs the appropriate in-sandbox `ufo fs` command. It returns the operation result as a dictionary.

**Call relations**: This public method plugs Docker into the shared sandbox filesystem-operation helper. The helper can call back through this carrier's execution path, so file operations get the same pinning and revival behavior as normal commands.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `DockerCarrier.dial`  (lines 529–536)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: Explains that this Docker carrier cannot expose an in-sandbox port to the outside world. It fails deliberately instead of pretending there is a reachable address.

**Data flow**: It receives a handle and a port number, but does not use them to open a connection. It raises `SandboxUnreachable` with a message telling the caller that Docker here has no external per-port route. Nothing is returned.

**Call relations**: Higher-level code may ask carriers to dial services running inside a sandbox, such as browser debugging or a preview server. For this carrier, the answer is always a clear failure; deployments that need this should use a remote carrier that supports it.

*Call graph*: 1 external calls (__init__).


##### `DockerCarrier._release`  (lines 538–552)

```
async def _release(self, conversation_id: UUID, container_id: str | None) -> bool
```

**Purpose**: Stops a conversation's container and removes its per-conversation Docker network. This is the actual cleanup step used by idle reclaim to free memory and Docker bridge subnet space.

**Data flow**: It receives a conversation ID and possibly a container ID. If there is a container, it asks Docker to stop it; then it removes the conversation's network name. It returns `true` when cleanup succeeded or the network was already gone, and `false` when stopping or removal failed in a way that should be retried later.

**Call relations**: `_reclaim_idle` calls this under the conversation's lifecycle lock. It uses `_network_name` to find the network and `_docker` to perform the stop and network removal.

*Call graph*: calls 2 internal fn (_network_name, _docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._revive`  (lines 554–574)

```
async def _revive(self, conversation_id: UUID, container_id: str) -> bool
```

**Purpose**: Starts a previously stopped sandbox container and reconnects it to its per-conversation network. This lets an idle-reclaimed conversation continue later instead of losing its workspace.

**Data flow**: It receives a conversation ID and container ID. Under the lifecycle lock, it marks the conversation as touched, ensures the network exists, connects the container to it, and starts the container. It returns `true` if the container is running again, `false` if Docker refused the connect or start in a recoverable way, and raises if network creation itself fails.

**Call relations**: `create`, `attach`, `_exec_with`, `write`, and `read` all call this when they find or suspect a stopped container. It calls `_ensure_network`, `_network_name`, and `_docker`, and the lock keeps it from interleaving with `_release`.

*Call graph*: calls 3 internal fn (_ensure_network, _network_name, _docker); called by 5 (_exec_with, attach, create, read, write).


##### `DockerCarrier._held_id`  (lines 576–584)

```
async def _held_id(self, name: str) -> str | None
```

**Purpose**: Finds the Docker container ID for a given container name in any state, including running, paused, or exited. Idle cleanup needs this because even a non-running-looking container may still hold the name or need stopping.

**Data flow**: It receives a Docker container name. It asks Docker for all matching containers and returns the found ID as text, or `None` if there is no match. If Docker itself fails, it raises a runtime error instead of treating that as absence.

**Call relations**: `_reclaim_idle` calls this before releasing a stale conversation. It uses `_docker` to query Docker's container list.

*Call graph*: calls 1 internal fn (_docker); called by 1 (_reclaim_idle).


##### `DockerCarrier._stopped_id`  (lines 586–595)

```
async def _stopped_id(self, name: str) -> str | None
```

**Purpose**: Finds an exited container with the given name. This is how the carrier detects a sandbox that idle reclaim stopped and can potentially restart.

**Data flow**: It receives a Docker container name. It asks Docker for matching containers whose status is exited, then returns the container ID or `None`. Docker command failures become runtime errors.

**Call relations**: `create` and `attach` use this after checking for a running container. If it finds a stopped container, those callers may pass the ID to `_revive`.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._running_id`  (lines 597–608)

```
async def _running_id(self, name: str) -> str | None
```

**Purpose**: Finds a currently running container with the given name. It carefully distinguishes “no such running container” from “Docker failed to answer.”

**Data flow**: It receives a Docker container name. It asks Docker for a matching running container and returns its ID, or `None` when the command succeeds but finds nothing. If Docker returns an error, it raises a runtime error so callers do not mistakenly create a conflicting container.

**Call relations**: `create` and `attach` call this first when reconnecting to a sandbox. It uses `_docker` to query Docker.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create).


##### `DockerCarrier._network_name`  (lines 610–611)

```
def _network_name(self, conversation_id: UUID) -> str
```

**Purpose**: Builds the deterministic Docker network name for a conversation. This keeps each conversation's network separate and easy to find later.

**Data flow**: It receives a conversation UUID. It combines the carrier's base network name with the UUID in compact hexadecimal form. It returns that string and changes nothing.

**Call relations**: `create`, `_revive`, and `_release` call this whenever they need the exact Docker network name for a conversation.

*Call graph*: called by 3 (_release, _revive, create).


##### `DockerCarrier._ensure_network`  (lines 613–624)

```
async def _ensure_network(self, network: str) -> None
```

**Purpose**: Makes sure a named Docker network exists. It is safe when two callers race: if another caller creates the network first, that already-existing result is treated as success.

**Data flow**: It receives a network name. It asks Docker whether that network already exists; if so, it returns. Otherwise it asks Docker to create it, raising a runtime error only if creation fails for a reason other than the network already existing.

**Call relations**: `create` calls this before starting a fresh container, and `_revive` calls it before reconnecting a stopped container. It uses `_docker` for both the lookup and creation.

*Call graph*: calls 1 internal fn (_docker); called by 2 (_revive, create).


##### `DockerCarrier._install_ca`  (lines 626–639)

```
async def _install_ca(self, container_id: str, ca_cert: str) -> None
```

**Purpose**: Installs the current proxy certificate authority inside a container. This lets HTTPS tools inside the sandbox trust the local proxy that inspects and controls outbound traffic.

**Data flow**: It receives a container ID and certificate text. It runs a root shell command inside the container, writes the certificate into the system certificate directory through standard input, and refreshes the container's certificate store. If Docker or certificate installation fails, it raises a runtime error.

**Call relations**: `create` calls this for both new and reused containers. That matters because the proxy certificate can change when the host process restarts, so an old container may need a fresh certificate before network requests work.

*Call graph*: calls 1 internal fn (_docker); called by 1 (create).


##### `DockerCarrier._ensure_runtime_root`  (lines 641–665)

```
async def _ensure_runtime_root(self, container_id: str, conversation_id: UUID) -> None
```

**Purpose**: Creates and fixes permissions for UFO's runtime directory inside the container. This gives the sandbox a safe place for per-conversation runtime files such as a session file.

**Data flow**: It receives a container ID and conversation ID. It calculates the runtime path, then runs a root shell command inside the container to create parent directories, create or repair the session file, set ownership, and set restrictive permissions. It raises a runtime error if the Docker command fails.

**Call relations**: `create` and `attach` call this before returning a usable `SandboxHandle`. It uses `sandbox_runtime_root` to choose the path and `_docker` to run the setup command.

*Call graph*: calls 1 internal fn (_docker); called by 2 (attach, create); 2 external calls (PurePosixPath, sandbox_runtime_root).


##### `manifest`  (lines 668–673)

```
def manifest() -> Manifest
```

**Purpose**: Advertises this Docker carrier extension to UFO's plugin system. It tells the host system the carrier's name and which class to instantiate.

**Data flow**: It takes no input. It builds a `Manifest` containing the Docker carrier name, version, and a `CarrierSpec` whose factory is `DockerCarrier`. It returns that manifest object.

**Call relations**: The extension loader calls this when discovering available carriers. The returned manifest is what lets configuration such as a Docker sandbox backend resolve to `DockerCarrier`.

*Call graph*: 2 external calls (__init__, __init__).


### `extensions/e2b/ufo_ext_e2b.py`

`io_transport` · `sandbox startup, command execution, file transfer, port dialing, and lease renewal`

A UFO conversation needs a private machine-like workspace where commands can run and files can live. This file provides that machine using E2B, which is a remote sandbox provider. Without it, a deployment configured with the e2b sandbox backend would have no way to start or talk to its cloud sandboxes.

The main class, E2BCarrier, is the adapter between UFO’s generic sandbox interface and E2B’s async software development kit, meaning calls wait on network I/O without blocking the whole process. It can open a fresh sandbox, reconnect to one saved from an earlier process, or reuse one this process already knows about. It also keeps short local “lease” records so the sandbox does not pause while work is happening, but can still pause when idle.

Before user work runs, the carrier installs the UFO client binary, trusts the proxy certificate, makes sure /workspace exists, and limits workload memory and process count so user commands cannot starve the sandbox daemon itself. Commands are run as their own process groups so timeouts and cancellations can stop the whole tree of child processes. Files move through E2B’s filesystem API, and network ports are exposed through E2B’s traffic-token system. The file also registers this provider in UFO’s manifest system.

#### Function details

##### `E2BCommandHandle.wait`  (lines 179–179)

```
async def wait(self) -> E2BCommandResult
```

**Purpose**: This protocol method describes how to wait for a command that was started in the background. It exists so the rest of this file can talk about a running E2B command in a typed, predictable way.

**Data flow**: It takes an already-started remote command handle, waits until that command finishes, and returns its standard output, standard error, and exit code. The actual work is done by E2B’s SDK object that matches this shape.

**Call relations**: E2BCarrier._exec_with starts commands in the background so it can learn their process id, then calls this wait method to collect the final result when the command ends.


##### `E2BCommands.run`  (lines 200–209)

```
async def run(self, cmd: str, *, cwd: str | None=None, envs: dict[str, str] | None=None, user: str | None=None, timeout: float | None=None, background: Literal[False]=False) -> E2BCommandResult
```

**Purpose**: This protocol method describes E2B’s command-running API. It can either run a command to completion or start it in the background and return a handle for later waiting.

**Data flow**: It receives a shell command string plus optional working directory, environment variables, user name, timeout, and background flag. It sends that command into the sandbox and returns either the finished command result or a running command handle.

**Call relations**: Most sandbox actions in this file eventually rely on this method: setup commands, workload commands, health probes, and cleanup signals all go through E2B’s command channel.


##### `E2BFileStream.__aiter__`  (lines 216–216)

```
def __aiter__(self) -> AsyncIterator[bytes]
```

**Purpose**: This protocol method describes a streamed file reader that can be used in an async loop. It lets large files be read piece by piece instead of loaded into memory all at once.

**Data flow**: It starts from an open file stream supplied by E2B and yields chunks of bytes over time. Nothing is transformed; the caller receives the file content in pieces.

**Call relations**: E2BCarrier.read receives one of these streams from E2BFiles.read and iterates over it while sending chunks back to UFO’s caller.


##### `E2BFileStream.aclose`  (lines 218–218)

```
async def aclose(self) -> None
```

**Purpose**: This protocol method closes an open streamed file connection. It matters because a half-read file still holds a network connection unless it is explicitly released.

**Data flow**: It takes the open stream state and asks the underlying E2B connection to close. It returns no data, but frees the remote and local resources tied to the stream.

**Call relations**: E2BCarrier.read calls this in a finally block, so the stream is closed whether the whole file is read or the caller stops early.


##### `E2BFiles.write`  (lines 222–222)

```
async def write(self, path: str, data: str | bytes, *, user: str | None=None) -> object
```

**Purpose**: This protocol method describes writing content into a file inside the E2B sandbox. It is the safe path for bytes, because the command API only accepts shell text.

**Data flow**: It receives a sandbox path, text or bytes, and optionally a user. It sends that data through E2B’s filesystem API so the file appears inside the sandbox.

**Call relations**: E2BCarrier.write uses it for user file uploads, and setup code uses it to place the UFO client and certificate material into the sandbox.


##### `E2BFiles.read`  (lines 224–224)

```
async def read(self, path: str, format: str) -> E2BFileStream
```

**Purpose**: This protocol method describes opening a file in the sandbox for reading. In this file it is used in streaming mode so large outputs can be transferred gradually.

**Data flow**: It receives a path and a format name, asks E2B for that file, and returns a stream object that yields byte chunks. Missing files are reported by the SDK and later translated into normal Python file errors.

**Call relations**: E2BCarrier.read calls this when UFO needs to download a produced file from the sandbox workspace.


##### `E2BSandbox.get_host`  (lines 233–233)

```
def get_host(self, port: int) -> str
```

**Purpose**: This protocol method describes how to turn an in-sandbox port into an externally reachable host name. It is used when a service running inside the sandbox needs to be reached from outside.

**Data flow**: It receives a port number and returns the provider-made host name for that port on this sandbox. It does not itself open a network connection.

**Call relations**: E2BCarrier.dial calls this after making sure the sandbox lease is long enough for the external connection to be useful.


##### `E2BSdk.create`  (lines 237–246)

```
async def create(self, *, template: str, timeout: int, metadata: dict[str, str], lifecycle: SandboxLifecycle, network: SandboxNetworkOpts, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes creating a brand-new E2B sandbox from a template. It is the provider call used when there is no existing sandbox to resume.

**Data flow**: It receives a template, timeout span, metadata, lifecycle settings, network settings, and API key. E2B creates a remote sandbox and returns an object used for commands, files, and network addresses.

**Call relations**: E2BCarrier._resume_or_open calls this only after it has decided there is no usable resume id. The returned sandbox is then prepared before being handed to the rest of UFO.


##### `E2BSdk.connect`  (lines 248–254)

```
async def connect(self, sandbox_id: str, *, timeout: int, api_key: str) -> E2BSandbox
```

**Purpose**: This protocol method describes reconnecting to an existing E2B sandbox. In E2B, this also wakes a paused sandbox and extends its lease.

**Data flow**: It receives a sandbox id, desired timeout span, and API key. If E2B still has that sandbox, it returns a live sandbox object; otherwise it raises a not-found error.

**Call relations**: E2BCarrier._connected wraps this method with retries and a total timeout, and all reconnect and lease-renewal paths go through that wrapper.


##### `E2BCarrier.create`  (lines 306–390)

```
async def create(self, spec: SandboxSpec) -> SandboxHandle
```

**Purpose**: This opens the sandbox for a conversation. It either resumes the saved sandbox, reuses this process’s current one, or creates a fresh remote sandbox and prepares it for work.

**Data flow**: It receives a SandboxSpec containing the conversation id, possible resume id, sandbox size, proxy settings, run token, environment, and turn id. It chooses or opens an E2B sandbox, installs or rechecks required runtime pieces, records a lease, and returns a SandboxHandle that the rest of UFO can use.

**Call relations**: This is the main sandbox startup path. It calls _leased to inspect the local cache, _resume_or_open to get the remote sandbox, then either _prepare_strictly or the lighter resumed-sandbox preparation path; on serious preparation failures it calls _drop and may raise SandboxUnreachable.

*Call graph*: calls 6 internal fn (_drop, _ensure_client, _leased, _prepare_runtime, _prepare_strictly, _resume_or_open); 8 external calls (__init__, __init__, __init__, timeout, emit_metric, log, egress_proxy_env, sandbox_runtime_root).


##### `E2BCarrier.attach`  (lines 392–417)

```
async def attach(self, spec: SandboxSpec) -> SandboxHandle | None
```

**Purpose**: This reconnects to an already-known sandbox without creating a new one. It is useful for read-only or follow-up operations where opening an empty replacement would be wrong.

**Data flow**: It receives a SandboxSpec and looks only at spec.resume_id. If there is no id or E2B says the sandbox is gone, it returns None; otherwise it reconnects, records a fresh lease, and returns a SandboxHandle.

**Call relations**: This uses _connected directly instead of the local cache, because it needs E2B’s current truth. It is a narrower sibling of create: attach never falls back to creating a new sandbox.

*Call graph*: calls 1 internal fn (_connected); 3 external calls (__init__, __init__, sandbox_runtime_root).


##### `E2BCarrier._resume_or_open`  (lines 419–459)

```
async def _resume_or_open(self, spec: SandboxSpec, resume_id: str | None) -> E2BSandbox
```

**Purpose**: This chooses between reconnecting to an existing sandbox and creating a new one. It hides the provider-specific details of E2B resume behavior from the public create method.

**Data flow**: It receives the desired SandboxSpec and an optional resume id. If an id is present, it tries _connected; if E2B no longer has that sandbox, it logs the miss and creates a new sandbox from the configured template for the requested size.

**Call relations**: E2BCarrier.create calls this as its first real provider step. This helper calls _connected for resumes and the SDK create method for new sandboxes, then create prepares whatever sandbox comes back.

*Call graph*: calls 1 internal fn (_connected); called by 1 (create); 1 external calls (log).


##### `E2BCarrier._prepare_strictly`  (lines 461–477)

```
async def _prepare_strictly(self, sandbox: E2BSandbox, spec: SandboxSpec) -> None
```

**Purpose**: This prepares a new or not-yet-trusted sandbox, retrying only the kind of network failure that might be temporary. It prevents UFO from publishing a sandbox handle before the box is actually usable.

**Data flow**: It receives a sandbox and its spec. It runs _prepare; if the provider connection drops, it logs and waits before trying again, but if setup itself fails or retries run out, it drops the local lease and raises the error.

**Call relations**: E2BCarrier.create calls this for fresh sandboxes and cached sandboxes that do not have durable proof of earlier preparation. It delegates setup to _prepare and uses _drop, logging, metrics, and sleep around retries.

*Call graph*: calls 2 internal fn (_drop, _prepare); called by 1 (create); 3 external calls (sleep, emit_metric, log).


##### `E2BCarrier._connected`  (lines 479–541)

```
async def _connected(self, conversation_id: UUID, sandbox_id: str, span: int) -> E2BSandbox
```

**Purpose**: This reconnects to an E2B sandbox with bounded retries. It turns a flaky provider control-plane call into one predictable operation with a clear maximum wait.

**Data flow**: It receives the conversation id, sandbox id, and lease span to request. It calls the SDK connect method, retries transport errors with backoff, and either returns a live sandbox, lets a not-found response pass through, or raises the final transport error after timeout or retry exhaustion.

**Call relations**: _resume_or_open, _sandbox, and attach all use this wrapper whenever they need E2B connect. It is the single place that standardizes resume retry behavior.

*Call graph*: called by 3 (_resume_or_open, _sandbox, attach); 4 external calls (sleep, timeout, ReadTimeout, log).


##### `E2BCarrier._prepare`  (lines 543–545)

```
async def _prepare(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This performs the full sandbox preparation bundle. It ensures both the UFO client and the runtime environment are ready before user commands run.

**Data flow**: It receives a sandbox and proxy certificate. It first calls _ensure_client, then _prepare_runtime; it returns nothing if both succeed, and lets setup errors surface if either fails.

**Call relations**: _prepare_strictly calls this during strict setup. It is a small coordinator that hands off the actual work to _ensure_client and _prepare_runtime.

*Call graph*: calls 2 internal fn (_ensure_client, _prepare_runtime); called by 1 (_prepare_strictly).


##### `E2BCarrier._prepare_runtime`  (lines 547–552)

```
async def _prepare_runtime(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This sets up the operating-system pieces UFO expects inside the sandbox. It installs trust for the proxy certificate, prepares /workspace, and limits workload resource use.

**Data flow**: It receives a sandbox and a certificate string. It writes and installs the certificate, ensures the workspace directory exists and is owned correctly, then applies memory and process limits to workload cgroups.

**Call relations**: It is called by _prepare for strict setup and directly by create during bounded resumed-sandbox rechecks. It delegates to _install_ca, _ensure_workspace, and _cap_workload.

*Call graph*: calls 3 internal fn (_cap_workload, _ensure_workspace, _install_ca); called by 2 (_prepare, create).


##### `E2BCarrier._ensure_client`  (lines 554–579)

```
async def _ensure_client(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure the correct UFO client binary is installed inside the sandbox. That client is needed because workloads enter through the baked ufo run and ufo fs commands.

**Data flow**: It receives a sandbox, computes the SHA-256 fingerprint of the local client binary, and checks the installed sandbox binary. If the fingerprint differs or the file is missing, it uploads a staged copy, verifies it, installs it atomically, and records the sandbox as ready.

**Call relations**: create calls this for resumed sandboxes, and _prepare calls it during strict setup. It uses E2B command and file APIs underneath, and avoids repeated checks with the _client_ready set.

*Call graph*: called by 2 (_prepare, create); 2 external calls (sha256, uuid4).


##### `E2BCarrier._leased`  (lines 581–596)

```
def _leased(self, conversation_id: UUID) -> _Lease | None
```

**Purpose**: This looks up the local remembered lease for a conversation and clears expired entries. It keeps the process from remembering every sandbox it has ever touched.

**Data flow**: It receives a conversation id, reads the _live lease map, removes all entries whose recorded expiry has passed, and returns the original conversation’s lease if one was present. Returning the original lease can still be useful because it names the sandbox to reconnect to.

**Call relations**: create uses this to prefer a current in-process sandbox when no durable resume id is supplied. _sandbox uses it to decide whether a provider reconnect is needed before work starts.

*Call graph*: called by 2 (_sandbox, create).


##### `E2BCarrier._install_ca`  (lines 598–606)

```
async def _install_ca(self, sandbox: E2BSandbox, ca_cert: str) -> None
```

**Purpose**: This installs the proxy certificate authority inside the sandbox’s system trust store. That lets sandbox programs trust TLS connections routed through UFO’s proxy.

**Data flow**: It receives a sandbox and certificate text. It writes the certificate to a staging path, runs the install command as root, and raises a clear RuntimeError if the sandbox command reports failure.

**Call relations**: _prepare_runtime calls this as the first runtime setup step. It uses the sandbox file API to place the certificate and the command API to update trust.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._ensure_workspace`  (lines 608–617)

```
async def _ensure_workspace(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This makes sure /workspace exists and belongs to the normal sandbox user. That directory is the durable working area for the conversation’s files.

**Data flow**: It receives a sandbox and runs a root command that creates the directory if needed and changes ownership. If the command fails, it raises an error with the sandbox’s own output.

**Call relations**: _prepare_runtime calls this after certificate installation. Later command execution uses /workspace as the working directory.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier._cap_workload`  (lines 619–629)

```
async def _cap_workload(self, sandbox: E2BSandbox) -> None
```

**Purpose**: This applies safety limits to the parts of the sandbox where user workload processes run. The goal is to stop user code from exhausting memory or process slots so badly that the sandbox daemon cannot recover.

**Data flow**: It receives a sandbox and runs a root command that calculates a memory ceiling, writes memory and process limits into workload cgroups, and raises a clear error if the command fails.

**Call relations**: _prepare_runtime calls this as the final runtime setup step. The limits then protect later commands run by _exec_with.

*Call graph*: called by 1 (_prepare_runtime).


##### `E2BCarrier.exec`  (lines 631–679)

```
async def exec(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a normal user command inside the sandbox. It applies the conversation’s proxy environment so network traffic goes through UFO’s metered and authenticated path.

**Data flow**: It receives a SandboxHandle, command arguments, and timeout in seconds. It forwards them to _exec_with with no forced user, then returns an ExecResult containing output, exit code, and timeout information if applicable.

**Call relations**: This is the public command path for regular sandbox work. It is a thin wrapper over _exec_with, which does the leasing, process tracking, timeout handling, and cleanup.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier.exec_skill`  (lines 681–685)

```
async def exec_skill(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int) -> ExecResult
```

**Purpose**: This runs a command as root for server-carried skill setup or synchronization. It is separated from normal exec so privileged maintenance work does not use the user’s egress environment.

**Data flow**: It receives a SandboxHandle, command arguments, and timeout. It calls _exec_with with user set to root and returns the resulting ExecResult.

**Call relations**: Like exec, this is a public command entry, but for trusted system-side work. It relies on the same _exec_with machinery.

*Call graph*: calls 1 internal fn (_exec_with).


##### `E2BCarrier._exec_with`  (lines 687–734)

```
async def _exec_with(self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int, user: str | None) -> ExecResult
```

**Purpose**: This is the core command runner. It renews the sandbox lease, starts the command as its own process group, waits for completion, maps provider exceptions into normal command results, and records running groups for later cancellation.

**Data flow**: It receives a handle, argument tuple, timeout, and optional user. It reconnects or reuses the sandbox through _sandbox, probes previously silent boxes with _still_there, quotes the command, starts it in the background, records its process id, waits for it, and returns an ExecResult. On timeouts it tries _stop_group; on cancellation or unknown failures it drops the lease.

**Call relations**: exec and exec_skill both call this. It ties together _sandbox, _still_there, _stop_group, _mark_silent, _forget_group, and _drop to make remote command execution behave like a local command as much as possible.

*Call graph*: calls 6 internal fn (_drop, _forget_group, _mark_silent, _sandbox, _still_there, _stop_group); called by 2 (exec, exec_skill); 3 external calls (__init__, join, emit_metric).


##### `E2BCarrier.stop_commands`  (lines 736–758)

```
async def stop_commands(self, handle: SandboxHandle) -> None
```

**Purpose**: This stops commands that belong to a particular turn after UFO has decided the turn itself was cancelled. It avoids killing other turns or subagents that share the same sandbox.

**Data flow**: It receives a SandboxHandle, removes the recorded process groups for that container and turn, and if any exist, reconnects to the sandbox and sends each group a kill signal. If nothing is recorded, it returns without touching E2B.

**Call relations**: This complements _exec_with, which deliberately does not stop commands on a bare asyncio cancellation because the caller might be replaying work. It uses _sandbox and _stop_group once the higher layer knows this is a real stop.

*Call graph*: calls 2 internal fn (_sandbox, _stop_group).


##### `E2BCarrier._forget_group`  (lines 760–770)

```
def _forget_group(self, handle: SandboxHandle, pid: int) -> None
```

**Purpose**: This removes a process group from the carrier’s record once the command has ended or been stopped. It keeps cancellation bookkeeping from growing forever or targeting old process ids.

**Data flow**: It receives a handle and process id. It looks up the group map for that container and turn, removes that pid if present, and deletes the turn entry if no groups remain.

**Call relations**: _exec_with calls this in its cleanup path whenever a command is no longer intentionally left running.

*Call graph*: called by 1 (_exec_with).


##### `E2BCarrier._stop_group`  (lines 772–798)

```
async def _stop_group(self, sandbox: E2BSandbox, container_id: str, pid: int, user: str | None) -> None
```

**Purpose**: This sends a strong kill signal to an entire process group inside the sandbox. It is how timeouts and real turn cancellations try to stop not just the shell, but also child processes it started.

**Data flow**: It receives a sandbox, container id, process id, and optional user. It runs kill -9 against the negative process id, which targets the group; if the sandbox does not answer, it marks the container as silent and emits a failure metric instead of replacing the caller’s original error.

**Call relations**: _exec_with calls this after command timeouts. stop_commands calls it for groups left running by cancelled commands.

*Call graph*: calls 1 internal fn (_mark_silent); called by 2 (_exec_with, stop_commands); 1 external calls (emit_metric).


##### `E2BCarrier._mark_silent`  (lines 800–804)

```
def _mark_silent(self, container_id: str) -> None
```

**Purpose**: This remembers that a sandbox stopped answering command requests. The mark is temporary, because a heavily loaded sandbox may become responsive again.

**Data flow**: It receives a container id and stores an expiry time in the _silent map based on the current clock plus the configured silent-mark span. It returns no data.

**Call relations**: _exec_with uses this when a command launch times out before a process id is known. _stop_group uses it when even the cleanup signal does not get an answer.

*Call graph*: called by 2 (_exec_with, _stop_group).


##### `E2BCarrier._still_there`  (lines 806–836)

```
async def _still_there(self, sandbox: E2BSandbox, container_id: str) -> None
```

**Purpose**: This probes a sandbox that was recently marked silent before committing a full command timeout to it. It fails fast if the command channel still appears broken.

**Data flow**: It receives a sandbox and container id. If the container is not marked silent, it returns. If the mark expired, it clears it. If still marked, it runs a tiny true command with a short timeout; success clears the mark, while failure raises SandboxUnreachable.

**Call relations**: _exec_with calls this before launching a command. It uses the temporary marks created by _mark_silent and logs or emits metrics when marks expire or probes fail.

*Call graph*: called by 1 (_exec_with); 3 external calls (__init__, emit_metric, log).


##### `E2BCarrier.write`  (lines 838–849)

```
async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None
```

**Purpose**: This uploads bytes into a file inside the sandbox. It uses E2B’s filesystem API because shell commands are a poor and unsafe way to carry arbitrary bytes.

**Data flow**: It receives a SandboxHandle, destination path, and byte content. It gets a leased sandbox through _sandbox and writes the content to the path; if the provider call fails, it drops the local lease and re-raises the error.

**Call relations**: This is the public file-upload path. It depends on _sandbox for lease safety and _drop for recovery after failed provider calls.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.read`  (lines 851–871)

```
async def read(self, handle: SandboxHandle, path: str) -> AsyncIterator[bytes]
```

**Purpose**: This streams a file out of the sandbox. It lets large files be downloaded without holding the whole file in this process’s memory.

**Data flow**: It receives a SandboxHandle and path. It leases the sandbox for the full autosuspend span, opens an E2B stream, yields chunks of bytes to the caller, translates E2B missing-file errors into FileNotFoundError, and always closes the stream afterward.

**Call relations**: This is the public file-download path. It uses _sandbox for a long enough lease and _drop if opening the stream fails unexpectedly.

*Call graph*: calls 2 internal fn (_drop, _sandbox).


##### `E2BCarrier.file_op`  (lines 873–878)

```
async def file_op(self, handle: SandboxHandle, op: str, params: dict[str, object]) -> dict[str, object]
```

**Purpose**: This runs higher-level file operations through the UFO client inside the sandbox. It gives callers a structured file API without duplicating all file behavior in this provider.

**Data flow**: It receives a handle, operation name, and parameter dictionary. It passes them to ufo_fs_file_op, which uses this carrier’s command execution path and returns a dictionary result.

**Call relations**: This is a bridge from UFO’s generic file-operation interface to the common helper ufo_fs_file_op. That helper calls back into the carrier as needed.

*Call graph*: 1 external calls (ufo_fs_file_op).


##### `E2BCarrier.dial`  (lines 880–901)

```
async def dial(self, handle: SandboxHandle, port: int) -> DialTarget
```

**Purpose**: This returns the outside address for a service running on a port inside the sandbox. It is used for things like browser debugging ports or preview web servers.

**Data flow**: It receives a SandboxHandle and port number. It renews the sandbox lease long enough for an external exchange, asks E2B for the host name, adds the traffic access token header if one exists, and returns a DialTarget with TLS enabled.

**Call relations**: Callers use this when they need network access into the sandbox. It relies on _sandbox for reconnect and lease behavior, and maps missing sandboxes into SandboxUnreachable.

*Call graph*: calls 1 internal fn (_sandbox); 2 external calls (__init__, __init__).


##### `E2BCarrier._sandbox`  (lines 903–945)

```
async def _sandbox(self, handle: SandboxHandle, needed_seconds: int, span_floor: int=SANDBOX_LEASE_SECONDS) -> E2BSandbox
```

**Purpose**: This returns a live sandbox object whose lease covers the work about to happen. It avoids a provider reconnect before every operation while still preventing work from running past a known lease window.

**Data flow**: It receives a handle, required seconds, and optional minimum lease span. It checks the local lease with _leased; if the lease names the right container and lasts long enough, it returns it. Otherwise it removes the old entry, reconnects through _connected with a sufficient span, records the new lease, logs the renewal, and returns the sandbox.

**Call relations**: _exec_with, write, read, stop_commands, and dial all call this before touching E2B. It is the central lease-renewal gate for active sandbox operations.

*Call graph*: calls 2 internal fn (_connected, _leased); called by 5 (_exec_with, dial, read, stop_commands, write); 2 external calls (__init__, log).


##### `E2BCarrier._drop`  (lines 947–952)

```
def _drop(self, conversation_id: UUID, during: str) -> None
```

**Purpose**: This forgets the local lease for a conversation after a provider call fails. It forces the next operation to reconnect instead of trusting a possibly stale sandbox object.

**Data flow**: It receives a conversation id and a short label for what was happening. It removes that conversation from the _live lease map and logs the drop.

**Call relations**: create, _prepare_strictly, _exec_with, write, and read call this when failures make the cached lease untrustworthy.

*Call graph*: called by 5 (_exec_with, _prepare_strictly, create, read, write); 1 external calls (log).


##### `sandbox_templates`  (lines 955–972)

```
def sandbox_templates(value: str) -> dict[str, str]
```

**Purpose**: This parses the E2B template configuration from an environment-variable string. It makes sure every supported sandbox size has exactly one template reference.

**Data flow**: It receives text like small=templateA,medium=templateB,large=templateC. It splits the entries into a dictionary and raises RuntimeError if any entry is malformed, missing a required size, or names an unexpected set of sizes.

**Call relations**: build_e2b_carrier calls this during provider construction. Its output becomes the size-to-template map used by _resume_or_open when creating fresh sandboxes.

*Call graph*: called by 1 (build_e2b_carrier).


##### `build_e2b_carrier`  (lines 975–984)

```
def build_e2b_carrier() -> E2BCarrier
```

**Purpose**: This builds an E2BCarrier from deployment environment variables. It is the factory UFO uses when the e2b carrier is selected.

**Data flow**: It reads E2B_API_KEY and E2B_TEMPLATES from the environment, validates that both exist, parses templates with sandbox_templates, reads the UFO client binary for the E2B Linux target, and returns an E2BCarrier configured with those values.

**Call relations**: manifest registers this function as the carrier factory. When the sandbox system asks for the e2b backend, this is what creates the carrier instance.

*Call graph*: calls 1 internal fn (sandbox_templates); 2 external calls (__init__, client_binary).


##### `manifest`  (lines 987–999)

```
def manifest() -> Manifest
```

**Purpose**: This exposes the E2B sandbox provider to UFO’s extension system. It is the registration point that lets configuration name e2b without the core code depending directly on this file.

**Data flow**: It takes no input. It returns a Manifest that names the extension, gives its version, and declares one CarrierSpec with the e2b factory, off-cluster flag, and supported sandbox sizes.

**Call relations**: UFO’s manifest loader calls this when loading extensions. The returned carrier spec points back to build_e2b_carrier, completing the path from configuration to a working E2BCarrier.

*Call graph*: 2 external calls (__init__, __init__).
