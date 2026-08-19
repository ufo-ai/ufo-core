# Context assembly, prompt construction, skills, and spawn catalog  `stage-8`

This stage is shared behind-the-scenes work that happens before each call to the AI model. Its job is to pack the model’s “briefcase”: the current conversation, the main instructions, the tools it may use, and the other agents it may ask for help.

Prompt rendering builds the final system prompt from templates. It inserts the agent’s instructions, available skills, citation rules, capability notes, and model knowledge cutoff, then records a hash so that exact prompt can be traced later. If the conversation has grown too large, compaction shrinks older history into a checked summary while leaving the newest messages intact.

Skill loading gathers reusable abilities from built-in folders, installed extensions, and user-created skill folders. It also tracks skill dependencies and protects helper files by copying only allowed content into a safe workspace. Agent setup adds a temporary skill when an installed agent still needs account connections, so the model can ask the user to complete setup. The spawn catalog lists which subagents can be started and what they require. Untrusted-content wrapping marks outside text as data, not commands.

## Sub-stages

- [Built-in and extension skill loading](stage-8.1.md) `stage-8.1` — 4 files

## Files in this stage

### Account-readiness skills
Creates temporary help-request skills for installed agents that still need account connections before they can operate.

### `core/src/ufo/agent_setup.py`

`domain_logic` · `conversation turn setup`

Some agents need access to outside services, such as a calendar or email account, before they can do their job. This file records that need in a small model called AgentSetup: it says which connector providers are required and gives human-facing instructions for getting them connected. A connector is a type of account provider, not one specific account, so the member gets to choose which account to grant.

The main work is checking the workspace database to compare two lists: what each shipped agent says it needs, and what account grants it already has. Any agent with missing grants is considered “pending setup.” This is like checking a checklist: the agent was installed with required boxes, and the file marks which boxes are still empty.

When a conversation turn begins, setup_skill decides whether to add a special RuntimeSkill. That skill is not always present. It appears only when a member is actually speaking, because connecting an account requires member consent. If the current agent itself is missing accounts, it gets instructions to ask for them. If the current agent is the main agent, it can instead show a roster of other agents that still need setup, because the main agent is allowed to help connect accounts on another agent’s behalf.

#### Function details

##### `pending_setup`  (lines 39–80)

```
async def pending_setup() -> tuple[tuple[UUID, str, AgentSetup], ...]
```

**Purpose**: This function finds every installed agent in the current workspace that still lacks one or more required account grants. It is used to keep setup status live, so the system notices immediately when a member connects or revokes an account.

**Data flow**: It reads the current workspace id, then opens a workspace database transaction, which is a safe database session tied to that workspace. It fetches installed agents that declared setup requirements, fetches the account providers already granted to those agents, compares required providers against granted providers, and returns a tuple of only the agents that are still missing something. Each returned item contains the agent id, its name, and an AgentSetup object narrowed down to the missing connectors plus the original instructions.

**Call relations**: setup_skill calls this at the start of deciding whether any setup instructions should be loaded for the current conversation turn. pending_setup does the database lookup and checklist comparison, then hands setup_skill a clean list of agents that still need member action.

*Call graph*: called by 1 (setup_skill); 4 external calls (__init__, select, workspace_tx, ws_current).


##### `_wants`  (lines 83–84)

```
def _wants(missing: AgentSetup) -> str
```

**Purpose**: This small helper turns a list of missing connector provider names into a readable phrase, such as asking for “a calendar account.” It exists so setup messages can sound natural instead of showing raw data.

**Data flow**: It receives an AgentSetup object that already contains only missing connectors. It formats each connector name as “a <provider> account,” joins them with commas, and returns that plain text string. It does not change any stored data.

**Call relations**: setup_skill uses this when writing the instructions shown to the agent or main agent. After pending_setup identifies what is missing, _wants turns that missing list into wording suitable for the model to say to a member.

*Call graph*: called by 1 (setup_skill).


##### `setup_skill`  (lines 103–143)

```
async def setup_skill(agent_id: UUID, is_main: bool, has_speaker: bool) -> RuntimeSkill | None
```

**Purpose**: This function decides whether the current conversation should receive the special “agent setup” skill, and if so, what it should say. It prevents setup instructions from appearing in turns where no member can actually grant consent.

**Data flow**: It receives the current agent id, whether that agent is the main agent, and whether a human speaker is present. If no speaker is present, it returns None. Otherwise it asks pending_setup for all unfinished setup work. If the current agent is missing accounts, it builds a RuntimeSkill telling that agent what it still needs and how to ask the member. If the current agent is the main agent and other agents need setup, it builds a roster-style RuntimeSkill listing those agents and their missing accounts. If there is nothing useful to do, it returns None.

