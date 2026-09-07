# Conversation state, contracts, and compaction  `stage-10.1`

This stage is shared behind-the-scenes support for each conversation turn. Before the model is called, it checks what conversation history can be shown. During and after the turn, it records what happened in a safe, consistent form.

The compaction pieces keep long chats from overflowing the model’s “context window,” meaning the limited amount of text the model can read at once. harness/context.py decides when shrinking is needed. runtime/compaction.py keeps recent messages as they are, replaces older ones with a verified summary, and stores both versions for review. runtime/transcript.py safely writes the transcript to shared storage without letting stale copies overwrite newer ones. turns/transcript.py defines the common saved record shapes so every reader and writer agrees.

The turns files add rules around those records. contracts.py checks data passed between agents, whether it comes from built-in models or user JSON rules. delivery_register.py defines how agents should write replies and how long they may be. activity.py turns raw tool calls into friendly status labels. audience.py and subjects.py define who a turn is for, so private, shared, and room-visible content stay separate.

## Files in this stage

### Compaction and transcript writes
These files decide when conversation history must be shortened, perform the compaction, and protect stored transcripts from unsafe overwrites.

### `core/src/ufo/runtime/compaction.py`

`domain_logic` · `request handling, when a conversation nears or exceeds the model context window`

As a conversation grows, it can become too large for the model to read. Without this file, long runs would eventually fail with a context overflow, or the system would have to throw away history without proof that important facts survived. This file provides a careful compaction pipeline: it chooses an older “head” of the transcript to summarize, keeps the recent “tail” exactly as it was, asks the model for a JSON summary, checks that important anchor facts still appear, then writes the before, after, and summary records to storage.

Think of it like packing a suitcase. The newest items stay on top untouched. Older bulky items are folded into a labeled packing cube, but the system checks that passports, tickets, and keys did not disappear.

The main class, Compaction, decides when compaction should run, prepares the transcript for summarizing, validates the model's summary, renders that summary into a replacement user message, and records metrics about whether anything was lost. It also preserves durable references such as saved tool-output files, loaded skill names, active requests, and error names. If the first summary drops important anchors, the pipeline can ask the model to try again with explicit correction instructions. Storage uses compressed JSON blobs, so investigators and evaluation tools can later compare what was replaced with what replaced it.

#### Function details

##### `harvest_anchors`  (lines 104–130)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: Finds important literal facts in the old transcript that the replacement summary must not lose. These include saved tool-output paths, named errors, loaded skills, and still-active request references.

**Data flow**: It receives the rendered text of the transcript head, the names of loaded skills, and active request text. It scans them with fixed patterns, removes duplicates, keeps only a bounded recent set for each kind, and returns Anchor objects that can later be checked against the compacted conversation.

**Call relations**: When Compaction._compact.open_boundary fixes the part of the transcript that will be summarized, it calls this function to create the checklist used later by Compaction._verify.

*Call graph*: called by 1 (open_boundary); 1 external calls (__init__).


##### `missing_anchors`  (lines 133–137)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: Checks which required anchor facts are absent from the text that will remain after compaction.

**Data flow**: It receives a tuple of anchors and a single carried-forward text string. It tests whether each anchor's exact literal text appears in that string and returns only the anchors that are missing.

**Call relations**: Compaction._verify calls this after rendering the candidate replacement window, so the system can decide whether a summary kept or lost required facts.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 176–180)

```
def __repr__(self) -> str
```

**Purpose**: Produces a short, safe debug description of a compaction request without printing the whole conversation.

**Data flow**: It reads the request's message count, reason, and active request count. It returns a compact string containing those counts rather than the full message contents.

**Call relations**: This supports logging and debugging around requests passed from Compaction.maybe_compact into Compaction._compact.


##### `_InvalidSummary.__init__`  (lines 184–187)

```
def __init__(self, error_class: str, message: str, usage: Usage) -> None
```

**Purpose**: Creates a special error for cases where the model's summary could not be accepted, while keeping the model usage information that was spent.

**Data flow**: It receives an error class name, a readable message, and a Usage record. It stores the class and usage on the exception and passes the details to the base RuntimeError.

**Call relations**: Compaction._summarize_once raises this when the summarizer returned text but Compaction._parse_summary could not turn it into a valid summary.

*Call graph*: called by 1 (_summarize_once).


##### `_InvalidSummary.__str__`  (lines 189–190)

```
def __str__(self) -> str
```

**Purpose**: Returns only the human-readable summary failure message when the exception is printed.

**Data flow**: It reads the message stored in the exception arguments and returns that message as text.

**Call relations**: This makes errors raised by Compaction._summarize_once easier to read when Compaction._compact logs or re-raises them.


##### `Compaction.window`  (lines 226–250)

```
def window(self) -> ContextWindow[Message]
```

**Purpose**: Builds the current token-window calculator for the model being used right now. This calculator decides how large the conversation is and where it can be safely split.

**Data flow**: It reads the serving model's limits and compaction settings, plus this class's text, image, and hidden-reasoning counters. It returns a ContextWindow object configured with those rules.

**Call relations**: Compaction.maybe_compact, Compaction._compact, Compaction._verify, and related helpers rely on this property whenever they need current token counts or compaction thresholds.

*Call graph*: 1 external calls (__init__).


##### `Compaction.__repr__`  (lines 252–253)

```
def __repr__(self) -> str
```

**Purpose**: Gives a short debug label for the compaction object, naming the conversation and model.

**Data flow**: It reads the conversation ID and serving model name. It returns a simple string for logs or debugging.

**Call relations**: This helps developers identify which Compaction instance is being inspected without exposing transcript content.


##### `Compaction.maybe_compact`  (lines 255–299)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether a transcript should be compacted now, then either returns it unchanged or runs the full compaction process. It is the normal public entry point for this file's workflow.

**Data flow**: It receives the current messages, a force flag, and active requests. It checks normal token limits and a repeated-tool-use trigger, builds a _CompactionRequest when compaction is needed, awaits Compaction._compact, and returns the resulting messages plus any model usage records.

**Call relations**: Callers use this before sending a conversation to the model. It consults Compaction._repeated_tool_policy and Compaction._repeated_tool_trigger, then hands real work to Compaction._compact; if automatic compaction failed harmlessly once, it suppresses more automatic attempts for the turn.

*Call graph*: calls 3 internal fn (_compact, _repeated_tool_policy, _repeated_tool_trigger); 2 external calls (__init__, log).


##### `Compaction._repeated_tool_trigger`  (lines 301–333)

```
def _repeated_tool_trigger(self, messages: tuple[Message, ...]) -> int | None
```

**Purpose**: Detects a loop-like pattern where the assistant repeatedly calls the same tools with the same inputs across recent turns. This lets the system compact earlier than usual when repetition is filling the context.

**Data flow**: It receives the message list, reads the configured repeated-tool policy, walks backward through recent messages, and compares tool name plus input pairs. If enough consecutive turns repeat, it returns a lower token trigger; otherwise it returns None.

**Call relations**: Compaction.maybe_compact uses this to decide whether to compact before the normal window limit. Compaction._compact.checkpoint uses it again to label why a completed compaction happened.

*Call graph*: calls 1 internal fn (_repeated_tool_policy); called by 2 (checkpoint, maybe_compact); 1 external calls (dumps).


##### `Compaction._repeated_tool_policy`  (lines 335–336)

```
def _repeated_tool_policy(self) -> RepeatedToolCompaction | None
```

**Purpose**: Fetches the model-specific settings for repeated-tool compaction, if that feature is enabled.

**Data flow**: It reads the serving model specification and returns its repeated_tool_compaction policy or None.

**Call relations**: Compaction.maybe_compact and Compaction._repeated_tool_trigger call this so they both follow the same model configuration.

*Call graph*: called by 2 (_repeated_tool_trigger, maybe_compact).


##### `Compaction._compact`  (lines 339–468)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction workflow: choose the boundary, summarize the old head, verify the replacement, save records, and fire hooks. It is wrapped as a DBOS step, meaning crash recovery can replay the recorded result instead of repeating external work.

