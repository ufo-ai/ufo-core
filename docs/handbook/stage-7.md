# Durable turn claiming, recovery, and engine loop  `stage-7`

This stage is the system’s main work loop for an agent turn, which is one unit of conversation work. Its job is to take work that was already admitted, claim it so only one worker runs it, and then finish it safely even if the process crashes halfway through.

The center is core/src/ufo/loop/engine.py. It runs the turn step by step: load saved state, build the context for the model, call the model, run any requested tools, add new messages, track cost, and save the final answer. It is careful not to repeat expensive model calls or tools that may have real-world effects after recovery.

core/src/ufo/loop/queue.py is the dispatcher. It pulls a queued turn from the database, prepares what the engine needs, runs it, stores the outcome, and wakes clients or parent turns waiting for the result.

core/src/ufo/durability.py is the packing and unpacking layer for saved Python objects, helping old stored data still load after code changes.

The heartbeat and cleanup sub-stage keeps this loop healthy by detecting dead workers, freeing abandoned turns, and passing cancellations to child turns.

## Sub-stages

- [Process heartbeats, abandoned work cleanup, and cancellation propagation](stage-7.1.md) `stage-7.1` — 1 files

## Files in this stage

### Durable turn execution
The stage claims queued turn work, uses durable serialization for recoverable state, and runs the engine loop that safely completes the turn.

### `core/src/ufo/loop/queue.py`

`orchestration` · `turn queue processing`

A turn is like a job ticket for an AI agent: it names the workspace, conversation, agent, message, tools, sandbox, model, and permissions involved. This file is the dispatcher and workshop for those tickets. It uses DBOS, a workflow system that can replay work after crashes, so a turn can survive process restarts without being lost or run out of order. The queue is partitioned by conversation, so one conversation’s turns run one at a time while other conversations can keep moving.

The main path starts at `turn_workflow`, which calls `_execute_turn`. That sets the workspace context, applies any extension-provided agent setup once for that workspace, opens tracing, and then calls `_run_turn`. `_run_turn` is the big assembler: it claims the turn, loads its database record, chooses tools, loads extension hooks and skills, opens the sandbox container, decides billing key use, creates the `TurnEngine`, and lets the engine run the model and tools.

The file also contains safety nets. If setup fails before the engine can write a final result, `_commit_failed_terminal` writes a failed terminal state and publishes it so user interfaces stop waiting. If a child subagent finishes, `_deliver_to_parent` sends its result back to the parent conversation. Without this file, turns would not be reliably picked up, isolated, authorized, billed, executed, or marked complete.

#### Function details

##### `_agent_tools`  (lines 115–138)

```
def _agent_tools(all_tools: tuple[ToolDef, ...], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses which tools a normal agent is allowed to see for this turn. It keeps hidden, specialist-only tools away from the general agent unless the turn is a direct intent action where the model is not choosing tools.

**Data flow**: It receives the live tool list, the agent’s optional allowlist of tool names, and the turn’s admission source. It filters the live tools according to those rules, then returns the exact tuple of tools the agent may call.

**Call relations**: `_run_turn` calls this while building the `ToolRegistry` for a regular, non-subagent turn. The result is handed into the turn engine so the model can only ask for approved tools.

*Call graph*: called by 1 (_run_turn).


##### `_resolve_profile`  (lines 141–156)

```
def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile
```

**Purpose**: Looks up the named subagent profile that a child turn should use. If the profile no longer exists, it logs a clear error naming both the requested profile and the profiles that are currently registered.

**Data flow**: It takes a subagent registry, the current turn id, and a profile name. It asks the registry for that profile; on success it returns the profile object, and on failure it records helpful diagnostic details before re-raising the error.

**Call relations**: `_run_turn` calls this when the turn is a subagent turn. It relies on `SubagentRegistry.get` for the lookup and uses the logging system when an extension deployment has removed a profile that was already queued.

*Call graph*: calls 1 internal fn (get); called by 1 (_run_turn); 1 external calls (log_error).


##### `_subagent_tools`  (lines 159–170)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses which tools a subagent may use. It respects the subagent profile’s declared tools, and optionally adds tools granted by the parent context when the profile is not isolated.

**Data flow**: It receives all available tools, a subagent profile, and a set of granted tool names. It builds the allowed names, filters the live tool list, and returns only tools that match the profile’s rules.

**Call relations**: `_run_turn` calls this while preparing a subagent turn. The returned tools become the subagent’s `ToolRegistry`, which limits what the subagent model can call.

*Call graph*: called by 1 (_run_turn).


##### `_apply_provisions`  (lines 176–185)

```
async def _apply_provisions(runtime: 'Runtime', workspace_id: UUID) -> None
```

**Purpose**: Applies extension-provided default agents to a workspace once per process. This makes sure a workspace has the agents shipped by installed extensions without doing the same setup on every turn.

**Data flow**: It receives the runtime and a workspace id. If this process has already provisioned that workspace, it does nothing; otherwise it runs `AgentProvisioning` using the active manifests and remembers that the workspace has been covered.

**Call relations**: `_execute_turn` calls this near the start of a turn, after entering the workspace context. It is a safety net in addition to onboarding, so existing and newly active workspaces get the shipped agents they need.

*Call graph*: called by 1 (_execute_turn); 1 external calls (__init__).


##### `init_runtime`  (lines 224–228)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the process-wide runtime object that turn execution needs. The runtime is the bundle of shared services, such as database workflow client, model registry, blob storage, sandbox opener, tools, credentials, and extension manifests.

**Data flow**: It receives a `Runtime` object. If no runtime has been installed, it stores it in the module-level variable; if one already exists, it raises an error to prevent accidental double setup.

**Call relations**: This is called during server setup before queued turns can run. Later, `_execute_turn` reads the installed runtime and refuses to continue if setup never happened.


##### `reset_runtime`  (lines 231–236)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed runtime so tests can install a fresh one. Production setup installs the runtime once, but tests need a clean seam between scenarios.

**Data flow**: It takes no input. It sets the module-level runtime variable back to `None`, changing the process from “ready to execute turns” back to “not initialized.”

**Call relations**: This is mainly a testing helper. It pairs with `init_runtime`; without it, tests that need different fake services would trip the single-initialization guard.


##### `_execute_turn`  (lines 239–291)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs the outer workflow body for one queued turn. It sets the correct workspace and tracing context, runs the turn, catches setup failures, and finally tries to deliver child results to a parent if needed.

**Data flow**: It receives workspace and turn ids as strings from the workflow queue. It reads the turn’s basic database fields, applies workspace provisions, enters agent and tracing scopes, calls `_run_turn`, writes a failed terminal result if an exception escapes, then calls `_deliver_to_parent` and returns a status string.

**Call relations**: `turn_workflow` calls this as the DBOS workflow body. Inside, it calls `_apply_provisions`, `_run_turn`, `_commit_failed_terminal`, and `_deliver_to_parent`, making it the protective shell around actual turn execution.

*Call graph*: calls 4 internal fn (_apply_provisions, _commit_failed_terminal, _deliver_to_parent, _run_turn); called by 1 (turn_workflow); 6 external calls (select, agent, workspace_tx, turn_span, ws, UUID).


##### `_deliver_to_parent`  (lines 294–326)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: After a child or subagent turn finishes, this function sends its final result back to the parent conversation. It deliberately reads the durable database result, not an in-memory result, so it delivers what was actually committed.

**Data flow**: It receives the runtime and a turn id. It loads the turn row; if there is no parent or no terminal result, it exits. Otherwise it builds a validated `Turn` record and asks `SubagentResult` to deliver it, logging and deferring if delivery fails.

**Call relations**: `_execute_turn` calls this after every turn attempt. If delivery fails, it logs `subagent.delivery_deferred` instead of failing the already-finished turn; a later sweep can pick up the missing delivery.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_enqueue_handoff`  (lines 329–364)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: Queues a follow-up workflow when a claimed turn discovers another turn that should take over or continue the ordered conversation work. It uses the same conversation as the partition key so ordering stays safe.