**Call relations**: This is the outward-facing decision point in the file. It calls pending_setup to learn the current setup state, uses _wants to make the missing accounts readable, and constructs a RuntimeSkill only when the turn can act on it. Other parts of the system can call setup_skill during conversation preparation to decide whether these setup instructions should be available to the model.

*Call graph*: calls 2 internal fn (_wants, pending_setup); 1 external calls (__init__).


### Context window management
Keeps conversation history within model limits by compacting older messages into a verified summary while preserving recent context.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling, when a conversation window is near or over the model context limit`

AI models can only read so much conversation at once. This file is the safety valve for long transcripts: when the history gets near the model's limit, it compresses the older part into a structured summary and keeps the recent tail exactly as it was. Without it, long sessions would eventually fail with “context too large” errors, or the system would have to drop important history without a record.

The flow works like careful moving: pack old boxes into a labeled summary, leave the most recent tools and replies on the desk, then check that the labels still mention the critical items. The compactor first decides whether compaction is needed. It splits the transcript into whole conversation rounds so tool calls are not separated from their results. It sends the older rounds to the model with special instructions asking for a JSON summary. If that request is itself too large, it drops the oldest portion and retries a bounded number of times.

After a summary comes back, the file does not trust it blindly. It harvests “anchors,” such as durable tool-output file paths, error names, loaded skills, and active request references, then checks whether the replacement window still carries them literally. It can retry once with missing anchors named. It also rejects replacements that do not actually reduce the token budget. Finally, it stores compressed before, after, and summary records so the original facts remain auditable.

#### Function details

##### `is_context_overflow`  (lines 103–109)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: This helper decides whether an exception means the model provider rejected a request because the input was too large. It lets the caller shrink and retry instead of treating the error as a normal failure.

**Data flow**: It takes an exception, turns the exception type and message into lowercase text, and looks for common phrases like “context length” or “prompt is too large.” It returns true when one of those signs is present, otherwise false.

**Call relations**: During summarization, Compaction._summarize calls this after a failed model request. If it recognizes a context-size problem, the summarizer tries again with less old history.

*Call graph*: called by 1 (_summarize).


##### `harvest_anchors`  (lines 112–138)

```
def harvest_anchors(head_text: str, loaded_skills: tuple[str, ...], active_requests: tuple[str, ...]) -> tuple[Anchor, ...]
```

**Purpose**: This function picks out facts that must survive compaction exactly, such as tool-output paths, error class names, loaded skills, and active request references. These are the “load-bearing” details used to judge whether a summary lost something important.

**Data flow**: It receives rendered old transcript text, the names of loaded skills, and active request text. It searches for known patterns, removes duplicates while keeping recent items, caps each kind, and returns Anchor objects describing each literal fact.

**Call relations**: Compaction._compact calls this after selecting the transcript head to summarize. The resulting anchors are later checked by Compaction._verify through missing_anchors.

*Call graph*: called by 1 (_compact); 1 external calls (__init__).


##### `missing_anchors`  (lines 141–145)

```
def missing_anchors(anchors: tuple[Anchor, ...], carried: str) -> tuple[Anchor, ...]
```

**Purpose**: This function finds which required literal facts did not make it into the compacted replacement text. It is intentionally strict: the exact text must still appear.

**Data flow**: It takes a tuple of anchors and a text string representing what will be carried forward. It filters out anchors whose literal text is absent and returns only the missing ones.

**Call relations**: Compaction._verify calls this while grading a proposed summary. Missing anchors can trigger one retry or be recorded as loss in the verification record.

*Call graph*: called by 1 (_verify).


##### `_CompactionRequest.__repr__`  (lines 183–187)

```
def __repr__(self) -> str
```

**Purpose**: This creates a short debug-friendly label for a compaction request. It avoids printing the full conversation and instead shows counts and the reason.

**Data flow**: It reads the request's messages, reason, and active requests. It returns a compact string containing the number of messages, whether compaction was automatic or forced, and how many active requests were included.

**Call relations**: It is used implicitly when a _CompactionRequest is logged or inspected. The request object itself is created by Compaction.maybe_compact and passed into Compaction._compact.


##### `Compaction.__repr__`  (lines 221–222)

```
def __repr__(self) -> str
```

**Purpose**: This creates a compact human-readable label for a Compaction object. It helps logs and debugging show which conversation and model are involved without dumping all settings.

**Data flow**: It reads the compaction object's conversation id and model name. It returns a short string with those two values.

**Call relations**: It is used implicitly when a Compaction instance is printed, logged, or inspected. It does not drive the compaction flow itself.


##### `Compaction.maybe_compact`  (lines 224–244)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: This is the public gatekeeper for compaction. It decides whether the current message window is small enough to leave alone or large enough to compress.

**Data flow**: It receives the current messages, a force flag, and active request text. It first checks whether there are enough messages to compact, then estimates tokens and compares them with the trigger unless forced. It returns either the unchanged messages with no usage records, or the compacted messages plus model usage from summarization.

**Call relations**: The engine calls this before sending a large transcript onward, or after a provider says the prompt is too large. When compaction is needed, it builds a _CompactionRequest and hands the real work to Compaction._compact.

*Call graph*: calls 3 internal fn (_compact, _tokens, _trigger); 1 external calls (__init__).


##### `Compaction._trigger`  (lines 246–252)

```
def _trigger(self) -> int
```

**Purpose**: This calculates the token level at which compaction should start. It leaves room for the summary response and an extra safety buffer so the system does not wait until the model is already full.

**Data flow**: It reads an optional explicit trigger value. If present, that value wins; otherwise it subtracts the summary allowance and buffer from the model's context window and returns the result.

**Call relations**: Compaction.maybe_compact uses this to decide whether to compact. Compaction._require_budget uses the same threshold to reject replacements that would immediately need compaction again.

*Call graph*: called by 2 (_require_budget, maybe_compact).


##### `Compaction._compact`  (lines 255–333)

```
async def _compact(self, request: _CompactionRequest) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: This is the main compaction pipeline. It selects old messages, summarizes them, verifies the replacement, stores audit records, fires hooks, and returns the new window.

