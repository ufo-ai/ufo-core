# Agent reasoning loop, tool dispatch, and subagent orchestration  `stage-11`

This stage is the agent’s main work loop for one user request. It is where a queued turn becomes real action: `engine.py` claims the turn, prepares the prompt, reads the model’s streamed decisions, runs requested tools, records costs, absorbs any new messages, and finishes, pauses, or fails safely.

The built-in tools are the basic workbench: they let the agent read and edit files, run commands, ask the user questions, manage artifacts, and keep a todo list. The sandbox, browser, and website tools add a safe workshop for running code and controlling web pages. External connector and research tools let the agent use web search and business services through protected adapters, so secrets are not exposed. Document and Office tools handle PDFs, Word, PowerPoint, and spreadsheets.

Subagents are helper workers. `subagents.py` starts, tracks, waits for, messages, or cancels child agent turns. Browser, research, and website delegation files package larger jobs into specialized child agents, so the main agent can split work and combine the results.

## Sub-stages

- [Built-in workspace, artifact, and conversation tools](stage-11.1.md) `stage-11.1` — 2 files
- [Sandboxed code, browser, and website automation](stage-11.2.md) `stage-11.2` — 21 files
- [External connector, research, and business-tool execution](stage-11.3.md) `stage-11.3` — 12 files
- [Document, Office, PDF, and skill-script execution](stage-11.4.md) `stage-11.4` — 19 files

## Files in this stage

### Turn execution loop
Core turn orchestration claims the request, streams model work, dispatches tools, tracks usage, absorbs messages, and commits the final outcome.

### `core/src/ufo/loop/engine.py`

`orchestration` · `turn execution`

Think of this file as the conductor for one conversation turn. It makes sure only one worker owns the turn, then gathers the earlier transcript and the new user message, adds useful context like time and sender, and starts asking the model what to do. If the model calls tools, the engine runs those tools, feeds the results back, and repeats until the model gives a final answer or the round limit is reached. While this happens, new user messages can arrive; the engine drains them between model rounds so the answer does not ignore someone who spoke while the bot was working. The file is careful about crashes and retries. Important actions are DBOS steps, meaning their results are recorded so a replay can resume without re-calling the model or re-running side-effecting tools. It also protects the system from runaway cost by checking spend caps, records token usage for billing, parks a turn when limits are hit, and publishes live updates like streamed text, tool activity, and cost ticks. It also keeps large tool outputs and images out of replay logs by writing them to files or blob storage and returning references instead.

#### Function details

##### `_claim_turn`  (lines 164–206)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> bool
```

**Purpose**: Marks a queued or parked turn as running for one specific execution attempt. This prevents two workers from doing the same turn at the same time.

**Data flow**: It receives a turn id and an attempt id, reads the turn's conversation, locks that conversation row, and updates the turn if it is claimable. It returns true when this attempt owns the turn, and false when another execution has it or it is already finished.

**Call relations**: TurnEngine._mark_running uses this at the start of a run. _claim_turn_with_handoff also uses it before looking for a following queued turn to hand off.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 6 external calls (and_, delete, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 217–274)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[bool, _TurnHandoff | None]
```

**Purpose**: Claims the current turn and, if possible, reserves the next queued turn in the same conversation for later dispatch. This keeps work moving in order.

**Data flow**: It receives a turn id and attempt id, first tries to claim the turn, then locks the conversation and searches for the next queued turn. It returns whether the claim succeeded and, when a next turn was stamped for dispatch, a small handoff record describing it.

**Call relations**: It builds on _claim_turn. It is meant for dispatch code that wants to claim one turn while also preparing the next one without racing another worker.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `ModelStreamError.__init__`  (lines 358–359)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Stores the model provider's original error class, message, and any partial text already streamed. This lets the engine bill and recover from partial model output instead of losing it.

**Data flow**: It receives the model error name, message, and optional partial output, and places all three into the exception's arguments. The exception object then carries those details through later error handling.

**Call relations**: TurnEngine._stream_recovering_overflow creates this after a recorded model round reports an error. Later engine logic can inspect the stored fields instead of treating every stream failure the same.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 361–363)

```
def __str__(self) -> str
```

**Purpose**: Formats a model stream error as readable text while keeping the provider's error class visible. That matters because other code detects context-size failures from the error text.

**Data flow**: It reads the stored error class and message from the exception and returns a single string like 'SomeError: message'. It deliberately leaves out the partial output.

**Call relations**: This is used whenever the exception is converted to text, such as logging or terminal error recording.


##### `ModelStreamError.model_error_class`  (lines 366–368)

```
def model_error_class(self) -> str
```

**Purpose**: Gives callers the model provider's original error class name. The engine uses that distinction to decide whether an error is recoverable.

**Data flow**: It reads the first stored exception argument and returns it unchanged. Nothing else is modified.

**Call relations**: The stream recovery and commit paths use this information to preserve the model's own error identity.


##### `ModelStreamError.partial_output`  (lines 371–373)

```
def partial_output(self) -> str
```

**Purpose**: Returns text or tool-call fragments that arrived before the model stream failed. This can be saved so the next attempt can salvage work already paid for.

**Data flow**: It reads the stored partial output from the exception arguments and returns it. It does not write anything itself.

**Call relations**: TurnEngine._model_round uses this after a truncation-style stream failure to offload partial content and tell the model where to find it.


##### `ModelStreamError.model_error_message`  (lines 376–378)

```
def model_error_message(self) -> str
```

**Purpose**: Gives callers the model provider's original error message. This keeps terminal failures useful and specific.

**Data flow**: It reads the stored message from the exception arguments and returns it. No state changes.

**Call relations**: TurnEngine._commit_once uses this kind of information when building a terminal frame for a failed turn.


##### `TurnParked.__init__`  (lines 385–387)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates the special exception used when a turn must pause because a spend or seat limit was hit. Parking is not a failure; it is a resumable stop.

**Data flow**: It receives a message for the user or surface, stores it on the exception, and passes it to the base exception. The message later becomes the parked-frame reason.

**Call relations**: TurnEngine._enforce_spend raises this. TurnEngine.run catches it and calls _park to record the resumable parked state.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 390–414)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits model-requested tool calls into safe execution groups. Tools marked safe for parallel work can run together; other tools become ordered barriers.

**Data flow**: It receives the tool registry and the model's ordered tool calls. It checks each tool's safety flag and yields groups of calls that can be dispatched together without changing the intended order.

**Call relations**: TurnEngine._model_round uses this before running tools. The resulting groups let the engine speed up independent reads while keeping risky or unknown actions serialized.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 417–419)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed JSON fragments for a tool call into a Python dictionary. If the model supplied no arguments, it returns an empty dictionary.

**Data flow**: It receives a list of partial JSON strings, joins them, and parses the result when it contains non-whitespace text. The output is the tool input object used to build a ToolUseBlock.

