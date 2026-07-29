# Model interaction and streaming tool-call loop  `stage-9`

This stage is the main work loop for one agent turn. A “turn” means one cycle of responding to a user request. After earlier startup and preparation steps have chosen the model and built the message history, this stage sends that material to the model provider and watches the answer arrive piece by piece as a stream.

The central file, `engine.py`, acts like the conductor. It first claims the turn so two workers do not answer the same request. It builds the final prompt, including the available tool descriptions, then calls the model. As the model streams back text, the engine collects it. If the model asks to use a tool, the engine runs that tool, adds the tool’s result to the conversation, and sends the updated messages back to the model. This loop continues until there is a final assistant answer, a safe parked state for later continuation, or a recorded failure. Along the way it tracks cost and commits the outcome so the system has a reliable record.

## Files in this stage

### Model interaction and streaming tool-call loop
### `core/src/ufo/loop/engine.py`

`orchestration` · `request handling`

Think of this file as the conductor for one conversation step. A turn starts as a queued database row. The engine claims it so only one worker owns it, loads the prior transcript, adds context such as time and sender, and then repeatedly asks the language model what to do next. If the model calls tools, the engine runs those tools, sends their results back to the model, and loops until the model gives a final answer. While this is happening, new incoming messages can arrive; the engine drains them between model rounds so the answer does not ignore someone who spoke mid-turn.

The file is careful about crashes and retries. Some operations are DBOS steps, meaning their outputs are recorded so a restarted workflow can replay them without re-calling the model, re-running a tool, or consuming the same queued message twice. It also keeps live clients updated with streamed text, tool activity, cost ticks, parked notices, and terminal frames.

It protects the system in several ways: it checks spending and seat limits before more model calls, parks resumable work when limits are hit, trims or offloads huge tool results, stores large images outside the replay log, retries terminal commits until durable, and preserves transcripts so future turns see the right history.

#### Function details

##### `_claim_turn`  (lines 169–211)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> bool
```

**Purpose**: Claims a queued or parked turn for one workflow attempt and marks it as running. This prevents two workers from doing the same user request at the same time.

**Data flow**: It receives a turn id and an attempt id, reads the turn’s conversation, locks that conversation row, and updates the turn if it is claimable. It also clears a one-shot scheduled resume task for that turn. It returns true when this attempt owns the turn, otherwise false.

**Call relations**: TurnEngine._mark_running calls this at the start of a run. _claim_turn_with_handoff also uses it before looking for a following queued turn to hand off.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 6 external calls (and_, delete, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 222–279)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[bool, _TurnHandoff | None]
```

**Purpose**: Claims one turn and, if possible, reserves the next queued turn in the same conversation for dispatch. It is a helper for handing work forward without racing another worker.

**Data flow**: It takes a turn id and attempt id, first tries to claim that turn, then locks the conversation and looks for the next queued turn. If that next turn has not already been queued for dispatch, it stamps it and returns a small handoff record with ids needed to run it.

**Call relations**: It builds on _claim_turn. Nothing in this file calls it directly, but it is part of the surrounding turn-dispatch flow.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `ModelStreamError.__init__`  (lines 376–377)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Creates an error object for a model stream that failed after producing some output or usage. It keeps the model’s real error class, message, and partial output together.

**Data flow**: It receives the model error class, readable message, and any partial text/tool-call data. It stores them as the exception’s arguments so retry and persistence systems can reconstruct them.

**Call relations**: TurnEngine._stream_recovering_overflow creates this after a recorded model stream reports an error instead of throwing one directly.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 379–381)

```
def __str__(self) -> str
```

**Purpose**: Formats the model stream failure as a readable string. It includes the original model-side error class so overflow detection and logs still have useful context.

**Data flow**: It reads the stored error class and message from the exception and combines them into one string. It deliberately leaves out partial output because that is for recovery, not user-facing error text.

**Call relations**: It is used whenever the exception is converted to text, such as during logging or terminal error recording.


##### `ModelStreamError.model_error_class`  (lines 384–386)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original class name of the model-provider error. Callers use this to tell truncation or context overflow apart from other failures.

