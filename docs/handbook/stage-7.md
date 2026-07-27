# Turn dispatch, recovery, cancellation, and process presence  `stage-7`

This stage is the system’s traffic controller for conversation work. It sits in the main work loop and also runs behind-the-scenes safety checks. A “turn” is one unit of agent work in a conversation. TurnEngine.run starts that work, while the durable queue decides who may run it and records how it ends.

The queue is the central conveyor belt. Workers claim waiting turns, process each conversation in order, and write a final success or failure result. If a worker crashes, the queue can make the abandoned turn available again instead of leaving it stuck.

runtime_instance is like a roll call for live serve processes. Each process keeps its presence visible, while background checks recover lost work and clean up cancellations, including child turns left behind by cancelled parent turns.

cancellation.py provides the safe stop procedure: tell the running workflow to stop first, then mark the database record cancelled. candidates.py is the guarded doorway for finding which workspaces have queued work, returning only workspace IDs. __init__.py simply makes the loop code importable.

## Files in this stage

### Turn coordination helpers
Shared helpers define safe cancellation behavior and the controlled discovery of workspaces with queued work.

### `core/src/ufo/cancellation.py`

`domain_logic` · `cancellation paths and recovery reconciliation`

A “turn” is one unit of work in the system, and some turns can start other turns as sub-work. Cancelling one turn does not automatically cancel all of its children, so the system needs a small, reliable building block that cancels exactly one turn. This file is that building block.

The important rule here is: first tell the durable workflow system to cancel the work, then write “cancelled” into the turn’s database row. That order matters. If the system crashes halfway through, it is safer to have a database row that still looks unfinished than to have a row saying “cancelled” when the workflow was never actually told to stop. In that safer unfinished state, other recovery or reconciliation code can still find it and try again.

The function also protects completed work. Before cancelling, it checks the turn’s current status in the database. If the turn is already finished, failed, or otherwise terminal, it leaves it alone. This prevents a late cancellation request from overwriting a real completed result. As an analogy, it checks whether a train has already arrived before trying to stop it; if it has arrived, it does nothing.

#### Function details

##### `cancel_one_turn`  (lines 23–58)

```
async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> bool
```

**Purpose**: Cancels one specific turn if it is still in progress. It is used when any part of the system needs the same safe cancellation behavior: stop the durable workflow first, then mark the turn as cancelled in the database.

**Data flow**: It receives a DBOS client, which can talk to the durable workflow system, and a turn ID. It first opens a database transaction and reads that turn’s current status. If the status is already terminal, it returns False and changes nothing. If the turn is still active, it asks DBOS to cancel the workflow whose ID matches the turn ID. After that, it opens another database transaction and updates the turn row to say it is cancelled, stores a cancelled terminal frame, and refreshes the updated time. It returns True only if that database update actually changed one row.

**Call relations**: This function is the shared cancellation step used by higher-level cancellation flows such as user-triggered cancellation, subagent cancellation, and reconciliation of cancellations. Inside its own flow, it calls the database transaction helper to read and write the turn row, uses SQLAlchemy to build the select and update statements, and calls DBOSClient.cancel_workflow_async to tell the durable workflow engine to stop the turn’s workflow before committing the cancelled status.

*Call graph*: 4 external calls (cancel_workflow_async, select, update, workspace_tx).


### `core/src/ufo/candidates.py`

`domain_logic` · `job scheduling and dispatch ticks`

Jobs in this system are not allowed to run in a vague, global way. Before a job handler runs, the dispatcher must know which workspace to bind it to, like choosing the right locked room before doing any work inside. This file supplies that first list of rooms.

Normally, database access is scoped to one workspace so one tenant cannot see another tenant’s rows. This is often called row-level security, meaning the database filters rows based on the current workspace. But to decide where work exists, the system needs one carefully limited read across all workspaces. This file wraps that read in a narrow helper called `owner_candidates`.

