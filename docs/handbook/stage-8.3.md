# Streaming model output normalization  `stage-8.3`

This stage sits in the main work loop, during one conversation turn with a model. Model providers send their answers as a live stream, but that stream can be uneven: pieces of text may arrive out of order, tool requests may be split across messages, and accounting details may only appear at the end. This stage turns that messy feed into clean events the rest of the system can trust.

The key file, core/src/ufo/harness/rounds.py, runs one full “round” with the provider. It listens to each incoming stream item, separates ordinary text from special events like tool calls and reasoning blocks, and only shows safe text to the user as it becomes available. At the same time, it keeps careful notes: what the model said, which tools it asked for, how much usage was reported, and how the turn ended. When the stream finishes, it packages all of this into a durable final record. In effect, it acts like a filter and recorder between a noisy live broadcast and a clean transcript.

## Files in this stage

### Streaming model output normalization
### `core/src/ufo/harness/rounds.py`

`orchestration` · `request handling during one model round`

A model provider does not usually return one neat answer all at once. It sends a stream of small events: text chunks, tool-call pieces, reasoning notes, usage records, and sometimes errors. This file turns that stream into something the rest of the system can trust.

The main piece is `ModelRoundRunner`. Given a request, it opens the provider stream and feeds every event into a private `_RoundState`, which is like a clipboard for the round. Text is collected for the final answer, but it is also buffered and periodically released to the live output. Before text is published, it passes through a `TextFilter`, so unwanted text can be removed before a user sees it.

Tool calls are assembled from a start event plus later JSON fragments. Usage records are saved because they prove the provider completed the generation and are needed for accounting. If the stream fails partway through, the file does not pretend the answer is complete. It returns a `CollectedRound` marked with error details and any partial output, so the engine can discard and retry the round when appropriate.

Timing is also recorded: when the provider first responded, when the first visible event arrived, and how long the whole round took.

#### Function details

##### `TextFilter.feed`  (lines 23–23)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This is the expected shape of a text filter. A filter receives new text as it streams in and returns only the part that is safe to publish live.

**Data flow**: A chunk of newly buffered model text goes in. The filter may remove or hold back forbidden text. The safe visible text comes out as a string.

**Call relations**: The runner does not implement filtering itself. `_RoundState.flush` calls this method just before publishing text, so every live text update passes through the caller’s chosen safety filter.


##### `ModelStreamInterrupted.__init__`  (lines 34–36)

```
def __init__(self, kind: str, message: str) -> None
```

**Purpose**: This creates a special error for a model stream that died midway because of a temporary provider or transport problem. The `kind` label tells the rest of the system what sort of interruption happened.

**Data flow**: An interruption kind and human-readable message go in. The message becomes the normal exception text, and the kind is stored on the exception for later retry decisions.

**Call relations**: Model clients raise this error when a stream is cut off or otherwise cannot be trusted as complete. `_RoundState.result` recognizes it and records `error_kind`, which tells the engine that this partial round should be discarded and may be retried.

*Call graph*: called by 9 (status, transport, _complete_chat, _complete_responses, status, transport, _generation_usage, complete, stream_error).


##### `ModelRoundRunner.run`  (lines 97–128)

```
async def run(self, request: RequestT, text_filter: TextFilter) -> CollectedRound[ToolCallT, ReasoningT, UsageT]
```

**Purpose**: This runs exactly one streamed model request from start to finish. It publishes live text while also collecting the final text, tool calls, reasoning, usage, errors, and timing.

**Data flow**: A model request and a text filter go in. The method starts timing, creates a `_RoundState`, starts a small background pacing task for regular text flushes, then reads every event from the provider stream. At the end, or after an error, it flushes any remaining text and returns a `CollectedRound` summarizing the round.

**Call relations**: This is the public entry point for the file’s work. It creates `_RoundState`, passes each provider event to `_RoundState.accept`, relies on the nested `pace` task to keep live text moving, and finally asks `_RoundState.result` to produce the round record.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (Event, create_task).


##### `ModelRoundRunner.run.pace`  (lines 106–111)

```
async def pace() -> None
```

**Purpose**: This background helper prevents live text from sitting in the buffer too long. It wakes up on a short timer and asks the round state to publish whatever text is ready.

**Data flow**: It reads the shared stop signal and the runner’s flush interval. Until the round is done, it waits for either the stop signal or the timeout. On each timeout, it calls `state.flush`; when stopped, it exits without returning a value.

**Call relations**: It is created inside `ModelRoundRunner.run` and runs alongside the provider stream reader. Its job is to make streaming feel responsive even when text chunks are small and do not reach the byte-based flush limit.

*Call graph*: 1 external calls (wait_for).


##### `_RoundState.__init__`  (lines 132–151)

