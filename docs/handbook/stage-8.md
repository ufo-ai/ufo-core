# Turn queue claiming, recovery, and context assembly  `stage-8`

This stage is the main doorway into doing one unit of conversation work, called a turn. It makes sure only one turn runs at a time for the same conversation, so replies do not collide. It also makes the work durable, meaning it is recorded safely enough that a crash can be noticed and repaired later.

The queue is the gatekeeper. It finds a runnable turn, claims it, restores failed or interrupted work, opens the right sandbox, and makes sure the turn ends with either a result or a saved failure. The engine is the traffic controller. After a turn is claimed, it gathers the conversation history, user and member permissions, tools, skills, memory, goals, model choice, and spending limits. It then runs the model, streams updates, executes tools, records costs, and saves the final state.

Before the model call, prompt construction packs the instructions and conversation history into a size the model can read. Context compaction summarizes older material when needed. Subagent and objective injection adds the current helper catalog and ongoing goals, so the agent can delegate work and continue unfinished plans.

## Sub-stages

- [Prompt construction and context compaction](stage-8.1.md) `stage-8.1` — 3 files
- [Subagent and objective context injection](stage-8.2.md) `stage-8.2` — 2 files

## Files in this stage

### Turn Queue and Execution
Durable queue claiming hands runnable turns to the turn engine, which assembles context, runs model/tool work, records outcomes, and coordinates recovery.

### `core/src/ufo/loop/queue.py`

`orchestration` · `turn execution`

This file is the bridge between “a turn was requested” and “the agent actually ran.” A turn may involve a language model, tools, credentials, a sandboxed workspace, subagents, transcript storage, and live updates to the user interface. Without this file, queued turns would not be claimed safely, resumed after crashes, isolated by workspace, or reported back when they finish.

The core idea is like a ticket counter with separate lines for each conversation. DBOS provides the durable queue and workflow system, so if the process crashes, the turn can be replayed from safe checkpoints instead of being lost. The file stores a single process-wide Runtime, which contains all the services needed to run turns: model registry, sandbox opener, credential store, tool registry, blob storage, event hub, and more.

When a workflow starts, _execute_turn binds the workspace, loads basic turn data, applies extension-provided agent setup, opens tracing, and calls _run_turn. _run_turn is the main assembly line. It claims the turn, loads the agent and audience, chooses tools, prepares skills and prompts, opens the sandbox with the right environment variables, builds a TurnEngine, and runs it. If the turn is a subagent child, the result is delivered back to the parent conversation after completion.

A key safety feature is _commit_failed_terminal. If anything fails before the engine can write a final answer, this backstop records a failed terminal state and publishes it, so clients waiting for the turn are not left hanging forever.

#### Function details

##### `_agent_tools`  (lines 113–136)

```
def _agent_tools(all_tools: tuple[ToolDef, ...], allowed: tuple[str, ...] | None, admission: TurnAdmissionSource) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses which tools a normal agent is allowed to see for this turn. It protects special tools so they are only available when an agent or admission path is supposed to have them.

**Data flow**: It receives the full live tool list, an optional allowlist of tool names from the agent, and the way the turn was admitted. If there is no allowlist, or if this is a prepared intent that does not let the model freely choose tools, it returns only the member-facing tools. If there is an allowlist, it returns the live tools whose names are on that list.

**Call relations**: _run_turn calls this while building the ToolRegistry for a main agent turn. Its output becomes the exact set of tools the model can call during that turn.

*Call graph*: called by 1 (_run_turn).


##### `_resolve_profile`  (lines 139–154)

```
def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile
```

**Purpose**: Finds the registered profile for a named subagent. If the profile has disappeared, it logs useful details before raising the error.

**Data flow**: It receives a subagent registry, the turn id, and the requested profile name. It asks the registry for that profile. On success, it returns the profile object. On failure, it logs the missing name and the currently registered names, then lets the failure continue.

**Call relations**: _run_turn calls this when the turn is for a subagent. It delegates the lookup to SubagentRegistry.get and uses log_error so operators can understand which extension/profile mismatch caused the turn to fail.

*Call graph*: calls 1 internal fn (get); called by 1 (_run_turn); 1 external calls (log_error).


##### `_subagent_tools`  (lines 157–168)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Builds the tool set for a subagent based on its profile and any extra grants it has received. This keeps delegated agents limited to the tools they were designed to use.

**Data flow**: It receives all live tools, a subagent profile, and granted tool names. It starts with the profile’s declared tool names. If the profile is not isolated, it adds granted tools and also includes tools marked as default for subagents. It returns only the selected tools.

**Call relations**: _run_turn calls this when preparing a subagent turn. The resulting tools are wrapped in a ToolRegistry and passed into the TurnEngine.

*Call graph*: called by 1 (_run_turn).


##### `_apply_provisions`  (lines 174–183)

```
async def _apply_provisions(runtime: 'Runtime', workspace_id: UUID) -> None
```

**Purpose**: Applies extension-shipped agent setup to a workspace once per process. This lets new or existing workspaces receive agents provided by active extensions without doing the work on every turn.

**Data flow**: It receives the Runtime and a workspace id. If this process has already provisioned that workspace, it does nothing. Otherwise, it creates an AgentProvisioning helper from the active manifests, applies it to the workspace, and remembers that the workspace has been handled.

**Call relations**: _execute_turn calls this near the start of a turn, after the workspace is known. It is intentionally idempotent, so repeated calls are safe even if startup or onboarding also provisioned the workspace.

*Call graph*: called by 1 (_execute_turn); 1 external calls (__init__).


##### `init_runtime`  (lines 222–226)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the process-wide Runtime used by queued turn execution. It prevents accidental double initialization, which would make the process use an unclear mix of services.

**Data flow**: It receives a Runtime object containing all dependencies needed to run turns. If no runtime is installed, it stores it in the module-level variable. If one is already installed, it raises an error.

**Call relations**: The serving process is expected to call this before any workflow runs. Later, _execute_turn reads this stored Runtime to access the database client, model registry, sandbox service, tools, credentials, and other services.


##### `reset_runtime`  (lines 229–234)

```
def reset_runtime() -> None
```

**Purpose**: Clears the installed Runtime so tests can install a fresh one. Production serving code normally does not use this.

**Data flow**: It takes no input. It sets the module-level runtime variable back to None. After that, init_runtime can be called again.

**Call relations**: This is a testing seam around the single-runtime rule enforced by init_runtime. It does not participate in normal turn execution.


##### `_execute_turn`  (lines 237–289)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs the outer workflow body for a queued turn. It sets the workspace context, performs safe setup, calls the main turn runner, records failures if setup breaks, and tries to deliver subagent results afterward.

**Data flow**: It receives workspace and turn ids as strings from the durable workflow. It converts them to UUIDs, checks that Runtime is installed, binds all work to the workspace, loads basic turn fields, applies provisions, opens tracing context, and calls _run_turn. If an unexpected error happens, it writes a failed terminal state. Finally, it asks _deliver_to_parent to forward any completed child result, and returns a status string.

**Call relations**: turn_workflow calls this as the DBOS workflow body. It calls _apply_provisions before the actual run, _run_turn for the main execution, _commit_failed_terminal as a backstop, and _deliver_to_parent after the turn reaches a terminal state.

*Call graph*: calls 4 internal fn (_apply_provisions, _commit_failed_terminal, _deliver_to_parent, _run_turn); called by 1 (turn_workflow); 6 external calls (select, agent, workspace_tx, turn_span, ws, UUID).


##### `_deliver_to_parent`  (lines 292–324)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: For a completed subagent turn, sends its durable final result back to the parent conversation that spawned it. It avoids changing the child turn if delivery itself fails.

**Data flow**: It receives the Runtime and a child turn id. It reads the turn row from the database. If the turn has no parent or no terminal result, it stops. Otherwise, it validates the database row into a Turn object and asks SubagentResult to deliver it. If delivery fails, it logs that delivery is deferred and leaves the already-finished child turn unchanged.

**Call relations**: _execute_turn calls this after every turn attempt. It creates a SubagentResult using Runtime.invoker_for and the subagent registry, so parent conversations can receive child outputs without the child runner needing to keep the result in memory.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_enqueue_handoff`  (lines 327–362)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: Queues another workflow when a turn claim discovers work that must be handed off. It keeps conversation ordering by partitioning the queued work by conversation id.

