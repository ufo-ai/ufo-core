# Agent Model Loop and Transcript Evolution  `stage-10`

This stage is the main work loop for one agent turn. It is where the system takes the current conversation, asks an AI model what to do next, reacts to the model’s answer, and writes the result back safely. The engine is the conductor. It claims the turn so two workers do not do the same job, gathers the transcript, sends the request, listens to streamed model events as they arrive, runs any requested tools, accepts new user messages that appear mid-turn, tracks cost and reasoning, and decides whether to keep going or finish with a final answer.

The transcript module is the notebook. It reads and writes the conversation in shared storage, while guarding against an older write replacing a newer one. The Anthropic and OpenAI modules are translators. UFO uses its own common request and event format, but each provider speaks a different API language. These files send the model request, stream the provider’s reply back into UFO’s format, and apply retry and error handling so the loop can stay reliable.

## Files in this stage

### Turn orchestration
Runs the central agent turn loop that coordinates model calls, tool execution, mid-turn messages, costs, recovery, and final transcript commits.

### `core/src/ufo/loop/engine.py`

`orchestration` · `turn execution`

This is the central turn engine. A “turn” is one unit of agent work, like one reply in a conversation or one prepared tool action submitted by a panel. The file’s job is to make that work safe and durable. It first claims the turn in the database so two workers do not run the same turn at once. Then it loads the prior conversation, applies prompt hooks, checks spending and seat limits, calls the model, streams text to listeners, runs tools, and repeats until the model gives a final answer.

A key idea here is replay safety. The code uses DBOS steps, meaning important side-effecting actions are recorded so that after a crash the workflow can resume without calling the model again, running the same tool twice, or consuming the same queued message twice. Think of it like a checklist where completed items are stamped; after a power outage, the worker starts at the first unstamped item.

The engine also protects the model’s context window by compacting old messages, offloading huge tool output into files, shrinking large images, and recovering from some model overflow errors. It records token usage for billing, publishes live progress frames, parks turns when spend caps are hit, and writes a durable terminal frame so clients know how the turn ended.

#### Function details

##### `_claim_turn`  (lines 174–216)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> bool
```

**Purpose**: Claims a queued or parked turn for one specific workflow attempt and marks it as running. This prevents duplicate workers from doing the same turn at the same time.

**Data flow**: It takes a turn id and an attempt id, reads the turn’s conversation, locks that conversation row, and updates the turn to running only if it is safe to do so. It returns true when this attempt owns the turn, and false when another execution or terminal state already owns it.

**Call relations**: TurnEngine._mark_running calls this at the start of normal and intent turns. _claim_turn_with_handoff also calls it before looking for a next queued turn to hand off.

*Call graph*: called by 2 (_mark_running, _claim_turn_with_handoff); 6 external calls (and_, delete, or_, select, update, workspace_tx).


##### `_claim_turn_with_handoff`  (lines 227–284)

```
async def _claim_turn_with_handoff(turn_id: UUID, attempt: str) -> tuple[bool, _TurnHandoff | None]
```

**Purpose**: Claims the current turn and, if possible, marks the next queued turn in the same conversation for dispatch. It is a helper for orderly conversation handoff.

**Data flow**: It receives a turn id and attempt id, first tries to claim the current turn, then locks the conversation and checks for the earliest queued next turn. It returns whether the current claim succeeded and, if it stamped a next turn, a small handoff record containing ids needed to dispatch it.

**Call relations**: It builds on _claim_turn and database locking. In the wider workflow, it is meant for code that wants to continue a conversation queue without racing another dispatcher.

*Call graph*: calls 1 internal fn (_claim_turn); 5 external calls (__init__, select, update, workspace_tx, uuid4).


##### `ModelStreamError.__init__`  (lines 404–405)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Creates an exception that preserves a model-stream failure together with any partial text already produced. This matters because partial model output may have already cost money and may be useful for recovery.

**Data flow**: It receives the model’s error class, error message, and optional partial output, then stores all three inside the exception arguments. Nothing is returned except the constructed exception object.

**Call relations**: TurnEngine._stream_recovering_overflow raises this after a recorded stream result reports an error, so higher-level turn logic can handle model failures without losing usage or partial output.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 407–409)

```
def __str__(self) -> str
```

**Purpose**: Formats the model error as readable text that includes the provider’s original error class. This keeps overflow detection and logs meaningful.

**Data flow**: It reads the stored error class and message from the exception and combines them into one string. The partial output is deliberately left out.

**Call relations**: It is used whenever Python turns the exception into text, such as logging or terminal error reporting.


##### `ModelStreamError.model_error_class`  (lines 412–414)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original model provider’s error class name. Callers use this to distinguish truncation or context overflow from other failures.

**Data flow**: It reads the first stored value from the exception and returns it as a string. It does not change anything.

**Call relations**: TurnEngine._model_round and TurnEngine._commit_once rely on this property when deciding how to recover and what error class to show in the terminal frame.


##### `ModelStreamError.partial_output`  (lines 417–419)

```
def partial_output(self) -> str
```

**Purpose**: Returns any text or partial tool-call JSON the model produced before the stream failed. This lets the engine save useful work instead of forcing the model to regenerate everything.

**Data flow**: It reads the stored partial-output field and returns it. No external state changes.

**Call relations**: TurnEngine._model_round uses it during truncation recovery, usually by offloading the partial output to a sandbox file and telling the model where to find it.


##### `ModelStreamError.model_error_message`  (lines 422–424)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original model provider’s error message. This gives terminal records a useful, provider-specific explanation.

**Data flow**: It reads the stored message from the exception and returns it. It does not include partial output.

**Call relations**: TurnEngine._commit_once uses it when building a failed terminal frame for a model stream error.


##### `TurnParked.__init__`  (lines 431–433)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates an exception meaning the turn should pause instead of finish because a spend or seat rule stopped it. Parking is resumable, unlike failure.

**Data flow**: It receives a human-readable message, stores it on the exception, and makes it available to the code that publishes the parked state.

**Call relations**: TurnEngine._enforce_spend raises this when a cap or seat check blocks further work. TurnEngine.run catches it and calls TurnEngine._park.

*Call graph*: called by 1 (_enforce_spend).


##### `_dispatch_segments`  (lines 442–466)

```
def _dispatch_segments(tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]) -> Iterator[tuple[ToolUseBlock, ...]]
```

**Purpose**: Splits a batch of model-requested tool calls into groups that can safely run together. It keeps risky or order-dependent tools as barriers so actions happen in the same order the model asked for them.

**Data flow**: It receives the tool registry and the tool calls from one model round, checks each tool’s parallel-safety flag, and yields ordered groups. Safe consecutive calls can share a group, while unsafe or unknown calls become their own group.

**Call relations**: TurnEngine._model_round uses this before dispatching tools. It hands each segment into binding and dispatch so parallelism never breaks the model’s intended order.

*Call graph*: calls 1 internal fn (get); called by 1 (_model_round).


##### `_parse_args`  (lines 469–471)

```
def _parse_args(partials: list[str]) -> dict[str, object]
```

**Purpose**: Turns streamed fragments of a tool-call argument JSON into a Python dictionary. Empty arguments become an empty dictionary.

**Data flow**: It receives a list of partial JSON strings, joins them, and parses the result if it contains non-whitespace text. The output is a dictionary used as the tool call’s input.

**Call relations**: TurnEngine._stream_once uses this after a model stream finishes and all tool-call argument fragments have arrived.

*Call graph*: called by 1 (_stream_once); 1 external calls (loads).


##### `_context_tag`  (lines 474–489)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the small metadata block placed before a user message so the model knows when, where, and from whom the message came. The model has no real clock, so this tag supplies that missing context.

**Data flow**: It receives a message id, optional turn context, and admission time, formats the time in the sender’s timezone when known, and returns a text tag. It includes sender and source only when supplied.

**Call relations**: TranscriptRepair.load_messages uses it for the founding inbound message, and TurnEngine._render_arrival uses it for later messages absorbed during a running turn.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 492–497)

```
def _bounded(content: str) -> str
```

**Purpose**: Cuts overly large text down to the maximum size allowed for tool-result context. It adds a clear note saying how much text was dropped.

**Data flow**: It receives a string. If it is short enough, it returns it unchanged; otherwise it returns the prefix plus a truncation notice.

**Call relations**: TurnEngine._dispatch_step uses it for error output and fallback text limits, and TurnEngine._model_round uses it when reporting finish-tool validation errors.

*Call graph*: called by 2 (_dispatch_step, _model_round).


##### `_meter_dispatch`  (lines 500–527)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str) -> None
```

