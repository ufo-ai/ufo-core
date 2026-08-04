# Turn Admission, Queueing, Claiming, and Recovery  `stage-8`

This stage is the handoff point between “a user or schedule wants something done” and “an agent turn is safely ready to run.” A turn is one unit of conversation work, like one job ticket in a workshop. The admission file is the front desk. It receives new messages, resumed work, or folded-together conversation events, then checks whether the request is allowed: enough seats, spending limits, no duplicate delivery, and the right agent attached. Once approved, it passes the turn into the queue.

The queue file is the job board and safety net. It records the turn durably so it is not lost if the process crashes. It attaches the correct workspace, conversation, parent turn, sandbox, and credentials, so the runner has the right environment and permissions. It also lets a worker claim a turn for execution, finds turns abandoned by dead workers, and puts them back on track. If setup fails before the agent really starts, it still writes a final success or failure result, so every admitted turn reaches a clear ending.

## Files in this stage

### Turn Admission and Execution Queue
Admits incoming conversation work through shared policy checks, then durably queues, claims, recovers, and finalizes the resulting agent turn.

### `core/src/ufo/surfaces/admission.py`

`domain_logic` · `request handling and scheduled task admission`

A “turn” is one unit of conversation work that the agent will answer. This file decides whether a new inbound message should create a new turn, join an already-live turn, resume a paused turn, wait because spending is capped, or be cancelled with a clear reason. It is like a receptionist for the conversation engine: every message must check in here before it can reach the worker queue.

The central rule is that a conversation stays tied to its chosen agent. Member messages do not choose an agent, and internal callers may only assert the agent already bound to the conversation. The file also protects against duplicate deliveries by using idempotency keys, meaning the same delivered message can be recognized and attached to the same turn instead of creating another one.

When a conversation already has a live turn, later messages are usually stored in an inbound-message queue for that turn rather than starting a separate run. This lets one agent reply cover all messages that arrived during the run. The file also checks seat eligibility and spending limits before queueing work. If the turn is allowed, it is inserted into the database and possibly placed on the DBOS durable workflow queue. If the surface needs durable writeback, a writeback row is created at the same time so replies are not lost.

#### Function details

##### `Admission.admit_member`  (lines 92–117)

```
async def admit_member(self, workspace_id: UUID, conversation_id: UUID, body: str, speaker_member_id: UUID | None, idempotency_key: str | None=None, context: TurnContext | None=None, intent: ToolInten
```

**Purpose**: Accepts a message spoken by a workspace member and asks the shared admission path to decide what should happen to it. It also validates prepared tool intents so the recorded message body exactly matches the intent envelope.

**Data flow**: It receives a workspace, conversation, message body, optional speaker, optional duplicate-delivery key, optional context, and optional intent. It checks that an intent has a real speaker and that the message body matches the serialized intent. Then it passes the message into the main admission routine with a marker saying this member message may consume a pending one-time pause, and it returns the resulting admitted turn information.

**Call relations**: Surfaces use this member-facing entry point when a person sends a message. After its small validation step, it hands everything to Admission._admit, which performs the database locking, duplicate checks, seat checks, spending decision, and queueing.

*Call graph*: calls 1 internal fn (_admit); 2 external calls (__init__, model_dump_json).


##### `Admission.invoke`  (lines 119–139)

