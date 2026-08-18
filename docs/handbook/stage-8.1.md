# Prompt construction and context compaction  `stage-8.1`

This stage happens just before the AI model is called. Its job is to prepare the instructions and conversation history so the model gets the right guidance without being overloaded. Think of it as packing a suitcase: the most important current items stay in full, older items may be folded into a smaller summary, and everything must still be checkable later.

The prompt rendering code builds the final system prompt from templates. A template is text with blanks to fill in, like a form letter. It fills the required slots, checks that no unfinished placeholders remain, and creates a fingerprint, which is a stable ID based on the prompt content so changes can be noticed and traced.

The compaction code watches the conversation length. If the history is too large for the model’s context window, meaning the amount of text the model can read at once, it keeps recent messages unchanged and summarizes older ones. It also records both the full and compacted histories for inspection. The package marker simply makes the prompt code importable.

## Files in this stage

### Context compaction
Compacts long conversation histories by summarizing older messages while preserving recent context and audit records.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling when a conversation window grows too large`

AI models can only read a limited amount of text at once. As a conversation grows, the system may hit that limit and fail. This file solves that by doing transcript compaction: it replaces the older part of the conversation with a structured summary while keeping the recent tail exactly as it was. Think of it like packing old paperwork into a labeled archive box while leaving today’s papers on the desk.

The main class, Compaction, decides when compaction is needed. It estimates how many tokens the current messages use, where a token is a rough unit of model input size. If the conversation is too large, it splits the messages into safe “rounds” so tool calls and their tool results stay together. The old rounds become the head to summarize, and the newest rounds become the tail to keep.

The file then asks the model to produce a CompactionSummary, checks that the model returned valid JSON, and verifies that important literal facts were not lost. These important facts include tool output file paths, error names, loaded skills, and active request references. If the summary missed something, it tries once more with explicit instructions to keep those facts.

Finally, it makes sure the compacted window is actually smaller, writes compressed before/after/summary records to blob storage, logs the verification result, and returns the new message window to use for the next model call.

#### Function details

##### `is_context_overflow`  (lines 102–108)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: Detects whether an error means the AI provider rejected a request because the input was too large. This lets the compaction flow shrink and retry instead of treating the failure as unrelated.

**Data flow**: It receives an exception, combines the exception class name and message into lowercase text, then looks for phrases such as “context length” or “prompt is too large”. It returns true when the error appears to be a context-window overflow, and false otherwise.

**Call relations**: Compaction._summarize calls this after a summarization attempt fails. If this detector says the prompt was too large, the summarizer drops older rounds and tries again; otherwise the error is allowed to stop the process.

*Call graph*: called by 1 (_summarize).


##### `harvest_anchors`  (lines 111–137)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: Finds important literal facts in the old conversation that the compacted version must still carry forward. These anchors are used to grade whether the summary lost something load-bearing.

**Data flow**: It receives rendered text from the head of the transcript, the names of loaded skills, and active request text. It searches for durable tool-output paths, error class names, skill names, and message references, keeps only a bounded recent set for each kind, and returns Anchor objects describing those facts.

**Call relations**: Compaction._compact calls this after selecting the transcript head. The anchors it returns are later checked by Compaction._verify through missing_anchors, so the model’s own summary cannot define what counts as preserved.

*Call graph*: called by 1 (_compact); 1 external calls (__init__).


##### `missing_anchors`  (lines 140–144)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: Checks which required literal facts did not appear in the compacted text. It is a strict check: the exact text must be present.

**Data flow**: It receives a tuple of anchors and a string representing what will be carried forward. It tests each anchor’s literal text against that string and returns only the anchors that are absent.

**Call relations**: Compaction._verify calls this while grading a proposed compacted window. The result decides whether Compaction._compact should ask the model for a second summary that explicitly includes the missing facts.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 182–186)

```
def __repr__(self) -> str
```

**Purpose**: Provides a short, safe text representation of a compaction request for debugging. It avoids printing the full conversation content.

**Data flow**: It reads the request’s message count, reason, and active request count. It returns a compact string with those counts rather than the full data.

**Call relations**: The request object is created by Compaction.maybe_compact and passed into Compaction._compact. This representation helps logs or debugging tools show what is happening without dumping huge or sensitive message bodies.


##### `Compaction.__repr__`  (lines 217–218)

```
def __repr__(self) -> str
```

**Purpose**: Provides a compact label for a Compaction object. It identifies the conversation and model without printing all internal state.

**Data flow**: It reads the conversation ID and model name from the object. It returns a short string containing those two values.

**Call relations**: This is used implicitly by Python debugging, logging, or interactive inspection when a Compaction instance is displayed.


##### `Compaction.maybe_compact`  (lines 220–240)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether the current conversation should be compacted now. It is the safe public entry point for normal automatic compaction and forced compaction after an overflow.

**Data flow**: It receives the current messages, a force flag, and active request text. It first refuses to compact very short windows, then estimates tokens and compares them with the trigger unless force is true. If compaction is needed, it builds a _CompactionRequest and returns the compacted messages plus model usage records; otherwise it returns the original messages and no usage.

**Call relations**: This calls Compaction._tokens and Compaction._trigger to make the decision, then hands real work to Compaction._compact. It is the function other conversation-loop code would call when preparing a model request.

*Call graph*: calls 3 internal fn (_compact, _tokens, _trigger); 1 external calls (__init__).


##### `Compaction._trigger`  (lines 242–248)

```
def _trigger(self) -> int
```

**Purpose**: Calculates the token size at which compaction should start. It leaves room for the summary output and a safety buffer before the model’s true limit.

**Data flow**: It reads either an explicit trigger override or the configured context window, summary size, and buffer. It returns one integer token threshold.

**Call relations**: Compaction.maybe_compact uses this to decide whether to compact. Compaction._require_budget uses the same threshold to ensure the replacement window will not immediately trigger another compaction.

*Call graph*: called by 2 (_require_budget, maybe_compact).


##### `Compaction._compact`  (lines 251–329)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction pipeline: choose what to summarize, call the model, verify the result, save records, fire hooks, and return the replacement window. It is the central workflow of the file.

**Data flow**: It receives a _CompactionRequest containing messages, reason, and active requests. It splits old and recent messages, records pre-compaction state, asks for a summary, drains loaded skill names, builds a boundary for verification, retries once if important anchors were missed, enforces the size budget, writes compressed records, logs metrics, fires hooks, and returns the new messages plus usage data.

**Call relations**: Compaction.maybe_compact calls this when compaction is needed. This function coordinates almost every helper in the file: selection, summarization, reference harvesting, verification, budget checks, persistence, and observability.

*Call graph*: calls 11 internal fn (_next_index, _persist, _record_verification, _references, _require_budget, _select, _summarize, _tokens, _verify, _window_text (+1 more)); called by 1 (maybe_compact); 6 external calls (__init__, __init__, __init__, replace, from_iterable, warn).


##### `Compaction._select`  (lines 331–347)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: Chooses which messages will be summarized and which recent messages will stay verbatim. It protects recent context and avoids splitting related tool messages apart.

**Data flow**: It receives all messages, groups them into rounds, then keeps enough whole trailing rounds to cover the configured number of recent messages. It returns the older rounds as the head and the kept messages as the tail, or returns none if there is no safe head to summarize.

**Call relations**: Compaction._compact calls this at the start of the pipeline. It relies on Compaction._rounds to build safe message groups.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 349–363)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Groups messages into model-interaction rounds so related assistant and tool-result messages stay together. This prevents compaction from cutting a tool call away from its answer.

**Data flow**: It receives a sequence of messages and walks through them in order. A new round begins when an assistant message appears after existing content; all accumulated messages become one round. It returns a tuple of these rounds.

**Call relations**: Compaction._select calls this before choosing the summarized head and kept tail. The grouping shapes the rest of compaction.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 365–391)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]=()) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: Asks the model to summarize the old transcript head, with recovery if the summarization prompt itself is too large. It returns a validated summary and usage records.

**Data flow**: It receives head rounds and optionally anchors missed by a previous attempt. It calls Compaction._summarize_once. If the provider says the input is too large, it drops the oldest portion of the head and tries again up to the configured limit. On success it returns the summary and the usage from the successful call.

**Call relations**: Compaction._compact calls this first for the normal summary and possibly again after a failed anchor check. It uses is_context_overflow to decide when shrinking-and-retrying is appropriate, and Compaction._drop_oldest to shrink the prompt.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 393–414)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Performs one actual model request to turn rendered old messages into a CompactionSummary. It is the single-call version underneath the retry wrapper.

**Data flow**: It receives rounds and any missed anchors, prepares prompt text, builds a ModelRequest with the compaction system prompt, streams text deltas from the model client, captures usage, and parses the final text into a validated CompactionSummary. It returns that summary and the usage record.

**Call relations**: Compaction._summarize calls this for each attempt. It delegates prompt construction to Compaction._prepare and output validation to Compaction._parse_summary.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 416–438)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: Builds the text prompt that asks the model to summarize the old conversation. It preserves useful structure while reducing wasteful repetition.

**Data flow**: It receives rounds and any missed anchors. It renders each message as role plus readable content, folds long repeated text runs, optionally appends a correction listing missed anchors, and finishes with a clear instruction to return one JSON object. It returns the complete prompt string.

**Call relations**: Compaction._summarize_once calls this before sending a model request. It uses Compaction._text to render message content, Compaction._fold_repeated_runs to reduce bulk, and Compaction._bullets to format retry anchors.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 440–453)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Compresses obvious repeated text inside the summarization prompt. This keeps spammy or looped output from making the summarization request too large.

**Data flow**: It receives a text string, searches for short word sequences repeated many times in a row, and replaces each run with one copy plus a marker saying how many times it repeated. It returns the shorter text.

**Call relations**: Compaction._prepare calls this while building the summarizer input. Its inner fold function performs the replacement for each regular-expression match.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 448–451)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement text for one repeated run found by Compaction._fold_repeated_runs. It keeps the repeated unit once and records the repetition count.

**Data flow**: It receives one regular-expression match, extracts the repeated phrase, calculates roughly how many copies were present, and returns text like the phrase followed by a repeated-count marker.

**Call relations**: This helper is used only inside Compaction._fold_repeated_runs as the replacement callback for the repeated-run search.


##### `Compaction._drop_oldest`  (lines 455–460)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Shrinks the summarization input after a prompt-too-large failure. It removes the oldest slice of the head before trying again.

**Data flow**: It receives the current head rounds. It drops at least one round, or about one fifth of the rounds, from the front and returns the remaining newer rounds.

**Call relations**: Compaction._summarize calls this when Compaction._summarize_once fails with a context-overflow error detected by is_context_overflow.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 462–503)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Turns the model’s raw text response into a valid CompactionSummary object. It rejects empty, malformed, or schema-invalid summaries rather than installing bad compacted context.

**Data flow**: It receives raw model output text. It finds the first balanced JSON object, validates it against the CompactionSummary schema, checks that the intent field is not empty, and returns the typed summary. If any step fails, it raises a RuntimeError.

**Call relations**: Compaction._summarize_once calls this after collecting streamed model text. The validated result is later checked by Compaction._verify before it can replace live messages.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 505–522)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds durable tool-output file paths from the old head that should be carried forward. These paths let future model calls re-read large outputs without copying their full contents into the summary.

**Data flow**: It receives the head rounds and kept tail. It first notes paths already visible in the tail, then scans the head for tool-output paths not already visible, preserves first-seen order without duplicates, keeps only the most recent bounded set, and returns them.

**Call relations**: Compaction._compact calls this while building the verification boundary. Compaction._render later places these references into the replacement message.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 524–563)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns a validated summary plus preserved references and active requests into the single replacement message for the old conversation head. It creates stable, readable compacted context.

**Data flow**: It receives a CompactionSummary, durable reference paths, and active request text. It creates only the sections that have content, formats lists as bullets, includes active requests verbatim, prefixes the whole thing as compacted context, and returns the rendered string.

**Call relations**: Compaction._verify calls this to build the proposed replacement message. Compaction._require_budget also calls it with an empty summary to estimate the unavoidable minimum size of the compacted message.

*Call graph*: calls 1 internal fn (_bullets); called by 2 (_require_budget, _verify).


##### `Compaction._bullets`  (lines 565–566)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats plain strings as markdown-style bullet lines. It is a small helper for readable prompts and summaries.

**Data flow**: It receives a tuple of strings and prefixes each one with “- ”. It returns the joined bullet-list text.

**Call relations**: Compaction._prepare uses this for missed-anchor retry instructions. Compaction._render uses it for summary sections and durable references.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 568–569)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: Renders a whole sequence of messages into one searchable text block. It is used when checking what facts the conversation contains or carries forward.

**Data flow**: It receives messages, converts each message to text with Compaction._text, joins them with newlines, and returns the result.

**Call relations**: Compaction._compact uses this to build pre-compaction text and anchor text. Compaction._verify uses it to check whether anchors survived in the proposed after-window.

*Call graph*: calls 1 internal fn (_text); called by 2 (_compact, _verify).


##### `Compaction._verify`  (lines 571–607)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: Checks whether one proposed summary is safe to install. It removes invented file references, renders the replacement, counts missing anchors, and records token sizes.

**Data flow**: It receives a summary, a fixed boundary describing the original window, and a retry flag. It keeps only summary file paths that appeared in the original pre-compaction text, injects the known loaded skills, renders the replacement message, combines it with the kept tail, computes token counts, finds missing anchors, and returns a _Candidate containing the checked summary, rendered text, after-window, and verification record.

**Call relations**: Compaction._compact calls this after each summarization attempt. It uses Compaction._render, Compaction._tokens, Compaction._window_text, and missing_anchors; its result determines whether to retry, persist, or fail.

*Call graph*: calls 4 internal fn (_render, _tokens, _window_text, missing_anchors); called by 1 (_compact); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._require_budget`  (lines 609–647)

