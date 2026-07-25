# Turn admission, durable queuing, and live stream attachment  `stage-7`

This stage is the traffic controller for incoming conversation messages. It sits at the point where a user message arrives and the system must decide what happens next. The admission code is the front door. It checks rules and current state, then either starts a new agent turn, attaches the message to a turn already in progress, parks it behind spending limits or pauses, or rejects it.

If a turn should run, the durable queue records it safely and runs turns one at a time for each conversation, so replies do not overlap or get lost after a crash. It also prepares the needed tools, credentials, sandbox, and model before the agent begins work.

While the turn runs, the hub acts like a live radio channel for progress updates. Interfaces can listen, disconnect, and resume from a saved position. The Redis stream hub extends that channel across multiple server processes. Finally, the hub tail connects clients to the live stream and checks the database for final or parked states, so late or reconnecting clients see the correct ending.

## Files in this stage

### Turn admission
Inbound messages are classified as new turns, live-turn joins, spend-limited waits, scheduled pauses, or access-rule cancellations.

### `core/src/ufo/surfaces/admission.py`

`domain_logic` · `request handling and scheduled-task admission`

Think of this file as the receptionist for conversations. Every incoming message, scheduled task, or internal extension call must check in here before work is placed on the durable turn queue. That matters because this is where the system applies important rules only once: seat access, spending limits, duplicate-message protection, conversation ordering, pause resumption, and durable reply delivery.

The central method opens a database transaction and locks the conversation row. That lock is like letting only one clerk write the next ticket number at a time, so turn sequence numbers cannot collide. If the caller supplied an idempotency key, meaning “this is the same message if retried,” the code looks for an existing turn or queued inbound message and returns that instead of creating a duplicate.

If a conversation already has a live turn, a new message usually does not start a second turn. Instead it is stored as an inbound message for the existing turn, so the agent can answer everything together. If the live turn was parked because of a spend cap, a newly allowed message can wake it back up.

For new turns, the file checks seat rules first, then spend rules. Allowed turns are queued; parked turns are saved but not dispatched; rejected turns are immediately recorded as cancelled. Durable surfaces also get a writeback row so replies can be delivered later. Finally, queued turns are offered to DBOS, the background workflow queue, with careful retry behavior so crashes do not create duplicate work.

#### Function details

##### `Admission.admit_member`  (lines 82–102)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None) -
```

**Purpose**: Accepts a message from a real conversation member. It marks the admission as member-originated so any pending one-time pause in that conversation can be resumed by this member message.

**Data flow**: It receives the workspace, conversation, target agent, message text, optional speaker member, optional duplicate-protection key, and optional context. It adds a small pending-pause marker for the same workspace and conversation, then passes everything to the shared admission path. The result is the UUID of the turn that accepted the message, whether newly created, reused, or folded into a live turn.

**Call relations**: Surface code uses this member-facing entry point when someone speaks. It immediately hands off to Admission._admit, which performs the database checks, pause handling, spend and seat decisions, and possible queue dispatch.

*Call graph*: calls 1 internal fn (_admit); 1 external calls (__init__).


##### `Admission.invoke`  (lines 104–123)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: Accepts an internal message that should invoke an agent turn, without treating it as a member speaking. This is used when jobs or extensions ask an agent to do work inside an existing conversation.

**Data flow**: It receives workspace, conversation, agent, message text, and optional idempotency key and context. It passes those values to the shared admission path with no speaker member and no pending-pause marker. It returns the turn UUID chosen by the shared admission logic.

**Call relations**: Internal callers use this when they are allowed to start or join a turn but must not consume a member’s pending pause. It delegates to Admission._admit, which applies the same queueing, folding, spend, and duplicate rules as all other admissions.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission.invoke_scheduled`  (lines 125–143)

```
async def invoke_scheduled(self, workspace_id: UUID, task: ScheduledTask) -> UUID | None
```

**Purpose**: Accepts a scheduled task firing and tries to turn it into an agent turn. If another worker or newer claim has already superseded the scheduled firing, it quietly reports that no turn was admitted.

**Data flow**: It receives a workspace and a scheduled task. It first requires the task to have a claim id, which proves this worker claimed the task. It builds an idempotency key from the task id and fire time, sends the task prompt through the shared admission path, and returns a turn UUID. If the scheduled claim is no longer valid, it returns null instead of creating duplicate scheduled work.

**Call relations**: The scheduler calls this after claiming a due task. It delegates to Admission._admit for all database and queue decisions, and catches the internal superseded-schedule signal so scheduler code can simply move on.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission._admit`  (lines 145–656)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, pending_pause: _P
```

**Purpose**: This is the main admission decision engine. It turns one incoming message request into exactly the right durable outcome: reuse an existing turn, fold into a live turn, create and queue a new turn, park it, or cancel it with a visible reason.

**Data flow**: It takes the workspace, conversation, agent, message body, optional speaker, optional idempotency key, optional context, and markers saying whether this is a member pause resumption or scheduled task. Inside one database transaction, it locks the conversation, checks that the speaker and scheduled task are valid, looks for duplicate work, checks for a live turn, applies seat rules and spend rules, writes turn, inbound-message, scheduled-task, and writeback rows as needed, and decides whether the turn should be dispatched now. After the transaction commits, it may enqueue the turn for background processing. It returns the UUID of the turn that owns the message, or raises an error for invalid or inconsistent inputs.

