# Transcript Compaction and Long Conversation Control  `stage-9.2`

This stage is behind-the-scenes support for long-running conversations. Language models can only read a limited amount of text at once, called the context window. When a conversation grows too large, the system must shrink the older history without losing the thread.

The harness context file acts like the traffic controller. It watches the transcript size, decides when it is getting too long, picks which older messages are safe to compress, and starts the replacement process. It makes sure the newest messages stay untouched, because they are usually the most important for the next response.

The runtime compaction file does the careful shrinking work. It turns selected older parts of the transcript into a summary, checks that the summary is acceptable, and stores it in a form the rest of the system can use as part of the conversation. Together, these files let the system keep talking to outside AI models during long sessions while preserving recent detail and keeping earlier facts available in shorter form.

## Files in this stage

### Conversation compaction flow
Runtime compaction creates safe summaries of older transcript regions, while harness context orchestration decides when and how to apply that shortening to stay within model limits.

### `core/src/ufo/runtime/compaction.py`

`domain_logic` · `request handling, when a conversation is about to exceed the model context window`

This file implements transcript compaction, which is like packing older papers from a messy desk into a labeled folder while leaving the current papers on top. When the conversation grows too large for the model, the code chooses an older “head” section to summarize and a recent “tail” section to keep exactly as it was. It asks the model for a structured JSON summary, then checks that important facts survived before allowing that summary to replace the old messages.

The file is careful because summarizing is risky. It looks for “anchors”: literal facts that must not disappear, such as tool output file paths, error names, loaded skill names, and still-active request references. If the first summary misses anchors, the pipeline can retry and explicitly tell the summarizer what it dropped. It also removes file paths the model invented, because made-up paths would become false facts in the next turn.

When compaction succeeds, the system writes three compressed records to blob storage: the full window before compaction, the replacement window after compaction, and the typed summary with verification details. That makes the live conversation shorter, while keeping an audit trail for debugging and evaluation. The workflow also fires hooks before and after real compaction so observers can record what happened.

#### Function details

##### `harvest_anchors`  (lines 105–131)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: Finds small but important facts from the soon-to-be-summarized part of the conversation. These facts act like checklist items the replacement summary must carry forward exactly.

**Data flow**: It receives rendered conversation text, the names of loaded skills, and active member request text. It searches for durable tool output paths, error class names, loaded skills, and message references, keeps only a bounded recent set for each kind, and returns them as Anchor objects.

**Call relations**: During compaction, open_boundary calls this before any summary exists. The resulting anchors are later used by verification to judge whether the summary and kept tail still contain the facts the old head contained.

*Call graph*: called by 1 (open_boundary); 1 external calls (__init__).


##### `missing_anchors`  (lines 134–138)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: Checks which required anchor facts did not make it into the replacement text. It uses exact text containment, not fuzzy matching, because these facts are meant to survive literally.

**Data flow**: It receives a tuple of anchors and a string containing the text carried forward after compaction. It filters the anchors to those whose literal text is absent and returns that missing subset.

**Call relations**: Compaction._verify calls this after rendering a candidate replacement window. Its result decides whether the candidate is clean, lossy, or needs a retry.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 177–181)

```
def __repr__(self) -> str
```

**Purpose**: Produces a short debug-friendly description of a compaction request. It avoids printing the whole conversation and instead shows counts and the reason.

**Data flow**: It reads the request’s messages, reason, and active request count. It returns a compact string such as the number of messages and whether the run is automatic or forced.

**Call relations**: This is used implicitly when the request object is logged or displayed. It supports the larger compaction flow by making diagnostics safer and easier to read.


##### `_InvalidSummary.__init__`  (lines 185–188)

```
def __init__(self, error_class: str, message: str, usage: Usage) -> None
```

**Purpose**: Creates a special error for a model summary that could not be accepted. It stores both the kind of failure and the token usage from the failed model call.

**Data flow**: It receives an error class name, a human-readable message, and Usage information. It stores these on the exception so callers can log the failure and still account for model usage.

**Call relations**: Compaction._summarize_once raises this when parsing or validating the model’s summary fails. Compaction._compact catches it for automatic compaction and can return without changing the transcript.

