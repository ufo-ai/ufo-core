# Transcript, cost, and final-result publication  `stage-9.3`

This stage is the “show what happened” part of a turn. It runs alongside the main work and is especially important near the end, when the system must publish the final answer, status, costs, and evidence of what changed. It keeps a clear record so user interfaces, portals, and later diagnostics can trust what they display.

The steps module takes the raw history from DBOS, the workflow storage system, and turns it into a readable timeline: model messages, tool results, timings, and workflow events. The transcript module safely reads and writes the conversation transcript in blob storage, making sure an older or smaller copy does not overwrite a newer, better one. The activity module translates raw tool calls into friendly progress text, like a mechanic changing “internal part number” into “checking the brakes.” The workspace changes module asks the sandbox for Git-style file differences and saves a summary of changed files. The Redis stream hub carries live frames, such as text and status updates, to viewers and supports reconnecting from the right point when possible.

## Files in this stage

### Turn Timeline and Transcript
Builds a trustworthy record of the turn and safely persists the conversation transcript without allowing stale data to overwrite better data.

### `core/src/ufo/runtime/steps.py`

`domain_logic` · `diagnostic read / request handling`

A “turn” can involve several hidden steps: the model may think and write text, ask for tools, receive tool results, and the workflow may do bookkeeping work. DBOS records those steps, but its records are shaped for durable execution, not for a human or external surface to read. This file acts like a translator between those two worlds.

The main class, DurableTurnSteps, asks DBOS for the recorded steps of a workflow. It then walks through them in order and creates TurnStep objects, which are the simpler timeline entries shown outside the runtime. It labels each entry as a model step, a tool step, or a workflow step. For tool results, it tries to recover the tool’s friendly name from earlier model tool calls, so the timeline says something useful instead of only showing an internal ID.

The helper _step_messages rebuilds the small message window that each step contributed. For a model step, that can include reasoning blocks, visible text, and tool calls. For a tool dispatch, it creates the tool-result message the model saw. If images were involved, it deliberately does not reload the image bytes; it adds a note saying images were omitted. This keeps the diagnostic view light and safe while still explaining what happened.

#### Function details

##### `_step_messages`  (lines 16–54)

```
def _step_messages(output: object) -> tuple[Message, ...]
```

**Purpose**: This helper rebuilds the message or messages that a recorded step added to the turn’s conversation. It is used for diagnostics, so a reader can see what the model produced or what tool result the model received.

**Data flow**: It takes one recorded step output. If the output is a model stream result, it gathers the model’s reasoning blocks, visible text, and tool calls into an assistant message; if the stream failed but left partial output, it uses that partial output instead. If the output is a tool dispatch result, it builds a user-side tool-result message, adding a short note when image attachments were present instead of loading the image data. If the output is anything else, it returns no messages.

**Call relations**: DurableTurnSteps.read calls this while building each TurnStep. Inside, it creates Message, TextBlock, and ToolResultBlock objects so the raw DBOS output becomes the same conversation-shaped data used by the surface layer.

*Call graph*: called by 1 (read); 3 external calls (__init__, __init__, __init__).


##### `DurableTurnSteps.read`  (lines 63–103)

```
async def read(self, workflow_id: str) -> tuple[TurnStep, ...]
```

**Purpose**: This method reads all recorded DBOS steps for one workflow and turns them into an ordered tuple of TurnStep timeline entries. It is the main entry point for getting a human-readable view of a turn’s path through model calls, tool calls, and workflow work.

**Data flow**: It receives a workflow ID, asks the DBOS client for that workflow’s recorded steps, and first scans model outputs to map tool-call IDs to tool names. Then it walks through the recorded steps in order, checks each step’s function name, decides whether the step is a model, tool, or workflow step, converts timing fields into timestamps, calculates duration when possible, and attaches rebuilt messages from _step_messages. It returns the finished timeline as an immutable tuple of TurnStep objects.

**Call relations**: This method sits between DBOS’s durable execution history and the external TurnStep view. It calls _timestamp to convert millisecond times into datetime objects, calls _step_messages to reconstruct the message content for each step, and creates TurnStep objects that callers can show or inspect.

*Call graph*: calls 2 internal fn (_timestamp, _step_messages); 1 external calls (__init__).