```
async def invoke(self, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, body: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: Starts a turn from inside the system, such as from a job or extension, while requiring the caller to name the agent it believes owns the conversation. This prevents internal work from silently switching a conversation to a different agent.

**Data flow**: It receives the workspace, conversation, asserted agent, message, optional idempotency key, and optional context. It forwards these to the shared admission routine without a speaking member and without permission to consume a member pause. It returns only the admitted turn id.

**Call relations**: Internal callers use this simpler method instead of member admission. It delegates to Admission._admit, and the shared routine verifies the asserted agent against the conversation’s stored binding before admitting anything.

*Call graph*: calls 1 internal fn (_admit).


##### `Admission.invoke_scheduled`  (lines 141–182)

```
async def invoke_scheduled(self, workspace_id: UUID, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: Turns a claimed scheduled task into an inbound conversation turn. It formats recurring scheduled work with timing information, refuses invalid schedule states, and returns no turn if another worker has already superseded the claimed task.

**Data flow**: It receives a workspace, a scheduled task, and an optional runtime instruction. It checks that the task is claimed, rejects runtime instructions for one-time pause resumptions, builds the message text, and creates a stable firing key for duplicate protection. It then calls the shared admission routine and returns the admitted turn id, or returns null if the scheduled invocation was no longer valid.

**Call relations**: The scheduler calls this after claiming a task. This method prepares schedule-specific input, then relies on Admission._admit to link one-time pauses, verify the claim under lock, decide whether to queue or park the turn, and handle duplicate firings.

*Call graph*: calls 1 internal fn (_admit); 1 external calls (firing_key).


##### `Admission._admit`  (lines 184–731)

```
async def _admit(self, workspace_id: UUID, conversation_id: UUID, asserted_agent_id: UUID | None, body: str, speaker_member_id: UUID | None, idempotency_key: str | None, context: TurnContext | None, p
```

**Purpose**: This is the main admission decision-maker. It safely decides, under a database lock, whether an inbound message creates a new turn, joins a live turn, takes over a queued pause-resume turn, parks because of spending limits, cancels because of seat or spend refusal, or gets enqueued for execution.

**Data flow**: It receives all facts about the attempted admission: workspace, conversation, optional asserted agent, message body, speaker, idempotency key, context, pause behavior, scheduled task, member on whose behalf the task runs, and optional intent. It locks the conversation row, reads conversation and task state, checks duplicate keys, checks live turns and queued inbound messages, applies seat rules and spending rules, writes or updates turn, inbound-message, scheduled-task, and writeback rows, then exits the transaction. After the database state is safely committed, it enqueues the turn if it is ready to run and returns an Admitted result saying which turn was accepted and whether a new run was opened.

**Call relations**: Admission.admit_member, Admission.invoke, and Admission.invoke_scheduled all funnel into this function so no caller can bypass the same boundary checks. When it decides a queued turn should run now, or a parked turn has been resumed by a folded message, it hands the final queueing step to Admission._enqueue.

*Call graph*: calls 1 internal fn (_enqueue); called by 3 (admit_member, invoke, invoke_scheduled); 16 external calls (__init__, __init__, __init__, __init__, model_dump, model_validate, delete, exists, insert, select (+6 more)).


##### `Admission._enqueue`  (lines 733–773)

```
async def _enqueue(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID, workflow_id: str | None=None) -> None
```

**Purpose**: Places an admitted queued turn onto the DBOS workflow queue, which is the durable work queue used by workers. It also repairs the database marker if enqueueing is cancelled or fails, so another attempt can try again later.

**Data flow**: It receives the workspace, conversation, turn id, and optionally a workflow id. It builds queue options including the queue name, workflow name, workflow id, partition key, and app version, then asks DBOS to enqueue the workflow. If enqueueing is cancelled or errors, it clears the turn’s dispatch timestamp in the database; on ordinary errors it also logs that enqueueing was deferred.

**Call relations**: Admission._admit calls this only after it has committed the database changes that say the turn exists and is queued. This split matters because the durable database record is the source of truth, while enqueueing is the handoff to background workers.

*Call graph*: called by 1 (_admit); 3 external calls (update, workspace_tx, log).


##### `AdmissionInvoker.invoke`  (lines 784–799)

```
async def invoke(self, conversation_id: UUID, agent_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None) -> UUID
```

**Purpose**: Provides a workspace-bound internal invocation helper. Callers using it do not need to repeat the workspace id, and they cannot accidentally use the member-admission path that consumes pending member pauses.

**Data flow**: It receives a conversation, agent, message, optional idempotency key, and optional context. It adds the stored workspace id from the AdmissionInvoker object and forwards the request to Admission.invoke. The result is the admitted turn id from the shared admission system.

**Call relations**: Jobs and extension workflows receive this limited helper as their capability. It tells Admission.invoke to admit internal work, which then flows into Admission._admit for the real checks and queue decision.


##### `AdmissionInvoker.invoke_scheduled`  (lines 801–804)

```
async def invoke_scheduled(self, task: ScheduledTask, runtime_instruction: str | None=None) -> UUID | None
```

**Purpose**: Provides a workspace-bound helper for invoking scheduled tasks. It keeps scheduled internal work on the internal path rather than the member-message path.

**Data flow**: It receives a scheduled task and optional runtime instruction. It adds the stored workspace id and forwards both to Admission.invoke_scheduled. The result is either the admitted turn id or null if the scheduled firing was superseded.

**Call relations**: Schedulers or worker code can use this helper without holding the full Admission object directly. It passes the request to Admission.invoke_scheduled, which prepares the scheduled message and then relies on the shared admission routine.


##### `MemberAdmission.admit`  (lines 815–833)

```
async def admit(self, conversation_id: UUID, message: str, idempotency_key: str | None=None, context: TurnContext | None=None, *, speaker_member_id: UUID | None, intent: ToolIntent | None=None) -> Adm
```

**Purpose**: Provides a workspace-bound member-message helper for surfaces. It makes sure surface-delivered messages use the member admission route, including the behavior that can consume a pending one-time pause.

**Data flow**: It receives a conversation, message, optional idempotency key, optional context, required speaker member id, and optional intent. It adds the stored workspace id and forwards the request to Admission.admit_member. It returns the Admitted result, which tells the surface which turn accepted the message and whether a new run started.

**Call relations**: User-facing surfaces receive this limited helper instead of the full admission object. It routes their messages to Admission.admit_member, which validates member-specific intent details and then hands the decision to Admission._admit.


### `core/src/ufo/loop/queue.py`

`orchestration` · `turn workflow execution`

This file is the traffic controller for agent turns. A turn is a single piece of work in a conversation, such as answering a user, using tools, or delegating to a subagent. The file connects the database, the durable DBOS workflow system, the sandbox where tools run, credentials, model selection, transcript storage, and live status updates.

The big idea is reliability. A turn is put on a partitioned queue, keyed by conversation, so turns from the same conversation do not step on each other. When the workflow starts, it binds all work to the correct workspace, claims the turn, loads its agent and audience, prepares tools and skills, opens or reuses a sandbox, then builds a TurnEngine to do the actual model-and-tool loop.

It also protects secrets. The sandbox receives signed tokens and harmless “sentinel” values instead of real credentials. A proxy later swaps those sentinels for real secrets when allowed. This is like giving a valet ticket instead of the car keys.

If anything goes wrong before the engine can write a final status, this file writes a failed terminal message itself and publishes it to waiting clients. Without this file, turns would not be safely serialized, recovered, scoped to workspaces, or reliably finished.

#### Function details

##### `init_runtime`  (lines 115–119)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the process-wide runtime object that turn execution needs. This runtime is the bundle of shared services, such as the database workflow client, model registry, sandbox provider, blob store, credential store, and tool registries.

**Data flow**: It receives a Runtime object → checks whether one is already installed → stores it in the module-level runtime slot. If a runtime already exists, it raises an error instead of silently replacing it.

**Call relations**: This is called during server setup before any turn workflow runs. Later, _execute_turn reads this installed runtime so it can start real work without rebuilding all shared services for every turn.


##### `reset_runtime`  (lines 122–127)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed runtime so another one can be installed. This is mainly a testing seam, because production setup is expected to initialize the runtime once and keep it.

**Data flow**: It takes no input → sets the module-level runtime slot back to empty → returns nothing. The only changed state is the stored runtime reference.

**Call relations**: Tests can call this before init_runtime to swap in a fake or temporary runtime. Normal turn execution depends on the runtime being present, so this function is not part of the usual production turn path.


##### `_execute_turn`  (lines 130–164)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Starts one turn inside the correct workspace and agent scope. It is the outer safety wrapper around the real turn runner.

**Data flow**: It receives workspace and turn IDs as strings → converts them to UUIDs → enters the workspace context → reads the turn’s agent ID from the database → enters that agent scope → calls _run_turn. If setup or running fails, it commits a failed terminal update so clients are not left waiting forever.

**Call relations**: turn_workflow calls this when DBOS starts a queued workflow. It hands the actual work to _run_turn, but if anything escapes that lower layer, it calls _commit_failed_terminal as the final backstop.

*Call graph*: calls 2 internal fn (_commit_failed_terminal, _run_turn); called by 1 (turn_workflow); 5 external calls (select, agent, workspace_tx, ws, UUID).


##### `_enqueue_handoff`  (lines 167–202)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: Queues another turn that should take over after the current turn claims work. This supports safe handoff without letting two turns for the same conversation run at once.

**Data flow**: It receives the DBOS client, workspace ID, turn ID, conversation ID, and workflow ID → builds queue options that partition by conversation → asks DBOS to enqueue the workflow. If enqueueing is cancelled or fails, it clears the turn’s dispatch marker in the database so the turn can be retried or picked up later.

**Call relations**: _run_turn calls this after claiming a turn when the claim operation reports a handoff. It talks to DBOS to schedule the next workflow and uses the database to undo the queued marker if scheduling did not succeed.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 205–385)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds everything needed to execute a turn and then runs the TurnEngine. This is the main assembly line for a turn: claim it, load it, prepare tools and prompts, open the sandbox, and start the model loop.