*Call graph*: called by 1 (_summarize_once).


##### `_InvalidSummary.__str__`  (lines 190–191)

```
def __str__(self) -> str
```

**Purpose**: Shows only the readable failure message when this error is converted to text. This keeps logs focused on what went wrong.

**Data flow**: It reads the message saved in the exception arguments. It returns that message as a string.

**Call relations**: This is used automatically by Python when the exception is printed or logged. It makes invalid-summary failures easier for operators to understand.


##### `Compaction.__post_init__`  (lines 232–250)

```
def __post_init__(self) -> None
```

**Purpose**: Builds the ContextWindow helper that knows how to estimate conversation size and choose compaction boundaries. This setup happens after the Compaction object is created.

**Data flow**: It reads configuration such as model context size, summary reserve, token estimates, message retention count, and helper methods for extracting text, images, and hidden reasoning size. It creates and stores a ContextWindow object on the Compaction instance.

**Call relations**: The rest of Compaction relies on this window helper. maybe_compact asks it whether compaction is needed, and the harness uses it to split the transcript and measure before-and-after size.

*Call graph*: 1 external calls (__init__).


##### `Compaction.__repr__`  (lines 252–253)

```
def __repr__(self) -> str
```

**Purpose**: Returns a short label for a Compaction object. It identifies the conversation and model without dumping internal state.

**Data flow**: It reads the conversation ID and model name. It returns a concise string representation.

**Call relations**: This is used implicitly in debugging and logging contexts. It helps identify which conversation’s compaction workflow is being discussed.


##### `Compaction.maybe_compact`  (lines 255–282)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether to compact now and returns the messages that should be sent onward. It is the safe public entry to this file’s workflow.

**Data flow**: It receives the current messages, a force flag, and active request text. It asks the window helper whether compaction is needed; if not, it returns the original messages and no usage. If yes, it builds a _CompactionRequest, calls _compact, and may suppress further automatic attempts in this turn if compaction spent tokens but did not change the transcript.

**Call relations**: Callers use this before sending a conversation to the model. It delegates the heavy work to Compaction._compact and protects the rest of the system from unnecessary or repeated automatic compaction attempts.

*Call graph*: calls 1 internal fn (_compact); 1 external calls (__init__).


##### `Compaction._compact`  (lines 285–399)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction pipeline: split the window, summarize the old part, verify the replacement, persist the record, and return the shortened transcript. It is wrapped as a durable DBOS step so crash recovery does not repeat expensive model calls or duplicate records.

**Data flow**: It receives a _CompactionRequest containing messages, reason, and active requests. It sets up boundary creation, retry behavior, and checkpoint persistence, then runs CompactionHarness. It returns the replacement messages plus any model usage, or returns the original messages after an automatic invalid-summary failure.

**Call relations**: maybe_compact calls this when compaction should run. Inside it, helper callbacks open the boundary, call the summarizer, verify candidates, write records, fire hooks, and log failures.

*Call graph*: called by 1 (maybe_compact); 1 external calls (log_error).


##### `Compaction._compact.open_boundary`  (lines 305–332)

```
async def open_boundary(head: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...], before_tokens: int) -> _Boundary
```

**Purpose**: Freezes the facts about the compaction boundary before any summary is trusted. This creates the fixed standard that all summary attempts are judged against.

**Data flow**: It receives the head rounds to summarize, the tail to keep, and the old token count. It finds the next record index, fires the pre-compaction hook, drains loaded skills, gathers durable references, renders old text, harvests anchors, and returns a _Boundary object.

**Call relations**: The compaction harness calls this after it has chosen a split. Later verification uses this boundary so retries are judged against the same original facts rather than against a moving target.

*Call graph*: calls 4 internal fn (_next_index, _references, _window_text, harvest_anchors); 3 external calls (__init__, __init__, from_iterable).


##### `Compaction._compact.retry_failed`  (lines 334–341)

```
def retry_failed(candidate: _Candidate, error: Exception) -> _Candidate
```

**Purpose**: Marks a candidate as having spent its anchor retry when the retry attempt itself fails. It preserves the best available candidate instead of throwing away all progress.