**Data flow**: It receives a DBOS client, workspace id, turn id, conversation id, and workflow id. It builds enqueue options and asks DBOS to enqueue the workflow. If enqueueing is cancelled or fails, it clears the turn’s `dispatch_enqueued_at` marker so the dispatch can be retried later.

**Call relations**: `_run_turn` calls this when the claim step returns a handoff turn. It hands the work to DBOS, and on trouble records enough state for a later dispatcher to try again rather than silently losing the turn.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 367–606)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds and runs the actual turn engine. This is where the job ticket becomes a live agent run: it claims the turn, loads records, selects tools and skills, opens the sandbox, chooses the model and billing setup, and starts the engine.

**Data flow**: It receives the runtime and turn id. It claims the turn, handles superseded or handed-off turns, loads the turn, agent, and audience, prepares subagents, extension tools, hooks, skills, prompts, model client, BYOK billing verdict, credentials, sandbox, and tool registry. It then creates a `TurnEngine`, runs either the normal flow or direct intent flow, and returns a status such as completed, failed, parked, or superseded.

**Call relations**: `_execute_turn` calls this inside the workspace and trace context. It calls many helper functions in this file, including `_load_turn`, `_agent_tools`, `_subagent_tools`, `_resolve_profile`, `_frozen_byok`, `_open_sandbox`, `_previous_turn_ended_at`, `_run_lineage`, `_enqueue_handoff`, and `_commit_failed_terminal`, then hands the fully prepared environment to `TurnEngine`.

*Call graph*: calls 10 internal fn (_agent_tools, _commit_failed_terminal, _enqueue_handoff, _frozen_byok, _load_turn, _open_sandbox, _previous_turn_ended_at, _resolve_profile, _run_lineage, _subagent_tools); called by 1 (_execute_turn); 28 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 404–408)

```
def subagents_for(acting_member_id: UUID | None) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates an authorized view of the subagent launcher for a specific acting member. This lets tools spawn subagents while preserving who is acting and what that person is allowed to do.

**Data flow**: It receives an optional member id. It asks the current `Subagents` object to authorize that member, then returns both the spawn function and the authorized subagent controller.

**Call relations**: This small nested helper is created inside `_run_turn` and passed into the `TurnEngine`. The engine can use it later when a tool or model action needs to spawn work on behalf of a particular member.


##### `_commit_failed_terminal`  (lines 609–670)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Writes a final failed result when something goes wrong before the normal engine can finish cleanly. It is the backstop that makes sure clients waiting for a turn get an ending instead of hanging forever.

**Data flow**: It receives the hub, turn id, and exception. It builds a failed terminal frame, repeatedly tries to update the database from queued/running to failed, emits a metric and error log only if that transition happened, publishes the terminal frame to listeners, and backs off and retries if even that reporting path fails.

**Call relations**: `_execute_turn` and `_run_turn` both call this when exceptions escape their normal paths. It uses the hub to publish the failure and the database to make the final state durable.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 10 external calls (__init__, __init__, sleep, update, workspace_tx, emit_metric, formatted_stack, log, log_error, turn_profile).


##### `turn_workflow`  (lines 674–675)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Defines the DBOS workflow entry for running a turn. DBOS is the durable workflow system that can enqueue, replay, and recover this work after crashes.

**Data flow**: It receives workspace and turn ids as strings from DBOS. It immediately passes them to `_execute_turn` and returns whatever status that function produces.

**Call relations**: DBOS calls this when an item from the turn queue is ready. It is intentionally thin, leaving the real orchestration and error protection to `_execute_turn`.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 678–760)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Loads the full turn record, its agent settings, and the conversation audience from the database. It turns raw database rows into typed objects the rest of the turn runner can use safely.

**Data flow**: It receives a turn id. It joins the turn, agent, and conversation tables, reads fields such as prompt, model, inbound message, status, parent information, sandbox conversation id, and audience, validates nested JSON fields, and returns a `Turn`, an `Agent`, and an `Audience`.

**Call relations**: `_run_turn` calls this after claiming a turn, and also when a claim shows the turn was already superseded. The returned objects become the foundation for prompt building, tool selection, sandbox opening, and engine creation.

*Call graph*: called by 1 (_run_turn); 8 external calls (__init__, __init__, model_validate, model_validate, model_validate, select, parse_audience, workspace_tx).


##### `_run_lineage`  (lines 763–794)

```
async def _run_lineage(turn: Turn) -> RunLineage | None
```

**Purpose**: Finds where a spawned turn should publish its live activity. For child turns, it traces back to the original root turn so user interfaces following the root conversation can still see the child’s progress.

**Data flow**: It receives a `Turn`. If the turn has no parent, it returns `None`. If it is a child, it walks parent links in the database until it reaches the root, determines a profile label for the child, and returns a `RunLineage` object describing root, parent, profile, and display name.

**Call relations**: `_run_turn` calls this before creating the engine. The resulting lineage is passed into `TurnEngine`, which uses it to publish child activity in the right stream.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, select, workspace_tx).


##### `_previous_turn_ended_at`  (lines 797–809)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: Finds when the immediately previous turn in the same conversation ended. This gives the engine timing context for ordinary conversational turns.

**Data flow**: It receives a `Turn`. If this is the first turn in the conversation, it returns `None`; otherwise it reads the previous sequence number’s `updated_at` timestamp and returns it with a timezone attached if needed.

**Call relations**: `_run_turn` calls this for non-intent turns before constructing the engine. The timestamp is then passed to `TurnEngine` as context about the gap since the previous turn.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_frozen_byok`  (lines 812–861)

```
async def _frozen_byok(workspace_id: UUID, turn_id: UUID, key_slot: str | None, attempt: str) -> bool
```

**Purpose**: Decides whether this run attempt uses the workspace’s own model key, and freezes that answer for the attempt. BYOK means “bring your own key”: the workspace supplies its own provider key, which affects billing.

**Data flow**: It receives workspace id, turn id, model key slot, and attempt id. It first checks whether the turn row already has a BYOK decision for this exact attempt. If not, it asks whether the workspace owns the needed key, writes that decision with the attempt id, rereads the settled row, and returns the final boolean answer.

**Call relations**: `_run_turn` calls this after choosing the model and before creating the engine. It uses `workspace_owns_the_key` and database updates so crash recovery and parallel retries cannot bill the same attempt in two different ways.

*Call graph*: called by 1 (_run_turn); 5 external calls (or_, select, update, workspace_owns_the_key, workspace_tx).


