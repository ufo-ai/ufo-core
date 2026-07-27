# Agent turn engine main loop  `stage-10`

This stage is the main work loop for one assistant turn. It starts after a user message is ready to be answered, and it ends when the assistant has either produced a final result, paused, or handed work to another agent. The key file is `engine.py`, which acts like a traffic controller. It claims the turn so only one worker handles it, gathers the conversation history, sends it to the model, streams the model’s answer as it arrives, and watches for tool calls. When the model asks to use a tool, the engine runs it safely, records what happened, and feeds the result back into the conversation.

The engine also handles interruptions. It can pause for a user question, stop when a spending limit is reached, retry when the conversation is too large, or absorb new messages that arrived mid-turn. It records usage for billing and saves progress carefully, so if the system crashes it can replay the turn without repeating paid model calls or tools with real-world side effects. `__init__.py` simply makes this folder importable as a Python package.

## Files in this stage

### Turn Loop Package and Engine
Package setup and the central turn engine that orchestrates a full replay-safe assistant turn.

### `core/src/ufo/loop/__init__.py`

`other` · `import/package discovery`

This is an empty `__init__.py` file. In Python projects, a file with this name tells Python that the surrounding folder should be treated as an importable package. You can think of it like a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the program find them by name.

Because this file has no code inside it, it does not create objects, run setup steps, or change program behavior directly. Its value is structural. Without it, depending on the Python version and packaging setup, other parts of the project might not be able to reliably import things from `core/src/ufo/loop` using package-style paths such as `ufo.loop...`.

So this file matters not because it performs work itself, but because it helps organize the codebase and makes the `loop` part of the system visible to Python’s import machinery.


### `core/src/ufo/loop/engine.py`

`orchestration` · `turn execution`

A “turn” is one stretch of work by the agent in a conversation, starting from a user message and ending in a final answer, a question to the user, a parked state, or a failure. This file is the turn engine. It makes sure only one worker owns a turn, then feeds prior conversation, time and sender context, and any injected extension guidance into the model. If the model asks to use tools, the engine runs those tools, sends their results back to the model, and repeats until the model gives a final answer.

The hard part is reliability. The system uses DBOS steps, which are recorded checkpoints for work with side effects. Think of them like stamped receipts: if the process crashes, completed model rounds, tool calls, arrival drains, and compactions are read from the receipt instead of being done again. That prevents double-billing and prevents tools from repeating external writes.

The engine also watches live cost and seat/spend limits, publishes progress to connected clients, shrinks long conversations when they approach model limits, offloads huge tool results into workspace files, stores large images outside replay logs, and writes the durable transcript at the end. Without this file, turns could race each other, lose messages, repeat expensive work after crashes, or commit answers that ignored messages arriving mid-run.

#### Function details

##### `_claim_turn`  (lines 164–206)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> bool
```

**Purpose**: Claims a queued or parked turn so this workflow becomes its owner. This prevents two workers from running the same turn at the same time.

**Data flow**: It receives a turn ID and an attempt ID. It locks the conversation row, updates the turn to running only if it is claimable by this attempt, clears the dispatch marker, and removes one-time resume tasks for that turn. It returns true if the claim succeeded and false if another execution already owns or finished it.

**Call relations**: TurnEngine._mark_running uses this at the start of a run. _claim_turn_with_handoff also uses it before checking whether another queued turn should be handed off for dispatch.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 6 external calls (and_, delete, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 217–274)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[bool, _TurnHandoff | None]
```

**Purpose**: Claims the current turn and, if possible, marks the next queued turn in the same conversation as ready to dispatch. It helps keep conversation work moving in order.

**Data flow**: It receives the current turn ID and attempt ID. First it tries to claim the current turn. If that works, it looks for the next queued turn in the same conversation and stamps it as enqueued when it has not already been stamped. It returns whether the current claim succeeded plus optional handoff details for the next turn.

**Call relations**: It builds on _claim_turn. The handoff object it creates gives another part of the system enough information to enqueue the next workflow without racing the current one.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `ModelStreamError.__init__`  (lines 361–362)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Creates an error object for a model stream that failed after already producing some output or usage. It keeps the provider’s error type, message, and partial text together.

**Data flow**: It receives an error class name, an error message, and optional partial output. It stores all three in the exception’s arguments so they survive persistence and replay. It returns a constructed exception object.

