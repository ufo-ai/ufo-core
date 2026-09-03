# Conversation compaction and context-window management  `stage-8.2`

This stage is behind-the-scenes support for long-running conversations. AI models can only read a limited amount of text at once; this limit is called the context window. When the transcript grows too large, the system must make room without losing the thread of the work.

The context helper checks the conversation size before sending it to the model. If it is too big, it chooses an older section to shorten while leaving the newest messages untouched, like keeping the latest pages of a notebook open and filing older pages into a brief report. It also checks that the shortened version is actually smaller, and it can retry safely if even the request to summarize is too large.

The compaction logic does the actual shrinking. It asks for a structured summary of the older transcript, validates that the summary has the expected shape, then replaces that older text with the summary. It also records what was replaced, so the system has a durable trail of the change.

## Files in this stage

### Conversation Compaction
Detects oversized conversations, summarizes older transcript content, preserves recent messages, validates shrinkage, retries safely, and records what was replaced.

### `core/src/ufo/runtime/compaction.py`

`domain_logic` · `request handling, before sending an oversized conversation to the model`

Language models can only read a limited amount of text at once. When a conversation grows too large, the system cannot simply send the whole history anymore. This file solves that by doing transcript compaction: it summarizes the older “head” of the conversation and keeps the recent “tail” exactly as it was, like packing old papers into a labeled archive box while leaving today’s paperwork on the desk.

The compaction flow is careful because a bad summary could lose important facts. It first decides whether the conversation is too large for the current model. If so, it chooses a safe boundary, gathers important “anchors” such as tool output file paths, active request references, loaded skills, and error names, then asks the model to produce a structured JSON summary. It also replaces images and hidden reasoning data with markers so the summarizer knows they existed without trying to copy unusable binary or encrypted content.

After the model replies, the file validates the summary, removes invented file paths, renders a replacement message, and checks whether required anchors survived in either the summary or the kept tail. If facts are missing, it can retry once with explicit instructions to include them. Finally, it writes compressed before/after/summary records to blob storage and logs verification metrics. Without this file, long conversations would either fail when they overflow the model window or be shortened in a much less auditable and less reliable way.

#### Function details

##### `harvest_anchors`  (lines 104–130)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: Finds small but important facts from the part of the transcript that may be summarized away. These facts become checkpoints used later to judge whether the summary carried forward what mattered.

**Data flow**: It receives rendered transcript text, names of loaded skills, and active request text. It scans for durable tool-output file paths, error class names, skill names, and message references, keeps only a bounded recent set for each kind, and returns them as Anchor objects.

**Call relations**: During compaction, open_boundary calls this before any summary exists. The result is later used by verification, so the model’s own summary cannot define the test it must pass.

*Call graph*: called by 1 (open_boundary); 1 external calls (__init__).


##### `missing_anchors`  (lines 133–137)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: Checks which required anchor facts did not survive in the replacement conversation text. It is a simple literal containment test, not a fuzzy similarity check.

**Data flow**: It receives the expected anchors and the text that will be carried forward. It returns only those anchors whose exact text is absent.

**Call relations**: Compaction._verify calls this after rendering a candidate compacted window. Its answer decides whether the candidate is clean, lossy, or needs a retry.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 176–180)

```
def __repr__(self) -> str
```

**Purpose**: Produces a short debug-friendly label for a compaction request. It avoids printing the full conversation contents.

**Data flow**: It reads the request’s message count, reason, and active request count. It returns a compact string with those counts.

**Call relations**: This is used implicitly by Python when the request is logged or displayed. It supports observability without leaking large transcript data.


##### `_InvalidSummary.__init__`  (lines 184–187)

```
def __init__(self, error_class: str, message: str, usage: Usage) -> None
```

**Purpose**: Creates a specific error for a model summary that could not be accepted. It preserves both the reason and the token usage from the failed model call.

**Data flow**: It receives an error class name, a message, and a Usage record. It stores those values on the exception and passes the key details to the base RuntimeError.