**Data flow**: It receives the DBOS client, workspace id, turn id, conversation id, and workflow id. It builds enqueue options with the turn queue name, workflow name, workflow id, app version, and conversation partition key. If enqueue succeeds, the handoff is scheduled. If enqueue is cancelled or fails, it clears the turn’s dispatch marker so the turn can be retried later, and logs non-cancellation failures.

**Call relations**: _run_turn calls this after claiming a turn if the claim operation returns a handoff. It hands the actual queue insertion to DBOSClient.enqueue_async and uses the database to undo the in-progress dispatch marker on failure.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 365–592)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds and runs the complete agent turn. This is the main orchestration path that turns database state into an executing TurnEngine with the right model, tools, sandbox, prompt, credentials, skills, subagents, and recovery behavior.

**Data flow**: It receives the Runtime and a turn id string. It claims the turn, possibly enqueues a handoff, loads the full turn, agent, and audience, finds lineage and previous timing, loads extension tools/hooks/skills, chooses different setup for a main agent versus a subagent, opens the sandbox, mounts preloaded skills, constructs the TurnEngine, and runs either an intent path or the normal model loop. It returns a status such as completed, failed, superseded, or parked. If an unexpected failure escapes setup or engine construction, it writes a failed terminal state.

**Call relations**: _execute_turn calls this to perform the real work. It calls helper functions in this file for tool filtering, profile lookup, turn loading, lineage, previous-turn timing, sandbox opening, handoff enqueueing, and failure recording. It also wires in many outside subsystems, then hands execution to TurnEngine.run or TurnEngine.run_intent.

*Call graph*: calls 9 internal fn (_agent_tools, _commit_failed_terminal, _enqueue_handoff, _load_turn, _open_sandbox, _previous_turn_ended_at, _resolve_profile, _run_lineage, _subagent_tools); called by 1 (_execute_turn); 28 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 401–405)

```
def subagents_for(acting_member_id: UUID | None) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates an authorized subagent helper for a specific acting member. This lets the engine spawn or interact with subagents while preserving who is acting.

**Data flow**: It receives an optional member id. It asks the current Subagents object to authorize actions for that member. It returns both the spawn function and the authorized Subagents wrapper.

**Call relations**: _run_turn defines this small closure while building the TurnEngine. The engine can call it later when a tool or workflow needs subagent access for a particular member identity.


##### `_commit_failed_terminal`  (lines 595–656)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Records and publishes a failed final state when a turn fails before the normal engine can do so. This prevents users or callers from waiting forever for a turn that has already broken.

**Data flow**: It receives the event hub, turn id, and the error. It creates a TerminalFrame containing the failure status, error class, and shortened error message. In a retry loop, it tries to update the database only if the turn is still queued or running. If that update is the actual transition to failed, it emits a metric and logs the stack trace. It then publishes the terminal event to the hub. If writing or publishing fails, it waits and retries with increasing delay.

**Call relations**: _execute_turn and _run_turn call this when an exception escapes their normal path. It uses the database for durable status, observability helpers for metrics/logs, and Hub.publish so listeners are notified of the failure.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 10 external calls (__init__, __init__, sleep, update, workspace_tx, emit_metric, formatted_stack, log, log_error, turn_profile).


##### `turn_workflow`  (lines 660–661)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Defines the durable DBOS workflow entry for running one queued turn. It is the function the queue invokes.

**Data flow**: It receives workspace and turn ids as strings from DBOS. It passes them directly to _execute_turn and returns the resulting status string.

**Call relations**: DBOS calls this workflow when a turn is dequeued from TURN_QUEUE. It immediately delegates to _execute_turn, which contains the actual setup, execution, and cleanup flow.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 664–744)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Reads all database information needed to run a turn and converts it into plain application objects. It combines turn, agent, conversation, and audience data into one usable bundle.

**Data flow**: It receives a turn UUID. It queries the database for turn fields, the linked agent fields, and the linked conversation audience. It builds a Turn object, an Agent object, and parses the audience field into an Audience value. It returns those three objects.

**Call relations**: _run_turn calls this after claiming a turn, and also when a claim is superseded and transcript repair may be needed. It relies on workspace_tx for the database transaction and parse_audience for interpreting the stored audience.

*Call graph*: called by 1 (_run_turn); 7 external calls (__init__, __init__, model_validate, model_validate, select, parse_audience, workspace_tx).


##### `_run_lineage`  (lines 747–778)

```
async def _run_lineage(turn: Turn) -> RunLineage | None
```

**Purpose**: Finds where a spawned turn should publish its live activity. For child turns, it traces back to the root turn so user-facing streams can show nested work under the original request.

**Data flow**: It receives a Turn. If the turn has no parent, it returns None. If it is a child, it follows parent_turn_id links in the database until it finds the root turn. It also determines a profile label, using the subagent profile when present or the agent name for agent-child turns. It returns a RunLineage object with the root id, parent id, profile, and subagent name.

**Call relations**: _run_turn calls this before constructing the TurnEngine. The resulting lineage is passed into the engine so progress events from child work are published in the right place.

*Call graph*: called by 1 (_run_turn); 3 external calls (__init__, select, workspace_tx).


##### `_previous_turn_ended_at`  (lines 781–793)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: Finds when the previous turn in the same conversation ended. This gives the engine timing context for a continuing conversation.

**Data flow**: It receives a Turn. If this is the first turn in the conversation, it returns None. Otherwise, it queries the database for the updated_at time of the immediately previous sequence number. If the timestamp has no timezone, it marks it as UTC, then returns it.

**Call relations**: _run_turn calls this for non-intent turns before building the TurnEngine. The timestamp is passed into the engine as previous_turn_ended_at.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_open_sandbox`  (lines 796–854)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens or attaches to the sandbox container where the turn’s commands and file work run. It also prepares environment variables for run identity, git proxy authentication, credentials, grants, and provider keys.