**Data flow**: It receives a _CompactionRequest. It sets up callbacks for the harness, drains loaded skills into the boundary, asks the summarizer for candidates, verifies them, persists successful before/after/summary records, and returns the final message window plus usage records. If an automatic summary is invalid, it logs the problem and leaves messages unchanged; forced recovery errors are allowed to surface.

**Call relations**: Compaction.maybe_compact calls this when compaction is needed. Inside, it delegates window splitting and retry control to CompactionHarness, while its nested helpers open boundaries, handle failed retries, and checkpoint successful candidates.

*Call graph*: called by 1 (maybe_compact); 1 external calls (log_error).


##### `Compaction._compact.open_boundary`  (lines 359–386)

```
async def open_boundary(head: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...], before_tokens: int) -> _Boundary
```

**Purpose**: Creates the fixed facts for one compaction boundary before any model summary is trusted. This is the contract the candidate summary will be judged against.

**Data flow**: It receives the transcript head rounds, the kept tail, and the original token count. It chooses the next storage index, fires the pre-compaction hook, drains loaded skills, gathers durable references, renders text for checking, harvests anchors, and returns a _Boundary object.

**Call relations**: CompactionHarness calls this from inside Compaction._compact after it has selected a head and tail. Later, Compaction._verify uses the returned boundary to grade each summary attempt.

*Call graph*: calls 4 internal fn (_next_index, _references, _window_text, harvest_anchors); 3 external calls (__init__, __init__, from_iterable).


##### `Compaction._compact.retry_failed`  (lines 388–395)

```
def retry_failed(candidate: _Candidate, error: Exception) -> _Candidate
```

**Purpose**: Marks a candidate as having gone through a failed anchor-retry attempt while preserving the candidate as the best available fallback.

**Data flow**: It receives a candidate and the exception from the retry. It logs a warning, updates the candidate's verification to say a retry happened, and returns the updated candidate.

**Call relations**: CompactionHarness uses this callback inside Compaction._compact when a retry meant to restore missing anchors itself fails.

*Call graph*: 2 external calls (replace, warn).


##### `Compaction._compact.checkpoint`  (lines 397–427)

```
async def checkpoint(candidate: _Candidate, boundary: _Boundary) -> None
```

**Purpose**: Commits a verified compaction result: saves it, records its quality, and notifies observers that compaction finished.

**Data flow**: It receives a candidate and its boundary. It writes before/after/summary blobs, decides whether the trigger was force, repeated-tool, or normal window pressure, records verification logs and metrics, fires the post-compaction hook, and returns nothing.

**Call relations**: CompactionHarness calls this from inside Compaction._compact only after a candidate has passed the harness's rules for installation.

*Call graph*: calls 3 internal fn (_persist, _record_verification, _repeated_tool_trigger); 1 external calls (__init__).


##### `Compaction._summarize_once`  (lines 470–498)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Makes one model call to turn transcript head text into a typed compaction summary.

**Data flow**: It receives transcript rounds and any anchors missed by a previous attempt. It renders the prompt with Compaction._prepare, sends a ModelRequest to the serving model, collects streamed text and usage, parses the text with Compaction._parse_summary, and returns the summary plus usage. If parsing fails, it raises _InvalidSummary with the usage attached.

**Call relations**: CompactionHarness calls this as the summarize callback inside Compaction._compact. It hands prompt building to Compaction._prepare and validation to Compaction._parse_summary.

*Call graph*: calls 3 internal fn (_parse_summary, _prepare, __init__); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 500–522)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: Builds the exact text sent to the summarizer model. It turns structured messages into readable transcript text and adds retry instructions when important anchors were previously missed.

**Data flow**: It receives grouped transcript rounds and missed anchors. It renders each message using Compaction._text, folds long repeated text runs with Compaction._fold_repeated_runs, optionally appends a bullet list of missing anchor literals, and ends with a strict reminder to return one JSON object.

**Call relations**: Compaction._summarize_once calls this before making the model request. It relies on Compaction._text for message rendering and Compaction._bullets for the correction list.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 524–537)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Compresses huge verbatim repetitions in the text sent to the summarizer. This avoids wasting the summarizer's input window on repeated spam or tool-loop output.

**Data flow**: It receives a text string. It uses a regular expression to find a short word sequence repeated many times in a row and replaces the run with one copy plus a marker saying how many times it repeated.

**Call relations**: Compaction._prepare calls this after rendering the transcript head, so the summarization prompt stays smaller while preserving the fact that repetition happened.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 532–535)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement text for one detected repeated run.

**Data flow**: It receives one regex match, extracts the repeated unit, estimates how many times it appeared, and returns the unit followed by a repeated-count marker.

**Call relations**: This is the small callback used by Compaction._fold_repeated_runs when applying the regular expression substitution.


##### `Compaction._parse_summary`  (lines 539–579)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Turns the summarizer's raw text into a validated CompactionSummary object. It is deliberately tolerant of extra text around the JSON but strict about the final schema.

**Data flow**: It receives raw model output. It finds the first balanced JSON object, validates it as a CompactionSummary, rejects schema errors and empty intent fields, and returns the typed summary. Failures become RuntimeError messages that callers can classify.

**Call relations**: Compaction._summarize_once calls this after collecting model output. If it fails, Compaction._summarize_once wraps the failure in _InvalidSummary so Compaction._compact can decide what to do.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 581–598)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds saved tool-output file paths from the summarized head that should remain available after compaction.

**Data flow**: It receives the head rounds and the kept tail. It scans both as text, ignores paths already visible in the tail, keeps unique paths found in the head, limits them to the most recent few, and returns them.

**Call relations**: Compaction._compact.open_boundary calls this while building the boundary. The returned references are later rendered by Compaction._render outside the model-authored summary.

*Call graph*: calls 1 internal fn (_text); called by 1 (open_boundary).


##### `Compaction._render`  (lines 600–639)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns a validated CompactionSummary plus preserved references and active requests into the single user message that replaces the old transcript head.

**Data flow**: It receives the summary, durable reference paths, and active request strings. It creates human-readable sections only for fields that have content, adds references and active requests verbatim, prefixes the whole thing as compacted context, and returns the rendered string.

**Call relations**: Compaction._verify calls this while building a candidate replacement. The rendered text is also what post-compaction hooks observe through Compaction._compact.checkpoint.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_verify).


##### `Compaction._bullets`  (lines 641–642)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a tuple of strings as a simple Markdown-style bullet list.

**Data flow**: It receives items and returns one string where each item is on its own line prefixed with '- '.

**Call relations**: Compaction._prepare uses this for retry correction anchors, and Compaction._render uses it for summary sections and durable references.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 644–645)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: Renders several messages into one plain text block for searching and verification.

**Data flow**: It receives messages, renders each with Compaction._text, joins them with newlines, and returns the combined text.

**Call relations**: Compaction._compact.open_boundary uses this to capture text for anchor harvesting and path checks. Compaction._verify uses it to check whether anchors survived in the replacement window.

*Call graph*: calls 1 internal fn (_text); called by 2 (open_boundary, _verify).


##### `Compaction._verify`  (lines 647–683)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: Grades one proposed summary before it is allowed to replace the transcript head. It removes invented file paths, renders the replacement, counts tokens, and checks for missing anchors.

**Data flow**: It receives a summary, a fixed boundary, and a flag saying whether this is a retry. It keeps only summary file references that appeared in the original window, inserts drained loaded skills, renders the compacted message, combines it with the tail, builds a CompactionVerification record, and returns a _Candidate containing the checked summary and replacement messages.

**Call relations**: CompactionHarness calls this inside Compaction._compact after each summarize attempt. It depends on Compaction._render, Compaction._window_text, and missing_anchors to decide what the candidate carries forward.

*Call graph*: calls 3 internal fn (_render, _window_text, missing_anchors); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._record_verification`  (lines 685–716)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], trigger: Literal['force', 'repeated_tool', 'window'], verification: CompactionVerification) -> None
```

**Purpose**: Writes an observable record of how good the compaction was. This lets operators see whether compaction was clean, lossy, retried, and why it triggered.

**Data flow**: It receives the compaction index, reason, trigger label, and verification result. It logs token counts, missing anchors, dropped paths, and retry status, then emits a metric split by model, provider, outcome, retry state, and trigger.

