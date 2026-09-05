# Model request, stream handling, and intent runs  `stage-9.2`

This stage is part of the main conversation loop, when the system asks an AI model for the next response and listens as the answer comes back piece by piece. It turns the provider’s live stream into safe, structured records the rest of UFO can use, including visible text, hidden reasoning notes, tool requests, usage counts, timing, and retry information if something goes wrong.

The replies.py file is like a filter at the front of the speaker. Models may include special “reply-to” sections meant for internal routing, not for the user. This file detects those sections and removes their hidden markup even while text is still streaming, so users only see the clean message.

The rounds.py file manages one complete model “round”: send the request, read the stream until it ends, publish safe text as it arrives, and gather all final results. Together, these files make model output usable, private parts controlled, and each response ready to become part of the transcript.

## Files in this stage

### Streamed model response handling
Recognizes hidden reply markup, filters streamed text safely, and runs a complete streamed model response while collecting transcript, tool, reasoning, usage, timing, and retry metadata.

### `core/src/ufo/harness/replies.py`

`domain_logic` · `during live response streaming and after a model round completes`

The model can mark part of its output as a direct reply to a particular message, using tags like `<reply-to message="..."></reply-to>`. Those tags are instructions for the system, not words a person should see. This file is the gatekeeper that separates the two.

After a model turn is complete, `marked_replies` scans the full text, finds every properly closed reply section, and returns two things: the replies that should be delivered to members, and the same full text with the markup removed. The actual reply words stay in the saved text, so the record still shows what was said.

While text is streaming live, the harder job belongs to `ReplyRedaction`. Streaming text arrives in chunks, and a tag can be split across chunks. This is like reading a torn note one scrap at a time: if a scrap ends with “<rep”, the system must wait before deciding whether it is normal text or the start of hidden markup. `ReplyRedaction.feed` holds back anything uncertain, hides complete reply spans, and only publishes text once it is safe.

Malformed or incomplete markup is deliberately not shown to members. A reply span is only delivered if the model clearly closes it.

#### Function details

##### `marked_replies`  (lines 42–51)

```
def marked_replies(text: str) -> tuple[tuple[MarkedReply, ...], str]
```

**Purpose**: This reads a completed model response and extracts the reply sections addressed to members. It also returns the response text with the special reply tags removed, so saved conversation text does not include system-only markup.

**Data flow**: It takes the full text of a finished round. It searches for complete `<reply-to ...>` sections, removes any reply markup inside each section, trims the spoken text, and turns non-empty sections into `MarkedReply` records. It returns the collected replies, plus a cleaned version of the original text with all reply markup removed.

**Call relations**: This is used after the model has finished speaking, when the system can safely inspect the whole response. For each reply span it finds, it asks `_named_message` to turn the tag’s message value into a real message identifier when possible, then packages the result as a `MarkedReply`.

*Call graph*: calls 1 internal fn (_named_message); 1 external calls (__init__).


##### `_named_message`  (lines 54–58)

```
def _named_message(named: str) -> UUID | None
```

**Purpose**: This small helper checks whether the message name written in a reply tag is a valid message identifier. If it is valid, it returns that identifier; if not, it marks it as unknown by returning nothing.

**Data flow**: It takes the raw string from the tag’s `message` field. It trims extra space and tries to read it as a UUID, which is a standard unique identifier format. If that succeeds, the UUID comes out; if it fails, the output is `None`.

**Call relations**: `marked_replies` calls this while turning reply tags into `MarkedReply` objects. This keeps the parsing decision in one place, so a bad message reference does not crash reply extraction.

*Call graph*: called by 1 (marked_replies); 1 external calls (UUID).


##### `ReplyRedaction.feed`  (lines 77–99)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This processes one new chunk of live model output and returns only the text that is safe to show immediately. It hides reply spans and their tags so users do not see internal markup or duplicate reply text in the live stream.

