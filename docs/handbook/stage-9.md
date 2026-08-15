# Durable turn claiming and per-turn context setup  `stage-9`

This stage is the careful “take a ticket and set up the desk” part of the main work loop. A turn is one unit of agent work, such as answering a user message. The system must claim each queued turn exactly once, and it must be able to continue safely if the process crashes halfway through.

queue.py is the durable waiting line. It stores and claims turn records, connects them to the database, sandbox, model, tools, credentials, subagents, and live notifications, and records the final outcome. engine.py is the runner for a claimed turn. It gathers the conversation and workspace context, opens the sandbox, loads skills, calls the model, runs tools, records usage, and commits results without repeating expensive or side-effecting work after a restart.

compaction.py keeps long conversations small enough for the model by replacing older messages with a checked summary while preserving recent messages. __init__.py simply makes this folder importable as a Python package. Together these pieces make turn startup reliable, complete, and ready for the agent’s actual work.

## Files in this stage

### Turn claiming and execution setup
Durable queue orchestration, package setup, history compaction, and the resumable turn engine prepare and run one agent turn safely.

### `core/src/ufo/loop/queue.py`

`orchestration` · `background turn execution`

A turn is like a job ticket in a workshop: it names the conversation, the agent, the incoming message, and the workspace it belongs to. This file makes sure each ticket is picked up safely, run in the right workspace, and finished with a durable status even if something goes wrong.

The file defines a single-partition queue so turns for the same conversation run in order. It also keeps a process-wide Runtime object, which is the bundle of services a worker needs: database access, blob storage, sandbox sessions, model registry, tools, credentials, search, subagents, and notification hub.

When the queue starts a workflow, the code binds the workspace context first. That matters because workspace context controls database visibility, billing, and workspace-owned keys. It then claims the turn, loads its database record, builds the prompt and tool set, opens or reuses the conversation sandbox, and creates a TurnEngine. The engine is the part that actually talks to the model, dispatches tools, drains new arrivals, and writes transcript changes.

The file also contains safety nets. If setup fails before the engine can write a final message, it writes a failed terminal record itself and notifies listeners. If the turn is a subagent child, it tries to deliver the child result back to the parent conversation, while avoiding turning a delivery glitch into a false turn failure.

#### Function details

##### `_subagent_tools`  (lines 103–114)

```
def _subagent_tools(all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]) -> tuple[ToolDef, ...]
```

**Purpose**: Chooses which tools a subagent is allowed to use. This prevents a delegated helper agent from automatically receiving every tool available to the main agent.

**Data flow**: It receives the full tool list, a subagent profile, and any extra granted tool names. It builds an allowed-name set from the profile, optionally adds granted tools, and returns only matching tools, plus default subagent tools when the profile is not isolated.

**Call relations**: _run_turn uses this while preparing a subagent turn. The chosen tools are wrapped into a ToolRegistry and passed into the TurnEngine so the model only sees tools it is allowed to call.

*Call graph*: called by 1 (_run_turn).


##### `init_runtime`  (lines 153–157)

```
def init_runtime(runtime: Runtime) -> None
```

**Purpose**: Installs the Runtime bundle that turn workers need in this process. It protects against accidental double setup, because mixing two runtimes in one worker could send turns to the wrong services.

**Data flow**: It receives a Runtime object and stores it in the module-level runtime slot. If a runtime is already present, it raises an error instead of replacing it silently.

**Call relations**: The serving process is expected to call this during setup before any queued turn is executed. Later, _execute_turn reads this stored runtime to run actual work.


##### `reset_runtime`  (lines 160–165)

```
def reset_runtime() -> None
```

**Purpose**: Clears the stored Runtime so another one can be installed. This is mainly a testing seam, letting tests swap in fake or temporary services without fighting the single-initialization guard.

**Data flow**: It takes no input and sets the module-level runtime slot back to empty. It returns nothing and does not close services itself.

**Call relations**: Normal serving code should not need this after startup. Tests can call it before init_runtime to create a clean process state.


##### `_execute_turn`  (lines 168–219)