##### `_open_sandbox`  (lines 864–922)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens or attaches to the sandbox container where the turn’s commands and tools run. The sandbox is the isolated workspace environment, like a rented workbench shared by turns in the same conversation.

**Data flow**: It receives the sandbox service, token codec, turn, optional grants, connector command credentials, credential store, credential slots, and a cache-rewrite flag. It creates a signed run token, prepares environment variables for conversation id, git proxy authentication, grants, connector CLIs, and keyed providers, then opens the conversation sandbox and returns a `SandboxSession`.

**Call relations**: `_run_turn` calls this during sandbox setup. It calls lower-level sandbox and environment helpers, then hands the resulting session to `SandboxAuthorizer` and `TurnEngine` so tools can run with the correct identity and credentials.

*Call graph*: calls 2 internal fn (open, encode); called by 1 (_run_turn); 6 external calls (__init__, cache_git_config, _git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env).


##### `SandboxAuthorizer.authorize`  (lines 933–945)

```
async def authorize(self, acting_member_id: UUID | None) -> SandboxSession
```

**Purpose**: Creates a re-authorized sandbox session for a specific acting member. This is used when a tool needs to run as a particular member rather than just as the turn in general.

**Data flow**: It receives an optional acting member id. It builds a new signed run token containing the workspace, turn, and acting member, prepares updated grant-related environment variables, and asks the existing sandbox session to authorize with those values. It returns the updated `SandboxSession`.

**Call relations**: `_run_turn` creates a `SandboxAuthorizer` and passes its `authorize` method to the engine for normal non-intent turns. Later, when the engine or tools need member-scoped sandbox access, this method refreshes the token and grant environment without reopening the whole sandbox.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### `core/src/ufo/durability.py`

`io_transport` · `startup and crash recovery`

DBOS records workflow inputs, step results, and errors so a workflow can be replayed after a crash or a redeploy. The tricky part is that the code reading old records may not be exactly the same code that wrote them. A normal Python pickle, which is Python’s built-in object-saving format, can restore a Pydantic model as a raw pile of fields without running Pydantic’s normal validation and default-filling logic. That means if a new field was added later, the restored object may simply not have it, and replay can fail with a confusing missing-attribute error.

This file fixes that by wrapping pickle with a custom serializer called `ReplaySafeSerializer`. When it sees a Pydantic `BaseModel`, it does not pickle the object in the usual way. Instead, it records the model’s class and its current field values. On load, `_rebuild` asks Pydantic to validate those values against the current model class. This lets new fields use their current defaults, ignores fields that no longer exist, and gives clearer failures when a required new field has no default.

The file also provides `replay_safe_client`, the standard way this project creates a `DBOSClient`. That matters because saved rows are tagged with this serializer’s name. If a client is built without the matching serializer, DBOS may return the stored text instead of the original object.

#### Function details

##### `replay_safe_client`  (lines 27–31)

```
def replay_safe_client(system_database_url: str) -> DBOSClient
```

**Purpose**: Creates a DBOS client configured with this project’s replay-safe serializer. Someone should use this instead of constructing `DBOSClient` directly, because old saved workflow records need the same named serializer to be decoded correctly.

**Data flow**: It takes a system database URL as input. It creates a new `ReplaySafeSerializer`, gives both the database URL and serializer to `DBOSClient`, and returns the configured client. The result is a database client that writes and reads records using this file’s safer pickle format.

**Call relations**: This is the front door for setting up DBOS access in this repository. During client creation it calls the external `DBOSClient` constructor and supplies a `ReplaySafeSerializer`, so later database reads and writes use the serializer methods defined below.

*Call graph*: 2 external calls (__init__, DBOSClient).


##### `_rebuild`  (lines 34–35)

```
def _rebuild(model_class: type[BaseModel], fields: dict[str, object]) -> BaseModel
```

**Purpose**: Reconstructs a saved Pydantic model using Pydantic’s normal validation path. This is what makes old saved model data adapt to the current model definition when possible.

**Data flow**: It receives a model class and a dictionary of saved field values. It asks that class to validate the dictionary, which produces a fresh model object. During that validation, current defaults can be filled in and removed fields can be ignored according to the model’s rules.

**Call relations**: This function is named in the pickle data produced by `_ModelPickler.reducer_override`. Later, when `ReplaySafeSerializer.deserialize` calls `pickle.loads`, pickle uses this function to rebuild any saved Pydantic model.


##### `_ModelPickler.reducer_override`  (lines 39–42)

```
def reducer_override(self, obj: object) -> tuple[Callable[..., object], tuple[object, ...]]
```

**Purpose**: Tells pickle to save Pydantic models in this project’s safer form. Instead of storing a model exactly as Python sees it internally, it stores enough information to rebuild it through Pydantic later.

**Data flow**: It receives each object that pickle is about to save. If the object is a Pydantic `BaseModel`, it returns instructions saying: rebuild this later by calling `_rebuild` with the object’s class and its field dictionary. If the object is not a Pydantic model, it returns `NotImplemented`, which means normal pickle behavior should continue.

**Call relations**: This method is used when `ReplaySafeSerializer.serialize` creates a `_ModelPickler` and dumps data into it. It sits in the middle of the save process, stepping in only for Pydantic models and letting ordinary objects pass through unchanged.


##### `ReplaySafeSerializer.name`  (lines 48–49)

```
def name(self) -> str
```

**Purpose**: Returns the stable name DBOS uses to label data written with this serializer. The name matters because DBOS uses it later to decide how a stored value should be decoded.

**Data flow**: It takes no outside data beyond the serializer object itself. It returns the constant string `ufo_pickle`. Nothing else is changed.

**Call relations**: DBOS calls this when registering or using the serializer. Rows written by this serializer carry this name, so matching future reads can route the stored data back through `ReplaySafeSerializer.deserialize`.


##### `ReplaySafeSerializer.serialize`  (lines 51–54)

```
def serialize(self, data: object) -> str
```

**Purpose**: Turns a Python object into text that can be stored in the DBOS database. It uses custom pickle behavior so Pydantic models are saved in a replay-safe way.

**Data flow**: It receives any Python object. It creates an in-memory byte buffer, pickles the object into that buffer using `_ModelPickler`, then base64-encodes the bytes into a UTF-8 string. The output is database-friendly text representing the original object.

**Call relations**: DBOS calls this when it needs to persist workflow inputs, step outputs, or errors. Inside the save process, `_ModelPickler.reducer_override` may redirect Pydantic model saving through `_rebuild`, which is what makes later replay safer.

*Call graph*: 3 external calls (__init__, b64encode, BytesIO).


##### `ReplaySafeSerializer.deserialize`  (lines 56–57)

```
def deserialize(self, serialized_data: str) -> object
```

**Purpose**: Turns stored serializer text back into the original Python object. For Pydantic models saved by this file, the rebuild goes through Pydantic validation instead of blindly restoring old internal state.

**Data flow**: It receives a base64 text string from storage. It decodes that string back into pickle bytes, then asks pickle to load the object. If the saved data contains Pydantic model rebuild instructions, loading calls `_rebuild` to create a current, validated model instance.

**Call relations**: DBOS calls this when reading persisted workflow data during normal operation or crash recovery. It hands the byte decoding to `base64.b64decode` and object reconstruction to `pickle.loads`; pickle may then use `_rebuild` for saved Pydantic models.