**Data flow**: It reads the first stored exception argument and returns it unchanged.

**Call relations**: TurnEngine._model_round and TurnEngine._commit_once rely on this property to decide recovery behavior and to record the correct terminal error class.


##### `ModelStreamError.partial_output`  (lines 389–391)

```
def partial_output(self) -> str
```

**Purpose**: Returns any text and partial tool-call arguments produced before the model stream failed. This lets the engine save useful paid-for output instead of throwing it away.

**Data flow**: It reads the stored partial output from the exception arguments and returns it as text.

**Call relations**: TurnEngine._model_round uses this after a model truncation to write the partial output to a workspace file and tell the model where to salvage it.


##### `ModelStreamError.model_error_message`  (lines 394–396)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original message from the model-provider error. This preserves the provider’s explanation for terminal records.

**Data flow**: It reads the stored message from the exception arguments and returns it unchanged.

**Call relations**: TurnEngine._commit_once uses this when building the terminal frame for a failed turn.


##### `TurnParked.__init__`  (lines 403–405)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates an exception meaning the turn should pause, not fail. This happens when spending or seat rules say the turn cannot continue right now.

**Data flow**: It receives a message suitable for the live surface, stores it in the exception, and also exposes it as a named field.

**Call relations**: TurnEngine._enforce_spend raises it. TurnEngine.run catches it, parks the turn durably, publishes the parked notice, and then stops.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 408–432)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits a model’s requested tool calls into safe execution groups. Tools marked safe for parallel use can run together; others run alone in order.

**Data flow**: It receives the tool registry and the ordered tool calls. It looks up whether each tool is parallel-safe, batches consecutive safe calls up to a limit, and yields ordered groups.

**Call relations**: TurnEngine._model_round uses these groups before dispatching tools, so reads can be concurrent but risky writes do not race each other.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 435–437)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed JSON fragments from a tool call into the final argument dictionary. Empty arguments become an empty dictionary.

**Data flow**: It receives a list of partial JSON strings, joins them, and parses the result with JSON. It outputs the dictionary that becomes the tool input.

**Call relations**: TurnEngine._stream_once uses it after the model stream finishes and all tool-call argument fragments have arrived.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 440–453)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the small context block placed before a member’s message. It tells the model which message is being referenced, when it was admitted, and who the sender was.

**Data flow**: It receives a message id, optional turn context, and admission time. It formats the time in the sender’s timezone when available, otherwise UTC, and returns a text tag.

**Call relations**: TranscriptRepair.load_messages uses it for the founding inbound message. TurnEngine._render_arrival uses it for later messages absorbed during a running turn.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 456–461)

```
def _bounded(content: str) -> str
```

**Purpose**: Keeps tool-result text under a maximum size. This prevents one huge result from flooding every later model prompt.

**Data flow**: It receives text. If the text is short enough, it returns it unchanged; otherwise it returns the first allowed portion plus a notice saying how much was dropped.

**Call relations**: TurnEngine._dispatch_step uses it for error results and fallback truncation. TurnEngine._model_round uses it when feeding finish-tool validation errors back to a subagent.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_loaded_skill_closures`  (lines 464–506)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedSkill, ...]]
```

**Purpose**: Finds which skill instructions are already present in the current message window. This stops the engine from re-injecting the same skill instructions unnecessarily.

**Data flow**: It scans messages for completed load_skill tool calls and their successful results. For each valid skill name, it asks the skill registry for the full closure of related skills and yields that closure.

**Call relations**: TurnEngine._reseed_loaded_skills calls this before and after compaction so the tool context knows which skills remain active.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 509–527)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts the structured payload from a tool that can end a turn, such as ask_user or request_credentials. It only accepts the payload if that tool was the last successful action.

**Data flow**: It receives the round’s tool calls, their results, a target tool name, and a Pydantic model used for validation. It parses the result text as JSON and returns the validated object, or none if it does not match.