```
async def _execute_turn(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Runs the full durable workflow body for one turn under the correct workspace. It is the wrapper that sets context, catches setup failures, and makes sure parent delivery is attempted after the turn finishes.

**Data flow**: It receives workspace and turn IDs as strings from the workflow queue. It converts them to UUIDs, enters the workspace scope, reads identifying turn data, starts tracing context, calls _run_turn, catches unexpected errors by writing a failed terminal, then asks _deliver_to_parent to forward child results if needed. It returns a status string such as failed, parked, or superseded.

**Call relations**: turn_workflow delegates directly to this function. Inside, it calls _run_turn for the main work, _commit_failed_terminal if work fails outside normal engine handling, and _deliver_to_parent at the end so subagent results can flow back to their parent conversation.

*Call graph*: calls 3 internal fn (_commit_failed_terminal, _deliver_to_parent, _run_turn); called by 1 (turn_workflow); 6 external calls (select, agent, workspace_tx, turn_span, ws, UUID).


##### `_deliver_to_parent`  (lines 222–254)

```
async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None
```

**Purpose**: Delivers a finished subagent child turn back to the conversation that spawned it. It reads the durable database result instead of trusting in-memory state, so it sends only the final result that actually stuck.

**Data flow**: It receives the Runtime and a child turn ID. It reads the turn row from the database; if there is no parent turn or no terminal result, it does nothing. Otherwise it builds a Turn record and asks SubagentResult to deliver it. If delivery fails, it logs the delay but does not change the child’s completed status.

**Call relations**: _execute_turn calls this after every turn attempt. It hands off to SubagentResult using the runtime’s invoker and subagent registry, but it intentionally swallows non-cancellation errors so a finished child turn is not relabeled as failed because delivery was temporarily unavailable.

*Call graph*: called by 1 (_execute_turn); 6 external calls (__init__, invoker_for, model_validate, select, workspace_tx, log_error).


##### `_enqueue_handoff`  (lines 257–292)

```
async def _enqueue_handoff(client: DBOSClient, workspace_id: UUID, turn_id: UUID, conversation_id: UUID, workflow_id: str) -> None
```

**Purpose**: Queues another workflow attempt when a claimed turn needs to be handed off. This keeps the durable queue moving while preserving conversation ordering through the conversation partition key.

**Data flow**: It receives a DBOS client, workspace ID, turn ID, conversation ID, and workflow ID. It builds enqueue options and asks DBOS to enqueue the workflow. If enqueueing is cancelled or fails, it clears the turn’s dispatch_enqueued_at marker for queued turns so another sweep or retry can enqueue it later, and logs non-cancellation failures.

**Call relations**: _run_turn calls this when claiming a turn produces a handoff. It hands the work to DBOSClient.enqueue_async, and on trouble repairs the database marker through workspace_tx so the dispatch is not stuck half-marked forever.

*Call graph*: called by 1 (_run_turn); 4 external calls (enqueue_async, update, workspace_tx, log).


##### `_run_turn`  (lines 295–495)

```
async def _run_turn(runtime: Runtime, turn_id: str) -> str
```

**Purpose**: Builds and runs the actual turn engine. This is the central assembly line: claim the turn, load its data, prepare tools and prompts, open the sandbox, create the engine, and run it.

**Data flow**: It receives the Runtime and a turn ID string. It claims the turn, optionally queues a handoff, loads the turn, agent, and audience, computes previous-turn timing, loads extensions, chooses main-agent or subagent settings, opens the sandbox, mounts preloaded skills, and constructs TurnEngine with all needed services. It runs the engine and returns the resulting status; if the turn parks, it returns parked; if an unexpected error occurs, it writes a failed terminal and returns failed.

**Call relations**: _execute_turn calls this once the workspace and tracing context are ready. This function coordinates many helpers in this file: _load_turn, _previous_turn_ended_at, _subagent_tools, _open_sandbox, _enqueue_handoff, and _commit_failed_terminal. It hands the fully prepared state to TurnEngine, which performs the model-and-tool conversation work.

*Call graph*: calls 6 internal fn (_commit_failed_terminal, _enqueue_handoff, _load_turn, _open_sandbox, _previous_turn_ended_at, _subagent_tools); called by 1 (_execute_turn); 25 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__ (+15 more)).


##### `_run_turn.subagents_for`  (lines 330–334)

```
def subagents_for(acting_member_id: UUID | None) -> tuple[Spawn, Subagents]
```

**Purpose**: Creates a member-specific view of subagent spawning rights. It lets the engine ask, “if this member is acting, what subagent actions are they allowed to start?”

**Data flow**: It receives an optional acting member ID. It asks the Subagents object to authorize that member, then returns both the spawn function and the authorized Subagents wrapper.

**Call relations**: _run_turn defines this small helper while building TurnEngine. The engine receives it so later tool calls can spawn subagents with the right member-based authorization instead of using one global permission set.


##### `_commit_failed_terminal`  (lines 498–556)

```
async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None
```

**Purpose**: Writes a final failed result when a turn crashes outside the normal engine path. Without this backstop, clients waiting for the turn could wait forever because no terminal message would be recorded or published.

**Data flow**: It receives the notification hub, turn ID, and error. It creates a failed TerminalFrame with the error class and shortened message, then repeatedly tries to update queued or running turns to failed. On the real transition it emits a metric and logs the stack. It publishes the terminal event to the hub and returns; if the write or publish fails, it waits and retries with increasing delay.

**Call relations**: _execute_turn and _run_turn both call this when unexpected errors escape setup or execution. It uses the database for the durable status and Hub.publish to wake listeners, making it the final safety net for failed turns.

*Call graph*: calls 1 internal fn (publish); called by 2 (_execute_turn, _run_turn); 10 external calls (__init__, __init__, sleep, update, workspace_tx, emit_metric, formatted_stack, log, log_error, turn_profile).


##### `turn_workflow`  (lines 560–561)

```
async def turn_workflow(workspace_id: str, turn_id: str) -> str
```

**Purpose**: Exposes turn execution as a DBOS workflow, which is a durable background job that can be replayed after crashes. It is the queue-visible entry point for running one turn.

**Data flow**: It receives workspace and turn IDs as strings from DBOS. It forwards them to _execute_turn and returns whatever status that function produces.

**Call relations**: DBOS calls this when a queued turn is ready. The function stays tiny on purpose: all real workflow behavior lives in _execute_turn, while the decorator makes DBOS recognize it as the named turn workflow.

*Call graph*: calls 1 internal fn (_execute_turn).


##### `_load_turn`  (lines 564–630)

```
async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]
```

**Purpose**: Reads the database rows needed to understand a turn: the turn itself, its agent settings, and the conversation audience. It turns raw database columns into typed records used by the rest of the runner.

**Data flow**: It receives a turn UUID. It queries the turn, agent, and conversation tables, then builds a Turn object, an Agent object, and an Audience object. Stored JSON fields such as context and terminal are validated before being placed on the Turn.

**Call relations**: _run_turn calls this after claiming a turn, and also when a claim shows the turn was already superseded and transcript repair may be needed. The returned objects become the base facts for prompt building, sandbox choice, model choice, and engine construction.

*Call graph*: called by 1 (_run_turn); 7 external calls (__init__, __init__, model_validate, model_validate, select, parse_audience, workspace_tx).


##### `_previous_turn_ended_at`  (lines 633–645)

```
async def _previous_turn_ended_at(turn: Turn) -> datetime | None
```

**Purpose**: Finds when the immediately previous turn in the same conversation ended. This gives the engine timing context, for example to reason about what happened since the last normal turn.

**Data flow**: It receives the current Turn. If this is the first turn, it returns nothing. Otherwise it reads the updated_at timestamp for the previous sequence number in the same conversation and ensures the returned datetime has a timezone.

**Call relations**: _run_turn calls this for non-intent-admission turns before constructing TurnEngine. The result is passed into the engine as previous_turn_ended_at.

*Call graph*: called by 1 (_run_turn); 2 external calls (select, workspace_tx).


##### `_open_sandbox`  (lines 648–698)

```
async def _open_sandbox(sandboxes: ConversationSandbox, run_tokens: RunTokenCodec, turn: Turn, grants: GrantStore | None, clis: Mapping[str, CliCredential], credentials: CredentialStore | None, slots:
```

**Purpose**: Opens or reuses the isolated workspace where the turn’s tools and commands run. It also prepares environment variables so code inside the sandbox can authenticate through the project’s proxy and credential system.

**Data flow**: It receives the sandbox service, token encoder, turn, optional grant store, connector command-line credentials, optional credential store, and credential slots. It creates a signed run token for the turn, chooses the sandbox conversation ID, builds environment variables for the conversation ID, git proxy behavior, grant-backed connector commands, and keyed providers, then asks ConversationSandbox.open for a SandboxSession.

**Call relations**: _run_turn calls this while preparing the engine. The returned SandboxSession is passed to TurnEngine and also wrapped by SandboxAuthorizer so later actions can re-authorize the same sandbox for a specific acting member.

*Call graph*: calls 2 internal fn (open, encode); called by 1 (_run_turn); 5 external calls (__init__, _git_config_env, _git_credential_config, _grant_cli_env, _keyed_provider_env).


##### `SandboxAuthorizer.authorize`  (lines 709–721)

```
async def authorize(self, acting_member_id: UUID | None) -> SandboxSession
```

**Purpose**: Re-authorizes an already-open sandbox for a specific acting member. This is needed when tools run on behalf of different people, because the sandbox must carry the right signed token and connector credentials for that person.

**Data flow**: It receives an optional acting member ID. It creates a new run token containing the workspace, turn, and acting member, encodes it, rebuilds the grant-based connector environment for that member, and asks the SandboxSession to produce an authorized session view.

**Call relations**: _run_turn creates a SandboxAuthorizer and passes its authorize method into TurnEngine for normal non-intent turns. When the engine later needs a sandbox for a member-scoped action, it calls this method, which hands off to the existing SandboxSession authorization mechanism.

*Call graph*: 2 external calls (__init__, _grant_cli_env).


### `core/src/ufo/loop/__init__.py`

`other` · `cross-cutting`

This file is intentionally empty. In Python, an `__init__.py` file tells the interpreter that a folder should be treated as a package, meaning its contents can be imported using a dotted name such as `ufo.loop.something`. Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the workshop know the drawer exists and can be opened by name. Without this file, depending on the Python version and packaging setup, imports from the `ufo.loop` area could become less reliable or fail in some environments. There are no functions, classes, settings, or runtime steps here; its value is structural. It helps define the shape of the codebase and gives future loop-related code a clear home.


### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling, when a conversation approaches or exceeds the model context window`

This file is the conversation “packing machine.” Large language models can only read a limited amount of text at once, called a context window. When a transcript gets too big, this code cuts the conversation into an older “head” and a recent “tail.” The tail stays exactly as it was, because recent details matter most. The head is sent to the model with special instructions asking for a JSON summary, which is then checked before it is allowed to replace anything.

The checking is important. The code looks for “anchors,” meaning facts that must survive exactly, such as saved tool-output file paths, error names, loaded skills, and active request references. If the first summary drops any of these, the code tries once more and explicitly tells the model what it missed. It also removes file paths the model invented and refuses to install a compacted version that does not actually make the transcript smaller when it reasonably should.

When compaction succeeds, the file writes three compressed records to blob storage: the original window, the replacement window, and the structured summary. This makes compaction auditable: later tools or evaluations can see exactly what was replaced, what replaced it, and whether anything important was lost.

#### Function details

##### `is_context_overflow`  (lines 102–108)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: Recognizes errors that mean the model request was too large for the provider to accept. This lets the compaction code shrink the input and try again instead of treating the failure like an unrelated crash.

**Data flow**: It takes an exception, reads its class name and message as text, and searches for phrases such as “context length” or “prompt is too large.” It returns true when the error looks like a context-window overflow, and false otherwise.

**Call relations**: During summary creation, Compaction._summarize calls this after a failed model request. If this function says the request was too large, _summarize drops some old transcript rounds and retries.

*Call graph*: called by 1 (_summarize).


##### `harvest_anchors`  (lines 111–137)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: Collects exact facts from the old transcript that the replacement must preserve. These facts are used to grade the summary, so the model cannot decide for itself what counts as safely remembered.

**Data flow**: It receives rendered transcript text, the names of loaded skills, and active request text. It scans for tool-output paths, error class names, skill names, and request references, keeps only a bounded recent set for each kind, and returns Anchor objects for those literals.

**Call relations**: Compaction._compact calls this after choosing the old head to summarize. The resulting anchors are later passed through the boundary into Compaction._verify, which checks whether the compacted window still contains them.

*Call graph*: called by 1 (_compact); 1 external calls (__init__).


##### `missing_anchors`  (lines 140–144)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: Finds which required facts did not survive into the compacted text. It uses exact text containment, not fuzzy matching, because these anchors are meant to be copied literally.

**Data flow**: It takes a tuple of anchors and a text string that represents what will be carried forward. It returns only the anchors whose literal text is absent from that carried text.

**Call relations**: Compaction._verify calls this while grading a summary. If anything is missing, Compaction._compact may ask the summarizer to try once more with those missing facts explicitly named.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 182–186)

```
def __repr__(self) -> str
```