*Call graph*: 2 external calls (b64decode, loads).


### `core/src/ufo/loop/engine.py`

`orchestration` · `turn execution`

A “turn” is one unit of agent work: a user message, a scheduled prompt, a subagent request, or a prepared tool intent. This file is the turn’s control room. It first claims ownership in the database so two workers do not answer the same turn. Then it loads the conversation transcript, applies prompt hooks, calls the language model, streams visible text to listeners, runs any tools the model asks for, and feeds tool results back into the next model round. While the turn is running, it can absorb new inbound messages so the answer does not ignore someone who spoke mid-run.

The file is built around DBOS steps, which are durable checkpoints for work that should not be repeated after a crash. A completed model round replays from the step log instead of calling the model again. A completed tool call replays instead of applying the same external action twice. This is like writing receipts after each risky operation, then resuming from the first missing receipt after a restart.

It also enforces spending and seat limits, parks a turn when limits are reached, bills consumed tokens, writes the transcript, publishes live frames, and records workspace file changes. Without this file, the system could double-run tools, lose messages, ignore cancellations, misbill usage, or leave users waiting forever.

#### Function details

##### `_claim_turn`  (lines 225–272)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued, parked, or same-attempt running turn for one execution. This prevents two workers from doing the same turn at the same time.

**Data flow**: It receives a turn id and an attempt id → reads and locks the related conversation, then updates the turn to running only if it is claimable → returns whether the claim was fresh, adopted from the same attempt, or lost.

**Call relations**: TurnEngine._mark_running uses this at the start of normal and intent turns. _claim_turn_with_handoff builds on it when a queue worker also wants to prepare the next queued turn.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 283–343)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[str | None, _TurnHandoff | None]
```

**Purpose**: Claims the current turn and, if possible, marks the next queued turn in the same conversation as ready to dispatch. This helps keep conversation work moving in order.

**Data flow**: It receives a turn id and attempt id → first claims the turn, then looks for the next queued turn whose dispatch has not been stamped → returns the claim result plus a small handoff record for the next turn, or no handoff.

**Call relations**: It calls _claim_turn for the ownership decision, then uses database locks and a generated workflow id when it successfully hands off another turn.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `_RoundInput.__repr__`  (lines 420–425)

```
def __repr__(self) -> str
```

**Purpose**: Gives a short debug-friendly description of a model round request. It avoids dumping the whole prompt and message list into logs.

**Data flow**: It reads the round input fields → summarizes counts and flags → returns a compact string.

**Call relations**: This is used implicitly when a _RoundInput is logged or displayed, especially around the model-round DBOS step.


##### `_BoundToolCall.__repr__`  (lines 433–434)

```
def __repr__(self) -> str
```

**Purpose**: Gives a compact debug label for a tool call that has already been tied to its execution context.

**Data flow**: It reads the tool name and call id → formats them → returns a short string.

**Call relations**: It supports readable logging and step descriptions around tool dispatch work.


##### `_RejectedToolCall.__repr__`  (lines 444–448)

```
def __repr__(self) -> str
```

**Purpose**: Gives a compact debug label for a tool call that was rejected before execution.

**Data flow**: It reads the rejected call, outcome, and error class → formats them → returns a short string.

**Call relations**: It helps make rejected dispatch inputs understandable when tool dispatch steps are inspected.


##### `ModelStreamError.__init__`  (lines 489–490)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Creates an exception that preserves a model stream failure together with any partial text already received.

**Data flow**: It receives the provider error class, message, and partial output → stores all three in the exception arguments → later code can bill usage and salvage partial output.

**Call relations**: TurnEngine._stream_recovering_overflow raises this after a model-round step reports an error without losing the round’s recorded usage.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 492–494)

```
def __str__(self) -> str
```

**Purpose**: Formats the model stream error as a readable provider-style message.

**Data flow**: It reads the stored error class and message → combines them → returns text that still identifies the original model-side error.

**Call relations**: This is used whenever the exception is logged or written into a terminal error.


##### `ModelStreamError.model_error_class`  (lines 497–499)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original class name of the model provider error.

**Data flow**: It reads the first stored exception argument → returns it unchanged.

**Call relations**: TurnEngine._model_round and _commit_once use this to distinguish truncation from other model failures and to report the right terminal error.


##### `ModelStreamError.partial_output`  (lines 502–504)

```
def partial_output(self) -> str
```

**Purpose**: Returns the text or raw tool-call fragments the model produced before the stream failed.

**Data flow**: It reads the stored partial output → returns it for recovery paths.

**Call relations**: TurnEngine._model_round uses this when a response was truncated, saving the partial content to a workspace file so the model can continue from it.


##### `ModelStreamError.model_error_message`  (lines 507–509)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original model provider error message.

**Data flow**: It reads the stored message argument → returns it unchanged.

**Call relations**: TurnEngine._commit_once uses this when building a failed terminal frame.


##### `TurnParked.__init__`  (lines 516–518)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates the special exception used when a running turn must pause because a limit was reached.

**Data flow**: It receives a human-readable reason → stores it in the exception and as a message field → the parking path can publish that reason.

**Call relations**: TurnEngine._enforce_spend raises this, and TurnEngine.run catches it to park the turn instead of failing it.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 527–551)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits a model’s tool calls into groups that can safely run together. Unsafe or unknown tools become barriers so tool effects stay in the model’s intended order.

**Data flow**: It receives the tool registry and ordered tool calls → checks whether each known tool is safe to run in parallel → yields ordered groups, capped at the configured parallel limit.

**Call relations**: TurnEngine._model_round uses these groups before dispatching tools, so parallel-safe calls can be faster without reordering sensitive work.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 554–556)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed pieces of a tool-call JSON argument into a Python dictionary.

**Data flow**: It receives partial JSON strings → joins them → parses JSON, or returns an empty dictionary if nothing meaningful was sent.

**Call relations**: TurnEngine._stream_once uses this after the model stream ends to build ToolUseBlock objects from streamed tool-call fragments.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 559–577)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the small metadata block placed before a member message so the model knows when, who, and where the message came from.

**Data flow**: It receives a message id, optional context, and admission time → formats the time in the sender’s timezone when available → returns a <context> text block.

**Call relations**: TranscriptRepair.load_messages uses it for the founding message, and TurnEngine._render_arrival uses it for messages absorbed mid-turn.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 580–585)

```
def _bounded(content: str) -> str
```

**Purpose**: Shortens overly long tool result text to a safe size and adds a note about what was cut.

**Data flow**: It receives text → leaves it alone if it is within the limit, otherwise keeps the front and appends a truncation notice → returns bounded text.

**Call relations**: TurnEngine._dispatch_step uses it for tool errors and fallback offload cases; TurnEngine._model_round uses it for finish-tool validation feedback.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_meter_dispatch`  (lines 588–615)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str) -> None
```

**Purpose**: Records metrics for one tool dispatch, including which tool ran, how long it took, and how it ended.

**Data flow**: It receives the registry, call, start time, outcome, optional error class, and profile → normalizes unknown tool names → emits count and timing metrics.

**Call relations**: TurnEngine._bind_or_error records binding failures through it, and TurnEngine._dispatch_step records every actual dispatch step exit.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 618–660)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedSkill, ...]]
```

**Purpose**: Finds which skills were successfully loaded in the current message window. This stops the engine from injecting the same skill instructions again unnecessarily.