**Call relations**: TurnEngine._stream_recovering_overflow creates this after _stream_once reports a model streaming failure in its recorded result.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 364–366)

```
def __str__(self) -> str
```

**Purpose**: Formats the model stream error as readable text. It includes the model provider’s error class so later checks can still recognize special cases such as context overflow.

**Data flow**: It reads the stored error class and message from the exception. It combines them into a single string. It does not expose the partial output, because that is meant for recovery, not terminal error display.

**Call relations**: This is used whenever the exception is converted to text, including logging or terminal error handling elsewhere in the engine.


##### `ModelStreamError.model_error_class`  (lines 369–371)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original class name of the model provider error. Callers use this to distinguish truncation, overflow, and ordinary failures.

**Data flow**: It reads the first stored argument from the exception and returns it unchanged. Nothing else is modified.

**Call relations**: TurnEngine._model_round checks this value after _stream_recovering_overflow raises the error, especially to recover from output truncation.


##### `ModelStreamError.partial_output`  (lines 374–376)

```
def partial_output(self) -> str
```

**Purpose**: Returns any text or tool-call fragments the model produced before the stream failed. This lets the engine salvage paid-for output instead of throwing it away.

**Data flow**: It reads the saved partial output from the exception. The caller can then write that content to a file or use it in recovery guidance.

**Call relations**: TurnEngine._model_round uses this when a model response was truncated, offloading the partial output so the model can continue from it.


##### `ModelStreamError.model_error_message`  (lines 379–381)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original message from the model provider error. This preserves useful detail for terminal frames and diagnostics.

**Data flow**: It reads the stored message from the exception and returns it. It does not change state.

**Call relations**: TurnEngine._commit_once uses this when building a terminal error frame for a failed turn caused by model streaming.


##### `TurnParked.__init__`  (lines 388–390)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates an exception that means the turn must pause instead of finish because a spend cap or seat rule blocked further work. Parking is resumable, unlike failure.

**Data flow**: It receives a user-facing message. It stores that message on the exception so the parked notification can explain why the turn stopped.

**Call relations**: TurnEngine._enforce_spend raises this. TurnEngine.run catches it and calls _park to record the parked state.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 393–417)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits a model’s tool calls into safe execution groups. Tools marked as safe to run in parallel can run together; other tools become one-at-a-time barriers.

**Data flow**: It receives the tool registry and the ordered tool calls from one model round. It checks each tool’s parallel-safety flag, groups consecutive safe calls up to the configured limit, and yields each group in the original order.

**Call relations**: TurnEngine._model_round uses this before dispatching tools. The grouping lets the engine gain speed without letting a write race ahead of a read that may depend on it.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 420–422)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed JSON fragments from a tool call into a normal argument dictionary. Empty arguments become an empty dictionary.

**Data flow**: It receives a list of partial JSON strings. It joins them, parses the result when it contains non-whitespace text, and returns the decoded dictionary-like value.

**Call relations**: TurnEngine._stream_once uses this after a model stream finishes to build ToolUseBlock objects from streamed tool-call argument fragments.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 425–435)

```
def _context_tag(context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the small context header placed before user messages, showing when the message was admitted and who sent it. This gives the model a stable sense of time and speaker.

**Data flow**: It receives optional turn context and the stored admission time. It formats the time in the sender’s timezone when available, otherwise UTC, adds the sender name if present, and returns a text block wrapped in a context tag.

**Call relations**: TranscriptRepair.load_messages uses it for the founding inbound message. TurnEngine._render_arrival uses it for later messages absorbed while the turn is running.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 438–444)

```
def _bounded(content: str) -> str
```

**Purpose**: Shortens overly large text to the maximum size allowed for tool results. This protects the model context from being flooded by one huge result.

**Data flow**: It receives text. If it is short enough, it returns the text unchanged. If it is too long, it returns the leading portion plus a note explaining how many characters were cut.

**Call relations**: TurnEngine._dispatch_step uses it for error results and fallback truncation. TurnEngine._model_round uses it when creating an artificial finish-tool error.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_final_act`  (lines 447–465)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Detects when a special tool call was the successful final action of a round and extracts its structured payload. This is how ask-user, credential, and connect-account requests become terminal frame fields.