**Data flow**: It receives a new piece of streamed text and adds it to any text already being held back. If it is inside a reply span, it waits until it sees the closing tag and discards the hidden span. If it is outside a span, it publishes ordinary text before an opener, starts withholding when it finds an opener, and keeps uncertain trailing text if it might become a tag in the next chunk. The returned value is cleaned of reply markup, while the object’s stored state remembers what is still undecided.

**Call relations**: The live streaming path calls this repeatedly as chunks arrive. It relies on `_growing_suffix` when waiting for the rest of a possible closing tag, and on `_settled_chars` to decide how much outside text is definitely ordinary text and can be published now.

*Call graph*: calls 2 internal fn (_growing_suffix, _settled_chars); 1 external calls (find).


##### `_growing_suffix`  (lines 102–107)

```
def _growing_suffix(text: str, token: str) -> str
```

**Purpose**: This finds the part at the end of some text that could still become a known token if more characters arrive. It is used to avoid accidentally publishing half of a reply closing tag.

**Data flow**: It takes a piece of text and a target token, such as the reply closing tag. It checks the tail end of the text for the longest suffix that matches the beginning of that token. It returns that possible unfinished token fragment, or an empty string if nothing at the end could grow into the token.

**Call relations**: `ReplyRedaction.feed` uses this while it is already hiding a reply span. If a streamed chunk does not yet contain the full closing tag, this helper lets the redactor keep only the small tail that might become the closer and discard the rest of the hidden reply text.

*Call graph*: called by 1 (feed).


##### `_settled_chars`  (lines 110–119)

```
def _settled_chars(text: str) -> int
```

**Purpose**: This decides how many characters are safe to publish when the stream is not currently inside a hidden reply. Its main job is to pause at a trailing `<` or partial tag that might turn into reply markup in the next chunk.

**Data flow**: It takes the currently held text and looks for the last `<` character. If there is no `<`, all text is safe. If the tail could be the beginning of a reply opener or closer, it returns the position before that tail so it can be held back. If the tail is clearly normal text, it returns the full length so everything can be published.

**Call relations**: `ReplyRedaction.feed` calls this after it has not found a complete opener in the held text. The result tells the redactor where to split the buffer: publish the settled beginning now, and keep the uncertain ending for the next streamed chunk.

*Call graph*: called by 1 (feed).


### `core/src/ufo/harness/rounds.py`

`domain_logic` · `request handling`

A model answer does not usually arrive all at once. It comes as a stream of small events: text fragments, tool-call fragments, reasoning records, usage bills, and sometimes errors. This file is the harness piece that turns that messy live stream into one clean “round” result.

The main object, ModelRoundRunner, is given a model streaming function, the event classes that provider uses, a way to build final tool-call objects, and a way to publish text to the live user interface. As events arrive, _RoundState sorts them into buckets. Text is collected for the final answer, but it is also passed through a TextFilter before being shown live, so hidden or unsafe text can be removed before the user sees it. Text is flushed either after enough bytes build up or after a short timer, like a faucet that releases water regularly instead of one drop at a time.

The file also records useful timing milestones, such as when the provider first started and when the first visible output appeared. If the stream fails, it does not pretend the round succeeded. Instead it returns a CollectedRound with error details and any partial output, so the caller can decide whether to retry or fail the turn.

#### Function details

##### `TextFilter.feed`  (lines 23–23)

```
def feed(self, chunk: str) -> str
```

**Purpose**: This is the expected shape of a text filter used during live streaming. A filter receives new text as it arrives and returns only the part that is safe or allowed to show to the user.

**Data flow**: Input is a new chunk of model text. The filter may keep some internal memory from earlier chunks, removes or delays text that should not be shown, and returns the visible text that can be published now.

**Call relations**: _RoundState.flush calls this whenever buffered text is ready to be shown. The runner does not know how filtering works; it only relies on this method to act like a safety screen between the raw model stream and the live output.


##### `ModelStreamInterrupted.__init__`  (lines 34–36)