**Call relations**: Admission.admit_member, Admission.invoke, and Admission.invoke_scheduled all funnel into this method so no caller can bypass the same boundary checks. When it decides a queued turn is ready to run, it hands off to Admission._enqueue to place the work on the DBOS queue. It also uses the seat checker, spend evaluator, turn context serialization, and terminal-frame records to make the admission result durable and understandable to the rest of the system.

*Call graph*: calls 1 internal fn (_enqueue); called by 3 (admit_member, invoke, invoke_scheduled); 14 external calls (__init__, __init__, __init__, model_dump, model_validate, delete, exists, insert, select, update (+4 more)).


##### `Admission._enqueue`  (lines 658–698)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Places an admitted queued turn onto the DBOS workflow queue, which is the background work system that will run the agent turn. It also cleans up the database marker if enqueueing is cancelled or fails, so another attempt can safely try later.

**Data flow**: It receives workspace id, conversation id, turn id, and optionally a workflow id. It builds queue options including the queue name, workflow name, workflow id, conversation partition key, and app version, then asks DBOS to enqueue the turn. If the async task is cancelled or enqueueing raises an error, it reopens a transaction and clears the turn’s dispatch timestamp while the turn is still queued; on ordinary errors it also logs that enqueueing was deferred.

**Call relations**: Admission._admit calls this only after it has committed the database rows that define the turn. This separation is important: the durable record exists first, and the queue offer comes after. If the queue offer is uncertain, the cleared dispatch marker lets recovery or a later admission try again without losing the turn.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 709–724)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: Provides a workspace-bound way for internal code to invoke an agent without passing the workspace id each time. It deliberately uses internal admission, not member admission, so it cannot consume a member pause.

**Data flow**: It receives a conversation id, agent id, message, optional idempotency key, and optional context. It combines those with the workspace id already stored on the AdmissionInvoker and calls the underlying Admission.invoke method. It returns the UUID of the admitted or reused turn.

**Call relations**: Jobs and extension workflows receive this narrower capability instead of the full Admission object. In the bigger flow, it is a small adapter that forwards internal invocation requests into Admission.invoke, which then reaches the shared Admission._admit path.


##### `AdmissionInvoker.invoke_scheduled`  (lines 726–727)

```
async def invoke_scheduled(self, task: ScheduledTask) -> UUID | None
```

**Purpose**: Provides a workspace-bound way to admit a claimed scheduled task. It keeps scheduler-style invocation inside the same safe admission boundary.

**Data flow**: It receives a scheduled task and combines it with the workspace id stored on the invoker. It calls the underlying Admission.invoke_scheduled method. The result is either the UUID of the turn created or reused for that firing, or null if the scheduled firing was superseded.

**Call relations**: Scheduler or background code can use this adapter when it already operates inside one workspace. It forwards to Admission.invoke_scheduled, which then relies on Admission._admit for claim validation, idempotency, pause linkage, and dispatch decisions.


##### `MemberAdmission.admit`  (lines 738–756)

```
async def admit(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None) -> UUID
```

**Purpose**: Provides a workspace-bound way for user-facing surfaces to admit a member’s message. It ensures member messages go through the member-specific path that can resume pending pauses and enforce speaker seat rules.

**Data flow**: It receives a conversation id, agent id, message, optional idempotency key, optional context, and a speaker member id. It combines those with the stored workspace id and calls Admission.admit_member. It returns the UUID of the turn that accepted the member’s message.

**Call relations**: Surface integrations receive this limited capability rather than the full Admission object. In the larger flow, it forwards member speech to Admission.admit_member, which then delegates to Admission._admit for duplicate detection, live-turn folding, seat and spend checks, database writes, and possible queueing.


### Durable turn queueing
Accepted turns are serialized per conversation, persisted across crashes, and prepared with the runtime services needed for agent execution.

### `core/src/ufo/loop/queue.py`

`orchestration` · `request handling`

A turn is the system’s basic job: take new input for a conversation, let the agent reason, call tools if needed, and finish with a terminal result. This file is the dispatcher and workshop setup for that job. Without it, turns could run out of order, lose their place after a crash, miss their sandbox, or leave clients waiting forever after a failure.

The file defines a DBOS queue, which is a durable background job queue. “Durable” means the job state is stored so another process can continue or safely replay work after a restart. The queue is partitioned by conversation, like having one checkout lane per conversation, so two turns from the same conversation do not step on each other.

At startup, the process installs a Runtime object. This is a bundle of shared services: configuration, blob storage, model registry, tool registry, sandbox carrier, credential store, search, memory, and messaging hub. When DBOS starts a queued workflow, the code binds the correct workspace, claims the turn, loads its database record, prepares tools and prompts, opens or resumes the sandbox, and builds a TurnEngine. The engine then does the real agent work.

The file also contains important safety nets. If a newer workflow has already taken over, it repairs the transcript and exits. If setup fails outside the engine, it writes a failed terminal record and publishes it so waiting clients are not stuck.

#### Function details

##### `init_runtime`  (lines 107–111)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the process-wide Runtime bundle that turn workflows need in order to run. It is meant to happen once when the service starts, so every queued turn can find the same shared services.

**Data flow**: A fully built Runtime goes in. The function checks whether one is already installed; if so, it raises an error to prevent accidentally replacing live service dependencies. If no runtime exists, it stores the Runtime in this module for later workflows to use.

