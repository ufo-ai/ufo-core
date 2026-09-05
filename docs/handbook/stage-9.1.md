# Context loading and conversation compaction  `stage-9.1`

This stage is behind-the-scenes support that prepares the material the AI model reads before it answers. The system may need to include old messages, notes about changed files, writing rules, saved hints, source panels, artifacts, questions, and summaries. But an AI model has a limited context window, meaning it can only read so much text and media at once. This stage keeps that bundle useful without letting it grow too large.

The context harness in `core/src/ufo/harness/context.py` acts like a traffic monitor. It checks whether the conversation is getting close to the model’s limit and decides when shortening is needed. If so, it runs the process in a controlled way.

The compaction logic in `core/src/ufo/runtime/compaction.py` does the actual shrinking. It keeps the newest messages exactly as they are, because recent details matter most. It turns older messages into a structured summary that can be checked later. It also saves both the original and compacted versions, so the system can audit what changed or replay the conversation if needed.

## Files in this stage

### Conversation Compaction
Implements and safely triggers conversation-history shortening so long transcripts remain within model context limits while preserving auditable state.

### `core/src/ufo/runtime/compaction.py`

`domain_logic` · `request handling`

AI models cannot read an unlimited conversation. When a chat grows too large, the system must either drop old information or compress it. This file implements the safer option: transcript compaction. Think of it like moving old papers from your desk into a labeled folder, while keeping today’s papers in front of you.

The main class, Compaction, decides when a transcript is too large. It can also run after a model provider says the request is too big. It chooses an older “head” of the conversation to summarize and a recent “tail” to keep exactly as written. Before trusting the summary, it records important anchors: durable tool-output file paths, active request references, loaded skills, and error names that must survive the boundary. It then asks the model for a JSON summary, parses and validates that summary, renders it into one replacement message, and checks whether the required anchors still appear in the new window.

If the first summary loses important facts, the file can retry with explicit instructions naming what was missed. It also prevents invented file references from becoming trusted facts. Finally, it writes compressed before, after, and summary records to blob storage, fires extension hooks, and emits logs and metrics. Without this file, long-running conversations would either fail when they exceed the model window or lose old context without a reliable record of what changed.

#### Function details

##### `harvest_anchors`  (lines 104–130)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: Finds small but important pieces of information that must not disappear during compaction. These include tool-output file paths, named errors, loaded skills, and active request references.

**Data flow**: It receives the old transcript text plus the currently loaded skills and active requests. It searches those inputs with fixed patterns, removes duplicates, keeps only a bounded number of recent items per kind, and returns Anchor objects that later checks can look for literally.

**Call relations**: When a compaction boundary is opened, Compaction._compact.open_boundary calls this to create the checklist that the replacement summary will be graded against. Later, Compaction._verify uses that checklist indirectly through missing_anchors.

*Call graph*: called by 1 (open_boundary); 1 external calls (__init__).


##### `missing_anchors`  (lines 133–137)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: Checks which required facts did not make it into the text carried forward after compaction. It uses exact text containment rather than guessing or fuzzy matching.

**Data flow**: It receives a tuple of anchors and the combined replacement text. It tests each anchor’s literal text against that replacement text and returns only the anchors that are absent.

**Call relations**: Compaction._verify calls this after rendering a candidate summary and appending the kept tail. Its result decides whether the summary is clean, lossy, or needs a retry.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 176–180)

```
def __repr__(self) -> str
```

**Purpose**: Produces a short, safe display string for a compaction request. It shows counts instead of dumping the whole transcript.

**Data flow**: It reads the request’s message count, reason, and active request count. It returns one formatted string useful in logs or debugging.

**Call relations**: This supports inspection of _CompactionRequest objects created by Compaction.maybe_compact before they are passed into Compaction._compact.


##### `_InvalidSummary.__init__`  (lines 184–187)

```
def __init__(self, error_class: str, message: str, usage: Usage) -> None
```

