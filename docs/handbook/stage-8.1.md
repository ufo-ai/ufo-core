# Transcript compaction and prompt construction  `stage-8.1`

This stage is behind-the-scenes support for the main conversation loop. Its job is to prepare what the language model sees before it answers, while staying within the model’s context limit, meaning the maximum amount of text it can read at once.

The compaction code acts like a careful editor. When a conversation gets too long, it summarizes older messages into a structured record and leaves the most recent messages untouched, so the model still sees the latest details exactly as written. It also saves both the original and compacted forms, so information is not simply thrown away.

The memory event file defines the small, structured note used when the memory extension brings back stored memories before a reply. It also limits how much extra recall detail can be attached, preventing memory data from crowding out the current conversation.

The prompt rendering code then assembles the final instruction prompt. It fills in template blanks, checks that required pieces are present, and creates a fingerprint so prompt versions can be recognized and tracked.

## Files in this stage

### Context preparation
Conversation history and recalled memories are prepared so the model receives relevant context within safe limits.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `main loop, before sending a turn to the model when the transcript is near or over the context limit`

A language model can only read a limited amount of text at once. This file is the system’s “make room without forgetting” tool. When a conversation gets too large, it takes the older part of the transcript, asks the model to summarize it into a strict JSON shape, then replaces that older text with one compact “Compacted context” message. The newest messages are kept word-for-word, because recent details are usually the most important and may include tool calls that must stay paired with their results.

The process is careful rather than casual. It first decides whether compaction is needed, then splits the transcript at a safe boundary. It renders the older messages into plain text for the summarizer, marking images as “[image]” and folding huge repeated text runs into a short count marker. If the summary request is still too large, it drops some of the oldest rounds and retries. Once a valid summary is produced, it adds system-known details such as loaded skills and durable tool-output file references.

Finally, it writes three compressed records to blob storage: the full transcript before compaction, the replacement transcript after compaction, and the typed summary. Like storing both the original document and the edited version, this lets later tools, audits, or evaluations recover exactly what happened.

#### Function details

##### `is_context_overflow`  (lines 79–85)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: Recognizes errors that likely mean the model provider rejected a request because it was too large. This lets the system shrink and retry instead of treating every failure as unrecoverable.

**Data flow**: It receives an exception, combines the exception type and message into lowercase text, and searches for known phrases such as “context length” or “prompt is too large.” It returns true when one of those phrases is found, otherwise false.

**Call relations**: Compaction._summarize uses this after a summary attempt fails. If this helper says the failure was a size problem, the compaction flow can drop some old rounds and try the model call again.

*Call graph*: called by 1 (_summarize).


##### `Compaction.maybe_compact`  (lines 116–136)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether a transcript needs to be compacted. It is the public gatekeeper: most calls return the messages unchanged, but oversized or forced calls start the compaction pipeline.

**Data flow**: It receives the current messages, a force flag, and any active member requests. It first refuses to compact very short transcripts, then estimates the token size with Compaction._tokens and compares that to the trigger limit unless force is set. It returns either the original messages with no usage records, or the compacted messages plus model usage from the summary call.

**Call relations**: This is the usual entry into this file’s workflow. When it decides compaction is needed, it hands the transcript to Compaction._compact, which performs the real summary, storage, and replacement work.

*Call graph*: calls 2 internal fn (_compact, _tokens).


##### `Compaction._compact`  (lines 139–182)