**Call relations**: TurnEngine._stream_once calls this after the model stream finishes assembling each tool call.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 422–432)

```
def _context_tag(context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the small context header placed before a user's message, including when it was admitted and who sent it. This gives the model time and speaker information it would not otherwise know.

**Data flow**: It receives optional turn context and an admission timestamp. It chooses the sender's timezone when available, formats the time, adds the sender name when present, and returns a '<context>' text block.

**Call relations**: TranscriptRepair.load_messages uses it for the founding message. TurnEngine._render_arrival uses it for later messages absorbed while the turn is running.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 435–441)

```
def _bounded(content: str) -> str
```

**Purpose**: Cuts overly large text down to the maximum size allowed for a tool result. This keeps model context and logs from being overwhelmed.

**Data flow**: It receives a string. If it is short enough, it returns it unchanged; otherwise it returns the beginning plus a note saying how much was truncated.

**Call relations**: TurnEngine._dispatch_step uses it for large error text. TurnEngine._model_round uses it when reporting finish-tool validation errors back to the model.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_final_act`  (lines 444–462)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Detects whether the last tool call in a round was a successful final action, such as asking the user or requesting credentials. It extracts the structured payload from that tool's result.

**Data flow**: It receives the round's tool calls, their results, the expected final tool name, and a validation model. It checks the last call and result, parses JSON from the result text, validates it, and returns the payload or None.

**Call relations**: TurnEngine._model_round calls this after tool dispatch to decide whether the turn should end with a question, credential request, or account connection request.

*Call graph*: called by 1 (_model_round); 1 external calls (loads).


##### `_total_usage`  (lines 465–471)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many token-usage records into one total. This gives billing, cost display, and spend checks a single number to work with.

**Data flow**: It receives a list of usage events and sums input, output, cache-read, and cache-write tokens separately. It returns one Usage object containing those totals.

**Call relations**: The engine uses this in cost publishing, spend enforcement, terminal commit, parking, and cancellation billing.

*Call graph*: called by 5 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 486–510)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal result for a turn that is already finished but whose live notification may have been missed. This helps a waiting client complete after a crash or duplicate delivery.

**Data flow**: It reads the turn's stored terminal frame from the database. If no terminal exists, it returns None; otherwise it persists the inbound transcript if needed, publishes the terminal to the hub, and returns the frame.

**Call relations**: TurnEngine._resolve_unclaimed reaches this when the current execution cannot claim the turn. It uses persist_inbound before publishing so the transcript is not lost.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 512–519)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the completed conversation transcript after a successful answer. It includes the assistant's final answer along with the messages the model saw.

**Data flow**: It receives the messages, answer, system prompt, and injected prompt text. It appends an assistant message containing the answer and passes the full conversation to write_conversation.

**Call relations**: TurnEngine._persist_transcript delegates here after a done terminal is committed.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 521–528)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=()) -> None
```

**Purpose**: Preserves user messages when the turn ends without a normal assistant answer, such as failure, cancellation, or parking. This prevents user input from disappearing from future context.

**Data flow**: It loads prior messages plus the founding inbound, appends any absorbed arrival messages, and writes that conversation. It does not persist partial assistant error text.

**Call relations**: TranscriptRepair.resolve may call this for repair. TurnEngine._persist_inbound delegates here on non-done exits.

*Call graph*: calls 2 internal fn (load_messages, write_conversation); called by 1 (resolve).


##### `TranscriptRepair.load_messages`  (lines 530–538)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Builds the message list that starts this turn: prior transcript plus this turn's inbound user message. For normal member turns, it prefixes the inbound with the context tag.

**Data flow**: It reads the turn's inbound text and prior transcript. It adds context for non-subagent turns, wraps the inbound as a user message, and returns the combined tuple.

**Call relations**: TurnEngine._load_messages and TranscriptRepair.persist_inbound use this as the canonical way to reconstruct the turn's starting conversation.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 540–546)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the saved conversation before this turn, while avoiding accidentally reading this turn's own transcript during a replay. This keeps replay context clean.

**Data flow**: It asks the transcript store for the latest saved conversation. If none exists, or if the saved sequence is at or after this turn, it returns an empty tuple; otherwise it returns the stored messages.

**Call relations**: TranscriptRepair.load_messages calls this before appending the current inbound.

*Call graph*: called by 1 (load_messages).


##### `TranscriptRepair.write_conversation`  (lines 548–568)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a conversation record to durable transcript storage with a few retries. This makes transcript persistence more tolerant of short storage outages.

**Data flow**: It receives messages plus optional system and injected text, builds a Conversation object, and tries to write it. On failure it logs, waits briefly, and retries before giving up silently after the configured attempts.

**Call relations**: persist_transcript and persist_inbound both use this final writer.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `TurnEngine.__post_init__`  (lines 603–610)

```
def __post_init__(self) -> None
```

**Purpose**: Checks a subagent safety rule after the engine is created. A subagent that needs a special finish tool must not also have a normal tool with the same name.

**Data flow**: It inspects output_model and the tool registry. If a finish tool name conflict exists, it raises an error; otherwise it leaves the engine unchanged.

**Call relations**: This runs automatically after TurnEngine is constructed, before run begins.


##### `TurnEngine.run`  (lines 612–761)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs the whole turn lifecycle. It claims ownership, prepares context, loops through model and tool work, commits the terminal state, and cleans up resources.

**Data flow**: It starts with the turn, agent, prompt, model, tools, storage, and live hub already wired into the engine. It collects usage and arrivals, fires hooks, calls model rounds, commits done/failed/parked states, writes transcripts, bills usage, publishes live frames, and returns the terminal frame when the turn finishes.

**Call relations**: This is the main body used by the turn workflow. It calls most helper methods in this file and is the place where errors, cancellation, parking, and successful completion are routed to the right cleanup path.

*Call graph*: calls 11 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _release_unabsorbed, _resolve_unclaimed (+1 more)); 7 external calls (__init__, __init__, __init__, __init__, emit_metric, log, turn_span).


##### `TurnEngine.run.rank_find`  (lines 625–642)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Lets the browser find tool ask the model to rank page elements. It is a host-side helper, not code run inside the sandbox.

**Data flow**: It receives a system prompt and user prompt, builds a small model request with reasoning off, streams text chunks into a string, and adds any usage events to the turn's running usage list. It returns the ranking text.

**Call relations**: TurnEngine.run places this function into ToolContext as the find callback so browser tools can call it while their token use is still charged to the same turn.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine._scheduled_system`  (lines 763–779)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. Scheduled turns do not come directly from a live user, so this gives them relevant past context.

**Data flow**: It receives the current system prompt, searches memory for the turn's inbound topic with a short timeout, escapes the returned text for safe markup, and appends it when matches exist. If search fails or returns nothing, it leaves the prompt unchanged.