**Call relations**: TurnEngine._model_round uses it after dispatching tools to remember whether the final answer should include a pending question, credential request, or account-connection request.

*Call graph*: called by 1 (_model_round); 1 external calls (loads).


##### `_total_usage`  (lines 530–536)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many token-usage events into one total. This gives billing, cost display, and cap checks a single number to work with.

**Data flow**: It receives a list of usage records and sums input, output, cache-read, and cache-write tokens separately. It returns one Usage record containing the totals.

**Call relations**: TurnEngine._publish_cost, _enforce_spend, _commit_once, _park, and _bill_cancelled all call it when they need the current accumulated usage.

*Call graph*: called by 5 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 551–575)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes an already-committed terminal result for a duplicate or redelivered run. This helps a waiting client finish even if the original worker crashed after committing but before publishing.

**Data flow**: It reads the turn’s stored terminal frame from the database. If none exists, it returns none; otherwise it validates the frame, ensures inbound transcript content is preserved, publishes the terminal live, and returns the frame.

**Call relations**: TurnEngine._resolve_unclaimed uses this when the current execution cannot claim the turn. It calls TranscriptRepair.persist_inbound before publishing.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 577–584)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the completed conversation transcript for a successful answer. It appends the assistant’s final answer to the messages that led to it.

**Data flow**: It receives the messages, final answer, system prompt, and injected context. It creates an assistant message for the answer and passes the full conversation to write_conversation.

**Call relations**: TurnEngine._persist_transcript delegates here after a done terminal is committed.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 586–605)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Preserves user messages when a turn ends without a normal done answer, such as failure or cancellation. This avoids losing what people said even when the assistant response is not saved.

**Data flow**: It either loads the normal founding inbound or builds a denied founding message, appends absorbed arrival messages, and writes that conversation state. It does not save partial assistant error text.

**Call relations**: TranscriptRepair.resolve may call it during repair. TurnEngine._persist_inbound delegates here on failed, parked, cancelled, or non-done exits.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 607–615)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the prior transcript and adds this turn’s founding user message. For normal member turns, it prefixes the message with a context tag so the model knows time and sender.

**Data flow**: It reads prior messages, prepares the inbound text, optionally adds the context tag, wraps it as a user message, and returns the combined message tuple.

**Call relations**: TurnEngine._load_messages delegates here at the start of a turn. TranscriptRepair.persist_inbound also uses it when preserving inbound-only transcript state.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 617–623)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the conversation history before this turn. It avoids accidentally reading this same turn’s own transcript during a replay.

**Data flow**: It asks the transcript store for the saved conversation. If there is no transcript or the saved sequence is at or beyond this turn, it returns no prior messages; otherwise it returns the stored messages.

**Call relations**: TranscriptRepair.load_messages and persist_inbound call it when building the message list to write or send to the model.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 625–645)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a conversation snapshot to durable transcript storage with a few retries. This makes transcript writes more resilient to brief storage failures.

**Data flow**: It receives messages and optional system/injected prompt text, creates a Conversation object, and attempts to write it. On failure it logs, waits briefly, and retries a fixed number of times.

**Call relations**: TranscriptRepair.persist_transcript and persist_inbound both hand their prepared conversation here.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `TurnEngine.__post_init__`  (lines 683–694)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the engine was wired together consistently after construction. It catches mismatched audiences and an invalid finish-tool conflict early.

**Data flow**: It inspects tool extension contexts, hook audience, output model, and tool registry. If something conflicts, it raises a ValueError; otherwise initialization continues unchanged.

**Call relations**: This runs automatically when a TurnEngine dataclass instance is created, before run starts.


##### `TurnEngine.run`  (lines 696–856)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs the entire turn lifecycle. It is the main body that claims the turn, calls the model, runs tools, commits the result, and cleans up.

**Data flow**: It starts with the turn, agent, prompt, model client, tools, transcript, sandbox, and services already attached to the engine. It builds a tool context, claims ownership, applies prompt hooks, loops through model rounds, records usage, commits done/failed/parked states, publishes live frames, and writes transcript state.