**Data flow**: It receives the round’s tool calls, their results, the tool name to look for, and the expected payload model. It checks the last call and result, parses the JSON payload from the result text, validates it, and returns the typed payload or None.

**Call relations**: TurnEngine._model_round calls this after tool dispatch to remember whether the latest round ended by asking the user, requesting credentials, or requesting an account connection.

*Call graph*: called by 1 (_model_round); 1 external calls (loads).


##### `_total_usage`  (lines 468–474)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many token-usage reports into one total. This gives billing, live cost display, and spend checks a single number to use.

**Data flow**: It receives a list of usage events. It sums input, output, cache-read, and cache-write token counts separately and returns one Usage object with those totals.

**Call relations**: TurnEngine uses it in _publish_cost, _enforce_spend, _commit_once, _park, and _bill_cancelled so all cost-related paths calculate from the same accumulated usage.

*Call graph*: called by 5 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 489–513)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal result for a turn that already finished, usually after a duplicate delivery or crash after commit. This helps waiting clients receive the ending they missed.

**Data flow**: It reads the turn’s stored terminal frame from the database. If none exists, it returns None. If one exists, it persists the inbound transcript if needed, publishes the terminal frame to the live hub, logs publish failures without crashing, and returns the frame.

**Call relations**: TurnEngine._resolve_unclaimed calls this when the current execution could not claim the turn. It may call TranscriptRepair.persist_inbound before publishing.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 515–522)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the completed conversation transcript for a successful turn. It includes the assistant’s final answer.

**Data flow**: It receives the model-visible messages, answer text, system prompt, and injected prompt text. It appends the assistant answer to the messages and passes the full conversation to write_conversation.

**Call relations**: TurnEngine._persist_transcript delegates here after a done terminal is committed. It relies on write_conversation for retries.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 524–531)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=()) -> None
```

**Purpose**: Writes only the user-side messages for a turn that did not complete normally. This preserves what the user said without saving a failed or partial assistant reply.

**Data flow**: It loads the prior transcript plus the founding inbound, appends any absorbed arrival messages, and writes that conversation. The assistant’s error text is intentionally left out.

**Call relations**: TranscriptRepair.resolve may call it when republishing a committed terminal. TurnEngine._persist_inbound delegates here on parked, failed, or cancelled paths.

*Call graph*: calls 2 internal fn (load_messages, write_conversation); called by 1 (resolve).


##### `TranscriptRepair.load_messages`  (lines 533–541)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Builds the initial message list the model should see for this turn. It combines prior conversation with this turn’s inbound message.

**Data flow**: It reads prior messages using _prior_messages. For ordinary user-facing turns, it prefixes the inbound text with a context tag; for subagent turns, it keeps the inbound payload bare. It returns the prior messages plus the new user message.

**Call relations**: TurnEngine._load_messages and TranscriptRepair.persist_inbound use this. It calls _context_tag and _prior_messages to assemble stable context.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 543–549)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the conversation transcript before this turn. It avoids treating this turn’s own previous write as prior context during replay.

**Data flow**: It asks the transcript store for the saved conversation. If there is no transcript, or the saved transcript is already at this turn’s sequence or later, it returns an empty tuple. Otherwise it returns the stored messages.

**Call relations**: TranscriptRepair.load_messages calls this before adding the current inbound message.

*Call graph*: called by 1 (load_messages).


##### `TranscriptRepair.write_conversation`  (lines 551–571)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a Conversation object to durable transcript storage with a few retries. This makes transcript writes more tolerant of temporary storage failures.

**Data flow**: It receives messages and optional system/injected prompt text. It wraps them in a Conversation with the current turn sequence, tries to write it, logs failures, waits briefly, and retries before giving up silently after the configured attempts.

**Call relations**: TranscriptRepair.persist_transcript and TranscriptRepair.persist_inbound both rely on this as the common transcript-writing path.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `TurnEngine.__post_init__`  (lines 606–613)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that subagent output mode does not conflict with a real tool named finish. The engine reserves finish as the special structured-return tool for subagents.

**Data flow**: After the dataclass is created, it looks at whether an output model is present. If so, it checks the tool registry for a tool named finish and raises an error if one exists. Otherwise it leaves the engine unchanged.

**Call relations**: This runs automatically when a TurnEngine is constructed. It protects later flow in _stream_once, _model_round, and _force_finish from ambiguity.


##### `TurnEngine.run`  (lines 615–764)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs the whole turn from claim to terminal outcome. It is the main body that coordinates context loading, hooks, model rounds, tool work, billing, transcript writes, parking, failure handling, and cleanup.

**Data flow**: It starts with the turn, agent, model, tools, stores, and live hub already wired into the engine. It claims the turn, prepares tool context, fires the inbound hook, loops through model rounds until a terminal result is ready, commits that result, publishes it, and writes the transcript. On parking, cancellation, or error, it records usage where possible, releases messages that should be retried, persists safe inbound text, and cleans up tool context resources.

**Call relations**: This is the top-level method called by the turn workflow. It calls most helper methods in this file, including _mark_running, _model_round, _commit, _park, _persist_transcript, and _persist_inbound.

*Call graph*: calls 11 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _release_unabsorbed, _resolve_unclaimed (+1 more)); 7 external calls (__init__, __init__, __init__, __init__, emit_metric, log, turn_span).


##### `TurnEngine.run.rank_find`  (lines 628–645)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Lets browser tooling ask the model to rank page elements for a find operation. It is host-side helper work whose token usage still counts toward the same turn.

**Data flow**: It receives a small system prompt and user prompt. It sends a model request with reasoning off, collects streamed text into one string, and appends any usage events to the turn’s usage list. It returns the model’s ranking text.

**Call relations**: TurnEngine.run places this function into ToolContext as the find callback. Browser-related tools can call it during dispatch, and its usage is later included in the turn’s billing.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine._scheduled_system`  (lines 766–793)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. Scheduled work has no fresh human present, so remembered context can help the agent act sensibly.

