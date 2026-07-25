# Prompt, transcript, and retrieval context construction  `stage-9`

This stage prepares the “briefing packet” the model receives before it answers. It runs during the main conversation loop, just before a reply is generated. Its job is to gather the current conversation, the right instructions, and any useful background facts, then fit them into the model’s limited context window, meaning the amount of text the model can read at once.

The compaction file acts like an editor when the transcript gets too long. It keeps the newest messages as they are, turns older messages into a structured summary, and saves both versions so people can review what changed. The prompt rendering file builds the system prompt, which is the instruction sheet for the model. It fills in templates, checks that required pieces are present, and records a digest, like a fingerprint, so prompt versions can be tracked.

The recall sub-stage adds outside knowledge. It searches saved memories, synced source pages, search indexes, and linked knowledge-graph facts. Together, these parts turn a raw chat into a focused, traceable context bundle.

## Sub-stages

- [Memory, search, and graph recall](stage-9.1.md) `stage-9.1` — 8 files

## Files in this stage

### Context Preparation
Compacts oversized conversation history and renders the final traceable system prompt for the model.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling, when a conversation window is near or over the model limit`

Long AI conversations eventually become too large to send back to the model. This file solves that by doing transcript compaction: like packing old paperwork into a labeled archive box while keeping today's papers on the desk. When the message history passes a token limit, it splits the transcript into an older head and a recent tail. The tail stays exactly as-is, because recent details are usually most important. The head is rendered into plain text, with images marked as "[image]" and huge repeated text runs collapsed so the summary request itself does not become wastefully large.

The file then asks the model for a strict JSON summary and validates that summary against the project's CompactionSummary shape. If the model provider says the summary prompt is still too large, it drops some of the oldest rounds and tries again a limited number of times. It also preserves references to durable tool-output files, so large outputs that were saved to disk are not lost.

Finally, it builds a new transcript window: one user message containing the compacted context, followed by the recent tail. It writes compressed records of the before window, after window, and typed summary to blob storage. Hooks are fired before and after compaction so extensions can observe what happened.

#### Function details

##### `is_context_overflow`  (lines 84–90)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: Checks whether an error from a model provider means the request was too large for the model's context window. This lets the system shrink and retry instead of treating every such failure as fatal.

**Data flow**: It takes an exception, combines the exception type and message into lowercase text, and searches for phrases such as "too long" or "maximum context". It returns true when one of those phrases appears, otherwise false.

**Call relations**: Compaction._summarize uses this after a failed summary attempt. If this helper says the failure was a size problem, the summarizer can drop old transcript rounds and try again.

*Call graph*: called by 1 (_summarize).


##### `Compaction.maybe_compact`  (lines 117–134)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether the current message window needs compaction. It is the public entry point for this file's main behavior.

**Data flow**: It receives the current messages and an optional force flag. It first refuses to compact very short histories, then estimates token use and compares it with the trigger limit unless compaction was forced. It returns either the original messages with no usage records, or the compacted messages plus model usage from the summary call.

**Call relations**: This is the method other loop code would call before sending a transcript to the model, or after a provider says the context is too large. When compaction is needed, it hands the work to Compaction._compact; to make the decision, it uses Compaction._tokens.

*Call graph*: calls 2 internal fn (_compact, _tokens).


##### `Compaction._compact`  (lines 137–176)

```
async def _compact(self, messages: tuple[Message, ...], reason: Literal['auto', 'force']) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction pipeline once: choose what to summarize, notify hooks, summarize, rebuild the transcript, save records, and notify hooks again.

**Data flow**: It receives the current messages and the reason for compaction, either automatic or forced. It splits the transcript into an old head and recent tail, measures the old size, fires a pre-compaction hook, finds the next storage index, summarizes the head, collects durable file references, renders the summary into a replacement message, saves before/after/summary records, fires a post-compaction hook, and returns the new message window plus usage records.