**Call relations**: This is the central orchestration method. It calls nearly every helper in this file: claiming, message loading, scheduled memory recall, model rounds, commits, parking, cancellation billing, transcript persistence, and cleanup.

*Call graph*: calls 12 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _release_unabsorbed, _repair (+2 more)); 9 external calls (__init__, __init__, __init__, __init__, __init__, escape, emit_metric, log, turn_span).


##### `TurnEngine.run.rank_find`  (lines 711–728)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Uses the model to rank browser elements for the browser find tool. It lets host-side browser tooling ask a smaller model question during a turn.

**Data flow**: It receives a system prompt and user text, builds a model request with reasoning off, streams text deltas into a string, and adds any usage events to the parent turn’s usage list. It returns the ranking text.

**Call relations**: TurnEngine.run places this function into ToolContext as find. Browser-related tools can call it while their usage is billed to the same turn.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine._scheduled_system`  (lines 858–888)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. Scheduled work has no live user sitting there, so remembered context can help the agent act appropriately.

**Data flow**: It receives the current system prompt, searches memory using audience subjects and the turn inbound text, and formats any matches as escaped recall lines. If memory search fails or finds nothing, it returns the original prompt.

**Call relations**: TurnEngine.run calls this only when the turn came from scheduled admission.

*Call graph*: called by 1 (run); 4 external calls (timeout, escape, audience_subjects, log).


##### `TurnEngine._mark_running`  (lines 890–898)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims the turn for this engine attempt. It is the engine’s wrapper around the lower-level database claim.

**Data flow**: It passes the current turn id and attempt id to _claim_turn and returns whether the claim succeeded.

**Call relations**: TurnEngine.run calls it near startup. If it returns false, run switches to the unclaimed repair path.

*Call graph*: calls 1 internal fn (_claim_turn); called by 1 (run).


##### `TurnEngine._repair`  (lines 900–901)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates a TranscriptRepair helper for this turn. This keeps transcript and terminal-republish work separate from the main engine flow.

**Data flow**: It packages the current turn, transcript store, and hub into a TranscriptRepair object and returns it.

**Call relations**: TurnEngine._load_messages, _persist_transcript, _persist_inbound, _resolve_unclaimed, and run use this helper instead of duplicating transcript repair logic.

*Call graph*: called by 5 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed, run); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 903–904)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the messages that should be sent to the model at the start of the turn. It delegates the transcript-specific details to TranscriptRepair.

**Data flow**: It creates a repair helper and asks it to load prior messages plus this turn’s inbound message. It returns that tuple of model messages.

**Call relations**: TurnEngine.run calls this during setup, unless the founding inbound was denied and needs a special denial transcript shape.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._model_round`  (lines 906–1061)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the repeated model-and-tools loop until the turn has an answer or must be forced to close. This is where the agent’s actual work happens.

**Data flow**: It receives the tool context, current messages, usage list, system prompt, arrival tracking, and active requesters. Each round absorbs new arrivals, checks spend, compacts context if needed, streams the model, dispatches tools when requested, feeds results back, and eventually returns final messages, answer text, and any pending user/account request.

**Call relations**: TurnEngine.run calls it after setup. It calls arrival draining, spend enforcement, compaction, streaming, dispatching, cost publishing, finish forcing, and final-act extraction.

*Call graph*: calls 12 internal fn (_absorb_arrivals, _dispatch, _enforce_spend, _force_final, _force_finish, _offload, _publish_cost, _reseed_loaded_skills, _stream_recovering_overflow, _bounded (+2 more)); called by 1 (run); 6 external calls (__init__, __init__, __init__, gather, emit_metric, log).


##### `TurnEngine._absorb_arrivals`  (lines 1063–1100)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Adds newly queued incoming messages to the current model window between rounds. This keeps the agent from answering while ignoring messages that arrived during its work.

**Data flow**: It receives current messages and tracking lists. For normal member turns, it claims arrivals, records their ids, turns admitted or denied arrivals into user messages, updates requester tracking, and returns the expanded message list.