**Data flow**: It receives the current system prompt. It searches memory for the conversation using the turn’s inbound text, with a short timeout. If search fails or finds nothing, it returns the original prompt; otherwise it appends escaped recalled-memory entries.

**Call relations**: TurnEngine.run calls this only for scheduled admissions. It degrades by logging and continuing if memory search is unavailable or slow.

*Call graph*: called by 1 (run); 3 external calls (timeout, escape, log).


##### `TurnEngine._mark_running`  (lines 795–803)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn as running under the current workflow attempt. This is the engine’s single-owner gate.

**Data flow**: It uses the turn ID and attempt stored on the engine. It delegates the database update to _claim_turn and returns whether the claim succeeded.

**Call relations**: TurnEngine.run calls this before doing model or tool work. If it returns false, run switches to _resolve_unclaimed.

*Call graph*: calls 1 internal fn (_claim_turn); called by 1 (run).


##### `TurnEngine._repair`  (lines 805–806)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates a small helper object for transcript repair and transcript writes. It keeps transcript-related actions separate from the main engine.

**Data flow**: It takes the engine’s turn, transcript store, and hub. It returns a TranscriptRepair object that can load, write, or republish transcript state.

**Call relations**: TurnEngine._load_messages, _persist_transcript, _persist_inbound, and _resolve_unclaimed all call this before delegating transcript-related work.

*Call graph*: called by 4 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 808–809)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the messages that should start this turn’s model context. It is a thin wrapper around the transcript repair helper.

**Data flow**: It creates a TranscriptRepair object from the engine state and asks it to load messages. It returns those messages to the caller.

**Call relations**: TurnEngine.run calls this when preparing the first model window or denial transcript.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._model_round`  (lines 811–957)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID]) -> tuple[tuple[Message,
```

**Purpose**: Runs repeated model rounds until the turn has an answer or must force a final response. It is where model streaming, tool dispatch, new-message absorption, compaction, spend checks, truncation recovery, and subagent finish rules come together.

**Data flow**: It receives the tool context, current messages, usage list, system prompt, and mutable logs of absorbed arrivals. Each loop absorbs queued arrivals, checks spend, compacts if needed, streams one model response, records cost, dispatches requested tools, and feeds results back as the next user message. It returns the final message window, answer text, and any structured ask-user, credential, or connect-account request.

**Call relations**: TurnEngine.run calls this inside its terminal-commit loop. It calls helpers such as _absorb_arrivals, _stream_recovering_overflow, _dispatch, _force_final, _force_finish, _publish_cost, and _offload.

*Call graph*: calls 11 internal fn (_absorb_arrivals, _dispatch, _enforce_spend, _force_final, _force_finish, _offload, _publish_cost, _stream_recovering_overflow, _bounded, _dispatch_segments (+1 more)); called by 1 (run); 6 external calls (__init__, __init__, __init__, gather, emit_metric, log).