**Purpose**: Records metrics for one tool dispatch: what tool ran, how long it took, and how it ended. This helps operators see slow tools and failure patterns.

**Data flow**: It receives the registry, tool call, start time, outcome label, optional error class, and profile. It normalizes unknown tool names, then emits a count and a duration measurement.

**Call relations**: TurnEngine._bind_or_error records binding failures through this helper, and TurnEngine._dispatch_step records the final outcome of real dispatch work.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 530–572)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedSkill, ...]]
```

**Purpose**: Finds which skills were successfully loaded in the visible message window. This prevents the engine from injecting the same skill instructions repeatedly.

**Data flow**: It scans messages for completed load_skill calls and their matching results, then asks the skill registry for each skill’s closure, meaning that skill plus its dependencies. It yields only loads that can be trusted as complete and uncut.

**Call relations**: TurnEngine._reseed_loaded_skills calls this after compaction or before dispatch so the compaction tracker knows which skill context is still in front of the model.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 575–593)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts a structured final payload from a successful tool result when a special tool, such as ask_user or connect_account, was the last action in a round. It trusts the handler’s returned structure, not the raw model arguments.

**Data flow**: It receives the round’s tool calls, tool results, the tool name to look for, and a validation model. If the last call and result match, it parses the result’s JSON payload and returns a validated object; otherwise it returns null.

**Call relations**: TurnEngine._model_round uses it after tool dispatches to detect pending questions, credential requests, and account-connection requests. TurnEngine.run_intent uses it for prepared intent results too.

*Call graph*: called by 2 (_model_round, run_intent); 1 external calls (loads).


##### `_total_usage`  (lines 596–602)

```
def _total_usage(usage_events: list[Usage]) -> Usage
```

**Purpose**: Adds many model usage events into one total usage record. This gives billing and live cost reporting a single summary.

**Data flow**: It receives a list of usage records and sums input, output, cache-read, and cache-write token counts. It returns a new Usage object with those totals.

**Call relations**: Cost publishing, spend checks, terminal commits, parking, cancellation billing, and model-round metrics all call this before pricing or reporting usage.

*Call graph*: called by 6 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost, _stream_once); 1 external calls (__init__).


##### `TranscriptRepair.resolve`  (lines 617–641)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes an already-committed terminal result when a duplicate or redelivered workflow no longer owns the turn. This helps waiting clients finish even if the original worker crashed after committing but before publishing.

**Data flow**: It reads the turn’s terminal frame from the database. If none exists, it returns null; otherwise it persists the inbound transcript if needed, tries to publish the terminal frame live, and returns the frame.

**Call relations**: TurnEngine._resolve_unclaimed reaches this through TurnEngine._repair when _mark_running fails because the turn is already running elsewhere or already finished.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 643–650)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the full successful conversation transcript, including the assistant’s final answer. This is what makes later turns see the completed exchange.

**Data flow**: It receives prior messages, the answer, the system prompt, and injected context. It appends an assistant message containing the answer and passes the full conversation to write_conversation.

**Call relations**: TurnEngine._persist_transcript delegates here after a done turn or intent turn is committed.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 652–671)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Writes only the user-side messages for turns that did not finish cleanly, such as failed, denied, cancelled, or parked turns. It avoids saving partial assistant errors as if they were real answers.

**Data flow**: It loads the founding inbound, optionally replaces it with a denial marker, appends absorbed arrivals, and writes that conversation snapshot. The transcript remains monotonic so a fuller successful transcript can win.

**Call relations**: TranscriptRepair.resolve calls it when repairing a terminal publish, and TurnEngine._persist_inbound delegates here during failure, cancellation, parking, or non-done terminal flows.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 673–682)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the prior transcript plus this turn’s inbound user message. For normal user turns, it prefixes the inbound with the context tag the model needs.

**Data flow**: It reads prior messages, prepares the turn’s inbound text, adds context metadata unless this is a subagent turn, and returns a tuple of messages ready for the model.

**Call relations**: TurnEngine._load_messages delegates here, and TranscriptRepair.persist_inbound uses it when preserving user messages after a non-successful turn.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 684–690)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the conversation history before this turn only. It prevents a replay from reading transcript content that this same turn already wrote.

**Data flow**: It reads the stored transcript. If none exists, or if the stored sequence is at or after this turn, it returns an empty tuple; otherwise it returns the stored messages.

**Call relations**: TranscriptRepair.load_messages and TranscriptRepair.persist_inbound use this as the safe base history.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 692–712)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None) -> None
```

**Purpose**: Writes a conversation snapshot to durable transcript storage, retrying a few times if storage briefly fails. This keeps transcript persistence from being too fragile.

**Data flow**: It receives messages plus optional system and injected context, wraps them in a Conversation object, and attempts to write it. On failures it logs, waits briefly, and retries.

**Call relations**: TranscriptRepair.persist_transcript and TranscriptRepair.persist_inbound both funnel their final transcript writes through this method.

*Call graph*: called by 2 (persist_inbound, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 745–756)

```
def exited(self, status: str) -> None
```

**Purpose**: Records how long one execution of a turn took and how many model rounds it ran. It records only the first exit so cleanup code does not double-count.

**Data flow**: It receives an exit status, checks whether this meter has already ended, then emits duration and round metrics using the meter’s start time and profile. It marks itself ended.

**Call relations**: TurnEngine._commit calls it after terminal commit or terminal readback. TurnEngine.run and run_intent also call it directly for parked, cancelled, or preempted exits.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 794–805)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the engine was assembled consistently before it starts running. It catches mismatched audiences and an illegal finish-tool name early.

**Data flow**: It reads the engine’s tool extension contexts, hook chain, audience, output model, and tool registry. It raises a ValueError if tool or hook audiences differ from the turn, or if a subagent tool set already defines the reserved finish tool.

**Call relations**: This runs automatically after TurnEngine is constructed, before run or run_intent can use the engine.