**Data flow**: It scans messages for completed load-skill tool calls → checks each requested skill in the registry → yields the skill closure only when the full result is still readable.

**Call relations**: TurnEngine._reseed_loaded_skills uses it before and after compaction so the shared skill tracker matches what the model can still see.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 663–681)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts a structured final payload from a successful final tool result, such as ask_user, request_credentials, or connect_account.

**Data flow**: It receives the round’s tool calls, results, target tool name, and expected data model → checks the last call/result pair → parses and validates the JSON payload, or returns none.

**Call relations**: TurnEngine._model_round uses it after normal tool rounds, and TurnEngine.run_intent uses it after a prepared intent dispatch.

*Call graph*: called by 2 (_model_round, run_intent); 1 external calls (loads).


##### `_created_refs`  (lines 684–716)

```
def _created_refs(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> tuple[ObjectRef, ...]
```

**Purpose**: Reads tool results to find which workspace objects were newly created.

**Data flow**: It receives tool calls and their results → looks for successful object_apply results marked created → parses them into ObjectRef values → returns the created references.

**Call relations**: TurnEngine._fold_created accumulates these during chat turns, and TurnEngine.run_intent includes them in an intent terminal.

*Call graph*: called by 2 (_fold_created, run_intent); 2 external calls (__init__, loads).


##### `_total_usage`  (lines 719–727)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many model usage events into one total usage record.

**Data flow**: It receives usage events → sums input, output, and cache token fields separately → returns one combined Usage object.

**Call relations**: Cost, billing, spend checks, live cost ticks, parking, cancellation billing, and model metrics all use this common total.

*Call graph*: called by 6 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost, _stream_once); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 742–766)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a turn’s already-committed terminal frame when a duplicate or redelivered execution finds it no longer owns the turn.

**Data flow**: It reads the turn terminal from the database → if none exists, returns none → otherwise validates the frame, preserves inbound transcript data, publishes the terminal, and returns it.

**Call relations**: TurnEngine._resolve_unclaimed delegates here when a running claim is lost, letting completed turns wake waiting clients without disturbing live turns.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 768–775)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the full successful conversation transcript, including the assistant’s final answer.

**Data flow**: It receives prior messages, answer text, system prompt, and injected context → appends the assistant answer → hands the complete conversation to write_conversation.

**Call relations**: TurnEngine._persist_transcript uses this after a turn commits successfully.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 777–796)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Preserves user messages when a turn ends without a normal final answer, such as failure, cancellation, or denial.

**Data flow**: It receives optional absorbed arrivals and optional founding denial → builds the safest user-message transcript without assistant error text → writes it as the conversation state.

**Call relations**: TranscriptRepair.resolve uses it before republishing, and TurnEngine._persist_inbound uses it on non-success exits.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 798–807)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the prior conversation and appends this turn’s founding inbound message in the form the model should see.

**Data flow**: It reads earlier transcript messages → prefixes member turns with a context tag, while spawned turns stay bare → returns the full message tuple ending with the current user message.

**Call relations**: TurnEngine._load_messages and TranscriptRepair.persist_inbound rely on this to reconstruct the model window.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 809–815)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads conversation history before this turn, while avoiding this turn’s own previous write during replay.

**Data flow**: It reads stored transcript data → if missing or not older than this turn, returns empty history → otherwise returns the stored messages.

**Call relations**: TranscriptRepair.load_messages and persist_inbound call this when assembling transcript state.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 817–837)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a conversation transcript with a few retries so temporary storage failures do not immediately lose history.

**Data flow**: It receives messages and optional system/injected text → wraps them in a Conversation object → tries to write, logging and sleeping before retrying on failure.

**Call relations**: TranscriptRepair.persist_transcript and persist_inbound both use this as the durable transcript writer.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 871–888)

```
def exited(self, status: str) -> None
```

**Purpose**: Records timing and round-count metrics once for the way this execution ended.

**Data flow**: It receives an exit status → if already ended, does nothing → otherwise emits wall-clock duration and round-count metrics.

**Call relations**: TurnEngine._commit calls it after terminal publication, while TurnEngine.run also marks parked, cancelled, and preempted exits directly.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 967–978)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the engine was wired with compatible audiences and a valid subagent finish-tool setup.

**Data flow**: It reads tool extension audiences, hook audience, output contract, and tool names → raises a configuration error if they conflict → otherwise leaves the engine ready to run.

**Call relations**: This runs automatically when TurnEngine is created, catching bad wiring before any turn work starts.


##### `TurnEngine.__repr__`  (lines 980–984)

```
def __repr__(self) -> str
```

**Purpose**: Returns a short identifying string for the engine in logs and debugging.

**Data flow**: It reads the turn id, agent id, and profile → formats them → returns the string.

**Call relations**: It is used implicitly when a TurnEngine object is printed or logged.


##### `TurnEngine.profile`  (lines 987–990)

```
def profile(self) -> str
```

**Purpose**: Computes the telemetry profile label for this turn, such as main, agent child, or named subagent.

**Data flow**: It reads whether the turn is spawned and its subagent profile → asks the telemetry helper for the label → returns it.

**Call relations**: Most metrics and logs in TurnEngine use this property so main and subagent activity can be separated.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 992–1181)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal model-driven turn from claim through final commit. This is the main path for chat-like agent work.

**Data flow**: It starts metrics and builds a tool context → claims the turn, loads and filters inbound text, repeatedly runs model rounds and tools, absorbs arrivals, commits the terminal, writes transcript and workspace changes → returns the terminal frame or none if another execution owns it.

**Call relations**: It is the top-level orchestration method. It calls nearly every helper in this file and catches parking, cancellation, preemption, and failure to route each exit safely.

*Call graph*: calls 16 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _publish, _publish_run (+6 more)); 12 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, escape, monotonic, emit_metric (+2 more)).


##### `TurnEngine.run.rank_find`  (lines 1011–1029)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Provides the browser find tool with a host-side model call for ranking page elements.

**Data flow**: It receives a system prompt and user text → builds a small model request → streams text and records usage into the parent turn’s usage list → returns the combined ranking text.