**Data flow**: It receives the sandbox service, token encoder, turn, optional grant store, connector CLI definitions, optional credential store, credential slots, and a cache-rewrite flag. It creates a signed run token for the turn, builds git configuration and credential environment variables, adds grant and provider-key variables, and opens the sandbox for the conversation that owns the workspace files. It returns a SandboxSession.

**Call relations**: _run_turn calls this during sandbox setup. It delegates token creation to RunTokenCodec.encode, environment construction to sandbox exec-env helpers, optional git cache setup to cache_git_config, and actual sandbox access to ConversationSandbox.open.

*Call graph*: calls 2 internal fn (open, encode); called by 1 (_run_turn); 6 external calls (__init__, cache_git_config, _git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env).


##### `SandboxAuthorizer.authorize`  (lines 865–877)

```
async def authorize(self, acting_member_id: UUID | None) -> SandboxSession
```

**Purpose**: Re-authorizes an existing sandbox session for a specific acting member. This lets tools run with the right member identity and grants without opening a new sandbox.

**Data flow**: It receives an optional acting member id. It creates a new run token containing the workspace, turn, and acting member, builds updated grant-related environment variables, and asks the existing SandboxSession to authorize itself with the new token while replacing connector CLI variables. It returns the authorized SandboxSession.

**Call relations**: _run_turn creates a SandboxAuthorizer and passes its authorize method into the TurnEngine for normal turns. When the engine needs sandbox access for a member-specific action, this method refreshes the token and grant environment before handing the sandbox session back.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### `core/src/ufo/loop/engine.py`

`orchestration` · `turn execution`

A “turn” is one unit of work where an agent responds to an incoming prompt or prepared tool intent. This file makes that turn safe and orderly. It first claims ownership in the database so two workers do not answer the same turn. It loads the prior transcript, adds context such as time and sender, applies extension hooks, then loops through model rounds. In each round it checks spending and seat permissions, optionally compacts old conversation history, streams the model’s text live, collects tool calls, runs those tools, and feeds the results back to the model. If new user messages arrive while the agent is working, it absorbs them between rounds so the final answer does not ignore them. The file also treats tool calls like careful workshop jobs: safe ones can run side by side, risky ones run in order, large outputs are written to files, images are stored outside the step log, and untrusted text is fenced off so the model reads it as data rather than instructions. The engine is built for replay. Expensive or side-effecting pieces are DBOS steps, meaning after a crash they can be replayed from recorded results instead of repeated. Finally, it commits the terminal frame, bills usage, writes the transcript, publishes live events, parks the turn if spending caps are hit, and cleans up on cancellation.

#### Function details

##### `_claim_turn`  (lines 219–266)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued or parked turn for one running attempt, or reclaims it if this same attempt is being replayed after a crash. This prevents two workers from doing the same conversational work at once.

**Data flow**: It receives a turn id and attempt id, reads the turn and its conversation, locks the conversation row, and tries to mark the turn as running under that attempt. It returns whether the claim was fresh, adopted from the same attempt, or not won at all.

**Call relations**: TurnEngine._mark_running uses this at the start of normal and intent turns. _claim_turn_with_handoff also uses it before deciding whether to enqueue the next turn in the same conversation.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 277–337)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[str | None, _TurnHandoff | None]
```

**Purpose**: Claims the current turn and, if possible, marks the next queued turn in the same conversation as ready to dispatch. It is a handoff helper for keeping conversation work moving in order.

**Data flow**: It takes a turn id and attempt id, first tries to claim the turn, then looks for the next queued turn in that conversation. If one is found and has not already been stamped for dispatch, it stamps it and returns a small handoff record for the dispatcher.

**Call relations**: It builds on _claim_turn. After a successful claim, it uses database locks and a generated workflow id when needed so the next turn can be picked up safely.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `_RoundInput.__repr__`  (lines 414–419)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug-friendly label for a model round input without printing the full conversation. This keeps logs readable and avoids dumping large or sensitive message content.

**Data flow**: It reads the stored messages, system prompt length, and round flags, then produces a compact string showing counts and booleans.

**Call relations**: DBOS and logging may display this object when _stream_once is recorded or inspected, so the representation helps explain what kind of round ran.


##### `_BoundToolCall.__repr__`  (lines 427–428)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact label for a tool call that has already been tied to a tool context. It helps logs show which tool and call id are involved.

**Data flow**: It reads the tool call name and id and returns a short string containing those values.

**Call relations**: Bound calls flow from TurnEngine._bind_or_error into TurnEngine._dispatch_step, and this representation makes that flow easier to inspect.


##### `_RejectedToolCall.__repr__`  (lines 438–442)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact label for a tool call that could not be prepared. It shows the tool name, call id, outcome, and error class without including bulky details.

**Data flow**: It reads the rejected call and its stored error information, then returns a short string for diagnostics.

**Call relations**: Rejected calls are produced by TurnEngine._bind_or_error and consumed by TurnEngine._dispatch_step as recorded error results.


##### `ModelStreamError.__init__`  (lines 483–484)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Stores the provider’s model-stream failure details in a form that can survive workflow persistence. It keeps the original error class, message, and any partial text that had already streamed.

**Data flow**: It receives an error class, message, and optional partial output, and places all three into the exception arguments.

**Call relations**: TurnEngine._stream_recovering_overflow creates this after _stream_once reports an error in its recorded StreamResult.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 486–488)

```
def __str__(self) -> str
```

**Purpose**: Formats the model-stream failure as a readable error string. It intentionally leaves out partial output so terminal errors do not expose large salvaged content.

**Data flow**: It reads the stored error class and message and combines them into one string.

**Call relations**: Any code that logs or displays the exception uses this when a model stream error escapes the round.


##### `ModelStreamError.model_error_class`  (lines 491–493)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original class name of the model provider error. This lets the engine distinguish truncation or context overflow from other failures.

**Data flow**: It reads the first stored exception argument and returns it.

**Call relations**: TurnEngine._model_round and TurnEngine._commit_once use this kind of information to recover when possible or report the right terminal error.


##### `ModelStreamError.partial_output`  (lines 496–498)

```
def partial_output(self) -> str
```

**Purpose**: Returns text and partial tool-call data that arrived before the model stream failed. This can be saved so the model can salvage work instead of starting over.

**Data flow**: It reads the third stored exception argument and returns it.

**Call relations**: TurnEngine._model_round uses it when a model response was truncated and writes it to a workspace file if possible.


##### `ModelStreamError.model_error_message`  (lines 501–503)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original message from the model provider. This preserves useful detail for terminal frames and diagnostics.

**Data flow**: It reads the second stored exception argument and returns it.

**Call relations**: TurnEngine._commit_once uses this when turning a failed model stream into the final error information for the turn.


##### `TurnParked.__init__`  (lines 510–512)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates an exception meaning the turn should pause, not fail, because a spending or seat limit was reached. The message is what users see in the parked notification.

**Data flow**: It receives a human-readable reason, stores it on the exception, and passes it to the base exception.

**Call relations**: TurnEngine._enforce_spend raises it. TurnEngine.run catches it, records usage, parks the turn, and publishes the park reason.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 521–545)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits model-requested tool calls into groups that can safely run together. This preserves the order the model chose while still allowing parallel execution for tools marked safe.

**Data flow**: It receives the tool registry and a tuple of tool calls, checks each tool’s parallel-safety setting, and yields ordered groups. Unknown or unsafe tools become single-call barriers.

**Call relations**: TurnEngine._model_round calls it before dispatching tools so safe batches can run concurrently and ordered calls remain ordered.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 548–550)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed fragments of a tool-call JSON argument into a Python dictionary. Empty argument text becomes an empty input object.

**Data flow**: It receives a list of partial JSON strings, joins them, parses the result if non-empty, and returns the parsed dictionary.

**Call relations**: TurnEngine._stream_once uses it after the model stream finishes to build ToolUseBlock objects from tool-call deltas.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 553–571)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the metadata tag that tells the model when, from whom, and from where a user message arrived. This gives the model useful context without relying on the current clock.

**Data flow**: It receives a message id, optional turn context, and admitted time, formats the time in the sender’s timezone when known, and returns a text tag.

**Call relations**: TranscriptRepair.load_messages adds it to the founding inbound message. TurnEngine._render_arrival adds it to later messages absorbed mid-turn.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 574–579)

```
def _bounded(content: str) -> str
```

**Purpose**: Shortens very large tool text to a fixed maximum and adds a notice saying how much was cut. This protects the model context from being overwhelmed by one result.

**Data flow**: It receives text and either returns it unchanged or returns the prefix plus a truncation marker.

**Call relations**: TurnEngine._dispatch_step uses it for tool errors or offload fallback. TurnEngine._model_round uses it when giving the model finish-tool validation errors.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_meter_dispatch`  (lines 582–609)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str) -> None
```

**Purpose**: Records metrics for one tool dispatch, including which tool ran, how long it took, and how it ended. This helps operators see slow tools and failure patterns.

**Data flow**: It receives the registry, tool call, start time, outcome, optional error class, and profile. It normalizes unknown tool names, then emits count and timing metrics.

**Call relations**: TurnEngine._bind_or_error records preparation failures through it. TurnEngine._dispatch_step records the final outcome for actual dispatch work.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 612–654)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedSkill, ...]]
```

