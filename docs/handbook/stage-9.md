# Model Harness and Agent Execution Loop  `stage-9`

This stage is the main work loop for one agent turn. It is the part that takes a request, asks the AI model what to do, streams the reply as it arrives, runs any tools the model asks for, and repeats until there is a real final answer.

The agent loop is the basic machine: ask the model, show safe visible text, collect tool requests, run those tools, then ask again with the new results. The engine wraps that loop in stronger guardrails. It makes sure a turn is not run twice after a crash, does not lose queued messages, does not spend past limits, and does not answer before all needed input is included.

Model Round Streaming is the adapter layer. It talks to different model services and turns their varied streaming formats into one standard event flow. Transcript Compaction and Long Conversation Control keeps long chats within the model’s reading limit by summarizing older history while preserving recent detail. The package marker simply lets the harness code be imported by the rest of the system.

## Sub-stages

- [Model Round Streaming](stage-9.1.md) `stage-9.1` — 5 files
- [Transcript Compaction and Long Conversation Control](stage-9.2.md) `stage-9.2` — 2 files

## Files in this stage

### Turn Execution Harness
Crash-safe turn orchestration delegates to the reusable agent loop and package boundary that support model streaming, tool execution, and final-answer detection.

### `core/src/ufo/runtime/engine.py`

`orchestration` · `request handling`

Think of this file as the turn conductor for the system. A turn starts as a row in the database, with a prompt from a person, a schedule, a subagent, or a prepared tool intent. The engine first claims that row so only one worker owns it. It then loads the previous transcript, adds context like time and sender, runs prompt hooks, and starts a loop: check spending and seat access, absorb any newly queued messages, compact old conversation history if it is too large, ask the model for the next response, and run any tools the model requested.

Many actions are wrapped as DBOS steps. DBOS is a workflow system that records step outputs, so after a crash it can replay completed steps from the log instead of calling the model again or running the same tool twice. That matters because model calls cost money and tools may change the outside world.

The file also publishes live updates to clients, records costs, stores transcripts, handles images and large tool results safely, enforces permissions, parks turns when billing caps are reached, and commits a terminal frame when the turn is done, failed, cancelled, or waiting on the user.

#### Function details

##### `_claim_turn`  (lines 289–336)

```
async def _claim_turn(turn_id: UUID, attempt: str) -> str | None
```

**Purpose**: Claims a queued, parked, or replayed running turn for one workflow attempt. This is the database lock that prevents two workers from running the same turn at the same time.

**Data flow**: It receives a turn id and attempt id, reads the current turn state, updates the row to running if the attempt is allowed, and returns whether this was a fresh claim, an adopted replay claim, or no claim.

**Call relations**: TurnEngine._mark_running calls this at the start of normal and intent turns. If it succeeds, the engine proceeds; if it fails, the engine switches to repair or no-op behavior.

*Call graph*: called by 1 (_mark_running); 5 external calls (and_, or_, select, update, workspace_tx).


##### `_activity_goal`  (lines 423–424)

```
def _activity_goal(requesters: Mapping[UUID, ActiveMessage]) -> str
```

**Purpose**: Builds a short plain-text goal from the active member messages. It gives activity summaries enough human context to describe what a tool is doing.

**Data flow**: It reads the rendered active messages, extracts their visible member text, joins them with newlines, and returns one combined goal string.

**Call relations**: Runtime tool preparation and intent execution call it just before starting activity summarization for a tool call.

*Call graph*: called by 2 (run_intent, prepare); 1 external calls (member_message_text).


##### `_RoundInput.__repr__`  (lines 438–443)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug label for a model round input. It avoids printing the full conversation while still showing useful sizes and flags.

**Data flow**: It reads the number of messages, system prompt length, and round options, then returns a short string.

**Call relations**: It is used implicitly by logging or debugging tools when a _RoundInput is displayed.


##### `EffectiveCall.parallel_safe`  (lines 464–468)

```
def parallel_safe(self) -> bool
```

**Purpose**: Says whether this resolved tool call may run beside other calls. This lets the engine keep unsafe calls in order while grouping safe ones.

**Data flow**: It reads the resolved tool declaration and returns that tool’s parallel-safe flag.

**Call relations**: Runtime tool scheduling asks this through _RuntimeTools.parallel_safe before deciding how calls can be dispatched.


##### `EffectiveCall.semantic_call`  (lines 470–481)

```
def semantic_call(self) -> ToolUseBlock
```

**Purpose**: Returns the call under its real meaning, not just the wire name the model used. This is important for object actions, where one transport tool can represent many actual actions.

**Data flow**: It reads the original call and resolved action details; if the identity changed, it rewrites the call name and input to the canonical action form.

**Call relations**: Activity summaries and final-act parsing use this so they reason about the real action that ran.


##### `EffectiveCall.meter_dimensions`  (lines 483–493)

```
def meter_dimensions(self) -> dict[str, str]
```

**Purpose**: Builds stable metric labels for this call. It keeps dashboards grouped by real action instead of by every raw name the model might invent.

**Data flow**: It reads the resolved tool, binding, contributor, and call id, then returns a small dictionary of label strings.

**Call relations**: Dispatch binding and dispatch execution pass these labels into _meter_dispatch.


##### `EffectiveCall.__repr__`  (lines 495–496)

```
def __repr__(self) -> str
```

**Purpose**: Creates a short debug representation of a resolved call. It identifies both the semantic call and the provider call id.

**Data flow**: It reads the call id fields and returns a compact string.

**Call relations**: It is used implicitly when EffectiveCall objects appear in logs or debugging output.


##### `_BoundToolCall.call`  (lines 506–507)

```
def call(self) -> ToolUseBlock
```

**Purpose**: Provides quick access to the original tool-use block inside a bound call. It keeps later dispatch code simple.

**Data flow**: It reads the EffectiveCall inside the bound call and returns its ToolUseBlock.

**Call relations**: Dispatch code uses this property when it needs the model’s call id, name, or input.


##### `_BoundToolCall.__repr__`  (lines 509–510)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug label for a bound tool call. It helps logs show which tool and call id are involved.

**Data flow**: It reads the bound call’s name and id and returns a short string.

**Call relations**: It is used implicitly by debugging or logging when bound calls are displayed.


##### `_RejectedToolCall.parallel_safe`  (lines 522–525)

```
def parallel_safe(self) -> bool
```

**Purpose**: Marks rejected or malformed calls as not safe to reorder. Even an invalid call must occupy its original place in the model’s sequence.

**Data flow**: It ignores external input and always returns false.

**Call relations**: The tool scheduler treats rejected calls as barriers when it decides dispatch grouping.


##### `_RejectedToolCall.__repr__`  (lines 527–531)

```
def __repr__(self) -> str
```

**Purpose**: Creates a readable debug label for a rejected tool call. It includes the tool name, call id, outcome, and error class.

**Data flow**: It reads fields from the rejection object and returns a compact string.

**Call relations**: It is used implicitly in logs or debugging output for invalid calls.


##### `ModelStreamError.__init__`  (lines 634–635)

```
def __init__(self, error_class: str, message: str, partial_output: str='') -> None
```

**Purpose**: Stores a model-stream failure with its original class, message, and any partial output. This preserves paid-for stream data even when the round failed.

**Data flow**: It receives the provider error class, message, and partial text, and stores them as exception arguments.

**Call relations**: TurnEngine._stream_recovering_overflow raises this after a recorded stream step reports an error.

*Call graph*: called by 1 (_stream_recovering_overflow).


##### `ModelStreamError.__str__`  (lines 637–639)

```
def __str__(self) -> str
```

**Purpose**: Formats the model error as a readable string. It includes the provider’s error class so overflow detection and logs can still understand it.

**Data flow**: It reads the stored class and message and returns a single string.

**Call relations**: Python uses this when the exception is logged, displayed, or stored in terminal error text.


##### `ModelStreamError.model_error_class`  (lines 642–644)

```
def model_error_class(self) -> str
```

**Purpose**: Returns the original model provider error class. This lets later code distinguish truncation, overflow, and other failures.

**Data flow**: It reads the first stored exception argument and returns it.

**Call relations**: Runtime model streaming and terminal commit logic inspect it when deciding recovery and error reporting.


##### `ModelStreamError.partial_output`  (lines 647–649)

```
def partial_output(self) -> str
```

**Purpose**: Returns any text the model streamed before failing. The engine can save that text so the next model round can salvage it.

**Data flow**: It reads the stored partial-output argument and returns it.

**Call relations**: RuntimeModel.stream uses it when recovering from response truncation.


##### `ModelStreamError.model_error_message`  (lines 652–654)

```
def model_error_message(self) -> str
```

**Purpose**: Returns the original provider error message. This keeps the terminal frame informative without losing the provider’s wording.

**Data flow**: It reads the stored message argument and returns it.

**Call relations**: TurnEngine._commit_once uses it when building a failed terminal frame.


##### `TurnParked.__init__`  (lines 661–663)

```
def __init__(self, message: str) -> None
```

**Purpose**: Creates the exception used when a turn must pause because spending or seat checks no longer allow it to continue. Parking is resumable, unlike failure.

**Data flow**: It receives a human-readable reason, stores it on the exception, and exposes it as message.

**Call relations**: Spend and seat enforcement raise it; TurnEngine.run and run_intent catch it and write the parked state.

*Call graph*: called by 2 (_enforce_seats, _enforce_spend).


##### `_intent_admits`  (lines 669–674)

```
def _intent_admits(tool: ToolDef) -> bool
```

**Purpose**: Checks whether a prepared member intent is allowed to reach a tool. This keeps panel-submitted actions limited to object mutations or actions that explicitly present themselves to members.

**Data flow**: It reads a tool definition and returns true only for allowed object CRUD tools or tools with a presentation.

**Call relations**: TurnEngine.run_intent calls it before dispatching a member-submitted intent.

*Call graph*: called by 1 (run_intent).


##### `_context_tag`  (lines 683–701)

```
def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str
```

**Purpose**: Builds the metadata block placed before a member message. It tells the model when the message was admitted, who sent it, and what surface context came with it.

**Data flow**: It receives a message id, optional context, and timestamp, formats those fields in the sender’s timezone when available, and returns a <context> text block.

**Call relations**: TranscriptRepair.load_messages uses it for the founding message, and TurnEngine._render_arrival uses it for queued arrivals.

*Call graph*: called by 2 (load_messages, _render_arrival); 2 external calls (astimezone, ZoneInfo).


##### `_bounded`  (lines 704–709)

```
def _bounded(content: str) -> str
```

**Purpose**: Caps long tool text so it cannot flood the model context. It adds a clear notice saying how much was cut.

**Data flow**: It receives text, returns it unchanged if short enough, or returns the front portion plus a truncation marker.

**Call relations**: TurnEngine._finish_dispatch uses it for error results and fallback handling when offloading fails.

*Call graph*: called by 1 (_finish_dispatch).


##### `_meter_dispatch`  (lines 712–746)

```
def _meter_dispatch(tools: ToolRegistry, call: ToolUseBlock, started: float, outcome: str, error_class: str | None, profile: str, semantic: Mapping[str, str] | None=None) -> None
```

**Purpose**: Records metrics for one tool dispatch: how often it ran, how long it took, and how it ended. This makes operational dashboards honest about both successes and failures.

**Data flow**: It receives the registry, call, start time, outcome, error class, profile, and semantic labels; it normalizes labels and emits a count and duration histogram.

**Call relations**: Binding failures and dispatch steps call it so every attempted call is counted in the same way.

*Call graph*: called by 2 (_bind_or_error, _dispatch_step); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `_loaded_skill_closures`  (lines 749–792)