**Purpose**: Creates a special error for a model-produced summary that could not be accepted. It also preserves token usage so the system can still account for the failed model call.

**Data flow**: It receives an error class name, a readable message, and Usage information. It stores the error class and usage on the exception and passes the main details to the base RuntimeError.

**Call relations**: Compaction._summarize_once raises this when the model answered but the answer was empty, not valid JSON, or did not match the summary schema.

*Call graph*: called by 1 (_summarize_once).


##### `_InvalidSummary.__str__`  (lines 189–190)

```
def __str__(self) -> str
```

**Purpose**: Returns the human-readable message for an invalid summary error. This keeps logs focused on the actual explanation.

**Data flow**: It reads the stored exception arguments and returns the message portion as a string.

**Call relations**: This is used whenever _InvalidSummary is converted to text, such as during logging or error reporting after Compaction._summarize_once fails.


##### `Compaction.window`  (lines 226–250)

```
def window(self) -> ContextWindow[Message]
```

**Purpose**: Builds the current model-window calculator for this compaction run. It knows how to estimate text, images, hidden reasoning data, buffers, and keep-last-message rules.

**Data flow**: It reads the serving model’s limits and compaction settings, plus any overrides on the Compaction object. It returns a ContextWindow object configured with helper functions from this file for text, image, and opaque-content counting.

**Call relations**: Most compaction decisions flow through this property. Compaction.maybe_compact uses it to decide whether to compact, Compaction._compact uses it inside the harness, and Compaction._verify uses it to compare before and after token estimates.

*Call graph*: 1 external calls (__init__).


##### `Compaction.__repr__`  (lines 252–253)

```
def __repr__(self) -> str
```

**Purpose**: Gives a compact debug label for a Compaction instance. It identifies the conversation and model without printing private transcript content.

**Data flow**: It reads the conversation id and serving model name. It returns a formatted string.

**Call relations**: This supports logging and debugging of the Compaction object throughout the runtime.


##### `Compaction.maybe_compact`  (lines 255–299)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether the current messages need compaction, then runs it if needed. It is the public gate that callers use before sending a transcript to the model.

**Data flow**: It receives the current messages, an optional force flag, and active request text. It checks normal token pressure, forced recovery, and repeated-tool-loop triggers; if no compaction is needed, it returns the original messages and no usage. If compaction runs, it returns the replacement messages plus any model-usage records from summarization.

**Call relations**: This is the entry into the file’s main workflow. It consults Compaction._repeated_tool_policy and Compaction._repeated_tool_trigger, wraps the inputs in _CompactionRequest, then hands the actual work to Compaction._compact.

*Call graph*: calls 3 internal fn (_compact, _repeated_tool_policy, _repeated_tool_trigger); 2 external calls (__init__, log).


##### `Compaction._repeated_tool_trigger`  (lines 301–333)

```
def _repeated_tool_trigger(self, messages: tuple[Message, ...]) -> int | None
```

**Purpose**: Detects a pattern where the assistant keeps calling the same tool with the same input over multiple turns. That pattern can bloat the transcript, so it may trigger compaction earlier than the normal token limit.

**Data flow**: It receives the message history and reads the configured repeated-tool policy. It walks backward through recent messages, compares tool names and JSON-normalized inputs across turns, and returns an earlier trigger token count if the pattern is found; otherwise it returns nothing.

**Call relations**: Compaction.maybe_compact uses this while deciding whether to compact. Compaction._compact.checkpoint uses it again to label the compaction trigger as a repeated-tool trigger rather than a normal window trigger.

*Call graph*: calls 1 internal fn (_repeated_tool_policy); called by 2 (checkpoint, maybe_compact); 1 external calls (dumps).


##### `Compaction._repeated_tool_policy`  (lines 335–336)

```
def _repeated_tool_policy(self) -> RepeatedToolCompaction | None
```

