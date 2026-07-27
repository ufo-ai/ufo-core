# Turn queueing, live streams, and worker dispatch  `stage-7`

This stage is part of the main work loop, after an incoming message has been accepted. The message is now a “turn”: one unit of agent work stored durably, meaning it is kept in the database so it survives crashes and can be picked up later. core/src/ufo/loop/queue.py is the dispatcher. It claims a waiting turn from the queue so only one worker runs it, rebuilds the needed runtime setup, opens the sandbox where tools can run safely, and hands the turn to the agent loop.

While that work runs, interfaces need to watch it. core/src/ufo/hub.py is the local live feed for one turn, sending small update “frames” such as text chunks, tool use, costs, and final results. It also supports reconnecting from a saved position. extensions/redis_hub/.../stream_hub.py provides the same kind of feed across multiple server processes using Redis Streams, a shared message pipe. core/src/ufo/surfaces/hub_tail.py ties this together for clients, combining fast live frames with database status checks so watchers know when a turn has paused or finished.

## Files in this stage

### Worker Turn Dispatch
Claims durable queued turns, reconstructs the runtime environment, and hands execution to the agent loop.

### `core/src/ufo/loop/queue.py`

`orchestration` · `turn execution`

A “turn” is one unit of conversation work: a user or subagent message that the system must process reliably. This file makes that work durable, meaning it can survive crashes and retries without losing the thread. It uses DBOS workflows, which are database-backed jobs that can replay safely after failure, and it partitions the queue by conversation so only one turn in the same conversation runs at a time. That is like giving each conversation its own checkout lane, so messages stay in order.

The file keeps a process-wide Runtime object with all shared services: configuration, model registry, blob storage, sandbox carrier, credentials, tools, skills, search, and messaging hub. When DBOS starts a turn workflow, the code binds the workspace, claims the turn, loads its database record, prepares tools and prompts, opens or resumes the conversation sandbox, and then creates a TurnEngine to do the actual model-and-tool loop.

It also contains several safety nets. If a turn was already superseded, it repairs the transcript rather than running twice. If a later turn needs to be handed off, it re-enqueues it. If setup fails before the engine can write a final result, it writes a failed terminal frame and publishes it so callers waiting for the turn are not left hanging. The sandbox setup is careful not to expose real secrets directly; it passes short-lived tokens or harmless sentinels that the proxy later swaps for real credentials only when allowed.

#### Function details

##### `init_runtime`  (lines 126–130)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the shared runtime services that turn execution needs, such as storage, models, credentials, tools, and sandbox access. It is meant to happen once per process so every queued turn uses the same prepared service bundle.

**Data flow**: It receives a Runtime object → checks whether one is already installed → stores it in the module-level runtime slot. If a runtime is already present, it raises an error instead of silently replacing it.

**Call relations**: This sets up the state later read by _execute_turn when DBOS starts a turn workflow. It is the entry seam between the server startup code and this queue runner.


##### `reset_runtime`  (lines 133–138)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed runtime so another one can be installed. This mainly exists for tests, where a fake or isolated runtime may need to replace the normal one.

**Data flow**: It takes no input → sets the module-level runtime slot back to empty → produces no return value. After this, init_runtime can be called again.

**Call relations**: Normal serving code initializes once and does not reset. Tests can call this before installing their own runtime, avoiding direct poking at the module global.


##### `_execute_turn`  (lines 141–157)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Starts execution of one queued turn inside the correct workspace. A workspace is the tenant or project boundary, so this makes sure database reads, credentials, model usage, and billing all happen under the right scope.

**Data flow**: It receives workspace and turn IDs as strings from the workflow → looks up the installed Runtime → converts the workspace ID into a UUID and enters that workspace context → calls _run_turn with the turn ID. It returns the final turn status string produced by _run_turn.

**Call relations**: turn_workflow calls this when DBOS runs the durable workflow. Its main job is to establish the workspace context before handing the actual work to _run_turn.

*Call graph*: calls 1 internal fn (_run_turn); called by 1 (turn_workflow); 2 external calls (ws, UUID).