```
def _loaded_skill_closures(messages: tuple[Message, ...], skills: SkillRegistry) -> Iterator[tuple[LoadedRef, ...]]
```

**Purpose**: Finds which skill cards are already present in the current conversation window. This prevents the engine from loading the same skill context again unnecessarily.

**Data flow**: It scans messages for completed load-skill calls, validates that their results were not cut off, asks the skill registry for each closure, and yields those closures.

**Call relations**: TurnEngine._reseed_loaded_skills calls it after compaction or message changes.

*Call graph*: calls 1 internal fn (closure); called by 1 (_reseed_loaded_skills).


##### `_final_act`  (lines 795–817)

```
def _final_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts a structured final action only when a specific successful tool call was the last call in the round. This is used for actions like asking the user a question, where later work can make the ask stale.

**Data flow**: It receives tool calls, their results, a tool name, and a payload model; it parses JSON from the matching result and returns a validated payload or none.

**Call relations**: _round_acts calls it for final-act types whose rule is “last call only.”

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_pending_act`  (lines 820–848)

```
def _pending_act(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...], tool_name: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Extracts the most recent successful pending action anywhere in a round. This is for things the user still owes, such as credentials or connection approval.

**Data flow**: It receives calls, results, a tool name, and a payload model; it searches backward, parses the handler result JSON, and returns a validated payload or none.

**Call relations**: _round_acts calls it for final-act types whose rule is “pending until member answers.”

*Call graph*: called by 1 (_round_acts); 1 external calls (loads).


##### `_round_acts`  (lines 851–882)

```
def _round_acts(resolved: tuple[_Resolution, ...], results: tuple[ToolResultBlock, ...]) -> dict[str, BaseModel]
```

**Purpose**: Collects structured open actions left by one tool round. It turns raw tool results into typed terminal-frame fields such as question, credential request, or connect request.

**Data flow**: It receives resolved calls and result blocks, converts calls to semantic identities, applies pending or final parsing rules, and returns a field-name-to-payload map.

**Call relations**: RuntimeTools.after_round uses it during model turns, and run_intent uses it after a single intent dispatch.

*Call graph*: calls 2 internal fn (_final_act, _pending_act); called by 2 (run_intent, after_round).


##### `_act`  (lines 885–889)

```
def _act(acts: dict[str, BaseModel], frame_field: str, model: type[PayloadT]) -> PayloadT | None
```

**Purpose**: Safely reads one typed action from the action map. It avoids returning a payload under the wrong type.

**Data flow**: It receives the acts dictionary, desired field, and expected model class; it returns the payload only if it is an instance of that class.

**Call relations**: RuntimeTools.after_round and run_intent use it to fill terminal fields.

*Call graph*: called by 2 (run_intent, after_round).


##### `_created_refs`  (lines 892–924)

```
def _created_refs(tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> tuple[ObjectRef, ...]
```

**Purpose**: Finds object references created by successful object_apply calls. This lets terminals and resumed turns know which new workspace objects already exist.

**Data flow**: It receives tool calls and results, reads JSON from object_apply results, validates created object references, and returns a tuple of ObjectRef values.

**Call relations**: TurnEngine._fold_created accumulates these during rounds, and run_intent includes them in an intent terminal.

*Call graph*: called by 2 (_fold_created, run_intent); 2 external calls (__init__, loads).


##### `_total_usage`  (lines 927–935)

```
def _total_usage(usage_events: Sequence[Usage]) -> Usage
```

**Purpose**: Adds many usage records into one total. Usage means token counts from model calls and related completions.

**Data flow**: It receives a sequence of Usage events, sums each token category, and returns a new Usage object with totals.

**Call relations**: Billing, spend enforcement, parking, cost publishing, committing, cancellation billing, and model metrics all use this shared total.

*Call graph*: called by 6 (_bill_cancelled, _commit_once, _enforce_spend, _park, _publish_cost, _stream_once); 1 external calls (__init__).


##### `_to_harness_reasoning`  (lines 938–939)

```
def _to_harness_reasoning(block: ReasoningBlock) -> HarnessReasoning
```

**Purpose**: Converts a runtime reasoning block into the harness format. The harness is the lower-level agent runner used by this engine.

**Data flow**: It receives a runtime reasoning block, records its kind and serialized payload, and returns a HarnessReasoning object.

**Call relations**: Message conversion and RuntimeModel.stream use it when handing model output back to the harness.

*Call graph*: called by 2 (stream, _to_harness_message); 2 external calls (__init__, model_dump).


##### `_from_harness_reasoning`  (lines 942–951)

```
def _from_harness_reasoning(block: HarnessReasoning) -> ReasoningBlock
```

**Purpose**: Converts a harness reasoning block back into the runtime model format. This keeps provider reasoning blocks intact across the adapter boundary.

**Data flow**: It reads the block kind, validates the stored payload as the matching runtime reasoning type, and returns it.

**Call relations**: _from_harness_message calls it while translating harness messages into runtime messages.

*Call graph*: called by 1 (_from_harness_message); 3 external calls (model_validate, model_validate, model_validate).


##### `_to_harness_call`  (lines 954–955)

```
def _to_harness_call(call: ToolUseBlock) -> HarnessToolCall
```

**Purpose**: Converts a runtime tool-use block into the harness call format. It is a small adapter between two nearby representations.

**Data flow**: It reads the call id, name, and input dictionary, then returns a HarnessToolCall.

**Call relations**: RuntimeModel.stream and _to_harness_message use it when passing calls to the harness.

*Call graph*: called by 2 (stream, _to_harness_message); 1 external calls (__init__).


##### `_from_harness_call`  (lines 958–959)

```
def _from_harness_call(call: HarnessToolCall) -> ToolUseBlock
```

**Purpose**: Converts a harness tool call into the runtime tool-use block. This lets runtime-specific dispatch code understand calls produced by the harness.

**Data flow**: It reads the harness call id, name, and input, then returns a ToolUseBlock.

**Call relations**: RuntimeTools resolution, after-round processing, and message conversion call it.

*Call graph*: called by 3 (_resolve, after_round, _from_harness_message); 1 external calls (__init__).


##### `_to_harness_result`  (lines 962–979)

```
def _to_harness_result(result: ToolResultBlock) -> HarnessToolResult
```

**Purpose**: Converts a runtime tool result into the harness result format, including text and images. This lets the harness feed tool answers back into its round loop.

**Data flow**: It receives a ToolResultBlock, converts string or block content into harness text/image parts, and returns a HarnessToolResult.

**Call relations**: RuntimeTools.execute and message conversion use it after dispatching tools.

*Call graph*: called by 2 (execute, _to_harness_message); 3 external calls (__init__, __init__, __init__).


##### `_from_harness_result`  (lines 982–999)

```
def _from_harness_result(result: HarnessToolResult) -> ToolResultBlock
```

**Purpose**: Converts a harness tool result back into the runtime format. It preserves text, image data, error flags, and activity markers.

**Data flow**: It receives a HarnessToolResult, converts content parts to runtime blocks, and returns a ToolResultBlock.

**Call relations**: RuntimeTools.after_round and message conversion use it when reading harness-produced message history.

*Call graph*: called by 2 (after_round, _from_harness_message); 4 external calls (__init__, __init__, __init__, __init__).


##### `_to_harness_message`  (lines 1002–1020)

```
def _to_harness_message(message: Message) -> HarnessMessage
```

**Purpose**: Converts one runtime message into one harness message. It translates every supported content block across the boundary.

**Data flow**: It receives a runtime Message, copies plain string content directly or converts each structured block, and returns a HarnessMessage.

**Call relations**: _to_harness_messages calls it for whole conversation batches.

*Call graph*: calls 3 internal fn (_to_harness_call, _to_harness_reasoning, _to_harness_result); called by 1 (_to_harness_messages); 3 external calls (__init__, __init__, __init__).


##### `_from_harness_message`  (lines 1023–1039)

```
def _from_harness_message(message: HarnessMessage) -> Message
```

**Purpose**: Converts one harness message back into the runtime message format. It restores text, images, tool calls, tool results, and reasoning blocks.

**Data flow**: It receives a HarnessMessage, copies plain strings or converts each structured block, and returns a runtime Message.

**Call relations**: _from_harness_messages calls it whenever the engine receives conversation state from the harness.

*Call graph*: calls 3 internal fn (_from_harness_call, _from_harness_reasoning, _from_harness_result); called by 1 (_from_harness_messages); 4 external calls (__init__, __init__, __init__, __init__).


##### `_to_harness_messages`  (lines 1042–1043)

```
def _to_harness_messages(messages: tuple[Message, ...]) -> tuple[HarnessMessage, ...]
```

**Purpose**: Converts a whole tuple of runtime messages into harness messages. It is the batch version of the message adapter.

**Data flow**: It receives runtime messages, maps each through _to_harness_message, and returns a tuple.

**Call relations**: TurnEngine._model_round and runtime conversation/model adapters use it when entering or returning from the harness.

*Call graph*: calls 1 internal fn (_to_harness_message); called by 4 (_model_round, prepare, prepare_exhaust, stream).


##### `_from_harness_messages`  (lines 1046–1047)

```
def _from_harness_messages(messages: tuple[HarnessMessage, ...]) -> tuple[Message, ...]
```

**Purpose**: Converts a whole tuple of harness messages back into runtime messages. It is the batch version of the reverse adapter.

**Data flow**: It receives harness messages, maps each through _from_harness_message, and returns a tuple.

**Call relations**: TurnEngine._model_round and runtime adapters use it when reading harness state.

*Call graph*: calls 1 internal fn (_from_harness_message); called by 5 (_model_round, checkpoint, prepare, prepare_exhaust, stream).


##### `_RuntimeConversation.prepare`  (lines 1066–1087)

```
async def prepare(self, messages: tuple[HarnessMessage, ...], round_index: int) -> HarnessPreparedRound
```

**Purpose**: Prepares the conversation before each model round. It absorbs new arrivals, checks spend, refreshes loaded skills, compacts context if needed, and tells the harness whether a final act was interrupted.

**Data flow**: It receives harness messages and a round index, converts them, adds arrivals, enforces limits, compacts, records usage, increments the round meter, and returns prepared harness messages.

**Call relations**: AgentEngine calls this before a round; it delegates arrival, billing, skill, and compaction work back to TurnEngine.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages); 2 external calls (__init__, span).


##### `_RuntimeConversation.checkpoint`  (lines 1089–1091)

```
async def checkpoint(self, messages: tuple[HarnessMessage, ...]) -> None
```

**Purpose**: Stores a consistent conversation snapshot after a round has completed. This is what later interruption handling can safely persist.

**Data flow**: It receives harness messages, converts them to runtime messages, stores them on the engine’s window, and marks that this turn has run at least one full round.

**Call relations**: AgentEngine calls it at safe checkpoints in the round loop.

*Call graph*: calls 1 internal fn (_from_harness_messages).


##### `_RuntimeConversation.prepare_exhaust`  (lines 1093–1102)

```
async def prepare_exhaust(self, messages: tuple[HarnessMessage, ...]) -> tuple[HarnessMessage, ...]
```

**Purpose**: Prepares messages for the forced final answer when the round budget is exhausted. It still checks spend and compacts before the final attempt.

**Data flow**: It receives harness messages, enforces spend, compacts converted runtime messages, records compaction usage, and returns harness messages.

**Call relations**: AgentEngine calls it when it needs to force the model to stop using tools.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages).


##### `_RuntimeModel.stream`  (lines 1111–1165)

```
async def stream(self, request: HarnessModelRequest, round_index: int) -> HarnessModelRound
```

**Purpose**: Adapts a harness model request into this runtime’s model-streaming flow. It also turns truncation failures into recoverable feedback when possible.

**Data flow**: It receives a harness request, builds runtime tool schemas and messages, streams through TurnEngine, updates cost and seat checks, and returns a harness model round.

**Call relations**: AgentEngine calls it for each model round; it calls TurnEngine._stream_recovering_overflow and publishes cost afterward.

*Call graph*: calls 5 internal fn (__init__, _from_harness_messages, _to_harness_call, _to_harness_messages, _to_harness_reasoning); 4 external calls (__init__, __init__, emit_metric, log).


##### `_RuntimeTools.definitions`  (lines 1178–1188)

```
def definitions(self) -> tuple[HarnessToolDefinition, ...]
```

**Purpose**: Supplies the harness with the tools available this round. It includes the requested_by field only when member references are meaningful.

**Data flow**: It reads the engine tool registry and active requesters, builds harness tool definitions from schemas, and returns them.

**Call relations**: AgentEngine asks this when preparing model tool offers.

*Call graph*: 1 external calls (__init__).


##### `_RuntimeTools.parallel_safe`  (lines 1190–1191)

```
def parallel_safe(self, call: HarnessToolCall) -> bool
```

**Purpose**: Tells the harness whether a tool call can run in parallel with neighbors. It bases the answer on the resolved tool, not just the raw name.

**Data flow**: It receives a harness call, resolves it if needed, and returns its parallel-safe property.

**Call relations**: AgentEngine calls it while segmenting tool calls for dispatch.

*Call graph*: calls 1 internal fn (_resolve).


##### `_RuntimeTools.prepare`  (lines 1193–1209)

```
async def prepare(self, calls: tuple[HarnessToolCall, ...]) -> None
```

**Purpose**: Resolves and binds a batch of tool calls before execution. This catches invalid calls early and starts live activity summaries for valid calls.

**Data flow**: It receives harness calls, resolves each one, binds requester authority and context, stores bound items by call id, and starts activity tasks for bound calls.

**Call relations**: AgentEngine calls it before executing a dispatch segment.

*Call graph*: calls 2 internal fn (_resolve, _activity_goal); 1 external calls (gather).


##### `_RuntimeTools.execute`  (lines 1211–1213)

```
async def execute(self, call: HarnessToolCall) -> HarnessToolResult
```

**Purpose**: Executes one prepared tool call and returns the answer to the harness. It assumes prepare has already bound the call.

**Data flow**: It looks up the bound call by id, dispatches it through TurnEngine, converts the result to harness format, and returns it.

**Call relations**: AgentEngine calls it for individual tool calls after RuntimeTools.prepare.

*Call graph*: calls 1 internal fn (_to_harness_result).


##### `_RuntimeTools.after_round`  (lines 1215–1241)

```
async def after_round(self, calls: tuple[HarnessToolCall, ...], results: tuple[HarnessToolResult, ...]) -> None
```

**Purpose**: Updates turn state after a tool round finishes. It records changed workspace targets, created objects, and any open user-facing acts.

**Data flow**: It receives harness calls and results, converts them, records change paths and creations, parses final or pending acts, stores those acts, and clears per-round caches.

**Call relations**: AgentEngine calls it after tool execution; it uses TurnEngine helpers to fold created objects and parse acts.

*Call graph*: calls 5 internal fn (_resolve, _act, _from_harness_call, _from_harness_result, _round_acts); 2 external calls (__init__, change_targets).


##### `_RuntimeTools.interrupted`  (lines 1243–1244)

```
def interrupted(self) -> None
```

**Purpose**: Clears a pending question when a round is interrupted. A question should not be treated as open if the turn did not finish cleanly.

**Data flow**: It reads current tool state and replaces only the question field with none.

**Call relations**: AgentEngine calls it when its tool loop is interrupted.

*Call graph*: 1 external calls (replace).


##### `_RuntimeTools._resolve`  (lines 1246–1252)

```
def _resolve(self, call: HarnessToolCall) -> _Resolution
```

**Purpose**: Resolves a harness call once and caches the result for the round. This keeps scheduling, binding, and after-round parsing consistent.

**Data flow**: It receives a harness call, returns the cached resolution if present, otherwise converts and asks TurnEngine to resolve it, then stores the result.

**Call relations**: RuntimeTools.parallel_safe, prepare, and after_round all call it.

*Call graph*: calls 1 internal fn (_from_harness_call); called by 3 (after_round, parallel_safe, prepare).


##### `_RuntimeEvents.speak`  (lines 1260–1261)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: Forwards mid-turn marked replies from the harness to the runtime. These are pieces of assistant text meant to be delivered before the final answer.

**Data flow**: It receives marked replies and a round number, then asks TurnEngine to publish and store them.

**Call relations**: AgentEngine calls it when the model emits speak-marked spans.


##### `_RuntimeEvents.closing`  (lines 1263–1264)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Forwards marked spans from the closing answer back to the live text stream. This prevents live terminal users from missing text that was hidden during redaction.

**Data flow**: It receives marked replies and asks TurnEngine to stream their text as closing deltas.

**Call relations**: AgentEngine calls it during final answer handling.


##### `_RuntimeEvents.exhausted`  (lines 1266–1273)

```
def exhausted(self) -> None
```

**Purpose**: Records that the model hit the maximum tool-round budget. It marks the turn incomplete reason and emits observability signals.

**Data flow**: It updates the meter’s incomplete reason, emits a metric, and logs the forced-final event.

**Call relations**: AgentEngine calls it when it must force a final answer because too many rounds have run.

*Call graph*: 2 external calls (emit_metric, log).


##### `TranscriptRepair.resolve`  (lines 1288–1312)

```
async def resolve(self) -> TerminalFrame | None
```

**Purpose**: Republishes a terminal frame for a turn that is already finished. This helps a redelivered client stop waiting even if the original worker crashed after committing.

**Data flow**: It reads the turn terminal from the database; if present, validates it, persists inbound transcript safety data, publishes the terminal, and returns the frame.

**Call relations**: TurnEngine._resolve_unclaimed uses it when this execution loses the running claim.

*Call graph*: calls 1 internal fn (persist_inbound); 5 external calls (__init__, model_validate, select, workspace_tx, log).


##### `TranscriptRepair.persist_transcript`  (lines 1314–1322)

```
async def persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Writes the finished conversation transcript, including the assistant’s final answer. This is the durable history future turns will read.

**Data flow**: It receives prior messages, answer, system prompt, and injected context, appends an assistant message, and writes the conversation.

**Call relations**: TurnEngine calls it after a successful terminal or intent result.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_interrupted`  (lines 1324–1340)

```
async def persist_interrupted(self, messages: tuple[Message, ...], ran: bool) -> None
```

**Purpose**: Writes the safe conversation state for a turn that ended without a normal answer. If the turn did real work, it adds a notice so the next turn does not repeat it blindly.

**Data flow**: It receives a message window and ran flag, optionally appends an interruption notice, and writes the conversation.

**Call relations**: TurnEngine uses it on failures, cancellation, or non-done terminal outcomes.

*Call graph*: calls 1 internal fn (write_conversation); 1 external calls (__init__).


##### `TranscriptRepair.persist_inbound`  (lines 1342–1361)

```
async def persist_inbound(self, arrivals: tuple[Message, ...]=(), founding_denial: str | None=None) -> None
```

**Purpose**: Preserves inbound member messages when a turn ends before producing useful assistant output. This prevents user input from disappearing.

**Data flow**: It loads the founding messages or builds a safe denied-message version, appends any absorbed arrivals, and writes the conversation.

**Call relations**: TranscriptRepair.resolve and TurnEngine interruption persistence use it when only inbound text should be durable.

*Call graph*: calls 3 internal fn (_prior_messages, load_messages, write_conversation); called by 1 (resolve); 1 external calls (__init__).


##### `TranscriptRepair.load_messages`  (lines 1363–1372)

```
async def load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads prior transcript plus this turn’s founding inbound message. For member turns it adds the context tag so past messages keep their original time and sender metadata.

**Data flow**: It reads prior messages, formats the inbound with context when needed, appends it as a user message, and returns the tuple.

**Call relations**: TurnEngine._load_messages and TranscriptRepair.persist_inbound rely on it.

*Call graph*: calls 2 internal fn (_prior_messages, _context_tag); called by 1 (persist_inbound); 1 external calls (__init__).


##### `TranscriptRepair._prior_messages`  (lines 1374–1380)

```
async def _prior_messages(self) -> tuple[Message, ...]
```

**Purpose**: Reads the conversation history before this turn. It deliberately ignores transcript writes at or after this turn’s sequence to avoid a replay reading its own output.

**Data flow**: It reads stored transcript data, checks the sequence number, and returns either stored messages or an empty tuple.

**Call relations**: load_messages and persist_inbound call it when rebuilding safe transcript content.

*Call graph*: called by 2 (load_messages, persist_inbound).


##### `TranscriptRepair.write_conversation`  (lines 1382–1408)

```
async def write_conversation(self, messages: tuple[Message, ...], system: str | None=None, injected: str | None=None, from_run: bool=False) -> None
```

**Purpose**: Writes a Conversation record to the transcript with retry. The retry keeps transient storage problems from immediately losing turn history.

**Data flow**: It receives messages and optional system/injected metadata, builds a Conversation, tries to write it several times, logs failures, and sleeps between retries.

**Call relations**: All TranscriptRepair persistence methods call this as their final write step.

*Call graph*: called by 3 (persist_inbound, persist_interrupted, persist_transcript); 3 external calls (__init__, sleep, log).


##### `_TurnMeter.exited`  (lines 1442–1459)

```
def exited(self, status: str) -> None
```

**Purpose**: Records wall-clock and round-count metrics when this execution reaches an exit. It only records once per execution.

**Data flow**: It receives an exit status, checks whether it already ended, measures elapsed time, emits duration and round metrics, and marks itself ended.

**Call relations**: TurnEngine._commit calls it for terminal exits, and exception paths call it directly for parked, cancelled, or preempted exits.

*Call graph*: called by 1 (_commit); 3 external calls (monotonic, emit_histogram, emit_metric).


##### `TurnEngine.__post_init__`  (lines 1553–1564)

```
def __post_init__(self) -> None
```

**Purpose**: Validates that the engine was wired consistently. It catches audience mismatches and prevents subagent output-contract turns from also defining a conflicting finish tool.

**Data flow**: It reads configured tool contexts, hook audience, output model, and tool registry; it raises ValueError if the configuration is inconsistent.

**Call relations**: Dataclass construction calls it automatically after TurnEngine is created.


##### `TurnEngine.__repr__`  (lines 1566–1570)

```
def __repr__(self) -> str
```

**Purpose**: Creates a compact debug label for the engine. It identifies the turn, agent, and profile without dumping large runtime state.

**Data flow**: It reads the turn id, agent id, and computed profile and returns a string.

**Call relations**: It is used implicitly by logs or debuggers that display a TurnEngine.


##### `TurnEngine.profile`  (lines 1573–1576)

```
def profile(self) -> str
```

**Purpose**: Returns the telemetry profile for this turn, such as main, agent, or a subagent profile. Metrics use this to separate different kinds of work.

**Data flow**: It reads whether the turn is spawned and its subagent profile, then asks the shared profile helper for the label.

**Call relations**: Most metrics and logs in TurnEngine include this property.

*Call graph*: 1 external calls (turn_profile).


##### `TurnEngine.run`  (lines 1578–1722)

```
async def run(self) -> TerminalFrame | None
```

**Purpose**: Runs a normal model-driven turn from claim to terminal. It is the main workflow body for user, scheduled, and subagent turns.

**Data flow**: It claims the turn, prepares messages and context, loops through model rounds and tool dispatches, commits the result, writes transcript and workspace changes, publishes live frames, and handles parking, cancellation, preemption, and failure.

**Call relations**: It orchestrates nearly every helper in this file and is the central path called by the workflow runner outside this file.

*Call graph*: calls 15 internal fn (_bill_cancelled, _commit, _mark_running, _model_round, _park, _persist_interrupted, _persist_transcript, _prepare_run, _publish, _publish_run (+5 more)); 9 external calls (__init__, __init__, __init__, __init__, __init__, partial, monotonic, emit_metric, log).


##### `TurnEngine._rank_find`  (lines 1724–1745)

```
async def _rank_find(self, usage_events: list[Usage], system: str, user: str) -> str
```

**Purpose**: Runs a small model completion used by tool-side search or ranking. It records the token usage back into the current tool dispatch.

**Data flow**: It receives a system prompt and user prompt, streams a model completion, collects text deltas and usage events, and returns the completed text.

**Call relations**: ToolContext exposes it as find during TurnEngine.run, and tool handlers call it indirectly.

*Call graph*: 2 external calls (__init__, __init__).


##### `TurnEngine._prepare_run`  (lines 1747–1803)

```
async def _prepare_run(self, usage_events: list[Usage], meter: _TurnMeter, absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage], pending_guard: bool) -> _PreparedRun
```

**Purpose**: Builds the starting prompt and message list for a normal turn. It applies scheduled-memory context, prompt-submit hooks, denial handling, injected context, and member skill text.

**Data flow**: It receives usage, meter, absorbed-arrival tracking, requester tracking, and a pending-arrival guard; it returns prepared system text, messages, injected text, or an already committed terminal.

**Call relations**: TurnEngine.run calls it after claiming and before entering the model-round loop.

*Call graph*: calls 7 internal fn (_commit, _load_messages, _persist_transcript, _publish_terminal, _record_workspace_changes, _repair, _scheduled_system); called by 1 (run); 6 external calls (__init__, __init__, __init__, __init__, escape, span).


##### `TurnEngine.run_intent`  (lines 1805–1939)

```
async def run_intent(self) -> TerminalFrame | None
```

**Purpose**: Runs a prepared tool intent without asking the model. This is used when a UI panel or sandbox bridge submits one exact tool call.

**Data flow**: It claims the turn, parses the intent, checks requester authority, resolves and binds the tool, dispatches it, commits success or failure, writes transcript, publishes terminal, and handles exits.

**Call relations**: It shares dispatch, billing, terminal, and transcript helpers with TurnEngine.run but skips model rounds.

*Call graph*: calls 19 internal fn (_bind_or_error, _commit, _dispatch_step_recovering, _enforce_seats, _load_messages, _mark_running, _park, _persist_transcript, _publish_terminal, _rejected (+9 more)); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, model_validate_json, model_validate_json, monotonic, emit_metric (+1 more)).