**Call relations**: This is the setup step that makes later workflow execution possible. When a DBOS workflow eventually calls into turn execution, _execute_turn reads the runtime installed here.


##### `reset_runtime`  (lines 114–119)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed Runtime so tests can install a fresh one. In normal serving, the runtime is installed once and not reset.

**Data flow**: No input is needed. The function changes the module’s stored runtime back to empty, so a later init_runtime call can provide a replacement.

**Call relations**: This is a test seam rather than part of the normal turn path. It exists because init_runtime intentionally refuses to overwrite an existing runtime.


##### `_execute_turn`  (lines 122–138)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs one queued turn under the correct workspace. The workspace binding matters because database access, credentials, model billing, and workspace-specific keys must all be scoped to the right tenant.

**Data flow**: It receives workspace_id and turn_id as strings from the durable workflow. It checks that the process runtime has been installed, converts the workspace id into a UUID, enters that workspace context, and then passes the runtime and turn id to _run_turn. It returns the final status string from the turn run.

**Call relations**: turn_workflow calls this when DBOS starts the durable workflow. _execute_turn then hands off to _run_turn for the real setup and execution, while wrapping that work in the workspace context provided by ws.

*Call graph*: calls 1 internal fn (_run_turn); called by 1 (turn_workflow); 2 external calls (ws, UUID).


##### `_enqueue_handoff`  (lines 141–176)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: Queues a replacement or follow-up workflow when ownership of a turn needs to be handed off. This keeps turn processing durable while preserving the rule that work is partitioned by conversation.

**Data flow**: It receives the DBOS client, workspace id, turn id, conversation id, and workflow id for the handoff. It builds enqueue options that say which queue and workflow to use and which conversation partition to place the job in. If enqueueing succeeds, the new workflow is scheduled. If enqueueing is cancelled or fails, it clears the turn’s dispatch marker in the database so the turn can be scheduled again later; on ordinary failure it also logs the problem.

**Call relations**: _run_turn calls this after claiming a turn if the claim process reports that another workflow should be enqueued. It uses DBOSClient.enqueue_async to place the durable job, and uses workspace_tx plus SQL updates to undo the dispatch marker if enqueueing does not complete.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 179–317)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds everything needed to execute a turn, then runs the TurnEngine. It is the main coordinator for one unit of agent work.

**Data flow**: It receives the Runtime and a turn id. First it claims the turn so duplicate workers do not both run it. If the turn was superseded, it loads the turn and asks TranscriptRepair to make the conversation record consistent. If a handoff is needed, it enqueues that handoff. For a real run, it loads the turn, agent, and conversation member; gathers tools, hooks, skills, model settings, prompts, and optional subagent settings; opens or resumes the sandbox; mounts any preloaded skills; then creates a TurnEngine with all of these pieces. The engine runs and returns a terminal frame, whose status becomes the function’s result. If the turn is parked, it returns parked. If an unexpected setup failure happens, it records a failed terminal result and returns failed.

**Call relations**: _execute_turn calls this inside the correct workspace. This function calls helper functions in this file for loading database records, enqueueing handoffs, opening the sandbox, and recording failures. It also hands the assembled runtime pieces to TurnEngine, which performs the actual model rounds, tool calls, transcript work, and compaction.

*Call graph*: calls 4 internal fn (_commit_failed_terminal, _enqueue_handoff, _load_turn, _open_sandbox); called by 1 (_execute_turn); 21 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+11 more)).


##### `_commit_failed_terminal`  (lines 320–352)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Writes a final failed result for a turn when something goes wrong outside the engine. This is a backstop so a client waiting for the turn always gets an ending instead of waiting forever.

**Data flow**: It receives the messaging hub, the turn id, and the exception that caused the failure. It creates a TerminalFrame containing a failed status, the error type, and a shortened error message. It repeatedly tries to update the turn row in the database and publish the terminal message. If that attempt itself fails, it logs the retry, waits a little longer each time up to a maximum delay, and tries again until it succeeds.

**Call relations**: _run_turn calls this only for unexpected exceptions that happen outside normal engine failure handling. It uses workspace_tx and SQL updates to store the terminal result, then Hub.publish to notify listeners through a Terminal message.

*Call graph*: calls 1 internal fn (publish); called by 1 (_run_turn); 6 external calls (__init__, __init__, sleep, update, workspace_tx, log).


##### `turn_workflow`  (lines 356–357)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Defines the DBOS durable workflow entry point for running a turn. DBOS uses this registered function when it pulls turn jobs from the queue.

**Data flow**: It receives workspace_id and turn_id as workflow arguments. It passes them directly to _execute_turn and returns the status string that comes back.