##### `_enqueue_handoff`  (lines 160–195)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: Queues another turn that should run after the current claim has determined a handoff is needed. This keeps conversation work moving while preserving ordering by using the conversation as the queue partition key.

**Data flow**: It receives a DBOS client plus workspace, turn, conversation, and workflow IDs → builds enqueue options for the turn queue → asks DBOS to enqueue the workflow. If enqueueing is cancelled or fails, it clears the turn’s dispatch marker in the database so the turn can be tried again; on ordinary failure it also logs the problem.

**Call relations**: _run_turn calls this after claiming a turn if the claim process reports a handoff turn. It hands the work to DBOSClient.enqueue_async, and falls back to database cleanup through workspace_tx if enqueueing cannot complete.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 198–339)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds and runs everything needed for a single agent turn. It is the central coordinator: it claims the turn, prepares prompts, tools, skills, model client, sandbox, transcript, compaction, credentials, and then starts the TurnEngine.

**Data flow**: It receives the shared Runtime and a turn ID string → claims the turn in the database → loads the turn, agent, and conversation member → chooses main-agent or subagent behavior → resolves the model and allowed tools → opens or resumes the sandbox → constructs TurnEngine with all supporting services → runs the engine. It returns a status such as completed, failed, parked, or superseded; if setup crashes, it writes a failed terminal result before returning failed.

**Call relations**: _execute_turn calls this after the workspace has been bound. It calls helper functions such as _load_turn, _enqueue_handoff, _open_sandbox, and _commit_failed_terminal, and it hands the fully assembled execution context to TurnEngine.run, where the actual model/tool loop happens.

*Call graph*: calls 4 internal fn (_commit_failed_terminal, _enqueue_handoff, _load_turn, _open_sandbox); called by 1 (_execute_turn); 22 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+12 more)).


##### `_commit_failed_terminal`  (lines 342–374)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Writes a final failed result for a turn when something goes wrong outside the normal engine flow. This prevents clients from waiting forever for a turn that crashed before the engine could publish a terminal state.

**Data flow**: It receives the hub, turn ID, and error → creates a terminal frame containing the failure status, error class, and shortened error message → repeatedly tries to update the turn row and publish the terminal event. If the update or publish fails, it logs and waits with an increasing delay before trying again.

**Call relations**: _run_turn calls this in its broad exception path. It uses the database to mark the turn failed and Hub.publish to notify listeners that the turn has ended.

*Call graph*: calls 1 internal fn (publish); called by 1 (_run_turn); 6 external calls (__init__, __init__, sleep, update, workspace_tx, log).


##### `turn_workflow`  (lines 378–379)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Defines the DBOS workflow entry for running a turn from the durable queue. DBOS uses this decorated function as the named workflow target.

**Data flow**: It receives workspace and turn IDs from DBOS → forwards them to _execute_turn → returns the status string from that execution.