**Purpose**: Retrieves the model’s repeated-tool compaction setting, if one exists. This keeps the policy lookup in one place.

**Data flow**: It reads the serving model specification and returns its repeated_tool_compaction setting or None.

**Call relations**: Compaction.maybe_compact and Compaction._repeated_tool_trigger call this before applying the repeated-tool shortcut.

*Call graph*: called by 2 (_repeated_tool_trigger, maybe_compact).


##### `Compaction._compact`  (lines 339–468)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction pipeline once a decision has been made. It chooses the split, summarizes the old part, verifies the replacement, persists the record, and fires hooks.

**Data flow**: It receives a _CompactionRequest containing messages, reason, and active requests. Through the compaction harness, it creates a boundary, calls the summarizer, verifies candidates, may retry if anchors were lost, writes compressed records, and returns the final messages plus usage. If an automatic summary is unusable, it logs the failure and leaves the original transcript unchanged; a forced failure is allowed to raise.

**Call relations**: Compaction.maybe_compact calls this when compaction should happen. Inside it, helper callbacks such as open_boundary, retry_failed, and checkpoint connect this file’s model calls, validation, persistence, metrics, and hook notifications into the harness flow.

*Call graph*: called by 1 (maybe_compact); 1 external calls (log_error).


##### `Compaction._compact.open_boundary`  (lines 359–386)

```
async def open_boundary(head: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...], before_tokens: int) -> _Boundary
```

**Purpose**: Freezes the facts about the split between old summarized messages and recent kept messages. This creates the standard against which every summary attempt is judged.

**Data flow**: It receives grouped head messages, the tail messages, and the original token count. It finds the next storage index, fires the pre-compaction hook, drains loaded skills, collects durable references and anchors, and returns a _Boundary object containing all of that fixed information.

**Call relations**: The compaction harness calls this at the start of an actual compaction. It uses Compaction._next_index, Compaction._references, Compaction._window_text, and harvest_anchors so that later verification and persistence all agree on the same boundary.

*Call graph*: calls 4 internal fn (_next_index, _references, _window_text, harvest_anchors); 3 external calls (__init__, __init__, from_iterable).


##### `Compaction._compact.retry_failed`  (lines 388–395)

```
def retry_failed(candidate: _Candidate, error: Exception) -> _Candidate
```

**Purpose**: Marks a candidate summary as having attempted a retry when that retry itself failed. This preserves the best available candidate while recording that recovery was tried.

**Data flow**: It receives the current candidate and the retry error. It logs a warning, updates the verification’s retried flag, and returns a modified candidate with that updated verification embedded in its summary.

**Call relations**: The compaction harness uses this when a retry prompted by missing anchors cannot complete successfully. It lets the pipeline continue with the previous candidate rather than discarding all progress.

*Call graph*: 2 external calls (replace, warn).


##### `Compaction._compact.checkpoint`  (lines 397–427)

```
async def checkpoint(candidate: _Candidate, boundary: _Boundary) -> None
```

**Purpose**: Commits a successful compaction. It saves the before and after records, records verification results, and notifies observers that compaction finished.

**Data flow**: It receives a verified candidate and its boundary. It writes the compacted artifacts, determines whether the trigger was forced, repeated-tool, or normal window pressure, logs and emits metrics, then fires the post-compaction hook with the rendered summary and token counts.

**Call relations**: The compaction harness calls this only after it has an installable candidate. It hands persistence to Compaction._persist and observability to Compaction._record_verification.

*Call graph*: calls 3 internal fn (_persist, _record_verification, _repeated_tool_trigger); 1 external calls (__init__).


##### `Compaction._summarize_once`  (lines 470–498)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Makes one model call to summarize the transcript head. It asks for a structured JSON summary and turns the streamed answer into a validated CompactionSummary.

**Data flow**: It receives message rounds to summarize and any anchors missed by a previous attempt. It renders the prompt with Compaction._prepare, sends a ModelRequest to the serving client, collects text chunks and Usage, then parses the combined text. It returns the summary and usage, or raises _InvalidSummary if the response cannot be accepted.