**Call relations**: Compaction._summarize_once raises this when parsing or validation fails. Compaction._compact can then distinguish an invalid summary from other failures and decide whether to fall back or raise.

*Call graph*: called by 1 (_summarize_once).


##### `_InvalidSummary.__str__`  (lines 189–190)

```
def __str__(self) -> str
```

**Purpose**: Returns the human-readable error message for an invalid summary. This makes logs and exception text focus on the useful explanation.

**Data flow**: It reads the message stored in the exception arguments and returns it as text.

**Call relations**: This is used implicitly whenever the exception is converted to a string, such as in logs or retry diagnostics.


##### `Compaction.window`  (lines 226–242)

```
def window(self) -> ContextWindow[Message]
```

**Purpose**: Builds the context-window calculator for the model currently serving the turn. This calculator knows when the transcript is too large and how to split it.

**Data flow**: It reads the current serving model’s context size and this compaction object’s token-budget settings. It returns a ContextWindow configured with helper functions for reading message text, counting images, and counting hidden opaque content.

**Call relations**: Compaction.maybe_compact and Compaction._compact rely on this property when deciding whether to compact, estimating token counts, and proving the replacement is smaller.

*Call graph*: 1 external calls (__init__).


##### `Compaction.__repr__`  (lines 244–245)

```
def __repr__(self) -> str
```

**Purpose**: Produces a short readable description of the compaction object. It identifies the conversation and model without dumping internal state.

**Data flow**: It reads the conversation id and serving model name. It returns a formatted string.

**Call relations**: This is used implicitly by Python when the object is shown in logs, traces, or debugging output.


##### `Compaction.maybe_compact`  (lines 247–274)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether compaction should run for the current message window. It is the safe public entry to the compaction workflow.

**Data flow**: It receives the current messages, a force flag, and any active requests. It asks the ContextWindow whether compaction is needed; if not, it returns the original messages and no usage records. If compaction is needed, it builds a _CompactionRequest, calls Compaction._compact, and returns the resulting message window plus any model usage.

**Call relations**: Higher-level runtime code calls this before a model request or after a context-overflow failure. It hands real work to Compaction._compact, and it suppresses repeated automatic attempts in the same turn if an automatic attempt spent tokens but did not change the window.

*Call graph*: calls 1 internal fn (_compact); 1 external calls (__init__).


##### `Compaction._compact`  (lines 277–391)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction pipeline: choose a boundary, summarize the old transcript head, verify the result, persist records, and return the compacted window. It is wrapped as a DBOS step, meaning a crash-retry can replay the recorded result instead of spending tokens again.

**Data flow**: It receives a _CompactionRequest containing messages, the reason, and active requests. It sets up callbacks for boundary creation, retry handling, verification, persistence, and model summarization, then lets CompactionHarness drive the process. It returns the final message tuple and usage records, or returns the original messages after a failed automatic summary.

**Call relations**: Compaction.maybe_compact calls this only when compaction is needed. Inside, it coordinates open_boundary, Compaction._summarize_once, Compaction._verify, retry_failed, checkpoint, and error logging.

*Call graph*: called by 1 (maybe_compact); 1 external calls (log_error).


##### `Compaction._compact.open_boundary`  (lines 297–324)

```
async def open_boundary(head: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...], before_tokens: int) -> _Boundary
```

**Purpose**: Prepares the fixed facts about the split between old messages to summarize and recent messages to keep. This creates the standard that later summaries must meet.

**Data flow**: It receives grouped head messages, the tail messages, and the original token count. It picks the next storage index, fires a pre-compaction hook, drains loaded skills, collects durable references, renders pre-compaction text, harvests anchors, and returns a _Boundary object.

**Call relations**: CompactionHarness calls this when it has chosen a split. It calls Compaction._next_index, Compaction._references, Compaction._window_text, and harvest_anchors so later verification has stable facts to compare against.

*Call graph*: calls 4 internal fn (_next_index, _references, _window_text, harvest_anchors); 3 external calls (__init__, __init__, from_iterable).