**Call relations**: TurnEngine._model_round calls it at the start of every round. It relies on _claim_arrivals for durable queue draining.

*Call graph*: calls 1 internal fn (_claim_arrivals); called by 1 (_model_round); 3 external calls (__init__, __init__, escape).


##### `TurnEngine._render_arrival`  (lines 1102–1126)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Turns one queued inbound message into the exact text the model will see. It applies the same user-prompt hook used for the founding message.

**Data flow**: It receives message identity, body, context, speaker, and creation time. It fires the user_prompt_submit hook; if denied, it returns denial text, otherwise it prepends a context tag and appends any injected context in a separate block.

**Call relations**: TurnEngine._claim_arrivals calls it while converting claimed database rows into memoized Arrival objects.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1129–1179)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Durably drains pending inbound messages for this conversation. Because it is a DBOS step, crash replay sees the same claimed batch instead of consuming messages twice.

**Data flow**: It receives ids already absorbed by this run, stamps unconsumed or recoverable inbound rows as consumed by this turn, reads their content, renders each one, and returns Arrival records in sequence order.

**Call relations**: TurnEngine._absorb_arrivals calls it. It calls _render_arrival so hooks fire inside the recorded step and are not repeated on replay.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 6 external calls (__init__, model_validate, and_, or_, update, workspace_tx).


##### `TurnEngine._release_unabsorbed`  (lines 1181–1200)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns claimed-but-not-fully-absorbed inbound messages to the pending queue after failure or cancellation. This reduces the chance of losing messages during an interrupted run.

**Data flow**: It receives the ids known to have been absorbed. It clears consumed_turn_id for any other inbound rows stamped by this turn, and logs rather than raising if the cleanup fails.

**Call relations**: TurnEngine.run calls it on cancellation and unexpected failure paths.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1202–1238)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, requesters: dict[UUID, ActiveMessage]) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a closing answer when the normal tool-use round budget is exhausted. This avoids failing a turn simply because the model kept working too long.

**Data flow**: It receives messages, usage, system prompt, and requesters. It logs and emits a metric, checks spend, compacts context, then either forces a subagent finish tool or asks the model for a final no-tools answer.

**Call relations**: TurnEngine._model_round calls it after the loop reaches max_rounds. It may call _force_finish or _stream_recovering_overflow.

*Call graph*: calls 4 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_recovering_overflow); called by 1 (_model_round); 3 external calls (__init__, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 1240–1265)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to end through the special finish tool. This keeps subagent answers shaped like the schema their parent expects.

**Data flow**: It receives messages, usage, and system prompt. It streams one model round offering only finish, publishes cost, validates the finish arguments against the output model, and returns the messages plus JSON answer.