##### `TurnEngine.profile`  (lines 808–810)

```
def profile(self) -> str
```

**Purpose**: Returns the telemetry profile name for this turn. Main-agent turns and subagent turns are separated in metrics.

**Data flow**: It reads the turn’s subagent profile and passes it through the shared telemetry formatter. The result is a short profile string.

**Call relations**: TurnEngine.run, run_intent, _stream_once, _meter_dispatch, _commit, and related metric paths use this property to label observations.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 812–987)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal conversational turn from claim to terminal result. This is the main workflow body for model-driven agent work.

**Data flow**: It starts telemetry, builds a ToolContext, claims the turn, prepares system and user messages, then loops through model rounds until an answer or special terminal request is ready. It commits done, failed, parked, or cancelled outcomes, bills usage, publishes live frames, and persists the right transcript content.

**Call relations**: This is the top-level orchestrator for chat turns. It calls the helpers that claim ownership, load messages, run model rounds, commit results, park on caps, release arrivals, bill cancelled work, and persist transcripts.

*Call graph*: calls 12 internal fn (_bill_cancelled, _commit, _load_messages, _mark_running, _model_round, _park, _persist_inbound, _persist_transcript, _release_unabsorbed, _repair (+2 more)); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, escape, monotonic, emit_metric, log (+1 more)).


##### `TurnEngine.run.rank_find`  (lines 836–853)

```
async def rank_find(system: str, user: str) -> str
```

**Purpose**: Runs a small host-side model call used by the browser find tool to rank page elements. It lets browser tooling use the same model while charging usage to the active turn.

**Data flow**: It receives a system prompt and user prompt, builds a model request with reasoning off and a smaller token limit, streams text pieces into a string, and appends usage events to the turn’s shared usage list. It returns the ranked text result.

**Call relations**: TurnEngine.run places this function into ToolContext as the find callback. Browser tools can call it during tool execution, and its usage is later included in turn billing.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine.run_intent`  (lines 989–1102)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a prepared intent turn by dispatching exactly one tool call described by the inbound envelope, without asking the model to paraphrase or decide. This is used for structured panel actions.

**Data flow**: It claims the turn, parses the inbound JSON as a ToolIntent, builds a ToolUseBlock, binds requester authority, dispatches the tool, then commits either a done frame or a failed frame. It writes a transcript with the tool result.

**Call relations**: It shares the same claim, binding, dispatch, commit, and transcript machinery as normal turns, but bypasses _model_round and turn-level prompt hooks.

*Call graph*: calls 8 internal fn (_bind_or_error, _commit, _dispatch_step, _load_messages, _mark_running, _persist_transcript, _resolve_unclaimed, _final_act); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, monotonic, emit_metric, log (+1 more)).


##### `TurnEngine._scheduled_system`  (lines 1104–1138)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. This gives the agent relevant past context when it wakes up on a schedule rather than from a fresh user message.

**Data flow**: It receives the current system prompt, searches memory for the scheduled inbound text, formats matching memories safely, and appends them in a recalled-memory block. If memory search fails or returns nothing, it returns the original system prompt.

**Call relations**: TurnEngine.run calls this only when the turn’s admission source is scheduled.

*Call graph*: called by 1 (run); 5 external calls (__init__, timeout, escape, audience_subjects, log).


##### `TurnEngine._mark_running`  (lines 1140–1148)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn for the current engine attempt. It is the engine’s ownership gate.

**Data flow**: It sends the turn id and attempt id to _claim_turn and returns the resulting true-or-false claim status.

**Call relations**: TurnEngine.run and run_intent call it before doing any model or tool work. If it returns false, they hand off to _resolve_unclaimed.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 1150–1151)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Builds a TranscriptRepair helper for this engine’s turn, transcript store, and hub. It keeps transcript repair logic separate from the main engine.

**Data flow**: It reads the engine’s turn, transcript, and hub fields and returns a TranscriptRepair object. It does not touch external systems by itself.

**Call relations**: The engine’s load, persist, and unclaimed-resolution wrappers call this to delegate transcript work.

*Call graph*: called by 5 (_load_messages, _persist_inbound, _persist_transcript, _resolve_unclaimed, run); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 1153–1154)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the messages that should be shown to the model at the start of this turn. It is a short wrapper around TranscriptRepair.

**Data flow**: It creates a repair helper and asks it to load messages. The returned tuple contains prior history plus the current inbound message.

**Call relations**: TurnEngine.run and run_intent call this during setup and transcript persistence.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._model_round`  (lines 1156–1329)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the repeated model-and-tools loop until the model gives a final answer or the round budget is exhausted. This is where most conversational work happens.

**Data flow**: It receives the current tool context, messages, usage list, system prompt, arrival tracking, requester tracking, and meter. Each loop absorbs new arrivals, enforces spend, compacts context, streams one model round, dispatches tool calls if present, and feeds results back into messages. It returns the final messages, answer text, and any pending question, credential request, or connect request.

**Call relations**: TurnEngine.run calls this after setup. It coordinates _absorb_arrivals, _enforce_spend, compaction, _stream_recovering_overflow, _dispatch_segments, _bind_or_error, _dispatch, _final_act, _force_finish, and _force_final.

*Call graph*: calls 13 internal fn (_absorb_arrivals, _bind_or_error, _dispatch, _enforce_spend, _force_final, _force_finish, _offload, _publish_cost, _reseed_loaded_skills, _stream_recovering_overflow (+3 more)); called by 1 (run); 6 external calls (__init__, __init__, __init__, gather, emit_metric, log).


##### `TurnEngine._absorb_arrivals`  (lines 1331–1368)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Adds queued inbound messages that arrived while the turn was already running. This lets a live conversation fold multiple user messages into one continuing model turn.

**Data flow**: It receives the current messages and tracking lists, asks _claim_arrivals for newly claimed arrivals, appends rendered arrivals or denial notices to both the model window and arrival log, and records active requesters. Subagent turns ignore arrivals.

**Call relations**: TurnEngine._model_round calls this at the start of every round so new messages are inserted between model/tool cycles, never in the middle of one.

*Call graph*: calls 1 internal fn (_claim_arrivals); called by 1 (_model_round); 3 external calls (__init__, __init__, escape).


##### `TurnEngine._render_arrival`  (lines 1370–1394)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Turns one queued inbound row into the exact text the model should see, after prompt-submit hooks approve or deny it. Denied messages reveal only safe denial text.

**Data flow**: It receives the message id, body, context, speaker id, and creation time, fires the user_prompt_submit hook, and returns either rendered content or a denial string. Approved content gets a context tag and optional injected context block.

**Call relations**: TurnEngine._claim_arrivals calls this inside its recorded step so replay uses the same rendered arrival without firing hooks again.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 1397–1447)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Drains pending inbound messages for this conversation and records the exact batch as a replay-safe step. This prevents messages from being lost or consumed twice after a crash.

**Data flow**: It receives ids already absorbed, updates eligible inbound rows to mark them consumed by this turn, reads their details, renders each one, and returns Arrival objects in sequence order.

**Call relations**: TurnEngine._absorb_arrivals calls this during each model round. It calls _render_arrival for hook processing and message formatting.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 6 external calls (__init__, model_validate, and_, or_, update, workspace_tx).