##### `Compaction._compact.retry_failed`  (lines 326–333)

```
def retry_failed(candidate: _Candidate, error: Exception) -> _Candidate
```

**Purpose**: Marks a candidate summary as having spent its retry when the anchor-repair retry itself fails. It keeps the previous candidate installable instead of throwing it away.

**Data flow**: It receives the previous candidate and the retry error. It logs a warning, updates the verification to say a retry was attempted, copies that verification into the summary, and returns the updated candidate.

**Call relations**: CompactionHarness calls this when a retry meant to recover missing anchors fails. It is the fallback path that lets the workflow continue with the best available verified candidate.

*Call graph*: 2 external calls (replace, warn).


##### `Compaction._compact.checkpoint`  (lines 335–350)

```
async def checkpoint(candidate: _Candidate, boundary: _Boundary) -> None
```

**Purpose**: Commits a successful compaction result. It saves the before/after/summary files, records verification information, and notifies hooks that compaction happened.

**Data flow**: It receives a candidate and its boundary. It writes compressed records through Compaction._persist, logs metrics through Compaction._record_verification, fires the post-compaction hook, and returns nothing.

**Call relations**: CompactionHarness calls this after a candidate has passed the workflow’s checks. It is the point where an in-memory candidate becomes durable system history.

*Call graph*: calls 2 internal fn (_persist, _record_verification); 1 external calls (__init__).


##### `Compaction._summarize_once`  (lines 393–421)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Asks the serving model for one structured summary of the old transcript head. It also turns bad model output into a special invalid-summary error that includes usage data.

**Data flow**: It receives grouped transcript rounds and any anchors missed by a previous attempt. It prepares a user prompt, builds a ModelRequest, streams text chunks from the model client, captures usage, parses the accumulated text into a CompactionSummary, and returns the summary plus usage.

**Call relations**: CompactionHarness calls this during Compaction._compact whenever it needs a summary attempt. It uses Compaction._prepare before the model call and Compaction._parse_summary after the model replies.

*Call graph*: calls 3 internal fn (_parse_summary, _prepare, __init__); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 423–445)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: Turns the transcript head into the exact text sent to the summarizer. It keeps the important visible content while shrinking repeated noise and adding retry instructions when needed.

**Data flow**: It receives grouped messages and missed anchors. It renders each message as role plus text, folds long repeated runs, appends a correction section if anchors were missed, appends a final instruction to output only JSON, and returns one prompt string.

**Call relations**: Compaction._summarize_once calls this before contacting the model. It uses Compaction._text to render message blocks, Compaction._fold_repeated_runs to reduce repetitive bulk, and Compaction._bullets for retry anchor lists.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 447–460)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Compresses obvious repeated text inside the prompt sent to the summarizer. This prevents spammy or looping output from making the summary request too large or likely to be refused.

**Data flow**: It receives a text string. It finds short word sequences repeated many times in a row and replaces each run with one copy plus a marker saying how many times it repeated.

**Call relations**: Compaction._prepare calls this after rendering the transcript head. It delegates each regex match to its nested fold helper.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 455–458)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement text for one repeated run. It keeps one copy of the repeated unit and records the repeat count.

**Data flow**: It receives a regular-expression match. It extracts the repeated unit, calculates how many times it appeared, and returns text like the unit followed by a repeated-count marker.

**Call relations**: This helper is called by the regex substitution inside Compaction._fold_repeated_runs. It exists only as part of that text-shrinking pass.


##### `Compaction._parse_summary`  (lines 462–502)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Extracts and validates the model’s JSON summary. It is tolerant of extra text around the JSON, but strict about the summary shape and requiring a non-empty intent.

**Data flow**: It receives raw model text. It finds the first balanced JSON object, validates it as a CompactionSummary, rejects invalid or empty summaries, and returns the typed summary object.

**Call relations**: Compaction._summarize_once calls this after streaming the model response. If parsing fails, the caller wraps the failure in _InvalidSummary so the compaction flow can handle it cleanly.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 504–521)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds durable tool-output file paths in the old head that should remain available after that head is summarized. It avoids repeating paths that are still visible in the kept tail.