**Call relations**: The compaction harness calls this as the summarization step inside Compaction._compact. It relies on Compaction._prepare for the input and Compaction._parse_summary for validation.

*Call graph*: calls 3 internal fn (_parse_summary, _prepare, __init__); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 500–522)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: Builds the text prompt that the summarizing model sees. It renders old messages clearly, compresses useless repetition, and appends correction instructions if a previous summary missed required anchors.

**Data flow**: It receives grouped transcript rounds and missed anchors. It turns each message into readable text with Compaction._text, folds repeated runs with Compaction._fold_repeated_runs, adds a bullet list of missed anchor text when needed, and finishes with a clear instruction to output only JSON.

**Call relations**: Compaction._summarize_once calls this before sending the model request. It uses Compaction._bullets when it needs to list anchors and uses Compaction._text to preserve the important content of each message.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 524–537)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Shrinks long stretches of the exact same short phrase repeated over and over. This prevents a stuck tool loop or pasted spam from making the summarization request too large or likely to be refused.

**Data flow**: It receives rendered text. It applies a regular expression that finds repeated word sequences and replaces each run with one copy plus a marker saying how many times it repeated.

**Call relations**: Compaction._prepare calls this after rendering the transcript head and before sending it to the summarizing model.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 532–535)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement text for one repeated run found by the regular expression. It keeps the repeated phrase once and records the count.

**Data flow**: It receives one regex match. It reads the repeated unit, estimates how many times it appeared from the match length, and returns a compact marker string.

**Call relations**: This small inner function is used only by Compaction._fold_repeated_runs as the replacement callback for each detected repetition.


##### `Compaction._parse_summary`  (lines 539–579)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Extracts and validates the JSON summary produced by the model. It is deliberately tolerant of extra surrounding text, but strict about the final structured object being valid and non-empty.

**Data flow**: It receives raw model text. It finds the first balanced JSON object, validates it as a CompactionSummary, checks that the intent field is not blank, and returns the typed summary. If anything is missing or invalid, it raises a RuntimeError with a clear message.

**Call relations**: Compaction._summarize_once calls this after collecting the model response. If it raises, Compaction._summarize_once wraps the problem as _InvalidSummary so the larger pipeline can account for usage and choose whether to retry, no-op, or fail.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 581–598)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds durable tool-output files mentioned in the summarized head that should still be visible after compaction. It avoids duplicating paths that already remain in the kept tail.

**Data flow**: It receives the head rounds and tail messages. It renders messages to text, searches for tool-output file paths, filters out paths still visible in the tail, removes duplicates, keeps only the most recent bounded set, and returns those paths.

**Call relations**: Compaction._compact.open_boundary calls this while building the boundary. The resulting references are later rendered by Compaction._render into the compacted context message.

*Call graph*: calls 1 internal fn (_text); called by 1 (open_boundary).


##### `Compaction._render`  (lines 600–639)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns a validated CompactionSummary plus system-carried facts into the single replacement message for the old transcript head. It uses stable section headings so later model turns can read the compacted context predictably.

**Data flow**: It receives a summary, durable references, and active request text. It includes only non-empty summary sections, formats lists as bullets, appends references and active requests outside the model-authored summary, and returns one plain text block beginning with the compacted-context prefix.

**Call relations**: Compaction._verify calls this while testing a candidate summary. The same rendered text is also passed through the post-compaction hook after checkpointing.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_verify).


##### `Compaction._bullets`  (lines 641–642)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a list of strings as simple markdown-style bullet lines. It gives summaries and correction prompts a consistent shape.

**Data flow**: It receives a tuple of strings and returns one string where each item is prefixed with “- ” and separated by newlines.