##### `TurnEngine._release_unabsorbed`  (lines 1449–1468)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns arrivals that were stamped for this turn but never safely absorbed. This lets a later turn pick them up after failure or cancellation.

**Data flow**: It receives the ids known to be absorbed, then clears consumed_turn_id for any other inbound rows stamped with this turn. If the database update fails, it logs and continues.

**Call relations**: TurnEngine.run calls this on exceptions, workflow cancellation, and preemption paths where the turn did not commit normally.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._force_final`  (lines 1470–1506)

```
async def _force_final(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, requesters: dict[UUID, ActiveMessage]) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Gets a best-effort final answer when the normal tool-use round limit is exhausted. It avoids failing a turn merely because the model kept looping.

**Data flow**: It logs and emits a budget-exhausted metric, checks spend, compacts once more, then either forces a subagent finish tool call or asks the main model to answer with no tools available. It returns final messages and answer text.

**Call relations**: TurnEngine._model_round calls this after using all allowed rounds.

*Call graph*: calls 4 internal fn (_enforce_spend, _force_finish, _publish_cost, _stream_recovering_overflow); called by 1 (_model_round); 3 external calls (__init__, emit_metric, log).


##### `TurnEngine._force_finish`  (lines 1508–1533)

```
async def _force_finish(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str) -> tuple[tuple[Message, ...], str]
```

**Purpose**: Forces a subagent to end through the reserved finish tool and validate its output schema. This keeps parent-facing subagent results structured.

**Data flow**: It receives messages, usage, and system prompt, runs a model round where only finish is offered and required, then validates the finish arguments against the output model. It returns messages and canonical JSON answer text or raises on malformed output.

**Call relations**: TurnEngine._model_round calls this when a subagent answered in prose, and _force_final calls it when a subagent runs out of rounds.

*Call graph*: calls 2 internal fn (_publish_cost, _stream_recovering_overflow); called by 2 (_force_final, _model_round).


##### `TurnEngine._stream_recovering_overflow`  (lines 1535–1578)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, offer_tools: bool=True, force_finish: bool=False, active_requests: tuple[str, ...]=()
```

**Purpose**: Runs one model round and retries once with forced compaction if the provider says the context is too large. This is a safety valve when proactive compaction was not enough.

**Data flow**: It receives messages, usage, system prompt, and tool-offering options. It calls _stream_once, records usage, turns recorded stream errors into ModelStreamError, and on context overflow compacts then tries again. It returns the message window used and the stream result.

**Call relations**: TurnEngine._model_round, _force_final, and _force_finish all use this instead of calling _stream_once directly.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_once); called by 3 (_force_final, _force_finish, _model_round); 3 external calls (is_context_overflow, emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 1580–1622)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks seat and spending rules before each model round. If the turn has crossed a cap, it parks the turn rather than spending further.

**Data flow**: It receives usage accumulated so far and active requester messages, gathers relevant member ids, checks seat admission, prices pending usage, and asks the spend evaluator for a decision. If not allowed, it raises TurnParked with the reason.

**Call relations**: TurnEngine._model_round calls it before every model call, and _force_final calls it before the forced closing round.

*Call graph*: calls 2 internal fn (__init__, _total_usage); called by 2 (_force_final, _model_round); 6 external calls (__init__, __init__, applicable_caps_absent, audience_member, workspace_tx, seat_gate_absent).


##### `TurnEngine._stream_once`  (lines 1625–1778)

```
async def _stream_once(self, messages: tuple[Message, ...], system: str, offer_tools: bool=True, force_finish: bool=False) -> StreamResult
```

**Purpose**: Performs one actual model streaming call as a replay-safe DBOS step. It collects text, tool calls, reasoning blocks, and usage while publishing live text deltas.

**Data flow**: It builds a ModelRequest from system prompt, messages, model settings, tools, and finish-tool options. As events stream back, it buffers text for live publishing, records tool-call fragments, reasoning, and usage, then returns a StreamResult. If the model stream raises, it returns the error details and partial output instead of raising inside the step.

**Call relations**: TurnEngine._stream_recovering_overflow is its caller. The returned tool ids and content drive later tool dispatch steps, and the recorded step prevents duplicate model calls during crash recovery.

*Call graph*: calls 2 internal fn (_parse_args, _total_usage); called by 1 (_stream_recovering_overflow); 7 external calls (__init__, __init__, __init__, __init__, monotonic, emit_histogram, emit_metric).


##### `TurnEngine._stream_once.flush`  (lines 1683–1689)

```
async def flush() -> None
```

**Purpose**: Publishes buffered model text to live listeners in chunks. This keeps clients updated without sending every tiny token separately.

**Data flow**: It reads the local text buffer, joins and publishes it as a TextDelta if non-empty, clears the buffer, resets the byte counter, and updates the last-flush time.

**Call relations**: It is an inner helper used only by TurnEngine._stream_once while streaming model output.

*Call graph*: 2 external calls (__init__, monotonic).


##### `TurnEngine._publish_cost`  (lines 1780–1793)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes the turn’s live cost and token count so the user interface can show spending as the turn progresses. Failure to publish does not fail the turn.

**Data flow**: It totals usage events, computes tokens and priced micro-dollars, wraps them in a CostTick frame, and sends it through _publish.

**Call relations**: TurnEngine._model_round calls it after normal model rounds, and forced closing paths call it after forced rounds.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 3 (_force_final, _force_finish, _model_round); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 1795–1806)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skills currently visible to the model. This prevents duplicate skill loading after context compaction changes the message window.

**Data flow**: It scans the message window with _loaded_skill_closures, combines that with preloaded skills, and reseeds the compaction tracker. It changes the shared loaded-skills tracker in place.

**Call relations**: TurnEngine._model_round calls it around compaction, and _stream_recovering_overflow calls it after forced compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 2 (_model_round, _stream_recovering_overflow).


##### `TurnEngine._bind_or_error`  (lines 1808–1829)

```
async def _bind_or_error(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Resolves which member a tool call acts for and turns binding problems into tool-like error results. This keeps invalid model tool requests inside the model feedback loop when possible.

**Data flow**: It receives a base ToolContext, one tool call, and active requester map. It calls _bind_requester; on success it returns a bound call, and on most errors it returns a rejected call carrying error text and outcome labels. Cancellation is re-raised and metered.

**Call relations**: TurnEngine._model_round uses it before tool dispatch, and run_intent uses it for prepared intent dispatch.

*Call graph*: calls 2 internal fn (_bind_requester, _meter_dispatch); called by 2 (_model_round, run_intent); 3 external calls (__init__, __init__, monotonic).


##### `TurnEngine._dispatch`  (lines 1831–1832)

```
def _dispatch(self, bound: _DispatchInput) -> Awaitable[ToolResultBlock]
```

**Purpose**: Starts a recorded tool dispatch step and converts its compact recorded result into the model-facing tool-result block. It is a small bridge between durable execution and model context.

**Data flow**: It receives a bound or rejected dispatch input, passes it to _dispatch_step, and hands the resulting awaitable to _dispatch_result. The output is an awaitable ToolResultBlock.

**Call relations**: TurnEngine._model_round calls this for each bound tool in a dispatch segment.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step); called by 1 (_model_round).