**Data flow**: It receives a _CompactionRequest containing messages, the reason, and active requests. It splits the window, records the original token count, fires a pre-compaction hook, gets the next storage index, asks the model for a summary, drains loaded skills into that summary, builds a boundary for verification, retries once if anchors were missed, checks the token budget, persists before/after/summary files, logs verification, fires a post-compaction hook, and outputs the replacement messages plus usage records.

**Call relations**: Compaction.maybe_compact calls this only when compaction is needed. It orchestrates the helper methods in this file: selection, summarization, anchor harvesting, verification, budget checking, persistence, and observability.

*Call graph*: calls 11 internal fn (_next_index, _persist, _record_verification, _references, _require_budget, _select, _summarize, _tokens, _verify, _window_text (+1 more)); called by 1 (maybe_compact); 6 external calls (__init__, __init__, __init__, replace, from_iterable, warn).


##### `Compaction._select`  (lines 335–351)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: This chooses which part of the transcript will be summarized and which recent part will remain verbatim. It preserves whole conversation rounds so connected tool calls and tool results stay together.

**Data flow**: It takes all messages, groups them into rounds, then keeps enough trailing rounds to cover at least the configured recent-message count. It returns the older rounds as the head and the kept messages as the tail, or none if there is no safe head to summarize.

**Call relations**: Compaction._compact calls this at the start. It relies on Compaction._rounds to form the safe units of conversation.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 353–367)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: This groups messages into conversation rounds that should not be split apart. A round begins around an assistant message and includes following user or tool-result messages that answer it.

**Data flow**: It receives a sequence of messages and walks through them in order. When it sees an assistant message after existing content, it closes the current group and starts a new one. It returns a tuple of message groups.

**Call relations**: Compaction._select calls this so the compactor can keep or summarize whole rounds rather than slicing the transcript in the middle of a tool exchange.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 369–395)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]=()) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: This asks the model to summarize the old transcript head, with recovery if the summary prompt is too large. It is bounded so it will not retry forever.

**Data flow**: It receives head rounds and optionally anchors missed by a previous attempt. It calls Compaction._summarize_once. If the provider reports a context overflow, it drops the oldest portion of rounds and retries until the limit is reached. On success, it returns a validated CompactionSummary and usage records.

**Call relations**: Compaction._compact calls this for the first summary and possibly for an anchor-repair retry. It uses is_context_overflow to distinguish a shrink-and-retry case from other failures, and Compaction._drop_oldest to reduce the prompt.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 397–418)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: This performs one actual model call to turn old transcript rounds into a structured summary. It also collects usage information so the system can account for the tokens spent.

**Data flow**: It prepares a prompt from the rounds and missed anchors, builds a ModelRequest, streams text chunks from the model client, captures the Usage event, joins the text, validates it as a summary, and returns the summary plus usage. If no usage appears, it raises an error.