**Call relations**: TurnEngine.run installs this function into ToolContext so browser tools can ask the model for help while still billing the same turn.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine.run_intent`  (lines 1183–1294)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a prepared intent by dispatching exactly one named tool without asking the model to rewrite or interpret it.

**Data flow**: It claims the turn → parses the inbound tool intent → binds requester authority, dispatches the tool step, commits either done or failed, writes transcript, and cleans up → returns the terminal frame.

**Call relations**: It shares the same dispatch, commit, transcript, and cancellation safeguards as TurnEngine.run, but skips model rounds and prompt hooks for machine-submitted intent envelopes.

*Call graph*: calls 10 internal fn (_bind_or_error, _commit, _dispatch_step, _load_messages, _mark_running, _persist_transcript, _resolve_unclaimed, _stop_sandbox_commands, _created_refs, _final_act); 10 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, monotonic, emit_metric, log).


##### `TurnEngine._scheduled_system`  (lines 1296–1330)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns.

**Data flow**: It receives the base system prompt → searches memory with a short timeout → if matches exist, escapes and formats them into a recalled-memory block → returns the augmented or original prompt.

**Call relations**: TurnEngine.run calls this only for scheduled admissions before the first model round.

*Call graph*: called by 1 (run); 5 external calls (__init__, timeout, escape, audience_subjects, log).


##### `TurnEngine._mark_running`  (lines 1332–1340)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn for the engine’s current attempt.

**Data flow**: It reads the turn id and attempt id from the engine → calls _claim_turn → returns true if ownership was obtained.

**Call relations**: TurnEngine.run and run_intent use it before doing any real work; losing the claim sends them to _resolve_unclaimed.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 1342–1343)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Builds a TranscriptRepair helper for this turn.

**Data flow**: It reads the turn, transcript, and hub from the engine → packages them into TranscriptRepair → returns it.

**Call relations**: Transcript loading, transcript persistence, and unclaimed-turn resolution all go through this helper.

*Call graph*: called by 5 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed, run); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 1345–1347)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the current model message window with tracing around the transcript read.

**Data flow**: It creates a trace span → asks TranscriptRepair to load messages → returns the message tuple.

**Call relations**: TurnEngine.run and run_intent use it when preparing transcript persistence and model context.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 1349–1550)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the repeated model-and-tools loop until the turn has a final answer or must be forced to finish.

**Data flow**: It receives context, messages, usage and state accumulators → absorbs arrivals, checks spend, compacts context, streams a model round, dispatches tool calls, records created objects, and repeats → returns final messages, answer text, and any pending structured request.

**Call relations**: TurnEngine.run calls this inside its main loop. It delegates model streaming, arrival claiming, tool binding, dispatching, cost publishing, and forced-final behavior to smaller helpers.

*Call graph*: calls 16 internal fn (_absorb_arrivals, _bind_or_error, _dispatch, _enforce_spend, _fold_created, _force_final, _force_finish, _offload, _publish_cost, _reseed_loaded_skills (+6 more)); called by 1 (run); 9 external calls (__init__, __init__, __init__, gather, marked_replies, emit_metric, log, span, change_targets).


##### `TurnEngine._fold_created`  (lines 1552–1580)

```
async def _fold_created(self, created: dict[ObjectRef, None], tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> None
```

**Purpose**: Adds newly created workspace object references to the turn’s durable record as soon as they happen.

**Data flow**: It receives the current created accumulator, tool calls, and results → extracts fresh object refs → updates the accumulator and the turn row if anything new appeared.

**Call relations**: TurnEngine._model_round calls it after tool dispatch, including on unwind, so created objects are not lost if a later step fails or parks.

*Call graph*: calls 1 internal fn (_created_refs); called by 1 (_model_round); 2 external calls (update, workspace_tx).


##### `TurnEngine._absorb_arrivals`  (lines 1582–1642)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Pulls queued inbound messages into the running turn between model rounds.

**Data flow**: It receives current messages and tracking lists → claims arrivals, turns each into a rendered user message or safe denial marker, records requester information, publishes absorbed frames for member messages → returns the expanded message window.

**Call relations**: TurnEngine._model_round calls it at the start of every round so new messages are considered before the next model call.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); called by 1 (_model_round); 5 external calls (__init__, __init__, __init__, escape, log).


##### `TurnEngine._speak`  (lines 1644–1696)

```
async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None
```

**Purpose**: Delivers mid-turn reply spans that the model explicitly marked for members.

**Data flow**: It receives marked replies and the round index → writes idempotent mid-turn reply rows and publishes live Reply frames → returns nothing.

**Call relations**: TurnEngine._model_round calls it for tool-calling rounds after separating spoken spans from working text.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_model_round); 4 external calls (__init__, workspace_tx, log, mid_turn_reply_id_for).


##### `TurnEngine._stream_closing_spans`  (lines 1698–1708)

```
async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Streams marked reply spans from the final answer back to listeners that were watching deltas.

**Data flow**: It receives marked reply spans → publishes each span as a text delta → returns nothing.

**Call relations**: TurnEngine._model_round and _force_final use it when a closing round has no separate Reply frame but still needs visible answer text.

*Call graph*: calls 1 internal fn (_publish); called by 2 (_force_final, _model_round); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 1710–1734)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Applies the user-prompt hook to one queued arrival and formats it for the model.

**Data flow**: It receives arrival id, body, context, speaker id, and creation time → fires the hook → returns either rendered message text with context and injection, or a safe denial reason.

**Call relations**: TurnEngine._claim_arrivals uses it inside the memoized arrival-drain step so replay reuses the same rendered content.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1737–1806)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Durably claims pending inbound messages for this turn and renders them exactly once.

**Data flow**: It receives ids already absorbed → stamps unclaimed or still-unabsorbed inbound rows as consumed by this turn, renders them in sequence, logs the batch → returns Arrival records.

**Call relations**: TurnEngine._absorb_arrivals calls this DBOS step. Because it is memoized, crash recovery does not consume different messages or re-fire hooks for the same drain.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 7 external calls (__init__, model_validate, and_, or_, update, workspace_tx, log).


##### `TurnEngine._release_unabsorbed`  (lines 1808–1827)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns claimed-but-not-absorbed arrivals to the queue after failure or cancellation.

**Data flow**: It receives absorbed ids → clears the consumed marker from any other rows stamped by this turn → logs failures without blocking exit.

**Call relations**: TurnEngine.run calls it on cancellation, preemption, and general failure paths so future turns can drain messages that this execution did not really use.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1829–1867)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, requesters: dict[UUID, ActiveMessage]) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Produces a final answer after the normal round budget is exhausted instead of immediately failing the turn.

**Data flow**: It receives messages, usage, system prompt, and requester data → checks spend, compacts once, then either forces a subagent finish call or asks the model for a no-tools final answer → returns final messages and text.

**Call relations**: TurnEngine._model_round calls it when the round loop reaches its limit.

*Call graph*: calls 5 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_closing_spans, _stream_recovering_overflow); called by 1 (_model_round); 4 external calls (__init__, marked_replies, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 1869–1894)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to close by calling only the finish tool and validating its structured output.

**Data flow**: It receives messages, usage, and system prompt → runs one model round with finish compelled → validates the finish arguments against the output contract → returns messages and canonical JSON answer.