**Call relations**: TurnEngine._model_round calls it when a subagent gives prose instead of finish. TurnEngine._force_final calls it when a subagent exhausts its rounds.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 1267–1310)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False, active_requests: tuple[str, ...]=()
```

**Purpose**: Runs one model round and retries once if the provider says the context is too large. It gives compaction a last chance to shrink the prompt before failing.

**Data flow**: It receives messages, usage, system prompt, and tool-offer options. It calls _stream_once, adds usage, and returns text/tool calls; on context overflow, it forces compaction, reseeds loaded skills, tries once more, and returns the compacted window’s result.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish use it for model calls that should recover from overflow in a consistent way.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 3 external calls (is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 1312–1354)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks whether the turn is still allowed to spend more model tokens. If caps or seat rules are violated, it parks the turn instead of continuing.

**Data flow**: It receives accumulated usage and active requester messages. It gathers relevant members, checks seat admission, prices current unbilled usage, asks the spend evaluator for a decision, and raises TurnParked if not allowed.

**Call relations**: TurnEngine._model_round calls it before each model round. TurnEngine._force_final calls it before spending the forced closing round.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 6 external calls (__init__, __init__, applicable_caps_absent, audience_member, workspace_tx, seat_gate_absent).


##### `TurnEngine._stream_once`  (lines 1357–1464)

```
async def _stream_once(self, messages: tuple[Message, ...], system: str, offer_tools: bool=True, force_finish: bool=False) -> StreamResult
```

**Purpose**: Performs one actual model streaming call. It records text, tool calls, and usage while also publishing live text chunks to connected clients.

**Data flow**: It receives messages, system prompt, and options controlling tool availability or forced finish. It builds a ModelRequest, consumes streamed events, buffers text for live publishing, gathers tool-call JSON fragments, records usage, and returns a StreamResult; if the stream errors mid-way, the error is returned inside StreamResult with partial output.

**Call relations**: TurnEngine._stream_recovering_overflow is the only caller. Because this method is a DBOS step, successful step replay does not call the model again.

*Call graph*: calls 1 internal fn (_parse_args); called by 1 (_stream_recovering_overflow); 5 external calls (__init__, __init__, __init__, __init__, monotonic).


##### `TurnEngine._stream_once.flush`  (lines 1413–1419)

```
async def flush() -> None
```

**Purpose**: Publishes buffered model text to live listeners in chunks. This avoids sending every tiny token separately while keeping the interface responsive.

**Data flow**: It reads the local text buffer, joins and publishes it as a TextDelta if non-empty, clears the buffer, resets the pending byte count, and updates the last-flush time.

**Call relations**: It is an inner helper used only by TurnEngine._stream_once while consuming the model stream and once more at the end.

*Call graph*: 2 external calls (__init__, monotonic).


##### `TurnEngine._publish_cost`  (lines 1466–1479)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the turn’s current cost and token count to live clients. It lets the user interface show a running meter before final billing is committed.

**Data flow**: It totals usage events, computes token count and model cost, creates a CostTick frame, and sends it through the safe publish helper.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish call it after model rounds.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 1481–1492)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skill instructions currently visible to the model. This matters after compaction because some old messages may have been summarized or removed.

**Data flow**: It receives the current message window, scans it for completed skill loads through _loaded_skill_closures, combines those with preloaded skills, and updates the compaction skill tracker.

**Call relations**: TurnEngine._model_round calls it around compaction. TurnEngine._stream_recovering_overflow calls it after forced compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 2 (_model_round, _stream_recovering_overflow).


##### `TurnEngine._dispatch`  (lines 1494–1534)

```
async def _dispatch(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> ToolResultBlock
```

**Purpose**: Turns one model-requested tool call into a ToolResultBlock the model can read. It also rehydrates any images that were stored outside the DBOS step log.

**Data flow**: It receives the tool context, tool call, and requester map. It first binds the requester-specific context, runs the recorded dispatch step, then either returns text directly or fetches image blobs and builds mixed text/image result blocks.

**Call relations**: TurnEngine._model_round calls it for each tool call segment. It delegates side-effect-safe execution to _dispatch_step.

*Call graph*: calls 2 internal fn (_bind_requester, _dispatch_step); called by 1 (_model_round); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._bind_requester`  (lines 1536–1575)

```
async def _bind_requester(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Adjusts a tool call so it runs on behalf of the correct member when the model names a specific inbound message. This is important in multi-member turns.

**Data flow**: It copies the tool input, removes and validates the special requester field if present, finds the corresponding member, chooses member-specific sandbox and subagent controls when providers are available, and returns an updated context plus cleaned tool call.

**Call relations**: TurnEngine._dispatch calls it before running _dispatch_step. If binding fails, _dispatch turns the failure into an error result for the model.

*Call graph*: called by 1 (_dispatch); 3 external calls (model_copy, replace, UUID).


##### `TurnEngine._offload`  (lines 1577–1602)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text content into the sandbox’s private tool-output directory and returns the file path. This keeps huge tool outputs or salvaged model text out of the prompt.

**Data flow**: It receives a file name and text content. It ensures the tool-output directory exists, writes the file, logs and emits metrics on failure, and returns either the path or none.

**Call relations**: TurnEngine._dispatch_step uses it for oversized successful tool results. TurnEngine._model_round uses it to save partial model output after truncation.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._dispatch_step`  (lines 1605–1731)

```
async def _dispatch_step(self, context: ToolContext, call: ToolUseBlock) -> DispatchResult
```

**Purpose**: Runs one tool call end to end as a recorded DBOS step. Recording means a crash replay returns the same result without repeating a side effect.

**Data flow**: It receives the tool context and call, publishes activity, validates the tool name and arguments, fires pre-tool hooks, runs the handler, collects text and images, bounds or offloads large text, wraps untrusted content, fires post-tool hooks, stores successful images in the blob store, and returns a DispatchResult.

**Call relations**: TurnEngine._dispatch calls it. It calls _publish_activity, _offload, _bounded, and _bounded_image while preparing a replay-safe result.

*Call graph*: calls 4 internal fn (_bounded_image, _offload, _publish_activity, _bounded); called by 1 (_dispatch); 8 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, replace).