An extension gives `owner_candidates` a small query builder named `due`. Each time the job scheduler checks for work, `due` builds a fresh database query that selects only distinct `workspace_id` values from the extension’s own tables. Building it fresh matters because “due now” may depend on the current time. The helper then runs that query through `owner_tx`, the special database path that bypasses normal workspace filtering. It collects only the first column of each row, the workspace ID, and returns those IDs as a tuple.

The important safety rule is that no actual work happens during this read. The returned IDs are later used by the dispatcher to enter each workspace scope before running the job handler.

#### Function details

##### `owner_candidates`  (lines 27–40)

```
def owner_candidates(due: Callable[[], sa.Select[tuple[UUID]]]) -> WorkspaceCandidates
```

**Purpose**: This function turns a workspace-ID query builder into a callable that the dispatcher can ask, “Which workspaces have work right now?” It exists so extensions can declare pending work without directly using the privileged cross-workspace database connection.

**Data flow**: It receives `due`, a function that can build a database `SELECT` query returning workspace IDs. It wraps that builder inside an async `candidates` function. The result is not the IDs immediately, but a reusable async callable that will fetch the IDs whenever the scheduler runs it.

**Call relations**: This is the public seam for extension candidate discovery. Code that schedules jobs calls `owner_candidates` when setting up a job’s candidate source, and the returned `owner_candidates.candidates` function is what later performs the actual privileged read.


##### `owner_candidates.candidates`  (lines 35–38)

```
async def candidates() -> tuple[UUID, ...]
```

**Purpose**: This inner async function performs the actual candidate lookup. It runs the latest `due()` query through the special owner-level database transaction and returns only workspace IDs.

**Data flow**: When called, it opens `owner_tx`, a privileged database transaction that can read across workspaces. It asks `due()` to build the current query, executes it, reads the first value from each returned row, and outputs those values as a tuple of workspace UUIDs. It does not return any other row data.

**Call relations**: This function is the callable produced by `owner_candidates`. During job dispatch, it is called to name the workspaces that may need the job. Its only direct handoff is to `ufo.db.owner_tx`, which provides the controlled cross-workspace database access needed for this lookup.

*Call graph*: 1 external calls (owner_tx).


### Durable turn queue
The loop package exposes the durable queue that claims, orders, runs, recovers, and finalizes conversation turns.

### `core/src/ufo/loop/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means other parts of the project can refer to code inside `core/src/ufo/loop/` using names like `ufo.loop.something`. Think of it like a label on a drawer: the label does not do the work, but it lets the rest of the system find what is inside. Because the file is empty, it does not run setup code, expose shortcuts, or change how the loop subsystem behaves. If it were missing in environments that expect traditional Python packages, imports from this folder could fail or become less predictable.


### `core/src/ufo/loop/queue.py`

`orchestration` · `turn dispatch and execution`

This file is the bridge between “a user sent something” and “the agent actually works on it.” A turn may involve database reads, model calls, tools, a sandboxed workspace, credentials, subagents, and a transcript. That is too much to run as a simple one-off task, because the process might crash or another message might arrive for the same conversation. This file puts turns into a partitioned DBOS queue, which is a durable task system that remembers workflow progress and runs only one queued item per partition at a time. Here the partition is the conversation, so turns in the same conversation do not step on each other.

At startup, the process installs a Runtime object. That object is like the tool belt for turn execution: database client, blob storage, model registry, sandbox carrier, credential store, search, tools, skills, and so on. When the queue calls the workflow, the file binds the correct workspace, claims the turn, loads the turn and agent, opens or resumes the conversation sandbox, prepares credentials and environment variables safely, builds a TurnEngine, and lets that engine run the model/tool loop.

A key safety feature is the terminal backstop. If something fails before the engine can write a final result, this file keeps retrying until the turn is marked failed and clients waiting for completion are released.

#### Function details

##### `init_runtime`  (lines 126–130)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the process-wide Runtime object that turn execution needs. It is meant to happen once, so the worker cannot accidentally swap out its storage, model, sandbox, or credential services mid-run.

**Data flow**: A fully built Runtime goes in. The function checks whether one is already installed; if not, it stores it in the module-level runtime slot. Nothing is returned, but future turn workflows can now use that shared runtime.