##### `TurnEngine._dispatch_result`  (lines 1834–1854)

```
async def _dispatch_result(self, step: Awaitable[DispatchResult]) -> ToolResultBlock
```

**Purpose**: Rebuilds the model-facing tool result after a dispatch step, including rehydrating any images that were kept out of the DBOS step log. This keeps durable logs small while still showing images to the model.

**Data flow**: It awaits a DispatchResult. If there are no image references, it returns a plain text ToolResultBlock; otherwise it fetches each image from blob storage, builds image blocks, combines them with any text, and returns a ToolResultBlock.

**Call relations**: TurnEngine._dispatch calls this immediately around _dispatch_step results.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._bind_requester`  (lines 1856–1895)

```
async def _bind_requester(self, context: ToolContext, call: ToolUseBlock, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Applies requester authority to a tool call. If the model names a specific inbound message as the requester, this function switches sandbox and subagent access to that member where configured.

**Data flow**: It copies the tool input, parses and removes the requested-by field when present, verifies it points to an active inbound message with a member, chooses the acting member, obtains member-specific sandbox and subagent controls if providers exist, and returns an updated context plus cleaned call.

**Call relations**: TurnEngine._bind_or_error calls this before dispatching model tool calls or intent tool calls.

*Call graph*: called by 1 (_bind_or_error); 3 external calls (model_copy, replace, UUID).


##### `TurnEngine._offload`  (lines 1897–1922)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text into the sandbox’s private tool-output directory and returns the file path. This keeps huge tool output or salvaged partial model text out of the model context.

**Data flow**: It receives a file name and content string, ensures the tool-output directory exists, writes bytes into the sandbox, and returns the path. If writing fails, it logs, emits a metric, and returns null.

**Call relations**: TurnEngine._dispatch_step uses it for oversized successful tool output, and TurnEngine._model_round uses it to save partial output after model truncation.

*Call graph*: called by 2 (_dispatch_step, _model_round); 2 external calls (emit_metric, log).


##### `TurnEngine._dispatch_step`  (lines 1925–2087)

```
async def _dispatch_step(self, bound: _DispatchInput) -> DispatchResult
```

**Purpose**: Runs one tool call as a replay-safe recorded step. It validates input, runs hooks, executes the handler, bounds or offloads output, protects untrusted content, stores images separately, and records metrics.

**Data flow**: It receives either a bound tool call or a rejected call. Rejections become error results immediately. Bound calls publish activity, validate arguments, run pre-tool hooks, execute the tool with an idempotency key when needed, process text and image output, run post hooks, store images in blob storage, and return a compact DispatchResult.

**Call relations**: TurnEngine._dispatch and run_intent call this. It calls _publish_activity, _offload, _bounded_image, _bounded, hooks, tool handlers, blob storage, and _meter_dispatch.

*Call graph*: calls 5 internal fn (_bounded_image, _offload, _publish_activity, _bounded, _meter_dispatch); called by 2 (_dispatch, run_intent); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, replace, monotonic).


##### `TurnEngine._bounded_image`  (lines 2089–2117)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized image blocks before giving them to the model. This avoids provider image-size limits and wasted pixels.

**Data flow**: It receives an ImageBlock with base64 data, tries to decode and open it, leaves it unchanged if already small, otherwise thumbnails it to the edge limit and re-encodes it. If decoding or resizing fails, it logs and returns the original image.

**Call relations**: TurnEngine._dispatch_step calls this before offloading successful tool-result images to blob storage.

*Call graph*: called by 1 (_dispatch_step); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._publish_activity`  (lines 2119–2138)

```
async def _publish_activity(self, call: ToolUseBlock) -> None
```

**Purpose**: Publishes a live activity frame when a tool starts, so clients can show what the agent is doing during long turns. It gives special treatment to skill loading.

**Data flow**: It receives a tool call, builds either a SkillLoad frame or a ToolCall frame with a short input preview and optional user description, and sends it through _publish.

**Call relations**: TurnEngine._dispatch_step calls this just before validating and running a bound tool.

*Call graph*: calls 1 internal fn (_publish); called by 1 (_dispatch_step); 3 external calls (__init__, __init__, dumps).


##### `TurnEngine._commit`  (lines 2140–2210)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Keeps retrying until a terminal result is durably written, then publishes it and records terminal metrics. This is the final gate that makes a client’s wait end reliably.

**Data flow**: It receives desired status, usage, meter, optional answer, error, and structured requests. It calls _commit_once with exponential backoff on database errors, publishes the terminal frame if one is returned, emits metrics for newly committed terminals, records execution timing, logs the outcome, and returns the frame or null.

**Call relations**: TurnEngine.run and run_intent call this for done and failed outcomes. It delegates the transaction details to _commit_once and live delivery to _publish.

*Call graph*: calls 3 internal fn (_commit_once, _publish, exited); called by 2 (run, run_intent); 5 external calls (__init__, sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._commit_once`  (lines 2212–2310)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction to bill usage and write the terminal frame. It can refuse to commit if new arrivals are still waiting to be absorbed.

**Data flow**: It totals usage, optionally locks the conversation and checks pending arrivals, records turn usage, reads final cost, builds a TerminalFrame, and updates the turn if it is still non-terminal. If another path already committed, it reads and returns the existing terminal frame with a false committed flag.

**Call relations**: TurnEngine._commit wraps this with retry, publishing, metrics, and logging.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 9 external calls (__init__, model_validate, and_, or_, select, update, read_turn_cost, record_turn_usage, workspace_tx).


##### `TurnEngine._park`  (lines 2312–2349)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Moves a turn into a non-terminal parked state when spending or seat rules stop it mid-run. Parking preserves billed work and allows later resume.

**Data flow**: It receives the park message and usage events, updates the turn to parked, records consumed usage, releases all inbound rows consumed by this attempt, then publishes a Parked frame and emits metrics if the update succeeded.

**Call relations**: TurnEngine.run calls this when it catches TurnParked from _enforce_spend.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 1 (run); 6 external calls (__init__, update, record_turn_usage, workspace_tx, emit_metric, log).


##### `TurnEngine._publish`  (lines 2351–2360)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Sends a live frame to the hub without allowing publish failures to break the turn. Durable database state remains the source of truth.

**Data flow**: It receives a live frame, tries to publish it for this turn id, and logs any exception. It returns nothing.

**Call relations**: _publish_cost, _publish_activity, _park, and _commit all use this for best-effort live updates.

*Call graph*: called by 4 (_commit, _park, _publish_activity, _publish_cost); 1 external calls (log).


##### `TurnEngine._bill_cancelled`  (lines 2362–2381)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Records model usage for a turn that was cancelled or preempted, as best effort. Even unfinished work may have consumed paid tokens.

**Data flow**: It totals usage events, opens a workspace transaction, and records turn usage for this attempt. If billing fails, it logs the failure and does not block cancellation.

**Call relations**: TurnEngine.run calls this when DBOS cancellation or asyncio cancellation interrupts a normal turn.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (record_turn_usage, workspace_tx, log).


##### `TurnEngine._resolve_unclaimed`  (lines 2383–2388)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles the case where this execution failed to claim the turn. It either republishes an already-finished terminal or quietly steps aside while another execution runs.