**Call relations**: This is the public workflow hook for the queue. It immediately delegates to _execute_turn, which sets the workspace context and continues the turn run.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 382–440)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, UUID | None]
```

**Purpose**: Reads the database records needed to run a turn: the turn itself, its agent’s prompt and model, and the conversation member used for memory. It turns raw database rows into typed application objects.

**Data flow**: It receives a turn UUID → queries the turn, agent, and conversation tables in one database transaction → converts JSON-like context and terminal fields into their record types when present → returns a Turn object, an Agent object, and the conversation member ID if one exists.

**Call relations**: _run_turn calls this when it needs the current turn data, both for normal execution and for superseded-turn transcript repair. It depends on workspace_tx so the query runs inside the already selected workspace.

*Call graph*: called by 1 (_run_turn); 6 external calls (__init__, __init__, model_validate, model_validate, select, workspace_tx).


##### `_open_sandbox`  (lines 443–499)

```
async def _open_sandbox(carrier: Carrier, backend: str, blob: BlobStore, workspace_fs: SandboxFsCredentialMinter | None, proxy: ProxyEndpoint, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore
```

**Purpose**: Creates or resumes the isolated workspace where tools and commands run for a conversation. The sandbox is like a private workbench for the agent: it keeps files, network rules, and credentials scoped to this conversation and turn.

**Data flow**: It receives sandbox services, blob storage, proxy details, token tools, the turn, grants, connector command-line declarations, credentials, and credential slots → checks whether the conversation already has a stored sandbox handle → creates a mount for the conversation workspace → prepares environment variables for git, keyed providers, and connector CLIs without exposing raw secrets → asks the carrier to create or resume the sandbox → saves the new sandbox handle if it changed → returns the live SandboxHandle.

**Call relations**: _run_turn calls this before constructing SandboxSession and TurnEngine. It coordinates several smaller helpers: _stored_sandbox_handle, _workspace_mount, _git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env, and _persist_sandbox_handle, then hands the final spec to Carrier.create.

*Call graph*: calls 9 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env, _persist_sandbox_handle, _stored_sandbox_handle, _workspace_mount, create, encode); called by 1 (_run_turn); 4 external calls (__init__, __init__, format_sandbox_handle, sandbox_handle_id).


##### `_git_config_env`  (lines 502–509)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Formats git settings as environment variables in the special layout git understands. This lets the sandbox affect git behavior without writing a git config file.

**Data flow**: It receives a tuple of git key/value settings → creates GIT_CONFIG_COUNT and numbered GIT_CONFIG_KEY and GIT_CONFIG_VALUE entries → returns a dictionary of environment variables.

**Call relations**: _open_sandbox calls this while building the sandbox environment. The settings it formats include proxy authentication behavior and, when available, git credential headers from _git_credential_config.

*Call graph*: called by 1 (_open_sandbox).


##### `_git_credential_config`  (lines 512–538)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds git header configuration for credential slots that are actually set in the workspace. It uses sentinels, not real secrets, so the sandbox never directly holds the credential.

**Data flow**: It receives the credential store, declared credential slots, and workspace ID → skips work if credentials are disabled → checks each slot for git-style injection and whether a value exists → resolves the host → returns git extraheader settings that contain the slot’s harmless sentinel. If a host cannot be resolved, it logs a warning and skips that slot.

**Call relations**: _open_sandbox calls this before _git_config_env. Its output becomes part of git’s environment configuration, allowing git commands in the sandbox to authenticate through the proxy when the workspace has the right credential.

*Call graph*: called by 1 (_open_sandbox); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 541–574)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Creates sandbox environment variables for provider credentials and provider hosts when the workspace has stored keys. It deliberately exports sentinels and resolved host names, not raw secret values.

**Data flow**: It receives the credential store, credential slots, and workspace ID → skips if credentials are disabled → walks through slots that declare environment-variable injection → checks whether each slot is set → resolves the target host → writes the declared secret environment variable to the sentinel and, if needed, a host environment variable to the resolved host. Missing or unavailable hosts are skipped with a warning.

**Call relations**: _open_sandbox calls this while assembling the sandbox environment. The proxy later recognizes the sentinels and replaces them with real credentials only for allowed outbound requests.

*Call graph*: called by 1 (_open_sandbox); 3 external calls (credential_host, slot_is_set, warn).


##### `_grant_cli_env`  (lines 577–618)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], turn: Turn) -> dict[str, str]
```

**Purpose**: Creates environment variables for connector command-line tools when the turn has an allowed account grant. A grant is permission for the agent to use an external account, and the function avoids choosing silently if more than one account could match.

**Data flow**: It receives an optional GrantStore, connector CLI declarations, and the turn → returns an empty environment if grants or CLIs are absent → determines the acting member for the turn → reads active grants for the workspace and agent → for each provider, prefers that member’s private grant, otherwise shared grants → writes the CLI environment variable to a grant sentinel if exactly one account matches. If multiple accounts match, it logs ambiguity and exports nothing for that provider.

**Call relations**: _open_sandbox calls this when preparing the sandbox environment. It uses GrantStore.active_grants to find allowed accounts and grant_sentinel to create the value that the proxy and CLI flow can recognize later.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (_open_sandbox); 2 external calls (grant_sentinel, log).


##### `_stored_sandbox_handle`  (lines 621–630)

```
async def _stored_sandbox_handle(conversation_id: UUID, workspace_id: UUID) -> str | None
```

**Purpose**: Reads the saved sandbox handle for a conversation, if one exists. This is how a later process can reconnect to the same sandbox instead of creating a fresh one unnecessarily.

**Data flow**: It receives conversation and workspace IDs → queries the conversation row for its sandbox_handle field → returns the stored string or null.

**Call relations**: _open_sandbox calls this first to decide whether it should ask the carrier to resume an existing sandbox. The database query runs through workspace_tx.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (select, workspace_tx).


##### `_persist_sandbox_handle`  (lines 633–642)

```
async def _persist_sandbox_handle(conversation_id: UUID, workspace_id: UUID, handle: str) -> None
```

**Purpose**: Stores the current sandbox handle on the conversation row. This makes the sandbox resumable by future turns or future server processes.

**Data flow**: It receives conversation and workspace IDs plus the handle string → updates the matching conversation row’s sandbox_handle field → returns nothing.

**Call relations**: _open_sandbox calls this after Carrier.create if the returned sandbox identity differs from what was already stored. It keeps the database as the durable record of which sandbox belongs to the conversation.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (update, workspace_tx).


##### `_workspace_mount`  (lines 645–698)

```
async def _workspace_mount(blob: BlobStore, workspace_fs: SandboxFsCredentialMinter | None, conversation_id: UUID, run: RunToken, fresh_sandbox: bool) -> MountSpec
```

**Purpose**: Builds the file mount that lets the sandbox see only the conversation’s workspace folder, not the full transcript or other stored data. It supports both local filesystem blobs and S3-style object storage.

**Data flow**: It receives blob storage, optional S3 credential minter, conversation ID, run token, and whether this is a fresh sandbox → for filesystem storage, it creates the local workspace directory, fixes ownership when running as root, and returns a filesystem MountSpec → for S3 storage, it ensures a workspace marker for fresh sandboxes, issues a scoped credential token for just this workspace prefix, and returns an S3 MountSpec. If the blob backend is unsupported, it raises an error.

**Call relations**: _open_sandbox calls this while building the SandboxSpec. Its MountSpec is passed into Carrier.create so the sandbox gets the correct private file area for the conversation.

*Call graph*: calls 1 internal fn (issue); called by 1 (_open_sandbox); 5 external calls (__init__, to_thread, geteuid, ensure_workspace_marker, workspace_key_prefix).


### Live Turn Streams
Provides real-time turn frame delivery, reconnectable tailing, and optional Redis-backed cross-process streaming.

### `core/src/ufo/surfaces/hub_tail.py`

`orchestration` · `request handling`

A “turn” is streamed as a series of live frames, like words appearing one by one in a chat response. The tricky part is that a viewer may connect late, reconnect after losing contact, or attach after the turn has already finished somewhere else. If this file only listened to the live hub, it could miss the final state. If it only checked the database, it would lose the smooth live stream. So it does both at once.

The main flow starts a hub subscription for live frames and also starts a repeated database poll that looks for the durable final state. Think of it like watching a package tracker while also standing by the door: the doorbell is fast, but the tracker is the backup truth.

The stream stops when it sees either a terminal frame, meaning the turn is finished, or a parked frame, meaning the turn is paused because of a spending cap or seat/admission problem. Reconnects can pass a cursor, which is a bookmark for the last frame already seen. If the hub still remembers that cursor, streaming resumes from there; otherwise it redraws from the retained start. A small `HubTailer` wrapper exposes this behavior without making other surface code import the hub directly.

#### Function details

##### `tail_frames`  (lines 27–53)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the main stream reader for one turn. It yields live frames until the turn is finished or parked, and it works even if the caller joins late or reconnects.

**Data flow**: It receives a hub, a turn id, and optionally a cursor bookmark called `since`. It first checks whether the hub can resume from that cursor; then it starts two background tasks: one listening to the hub and one polling the database for a finished or parked state. Before waiting, it also checks the database once in case the turn already ended. It yields each frame it receives, and when a terminal or parked frame appears, it stops and cancels the background work.

**Call relations**: This function is called by `HubTailer.tail` when a surface wants to watch a turn. Inside, it asks `Hub.covers` whether a reconnect cursor is still usable, starts `_pump` to receive live hub frames, starts `_poll_status` to catch durable end states, and calls `turn_status_frame` as the database source of truth.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, turn_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 56–63)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This helper copies live frames from the hub into the shared queue used by `tail_frames`. It is the fast path for frames that are being published right now.