##### `DurableTurnSteps._timestamp`  (lines 106–107)

```
def _timestamp(epoch_ms: int | None) -> datetime | None
```

**Purpose**: This small helper converts a DBOS timestamp stored as milliseconds since the Unix epoch into a timezone-aware UTC datetime. It keeps timestamp conversion consistent across all projected steps.

**Data flow**: It receives either an integer millisecond timestamp or None. If the value is None, it returns None. Otherwise, it divides the milliseconds by 1000 to get seconds and uses datetime.fromtimestamp with UTC to produce a datetime object.

**Call relations**: DurableTurnSteps.read calls this for each step’s start and completion times before creating the TurnStep. It hides the low-level time conversion so the main timeline-building code stays easier to read.

*Call graph*: called by 1 (read); 1 external calls (fromtimestamp).


### `core/src/ufo/runtime/transcript.py`

`io_transport` · `turn completion and repair/redelivery transcript persistence`

A conversation transcript is the durable written record of what has happened so far. This file wraps the low-level blob storage with rules that protect that record from race conditions, where two parts of the system may try to publish transcript data around the same time. Think of it like a shared notebook: anyone writing must first check whether their note is newer or more complete than what is already there.

The `Transcript` class knows two things: where blobs are stored, and which conversation it is responsible for. Its `read` method looks up the transcript blob for that conversation. If nothing has been written yet, it returns nothing instead of treating that as an error. If a blob exists, it turns the stored bytes back into a `Conversation` object.

Its `write` method is deliberately cautious. It reads the current transcript, asks `_supersedes` whether the incoming transcript is allowed to replace it, and only then writes the encoded conversation back to storage. The replacement rule is important because turn completion and repair or redelivery flows can overlap. A later turn should replace an earlier one, but at the same turn number the file prefers the record that came from the actual run, unless doing so would lose messages preserved by a fallback record.

#### Function details

##### `Transcript.read`  (lines 18–23)

```
async def read(self) -> Conversation | None
```

**Purpose**: Reads the saved transcript for this conversation, if one exists. It gives callers a `Conversation` object instead of making them deal directly with blob keys, missing blobs, or raw encoded bytes.

**Data flow**: It starts with the transcript's `conversation_id` and blob store. It builds the storage key for that conversation and asks the blob store for the saved body. If the blob is missing, it returns `None`; otherwise it decodes the stored bytes into a `Conversation` and returns that.

**Call relations**: This is the first step used by `Transcript.write` before any update is attempted. It relies on the shared transcript key and decoder from `ufo.runtime.turns.transcript` so the file uses the same storage format as the rest of the runtime.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 25–30)

```
async def write(self, conversation: Conversation) -> bool
```

**Purpose**: Attempts to save a conversation transcript without accidentally overwriting a newer or more complete one. It returns `True` when it actually writes, and `False` when the incoming transcript is rejected as stale or weaker.

**Data flow**: It receives a `Conversation` that someone wants to publish. It reads the currently stored transcript, compares the incoming and stored versions with `_supersedes`, and stops early if the incoming one should not replace the stored one. If the incoming record is allowed, it encodes the conversation into bytes and stores it under this conversation's transcript key.

**Call relations**: This is the main public write path in the file. It calls `Transcript.read` to see what is already durable, delegates the replacement decision to `_supersedes`, and then uses the shared encoder and key builder before writing to the blob store.

*Call graph*: calls 2 internal fn (read, _supersedes); 2 external calls (encode, transcript_key).


##### `_supersedes`  (lines 33–57)

```
def _supersedes(incoming: Conversation, stored: Conversation) -> bool
```

**Purpose**: Decides whether one transcript is allowed to replace another. This protects the durable conversation history from moving backward or losing useful messages when normal turn completion and repair fallback writes race each other.

**Data flow**: It receives two `Conversation` records: the incoming candidate and the currently stored record. It first compares their sequence numbers, where a higher sequence means a later point in the conversation and can replace an earlier one. If they are for the same sequence, it checks whether the incoming record came from the real run, whether either record is a parked or resumed attempt, and whether replacing would shrink the stored message history. It returns a simple yes-or-no boolean.