**Call relations**: Compaction._compact.checkpoint calls this after persisting a candidate, so every installed compaction has a matching quality record.

*Call graph*: called by 1 (checkpoint); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 718–729)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Saves the complete compaction record to blob storage: the original window, the replacement window, and the typed summary.

**Data flow**: It receives an index, before messages, after messages, and the summary. It writes the before and after windows through Compaction._write, compresses the summary JSON, and stores it under the summary key.

**Call relations**: Compaction._compact.checkpoint calls this when a compaction is accepted. It relies on Compaction._key for stable storage paths.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (checkpoint); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 731–735)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next unused numbered slot for a conversation's compaction records.

**Data flow**: It starts at index 1 and checks blob storage for an existing 'after' record at each index. It returns the first index whose record does not exist.

**Call relations**: Compaction._compact.open_boundary calls this before saving a new compaction, ensuring records are ordered and not overwritten.

*Call graph*: calls 1 internal fn (_key); called by 1 (open_boundary).


##### `Compaction.read_record`  (lines 737–744)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads a saved compaction record back from storage, if it exists.

**Data flow**: It receives an index, tries to fetch the before, after, and summary blobs, and returns None if any are missing. If all are present, it decodes them into a CompactionRecord.

**Call relations**: This is the retrieval counterpart to Compaction._persist, useful for inspection, replay, or evaluation tools that need to compare the original and compacted windows.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 746–754)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes either the before or after message window as compressed JSON.

**Data flow**: It receives an index, a label saying before or after, and messages. It wraps the messages in a CompactionWindow, serializes them deterministically to JSON, compresses the bytes, and stores them in blob storage.

**Call relations**: Compaction._persist calls this twice for every accepted compaction, once for the original window and once for the replacement window.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 756–757)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the storage key for one part of one compaction record.

**Data flow**: It receives an index and a part name such as before, after, or summary. It combines those with the conversation ID through the shared compaction_key helper and returns the resulting blob path.