**Data flow**: It receives the hub, the turn id, the starting cursor, and a queue. It subscribes to the hub and, for every frame the hub emits, places that cursor-and-frame pair into the queue. If the hub subscription fails, it logs the error instead of crashing the whole stream.

**Call relations**: `tail_frames` starts this in the background while it also polls the database. `_pump` talks directly to `Hub.subscribe`; its output goes back through the queue so `tail_frames` can yield the frames to the caller in the same stream as database-discovered end states.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 66–75)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: This helper repeatedly checks the database to find out whether the turn has finished or become parked. It is the safety net for cases where the live hub is late, missed, or no longer has the needed frame.

**Data flow**: It receives a turn id and the shared queue. Every second it calls `turn_status_frame`. If that returns a terminal or parked frame, it puts that frame into the queue with an empty cursor and stops. If something goes wrong during polling, it logs the error.

**Call relations**: `tail_frames` runs this alongside `_pump`. While `_pump` listens for live events, `_poll_status` asks `turn_status_frame` for the durable database truth and feeds the result back to `tail_frames` through the queue.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `turn_status_frame`  (lines 78–107)

```
async def turn_status_frame(turn_id: UUID) -> LiveFrame | None
```

**Purpose**: This function reads the saved state of a turn and turns it into the frame that should end the stream, if there is one. It returns nothing while the turn is still queued or running.