**Call relations**: Compaction.maybe_compact calls this when the transcript must shrink. This method is the central assembly line: it calls selection, summarization, reference collection, rendering, persistence, and token-counting helpers in order.

*Call graph*: calls 7 internal fn (_next_index, _persist, _references, _render, _select, _summarize, _tokens); called by 1 (maybe_compact); 3 external calls (__init__, __init__, __init__).


##### `Compaction._select`  (lines 178–194)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: Chooses which messages are old enough to summarize and which recent messages must stay untouched. It avoids splitting connected assistant/tool-result exchanges.

**Data flow**: It receives all messages, groups them into conversation rounds, then keeps enough whole rounds from the end to cover at least the configured recent-message count. If nothing remains to summarize, it returns nothing; otherwise it returns the old rounds and the flattened recent tail.

**Call relations**: Compaction._compact calls this before doing any expensive work. It relies on Compaction._rounds to make safe groups so later steps do not separate a tool request from its answer.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 196–210)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Groups messages into API rounds so related messages stay together. A round starts at an assistant message and includes following user/tool-result messages that answer it.

**Data flow**: It reads the message sequence from oldest to newest, collecting messages into a current group. When it sees a new assistant message after existing content, it closes the current group and starts a new one. It returns a tuple of these groups.

**Call relations**: Compaction._select calls this while deciding the compacted boundary. Its grouping protects the rest of the pipeline from creating a transcript where a tool use and its result are separated.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 212–234)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: Turns the old transcript head into a validated structured summary, with controlled retries if the summary request is still too large.

**Data flow**: It starts with the selected head rounds. It asks Compaction._summarize_once to summarize them. If that fails because of context overflow, it uses Compaction._drop_oldest to remove the oldest part and retries, up to the configured limit. On success it returns the summary and usage record; on non-recoverable failure it raises an error.

**Call relations**: Compaction._compact calls this after selecting the old transcript head. It uses is_context_overflow to tell retryable size failures apart from other problems and delegates the actual model call to Compaction._summarize_once.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 236–256)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Makes one model request asking for a JSON compaction summary. It represents the single external model call in one summary attempt.

**Data flow**: It receives transcript rounds, prepares them as text, builds a ModelRequest with the compaction system prompt, and streams the model response. It collects text chunks and the usage record, then parses the combined text into a CompactionSummary. It returns the validated summary and usage.

**Call relations**: Compaction._summarize calls this for each attempt. It hands rendering to Compaction._prepare and validation to Compaction._parse_summary.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 258–277)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...]) -> str
```

**Purpose**: Converts old transcript rounds into the plain text prompt sent to the summarizing model. It also repeats the required output format at the end so the model is less likely to continue the conversation instead of returning JSON.

**Data flow**: It receives grouped messages, turns each message into a "role: content" block using Compaction._text, joins those blocks, folds long repeated runs with Compaction._fold_repeated_runs, and appends the final instruction telling the model to return one JSON object.

**Call relations**: Compaction._summarize_once calls this before building the model request. It depends on Compaction._text to preserve message content in readable form and on Compaction._fold_repeated_runs to keep repetitive prompts small.

*Call graph*: calls 2 internal fn (_fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 279–292)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Shrinks large verbatim repetitions in the rendered transcript. This prevents a stuck loop or pasted spam from making the summary prompt enormous while preserving the fact that repetition happened.

**Data flow**: It receives text and searches for short word sequences repeated many times in a row. Each repeated run is replaced with one copy followed by a marker like "[repeated 20 times]". It returns the shortened text.

**Call relations**: Compaction._prepare calls this after rendering transcript messages. Its nested replacement helper, Compaction._fold_repeated_runs.fold, computes each replacement string.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 287–290)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement text for one repeated run found by the repetition-folding regular expression.

**Data flow**: It receives one regex match, reads the repeated unit, estimates how many times it appeared, and returns the unit followed by a repeated-count marker.

**Call relations**: This is used inside Compaction._fold_repeated_runs as the callback passed to the regex substitution. It is not a separate pipeline step; it is the small formatter used for each match.


##### `Compaction._drop_oldest`  (lines 294–299)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Removes the oldest slice of transcript rounds before a retry. This is the system's pressure-release valve when the summary request itself is too large.

**Data flow**: It receives the current head rounds and calculates a drop count equal to at least one round, roughly one fifth of the set. It returns the remaining newer rounds.

**Call relations**: Compaction._summarize calls this only after a context-overflow failure from Compaction._summarize_once. The reduced rounds are then retried.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 301–342)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Extracts and validates the JSON summary returned by the model. It refuses to accept empty, malformed, or schema-invalid summaries.

**Data flow**: It receives raw model text, finds the first balanced JSON object even if extra text surrounds it, and asks CompactionSummary to validate that JSON. If validation succeeds and the intent is not blank, it returns the typed summary. Otherwise it raises a RuntimeError.

**Call relations**: Compaction._summarize_once calls this after collecting the model's streamed text. It is the safety gate that stops a bad model response from replacing the transcript with unusable context.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 344–361)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds durable tool-output file paths mentioned in the old transcript head that should still be visible after compaction. These are saved file references, not inlined file contents.

**Data flow**: It receives the head rounds and the kept tail. It scans the tail for already-visible tool-output paths, then scans the head for paths that are not already visible and have not been added before. It returns only the most recent limited set of paths.

**Call relations**: Compaction._compact calls this after summarization and before rendering the compacted message. It uses Compaction._text so it can search all message block types as plain text.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 363–394)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...]) -> str
```