**Call relations**: Compaction._summarize calls this for each attempt. It delegates input formatting to Compaction._prepare and output validation to Compaction._parse_summary.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 420–442)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...], missed: tuple[Anchor, ...]) -> str
```

**Purpose**: This turns the old transcript head into the text sent to the summarizing model. It keeps useful structure while reducing pointless bulk.

**Data flow**: It receives grouped messages and any anchors missed before. It renders each message as role plus readable content, folds very long repeated runs, optionally appends a correction naming missed anchors, and ends with a firm instruction to return one JSON object. The result is a single prompt string.

**Call relations**: Compaction._summarize_once calls this before making the model request. It uses Compaction._text to render message blocks, Compaction._fold_repeated_runs to shrink spam-like repetition, and Compaction._bullets to format anchor corrections.

*Call graph*: calls 3 internal fn (_bullets, _fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 444–457)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: This compresses long stretches of exactly repeated short text inside the summarization prompt. It keeps one copy and a count marker, which preserves the fact of repetition without wasting huge space.

**Data flow**: It receives a text string and applies a regular expression that detects repeated word sequences. Each match is replaced with the repeated unit followed by a marker like “[repeated N times].” It returns the shortened text.

**Call relations**: Compaction._prepare calls this while building the summarizer input. The nested fold function creates each replacement string.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 452–455)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: This local helper builds the replacement for one detected repeated run. It calculates how many times the repeated text occurred.

**Data flow**: It receives a regular-expression match, extracts the repeated unit, estimates the occurrence count from the matched text length, and returns one copy plus the repeated-count marker.

**Call relations**: It is used only inside Compaction._fold_repeated_runs as the replacement callback for the regular-expression substitution.


##### `Compaction._drop_oldest`  (lines 459–464)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: This removes the oldest slice of transcript rounds when the summarization prompt is still too large. It is a controlled way to make the next retry smaller.

**Data flow**: It receives the current head rounds. It drops at least one round, or about one fifth of the rounds, from the front and returns the remaining newer rounds.

**Call relations**: Compaction._summarize calls this only after is_context_overflow says the model rejected the prompt for size.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 466–507)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: This turns the model's raw text response into a valid CompactionSummary object. It protects the live transcript from malformed, empty, or schema-breaking summaries.

**Data flow**: It receives raw model text, finds the first balanced JSON object inside it, and validates that object against the expected summary shape. It raises RuntimeError if no JSON exists, the JSON is invalid, or the summary has no intent; otherwise it returns the typed summary.

**Call relations**: Compaction._summarize_once calls this after collecting streamed model text. Its output is later verified by Compaction._verify before it can replace transcript history.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 509–526)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: This finds durable tool-output files from the summarized head that should still be mentioned after compaction. These file paths let future steps re-read large outputs instead of stuffing their contents into the summary.

**Data flow**: It receives the head rounds and kept tail. It first notes tool-output paths still visible in the tail, then scans the head for additional paths, avoids duplicates and already-visible paths, keeps only the most recent few, and returns them.

**Call relations**: Compaction._compact calls this while building the verification boundary. Compaction._render later includes these references in the replacement message.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 528–567)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: This converts a validated summary and carried-forward facts into the single replacement message for the old transcript head. It makes the compacted context predictable and readable.

**Data flow**: It receives a CompactionSummary, durable references, and active requests. It creates sections only for non-empty summary fields, appends loaded skills, durable references, and active requests when present, prefixes the whole text as compacted context, and returns the rendered string.

**Call relations**: Compaction._verify calls this to build the candidate replacement. Compaction._require_budget also calls it with an empty summary to estimate the unavoidable cost of the carried-forward wrapper.

*Call graph*: calls 1 internal fn (_bullets); called by 2 (_require_budget, _verify).


##### `Compaction._bullets`  (lines 569–570)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: This formats a list of strings as markdown-style bullet lines. It keeps repeated rendering code simple and consistent.

**Data flow**: It receives a tuple of strings and prefixes each one with “- ” on its own line. It returns the joined text.

**Call relations**: Compaction._prepare uses this for anchor retry instructions. Compaction._render uses it for summary sections such as concepts, errors, pending tasks, loaded skills, and references.

*Call graph*: called by 2 (_prepare, _render).


##### `Compaction._window_text`  (lines 572–573)

```
def _window_text(self, messages: tuple[Message, ...]) -> str
```

**Purpose**: This creates a plain text view of a whole message window. It is used when the compactor needs to search or verify what information is present.

**Data flow**: It receives messages, renders each one with Compaction._text, joins them with newlines, and returns the combined string.

**Call relations**: Compaction._compact uses this to capture pre-compaction text and head text for anchor harvesting. Compaction._verify uses it to check whether anchors appear in the replacement window.

*Call graph*: calls 1 internal fn (_text); called by 2 (_compact, _verify).


##### `Compaction._verify`  (lines 575–611)

```
def _verify(self, summary: CompactionSummary, boundary: _Boundary, retried: bool) -> _Candidate
```

**Purpose**: This grades one proposed summary before it can replace the old transcript. It removes invented file references, builds the replacement, counts lost anchors, and records token sizes.

**Data flow**: It receives a summary, a fixed boundary describing the original window, and whether this is a retry. It keeps only summary file paths that appeared in the original text, inserts tracked loaded skills, renders the compacted message, combines it with the tail, estimates token counts, finds missing anchors, and returns a _Candidate containing the checked summary, rendered text, replacement messages, and verification details.

**Call relations**: Compaction._compact calls this after the first summary and possibly after a retry. It uses Compaction._render, Compaction._tokens, Compaction._window_text, and missing_anchors to decide what was preserved.

*Call graph*: calls 4 internal fn (_render, _tokens, _window_text, missing_anchors); called by 1 (_compact); 4 external calls (__init__, __init__, __init__, model_copy).


##### `Compaction._require_budget`  (lines 613–651)

```
def _require_budget(self, verification: CompactionVerification, boundary: _Boundary) -> None
```

**Purpose**: This enforces the rule that compaction must not make the window worse. It raises an error if the replacement fails to shrink when shrinking was possible, or if it remains above the trigger when it could have fit.

**Data flow**: It receives verification results and the original boundary. It computes the trigger, estimates the unavoidable carried-forward text cost, compares head size, tail size, summary allowance, and final after-token count, then either returns silently or raises RuntimeError.

**Call relations**: Compaction._compact calls this after verification and before persistence. It uses Compaction._trigger, Compaction._render, and Compaction._tokens to enforce the budget invariant.

*Call graph*: calls 3 internal fn (_render, _tokens, _trigger); called by 1 (_compact); 1 external calls (__init__).


##### `Compaction._record_verification`  (lines 653–676)

```
def _record_verification(self, index: int, reason: Literal['auto', 'force'], verification: CompactionVerification) -> None
```

**Purpose**: This records the outcome of a compaction in logs and metrics. It makes missing anchors and dropped paths visible to operators instead of hiding them inside stored files.

**Data flow**: It receives the compaction index, reason, and verification object. It writes a structured log with token counts, anchor counts, missing anchor literals, dropped paths, and retry status, then emits a metric labeled as clean or lossy.

**Call relations**: Compaction._compact calls this after persistence. It hands the verification results to the observability layer through log and emit_metric.

*Call graph*: called by 1 (_compact); 2 external calls (emit_metric, log).


##### `Compaction._persist`  (lines 678–689)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: This saves the audit trail for a completed compaction. It stores the original window, the replacement window, and the structured summary.

**Data flow**: It receives an index, before messages, after messages, and the summary. It writes before and after windows through Compaction._write, then serializes and compresses the summary JSON and stores it in the blob store under the matching key.

**Call relations**: Compaction._compact calls this once a candidate passes budget checks. It uses Compaction._key to choose storage paths and Compaction._write for the two message windows.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 691–695)

```
async def _next_index(self) -> int
```

**Purpose**: This finds the next unused compaction number for a conversation. It prevents a new compaction record from overwriting an older one.

**Data flow**: It starts at index 1 and asks the blob store whether an “after” record already exists for that index. It increments until it finds a free index and returns it.

**Call relations**: Compaction._compact calls this before writing records. It uses Compaction._key to check the storage location for each candidate index.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 697–704)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: This reads a saved compaction record back from storage. It is useful for inspection, evaluation, or audit tools that need to see what changed.

**Data flow**: It receives an index, fetches the compressed before, after, and summary blobs using Compaction._key, and passes them to decode_compaction. If any required blob is missing, it returns null instead of raising.

**Call relations**: This is an external read path for stored compactions. It does not participate in making a new compaction, but it uses the same key scheme as the write path.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 706–714)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: This writes one side of a compaction window, either before or after, to compressed blob storage. It keeps the stored representation stable and compact.

**Data flow**: It receives an index, a label of “before” or “after,” and messages. It wraps messages in a CompactionWindow, dumps deterministic JSON, compresses the bytes with LZ4, and writes them to the blob store.

**Call relations**: Compaction._persist calls this twice for each completed compaction. It uses Compaction._key to choose the blob path.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 716–717)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: This builds the storage key for a compaction artifact. It centralizes the naming scheme so reads and writes use the same path.

**Data flow**: It receives an index and a half name: before, after, or summary. It combines those with the conversation id through compaction_key and returns the resulting string.

**Call relations**: Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record all call this whenever they need to locate compaction data in the blob store.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 719–742)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: This estimates how many model tokens a set of messages will cost. Tokens are chunks of text the model reads; the estimate decides when compaction should happen and whether it helped.

**Data flow**: It receives messages. For each message, it counts role text, rendered content text, hidden reasoning bytes, and a fixed estimate per image, then divides text characters by a measured characters-per-token ratio. It returns the total estimated token count.

**Call relations**: Compaction.maybe_compact uses this to decide whether to start. Compaction._compact records before size, Compaction._verify records after size, and Compaction._require_budget enforces shrinking based on it. It relies on Compaction._text, Compaction._opaque_chars, and Compaction._image_count.

*Call graph*: calls 3 internal fn (_image_count, _opaque_chars, _text); called by 4 (_compact, _require_budget, _verify, maybe_compact).


##### `Compaction._opaque_chars`  (lines 744–763)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: This counts hidden reasoning data that still costs space in model requests but is not useful transcript prose. Without this, the token estimate could be dangerously low.

**Data flow**: It receives one message. If the content is plain text, it returns zero. For structured content, it adds the lengths of thinking signatures, redacted reasoning data, and encrypted reasoning item content, then returns that total.

**Call relations**: Compaction._tokens calls this for every message as part of the token estimate. The counted data is not rendered by Compaction._text.

*Call graph*: called by 1 (_tokens).


##### `Compaction._image_count`  (lines 765–775)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: This counts inline images inside a message so token estimates include their cost. Images do not add text characters, but they still take model context space.

**Data flow**: It receives one message. Plain text messages count as zero. For structured content, it counts direct image blocks and image blocks nested inside tool results, then returns the total.

**Call relations**: Compaction._tokens calls this for each message and multiplies the count by a fixed image-token estimate.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 777–801)

```
def _text(self, message: Message) -> str
```

**Purpose**: This turns a message with mixed content blocks into readable text. It gives the summarizer and verifier a common plain-text view of messages.

**Data flow**: It receives one message. Plain string content is returned as-is. Structured blocks are rendered according to their kind: text and thinking become text, redacted reasoning becomes a marker, images become “[image],” tool results become their text or image markers, and tool uses become a function-like name with JSON arguments. The pieces are joined with newlines.

**Call relations**: Compaction._prepare uses this to build the summarizer input. Compaction._references and Compaction._window_text use it to search for paths and anchors. Compaction._tokens uses it to estimate text cost.

*Call graph*: called by 4 (_prepare, _references, _tokens, _window_text); 1 external calls (dumps).


### Prompt assembly inputs
Builds the final model prompt and its supporting runtime capability text, including spawn instructions and safe wrapping for untrusted content.

### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `prompt construction before a model turn`

This file is the prompt assembly room. The project keeps large pieces of system prompt text in separate Markdown files, with named holes such as {{agent-prompt}}, {{sections}}, and {{knowledge_cutoff}}. This renderer fills those holes before the prompt is sent to the model.

The main job is to prevent half-built prompts from slipping through. If an agent prompt says it needs a variable like {{project_name}}, the caller must supply exactly that variable. Missing variables fail loudly. Extra variables also fail loudly, because they probably mean the caller and prompt disagree. After all replacements are done, the renderer checks again for any leftover {{...}} slots. If any remain, it raises an error instead of sending confusing raw braces to the model.

It also formats supporting blocks. Skills are turned into an <available_skills> list. Pack-provided sections are sorted and joined. The model’s machine-readable knowledge cutoff, such as “2026-02”, is turned into a human-readable date like “February 2026”.

Finally, the completed prompt is cleaned up to avoid long runs of blank lines and wrapped in a RenderedPrompt object with a SHA-256 digest, a short fingerprint of the prompt content. That digest helps logs and observability answer: “Which exact prompt did this model turn use?”

#### Function details

##### `rendered_prompt`  (lines 53–54)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This function packages finished prompt text together with a digest, which is a stable fingerprint of the exact content. Someone uses it when they need both the text to send to the model and an identifier that changes whenever the prompt text changes.

**Data flow**: It receives the completed prompt text. It encodes that text, calculates a SHA-256 hash from it, prefixes the hash with “sha256:”, and returns a RenderedPrompt containing both the digest and the original content. It does not change anything outside itself.

**Call relations**: This is the final wrapping step used by render_template. After render_template has filled every slot, checked for leftovers, and cleaned blank lines, it calls rendered_prompt so the result can be sent onward with a reliable fingerprint.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 57–74)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This function builds the main agent system prompt from the standard shell template. It combines the agent’s own instructions, contributed capability sections, optional skills, citation text, and the model’s knowledge cutoff into one finished prompt.

**Data flow**: It receives an agent prompt, a list of section name/body pairs, an optional list of skill name/description pairs, and a knowledge cutoff string in YYYY-MM form. It turns the cutoff into a readable month and year, inserts it into the knowledge-cutoff block, places that block into the shell template, and then passes everything to render_template. The result is a RenderedPrompt with final text and digest.

**Call relations**: This is the high-level entry point in this file for normal system prompt rendering. It prepares the shell-specific knowledge cutoff piece, then hands off to render_template for the shared filling, validation, cleanup, and digest creation.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 77–95)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This function is the general template filler and safety checker. It replaces the known prompt slots, verifies that no unexpected {{...}} placeholders remain, and returns the finished prompt with a digest.

**Data flow**: It receives a template, an agent prompt, a mapping of variable names to replacement text, a list of skills, and a list of sections. First it substitutes variables inside the agent prompt. If there is agent prompt text but the template has no place for it, it raises an error. Then it fills the skill index, citation block, sorted sections, and agent prompt into the template. It searches the finished text for any leftover double-brace slots and raises an error if it finds any. Otherwise it collapses excessive blank lines, trims the end, and returns a RenderedPrompt.

**Call relations**: render_system_prompt calls this after preparing the outer shell. Inside, render_template relies on _substitute_vars to make the agent prompt safe, render_skill_index to format the skills block, and rendered_prompt to package the completed result.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 98–107)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This function turns the list of available skills into a small prompt block the model can read. If there are no skills, it returns an empty string so the final prompt does not contain a useless empty section.

**Data flow**: It receives skill pairs, where each pair has a skill name and description. With no skills, it outputs an empty string. With skills, it builds a block wrapped in <available_skills> and </available_skills>, with one bullet line per skill.

**Call relations**: render_template calls this while filling the {{skill_index}} slot. Its output becomes the part of the system prompt that tells the model which loadable skills are available.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 110–117)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This function safely fills variables inside an agent prompt. It is strict on purpose: every variable written in the prompt must be supplied, and every supplied variable must actually appear in the prompt.

**Data flow**: It receives prompt text that may contain placeholders like {{name}} and a mapping from names to replacement strings. It scans the text to find declared placeholders, compares them with the supplied mapping keys, and raises an error if anything is missing or extra. If the two sides match exactly, it replaces each placeholder with its supplied value and returns the filled text.

**Call relations**: render_template calls this before inserting the agent prompt into the larger template. This keeps bad prompt variables from reaching later steps, where they might otherwise appear to the model as raw {{...}} text.

*Call graph*: called by 1 (render_template).


### `core/src/ufo/loop/spawn_catalog.py`

`domain_logic` · `per-turn request handling`

When an agent wants to delegate work, it needs to know two things: what targets it may spawn, and what payload fields each target expects. This file creates that guide on demand for the current turn. Think of it like printing the current restaurant menu right before ordering, rather than relying on an old copy taped to the wall.

There are two kinds of spawn targets. First are fixed subagent profiles, which come from the live subagent registry. Second are workspace agents, which come from database rows for the current workspace. Workspace agents are filtered by permission: a normal member sees their own agents, while an admin can see all agents in the workspace, including ownerless provisioned ones.

The file turns both sources into a Markdown table with columns for the target name, the kind of target, and the payload keys. It also handles name collisions: if a workspace agent has the same name as a profile, the agent is shown with an `agent:` prefix, matching the form that spawning accepts. Finally, the table is wrapped as a `RuntimeSkill`, so another agent can load it by the fixed name `spawn-catalog` before choosing a spawn target.

#### Function details

##### `_profile_payload`  (lines 29–36)

```
def _profile_payload(profile: SubagentProfile) -> str
```

**Purpose**: This helper describes what input fields a fixed subagent profile expects. It produces a short, human-readable list of field names, marking which ones are optional.

**Data flow**: It receives a `SubagentProfile`, reads the fields from that profile’s input model, and sorts them by name. If there are no fields, it returns “(no fields)”; otherwise it returns a comma-separated list where required fields are plain names and optional fields are labeled optional.

**Call relations**: The main catalog builder calls this while writing the table rows for subagent profiles. Its output becomes the payload column for each profile, so the agent reading the catalog knows what keys to include when spawning that profile.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `_schema_payload`  (lines 39–49)

```
def _schema_payload(schema: Mapping[str, object] | None) -> str
```

**Purpose**: This helper describes what input fields a workspace agent expects, based on that agent’s stored input schema. If no custom schema is present, it falls back to the default task input fields.

**Data flow**: It receives a schema-like mapping or `None`. With `None`, it reads the default `TaskInput` fields and returns their names. With a schema, it looks for a `properties` section and a `required` list, then returns sorted field names, labeling any non-required fields as optional. If the schema has no usable properties, it returns “(no fields)”.

**Call relations**: The main catalog builder calls this for each workspace agent row fetched from the database. Its result fills in the payload column for agent targets, matching the input shape that spawning will expect.

*Call graph*: called by 1 (spawn_catalog_skill).


##### `spawn_catalog_skill`  (lines 52–103)

```
async def spawn_catalog_skill(registry: SubagentRegistry, member_id: UUID | None) -> RuntimeSkill
```

**Purpose**: This is the main builder for the `spawn-catalog` runtime skill. It gathers the current spawnable profiles and workspace agents, formats them into a readable table, and returns a skill object that another agent can load for delegation guidance.

**Data flow**: It receives the live subagent registry and the current member’s ID, if there is one. It reads profile names from the registry, opens a workspace database transaction, checks whether the member is an admin, and queries the current workspace’s agent rows using the right permission filter. It then combines profile rows and agent rows into Markdown instructions, adds skill metadata, and returns a `RuntimeSkill` containing the catalog.

**Call relations**: This function sits next to the spawn dispatch path in the larger flow: it uses the same registry and workspace records that spawning resolves against, so the instructions cannot drift away from reality. While building the catalog, it asks `_profile_payload` and `_schema_payload` to describe inputs, uses the workspace transaction and current workspace helpers to read the right database rows, checks admin access through the membership helper, and finally hands the assembled text to `RuntimeSkill` so the runtime can expose it as a loadable skill.

*Call graph*: calls 2 internal fn (_profile_payload, _schema_payload); 5 external calls (__init__, select, workspace_tx, member_is_admin, ws_current).


### `core/src/ufo/untrusted.py`

`util` · `cross-cutting`

This small file solves a safety problem: sometimes the system must show an agent text that came from outside the trusted workspace. That text might contain commands like “ignore previous instructions.” The system still needs to pass the text along, but it must clearly mark it as untrusted.

The file defines a standard “wall” around that content. Think of it like putting a suspicious document inside a clear evidence bag: the reader can inspect it, but the bag says not to treat the paper as an instruction sheet. The wrapper includes a plain warning, an opening marker that names the source, the content itself, and a closing marker.

One important detail is that the content is not allowed to break out of its own wrapper. If the outside text already contains the closing marker, this file replaces that marker with a harmless escaped version. That means malicious or accidental text cannot end the untrusted section early and continue afterward as if it were trusted instructions.

This file matters because multiple parts of the system need the same protection. Tool results and background subagent results can both carry outside text. By keeping the wrapping rule here, those paths do not invent slightly different safety behavior.

#### Function details

##### `wall`  (lines 22–30)

```
def wall(source: str, content: str) -> str
```

**Purpose**: Wraps a piece of outside text in a clear warning and boundary markers, so an agent can read it as untrusted data rather than trusted instructions. It also makes sure the text cannot fake the closing marker and escape the wrapper.

**Data flow**: It receives a source name and the content from that source. It builds a warning that names the source, adds an opening untrusted-content marker, copies in the content after replacing any real closing marker with a safe escaped version, and then adds the genuine closing marker at the end. The result is one string that safely labels the original content as untrusted.

**Call relations**: Other parts of the system call this when they are about to show an agent content that came from outside the trusted workspace, such as tool output or a background child agent’s returned result. This function does not call deeper helpers; it is the shared final step that turns raw outside text into a clearly fenced-off block.

## 📊 State Registers Touched

- `reg-extension-registry` — The loaded list of installed extensions, packs, routes, tools, skills, jobs, credentials, backends, and migrations.
- `reg-workspace-principals` — The current workspace, members, agents, controlling users, and ownership identities used to decide who is acting.
- `reg-credential-vault` — The encrypted store of API keys, OAuth tokens, and other secrets that can be injected only into approved places.
- `reg-connection-grants` — The saved account connections and per-agent permissions that say which outside accounts an agent may use.
- `reg-agent-settings` — The durable settings for each agent, including model choice, reasoning mode, internet access, sandbox size, tools, setup needs, icon, and visibility.
- `reg-conversation-records` — The durable conversation rows that remember where a conversation came from, which agent owns it, its audience, title, sandbox, and current metadata.
- `reg-transcript-history` — The saved conversation transcript and compaction snapshots that all surfaces, workers, prompts, and recovery logic read and update.
- `reg-model-catalog` — The shared directory of available AI models, their providers, limits, prices, key requirements, and routing behavior.
- `reg-tool-catalog` — The shared catalog of tools the model can call, including built-in tools, extension tools, connector tools, and their safety labels.
- `reg-memory-store` — The durable store of remembered facts and memory-search results that can be written, deduplicated, recalled, and shown later.
- `reg-subagent-delegation` — The shared state for spawned helper agents, including their catalog entries, parent-child turn links, required results, names, and cancellation state.
- `reg-prompt-change-proposals` — The durable proposal and governance state for suggested agent prompt or behavior changes, including approval and offline-improvement outcomes before agent settings are rewritten.
- `reg-skill-assets-state` — The discovered skill packages, dependency metadata, copied helper files, and per-agent skill asset state used when building prompts and executing skill-backed work.
- `reg-connector-action-cache` — Dynamic connector/MCP action schemas, allowed-action listings, and runtime client/session caches reused when exposing and executing external-service actions.
- `reg-prompt-render-audit` — Rendered-prompt fingerprints, template provenance, and compaction/prompt hashes used to trace or reproduce the exact context sent to models.
- `reg-turn-created-references` — Saved references or citations created by a turn so final replies, source panels, transcripts, and later turns can resolve cited material consistently.
- `reg-turn-surface-context` — Durable per-turn inbound context such as speaker, on-behalf-of member, timezone, original surface metadata, and connection-authorization status carried from admission into prompting, execution, and delivery.
- `reg-conversation-workspace-files` — The mutable per-conversation working file tree that tools, skills, document automation, site building, artifacts, and cleanup read or modify before changes are snapshotted or shared.