**Data flow**: It receives head message rounds and tail messages. It renders messages to text, collects matching tool-output paths from the head, skips ones already visible in the tail, keeps only recent unique paths, and returns them.

**Call relations**: open_boundary calls this while preparing the compaction boundary. Compaction._render later includes these references in the replacement message so the model can re-read large outputs when needed.

*Call graph*: calls 1 internal fn (_text); called by 1 (open_boundary).


##### `Compaction._render`  (lines 523–562)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns a validated CompactionSummary plus system-carried facts into the single user message that replaces the old transcript head. The output is deterministic, so the same inputs always produce the same compacted text.

**Data flow**: It receives a summary, durable references, and active requests. It creates readable sections for intent, concepts, files, errors, decisions, pending work, current work, next step, loaded skills, references, and active requests, then returns one compacted-context string.

**Call relations**: Compaction._verify calls this to build the candidate replacement window. The rendered text is also what post-compaction hooks observe as the summary.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_verify).


##### `Compaction._bullets`  (lines 564–565)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a list of strings as simple bullet lines. It keeps rendered summary sections and retry instructions consistent.

**Data flow**: It receives a tuple of strings. It prefixes each item with “- ” and joins them with newlines.

**Call relations**: Compaction._prepare uses this for missed-anchor retry instructions. Compaction._render uses it for summary sections and durable reference lists.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 567–568)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: Renders a whole sequence of messages into plain text for scanning and verification. It gives the compaction code one simple text view of mixed message content.

**Data flow**: It receives messages. It renders each one with Compaction._text and joins the results with newlines.

**Call relations**: open_boundary uses this to capture pre-compaction text and head text. Compaction._verify uses it to check whether anchors survived in the replacement window.

*Call graph*: calls 1 internal fn (_text); called by 2 (open_boundary, _verify).


##### `Compaction._verify`  (lines 570–606)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: Checks one candidate summary before it can replace live conversation history. It removes file paths the model appears to have invented and measures what important anchors are missing.

**Data flow**: It receives a summary, a boundary, and a flag saying whether this is a retry. It keeps only summary file references that appeared in the original window, inserts drained loaded skills, renders the replacement message, combines it with the tail, counts tokens, finds missing anchors, records dropped paths, and returns a _Candidate containing the checked summary, rendered text, after-window, and verification.

**Call relations**: CompactionHarness calls this after each summary attempt in Compaction._compact. It uses Compaction._render, Compaction._window_text, and missing_anchors, and its result drives retry and checkpoint decisions.

*Call graph*: calls 3 internal fn (_render, _window_text, missing_anchors); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._record_verification`  (lines 608–631)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], verification: CompactionVerification) -> None
```

**Purpose**: Logs the quality check for a completed compaction. This makes loss, retries, token savings, and dropped paths visible to operators and metrics dashboards.

**Data flow**: It receives the compaction index, reason, and verification object. It writes a structured log with token counts and missing facts, then emits a metric labeled clean or lossy and retried or not.

**Call relations**: checkpoint calls this after persisting a candidate. It is the observability bridge from the compaction workflow to fleet-level monitoring.

*Call graph*: called by 1 (checkpoint); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 633–644)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Saves the full compaction record: what the window looked like before, what replaced it, and the structured summary. This makes compaction auditable after the live transcript has moved on.

**Data flow**: It receives an index, before messages, after messages, and a summary. It writes compressed before and after windows through Compaction._write, then writes the compressed summary JSON to blob storage.

**Call relations**: checkpoint calls this once a candidate is accepted. It uses Compaction._key to choose storage locations and Compaction._write for the message-window files.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (checkpoint); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 646–650)

```
async def _next_index(self) -> int
```

**Purpose**: Chooses the next unused compaction number for this conversation. This keeps multiple compaction records ordered and prevents overwriting older ones.

**Data flow**: It starts at index 1 and checks blob storage for an existing “after” file at each index. It returns the first index with no existing record.