```
def __init__(self, kind: str, message: str) -> None
```

**Purpose**: This creates an error that means the model stream stopped in a temporary, retryable way. It keeps a short machine-readable kind, so the caller can record what sort of interruption happened.

**Data flow**: Input is an interruption kind and a human-readable message. The message becomes the normal exception text, and the kind is stored on the exception for later reporting and retry decisions.

**Call relations**: Model provider clients raise this when a connection drops, a provider sends an error frame, or usage tracking fails mid-stream. _RoundState.result recognizes this specific error and writes its kind into the collected round so the higher-level engine can discard and rerun the round if appropriate.

*Call graph*: called by 9 (status, transport, _complete_chat, _complete_responses, status, transport, _generation_usage, complete, stream_error).


##### `ModelRetryAfter.__init__`  (lines 42–44)

```
def __init__(self, seconds: float) -> None
```

**Purpose**: This creates an error for the case where a model provider says, in effect, “try again after this many seconds.” It preserves the requested delay in a structured way.

**Data flow**: Input is a number of seconds. The exception stores that number both as its message value and as a seconds field that callers can read directly.

**Call relations**: Provider retry code raises this when it receives a rate-limit or retry-after response. _RoundState.result notices this error type and copies the delay into the collected round, giving the caller enough information to wait before retrying.

*Call graph*: called by 1 (status).


##### `ModelRoundRunner.run`  (lines 106–137)

```
async def run(self, request: RequestT, text_filter: TextFilter) -> CollectedRound[ToolCallT, ReasoningT, UsageT]
```

**Purpose**: This runs exactly one streamed model round. It consumes provider events, publishes filtered live text, gathers the final result, and records errors without losing useful partial information.

**Data flow**: Input is a model request and a text filter. The method creates a fresh _RoundState, starts a small background pacing task for timed text flushes, reads each provider event from the model stream, and sends each event into the state object. At the end it flushes any remaining text, measures total time, and returns a CollectedRound containing either a successful answer or error details.

**Call relations**: Higher-level model orchestration calls this when it needs one model generation. Inside, it creates _RoundState to do the event-by-event bookkeeping, starts the nested pace task to keep live output moving, and finally asks the state object to build the result.

*Call graph*: calls 1 internal fn (__init__); 2 external calls (Event, create_task).


##### `ModelRoundRunner.run.pace`  (lines 115–120)

```
async def pace() -> None
```

**Purpose**: This small background task makes sure live text is not held back too long. Even if only a little text has arrived, it periodically asks the state to flush what it can.

**Data flow**: It watches a stop signal. Until that signal is set, it waits for the configured flush interval; whenever the wait times out, it calls the state’s flush method. When the main run ends, the stop signal lets this task finish.

**Call relations**: ModelRoundRunner.run starts this task before reading the provider stream and stops it in the cleanup path. It works alongside _RoundState.accept, which can also trigger a flush when enough text has accumulated.

*Call graph*: 1 external calls (wait_for).


##### `_RoundState.__init__`  (lines 141–160)

```
def __init__(self, runner: ModelRoundRunner[RequestT, ToolCallT, ReasoningT, UsageT], text_filter: TextFilter, started: float) -> None
```

**Purpose**: This sets up the temporary notebook used while one model stream is being consumed. It prepares places to store text, tool-call fragments, reasoning events, usage records, timing data, and buffered live text.

**Data flow**: Input is the runner configuration, the text filter, and the start time. The constructor stores those and initializes empty lists and dictionaries for everything that may arrive during the stream. It also creates a lock, which is a small guard that stops two flushes from publishing the same buffered text at once.

**Call relations**: ModelRoundRunner.run creates one _RoundState at the beginning of each round. All later event processing, flushing, timing, and final result assembly happens through this state object.

*Call graph*: called by 1 (run); 1 external calls (Lock).


##### `_RoundState.accept`  (lines 162–196)

```
async def accept(self, event: object) -> None
```