**Data flow**: It creates a TranscriptRepair helper and asks it to resolve the unclaimed turn. The output is a terminal frame if one was already committed, otherwise null.

**Call relations**: TurnEngine.run and run_intent call this after _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 2390–2393)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Delegates successful transcript persistence to TranscriptRepair. It keeps the engine’s main flow short.

**Data flow**: It receives messages, answer, system prompt, and injected context, creates a repair helper, and asks it to persist the full transcript.

**Call relations**: TurnEngine.run calls this after done chat turns, and run_intent calls it after intent dispatch.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_inbound`  (lines 2395–2400)

```
async def _persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Delegates preservation of user-side messages for non-successful endings. This ensures later turns still see what users said.

**Data flow**: It receives optional absorbed arrival messages and an optional founding denial marker, creates a repair helper, and asks it to persist inbound-only transcript content.

**Call relations**: TurnEngine.run calls this on failed, cancelled, parked, and non-done terminal paths.

*Call graph*: calls 1 internal fn (_repair); called by 1 (run).


### Transcript persistence
Provides durable transcript reads and writes with concurrency protection so turn state evolves safely.

### `core/src/ufo/loop/transcript.py`

`io_transport` · `conversation turn persistence`

A conversation transcript is the long-lived record of what has happened in a conversation. This file wraps a BlobStore, which is a storage place for raw bytes, and gives the rest of the loop a simple way to read or update that record.

The important safety rule here is the sequence number, called seq. Think of it like a page number in a notebook: page 5 should not be replaced by page 4 after the story has already moved on. Before writing a transcript, this code first reads the currently stored transcript. If there is already one with the same or a newer sequence number, the write is ignored. That makes the first valid write for a turn the authoritative one and prevents late or repeated work from rolling the transcript backward.

The file also hides the storage details. It turns a conversation ID into the blob key used for storage, encodes a Conversation object into bytes before saving it, and decodes bytes back into a Conversation when reading. If no transcript has been saved yet, reading returns None instead of treating that as an error.

#### Function details

##### `Transcript.read`  (lines 17–22)

```
async def read(self) -> Conversation | None
```

**Purpose**: This function fetches the saved transcript for this conversation, if one exists. It gives callers a Conversation object they can use, or None when the transcript has not been written yet.

**Data flow**: It starts with the Transcript object's conversation_id and blob store. It builds the storage key for that conversation, asks the blob store for the saved bytes, and if the blob is missing it returns None. If bytes are found, it decodes them into a Conversation and returns that conversation.

**Call relations**: Transcript.write calls this first so it can compare the stored sequence number with the new one before saving. Inside the read step, it relies on transcript_key to find the right storage location and decode to turn the stored bytes back into a usable conversation record.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 24–28)

```
async def write(self, conversation: Conversation) -> None
```

**Purpose**: This function saves a conversation transcript only if it is newer than what is already stored. It protects the durable record from being overwritten by an older or duplicate update.

**Data flow**: It receives a Conversation to save. First it reads the currently stored transcript. If an existing transcript has a sequence number greater than or equal to the incoming one, it stops without changing storage. Otherwise, it encodes the incoming Conversation into bytes, builds the storage key from the conversation ID, and writes those bytes to the blob store.

**Call relations**: This is the write path used when a conversation turn reaches a committed transcript state. It calls Transcript.read to enforce the sequence-number guard, then hands the accepted conversation to encode and stores it under the key produced by transcript_key.

*Call graph*: calls 1 internal fn (read); 2 external calls (encode, transcript_key).


### Model provider streams
Adapts UFO model requests to external streaming provider APIs and converts their responses back into internal events.

### `core/src/ufo/models/anthropic.py`

`io_transport` · `request handling during model calls`

This file lets the rest of the system talk to Anthropic without knowing Anthropic's exact network format. Think of it as a translator at a live conversation: UFO speaks in its own standard message, image, tool, and reasoning objects; Anthropic expects a different shape; this file converts both directions.

Before sending a request, it turns UFO content blocks into Anthropic content blocks. That includes plain text, images encoded as base64, tool calls, tool results, and Anthropic-style reasoning blocks. It deliberately drops reasoning blocks that came from another provider, because reasoning traces can only be safely given back to the provider that created them.

The main class, `AnthropicClient`, opens a streaming request. As Anthropic sends pieces of the answer, the client yields UFO events: text fragments, tool-call starts, tool-call argument fragments, hidden reasoning blocks, and finally token usage. It also keeps reasoning blocks until the stream finishes, because those blocks must be returned later in the exact order Anthropic requires.

The file is also careful about failure. Timeouts and temporary provider errors are retried with increasing wait times, but only before any output has been shown to the rest of the system. Once output has started, retrying could mix two different attempts, so errors are raised immediately.

#### Function details

##### `anthropic_sdk_client`  (lines 46–50)

```
def anthropic_sdk_client(api_key: str) -> anthropic.AsyncAnthropic
```

**Purpose**: Creates the official Anthropic asynchronous client with UFO's chosen timeout and with the SDK's built-in retries turned off. UFO wants retries to happen in one predictable place, inside `AnthropicClient.complete`.

**Data flow**: It receives an Anthropic API key. It builds an `AsyncAnthropic` client using that key, a fixed request timeout, and zero SDK retries. It returns that ready-to-use client object.

**Call relations**: This is the small setup helper used before model calls begin. It hands back the low-level Anthropic client that `AnthropicClient` stores and later uses to create streaming message requests.

*Call graph*: 1 external calls (AsyncAnthropic).


##### `_anthropic_image`  (lines 53–57)

```
def _anthropic_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO's image representation into the image format Anthropic expects. This is needed whenever a user message or tool result includes an image.

**Data flow**: It receives an `ImageSource`, which contains the image's media type and base64 data. It wraps those fields in Anthropic's dictionary shape for an image block. It returns that dictionary without changing anything else.

**Call relations**: This helper is called by `anthropic_content` when normal message content includes an image, and by `_anthropic_tool_result_part` when a tool result contains an image. It keeps the image conversion rule in one place.

*Call graph*: called by 2 (_anthropic_tool_result_part, anthropic_content).


##### `_anthropic_tool_result_part`  (lines 60–65)

```
def _anthropic_tool_result_part(part: ToolResultContent) -> dict[str, object]
```

**Purpose**: Converts one piece of a tool result into Anthropic's format. Tool results can contain either text or images, so this function chooses the right conversion for each piece.

**Data flow**: It receives one tool-result content item. If the item is text, it returns Anthropic's text-block dictionary. If the item is an image, it sends the image source to `_anthropic_image` and returns the resulting image dictionary.

**Call relations**: This function is used inside `anthropic_content` when a message contains a structured tool result. It hands image parts off to `_anthropic_image` so tool-result images look the same as message images.

*Call graph*: calls 1 internal fn (_anthropic_image); called by 1 (anthropic_content).


##### `anthropic_content`  (lines 68–102)

```
def anthropic_content(content: str | tuple[ContentBlock, ...]) -> str | list[dict[str, object]]
```

**Purpose**: Translates UFO message content into the content shape Anthropic's API accepts. It is the main outbound content converter for text, images, tool calls, tool results, and Anthropic reasoning blocks.

**Data flow**: It receives either a plain string or a tuple of UFO content blocks. A plain string is returned as-is. For block content, it walks through each block and builds a list of Anthropic dictionaries. It keeps Anthropic thinking and redacted-thinking blocks, skips reasoning blocks from the OpenAI-style wire format, converts images through `_anthropic_image`, and converts tool-result parts through `_anthropic_tool_result_part`. The output is ready to place into Anthropic's `messages` request field.

**Call relations**: `AnthropicClient.complete` calls this while building the request it will send to Anthropic. This function relies on `_anthropic_image` and `_anthropic_tool_result_part` for nested image and tool-result conversion, so the client can focus on streaming and retry behavior.

*Call graph*: calls 2 internal fn (_anthropic_image, _anthropic_tool_result_part); called by 1 (complete).


##### `AnthropicClient.complete`  (lines 110–306)

```
async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends one model request to Anthropic and yields UFO-standard events as the streamed answer arrives. It also enforces UFO's retry policy, turns Anthropic stop reasons into clear errors, preserves hidden reasoning blocks in the order Anthropic requires, and finishes by yielding token usage.