**Call relations**: Compaction._prepare uses this to list missed anchors for a retry. Compaction._render uses it to format summary sections, loaded skills, and durable references.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 644–645)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: Combines a set of messages into the plain text that this file uses for searching and verification. It is the transcript view used for anchors and path checks.

**Data flow**: It receives messages, converts each one through Compaction._text, joins them with newlines, and returns the combined text.

**Call relations**: Compaction._compact.open_boundary uses this to capture pre-compaction text and anchor source text. Compaction._verify uses it to check what the replacement window carries.

*Call graph*: calls 1 internal fn (_text); called by 2 (open_boundary, _verify).


##### `Compaction._verify`  (lines 647–683)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: Checks whether one proposed summary is safe enough to replace the transcript head. It removes invented file paths, renders the replacement, counts tokens, and records any missing anchors.

**Data flow**: It receives a summary, the fixed boundary, and a flag saying whether this is a retry. It keeps only file references that appeared in the original window, injects loaded skills from the tracker, renders the compacted message, attaches the kept tail, computes token counts and missing anchors, and returns a _Candidate containing the checked summary, rendered text, replacement messages, and verification record.

**Call relations**: The compaction harness calls this after each summarization attempt inside Compaction._compact. It hands rendering to Compaction._render and missing-anchor detection to missing_anchors.

*Call graph*: calls 3 internal fn (_render, _window_text, missing_anchors); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._record_verification`  (lines 685–716)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], trigger: Literal['force', 'repeated_tool', 'window'], verification: CompactionVerification) -> None
```

**Purpose**: Reports the quality of a completed compaction. It makes losses and retries visible through logs and metrics rather than hiding them inside saved files.

**Data flow**: It receives the compaction index, reason, trigger label, and verification object. It logs token counts, missing anchors, dropped paths, and retry status, then emits a metric labeled by model, provider, outcome, retry state, and trigger.

**Call relations**: Compaction._compact.checkpoint calls this after persistence has been prepared for an installable candidate. Operators and evaluation tools can later use these records to understand compaction behavior.

*Call graph*: called by 1 (checkpoint); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 718–729)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Saves the full before-and-after compaction artifacts. This preserves an audit trail so old facts are not lost merely because the live transcript was shortened.

**Data flow**: It receives the compaction index, original messages, replacement messages, and typed summary. It writes the before and after windows through Compaction._write, compresses the summary JSON with LZ4, and stores all three blobs under matching keys.

**Call relations**: Compaction._compact.checkpoint calls this when a candidate is ready to install. It uses Compaction._key to place each artifact in the conversation’s compaction storage area.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (checkpoint); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 731–735)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next unused compaction number for this conversation. This keeps each compaction record in order.

**Data flow**: It starts at index 1 and checks blob storage for an existing “after” record at that index. It increments until it finds a free slot and returns that number.

**Call relations**: Compaction._compact.open_boundary calls this before persistence so the boundary and later checkpoint agree on where the compaction will be stored.

*Call graph*: calls 1 internal fn (_key); called by 1 (open_boundary).


##### `Compaction.read_record`  (lines 737–744)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads back a saved compaction record for inspection or evaluation. It returns nothing if that numbered record does not exist.

**Data flow**: It receives an index, fetches the before, after, and summary blobs for that index, and decodes them into a CompactionRecord. If any blob is missing, it returns None.

**Call relations**: This is the read-side companion to Compaction._persist. It uses Compaction._key to locate records and decode_compaction to turn stored compressed data back into a structured object.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 746–754)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes one side of a compaction window, either the original transcript or the replacement transcript. It stores the data in a compact, deterministic JSON form.

**Data flow**: It receives an index, a label of before or after, and messages. It wraps the messages in CompactionWindow, serializes them to sorted compact JSON, compresses the bytes with LZ4, and writes them to blob storage.

**Call relations**: Compaction._persist calls this twice for every completed compaction: once for the pre-compaction window and once for the post-compaction window.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 756–757)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the storage key for a compaction artifact. It centralizes the naming rule for before, after, and summary blobs.