**Call relations**: Only `Transcript.write` calls this helper, using it as the gatekeeper before touching storage. The rules encode the file's central promise: later conversation progress wins, and at the same turn the system prefers the run's own record, but not if that would throw away fuller history already saved by a fallback.

*Call graph*: called by 1 (write).


### User-Readable Activity and Changes
Converts internal tool activity and sandbox file diffs into friendly progress labels and reusable changed-file summaries.

### `core/src/ufo/runtime/turns/activity.py`

`domain_logic` · `during a turn, when reporting progress for tool calls`

When the system uses a tool, the raw record of that tool call can be technical and unsafe to show directly. It may include tool names, arguments, paths, URLs, IDs, or other details that are not helpful to a regular user. This file solves that by asking a language model to write a tiny plain-language label for the current step.

The main piece is ActivitySummarizer. It takes one tool call and, optionally, the user’s goal. It builds a small JSON summary containing only a shortened version of the goal and a shortened version of the tool arguments. Then it sends that to a model with strict instructions: write only a 3 to 8 word label, do not reveal internal details, and describe what is happening now.

There are guardrails around this process. Long goals and arguments are cut down so the request stays small. The model is given only a few tokens to answer. The whole request must finish within a short timeout. If anything goes wrong, the failure is logged and counted as a metric, and the caller gets no label rather than a broken or misleading one.

Finally, the model’s answer is cleaned up so it becomes one neat label: extra spaces, bullets, quotes, and ending punctuation are removed.

#### Function details

##### `ActivityModel.model`  (lines 33–33)

```
def model(self) -> str
```

**Purpose**: This describes the model name that will be used for activity summaries. It is part of a small contract: any model-like object used here must be able to say which model it represents.

**Data flow**: An ActivitySummarizer reads this property from its model object. The value becomes the model field in the request sent for a short progress label.

**Call relations**: ActivitySummarizer.summarize relies on this property when it builds a ModelRequest. The protocol does not implement the property itself; it states what any compatible model object must provide.


##### `ActivityModel.complete`  (lines 35–35)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This describes the method that asks a language model to complete a request and return text. In this file, that returned text is expected to be a short user-facing activity label.

**Data flow**: It receives a ModelRequest containing the prompt, user message, token limit, and other settings. It returns the model’s text response, which is later cleaned before being shown or stored.

**Call relations**: ActivitySummarizer.summarize calls this method after preparing the request. The protocol only defines the expected shape of the method, so different model implementations can be plugged in.


##### `ActivitySummarizer.summarize`  (lines 44–71)

```
async def summarize(self, call: ToolUseBlock, goal: str='') -> str | None
```

**Purpose**: This function creates a short, safe, plain-language label for one tool call. It is used when the system wants to tell a user what step is happening without exposing internal tool details.

**Data flow**: It receives a tool call and an optional user goal. It trims the goal, turns the tool name and shortened arguments into compact JSON, builds a model request with strict wording rules, and waits up to a few seconds for a response. If the model succeeds, it cleans the response into one label and returns it. If the model fails or times out, it records the failure and returns None.

**Call relations**: This is the main flow in the file. It asks _bounded_arguments to shorten the tool inputs before sending them to the model, constructs Message and ModelRequest objects for the model call, uses asyncio.timeout to avoid waiting too long, and then passes the model’s answer to activity_line for final cleanup. On errors, it reports the problem through emit_metric and log instead of letting the failure disrupt the larger run.

*Call graph*: calls 2 internal fn (_bounded_arguments, activity_line); 6 external calls (__init__, __init__, timeout, dumps, emit_metric, log).


##### `_bounded_arguments`  (lines 74–78)

```
def _bounded_arguments(arguments: dict[str, object]) -> str
```

**Purpose**: This helper turns a tool’s argument dictionary into JSON, but limits how much text can be sent to the model. It keeps activity summary requests from becoming too large or leaking excessive detail.

**Data flow**: It receives the tool arguments as a dictionary. It serializes them into compact JSON text. If that text is short enough, it returns it unchanged; if it is too long, it cuts it off at the configured limit and adds an ellipsis to show it was shortened.