**Call relations**: open_boundary calls this before firing compaction hooks and building the boundary. It uses Compaction._key to ask about the correct blob path.

*Call graph*: calls 1 internal fn (_key); called by 1 (open_boundary).


##### `Compaction.read_record`  (lines 652–659)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads a saved compaction record back from storage. This is useful for audits, evaluations, debugging, or any tool that needs to inspect what compaction changed.

**Data flow**: It receives a compaction index. It tries to fetch compressed before, after, and summary blobs; if any are missing it returns None. Otherwise it decodes them into a CompactionRecord and returns it.

**Call relations**: External readers can call this when they need historical compaction details. It uses Compaction._key for blob paths and decode_compaction to turn stored bytes into typed data.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 661–669)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes one side of a compaction window, either before or after, to compressed blob storage. It stores message windows in a stable JSON form.

**Data flow**: It receives an index, a label saying before or after, and messages. It wraps messages in a CompactionWindow, serializes them to compact sorted JSON, compresses the bytes with LZ4, and stores them under the generated key.

**Call relations**: Compaction._persist calls this twice for each completed compaction. It uses Compaction._key to decide where the blob belongs.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 671–672)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the storage key for one compaction artifact. It centralizes the naming scheme for before, after, and summary files.

**Data flow**: It receives a compaction index and artifact kind. It combines them with the conversation id through compaction_key and returns the blob path string.