**Call relations**: TurnEngine._model_round uses it when a subagent stops with prose, and _force_final uses it when a subagent exhausts its round budget.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 1896–1956)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False, active_requests: tuple[str, ...]=()
```

**Purpose**: Runs one model round and retries once after forced compaction if the provider says the context is too large.

**Data flow**: It receives messages, usage, system prompt, and round flags → calls _stream_once, records usage, converts reported stream errors into ModelStreamError → on context overflow, compacts and retries once → returns the possibly compacted messages and stream result.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish all use this wrapper instead of calling _stream_once directly.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 1958–2018)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks seats, balance, and spending caps before another model round is allowed.

**Data flow**: It receives usage so far and active requesters → checks member seats, prices current in-flight usage, checks balance and caps → either returns normally or raises TurnParked with a reason.

**Call relations**: TurnEngine._model_round and _force_final call it before more model tokens are spent; TurnEngine.run catches TurnParked and parks the turn.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 7 external calls (__init__, __init__, __init__, applicable_caps_absent, audience_member, balance_absent, workspace_tx).


##### `TurnEngine._stream_once`  (lines 2021–2262)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Performs one streamed model call and records its complete result as a recoverable DBOS step.

**Data flow**: It receives a _RoundInput → builds the model request, streams text, tool-call fragments, reasoning blocks, and usage, publishes visible text deltas, records metrics → returns StreamResult with text, tool calls, reasoning, usage, or captured error details.

**Call relations**: TurnEngine._stream_recovering_overflow is its only caller. Memoization here is what prevents crash recovery from re-calling the model and spending tokens twice.

*Call graph*: calls 2 internal fn (_parse_args, _total_usage); called by 1 (_stream_recovering_overflow); 14 external calls (__init__, __init__, __init__, __init__, __init__, Event, Lock, ensure_future, now, monotonic (+4 more)).


##### `TurnEngine._stream_once.flush`  (lines 2099–2108)

```
async def flush() -> None
```

**Purpose**: Flushes buffered model text to the live hub while respecting reply redaction.

**Data flow**: It reads buffered chunks → passes them through the redactor, which hides marked reply spans until the right time → publishes visible text and clears the buffer.

**Call relations**: This inner helper is used by _stream_once during streaming, both when enough bytes accumulate and when the round ends.

*Call graph*: 1 external calls (__init__).


##### `TurnEngine._stream_once.pace`  (lines 2110–2115)

```
async def pace() -> None
```

**Purpose**: Periodically flushes model text so live listeners see progress even when chunks are small.

**Data flow**: It waits in short intervals until the stop signal is set → calls flush on each timeout → exits when streaming is done.

**Call relations**: TurnEngine._stream_once starts it as a background task for each model stream.

*Call graph*: 1 external calls (wait_for).


##### `TurnEngine._publish_cost`  (lines 2264–2279)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the turn’s running token and cost total after a model round.

**Data flow**: It receives usage events → totals tokens and prices them → publishes a CostTick live frame.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish call it after streamed model work so clients can show live cost.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 2281–2292)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skills currently visible to the model.

**Data flow**: It receives the message window → scans completed load-skill calls with _loaded_skill_closures and includes preloaded skills → reseeds the compaction tracker.

**Call relations**: TurnEngine._model_round calls it around compaction, and _stream_recovering_overflow calls it after forced compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 2 (_model_round, _stream_recovering_overflow).


##### `TurnEngine._bind_or_error`  (lines 2294–2315)

```
async def _bind_or_error(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Attempts to bind a tool call to the correct requester context, but converts binding problems into tool-error results instead of crashing the round.

**Data flow**: It receives a base ToolContext, tool call, and active requesters → calls _bind_requester → returns a _BoundToolCall on success, or a _RejectedToolCall with error text on normal validation failure.

**Call relations**: TurnEngine._model_round and run_intent use it before dispatch. _dispatch_step later records either bound calls or rejected calls in the same step sequence.

*Call graph*: calls 2 internal fn (_bind_requester, _meter_dispatch); called by 2 (_model_round, run_intent); 3 external calls (__init__, __init__, monotonic).


##### `TurnEngine._dispatch`  (lines 2317–2318)

```
def _dispatch(self, bound: _DispatchInput) -> Awaitable[ToolResultBlock]
```

**Purpose**: Starts the durable tool-dispatch step and converts its stored result into the model-facing tool result block.

**Data flow**: It receives a bound or rejected dispatch input → calls _dispatch_step → passes the awaited result to _dispatch_result → returns an awaitable tool result block.

**Call relations**: TurnEngine._model_round uses this for each dispatch item in a segment.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step); called by 1 (_model_round).


##### `TurnEngine._dispatch_result`  (lines 2320–2346)

```
async def _dispatch_result(self, step: Awaitable[DispatchResult]) -> ToolResultBlock
```

**Purpose**: Rehydrates a recorded dispatch result into the format the model expects, including any images stored outside the step log.

**Data flow**: It awaits a DispatchResult → if there are no images, returns a text ToolResultBlock → otherwise reads image blobs and returns mixed text/image content.

**Call relations**: TurnEngine._dispatch calls this after _dispatch_step so image bytes do not have to live inside DBOS step records.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._bind_requester`  (lines 2348–2387)

```
async def _bind_requester(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Adjusts a tool call’s context when the model says the call is requested by a particular message.

**Data flow**: It receives context, call, and active requesters → validates and removes the requested_by field, chooses the acting member’s sandbox and subagent controls when configured → returns updated context and cleaned call.

**Call relations**: TurnEngine._bind_or_error wraps this so requester mistakes become model-visible tool errors.

*Call graph*: called by 1 (_bind_or_error); 3 external calls (model_copy, replace, UUID).


##### `TurnEngine._offload`  (lines 2389–2414)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text to the sandbox’s private tool-output directory and returns the path for the model to read later.

**Data flow**: It receives a file name and content → ensures the output directory exists, writes bytes to the sandbox, logs failures → returns the path or none if offload failed.

**Call relations**: TurnEngine._dispatch_step uses it for large tool output, and _model_round uses it to save truncated model output for salvage.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._dispatch_step`  (lines 2417–2598)

```
async def _dispatch_step(self, bound: _DispatchInput) -> DispatchResult
```

**Purpose**: Runs one tool call as a recoverable DBOS step, including hooks, validation, offloading, image handling, and metrics.

**Data flow**: It receives a bound or rejected call → validates the tool and arguments, runs pre hooks, invokes the handler with an idempotency key when needed, bounds or offloads text, stores images in blobs, runs post hooks, meters the outcome → returns a DispatchResult.

**Call relations**: TurnEngine._dispatch uses it during model rounds, and run_intent uses it directly. Its step memoization prevents crash recovery from re-running completed side effects.

*Call graph*: calls 8 internal fn (_bounded_image, _offload, _pending_member_guidance, _publish, _publish_run, _redoes_on_replay, _bounded, _meter_dispatch); called by 2 (_dispatch, run_intent); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, replace, monotonic, tool_activity (+3 more)).


##### `TurnEngine._redoes_on_replay`  (lines 2600–2609)

```
def _redoes_on_replay(self, name: str) -> bool
```

**Purpose**: Decides whether re-executing a tool call after crash recovery would redo work and can therefore be preempted by new member guidance.

**Data flow**: It receives a tool name → looks it up → returns true for known non-side-effecting tools, false for side-effecting or unknown tools.

**Call relations**: TurnEngine._dispatch_step uses it during adoption replay to decide whether pending member guidance should stop an old read-like call.

*Call graph*: called by 1 (_dispatch_step).


##### `TurnEngine._pending_member_guidance`  (lines 2611–2627)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether a member message is waiting in the queue for this conversation.

**Data flow**: It reads the inbound-message table for an unconsumed member admission → returns true if any exists.

**Call relations**: TurnEngine._dispatch_step uses it during crash-recovery adoption before redoing certain tool calls.

*Call graph*: called by 1 (_dispatch_step); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 2629–2657)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized tool-result images before sending them back to the model.

**Data flow**: It receives an ImageBlock with base64 data → decodes and opens the image, resizes it if its largest edge exceeds the limit, re-encodes it → returns the resized or original image if processing fails.

**Call relations**: TurnEngine._dispatch_step calls it before storing successful tool-result images in the blob store.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 2659–2733)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Makes the turn’s terminal result durable, retrying database failures until the client can be answered.

**Data flow**: It receives desired status, usage, meter, answer/error data, and terminal extras → repeatedly calls _commit_once until it gets a result → publishes terminal and subagent status, records metrics, and returns the frame or none if arrivals blocked the commit.

**Call relations**: TurnEngine.run and run_intent use this for done and failed endings.

*Call graph*: calls 4 internal fn (_commit_once, _publish, _publish_run, exited); called by 2 (run, run_intent); 5 external calls (__init__, sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._record_workspace_changes`  (lines 2735–2748)

```
async def _record_workspace_changes(self, targets: tuple[str, ...]) -> None
```

**Purpose**: Refreshes the list of sandbox workspace changes after the turn has finished answering.

**Data flow**: It receives target paths gathered from tool calls → constructs a WorkspaceChangeRecorder for the correct conversation workspace → asks it to scan and record changes.

**Call relations**: TurnEngine.run calls it after terminal publication and transcript writing so change scanning does not delay the user-facing answer.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 2750–2857)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction to bill usage and write the terminal frame.