**Call relations**: DBOS calls this function as the named turn workflow. It is intentionally thin: it delegates the actual workspace binding and turn execution to _execute_turn.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 360–411)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, UUID | None]
```

**Purpose**: Loads the database information needed to run a turn: the turn itself, the agent’s prompt and model, and the conversation member used for memory. This keeps the rest of the workflow working with typed records instead of raw database rows.

**Data flow**: It receives a turn id. It queries the turn, agent, and conversation tables in the current workspace transaction. From the returned row, it builds a Turn object, validates any saved context or terminal data, builds an Agent object from the stored prompt and model, and returns those along with the conversation member id.

**Call relations**: _run_turn calls this after claiming a turn, and also when a turn has been superseded and needs transcript repair. It uses workspace_tx and SQL select queries to read the records, then hands typed Turn and Agent objects back to the turn orchestration flow.

*Call graph*: called by 1 (_run_turn); 6 external calls (__init__, __init__, model_validate, model_validate, select, workspace_tx).


##### `_open_sandbox`  (lines 414–447)

```
async def _open_sandbox(carrier: Carrier, backend: str, blob: BlobStore, workspace_fs: SandboxFsCredentialMinter | None, proxy: ProxyEndpoint, turn: Turn, grants: GrantStore | None, clis: Mapping[str,
```

**Purpose**: Creates or resumes the sandbox used by the turn. The sandbox is the isolated place where tools and command-line programs can run without directly touching the host system.

**Data flow**: It receives the sandbox carrier, backend name, blob storage, optional workspace filesystem credential minter, proxy settings, the turn, optional grants, and connector command-line credential declarations. It reads any saved sandbox handle from the conversation, turns that into a resume id if it matches the backend, prepares a workspace mount and environment variables, then asks the carrier to create or attach to the sandbox. It formats the returned handle and stores it back on the conversation row if it changed. The resulting SandboxHandle comes out.

**Call relations**: _run_turn calls this before creating the SandboxSession for the engine. Internally it calls _stored_sandbox_handle, _workspace_mount, _grant_cli_env, and sometimes _persist_sandbox_handle, then delegates sandbox creation to Carrier.create.

*Call graph*: calls 5 internal fn (_grant_cli_env, _persist_sandbox_handle, _stored_sandbox_handle, _workspace_mount, create); called by 1 (_run_turn); 4 external calls (__init__, __init__, format_sandbox_handle, sandbox_handle_id).


##### `_grant_cli_env`  (lines 450–486)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], turn: Turn) -> dict[str, str]
```

**Purpose**: Builds environment variables that let connector command-line tools inside the sandbox authenticate through existing grants. A grant is permission for an agent to use a connected account.

**Data flow**: It receives an optional GrantStore, connector CLI declarations, and the turn. If grants or CLI declarations are missing, it returns an empty dictionary. Otherwise it decides which member is acting for the turn, reads active grants for the workspace and agent, and for each provider chooses the one usable account if there is exactly one. It turns that account id into a sentinel value, which is a special token understood by the proxy, and stores it under the CLI’s environment variable name. If more than one account could match, it logs the ambiguity and skips that variable rather than guessing.

**Call relations**: _open_sandbox calls this while preparing SandboxSpec. It relies on GrantStore.active_grants to find permissions and grant_sentinel to create the value that sandbox CLIs and the proxy can both recognize.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (_open_sandbox); 2 external calls (grant_sentinel, log).


##### `_stored_sandbox_handle`  (lines 489–498)

```
async def _stored_sandbox_handle(conversation_id: UUID, workspace_id: UUID) -> str | None
```

**Purpose**: Reads the saved sandbox handle for a conversation. This lets a new process reconnect to the same sandbox instead of always starting from scratch.

**Data flow**: It receives a conversation id and workspace id. It opens a workspace database transaction, selects the sandbox_handle field from the conversation row, and returns either the saved string or None.

**Call relations**: _open_sandbox calls this at the start of sandbox setup. The returned handle is used to decide whether Carrier.create should try to resume an existing sandbox.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (select, workspace_tx).


##### `_persist_sandbox_handle`  (lines 501–510)

```
async def _persist_sandbox_handle(conversation_id: UUID, workspace_id: UUID, handle: str) -> None
```

**Purpose**: Stores the current sandbox handle on the conversation row. This makes the sandbox discoverable by future turns, restarted workers, and cleanup code.

**Data flow**: It receives a conversation id, workspace id, and formatted handle string. It opens a workspace database transaction and updates the matching conversation row so its sandbox_handle field contains the new value.

**Call relations**: _open_sandbox calls this only when the sandbox handle returned by the carrier differs from what was already stored. It is the write-side partner to _stored_sandbox_handle.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (update, workspace_tx).


##### `_workspace_mount`  (lines 513–558)

```
async def _workspace_mount(blob: BlobStore, workspace_fs: SandboxFsCredentialMinter | None, conversation_id: UUID) -> MountSpec
```

**Purpose**: Prepares the sandbox’s view of the conversation workspace files. It deliberately exposes only the conversation’s workspace folder, not the transcript or other private blob data.

**Data flow**: It receives the blob storage backend, an optional credential minter for sandbox filesystem access, and the conversation id. For local filesystem storage, it creates the conversation’s workspace directory, fixes ownership for the sandbox user when running as root, and returns a filesystem MountSpec with an absolute host path. For S3 storage, it requires the credential minter, asks it for short-lived credentials limited to this conversation’s workspace prefix, and returns an S3 MountSpec. For any unsupported blob backend, it raises an error.

**Call relations**: _open_sandbox calls this while building the SandboxSpec. It may call SandboxFsCredentialMinter.mint for S3-backed workspaces, or use local filesystem and OS helpers for filesystem-backed workspaces, then hands the MountSpec to the sandbox carrier.

*Call graph*: calls 1 internal fn (mint); called by 1 (_open_sandbox); 4 external calls (__init__, to_thread, geteuid, workspace_key_prefix).


### Live stream delivery
Turn progress frames are published, replayed, distributed across processes, and tailed by reconnecting clients until a final or parked state is reached.

### `core/src/ufo/hub.py`

`io_transport` · `cross-cutting during live turn streaming`

When an agent is working, the user interface needs a steady stream of updates: new text, tool activity, cost changes, loaded skills, and final completion. This file is the in-memory “broadcast room” for those updates. Publishers drop frames into the room, and any connected surface, such as a CLI or web view, receives them.

The file defines the kinds of live frames that can appear. Some are ordinary progress updates, such as text, cost ticks, tool calls, or skill loads. Others end the visible stream, such as a terminal frame or a parked message saying the turn stopped because of a spending cap.

The key idea is cursor replay. Each frame gets a simple increasing cursor, like a numbered ticket. If a surface disconnects and returns with its last cursor, the hub can replay the frames after that point from a bounded ring buffer. This avoids repainting everything when the missing gap is still available.

The in-process implementation is deliberately non-blocking for publishers. If a subscriber is too slow and its queue fills up, the oldest queued frame is dropped for that subscriber. This protects the agent’s work from being stalled by a slow screen. A lock is used because publishers and subscribers may run on different event loops or threads.

#### Function details

##### `Hub.publish`  (lines 75–75)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This is the interface promise for publishing one live frame for a turn. A caller uses it when it has a new piece of progress, activity, cost, or final state to show to connected surfaces.

**Data flow**: It receives a turn identifier and a live frame. An implementation stores or broadcasts that frame and returns a cursor string that marks the frame’s position in that turn’s stream.

**Call relations**: This protocol method is the contract used by other parts of the system. For example, the queue failure path calls it when it needs to publish a failed terminal frame, while concrete hubs such as InProcessHub provide the actual behavior.

*Call graph*: called by 1 (_commit_failed_terminal).


##### `Hub.subscribe`  (lines 77–79)

```
def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This is the interface promise for following a turn’s live stream. A surface uses it to receive past frames after a cursor and then keep receiving new frames as they arrive.

**Data flow**: It receives a turn identifier and, optionally, the last cursor the caller has already seen. An implementation yields pairs of cursor and frame, first for replayed missed frames and then for live updates.

**Call relations**: This protocol method is the contract used by the live tailing code. The surface-side pump calls it when it needs to connect a user-facing stream to the hub.

*Call graph*: called by 1 (_pump).


##### `Hub.covers`  (lines 81–81)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This is the interface promise for checking whether a reconnect can safely resume from a cursor. It answers whether the hub still has enough buffered history to avoid a gap.

**Data flow**: It receives a turn identifier and a cursor. An implementation checks its retained frames and returns true if that cursor is still covered, or false if the caller should assume some frames may be missing.

**Call relations**: The surface tailing flow calls this before deciding whether to resume from the hub or fall back to a more durable source of truth.

*Call graph*: called by 1 (tail_frames).


##### `_offer`  (lines 84–87)

```
def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None
```

**Purpose**: This helper puts one frame into a subscriber’s queue without ever waiting. If the queue is already full, it removes the oldest queued frame first so the newest update can fit.

**Data flow**: It receives a queue and a cursor-frame pair. It checks whether the queue is full, optionally discards the oldest item, and then adds the new item; it returns nothing.

**Call relations**: InProcessHub.publish schedules this helper onto each subscriber’s event loop. That lets publishing stay fast and safe even when subscribers are running elsewhere.


##### `InProcessHub.publish`  (lines 116–130)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: This publishes a live frame to all current subscribers of one turn and records it briefly for possible replay. It is built so publishing never blocks on a slow subscriber.

**Data flow**: It receives a turn identifier and a frame. Under a lock, it finds or creates that turn’s stream, increments the stream’s cursor number, stores the frame in a bounded replay buffer, and copies the current subscriber list. After releasing the lock, it schedules delivery to each subscriber queue. It returns the new cursor string. If the frame ends the stream and nobody is subscribed, it removes the turn’s in-memory stream to free memory.

**Call relations**: This is the concrete implementation behind the Hub.publish contract. Other parts of the system call the hub when new live output exists; this method records the frame and hands it off to subscriber queues using _offer.

*Call graph*: 2 external calls (__init__, deque).


##### `InProcessHub.subscribe`  (lines 132–162)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: This lets a caller follow one turn’s stream, starting with any buffered frames it missed and then continuing with live frames. It is the main path used by user-facing surfaces to watch progress.

**Data flow**: It receives a turn identifier and an optional cursor. It creates a bounded queue for live frames, records the subscriber’s current event loop, and under a lock registers that queue with the turn stream. At the same time it snapshots buffered frames whose cursor is newer than the caller’s cursor. It then yields replayed frames first, followed by new frames from the queue. When the caller stops listening, it unregisters the queue and removes the turn stream if no subscribers remain.

**Call relations**: This is the concrete implementation behind the Hub.subscribe contract. The surface pump calls it to tail frames. It coordinates with publish through the same lock so replayed frames and live frames do not overlap or leave a race-time gap.

*Call graph*: 4 external calls (__init__, Queue, get_running_loop, deque).


##### `InProcessHub.covers`  (lines 164–172)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: This checks whether the in-memory replay buffer still reaches back far enough to resume from a given cursor. It helps a reconnecting surface know whether it can catch up cleanly.

**Data flow**: It receives a turn identifier and cursor string. If the cursor is empty, or the turn has no retained stream or no buffered frames, it returns false. Otherwise it compares the buffer’s earliest cursor with the requested cursor and returns true when the requested point is still within the retained range.

**Call relations**: This is the concrete implementation behind the Hub.covers contract. The surface tailing code asks this before relying on cursor replay; if it returns false, the surface can choose a safer redraw or durable polling path.


### `extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py`

`io_transport` · `request handling and live-stream delivery`

This file is a Redis-backed live update hub. When a turn is running, it may produce many small “frames”: pieces of text, tool-call notices, cost updates, or a final terminal signal. Instead of storing those short-lived updates in the database, this hub writes them to Redis Streams, which are like append-only message logs with cursor positions. A client watching the turn can start from a cursor and read forward, so reconnects can replay recent frames without losing their place.

The key idea is one Redis stream per turn. `publish` converts a typed frame into a small JSON message, appends it to the turn’s stream, trims old entries to a maximum length, and refreshes the stream’s expiry time. `subscribe` reads entries from a cursor, first catching up on already-retained frames and then waiting for new ones. A normal idle timeout while waiting is not treated as failure; it simply tries again.

One important detail is that Redis async clients are tied to the event loop that created them. An event loop is the scheduler that runs asynchronous tasks. Because this system may publish from one loop and subscribe from another, the hub keeps a separate Redis client per loop. Like giving each kitchen station its own order pad, this avoids sharing state that cannot safely cross threads or loops.

#### Function details

##### `frame_payload`  (lines 53–56)

```
def frame_payload(frame: LiveFrame) -> dict[str, object]
```

**Purpose**: Turns a live frame object into a simple dictionary that can be sent through Redis as JSON. It records both what kind of frame it is and the frame’s data, so the reader can rebuild the same kind of object later.

**Data flow**: It receives a `LiveFrame`, such as a text delta or terminal frame. It looks up the frame’s kind name from its concrete Python type, asks the frame to dump its fields in JSON-friendly form, and returns a dictionary with `kind` and `data` keys. It does not change the frame.

**Call relations**: When `RedisStreamHub.publish` is about to append a frame to Redis, it calls this function first. The resulting plain dictionary is then turned into a JSON string and stored in the stream.

*Call graph*: called by 1 (publish); 1 external calls (model_dump).


##### `frame_from_payload`  (lines 59–63)

```
def frame_from_payload(payload: dict[str, object]) -> LiveFrame
```

**Purpose**: Rebuilds a typed live frame object from the dictionary form read out of Redis. It also protects the system from unknown frame kinds by raising an error instead of guessing.

**Data flow**: It receives a dictionary that should contain a `kind` label and `data` fields. It checks that the kind is a known string, chooses the matching frame model, validates the data against that model, and returns the rebuilt `LiveFrame` object.

**Call relations**: When `RedisStreamHub.subscribe` reads a JSON message from Redis, it parses the JSON and passes the dictionary here. This function hands back the real frame object that the subscriber yields to the rest of the live-stream flow.

*Call graph*: called by 1 (subscribe); 1 external calls (cast).


##### `_stream_id`  (lines 66–68)

```
def _stream_id(entry_id: str) -> tuple[int, int]
```

**Purpose**: Converts a Redis Stream entry ID into numbers that can be compared reliably. Redis IDs look like `milliseconds-sequence`, and this helper turns that text into a pair of integers.

**Data flow**: It receives an entry ID string from Redis. It splits the string around the dash, treats the first part as a millisecond timestamp and the second part as a sequence number, and returns them as a tuple of integers.

**Call relations**: `RedisStreamHub.covers` uses this when deciding whether a saved cursor is still within the retained part of a stream. By comparing numeric IDs, it can tell whether Redis still has the entries needed for a gap-free resume.

*Call graph*: called by 1 (covers).


##### `_stream_entries`  (lines 71–80)

```
def _stream_entries(batch: XReadResponse) -> list[StreamEntry]
```

**Purpose**: Extracts the actual stream entries from the shape returned by Redis `XREAD`. It makes sure the response has the expected form, so the code fails clearly if Redis replies in a different format.

**Data flow**: It receives the raw batch returned by Redis. If the batch is empty, it returns an empty list. If the batch is not the expected list shape, it raises a `TypeError`. Otherwise, it pulls out and returns the entries for the first stream in the response.

**Call relations**: `RedisStreamHub.subscribe` calls this after each Redis read. This keeps the subscribe loop focused on cursor reading and frame decoding, while this helper deals with the Redis response wrapper.

*Call graph*: called by 1 (subscribe).


##### `RedisStreamHub._client`  (lines 96–102)

```
def _client(self) -> Redis
```

**Purpose**: Gets the Redis client for the currently running asynchronous event loop. If this loop has not used Redis yet, it creates and remembers a new client for it.

**Data flow**: It reads the current event loop, checks the hub’s `_clients` dictionary for an existing Redis client for that loop, and returns it if present. If none exists, it builds one from the configured Redis URL, stores it under that loop, and returns it.

**Call relations**: `publish`, `subscribe`, and `covers` all call this before talking to Redis. This is the safety valve that prevents async Redis clients from being shared across different event loops.

*Call graph*: called by 3 (covers, publish, subscribe); 2 external calls (get_running_loop, from_url).


##### `RedisStreamHub._stream`  (lines 104–105)

```
def _stream(self, turn_id: UUID) -> str
```

**Purpose**: Builds the Redis stream name for one turn. It gives every turn its own separate stream by combining a fixed prefix with the turn ID.

**Data flow**: It receives a turn UUID. It formats that ID into a Redis key string such as a namespaced stream name and returns the string. It does not read Redis or change anything.

**Call relations**: `publish`, `subscribe`, and `covers` call this whenever they need to address the correct Redis stream for a turn. It centralizes the naming rule so all three operations look at the same place.

*Call graph*: called by 3 (covers, publish, subscribe).


##### `RedisStreamHub.publish`  (lines 107–114)

```
async def publish(self, turn_id: UUID, frame: LiveFrame) -> str
```

**Purpose**: Appends one live frame to the Redis stream for a turn and returns the new stream cursor. This is how running work broadcasts small live updates to any surface that is watching.

**Data flow**: It receives a turn ID and a live frame. It turns the turn ID into a stream name, converts the frame to a JSON string, then uses a Redis pipeline to add the frame to the stream and refresh the stream’s expiry time. Redis returns the new entry ID, and this method returns that ID as a string cursor.

**Call relations**: This is the writer side of the hub. It uses `_stream` to choose the stream, `_client` to get the correct loop-local Redis connection, and `frame_payload` to prepare the message. Subscribers later use the returned cursor to continue reading from the right place.

*Call graph*: calls 3 internal fn (_client, _stream, frame_payload); 1 external calls (dumps).


##### `RedisStreamHub.subscribe`  (lines 116–140)

```
async def subscribe(self, turn_id: UUID, cursor: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Continuously reads live frames from a turn’s Redis stream, starting after a given cursor. It can replay retained frames first and then wait for new frames as they arrive.

**Data flow**: It receives a turn ID and an optional cursor. It chooses the stream, starts from the given cursor or from the beginning, and repeatedly asks Redis for up to a batch of entries. If no entries are ready, it performs a blocking wait with a timeout. For each entry found, it updates the cursor, decodes the stored JSON frame, rebuilds the typed frame object, and yields the new cursor together with the frame.

**Call relations**: This is the reader side of the hub. It uses `_stream` and `_client` to reach Redis, `_stream_entries` to unwrap Redis read responses, and `frame_from_payload` to reconstruct frames. If Redis times out while waiting, it simply loops and reads again, because an idle wait is expected behavior rather than a broken stream.

*Call graph*: calls 4 internal fn (_client, _stream, _stream_entries, frame_from_payload); 1 external calls (loads).


##### `RedisStreamHub.covers`  (lines 142–148)

```
async def covers(self, turn_id: UUID, cursor: str) -> bool
```

**Purpose**: Checks whether Redis still has enough history for a subscriber to resume from a saved cursor without a gap. If the needed entries have been trimmed or expired, the caller knows it should redraw or restart from a safer point.

**Data flow**: It receives a turn ID and a cursor. If the cursor is empty, it returns `false`. Otherwise, it reads the first retained entry in that turn’s Redis stream. If the stream has no entries, it returns `false`. If there is a first entry, it compares that first retained ID with the cursor and returns whether the cursor is still at or after the retained beginning.

**Call relations**: This method uses `_stream` to find the turn’s stream, `_client` to query Redis, and `_stream_id` to compare Redis entry IDs numerically. It supports reconnect logic by answering the question: can this cursor still be replayed cleanly, or has Redis already dropped the needed history?

*Call graph*: calls 3 internal fn (_client, _stream, _stream_id).


### `core/src/ufo/surfaces/hub_tail.py`

`orchestration` · `live turn streaming and reconnect handling`

A “turn” is streamed as small live frames, like words appearing one by one in a chat window. The tricky part is timing: a viewer may arrive after the turn has already started, after some frames were missed, or even after the turn already ended on another event loop. This file makes that safe.

It does this by listening to two sources at once. One source is the live hub, which is like a bulletin board for frames as they are published. The other source is the durable database record for the turn, which can say, “this turn ended” or “this turn is parked.” Parked means the turn is paused rather than finished, for example because a spending cap was reached.

The main stream stops when it sees either a final terminal frame or a parked notice. If the live hub drops a frame because a queue is full, correctness is still protected because the database poll will eventually find the durable end-or-parked state. Reconnects use a cursor, which is a bookmark saying what the caller last saw. If the hub can still resume from that bookmark, it does; otherwise the stream starts from the retained live history.

#### Function details

##### `tail_frames`  (lines 27–53)

```
async def tail_frames(hub: Hub, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Streams live frames for one turn until the turn either finishes or becomes parked. It is the main helper used by surfaces that need to show a live turn to a caller, including callers who reconnect with a last-seen cursor.

**Data flow**: It receives a hub, a turn ID, and optionally a cursor bookmark called `since`. It first checks whether the hub can resume from that cursor, starts one background task to read live hub frames, and starts another background task to poll the database for a terminal or parked state. It yields frames as they arrive, and stops after yielding a `Terminal` or `Parked` frame; when it stops, it cancels both background tasks.

**Call relations**: HubTailer.tail calls this as the real stream producer. Inside, it asks the hub whether a cursor is still covered, starts _pump to feed live hub messages into a shared queue, starts _poll_status to feed durable end-state messages into the same queue, and directly calls turn_status_frame once at the start in case the turn already ended before streaming began.

*Call graph*: calls 4 internal fn (covers, _poll_status, _pump, turn_status_frame); called by 1 (tail); 3 external calls (Queue, ensure_future, gather).


##### `_pump`  (lines 56–63)

```
async def _pump(hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: Copies live frames from the hub subscription into the shared queue used by tail_frames. It is the live side of the two-source race.

**Data flow**: It receives the hub, the turn ID, the starting cursor, and a queue. It subscribes to the hub for that turn and places every received cursor-and-frame pair into the queue. If the subscription fails, it records a log message rather than crashing the whole stream.

**Call relations**: tail_frames starts this in the background when a caller begins tailing a turn. It depends on Hub.subscribe for the actual live feed, and it hands all received items back to tail_frames through the queue.

*Call graph*: calls 1 internal fn (subscribe); called by 1 (tail_frames); 1 external calls (log).


##### `_poll_status`  (lines 66–75)

```
async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None
```

**Purpose**: Periodically checks the database to see whether the turn has reached a durable stopping point. This protects the stream from missing the final state if the live hub is late, unavailable, or no longer has the needed frames.

**Data flow**: It receives a turn ID and the shared queue. Once per polling interval, it asks turn_status_frame whether the turn is finished or parked. If a frame is found, it puts that frame into the queue with an empty cursor and then exits. If something goes wrong, it logs the failure.

**Call relations**: tail_frames starts this alongside _pump. While _pump listens to live hub messages, _poll_status watches the stored turn record; whichever source produces a terminal-or-parked frame first causes tail_frames to end the stream.

*Call graph*: calls 1 internal fn (turn_status_frame); called by 1 (tail_frames); 2 external calls (sleep, log).


##### `turn_status_frame`  (lines 78–107)

```
async def turn_status_frame(turn_id: UUID) -> LiveFrame | None
```

**Purpose**: Looks up a turn in the database and returns the frame that should end its stream, if one exists. It understands both true completion and the special parked state.

**Data flow**: It receives a turn ID. It opens a workspace database transaction, reads the turn’s status, stored terminal frame, workspace, speaker, admission source, and related member information. If the turn has a stored terminal frame, it converts that stored data into a `Terminal` live frame. If the turn is not parked, it returns nothing. If the turn is parked, it may check seat admission rules; if the relevant seat was revoked, it returns a parked frame with the seat-revoked message, otherwise it returns the normal spending-cap parked message.

**Call relations**: tail_frames calls this once before waiting on live messages, so late subscribers immediately see an already-ended or already-parked turn. _poll_status calls it repeatedly so the durable database state can end the stream even if the hub path does not deliver the final notice.

*Call graph*: called by 2 (_poll_status, tail_frames); 8 external calls (__init__, __init__, __init__, model_validate, select, workspace_tx, gate_member, seat_gate_absent).


##### `terminal_frame`  (lines 110–120)

```
async def terminal_frame(turn_id: UUID) -> TerminalFrame | None
```

**Purpose**: Fetches only the committed terminal frame for a turn, without considering the parked state. Use it when the caller specifically wants the final stored result and not the live-stream stopping behavior.

**Data flow**: It receives a turn ID, opens a workspace database transaction, and reads the turn’s stored terminal field. If there is no turn or no terminal data yet, it returns nothing. If terminal data exists, it validates and converts that stored data into a TerminalFrame object.

**Call relations**: This function is a narrower database lookup helper. Unlike turn_status_frame, it is not part of the live tailing race in this file; it simply exposes the durable terminal result for callers that need that exact record.

*Call graph*: 3 external calls (model_validate, select, workspace_tx).


##### `HubTailer.tail`  (lines 132–133)

```
def tail(self, turn_id: UUID, since: str='') -> AsyncIterator[tuple[str, LiveFrame]]
```

**Purpose**: Provides a small object-oriented wrapper around tail_frames for code that is given a HubTailer instead of importing this module directly. It lets surface code ask for a turn stream through an injected dependency.

**Data flow**: It receives a turn ID and optional last-seen cursor. It passes its stored hub, the turn ID, and the cursor to tail_frames, and returns the resulting asynchronous stream of cursor-and-frame pairs.

**Call relations**: Surface code can call HubTailer.tail as the public seam. This method immediately delegates to tail_frames, which performs the actual work of combining hub subscription and database polling.

*Call graph*: calls 1 internal fn (tail_frames).

## 📊 State Registers Touched

- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-permission-grants` — The permission ledger saying which agent may use which connected account or provider access.
- `reg-tool-catalog` — The shared list of tools the agent may call, including their names, inputs, safety labels, and handlers.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-live-event-stream` — The live progress channel that lets clients attach, resume, and receive streamed turn updates.
- `reg-inbound-surface-state` — The stored inbound messages and surface delivery keys that connect Slack, web, terminal, and other fronts to conversations.
- `reg-runtime-instance-fleet` — The heartbeat records for live worker and runtime processes, used to detect dead workers and clean up abandoned work.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-seat-billing-state` — The workspace seat limits, granted seats, included seats, and external billing integration state.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-database-connection-pool` — The process-wide database engine/session factory and connection pool used by request handlers, workers, schedulers, and persistence code.
- `reg-redis-connection-pool` — Process-wide Redis client/connection pool and stream backend handles used to distribute live turn events across server processes.
- `reg-adapter-implementation-registry` — Process-wide mapping from configured backend/provider names to implementation adapters for Redis hubs, sandboxes, browsers, models, search, sources, and related services.