```
def _require_budget(self, verification: CompactionVerification, boundary: _Boundary) -> None
```

**Purpose**: Makes sure compaction did not make the conversation worse. It raises an error if the replacement should have been able to shrink but did not, or if it should have fit under the trigger but still does not.

**Data flow**: It receives a verification record and the fixed boundary. It calculates the trigger, estimates the unavoidable carried text using an empty summary, compares old head size, tail size, summary allowance, and after-window size, and either returns silently or raises a RuntimeError.

**Call relations**: Compaction._compact calls this after verification and before persistence. It uses Compaction._trigger, Compaction._render, and Compaction._tokens to enforce the size invariant.

*Call graph*: calls 3 internal fn (_render, _tokens, _trigger); called by 1 (_compact); 1 external calls (__init__).


##### `Compaction._record_verification`  (lines 649–672)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], verification: CompactionVerification) -> None
```

**Purpose**: Writes observability data about how well compaction preserved important facts. This makes compaction quality visible in logs and metrics.

**Data flow**: It receives the compaction index, reason, and verification record. It logs token counts, missing anchors, dropped paths, and retry status, then emits a metric labeled clean or lossy and whether a retry was used.

**Call relations**: Compaction._compact calls this after persisting the compacted record. It hands the outcome to the project’s logging and metric systems.

*Call graph*: called by 1 (_compact); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 674–685)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Saves the before-window, after-window, and structured summary for a completed compaction. This gives operators and evaluators an audit trail.

**Data flow**: It receives an index, original messages, compacted messages, and summary. It writes before and after windows through Compaction._write, serializes the summary to JSON, compresses it with lz4, and stores it in the blob store under the summary key.

**Call relations**: Compaction._compact calls this once a candidate passes verification and budget checks. It uses Compaction._key to place records at stable conversation-specific paths.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 687–691)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next unused compaction record number for this conversation. This avoids overwriting earlier compaction records.

**Data flow**: It starts at index 1 and checks whether an “after” blob already exists for that index. It increments until it finds a free slot and returns that number.

**Call relations**: Compaction._compact calls this before writing records. It uses Compaction._key to ask the blob store about each possible location.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 693–700)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads a previously saved compaction record. It returns none if the requested record does not exist.

**Data flow**: It receives an index, tries to read the compressed before, after, and summary blobs from storage, and returns None if any are missing. If all are present, it passes them to decode_compaction and returns the decoded CompactionRecord.

**Call relations**: This is a retrieval helper for tools, audits, or evaluations that need to inspect past compactions. It uses Compaction._key to locate the saved blobs.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 702–710)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes either the before or after message window to compressed blob storage. It stores messages in a deterministic JSON form.

**Data flow**: It receives an index, a label of before or after, and messages. It wraps the messages in a CompactionWindow, dumps compact sorted JSON, compresses the bytes with lz4, and writes them to the blob store.

**Call relations**: Compaction._persist calls this for the before-window and after-window. It uses Compaction._key to choose the storage path.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 712–713)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the storage key for one compaction artifact. It keeps before, after, and summary records organized by conversation and index.

**Data flow**: It receives an index and artifact kind. It passes the conversation ID, index, and kind to compaction_key and returns the resulting blob path.

**Call relations**: Compaction._persist, Compaction._write, Compaction._next_index, and Compaction.read_record all use this so they agree on where compaction records live.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 715–738)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: Estimates how much model context a set of messages will consume. This drives when compaction starts and whether the compacted result is small enough.

**Data flow**: It receives messages. For each message it counts role text, rendered content text, hidden reasoning bytes, and inline image estimates, converts character counts to approximate tokens, and sums everything into one integer.

**Call relations**: Compaction.maybe_compact uses this to decide whether to compact. Compaction._compact records before size, Compaction._verify records after size, and Compaction._require_budget enforces size rules using it.

*Call graph*: calls 3 internal fn (_image_count, _opaque_chars, _text); called by 4 (_compact, _require_budget, _verify, maybe_compact).


##### `Compaction._opaque_chars`  (lines 740–759)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: Counts hidden reasoning data that is sent to the model but not useful as readable summary text. Without this, token estimates would be too low for reasoning-heavy messages.

**Data flow**: It receives one message. If the content is plain text, it returns zero. Otherwise it scans structured blocks for thinking signatures, redacted data, and encrypted reasoning content, adds their lengths, and returns the total.

**Call relations**: Compaction._tokens calls this for each message while estimating context size. The value is counted but not rendered by Compaction._text.

*Call graph*: called by 1 (_tokens).


##### `Compaction._image_count`  (lines 761–771)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts inline images in a message so token estimates include their cost. Images may have little text but still consume a lot of model context.

**Data flow**: It receives one message. If the content is plain text, it returns zero. Otherwise it scans image blocks and image parts inside tool results, counts them, and returns the number.

**Call relations**: Compaction._tokens calls this for each message and multiplies the count by a fixed image token estimate.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 773–797)

```
def _text(self, message: Message) -> str
```

**Purpose**: Converts a message’s different content block types into readable text for summarization, searching, and token estimation. Non-text items get clear markers instead of silently disappearing.

**Data flow**: It receives one message. If the content is already a string, it returns it. Otherwise it walks through text, thinking, redacted reasoning, reasoning summaries, images, tool results, and tool uses, rendering each into plain text or markers such as “[image]”, then joins the pieces with newlines.

**Call relations**: Compaction._prepare uses this to build the summarizer input. Compaction._references and Compaction._window_text use it for searching, and Compaction._tokens uses it for size estimation.

*Call graph*: called by 4 (_prepare, _references, _tokens, _window_text); 1 external calls (dumps).


### Prompt rendering
Provides the prompt package entry point and renders validated, fingerprinted system prompts from templates.

### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import setup`