```
async def _compact(self, messages: tuple[Message, ...], reason: Literal['auto', 'force'], active_requests: tuple[str, ...]) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction job once the decision has been made. It chooses what to summarize, fires observation hooks, asks for a summary, builds the replacement message, saves records, and returns the new transcript window.

**Data flow**: It receives all messages, the reason for compaction, and active requests. It splits old and recent messages with Compaction._select, counts the original size, fires a pre-compaction hook, finds the next storage index, summarizes the old part, drains loaded skill names into the summary, finds durable file references, renders the final compacted context message, saves before/after/summary records, fires a post-compaction hook, and returns the replacement transcript plus usage records.

**Call relations**: Compaction.maybe_compact calls this only when compaction should happen. Inside, it coordinates nearly every helper in the file: selection, token counting, summarizing, rendering, persistence, and hook notification.

*Call graph*: calls 7 internal fn (_next_index, _persist, _references, _render, _select, _summarize, _tokens); called by 1 (maybe_compact); 3 external calls (__init__, __init__, __init__).


##### `Compaction._select`  (lines 184–200)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: Chooses the safe split point between old messages to summarize and recent messages to keep exactly. It avoids cutting apart a tool request from the tool result that answers it.

**Data flow**: It receives the full message tuple, groups it into rounds with Compaction._rounds, then keeps whole rounds from the end until at least the configured number of recent messages is preserved. It returns the older rounds as the summary head and the recent messages as the tail, or returns nothing if there is no older part left to compact.

**Call relations**: Compaction._compact calls this before doing any expensive work. Its output decides what Compaction._summarize will compress and what will be appended unchanged after the compacted summary message.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 202–216)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Groups messages into conversation rounds so related messages stay together. A round starts at an assistant message and includes following user or tool-result messages until the next assistant message.

**Data flow**: It receives a flat list of messages and walks through them in order. Whenever it sees a new assistant message after existing content, it closes the current group and starts another. It returns a tuple of message groups.

**Call relations**: Compaction._select depends on this grouping so it can keep or summarize complete rounds. This protects the transcript from being split in a way that would leave a tool call without its answer.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 218–240)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: Turns the older transcript rounds into a validated structured summary, with retries if the summary prompt itself is too large. It is the safety wrapper around the single external model call.

**Data flow**: It receives the head rounds selected for summarizing. It calls Compaction._summarize_once, and if that fails because of context overflow, it uses Compaction._drop_oldest to remove some oldest rounds and tries again up to the retry limit. On success it returns the summary and usage record; on unrecoverable failure it raises an error.

**Call relations**: Compaction._compact calls this after choosing the old transcript head. This function calls is_context_overflow to decide whether retrying makes sense, and delegates the actual model request to Compaction._summarize_once.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 242–262)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Makes one model request asking for a compact JSON summary of the old transcript. It also collects the usage information needed for metering or accounting.

**Data flow**: It receives rounds to summarize, prepares them as text with Compaction._prepare, builds a ModelRequest with the compaction system prompt, then streams the model response. Text chunks are joined together, the usage event is saved, and the final text is parsed by Compaction._parse_summary. It returns a CompactionSummary and a Usage record.

**Call relations**: Compaction._summarize calls this for each attempt. This function is the only place in the compaction pipeline that actually asks the model to write the summary.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 264–283)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...]) -> str
```

**Purpose**: Builds the text prompt that the summarizing model reads. It converts structured messages into clear role-labeled text and adds a final reminder that the answer must be JSON.

**Data flow**: It receives grouped transcript rounds, turns each message into text with Compaction._text, joins them as blocks like “user: ...” or “assistant: ...”, folds repeated runs with Compaction._fold_repeated_runs, and appends the format restatement. It returns one large prompt string.

**Call relations**: Compaction._summarize_once calls this just before making the model request. Its output becomes the user message sent to the summarizer.

*Call graph*: calls 2 internal fn (_fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 285–298)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Shortens long stretches where the same small phrase is repeated many times in a row. This prevents a stuck tool loop or pasted spam from making the summary prompt impossibly large.

**Data flow**: It receives a text string, searches for repeated word sequences that meet the configured size and count rules, and replaces each run with one copy plus a marker like “[repeated 25 times].” It returns the shortened text.

**Call relations**: Compaction._prepare calls this after rendering the old transcript. The nested fold helper is used by the regular expression replacement to build each shortened phrase.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 293–296)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement text for one repeated run found by the regular expression. It keeps one copy of the repeated phrase and records how many times it appeared.

**Data flow**: It receives a regex match, reads the repeated unit from the match, calculates the approximate repeat count from the matched text length, and returns a string containing the unit plus a “[repeated N times]” marker.