**Data flow**: It receives a turn id. It opens a workspace database transaction and reads the turn row, including its status, terminal data, workspace, speaker, behalf-of member, and admission source. If the turn has a stored terminal frame, it validates that stored data and returns a `Terminal` live frame. If the turn is not parked, it returns nothing. If it is parked, it may also check whether the relevant seat or admission gate is no longer valid; in that case it returns a parked frame with a seat-revoked message. Otherwise it returns a parked frame explaining that the turn is paused because it is over a spending cap.

**Call relations**: Both `tail_frames` and `_poll_status` call this when they need the database’s answer about whether a stream should end. It uses the schema tables to read the turn, `TerminalFrame.model_validate` to rebuild stored terminal data safely, and seat-admission helpers to choose the right parked message.

*Call graph*: called by 2 (_poll_status, tail_frames); 8 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx, gate_member, seat_gate_absent).


##### `HubTailer.tail`  (lines 119–120)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is a small adapter method that lets other parts of the system tail a turn without knowing the details of this file. It exposes the hub-backed streaming behavior through a simple method.

**Data flow**: It receives a turn id and an optional cursor bookmark. It passes its stored hub, the turn id, and the cursor to `tail_frames`, then returns the async stream produced there.

**Call relations**: Surface code calls `HubTailer.tail` as the public seam. This method immediately hands the real work to `tail_frames`, keeping the hub dependency tucked inside `HubTailer`.

*Call graph*: calls 1 internal fn (tail_frames).


### `core/src/ufo/hub.py`

`io_transport` · `request handling`

This file is like a small broadcast station for each running turn. As the agent produces live pieces of output, they are published here as “frames”: text chunks, cost meter updates, tool calls, skill loads, a final terminal frame, or a parked message when a spending cap pauses the turn. The hub gives every frame a simple increasing cursor, like a numbered ticket, so a client can later ask, “give me everything after ticket 42.”

The main implementation, InProcessHub, keeps one stream per turn. Each stream has a bounded replay buffer, which is a fixed-size ring of recent frames, and a list of subscribers waiting for new frames. Publishing never waits for slow listeners. If a listener’s queue is full, the oldest queued frame for that listener is dropped, so the agent can keep moving.

The file is careful about concurrency. Publishers and subscribers may be running on different asyncio event loops, possibly in different threads, so a lock protects shared state. Delivery to a subscriber is scheduled safely onto that subscriber’s own loop.

The replay behavior is important: subscribing takes a snapshot of old buffered frames and registers for live frames under the same lock. That prevents gaps or duplicates between replayed history and new live updates.