**Call relations**: ActivitySummarizer.summarize calls this before building the model prompt. Its job is like putting a long document through a mail slot: only a bounded amount is allowed through to the summarizing model.

*Call graph*: called by 1 (summarize); 1 external calls (dumps).


##### `activity_line`  (lines 81–84)

```
def activity_line(text: str) -> str | None
```

**Purpose**: This helper cleans a model’s raw answer into one tidy activity label. It removes common formatting noise so the result looks like a simple progress phrase.

**Data flow**: It receives text from the model. It collapses repeated whitespace, removes leading bullets or list markers, strips surrounding quotes or backticks, and removes ending punctuation such as periods or question marks. If nothing meaningful remains, it returns None; otherwise it returns the cleaned label.

**Call relations**: ActivitySummarizer.summarize calls this after the model replies. It is the final polish step before the summary can be used elsewhere in the system.

*Call graph*: called by 1 (summarize); 1 external calls (sub).


### `core/src/ufo/runtime/turns/workspace_changes.py`

`domain_logic` · `turn end / post-turn workspace refresh`

A conversation may edit files, write new files, delete things, rename things, or run shell commands that change many files at once. This file is the bookkeeping layer that turns those possible edits into a durable “workspace changes” record. Think of it like a clerk who checks the workbench after a job ends and writes down which items are different.

The file first identifies which parts of the workspace are worth checking. Direct file tools such as `write` and `edit` name a file, so their paths become scan targets. A `bash` command can change almost anything, so it marks the workspace root as a target. The recorder also keeps watching directories that were changed in the previous scan, so a changed checkout does not disappear from view until the sandbox reports it is clean.

`WorkspaceChange` and `WorkspaceChanges` are strict data shapes for the stored result: each changed path has a patch text, and both individual patches and the full list have size limits so the database record cannot grow without bound.

At the end of a turn, `WorkspaceChangeRecorder` asks the sandbox filesystem helper for changes, validates the answer, and stores it in the database. If two recorders update the same conversation at the same time, it merges carefully: newly scanned directories replace old information, while unrelated directories from another concurrent scan are preserved. If scanning fails, it logs the problem but does not fail the already-finished turn.

#### Function details

##### `change_targets`  (lines 37–55)

```
def change_targets(calls: Iterable[ToolUseBlock]) -> tuple[str, ...]
```

**Purpose**: Finds the workspace paths that a set of tool calls may have changed. It is used to decide where the later change scan should look, instead of blindly scanning an arbitrarily large workspace.

**Data flow**: It receives tool-call records. For `write` and `edit`, it reads the named `file_path`, checks that the path belongs under the workspace, and stores the workspace-relative path. For `bash`, it stores `.` because a shell command may change the workspace root or any checkout under it. Invalid or irrelevant calls are skipped. It returns the unique target paths in the order they first appeared.

**Call relations**: This is the first filtering step before a recorder is built. It relies on `workspace_path` to reject paths outside the workspace and on `PurePosixPath` to turn accepted paths into clean workspace-relative names.

*Call graph*: 2 external calls (PurePosixPath, workspace_path).


##### `WorkspaceChangeRecorder.record`  (lines 101–114)

```
async def record(self) -> None
```

**Purpose**: Runs the full end-of-turn recording process: find what was already known, choose directories to scan, ask the sandbox what changed, and store the updated answer. It is deliberately best-effort: a scan failure is logged, not raised to the user after the turn is already complete.

**Data flow**: It starts with the recorder’s sandbox, conversation identity, workspace identity, and target paths. If there is no created sandbox and no targets, it does nothing. Otherwise it reads the previously recorded changes, expands that into directories to watch, scans those directories in the sandbox, and saves the result. If anything goes wrong, it writes a structured log message and leaves the previous stored scan untouched.

**Call relations**: This is the public driver for the file’s main behavior. It calls `recorded_workspace_changes` to fetch the old view, `_directories` to decide what to ask about, `_scan` to query the sandbox, and `_store` to update the database. It calls the logging system only when the refresh fails.

*Call graph*: calls 4 internal fn (_directories, _scan, _store, recorded_workspace_changes); 1 external calls (log).


##### `WorkspaceChangeRecorder._directories`  (lines 116–129)