##### `TurnEngine._absorb_arrivals`  (lines 959–981)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID]) -> tuple[Message, ...]
```

**Purpose**: Adds new inbound messages that arrived while the agent was already working. This prevents the agent from closing a reply while ignoring newly queued user input.

**Data flow**: It receives the current messages plus mutable arrival and ID logs. For normal turns, it claims arrival rows, appends their IDs, skips denied arrivals, converts rendered arrivals into user messages, records them, and returns the expanded message tuple. For subagent turns, it returns the original messages unchanged.

**Call relations**: TurnEngine._model_round calls this at the start of every round. It delegates the durable drain to _claim_arrivals.

*Call graph*: calls 1 internal fn (_claim_arrivals); called by 1 (_model_round); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 983–1006)

```
async def _render_arrival(self, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> str | None
```

**Purpose**: Turns one queued inbound message into the exact text the model should see. It applies the same prompt-submission hook used for the founding message.

**Data flow**: It receives the raw body, stored context, speaker ID, and creation time. It fires the user_prompt_submit hook, returns None if the hook denies the message, otherwise prefixes a context tag and appends injected context in its own marked block.

**Call relations**: TurnEngine._claim_arrivals calls this for each claimed row so the rendered result is recorded with the drain step.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1009–1051)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Durably drains pending inbound messages for this conversation into the current turn. Because it is a recorded step, replay sees the same drained batch instead of consuming different messages.

**Data flow**: It receives IDs already absorbed by this run. It marks eligible inbound rows as consumed by this turn, reads their data, sorts them by sequence, renders each arrival, and returns Arrival objects containing the row ID and rendered text or None.

**Call relations**: TurnEngine._absorb_arrivals calls this. It calls _render_arrival for hook processing and context formatting.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 6 external calls (__init__, model_validate, and_, or_, update, workspace_tx).


##### `TurnEngine._release_unabsorbed`  (lines 1053–1072)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns arrival messages to the pending queue if this run stamped them but did not safely absorb them. This avoids losing user messages after cancellation or failure.

**Data flow**: It receives the IDs known to have been absorbed. It best-effort clears consumed_turn_id for other rows stamped with this turn ID. If the database update fails, it logs the problem and lets later admission recovery handle what remains.

**Call relations**: TurnEngine.run calls this on cancellation and error paths, after billing or committing failure as appropriate.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1074–1102)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a closing response when the model has spent its allowed number of tool-use rounds. This avoids endless tool loops and gives the user the best available answer.

**Data flow**: It receives messages, usage, and system prompt. It logs and emits a metric, checks spend, compacts if needed, then either forces a subagent finish call or adds a no-more-tools prompt and streams one final model response without tools. It returns the updated messages and final text.

**Call relations**: TurnEngine._model_round calls this after its normal round loop is exhausted. It may call _force_finish for subagents, or _stream_recovering_overflow and _publish_cost for ordinary turns.

*Call graph*: calls 4 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_recovering_overflow); called by 1 (_model_round); 3 external calls (__init__, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 1104–1129)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to end through the reserved finish tool so the parent receives schema-shaped data instead of free-form prose. It fails loudly if the model still does not obey the output contract.

**Data flow**: It receives messages, usage, and system prompt. It streams one round with only the finish tool offered and required, publishes cost, validates the finish arguments against the output model, and returns the messages plus canonical JSON answer. If the call is missing or invalid, it raises an error.

**Call relations**: TurnEngine._model_round uses this when a subagent tries to end with prose. TurnEngine._force_final uses it when a subagent exhausts its round budget.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 1131–1168)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False) -> tuple[tuple[Message, ...], str,
```

**Purpose**: Runs one model round and retries once after forced compaction if the provider says the context is too large. It separates recoverable context overflow from ordinary model failures.

**Data flow**: It receives messages, usage, system prompt, and tool-offering options. It calls _stream_once, adds usage, and converts recorded stream errors into ModelStreamError. If an exception looks like context overflow, it forces compaction, adds compaction usage, retries once, and returns the compacted messages plus response text and tool calls.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish all call this instead of calling _stream_once directly. It uses is_context_overflow to decide whether retry is safe.

*Call graph*: calls 2 internal fn (__init__, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 3 external calls (is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 1170–1207)

```
async def _enforce_spend(self, usage_events: list[Usage]) -> None
```

**Purpose**: Checks whether the turn may continue spending tokens. It also rechecks seat access so a revoked member stops before the next model call.

**Data flow**: It receives the in-memory usage events for this attempt. It checks seat admission when relevant, then skips accounting work if no caps apply. Otherwise it prices current in-flight usage, asks the spend evaluator for a decision, and raises TurnParked with the decision message when work must pause.

**Call relations**: TurnEngine._model_round calls this before each model round, and _force_final calls it before the forced closing round. TurnEngine.run catches TurnParked and records a parked state.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 6 external calls (__init__, __init__, applicable_caps_absent, workspace_tx, gate_member, seat_gate_absent).


##### `TurnEngine._stream_once`  (lines 1210–1317)

```
async def _stream_once(self, messages: tuple[Message, ...], system: str, offer_tools: bool=True, force_finish: bool=False) -> StreamResult
```

**Purpose**: Performs one actual model streaming request and records the result as a replayable step. It streams live text to clients while collecting final text, tool calls, usage, and any mid-stream error.

**Data flow**: It receives messages, system prompt, and flags controlling whether tools or forced finish are offered. It builds a model request, collects text deltas, tool-call starts and argument fragments, and usage events. It flushes live text to the hub as it arrives. On stream error it returns a StreamResult carrying usage and partial output; on success it parses tool arguments and returns text, tool calls, and usage.

**Call relations**: TurnEngine._stream_recovering_overflow is its caller. The recorded StreamResult lets crash replay avoid calling the model again.

*Call graph*: calls 1 internal fn (_parse_args); called by 1 (_stream_recovering_overflow); 5 external calls (__init__, __init__, __init__, __init__, monotonic).


##### `TurnEngine._stream_once.flush`  (lines 1266–1272)

```
async def flush() -> None
```

**Purpose**: Publishes buffered text chunks from a streaming model response to the live hub. It keeps clients updated without publishing every tiny fragment one by one.

**Data flow**: It reads the current buffer of text chunks. If there is content, it joins and publishes it as a TextDelta, clears the buffer, resets the byte count, and records the latest flush time.

**Call relations**: TurnEngine._stream_once calls this during streaming when the buffer is large enough or old enough, and once more at the end.

*Call graph*: 2 external calls (__init__, monotonic).


##### `TurnEngine._publish_cost`  (lines 1319–1332)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the turn’s live cost and token count so the user interface can show spending as the turn progresses. It does not perform final billing.

**Data flow**: It receives usage events, totals them, prices them with the model pricing table, counts all token categories, and sends a CostTick frame through _publish.

**Call relations**: TurnEngine._model_round calls this after model rounds. _force_final and _force_finish also call it after forced closing streams.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._dispatch`  (lines 1334–1361)

```
async def _dispatch(self, context: ToolContext, call: ToolUseBlock) -> ToolResultBlock
```

**Purpose**: Runs one tool call and rebuilds the model-facing result, including any images that were stored outside the replay log. It is the safe wrapper around the recorded dispatch step.

**Data flow**: It receives the tool context and one tool-use block. It calls _dispatch_step to get bounded text, error status, and image references. If there are image references, it reads their bytes from the blob store and returns a ToolResultBlock with text and image blocks; otherwise it returns a plain text ToolResultBlock.

**Call relations**: TurnEngine._model_round calls this for each tool in each dispatch segment. It delegates side-effect-sensitive work to _dispatch_step.

*Call graph*: calls 1 internal fn (_dispatch_step); called by 1 (_model_round); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._offload`  (lines 1363–1388)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large or salvaged text into the sandbox’s tool-output directory and returns the path. This keeps huge content out of the model context while still making it available to the agent.

**Data flow**: It receives a file name and text content. It ensures the tool-output directory exists, writes the bytes into the sandbox, logs and emits a metric on failure, and returns the path or None.

**Call relations**: TurnEngine._dispatch_step calls this for oversized successful tool output. TurnEngine._model_round calls it to save partial model output after truncation.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._dispatch_step`  (lines 1391–1520)

```
async def _dispatch_step(self, context: ToolContext, call: ToolUseBlock) -> DispatchResult
```

**Purpose**: Executes one tool call as a replayable step. It validates input, runs hooks, invokes the tool, bounds or offloads output, protects the model from untrusted content, stores large images separately, and returns a compact dispatch result.

**Data flow**: It receives tool context and a tool call. It announces activity, looks up and validates the tool, runs the pre-tool hook, executes the handler with an idempotency key when needed, gathers text and images, turns exceptions into error text, offloads large successful text, wraps untrusted output, fires the proper post hook, resizes and stores images in the blob store, and returns a DispatchResult.

**Call relations**: TurnEngine._dispatch calls this and then rehydrates image references. It calls _publish_activity, _offload, _bounded, and _bounded_image as part of tool execution.

*Call graph*: calls 4 internal fn (_bounded_image, _offload, _publish_activity, _bounded); called by 1 (_dispatch); 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, replace).


##### `TurnEngine._bounded_image`  (lines 1522–1550)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized tool-result images before they are sent back to the model. This avoids provider limits and avoids paying for pixels that add little value.

**Data flow**: It receives an ImageBlock whose data is base64 text. It decodes and opens the image, returns it unchanged if already small, otherwise resizes it to the edge limit, saves it in a suitable format, re-encodes it, and returns a new ImageBlock. If decoding or resizing fails, it logs and returns the original image.

**Call relations**: TurnEngine._dispatch_step calls this before writing image content to the blob store.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._publish_activity`  (lines 1552–1571)

```
async def _publish_activity(self, call: ToolUseBlock) -> None
```

**Purpose**: Sends a live activity update when a tool call starts. This lets users see that the agent is doing work during long tool-heavy turns.

**Data flow**: It receives a tool call. For load_skill it publishes the skill name. For other tools it builds a short preview from the arguments and uses the model-provided user_description when present. It sends the frame through _publish.

**Call relations**: TurnEngine._dispatch_step calls this before validating and running the tool. Publish failures are absorbed by _publish.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_dispatch_step); 3 external calls (__init__, __init__, dumps).


##### `TurnEngine._commit`  (lines 1573–1623)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request: CredentialRequest |
```

**Purpose**: Persists the terminal state of a turn and publishes it live. It retries database commit failures so a temporary outage does not lose the turn ending.

**Data flow**: It receives the desired status, usage, answer, optional error and request payloads, plus arrival-safety options. It repeatedly calls _commit_once with backoff until it gets a frame or a no-commit signal. If a frame is returned, it publishes a Terminal frame, emits a metric, logs the terminal, and returns it.

**Call relations**: TurnEngine.run calls this for done and failed outcomes. It relies on _commit_once for the actual database transaction and _publish for the live notification.

*Call graph*: calls 2 internal fn (_commit_once, _publish); called by 1 (run); 4 external calls (__init__, sleep, emit_metric, log).


##### `TurnEngine._commit_once`  (lines 1625–1723)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction to bill usage and store the turn’s terminal frame. It can refuse to commit if new arrivals are still unabsorbed.

**Data flow**: It totals usage, optionally locks the conversation and checks for pending or unrecorded arrivals, records turn usage, reads final cost, builds a TerminalFrame with status, answer, error details, tokens, cost, cache percentage, model, reasoning, and any structured request, then updates the turn row. If another execution already committed, it reads and returns the existing terminal frame.

**Call relations**: TurnEngine._commit calls this inside a retry loop. It uses shared accounting helpers and _total_usage to keep terminal billing consistent.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 9 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx).


##### `TurnEngine._park`  (lines 1725–1762)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Stores a non-terminal parked state when spend or seat rules stop a running turn. Parking preserves work and allows a later resume instead of failing the turn.

**Data flow**: It receives a message and usage events. In one database transaction it marks the turn parked, bills consumed usage for this attempt, and releases inbound messages stamped by this turn so a resumed workflow can drain them again. If the update succeeded, it publishes a Parked frame and emits/logs the parked event.

**Call relations**: TurnEngine.run calls this after catching TurnParked from _enforce_spend.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 1764–1773)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes a live frame to the hub without letting publish failures break the turn. Durable database state remains the source of truth.

**Data flow**: It receives a live frame such as text, cost, tool activity, parked, or terminal. It tries to publish it for this turn ID. If publishing fails, it logs the error and returns normally.

**Call relations**: TurnEngine._commit, _park, _publish_activity, and _publish_cost all use this shared safe publishing path.

*Call graph*: called by 4 (_commit, _park, _publish_activity, _publish_cost); 1 external calls (log).


##### `TurnEngine._bill_cancelled`  (lines 1775–1794)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for a turn that was cancelled after consuming model tokens. It tries to count real usage without delaying cancellation too much.

**Data flow**: It receives usage events, totals them, and attempts to record usage in the database for the current attempt. If billing fails, it logs the failure and does not raise.

**Call relations**: TurnEngine.run calls this when DBOS cancellation or asyncio cancellation interrupts the turn.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 1796–1801)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this execution did not win ownership of the turn. It either republishes an already committed terminal or steps aside while another execution continues.

**Data flow**: It creates a TranscriptRepair helper and asks it to resolve the turn. The result is a terminal frame if the turn had already finished, or None if it is still running elsewhere.

**Call relations**: TurnEngine.run calls this after _mark_running returns false. It delegates to TranscriptRepair.resolve through _repair.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._persist_transcript`  (lines 1803–1806)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Persists the full successful transcript through the repair helper. It keeps the main run method from knowing transcript-writing details.

**Data flow**: It receives messages, answer, system prompt, and injected prompt text. It creates a TranscriptRepair helper and delegates the write.

**Call relations**: TurnEngine.run calls this after a done terminal is committed. It delegates through _repair to TranscriptRepair.persist_transcript.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._persist_inbound`  (lines 1808–1809)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=()) -> None
```

**Purpose**: Persists only inbound user messages for a turn that did not finish with a normal answer. This protects future context without saving bad assistant output.

**Data flow**: It receives optional arrival messages. It creates a TranscriptRepair helper and delegates the inbound-only write.

**Call relations**: TurnEngine.run calls this on non-done endings and some error or cancellation paths. It delegates through _repair to TranscriptRepair.persist_inbound.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).