**Purpose**: Turns the validated structured summary into the single human-readable message that replaces the old transcript head.

**Data flow**: It receives a CompactionSummary and optional durable file references. It builds named sections for intent, concepts, files, errors, decisions, pending tasks, current work, next step, loaded skills, and references, omitting empty sections. It returns one text string prefixed as compacted context.

**Call relations**: Compaction._compact calls this to create the replacement user message. It uses Compaction._bullets to format list-like summary fields.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_compact).


##### `Compaction._bullets`  (lines 396–397)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a tuple of strings as markdown-style bullet lines. It keeps rendered summary sections consistent.

**Data flow**: It receives text items and prefixes each one with "- ". It returns the items joined with newline characters.

**Call relations**: Compaction._render calls this whenever a summary section is naturally a list, such as pending tasks or durable references.

*Call graph*: called by 1 (_render).


##### `Compaction._persist`  (lines 399–410)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Saves the complete compaction record: the transcript before compaction, the transcript after compaction, and the structured summary.

**Data flow**: It receives an index, before messages, after messages, and the summary. It writes the before and after windows through Compaction._write, serializes the summary as JSON, compresses it with LZ4 compression, and stores it in the blob store under the summary key.

**Call relations**: Compaction._compact calls this after building the compacted window. It uses Compaction._key to choose storage locations and Compaction._write for the two message windows.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 412–416)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next available numbered compaction slot for this conversation. This prevents a new compaction from overwriting an older saved record.

**Data flow**: It starts at index 1 and checks whether an "after" record already exists for that index. It increments until it finds a missing slot, then returns that number.

**Call relations**: Compaction._compact calls this just before saving records. It uses Compaction._key to ask the blob store about each possible record path.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 418–425)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads a previously saved compaction record, if it exists. This is useful for inspection, evaluation, or recovery tooling.

**Data flow**: It receives a compaction index, builds keys for before, after, and summary records, and fetches those compressed blobs. If any blob is missing, it returns nothing. If all are present, it decodes them into a CompactionRecord.

**Call relations**: This is a read-side helper rather than part of the live compaction flow. It uses Compaction._key for storage paths and hands the raw blobs to ufo.transcript.decode_compaction to rebuild the typed record.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 427–435)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes one half of a compaction window, either the before messages or the after messages, to blob storage.

**Data flow**: It receives an index, a label of "before" or "after", and the messages. It wraps the messages in a CompactionWindow, serializes them as stable compact JSON, compresses the bytes with LZ4, and stores them at the key for that half.