**Purpose**: Finds which skill instruction bundles are already present in the current message window. This avoids reloading the same skill instructions unnecessarily.

**Data flow**: It scans messages for completed load-skill calls, checks their clean text results, asks the skill registry for each requested skill’s closure, and yields those closures.

**Call relations**: TurnEngine._reseed_loaded_skills uses it to keep the compaction skill tracker accurate after messages change.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 657–675)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts the structured payload from a successful final tool action such as asking the user, requesting credentials, or connecting an account. It trusts the tool result, not the raw model arguments.

**Data flow**: It receives the round’s tool calls and results, the expected tool name, and a validation model. If the last call and result match, it parses and validates the JSON payload; otherwise it returns nothing.

**Call relations**: TurnEngine._model_round uses it after tool dispatch rounds. TurnEngine.run_intent uses it after direct intent dispatch to populate terminal requests.

*Call graph*: called by 2 (_model_round, run_intent); 1 external calls (loads).


##### `_total_usage`  (lines 678–685)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many model usage records into one total. This gives billing, cost display, and spend checks a single number to work with.

**Data flow**: It receives a list of usage events, sums each token category, and returns one Usage object.

**Call relations**: Billing, commit, parking, cost publishing, spend enforcement, and model-round metrics all call it when they need the turn’s accumulated token use.

*Call graph*: called by 6 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost, _stream_once); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 700–724)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal result for a turn that is already finished but whose client may still be waiting. It is a repair path for duplicate deliveries or crashes after commit.

**Data flow**: It reads the turn’s stored terminal frame. If one exists, it persists inbound transcript data if needed, publishes the terminal frame, and returns it; otherwise it returns nothing.

**Call relations**: TurnEngine._resolve_unclaimed calls this when the current execution fails to claim the turn because another execution owns it or it already ended.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 726–733)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the full completed conversation for a successful answer. It appends the assistant’s final answer to the message history.

**Data flow**: It receives messages, answer, system prompt, and injected context, creates a final assistant message, and hands the whole conversation to write_conversation.

**Call relations**: TurnEngine._persist_transcript delegates here after a done terminal is committed.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 735–754)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Writes only the user-side messages when the turn ends without a normal assistant answer. This preserves what users said so the next turn can still see it.

**Data flow**: It receives optional absorbed arrivals and optional founding denial text. It builds the founding message set, appends arrivals, and writes that conversation without assistant error text.

**Call relations**: TranscriptRepair.resolve uses it during terminal repair, and TurnEngine._persist_inbound uses it on failed, cancelled, or non-done exits.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 756–765)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads prior conversation history and adds this turn’s inbound message. For normal member turns, it prefixes the inbound text with a context tag.

**Data flow**: It reads the turn’s inbound text and prior transcript messages, optionally adds a context tag, and returns the combined message tuple.

**Call relations**: TurnEngine._load_messages uses this at turn startup. TranscriptRepair.persist_inbound can also use it when preserving inbound messages.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 767–773)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the transcript that came before this turn. It avoids reading this turn’s own write during replay.

**Data flow**: It asks the transcript store for the stored conversation, checks its sequence number, and returns either its messages or an empty tuple.

**Call relations**: TranscriptRepair.load_messages and persist_inbound call it while assembling what should be written or shown to the model.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 775–795)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a conversation snapshot to durable transcript storage with a few retries. This makes transcript writes more tolerant of temporary storage failures.

**Data flow**: It receives messages plus optional system and injected text, wraps them in a Conversation object, tries to write, logs failures, and sleeps before retrying.

**Call relations**: TranscriptRepair.persist_transcript and persist_inbound both use this as the final write operation.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 829–846)

```
def exited(self, status: str) -> None
```

**Purpose**: Records how long this execution ran and how many model rounds it used. It only records once, even if cleanup paths call it again.

**Data flow**: It receives an exit status, compares current time to its start time, emits duration and round-count metrics, and marks itself ended.

**Call relations**: TurnEngine._commit calls it after a terminal is reached. TurnEngine.run and run_intent also call it directly for parked, cancelled, or preempted paths.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 915–926)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the engine was wired together consistently before a turn starts. It prevents audience mismatches and forbids a subagent tool set from already defining the reserved finish tool.

**Data flow**: It reads tool extension contexts, hook audience, output contract, and tool registry. If something is inconsistent, it raises an error; otherwise construction continues.

**Call relations**: Dataclass initialization invokes this automatically when a TurnEngine is created by higher-level workflow setup.