##### `TurnEngine._scheduled_system`  (lines 1941–1975)

```
async def _scheduled_system(self, system: str) -> str
```

**Purpose**: Adds recalled memory to the system prompt for scheduled turns. If memory search fails, it degrades gracefully and keeps the original prompt.

**Data flow**: It receives the base system prompt, searches memory for the scheduled inbound text, formats matches safely, and returns the augmented or original prompt.

**Call relations**: TurnEngine._prepare_run calls it only for scheduled admissions.

*Call graph*: called by 1 (_prepare_run); 5 external calls (__init__, timeout, escape, log, audience_subjects).


##### `TurnEngine._mark_running`  (lines 1977–1985)

```
async def _mark_running(self) -> bool
```

**Purpose**: Claims this turn for the current engine attempt. It is the first ownership check before doing any expensive or side-effecting work.

**Data flow**: It passes the turn id and attempt id to _claim_turn and returns whether a claim was obtained.

**Call relations**: TurnEngine.run and run_intent call it; failure sends them to _resolve_unclaimed.

*Call graph*: calls 1 internal fn (_claim_turn); called by 2 (run, run_intent).


##### `TurnEngine._repair`  (lines 1987–1988)

```
def _repair(self) -> TranscriptRepair
```

**Purpose**: Creates a TranscriptRepair helper bound to this turn. It keeps transcript repair and republishing logic separate from the main engine.