This is an empty package marker file. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. Here, it makes `ufo.loop.prompts` a named place in the codebase where prompt-related modules can live and be imported from elsewhere.

Think of it like a label on a drawer. The drawer may contain useful documents, but the label itself does not do the work; it just makes the drawer recognizable and easy to refer to. Without this file, some Python tooling or older import setups might not reliably recognize this directory as a package, which could make imports fail or behave inconsistently.

There are no functions, classes, settings, or side effects here. Its importance is structural: it helps organize the project and supports clean imports for the prompt code that belongs under this directory.


### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `prompt construction before a model turn`

This file is the prompt assembly room. The project keeps reusable prompt text in nearby Markdown files, with blank spaces such as `{{agent-prompt}}`, `{{sections}}`, and `{{knowledge_cutoff}}`. This renderer fills those spaces with the agent’s instructions, contributed capability sections, available skill descriptions, citation guidance, and the model’s knowledge cutoff date.

The main reason this file exists is safety and repeatability. A prompt with an unfilled `{{something}}` placeholder could confuse the model or hide a bug. So the renderer is strict: if the agent prompt declares a variable, the caller must supply it; if the caller supplies an extra variable, that is also an error. After all replacements are done, it checks again for any leftover `{{...}}` text and fails loudly if one remains.

It also tidies the finished prompt by collapsing long runs of blank lines and trimming the end. Finally, it computes a SHA-256 digest, which is a stable fingerprint of the exact prompt text. Like a label on a sealed package, that digest lets logs and observability tools show exactly which prompt version was used.