**Data flow**: It receives an index and artifact label. It combines those with the conversation id through the shared compaction_key helper and returns the resulting path-like key.

**Call relations**: Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record all use this so reads and writes point to the same storage locations.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._opaque_chars`  (lines 759–778)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: Estimates the size of hidden reasoning data that still counts against the model window even though it is not useful text for summarization. This prevents token estimates from being dangerously too low.

**Data flow**: It receives one message. If the message is plain text, it returns zero; otherwise it scans structured blocks and adds the lengths of reasoning signatures, redacted data, and encrypted reasoning content. It returns that character count.

**Call relations**: Compaction.window passes this function into ContextWindow so window-size decisions include non-rendered but still transmitted content.


##### `Compaction._image_count`  (lines 780–790)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts images inside a message so the context-window estimate can include their token cost. Images are not normal text, but they still consume model capacity.

**Data flow**: It receives one message. It returns zero for plain text, otherwise scans top-level image blocks and images nested inside tool results, returning the total number found.

**Call relations**: Compaction.window gives this function to ContextWindow, which uses it when deciding whether the transcript is too large.


##### `Compaction._text`  (lines 792–816)

```
def _text(self, message: Message) -> str
```

**Purpose**: Turns a message with mixed content blocks into readable text for summarization, searching, and verification. It uses clear markers for images and redacted reasoning so those items do not silently vanish.

**Data flow**: It receives one message. Plain string content is returned directly; structured blocks are rendered according to their type, including text, thinking summaries, tool results, tool calls with JSON inputs, image markers, and redacted markers. The rendered pieces are joined with newlines.

**Call relations**: Compaction._prepare uses this to build the summarizer prompt, Compaction._references uses it to find durable file paths, and Compaction._window_text uses it to create searchable transcript text.

*Call graph*: called by 3 (_prepare, _references, _window_text); 1 external calls (dumps).


### `core/src/ufo/harness/context.py`

`domain_logic` · `request handling / transcript growth`

AI models can only read a limited amount of text and image information at once. This file is the project’s safety system for that limit. It estimates how large a conversation is, decides whether old parts should be compacted into a summary, chooses which old parts to summarize, and checks that the replacement really made the conversation smaller.

The main idea is like packing a suitcase. The newest items are kept as they are, because they are most likely still needed. Older items are folded down into a smaller bundle, but only if there is enough old material to compact. `ContextWindow` contains the rules for this: how to count approximate tokens, how many recent messages to keep, and where the compaction trigger is. A token is a rough unit of text size used by AI models; this code estimates it from character counts and image counts without needing to know the exact message type.

`CompactionHarness` then performs one compaction attempt. It selects the old “head” and recent “tail,” asks an external summarizer to summarize the head, verifies that the summary covers important anchors, retries if needed, checks the size budget, saves a checkpoint, and returns the new transcript plus any usage records. If the summarizer itself fails because its request is too large, it drops some of the oldest rounds and tries again.

#### Function details

##### `is_context_overflow`  (lines 14–17)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: This function guesses whether an error from an external model means the request was too large for the model’s context window. It looks for common phrases such as “too long” or “maximum context.”

**Data flow**: It takes an exception as input, turns the exception type and message into lowercase text, then searches for known overflow phrases. It returns `True` if one of those phrases appears, otherwise `False`; it does not change anything else.

**Call relations**: `CompactionHarness._summarize` uses this when a summary request fails. If the failure looks like a size-limit problem, the harness can retry with less old conversation; if not, the error is treated as a real failure and is raised.

*Call graph*: called by 1 (_summarize).


##### `ContextWindow.trigger`  (lines 46–50)

```
def trigger(self) -> int
```

**Purpose**: This property gives the token count at which automatic compaction should begin. It either uses an explicitly configured trigger or calculates one from the total context size minus space reserved for the future summary and buffer.

**Data flow**: It reads the window’s settings: `trigger_tokens`, `context_tokens`, `summary_tokens`, and `buffer_tokens`. If a custom trigger exists, that value comes out; otherwise it returns the calculated safe threshold.

**Call relations**: Other parts of `ContextWindow`, especially budget checks and compaction decisions, rely on this threshold to know when the transcript is too large. It acts as the warning line before the model’s hard limit.


##### `ContextWindow.should_compact`  (lines 52–66)

```
def should_compact(self, messages: tuple[MessageT, ...], *, force: bool, automatic_suppressed: bool, automatic_trigger_tokens: int | None=None) -> bool
```

**Purpose**: This function decides whether the current conversation should be compacted now. It respects both size pressure and caller intent, including forced compaction and situations where automatic compaction has been temporarily suppressed.

**Data flow**: It receives the current messages and flags that say whether compaction is forced, suppressed, or using a temporary trigger. It first refuses to compact if there are too few messages to safely keep a recent tail. If compaction is not forced and automatic compaction is suppressed, it also refuses. Otherwise it estimates the transcript size with `ContextWindow.tokens` and compares it with the trigger, returning `True` or `False`.

**Call relations**: This is the gatekeeper a caller would use before running the heavier compaction process. It calls `ContextWindow.tokens` because the decision depends on the estimated size of the transcript.

*Call graph*: calls 1 internal fn (tokens).


##### `ContextWindow.select`  (lines 68–82)

```
def select(self, messages: tuple[MessageT, ...]) -> WindowSelection[MessageT] | None
```

**Purpose**: This function splits a conversation into two parts: older rounds to summarize and recent messages to keep exactly as they are. It keeps whole conversation rounds so the remaining tail does not start in the middle of an exchange.

**Data flow**: It takes all messages, groups them into rounds using `ContextWindow.rounds`, then walks backward from the newest round until it has kept at least `keep_messages` messages. If there is no older material left to summarize, it returns `None`. Otherwise it returns a `WindowSelection` containing the old head and the retained tail.

**Call relations**: `CompactionHarness.run` starts by calling this to find out what can be replaced by a summary. Internally, it calls `ContextWindow.rounds` so the split happens along meaningful conversation boundaries.

*Call graph*: calls 1 internal fn (rounds); 1 external calls (__init__).


##### `ContextWindow.rounds`  (lines 84–96)

```
def rounds(self, messages: tuple[MessageT, ...]) -> tuple[tuple[MessageT, ...], ...]
```

**Purpose**: This function groups raw messages into conversation rounds. A new round begins when an assistant message appears after earlier messages, so assistant turns and the user or tool results around them stay together.

**Data flow**: It receives a tuple of messages and uses the configured `role` reader to ask each message whether it is from the assistant or not. It builds small tuples of messages as rounds, then returns all rounds as one tuple. The original messages are not modified.

**Call relations**: `ContextWindow.select` uses this before deciding what to summarize and what to keep. This matters because compaction should not cut through a logical exchange if it can avoid it.

*Call graph*: called by 1 (select).


##### `ContextWindow.drop_oldest`  (lines 98–102)

```
def drop_oldest(self, rounds: tuple[tuple[MessageT, ...], ...]) -> tuple[tuple[MessageT, ...], ...]
```

**Purpose**: This function removes an oldest slice of already-selected rounds when even the summarization request is too large. It is a recovery step, not the normal compaction path.

**Data flow**: It takes the rounds that were going to be summarized and drops at least one old round, or roughly one configured fraction of the head. It returns the smaller remaining set of rounds.

**Call relations**: `CompactionHarness._summarize` uses this after a context-overflow error from the summarizer. The harness then retries with the reduced input, giving the system a chance to compact something rather than failing immediately.


##### `ContextWindow.tokens`  (lines 104–119)

```
def tokens(self, messages: tuple[MessageT, ...]) -> int
```

**Purpose**: This function estimates how many model tokens a set of messages will cost. It is deliberately generic: callers provide small reader functions so this file does not need to know the project’s exact message classes.

**Data flow**: It receives messages and, for each one, reads the role text, visible message text, any extra opaque character cost, and image count. It converts characters to approximate tokens using `chars_per_token`, adds a fixed token cost for images, and returns the total. If `chars_per_token` is invalid, it raises an error instead of making a bad estimate.

**Call relations**: `ContextWindow.should_compact` uses this to decide whether the transcript has crossed the trigger. `CompactionHarness.run` also relies on token estimates before and after compaction so the budget check can prove the replacement helped.

*Call graph*: called by 1 (should_compact).


##### `ContextWindow.require_budget`  (lines 121–140)

```
def require_budget(self, *, before_tokens: int, after_tokens: int, tail_tokens: int, fixed_replacement_tokens: int) -> None
```

**Purpose**: This function checks that a compaction result is actually useful and safe. It rejects a replacement that does not shrink the transcript when it had room to shrink, or that still remains over the automatic compaction trigger when it should have fit.

**Data flow**: It receives token counts from before compaction, after compaction, the retained tail, and the fixed cost of the replacement wrapper. It calculates how large the old head was and how much room the replacement should need. If the result fails the size expectations, it raises a `RuntimeError`; otherwise it returns normally with no value.

**Call relations**: `CompactionHarness.run` calls this after building the candidate compacted transcript and before saving it. This makes the size check a final safety gate before the new transcript becomes official.


##### `CompactionHarness.run`  (lines 173–201)

```
async def run(self, messages: tuple[MessageT, ...]) -> CompactionResult[MessageT, UsageT]
```

**Purpose**: This function performs one full compaction attempt from start to finish. It chooses what to summarize, asks for a summary, verifies it, checks the size budget, saves a checkpoint, and returns the installed transcript with usage information.

**Data flow**: It takes the current messages. First it asks `ContextWindow.select` for an old head and recent tail; if there is nothing safe to compact, it returns the original messages and no usage. Otherwise it estimates the old size, calls `_summarize`, opens a boundary object supplied by the caller, verifies the summary, and asks whether any important anchors are missing. If anchors are missing, it tries a second summary request focused on those anchors; if that retry fails, it records failed usage and marks the candidate through `retry_failed`. Then it gets the final messages from the candidate, checks the token budget, checkpoints the result, and returns `CompactionResult`.

**Call relations**: This is the main coordinator in the file. It calls `CompactionHarness._summarize` for model-facing summary attempts and relies on caller-provided callbacks for project-specific work such as verifying summaries, opening boundaries, recording usage, and checkpointing.

*Call graph*: calls 1 internal fn (_summarize); 1 external calls (__init__).


##### `CompactionHarness._summarize`  (lines 203–220)

```
async def _summarize(self, head: tuple[tuple[MessageT, ...], ...], missing: tuple[AnchorT, ...]) -> tuple[SummaryT, UsageT]
```

**Purpose**: This helper asks the external summarizer to summarize selected old conversation rounds, with a special retry path for “prompt too large” failures. It tries to salvage compaction by dropping some oldest material if the summarizer cannot accept the full request.

**Data flow**: It receives the head rounds to summarize and any missing anchors that the next summary should cover. It calls the configured `summarize` function. If summarizing succeeds, it returns the summary and usage record. If it fails, it uses `is_context_overflow` to decide whether the failure was caused by an oversized request. For overflow errors, while retries remain and more than one round is present, it drops old rounds with `ContextWindow.drop_oldest` and tries again. Other errors, exhausted retries, or too little material cause the exception to be raised.

**Call relations**: `CompactionHarness.run` calls this for the first summary and possibly for a second anchor-focused retry. This helper is where context-overflow recovery is isolated, so the main compaction flow can stay focused on selection, verification, budgeting, and checkpointing.

*Call graph*: calls 1 internal fn (is_context_overflow); called by 1 (run).