**Call relations**: This is setup for everything else in the file. Later, turn_workflow reaches _execute_turn, and _execute_turn expects this runtime to already exist before it can call _run_turn.


##### `reset_runtime`  (lines 133–138)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed Runtime so tests can replace it. In normal serving, the runtime is installed once and not reset.

**Data flow**: No input is needed. The function sets the module-level runtime slot back to empty. It returns nothing, but the next execution will fail until init_runtime installs a fresh Runtime.

**Call relations**: This is mainly a test seam. It exists so tests can avoid poking at the global runtime directly while still exercising the same flow used by turn_workflow.


##### `_execute_turn`  (lines 141–157)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs one queued turn inside the correct workspace. The workspace binding matters because database access, credentials, model billing, and storage must all be scoped to the right tenant or project.

**Data flow**: It receives workspace_id and turn_id as strings from the durable workflow. It checks that the Runtime has been installed, converts the workspace id into a UUID, opens the workspace context, and passes the turn id to _run_turn. The output is the final status string returned by _run_turn.

**Call relations**: turn_workflow calls this as the body of the DBOS workflow. This function does the workspace setup first, then hands the real work to _run_turn so all later database and service calls happen under the right workspace.

*Call graph*: calls 1 internal fn (_run_turn); called by 1 (turn_workflow); 2 external calls (ws, UUID).


##### `_enqueue_handoff`  (lines 160–195)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: Queues another turn that should take over or continue work for the same conversation. It also cleans up the database if enqueueing fails, so the turn is not left looking dispatched when it is not.

**Data flow**: It receives a DBOS client, workspace id, turn id, conversation id, and workflow id. It builds queue options that place the work on the turn queue and partition it by conversation. If enqueueing succeeds, nothing else changes. If enqueueing is cancelled or fails, it clears the turn’s dispatch_enqueued_at field while the turn is still queued; on ordinary failure it also logs that the enqueue was deferred.

**Call relations**: _run_turn calls this after claiming a turn when the claim process reports a handoff. It hands the next durable unit of work to DBOS, and on failure it repairs the turn row so another dispatcher can try again later.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 198–340)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: This is the main assembly line for executing a turn. It claims the turn, prepares the agent, tools, skills, credentials, sandbox, transcript, and model, then runs the TurnEngine that performs the actual conversation work.

**Data flow**: It receives the installed Runtime and a turn id string. First it claims the turn so duplicate workers do not both run it. If the turn has been superseded, it repairs transcript state and returns “superseded.” If a handoff is needed, it enqueues that. Then it loads the turn and agent, gathers tools and hooks from extension manifests, resolves the right model, chooses main-agent or subagent behavior, opens or resumes the sandbox, mounts preloaded skills, builds a TurnEngine, and awaits engine.run(). It returns the resulting terminal status, “parked” if the engine paused for later, “superseded” if no final frame is produced, or “failed” after committing a failure backstop.

**Call relations**: _execute_turn calls this after binding the workspace. This function coordinates nearly every helper in the file: _load_turn for database state, _enqueue_handoff for follow-up work, _open_sandbox for the execution environment, and _commit_failed_terminal if setup or execution fails outside the engine’s normal failure path.

*Call graph*: calls 4 internal fn (_commit_failed_terminal, _enqueue_handoff, _load_turn, _open_sandbox); called by 1 (_execute_turn); 22 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+12 more)).


##### `_commit_failed_terminal`  (lines 343–375)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Marks a turn as failed when an error happens outside the normal engine path. This prevents clients from waiting forever for a turn that crashed before writing its own final result.

**Data flow**: It receives the hub used for notifications, the turn id, and the error that occurred. It builds a failed TerminalFrame containing the error class and a shortened error message. Then it repeatedly tries to update the turn row from queued or running to failed and publish the terminal event. If the database or publish step fails, it logs the retry, waits, and tries again with a longer delay up to a maximum.