**Data flow**: It receives the shared Runtime and a turn ID → claims the turn so duplicate workers do not run it → optionally enqueues a handoff → loads the turn, agent, and audience → builds subagent access, tools, hooks, skills, prompts, model client, credentials, grants, sandbox session, and compaction support → creates TurnEngine → runs either the normal flow or intent-admission flow → returns a status such as completed, failed, parked, or superseded. It may also mount preloaded skills into the sandbox and may write a failed terminal if an unexpected error occurs.

**Call relations**: _execute_turn calls this after binding the workspace and agent. This function coordinates many subsystems, then delegates the actual conversation/tool loop to TurnEngine. It also calls _load_turn, _open_sandbox, _enqueue_handoff, and _commit_failed_terminal at the right moments.

*Call graph*: calls 4 internal fn (_commit_failed_terminal, _enqueue_handoff, _load_turn, _open_sandbox); called by 1 (_execute_turn); 24 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+14 more)).


##### `_run_turn.subagents_for`  (lines 233–237)

```
def subagents_for(acting_member_id: UUID | None) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates a member-specific view of subagent abilities. It lets a turn ask, “if this member is acting, what subagents may they spawn?”

**Data flow**: It receives an optional acting member ID → asks the Subagents object to authorize that member → returns both the spawn function and the authorized Subagents view. It does not change the database itself.

**Call relations**: _run_turn defines this helper while building the TurnEngine. The engine can later use it when tool calls or delegated work need member-aware subagent permissions.


##### `_commit_failed_terminal`  (lines 388–446)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Writes and publishes a final failed status when a turn breaks outside the normal engine failure path. Its job is to make sure callers waiting for the turn always get an ending.

**Data flow**: It receives the hub, turn ID, and exception → creates a terminal failure frame containing the error class and a shortened message → repeatedly tries to update the turn row from queued/running to failed → emits a metric and logs the stack only if that update actually changed the turn → publishes the terminal frame to the hub → returns. If the database or publishing path fails, it waits and retries with increasing delay.

**Call relations**: _execute_turn and _run_turn both call this as a safety net. The TurnEngine may already have recorded its own failure; in that case this update will not match a live row, which prevents double-counting and duplicate error logs.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 10 external calls (__init__, __init__, sleep, update, workspace_tx, emit_metric, formatted_stack, log, log_error, turn_profile).


##### `turn_workflow`  (lines 450–451)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Defines the DBOS durable workflow entry for a turn. DBOS is the workflow system that can replay or resume work after crashes.

**Data flow**: It receives workspace and turn IDs as strings from the queued workflow → passes them to _execute_turn → returns the status string produced by that execution.

**Call relations**: DBOS invokes this when a queued turn workflow is ready to run. It is intentionally thin: it hands off immediately to _execute_turn, which performs workspace binding, safety wrapping, and the real orchestration.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 454–518)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Reads the database records needed to run a turn and turns them into in-memory objects. It also derives the audience, meaning who the agent is speaking or acting for.

**Data flow**: It receives a turn UUID → queries the turn, its agent, and its conversation in one database read → builds a Turn object, an Agent object, and an Audience object → returns those three. JSON-like stored fields such as context and terminal are validated into structured objects when present.

**Call relations**: _run_turn calls this after a turn is claimed, and also when a turn was not claimed but may need transcript repair. The returned objects become the base inputs for prompts, model selection, tools, sandbox setup, and subagent behavior.

*Call graph*: called by 1 (_run_turn); 7 external calls (__init__, __init__, model_validate, model_validate, select, parse_audience, workspace_tx).


##### `_open_sandbox`  (lines 521–571)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens or reuses the sandbox container where the turn’s tool and CLI work will run. It prepares safe environment variables so the sandbox can identify the turn and use approved credentials without receiving raw secrets.

**Data flow**: It receives the sandbox provider, token codec, turn, optional grants, connector CLI declarations, optional credential store, and credential slots → creates a signed run token for the turn → builds environment variables for conversation identity, Git proxy configuration, connector CLI grants, and keyed provider credentials → asks ConversationSandbox.open to open the sandbox for the owning conversation → returns a SandboxHandle.

**Call relations**: _run_turn calls this before creating the SandboxSession and TurnEngine. It relies on _git_config_env, _git_credential_config, _grant_cli_env, and _keyed_provider_env to prepare the sandbox environment.

*Call graph*: calls 6 internal fn (_git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env, open, encode); called by 1 (_run_turn); 1 external calls (__init__).


##### `_git_config_env`  (lines 574–581)

```
def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]
```

**Purpose**: Converts Git configuration settings into environment variables that Git understands. This lets the sandbox influence Git behavior without writing a Git config file.

**Data flow**: It receives a tuple of Git key/value settings → creates GIT_CONFIG_COUNT and numbered GIT_CONFIG_KEY_N / GIT_CONFIG_VALUE_N variables → returns them as a dictionary.

**Call relations**: _open_sandbox calls this while preparing the sandbox environment. The output lets Git send proxy authentication and any credential headers correctly when commands like clone or push run inside the sandbox.

*Call graph*: called by 1 (_open_sandbox).


##### `_git_credential_config`  (lines 584–619)

```
async def _git_credential_config(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds Git HTTP header settings for credential slots that are actually filled. It exports only sentinel values, not real secrets.

**Data flow**: It receives an optional credential store, credential slot declarations, and workspace ID → skips all work if credentials are unavailable → for each Git-capable slot, checks whether the workspace has a stored value → resolves the allowed host → adds a Git extraheader setting containing the declared header and sentinel. If a slot check or host lookup fails, it logs a warning and skips that slot.

**Call relations**: _open_sandbox calls this before _git_config_env. Its settings are folded into Git’s environment so Git traffic can be recognized and authorized by the proxy without exposing the secret inside the sandbox.

*Call graph*: called by 1 (_open_sandbox); 3 external calls (credential_host, slot_is_set, warn).


##### `_keyed_provider_env`  (lines 622–664)

```
async def _keyed_provider_env(credentials: CredentialStore | None, slots: tuple[CredentialSlot, ...], workspace_id: UUID) -> dict[str, str]
```

**Purpose**: Creates environment variables for external providers that need API keys or host choices. It gives the sandbox safe sentinel values and resolved host names, never the real stored keys.

**Data flow**: It receives an optional credential store, credential slot declarations, and workspace ID → skips work if credentials are unavailable → checks each slot to see whether it is filled → resolves the selected host → writes the provider’s declared environment variable to the sentinel and, when needed, writes a host environment variable to the resolved host. Missing or invalid credentials are warned about and skipped.

**Call relations**: _open_sandbox calls this while building the sandbox’s startup environment. The TurnEngine’s tools or agent-written code can then see normal-looking environment variables, while the proxy keeps control of real credential use.

*Call graph*: called by 1 (_open_sandbox); 3 external calls (credential_host, slot_is_set, warn).


##### `SandboxAuthorizer.authorize`  (lines 675–687)

```
async def authorize(self, acting_member_id: UUID | None) -> SandboxSession
```

**Purpose**: Re-authorizes an existing sandbox session for a specific acting member. This is needed when different users or subagents may act through the same sandbox but should receive different permissions.

**Data flow**: It receives an optional acting member ID → creates a new signed run token naming the workspace, turn, and acting member → asks _grant_cli_env for the connector CLI variables allowed for that member → calls the sandbox session’s authorize method with the new token, the CLI environment names to replace, and the new grant variables → returns the re-authorized SandboxSession.

**Call relations**: _run_turn gives this method to TurnEngine as the sandbox authorization callback for normal turns. When the engine needs sandbox access on behalf of a particular member, this method refreshes the session’s token and CLI grant environment.

*Call graph*: calls 1 internal fn (_grant_cli_env); 1 external calls (__init__).


##### `_grant_cli_env`  (lines 690–729)

```
async def _grant_cli_env(grants: GrantStore | None, clis: Mapping[str, CliCredential], acting_member_id: UUID | None, turn_id: UUID) -> dict[str, str]
```

**Purpose**: Builds environment variables that let command-line connector tools authenticate through approved grants. A grant is permission for a member or audience to use a connected account.

**Data flow**: It receives an optional GrantStore, connector CLI declarations, optional acting member ID, and turn ID → returns an empty environment if grants or CLIs are absent → loads active grants → for each provider, prefers a private grant owned by the acting member, otherwise uses shared grants → if exactly one account is available, sets the CLI’s environment variable to a sentinel for that account. If multiple accounts would be ambiguous, it logs the ambiguity and exports nothing for that provider.

**Call relations**: _open_sandbox calls this for the initial sandbox environment, and SandboxAuthorizer.authorize calls it again when a member-specific sandbox session is needed. The result lets CLI tools authenticate through the proxy without choosing the wrong account silently.

*Call graph*: calls 1 internal fn (active_grants); called by 2 (authorize, _open_sandbox); 2 external calls (grant_sentinel, log).

## 📊 State Registers Touched

- `reg-workspace-tenant-state` — The saved customer workspace boundary, including its owners, admins, limits, main agent, and tenant separation rules.
- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-inbound-message-state` — The durable inbox of incoming messages and surface events waiting to be admitted into a conversation turn.
- `reg-turn-run-state` — The durable job ticket for each agent turn, including admission source, queue status, claim owner, parent turn, and final result.
- `reg-cancellation-state` — The shared stop signal and cancellation record used to safely halt turns, child turns, jobs, and cleanup work.
- `reg-runtime-fleet-state` — The live fleet heartbeat table that says which runtime processes are alive and what stranded work they may own.
- `reg-usage-accounting-ledger` — The spending ledger that records model usage, egress usage, prices, caps, billing exports, and payment-related state.
- `reg-credential-secret-store` — The encrypted store of workspace and connector secrets, plus the requests that say which secrets a tool or proxy may reveal.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-subagent-workflow-state` — The shared parent-child workflow state used when an agent delegates work to helper agents and waits for or cancels them.
- `reg-scheduled-task-state` — The durable timers and recurring jobs that remember what should run later, whether it is paused, expired, claimed, or rescheduled.
- `reg-observability-trace-state` — The shared logging, metrics, trace IDs, trace parents, and redaction context used to follow work across processes without leaking secrets.
- `reg-member-seat-state` — The durable workspace membership and seat-assignment state used to decide who belongs, who is an admin, and whether a member may admit or run work.
- `reg-user-question-state` — The pending human-question/answer state created when an agent asks the user for information and later resumed when the surface delivers a reply.
- `reg-turn-execution-budget-state` — The per-turn live execution limits and counters for context size, tokens, reasoning, tool iterations, cost checks, and stop conditions that gate the model loop before final ledger recording.
- `reg-page-alert-subscription-state` — The saved routing/subscription state that decides which conversations or agents should be alerted when synced source pages change.