**Call relations**: Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record all call this so they agree on where compaction records live.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._opaque_chars`  (lines 674–693)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: Estimates hidden non-text content that still costs context space, especially reasoning or encrypted blocks. This prevents the token estimate from pretending that invisible payloads are free.

**Data flow**: It receives one message. If the content is plain text, it returns zero. If the content is block-based, it adds the lengths of thinking signatures, redacted data, and encrypted reasoning content, then returns the total character count.

**Call relations**: Compaction.window passes this function to ContextWindow. The window calculator uses it when deciding whether the transcript is too large.


##### `Compaction._image_count`  (lines 695–705)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts images inside a message so the token estimate can include their approximate cost. Images are not plain text, but they still consume model context.

**Data flow**: It receives one message. It returns zero for plain text, otherwise counts direct image blocks and image parts nested inside tool results.

**Call relations**: Compaction.window passes this function to ContextWindow. The window calculator combines the image count with an estimated per-image token cost.


##### `Compaction._text`  (lines 707–731)

```
def _text(self, message: Message) -> str
```

**Purpose**: Converts a message with mixed content blocks into plain text that can be summarized, scanned, or logged. It uses markers for things like images and redacted reasoning so they do not disappear silently.

**Data flow**: It receives one message. Plain string content is returned directly; block content is rendered by extracting text, thinking summaries, tool results, tool-call names and arguments, or marker text for images and redacted reasoning, then joining those pieces with newlines.

**Call relations**: Compaction._prepare uses this to build the summarizer prompt. Compaction._references and Compaction._window_text use it to scan transcript content for paths, anchors, and verification text.

*Call graph*: called by 3 (_prepare, _references, _window_text); 1 external calls (dumps).


### `core/src/ufo/harness/context.py`

`domain_logic` · `request handling, when a conversation window grows near or beyond the model limit`

AI models can only read a limited amount of text and images at once. This file is the project’s safety system for that limit. It treats a conversation like a suitcase: when it gets too full, the older clothes are folded into a compact bundle, while the newest items stay easy to reach.

The main piece, `ContextWindow`, knows how to estimate the size of messages, decide whether compaction should happen, split the conversation into whole “rounds,” and choose which old rounds should be summarized. A round is a meaningful chunk of conversation, grouped so the system does not cut through the middle of an assistant response and its related follow-up results.

`CompactionHarness` runs the actual compaction process, but it does not own the project’s message types or storage rules. Instead, callers give it small callback functions: how to summarize, how to verify the summary, how to build the replacement messages, and how to save a checkpoint. This keeps the file generic.

A key guardrail is budget checking. After compaction, the file verifies that the new transcript either shrank when it needed to, or at least falls back under the trigger size. If not, it raises an error instead of silently continuing with a still-too-large conversation.

#### Function details

##### `is_context_overflow`  (lines 14–17)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: This function guesses whether an error from an outside AI model means “the request was too large.” It is used so the system can react differently to size problems than to ordinary failures.

**Data flow**: It receives an exception. It turns the exception type and message into lowercase text, searches for common phrases such as “too long” or “maximum context,” and returns `true` if any phrase is found. It does not change anything else.

**Call relations**: When `CompactionHarness._summarize` catches an error while asking for a summary, it calls this function to decide whether the right response is to try again with less old conversation, or to give up and let the original error continue.

*Call graph*: called by 1 (_summarize).


##### `ContextWindow.trigger`  (lines 46–50)

```
def trigger(self) -> int
```

**Purpose**: This property gives the token limit at which automatic compaction should begin. A token is a rough piece of text used by AI models, often part of a word.

**Data flow**: It reads the window settings. If an explicit trigger was provided, it returns that. Otherwise, it calculates the trigger by taking the model’s context size and subtracting space reserved for the future summary and a safety buffer.

**Call relations**: Other `ContextWindow` checks use this value as the line where the transcript is considered too full. It is the measuring mark that tells compaction when to start.


##### `ContextWindow.should_compact`  (lines 52–64)

```
def should_compact(self, messages: tuple[MessageT, ...], *, force: bool, automatic_suppressed: bool) -> bool
```

**Purpose**: This function decides whether a given set of messages should be compacted now. It considers both the size of the transcript and practical rules such as keeping a minimum number of recent messages.

**Data flow**: It receives the current messages plus two flags: whether compaction is forced, and whether automatic compaction is currently suppressed. It first refuses if there are not enough messages to safely remove older ones. It then refuses automatic compaction if suppression is active. Otherwise, it returns `true` if compaction was forced or if the estimated token count is above the trigger.

**Call relations**: This is the gatekeeper before compaction work begins. It calls `ContextWindow.tokens` to estimate the conversation size when compaction is not forced, using the trigger value as the comparison point.

*Call graph*: calls 1 internal fn (tokens).


##### `ContextWindow.select`  (lines 66–80)

```
def select(self, messages: tuple[MessageT, ...]) -> WindowSelection[MessageT] | None
```

**Purpose**: This function chooses which part of a transcript should be summarized and which recent messages should be kept exactly as they are. It protects the latest conversation from being altered.

**Data flow**: It receives all messages. It first groups them into whole rounds, then walks backward from the newest rounds until it has kept at least the configured minimum number of messages. Everything older becomes the `head` to summarize, and the kept newest messages become the `tail`. If there is nothing old enough to summarize, it returns nothing.

**Call relations**: This is used by `CompactionHarness.run` before asking for a summary. It calls `ContextWindow.rounds` so that selection happens on conversation chunks rather than arbitrary individual messages, then returns a `WindowSelection` describing the old head and preserved tail.

*Call graph*: calls 1 internal fn (rounds); 1 external calls (__init__).


##### `ContextWindow.rounds`  (lines 82–94)

```
def rounds(self, messages: tuple[MessageT, ...]) -> tuple[tuple[MessageT, ...], ...]
```

**Purpose**: This function splits a message list into natural conversation rounds. The goal is to avoid cutting the transcript in a confusing place.

**Data flow**: It receives messages in order. It starts a new round whenever it sees an assistant message after already collecting earlier messages in the current round. At the end, it returns a tuple of rounds, where each round is a tuple of messages.

**Call relations**: It is called by `ContextWindow.select`, which needs these rounds before deciding what can be summarized and what should remain untouched.

*Call graph*: called by 1 (select).


##### `ContextWindow.drop_oldest`  (lines 96–100)

```
def drop_oldest(self, rounds: tuple[tuple[MessageT, ...], ...]) -> tuple[tuple[MessageT, ...], ...]
```

**Purpose**: This function removes a bounded oldest slice of the rounds before retrying a summary request that was itself too large. It is a recovery step for when even the compaction prompt cannot fit the model.

**Data flow**: It receives the rounds planned for summarization. It drops at least one old round, or a fraction based on the configured denominator, and returns the remaining newer rounds.

**Call relations**: It is used inside `CompactionHarness._summarize` after the outside model reports a context overflow. The next retry summarizes a smaller set of rounds.


##### `ContextWindow.tokens`  (lines 102–117)

```
def tokens(self, messages: tuple[MessageT, ...]) -> int
```

**Purpose**: This function estimates how much model context a list of messages will consume. It works without needing to know the real message class, because callers provide small functions for reading each message’s role, text, hidden size, and image count.

**Data flow**: It receives messages. For each message, it adds the lengths of the role text, visible text, and opaque extra characters, converts that character count into an estimated token count, and adds a fixed cost for each image. It returns the total estimate. If the character-per-token setting is invalid, it raises an error.

**Call relations**: It supports size decisions throughout the file. `ContextWindow.should_compact` uses it to decide whether the trigger was crossed, and the compaction flow uses the same kind of estimate when checking that the replacement window fits.

*Call graph*: called by 1 (should_compact).


##### `ContextWindow.require_budget`  (lines 119–138)

```
def require_budget(self, *, before_tokens: int, after_tokens: int, tail_tokens: int, fixed_replacement_tokens: int) -> None
```

**Purpose**: This function enforces the rule that compaction must actually help. It prevents the system from replacing old messages with a summary that is still too large.

**Data flow**: It receives token counts from before and after compaction, the size of the preserved tail, and the fixed size of the replacement structure. It calculates whether the removed head had enough room to shrink and whether the final result should have fallen below the trigger. If the result fails those expectations, it raises a runtime error; otherwise, it returns normally.

**Call relations**: After `CompactionHarness.run` builds a candidate compacted transcript, it calls this function before saving anything. This makes it a final safety check between “we made a summary” and “we commit the new transcript.”


##### `CompactionHarness.run`  (lines 171–199)

```
async def run(self, messages: tuple[MessageT, ...]) -> CompactionResult[MessageT, UsageT]
```

**Purpose**: This function performs one full compaction attempt from start to finish. It selects old messages, asks for a summary, verifies the result, checks the size budget, saves a checkpoint, and returns the new transcript plus any usage records from the external model.

**Data flow**: It receives the current messages. It asks the window to select an old head and recent tail; if there is no removable head, it returns the original messages and no usage. Otherwise it measures the current size, summarizes the old head, opens a boundary object, verifies the summary, optionally retries if required anchors are missing, builds the replacement messages, checks the token budget, checkpoints the accepted candidate, and returns the compacted messages with usage information.

**Call relations**: This is the main driver in the file. It calls `CompactionHarness._summarize` for the external summary step and relies on caller-provided callbacks for project-specific work such as verification, building the final messages, and checkpointing.

*Call graph*: calls 1 internal fn (_summarize); 1 external calls (__init__).


##### `CompactionHarness._summarize`  (lines 201–218)

```
async def _summarize(self, head: tuple[tuple[MessageT, ...], ...], missing: tuple[AnchorT, ...]) -> tuple[SummaryT, UsageT]
```

**Purpose**: This helper asks the external summarizer to summarize a set of rounds, with special retry behavior for “prompt too large” errors. It makes compaction more robust when the very text being summarized is too big to send all at once.

**Data flow**: It receives the rounds to summarize and any missing anchors that the summary should cover. It calls the supplied summarizer. If summarization succeeds, it returns the summary and usage record. If the model says the request was too large, and retries remain, it drops some of the oldest rounds and tries again. For other errors, or when retries are exhausted, it raises the error.

**Call relations**: `CompactionHarness.run` calls this during the first summary attempt and possibly during a second attempt when verification says something is missing. This helper calls `is_context_overflow` to distinguish size-limit failures from other failures.

*Call graph*: calls 1 internal fn (is_context_overflow); called by 1 (run).