#### Function details

##### `Hub.publish`  (lines 75–75)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This is the interface promise for adding a live frame to a turn’s stream. Code can depend on this promise without caring whether the hub is in-process or provided by another backend.

**Data flow**: It receives a turn ID and a live frame. An implementation appends that frame to the turn’s stream, assigns it a cursor, sends it to listeners, and returns the cursor as text.

**Call relations**: The failed-terminal commit path calls this through the Hub interface when it needs to publish a final failure frame. Concrete behavior is supplied by an implementation such as InProcessHub.publish.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 77–79)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the interface promise for following a turn’s live stream. A caller can provide the last cursor it saw and receive newer frames from there onward.

**Data flow**: It receives a turn ID and an optional cursor. An implementation first yields buffered frames after that cursor, then keeps yielding new frames as they arrive.

**Call relations**: The surface tailing code calls this through the Hub interface when a user interface wants to watch a turn. Concrete streaming behavior is supplied by an implementation such as InProcessHub.subscribe.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 81–81)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for checking whether a cursor is still safely replayable. It helps a reconnecting client decide whether it can resume smoothly or must fall back to a full redraw or durable poll.

**Data flow**: It receives a turn ID and a cursor. An implementation checks whether its retained replay buffer still reaches back far enough, then returns true or false.

**Call relations**: The frame-tailing code asks this before resuming from a cursor. Concrete coverage checking is supplied by an implementation such as InProcessHub.covers.

*Call graph*: called by 1 (tail_frames).


##### `_offer`  (lines 84–87)

```
def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None
```

**Purpose**: This helper puts one frame into a subscriber’s queue without ever blocking the publisher. If the queue is already full, it makes room by discarding the oldest queued frame.

**Data flow**: It receives a queue and one cursor-frame pair. If the queue has no space, it removes one old item, then adds the new item; it returns nothing and only changes the queue.

**Call relations**: InProcessHub.publish schedules this helper on each subscriber’s own event loop. That keeps cross-thread delivery safe while preserving the rule that publishing should not wait for slow consumers.


##### `InProcessHub.publish`  (lines 116–130)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This adds a new live frame to one turn and fans it out to all current subscribers. It is designed so publishing stays fast even if some subscribers are slow or disconnected.

**Data flow**: It receives a turn ID and a live frame. Under a lock, it finds or creates that turn’s stream, increments the frame number, stores the frame in the bounded replay buffer, and copies the current subscriber list. If the frame ends the stream and nobody is listening, it removes the stream. After releasing the lock, it schedules delivery to each subscriber queue and returns the new cursor.

**Call relations**: This is the concrete implementation behind the Hub.publish promise. When it needs a new per-turn stream, it creates a _TurnStream with a deque ring buffer; when it needs to deliver to listeners, it hands each frame to _offer through the listener’s event loop.

*Call graph*: 2 external calls (__init__, deque).


##### `InProcessHub.subscribe`  (lines 132–162)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This lets a caller follow a turn from a previous cursor and then continue receiving new frames live. It is the main path used by user-facing surfaces that display a running turn.

**Data flow**: It receives a turn ID and optional cursor. It creates a bounded queue for this subscriber, records the subscriber’s current event loop, registers the subscriber under a lock, and copies buffered frames newer than the cursor. It yields those replayed frames first, then waits on the queue and yields live frames forever. When the caller stops listening, it unregisters the queue and may remove the turn stream if no subscribers remain.

**Call relations**: This is the concrete implementation behind the Hub.subscribe promise. Surface tailing code uses it to pump live frames to a UI, while InProcessHub.publish sends new frames into the queue that this function is awaiting.

*Call graph*: 4 external calls (__init__, Queue, get_running_loop, deque).


##### `InProcessHub.covers`  (lines 164–172)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still contains enough history to resume from a given cursor. It protects clients from assuming a gapless resume when the old frames have already been dropped.

**Data flow**: It receives a turn ID and cursor. If the cursor is empty, or the turn has no retained buffer, it returns false. Otherwise it compares the requested cursor with the earliest cursor still stored and returns whether the buffer reaches that far back.