**Purpose**: Produces a short debug-friendly label for a compaction request without printing the whole transcript. This is useful because transcripts can be huge.

**Data flow**: It reads the number of messages, the compaction reason, and the number of active requests from the object. It returns a compact string showing those counts.

**Call relations**: This method is used implicitly by Python when the request object is logged or displayed. It supports the broader compaction flow by making diagnostics readable and safe.


##### `Compaction.__repr__`  (lines 217–218)

```
def __repr__(self) -> str
```

**Purpose**: Produces a short debug-friendly label for a Compaction instance. It identifies the conversation and model without exposing the full state.

**Data flow**: It reads the conversation ID and model name from the object and returns a concise string containing both.

**Call relations**: This is used implicitly when a Compaction object is printed or logged. It helps operators understand which conversation a compaction object belongs to.


##### `Compaction.maybe_compact`  (lines 220–240)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether a transcript needs compaction and starts the process only when needed. It is the safe public entry into this file’s main workflow.

**Data flow**: It receives the current messages, a force flag, and active requests. It returns the original messages with no usage records if the transcript is still small enough or cannot be split safely; otherwise it builds a _CompactionRequest and returns the compacted messages plus model usage from the summary call.

**Call relations**: Callers use this before sending a conversation window to the model, or after a provider complains that the window is too large. It checks size with Compaction._tokens and Compaction._trigger, then hands real work to Compaction._compact.

*Call graph*: calls 3 internal fn (_compact, _tokens, _trigger); 1 external calls (__init__).


##### `Compaction._trigger`  (lines 242–248)

```
def _trigger(self) -> int
```

**Purpose**: Computes the token size at which automatic compaction should begin. It leaves room for the model’s summary response and a safety buffer.

**Data flow**: It reads an optional manual trigger setting. If one exists, it returns that; otherwise it subtracts the summary allowance and buffer from the configured context window.

**Call relations**: Compaction.maybe_compact uses this to decide whether to compact. Compaction._require_budget uses the same threshold to check that the replacement window will not immediately trigger another compaction.

*Call graph*: called by 2 (_require_budget, maybe_compact).


##### `Compaction._compact`  (lines 251–329)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction pipeline: choose what to summarize, ask the model for a structured summary, verify the result, save an audit trail, and return the replacement conversation window. It is the heart of the file.

**Data flow**: It receives a _CompactionRequest containing the transcript and reason. It splits the transcript into head and tail, fires a pre-compaction hook, summarizes the head, drains loaded skills into the summary, checks anchors and size, persists before/after/summary records, fires a post-compaction hook, and returns the new messages plus usage records.

**Call relations**: Compaction.maybe_compact calls this after deciding compaction is needed. This method coordinates many helpers: selection, summarization, reference harvesting, verification, budget enforcement, persistence, and reporting.

*Call graph*: calls 11 internal fn (_next_index, _persist, _record_verification, _references, _require_budget, _select, _summarize, _tokens, _verify, _window_text (+1 more)); called by 1 (maybe_compact); 6 external calls (__init__, __init__, __init__, replace, from_iterable, warn).


##### `Compaction._select`  (lines 331–347)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: Chooses which messages are summarized and which recent messages stay untouched. It preserves whole interaction rounds so a tool call is not separated from its tool result.

**Data flow**: It receives all messages, groups them into rounds, keeps enough trailing rounds to cover the configured recent-message count, and returns older rounds as the head plus recent messages as the tail. If there is no older head left, it returns None.

**Call relations**: Compaction._compact calls this first. It relies on Compaction._rounds to form safe chunks before deciding the split.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 349–363)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Groups messages into conversation rounds that should stay together. A round starts around an assistant message and includes following tool-result or user messages that answer it.

**Data flow**: It receives a flat tuple of messages and walks through them in order. It starts a new group when an assistant message appears after an existing group, then returns a tuple of message groups.

**Call relations**: Compaction._select calls this so the compaction boundary does not cut through a tool-use exchange. This protects the recent tail from becoming confusing or invalid.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 365–391)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]=()) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: Asks the model to summarize the old transcript head, with retry behavior when the summary request itself is too large. It is careful to fail loudly if it cannot produce a usable summary.

**Data flow**: It receives head rounds and, optionally, anchors missed by a previous attempt. It calls Compaction._summarize_once; if the provider says the prompt is too large, it drops the oldest portion of the head and retries within a limit. It returns a validated summary and usage records from the successful call.

**Call relations**: Compaction._compact calls this for the first summary and possibly a second anchor-repair summary. It uses is_context_overflow to distinguish size failures and Compaction._drop_oldest to shrink retry inputs.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 393–414)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Performs one actual model call for compaction and turns the streamed answer into a validated summary object. It does not do the retry ladder itself.

**Data flow**: It builds a ModelRequest with the compaction prompt and prepared transcript text, streams text chunks from the model client, captures the usage record, joins the chunks, and parses the result into a CompactionSummary. It returns that summary and usage.

**Call relations**: Compaction._summarize calls this inside its retry loop. This function depends on Compaction._prepare to build the model input and Compaction._parse_summary to validate the output.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 416–438)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: Turns the old transcript head into text suitable for the summarizer. It also adds correction instructions when a previous summary missed required facts.

**Data flow**: It receives grouped head messages and any missed anchors. It renders each message as role plus readable content, folds huge repeated text runs, appends missed-anchor bullets if needed, adds a final instruction to answer as JSON, and returns the prompt text.

**Call relations**: Compaction._summarize_once calls this before making the model request. It uses Compaction._text to render message blocks, Compaction._fold_repeated_runs to shrink wasteful repetition, and Compaction._bullets for correction lists.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 440–453)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Compresses long verbatim repetition in the text sent to the summarizer. This prevents a stuck tool loop or pasted spam from making the summary prompt enormous while preserving the fact that repetition happened.

**Data flow**: It receives rendered transcript text and searches for short word sequences repeated many times in a row. Each repeated run is replaced by one copy plus a marker saying how many times it repeated.

**Call relations**: Compaction._prepare calls this while building the summarizer input. Its nested fold function performs the replacement for each regex match.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 448–451)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement text for one repeated run found by Compaction._fold_repeated_runs. It keeps one example of the repeated phrase and adds a count marker.

**Data flow**: It receives a regex match, extracts the repeated unit, estimates how many times it appeared, and returns text like the unit followed by “[repeated N times].”

**Call relations**: This helper is used only inside Compaction._fold_repeated_runs as the replacement callback for the regular expression substitution.


##### `Compaction._drop_oldest`  (lines 455–460)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Shrinks a too-large summarization prompt by removing the oldest part of the head. This gives the next retry a better chance of fitting into the provider’s context limit.

**Data flow**: It receives grouped head rounds, removes the oldest fifth or at least one round, and returns the remaining newer rounds.

**Call relations**: Compaction._summarize calls this only after is_context_overflow says the model request was too large and retry attempts remain.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 462–503)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Extracts and validates the JSON summary returned by the model. It protects the live transcript from being replaced by malformed or empty model output.

**Data flow**: It receives raw model text, finds the first balanced JSON object inside it, validates that object as a CompactionSummary, and checks that the intent field is not empty. It returns the typed summary or raises RuntimeError on failure.

**Call relations**: Compaction._summarize_once calls this after collecting the model’s streamed text. Its result flows into Compaction._verify before anything is persisted or installed.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 505–522)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds durable tool-output files mentioned in the summarized head that should remain visible after compaction. Instead of copying large file contents into the summary, it carries file paths the model can re-read later.

**Data flow**: It receives head rounds and the kept tail. It scans text for tool-output paths, skips paths already visible in the tail, keeps unique paths from the head, bounds the list to the most recent few, and returns them.

**Call relations**: Compaction._compact calls this while building the boundary for verification. The returned references are later rendered by Compaction._render into the replacement message.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 524–563)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns a validated CompactionSummary plus carried references and active requests into the single message that replaces the old head. The output is deterministic, meaning the same inputs produce the same compacted context text.

**Data flow**: It receives a summary, durable reference paths, and active request text. It includes only non-empty sections, formats lists as bullets, appends references and active requests verbatim, and returns one compacted-context string.

**Call relations**: Compaction._verify calls this to build the candidate replacement window. Compaction._require_budget also calls it with an empty summary to estimate the unavoidable minimum size of the replacement.

*Call graph*: calls 1 internal fn (_bullets); called by 2 (_require_budget, _verify).


##### `Compaction._bullets`  (lines 565–566)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a tuple of strings as a markdown-style bullet list. It is a small helper used wherever compacted text needs simple lists.

**Data flow**: It receives text items and returns one string with each item prefixed by “- ” on its own line.