**Data flow**: It receives a `ModelRequest` containing the model name, system prompt, conversation messages, tools, token limit, and reasoning preference. It builds Anthropic request arguments, converting each message through `anthropic_content` and adding tool and reasoning settings when needed. It opens Anthropic's stream, reads each incoming event, and turns it into UFO events such as `TextDelta`, `ToolCallStart`, or `ToolCallDelta`. It collects token counts and hidden reasoning blocks while the stream runs. At the end, it raises an error if the answer was truncated or refused, retries certain empty responses, then yields any saved reasoning blocks followed by a final `Usage` record.

**Call relations**: This is the central runtime path for Anthropic model calls. It calls `anthropic_content` before sending the request, then consumes the Anthropic SDK stream. As events arrive, it creates UFO event objects for the rest of the engine. If Anthropic times out or returns a temporary status error before any output has been yielded, it waits and tries again; if output has already begun, it raises the error rather than risk combining two different attempts.

*Call graph*: calls 1 internal fn (anthropic_content); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep, trim_images (+1 more)).


### `core/src/ufo/models/openai.py`

`io_transport` · `request handling`

UFO has its own way to describe a model request: system instructions, messages, images, tool calls, tool results, reasoning blocks, token limits, and so on. OpenAI-style services expect that same information in very specific shapes, and there are two different shapes in use: the older Chat Completions API and the newer Responses API. This file is the adapter between those worlds.

Think of it like a travel plug adapter. The electricity is the same conversation, but the socket shape depends on the provider API. The helper functions translate UFO messages into the format each API accepts, including special handling for images, tool calls, tool results, and hidden reasoning data.

The `OpenAIClient` class then sends the request through the official OpenAI asynchronous client. It reads the provider’s streaming response piece by piece and emits UFO `ModelEvent` objects such as text updates, tool-call starts, tool-call argument chunks, reasoning items, and final usage counts.

The file also protects the rest of the system from common provider problems. It retries timeouts and temporary server errors before any output has been produced, respects `retry-after` delays, treats token-limit stops as truncation errors, and turns provider refusals into explicit refusal errors. Without this file, UFO could not reliably talk to OpenAI-compatible model providers or preserve important conversation state across tool-use rounds.

#### Function details

##### `openai_sdk_client`  (lines 81–87)

```
def openai_sdk_client(api_key: str, base_url: str | None=None) -> openai.AsyncOpenAI
```

**Purpose**: Creates the low-level asynchronous OpenAI SDK client used to talk to OpenAI or an OpenAI-compatible service. It deliberately disables the SDK’s own retries so this file’s retry rules stay in one clear place.

**Data flow**: It takes an API key and optionally a base URL for a compatible provider. It builds an `AsyncOpenAI` client with a fixed timeout and no automatic SDK retries. The result is a ready-to-use network client for later model requests.

**Call relations**: This is the setup doorway for the transport layer. Other code can call it when constructing an `OpenAIClient`, and the returned SDK client is then used by the streaming completion methods.

*Call graph*: 1 external calls (AsyncOpenAI).


##### `_openai_image`  (lines 90–94)

```
def _openai_image(source: ImageSource) -> dict[str, object]
```

**Purpose**: Converts UFO’s internal image object into the image format expected by the Chat Completions API. It packages the image as a data URL, which means the image bytes are embedded directly as base64 text.

**Data flow**: It receives an `ImageSource` containing a media type and base64 image data. It wraps those values in the dictionary shape OpenAI expects for an image URL part. The output is a small provider-ready image block.

**Call relations**: This helper is used whenever chat-format messages need to include images. `openai_messages` uses it for normal image messages, and `_openai_tool_result` uses it when a tool result contains images.

*Call graph*: called by 2 (_openai_tool_result, openai_messages).


##### `_openai_tool_result`  (lines 97–113)

```
def _openai_tool_result(result: str | tuple[ToolResultContent, ...]) -> tuple[str, list[dict[str, object]]]
```

**Purpose**: Splits a tool result into text and images because the Chat Completions API cannot put images directly inside a tool-result message. This keeps tool output usable without losing image results.

**Data flow**: It receives either plain text or a tuple of content blocks from a tool. Text blocks are joined into one text string, while image blocks are converted into OpenAI image parts. It returns both pieces: the text for the tool message and the images for a following user message.

**Call relations**: This is called by `openai_messages` while translating a conversation for the Chat Completions API. It hands image conversion off to `_openai_image`, then gives `openai_messages` the separated text and image pieces so they can be placed where OpenAI allows them.

*Call graph*: calls 1 internal fn (_openai_image); called by 1 (openai_messages).


##### `openai_messages`  (lines 116–175)

```
def openai_messages(system: str, messages: tuple[Message, ...]) -> list[dict[str, object]]
```

**Purpose**: Turns UFO’s internal conversation history into the message list required by OpenAI’s Chat Completions API. It also removes reasoning blocks because that older API shape has no safe place to replay them.

**Data flow**: It receives the system prompt and a tuple of UFO messages. It first trims images through the shared image-trimming helper, then walks through every message block. Text becomes message content, images become OpenAI image parts, tool uses become OpenAI function tool calls, and tool results become tool messages, with any tool-result images lifted into a trailing user message. The output is a list of provider-ready chat messages.

**Call relations**: `OpenAIClient._chat_kwargs` calls this while building the request body for the Chat Completions path. It depends on `_openai_image` and `_openai_tool_result` for image and tool-result conversion, and it uses JSON encoding so tool arguments travel as strings as OpenAI expects.

*Call graph*: calls 2 internal fn (_openai_image, _openai_tool_result); called by 1 (_chat_kwargs); 2 external calls (dumps, trim_images).


##### `responses_input`  (lines 178–261)

```
def responses_input(messages: tuple[Message, ...]) -> list[ResponseInputItemParam]
```

**Purpose**: Turns UFO’s internal conversation history into the input-item format required by OpenAI’s Responses API. Unlike the chat path, this format can carry reasoning items, so this function preserves them when possible.