**Call relations**: _run_turn calls this in its broad error catch. It is the last-resort safety net: once it succeeds, the hub can notify waiters that the turn ended instead of leaving them stuck.

*Call graph*: calls 1 internal fn (publish); called by 1 (_run_turn); 6 external calls (__init__, __init__, sleep, update, workspace_tx, log).


##### `turn_workflow`  (lines 379–380)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Defines the DBOS durable workflow entry for running a turn. DBOS uses this decorated function as the named workflow that the queue can schedule and replay after crashes.

**Data flow**: It receives workspace_id and turn_id as strings from DBOS. It passes them directly to _execute_turn and returns that function’s status string.

**Call relations**: The turn queue schedules this workflow by name. turn_workflow keeps the public DBOS-facing wrapper small and lets _execute_turn do the workspace binding and runtime checks.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 383–441)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, UUID | None]
```

**Purpose**: Reads the database records needed to run a turn: the turn itself, the agent’s prompt and model, and the conversation member used for memory. It turns raw database rows into the project’s typed records.

**Data flow**: It receives a turn UUID. It queries the turn table joined with the agent and conversation tables. From the row, it builds a Turn object, validates any saved context and terminal data, builds an Agent object, and returns those plus the conversation member id, which may be absent.

**Call relations**: _run_turn calls this after claim decisions and also when a turn was not claimed but needs transcript repair. The loaded records become the inputs for model selection, prompt rendering, sandbox setup, memory scope, and engine construction.

*Call graph*: called by 1 (_run_turn); 6 external calls (__init__, __init__, model_validate, model_validate, select, workspace_tx).


##### `_open_sandbox`  (lines 444–500)

```
async def _open_sandbox(carrier: Carrier, backend: str, blob: BlobStore, workspace_fs: SandboxFsCredentialMinter | None, proxy: ProxyEndpoint, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore
```

**Purpose**: Creates or resumes the sandbox where tools and commands for the conversation run. The sandbox is an isolated workspace, like a clean workshop for the agent, with only the right files, proxy token, and safe credential placeholders.

**Data flow**: It receives the sandbox carrier, backend name, blob storage, workspace filesystem credential minter if any, proxy endpoint, run-token codec, the turn, optional grant and credential stores, connector CLI declarations, and credential slots. It reads any previously stored sandbox handle for the conversation, asks the carrier to create or resume a sandbox with the correct mount and environment variables, then saves the new handle back to the conversation row if it changed. It returns the SandboxHandle needed to open a SandboxSession.

**Call relations**: _run_turn calls this before building the TurnEngine. This helper gathers environment pieces from _git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env, reads and writes the persisted handle through _stored_sandbox_handle and _persist_sandbox_handle, and asks _workspace_mount how to attach the conversation workspace.

*Call graph*: calls 9 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env, _persist_sandbox_handle, _stored_sandbox_handle, _workspace_mount, create, encode); called by 1 (_run_turn); 4 external calls (__init__, __init__, format_sandbox_handle, sandbox_handle_id).


##### `_git_config_env`  (lines 503–510)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Formats Git configuration settings as environment variables that Git understands. This lets the sandbox influence Git behavior without writing a Git config file.

**Data flow**: It receives a tuple of key/value Git settings. It creates GIT_CONFIG_COUNT plus numbered GIT_CONFIG_KEY_n and GIT_CONFIG_VALUE_n variables for each setting. It returns a dictionary ready to merge into the sandbox environment.

**Call relations**: _open_sandbox calls this while building the sandbox’s environment. It is fed both the always-needed proxy authentication setting and any Git credential header settings created by _git_credential_config.

*Call graph*: called by 1 (_open_sandbox).


##### `_git_credential_config`  (lines 513–539)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds Git header settings for credential slots that are actually filled in this workspace. It uses sentinels, which are harmless placeholder strings, rather than putting real secrets inside the sandbox.

**Data flow**: It receives the credential store, declared credential slots, and workspace id. If credentials are disabled, it returns no settings. Otherwise, it scans slots that declare Git basic-auth injection, skips slots with no stored secret, resolves the target host, warns if the host cannot be resolved, and returns Git extraheader settings containing the declared header name and sentinel value.

**Call relations**: _open_sandbox calls this before _git_config_env. The resulting settings let Git commands inside the sandbox send a placeholder header; later, the egress proxy can swap that placeholder for the real credential on approved network requests.

*Call graph*: called by 1 (_open_sandbox); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 542–575)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Exports environment variables for API-key-style providers, but only when the workspace really has the corresponding secret. Like the Git path, it exports sentinels and resolved hosts, not raw secrets.

**Data flow**: It receives the credential store, credential slots, and workspace id. If credentials are disabled, it returns an empty environment. For each slot, it checks whether the slot has injection settings and whether the secret is stored. It resolves the provider host, warns if the host choice is unavailable, then adds the configured secret environment variable with the sentinel and, when requested, a host environment variable with the resolved host. The result is a dictionary of safe environment variables for the sandbox.

**Call relations**: _open_sandbox merges this into the sandbox environment. These variables allow code inside the sandbox to behave as if credentials are present, while the proxy remains responsible for replacing sentinels with real secrets only on allowed outbound requests.

*Call graph*: called by 1 (_open_sandbox); 3 external calls (credential_host, slot_is_set, warn).


##### `_grant_cli_env`  (lines 578–619)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], turn: Turn) -> dict[str, str]
```

**Purpose**: Exports connector CLI authentication placeholders for accounts this turn is allowed to use. It avoids guessing when multiple accounts could match, because choosing the wrong account would be worse than failing clearly.

**Data flow**: It receives an optional GrantStore, connector CLI declarations, and the turn. If grants are unavailable or no CLIs are declared, it returns an empty environment. It determines the acting member, reads active grants for the workspace and agent, prefers that member’s private grant over shared grants, and sets the CLI’s environment variable to a grant sentinel when exactly one account matches. If more than one account matches, it logs the ambiguity and exports nothing for that provider.

**Call relations**: _open_sandbox calls this while preparing the sandbox environment. The returned variables let connector command-line tools inside the sandbox authenticate through the proxy using the same grant identity the rest of the system recognizes.

*Call graph*: calls 1 internal fn (active_grants); called by 1 (_open_sandbox); 2 external calls (grant_sentinel, log).


##### `_stored_sandbox_handle`  (lines 622–631)

```
async def _stored_sandbox_handle(conversation_id: UUID, workspace_id: UUID) -> str | None
```

**Purpose**: Reads the saved sandbox handle for a conversation. That handle lets a later turn or restarted process reconnect to the same sandbox instead of creating a new one unnecessarily.

**Data flow**: It receives a conversation id and workspace id. It queries the conversation row for sandbox_handle and returns the stored string or null if there is none.

**Call relations**: _open_sandbox calls this first. The value it returns becomes the possible resume id passed to the sandbox carrier, so conversation state can survive across turns and worker restarts.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (select, workspace_tx).


##### `_persist_sandbox_handle`  (lines 634–643)

```
async def _persist_sandbox_handle(conversation_id: UUID, workspace_id: UUID, handle: str) -> None
```

**Purpose**: Saves the current sandbox handle on the conversation row. This records where the conversation’s sandbox lives so future work can resume it.

**Data flow**: It receives a conversation id, workspace id, and handle string. It updates the matching conversation row’s sandbox_handle field. It returns nothing.

**Call relations**: _open_sandbox calls this after the carrier creates or resumes a sandbox, but only when the formatted handle differs from what was already stored. That keeps the database in sync without extra writes on ordinary resumes.

*Call graph*: called by 1 (_open_sandbox); 2 external calls (update, workspace_tx).


##### `_workspace_mount`  (lines 646–699)

```
async def _workspace_mount(blob: BlobStore, workspace_fs: SandboxFsCredentialMinter | None, conversation_id: UUID, run: RunToken, fresh_sandbox: bool) -> MountSpec
```

**Purpose**: Builds the mount description for the sandbox’s workspace files. It gives the sandbox access only to the conversation’s workspace folder, not to the transcript or other storage nearby.

**Data flow**: It receives blob storage, an optional S3 workspace credential minter, conversation id, run token, and a flag saying whether this is a fresh sandbox. For filesystem storage, it creates the workspace directory on the host, adjusts ownership when running as root, and returns a filesystem MountSpec. For S3 storage, it ensures a workspace marker for fresh sandboxes, issues a short-lived scoped credential token for that conversation’s workspace prefix, and returns an S3 MountSpec. If the blob backend is unsupported, it raises an error.

**Call relations**: _open_sandbox calls this while constructing the SandboxSpec. The returned MountSpec tells the sandbox carrier exactly what storage to attach before the TurnEngine starts running tools inside the sandbox.

*Call graph*: calls 1 internal fn (issue); called by 1 (_open_sandbox); 5 external calls (__init__, to_thread, geteuid, ensure_workspace_marker, workspace_key_prefix).


### Process presence and recovery
Runtime instance tracking keeps serve processes visible and runs background recovery and cancellation cleanup across the fleet.

### `core/src/ufo/runtime_instance.py`

`orchestration` · `startup and background runtime`

This file is the fleet’s “pulse and cleanup crew.” Each serve process records a simple row in the database saying “I am here,” then keeps that row fresh with a heartbeat. Other processes use those rows to tell whether a process is still alive. If a process disappears, its heartbeat goes stale, which is the signal that any pending DBOS workflows assigned to it may be stranded and should be recovered.

There are three main background loops. `Heartbeat` updates this process’s own liveness row every few seconds and removes it during graceful shutdown. `ExecutorRecovery` looks for pending workflows whose executor no longer has a fresh heartbeat, then asks DBOS to recover them so another live process can continue the work. This avoids both lost work and dangerous duplicate execution: live executors are deliberately skipped.

`CancelReconciler` solves a different cleanup problem. Cancelling one turn only directly cancels that turn. Child or grandchild turns may still be running. This reconciler periodically searches the turn tree for any still-live turn that has a cancelled ancestor, then cancels it too. Like a nightly building sweep that turns off lights left on in side rooms, it makes the system converge to the correct state even after crashes or missed events.

#### Function details

##### `record_fleet_seat`  (lines 38–53)

```
async def record_fleet_seat(instance_id: UUID) -> None
```

**Purpose**: Creates this process’s liveness record in the database before DBOS starts running work. This makes the process visible to the fleet immediately, so its own newly assigned work is not mistaken for abandoned work.

**Data flow**: It receives an instance UUID. It opens an owner-level database transaction, inserts a `runtime_instance` row with no workspace, sets the heartbeat and timestamps to the current database time, then logs that the fleet seat was recorded. It does not return a value; the lasting result is the new database row.

**Call relations**: This is the first step in making a serve process known to the shared fleet. It uses the database transaction helper to write the row and the logging helper to report success. Later, `Heartbeat.beat` keeps this row fresh, and `ExecutorRecovery._live_executors` reads rows like it to decide which executors are alive.

*Call graph*: 3 external calls (insert, owner_tx, log).


##### `Heartbeat.run`  (lines 66–76)

```
async def run(self) -> None
```

**Purpose**: Runs the endless heartbeat loop for one process. It repeatedly refreshes the process’s liveness row so peers know this process is still alive.

**Data flow**: It reads the `instance_id` stored on the `Heartbeat` object. On each loop, it calls `Heartbeat.beat` to update the database. If that database update fails with a SQLAlchemy database error, it logs the failure instead of stopping. Then it sleeps for the configured heartbeat interval and tries again.

**Call relations**: This is the driver for `Heartbeat.beat`. A serve process starts it as a background task or thread so the liveness signal continues independently of normal request work. Its main handoff is to `Heartbeat.beat`, which performs the actual database update.

*Call graph*: calls 1 internal fn (beat); 2 external calls (sleep, log).


##### `Heartbeat.beat`  (lines 78–88)

```
async def beat(self) -> None
```

**Purpose**: Writes one fresh heartbeat timestamp for this process. This is the small database update that says, in effect, “I am still alive right now.”

**Data flow**: It uses the object’s `instance_id` to find the matching `runtime_instance` row. Inside a database transaction, it updates `heartbeat_at` and `updated_at` to the current database time. It returns nothing; the important output is the refreshed row.

**Call relations**: `Heartbeat.run` calls this on every heartbeat tick. `ExecutorRecovery._live_executors` later reads these timestamps to decide which executors are fresh enough to leave alone.

*Call graph*: called by 1 (run); 2 external calls (update, owner_tx).


##### `Heartbeat.retire`  (lines 90–96)

```
async def retire(self) -> None
```

**Purpose**: Removes this process’s liveness row during graceful shutdown. This lets other processes see immediately that the seat is gone instead of waiting for the heartbeat to become stale.

**Data flow**: It reads the `instance_id` from the `Heartbeat` object. In a database transaction, it deletes the matching row from `runtime_instance`. It returns nothing; the database no longer contains this process’s seat.

**Call relations**: The serve shutdown path calls this through `ufo.serve._stop_executor`. It is the clean ending counterpart to `record_fleet_seat` and `Heartbeat.beat`: startup creates the row, heartbeat refreshes it, and shutdown removes it.

*Call graph*: called by 1 (_stop_executor); 2 external calls (delete, owner_tx).


##### `ExecutorRecovery.run`  (lines 116–122)

```
async def run(self) -> None
```

**Purpose**: Runs the endless recovery loop that looks for work left behind by dead processes. It keeps the system from leaving pending workflows stuck forever after a crash.

**Data flow**: It reads its configured interval. On each loop, it sleeps, then calls `ExecutorRecovery.sweep`. If the sweep hits a database error or DBOS error, it logs the failure and keeps looping. It returns nothing because it is meant to run for the lifetime of the process.

**Call relations**: This is the scheduler for `ExecutorRecovery.sweep`. Every serve process can run it, so any surviving process can notice and recover work from a crashed peer.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `ExecutorRecovery.sweep`  (lines 124–132)

```
async def sweep(self) -> None
```

**Purpose**: Performs one recovery pass. It finds executor IDs that still own pending workflows but no longer have a fresh heartbeat, then asks DBOS to recover those workflows.

**Data flow**: It asks `_pending_executors` for executors with pending DBOS workflows and `_live_executors` for executors that still look alive. It subtracts the live set from the pending set. For each remaining executor, it runs DBOS recovery in a worker thread and logs how many workflows were recovered. It returns nothing; the effect is that stranded workflows are re-dispatched by DBOS.

**Call relations**: `ExecutorRecovery.run` calls this every interval. This function coordinates the two facts it needs: pending work from DBOS and live process rows from the database. It then hands stranded executor IDs to DBOS’s recovery mechanism.

*Call graph*: calls 2 internal fn (_live_executors, _pending_executors); called by 1 (run); 2 external calls (to_thread, log).


##### `ExecutorRecovery._pending_executors`  (lines 134–146)

```
async def _pending_executors(self) -> set[str]
```

**Purpose**: Finds executor IDs currently attached to pending DBOS workflows. These are candidates for recovery, but only if their executor is not still alive.

**Data flow**: It asks DBOS, in a worker thread, for up to the configured scan limit of workflows with status `PENDING`. It avoids loading workflow inputs and outputs because only status and executor identity are needed. If the result reaches the limit, it logs that the scan may be capped. It returns a set of executor ID strings, ignoring pending workflows that do not report an executor ID.

**Call relations**: `ExecutorRecovery.sweep` calls this as one half of its comparison. The returned set is later reduced by `_live_executors`, so live processes are not accidentally recovered.

*Call graph*: called by 1 (sweep); 2 external calls (to_thread, log).


##### `ExecutorRecovery._live_executors`  (lines 148–158)

```
async def _live_executors(self) -> set[str]
```

**Purpose**: Finds executor IDs whose heartbeat is still fresh. These executors are treated as alive and must not have their workflows recovered by another process.

**Data flow**: It computes a cutoff time by subtracting the stale-after setting from the current UTC time. It queries the `runtime_instance` table for rows whose `heartbeat_at` is at or after that cutoff. It returns those row IDs as strings.

**Call relations**: `ExecutorRecovery.sweep` calls this to protect live executors. Its output is subtracted from the pending-executor set, which is the key safety check that prevents starting a second copy of work that is already running.

*Call graph*: called by 1 (sweep); 4 external calls (now, timedelta, select, owner_tx).


##### `CancelReconciler.run`  (lines 182–188)

```
async def run(self) -> None
```

**Purpose**: Runs the endless cancellation reconciliation loop. It periodically checks whether any still-running turn sits underneath a cancelled parent or ancestor, and if so, makes cancellation catch up.

**Data flow**: It reads its configured interval. On each loop, it sleeps, then calls `CancelReconciler.sweep`. If a database or DBOS error happens, it logs the failure and continues. It does not return during normal operation.

**Call relations**: This is the scheduler for `CancelReconciler.sweep`. Every serve process can run it, so cancellation cleanup does not depend on the exact process that first issued the cancel.

*Call graph*: calls 1 internal fn (sweep); 2 external calls (sleep, log).


##### `CancelReconciler.sweep`  (lines 190–197)

```
async def sweep(self) -> None
```

**Purpose**: Performs one pass of cancellation cleanup. It finds live turns that should be cancelled because some ancestor turn was cancelled, then cancels each one using the normal cancellation path.

**Data flow**: It opens a database transaction and runs `_orphans_query` to get live descendant turns with cancelled ancestors, including their workspace IDs. For each result, it enters that workspace context, calls `cancel_one_turn` with the DBOS client and turn ID, and logs the turn if cancellation actually happened. It returns nothing; the effects are updated turn state and workflow cancellation.

**Call relations**: `CancelReconciler.run` calls this every interval. This function relies on `_orphans_query` to find the work, uses `ws` to make sure cancellation runs in the correct workspace, and delegates the actual turn cancellation to `cancel_one_turn`.

*Call graph*: calls 1 internal fn (_orphans_query); called by 1 (run); 4 external calls (cancel_one_turn, owner_tx, log, ws).


##### `CancelReconciler._orphans_query`  (lines 199–234)

```
def _orphans_query(self) -> sa.Select
```

**Purpose**: Builds the database query that finds every still-live turn with a cancelled ancestor. This is what lets cancellation travel down an entire parent-child tree, not just to direct children.

**Data flow**: It starts from turns whose status is not terminal, then uses a recursive database query to walk upward through each turn’s `parent_turn_id` chain. The walk stops once it reaches a cancelled ancestor. The query outputs distinct live turn IDs and their workspace IDs for turns that should now be cancelled.

**Call relations**: `CancelReconciler.sweep` calls this before doing any cancellation. This function only constructs the search; `sweep` executes it and then hands each matching turn to `cancel_one_turn`.

*Call graph*: called by 1 (sweep); 1 external calls (select).

## 📊 State Registers Touched

- `reg-workspace-tenant-record` — The customer workspace record that all users, conversations, data, tools, and billing are kept under.
- `reg-inbound-message-dedup` — The durable inbox and duplicate-detection state for messages arriving from external surfaces.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-durable-work-queue` — The shared queue of conversation and job work waiting to be claimed, retried, resumed, or completed by workers.
- `reg-runtime-presence` — The live roll-call of server and worker processes used to recover abandoned work safely.
- `reg-cancellation-state` — The shared stop signal and saved cancellation status for turns, child turns, and paused work.
- `reg-scheduled-task-state` — The saved clock-based tasks, waits, pauses, last-run markers, and expiration times used to wake work later.
- `reg-live-stream-hub` — The live stream of turn updates that clients can watch and replay after reconnecting.
- `reg-observability-trace-context` — The trace, metric, and log context that follows requests and turns so operators can understand what happened.