**Call relations**: Compaction._prepare uses it to list anchors the model missed. Compaction._render uses it to format summary sections and durable references.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 568–569)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: Creates one plain text view of a group of messages. This makes it easy to search the whole window for anchors or file paths.

**Data flow**: It receives messages, renders each one with Compaction._text, joins them with newlines, and returns the combined string.

**Call relations**: Compaction._compact uses this to define what the original window contained. Compaction._verify uses it to check whether the replacement window carries required anchors.

*Call graph*: calls 1 internal fn (_text); called by 2 (_compact, _verify).


##### `Compaction._verify`  (lines 571–607)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: Checks whether one proposed summary is safe enough to install. It removes invented file paths, injects known loaded skills, renders the replacement, and records what was preserved or lost.

**Data flow**: It receives a summary, a fixed boundary, and a flag saying whether this was a retry. It keeps only summary file references that appeared in the original window, renders the compacted message, combines it with the unchanged tail, counts tokens, finds missing anchors, and returns a _Candidate containing the checked summary, rendered text, replacement messages, and verification record.

**Call relations**: Compaction._compact calls this after each summary attempt. If the verification reports missing anchors, _compact may call Compaction._summarize again with those anchors named.

*Call graph*: calls 4 internal fn (_render, _tokens, _window_text, missing_anchors); called by 1 (_compact); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._require_budget`  (lines 609–647)

```
def _require_budget(self, verification: CompactionVerification, boundary: _Boundary) -> None
```

**Purpose**: Refuses to install a compacted window that failed the basic size goal when the pipeline had enough room to do better. This prevents compaction from making future requests worse or causing repeated compactions every round.

**Data flow**: It receives a verification record and the boundary. It computes the trigger size, the unavoidable carried text size, the old head size, and the tail size. If the replacement did not shrink or still exceeds the trigger in cases where that should have been avoidable, it raises RuntimeError; otherwise it returns normally.

**Call relations**: Compaction._compact calls this after verification and before persistence. It uses Compaction._trigger, Compaction._render, and Compaction._tokens to apply the same budget rules used by the rest of the file.

*Call graph*: calls 3 internal fn (_render, _tokens, _trigger); called by 1 (_compact); 1 external calls (__init__).


##### `Compaction._record_verification`  (lines 649–672)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], verification: CompactionVerification) -> None
```

**Purpose**: Reports the quality of a completed compaction. This gives operators a direct log and metric showing whether anchors were preserved or lost.

**Data flow**: It receives the compaction index, reason, and verification record. It writes a structured log containing token counts, missing anchors, dropped paths, and retry status, then emits a metric labeled clean or lossy.

**Call relations**: Compaction._compact calls this after persisting the compaction. It hands the result to the observability system through log and emit_metric.

*Call graph*: called by 1 (_compact); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 674–685)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Saves the audit trail for a compaction: the original messages, the replacement messages, and the structured summary. This makes the compaction reviewable later.

**Data flow**: It receives an index, before messages, after messages, and a summary. It writes the before and after windows through Compaction._write, compresses the summary JSON, and stores it in the blob store under the summary key.

**Call relations**: Compaction._compact calls this once the candidate has passed budget checks. It uses Compaction._key to place all three records under the conversation’s compaction path.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 687–691)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next available compaction number for a conversation. This prevents new records from overwriting older compaction records.

**Data flow**: It starts at index 1 and asks the blob store whether an “after” record already exists for that index. It increments until it finds a free index, then returns it.

**Call relations**: Compaction._compact calls this before persistence. It uses Compaction._key to check the storage location for each possible index.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 693–700)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads a saved compaction record back from storage. This is useful for inspection, debugging, or evaluation.

**Data flow**: It receives a compaction index, tries to fetch the compressed before, after, and summary blobs, and returns None if any are missing. If all are present, it decodes them into a CompactionRecord.

**Call relations**: This is a read-side helper separate from the live compaction path. It uses Compaction._key to locate blobs and delegates decoding to the transcript module.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 702–710)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Serializes and compresses one saved message window, either the before window or the after window. This keeps compaction audit records compact on disk or blob storage.

**Data flow**: It receives an index, a label saying “before” or “after,” and messages. It wraps the messages in a CompactionWindow, dumps stable JSON, compresses the bytes with LZ4, and writes them to the blob store.

**Call relations**: Compaction._persist calls this for the before and after records. It uses Compaction._key to decide where each compressed blob belongs.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 712–713)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the storage key for one compaction artifact. It centralizes the naming rule so every part of the file reads and writes the same locations.

**Data flow**: It receives a compaction index and a part name such as before, after, or summary. It combines those with the conversation ID through the transcript key helper and returns the blob-storage path.

**Call relations**: Persistence, lookup, index selection, and record reading all call this. It delegates the exact key format to ufo.transcript.compaction_key.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 715–738)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: Estimates how many model tokens a group of messages will cost. A token is a small chunk of text used by language models; this estimate decides when compaction should happen.

**Data flow**: It receives messages and, for each one, counts role text, rendered content text, hidden reasoning bytes, and a fixed cost for images. It sums those estimates and returns the total token count.

**Call relations**: Compaction.maybe_compact uses this to decide whether to compact. Compaction._compact, Compaction._verify, and Compaction._require_budget use it to record and enforce before-and-after size behavior.

*Call graph*: calls 3 internal fn (_image_count, _opaque_chars, _text); called by 4 (_compact, _require_budget, _verify, maybe_compact).


##### `Compaction._opaque_chars`  (lines 740–759)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: Counts hidden reasoning data that affects request size but is not useful text for the summary. This prevents token estimates from pretending those blocks are free.

**Data flow**: It receives one message. If the message is plain text, it returns zero; otherwise it scans structured blocks and adds the lengths of signatures or encrypted reasoning payloads.

**Call relations**: Compaction._tokens calls this while estimating window size. It complements Compaction._text, which renders readable content but intentionally does not expose these opaque bodies.

*Call graph*: called by 1 (_tokens).


##### `Compaction._image_count`  (lines 761–771)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts inline images in a message so token estimates include their cost. Images have little or no text, but they still consume context space in model requests.

**Data flow**: It receives one message. It returns zero for plain text, otherwise scans top-level image blocks and images inside tool results, returning the total count.

**Call relations**: Compaction._tokens calls this and multiplies the count by a fixed image token estimate. This helps image-heavy conversations trigger compaction instead of silently growing too large.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 773–797)

```
def _text(self, message: Message) -> str
```

**Purpose**: Creates a readable text version of one message, no matter whether the message is plain text or made of structured blocks. This is the common view used for summarization, searching, and token estimates.

**Data flow**: It receives one message. Plain strings are returned directly; structured blocks are converted into text, image markers, redacted-reasoning markers, tool result text, or tool-call descriptions, then joined with newlines.

**Call relations**: Compaction._prepare uses this to feed the summarizer, Compaction._references and Compaction._window_text use it for searching, and Compaction._tokens uses it as part of size estimation.

*Call graph*: called by 4 (_prepare, _references, _tokens, _window_text); 1 external calls (dumps).


### `core/src/ufo/loop/engine.py`

`orchestration` · `turn execution`

Think of this file as the conductor for one conversation turn. A turn may involve several model rounds, tool calls, new messages arriving while the agent is working, spending limits, transcript writes, and live updates to the user interface. Without this file, those parts could happen out of order, be repeated after a crash, or leave a user waiting forever.

The main class, `TurnEngine`, first claims the turn in the database so only one worker owns it. It loads prior transcript messages, adds context such as time and sender, optionally recalls memory for scheduled work, and then loops. Each loop checks spending and seats, compacts old conversation when the model context is too large, calls the model, streams text live, and dispatches any requested tools. Tool results are fed back into the next model round until the model gives a final answer.

Several operations are DBOS steps. DBOS is a workflow system that records step outputs so replay after a crash returns the recorded result instead of running the external action again. That matters for expensive model calls, tool side effects, and queue drains. The file also handles special endings: parked turns when spending or seats block progress, cancelled turns, failed turns, and structured subagent finishes.

#### Function details

##### `_claim_turn`  (lines 200–247)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued, parked, or already-owned running turn for one workflow attempt. This prevents two workers from doing the same turn at the same time.

**Data flow**: It takes a turn ID and an attempt ID, reads the turn and locks its conversation, then updates the turn to `running` if it is available or already belongs to the same attempt. It returns whether this was a fresh claim, a replay adoption, or no claim at all.