**Data flow**: It totals usage, optionally locks the conversation and refuses commit if unabsorbed arrivals exist, records usage, reads cost, builds a TerminalFrame, updates the turn if still non-terminal → returns the frame plus whether this call committed it.

**Call relations**: TurnEngine._commit wraps this with retry, publication, and metrics.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 10 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx, log).


##### `TurnEngine._park`  (lines 2859–2897)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Puts a running turn into a resumable parked state when spending or access limits stop it.

**Data flow**: It receives the park reason and usage so far → updates the turn to parked, bills consumed usage, releases claimed arrivals, then publishes a Parked frame and metrics if the update succeeded.

**Call relations**: TurnEngine.run calls it after catching TurnParked from _enforce_spend.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 2899–2908)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes one live frame without letting publish failures break the turn.

**Data flow**: It receives a live frame → sends it through the hub → logs any exception and otherwise changes nothing.

**Call relations**: Many helpers use this for deltas, replies, absorbed notices, cost ticks, terminal frames, tool activity, and parked notices.

*Call graph*: called by 8 (_absorb_arrivals, _commit, _dispatch_step, _park, _publish_cost, _speak, _stream_closing_spans, run); 1 external calls (log).


##### `TurnEngine._publish_run`  (lines 2910–2943)

```
async def _publish_run(self, activity: ToolCall | SkillLoad | None=None, status: str='') -> None
```

**Purpose**: Mirrors subagent activity onto the root turn stream that user interfaces follow.

**Data flow**: It receives optional tool or skill activity and optional status → if this is a subagent with lineage, builds a SubagentActivity frame → publishes it to the root turn hub stream.

**Call relations**: TurnEngine.run announces subagent start, _dispatch_step announces subagent tool activity, and _commit announces subagent terminal status.

*Call graph*: called by 3 (_commit, _dispatch_step, run); 2 external calls (__init__, log).


##### `TurnEngine._stop_sandbox_commands`  (lines 2945–2961)

```
async def _stop_sandbox_commands(self) -> None
```

**Purpose**: Stops sandbox commands left running after a deliberate workflow cancellation.

**Data flow**: It asks the sandbox to stop commands → logs errors without changing the already-durable cancellation result.

**Call relations**: TurnEngine.run and run_intent call it only on DBOS workflow cancellation, not ordinary executor preemption.

*Call graph*: called by 2 (run, run_intent); 1 external calls (log_error).


##### `TurnEngine._bill_cancelled`  (lines 2963–2983)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for model tokens consumed before a cancellation or preemption.

**Data flow**: It totals usage events → tries to record usage in the database → logs any failure without blocking cancellation.

**Call relations**: TurnEngine.run calls it in cancellation and preemption paths so spent tokens are not ignored.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 2985–2990)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this execution did not win ownership of the turn.

**Data flow**: It builds a TranscriptRepair helper → asks it to republish a committed terminal if one exists → returns that frame or none.

**Call relations**: TurnEngine.run and run_intent call it immediately after _mark_running fails.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 2992–2995)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Delegates successful transcript persistence to TranscriptRepair.

**Data flow**: It receives messages, answer, system prompt, and injected context → creates the repair helper → asks it to persist the full transcript.

**Call relations**: TurnEngine.run and run_intent call this after committing and before finishing their cleanup.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_inbound`  (lines 2997–3002)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Delegates non-success inbound-message preservation to TranscriptRepair.

**Data flow**: It receives optional absorbed arrival messages and an optional founding denial → creates the repair helper → asks it to write inbound-only transcript state.

**Call relations**: TurnEngine.run calls this on failed, cancelled, parked, and non-done terminal paths where the assistant answer should not be written as normal conversation.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).

## 📊 State Registers Touched

- `reg-database-store` — The shared database connection and tables where workspaces, users, agents, turns, files, jobs, costs, and extension data are saved.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-inbound-message-queue` — The saved holding area for incoming chat messages before they are admitted into a running or queued turn.
- `reg-turn-run-state` — The shared state of each unit of agent work, including queued, claimed, running, parked, canceled, recovered, or finished.
- `reg-runtime-fleet` — The records of running server or worker instances, their heartbeats, listener claims, and cleanup ownership.
- `reg-transcript-history` — The saved conversation transcript and compaction snapshots that all surfaces, workers, prompts, and recovery logic read and update.
- `reg-live-stream-state` — The live update stream that broadcasts turn progress, tool activity, subagent activity, cancellations, and final replies to connected clients.
- `reg-midturn-replies` — The durable outbox for replies sent before a turn is fully complete, so they can be delivered once even after retries.
- `reg-model-catalog` — The shared directory of available AI models, their providers, limits, prices, key requirements, and routing behavior.
- `reg-model-usage-accounting` — The recorded token, image, video, embedding, sandbox, egress, and cost usage used for billing and audit trails.
- `reg-spend-controls` — The workspace spending caps, prepaid balances, top-up settings, BYOK flags, and billing export state.
- `reg-tool-catalog` — The shared catalog of tools the model can call, including built-in tools, extension tools, connector tools, and their safety labels.
- `reg-tool-execution-context` — The per-turn but shared rulebook passed through tools, saying who the tool acts for, what files, accounts, sandboxes, and subagents it may use.
- `reg-sandbox-runtime` — The remembered sandbox handles, workspace directories, terminals, command sessions, ports, and cleanup state used for safe code execution.
- `reg-scheduled-jobs` — The durable background job and scheduled task state used for recurring work, wakeups, retries, monitors, billing, and offline evaluation.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-observability-traces` — The shared trace, metric, log, and traceparent information that lets operators connect startup, turns, tools, subagents, and billing events.
- `reg-active-cancellation-handles` — Process-local abort tokens and cancellation handles that bridge durable cancel requests to currently running turns, tools, sandboxes, and child turns.
- `reg-prompt-render-audit` — Rendered-prompt fingerprints, template provenance, and compaction/prompt hashes used to trace or reproduce the exact context sent to models.
- `reg-turn-surface-context` — Durable per-turn inbound context such as speaker, on-behalf-of member, timezone, original surface metadata, and connection-authorization status carried from admission into prompting, execution, and delivery.
- `reg-admission-ordering-locks` — Conversation-level admission and serialization locks/cursors that prevent concurrent messages, starts, stops, or queued turns from racing before durable turn execution begins.