**Call relations**: Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record all call this so they agree on exactly where compaction data lives.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._opaque_chars`  (lines 759–778)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: Counts hidden reasoning data that affects request size even though it is not useful text for the summarizer. This prevents the window estimate from being too optimistic.

**Data flow**: It receives one message. If the content is plain text, it returns zero; otherwise it adds the lengths of signatures, redacted data, and encrypted reasoning content blocks and returns the total character count.

**Call relations**: Compaction.window gives this function to ContextWindow so token estimates include non-rendered reasoning payloads when deciding whether compaction is needed.


##### `Compaction._image_count`  (lines 780–790)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts images inside a message so the window calculator can estimate their token cost.

**Data flow**: It receives one message. It returns zero for plain text, otherwise counts direct image blocks and image parts inside tool results, then returns the total.

**Call relations**: Compaction.window gives this function to ContextWindow, which uses the count when estimating how much of the model's context window is being used.


##### `Compaction._text`  (lines 792–816)

```
def _text(self, message: Message) -> str
```

**Purpose**: Converts a structured message into plain text for summarizing, searching, and token estimation. Non-text items such as images or redacted reasoning become clear markers instead of silently disappearing.

**Data flow**: It receives one message. Plain string content is returned directly; structured blocks are rendered according to their type, including text, thinking summaries, image markers, tool results, and tool calls with JSON arguments. The pieces are joined with newlines and returned.

**Call relations**: Compaction._prepare uses this to build the summarizer prompt, Compaction._references uses it to find saved output paths, and Compaction._window_text uses it to build verification text.

*Call graph*: called by 3 (_prepare, _references, _window_text); 1 external calls (dumps).


### `core/src/ufo/harness/context.py`

`domain_logic` · `during conversation/request handling when transcript context may exceed the model limit`

AI models can only read a limited amount of text and image data at once. This file is the project’s safety mechanism for that limit. It treats the transcript as an opaque list of messages, meaning it does not need to know the project’s exact message class. Instead, callers give it small functions for reading each message’s role, text, hidden size cost, and image count.

The main idea is like packing a suitcase. The newest items stay because they are most useful right now. Older items are folded into a smaller summary. ContextWindow estimates how “full” the suitcase is, splits the transcript into whole conversation rounds, chooses the old part to summarize, and keeps a recent tail unchanged.

CompactionHarness runs the actual shrinking process. It asks an outside summarizer to summarize the old rounds, opens a boundary between the summary and the kept tail, verifies the summary, retries if required details are missing, checks that the new transcript is truly smaller or below the trigger, saves a checkpoint, and returns the replacement messages plus any usage records. It also has special recovery for a common failure: the summarization prompt itself may be too large, so it drops some oldest rounds and tries again.

#### Function details

##### `is_context_overflow`  (lines 14–17)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: Checks whether an error probably means an external AI model rejected a request because the request was too large. This lets the compaction process react differently to “too much context” than to unrelated failures.

**Data flow**: It receives an exception, turns the exception type and message into lowercase text, then looks for known phrases such as “too long” or “maximum context”. It returns true if one of those phrases appears, otherwise false. It does not change anything outside itself.

**Call relations**: CompactionHarness._summarize calls this after a summarization attempt fails. If this function says the failure was caused by context overflow, the harness can retry with less old transcript included; otherwise the original error is allowed to stop the process.

*Call graph*: called by 1 (_summarize).


##### `ContextWindow.trigger`  (lines 46–50)

```
def trigger(self) -> int
```

**Purpose**: Gives the token estimate at which automatic compaction should begin. A token is a rough chunk of text used by AI models for size limits.

**Data flow**: It reads the window’s settings. If an explicit trigger value was provided, it returns that. Otherwise it calculates the trigger by taking the model’s total context size and reserving space for the future summary and a safety buffer.

**Call relations**: This property is used by other ContextWindow decisions, especially when checking whether the transcript is too large and whether a compacted transcript has become small enough.


##### `ContextWindow.should_compact`  (lines 52–66)

```
def should_compact(self, messages: tuple[MessageT, ...], *, force: bool, automatic_suppressed: bool, automatic_trigger_tokens: int | None=None) -> bool
```

**Purpose**: Decides whether the current transcript should be compacted now. It prevents unnecessary summarizing when there are too few messages, when automatic compaction is suppressed, or when the transcript is still below the trigger.

**Data flow**: It receives the message list plus flags saying whether compaction is forced and whether automatic compaction is currently disabled. It first checks that there are more messages than the minimum number to keep. If compaction is not forced and automatic compaction is suppressed, it returns false. Otherwise it estimates the transcript size with ContextWindow.tokens and compares it to the chosen trigger. It returns true for forced compaction or for transcripts over the trigger.

**Call relations**: Callers use this as the gate before starting a compaction run. Inside that decision, it relies on ContextWindow.tokens to estimate the current cost of the messages.

*Call graph*: calls 1 internal fn (tokens).


##### `ContextWindow.select`  (lines 68–82)

```
def select(self, messages: tuple[MessageT, ...]) -> WindowSelection[MessageT] | None
```

**Purpose**: Chooses which old conversation rounds should be summarized and which recent messages should be kept exactly as they are. It preserves whole rounds so the transcript is not cut in the middle of a useful exchange.

**Data flow**: It receives all messages, groups them into rounds with ContextWindow.rounds, then walks backward from the newest round until it has kept at least the configured minimum number of messages. The older rounds become the head to summarize, and the kept newer messages become the tail. If there is no older head left to summarize, it returns nothing.

**Call relations**: CompactionHarness.run uses this at the start of compaction. The result tells the harness what to send to the summarizer and what to carry forward unchanged.

*Call graph*: calls 1 internal fn (rounds); 1 external calls (__init__).


##### `ContextWindow.rounds`  (lines 84–96)

```
def rounds(self, messages: tuple[MessageT, ...]) -> tuple[tuple[MessageT, ...], ...]
```

**Purpose**: Splits a flat transcript into conversation rounds. A round starts at an assistant message and includes the following messages until the next assistant message begins a new round.

**Data flow**: It receives the messages in order. For each message, it asks the caller-provided role function whether the message is from the assistant. When it sees a new assistant message after collecting earlier messages, it closes the current round and starts a new one. It returns all rounds as immutable tuples.

**Call relations**: ContextWindow.select calls this before deciding what to summarize. Keeping rounds together helps the compaction avoid separating an assistant action from the user or tool messages that answer it.

*Call graph*: called by 1 (select).


##### `ContextWindow.drop_oldest`  (lines 98–102)

```
def drop_oldest(self, rounds: tuple[tuple[MessageT, ...], ...]) -> tuple[tuple[MessageT, ...], ...]
```

**Purpose**: Removes a bounded slice of the oldest rounds before retrying a summarization request that was itself too large. This is a fallback for when even the summary prompt cannot fit.

**Data flow**: It receives the old rounds chosen for summarization. It drops at least one round, or about one configured fraction of the rounds, from the front. It returns the remaining newer rounds.

**Call relations**: CompactionHarness._summarize uses this only after a summarization attempt fails with a context-overflow error. The next retry summarizes less history so the external model has a better chance of accepting the request.


##### `ContextWindow.tokens`  (lines 104–119)

```
def tokens(self, messages: tuple[MessageT, ...]) -> int
```

**Purpose**: Estimates how much model context a group of messages will consume. It is intentionally approximate because this file does not own the real message types or the model’s exact tokenizer.

**Data flow**: It receives messages and reads each one through caller-provided functions: role text, message text, extra hidden character cost, and image count. It converts character counts into estimated tokens using chars_per_token, adds a fixed token cost per image, and returns the total. If chars_per_token is invalid, it raises an error.

**Call relations**: ContextWindow.should_compact uses this estimate to decide when to compact. CompactionHarness.run also uses it indirectly through the window to compare the transcript size before and after compaction.

*Call graph*: called by 1 (should_compact).


##### `ContextWindow.require_budget`  (lines 121–140)

```
def require_budget(self, *, before_tokens: int, after_tokens: int, tail_tokens: int, fixed_replacement_tokens: int) -> None
```

**Purpose**: Checks that a proposed compacted transcript is actually useful. It rejects a replacement that could have made the transcript smaller or below the trigger but failed to do so.

**Data flow**: It receives the estimated token counts before and after compaction, the token cost of the unchanged tail, and the fixed cost of the replacement boundary. It calculates how much room the old head should have been able to occupy after summarizing. If the new transcript did not shrink when it should have, or if it remains over the compaction trigger when it should fit, it raises a runtime error. Otherwise it returns normally.

**Call relations**: CompactionHarness.run calls this near the end, before saving the checkpoint. It acts as the quality gate that prevents installing a “compacted” transcript that does not solve the size problem.


##### `CompactionHarness.run`  (lines 173–201)

```
async def run(self, messages: tuple[MessageT, ...]) -> CompactionResult[MessageT, UsageT]
```

**Purpose**: Performs one complete compaction: choose old messages, summarize them, verify the summary, check the size budget, save progress, and return the new transcript. It is the main workflow for shrinking a transcript.

**Data flow**: It receives the current messages. It asks the ContextWindow to select an old head and a recent tail. If nothing can be summarized, it returns the original messages and no usage records. Otherwise it estimates the current size, summarizes the head, opens a boundary against the retained tail, verifies the candidate replacement, optionally retries summarization if important anchors are missing, builds the final message list, checks the budget, writes a checkpoint, and returns the new messages plus usage from summarization attempts.

**Call relations**: This is the public method callers use when they have decided compaction should happen. It calls CompactionHarness._summarize for external summary attempts and uses caller-provided functions for project-specific work such as verification, failure recovery, message construction, and checkpoint saving.

*Call graph*: calls 1 internal fn (_summarize); 1 external calls (__init__).


##### `CompactionHarness._summarize`  (lines 203–220)

```
async def _summarize(self, head: tuple[tuple[MessageT, ...], ...], missing: tuple[AnchorT, ...]) -> tuple[SummaryT, UsageT]
```

**Purpose**: Calls the external summarizer with retry behavior for prompts that are too large. It gradually drops the oldest material only when the failure looks like a model context-size rejection.

**Data flow**: It receives the rounds to summarize and any missing anchors that the new summary must cover. It tries to summarize those rounds. If summarization succeeds, it returns the summary and its usage record. If it fails, it checks the error with is_context_overflow. For context-overflow errors, while retries remain and more than one round is available, it drops some oldest rounds and tries again. For other errors, or after retries are exhausted, it raises the error.

**Call relations**: CompactionHarness.run calls this for the first summary and, when needed, for a second summary that includes missing anchors. This helper is where overflow recovery is centralized, using is_context_overflow to decide whether retrying with less history is appropriate.

*Call graph*: calls 1 internal fn (is_context_overflow); called by 1 (run).


### `core/src/ufo/runtime/transcript.py`

`io_transport` · `turn completion and repair/redelivery persistence`

A conversation transcript is the durable record of what has happened so far. In this system, more than one path may try to write that record: the normal run that finishes a turn, and a repair or redelivery path that may publish a fallback record if something is retried. Without this file, a late or less informed write could erase useful history, like someone replacing a full meeting notebook with a partial copy.

The `Transcript` class wraps two pieces of information: the blob store, which is a durable storage place for bytes, and the conversation id, which says which transcript to read or write. `read` fetches the stored blob for that conversation and turns it back into a `Conversation` object. If nothing has been written yet, it returns `None`.

`write` is deliberately cautious. Before saving, it reads the current transcript and asks `_supersedes` whether the new conversation is allowed to replace it. The rules prefer later turn sequence numbers. At the same sequence number, they prefer the real run’s record over a repair fallback, and they allow certain parked or resumed attempts to replace earlier partial windows. The main idea is simple: the stored transcript should move forward, or become more complete, but not silently go backward.

#### Function details

##### `Transcript.read`  (lines 18–23)

```
async def read(self) -> Conversation | None
```

**Purpose**: Reads the durable transcript for this conversation, if one exists. It gives callers a decoded `Conversation` object instead of raw stored bytes.

**Data flow**: It starts with the transcript’s conversation id and blob store. It builds the storage key for that conversation, asks the blob store for the saved bytes, and if the blob is missing it returns `None`. If bytes are found, it decodes them into a `Conversation` and returns that object.

**Call relations**: This is the lookup step used by `Transcript.write` before any save is attempted. It relies on `transcript_key` to find the right blob and `decode` to turn the stored bytes back into the in-memory conversation record.

*Call graph*: called by 1 (write); 2 external calls (decode, transcript_key).


##### `Transcript.write`  (lines 25–30)

```
async def write(self, conversation: Conversation) -> bool
```

**Purpose**: Attempts to save a conversation transcript, but only if it is newer or more authoritative than what is already stored. It returns `True` when it writes and `False` when it refuses to overwrite a better existing record.

**Data flow**: It receives a `Conversation` that someone wants to persist. First it reads the currently stored transcript. If there is already a transcript and `_supersedes` says the incoming one should not replace it, the function stops and returns `False`. Otherwise it encodes the conversation into bytes, stores those bytes under this conversation’s transcript key, and returns `True`.

**Call relations**: This is the main public save path in the file. It calls `Transcript.read` to inspect the current state, asks `_supersedes` to enforce the safety rules, then uses `encode` and `transcript_key` to write the accepted record to the blob store.

*Call graph*: calls 2 internal fn (read, _supersedes); 2 external calls (encode, transcript_key).


##### `_supersedes`  (lines 33–57)

```
def _supersedes(incoming: Conversation, stored: Conversation) -> bool
```

**Purpose**: Decides whether one conversation record is allowed to replace another. Its job is to prevent an older, fallback, or less complete transcript from overwriting a better one.

**Data flow**: It compares the incoming conversation with the stored conversation. If the incoming turn sequence number is later, it is allowed; if it is earlier, it is not. If both are for the same turn, it checks whether the incoming record came from the real run, whether either record represents a parked paused attempt, and whether the incoming message list is at least as complete as the stored one. It returns a simple yes-or-no boolean.

**Call relations**: `Transcript.write` calls this function whenever there is already a transcript in storage. `_supersedes` does not write anything itself; it only gives the permission decision that determines whether `Transcript.write` proceeds to encode and save the new record.

*Call graph*: called by 1 (write).


### Turn labels and handoff rules
These files define user-facing activity labels, turn audience checks, agent handoff contracts, and delivery-writing constraints.

### `core/src/ufo/runtime/turns/activity.py`

`domain_logic` · `during a turn, when a tool call needs a user-facing activity label`

When the system uses a tool, the raw record of that action is usually not suitable to show to a person. It may contain tool names, file paths, IDs, command text, URLs, or other details that are confusing or sensitive. This file creates a safer, simpler label for that moment in the work.

The main piece is ActivitySummarizer. It takes one tool call plus the user’s goal, trims both down to safe size limits, and wraps them in a carefully written prompt for a language model. The prompt tells the model to produce only a 3 to 8 word plain-language label, focused on what the current step is doing now. It also explicitly tells the model not to reveal raw tool names, commands, paths, URLs, secrets, or JSON.

The file also protects the rest of the run from slow or failing summaries. The model only gets a short timeout. If summarizing fails, the code records a metric and log message, then returns None instead of breaking the main task. Finally, it cleans up the model’s answer so a bullet, quote, or trailing punctuation does not leak into the user-facing activity line.

#### Function details

##### `ActivityModel.model`  (lines 33–33)

```
def model(self) -> str
```

**Purpose**: This is the required way to ask an activity-summary model which model name it uses. ActivitySummarizer needs that name when it builds the request sent to the language model.

**Data flow**: The summarizer has an object that follows the ActivityModel shape. It reads this property to get a model identifier string, then places that string into the model request. Nothing is changed by reading it.

**Call relations**: ActivitySummarizer.summarize relies on any supplied ActivityModel to provide this value before it asks for a summary. The protocol does not implement the property itself; it states what a compatible model object must provide.


##### `ActivityModel.complete`  (lines 35–35)

```
async def complete(self, request: ModelRequest) -> str
```

**Purpose**: This is the required method for asking a model to complete a summary request. It lets ActivitySummarizer use any model-like object that can accept a ModelRequest and return text.

**Data flow**: A ModelRequest goes in, containing the prompt, the user message, token limit, and other model settings. The model processes that request and returns a text completion. The protocol only defines this promise; the actual work happens in the concrete model object supplied elsewhere.

**Call relations**: ActivitySummarizer.summarize calls this method after building the request. Because it is part of a protocol, different model implementations can be plugged in without changing the summarizer.


##### `ActivitySummarizer.summarize`  (lines 44–71)

```
async def summarize(self, call: ToolUseBlock, goal: str='') -> str | None
```

**Purpose**: This turns one tool call into a short label suitable to show to a user. It exists so the interface can describe progress in human terms without exposing raw tool details.

**Data flow**: It receives a ToolUseBlock, which contains the tool name and its input arguments, plus an optional goal string. It trims the goal, converts the arguments into a bounded JSON string, and packages that into a model request with strict instructions. It waits up to a fixed timeout for the model answer. If the model succeeds, the answer is cleaned into one activity line and returned. If anything goes wrong, it records the failure and returns None.

**Call relations**: This is the file’s main flow. It calls _bounded_arguments to keep the tool input small enough and activity_line to normalize the model’s reply. It constructs Message and ModelRequest objects for the model call, uses asyncio.timeout so this side task cannot hang the turn, and reports failures through emit_metric and log.

*Call graph*: calls 2 internal fn (_bounded_arguments, activity_line); 6 external calls (__init__, __init__, timeout, dumps, emit_metric, log).


##### `_bounded_arguments`  (lines 74–78)

```
def _bounded_arguments(arguments: dict[str, object]) -> str
```

**Purpose**: This converts a tool’s argument dictionary into a compact string while enforcing a maximum length. It keeps the summary prompt from becoming too large or accidentally carrying too much raw detail.

**Data flow**: A dictionary of tool arguments goes in. The function turns it into compact JSON text. If the text is short enough, it returns it as-is. If it is too long, it cuts it to the configured character limit and adds an ellipsis to show it was shortened.

**Call relations**: ActivitySummarizer.summarize calls this before sending the tool call to the model. It acts like a safety valve: the summarizer can still describe the action without feeding the model an unlimited amount of argument data.

*Call graph*: called by 1 (summarize); 1 external calls (dumps).


##### `activity_line`  (lines 81–84)

```
def activity_line(text: str) -> str | None
```

**Purpose**: This cleans a model’s raw answer into one neat label, or returns None if nothing usable remains. It helps make the final activity text consistent even if the model adds small formatting marks.

**Data flow**: A text completion goes in. The function collapses repeated whitespace, trims spaces, removes common bullet markers and surrounding quotes or backticks, and strips trailing sentence punctuation. If the result still contains text, it returns that cleaned label. If it becomes empty, it returns None.

**Call relations**: ActivitySummarizer.summarize calls this after the model responds. The model is asked to return only a label, but this function is the final cleanup step before that label can be shown to a member.

*Call graph*: called by 1 (summarize); 1 external calls (sub).


### `core/src/ufo/runtime/turns/audience.py`

`domain_logic` · `turn handling and member-facing reads`

A conversation can be visible to different audiences: everyone in the workspace, one specific member, a named room, or a room that is shared with an outside organization. This file gives those audiences a strict text format and centralizes the safety checks around them. Think of it like putting color-coded stickers on folders: every folder must have a sticker in the right format, and the system uses that sticker to decide who may read it.

The file creates an `Audience` type, which is really a string with a clearer meaning. It then provides helper functions to build audience labels, such as the shared audience, a member audience, a room audience, or a foreign room audience. It also provides `parse_audience`, which acts like a gatekeeper: it accepts only labels that match the allowed forms and rejects malformed ones.

The most important behavior is about disclosure boundaries. `readable_audiences` says a member can read workspace-shared content and their own private content. `audience_subjects` decides what stored subjects a conversation may look up. Foreign rooms are deliberately isolated: they read only their own subject and not the workspace-shared subject. `narrow_audience` lets code move from a broader audience to a more specific compatible one, but rejects unsafe changes that would silently cross privacy boundaries.

#### Function details

##### `conversation_audience`  (lines 14–15)

```
def conversation_audience(member_id: UUID | None) -> Audience
```

**Purpose**: This function builds the audience label for a normal conversation. If there is no specific member, it returns the shared workspace audience; otherwise it returns the private audience for that member.

**Data flow**: It receives either a member UUID, which is a unique identifier, or `None`. With `None`, it outputs the shared audience label. With a UUID, it turns that ID into a member-specific audience string and returns it as an `Audience`.

**Call relations**: Other code in this file uses it whenever it needs the official form of a member audience. `parse_audience` uses it to verify that a member label is written exactly right, and `readable_audiences` uses it to include a member's private audience beside the shared one.

*Call graph*: called by 2 (parse_audience, readable_audiences).


##### `room_audience`  (lines 18–19)

```
def room_audience(surface: str, room: str) -> Audience
```

**Purpose**: This function builds an audience label for an internal room. It gives callers one simple way to describe content meant for a particular room on a particular surface.

**Data flow**: It receives a surface name and a room name. It passes those values, along with the internal-room prefix, to the shared room-building helper. The result is an `Audience` string like a structured address for that room.

**Call relations**: It relies on `_room_audience` to do the validation and formatting. `parse_audience` calls it when checking whether a text value that claims to be a room audience really matches the allowed format.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `foreign_room_audience`  (lines 22–23)

```
def foreign_room_audience(surface: str, room: str) -> Audience
```

**Purpose**: This function builds an audience label for a room that is shared outside the workspace. This distinction matters because externally shared rooms have stricter reading rules.

**Data flow**: It receives a surface name and a room name. It sends them to `_room_audience` with the foreign-room prefix, and gets back a validated `Audience` label.

**Call relations**: Like `room_audience`, it delegates the common formatting work to `_room_audience`. `parse_audience` uses it to confirm that foreign room labels are valid and canonical.

*Call graph*: calls 1 internal fn (_room_audience); called by 1 (parse_audience).


##### `_room_audience`  (lines 26–29)

```
def _room_audience(prefix: str, surface: str, room: str) -> Audience
```

**Purpose**: This private helper does the common work for making room audience labels. It also prevents ambiguous labels by rejecting empty names or names containing colons.

**Data flow**: It receives a prefix, a surface, and a room. It first checks that the surface and room are nonempty and do not contain `:`, because colons are used as separators in the label format. If the inputs are valid, it combines them into one audience string; if not, it raises an error.

**Call relations**: `room_audience` and `foreign_room_audience` both call this helper so their labels are built by the same rules. That keeps internal and foreign room audiences consistent while still giving them different prefixes.

*Call graph*: called by 2 (foreign_room_audience, room_audience).


##### `parse_audience`  (lines 32–55)

```
def parse_audience(value: str) -> Audience
```

**Purpose**: This function validates an audience label and returns it only if it is one of the accepted forms. It protects the rest of the system from acting on misspelled, incomplete, or unsafe audience strings.

**Data flow**: It receives a plain string. It first accepts the exact shared audience. Otherwise it splits the string into its prefix and remaining text, then checks the format for member, room, or foreign room audiences. For member labels it verifies the UUID, and for room labels it rebuilds the expected label using the official helper. If everything matches, it returns the label as an `Audience`; otherwise it raises a `ValueError`.

**Call relations**: This is the file's main gatekeeper. `audience_member`, `audience_subjects`, and `narrow_audience` call it before trusting an audience value. It calls `conversation_audience`, `room_audience`, and `foreign_room_audience` so validation uses the same builders that create valid labels.

*Call graph*: calls 3 internal fn (conversation_audience, foreign_room_audience, room_audience); called by 3 (audience_member, audience_subjects, narrow_audience); 1 external calls (UUID).


##### `readable_audiences`  (lines 58–63)

```
def readable_audiences(member_id: UUID) -> tuple[Audience, ...]
```

**Purpose**: This function tells the system which normal conversation audiences a member may read. A member can read workspace-shared content and their own private member content.

**Data flow**: It receives a member UUID. It returns a two-item tuple: the shared audience and that member's private audience. It does not include room or foreign-room audiences because this workspace-level rule does not know who belongs to those rooms.

**Call relations**: It calls `conversation_audience` to build the member-specific entry in the official format. It is meant for member-facing reads, such as listing conversations or objects connected to those conversations.

*Call graph*: calls 1 internal fn (conversation_audience).


##### `audience_member`  (lines 66–70)

```
def audience_member(audience: Audience) -> UUID | None
```

**Purpose**: This function extracts the member ID from a member-specific audience label. If the audience is not for a single member, it returns nothing.

**Data flow**: It receives an `Audience`. It first runs it through `parse_audience` so invalid labels are rejected. If the validated label starts with the member prefix, it removes that prefix, turns the remaining text into a UUID, and returns it. Otherwise it returns `None`.

**Call relations**: It depends on `parse_audience` to avoid interpreting malformed text. Callers use it when they need to know whether an audience points to a particular member and, if so, which one.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (UUID).


##### `audience_subjects`  (lines 73–80)

```
def audience_subjects(audience: Audience) -> frozenset[str]
```

**Purpose**: This function says which stored subjects a conversation audience is allowed to read. It is a key privacy rule, especially for externally shared rooms.

**Data flow**: It receives an audience label and validates it with `parse_audience`. If it is a foreign-room audience, it returns only that audience as the readable subject. For all other valid audiences, it returns both the workspace-shared subject and the audience's own subject.

**Call relations**: It calls `parse_audience` before making access decisions. Its special treatment of foreign rooms prevents internal workspace-shared information from being pulled into a channel that includes another organization.

*Call graph*: calls 1 internal fn (parse_audience).


##### `narrow_audience`  (lines 83–98)

```
def narrow_audience(current: Audience, requested: Audience) -> Audience
```

**Purpose**: This function safely combines a current audience with a requested audience. It allows a conversation to stay in the same audience or become a compatible narrower audience, but blocks changes that would cross an unsafe boundary.

**Data flow**: It receives the current audience and a requested audience. It validates both. If they match, or if the request is merely for the shared audience, it keeps the current audience. If the current audience is shared, it allows the requested valid audience. For matching room keys, it chooses the stricter foreign-room version when one side is foreign. If the two audiences are incompatible, it raises an error.

**Call relations**: It calls `parse_audience` for both inputs before comparing them. This function is used at the point where code must decide whether an audience change is allowed, so it acts like a guardrail against accidentally moving conversation content into the wrong visibility scope.

*Call graph*: calls 1 internal fn (parse_audience); 1 external calls (partition).


### `core/src/ufo/runtime/turns/contracts.py`

`domain_logic` · `spawn, dispatch, delivery validation, and schema write-time checks`

When the system spawns or talks to an agent, it needs to know what shape of data is allowed in and what shape should come back. This file is the rulebook for that. Some agents use normal Pydantic models, which are Python classes that validate data. Workspace agents can instead store raw JSON Schema, which is a data description of allowed JSON. The important job here is to make both kinds look the same to the rest of the system.

The default shapes are simple: an input with a `task` string, and outputs with a `result` string. If an agent declares its own schema, `JsonContract` wraps that schema and offers Pydantic-like methods such as `model_validate` and `model_json_schema`. That means later code can ask, “is this payload valid?” in one standard way.

The file is also careful about safety. Declared schemas are checked before they are stored. They must be small, describe a top-level JSON object, and avoid references or regular expressions. This matters because references could make the server try to fetch outside data, and regular expressions can sometimes take a very long time to match. In everyday terms, the file lets users bring their own form template, but checks that the template is local, bounded, and safe before anyone starts filling it out.

#### Function details

##### `ValidatedJson.model_dump`  (lines 55–56)

```
def model_dump(self) -> object
```

**Purpose**: Returns the already-validated payload as normal Python data. This lets a raw JSON Schema result behave like a Pydantic model result when other code asks to dump it.

**Data flow**: It starts with a `ValidatedJson` object that holds validated data in `self.data`. It does not transform or re-check anything. It simply gives that stored data back to the caller.

**Call relations**: This is used after `JsonContract.model_validate` has accepted some data and wrapped it in `ValidatedJson`. It provides the same kind of read method that callers expect from Pydantic model instances.


##### `ValidatedJson.model_dump_json`  (lines 58–59)

```
def model_dump_json(self) -> str
```

**Purpose**: Turns the validated payload into a JSON text string. This gives raw-schema validation results the same convenient JSON output behavior as Pydantic models.

**Data flow**: It reads the stored validated data from `self.data`, passes it to `json.dumps`, and returns the resulting JSON string. It changes nothing on the object.

**Call relations**: This sits on the object returned by `JsonContract.model_validate` or `JsonContract.model_validate_json`. Any later code that wants a JSON string can call it without needing to know that the payload came from a raw JSON Schema contract.

*Call graph*: 1 external calls (dumps).


##### `JsonContract.model_json_schema`  (lines 68–69)

```
def model_json_schema(self) -> dict[str, object]
```

**Purpose**: Returns the raw JSON Schema for this contract. Other parts of the runtime use this to describe what keys a payload should contain or to show the contract to users.

**Data flow**: It reads the schema stored on the `JsonContract`, copies it into a plain dictionary, and returns that copy. The original stored schema is not changed.

**Call relations**: This mirrors the `model_json_schema` method on Pydantic model classes. `payload_keys` relies on it so it can describe both Pydantic contracts and raw JSON Schema contracts through the same doorway.


##### `JsonContract.model_validate`  (lines 71–106)

```
def model_validate(self, data: object) -> ValidatedJson
```

**Purpose**: Checks a Python value against this contract’s JSON Schema. If the value is valid, it wraps it as `ValidatedJson`; if not, it raises a Pydantic-style `ValidationError` so callers see one familiar error format.

**Data flow**: It receives any Python data, builds a JSON Schema validator from the stored schema, and checks the data. If the schema contains an unresolvable reference, it turns that into a validation error. If the data breaks schema rules, it gathers those faults and reports them as validation errors at the matching payload locations. If everything passes, it returns `ValidatedJson(data)`.

**Call relations**: This is the main validation path for raw JSON Schema contracts. `JsonContract.model_validate_json` calls it after parsing JSON text. It deliberately uses Pydantic-shaped errors so the spawn, dispatch, and delivery code can catch the same kind of failure whether the contract was a Pydantic model or a raw schema.

*Call graph*: called by 1 (model_validate_json); 6 external calls (__init__, from_exception_data, Draft202012Validator, InitErrorDetails, PydanticCustomError, Registry).


##### `JsonContract.model_validate_json`  (lines 108–124)

```
def model_validate_json(self, text: str) -> ValidatedJson
```

**Purpose**: Checks JSON text against this contract’s JSON Schema. It first makes sure the text is valid JSON, then uses the normal object validator.

**Data flow**: It receives a string. It tries to parse that string with `json.loads`. If the text is not valid JSON, it raises a Pydantic-style `ValidationError`. If parsing succeeds, it passes the resulting Python data into `JsonContract.model_validate` and returns that result.

**Call relations**: This is the text-input companion to `JsonContract.model_validate`. It hands parsed data into that function so all schema checking and error formatting stay in one place.

*Call graph*: calls 1 internal fn (model_validate); 4 external calls (from_exception_data, loads, InitErrorDetails, PydanticCustomError).


##### `freeform_result_contract`  (lines 130–137)

```
def freeform_result_contract(contract: Contract) -> bool
```

**Purpose**: Tells whether a contract is the simple built-in shape of one string field named `result`. This helps the runtime recognize the most basic freeform result handoff.

**Data flow**: It receives a contract. If the contract is a Pydantic model class, it inspects its fields and returns `True` only when the model has exactly one field, `result`, whose type is `str`. For raw JSON Schema contracts and all other shapes, it returns `False`.

**Call relations**: Other runtime code can use this as a small yes-or-no test before deciding how to describe or treat an agent result. It does not call out to other project code; it only inspects the contract it was given.


##### `payload_keys`  (lines 140–156)

```
def payload_keys(contract: Contract) -> str
```

**Purpose**: Creates a short, human-readable list of the keys a payload should contain. Required keys are shown plainly, while optional keys are marked as optional.

**Data flow**: It receives either a Pydantic model contract or a `JsonContract`. It asks the contract for its JSON Schema, reads the `properties` and `required` sections, sorts the property names with required ones first, and returns text such as `` `task` `` or `` `name` (optional) ``. If there are no usable properties, it returns `(no keys)`.

**Call relations**: This function depends on the shared `model_json_schema` method, which is why both Pydantic models and `JsonContract` can be described the same way. It is meant for messages such as spawn catalogs or refusal explanations, where a user needs to know what fields were expected.

*Call graph*: 1 external calls (model_json_schema).


##### `input_contract`  (lines 159–160)

```
def input_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the input contract for an agent. If no custom schema is supplied, it uses the default `task: str` input shape; otherwise it wraps the supplied JSON Schema in `JsonContract`.