```
def _directories(self, recorded: WorkspaceChanges) -> list[str]
```

**Purpose**: Builds the list of directories that should be scanned for changes. It includes both the current turn’s targets and the directories from the last stored scan, so older changes keep being checked until they are gone.

**Data flow**: It receives the previously recorded change list. It combines each current target path with each previously changed path, takes the parent directory of each, sorts the unique directory names, and caps the list at a fixed maximum. If the cap drops some directories, it logs how many were dropped. It returns the final directory list.

**Call relations**: `record` calls this after loading the old scan. The returned directories are passed directly to `_scan`, which asks the sandbox filesystem helper about those locations.

*Call graph*: called by 1 (record); 2 external calls (PurePosixPath, log).


##### `WorkspaceChangeRecorder._scan`  (lines 131–136)

```
async def _scan(self, directories: list[str]) -> WorkspaceChanges
```

**Purpose**: Asks the sandbox filesystem helper for the current changes in selected directories and checks that the answer has the expected shape. This protects the rest of the system from storing malformed scan data.

**Data flow**: It receives a list of workspace-relative directories. It sends them to the sandbox command named `changes`. The sandbox returns raw data, which this function validates as a `WorkspaceChanges` object. If validation succeeds, that object comes out. If the sandbox response is malformed, it raises a runtime error explaining that the scan was bad.

**Call relations**: `record` calls this after `_directories` chooses the scan targets. Its validated result is passed to `_store` so the database only receives data that matches the file’s change-record schema.

*Call graph*: called by 1 (record).


##### `WorkspaceChangeRecorder._store`  (lines 138–161)

```
async def _store(self, scanned: WorkspaceChanges, asked: frozenset[str]) -> None
```

**Purpose**: Writes the latest workspace-change scan into the database, while safely combining it with any concurrent update for the same conversation. This matters when more than one turn or agent shares the same sandbox and finishes around the same time.

**Data flow**: It receives the freshly scanned changes and the set of directories that were actually scanned. Inside a database transaction, it ensures a row exists for this workspace and conversation. It then locks and reads the current stored scan, merges the new scan with the stored one, and updates the row with the merged JSON data. The database is changed; nothing is returned.

**Call relations**: `record` calls this after `_scan`. This function uses `workspace_tx` for the database transaction, SQLAlchemy to insert/select/update the row, and `_merged` to decide exactly which old entries survive alongside the new scan.

*Call graph*: calls 1 internal fn (_merged); called by 1 (record); 5 external calls (model_dump, and_, select, update, workspace_tx).


##### `WorkspaceChangeRecorder._merged`  (lines 163–178)

```
def _merged(self, scanned: WorkspaceChanges, stored: WorkspaceChanges, asked: frozenset[str]) -> WorkspaceChanges
```

**Purpose**: Combines a fresh scan with the scan already stored in the database. It replaces information for directories that were just scanned, but keeps old changes from directories this scan did not ask about.

**Data flow**: It receives the fresh scan, the stored scan, and the set of directories that were scanned. It gathers the fresh changed paths, keeps only stored changes whose parent directories were not scanned and whose paths are not already present in the fresh scan, then appends those kept changes after the fresh ones. It trims the result to the maximum allowed number of changes and marks the result as truncated if either scan was truncated in a still-relevant way or if trimming was needed.

**Call relations**: `_store` calls this while holding the database row lock. Its result is the final `WorkspaceChanges` value that `_store` writes back to the database.

*Call graph*: called by 1 (_store); 2 external calls (__init__, PurePosixPath).


##### `recorded_workspace_changes`  (lines 181–205)

```
async def recorded_workspace_changes(conversation_id: UUID) -> WorkspaceChanges
```

**Purpose**: Reads the last stored workspace-change scan for a conversation. If the conversation belongs to a parent sandbox conversation, it reads the parent’s scan, because the shared workspace has one shared answer.

**Data flow**: It receives a conversation ID. It opens a database transaction, looks up the owning sandbox conversation ID if there is one, then reads that owner’s stored change scan. If the conversation does not exist or no scan has been stored, it returns the shared “nothing changed” value. If a scan exists, it validates it as `WorkspaceChanges` and returns it.