**Call relations**: Compaction._persist calls this twice, once for the original transcript and once for the replacement transcript. It uses Compaction._key to decide where the blob should go.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 437–438)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the blob-storage key for a compaction artifact. It centralizes the naming rule so all reads and writes use the same path layout.

**Data flow**: It receives a compaction index and a half name: before, after, or summary. It combines those with the conversation id through the transcript compaction-key helper and returns the resulting string.

**Call relations**: Storage-related methods call this whenever they need to read, write, or check a compaction artifact. It is used by Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 440–448)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: Estimates how many tokens the current message window costs. A token is a chunk of text the model counts internally; this function uses a rough but practical estimate.

**Data flow**: It receives messages, turns each message into text with Compaction._text, estimates text tokens from character length, and adds a fixed token cost for each inline image found by Compaction._image_count. It returns the total estimate.

**Call relations**: Compaction.maybe_compact uses this to decide whether compaction should start. Compaction._compact also uses it to report before-and-after sizes to hooks.

*Call graph*: calls 2 internal fn (_image_count, _text); called by 2 (_compact, maybe_compact).


##### `Compaction._image_count`  (lines 450–460)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts inline images inside a message so token estimates do not ignore image-heavy conversations. Images can consume model context even though they have little or no text.

**Data flow**: It receives one message. If the content is plain text, it returns zero. If the content is block-based, it counts ImageBlock entries directly and images nested inside tool-result blocks, then returns the total.

**Call relations**: Compaction._tokens calls this for each message while estimating context size. It complements Compaction._text, which cannot represent image cost by character length alone.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 462–480)

```
def _text(self, message: Message) -> str
```

**Purpose**: Renders any supported message content into searchable, readable text. It preserves important non-text signals, such as images and tool calls, in simple textual form.

**Data flow**: It receives one message. If the content is already a string, it returns it. Otherwise it walks through content blocks, appending text blocks, "[image]" markers, tool-result content, and tool-use calls formatted with JSON arguments. It returns the pieces joined by newlines.

**Call relations**: Compaction._prepare uses this to build the summary prompt, Compaction._references uses it to find saved tool-output paths, and Compaction._tokens uses it for token estimation.

*Call graph*: called by 3 (_prepare, _references, _tokens); 1 external calls (dumps).


### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `prompt construction before a model request`

A system prompt is the instruction sheet the application gives to a language model before asking it to work. This file is the prompt assembly bench. It starts with core markdown templates from disk, then inserts the agent’s own instructions, available skills, capability sections from packs, a shared citation rule block, and the model’s knowledge cutoff date. The knowledge cutoff matters because the model should know the date after which its built-in knowledge may be stale; if the selected model has no declared cutoff, this code stops immediately instead of sending an incomplete prompt.

The renderer is intentionally strict. Agent prompts can contain variables written like `{{name}}`. The caller must supply exactly those variables: no missing ones and no extras. This is like filling out a form where every blank must be completed, and you are not allowed to bring unrelated answers. After all replacements are done, the file scans for any leftover `{{...}}` slots. If any remain, it raises an error rather than letting raw template markers leak to the model.

Finally, the completed prompt is cleaned up by reducing long blank gaps, then wrapped with a SHA-256 digest, which is a stable fingerprint of the exact text. That fingerprint helps logs and observability tools show which prompt version was used.

#### Function details

##### `rendered_prompt`  (lines 71–72)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This function packages finished prompt text together with a fingerprint of that exact text. The fingerprint makes it possible to tell when the prompt content changed, even if the change is small.

**Data flow**: It receives the final prompt text as a string. It converts that text into bytes, computes a SHA-256 hash, prefixes it with `sha256:`, and returns a `RenderedPrompt` object containing both the digest and the original content. It does not change any outside state.