**Call relations**: The engine calls this through `TurnEngine._mark_running` before doing any turn work. `_claim_turn_with_handoff` also uses it when claiming a turn and possibly scheduling the next queued turn.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 258–318)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[str | None, _TurnHandoff | None]
```

**Purpose**: Claims one turn and, if possible, marks the next queued turn in the same conversation as ready to dispatch. This helps keep the conversation moving in order.

**Data flow**: It receives a turn ID and attempt ID, first claims the current turn, then looks for the next queued turn in the same conversation. If an unstamped next turn exists, it stamps it and returns a small handoff record describing how to start it.

**Call relations**: It builds on `_claim_turn`. It is a queue-level helper for workers that need to claim current work while preparing the next turn.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `_RoundInput.__repr__`  (lines 395–400)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug-friendly label for a model round input without printing the whole conversation. This keeps logs readable and avoids dumping large prompt contents.

**Data flow**: It reads the number of messages, system prompt length, and round flags from the object, then formats those facts into a compact string.

**Call relations**: It is used implicitly when `_RoundInput` values are logged or displayed, especially around `_stream_once` calls.


##### `_BoundToolCall.__repr__`  (lines 408–409)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug label for a tool call that has already been connected to its execution context.

**Data flow**: It reads the tool name and call ID from the stored call and returns a compact string.

**Call relations**: It helps make logs around tool dispatch readable without exposing full tool arguments.


##### `_RejectedToolCall.__repr__`  (lines 419–423)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug label for a tool call that could not be prepared for execution.

**Data flow**: It reads the rejected call, outcome, and error class, then formats them into a compact string.

**Call relations**: It supports readable diagnostics when binding fails before a tool can run.


##### `ModelStreamError.__init__`  (lines 464–465)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Stores a model streaming failure together with any partial output already received. This lets the engine bill consumed usage and sometimes salvage cut-off text.

**Data flow**: It takes the model's error class, message, and optional partial output, then stores all three in the exception arguments.

**Call relations**: `TurnEngine._stream_recovering_overflow` creates this after `_stream_once` reports an error in its recorded result.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 467–469)

```
def __str__(self) -> str
```

**Purpose**: Turns the model streaming error into a readable message that includes the provider's original error class.

**Data flow**: It reads the stored error class and message and returns them as one string. It deliberately leaves out the partial output.

**Call relations**: This is used whenever the exception is logged or converted to text during failure handling.


##### `ModelStreamError.model_error_class`  (lines 472–474)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original model provider error class. The engine uses this to tell a truncation or context overflow from other model failures.

**Data flow**: It reads the first stored exception argument and returns it.

**Call relations**: `TurnEngine._model_round` checks this to decide whether a truncated response can be recovered.


##### `ModelStreamError.partial_output`  (lines 477–479)

```
def partial_output(self) -> str
```

**Purpose**: Returns any text or tool-call fragments received before the model stream failed. This is used for recovery, not for showing the terminal error.

**Data flow**: It reads the stored partial output string and returns it.

**Call relations**: `TurnEngine._model_round` uses it when saving a truncated response into the sandbox for the model to salvage.


##### `ModelStreamError.model_error_message`  (lines 482–484)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original model provider error message. This preserves the provider's explanation for terminal records and diagnostics.

**Data flow**: It reads the stored message from the exception arguments and returns it.

**Call relations**: `TurnEngine._commit_once` uses it when building a terminal frame for a failed model stream.


##### `TurnParked.__init__`  (lines 491–493)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates the exception used when a running turn must pause because spending or seat rules block it. Parking is resumable, unlike a failure.

**Data flow**: It takes a human-facing message and stores it both as the exception text and as a `message` attribute.

**Call relations**: `TurnEngine._enforce_spend` raises it, and `TurnEngine.run` catches it to park the turn durably.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 502–526)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits tool calls into safe execution groups. Parallel-safe tools can run together, while unsafe or unknown tools become ordering barriers.

**Data flow**: It takes the tool registry and the model's ordered tool calls, looks up whether each tool is safe to run in parallel, and yields ordered batches capped at a maximum size.

**Call relations**: `TurnEngine._model_round` uses these batches before binding and dispatching tools, so tool work keeps the model's intended order.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 529–531)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Combines streamed tool-call argument fragments into one JSON object. Empty arguments become an empty dictionary.

**Data flow**: It receives a list of partial JSON strings, joins them, parses the result if it contains content, and returns a dictionary.

**Call relations**: `TurnEngine._stream_once` uses it after a model stream finishes to build `ToolUseBlock` objects.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 534–549)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the small context header placed before a member message. It tells the model when, who, and where the message came from.

**Data flow**: It takes a message ID, optional turn context, and admitted time, formats the time in the sender's timezone when available, and returns an XML-like text block.

**Call relations**: `TranscriptRepair.load_messages` uses it for the founding message, and `TurnEngine._render_arrival` uses it for messages absorbed mid-turn.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 552–557)

```
def _bounded(content: str) -> str
```

**Purpose**: Cuts very large tool text down to the maximum size the engine will put back into model context. It adds a notice saying how much was dropped.

**Data flow**: It takes a string, returns it unchanged if it is small enough, or returns a prefix plus a truncation marker if it is too large.

**Call relations**: `TurnEngine._dispatch_step` uses it for error output, and `_model_round` uses it when feeding finish-tool validation errors back to a subagent.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_meter_dispatch`  (lines 560–587)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str) -> None
```

**Purpose**: Records metrics for one tool dispatch, including which tool ran, how it ended, and how long it took. This makes operational dashboards honest about both successes and failures.

**Data flow**: It receives the tool registry, call, start time, outcome, error class, and profile, normalizes unknown tools to a single name, then emits count and duration metrics.

**Call relations**: `TurnEngine._bind_or_error` uses it when binding is cancelled or fails, and `TurnEngine._dispatch_step` uses it for every tool-step exit.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 590–632)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedSkill, ...]]
```

**Purpose**: Figures out which skill instructions are already present in the current conversation window. This prevents re-loading the same skill unnecessarily.

**Data flow**: It scans model messages for completed `load_skill` tool calls and their intact text results, asks the skill registry for each requested skill's closure, and yields those closures.

**Call relations**: `TurnEngine._reseed_loaded_skills` calls it after arrivals and compaction so the compaction tracker knows what skill context is still visible.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 635–653)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts a structured payload from a successful final tool result, such as asking the user, requesting credentials, or connecting an account.

**Data flow**: It looks at the last tool call and last result, checks that the named tool succeeded, parses the first JSON payload line from the result, and validates it against the expected model.

**Call relations**: `TurnEngine._model_round` uses it after normal model tool rounds, and `TurnEngine.run_intent` uses it after a prepared intent tool dispatch.

*Call graph*: called by 2 (_model_round, run_intent); 1 external calls (loads).


##### `_total_usage`  (lines 656–663)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many model usage events into one total. This gives billing and live cost displays a single summary.

**Data flow**: It receives usage records and sums input, output, cache-read, and cache-write token counts into a new `Usage` object.

**Call relations**: Cost, billing, parking, cancellation, and streaming metrics all call this through `TurnEngine` methods when they need the turn's accumulated token use.

*Call graph*: called by 6 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost, _stream_once); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 678–702)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal result for a turn that already finished but whose client may not have heard it. This is a repair path for duplicate delivery or crash recovery.

**Data flow**: It reads the turn's stored terminal frame from the database. If one exists, it persists inbound transcript content as a safety measure, publishes the terminal to the live hub, and returns the frame; otherwise it returns nothing.

**Call relations**: `TurnEngine._resolve_unclaimed` calls this when the engine cannot claim the turn because another run owns it or it already ended.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 704–711)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the final conversation transcript for a successful answer. It appends the assistant's answer to the messages that led to it.

**Data flow**: It receives model messages, final answer, system prompt, and injected context, adds the assistant message, and passes the full conversation to `write_conversation`.

**Call relations**: `TurnEngine._persist_transcript` delegates here after a terminal answer is committed.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 713–732)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Preserves user messages when a turn ends without a normal final answer. This keeps the next turn from losing what the user said.

**Data flow**: It loads the founding inbound or builds a denial message, appends any absorbed arrivals, and writes that user-side transcript without assistant error text.

**Call relations**: `TranscriptRepair.resolve` may use it during repair, and `TurnEngine._persist_inbound` uses it for failed, cancelled, parked, or non-done endings.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 734–743)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads prior conversation messages and adds this turn's inbound message. Main-agent messages get a context header so the model can see time and sender information.

**Data flow**: It reads the turn inbound, prefixes it with `_context_tag` for normal member-facing turns, combines it with prior transcript messages, and returns model-ready messages.

**Call relations**: `TurnEngine._load_messages` uses it at the start of normal and intent turns, and `persist_inbound` uses it when saving user content.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 745–751)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the transcript before this turn, while avoiding this same turn's own previous write during replay. This prevents duplicated context.