#### Function details

##### `rendered_prompt`  (lines 53–54)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This function wraps finished prompt text in a `RenderedPrompt` object and adds a digest, which is a fingerprint of the exact content. It is used when the prompt is ready to send or record.

**Data flow**: It receives plain prompt text. It encodes that text, computes a SHA-256 hash from it, prefixes the hash with `sha256:`, and returns a `RenderedPrompt` containing both the fingerprint and the original content. It does not change any outside state.

**Call relations**: After `render_template` has filled and checked the prompt, it calls `rendered_prompt` as the final packaging step. `rendered_prompt` relies on the standard hashing library and the `RenderedPrompt` data container to produce the final object.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 57–74)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This is the high-level builder for the main agent’s system prompt. It combines the shared shell prompt, the agent-specific instructions, skill information, contributed sections, and the model’s knowledge cutoff.

**Data flow**: It receives the agent prompt, a list of section name/body pairs, an optional list of skill name/description pairs, and a required knowledge cutoff in machine form such as `2026-02`. It turns that date into a human-readable form such as `February 2026`, inserts it into the knowledge cutoff block, places that block into the shell template, and then passes everything to `render_template`. The output is a fully rendered prompt with a digest.

**Call relations**: This function is the usual front door for building the main system prompt. It prepares the special knowledge cutoff text using date parsing, then hands the real template filling work to `render_template`.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 77–95)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This function fills a prompt template and enforces that every placeholder has been dealt with. It is the central checker that prevents half-rendered prompt text from reaching the model.