**Data flow**: It receives the current candidate and the retry error. It logs a warning, copies the verification with the retried flag set, updates the summary to carry that verification, and returns the updated candidate.

**Call relations**: The compaction harness uses this when an anchor-repair retry fails. This lets checkpointing still install a verified candidate if the harness decides it is acceptable.

*Call graph*: 2 external calls (replace, warn).


##### `Compaction._compact.checkpoint`  (lines 343–358)

```
async def checkpoint(candidate: _Candidate, boundary: _Boundary) -> None
```

**Purpose**: Commits a successful compaction. It writes the before, after, and summary records, logs verification, and fires the post-compaction hook.

**Data flow**: It receives a candidate replacement and its boundary. It persists the old messages, new messages, and summary, records verification metrics, then sends observers the rendered summary and token counts.

**Call relations**: The compaction harness calls this only after a candidate has passed the pipeline’s checks. It hands off to _persist and _record_verification, then notifies hook listeners.

*Call graph*: calls 2 internal fn (_persist, _record_verification); 1 external calls (__init__).


##### `Compaction._summarize_once`  (lines 401–429)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Makes one model call to summarize the selected old conversation head. It also turns the model’s text response into a validated CompactionSummary object.

**Data flow**: It receives grouped message rounds and any anchors missed by a previous attempt. It builds a ModelRequest using the compaction system prompt and prepared transcript text, streams text chunks from the model, captures usage, parses the combined response as a summary, and returns the summary with usage. If parsing fails, it raises _InvalidSummary with the usage included.

**Call relations**: The compaction harness calls this as the summarization step. It relies on _prepare to make the prompt and _parse_summary to validate the response.

*Call graph*: calls 3 internal fn (_parse_summary, _prepare, __init__); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 431–453)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: Turns the old conversation head into the prompt text sent to the summarizer. It makes the transcript readable while shrinking obvious repeated spam.

**Data flow**: It receives rounds of messages and any anchors the prior summary missed. It renders each message as role plus text, folds long repeated runs, optionally appends a correction listing missed anchors, adds a final instruction to answer with JSON only, and returns the full prompt text.

**Call relations**: _summarize_once calls this before contacting the model. It uses _text to render message blocks, _fold_repeated_runs to reduce bulk, and _bullets to format retry corrections.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 455–468)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Compresses long stretches of exactly repeated short text inside the summarizer prompt. This prevents a stuck tool loop or pasted spam from making the summary request too large or likely to be refused.

**Data flow**: It receives rendered text. It searches for repeated word sequences and replaces each run with one copy plus a marker saying how many times it repeated, then returns the shortened text.

**Call relations**: _prepare calls this after rendering the head. Its inner fold function builds the replacement text for each repeated run found by the regular expression.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 463–466)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement for one detected repeated text run. It keeps one instance and records the repeat count.

**Data flow**: It receives a regular-expression match. It extracts the repeated unit, calculates how many copies were present, and returns text like the unit followed by a repeated-count marker.

**Call relations**: This helper is passed to the repeated-run regular expression inside _fold_repeated_runs. It is only used as part of preparing the summarizer input.


##### `Compaction._parse_summary`  (lines 470–510)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Extracts and validates the structured JSON summary returned by the model. It tolerates extra surrounding text but insists that the actual summary matches the expected schema and has an intent.

**Data flow**: It receives raw model output text. It finds the first balanced JSON object, validates it as a CompactionSummary, rejects missing or invalid JSON, and returns the typed summary. On failure it raises a RuntimeError with a clear message.

**Call relations**: _summarize_once calls this after collecting streamed model text. If this fails, _summarize_once wraps the failure in _InvalidSummary so _compact can decide whether to recover or raise.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 512–529)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds durable tool-output file paths from the summarized head that should be carried forward separately from the model-written summary. These paths point to saved large outputs that the model can reread later.

**Data flow**: It receives the head rounds and the kept tail. It first finds paths already visible in the tail, then scans the head for tool-output paths not already visible, keeps unique paths, limits them to the most recent allowed count, and returns them.