##### `TurnEngine.__repr__`  (lines 928–932)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short readable label for this engine instance. It helps logs and debugging identify the turn, agent, and profile.

**Data flow**: It reads the turn id, agent id, and computed profile, then returns a compact string.

**Call relations**: Debugging and framework logging may call it whenever the engine object is printed.


##### `TurnEngine.profile`  (lines 935–938)

```
def profile(self) -> str
```

**Purpose**: Computes the telemetry profile name for this turn, such as main, agent, or a subagent profile. Metrics use this to separate different kinds of work.

**Data flow**: It reads whether the turn is spawned and its subagent profile, passes them to the profile helper, and returns the resulting string.

**Call relations**: Most metric and log paths in TurnEngine read this property when labeling turn, model, and tool activity.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 940–1120)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal chat-style turn from claim to final terminal. This is the main body for model-driven agent work.

**Data flow**: It starts metrics, builds a ToolContext, claims the turn, loads and filters inbound text, then repeatedly calls _model_round until an answer can be committed. It records transcripts and workspace changes, parks on spend limits, bills on cancellation, releases unabsorbed arrivals on failure, and cleans up tool context resources.

**Call relations**: This is the top-level orchestration path. It calls the claim, prompt hook, scheduled memory, model-round, commit, transcript, park, billing, release, sandbox-stop, and publish helpers as the turn moves through its lifecycle.

*Call graph*: calls 15 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _publish_run, _record_workspace_changes (+5 more)); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, escape, monotonic, emit_metric, log (+1 more)).


##### `TurnEngine.run.rank_find`  (lines 957–975)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Provides the browser find tool with a small host-side model call that ranks elements. It lets browser tooling ask the model for help without running that logic inside the sandbox.

**Data flow**: It receives a system prompt and user prompt, sends a no-reasoning model request with a smaller token limit, appends text deltas into a result string, and adds usage events to the turn’s shared usage list.

**Call relations**: TurnEngine.run places this function into ToolContext as find, so tools can call it while their usage is still charged to the current turn.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine.run_intent`  (lines 1122–1230)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a prepared intent that directly asks for one tool call, without asking the model to decide anything. This is used when a UI panel submits exact structured values.

**Data flow**: It claims the turn, parses the inbound intent, creates one tool call, binds requester authority, dispatches it, commits success or refusal, detects credential/connect final actions, writes the transcript, and cleans up.

**Call relations**: It shares binding, dispatch, commit, transcript, and cancellation helpers with TurnEngine.run, but skips model rounds and turn-level prompt/stop hooks.

*Call graph*: calls 9 internal fn (_bind_or_error, _commit, _dispatch_step, _load_messages, _mark_running, _persist_transcript, _resolve_unclaimed, _stop_sandbox_commands, _final_act); 10 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, monotonic, emit_metric, log).


##### `TurnEngine._scheduled_system`  (lines 1232–1266)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. This gives scheduled work relevant past context even though no member is actively chatting.

**Data flow**: It receives the current system prompt, searches memory with a timeout, escapes any recalled text, and appends a recalled-memory section if matches exist. On search failure it logs and returns the original prompt.

**Call relations**: TurnEngine.run calls this only for scheduled admissions before the first model round.

*Call graph*: called by 1 (run); 5 external calls (__init__, timeout, escape, audience_subjects, log).


##### `TurnEngine._mark_running`  (lines 1268–1276)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn as owned by the current workflow attempt. It is the engine’s local wrapper around the database claim operation.

**Data flow**: It sends the turn id and attempt id to _claim_turn and returns true if a claim was won.

**Call relations**: TurnEngine.run and run_intent call it before doing any real work. If it fails, they switch to _resolve_unclaimed.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 1278–1279)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Builds a TranscriptRepair helper for this engine’s turn. This keeps transcript repair and persistence code separate from the main engine.

**Data flow**: It reads the engine’s turn, transcript, and hub, and returns a TranscriptRepair object.

**Call relations**: The engine’s transcript-loading, transcript-persisting, and unclaimed-resolution helpers all create this repair object through _repair.

*Call graph*: called by 5 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed, run); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 1281–1283)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the conversation messages for the model while wrapping the operation in an observability span. It is the engine’s simple entry point for transcript loading.

**Data flow**: It creates a repair helper and asks it to load messages, returning the resulting tuple.

**Call relations**: TurnEngine.run and run_intent use it when building the initial transcript window.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 1285–1477)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the repeated model/tool loop until the model gives a final answer or a forced final answer is needed. This is the heart of agent reasoning during a normal turn.

**Data flow**: It receives context, current messages, usage storage, system prompt, arrival logs, absorbed ids, active requesters, and a meter. It absorbs new arrivals, enforces spend, compacts history, streams one model round, speaks marked mid-turn replies, dispatches tools, extracts final tool actions, and updates the message window. It returns final messages, answer text, and any pending structured request.

**Call relations**: TurnEngine.run calls this inside its commit loop. This method calls arrival claiming, spend checking, compaction, model streaming, tool binding and dispatch, cost publishing, final forcing, and reply streaming helpers.

*Call graph*: calls 15 internal fn (_absorb_arrivals, _bind_or_error, _dispatch, _enforce_spend, _force_final, _force_finish, _offload, _publish_cost, _reseed_loaded_skills, _speak (+5 more)); called by 1 (run); 8 external calls (__init__, __init__, __init__, gather, marked_replies, emit_metric, log, span).


##### `TurnEngine._absorb_arrivals`  (lines 1479–1539)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Adds newly queued inbound messages into the current model window between rounds. This keeps the agent from answering while ignoring messages that arrived mid-turn.

**Data flow**: It receives current messages plus mutable logs of arrivals and absorbed ids. It claims arrivals, turns each admitted or denied arrival into a user message, updates requester tracking, publishes absorbed member ids, and returns the enlarged message tuple.

**Call relations**: TurnEngine._model_round calls it at the start of every round. It relies on _claim_arrivals for the durable queue drain and _publish for live absorbed frames.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); called by 1 (_model_round); 5 external calls (__init__, __init__, __init__, escape, log).


##### `TurnEngine._speak`  (lines 1541–1593)

```
async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None
```

**Purpose**: Delivers marked mid-turn reply spans to members before the whole turn is done. This lets an agent say specific things during tool-heavy work.

**Data flow**: It receives marked replies and the round number, writes idempotent delivery rows for non-subagent turns, logs new writes, and publishes Reply frames to the live hub.

**Call relations**: TurnEngine._model_round calls it when a tool-calling round contains marked replies. It uses _publish for live delivery and database inserts for surface pollers.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_model_round); 4 external calls (__init__, workspace_tx, log, mid_turn_reply_id_for).


##### `TurnEngine._stream_closing_spans`  (lines 1595–1605)

```
async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Sends marked reply text from the final answer back onto the live text stream. This prevents final-answer text from disappearing on stream-only clients.

**Data flow**: It receives marked replies and publishes each reply’s text as a TextDelta.

**Call relations**: TurnEngine._model_round calls it for normal final answers, and _force_final calls it for forced final answers.