**Call relations**: TurnEngine.run calls this only for scheduled admissions before the first model round.

*Call graph*: called by 1 (run); 3 external calls (timeout, escape, log).


##### `TurnEngine._mark_running`  (lines 781–789)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn for the current engine attempt. It is the engine's wrapper around the database claim helper.

**Data flow**: It passes the turn id and attempt id to _claim_turn and returns that boolean result. No other state is changed in the engine object.

**Call relations**: TurnEngine.run calls this at startup. If it returns false, run switches to the repair path instead of executing the turn.

*Call graph*: calls 1 internal fn (_claim_turn); called by 1 (run).


##### `TurnEngine._repair`  (lines 791–792)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Builds a TranscriptRepair helper tied to this turn, transcript store, and hub. This keeps transcript repair behavior separate from the main engine object.

**Data flow**: It reads the engine's turn, transcript, and hub fields and returns a new TranscriptRepair instance. It does not perform I/O by itself.

**Call relations**: The engine's load, persist, and unclaimed-resolution helpers all create a repair helper through this method.

*Call graph*: called by 4 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 794–795)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the starting messages for this turn through the transcript repair helper. It keeps the main run method from knowing transcript details.

**Data flow**: It creates a TranscriptRepair and asks it to load messages. The output is the message tuple used as the initial model context.

**Call relations**: TurnEngine.run calls this when preparing either a normal prompt or a denied prompt path.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._model_round`  (lines 797–942)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID]) -> tuple[tuple[Message,
```

**Purpose**: Runs the repeated model/tool loop until the model gives a final answer or the engine forces one. This is where most turn work actually happens.

**Data flow**: It receives tool context, current messages, usage list, system prompt, and arrival tracking lists. Each loop absorbs new arrivals, checks spending, compacts context if needed, streams one model response, dispatches any tools, feeds results back, and eventually returns final messages, answer text, and any pending question or request object.

**Call relations**: TurnEngine.run calls this after setup. It coordinates _absorb_arrivals, _stream_recovering_overflow, _dispatch, _publish_cost, _force_final, and _force_finish.

*Call graph*: calls 11 internal fn (_absorb_arrivals, _dispatch, _enforce_spend, _force_final, _force_finish, _offload, _publish_cost, _stream_recovering_overflow, _bounded, _dispatch_segments (+1 more)); called by 1 (run); 6 external calls (__init__, __init__, __init__, gather, emit_metric, log).