**Call relations**: This is the concrete implementation behind the Hub.covers promise. The tailing layer calls it before deciding whether to resume from a saved cursor or use another way to recover the turn’s state.


### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `request handling and live streaming`

When a turn is running, the system wants to show small live updates as they happen: a few words of text, a tool call, a cost update, or a final status. This file sends those updates through Redis Streams, which are append-only message logs stored in Redis. Think of each turn as getting its own small ticker tape. Publishers add new strips of tape, and viewers read forward from the last strip they saw.

This matters because the live frames are useful for display, but they are not the source of truth. If an old live frame is lost, the UI can redraw or fall back to the durable final answer stored elsewhere. Redis is used here because it lets many server instances publish and subscribe across process boundaries, unlike an in-memory hub that only works inside one running server.

The file converts frame objects into a tagged JSON form before writing them, then converts them back when reading. It also keeps Redis streams bounded and temporary: streams are trimmed to a maximum length and expire after a day of no publishing. One important detail is that Redis async clients are tied to the event loop that created them, so this hub keeps a separate Redis client per event loop. That avoids subtle cross-thread async failures when background workflow code and web-serving code run on different loops.

#### Function details

##### `frame_payload`  (lines 53–56)

```
def frame_payload(frame: LiveFrame) -> dict[str, object]
```

**Purpose**: Turns one live frame object into a simple dictionary that can be safely sent through Redis as JSON. It records both what kind of frame it is and the frame's actual data, so the reader can rebuild the right kind later.

**Data flow**: A live frame comes in, such as a text update or a terminal result. The function looks up the frame's kind name, asks the frame to turn its fields into JSON-friendly values, and returns a dictionary with a kind label and data payload.

**Call relations**: Publishing uses this right before writing to Redis. It is the packing step: `RedisStreamHub.publish` hands it a frame, then serializes the returned dictionary into the stream entry.

*Call graph*: called by 1 (publish); 1 external calls (model_dump).


##### `frame_from_payload`  (lines 59–63)

```
def frame_from_payload(payload: dict[str, object]) -> LiveFrame
```

**Purpose**: Rebuilds a live frame object from the dictionary form stored in Redis. It also rejects unknown frame kinds, so bad or unexpected stream data fails clearly instead of being silently misread.

**Data flow**: A decoded payload dictionary comes in with a kind label and data. The function checks that the kind is known, chooses the matching frame model, validates the data against that model, and returns the reconstructed live frame.

**Call relations**: Subscribing uses this after reading JSON from Redis. `RedisStreamHub.subscribe` pulls a stream entry, parses its JSON, and hands the payload here so callers receive normal live frame objects instead of raw wire data.

*Call graph*: called by 1 (subscribe); 1 external calls (cast).


##### `_stream_id`  (lines 66–68)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Turns a Redis stream entry id into two numbers that can be compared in time order. Redis ids look like `milliseconds-sequence`, and comparing them as numbers tells whether one entry is before or after another.

**Data flow**: A stream id string comes in. The function splits it around the dash, converts the millisecond part and sequence part to integers, and returns them as a pair for straightforward ordering.

**Call relations**: `RedisStreamHub.covers` uses this when deciding whether a reconnecting reader's cursor still points into the retained stream. It is the small comparison tool behind the resume-safely check.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 71–80)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from Redis's `xread` response and checks that the response has the shape this code expects. This prevents the hub from accidentally treating a stream name as if it were a message.

**Data flow**: A Redis read response comes in. If it is empty, the function returns an empty list. If it is not the expected list-shaped response, it raises an error. Otherwise, it returns the entries for the first stream in the response.

**Call relations**: `RedisStreamHub.subscribe` calls this after each Redis read. It acts like a careful unpacker between Redis's low-level response format and the subscription loop that wants plain entries to process.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 96–102)

```
def _client(self) -> Redis
```

**Purpose**: Gives the current async event loop its own Redis client, creating one the first time that loop needs it. This avoids sharing a loop-bound Redis client between background work and web-serving code.

**Data flow**: The function reads the currently running asyncio event loop, which is Python's async task scheduler. It checks whether this hub already has a Redis client for that loop. If not, it creates one from the configured Redis URL, stores it, and returns it.