**Data flow**: It receives a tuple of UFO messages, trims images, and walks through each message block in order. Text and images become Responses input content, tool calls become function-call input items, tool results become function-call output items, and preserved reasoning becomes reasoning input items with encrypted content and summaries. It returns a list of Responses API input items ready to send.

**Call relations**: `responses_request` calls this when building a Responses API request. It is the main translator for the newer API surface, using OpenAI SDK parameter types so the final request matches what `/v1/responses` accepts.

*Call graph*: called by 1 (responses_request); 11 external calls (dumps, ResponseReasoningItemParam, EasyInputMessageParam, ResponseFunctionToolCallParam, ResponseInputImageContentParam, ResponseInputImageParam, FunctionCallOutput, ResponseInputTextContentParam, ResponseInputTextParam, Summary (+1 more)).


##### `responses_request`  (lines 264–294)

```
def responses_request(request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the full request dictionary for OpenAI’s Responses API. It combines model choice, instructions, translated messages, reasoning settings, token limits, streaming, and tool definitions.

**Data flow**: It receives a `ModelRequest`. It converts the messages through `responses_input`, adds the system instructions, model name, maximum output tokens, streaming flag, and a request to include encrypted reasoning content. If tools are available, it converts them into Responses function tools and adds tool-choice rules. The output is a complete keyword-argument dictionary for the SDK call.

**Call relations**: `OpenAIClient._complete_responses` calls this immediately before sending a request to the provider. It sits between UFO’s model request object and the OpenAI SDK’s required request shape.

*Call graph*: calls 1 internal fn (responses_input); called by 1 (_complete_responses); 1 external calls (FunctionToolParam).


##### `OpenAIClient.complete`  (lines 306–309)

```
def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Chooses which OpenAI-style API surface to use for a model request. The decision comes from the model specification, not from guessing based on the model name.

**Data flow**: It receives a `ModelRequest` and reads `self.spec.api_surface`. If the model is marked for the Responses API, it returns the Responses streaming generator. Otherwise, it returns the Chat Completions streaming generator. The output is an asynchronous stream of UFO model events.

**Call relations**: This is the public entry into `OpenAIClient` for completing a model request. It routes the request to either `_complete_responses` or `_complete_chat`, so the rest of the system can call one method without caring which OpenAI API shape the model needs.

*Call graph*: calls 2 internal fn (_complete_chat, _complete_responses).


##### `OpenAIClient._chat_kwargs`  (lines 311–340)

```
def _chat_kwargs(self, request: ModelRequest) -> dict[str, Any]
```

**Purpose**: Builds the request dictionary for the Chat Completions API. It prepares messages, token limits, streaming options, reasoning effort when allowed, and tool definitions.

**Data flow**: It receives a `ModelRequest`. It converts UFO messages through `openai_messages`, asks the model spec what reasoning setting is valid for this request, and adds tools and tool-choice settings if present. The result is a provider-ready dictionary used by the chat completion SDK call.

**Call relations**: `OpenAIClient._complete_chat` calls this just before contacting the provider. It is the Chat Completions counterpart to `responses_request`, focused on shaping the outgoing request rather than reading the stream back.

*Call graph*: calls 1 internal fn (openai_messages); called by 1 (_complete_chat).


##### `OpenAIClient._complete_chat`  (lines 342–444)

```
async def _complete_chat(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends a streaming Chat Completions request and turns the incoming chunks into UFO model events. It also applies careful retry rules for temporary provider failures before any output has been delivered.

**Data flow**: It starts with a `ModelRequest`, builds chat request arguments with `_chat_kwargs`, and opens a provider stream. As chunks arrive, text becomes `TextDelta`, tool-call starts become `ToolCallStart`, tool-call argument fragments become `ToolCallDelta`, and token counts become a final `Usage` event. If the provider times out or returns a retryable status before anything has been yielded, it waits and retries. If the response is truncated, missing usage, or fails after output has begun, it raises an error instead of hiding the problem.

**Call relations**: `OpenAIClient.complete` routes chat-surface models here. During the stream, this method is the live translator from OpenAI chunks to UFO events, and it hands retry and timeout information to the logging system when needed.

*Call graph*: calls 1 internal fn (_chat_kwargs); called by 1 (complete); 7 external calls (__init__, __init__, __init__, __init__, __init__, sleep, log).


##### `OpenAIClient._complete_responses`  (lines 446–580)

```
async def _complete_responses(self, request: ModelRequest) -> AsyncIterator[ModelEvent]
```

**Purpose**: Sends a streaming Responses API request and turns the incoming events into UFO model events. It supports the newer Responses features, especially replayable reasoning items, while keeping the same retry and error contract as the chat path.

**Data flow**: It receives a `ModelRequest`, asks the model spec for the valid reasoning effort, builds a Responses request with `responses_request`, and opens a provider stream. Text deltas become `TextDelta`, function-call events become `ToolCallStart` and `ToolCallDelta`, completed reasoning items are collected, and the final provider usage becomes a `Usage` event. Refusals, truncation, incomplete responses, failed responses, and stream errors are converted into explicit UFO errors. Reasoning blocks are yielded only after the stream successfully finishes, just before final usage.

**Call relations**: `OpenAIClient.complete` routes Responses-surface models here. This method relies on `responses_request` for the outgoing request shape, then acts as the incoming event interpreter for the Responses stream, including preserving reasoning so later tool-result rounds can continue the model’s chain of work correctly.

*Call graph*: calls 1 internal fn (responses_request); called by 1 (complete); 10 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, sleep, model_copy, log).

## 📊 State Registers Touched

- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-inbound-message-state` — The durable inbox of incoming messages and surface events waiting to be admitted into a conversation turn.
- `reg-turn-run-state` — The durable job ticket for each agent turn, including admission source, queue status, claim owner, parent turn, and final result.
- `reg-cancellation-state` — The shared stop signal and cancellation record used to safely halt turns, child turns, jobs, and cleanup work.
- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-model-provider-catalog` — The shared list of AI models and providers, including how to call them, what keys they need, and what features they support.
- `reg-usage-accounting-ledger` — The spending ledger that records model usage, egress usage, prices, caps, billing exports, and payment-related state.
- `reg-subagent-workflow-state` — The shared parent-child workflow state used when an agent delegates work to helper agents and waits for or cancels them.
- `reg-live-update-hub` — The short-lived stream of progress updates, tool activity, costs, final answers, and Redis fan-out frames for live viewers.
- `reg-prompt-governance-state` — The prompt templates, rendered fingerprints, proposals, evaluations, approvals, and replacement decisions used to change agent behavior safely.
- `reg-observability-trace-state` — The shared logging, metrics, trace IDs, trace parents, and redaction context used to follow work across processes without leaking secrets.
- `reg-external-client-pools` — The live reusable HTTP/provider client sessions and connection pools for model and connector calls, including lifecycle cleanup handles.
- `reg-user-question-state` — The pending human-question/answer state created when an agent asks the user for information and later resumed when the surface delivers a reply.
- `reg-turn-execution-budget-state` — The per-turn live execution limits and counters for context size, tokens, reasoning, tool iterations, cost checks, and stop conditions that gate the model loop before final ledger recording.
- `reg-self-improvement-feedback-state` — Collected failed-task examples, replay inputs/results, judgments, and candidate feedback buffers used by self-improvement background loops before governance approval.