```
def __init__(self, runner: ModelRoundRunner[RequestT, ToolCallT, ReasoningT, UsageT], text_filter: TextFilter, started: float) -> None
```

**Purpose**: This sets up the scratch space for one model round. It prepares places to store text, buffered live output, tool-call fragments, reasoning events, usage records, and timing markers.

**Data flow**: The runner, text filter, and start time go in. The constructor creates empty lists and dictionaries for incoming events, initializes counters and timing fields, and creates a lock so two flushes cannot publish the same buffer at once.

**Call relations**: `ModelRoundRunner.run` creates one `_RoundState` at the beginning of a round. All later event processing, flushing, and result building happen through this state object.

*Call graph*: called by 1 (run); 1 external calls (Lock).


##### `_RoundState.accept`  (lines 153–187)

```
async def accept(self, event: object) -> None
```

**Purpose**: This sorts each incoming provider event into the right bucket. It recognizes stream starts, text, tool-call starts and fragments, reasoning records, and usage records.

**Data flow**: One raw event goes in. If it is text, the text is saved for the final answer and buffered for live publishing. If it starts a tool call, the tool’s name and order are recorded. If it carries tool JSON, that fragment is appended. Reasoning and usage events are saved. Unknown event types cause an error, because the runner cannot safely guess what they mean.

**Call relations**: `ModelRoundRunner.run` calls this for every event from the provider. It calls `_mark_visible` when the model first produces something user-visible, calls `_elapsed_ms` for timing, and calls `flush` when the buffered text grows large enough.

*Call graph*: calls 3 internal fn (_elapsed_ms, _mark_visible, flush); 1 external calls (cast).


##### `_RoundState.flush`  (lines 189–199)

```
async def flush(self) -> None
```

**Purpose**: This publishes buffered text to the live stream after filtering it. It is what turns accumulated text chunks into visible output for the user.

**Data flow**: It reads the current text buffer. If the buffer is empty, nothing happens. Otherwise, it joins the buffered chunks, passes them through the `TextFilter`, clears the buffer and byte count, and sends any safe visible text to the runner’s `publish_text` callback. If publishing returns something awaitable, it waits for it to finish.

**Call relations**: `_RoundState.accept` calls this when enough text has accumulated, and the `pace` helper calls it on a timer. The flush lock makes those two paths cooperate, like a single cashier line, so they do not publish overlapping text.

*Call graph*: called by 1 (accept); 1 external calls (isawaitable).


##### `_RoundState.result`  (lines 201–244)

```
def result(self, error: Exception | None, wall_ms: int) -> CollectedRound[ToolCallT, ReasoningT, UsageT]
```

**Purpose**: This converts the scratch state of a round into the final `CollectedRound` record. It also decides how to represent a failed or interrupted stream.

**Data flow**: An optional error and the total elapsed time go in. If there was an error, it returns a record with usage seen so far, error details, and partial output made from text plus any tool-call fragments. If there was no error, it requires at least one usage record, parses completed tool-call JSON, builds tool-call objects, and returns the complete text, tool calls, reasoning, usage, and timing.

**Call relations**: `ModelRoundRunner.run` calls this once after the stream and final flush are done. It uses the runner’s `new_tool_call` callback to turn collected tool-call pieces into the caller’s own tool-call type, and it treats `ModelStreamInterrupted` specially by preserving its interruption kind.

*Call graph*: 3 external calls (__init__, loads, cast).


##### `_RoundState._mark_visible`  (lines 246–251)

```
def _mark_visible(self) -> None
```

**Purpose**: This records the first moment when the model produced something visible, such as text or a tool-call start. It only records that milestone once.

**Data flow**: It reads the current timing state. If the first visible time is already set, it does nothing. Otherwise, it calculates elapsed milliseconds, stores them, and optionally notifies the runner’s milestone callback.

**Call relations**: `_RoundState.accept` calls this when it sees the first visible event. It uses `_elapsed_ms` to measure the time from the start of the round and lets outside instrumentation know that visible output has begun.

*Call graph*: calls 1 internal fn (_elapsed_ms); called by 1 (accept).


##### `_RoundState._elapsed_ms`  (lines 253–254)

```
def _elapsed_ms(self) -> int
```

**Purpose**: This measures how many milliseconds have passed since the round began. It gives the rest of the file a consistent way to record timing.

**Data flow**: It reads the runner’s clock and the stored start time. It subtracts the start time from the current time, converts seconds to milliseconds, and returns the integer result.

**Call relations**: `_RoundState.accept` uses this for the provider-start timestamp, and `_mark_visible` uses it for the first-visible-event timestamp. These timings are later included in the `CollectedRound`.

*Call graph*: called by 2 (_mark_visible, accept).