**Data flow**: It reads the engine’s turn, transcript, and hub and returns a TranscriptRepair object.

**Call relations**: Load, persistence, and unclaimed-resolution methods call it whenever transcript repair behavior is needed.

*Call graph*: called by 5 (_load_messages, _persist_interrupted, _persist_transcript, _prepare_run, _resolve_unclaimed); 1 external calls (__init__).


##### `TurnEngine._load_messages`  (lines 1990–1992)

```
async def _load_messages(self) -> tuple[Message, ...]
```

**Purpose**: Loads the transcript messages under an observability span. It is a thin wrapper around TranscriptRepair.load_messages.

**Data flow**: It opens a tracing span, creates the repair helper, loads messages, and returns them.

**Call relations**: _prepare_run and run_intent call it when they need the conversation history.

*Call graph*: calls 1 internal fn (_repair); called by 2 (_prepare_run, run_intent); 1 external calls (span).


##### `TurnEngine._model_round`  (lines 1994–2083)

```
async def _model_round(self, context: ToolContext, messages: tuple[Message, ...], usage_events: list[Usage], system: str, arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, A
```

**Purpose**: Runs the harness agent loop for one stretch of model/tool interaction until it has an answer. It wires this runtime’s model, tools, conversation, events, and optional structured output contract into the harness.

**Data flow**: It receives context, messages, usage, system prompt, tracking lists, and accumulators; it runs AgentEngine and returns final messages, answer text, and any open acts.

**Call relations**: TurnEngine.run calls it inside its main loop.

*Call graph*: calls 2 internal fn (_from_harness_messages, _to_harness_messages); called by 1 (run); 9 external calls (__init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__, __init__).


##### `TurnEngine._model_round.validate`  (lines 2027–2028)

```
def validate(call: HarnessToolCall) -> str
```

**Purpose**: Validates a finish-tool payload against a subagent output contract. It ensures structured subagent answers have the required shape.

**Data flow**: It receives a harness tool call, validates its input with the output model, and returns normalized JSON.

**Call relations**: The StructuredOutput adapter inside _model_round uses it when the model calls finish.


##### `TurnEngine._model_round.accept_prose`  (lines 2030–2039)

```
def accept_prose(text: str) -> str | None
```

**Purpose**: Allows short plain prose to become a structured result when the output contract supports freeform text. This makes simple subagent answers less brittle.

**Data flow**: It receives answer text, checks contract and length limits, validates it as a result field, and returns JSON or none.

**Call relations**: The StructuredOutput adapter inside _model_round uses it as an alternate way to finish.

*Call graph*: 1 external calls (freeform_result_contract).


##### `TurnEngine._fold_created`  (lines 2085–2113)

```
async def _fold_created(self, created: dict[ObjectRef, None], tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]) -> None
```

**Purpose**: Adds newly created object references to the turn row as soon as they happen. This makes creations durable even if the turn later parks, fails, or resumes.

**Data flow**: It receives the existing created accumulator plus round calls and results, finds fresh created refs, updates the accumulator, and writes the full set to the database.

**Call relations**: RuntimeTools.after_round calls it after each tool round.

*Call graph*: calls 1 internal fn (_created_refs); 2 external calls (update, workspace_tx).


##### `TurnEngine._absorb_arrivals`  (lines 2115–2175)

```
async def _absorb_arrivals(self, messages: tuple[Message, ...], arrival_log: list[Message], absorbed_ids: list[UUID], requesters: dict[UUID, ActiveMessage] | None=None) -> tuple[Message, ...]
```

**Purpose**: Adds queued inbound messages to the conversation while the turn is running. This lets the model respond to new member guidance before closing.

**Data flow**: It receives current messages and tracking lists, claims arrivals, appends safe rendered or denied user messages, updates active requesters, logs and publishes absorbed member arrivals, and returns the expanded messages.

**Call relations**: RuntimeConversation.prepare calls it before model rounds.

*Call graph*: calls 2 internal fn (_claim_arrivals, _publish); 5 external calls (__init__, __init__, __init__, escape, log).


##### `TurnEngine._speak`  (lines 2177–2229)

```
async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None
```

**Purpose**: Publishes and stores mid-turn reply spans for member-facing surfaces. It lets the assistant speak before the final answer without duplicating delivery on replay.

**Data flow**: It receives marked replies and a round index, writes idempotent delivery rows, logs new writes, and publishes Reply frames.

**Call relations**: RuntimeEvents.speak forwards harness speak events here.

*Call graph*: calls 1 internal fn (_publish); 4 external calls (__init__, workspace_tx, log, mid_turn_reply_id_for).


##### `TurnEngine._stream_closing_spans`  (lines 2231–2241)