**Data flow**: It receives either `None` or a schema mapping. With `None`, it returns the built-in `TaskInput` Pydantic model class. With a schema, it creates and returns a `JsonContract` around that schema.

**Call relations**: This is a small adapter used when the runtime needs a uniform input contract. It hides the choice between the default model and a user-declared schema from later validation code.

*Call graph*: 1 external calls (__init__).


##### `output_contract`  (lines 163–164)

```
def output_contract(schema: Mapping[str, object] | None) -> Contract
```

**Purpose**: Chooses the output contract for an agent. If no custom schema is supplied, it uses the default `result: str` output shape; otherwise it wraps the supplied JSON Schema in `JsonContract`.

**Data flow**: It receives either `None` or a schema mapping. With `None`, it returns the built-in `AgentResultOutput` Pydantic model class. With a schema, it creates and returns a `JsonContract` around that schema.

**Call relations**: This mirrors `input_contract` for returned data. It lets delivery and result-validation code work with one contract interface whether the output shape is built in or declared by a workspace agent.

*Call graph*: 1 external calls (__init__).


##### `check_declared_schema`  (lines 167–182)

```
def check_declared_schema(candidate: Mapping[str, object], field: str) -> None
```

**Purpose**: Checks whether a user-declared input or output schema is safe and acceptable before it is stored. This prevents unsafe or overly costly schema features from reaching the live spawn and serving path.