## 📊 State Registers Touched

- `reg-workspace-directory` — The shared record of workspaces, members, owners, agents, and workspace boundaries.
- `reg-conversation-transcript` — The stored conversation history, messages, files, speakers, and outcomes that later stages read and append to.
- `reg-turn-state` — The durable status of each unit of agent work, including whether it is waiting, running, paused, finished, failed, or cancelled.
- `reg-live-stream` — The live feed of turn updates, text chunks, tool events, costs, and final frames that clients and debuggers can watch.
- `reg-cancellation-state` — The shared stop signal state used to cancel active turns, child tasks, tools, and abandoned work safely.
- `reg-model-catalog` — The shared list of available AI models, providers, limits, prices, and client adapters.
- `reg-prompt-state` — The agent instructions, rendered prompt templates, fingerprints, and governed prompt-change proposals.
- `reg-compaction-state` — The saved summaries and reduced conversation versions used when a conversation is too large for a model call.
- `reg-tool-catalog` — The shared catalog of tools the model may call, including their names, schemas, handlers, and safety properties.
- `reg-tool-context` — The per-run authority envelope that gives tools only the workspace, credentials, cleanup hooks, and permissions they are allowed to use.
- `reg-sandbox-session` — The sandbox handle and lifecycle state for the safe workspace where code, files, browsers, and commands run.
- `reg-browser-session` — The browser automation connection state used when tools need a controlled browser for a turn.
- `reg-subagent-tree` — The shared parent-child work structure for delegated agents, including child turns, messages, waits, and cancellations.
- `reg-accounting-ledger` — The usage, price, spend-cap, billing, export, and cost records used to track and limit money spent by workspaces and turns.
- `reg-observability-context` — The shared tracing, metrics, structured logs, and trace-parent links used to understand work across processes and turns.
- `reg-todo-checklist` — The per-conversation persistent checklist or task-progress state maintained by the todo extension across agent turns.
- `reg-turn-admission-context` — Durable per-turn requester/speaker/on-behalf-of, timezone, surface context, and authorization-link metadata used to attribute, resume, and safely handle work.
- `reg-agent-runtime-settings` — Persistent non-prompt agent configuration such as selected runtime profile, workflow/tool policy, internet-access setting, and conversation or surface agent bindings.
- `reg-turn-replay-journal` — Durable per-turn execution checkpoints for model calls, tool results, and side-effect/idempotency markers used to resume work without duplicating paid calls or irreversible actions.