**Data flow**: It reads the stored conversation. If nothing is stored, or the stored sequence is at or beyond this turn, it returns an empty tuple; otherwise it returns the stored messages.

**Call relations**: `load_messages` and `persist_inbound` use it whenever they need the conversation history before the current turn.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 753–773)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a conversation transcript with a few retries. Transcript writing is important, but a temporary failure should get another chance before the engine gives up.

**Data flow**: It builds a `Conversation` record from the turn sequence, messages, system prompt, and injected context, then tries to write it several times, logging failures and sleeping between attempts.

**Call relations**: `persist_transcript` and `persist_inbound` both use this as the actual durable transcript writer.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 807–824)

```
def exited(self, status: str) -> None
```

**Purpose**: Records how long this execution took and how many model rounds it entered. It makes turn duration metrics reflect the path that actually ended the execution.

**Data flow**: It receives a final status, checks whether an exit was already recorded, then emits duration and round-count metrics once.

**Call relations**: `TurnEngine._commit` calls it after terminal publication. `TurnEngine.run` also records exits for parked, cancelled, and preempted paths.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 877–888)

```
def __post_init__(self) -> None
```

**Purpose**: Validates that the engine was wired safely. It checks that tools and hooks belong to the same audience and that subagent finish behavior cannot conflict with a real tool named `finish`.

**Data flow**: It reads initialized fields, compares audiences, and may raise a `ValueError` if configuration is inconsistent.

**Call relations**: This runs automatically after a `TurnEngine` is created, before `run` or `run_intent` can start.


##### `TurnEngine.__repr__`  (lines 890–894)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact label for the engine showing the turn, agent, and profile. This helps logs identify which turn is being described.

**Data flow**: It reads IDs and the computed profile and returns a formatted string.

**Call relations**: It is used implicitly by Python logging, debugging, or tracing when the engine object is displayed.


##### `TurnEngine.profile`  (lines 897–899)

```
def profile(self) -> str
```

**Purpose**: Returns the telemetry profile for this turn, such as `main` or a subagent profile name. Metrics use it to separate different kinds of work.

**Data flow**: It reads the turn's subagent profile and converts it with `turn_profile` into a standard metric label.

**Call relations**: Many engine methods use this property when emitting logs and metrics.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.cache_ttl`  (lines 902–907)

```
def cache_ttl(self) -> PromptCacheTtl
```

**Purpose**: Chooses how long model prompt cache entries should live. Main conversations use a longer cache because future turns may reuse the prefix; subagents use a shorter one because they are short-lived.

**Data flow**: It checks whether the turn is a subagent turn and returns either a one-hour or five-minute cache setting.

**Call relations**: `run.rank_find` and `_stream_once` use this when building model requests.


##### `TurnEngine.run`  (lines 909–1079)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal model-driven turn from claim to terminal commit. This is the main body that coordinates context loading, hooks, model rounds, tools, arrivals, billing, transcript persistence, and cleanup.

**Data flow**: It starts metrics, builds a tool context, claims the turn, prepares the system and inbound message, loops through `_model_round` until an answer can be committed, and then writes transcript and workspace-change records. On parking, cancellation, preemption, or errors, it bills or releases work as appropriate and cleans up tool context resources.

**Call relations**: This is the central flow. It calls most helper methods in this file and is the normal execution body for a turn workflow.

*Call graph*: calls 13 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _record_workspace_changes, _release_unabsorbed (+3 more)); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, escape, monotonic, emit_metric, log (+1 more)).


##### `TurnEngine.run.rank_find`  (lines 926–944)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Provides the browser find tool with a host-side model call for ranking page elements. Its token usage is charged to the same turn.

**Data flow**: It receives a system prompt and user prompt, builds a small model request with reasoning off, streams text chunks into a string, records usage events, and returns the combined ranking text.

**Call relations**: `TurnEngine.run` installs this function into `ToolContext` so browser tools can call it during dispatch.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine.run_intent`  (lines 1081–1187)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a prepared intent turn, where the inbound payload already names exactly one tool to call. It skips the model so submitted structured values are applied directly or refused directly.

**Data flow**: It claims the turn, parses the inbound tool intent, builds one tool call, binds and dispatches it, extracts any structured connect or credential request, commits success or failure, writes transcript, and cleans up.

**Call relations**: This is the alternate top-level path for machine-submitted tool intents. It shares the same binding, dispatch, commit, and transcript helpers as normal model turns.

*Call graph*: calls 8 internal fn (_bind_or_error, _commit, _dispatch_step, _load_messages, _mark_running, _persist_transcript, _resolve_unclaimed, _final_act); 10 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, monotonic, emit_metric, log).


##### `TurnEngine._scheduled_system`  (lines 1189–1223)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. This gives background work relevant past context without requiring a live user message.

**Data flow**: It searches memory with the scheduled inbound text and audience subjects, formats any matches safely into a recalled-memory block, and appends that block to the system prompt. If memory search fails or finds nothing, it returns the original system prompt.

**Call relations**: `TurnEngine.run` calls this only for scheduled admissions before the first model round.

*Call graph*: called by 1 (run); 5 external calls (__init__, timeout, escape, audience_subjects, log).


##### `TurnEngine._mark_running`  (lines 1225–1233)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims the turn for this engine execution. It is the engine's guard against duplicate workers doing the same work.

**Data flow**: It passes the turn ID and attempt ID to `_claim_turn` and returns true if any claim branch succeeded.

**Call relations**: `run` and `run_intent` call this before doing model or tool work; if it fails, they go to `_resolve_unclaimed`.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 1235–1236)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Builds a `TranscriptRepair` helper for this turn. This keeps transcript repair and persistence logic in one small companion object.

**Data flow**: It packages the engine's turn, transcript, and hub into a new `TranscriptRepair` instance.

**Call relations**: Several engine methods call this before delegating transcript loading, saving, or terminal republishing.

*Call graph*: called by 5 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed, run); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 1238–1240)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads model-ready messages for this turn while wrapping the operation in a trace span. This marks transcript loading in observability tools.

**Data flow**: It creates a repair helper, asks it to load messages, and returns the result.

**Call relations**: `run` and `run_intent` call this near startup, and successful or intent transcript persistence may use the same loaded messages.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 1242–1421)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the repeated model-and-tools loop until the turn has a final answer. It is where arrivals are folded in, spending is checked, context is compacted, model output is streamed, and requested tools are dispatched.

**Data flow**: It receives the current tool context, messages, usage list, system prompt, arrival logs, absorbed IDs, requester map, and meter. Each iteration may add arrivals, compact messages, call the model, recover truncation, dispatch tool calls, update pending final actions, or return final text and structured requests.

**Call relations**: `TurnEngine.run` calls this inside its main loop. It coordinates lower-level helpers such as `_absorb_arrivals`, `_stream_recovering_overflow`, `_dispatch`, `_force_final`, and `_publish_cost`.

*Call graph*: calls 13 internal fn (_absorb_arrivals, _bind_or_error, _dispatch, _enforce_spend, _force_final, _force_finish, _offload, _publish_cost, _reseed_loaded_skills, _stream_recovering_overflow (+3 more)); called by 1 (run); 7 external calls (__init__, __init__, __init__, gather, emit_metric, log, span).


##### `TurnEngine._absorb_arrivals`  (lines 1423–1471)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Adds messages that arrived while the turn was already running into the model's conversation window. This lets the agent respond to late user guidance before closing the turn.

**Data flow**: It asks `_claim_arrivals` for newly drained queue rows, records absorbed IDs, turns each rendered or denied arrival into a user message, updates requester tracking, publishes an absorbed notice for member messages, and returns the expanded message tuple.

**Call relations**: `_model_round` calls this at the start of every round so new messages land between completed model/tool cycles.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); called by 1 (_model_round); 4 external calls (__init__, __init__, __init__, escape).


##### `TurnEngine._render_arrival`  (lines 1473–1497)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Turns one queued inbound message into exactly what the model should see. It runs the same user-prompt hook used for the founding message.

**Data flow**: It sends the body through the `user_prompt_submit` hook. If denied, it returns denial text; otherwise it prefixes the body with a context tag and appends injected context inside its own delimiter.

**Call relations**: `_claim_arrivals` calls this while draining arrivals, so hook results are recorded with the drain step.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1500–1562)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Drains pending inbound messages for this conversation as a recorded DBOS step. Recording the drain means crash replay sees the same batch instead of consuming different messages.

**Data flow**: It stamps eligible inbound rows as consumed by this turn, sorts them by sequence, renders each one through `_render_arrival`, and returns `Arrival` records.

**Call relations**: `_absorb_arrivals` calls this in each model round. Because it is a step, replay reuses the recorded arrivals instead of re-running hooks or re-draining rows.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 6 external calls (__init__, model_validate, and_, or_, update, workspace_tx).