**Call relations**: This helper is used only inside Compaction._fold_repeated_runs as the replacement callback. It is the small counting step inside the larger repetition-folding pass.


##### `Compaction._drop_oldest`  (lines 300–305)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Shrinks an oversized summary request by removing the oldest slice of the transcript head. This gives retry attempts a smaller prompt to send to the model.

**Data flow**: It receives the grouped head rounds, removes at least one round and otherwise about one fifth of the oldest rounds, and returns the remaining newer rounds.

**Call relations**: Compaction._summarize calls this only after a model request fails because the prompt was too large. The smaller result is passed back into Compaction._summarize_once for another attempt.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 307–348)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Checks that the model’s answer is a usable compaction summary, not stray prose or malformed data. It protects the conversation from being replaced by a broken summary.

**Data flow**: It receives raw model text, finds the first balanced JSON object inside it, validates that JSON against the CompactionSummary schema, and checks that the intent field is not empty. It returns a typed CompactionSummary or raises a RuntimeError if anything is missing or invalid.

**Call relations**: Compaction._summarize_once calls this after collecting the streamed model text. It is the final gate before the summary can be rendered and persisted by the rest of the pipeline.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 350–367)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds important tool-output files mentioned in the compacted-away part of the conversation. These paths are kept as durable references so the model can re-read large outputs later instead of losing them.

**Data flow**: It receives the old head rounds and the kept tail messages. It scans text from the tail first to see which tool-output paths are already visible, then scans the head for additional .tool-output text files, avoids duplicates, and returns only the most recent limited set.

**Call relations**: Compaction._compact calls this after summarizing. The returned paths are passed to Compaction._render so they appear in the compacted context message.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 369–409)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns the validated summary into the single compacted context message that replaces the old transcript head. It formats only the sections that contain content and includes active requests exactly as supplied.

**Data flow**: It receives a CompactionSummary, durable reference paths, and active requests. It builds labeled sections such as intent, files, errors, pending tasks, loaded skills, and durable references, using Compaction._bullets for lists. It returns one text string beginning with “Compacted context:”.

**Call relations**: Compaction._compact calls this after summary validation and reference collection. The resulting text is wrapped in a new user Message and placed before the unchanged recent tail.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_compact).


##### `Compaction._bullets`  (lines 411–412)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a list of strings as simple markdown-style bullet lines. It keeps rendered summary sections readable and consistent.

**Data flow**: It receives a tuple of strings and prefixes each item with “- ”, joining them with newlines. It returns the formatted block of text.

**Call relations**: Compaction._render uses this whenever it needs to display summary lists, loaded skills, durable references, or similar section contents.

*Call graph*: called by 1 (_render).


##### `Compaction._persist`  (lines 414–425)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Saves the complete compaction record: what the transcript looked like before, what replaced it after, and the structured summary. This makes compaction auditable and recoverable.

**Data flow**: It receives a storage index, before messages, after messages, and the summary object. It writes the before and after windows through Compaction._write, serializes the summary to JSON, compresses it with LZ4, and stores it in the blob store under a generated key.

**Call relations**: Compaction._compact calls this once it has built the replacement transcript. It relies on Compaction._key and Compaction._write to place the data in the expected conversation compaction location.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 427–431)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next available compaction number for this conversation. This prevents a new compaction record from overwriting an older one.

**Data flow**: It starts at index 1 and checks blob storage for an existing “after” record at that index using Compaction._key. It increments until it finds a free slot, then returns that number.

**Call relations**: Compaction._compact calls this before saving records. The returned index is passed to Compaction._persist so all three compaction files share the same numbered location.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 433–440)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads back a previously saved compaction record if it exists. This is useful for inspection, evaluation, recovery, or tools that need to see what changed during compaction.

**Data flow**: It receives a compaction index, fetches the compressed before, after, and summary blobs using Compaction._key, and returns None if any are missing. If all are present, it passes the bytes to decode_compaction and returns a CompactionRecord.