*Call graph*: calls 1 internal fn (_publish); called by 2 (_force_final, _model_round); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 1607–1631)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Runs prompt-submit hooks for one queued arrival and formats the message exactly as the model should see it. Denied messages produce only safe denial text.

**Data flow**: It receives message id, body, context, speaker id, and creation time. It fires the user_prompt_submit hook, then returns either rendered content with context/injected text or a denial reason.

**Call relations**: TurnEngine._claim_arrivals calls it inside the memoized arrival-drain step so replay reuses the same rendered result instead of refiring hooks.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1634–1703)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Durably drains pending inbound messages for this conversation and records the exact batch for replay. This ensures arrivals are consumed once, not lost or duplicated.

**Data flow**: It receives ids already absorbed, stamps eligible inbound rows as consumed by this turn, sorts them, renders each one through _render_arrival, logs the batch, and returns Arrival records.

**Call relations**: TurnEngine._absorb_arrivals calls it. Because it is a DBOS step, crash recovery replays the same claimed arrivals rather than draining the queue again.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 7 external calls (__init__, model_validate, and_, or_, update, workspace_tx, log).


##### `TurnEngine._release_unabsorbed`  (lines 1705–1724)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns arrivals that were stamped by an unrecorded drain back to the pending queue after failure or cancellation. This gives the next live run another chance to read them.

**Data flow**: It receives absorbed ids, clears consumed_turn_id for this turn’s other stamped arrivals, and logs if the best-effort database update fails.

**Call relations**: TurnEngine.run calls it in failure, cancellation, and preemption paths after tracking which arrivals had truly been absorbed.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1726–1764)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, requesters: dict[UUID, ActiveMessage]) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Produces a best-effort final answer when the normal round budget is exhausted. It avoids failing the turn just because the model kept using tools too long.

**Data flow**: It receives messages, usage events, system prompt, and requesters. It records exhaustion metrics, checks spend, compacts if needed, then either forces a subagent finish tool or asks the model for one no-tool final answer.

**Call relations**: TurnEngine._model_round calls it after all allowed rounds are spent. It uses _force_finish for schema-bound subagents and _stream_recovering_overflow for normal final text.

*Call graph*: calls 5 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_closing_spans, _stream_recovering_overflow); called by 1 (_model_round); 4 external calls (__init__, marked_replies, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 1766–1791)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to close through the reserved finish tool so its output matches the required schema. This keeps parent agents from receiving malformed child results.

**Data flow**: It receives messages, usage events, and system prompt, runs one model round with only finish available and required, validates the finish arguments, and returns the updated messages plus canonical JSON answer.

**Call relations**: TurnEngine._model_round uses it when a subagent tries to stop with prose. TurnEngine._force_final uses it when a subagent exhausts its round budget.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 1793–1853)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False, active_requests: tuple[str, ...]=()
```

**Purpose**: Runs one model round and retries once after forced compaction if the provider says the context is too large. This gives oversized conversations a chance to recover automatically.

**Data flow**: It receives messages, usage storage, system prompt, tool/finish flags, active requests, and first-round flag. It calls _stream_once, adds usage, converts recorded stream errors into ModelStreamError, and on context overflow compacts and retries once.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish all call this instead of calling _stream_once directly so they get overflow recovery consistently.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 1855–1898)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks spending caps and member seat status before a model round begins. If the turn has crossed a limit, it parks the turn so work can resume later instead of being discarded.

**Data flow**: It receives accumulated usage and active requester messages, checks relevant member seats, computes pending cost, asks the spend evaluator for a decision, and raises TurnParked if the decision is not allowed.

**Call relations**: TurnEngine._model_round and _force_final call it before more model usage can happen. TurnEngine.run catches TurnParked and commits a parked state.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 6 external calls (__init__, __init__, applicable_caps_absent, audience_member, balance_absent, workspace_tx).


##### `TurnEngine._stream_once`  (lines 1901–2135)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Performs one actual model call, streams visible text live, collects tool calls and reasoning blocks, and records usage. As a DBOS step, its result can be replayed after a crash without calling the model again.

**Data flow**: It receives a _RoundInput, builds a ModelRequest with the right tools and cache settings, streams events from the model, buffers text for live publishing, records tool-call fragments, reasoning, timings, cache metrics, and usage. It returns a StreamResult, including error details and partial output if the stream failed.

**Call relations**: TurnEngine._stream_recovering_overflow is its caller. Later tool dispatch depends on the stable tool ids and tool-call order produced here.

*Call graph*: calls 2 internal fn (_parse_args, _total_usage); called by 1 (_stream_recovering_overflow); 14 external calls (__init__, __init__, __init__, __init__, __init__, Event, Lock, ensure_future, now, monotonic (+4 more)).


##### `TurnEngine._stream_once.flush`  (lines 1975–1984)

```
async def flush() -> None
```

**Purpose**: Flushes buffered streamed model text to the live hub after applying reply redaction. This keeps live output responsive without leaking marked reply spans too early.

**Data flow**: It reads the local text buffer, sends it through the redaction helper, clears the buffer and byte count, and publishes visible text if any remains.

**Call relations**: It is used inside _stream_once both when enough bytes accumulate and when the stream or pacing task reaches a flush point.

*Call graph*: 1 external calls (__init__).


##### `TurnEngine._stream_once.pace`  (lines 1986–1991)

```
async def pace() -> None
```

**Purpose**: Periodically flushes streamed text even if the buffer has not reached the byte threshold. This avoids making users wait during slow trickles of output.

**Data flow**: It waits in short intervals until the stop event is set, calling flush after each timeout.

**Call relations**: TurnEngine._stream_once starts it as a background task during the model stream and stops it when the round finishes or errors.

*Call graph*: 1 external calls (wait_for).


##### `TurnEngine._publish_cost`  (lines 2137–2151)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the turn’s current token and cost total as a live update. This lets clients show a running cost meter before the final bill is committed.

**Data flow**: It totals usage events, prices them, counts all token categories, builds a CostTick frame, and publishes it.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish call it after model rounds. It uses _publish so live delivery failure does not fail the turn.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 2153–2164)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skill instructions currently visible to the model. This prevents duplicate skill loads after compaction or message-window changes.

**Data flow**: It receives the current messages, extracts loaded skill closures, includes preloaded skills, and reseeds the compaction tracker.

**Call relations**: TurnEngine._model_round calls it before and after compaction. _stream_recovering_overflow calls it after forced compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 2 (_model_round, _stream_recovering_overflow).


##### `TurnEngine._bind_or_error`  (lines 2166–2187)

```
async def _bind_or_error(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Prepares a model tool call for dispatch, including requester authority, and turns preparation failures into tool-style error results. This keeps bad calls from crashing the whole turn unnecessarily.

**Data flow**: It receives a ToolContext, tool call, and active requesters. It tries to bind requester-specific context through _bind_requester; on success it returns a _BoundToolCall, and on ordinary failure it returns a _RejectedToolCall.

**Call relations**: TurnEngine._model_round and run_intent call it before dispatch. It uses _meter_dispatch for cancellation or preparation failures.

*Call graph*: calls 2 internal fn (_bind_requester, _meter_dispatch); called by 2 (_model_round, run_intent); 3 external calls (__init__, __init__, monotonic).


##### `TurnEngine._dispatch`  (lines 2189–2190)

```
def _dispatch(self, bound: _DispatchInput) -> Awaitable[ToolResultBlock]
```

**Purpose**: Starts a recorded tool dispatch and converts its compact stored result into the model-facing tool result. It is a small bridge between DBOS step output and conversation message format.

**Data flow**: It receives a bound or rejected dispatch input, calls _dispatch_step, and passes the awaited result into _dispatch_result.

**Call relations**: TurnEngine._model_round calls it for each prepared tool dispatch.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step); called by 1 (_model_round).