```
async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Streams final marked reply text back to live clients. This fills the gap created when marked spans were hidden from the normal delta stream.

**Data flow**: It receives marked replies and publishes each reply’s text as a TextDelta frame.

**Call relations**: RuntimeEvents.closing calls it during final answer handling.

*Call graph*: calls 1 internal fn (_publish); 1 external calls (__init__).


##### `TurnEngine._render_arrival`  (lines 2243–2267)

```
async def _render_arrival(self, message_id: UUID, body: str, context: TurnContext | None, speaker_member_id: UUID | None, created_at: datetime) -> tuple[str | None, str | None]
```

**Purpose**: Turns one queued arrival into the exact text the model should see. It applies the same prompt-submit hook treatment as the founding message.

**Data flow**: It receives arrival id, body, context, speaker, and timestamp; it fires hooks, returns either rendered context-tagged content with injection or a denial reason.

**Call relations**: TurnEngine._claim_arrivals calls it while draining inbound rows.

*Call graph*: calls 1 internal fn (_context_tag); called by 1 (_claim_arrivals); 1 external calls (__init__).


##### `TurnEngine._claim_arrivals`  (lines 2270–2339)

```
async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]
```

**Purpose**: Claims pending inbound-message rows for this turn and records their rendered form as a DBOS step. This prevents arrivals from being lost or hook-processed twice after crashes.

**Data flow**: It receives already absorbed ids, stamps eligible rows as consumed by this turn, renders each one, logs the batch, and returns Arrival objects.

**Call relations**: TurnEngine._absorb_arrivals calls it whenever a round prepares conversation state.

*Call graph*: calls 1 internal fn (_render_arrival); called by 1 (_absorb_arrivals); 7 external calls (__init__, model_validate, and_, or_, update, workspace_tx, log).


##### `TurnEngine._release_unabsorbed`  (lines 2341–2360)

```
async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None
```

**Purpose**: Returns claimed-but-not-absorbed arrivals to the pending queue on failed or cancelled exits. This is a cleanup safety net for arrivals whose drain did not become durable.

**Data flow**: It receives absorbed ids, clears consumed_turn_id for this turn except those ids, and logs any cleanup failure without raising.

**Call relations**: TurnEngine.run calls it in cancellation and failure paths.

*Call graph*: called by 1 (run); 3 external calls (update, workspace_tx, log).


##### `TurnEngine._stream_recovering_overflow`  (lines 2362–2431)

```
async def _stream_recovering_overflow(self, messages: tuple[Message, ...], usage_events: list[Usage], system: str, tool_schemas: tuple[ToolSchema, ...], tool_choice: str | None, offer_tools: bool=True
```

**Purpose**: Runs one model round and recovers once from a context-overflow error. Context overflow means the prompt is too large for the model.

**Data flow**: It receives messages, usage, system prompt, tool options, and round flags; it streams the round, raises recorded model errors, or force-compacts and retries on overflow, returning the possibly compacted messages and stream result.

**Call relations**: RuntimeModel.stream calls it for every model round.

*Call graph*: calls 3 internal fn (__init__, _reseed_loaded_skills, _stream_retrying_interruption); 4 external calls (__init__, is_context_overflow, emit_metric, log).


##### `TurnEngine._stream_retrying_interruption`  (lines 2433–2463)

```
async def _stream_retrying_interruption(self, round_input: _RoundInput, usage_events: list[Usage]) -> StreamResult
```

**Purpose**: Retries a model round once when the provider stream is interrupted by a transient fault. It still bills usage from the interrupted attempt.

**Data flow**: It receives round input and usage list, calls _stream_once, appends usage, retries once if the result marks an interruption, and returns the final StreamResult.

**Call relations**: _stream_recovering_overflow calls it for the initial attempt and any overflow retry.

*Call graph*: calls 1 internal fn (_stream_once); called by 1 (_stream_recovering_overflow); 2 external calls (emit_metric, log).


##### `TurnEngine._enforce_spend`  (lines 2465–2509)

```
async def _enforce_spend(self, usage_events: list[Usage], requesters: dict[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks whether the turn may continue spending before another model round. If caps or balance are exceeded, it parks the turn instead of failing it.

**Data flow**: It receives usage so far and active requesters, checks seats, prices pending usage, consults balance and cap evaluators, and raises TurnParked if not allowed.

**Call relations**: RuntimeConversation.prepare and prepare_exhaust call it before model work.

*Call graph*: calls 3 internal fn (_enforce_seats, __init__, _total_usage); 6 external calls (__init__, __init__, workspace_tx, applicable_caps_absent, balance_absent, audience_member).


##### `TurnEngine._enforce_seats`  (lines 2511–2520)

```
async def _enforce_seats(self, requesters: Mapping[UUID, ActiveMessage]) -> None
```

**Purpose**: Checks that all relevant members still have seats in the workspace. If a seat was revoked, the turn is parked.

**Data flow**: It gathers member ids from active requesters and on-behalf-of authority, queries seat status, and raises TurnParked when any are not seated.

**Call relations**: _enforce_spend and run_intent call it before continuing member-authorized work.

*Call graph*: calls 1 internal fn (__init__); called by 2 (_enforce_spend, run_intent); 2 external calls (__init__, workspace_tx).


##### `TurnEngine._stream_once`  (lines 2523–2691)

```
async def _stream_once(self, round_input: _RoundInput) -> StreamResult
```

**Purpose**: Performs one durable model stream step. It streams live text, records tool calls, reasoning blocks, usage, timings, and any mid-stream error into a replayable result.

**Data flow**: It receives a _RoundInput, builds a ModelRequest, runs ModelRoundRunner, publishes text deltas, emits model/cache metrics, and returns a StreamResult.

**Call relations**: _stream_retrying_interruption calls it; DBOS records its output so crash recovery does not call the model again.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_stream_retrying_interruption); 11 external calls (__init__, __init__, __init__, __init__, __init__, __init__, now, emit_histogram, emit_metric, emit_up_down_metric (+1 more)).


##### `TurnEngine._publish_cost`  (lines 2693–2708)

```
async def _publish_cost(self, usage_events: list[Usage]) -> None
```

**Purpose**: Publishes a live cost update for the turn so clients can show current spend. It is informational and does not decide billing.

**Data flow**: It totals usage events, calculates tokens and micro-dollar cost, and publishes a CostTick frame.

**Call relations**: RuntimeModel.stream calls it after successful model streaming.

*Call graph*: calls 2 internal fn (_publish, _total_usage); 1 external calls (__init__).


##### `TurnEngine._reseed_loaded_skills`  (lines 2710–2721)

```
def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Refreshes the tracker of skills already present in the conversation. This keeps skill-loading behavior correct after compaction or replayed history changes.

**Data flow**: It receives messages, finds loaded skill closures, adds preloaded skills, and reseeds the compaction skill tracker.

**Call relations**: RuntimeConversation.prepare and _stream_recovering_overflow call it around compaction.

*Call graph*: calls 1 internal fn (_loaded_skill_closures); called by 1 (_stream_recovering_overflow).


##### `TurnEngine._resolve_call`  (lines 2723–2737)

```
def _resolve_call(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Resolves a model’s raw tool call into either a known tool/action or a structured rejection. This gives later code one trusted identity to use.

**Data flow**: It receives a ToolUseBlock, routes object_action calls to action resolution, otherwise looks up a global tool, and returns EffectiveCall or _RejectedToolCall.

**Call relations**: RuntimeTools._resolve and run_intent use it before binding or dispatch.

*Call graph*: calls 2 internal fn (_rejected, _resolve_action); called by 1 (run_intent); 1 external calls (__init__).


##### `TurnEngine._resolve_action`  (lines 2739–2813)

```
def _resolve_action(self, call: ToolUseBlock) -> _Resolution
```

**Purpose**: Resolves an object_action transport call into the specific object action it names. It validates shape, kind, action, binding rules, targetability, grants, and input schema.

**Data flow**: It receives the raw call, parses ObjectActionInput, looks up the action binding, checks permissions and input, and returns an EffectiveCall or rejection.

**Call relations**: _resolve_call delegates to it for object_action calls.

*Call graph*: calls 1 internal fn (_rejected); called by 1 (_resolve_call); 3 external calls (__init__, model_validate, replace).


##### `TurnEngine._rejected`  (lines 2815–2827)

```
def _rejected(self, call: ToolUseBlock, error: Exception, dimensions: Mapping[str, str] | None=None) -> _RejectedToolCall
```

**Purpose**: Builds a standardized rejected-call object from an exception. This lets invalid calls flow through the normal dispatch result path as model-visible errors.

**Data flow**: It receives the call, exception, and optional metric dimensions, formats error text, classifies the outcome, and returns _RejectedToolCall.

**Call relations**: Call resolution, binding, action resolution, and intent admission checks use it when a call cannot safely run.

*Call graph*: called by 4 (_bind_or_error, _resolve_action, _resolve_call, run_intent); 1 external calls (__init__).


##### `TurnEngine._bind_or_error`  (lines 2829–2868)

```
async def _bind_or_error(self, context: ToolContext, item: _Resolution, requesters: dict[UUID, ActiveMessage]) -> _DispatchInput
```

**Purpose**: Binds a resolved call to the right requester context, or converts binding failures into rejected calls. Binding decides which member authority, sandbox, and subagent controls the tool gets.

**Data flow**: It receives a tool context, resolution, and active requesters; it returns an already rejected item, a bound call, or a new rejection, while metering serious binding failures.

**Call relations**: RuntimeTools.prepare and run_intent call it before dispatch.

*Call graph*: calls 4 internal fn (_bind_requester, _member_refs, _rejected, _meter_dispatch); called by 1 (run_intent); 5 external calls (__init__, __init__, meter_dimensions, replace, monotonic).


##### `TurnEngine._dispatch`  (lines 2870–2876)

```
async def _dispatch(self, bound: _DispatchInput, usage_events: list[Usage] | None=None) -> ToolResultBlock
```

**Purpose**: Runs a bound or rejected tool call and returns a model-ready ToolResultBlock. It combines the durable step result with image rehydration.

**Data flow**: It receives dispatch input and optional usage list, obtains a DispatchResult through recovery, converts it to a ToolResultBlock, and returns it.

**Call relations**: RuntimeTools.execute calls it for model-requested tools.

*Call graph*: calls 2 internal fn (_dispatch_result, _dispatch_step_recovering).


##### `TurnEngine._dispatch_result`  (lines 2878–2910)

```
async def _dispatch_result(self, result: DispatchResult) -> ToolResultBlock
```

**Purpose**: Converts a serialized DispatchResult into the ToolResultBlock the model sees. It reloads any offloaded images from blob storage.

**Data flow**: It receives a DispatchResult, either builds a text-only result or fetches image blobs and combines text/images, marks activity completion, and returns the block.

**Call relations**: _dispatch calls it after _dispatch_step_recovering.

*Call graph*: called by 1 (_dispatch); 4 external calls (__init__, __init__, __init__, __init__).


##### `TurnEngine._dispatch_step_recovering`  (lines 2912–2922)

```
async def _dispatch_step_recovering(self, bound: _DispatchInput, usage_events: list[Usage] | None) -> DispatchResult
```

**Purpose**: Runs dispatch steps until an interrupted dispatch has been properly resumed or accepted. This supports cancellation-safe tool execution.

**Data flow**: It receives dispatch input and usage tracking, repeatedly calls _dispatch_step with any resume target, feeds each result to _accept_dispatch_result, and returns the first accepted result.

**Call relations**: _dispatch and run_intent use it around DBOS dispatch steps.

*Call graph*: calls 2 internal fn (_accept_dispatch_result, _dispatch_step); called by 2 (_dispatch, run_intent).


##### `TurnEngine._accept_dispatch_result`  (lines 2924–2935)

```
def _accept_dispatch_result(self, result: DispatchResult, usage_events: list[Usage] | None) -> bool
```

**Purpose**: Decides whether a dispatch step result is final or represents live interruption. It also records tool-side usage exactly once.

**Data flow**: It receives a DispatchResult and usage list, checks whether the step body ran live, extends usage if needed, raises cancellation for live interruption, and returns whether to stop retrying.

**Call relations**: _dispatch_step_recovering calls it after every dispatch step attempt.

*Call graph*: called by 1 (_dispatch_step_recovering).


##### `TurnEngine._bind_requester`  (lines 2937–2989)

```
async def _bind_requester(self, context: ToolContext, item: EffectiveCall, requesters: dict[UUID, ActiveMessage]) -> tuple[ToolContext, ToolUseBlock]
```

**Purpose**: Attaches the correct member authority to a tool call. It uses requested_by when supplied, or the single member audience when that is unambiguous.

**Data flow**: It receives base context, an EffectiveCall, and active requesters; it validates requested_by, selects acting member, swaps sandbox/subagent controls if needed, removes requester metadata from tool input, and returns updated context plus call.

**Call relations**: _bind_or_error calls it for valid resolved calls.

*Call graph*: calls 1 internal fn (_own_member); called by 1 (_bind_or_error); 2 external calls (replace, UUID).


##### `TurnEngine._own_member`  (lines 2991–2999)

```
def _own_member(self, requesters: Mapping[UUID, ActiveMessage]) -> UUID | None
```

**Purpose**: Finds the member who owns this conversation when one of their active messages is present. This supports implicit requester binding in one-member conversations.

**Data flow**: It reads the audience and active requesters, returning the audience member id only if that member has an active message.

**Call relations**: _bind_requester and _member_refs use it to decide whether requested_by is needed.

*Call graph*: called by 2 (_bind_requester, _member_refs); 1 external calls (audience_member).


##### `TurnEngine._member_refs`  (lines 3001–3007)

```
def _member_refs(self, requesters: Mapping[UUID, ActiveMessage]) -> tuple[UUID, ...]
```

**Purpose**: Lists the active message ids that a tool may name with requested_by. It omits refs when the conversation already belongs to one member.

**Data flow**: It receives active requesters, checks whether there is an own-member shortcut, and returns message ids that have member authors.

**Call relations**: _bind_or_error and RuntimeModel.stream use it for requester hints and schema decisions.

*Call graph*: calls 1 internal fn (_own_member); called by 1 (_bind_or_error).


##### `TurnEngine._offload`  (lines 3009–3034)

```
async def _offload(self, name: str, content: str) -> str | None
```

**Purpose**: Writes large text into the sandbox tool-output directory and returns a display path. This keeps huge tool results out of the model context while still making them available.

**Data flow**: It receives a file name and content, ensures the output directory exists, writes the bytes, logs/metrics failures, and returns a runtime-visible path or none.

**Call relations**: _finish_dispatch uses it for oversized tool output and RuntimeModel.stream uses it for truncation salvage.

*Call graph*: called by 1 (_finish_dispatch); 2 external calls (emit_metric, log).


##### `TurnEngine._start_activity`  (lines 3036–3041)

```
def _start_activity(self, call: ToolUseBlock, goal: str) -> None
```

**Purpose**: Starts an asynchronous summary task for a tool call’s live activity label. This lets clients see a friendly “what it is doing” message.

**Data flow**: It increments an activity sequence, creates a task to summarize the call, stores the task, and arranges cleanup when done.

**Call relations**: RuntimeTools.prepare and run_intent call it for bound calls.

*Call graph*: calls 1 internal fn (_generate_activity); called by 1 (run_intent); 1 external calls (create_task).


##### `TurnEngine._generate_activity`  (lines 3043–3055)

```
async def _generate_activity(self, call: ToolUseBlock, goal: str, sequence: int) -> None
```

**Purpose**: Generates and publishes one activity summary in original call order. The ordering prevents later tool labels from appearing before earlier ones.

**Data flow**: It receives a call, goal text, and sequence number, asks the summarizer for text, stores labels, waits for earlier summaries, and publishes activity frames.

**Call relations**: _start_activity launches it as a background task.

*Call graph*: calls 2 internal fn (_publish, _publish_run); called by 1 (_start_activity); 1 external calls (__init__).


##### `TurnEngine._stop_activity`  (lines 3057–3059)

```
def _stop_activity(self) -> None
```

**Purpose**: Cancels outstanding activity-summary tasks when a turn reaches its terminal frame. No more live activity should be published after the turn is over.

**Data flow**: It reads the current activity task set and cancels each task.

**Call relations**: _publish_terminal calls it after publishing the terminal frame.

*Call graph*: called by 1 (_publish_terminal).


##### `TurnEngine._dispatch_step`  (lines 3062–3167)

```
async def _dispatch_step(self, bound: _DispatchInput, resume_target: ObjectActionTarget | None=None) -> DispatchResult
```

**Purpose**: Runs one tool dispatch as a DBOS-recorded step. It covers rejected calls, pre-hooks, handler execution, post-hooks, usage capture, interruption capture, and metrics.

**Data flow**: It receives dispatch input and optional resume target, prepares the call, invokes the handler if allowed, finishes output processing, returns a DispatchResult, or records an interrupted result on cancellation.

**Call relations**: _dispatch_step_recovering calls it; DBOS memoization prevents completed tools from re-running after crash recovery.

*Call graph*: calls 4 internal fn (_finish_dispatch, _invoke_dispatch, _prepare_dispatch, _meter_dispatch); called by 1 (_dispatch_step_recovering); 3 external calls (__init__, monotonic, span).


##### `TurnEngine._prepare_dispatch`  (lines 3169–3265)

```
async def _prepare_dispatch(self, bound: _BoundToolCall, target: ObjectActionTarget | None) -> _DispatchGate
```

**Purpose**: Validates and gates a bound tool call before the handler runs. It may refuse a replayed read when fresh member guidance is waiting, validate inputs, resolve object targets, and run pre-tool hooks.

**Data flow**: It receives a bound call and optional target, returns either a ready dispatch package or an immediate DispatchResult explaining why the call did not run.

**Call relations**: _dispatch_step calls it as the first phase of tool execution.

*Call graph*: calls 2 internal fn (_pending_member_guidance, _redoes_on_replay); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, log).


##### `TurnEngine._invoke_dispatch`  (lines 3267–3323)

```
async def _invoke_dispatch(self, bound: _BoundToolCall, ready: _DispatchReady, find_usages: list[Usage]) -> _HandlerOutput
```

**Purpose**: Calls the actual tool handler with the prepared context and arguments. It captures text, images, tool errors, thrown exceptions, and usage from nested find calls.

**Data flow**: It receives the bound call, ready dispatch data, and find-usage list; it sets handler context, awaits the handler, separates text/images, and returns a _HandlerOutput.

**Call relations**: _dispatch_step calls it after _prepare_dispatch approves the call.

*Call graph*: called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, replace).


##### `TurnEngine._finish_dispatch`  (lines 3325–3391)

```
async def _finish_dispatch(self, ready: _DispatchReady, handled: _HandlerOutput, find_usages: list[Usage]) -> DispatchResult
```

**Purpose**: Turns raw handler output into a durable DispatchResult. It bounds or offloads long text, walls off untrusted content, runs post hooks, stores images as blobs, and preserves tool-side usage.

**Data flow**: It receives ready dispatch data, handler output, and find usage, transforms content safely, writes image blobs if needed, and returns DispatchResult.

**Call relations**: _dispatch_step calls it after handler invocation.

*Call graph*: calls 3 internal fn (_bounded_image, _offload, _bounded); called by 1 (_dispatch_step); 5 external calls (__init__, __init__, __init__, __init__, wall).


##### `TurnEngine._redoes_on_replay`  (lines 3393–3400)

```
def _redoes_on_replay(self, tool: ToolDef) -> bool
```

**Purpose**: Decides whether re-executing a tool during crash replay would redo work. Read-only tools can be preempted for new guidance, while side-effecting tools should continue through idempotency.

**Data flow**: It receives a tool definition and returns true when the tool is not side-effecting.

**Call relations**: _prepare_dispatch uses it during adoption replay checks.

*Call graph*: called by 1 (_prepare_dispatch).


##### `TurnEngine._pending_member_guidance`  (lines 3402–3418)

```
async def _pending_member_guidance(self) -> bool
```

**Purpose**: Checks whether an unclaimed member message is waiting in the same conversation. This lets a replayed read yield to fresh guidance instead of running ahead.

**Data flow**: It queries inbound_message for one pending member row in the conversation and returns whether one exists.

**Call relations**: _prepare_dispatch calls it when deciding whether to preempt replayed read-like work.

*Call graph*: called by 1 (_prepare_dispatch); 2 external calls (select, workspace_tx).


##### `TurnEngine._bounded_image`  (lines 3420–3448)

```
async def _bounded_image(self, image: ImageBlock) -> ImageBlock
```

**Purpose**: Shrinks oversized tool-result images before sending them to the model. This avoids provider image limits and wasted pixels.

**Data flow**: It receives an ImageBlock with base64 data, decodes and opens it, thumbnails it if too large, re-encodes it, and returns either the smaller image or the original on safe failure.

**Call relations**: _finish_dispatch calls it before storing image outputs in blob storage.

*Call graph*: called by 1 (_finish_dispatch); 7 external calls (__init__, __init__, to_thread, b64decode, b64encode, BytesIO, log).


##### `TurnEngine._commit`  (lines 3450–3522)

```
async def _commit(self, status: TerminalStatus, usage_events: list[Usage], meter: _TurnMeter, answer: str='', error: BaseException | None=None, question: AskUserInput | None=None, credential_request:
```

**Purpose**: Retries terminal commit until it is durable, then records metrics and logs. A turn should not leave clients waiting because of a temporary database failure.

**Data flow**: It receives desired status, usage, meter, answer/error/act fields, and guard options; it retries _commit_once with backoff, emits terminal metrics if it wrote the transition, marks the meter exited, and returns the frame or none.

**Call relations**: run, _prepare_run, and run_intent call it whenever they need to finish or fail a turn.

*Call graph*: calls 2 internal fn (_commit_once, exited); called by 3 (_prepare_run, run, run_intent); 4 external calls (sleep, emit_metric, formatted_stack, log).


##### `TurnEngine._publish_terminal`  (lines 3524–3527)

```
async def _publish_terminal(self, frame: TerminalFrame) -> None
```

**Purpose**: Publishes the terminal frame to live clients and mirrors subagent status if needed. It also stops pending activity summaries.

**Data flow**: It receives a TerminalFrame, publishes it on the turn hub, publishes subagent run status, cancels activity tasks, and returns nothing.

**Call relations**: run, _prepare_run, and run_intent call it after commit.

*Call graph*: calls 3 internal fn (_publish, _publish_run, _stop_activity); called by 3 (_prepare_run, run, run_intent); 1 external calls (__init__).


##### `TurnEngine._record_workspace_changes`  (lines 3529–3542)

```
async def _record_workspace_changes(self, targets: tuple[str, ...]) -> None
```

**Purpose**: Refreshes the portal’s view of files or workspace objects changed by the turn. It runs after the user-visible turn result is already durable.

**Data flow**: It receives target paths, builds a WorkspaceChangeRecorder for the relevant sandbox conversation, and asks it to record changes.

**Call relations**: run and _prepare_run call it after terminal publication.

*Call graph*: called by 2 (_prepare_run, run); 1 external calls (__init__).


##### `TurnEngine._commit_once`  (lines 3544–3663)

```
async def _commit_once(self, status: TerminalStatus, usage_events: list[Usage], answer: str, error: BaseException | None, question: AskUserInput | None, credential_request: CredentialRequest | None, c
```

**Purpose**: Performs one database transaction to bill usage and write the terminal frame. It can refuse to close if unabsorbed arrivals are still pending.

**Data flow**: It totals usage, optionally locks the conversation and checks pending arrivals, records billing, reads final cost, builds a TerminalFrame, updates the turn if still non-terminal, and returns the frame plus whether it committed.

**Call relations**: _commit calls it repeatedly until it succeeds or returns a guarded none.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (_commit); 11 external calls (__init__, model_validate, and_, or_, select, update, workspace_tx, log, log_error, read_turn_cost (+1 more)).


##### `TurnEngine._park`  (lines 3665–3703)

```
async def _park(self, message: str, usage_events: list[Usage]) -> None
```

**Purpose**: Moves a running turn into parked state when spend or seat rules stop it. Parked turns are resumable instead of failed.

**Data flow**: It updates the turn to parked, records usage, releases claimed arrivals, publishes a Parked frame, emits a metric, and logs the event.

**Call relations**: run and run_intent catch TurnParked and call this.

*Call graph*: calls 2 internal fn (_publish, _total_usage); called by 2 (run, run_intent); 6 external calls (__init__, update, workspace_tx, emit_metric, log, record_turn_usage).


##### `TurnEngine._publish`  (lines 3705–3714)

```
async def _publish(self, frame: LiveFrame) -> None
```

**Purpose**: Publishes a live frame without allowing publish failure to fail the turn. The database state remains the source of truth.

**Data flow**: It receives a live frame, tries to publish it to the hub, and logs any publish exception.

**Call relations**: Many helpers use it for text deltas, replies, activity, cost ticks, absorbed arrivals, parked notices, and terminal frames.

*Call graph*: called by 8 (_absorb_arrivals, _generate_activity, _park, _publish_cost, _publish_terminal, _speak, _stream_closing_spans, run); 1 external calls (log).


##### `TurnEngine._publish_run`  (lines 3716–3738)

```
async def _publish_run(self, activity: str='', status: str='') -> None
```

**Purpose**: Mirrors subagent activity onto the root turn’s stream. This lets clients tail one stream and still see child-agent progress.

**Data flow**: It receives optional activity and status text, returns immediately for main turns, otherwise builds a SubagentActivity frame and publishes it to the root turn hub.

**Call relations**: run, _generate_activity, and _publish_terminal call it at start, activity, and terminal moments.

*Call graph*: called by 3 (_generate_activity, _publish_terminal, run); 2 external calls (__init__, log).


##### `TurnEngine._stop_sandbox_commands`  (lines 3740–3756)

```
async def _stop_sandbox_commands(self) -> None
```

**Purpose**: Stops sandbox commands after a deliberate workflow cancellation. It does not run for executor preemption, where the workflow may resume and still need those commands.

**Data flow**: It asks the sandbox to stop commands and logs any failure without changing the already-durable cancellation outcome.

**Call relations**: run and run_intent call it in DBOS cancellation paths.

*Call graph*: called by 2 (run, run_intent); 1 external calls (log_error).


##### `TurnEngine._bill_cancelled`  (lines 3758–3778)

```
async def _bill_cancelled(self, usage_events: list[Usage]) -> None
```

**Purpose**: Best-effort bills tokens consumed before a cancellation or preemption. Cancellation should not erase real model usage.

**Data flow**: It totals usage events, tries to record them in a database transaction, and logs any failure without raising.

**Call relations**: TurnEngine.run calls it in DBOS cancellation, preemption, and some failure paths.

*Call graph*: calls 1 internal fn (_total_usage); called by 1 (run); 3 external calls (workspace_tx, log, record_turn_usage).


##### `TurnEngine._resolve_unclaimed`  (lines 3780–3785)

```
async def _resolve_unclaimed(self) -> TerminalFrame | None
```

**Purpose**: Handles a worker that failed to claim the turn. It either republishes an already committed terminal or leaves the live owner alone.

**Data flow**: It creates a TranscriptRepair helper, asks it to resolve the committed terminal if any, and returns that frame or none.

**Call relations**: run and run_intent call it when _mark_running returns false.

*Call graph*: calls 1 internal fn (_repair); called by 2 (run, run_intent).


##### `TurnEngine._persist_transcript`  (lines 3787–3790)

```
async def _persist_transcript(self, messages: tuple[Message, ...], answer: str, system: str, injected: str) -> None
```

**Purpose**: Persists a completed transcript after labeling tool results with activity text. This stores history in the same terms the user saw live.

**Data flow**: It receives messages, answer, system, and injected text, labels messages, creates a repair helper, and delegates transcript writing.

**Call relations**: run, _prepare_run, and run_intent call it after successful or denied completion.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 3 (_prepare_run, run, run_intent).


##### `TurnEngine._persist_interrupted`  (lines 3792–3796)

```
async def _persist_interrupted(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Persists the safest available transcript for an interrupted or failed turn. If no message window exists, it preserves only inbound or founding denial text.

**Data flow**: It receives messages, chooses between interrupted-window persistence and inbound-only persistence, labels messages when present, and delegates to TranscriptRepair.

**Call relations**: TurnEngine.run calls it in cancellation, failure, and non-done terminal paths.

*Call graph*: calls 2 internal fn (_labeled, _repair); called by 1 (run).


##### `TurnEngine._labeled`  (lines 3798–3823)

```
def _labeled(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Adds activity labels to tool result blocks before transcript storage. This makes old transcripts show the same friendly activity text live clients saw.

**Data flow**: It receives messages, walks structured message content, fills activity_text for completed activity results, and returns updated messages.

**Call relations**: _persist_transcript and _persist_interrupted call it before writing history.

*Call graph*: called by 2 (_persist_interrupted, _persist_transcript).


### `core/src/ufo/harness/__init__.py`

`other` · `import time`

This is an empty package marker file. In Python, a folder with an `__init__.py` file is treated as an importable package, which means other parts of the project can refer to code inside `ufo.harness` using normal import paths. Think of it like putting a label on a drawer: the drawer may contain useful tools, but the label itself does not do the work. Without this file, depending on the Python version and packaging setup, imports from the `ufo.harness` area could be less reliable or fail in some environments. Because the file is empty, it has no startup code, no settings, and no side effects. Its only job is to make the package structure explicit.


### `core/src/ufo/harness/agent.py`

`orchestration` · `active during each agent run`

This file is the agent runner. It is like a stage manager for a conversation: it gives the model the script so far, listens for what the model says, sends tool requests to the right backstage helpers, records the results, and knows when the show is finished.

The file first defines the pieces that can appear in a conversation: plain text, images, tool calls, tool results, reasoning notes, and messages from either the user or assistant. It also defines an agent’s settings, such as the system prompt and the maximum number of rounds allowed.

The central class is AgentEngine. It does not directly know how to call a specific AI model, save a transcript, run a real tool, or stream events to a user. Instead, those are supplied through small interfaces called protocols: AgentModel, AgentTools, AgentConversation, and AgentEvents. This keeps the engine reusable.

During a run, the engine prepares the conversation, asks the model for a round, separates member-facing replies from hidden control text, runs any requested tools, and adds tool results back into the transcript. If the model gives a final text answer, the engine closes the run. If a structured output contract is configured, the engine can require the model to finish by calling a special finish tool. If the round limit is reached, it forces a final answer rather than looping forever.

#### Function details

##### `ToolDefinition.__post_init__`  (lines 61–63)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that a tool has a real name after it is created. A nameless tool would be impossible for the model and dispatcher to refer to safely.

**Data flow**: It reads the newly created tool definition’s name. If the name is only blank space, it stops creation by raising an error; otherwise the tool definition remains unchanged.

**Call relations**: This validation runs automatically when a ToolDefinition is built. Later, AgentEngine._stream collects tool definitions and sends them to the model, so catching an empty name early prevents confusing model requests.


##### `AgentDefinition.__post_init__`  (lines 74–78)

```
def __post_init__(self) -> None
```

**Purpose**: Checks that the agent’s round limits are usable. The engine needs at least one model round and at least one allowed parallel tool call to make progress.

**Data flow**: It reads max_rounds and max_parallel_calls from the new agent definition. If either value is less than one, it raises an error; otherwise the definition is accepted as-is.

**Call relations**: This runs automatically when an AgentDefinition is created. AgentEngine.run depends on max_rounds to bound the loop, and AgentEngine._tool_exchange depends on max_parallel_calls when grouping tool work.


##### `RecoverableModelError.__init__`  (lines 126–128)

```
def __init__(self, feedback: str) -> None
```

**Purpose**: Creates an error that means a model round failed in a way the agent can try to recover from. It carries feedback text that can be added to the conversation so the model can try again.

**Data flow**: It receives a feedback string. It stores that string both as the exception message and as a feedback attribute, so callers can read it later.

**Call relations**: The runtime model layer can raise this when a model response is bad but not fatal. AgentEngine.run catches it, adds the feedback as a new user message, and starts the next round instead of ending the run.

*Call graph*: called by 1 (stream).


##### `AgentModel.stream`  (lines 134–134)

```
async def stream(self, request: ModelRequest, round_index: int) -> ModelRound
```

**Purpose**: Describes the required model call: take a complete model request and produce one model round. Implementations use this to connect the engine to a real AI model service or local model.

**Data flow**: It receives a ModelRequest containing the prompt, transcript, available tools, and round mode, plus the round number. It should return a ModelRound containing the assistant messages, text, tool calls, and optional reasoning.

**Call relations**: AgentEngine._stream builds the request and calls this method. The returned round is then interpreted by AgentEngine.run, which either closes, runs tools, retries, or exhausts the run.


##### `AgentTools.definitions`  (lines 142–142)

```
def definitions(self) -> tuple[ToolDefinition, ...]
```

**Purpose**: Provides the list of tools this agent can offer to the model. Each definition tells the model the tool’s name, purpose, and expected input shape.

**Data flow**: It takes no arguments and reads the tool provider’s available tool declarations. It returns them as an immutable tuple of ToolDefinition objects.

**Call relations**: AgentEngine._stream asks for these definitions during normal rounds. It checks them for duplicate names, may add a special structured-output finish tool, and then passes the full tool list to the model.


##### `AgentTools.parallel_safe`  (lines 144–144)

```
def parallel_safe(self, call: ToolCall) -> bool
```

**Purpose**: Answers whether a particular tool call is safe to run at the same time as other calls. This protects tools that might conflict if used together, like two tasks writing to the same file.

**Data flow**: It receives a ToolCall and inspects whatever tool declaration or rules apply to that call. It returns true if the call can run in parallel, or false if it must be isolated.

**Call relations**: AgentEngine._tool_exchange gives this method to dispatch_segments, which divides tool calls into safe batches before execution.


##### `AgentTools.prepare`  (lines 146–146)

```
async def prepare(self, calls: tuple[ToolCall, ...]) -> None
```

**Purpose**: Gives the tool layer a chance to get ready before a batch of tool calls runs. This can reserve resources, validate a group, or set up shared state.

**Data flow**: It receives a tuple of ToolCall objects about to be executed. It performs any needed setup and returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this before executing each segment of tool calls. After the executions finish or fail, the engine still calls after_round so the tool layer can clean up or observe results.


##### `AgentTools.execute`  (lines 148–148)

```
async def execute(self, call: ToolCall) -> ToolResult
```

**Purpose**: Runs one requested tool call and returns the result that will be shown back to the model. This is where a model request like “search” or “read file” becomes real work.

**Data flow**: It receives one ToolCall with an id, name, and input data. It performs the tool’s action and returns a ToolResult tied to the same call id, including content or an error marker.

**Call relations**: AgentEngine._tool_exchange schedules this method for each call in a dispatch segment, using asyncio.gather to run safe calls together. The collected results become the next user message to the model.


##### `AgentTools.after_round`  (lines 150–152)

```
async def after_round(self, calls: tuple[ToolCall, ...], results: tuple[ToolResult, ...]) -> None
```

**Purpose**: Lets the tool layer observe or clean up after a model round that included tool calls. It runs even when tool execution fails partway through.

**Data flow**: It receives all tool calls from the round and the results that were produced before completion or failure. It can update outside state, release resources, or record what happened, and returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this in a finally block, meaning it is the tool layer’s reliable end-of-round hook after prepare and execute have been attempted.


##### `AgentTools.interrupted`  (lines 154–154)

```
def interrupted(self) -> None
```

**Purpose**: Notifies the tool layer that a previously open final action was interrupted. This gives tools a chance to abandon or mark unfinished work.

**Data flow**: It receives no explicit input, but the tool implementation can use its own stored state. It changes whatever internal or external cleanup state is needed and returns nothing.

**Call relations**: AgentEngine.run calls this when the conversation boundary says preparation found an interrupted final act. This connects transcript recovery to tool cleanup.


##### `AgentConversation.prepare`  (lines 160–160)

```
async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound
```

**Purpose**: Prepares the transcript before a model round. A conversation store can restore, trim, or adjust messages before they are sent to the model.

**Data flow**: It receives the current messages and the round number. It returns a PreparedRound containing the messages to use next and a flag saying whether an interrupted final act was detected.

**Call relations**: AgentEngine.run calls this at the start of each round. If the result reports an interrupted final act, the engine tells AgentTools.interrupted before streaming the model.


##### `AgentConversation.checkpoint`  (lines 162–162)

```
async def checkpoint(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Saves a transcript checkpoint after tool results have been added. This makes the conversation durable at important points.

**Data flow**: It receives the updated messages. It writes or records them according to the implementation and returns nothing.

**Call relations**: AgentEngine.run calls this after AgentEngine._tool_exchange returns an updated transcript and before the next model round begins.


##### `AgentConversation.prepare_exhaust`  (lines 164–164)

```
async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Prepares the transcript when the agent has used all allowed rounds. This gives the conversation boundary one last chance to adjust messages before forcing a final answer.

**Data flow**: It receives the current messages. It returns the messages that should be used for the exhaustion step.

**Call relations**: AgentEngine._exhaust calls this after announcing exhaustion and before adding the final-answer prompt or structured finish prompt.


##### `AgentEvents.speak`  (lines 170–170)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: Reports assistant replies that are visible during an ongoing round. This lets a user interface show progress without changing the engine’s decisions.

**Data flow**: It receives marked replies and a human-friendly round number. It emits, records, or ignores those replies depending on the implementation, and returns nothing.

**Call relations**: AgentEngine._tool_exchange calls this before running tool calls, because the assistant may have said something visible before asking tools to work.


##### `AgentEvents.closing`  (lines 172–172)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Reports the final visible replies when an agent run is closing. This is the final event hook for user-facing text.

**Data flow**: It receives the marked replies that belong to the closing answer. It sends or records them as needed and returns nothing.

**Call relations**: AgentEngine._close calls this for an ordinary final text answer, and AgentEngine._exhaust calls it when the round budget forced a final no-tool answer.


##### `AgentEvents.exhausted`  (lines 174–174)

```
def exhausted(self) -> None
```

**Purpose**: Announces that the agent ran out of normal rounds. This lets observers record that the answer was forced by the limit.

**Data flow**: It receives no input. It may update outside observers or logs and returns nothing.

**Call relations**: AgentEngine._exhaust calls this before preparing and forcing the final response.


##### `MemoryConversation.prepare`  (lines 193–194)

```
async def prepare(self, messages: tuple[Message, ...], round_index: int) -> PreparedRound
```

**Purpose**: Provides a simple in-memory conversation preparation step for local runs and tests. It does not change the transcript.

**Data flow**: It receives messages and a round number. It wraps the same messages in a PreparedRound with no interruption flag and returns it.

**Call relations**: This is the default conversation boundary used by AgentEngine when no persistent store is supplied. It calls PreparedRound construction and then hands the unchanged transcript back to AgentEngine.run.

*Call graph*: 1 external calls (__init__).


##### `MemoryConversation.checkpoint`  (lines 196–197)

```
async def checkpoint(self, messages: tuple[Message, ...]) -> None
```

**Purpose**: Acts as a no-op checkpoint for runs that do not need transcript persistence. It satisfies the conversation interface without writing anywhere.

**Data flow**: It receives messages but does not store or modify them. It returns nothing.

**Call relations**: AgentEngine.run can call this after tool exchanges when MemoryConversation is being used. Because it does nothing, local and test runs avoid storage side effects.


##### `MemoryConversation.prepare_exhaust`  (lines 199–200)

```
async def prepare_exhaust(self, messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: Provides a simple exhaustion preparation step that leaves messages untouched. It is useful when there is no external transcript system to consult.

**Data flow**: It receives the current messages and returns the same messages. Nothing else changes.

**Call relations**: AgentEngine._exhaust can call this through the AgentConversation boundary. With MemoryConversation, the forced-final-answer step starts from the existing transcript exactly as it is.


##### `NullEvents.speak`  (lines 207–208)

```
async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None
```

**Purpose**: Ignores ongoing visible replies. This is the default observer for runs that do not need live event output.

**Data flow**: It receives marked replies and a round number, then intentionally does nothing and returns nothing.

**Call relations**: AgentEngine._tool_exchange may call this when no real event stream is supplied. It lets the engine use the same flow without requiring a user interface.


##### `NullEvents.closing`  (lines 210–211)

```
async def closing(self, replies: tuple[MarkedReply, ...]) -> None
```

**Purpose**: Ignores closing replies. This keeps final event reporting optional.

**Data flow**: It receives marked final replies, does not emit or store them, and returns nothing.

**Call relations**: AgentEngine._close and AgentEngine._exhaust may call this through the AgentEvents boundary. With NullEvents, finishing a run has no event side effects.


##### `NullEvents.exhausted`  (lines 213–214)

```
def exhausted(self) -> None
```

**Purpose**: Ignores the exhaustion notification. This is useful for local runs where no observer needs to know the round limit was reached.

**Data flow**: It receives no input and changes nothing. It returns nothing.

**Call relations**: AgentEngine._exhaust calls this through the event boundary. NullEvents keeps that call harmless when no event system is attached.


##### `AgentEngine.run`  (lines 228–258)

```
async def run(self, messages: tuple[Message, ...]) -> Finished
```

**Purpose**: Runs the full agent conversation until it reaches a final answer or must force one because the round limit is used up. This is the main control loop of the file.

**Data flow**: It starts with the current transcript. Each round, it prepares the transcript, asks the model for a response, separates visible replies from control text, runs tools if requested, saves checkpoints after tool work, and returns a Finished result when an answer is ready. If the model gives an empty response once, it adds feedback and retries; if it happens twice, it raises an error.

**Call relations**: This method is the top-level engine flow. It calls AgentEngine._stream to ask the model, marked_replies to interpret visible reply markers, AgentEngine._tool_exchange when tools are requested, AgentEngine._close for a normal final answer, and AgentEngine._exhaust when the round budget runs out.

*Call graph*: calls 4 internal fn (_close, _exhaust, _stream, _tool_exchange); 3 external calls (__init__, __init__, marked_replies).


##### `AgentEngine._stream`  (lines 260–296)

```
async def _stream(self, messages: tuple[Message, ...], round_index: int, mode: RoundMode) -> ModelRound
```

**Purpose**: Builds the exact request sent to the model for one round. It decides which tools, if any, the model is allowed to use.

**Data flow**: It receives messages, a round number, and a mode. In normal mode it gathers tool definitions, checks for duplicate names, and optionally adds a structured-output finish tool. In no-tool mode it sends no tools. In force-finish mode it allows only the finish tool and requires that choice. It then sends a ModelRequest to the model and returns the ModelRound it receives.

**Call relations**: AgentEngine.run uses this for ordinary rounds. AgentEngine._force_finish uses it when a structured final answer must be forced, and AgentEngine._exhaust uses it when the engine has run out of normal rounds.

*Call graph*: called by 3 (_exhaust, _force_finish, run); 1 external calls (__init__).


##### `AgentEngine._tool_exchange`  (lines 298–364)

```
async def _tool_exchange(self, streamed: ModelRound, spoken: tuple[MarkedReply, ...], round_index: int) -> Finished | tuple[Message, ...]
```

**Purpose**: Turns model-requested tool calls into tool results and appends both sides of that exchange to the transcript. It is the bridge between the model saying “use this tool” and the next model round seeing what happened.

**Data flow**: It receives the streamed model round, visible replies, and round number. It emits the visible replies, checks whether any call is the special structured finish call, validates or rejects that finish call, groups normal tool calls into safe execution segments, prepares and executes each segment, and collects ToolResult objects. It returns either a Finished structured answer or an updated message tuple containing the assistant’s tool calls and the user-side tool results.

**Call relations**: AgentEngine.run calls this whenever a model round contains tool calls. Inside, it uses dispatch_segments to decide batching, asyncio.gather to run safe calls together, and the AgentTools boundary for preparation, execution, and after-round cleanup.

*Call graph*: called by 1 (run); 6 external calls (__init__, __init__, __init__, __init__, gather, dispatch_segments).


##### `AgentEngine._close`  (lines 366–383)

```
async def _close(self, streamed: ModelRound, spoken: tuple[MarkedReply, ...], round_index: int) -> Finished
```

**Purpose**: Finishes a run when the model has produced text instead of tool calls. If structured output is required, it may turn that text into a validated structured answer or ask the model to finish properly.

**Data flow**: It receives the final-looking model round, marked visible replies, and the round number. Without structured output, it reports closing replies and returns the text as the answer. With structured output, it first checks whether prose is acceptable; if not, it appends a prompt asking for the required finish action and calls the forced-finish path.

**Call relations**: AgentEngine.run calls this when a streamed round has non-empty text and no tool calls. If needed, this method hands off to AgentEngine._force_finish; otherwise it creates the Finished result itself.

*Call graph*: calls 1 internal fn (_force_finish); called by 1 (run); 2 external calls (__init__, __init__).


##### `AgentEngine._force_finish`  (lines 385–394)

```
async def _force_finish(self, messages: tuple[Message, ...], round_index: int) -> Finished
```

**Purpose**: Forces a structured-output agent to end by making exactly one special finish-tool call. This protects callers that need a validated answer shape rather than free-form text.

**Data flow**: It receives a transcript and a round number. It streams one force-finish model round, checks that the model returned exactly one call to the configured finish tool, validates that call, and returns a Finished structured answer. If the model does not comply or validation fails, it raises an error.

**Call relations**: AgentEngine._close calls this when ordinary prose did not satisfy the structured-output contract. AgentEngine._exhaust also calls it when the round limit is reached but a structured answer is still required.

*Call graph*: calls 1 internal fn (_stream); called by 2 (_close, _exhaust); 1 external calls (__init__).


##### `AgentEngine._exhaust`  (lines 396–413)

```
async def _exhaust(self, messages: tuple[Message, ...]) -> Finished
```

**Purpose**: Produces a final answer after the agent has used all allowed normal rounds. This prevents endless looping and marks the result as exhausted.

**Data flow**: It announces exhaustion, lets the conversation boundary prepare the transcript, and then takes one of two paths. For structured output, it adds the force-finish prompt and calls AgentEngine._force_finish, then marks the result exhausted. For ordinary output, it adds the final-answer prompt, streams one no-tool model round, extracts visible reply text, emits closing events, and returns an exhausted Finished result.

**Call relations**: AgentEngine.run calls this after the normal round loop ends. It uses AgentEngine._force_finish for structured contracts, AgentEngine._stream for ordinary forced final text, and marked_replies before reporting closing events.

*Call graph*: calls 2 internal fn (_force_finish, _stream); called by 1 (run); 3 external calls (__init__, __init__, marked_replies).

## 📊 State Registers Touched

- `reg-durable-database` — The main long-term database where shared business and runtime records are stored.
- `reg-agent-configuration` — Each agent’s saved settings, such as model choice, reasoning mode, tools, visibility, internet access, sandbox size, and setup needs.
- `reg-billing-spend-ledger` — The shared accounting state for spend caps, usage charges, prepaid balances, BYOK billing, and ledger exports.
- `reg-feature-flags` — The rollout switches that turn product and infrastructure behavior on or off across the system.
- `reg-model-catalog-providers` — The shared catalog of available AI models, their prices and limits, and the provider clients used to call them.
- `reg-tool-catalog-allowlists` — The shared list of tools and actions an agent may see or run, including extension tools and sandbox bridge tools.
- `reg-skill-prompt-library` — The reusable instructions, skills, prompt rules, and agent setup guidance loaded into turns.
- `reg-conversation-turn-queue` — The durable state of conversations and turns, including admission, ordering, current runner, lifecycle status, and queued work.
- `reg-live-turn-stream` — The live event feed that lets clients and other processes watch a running turn and learn how it ended.
- `reg-transcript-history` — The saved conversation transcript, summaries, compactions, and access records that preserve what happened in a chat.
- `reg-sandbox-runtime` — The durable sandbox and browser workspace handles where agent commands, files, web browsing, and hosted previews run safely.
- `reg-delegation-workflows` — The saved state for subagents, parent-child turns, objectives, workflow checkpoints, pending deliveries, and recovery.
- `reg-observability-trace` — The logs, metrics, traces, health signals, and trace links used to understand what the system is doing.
- `reg-database-connection-pool` — The shared SQLAlchemy engine/session and connection-pool state used by requests, turns, workers, migrations, and persistence helpers to access the database safely.
- `reg-active-turn-cancellation-handles` — The in-process registry of currently running turn/workflow tasks and cancellation handles used to stop active work before marking it cancelled durably.
- `reg-turn-runtime-snapshot` — The per-turn frozen runtime configuration and generated references used to run, recover, bill, and debug a turn consistently after settings change.
- `reg-execution-step-log` — The structured persisted model, tool, and workflow execution records that power debugger timelines and post-run inspection beyond the user transcript.
- `reg-provider-rate-limit-budgets` — Shared per-provider throttle, retry, and backoff budget state for model, search, connector, and external API calls so workers avoid overrunning provider limits.
- `reg-turn-context-token-budget` — The active per-turn context-window and token/image budget accounting used to choose prompt contents, trigger compaction, constrain model rounds, and reconcile usage.