**Call relations**: This is a read-side helper separate from the main compaction write path. It uses the same key scheme as Compaction._persist, Compaction._write, and Compaction._next_index so saved records can be located consistently.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 442–450)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Serializes and stores one transcript window, either the before version or the after version. It writes messages in a stable compressed JSON form.

**Data flow**: It receives an index, a label of “before” or “after,” and the messages to save. It wraps the messages in a CompactionWindow, converts that to sorted compact JSON, compresses the bytes with LZ4, and stores them in the blob store under Compaction._key.

**Call relations**: Compaction._persist calls this twice, once for the original transcript and once for the replacement transcript. It is the shared low-level writer for transcript windows.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 452–453)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the blob-storage path for a compaction file. It gives every saved before, after, or summary blob a predictable location tied to the conversation and compaction index.

**Data flow**: It receives a compaction index and which part is being addressed: before, after, or summary. It passes the conversation id, index, and part to compaction_key and returns the resulting storage key string.

**Call relations**: Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record all use this so reads and writes agree on the exact same blob names.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 455–471)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: Estimates how much of the model’s context window the messages will use. This estimate decides when automatic compaction should start.

**Data flow**: It receives messages, converts each message to readable text with Compaction._text, counts text roughly by characters per token, adds a fixed cost for each image counted by Compaction._image_count, and returns the total estimate.

**Call relations**: Compaction.maybe_compact uses this to decide whether the transcript has crossed the trigger. Compaction._compact also uses it to report before and after sizes to compaction hooks.

*Call graph*: calls 2 internal fn (_image_count, _text); called by 2 (_compact, maybe_compact).


##### `Compaction._image_count`  (lines 473–483)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts images inside a message so token estimates do not ignore them. Images have little or no text, but they still take up model context.

**Data flow**: It receives one message. If the content is plain text, it returns zero; otherwise it walks through content blocks and nested tool-result parts, counting ImageBlock items. It returns the number of images found.

**Call relations**: Compaction._tokens calls this for each message. The count is multiplied by a fixed image token estimate and added to the text estimate.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 485–503)

```
def _text(self, message: Message) -> str
```

**Purpose**: Converts a message’s mixed content into plain text for counting, summarizing, and path scanning. It makes text, images, tool results, and tool calls visible in a single string form.

**Data flow**: It receives one message. Plain string content is returned directly; structured blocks are converted piece by piece: text blocks become their text, images become “[image],” tool results become their content, and tool uses become a function-like name plus JSON arguments. It returns the joined text.

**Call relations**: Compaction._prepare uses this to render the old transcript for the summarizer. Compaction._references uses it to find tool-output paths, and Compaction._tokens uses it to estimate context size.

*Call graph*: called by 3 (_prepare, _references, _tokens); 1 external calls (dumps).


### `extensions/memory/ufo_ext_memory/events.py`

`data_model` · `cross-cutting`

The memory extension needs a shared vocabulary for reporting what it is doing. This file provides that vocabulary for one important moment: when the system looks up relevant memories before generating a reply. The constant `MEMORY_RECALL_EVENT` is the event name other code can use consistently, rather than each part of the system inventing its own string. That matters because event consumers, logs, or tracing tools only work reliably when everyone uses the same label. The file also defines two limits. `MAX_RECALLED_MEMORY_IDS` caps how many recalled memory identifiers should be included with the event, so event records stay small and readable. `MAX_RECALL_ERROR_CLASS_CHARS` limits the length of an error class name recorded during recall failures, which helps prevent oversized or messy diagnostic data. In everyday terms, this file is like a small label sheet for a warehouse: it does not move any boxes itself, but it makes sure everyone uses the same tag and does not overload the paperwork.


### Prompt rendering
The final system prompt is assembled from templates, validated, and fingerprinted for change tracking.

### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `prompt construction before model calls`

A system prompt is the instruction sheet the application gives the language model before asking it to work. This file is the prompt assembly station. It starts with Markdown template files shipped with the project, such as the main shell prompt, citation rules, knowledge cutoff wording, and compaction prompt. Then it fills special slots like the agent’s own instructions, the list of available skills, extra capability sections, and the model’s knowledge cutoff date.