**Call relations**: `WorkspaceChangeRecorder.record` calls this before deciding what directories to rescan. It uses `workspace_tx` for database access and SQLAlchemy queries to find both the workspace owner conversation and the saved scan.

*Call graph*: called by 1 (record); 2 external calls (select, workspace_tx).


### Live Stream Publication
Publishes running turn updates through Redis Streams and supports reconnecting viewers that resume from the correct cursor.

### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `live turn streaming and reconnect handling`

This file is the Redis-backed live update hub. A “frame” is one small update, such as a bit of generated text, a tool-status message, a cost update, or a final terminal signal. Instead of saving every live frame in the main database, this hub sends them through Redis Streams, which are like short-lived ordered message logs. That keeps live streaming fast and shared across many server processes, while the lasting final answer is stored elsewhere.

The main class, RedisStreamHub, uses one Redis stream per turn. When code publishes a frame, the hub turns it into a small JSON message, appends it to the stream, trims old entries after a limit, and refreshes an expiry time so idle streams disappear later. When a client subscribes, the hub reads forward from a cursor, first replaying any saved entries after that cursor and then waiting for new ones.

A key detail is that the file keeps a separate Redis client per asyncio event loop. An asyncio event loop is the scheduler that runs async tasks; Redis client objects are tied to the loop that created them. Sharing one client across loops would cause runtime errors, so the hub creates clients lazily as needed. Timeouts while waiting for new stream entries are treated as normal quiet periods, not failures.

#### Function details

##### `frame_payload`  (lines 78–84)

```
def frame_payload(frame: HubFrame) -> dict[str, object]
```

**Purpose**: Turns an in-memory live frame into a simple tagged payload that can be written to Redis as JSON. The tag says what kind of frame it is, so another process can rebuild the right frame type later.

**Data flow**: It receives a HubFrame object. If the frame is an Activity, it converts it into the special tool-activity wire shape used on the stream; otherwise it looks up the frame’s kind tag and asks the frame to dump its fields into JSON-friendly data. It returns a dictionary with a kind and data section.

**Call relations**: RedisStreamHub.publish calls this just before writing to Redis. The payload it creates is later read back by subscribe or latest_activity and reversed by frame_from_payload.

*Call graph*: called by 1 (publish); 2 external calls (__init__, model_dump).


##### `frame_from_payload`  (lines 87–97)

```
def frame_from_payload(payload: dict[str, object]) -> HubFrame
```

**Purpose**: Rebuilds a live frame object from the tagged data that came out of Redis. This is what lets subscribers receive normal application objects instead of raw JSON.

**Data flow**: It receives a dictionary containing a kind tag and a data body. For tool and skill activity messages, it translates the older or special wire shapes into a plain Activity message. For known frame kinds, it validates the stored data into the matching frame class. It returns the reconstructed HubFrame, or raises an error if the kind is unknown.

**Call relations**: RedisStreamHub.subscribe uses this when delivering frames to a live viewer. RedisStreamHub.latest_activity also uses it when it finds the newest activity-related entry and wants to return it as an Activity object.

*Call graph*: called by 2 (latest_activity, subscribe); 2 external calls (__init__, cast).


##### `_stream_id`  (lines 100–102)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis stream entry id into two numbers so ids can be compared correctly. Redis ids look like a timestamp plus a sequence number, so string comparison would be the wrong tool.

**Data flow**: It receives an entry id string such as a Redis stream id. It splits the id into the millisecond part and sequence part, fills in zero if the sequence is missing, and returns both as integers.

**Call relations**: RedisStreamHub.covers uses this to compare the oldest entry still in Redis with a caller’s cursor. That comparison tells the caller whether Redis still has enough history to resume without a gap.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 105–114)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from Redis’s XREAD response and checks that the response has the expected shape. This avoids silently reading the wrong thing if Redis returns data in a different format.

**Data flow**: It receives the raw result from a Redis stream read. If there is no data, it returns an empty list. If the result is not the expected list format, it raises a clear error. Otherwise it pulls out and returns the entries for the stream.

**Call relations**: RedisStreamHub.subscribe calls this after each Redis read. It acts like a small adapter between Redis’s nested response format and the simpler list of entries that subscribe wants to loop over.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 130–136)