**Data flow**: It receives a candidate schema and the name of the field being checked, for use in error messages. It serializes the schema to measure its size, requires the top-level type to be `object`, searches for refused keywords such as references and regular-expression patterns, and asks the JSON Schema library to confirm the schema is valid. If any check fails, it raises `ValueError`; if all pass, it returns nothing and the schema is considered acceptable.

**Call relations**: This function calls `_refused_keyword` to scan through nested schema data. It is intended to run at write time, before the schema becomes part of an agent record, so later spawn and validation code can assume declared schemas are bounded, local, and structurally valid.

*Call graph*: calls 1 internal fn (_refused_keyword); 2 external calls (dumps, check_schema).


##### `_refused_keyword`  (lines 185–197)

```
def _refused_keyword(node: object) -> str | None
```

**Purpose**: Searches a schema-like structure for keywords this system does not allow, such as `$ref` or `pattern`. It is a safety helper for declared schema checking.

**Data flow**: It receives any nested value. If the value is a mapping, it checks each key and then recursively checks each value. If the value is a list, it recursively checks each item. It returns the first refused keyword it finds, or `None` if the whole structure is clean.

**Call relations**: This helper is called by `check_declared_schema`. It keeps the recursive scan separate, so the main schema check can simply ask whether any unsafe keyword appears anywhere inside the candidate schema.