**Call relations**: open_boundary calls this while building the boundary. Later _render adds these references to the replacement message outside the model-authored summary.

*Call graph*: calls 1 internal fn (_text); called by 1 (open_boundary).


##### `Compaction._render`  (lines 531–570)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns a validated CompactionSummary plus guaranteed references and active requests into the single replacement message. This is the text that stands in for the old conversation head.

**Data flow**: It receives a summary, durable reference paths, and active request text. It builds readable sections only for fields that have content, appends references and active requests verbatim, prefixes the whole thing as compacted context, and returns the rendered string.

**Call relations**: _verify calls this while building a candidate replacement. The rendered text is also what post-compaction observers receive after checkpointing.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_verify).


##### `Compaction._bullets`  (lines 572–573)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a list of strings as Markdown-style bullet lines. It gives rendered summaries and retry instructions a consistent readable shape.

**Data flow**: It receives a tuple of strings. It prefixes each item with “- ”, joins them with newlines, and returns the resulting text.

**Call relations**: _prepare uses this for missed-anchor retry instructions, and _render uses it for summary sections and reference lists.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 575–576)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: Renders a group of messages into one plain text string. This gives other checks a simple view of what the conversation says.

**Data flow**: It receives messages. It renders each message through _text, joins those pieces with newlines, and returns the combined text.

**Call relations**: open_boundary uses this to capture original window text and head text for anchor harvesting. _verify uses it to check whether anchors survived in the replacement window.

*Call graph*: calls 1 internal fn (_text); called by 2 (open_boundary, _verify).


##### `Compaction._verify`  (lines 578–614)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: Checks one proposed summary before it can replace the old messages. It removes invented file paths, forces tracked loaded skills into the summary, renders the replacement, and measures what was lost.

**Data flow**: It receives a model summary, the frozen boundary, and whether this is a retry. It keeps only summary file references that appeared in the original window, records dropped paths, renders the replacement user message, combines it with the kept tail, counts tokens, finds missing anchors, creates a CompactionVerification, and returns a _Candidate containing all of that.

**Call relations**: The compaction harness calls this after each summarize attempt. It uses _render, _window_text, and missing_anchors, and its candidate is later either retried, checkpointed, or rejected by the harness.

*Call graph*: calls 3 internal fn (_render, _window_text, missing_anchors); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._record_verification`  (lines 616–639)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], verification: CompactionVerification) -> None
```

**Purpose**: Writes an operational record of how well compaction preserved important facts. This makes loss visible in logs and metrics instead of hidden inside the shortened transcript.

**Data flow**: It receives the compaction index, reason, and verification object. It logs token counts, missing anchors, dropped paths, and retry status, then emits a metric marked clean or lossy.

**Call relations**: checkpoint calls this after persisting a successful compaction. It feeds monitoring and fleet-level analysis of compaction quality.

*Call graph*: called by 1 (checkpoint); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 641–652)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Stores the permanent record of a compaction. It saves the transcript before compaction, the transcript after compaction, and the structured summary.

**Data flow**: It receives the record index, before messages, after messages, and summary. It writes the before and after windows through _write, compresses the summary JSON with LZ4, and stores it in the blob store under the summary key.

**Call relations**: checkpoint calls this when a candidate is accepted. It delegates key construction to _key and message-window serialization to _write.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (checkpoint); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 654–658)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next unused numbered compaction slot for this conversation. This keeps multiple compaction records in order.

**Data flow**: It starts at index 1 and checks whether an “after” record already exists at each key. It increments until it finds a missing slot, then returns that index.

**Call relations**: open_boundary calls this before persisting a new compaction. It uses _key to ask the blob store about the standard record locations.

*Call graph*: calls 1 internal fn (_key); called by 1 (open_boundary).


##### `Compaction.read_record`  (lines 660–667)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads a previously saved compaction record. It is useful for inspection, debugging, evaluation, or replaying what changed.

**Data flow**: It receives an index. It fetches compressed before, after, and summary blobs using _key; if any are missing, it returns None. If all are present, it decodes them into a CompactionRecord and returns it.