```
def _client(self) -> Redis
```

**Purpose**: Returns the Redis client for the currently running asyncio event loop, creating one if needed. This prevents one loop from accidentally using another loop’s Redis connection state.

**Data flow**: It reads the current running event loop and checks the hub’s internal client dictionary. If a client already exists for that loop, it returns it. If not, it creates a Redis client from the configured URL, stores it under that loop, and returns the new client.

**Call relations**: All Redis-using methods call this before talking to Redis: publish, subscribe, covers, and latest_activity. It is the shared doorway that keeps Redis access safe when publishers and subscribers run on different loop threads.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 138–139)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name for a turn. This keeps all live frames for one turn in one predictable Redis stream.

**Data flow**: It receives a turn UUID. It combines the fixed stream prefix with that id and returns the resulting Redis key string.

**Call relations**: publish, subscribe, covers, and latest_activity all call this before reading or writing. It gives each method the same address for the turn’s live-frame log.

*Call graph*: called by 4 (covers, latest_activity, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 141–148)

```
async def publish(self, turn_id: UUID, frame: HubFrame) -> str
```

**Purpose**: Appends one live frame to the Redis stream for a turn and returns the new cursor. Code uses this when it wants viewers to see a new text chunk, status update, or other live event.

**Data flow**: It receives a turn id and a frame. It builds the stream name, converts the frame to a JSON-friendly payload, serializes that payload, and writes it into Redis with a maximum retained length. In the same Redis pipeline, it refreshes the stream expiry time. It returns the Redis entry id for the new frame, which acts as a cursor.

**Call relations**: This is the publishing side of the hub. It calls _stream to choose the Redis key, _client to get the right Redis connection, and frame_payload to prepare the frame. Subscribers later read the entry id and payload that this method wrote.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 150–174)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, HubFrame]]
```

**Purpose**: Streams live frames for a turn, starting after a given cursor or from the beginning when no cursor is supplied. It lets a surface follow a running turn and also replay retained frames after a reconnect.

**Data flow**: It receives a turn id and an optional cursor. It repeatedly asks Redis for entries after the last seen id, first with a non-blocking read and then with a blocking wait when there is nothing ready. For each entry found, it updates the cursor, parses the stored JSON, rebuilds the frame object, and yields the pair of new cursor and frame. If Redis times out while waiting, it simply tries again.

**Call relations**: This is the receiving side of the hub. It uses _stream and _client to read the right Redis stream, _stream_entries to unpack Redis responses, and frame_from_payload to turn stored messages back into live frames. Its output is meant for code that sends live updates to a user interface.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 176–182)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether Redis still has enough retained history for a subscriber to resume from a cursor without missing frames. If not, the caller should redraw or restart from a safer source.

**Data flow**: It receives a turn id and a cursor. If the cursor is empty, it returns false. Otherwise it reads the oldest retained entry in the turn’s Redis stream. If the stream is gone or empty, it returns false. If the oldest retained id is less than or equal to the cursor, it returns true, meaning the stream still covers that resume point.

**Call relations**: Reconnect logic can call this before using subscribe with an old cursor. It relies on _stream to find the stream, _client to read Redis, and _stream_id to compare Redis ids as numbers rather than plain text.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


##### `RedisStreamHub.latest_activity`  (lines 184–216)

```
async def latest_activity(self, turn_id: UUID) -> Activity | None
```

**Purpose**: Finds the most recent activity-style message for a turn, such as a tool call or skill load, without scanning the whole stream. This gives callers a quick “what is it doing now?” answer when one is still recent enough to be meaningful.

**Data flow**: It receives a turn id. It reads the turn’s stream backwards in bounded batches, looking only through a fixed number of recent frames. For each entry, it parses the JSON and checks the kind tag before doing full frame reconstruction. If it finds an activity kind, it returns it as an Activity. If the stream is empty, gone, or no recent activity is found within the limit, it returns None.

**Call relations**: This method is a lightweight side query over the same Redis stream used by publish and subscribe. It calls _stream and _client to read from Redis, then uses frame_from_payload only for the activity entry it decides to return.

*Call graph*: calls 3 internal fn (_client, _stream, frame_from_payload); 2 external calls (loads, cast).