##### `TurnEngine._release_unabsorbed`  (lines 1564–1583)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns messages that were stamped for this turn but never safely absorbed. This prevents arrivals from being lost after failure or cancellation.

**Data flow**: It receives IDs already known to be absorbed, then clears the consumed marker from any other inbound rows stamped with this turn. Failures are logged but not allowed to block shutdown.

**Call relations**: `TurnEngine.run` calls this on cancellation, preemption, and error paths.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1585–1621)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, requesters: dict[UUID, ActiveMessage]) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Gets a best-effort final answer when the turn has used all allowed tool-use rounds. It avoids failing just because the model kept looping.

**Data flow**: It logs and meters budget exhaustion, checks spending, compacts context, then either forces a structured subagent finish or adds a no-tools final prompt and calls the model once more. It returns the closing messages and answer.

**Call relations**: `_model_round` calls this when its round loop reaches the maximum.

*Call graph*: calls 4 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_recovering_overflow); called by 1 (_model_round); 3 external calls (__init__, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 1623–1648)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to close through the special `finish` tool. This keeps subagent outputs in the required schema instead of free-form prose.

**Data flow**: It verifies an output model exists, runs one model round with only the finish tool forced, publishes cost, validates the returned finish arguments, and returns canonical JSON.

**Call relations**: `_model_round` uses it when a subagent answers in prose, and `_force_final` uses it when a subagent exhausts its round budget.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 1650–1710)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False, active_requests: tuple[str, ...]=()
```

**Purpose**: Runs one model round and retries once after forced compaction if the provider says the context is too large. This gives oversized conversations one recovery path.

**Data flow**: It builds `_RoundInput`, calls `_stream_once`, adds usage, and converts recorded stream errors into `ModelStreamError`. If the error is context overflow, it forces compaction, reseeds skill tracking, and tries `_stream_once` again with the smaller message window.

**Call relations**: `_model_round`, `_force_final`, and `_force_finish` all use this instead of calling `_stream_once` directly.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 1712–1754)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks seats and spending caps before each model round. If the turn is no longer allowed to spend, it parks rather than continuing or throwing away work.

**Data flow**: It gathers all member IDs whose messages are active, checks seat status, computes the pending model cost from accumulated usage, asks the spend evaluator for a decision, and raises `TurnParked` if blocked.

**Call relations**: `_model_round` and `_force_final` call it before more model tokens can be spent.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 5 external calls (__init__, __init__, applicable_caps_absent, audience_member, workspace_tx).


##### `TurnEngine._stream_once`  (lines 1757–1989)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Performs one recorded model call, streams visible text live, and returns the full round result. It is a DBOS step so replay does not call the model again or spend tokens again.

**Data flow**: It chooses tool schemas, builds a model request, streams events from the model, buffers text deltas to the hub, collects reasoning, tool-call fragments, and usage, emits timing and cache metrics, and returns a `StreamResult`. If the stream errors mid-way, it returns the error details and partial output inside the result.

**Call relations**: `_stream_recovering_overflow` is the only caller. Tool dispatch later relies on the stable tool call IDs recorded here.

*Call graph*: calls 2 internal fn (_parse_args, _total_usage); called by 1 (_stream_recovering_overflow); 13 external calls (__init__, __init__, __init__, __init__, Event, Lock, ensure_future, now, monotonic, emit_histogram (+3 more)).


##### `TurnEngine._stream_once.flush`  (lines 1830–1838)

```
async def flush() -> None
```

**Purpose**: Sends buffered model text to the live hub in chunks. This avoids publishing every tiny token separately while still keeping the UI responsive.

**Data flow**: It takes text accumulated in the local buffer, clears that buffer, resets the pending byte count, and publishes a `TextDelta` frame.

**Call relations**: It is an inner helper used by `_stream_once` during streaming and at the end of the round.

*Call graph*: 1 external calls (__init__).


##### `TurnEngine._stream_once.pace`  (lines 1840–1845)

```
async def pace() -> None
```

**Purpose**: Periodically flushes streamed model text even if the byte threshold has not been reached. This keeps slow trickles of text visible to clients.

**Data flow**: It waits in short intervals until a stop event is set, calling `flush` after each timeout.

**Call relations**: `_stream_once` starts this as a background task while consuming the model stream.

*Call graph*: 1 external calls (wait_for).


##### `TurnEngine._publish_cost`  (lines 1991–2005)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes a live cost update for the turn so the user interface can show spending before the turn finishes.

**Data flow**: It totals usage events, computes token count and micro-dollar cost, builds a `CostTick`, and publishes it through `_publish`.

**Call relations**: `_model_round`, `_force_final`, and `_force_finish` call it after model rounds.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 2007–2018)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skills currently visible to the model. This keeps skill-loading behavior correct after arrivals and compaction.

**Data flow**: It scans messages through `_loaded_skill_closures`, includes preloaded skills, and reseeds the compaction skill tracker.

**Call relations**: `_model_round` calls it before and after compaction, and `_stream_recovering_overflow` calls it after forced compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 2 (_model_round, _stream_recovering_overflow).


##### `TurnEngine._bind_or_error`  (lines 2020–2041)

```
async def _bind_or_error(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Prepares a tool call for execution, turning preparation failures into tool-style error results. This lets the model correct bad calls instead of crashing the turn.

**Data flow**: It records start time, tries to bind the call to the correct requester-specific context, returns a bound call on success, returns a rejected call on ordinary errors, and meters cancellations or failures.

**Call relations**: `_model_round` uses it before dispatching model-requested tools, and `run_intent` uses it for prepared intent tools.

*Call graph*: calls 2 internal fn (_bind_requester, _meter_dispatch); called by 2 (_model_round, run_intent); 3 external calls (__init__, __init__, monotonic).


##### `TurnEngine._dispatch`  (lines 2043–2044)

```
def _dispatch(self, bound: _DispatchInput) -> Awaitable[ToolResultBlock]
```

**Purpose**: Starts a recorded tool dispatch and converts its stored result into a model-facing tool result block. It is a small bridge between the DBOS step and message assembly.

**Data flow**: It calls `_dispatch_step` with the bound or rejected input, passes that awaitable to `_dispatch_result`, and returns the resulting awaitable.

**Call relations**: `_model_round` calls this for each bound tool call in a dispatch segment.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step); called by 1 (_model_round).


##### `TurnEngine._dispatch_result`  (lines 2046–2072)

```
async def _dispatch_result(self, step: Awaitable[DispatchResult]) -> ToolResultBlock
```

**Purpose**: Turns a serialized dispatch result back into the `ToolResultBlock` the model expects. If images were offloaded, it reads them from blob storage and reattaches them.

**Data flow**: It awaits a `DispatchResult`. If there are no image references, it returns plain text. If there are images, it loads each blob, builds image blocks, combines them with optional text, and returns a rich tool result.