**Call relations**: This is a read-side helper outside the live compaction path. It relies on the same key scheme used by _persist and hands the raw blobs to decode_compaction.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 669–677)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Serializes and stores either the before or after message window for a compaction record. It writes a compact, compressed JSON representation.

**Data flow**: It receives an index, whether this is the before or after half, and the messages. It wraps the messages in a CompactionWindow, dumps stable JSON, compresses it with LZ4, and stores it in the blob store.

**Call relations**: _persist calls this twice for each accepted compaction. It uses _key so records land in the standard conversation-specific location.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 679–680)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the blob-storage key for one part of a compaction record. It centralizes the naming scheme so reads and writes agree.

**Data flow**: It receives an index and a part name: before, after, or summary. It combines those with the conversation ID through compaction_key and returns the resulting storage path.

**Call relations**: _next_index, _persist, _write, and read_record all call this. It is the common path-maker for every saved compaction artifact.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._opaque_chars`  (lines 682–701)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: Estimates hidden reasoning content that still costs context space even though it is not useful text for the summarizer. This prevents the system from undercounting messages with encrypted or redacted reasoning blocks.

**Data flow**: It receives one message. If the content is plain text, it returns zero. If the message is block-based, it adds the lengths of thinking signatures, redacted data, and encrypted reasoning bodies, then returns the total character count.

**Call relations**: The ContextWindow created in __post_init__ uses this when estimating token load. It helps maybe_compact trigger before the real model request overflows.


##### `Compaction._image_count`  (lines 703–713)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts images inside a message so the window estimator can charge them against the context budget. Images are not plain text, but they still consume model capacity.

**Data flow**: It receives one message. Plain text returns zero. For block-based content, it counts direct image blocks and image parts inside tool results, then returns the total.

**Call relations**: The ContextWindow created in __post_init__ uses this count with a fixed image-token estimate. That affects whether maybe_compact decides compaction is needed.


##### `Compaction._text`  (lines 715–739)

```
def _text(self, message: Message) -> str
```

**Purpose**: Turns a message’s mixed content blocks into readable text for summarizing, reference scanning, and verification. It uses markers for things like images or redacted reasoning so their presence is not silently lost.

**Data flow**: It receives one message. If the message is already a string, it returns it. Otherwise it walks each content block, extracts text, thinking summaries, tool results, tool-use calls, image markers, and redaction markers, joins them with newlines, and returns the rendered text.

**Call relations**: _prepare uses this to build the summarizer prompt, _references uses it to find tool-output paths, and _window_text uses it to render whole windows for anchor checks.

*Call graph*: called by 3 (_prepare, _references, _window_text); 1 external calls (dumps).


### `core/src/ufo/harness/context.py`

`orchestration` · `request handling, when a conversation transcript grows near or past the model context limit`

AI models can only read a limited amount of conversation at one time. This file is the project’s “conversation packing” logic: it keeps the newest messages intact, summarizes older messages, and checks that the replacement really makes the transcript smaller enough to be useful.

The file is deliberately generic. It does not know the project’s exact message type. Instead, callers provide small functions that tell it a message’s role, text, hidden size cost, and image count. That lets `ContextWindow` estimate how large a transcript is and split it into “rounds,” where a round is a chunk of conversation that should stay together.

`CompactionHarness` is the higher-level runner. It selects the old head of the transcript to summarize and the recent tail to keep. It asks an outside summarizer to compress the head, opens a boundary for the replacement, verifies whether the summary covered required anchor points, optionally retries for missing anchors, and finally checkpoints the new transcript. If the summarizer itself is given too much text, the harness can drop some of the oldest rounds and retry.

The important safety feature is budget checking. A compaction that does not actually shrink the transcript, or still leaves it over the trigger when it should fit, is rejected rather than silently installed.

#### Function details

##### `is_context_overflow`  (lines 14–17)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: This function guesses whether an exception means an external AI model rejected a request because the prompt was too large. It looks for common phrases such as “context length” or “prompt is too large” in the error text.

**Data flow**: It receives an exception. It combines the exception’s type name and message, lowercases the text, and searches for known overflow phrases. It returns `true` if one is found, otherwise `false`; it does not change anything else.

**Call relations**: `CompactionHarness._summarize` calls this when a summary request fails. If the failure looks like a too-large prompt, the harness may retry with less old conversation; if not, the original error is allowed to stop the process.

*Call graph*: called by 1 (_summarize).


##### `ContextWindow.trigger`  (lines 46–50)

```
def trigger(self) -> int
```

**Purpose**: This property gives the token level where automatic compaction should begin. A token is a rough unit of model input size, like a word piece rather than a full word.

**Data flow**: It reads the window’s configured limits. If an explicit trigger was supplied, it returns that. Otherwise, it calculates a trigger by taking the total context budget and subtracting space reserved for the future summary and a safety buffer.

**Call relations**: `ContextWindow.should_compact` uses this value to decide whether a transcript has grown large enough to shorten. It acts like the mark on a measuring cup that says “stop before it spills.”


##### `ContextWindow.should_compact`  (lines 52–64)

```
def should_compact(self, messages: tuple[MessageT, ...], *, force: bool, automatic_suppressed: bool) -> bool
```

**Purpose**: This function decides whether the current transcript should be compacted now. It protects very short transcripts, respects a setting that can pause automatic compaction, and otherwise compares the estimated size to the trigger.

**Data flow**: It receives the current messages plus two flags: `force`, meaning compact even if not over the usual limit, and `automatic_suppressed`, meaning do not compact automatically right now. It first refuses to compact if there are not enough messages to keep a safe recent tail. If automatic compaction is suppressed and this is not forced, it returns `false`. Otherwise it estimates the transcript size and returns whether compaction is forced or the size is over the trigger.

**Call relations**: Callers use this as the gate before starting a compaction run. When it needs a size estimate, it calls `ContextWindow.tokens`, then compares that estimate with `ContextWindow.trigger`.

*Call graph*: calls 1 internal fn (tokens).


##### `ContextWindow.select`  (lines 66–80)

```
def select(self, messages: tuple[MessageT, ...]) -> WindowSelection[MessageT] | None
```

**Purpose**: This function chooses which old conversation rounds will be summarized and which recent messages will be kept exactly as they are. It keeps whole rounds so the transcript is not cut in the middle of a meaningful exchange.

**Data flow**: It receives all messages. It groups them into rounds, then walks backward from the newest rounds until it has kept at least the required number of recent messages. Everything before that becomes the head to summarize, and the kept recent messages become the tail. If there is no older head left to summarize, it returns nothing.

**Call relations**: `CompactionHarness.run` uses this as the first step in compaction. Internally it calls `ContextWindow.rounds` to make sensible chunks, then packages the chosen head and tail into a `WindowSelection`.

*Call graph*: calls 1 internal fn (rounds); 1 external calls (__init__).


##### `ContextWindow.rounds`  (lines 82–94)

```
def rounds(self, messages: tuple[MessageT, ...]) -> tuple[tuple[MessageT, ...], ...]
```

**Purpose**: This function splits a transcript into conversation rounds that should be treated as units. In this file’s convention, a new assistant message begins a new round when there is already content collected.

**Data flow**: It receives the full message tuple. It scans messages from oldest to newest, using the configured `role` function to identify assistant messages. When an assistant message starts after existing content, the existing group is closed and a new group begins. It returns a tuple of message groups.

**Call relations**: `ContextWindow.select` calls this before deciding what to summarize and what to keep. The grouping helps prevent compaction from separating an assistant action from the user or tool results that belong with it.

*Call graph*: called by 1 (select).


##### `ContextWindow.drop_oldest`  (lines 96–100)

```
def drop_oldest(self, rounds: tuple[tuple[MessageT, ...], ...]) -> tuple[tuple[MessageT, ...], ...]
```

**Purpose**: This function removes a bounded slice of the oldest rounds before retrying a summary request that was itself too large. It is a recovery tool for the case where even the summarization prompt overflows the model.

**Data flow**: It receives the rounds that were going to be summarized. It drops at least one round, or about one configured fraction of the rounds, from the front. It returns the remaining newer rounds.

**Call relations**: `CompactionHarness._summarize` uses this only after a failed summary request that looks like a context overflow. The next retry asks the model to summarize a smaller head.


##### `ContextWindow.tokens`  (lines 102–117)

```
def tokens(self, messages: tuple[MessageT, ...]) -> int
```

**Purpose**: This function estimates how expensive a set of messages will be in model context space. It does this without knowing the real message class, by using caller-provided functions to read the role, text, extra hidden character cost, and image count.

**Data flow**: It receives messages. For each message, it counts visible role and text characters, adds any caller-reported opaque character cost, converts characters to an approximate token count, and adds a fixed token cost for each image. It returns the total estimated tokens. If `chars_per_token` is invalid, it raises an error.

**Call relations**: `ContextWindow.should_compact` calls this to decide whether a transcript is too large. `CompactionHarness.run` also relies on this estimate when checking that a compacted transcript is actually smaller and under the intended budget.

*Call graph*: called by 1 (should_compact).


##### `ContextWindow.require_budget`  (lines 119–138)

```
def require_budget(self, *, before_tokens: int, after_tokens: int, tail_tokens: int, fixed_replacement_tokens: int) -> None
```

**Purpose**: This function enforces the promise that compaction must either shrink the transcript when it has room to do so, or bring it back under the trigger when the configured budget says that should be possible. It prevents a bad summary from being accepted just because the process completed.

**Data flow**: It receives token counts from before and after compaction, the size of the kept tail, and the fixed token cost of the replacement boundary. It calculates how much of the old head was removed and how much room the replacement summary is expected to have. If the new transcript is not smaller when it should be, or is still over the trigger when it should fit, it raises an error. Otherwise it returns normally.

**Call relations**: `CompactionHarness.run` calls this after building the candidate replacement and before checkpointing it. This makes the budget check a final safety gate before the new transcript is installed.


##### `CompactionHarness.run`  (lines 171–199)

```
async def run(self, messages: tuple[MessageT, ...]) -> CompactionResult[MessageT, UsageT]
```

**Purpose**: This is the main compaction workflow. It turns a long transcript into a shorter one by selecting old content, summarizing it, verifying the summary, checking the size budget, and saving the result.

**Data flow**: It receives the current messages. It asks the window to choose an old head and recent tail. If nothing can be compacted, it returns the original messages with no usage records. Otherwise it measures the original size, asks for a summary of the head, opens a replacement boundary, verifies the summary, and checks whether any required anchors are missing. If anchors are missing, it tries a second summary request focused on those missing items; if that retry fails, it records failure usage and marks the candidate accordingly. It then builds the final messages, checks the budget, checkpoints the accepted result, and returns the new transcript plus usage records from the summarization attempts.

**Call relations**: This method is the coordinator for the file. It calls `_summarize` for external summary attempts, uses the caller-provided hooks to open boundaries, verify candidates, collect missing anchors, record retry failures, build the final transcript, compute fixed replacement cost, and checkpoint the outcome. It returns a `CompactionResult` for the caller to install or inspect.

*Call graph*: calls 1 internal fn (_summarize); 1 external calls (__init__).


##### `CompactionHarness._summarize`  (lines 201–218)

```
async def _summarize(self, head: tuple[tuple[MessageT, ...], ...], missing: tuple[AnchorT, ...]) -> tuple[SummaryT, UsageT]
```

**Purpose**: This helper asks the configured summarizer to summarize old conversation rounds, with special retry behavior for prompts that are too large. It makes the summary step more resilient without hiding unrelated failures.

**Data flow**: It receives the head rounds to summarize and any missing anchors that the summary should cover. It calls the provided `summarize` function. If that succeeds, it returns the summary and usage information. If it fails because the prompt appears too large, and retries remain, and there is more than one round left, it drops some oldest rounds and tries again. If the error is not a context overflow, or retries are exhausted, it re-raises the error.

**Call relations**: `CompactionHarness.run` calls this for the first summary and possibly for a second anchor-focused retry. This helper calls `is_context_overflow` to distinguish “the model could not fit the prompt” from other failures that should not be silently retried in this way.

*Call graph*: calls 1 internal fn (is_context_overflow); called by 1 (run).