##### `TurnEngine._absorb_arrivals`  (lines 944–966)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID]) -> tuple[Message, ...]
```

**Purpose**: Adds queued user messages that arrived while the turn was running. This lets one long-running turn respond to fresh input instead of closing over stale context.

**Data flow**: It receives the current message list and tracking lists. It claims arrivals, records their ids, turns rendered arrivals into user messages, appends them to the model context and arrival log, and returns the expanded message tuple.

**Call relations**: TurnEngine._model_round calls this at the start of each round. It delegates the durable queue drain to _claim_arrivals.

*Call graph*: calls 1 internal fn (_claim_arrivals); called by 1 (_model_round); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 968–991)

```
async def _render_arrival(self, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> str | None
```

**Purpose**: Turns one queued inbound message into the exact text the model should see. It applies prompt hooks and adds context like time and speaker.

**Data flow**: It receives the arrival body, context, speaker id, and creation time. It fires the user prompt hook, returns None if denied, otherwise prefixes a context tag and appends any injected context in a separate block.

**Call relations**: TurnEngine._claim_arrivals calls this for every claimed inbound row so the recorded arrival batch includes final rendered text.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 994–1036)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Durably drains pending inbound messages for this conversation into the current turn. It is recorded as a DBOS step so replay sees the same batch.

**Data flow**: It receives ids already absorbed by this run, stamps claimable inbound rows with this turn id, reads their content and metadata, renders them, and returns Arrival records in sequence order.

**Call relations**: TurnEngine._absorb_arrivals calls this between model rounds. Because it is memoized, a crash replay does not consume a different set of arrivals or re-fire hooks for an already recorded drain.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 6 external calls (__init__, model_validate, and_, or_, update, workspace_tx).


##### `TurnEngine._release_unabsorbed`  (lines 1038–1057)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns arrivals that were claimed but not fully absorbed when a run fails or is cancelled. This reduces the chance of losing a message in an awkward crash window.

**Data flow**: It receives ids known to have been absorbed, then clears the consumed marker for other inbound rows stamped by this turn. If the database operation fails, it logs and continues.

**Call relations**: TurnEngine.run calls this on cancellation and failures so a later turn can drain those messages.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1059–1087)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Produces a best-effort closing answer when the normal tool-use round budget is exhausted. This avoids failing a turn just because the model kept looping.

**Data flow**: It receives messages, usage, and system prompt, logs the budget exhaustion, checks spending, compacts context, and either forces a subagent finish tool call or asks the model for one final no-tool answer. It returns updated messages and final text.

**Call relations**: TurnEngine._model_round calls this after all normal rounds are used. It may call _force_finish for schema-bound subagents.

*Call graph*: calls 4 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_recovering_overflow); called by 1 (_model_round); 3 external calls (__init__, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 1089–1114)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to end through the special finish tool. This guarantees the parent receives data shaped like the subagent's declared output schema.

**Data flow**: It receives messages, usage, and system prompt, makes one model call where only finish is offered and required, publishes cost, validates the tool arguments against the output model, and returns canonical JSON. It raises if the model does not produce a valid lone finish call.

**Call relations**: TurnEngine._model_round uses this when a subagent answered in prose. _force_final also uses it when a subagent runs out of rounds.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 1116–1153)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False) -> tuple[tuple[Message, ...], str,
```

**Purpose**: Runs one model stream and retries once after forced context compaction if the provider says the prompt is too large. This gives the turn a chance to recover from context overflow.

**Data flow**: It receives messages, usage, system prompt, and tool-offering flags. It calls _stream_once, adds usage, converts recorded stream errors into ModelStreamError, and on context overflow compacts the messages and retries once. It returns the possibly compacted messages, text, and tool calls.

**Call relations**: _model_round, _force_final, and _force_finish use this for model calls. It is the layer between raw streaming and higher-level turn logic.

*Call graph*: calls 2 internal fn (__init__, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 3 external calls (is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 1155–1192)

```
async def _enforce_spend(self, usage_events: list[Usage]) -> None
```

**Purpose**: Checks whether the turn may continue spending tokens before another model call. If caps or seat rules are violated, it parks the turn instead of letting costs run past limits.

**Data flow**: It receives the usage events so far, checks seat admission when relevant, calculates pending cost from token usage, asks the spend evaluator for a decision, and raises TurnParked if the turn must pause. If no limits apply or the decision allows spending, it returns normally.

**Call relations**: TurnEngine._model_round calls this before each model round. _force_final also calls it before the forced closing round.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 6 external calls (__init__, __init__, applicable_caps_absent, workspace_tx, gate_member, seat_gate_absent).


##### `TurnEngine._stream_once`  (lines 1195–1302)

```
async def _stream_once(self, messages: tuple[Message, ...], system: str, offer_tools: bool=True, force_finish: bool=False) -> StreamResult
```

**Purpose**: Performs one actual model request and records the result as a DBOS step. It streams live text to listeners while collecting final text, tool calls, and usage.

**Data flow**: It receives messages, system prompt, and flags controlling tool availability. It builds a model request, consumes streamed text, tool-call starts, tool-call argument fragments, and usage events, flushes text deltas to the hub, and returns a StreamResult. If the stream errors mid-way, it returns the error details and partial output inside the result instead of raising.

**Call relations**: TurnEngine._stream_recovering_overflow is the only caller. Because this is a memoized step, replay can reuse the recorded model round rather than spending tokens again.

*Call graph*: calls 1 internal fn (_parse_args); called by 1 (_stream_recovering_overflow); 5 external calls (__init__, __init__, __init__, __init__, monotonic).


##### `TurnEngine._stream_once.flush`  (lines 1251–1257)

```
async def flush() -> None
```

**Purpose**: Sends buffered text chunks from a model stream to live listeners. It batches small chunks so the hub is not flooded.

**Data flow**: It reads the local text buffer, publishes a TextDelta when there is content, clears the buffer, resets the pending byte count, and updates the last-flush time.

**Call relations**: This helper lives inside _stream_once and is called during streaming and once at the end to publish any remaining text.

*Call graph*: 2 external calls (__init__, monotonic).


##### `TurnEngine._publish_cost`  (lines 1304–1317)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the turn's current token and cost total as a live update. This lets user interfaces show a running cost meter before the turn commits.

**Data flow**: It totals usage events, prices them for the agent's model, counts all token types, builds a CostTick frame, and sends it through _publish. It does not write billing records itself.

**Call relations**: _model_round, _force_final, and _force_finish call this after model calls. Final durable billing happens later in commit, park, or cancellation code.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._dispatch`  (lines 1319–1346)

```
async def _dispatch(self, context: ToolContext, call: ToolUseBlock) -> ToolResultBlock
```

**Purpose**: Runs one tool call and turns its stored, replay-safe result back into the format the model expects. It rehydrates any images that were kept out of the DBOS step log.

**Data flow**: It receives the tool context and a tool call, calls _dispatch_step, and if no images are referenced returns a text ToolResultBlock. If images are referenced, it fetches their base64 data from blob storage and returns a result containing text and image blocks.

**Call relations**: TurnEngine._model_round calls this for each tool call segment. It wraps _dispatch_step so the memoized step output stays small while the model still receives images.

*Call graph*: calls 1 internal fn (_dispatch_step); called by 1 (_model_round); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._offload`  (lines 1348–1358)

```
async def _offload(self, name: str, content: str) -> str
```

**Purpose**: Writes large text content to the sandbox's private tool-output directory and returns the path. This keeps huge tool output or salvaged model output out of the prompt.

**Data flow**: It receives a file name and content, ensures the tool-output directory is usable, writes the content bytes into the sandbox, and returns the sandbox path. It logs if it had to reclaim the directory.

**Call relations**: TurnEngine._dispatch_step uses this for large tool outputs. TurnEngine._model_round uses it to save partial output after a truncated model response.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._dispatch_step`  (lines 1361–1480)

```
async def _dispatch_step(self, context: ToolContext, call: ToolUseBlock) -> DispatchResult
```

**Purpose**: Executes one tool call end to end as a memoized DBOS step. It validates inputs, runs hooks, calls the tool, bounds or offloads large output, protects untrusted content, and records image references.

**Data flow**: It receives tool context and a ToolUseBlock. It publishes tool activity, validates the tool name and arguments, lets pre-tool hooks deny or modify input, runs the handler, collects text and images, applies error handling, offloading, untrusted-content wrapping, post-tool hooks, image resizing, and blob storage, then returns a DispatchResult.

**Call relations**: TurnEngine._dispatch calls this and then reassembles the model-facing ToolResultBlock. Because this step is recorded, replay avoids re-running side-effecting tools.

*Call graph*: calls 4 internal fn (_bounded_image, _offload, _publish_activity, _bounded); called by 1 (_dispatch); 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, replace).


##### `TurnEngine._bounded_image`  (lines 1482–1510)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized tool-result images before they are sent back to the model. This avoids provider limits and wasted image detail.

**Data flow**: It receives an ImageBlock with base64 data, decodes it, opens it with Pillow, and if its largest edge is over the limit, resizes and re-encodes it. If image processing fails, it logs and returns the original image.

**Call relations**: TurnEngine._dispatch_step calls this before storing image data in the blob store.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._publish_activity`  (lines 1512–1531)

```
async def _publish_activity(self, call: ToolUseBlock) -> None
```

**Purpose**: Publishes a live notice that a tool call is starting. This helps users see what the agent is doing during long tool-heavy turns.

**Data flow**: It receives a tool call, builds either a SkillLoad frame for the load_skill tool or a ToolCall frame with a short argument preview and optional user description, then sends it through _publish.

**Call relations**: TurnEngine._dispatch_step calls this before validating and running the tool. Publication failures are swallowed by _publish so they do not break the turn.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_dispatch_step); 3 external calls (__init__, __init__, dumps).


##### `TurnEngine._commit`  (lines 1533–1578)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request: CredentialRequest |
```

**Purpose**: Retries until the turn's terminal result is durably written, then publishes it live. This makes the database state the source of truth even during outages.

**Data flow**: It receives status, usage, answer or error details, optional pending requests, and arrival-safety options. It repeatedly calls _commit_once with backoff until it gets a result, then publishes a Terminal frame, emits metrics, logs, and returns the frame or None.

**Call relations**: TurnEngine.run calls this for done and failed outcomes. If _commit_once returns None because new arrivals must be absorbed first, run continues the model loop.

*Call graph*: calls 2 internal fn (_commit_once, _publish); called by 1 (run); 4 external calls (__init__, sleep, emit_metric, log).


##### `TurnEngine._commit_once`  (lines 1580–1678)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database attempt to record the final terminal frame and billing. It also protects against closing a foreground turn while unread arrivals exist.

**Data flow**: It totals usage, optionally locks the conversation and checks for pending or unabsorbed inbound messages, records turn usage, reads final cost, builds a TerminalFrame, and updates the turn row. If another execution already committed, it reads and returns the existing terminal frame.

**Call relations**: TurnEngine._commit wraps this with retry and live publication. It is the durable commit point for successful and failed turns.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 9 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx).


##### `TurnEngine._park`  (lines 1680–1717)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Records a turn as parked when it hits a cap or loses seat permission. Parking saves spent usage and leaves the turn resumable later.

**Data flow**: It receives the parked message and usage events, updates the turn to parked if still non-terminal, records usage for this attempt, releases all inbound rows consumed by this turn, then publishes a Parked frame and emits metrics.

**Call relations**: TurnEngine.run calls this after catching TurnParked from _enforce_spend.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 1719–1728)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Sends a live frame to the hub without letting publish failures break the turn. Durable database state remains authoritative.

**Data flow**: It receives a live frame, tries to publish it for this turn id, and logs any exception. It returns nothing and does not re-raise publish errors.

**Call relations**: Cost, activity, parked, and terminal paths all use this helper so live updates are best-effort everywhere.

*Call graph*: called by 4 (_commit, _park, _publish_activity, _publish_cost); 1 external calls (log).


##### `TurnEngine._bill_cancelled`  (lines 1730–1749)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort billing for tokens consumed before a cancellation. It avoids losing cost records when a workflow is stopped.

**Data flow**: It totals usage events and tries to record them in the database with this attempt id. If billing fails, it logs the failure and does not block cancellation.

**Call relations**: TurnEngine.run calls this when DBOS or asyncio cancellation interrupts the turn.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 1751–1756)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this execution cannot claim the turn. It repairs missed terminal publication if the turn is already finished, or does nothing if another execution is still running it.

**Data flow**: It creates a TranscriptRepair helper and asks it to resolve the situation. The result is a terminal frame when one was already committed, or None when there is nothing for this duplicate execution to do.

**Call relations**: TurnEngine.run calls this immediately after _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._persist_transcript`  (lines 1758–1761)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Persists the full successful transcript through the repair helper. It is a small wrapper that keeps run's finalization code simple.

**Data flow**: It receives final messages, answer, system prompt, and injected prompt text, creates a TranscriptRepair helper, and delegates transcript writing. It returns nothing.

**Call relations**: TurnEngine.run calls this after a done terminal is committed.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._persist_inbound`  (lines 1763–1764)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=()) -> None
```

**Purpose**: Persists only inbound user messages when the turn does not produce a normal answer. This preserves context for the next turn.

**Data flow**: It receives optional absorbed arrival messages, creates a TranscriptRepair helper, and delegates inbound-only transcript writing. It returns nothing.

**Call relations**: TurnEngine.run calls this on failed, cancelled, parked, or non-done terminal paths where assistant output should not be saved.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


### Subagent orchestration
Subagent support lets the main turn delegate work to child agents and manage their lifecycle, messages, waiting, and cancellation.

### `core/src/ufo/loop/subagents.py`

`orchestration` · `during turn execution when a parent agent spawns, waits for, messages, or cancels subagents`

A subagent is like sending a specialist to work on a side task while the main agent keeps control of the overall job. This file defines the list of allowed specialist profiles, builds the instructions each specialist receives, and creates the database and queue records needed to run the specialist as its own turn.

The main problem it solves is safe delegation. A parent turn can spawn a child turn with a named profile, a checked input shape, its own conversation, and its own queue partition. That separation matters: it lets the parent wait for the child without blocking the queue in a way that would deadlock both of them.

The file also supports reliable retries. If the caller gives a deduplication key, the child turn ID is derived from the parent and that key, so a repeated attempt reconnects to the same already-created child instead of creating and charging for a duplicate.

When a child finishes, its final message is read from the durable terminal record, validated against the profile’s output schema, and returned to the parent. Background children can be started now and checked later. The parent can also cancel them or send a follow-up message, but only if the turn really belongs to that parent.

#### Function details

##### `SubagentRegistry.__post_init__`  (lines 77–81)

```
def __post_init__(self) -> None
```

**Purpose**: This checks the registry as soon as it is created to make sure no two subagent profiles use the same name. Without this, asking for a profile by name could be ambiguous.

**Data flow**: It reads the names from the profile list stored in the registry → looks for repeated names → either leaves the registry unchanged or raises an error naming the duplicates.

**Call relations**: This runs automatically after a SubagentRegistry is built. It protects later lookups done by SubagentRegistry.get and, through that, every subagent spawn.


##### `SubagentRegistry.get`  (lines 83–90)

```
def get(self, name: str) -> SubagentProfile
```

**Purpose**: This finds the subagent profile with a requested name. It is the gatekeeper that turns a plain profile name into the full profile definition used to run a child agent.

**Data flow**: It takes a profile name → searches the registry’s stored profiles → returns the matching profile. If none exists, it raises an UnknownSubagentProfile error that includes the valid names.

**Call relations**: Subagents.spawn uses this before creating a child turn, and Subagents._untrusted_output uses it to decide whether a finished child’s output should be trusted. If the name is unknown, the flow stops loudly instead of running an undefined subagent.

*Call graph*: 1 external calls (__init__).


##### `subagent_system_prompt`  (lines 93–125)

```
def subagent_system_prompt(profile: SubagentProfile, *, skills: Sequence[tuple[str, str]]=CORE_SKILL_INDEX, preload: tuple[RuntimeSkill, ...]=()) -> str
```

**Purpose**: This builds the system prompt, meaning the core instruction text, for a subagent. It combines the profile’s instructions, optional skill information, shared citation and output rules, and the final requirement that the subagent must call the finish tool with its final answer.

**Data flow**: It takes a subagent profile, a list of available skills, and optional preloaded skill bodies → fills the skill-index placeholder, checks that no template placeholders were left unresolved, optionally appends preloaded skill instructions within a size limit → returns the complete prompt string sent to the child agent.

**Call relations**: This is used wherever the engine prepares a subagent’s model instructions. It calls the prompt-rendering helpers to build the skill list and detect unresolved prompt variables, so bad prompts fail before reaching the model.

*Call graph*: 2 external calls (findall, render_skill_index).


##### `Subagents.spawn`  (lines 137–181)

```
async def spawn(self, profile: str, payload: dict[str, Any], background: bool=False, dedup_key: str | None=None) -> SpawnResult
```

**Purpose**: This starts a child subagent turn. It can either return immediately with the child turn ID for background work, or wait until the child finishes and return its validated output.

**Data flow**: It takes a profile name, an input payload, a background flag, and an optional deduplication key → finds the profile, validates the input, chooses or derives a child conversation ID and turn ID, admits the child turn into the database, enqueues it for execution, and then either returns the ID immediately or waits for the terminal result → returns a SpawnResult containing the child ID and, for foreground work, the parsed output.

**Call relations**: This is the main public path for delegation. It relies on SubagentRegistry.get for the profile, _admit to create durable records, _enqueue to place the child on the work queue, and _await_terminal when the parent must wait. It creates SpawnResult objects for successful starts or completions, and raises an untrusted-content error when a profile marked as untrusted returns output that does not match its contract.

*Call graph*: calls 3 internal fn (_admit, _await_terminal, _enqueue); 5 external calls (__init__, __init__, turn_id_for, uuid4, uuid5).


##### `Subagents.wait`  (lines 183–202)

```
async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]
```

**Purpose**: This waits for one or more background subagents to finish and reports what happened to each. It is used when the parent previously started background work and later wants to collect the results.

**Data flow**: It takes a tuple of child turn IDs → first confirms each one was spawned by this parent and remembers its profile → waits for each child’s terminal record → returns a tuple of SubagentStatus objects with each child’s status, final text, and trust flag.

**Call relations**: This completes the background-spawn loop started by Subagents.spawn. It uses _require_child to prevent a parent from waiting on unrelated turns, _await_terminal to poll durable completion records, and _untrusted_output to mark outputs from untrusted or missing profiles.

*Call graph*: calls 3 internal fn (_await_terminal, _require_child, _untrusted_output); 1 external calls (__init__).


##### `Subagents.cancel`  (lines 204–221)

```
async def cancel(self, turn_id: UUID) -> SubagentStatus
```

**Purpose**: This cancels a child subagent turn that belongs to the current parent. It gives the parent a controlled way to stop work it no longer needs.

**Data flow**: It takes a child turn ID → verifies the child belongs to this parent → calls the shared cancellation routine → reads the turn’s current status and terminal text from the database → returns a SubagentStatus summarizing the cancelled or already-finished child.

**Call relations**: This is called when parent-side logic wants to stop a background child. It uses _require_child as a safety check, hands the actual cancellation to cancel_one_turn, then reads the resulting terminal frame inside a workspace database transaction.

*Call graph*: calls 1 internal fn (_require_child); 5 external calls (__init__, model_validate, select, cancel_one_turn, workspace_tx).


##### `Subagents.message`  (lines 223–297)

```
async def message(self, turn_id: UUID, text: str) -> SubagentStatus
```

**Purpose**: This sends a follow-up message to a background subagent by creating the next turn in that child’s own conversation. It lets the parent continue an existing subagent thread instead of starting a new one.

**Data flow**: It takes an existing child turn ID and message text → verifies the child belongs to this parent → reads the child conversation and profile from the database → locks the conversation, computes the next sequence number, inserts a new queued turn with the follow-up text, and marks it ready for dispatch if no earlier queued turn is ahead of it → enqueues it when appropriate and returns a queued SubagentStatus for the new follow-up turn.

**Call relations**: This is the parent’s follow-up path after a background spawn. It uses _require_child to enforce ownership and _enqueue to start the new turn when the child conversation is ready. The database locking keeps messages in order, like adding tickets to a single numbered line.

*Call graph*: calls 2 internal fn (_enqueue, _require_child); 8 external calls (__init__, exists, insert, select, update, workspace_tx, current_traceparent, turn_id_for).


##### `Subagents._untrusted_output`  (lines 299–305)

```
def _untrusted_output(self, profile: str) -> bool
```

**Purpose**: This decides whether a child’s output should be treated as untrusted. It errs on the safe side: if the profile cannot be found anymore, the output is treated as untrusted.

**Data flow**: It takes a profile name → tries to look it up in the registry → returns the profile’s untrusted-output setting, or returns true if the profile is unknown.

**Call relations**: Subagents.wait calls this when building each SubagentStatus. It uses SubagentRegistry.get indirectly as a trust check, so missing profile definitions do not accidentally become trusted output.

*Call graph*: called by 1 (wait).


##### `Subagents._require_child`  (lines 307–318)

```
async def _require_child(self, turn_id: UUID) -> str
```

**Purpose**: This verifies that a turn ID really belongs to a subagent spawned by the current parent turn. It prevents one parent from interfering with or reading another turn’s children.

**Data flow**: It takes a turn ID → reads that turn’s parent ID and subagent profile from the database → returns the profile name if the parent matches, or raises an error if the turn is missing or belongs elsewhere.

**Call relations**: Subagents.wait, Subagents.cancel, and Subagents.message all call this before acting on a child turn. It is the ownership check that makes those public operations safe.

*Call graph*: called by 3 (cancel, message, wait); 2 external calls (select, workspace_tx).


##### `Subagents._admit`  (lines 320–388)

```
async def _admit(self, conversation_id: UUID, turn_id: UUID, profile: str, inbound: str) -> bool
```

**Purpose**: This creates the database records for a new child conversation and its first turn. It is careful to be repeatable, so retrying the same deterministic spawn does not create duplicate work.

**Data flow**: It takes a child conversation ID, turn ID, profile name, and serialized input text → inserts the conversation if it does not already exist, inserts the first child turn if it does not already exist, reads the turn status, and marks queued turns as dispatch-ready → returns true if this turn should be enqueued now, or false if it is already past the queued state.

**Call relations**: Subagents.spawn calls this before trying to enqueue the child. It writes the durable records that the rest of the system will run, wait for, or reconnect to after a retry. It also stores the current tracing context so observability can connect the child’s work back to the parent.

*Call graph*: called by 1 (spawn); 4 external calls (select, update, workspace_tx, current_traceparent).


##### `Subagents._enqueue`  (lines 390–419)

```
async def _enqueue(self, turn_id: UUID, conversation_id: UUID) -> None
```

**Purpose**: This asks the DBOS queue system to run a child or follow-up turn. DBOS is the durable workflow runner used here to execute queued turn workflows reliably.

**Data flow**: It takes a turn ID and conversation ID → builds queue options including the queue name, workflow name, workflow ID, queue partition, and app version → calls the DBOS client to enqueue the work. If enqueueing is cancelled or fails, it clears the dispatch marker in the database so the turn can be picked up later; ordinary failures are logged instead of crashing this path.

**Call relations**: Subagents.spawn calls this for a newly admitted child, and Subagents.message calls it for a follow-up turn when no earlier queued message blocks it. It is the bridge between durable database admission and actual asynchronous execution.

*Call graph*: called by 2 (message, spawn); 3 external calls (update, workspace_tx, log).


##### `Subagents._await_terminal`  (lines 421–431)

```
async def _await_terminal(self, turn_id: UUID) -> TerminalFrame
```

**Purpose**: This waits until a turn has a terminal record, meaning the durable final result of that turn. It is a simple polling loop used by foreground spawns and background waits.

**Data flow**: It takes a turn ID → repeatedly reads the turn’s terminal field from the database → if no terminal exists yet, sleeps briefly and checks again → once present, validates it as a TerminalFrame and returns it.

**Call relations**: Subagents.spawn uses this when the parent wants a foreground answer, and Subagents.wait uses it to collect background child results. It is the common waiting point for all child completion reads.

*Call graph*: called by 2 (spawn, wait); 4 external calls (sleep, model_validate, select, workspace_tx).


### Delegated specialist tools
Extension tools package specialized browser, research, and website-building tasks for execution by dedicated subagents.

### `extensions/browser/ufo_ext_browser/delegation.py`

`orchestration` · `request handling`

This file is a bridge between a parent agent and a separate browser agent. Instead of giving the parent direct control of a browser, it asks a child agent with the browser profile to do the web work and return a summary. That keeps browser automation isolated, easier to time-limit, and scoped to the right tools.

There are two main tools here. `browser_task` is for one complete browser session, such as opening a site, clicking through pages, filling a form, or extracting information. Each call starts a fresh browser session, so the task description must include all needed context. The file also protects the parent agent from getting stuck forever: if the browser task runs past its time budget, the child task is cancelled and an error result is returned.

`wide_browse` is for batch work. It reads a workspace file containing one URL or site name per line, removes duplicates, and sends each item to a browser subagent. It limits how many browser jobs run at once, like a checkout line with only a fixed number of open registers. Results are gathered into `wide_browse.json` in the workspace and also returned to the caller. It uses stable deduplication keys so that, after recovery from a crash, already-started child tasks can be reconnected to instead of launched again.

#### Function details

##### `_browser_task`  (lines 86–111)

```
async def _browser_task(ctx: ToolContext, args: BrowserTaskInput) -> ToolResult
```

**Purpose**: Runs one full browser automation job by spawning a browser subagent. It waits for that child job to finish, cancels it if it takes too long, and returns the browser agent's final result.

**Data flow**: It receives the tool context and a `BrowserTaskInput` containing the starting URL, task instructions, task name, timeout, and user-facing description. It checks that subagent control is available, starts a fresh browser child turn with the task details, and waits within the requested time limit. If the timeout expires, it cancels the child and returns an error message. If the child finishes successfully, it reads the child's text as a `BrowserResult`, converts it back to JSON, and returns that JSON as tool output.

**Call relations**: This is the handler behind the `browser_task` tool definition. When the tool is invoked, it uses `ToolContext.spawn` to create the browser child session, waits through the subagent control interface, and packages the result with `TextContent` and `ToolResult` so the parent agent receives a normal tool response.

*Call graph*: 5 external calls (__init__, __init__, timeout, spawn, model_validate_json).


##### `_read_lines`  (lines 114–125)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a workspace text file and turns it into a clean list of unique, non-empty lines. In this file, those lines are the URLs or site names that `wide_browse` should visit.

**Data flow**: It receives the tool context and a file path. It asks the sandbox to run `cat` on that path, checks whether reading succeeded, then walks through the file line by line. Blank lines are ignored, surrounding spaces are removed, and repeated entries are skipped. The output is an ordered list of unique entities.

**Call relations**: `_wide_browse` calls this first to learn what browser jobs need to be launched. It does the file-reading cleanup before the wider fan-out logic begins, so `_wide_browse` can work with a simple list instead of raw file text.

*Call graph*: called by 1 (_wide_browse); 1 external calls (dumps).


##### `_wide_browse`  (lines 128–155)

```
async def _wide_browse(ctx: ToolContext, args: WideBrowseInput) -> ToolResult
```

**Purpose**: Runs the same kind of browser task across many URLs or site names in parallel, then saves all results to a JSON file. It is meant for broad data collection where each item can be visited independently.

**Data flow**: It receives the tool context and a `WideBrowseInput` containing the entities file, a prompt template, an output schema file, and a user-facing description. It reads and deduplicates the entities, rejects the request if there are too many, optionally reads a JSON schema to tell each browser job what shape to return, and creates a semaphore, which is a limit on how many jobs may run at once. It then launches a visit task for each entity, waits for all of them with `asyncio.gather`, writes the collected rows to `wide_browse.json`, and returns a JSON message containing both the rows and the output file name.

**Call relations**: This is the handler behind the `wide_browse` tool definition. It first relies on `_read_lines` to prepare the batch input. Then it uses its nested `_wide_browse.visit` helper for each entity, gathers all visit results, writes the combined output through the sandbox, and returns the final summary as a normal tool result.

*Call graph*: calls 1 internal fn (_read_lines); 5 external calls (__init__, __init__, Semaphore, gather, dumps).


##### `_wide_browse.visit`  (lines 136–149)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Runs the browser subtask for one entity inside a wider batch browse. It builds the specific prompt for that entity and returns one result row.

**Data flow**: It receives one entity, such as a URL or site name. It waits for permission from the semaphore so the batch does not start too many browser jobs at once, replaces `{entity}` in the prompt template, appends the output schema text if one was available, and spawns a browser subagent with a deterministic deduplication key. It returns a small dictionary containing the original entity and the browser job's JSON output, or an empty string if there was no output.

**Call relations**: This helper lives inside `_wide_browse` and is used once per entity in the batch. `_wide_browse` schedules all these visits together with `asyncio.gather`, while the semaphore inside `visit` keeps the parallel browser fan-out bounded.


### `extensions/research/ufo_ext_research/delegation.py`

`orchestration` · `request handling during wide_research tool execution`

This file exists for cases where a user needs the same kind of research done for many entities, such as a list of companies, people, or topics. Instead of asking one agent to work through the whole list one by one, `wide_research` spreads the work across several research subagents at the same time. An everyday analogy is handing a stack of index cards to a small team: each person researches one card, then everyone’s notes are collected into one folder.

The tool starts by reading an entities file from the sandbox, where each non-empty line is one research target. It removes duplicates while keeping the original order, so the same entity is not researched twice. It also enforces a maximum list size to avoid launching too much work at once.

For each entity, it fills `{entity}` into a prompt template. If an output schema file can be read, it appends that schema to the research instruction so each subagent knows what shape of answer is expected. The file uses a semaphore, which is a limit that prevents too many tasks from running at once, to keep parallel research bounded.

Each child research task is spawned with a deterministic deduplication key based on the parent call and the entity name. That matters for crash recovery: if the same batch is resumed, already-started or completed child jobs can be reconnected to instead of duplicated. Finally, all results are written to `wide_research.json` and also returned in the tool response.

#### Function details

##### `_read_lines`  (lines 41–52)

```
async def _read_lines(ctx: ToolContext, path: str) -> list[str]
```

**Purpose**: Reads a text file from the sandbox and turns it into a clean list of unique entities. It is used so the rest of the tool can work with a simple list instead of raw file text.

**Data flow**: It receives a tool context and a file path. It asks the sandbox to run `cat` on that path, checks whether the read succeeded, then splits the file into lines. Blank lines are ignored, surrounding spaces are removed, and repeated entries are skipped. It returns the cleaned list of entity names, or raises an error if the file cannot be read.

**Call relations**: The main `_wide_research` function calls `_read_lines` at the start of the batch. `_read_lines` prepares the list that drives the rest of the fan-out, so no research subagents are started until this file-reading step succeeds.

*Call graph*: called by 1 (_wide_research); 1 external calls (dumps).


##### `_wide_research`  (lines 55–82)

```
async def _wide_research(ctx: ToolContext, args: WideResearchInput) -> ToolResult
```

**Purpose**: Runs the full wide research workflow: read the entity list, start several research subagents in parallel, collect their answers, write a JSON output file, and return a summary to the caller.

**Data flow**: It receives the tool context and validated input fields: the entities file, prompt template, optional schema file, and user description. It reads and checks the entity list, tries to read the schema file, creates a concurrency limit, then launches one `visit` task per entity. When all visits finish, it writes the combined rows to `wide_research.json` in the sandbox and returns a tool result containing the rows and output filename.

**Call relations**: This is the handler connected to the `WIDE_RESEARCH_TOOL` definition, so it is called when the user invokes `wide_research`. It calls `_read_lines` to prepare the targets, uses `asyncio.gather` to wait for all child visits, and wraps the final JSON text in `TextContent` and `ToolResult` so the tool system can send it back to the user.

*Call graph*: calls 1 internal fn (_read_lines); 5 external calls (__init__, __init__, Semaphore, gather, dumps).


##### `_wide_research.visit`  (lines 63–76)

```
async def visit(entity: str) -> dict[str, object]
```

**Purpose**: Researches one entity from the batch by building that entity’s prompt and spawning a research subagent. It is the per-item worker used by the wider batch process.

**Data flow**: It receives a single entity name from the outer `_wide_research` loop and uses the shared input arguments and schema text from that outer scope. It waits for permission from the semaphore so only a limited number of visits run at once, fills the entity into the prompt template, optionally appends the expected output schema, then calls `ctx.spawn` to run the research profile. It returns a dictionary containing the entity name and the child agent’s serialized result text, or an empty string if there was no output.

**Call relations**: `_wide_research` creates one `visit` task for each entity and waits for all of them together. Each `visit` hands the actual research work off to the research subagent profile, using a deduplication key based on the parent idempotency key and the entity so repeated or recovered runs do not duplicate completed work.


### `extensions/sites/ufo_ext_sites/delegation.py`

`orchestration` · `request handling`

This file exists so the main agent does not have to build an entire website by itself. Instead, it can delegate that work to a focused helper agent that knows how to build, serve, and check websites inside the sandbox. Think of it like a project manager handing a full brief to a specialist contractor, then waiting for the contractor's report.

The file defines a clear input shape called `BuildWebsiteInput`. It requires an `objective`, which must describe the whole site because the child agent does not inherit the parent conversation. It can also include a friendly `task_name`, a list of `preload_skills` so the child starts with useful instructions already loaded, and an `extended_context` flag for larger jobs that may need more working time.

The actual tool function, `_build_website`, calls `ctx.spawn`, which starts the `website_building` subagent using the current tool context. This matters because the child runs within the allowed tools and limits of the current profile, rather than getting unrestricted access. The result from the child is turned into plain text and wrapped as a standard tool result. Finally, `DELEGATION_TOOLS` registers this as the public `build_website` tool that other parts of the system can expose to the agent.

#### Function details

##### `_build_website`  (lines 47–50)

```
async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult
```

**Purpose**: This is the tool's action function. When the agent asks to build a website, this function starts a fresh `website_building` child agent session with the requested objective and options, then returns the child agent's summary as the tool output.

**Data flow**: It receives a tool context, which is the safe doorway for starting child agents, and a `BuildWebsiteInput` object containing the website brief and optional settings. It converts that input into a plain data dictionary, leaving out options that were not provided, then passes it to `ctx.spawn` to run the website-building subagent. When the child finishes, it takes the child's output, converts it to JSON text if there is any output, and wraps that text in a `ToolResult` containing `TextContent`.

**Call relations**: This function is registered as the handler for the `build_website` tool in `DELEGATION_TOOLS`. When that tool is invoked, `_build_website` hands the work to `ToolContext.spawn`, using the shared `WEBSITE_BUILDING_NAME` so the correct subagent is started. After the subagent returns, it hands the final summary back through the normal tool-result format.

*Call graph*: 4 external calls (__init__, __init__, spawn, model_dump).

## 📊 State Registers Touched

- `reg-workspace-context` — The current workspace and member context that keeps every request acting inside the right tenant boundary.
- `reg-workspaces-members-agents` — The durable records for workspaces, their members, and the agents that can act for them.
- `reg-connected-credentials` — The encrypted store of outside account connections and secrets that tools and sync jobs may use safely.
- `reg-permission-grants` — The permission ledger saying which agent may use which connected account or provider access.
- `reg-extension-state-store` — The per-workspace key-value storage area where extensions keep their own persistent settings and data.
- `reg-capability-registry` — The loaded menu of extension-provided routes, tools, skills, hooks, jobs, credentials, models, and search backends.
- `reg-tool-catalog` — The shared list of tools the agent may call, including their names, inputs, safety labels, and handlers.
- `reg-model-catalog` — The model switchboard that maps model names to providers, credentials, request formats, and prices.
- `reg-connector-catalog` — The shared directory of external service connectors and broker-backed provider access.
- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-sandbox-session-policy` — The safe execution room state: sandbox handles, mounted workspace files, storage access, and restrictions.
- `reg-egress-proxy-state` — The controlled network gateway state that decides which outside sites sandboxed work may contact and how usage is counted.
- `reg-browser-session` — The browser connection state used when a turn needs a Chrome endpoint or computer-use actions.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-turn-queue-run-state` — The durable state of each agent turn, including whether it is queued, claimed, running, parked, finished, failed, or cancelled.
- `reg-live-event-stream` — The live progress channel that lets clients attach, resume, and receive streamed turn updates.
- `reg-artifact-blob-store` — The shared file and blob storage used for uploaded content, generated artifacts, and token-protected downloads.
- `reg-source-sync-state` — The remembered external sources, imported pages, raw bodies, change records, cursors, errors, and deletion markers.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-scheduled-task-calendar` — The durable calendar of one-time and repeating tasks that workers can safely claim and run later.
- `reg-accounting-ledger-spend` — The usage ledger, spend caps, exports, and cost totals for models, egress, and other billable work.
- `reg-observability-trace-state` — The logs, metrics, traces, traceparent links, and redaction rules used to monitor work safely.
- `reg-workspace-object-catalog` — The named workspace objects and object-type registry used to list, inspect, validate, change, or delete stored things.
- `reg-prompt-governance` — The saved prompt digests, prompt-change proposals, approvals, and experiment evidence that control instruction changes.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-action-proposal-state` — Durable non-governance proposals created by agents or tools for later user/operator review, approval, rejection, or commit.
- `reg-turn-cleanup-finalizers` — Per-turn cleanup/finalizer stack for resources acquired during runtime assembly and tool execution so result commit or shutdown can release them safely.
- `reg-agent-todo-goal-state` — The agent-maintained goal/todo checklist state that tools update and later prompt/runtime assembly can reload as working context.