**Data flow**: It receives a template, an agent prompt, replacement variables for that agent prompt, skill entries, and section entries. First it asks `_substitute_vars` to fill variables inside the agent prompt. If there is an agent prompt but the template has no place for it, it raises an error. Then it replaces the skill, citation, section, and agent-prompt slots. It checks the final text for any remaining `{{...}}` placeholders, raises an error if any are found, cleans up extra blank lines, trims the end, and returns a `RenderedPrompt` through `rendered_prompt`.

**Call relations**: `render_system_prompt` calls this after preparing the shell template. Inside the flow, `render_template` delegates variable replacement to `_substitute_vars`, skill block formatting to `render_skill_index`, and final packaging plus fingerprinting to `rendered_prompt`.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 98–107)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This function turns the list of available skills into the prompt block that tells the model what skills can be loaded. If there are no skills, it returns an empty string so the prompt does not include a useless empty section.

**Data flow**: It receives a sequence of skill name and description pairs. With no entries, it outputs an empty string. With entries, it creates a small text block wrapped in `<available_skills>` and `</available_skills>`, with one bullet line per skill.

**Call relations**: `render_template` calls this when it reaches the skill slot in the prompt template. The formatted block is then inserted into the larger prompt alongside the agent instructions, sections, and citation text.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 110–117)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This helper fills variables inside the agent prompt while checking both sides: every declared variable must be supplied, and every supplied variable must actually be declared. This prevents silent mistakes such as misspelled variable names.

**Data flow**: It receives a prompt template and a mapping of variable names to replacement text. It scans the template for placeholders like `{{user_name}}`, compares those names with the supplied mapping keys, and raises an error if anything is missing or extra. If the names match exactly, it replaces each placeholder with its supplied value and returns the filled text.

**Call relations**: `render_template` calls this at the start, before placing the agent prompt into the larger shell. Its strict check is an early guardrail, so the later full-template check is not the first time mistakes are caught.

*Call graph*: called by 1 (render_template).