##### `TurnEngine._dispatch_result`  (lines 2192–2218)

```
async def _dispatch_result(self, step: Awaitable[DispatchResult]) -> ToolResultBlock
```

**Purpose**: Rebuilds a ToolResultBlock from a DispatchResult, including rehydrating offloaded images from blob storage. This keeps large image bytes out of the durable step log but still gives them to the model.

**Data flow**: It awaits a DispatchResult. If there are no images, it returns a text ToolResultBlock; otherwise it reads each image blob, builds image blocks, combines them with text, and returns the full result block.

**Call relations**: TurnEngine._dispatch calls it after _dispatch_step. It is the point where image references become model-visible images again.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._bind_requester`  (lines 2220–2259)

```
async def _bind_requester(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Applies per-message requester authority to a tool call. This lets a tool act on behalf of the specific member named by the model when the call includes a request reference.

**Data flow**: It copies the tool input, validates and removes the special requester field if present, finds the active message’s member, selects member-specific sandbox and subagent controls when configured, and returns an updated context plus cleaned tool call.

**Call relations**: TurnEngine._bind_or_error calls this before creating a _BoundToolCall.

*Call graph*: called by 1 (_bind_or_error); 3 external calls (model_copy, replace, UUID).


##### `TurnEngine._offload`  (lines 2261–2286)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text content into the sandbox’s tool-output directory and returns the file path. This keeps huge tool results or salvaged model output out of the prompt while still making them available.

**Data flow**: It receives a file name and content, ensures the output directory exists, writes bytes into the sandbox, logs and counts failures, and returns either the path or None.

**Call relations**: TurnEngine._dispatch_step uses it for oversized tool outputs. TurnEngine._model_round uses it to save truncated model partial output.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._dispatch_step`  (lines 2289–2470)

```
async def _dispatch_step(self, bound: _DispatchInput) -> DispatchResult
```

**Purpose**: Runs one tool call safely and records its result for crash replay. It validates input, fires hooks, executes the handler, bounds or offloads output, fences untrusted text, stores images separately, and meters the outcome.

**Data flow**: It receives either a bound call or rejected call. Rejected calls immediately become error results. Bound calls may be preempted during adoption replay if fresh member guidance is waiting; otherwise the method publishes activity, validates the tool, fires pre hooks, runs the handler, processes text and images, fires post hooks, stores image blobs, and returns a DispatchResult.

**Call relations**: TurnEngine._dispatch and run_intent call it. It calls helpers for offload, image bounding, member-guidance checks, live publishing, subagent activity, and metrics.

*Call graph*: calls 8 internal fn (_bounded_image, _offload, _pending_member_guidance, _publish, _publish_run, _redoes_on_replay, _bounded, _meter_dispatch); called by 2 (_dispatch, run_intent); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, replace, monotonic, tool_activity (+3 more)).


##### `TurnEngine._redoes_on_replay`  (lines 2472–2481)

```
def _redoes_on_replay(self, name: str) -> bool
```

**Purpose**: Decides whether re-running a tool call during crash recovery would redo non-side-effecting work and can be preempted for new member guidance. Side-effecting tools are allowed to reattach or deduplicate through their idempotency key.

**Data flow**: It receives a tool name, looks it up, and returns true only for known non-side-effecting tools. Unknown tools return false.

**Call relations**: TurnEngine._dispatch_step uses it during adoption replay before deciding whether pending member guidance should interrupt a re-executed call.

*Call graph*: called by 1 (_dispatch_step).


##### `TurnEngine._pending_member_guidance`  (lines 2483–2499)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether a member has queued a new message that no drain has consumed yet. This lets recovery avoid running ahead with stale work when guidance is waiting.

**Data flow**: It queries the inbound-message table for an unconsumed member message in this conversation and returns a boolean.

**Call relations**: TurnEngine._dispatch_step calls it only inside a live dispatch body during adoption replay.

*Call graph*: called by 1 (_dispatch_step); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 2501–2529)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Downsizes large tool-result images before sending them to the model. This avoids provider limits and wasted tokens on pixels the model will not use.

**Data flow**: It receives an ImageBlock, decodes the base64 image, checks its dimensions, thumbnails it if needed, saves it in a compatible format, and returns a new ImageBlock. If decoding or resizing fails, it logs and returns the original image.

**Call relations**: TurnEngine._dispatch_step calls it before putting successful tool images into blob storage.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 2531–2603)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Retries until the turn’s terminal result is durably written, then publishes it and records metrics. This is the final doorway from running work to a client-visible ending.

**Data flow**: It receives desired status, usage events, meter, answer or error details, optional structured requests, and arrival-guard options. It repeatedly calls _commit_once until it succeeds, publishes terminal and subagent status frames, emits terminal metrics if newly committed, records execution timing, logs, and returns the frame.

**Call relations**: TurnEngine.run and run_intent call it for done and failed outcomes. It delegates the database transaction to _commit_once and live delivery to _publish and _publish_run.

*Call graph*: calls 4 internal fn (_commit_once, _publish, _publish_run, exited); called by 2 (run, run_intent); 5 external calls (__init__, sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._record_workspace_changes`  (lines 2605–2615)

```
async def _record_workspace_changes(self) -> None
```

**Purpose**: Refreshes the list of sandbox file changes after a turn has ended. This lets the portal show what the agent changed without delaying the answer stream.

**Data flow**: It builds a WorkspaceChangeRecorder with the sandbox, workspace, and owning conversation id, then asks it to record changes.

**Call relations**: TurnEngine.run calls it after committing and writing the transcript for normal turn exits.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 2617–2721)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs the single database transaction that bills usage and writes the terminal frame. It can refuse to commit if unseen arrivals exist.

**Data flow**: It totals usage, optionally locks the conversation and checks for pending or unrecorded arrivals, records turn usage, reads billed cost, builds a TerminalFrame, and updates the turn if it is still non-terminal. If another path already ended the turn, it reads and returns the existing terminal frame.

**Call relations**: TurnEngine._commit wraps this with retry, publishing, logging, and metrics.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 10 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx, log).


##### `TurnEngine._park`  (lines 2723–2760)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Moves a turn into a resumable parked state when spending or seat rules stop it mid-run. It preserves billed usage and releases arrivals so a resumed attempt can drain them again.