**Call relations**: After `render_template` has filled and checked the prompt, it calls this function as the final wrapping step. This function hands back the object that downstream code can send to the model and also log or trace by digest.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 75–92)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, model: str) -> RenderedPrompt
```

**Purpose**: This is the main entry point for building the ordinary agent system prompt. It combines the core shell template with the agent prompt, pack sections, skill list, citation block, and the selected model’s knowledge cutoff.

**Data flow**: It receives the agent’s prompt text, a list of capability sections, an optional list of skills, and the model name. It looks up the model’s knowledge cutoff date, inserts that date into the knowledge-cutoff block, puts that block into the shell template, and then asks `render_template` to fill the remaining pieces. If the model is unknown, it raises an error instead of producing a prompt without a cutoff.

**Call relations**: Higher-level prompt-building code calls this when it needs a complete system prompt for an agent turn. This function does the model-specific preparation, then delegates the general template filling and validation work to `render_template`.

*Call graph*: calls 1 internal fn (render_template).


##### `render_template`  (lines 95–113)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This function fills a prompt template and refuses to return it if any slot or variable is wrong. It is the central safety check that prevents half-rendered prompt text from being sent to the model.

**Data flow**: It receives a template, agent prompt text, variable values for that agent prompt, a skill list, and section text. First it substitutes variables inside the agent prompt through `_substitute_vars`. If there is agent prompt text but the template has no place for it, it raises an error. Then it replaces the skill slot, citation slot, sections slot, and agent-prompt slot. It scans the finished text for any remaining `{{...}}` markers, raises an error if it finds any, normalizes extra blank lines, trims the end, and returns a `RenderedPrompt` with a digest.

**Call relations**: `render_system_prompt` calls this after preparing the shell and knowledge cutoff. Inside the template-filling flow, it relies on `_substitute_vars` for strict variable replacement, `render_skill_index` for the skills block, and `rendered_prompt` to produce the final prompt object.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 116–125)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This function turns the list of available skills into a small text block that can be inserted into the system prompt. The block tells the model which named skills exist and what each one is for.

**Data flow**: It receives a sequence of skill name and description pairs. If the list is empty, it returns an empty string. Otherwise, it creates an `<available_skills>` block with one bullet per skill and returns that text.

**Call relations**: `render_template` calls this when it reaches the skill slot in the prompt template. Its output is inserted directly into the final prompt so the model can see what extra skills are available.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 128–135)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This helper fills variables inside the agent prompt, but only when the caller’s variable map exactly matches the placeholders in the text. It prevents both forgotten values and accidental unused values.

**Data flow**: It receives a prompt template string and a mapping of variable names to replacement text. It finds all placeholders shaped like `{{variable_name}}`, compares them with the supplied variable names, and raises an error if any placeholder is missing a value or any supplied value has no matching placeholder. If everything matches, it replaces each placeholder with its value and returns the filled prompt text.

**Call relations**: `render_template` calls this at the start, before inserting the agent prompt into the larger shell. By doing this early, the rest of the rendering flow only works with an already-validated agent prompt.

*Call graph*: called by 1 (render_template).

## 📊 State Registers Touched

- `reg-search-index-state` — The searchable content indexes, chunks, embeddings, and search backend choices used for recall and source replay.
- `reg-conversation-transcript` — The saved conversation thread, messages, transcript edits, and compacted summaries.
- `reg-source-sync-state` — The remembered external sources, imported pages, raw bodies, change records, cursors, errors, and deletion markers.
- `reg-memory-store` — The workspace memory facts, episodes, ownership labels, confidence, and consolidation indexes used for recall.
- `reg-knowledge-graph` — The stored people, companies, things, and relationships used as structured background knowledge.
- `reg-prompt-governance` — The saved prompt digests, prompt-change proposals, approvals, and experiment evidence that control instruction changes.
- `reg-subagent-work-tree` — The parent-child turn links, delegated work state, and message flow between main agents and subagents.
- `reg-agent-todo-goal-state` — The agent-maintained goal/todo checklist state that tools update and later prompt/runtime assembly can reload as working context.