##### `TurnEngine._bounded_image`  (lines 1733–1761)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized tool-result images before sending them back to the model. This avoids provider limits and wasted image detail the model will not use.

**Data flow**: It receives an ImageBlock with base64 data. It decodes and opens the image in a worker thread, leaves it alone if small enough, otherwise thumbnails it, re-encodes it, and returns a new ImageBlock. If image processing fails, it logs and returns the original image.

**Call relations**: TurnEngine._dispatch_step calls it before storing image results in the blob store.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._publish_activity`  (lines 1763–1782)

```
async def _publish_activity(self, call: ToolUseBlock) -> None
```

**Purpose**: Publishes a live notice that a tool call has started. This helps users understand what the agent is doing during long turns.

**Data flow**: It receives the tool call. For load_skill it publishes the skill name; otherwise it builds a short JSON preview of the arguments plus any user-facing description and publishes a ToolCall frame.

**Call relations**: TurnEngine._dispatch_step calls it before validating and running the tool. It uses _publish so live publish failures do not fail the turn.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_dispatch_step); 3 external calls (__init__, __init__, dumps).


##### `TurnEngine._commit`  (lines 1784–1834)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request: CredentialRequest |
```

**Purpose**: Commits the terminal outcome of a turn and publishes it live. It retries database commit failures so a client’s wait does not end in limbo.

**Data flow**: It receives status, usage, answer, optional error and pending request payloads, plus arrival-safety options. It repeatedly calls _commit_once with backoff until it gets a result, then publishes a Terminal frame, emits metrics, logs, and returns the frame or none.

**Call relations**: TurnEngine.run calls it for done, denied, and failed outcomes. It delegates the actual database transaction to _commit_once.

*Call graph*: calls 2 internal fn (_commit_once, _publish); called by 1 (run); 4 external calls (__init__, sleep, emit_metric, log).


##### `TurnEngine._commit_once`  (lines 1836–1934)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction to record a turn’s terminal frame and bill usage. It is the durable point where a turn becomes done or failed.

**Data flow**: It totals usage, optionally locks the conversation and checks for unabsorbed arrivals, records turn usage, reads final cost, builds a TerminalFrame, and updates the turn row. If another execution already committed, it reads and returns that existing terminal.

**Call relations**: TurnEngine._commit calls it inside a retry loop. It uses accounting helpers and database locks to keep terminal state and billing consistent.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 9 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx).


##### `TurnEngine._park`  (lines 1936–1973)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Puts a turn into a resumable parked state when spending or seat checks stop it. Parked is not a failure; it means work can continue after limits change.

**Data flow**: It receives the parking message and current usage. In one transaction it marks the turn parked, records consumed usage, and releases inbound messages claimed by the attempt. It then publishes a Parked frame and logs metrics if the update succeeded.

**Call relations**: TurnEngine.run calls it after catching TurnParked from _enforce_spend.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 1975–1984)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Safely publishes live frames without letting publish failures break the turn. The database state remains the source of truth.

**Data flow**: It receives a live frame, tries to publish it through the hub, and logs any exception instead of raising it.

**Call relations**: TurnEngine._commit, _park, _publish_activity, and _publish_cost all use it for live updates.