**Data flow**: It receives a park message and usage events, updates the turn to parked, records usage, clears consumed arrivals for this turn, then publishes a Parked frame and emits metrics if the update matched.

**Call relations**: TurnEngine.run calls it after catching TurnParked from _enforce_spend.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 2762–2771)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes one live frame to the turn’s hub stream without letting publish failure break the turn. Durable database state remains the source of truth.

**Data flow**: It receives a live frame, tries to publish it by turn id, and logs any exception.

**Call relations**: Many helpers use it for absorbed arrivals, replies, text deltas, cost ticks, tool activity, parked notices, and terminal frames.

*Call graph*: called by 7 (_absorb_arrivals, _commit, _dispatch_step, _park, _publish_cost, _speak, _stream_closing_spans); 1 external calls (log).


##### `TurnEngine._publish_run`  (lines 2773–2806)

```
async def _publish_run(self, activity: ToolCall | SkillLoad | None=None, status: str='') -> None
```

**Purpose**: Mirrors subagent activity onto the root turn’s live stream. This lets user-facing surfaces see child-agent starts, tool calls, skill loads, and endings.

**Data flow**: It receives optional activity and status. If the turn has lineage, it extracts activity details, builds a SubagentActivity frame, and publishes it to the root turn id; otherwise it does nothing.

**Call relations**: TurnEngine.run publishes start activity, _dispatch_step publishes tool or skill activity, and _commit publishes final subagent status through this helper.

*Call graph*: called by 3 (_commit, _dispatch_step, run); 2 external calls (__init__, log).


##### `TurnEngine._stop_sandbox_commands`  (lines 2808–2824)

```
async def _stop_sandbox_commands(self) -> None
```

**Purpose**: Stops sandbox commands left running after a deliberate workflow cancellation. This prevents cancelled work from continuing in the background.

**Data flow**: It asks the sandbox to stop commands and logs any failure without changing the already-cancelled turn result.

**Call relations**: TurnEngine.run and run_intent call it on DBOS workflow cancellation, not on ordinary executor preemption.

*Call graph*: called by 2 (run, run_intent); 1 external calls (log_error).


##### `TurnEngine._bill_cancelled`  (lines 2826–2845)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for tokens consumed before a turn was cancelled. Cancellation should not erase model usage that already happened.

**Data flow**: It totals usage events, opens a transaction, records usage for this attempt, and logs if billing fails.

**Call relations**: TurnEngine.run calls it in cancellation and preemption paths before cleanup or rethrowing.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 2847–2852)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this execution did not win the running claim. It either republishes an already committed terminal or does nothing while another execution continues.

**Data flow**: It creates a TranscriptRepair helper and asks it to resolve the turn, returning that result.

**Call relations**: TurnEngine.run and run_intent call it immediately after _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 2854–2857)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Delegates completed transcript writing to TranscriptRepair. It keeps the main engine code focused on turn flow.

**Data flow**: It receives final messages, answer, system prompt, and injected text, creates a repair helper, and asks it to persist the full transcript.

**Call relations**: TurnEngine.run and run_intent call it after successful commit paths.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_inbound`  (lines 2859–2864)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Delegates inbound-only transcript preservation to TranscriptRepair. This is used when the assistant should not write a final answer into history.

**Data flow**: It receives optional arrivals and founding denial text, creates a repair helper, and asks it to persist inbound messages only.

**Call relations**: TurnEngine.run calls it on non-done, failed, or cancelled paths where user messages must be preserved for later context.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).

## 📊 State Registers Touched

- `reg-effective-config` — The merged settings that tell the whole system what is enabled, safe, and available in this run.
- `reg-extension-catalog` — The shared list of installed extensions and the capabilities they registered for this deployment.
- `reg-db-session` — The active database connection, transaction, and workspace-safe persistence context used while work is running.
- `reg-workspace-roster` — The saved list of workspaces, members, admins, seats, and membership rules.
- `reg-agent-definitions` — The saved assistant agents for each workspace, including their settings, tools, model choices, and provisioning source.
- `reg-identity-context` — The current answer to who is acting, in which workspace, and on behalf of which member or agent.
- `reg-model-catalog` — The shared catalog of AI models, providers, routing rules, reasoning modes, key lookup rules, and usage shapes.
- `reg-tool-registry` — The shared catalog of tools the model is allowed to call and the input rules for each tool.
- `reg-conversation-state` — The durable conversation records, titles, audience, surface labels, sandbox links, and visible thread metadata.
- `reg-transcript-state` — The saved message history and transcript snapshots that are read, compacted, updated, audited, and shown later.
- `reg-turn-queue` — The durable queue of conversation turns, including admitted work, claimed work, failures, retries, and completion state.
- `reg-cancellation-state` — The shared stop-and-recovery state used to cancel running turns and prevent abandoned work from continuing.
- `reg-live-delivery` — The live stream and delivery state for partial replies, tool updates, terminal output, final status, and missed messages.
- `reg-runtime-fleet` — The records of which runtime processes and workers are alive, what they own, and when they last checked in.
- `reg-sandbox-state` — The per-conversation sandbox handle, size, filesystem environment, command execution state, and cleanup state.
- `reg-browser-sessions` — The browser or Chrome DevTools session state used when tools and hosted sandbox websites need a controlled browser.
- `reg-portal-slots` — The safe display state for conversation panels such as sources, artifacts, tasks, sites, automations, and workspace changes.
- `reg-page-index` — The stored pages, revisions, chunks, embeddings, and search indexes used to find synced knowledge later.
- `reg-memory-store` — The durable remembered facts and notes that agents can search, browse, update, consolidate, and show with provenance.
- `reg-scheduled-work` — The saved jobs, scheduled tasks, pauses, monitors, due times, retry state, and duplicate-run guards.
- `reg-workflow-plans` — The longer-running goals, objective steps, blockers, todos, delegated work, and progress evidence that survive across turns.
- `reg-subagent-delivery` — The parent-child turn links and pending result records used when helper agents run work and report back.
- `reg-usage-ledger` — The shared cost and usage records for model calls, tools, sandboxes, connectors, network use, and generated media.
- `reg-spend-controls` — The spend caps, prepaid balances, price table fingerprints, and checks that decide whether work may continue.
- `reg-visibility-policy` — The shared audience, sharing, governance, and permission rules that decide who may see or change private data.
- `reg-observability` — The shared logs, traces, metrics, trace links, and sanitized diagnostic records used to understand system behavior.
- `reg-prompt-context-budget` — The per-turn context-window and token-allocation state used to pack history, compact old context, reserve output/reasoning room, and pass normalized usage expectations to model calls.
- `reg-tool-execution-context` — The per-turn tool runtime context carrying permitted workspace handles, account/credential accessors, cleanup callbacks, sandbox/browser handles, and helper-agent hooks across tool calls.
- `reg-skill-workflow-catalog` — The registered agent skills, helper subagent profiles, workflow profiles, and related prompt/activity metadata injected into turns and surfaced to users.