*Call graph*: called by 1 (check_declared_schema).


### `core/src/ufo/runtime/turns/delivery_register.py`

`config` · `prompt construction`

This file is a small but important rule book for how agents are allowed to “hand over” their final words. Think of it like a house style guide: every agent, subagent, and direct model call should use the same expectations when presenting an answer.

The main rule text lives in a nearby Markdown file called `delivery_register.md`. This Python file reads that Markdown file once and exposes its contents as `DELIVERY_REGISTER_BLOCK`, so other parts of the system can insert the same instructions into prompts instead of copying them in many places. That matters because duplicated rules can drift out of sync; one shared source keeps the behavior consistent.

It also defines two limits. `DIRECT_PROSE_RESULT_MAX_CHARS` caps how long a direct prose result should be. `SUBAGENT_RESULT_MAX_WORDS` caps what a subagent may report back to its parent. Finally, `SUBAGENT_RESULT_DESCRIPTION` turns those limits into clear instructions for a subagent-facing result field: give one short parent-visible result, do not write it as normal assistant text first, include an absolute path when an artifact is required, and avoid creating files for simple result-only tasks.

Without this file, prompt assembly would lose a central, consistent contract for final deliveries.


### Conversation record schemas
These files provide shared subject labels and the persisted record shapes used for transcripts and compaction history.

### `core/src/ufo/runtime/turns/subjects.py`

`data_model` · `cross-cutting`

This file is about visibility: when some content is created, the system needs a simple way to describe its audience. Think of it like putting a sticker on a note: either the sticker says “shared”, meaning everyone in the workspace may read it, or it says “member:<id>”, meaning it belongs to one specific member.

The file defines two pieces of vocabulary. `SHARED_SUBJECT` is the exact text used for content visible to the whole workspace. `MEMBER_SUBJECT_PREFIX` is the text placed before a member’s unique ID to make a member-specific subject.

The helper `member_subject` builds that member-specific sticker from a UUID, which is a standard unique identifier. Using one helper matters because every part of the system must spell these subject strings the same way. If one part wrote `member-123` and another expected `member:123`, private visibility checks would fail.

The helper `subject_shared` answers the narrow question: “Is this subject the special shared one?” It deliberately only returns true for the exact shared label. Other audience-like places, such as rooms or externally shared channels, are not treated as shared here because this visibility system is based on workspace membership, and those cases do not provide the same membership fact.

#### Function details

##### `member_subject`  (lines 9–10)

```
def member_subject(member_id: UUID) -> str
```

**Purpose**: Builds the standard subject string for content tied to one workspace member. Code uses this so member-specific visibility labels are written in exactly one format everywhere.

**Data flow**: It receives a member ID as a UUID, which is a unique identifier. It places that ID after the fixed text `member:` and returns the finished string, such as `member:<uuid>`. It does not change any stored data.