*Call graph*: called by 4 (_commit, _park, _publish_activity, _publish_cost); 1 external calls (log).


##### `TurnEngine._bill_cancelled`  (lines 1986–2005)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Records model usage even if the workflow is cancelled. This keeps billing from ignoring tokens already consumed.

**Data flow**: It totals the usage events and tries to write them to the accounting ledger. If billing fails during cancellation, it logs the failure and does not block cancellation.

**Call relations**: TurnEngine.run calls it when DBOS cancellation or asyncio cancellation interrupts the turn.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 2007–2012)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this execution did not get ownership of the turn. It either republishes an already-finished terminal result or quietly backs off.

**Data flow**: It creates a TranscriptRepair helper and asks it to resolve the unclaimed turn. The result is a terminal frame if one was already committed, or none if another worker is still running it.

**Call relations**: TurnEngine.run calls it when _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._persist_transcript`  (lines 2014–2017)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Persists the final successful transcript through TranscriptRepair. It is a small wrapper that keeps run’s main flow readable.

**Data flow**: It receives messages, answer, system prompt, and injected context, creates a repair helper, and delegates the write.

**Call relations**: TurnEngine.run calls it after committing a done terminal.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


##### `TurnEngine._persist_inbound`  (lines 2019–2024)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Persists inbound messages without an assistant answer for non-done exits. This keeps user input available to future turns even when the current run fails or parks.

**Data flow**: It receives absorbed arrival messages and an optional founding denial, creates a repair helper, and delegates inbound-only persistence.

**Call relations**: TurnEngine.run calls it on non-done terminals, cancellation, and unexpected errors.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).

## 📊 State Registers Touched

- `reg-model-catalog` — The shared list of available AI models, their limits, features, provider names, and calling rules.
- `reg-model-provider-adapters` — The shared provider clients that translate internal model requests into Anthropic, OpenAI, OpenRouter, or similar APIs.
- `reg-pricing-table` — The shared price list used to turn model and service usage into cost records.
- `reg-conversation-state` — The durable conversation record that ties a surface, agent, audience, sandbox handle, and message history together.
- `reg-turn-queue` — The durable queue of conversation turns waiting to be claimed, run, completed, cancelled, or retried.
- `reg-transcript-state` — The stored conversation transcript, including exact recent messages and compact summaries of older content.
- `reg-tool-catalog` — The shared catalog of tools the model is allowed to see and call during a turn.
- `reg-sandbox-session` — The saved or live sandbox workspace where an agent can run commands and keep files across tool calls.
- `reg-memory-store` — The long-term memory store of remembered facts and recall results used to inform later responses.
- `reg-live-stream-hub` — The live stream of turn updates that lets clients watch progress and reconnect without losing recent events.
- `reg-surface-installations` — The saved Slack, web, terminal, and other surface bindings used to receive messages and send replies back.
- `reg-fleet-presence` — The shared record of live runtime processes used for supervision, cancellation, and recovery after crashes.
- `reg-accounting-ledger` — The shared usage ledger that records tokens, egress, sandbox usage, billing exports, and spend-limit checks.
- `reg-observability-context` — The shared logging, metrics, tracing, and trace-link state used to understand work across requests and subagents.
- `reg-proposal-governance` — The saved proposals and safety checks used to govern prompt or system improvements before applying them.
- `reg-subagent-delegation-state` — The parent-child turn and conversation links plus in-flight child-task tracking used to coordinate delegated subagents, cancellation, and result collection.
- `reg-prompt-template-state` — The canonical prompt and instruction templates, versions, and digests used to assemble model prompts and guard prompt-improvement proposals against stale edits.
- `reg-outbound-http-client-pool` — Shared outbound HTTP client/session pool state used for connection reuse, retries, and provider/API calls across model adapters, connectors, search, tools, and billing jobs.
- `reg-model-token-budget` — The per-turn model budget state derived from model context/output limits and spend policy, used while assembling prompts, truncating or summarizing context, and tracking remaining usage during model calls.