**Call relations**: `_dispatch` uses this after `_dispatch_step` so DBOS logs stay small while the model still receives images.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._bind_requester`  (lines 2074–2113)

```
async def _bind_requester(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Associates a tool call with the member it is acting for when the call names a requester message. This matters for permissions, sandbox choice, and subagent routing.

**Data flow**: It copies tool input, extracts and validates the special requester reference if present, chooses the acting member, obtains member-specific sandbox and subagent controls when providers are configured, and returns an updated context plus call with the internal requester field removed.

**Call relations**: `_bind_or_error` calls this before a tool can be dispatched.

*Call graph*: called by 1 (_bind_or_error); 3 external calls (model_copy, replace, UUID).


##### `TurnEngine._offload`  (lines 2115–2140)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text into the sandbox `.tool-output` directory and returns the file path. This keeps huge tool output or salvaged model text out of the prompt while still making it available.

**Data flow**: It builds a path, ensures the tool-output directory exists, writes bytes into the sandbox, logs and returns `None` on failure, or returns the path on success.

**Call relations**: `_dispatch_step` uses it for oversized tool results, and `_model_round` uses it for truncated model-output salvage.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._dispatch_step`  (lines 2143–2322)

```
async def _dispatch_step(self, bound: _DispatchInput) -> DispatchResult
```

**Purpose**: Runs one tool call as a recorded DBOS step. Recording prevents a side-effecting tool from being re-applied after crash recovery.

**Data flow**: It receives either a bound call or rejected call. It handles rejected calls, replay preemption, tool lookup and argument validation, pre-tool hooks, tool execution, output bounding or offloading, untrusted-content wrapping, post-tool hooks, image resizing and blob offload, metrics, and returns a compact `DispatchResult`.

**Call relations**: `_dispatch` calls this for model-requested tools, and `run_intent` calls it directly for prepared intent tools.

*Call graph*: calls 7 internal fn (_bounded_image, _offload, _pending_member_guidance, _publish, _redoes_on_replay, _bounded, _meter_dispatch); called by 2 (_dispatch, run_intent); 13 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, replace, monotonic, tool_activity (+3 more)).


##### `TurnEngine._redoes_on_replay`  (lines 2324–2333)

```
def _redoes_on_replay(self, name: str) -> bool
```

**Purpose**: Decides whether re-running a tool step would redo work and therefore can be preempted when fresh member guidance is waiting. Unknown tools are treated as not redoable because they never dispatch.

**Data flow**: It looks up the tool by name and returns true for non-side-effecting tools, false for side-effecting or unknown tools.

**Call relations**: `_dispatch_step` uses this during adopted crash-replay windows before deciding whether to run a tool or yield to queued member guidance.

*Call graph*: called by 1 (_dispatch_step).


##### `TurnEngine._pending_member_guidance`  (lines 2335–2351)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether an unconsumed member message is waiting in the conversation. This lets replayed work avoid running ahead of new guidance.

**Data flow**: It queries the inbound-message table for one unconsumed member-admitted row in the turn's conversation and returns whether one exists.

**Call relations**: `_dispatch_step` calls it only inside a live dispatch body during adoption replay handling.

*Call graph*: called by 1 (_dispatch_step); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 2353–2381)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized tool-result images before giving them to the model. This avoids provider limits and wasted image detail.

**Data flow**: It decodes the base64 image, opens it with PIL, leaves it alone if already small enough, otherwise thumbnails it to the configured edge limit, re-encodes it, and returns a new image block. If image processing fails, it logs and returns the original image.

**Call relations**: `_dispatch_step` calls this before storing successful tool-result images in the blob store.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 2383–2454)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Durably ends the turn and publishes the terminal frame. It retries database commit failures so clients are not left waiting because of a temporary outage.

**Data flow**: It repeatedly calls `_commit_once` until it gets a result, publishes the terminal if one exists, emits terminal and duration metrics, logs the outcome, and returns the frame. If pending arrivals block the commit, it returns `None` so the turn can keep working.

**Call relations**: `run` and `run_intent` use this for successful and failed endings.

*Call graph*: calls 3 internal fn (_commit_once, _publish, exited); called by 2 (run, run_intent); 5 external calls (__init__, sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._record_workspace_changes`  (lines 2456–2466)

```
async def _record_workspace_changes(self) -> None
```

**Purpose**: Refreshes the list of sandbox workspace changes after the user-visible turn is complete. This makes the portal's changes view reflect files modified during the turn.

**Data flow**: It builds a `WorkspaceChangeRecorder` for the relevant workspace and sandbox conversation and asks it to record changes.

**Call relations**: `run` calls this after terminal publication and transcript persistence, so it does not delay the main answer path.

*Call graph*: called by 1 (run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 2468–2565)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction that bills usage and writes the terminal turn state. It is the durable core under the retrying `_commit` wrapper.

**Data flow**: It totals usage, optionally locks the conversation and checks for unabsorbed arrivals, records billed usage, reads cost totals, builds a `TerminalFrame`, and updates the turn if it is still non-terminal. If another path already ended the turn, it reads and returns the existing terminal instead.

**Call relations**: `_commit` calls this until it succeeds or yields to pending arrivals.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 9 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx).


##### `TurnEngine._park`  (lines 2567–2604)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Puts a turn into a resumable parked state when spending or seat rules stop it. It bills tokens already consumed so future resume checks see the true cost.

**Data flow**: It updates the turn to parked, records accumulated usage, releases all inbound messages consumed by this attempt, publishes a parked frame, emits metrics, and logs the park.

**Call relations**: `run` catches `TurnParked` from `_enforce_spend` and calls this before re-raising.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 2606–2615)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Sends a live frame to clients without letting publish failure break the turn. The database remains the source of truth.

**Data flow**: It receives a live frame, tries to publish it through the hub, and logs any exception.

**Call relations**: Arrivals, cost ticks, tool activity, terminal frames, and parked frames all pass through this helper.

*Call graph*: called by 5 (_absorb_arrivals, _commit, _dispatch_step, _park, _publish_cost); 1 external calls (log).


##### `TurnEngine._bill_cancelled`  (lines 2617–2636)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for a turn that was cancelled or preempted after spending model tokens. Cancellation should not erase real usage.

**Data flow**: It totals usage and tries to record it in the accounting ledger. If billing fails, it logs the failure and does not block cancellation.

**Call relations**: `run` calls this on DBOS workflow cancellation and asyncio cancellation paths.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 2638–2643)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this execution could not claim the turn. It repairs a finished turn's live notification or quietly steps aside for another running owner.

**Data flow**: It creates a transcript repair helper and asks it to resolve the turn, returning a terminal frame if one was already committed or `None` if the turn is still live elsewhere.

**Call relations**: `run` and `run_intent` call this after `_mark_running` fails.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 2645–2648)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Delegates successful transcript persistence to the repair helper. This keeps the engine's main flow shorter.

**Data flow**: It receives messages, answer, system prompt, and injected text, builds a `TranscriptRepair`, and asks it to persist the final transcript.

**Call relations**: `run` calls it after a done terminal, and `run_intent` calls it after an intent dispatch completes.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_inbound`  (lines 2650–2655)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Delegates inbound-only transcript persistence to the repair helper. This preserves user messages for non-normal exits.

**Data flow**: It receives optional arrival messages and founding-denial text, builds a `TranscriptRepair`, and asks it to save inbound content.

**Call relations**: `run` calls it when the turn fails, is cancelled, or ends in a non-done state.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).

## 📊 State Registers Touched

- `reg-workspace-membership` — The roster of workspaces, members, admins, seats, and which people belong where.
- `reg-agent-directory` — The saved assistants in each workspace and their settings, such as model behavior, sandbox size, and internet access.
- `reg-model-catalog` — The lookup table of available AI models, providers, routing details, capabilities, and pricing metadata.
- `reg-tool-catalog` — The runtime menu of tools the agent may call, including built-ins and extension-provided tools.
- `reg-credential-vault` — Encrypted workspace secrets and short-lived brokered credentials used without exposing raw secrets to tools.
- `reg-connection-grants` — Connected third-party accounts and permissions saying which agents may use which external accounts.
- `reg-conversation-transcript` — The durable history of conversations, messages, speakers, titles, context, and results.
- `reg-turn-queue` — The durable waiting line and status record for each unit of agent work, from queued to running to finished or failed.
- `reg-live-hub` — The live stream state that lets browsers, terminals, and operators watch progress and reconnect without losing updates.
- `reg-runtime-fleet` — The sign-in sheet of running server and worker instances used to detect active work, crashes, and abandoned turns.
- `reg-sandbox-workspace` — The per-conversation isolated workbench, including its handle, files, execution backend, and recorded file changes.
- `reg-sandbox-network-policy` — The per-run rules and proxy state that decide what sandboxed code may reach on the internet and when secrets may be injected.
- `reg-browser-session` — The leased browser instance for a turn, including tabs, page state, downloads, dialogs, and provider connection details.
- `reg-subagent-state` — The helper-agent catalog and child-conversation handoff state used when one agent delegates work to another.
- `reg-search-index` — The searchable text chunks, embeddings, and backend index state used to find relevant documents and pages.
- `reg-memory-store` — Saved facts, memory pages, confidence, provenance, and audience labels that let the assistant remember useful context.
- `reg-accounting-ledger` — Usage, cost, spend limits, prepaid balances, billing exports, and price versions for workspace spending.
- `reg-artifact-store` — Generated files, previews, blobs, and signed shared-artifact links that outlive a single message.
- `reg-observability-context` — Trace IDs, metrics, logs, and sanitized operational events used to understand work across services and turns.
- `reg-database-connection-pool` — Per-process database engine/session pools and workspace-scoped connection context used by servers, workers, migrations, and persistence code.
- `reg-execution-scope-context` — Per-request, per-job, and per-turn scoped context carrying the active workspace, member, agent, turn, permissions, credentials, billing, and service handles through core code.
- `reg-prompt-version-state` — Prompt-render metadata such as system-prompt digests, contributed sections, cutoff/version information, and replay identifiers attached to turns for change detection and evaluation.
- `reg-inflight-cancellation-handles` — Live cancellation signals, workflow handles, and parent-child cancellation propagation state for active turns and delegated work before final durable status is written.
- `reg-durable-workflow-state` — DBOS/workflow-runtime execution metadata for durable job and turn workflows, including workflow IDs, retries, scheduled starts, cancellation, and resume bookkeeping.