**Purpose**: This receives one event from the model stream and files it in the right place. It is the sorter that understands whether an event is text, a tool call, reasoning, usage, or the start of the provider stream.

**Data flow**: Input is one raw stream event. If it is a stream-start event, timing is recorded. If it is text, the text is added to the final answer and the live-output buffer. If it starts or extends a tool call, the tool-call pieces are stored by call id. If it is reasoning or usage, it is appended to those collections. It may also trigger a flush when enough text has built up.

**Call relations**: ModelRoundRunner.run calls this for every event produced by the provider. It hands off to _mark_visible when something user-visible first appears, to _elapsed_ms for timing, and to flush when the live text buffer grows large enough.

*Call graph*: calls 3 internal fn (_elapsed_ms, _mark_visible, flush); 1 external calls (cast).


##### `_RoundState.flush`  (lines 198–208)

```
async def flush(self) -> None
```

**Purpose**: This publishes any buffered text that is ready to be shown live. Before publishing, it runs the text through the filter so restricted text does not reach the visible stream.

**Data flow**: It reads the current text buffer. If the buffer is empty, nothing changes. Otherwise it joins the buffered chunks, passes them into the TextFilter, clears the buffer and byte count, and sends any returned visible text to the runner’s publish_text function. If publishing returns something awaitable, meaning it needs asynchronous waiting, flush waits for it to finish.

**Call relations**: _RoundState.accept calls this when enough text accumulates, and the pacing task inside ModelRoundRunner.run calls it on a timer. The flush lock ensures those two paths do not publish the same buffered text at the same time.

*Call graph*: called by 1 (accept); 1 external calls (isawaitable).


##### `_RoundState.result`  (lines 210–254)

```
def result(self, error: Exception | None, wall_ms: int) -> CollectedRound[ToolCallT, ReasoningT, UsageT]
```

**Purpose**: This turns the temporary stream state into the final CollectedRound. It either builds a successful round with text, tool calls, reasoning, and usage, or an error round with partial output and failure details.

**Data flow**: Input is an optional error and the total elapsed time. If there was an error, it gathers any text and partial tool-call JSON into a partial_output field and records the error class, message, retry kind, or retry-after delay. If there was no error, it requires at least one usage record, parses each completed tool-call JSON string into arguments, builds final tool-call objects, and returns the completed round.

**Call relations**: ModelRoundRunner.run calls this after the stream and final flush are done. It uses json.loads to turn accumulated tool-call text into structured data, and it uses the runner’s new_tool_call callback so provider-specific callers can decide what a finished tool call should look like.

*Call graph*: 3 external calls (__init__, loads, cast).


##### `_RoundState._mark_visible`  (lines 256–261)

```
def _mark_visible(self) -> None
```

**Purpose**: This records the moment when the round first produced something visible to the user. It also optionally reports that milestone to outside monitoring code.

**Data flow**: It checks whether the first-visible timestamp has already been set. If not, it calculates elapsed milliseconds since the round started, stores that value, and calls the runner’s milestone callback with the name of the event if such a callback was provided.

**Call relations**: _RoundState.accept calls this when text arrives or a tool call starts, because both count as visible progress. It uses _elapsed_ms to calculate the timing in the same way as other round measurements.

*Call graph*: calls 1 internal fn (_elapsed_ms); called by 1 (accept).


##### `_RoundState._elapsed_ms`  (lines 263–264)

```
def _elapsed_ms(self) -> int
```

**Purpose**: This calculates how many milliseconds have passed since the round began. It provides a consistent clock reading for the timing fields stored in the final result.

**Data flow**: It reads the runner’s monotonic clock, subtracts the saved start time, converts the difference from seconds to milliseconds, and returns the integer value.

**Call relations**: _RoundState.accept uses this when the provider stream starts, and _mark_visible uses it when the first visible event appears. Keeping this calculation in one place makes all round timing measurements use the same clock and format.

*Call graph*: called by 2 (_mark_visible, accept).