The important safety feature here is strict checking. If an agent prompt declares a variable like {{project_name}}, the caller must supply exactly that variable. Missing variables fail with an error. Extra variables also fail. After the full prompt is assembled, the file checks again for any leftover {{slot}} text. That prevents accidental raw template markers from being sent to the model, which could confuse the model or hide a broken prompt configuration.

The file also normalizes the finished text by shrinking long blank gaps and trimming the end. Finally, it computes a SHA-256 digest, which is a stable fingerprint of the prompt content. Like a barcode on a product, this digest lets logs and observability tools tell exactly which prompt version was used.

#### Function details

##### `rendered_prompt`  (lines 49–50)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This function wraps finished prompt text together with a digest, which is a fingerprint of the exact content. Someone uses it when they need both the prompt to send to the model and a reliable way to identify that prompt later.

**Data flow**: It takes completed prompt text as input. It turns the text into bytes, runs SHA-256 over it to make a stable digest string, and returns a RenderedPrompt object containing both the digest and the original content. It does not change any outside state.

**Call relations**: After render_template has fully filled and cleaned the prompt, it calls rendered_prompt as the final packaging step. rendered_prompt creates the object that later parts of the system can send to the model and record in logs.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 53–70)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This is the main entry for building a normal agent system prompt. It combines the core shell template, the agent’s instructions, contributed sections, available skills, and the model’s knowledge cutoff into one final prompt.

**Data flow**: It receives the agent prompt, a list of section name/body pairs, an optional list of skill name/description pairs, and a required knowledge cutoff string in YYYY-MM form. It converts the machine-readable date into a human-readable month and year, inserts that into the knowledge cutoff block, puts that block into the shell template, and then passes everything to render_template. The output is a RenderedPrompt with final text and digest.

**Call relations**: This function sits above the lower-level template renderer. When the system needs the main prompt for an agent, it calls render_system_prompt, which prepares the knowledge cutoff wording and then hands the actual slot filling to render_template.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 73–91)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This function fills a prompt template and refuses to produce a prompt if any slot or variable is wrong. It is the central safety gate that turns template pieces into model-ready text.

**Data flow**: It receives a template, an agent prompt, a variable mapping for that agent prompt, a skill list, and a section list. First it asks _substitute_vars to replace variables inside the agent prompt. If there is agent prompt text but the template has no agent-prompt slot, it raises an error. Then it replaces the skill, citation, sections, and agent-prompt slots. It checks the finished text for leftover {{name}} patterns and raises an error if any remain. If all is well, it reduces extra blank lines, trims the end, and returns a RenderedPrompt.

**Call relations**: render_system_prompt calls this function after preparing the shell prompt. render_template relies on _substitute_vars for agent-prompt variables, render_skill_index for the available-skills block, and rendered_prompt to package the final result with its digest.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 94–103)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This function turns the list of loadable skills into a small text block the model can read. If there are no skills, it returns an empty string so the prompt does not contain an unnecessary section.

**Data flow**: It receives a sequence of skill names and descriptions. With no skills, it outputs an empty string. With skills, it creates an <available_skills> block where each skill appears as a bullet with its description, then returns that text.

**Call relations**: render_template calls this function while filling the skill-index slot. The result is inserted into the larger prompt so the model knows which optional skills are available.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 106–113)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This helper replaces variables inside an agent prompt, but only when the supplied variables exactly match the variables the prompt declares. It exists to catch prompt mistakes early instead of letting broken {{variable}} text reach the model.

**Data flow**: It receives a prompt template string and a mapping of variable names to replacement text. It scans the template for variable names, compares that set with the supplied mapping keys, and raises an error if anything is missing or extra. If the sets match, it replaces each {{name}} marker with its supplied value and returns the filled prompt text.

**Call relations**: render_template calls this before inserting the agent prompt into the larger shell. This means variable validation happens before the final unresolved-slot check, giving clear errors for missing or undeclared agent-prompt variables.

*Call graph*: called by 1 (render_template).