**Call relations**: Publishing, subscribing, and coverage checks all call this before talking to Redis. It is the hub's safety gate for Redis access, making sure each part of the program uses a client that belongs to its own async loop.

*Call graph*: called by 3 (covers, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 104–105)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name for a specific turn. This keeps every turn's live updates on its own separate stream.

**Data flow**: A turn id comes in. The function prefixes it with the shared stream namespace and returns the Redis key string for that turn's stream.

**Call relations**: Every Redis operation in this hub needs the correct stream name first. `publish`, `subscribe`, and `covers` all call this so they read or write the ticker tape belonging to the requested turn.

*Call graph*: called by 3 (covers, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 107–114)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: Adds one live frame to the Redis stream for a turn and returns the new stream cursor. Callers use the cursor to know exactly where this update landed in the live message log.

**Data flow**: A turn id and live frame come in. The function builds the stream name, converts the frame into JSON, then uses a Redis pipeline, which is a small batch of commands sent together, to append the frame and refresh the stream's expiry time. It returns Redis's entry id for the new frame.

**Call relations**: This is the write side of the hub. Workflow or server code calls it when there is a new live update. It relies on `_stream` for the key name, `frame_payload` for the wire format, and `_client` for the right Redis connection.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 116–140)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Streams live frames for a turn, starting after a given cursor or from the beginning if no cursor is provided. It keeps reading forever, yielding each new cursor and frame as updates arrive.

**Data flow**: A turn id and optional cursor come in. The function finds the turn's Redis stream and starts reading after the cursor, first trying a non-blocking read to catch retained entries, then a blocking read that waits briefly for new ones. Each Redis entry is decoded from JSON, rebuilt into a live frame, and yielded with its stream id as the next cursor.

**Call relations**: This is the read side of the hub, used by surfaces or clients that want live updates. It calls `_stream` and `_client` to read from Redis, `_stream_entries` to unpack Redis responses, and `frame_from_payload` to turn stored JSON back into frame objects. Timeouts during the wait are treated as normal idle moments, so the loop simply tries again without losing its place.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 142–148)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether a saved cursor can still be resumed without a gap. If the stream has expired, is empty, or has been trimmed past that cursor, the caller should redraw or restart from a safer point.

**Data flow**: A turn id and cursor come in. If the cursor is blank, the function returns false. Otherwise it reads the first retained entry in the turn's stream and compares that entry's id with the cursor. It returns true only when the stream still includes the cursor's position or something earlier.

**Call relations**: Reconnect logic uses this before trusting a client's old cursor. It uses `_stream` and `_client` to inspect Redis, and `_stream_id` to compare Redis ids in the same order Redis uses.

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).

## 📊 State Registers Touched

- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-inbound-message-queue` — The durable queue of incoming messages and uploads before they are admitted into conversation turns.
- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-turn-state` — The durable status of each unit of agent work, including whether it is waiting, running, paused, finished, failed, or cancelled.
- `reg-live-stream` — The live feed of turn updates, text chunks, tool events, costs, and final frames that clients and debuggers can watch.
- `reg-runtime-fleet` — The shared heartbeat and ownership records that show which server processes are alive and which work they are responsible for.
- `reg-cancellation-state` — The shared stop signal state used to cancel active turns, child tasks, tools, and abandoned work safely.
- `reg-sandbox-session` — The sandbox handle and lifecycle state for the safe workspace where code, files, browsers, and commands run.
- `reg-scheduled-jobs` — The background job and scheduled-task state that records what should run later, what is claimed, and what repeats.
- `reg-observability-context` — The shared tracing, metrics, structured logs, and trace-parent links used to understand work across processes and turns.
- `reg-redis-backplane-pool` — The optional Redis client/backplane connection state used to share live stream infrastructure across server processes.
- `reg-turn-admission-context` — Durable per-turn requester/speaker/on-behalf-of, timezone, surface context, and authorization-link metadata used to attribute, resume, and safely handle work.
- `reg-turn-replay-journal` — Durable per-turn execution checkpoints for model calls, tool results, and side-effect/idempotency markers used to resume work without duplicating paid calls or irreversible actions.