**Call relations**: This is a small shared vocabulary helper. Other parts of the runtime can call it whenever they need to create or compare a subject label for one member, instead of hand-building the string themselves.


##### `subject_shared`  (lines 13–18)

```
def subject_shared(subject: str) -> bool
```

**Purpose**: Checks whether a subject string means “readable by every member of the workspace.” It is used when the system needs to distinguish truly shared content from member-scoped or other audience-scoped content.

**Data flow**: It receives a subject string and compares it with the exact shared label, `shared`. If they match, it returns `true`; otherwise it returns `false`. It only answers this one question and does not modify anything.

**Call relations**: This helper sits beside the subject naming constants and gives other visibility code a safe, central test for shared content. When called during audience or readability decisions, it gives back a simple yes-or-no answer that the caller can use in its larger access decision.


### `core/src/ufo/runtime/turns/transcript.py`

`data_model` · `cross-cutting: used whenever conversation transcripts or compaction records are written, read, debugged, or evaluated`

This file is the contract for durable conversation history. A running agent writes messages, later tools read them for debugging or evaluation, and compaction can replace an older stretch of messages with a shorter summary. Those pieces live in different parts of the project, so this file acts like a shared form everyone must fill out the same way.

The main conversation record is `Conversation`. It stores the message window, its sequence number, optional system prompt text, injected context, whether the turn itself wrote the record, and any parked turn information. “Parked” means a turn was paused with enough requester identity and rendered text saved so it can be resumed safely.

The file also defines the shapes used when a long conversation is compacted. A compaction keeps the messages before the swap, the messages after the swap, and a structured summary. The summary includes what work was happening, files that mattered, errors, decisions, pending tasks, loaded skills, and a verification report. That verification report records whether important “anchors” survived the compaction.

For storage, records are turned into compact JSON and then compressed with LZ4, a fast compression format. On reading, the file decompresses the bytes and validates that the data still matches the expected shape. If the bytes are corrupt or the shape is wrong, it raises `TranscriptDecodeError` instead of letting bad history pass through unnoticed.

#### Function details

##### `transcript_key`  (lines 61–62)

```
def transcript_key(conversation_id: UUID) -> str
```

**Purpose**: Builds the storage path for the main saved transcript of one conversation. A caller uses it when it needs to put or fetch the conversation’s compressed message record from the blob store.

**Data flow**: It receives a conversation ID, which is a unique identifier. It inserts that ID into a fixed path pattern and returns a string such as a file-like address ending in `messages.json.lz4`.

**Call relations**: This is the shared naming rule for transcript blobs. Writers and readers can meet at the same stored object because they both use this same path format.


##### `encode`  (lines 65–67)

```
def encode(conversation: Conversation) -> bytes
```

**Purpose**: Turns a validated `Conversation` object into compressed bytes ready for storage. This keeps saved transcripts small and gives storage a simple byte payload.

**Data flow**: It receives a `Conversation`. It first asks the model for a JSON-safe dictionary, then converts that to compact JSON text, turns the text into bytes, and compresses those bytes with LZ4. The output is the compressed byte string that can be written to durable storage.

**Call relations**: This is the write-side partner to `decode`. When a turn or repair path has a conversation record to persist, this function produces the exact stored form that later readers expect.

*Call graph*: 2 external calls (model_dump, dumps).


##### `decode`  (lines 70–74)

```
def decode(body: bytes) -> Conversation
```

**Purpose**: Reads compressed transcript bytes back into a validated `Conversation`. It protects the rest of the system from corrupt or outdated stored data.

**Data flow**: It receives compressed bytes from storage. It decompresses them, validates the JSON against the `Conversation` shape, and returns a `Conversation` object. If decompression or validation fails, it changes the low-level error into `TranscriptDecodeError`, which clearly says the stored transcript could not be read.

**Call relations**: This is the read-side partner to `encode`. Any reader that retrieves a transcript blob can use it to get a safe, typed conversation record instead of raw bytes.

*Call graph*: 1 external calls (__init__).


##### `compaction_key`  (lines 160–161)

```
def compaction_key(conversation_id: UUID, index: int, half: CompactionHalf) -> str
```

**Purpose**: Builds the storage path for one piece of one compaction record. A compaction has separate stored pieces for the window before, the window after, and the summary.

**Data flow**: It receives a conversation ID, a compaction index, and which half or piece is wanted: `before`, `after`, or `summary`. It combines them into a stable path string under that conversation’s compaction folder.

**Call relations**: The compaction readers call this before fetching from the blob store. Because `read_compaction_after` and `read_compaction_record` both use it, they look for compaction data in the same places that the writer used.

*Call graph*: called by 2 (read_compaction_after, read_compaction_record).


##### `decode_compaction`  (lines 164–173)

```
def decode_compaction(index: int, before: bytes, after: bytes, summary: bytes) -> CompactionRecord
```

**Purpose**: Turns the three stored byte blobs for a compaction into one easy-to-use `CompactionRecord`. This lets readers see what was removed, what replaced it, and what summary explains the swap.

**Data flow**: It receives the compaction index plus three compressed byte strings: the `before` window, the `after` window, and the structured summary. It decompresses and validates each one, pulls out the message tuples for the two windows, and returns a `CompactionRecord`. If any piece is unreadable or has the wrong shape, it raises `TranscriptDecodeError`.

**Call relations**: `read_compaction_record` calls this after it has fetched all three blobs. This function is the point where raw stored bytes become the typed record used by debug and evaluation readers.

*Call graph*: called by 1 (read_compaction_record); 2 external calls (__init__, __init__).


##### `read_compaction_after`  (lines 176–189)

```
async def read_compaction_after(blob: BlobStore, conversation_id: UUID, index: int) -> tuple[Message, ...] | None
```

**Purpose**: Fetches only the `after` window for a single compaction. This is a lighter read for callers that only need to check what the conversation looked like after compaction, not the whole before-and-summary record.

**Data flow**: It receives a blob store, a conversation ID, and a compaction index. It builds the `after` key, asks the blob store for that compressed object, and returns the decoded messages. If the blob is missing, it returns `None`. If the blob exists but cannot be decoded, it raises `TranscriptDecodeError`.

**Call relations**: It uses `compaction_key` to find the right object and `BlobStore.get` to fetch it. Unlike the full record reader, it stops after the small `after` piece so a caller can make a quick check without loading the whole compaction history.

*Call graph*: calls 2 internal fn (get, compaction_key); 1 external calls (__init__).


##### `read_compaction_record`  (lines 192–203)

```
async def read_compaction_record(blob: BlobStore, conversation_id: UUID, index: int) -> CompactionRecord | None
```

**Purpose**: Fetches one complete compaction record for a conversation. It returns the before messages, after messages, and summary together, or `None` if that compaction index has no stored record.

**Data flow**: It receives a blob store, a conversation ID, and an index. It builds and fetches the `before`, `after`, and `summary` blobs. If any required blob is missing, it treats the whole record as absent and returns `None`. If all are present, it passes the bytes to `decode_compaction` and returns the resulting `CompactionRecord`.

**Call relations**: This is the per-index reader used by `read_compaction_records`. It handles the storage lookup work, then hands the decoding work to `decode_compaction` so the validation logic stays in one place.

*Call graph*: calls 3 internal fn (get, compaction_key, decode_compaction); called by 1 (read_compaction_records).


##### `read_compaction_records`  (lines 206–216)

```
async def read_compaction_records(blob: BlobStore, conversation_id: UUID) -> tuple[CompactionRecord, ...]
```

**Purpose**: Reads all compaction records for a conversation in order, starting with the oldest. It gives callers the full compaction history without requiring them to know how many records exist.

**Data flow**: It receives a blob store and a conversation ID. It starts at index 1 and repeatedly asks `read_compaction_record` for the next record. Each found record is added to a list. The first missing index means there are no more records, and the function returns the collected records as an immutable tuple.

**Call relations**: This function is the simple outer loop around `read_compaction_record`. It relies on the rule that compaction indices are written sequentially, so the first gap marks the end of the history.

*Call graph*: calls 1 internal fn (read_compaction_record).
