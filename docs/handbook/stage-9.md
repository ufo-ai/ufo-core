# Per-Turn Setup: Context, Sandbox, Skills, Prompts, and Models  `stage-9`

Before the engine asks the AI model what to do next, it prepares the “workbench” for the turn. This stage gathers the material the model will need: the conversation so far, saved memory, available tools, reusable skills, safety limits, prompt text, and the model choice. It is part of the setup before the main work loop begins.

The compaction code acts like an editor for an overlong notebook. AI models can only read a limited amount of text at once, so it keeps the newest messages unchanged and turns older parts into a structured summary that can be checked later. It stores both versions for review.

The prompt rendering code builds the final instruction sheet for the model. It fills in named blanks in prompt templates, makes sure no required piece is missing, and records a fingerprint so changes can be traced.

The skills runtime loads reusable abilities from skill folders. It understands what each skill needs, orders dependent skills correctly, and copies needed files into the sandbox workspace, so the agent has the right instructions and resources before it starts acting.

## Files in this stage

### Transcript compaction
Prepares long conversation history for the model by preserving recent turns and summarizing older context.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling, before sending a model request when the transcript is large`

AI models can only read a certain amount of text at once. When a conversation grows past that limit, simply sending the whole history would fail. This file solves that by doing transcript compaction: like packing old papers into a labeled archive box while leaving today’s paperwork on the desk.

The main `Compaction` workflow first estimates how large the current message window is. If it is still small enough, nothing happens. If it is too large, the file splits the conversation into safe chunks called rounds, so a tool call is not separated from the tool result that answers it. The older rounds become the “head” to summarize, and the newest rounds become the “tail” to keep word-for-word.

The head is rendered into plain text for a summarizer model. Images become markers, hidden reasoning is represented safely, and long repeated text is folded down so wasteful loops do not block compaction. The summarizer must return a `CompactionSummary`, a structured JSON object. The code validates that object before using it.

After that, the file builds one replacement message containing the summary, important file references, loaded skills, and active requests. It writes compressed records of the full “before” window, the “after” window, and the structured summary into blob storage. This matters because the live conversation stays usable, while evaluators and debugging tools can still inspect exactly what was compacted.

#### Function details

##### `is_context_overflow`  (lines 83–89)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: Detects whether an error probably means the AI provider rejected a request because the input was too large. This lets the system shrink and retry instead of treating every such failure as fatal.

**Data flow**: It receives an exception → combines the exception’s type name and message into lowercase text → checks for phrases such as “context length” or “prompt is too large” → returns true if one is found, otherwise false.

**Call relations**: During summarization, `Compaction._summarize` calls this after a failed summarizer request. If it says the failure was a size problem, the workflow drops some old rounds and tries again.

*Call graph*: called by 1 (_summarize).


##### `Compaction.maybe_compact`  (lines 120–140)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False, active_requests: tuple[str, ...]=()) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Decides whether the current conversation needs to be compacted. It is the safe public doorway: it can skip compaction, run it automatically when the transcript is too large, or force it after a provider overflow.

**Data flow**: It receives the current messages, a force flag, and active requests → checks whether there are enough messages to compact → estimates token size with `_tokens` → compares that estimate with the trigger limit unless forced → either returns the original messages and no usage, or returns the compacted messages and model-usage records from `_compact`.

**Call relations**: The rest of the conversation loop calls this before sending history to the model. When compaction is needed, it hands control to `Compaction._compact`; otherwise it keeps the transcript unchanged.

*Call graph*: calls 2 internal fn (_compact, _tokens).


##### `Compaction._compact`  (lines 143–186)

```
async def _compact(self, messages: tuple[Message, ...], reason: Literal['auto', 'force'], active_requests: tuple[str, ...]) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: Runs the full compaction pipeline once the decision to compact has been made. It chooses what to summarize, calls the summarizer, builds the replacement message, saves records, and fires observation hooks.

**Data flow**: It receives the full message window, the reason for compaction, and active requests → splits old and recent messages with `_select` → counts tokens before compaction → fires a pre-compaction hook → chooses the next storage index → summarizes the old head → adds locally known loaded skills → finds durable tool-output references → renders the new compacted context message → saves before/after/summary records → fires a post-compaction hook → returns the new message window and usage records.

**Call relations**: `Compaction.maybe_compact` calls this when compaction should happen. It coordinates the helper functions in this file and hands the final compacted window back to the caller.

*Call graph*: calls 7 internal fn (_next_index, _persist, _references, _render, _select, _summarize, _tokens); called by 1 (maybe_compact); 3 external calls (__init__, __init__, __init__).


##### `Compaction._select`  (lines 188–204)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: Chooses which part of the conversation will be summarized and which recent part will stay untouched. It protects recent context and avoids cutting apart related tool calls and results.

**Data flow**: It receives all messages → groups them into rounds with `_rounds` → keeps whole trailing rounds until at least the configured number of recent messages is preserved → returns older rounds as the head and recent messages as the tail, or returns nothing if there is no older head to summarize.

**Call relations**: `Compaction._compact` calls this at the start. If `_select` cannot find a safe older section, compaction becomes a no-op even if it was forced.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 206–220)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Groups messages into conversation rounds so connected messages stay together. In particular, it keeps an assistant tool request with the following user/tool-result messages that answer it.

**Data flow**: It receives messages in order → starts a new round whenever an assistant message appears after existing content → collects messages until the next assistant boundary → returns a tuple of message groups.

**Call relations**: `Compaction._select` relies on this grouping before deciding what to keep and what to summarize. This prevents the compactor from leaving half of a tool exchange on one side of the boundary.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 222–244)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: Turns the old head of the conversation into a structured summary, with retry logic if the summary request itself is too large. It makes compaction resilient without silently producing a bad summary.

**Data flow**: It receives grouped old rounds → tries `_summarize_once` → if the model rejects the request for being too large, confirmed by `is_context_overflow`, it removes the oldest slice with `_drop_oldest` and retries → returns a valid `CompactionSummary` and the usage record from the successful model call, or raises an error if it cannot recover.

**Call relations**: `Compaction._compact` calls this after choosing the head. It delegates the actual model call to `_summarize_once` and uses `_drop_oldest` only for prompt-too-long recovery.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 246–266)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: Makes one actual summarizer model request. It asks the model to summarize the old transcript head into the required structured JSON form.

**Data flow**: It receives the old rounds → prepares them as text with `_prepare` → builds a `ModelRequest` using the compaction system prompt → streams text chunks and usage information from the model client → joins the chunks → parses and validates the result with `_parse_summary` → returns the summary and usage.

**Call relations**: `Compaction._summarize` calls this for each attempt. If this fails because the prompt is too large, `_summarize` decides whether to shrink and retry.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 268–287)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...]) -> str
```

**Purpose**: Converts the old conversation rounds into the text sent to the summarizer. It preserves useful meaning while reducing wasteful bulk and reminding the model to output only JSON.

**Data flow**: It receives grouped messages → renders each message as `role: content` using `_text` → joins the rendered messages → folds long repeated runs with `_fold_repeated_runs` → appends a final instruction restating the required JSON-only format → returns the prompt text.

**Call relations**: `Compaction._summarize_once` uses this just before creating the model request. It depends on `_text` for message rendering and `_fold_repeated_runs` for compacting repeated spam-like text.

*Call graph*: calls 2 internal fn (_fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 289–302)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: Replaces long verbatim repetition with one copy plus a count marker. This keeps runaway loops or pasted repeated text from making the summarizer prompt too large.

**Data flow**: It receives rendered transcript text → searches for short word sequences repeated many times in a row → replaces each repeated stretch with the sequence once followed by a marker like `[repeated 20 times]` → returns the shorter text.

**Call relations**: `Compaction._prepare` calls this after rendering the transcript. Its inner replacement function, `Compaction._fold_repeated_runs.fold`, computes the exact replacement for each repeated match.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 297–300)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: Builds the replacement text for one repeated run found by `_fold_repeated_runs`. It keeps one copy of the repeated phrase and records how many times it appeared.

**Data flow**: It receives a regex match containing repeated text → extracts the repeated unit → estimates the repetition count from the matched length → returns the unit followed by a repeated-count marker.

**Call relations**: This is used internally by `Compaction._fold_repeated_runs` as the replacement callback during regex substitution. No other part of the file calls it directly.


##### `Compaction._drop_oldest`  (lines 304–309)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: Shrinks the summarizer input after a prompt-too-long failure. It removes the oldest part of the head so the next summarizer attempt has less to read.

**Data flow**: It receives the head rounds → calculates one fifth of the rounds, with a minimum of one → drops that many rounds from the front → returns the remaining newer rounds.

**Call relations**: `Compaction._summarize` calls this only after `is_context_overflow` confirms that the previous summarizer attempt was too large.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 311–352)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: Extracts and validates the JSON summary produced by the model. It refuses to use empty, malformed, or schema-invalid summaries, because replacing history with a bad summary would be dangerous.

**Data flow**: It receives raw model text → finds the first balanced JSON object inside it → asks `CompactionSummary` to validate that JSON against the expected shape → checks that the main intent is not blank → returns the typed summary, or raises a clear runtime error.

**Call relations**: `Compaction._summarize_once` calls this after collecting streamed model text. The parsed summary then flows into `_compact`, where it is rendered and persisted.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 354–371)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: Finds durable tool-output file paths from the summarized old head that should still be visible after compaction. This lets the model re-read large offloaded outputs later without copying their full contents into the summary.

**Data flow**: It receives old head rounds and the kept tail → renders messages with `_text` and scans for `.tool-output/...txt` paths → ignores paths already visible in the tail → keeps unique paths from the head → returns only the most recent limited set.

**Call relations**: `Compaction._compact` calls this after summarization. The returned paths are passed to `_render`, which includes them in the compacted context message.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 373–413)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...], active_requests: tuple[str, ...]) -> str
```

**Purpose**: Turns the validated structured summary into the single user message that replaces the old transcript head. It creates a predictable, readable compacted context block for the next model call.

**Data flow**: It receives a `CompactionSummary`, durable references, and active requests → creates labeled sections only for fields that have content → formats list-like sections with `_bullets` → appends exact active requests outside the model-written summary → returns one string beginning with the compacted-context prefix.

**Call relations**: `Compaction._compact` calls this after collecting the summary and references. Its output becomes the content of the new replacement `Message` and is also reported to the post-compaction hook.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_compact).


##### `Compaction._bullets`  (lines 415–416)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: Formats a list of strings as simple bullet points. It keeps rendered summary sections easy for both humans and models to scan.

**Data flow**: It receives a tuple of strings → prefixes each item with `- ` → joins them with newlines → returns the bullet-list text.

**Call relations**: `Compaction._render` calls this whenever a summary section contains multiple items, such as concepts, errors, pending tasks, loaded skills, or durable references.

*Call graph*: called by 1 (_render).


##### `Compaction._persist`  (lines 418–429)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: Saves the compaction record so the system can later inspect exactly what changed. It stores the full before window, the new after window, and the structured summary.

**Data flow**: It receives a compaction index, before messages, after messages, and the summary → writes before and after message windows through `_write` → serializes and compresses the summary → stores it in the blob store under a key from `_key`.

**Call relations**: `Compaction._compact` calls this near the end of a successful compaction. It delegates message-window writing to `_write` and uses `_key` to choose stable storage paths.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 431–435)

```
async def _next_index(self) -> int
```

**Purpose**: Finds the next unused compaction number for this conversation. This keeps multiple compaction records ordered and avoids overwriting earlier ones.

**Data flow**: It starts at index 1 → checks blob storage for an existing `after` record at that index using `_key` → increments until it finds a free slot → returns that index.

**Call relations**: `Compaction._compact` calls this before saving records. The returned number is then passed into `_persist`.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 437–444)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: Reads a saved compaction record back from storage. This is useful for debugging, evaluation, or any tool that wants to inspect what was compacted.

**Data flow**: It receives a compaction index → tries to fetch the compressed before, after, and summary blobs using `_key` → returns `None` if any blob is missing → otherwise decodes them into a `CompactionRecord`.

**Call relations**: This is a read-side helper rather than part of the live compaction path. It uses the same key scheme as `_persist`, `_write`, and `_next_index`, then hands the raw blobs to `decode_compaction`.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 446–454)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: Writes either the before or after message window to compressed blob storage. It stores messages in a stable JSON form so the record can be reconstructed later.

**Data flow**: It receives an index, a label of `before` or `after`, and messages → wraps the messages in a `CompactionWindow` → dumps them to sorted compact JSON → compresses the bytes with LZ4 → stores them using a key from `_key`.

**Call relations**: `Compaction._persist` calls this twice, once for the original window and once for the replacement window.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 456–457)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: Builds the storage key for one part of a compaction record. It centralizes the naming scheme so all reads and writes look in the same place.

**Data flow**: It receives an index and a part name such as `before`, `after`, or `summary` → combines them with the conversation ID through `compaction_key` → returns the blob-storage key string.

**Call relations**: `Compaction._persist`, `_write`, `_next_index`, and `read_record` all use this helper. That shared path builder keeps the save and load sides aligned.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 459–482)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: Estimates how many model tokens the current message window will cost. A token is a small chunk of text used by model providers for input limits and billing; this estimate decides when to compact.

**Data flow**: It receives messages → for each message, counts role text, rendered text from `_text`, hidden reasoning size from `_opaque_chars`, and image cost from `_image_count` → converts characters to estimated tokens and adds image estimates → returns the total estimated token count.

**Call relations**: `Compaction.maybe_compact` uses this to decide whether the trigger has been crossed. `Compaction._compact` uses it again to report before and after sizes to hooks.

*Call graph*: calls 3 internal fn (_image_count, _opaque_chars, _text); called by 2 (_compact, maybe_compact).


##### `Compaction._opaque_chars`  (lines 484–503)

```
def _opaque_chars(self, message: Message) -> int
```

**Purpose**: Counts hidden reasoning data that still costs input space even though it is not useful text for the summary. This avoids underestimating conversations that contain encrypted or redacted reasoning blocks.

**Data flow**: It receives one message → if the content is plain text, returns zero → otherwise scans structured blocks for thinking signatures, redacted data, and encrypted reasoning content → adds their lengths → returns that hidden-character count.

**Call relations**: `Compaction._tokens` calls this while estimating the window size. The result is counted but is not rendered by `_text` for summarization.

*Call graph*: called by 1 (_tokens).


##### `Compaction._image_count`  (lines 505–515)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: Counts images inside a message so image-heavy conversations can still trigger compaction. Images have little or no text, but they consume model context space.

**Data flow**: It receives one message → returns zero for plain text → otherwise scans top-level image blocks and images inside tool-result blocks → returns the total number of images found.

**Call relations**: `Compaction._tokens` calls this and multiplies the count by a fixed image-token estimate. This prevents image-heavy transcripts from looking artificially tiny.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 517–541)

```
def _text(self, message: Message) -> str
```

**Purpose**: Renders a message’s content into plain text for token estimation, summarizer input, and reference scanning. It preserves useful meaning while replacing non-text items with clear markers.

**Data flow**: It receives one message → if the content is already a string, returns it → otherwise walks each structured block → extracts text, thinking summaries, tool results, and tool-use names plus JSON arguments → replaces images with `[image]` and redacted reasoning with a marker → joins all pieces with newlines and returns the text.

**Call relations**: `Compaction._prepare` uses this to build the summarizer prompt, `_references` uses it to find durable tool-output paths, and `_tokens` uses it to estimate input size.

*Call graph*: called by 3 (_prepare, _references, _tokens); 1 external calls (dumps).


### Prompt rendering
Builds and verifies the final system prompt from templates before it is sent to the language model.

### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `prompt construction before a model turn`

A language model prompt here is assembled from several pieces, like filling in a form before mailing it. The core project provides template text with named holes such as the agent instructions, available skills, capability sections, citation rules, and the model's knowledge cutoff date. This file puts the right text into those holes and refuses to continue if any hole is left unfilled.

That strictness matters because an accidental leftover like `{{some_var}}` would otherwise be sent to the model as confusing text. The renderer checks both sides: if the agent prompt declares a variable, the caller must supply it; if the caller supplies an extra variable, that is also treated as a mistake. This makes prompt construction fail early and clearly instead of producing a subtly broken model instruction.

The file also formats the model knowledge cutoff from a machine-friendly date such as `2026-02` into a human phrase such as `February 2026`. It can add an `<available_skills>` block listing loadable skills, join contributed sections in a stable order, inject the shared citation block, clean up extra blank lines, and compute a SHA-256 digest, which is a content fingerprint used to identify exactly which prompt was sent.

#### Function details

##### `rendered_prompt`  (lines 49–50)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This wraps finished prompt text in a `RenderedPrompt` object and adds a SHA-256 digest, which is a stable fingerprint of the exact text. Someone would use it when they need both the prompt content and a way to identify whether that content changed.

**Data flow**: It receives the final prompt text as a string. It turns that text into bytes, computes a SHA-256 hash from it, prefixes the hash with `sha256:`, and returns a `RenderedPrompt` containing both the digest and the original content. It does not change any outside state.

**Call relations**: After `render_template` has filled and cleaned the prompt, it hands the finished text to `rendered_prompt`. This function is the final packaging step before the prompt can be sent onward and recorded for observability.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 53–70)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This builds the main agent system prompt from the standard shell template. It inserts the agent's instructions, contributed sections, available skills, citation guidance, and the model's knowledge cutoff.

**Data flow**: It receives an agent prompt, section pairs, optional skill pairs, and a required knowledge cutoff string in `YYYY-MM` form. It converts the cutoff into a human-readable month and year, puts that into the knowledge-cutoff block, inserts that block into the shell template, and then passes everything to `render_template`. The result is a fully rendered prompt with a digest.

**Call relations**: This is the higher-level entry into the renderer for ordinary system prompts. It prepares the model-specific knowledge cutoff, then delegates the detailed slot filling and validation to `render_template`.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 73–91)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This is the main template-filling routine. It replaces all known prompt slots, checks that no hidden placeholders remain, tidies blank lines, and returns a rendered prompt with a digest.

**Data flow**: It takes a template, an agent prompt, a mapping of variable names to values, a list of skills, and a list of sections. First it asks `_substitute_vars` to fill variables inside the agent prompt. If there is agent prompt text but the template has no agent-prompt slot, it raises an error. Then it inserts the skill index, citation block, joined sections, and filled agent prompt. If any `{{name}}`-style placeholder is still present afterward, it raises an error. Otherwise it collapses long blank-line runs, trims the end, and returns the packaged result from `rendered_prompt`.

**Call relations**: `render_system_prompt` calls this after preparing the shell template. Inside, it relies on `_substitute_vars` for safe variable replacement, `render_skill_index` to create the skills block, and `rendered_prompt` to produce the final object with its digest.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 94–103)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This turns a list of available skills into the small prompt block that tells the model what skills can be loaded. If there are no skills, it returns an empty string so the prompt does not contain an unnecessary block.

**Data flow**: It receives skill name and description pairs. With an empty list, it outputs an empty string. With skills present, it builds a text block wrapped in `<available_skills>` and `</available_skills>`, with one bullet line per skill. It does not modify the input list.

**Call relations**: `render_template` calls this when filling the skill-index slot in a prompt template. Its output becomes one piece of the final system prompt.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 106–113)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This safely fills `{{variable}}` placeholders inside an agent prompt. It is deliberately strict so missing or unexpected variables are caught as clear errors before the prompt reaches the model.

**Data flow**: It receives a template string and a mapping of variable names to replacement text. It scans the template to find all declared variable names, compares them with the supplied names, and raises an error if any declared name is missing or any supplied name was not declared. If the two sets match exactly, it replaces each placeholder with its supplied value and returns the filled string.

**Call relations**: `render_template` calls this before inserting the agent prompt into the larger template. This protects the rest of the rendering flow from carrying unresolved or accidental prompt variables forward.

*Call graph*: called by 1 (render_template).


### Skill runtime
Loads reusable skills, resolves their dependencies, and materializes their files into the sandbox workspace.

### `core/src/ufo/skills/runtime.py`

`domain_logic` · `startup and skill loading during conversation`

A skill is a folder that teaches the agent a reusable workflow. Each skill has a SKILL.md file with a small YAML frontmatter section, which is structured metadata, followed by markdown instructions for the agent. The skill folder can also contain asset files, such as examples or scripts, that are mounted into the sandbox so the agent can read or run them by path.

This file solves several practical problems. It checks that a skill folder is valid. It separates user-facing instructions from load-time metadata such as the skill name, description, and dependencies. It discovers child skills in nested folders, but treats nesting mostly as naming: a child is not automatically loaded with its parent unless it declares a dependency.

At startup, core skills are loaded from disk into a registry. Later, when the agent asks to load a skill, the registry expands that request to include all declared dependencies, while avoiding duplicates and dependency loops. The file then creates the text that will be shown to the model: new skill instructions are included, already-seen instructions are replaced by a short note, and a compact tree lists the mounted files.

Finally, the file writes each skill’s SKILL.md and bundled files into the sandbox under `.skills/<skill-name>/`. This keeps reusable project material available inside the safe workspace rather than exposing framework internals.

#### Function details

##### `RuntimeSkill.mounted_files`  (lines 59–60)

```
def mounted_files(self) -> dict[str, bytes]
```

**Purpose**: Builds the set of files that should appear in the sandbox for one skill. It includes the original SKILL.md exactly as written, plus any bundled asset files.

**Data flow**: It starts with a parsed RuntimeSkill, reading its saved raw SKILL.md text and its asset file list. It turns the markdown text into bytes and combines it with the existing asset bytes. The result is a dictionary from relative file paths to file contents, ready to be written into the sandbox.

**Call relations**: When a skill is being mounted, mount_skill asks RuntimeSkill.mounted_files for the exact files to copy. This keeps the write step simple: it receives a ready-made list of paths and bytes.

*Call graph*: called by 1 (mount_skill).


##### `RuntimeSkill.mount_root`  (lines 62–63)

```
def mount_root(self) -> str
```

**Purpose**: Returns the sandbox folder where this skill should be placed. It gives every skill its own directory under the shared `.skills` area.

**Data flow**: It reads the skill’s registry name and joins it with the fixed skills mount directory. The output is a string path such as the workspace’s `.skills/<name>` location.

**Call relations**: mount_skill uses this path before writing the skill’s files. That means the mounting code does not have to know the naming rules; the skill object can say where it belongs.

*Call graph*: called by 1 (mount_skill).


##### `LoadedSkill.prompt_body`  (lines 75–85)

```
def prompt_body(self) -> str
```

**Purpose**: Creates the piece of prompt text contributed by one loaded skill. It labels whether the skill was directly requested or was pulled in as a dependency, then includes the skill’s workflow instructions.

**Data flow**: It reads the LoadedSkill’s RuntimeSkill and the optional dependency_of name. If there is no dependency_of value, it writes a normal skill header; otherwise it adds a note saying which skill pulled this one in. It returns a single text block containing the header and the markdown instructions.

**Call relations**: This is the small formatter used when building the context shown to the model. It keeps dependency wording consistent so the agent can tell the difference between a skill it chose and one that came along because another skill needed it.


##### `LoadedSkills.reseed`  (lines 101–118)

```
def reseed(self, loads: Iterable[tuple[LoadedSkill, ...]], preloaded: tuple[LoadedSkill, ...]=()) -> None
```

**Purpose**: Rebuilds the memory of which skill instructions are already present in the model’s context. This prevents the system from paying for the same instructions repeatedly in later turns.

**Data flow**: It receives earlier skill-load results and optional preloaded skills. First it clears the current tracker. Then it records every skill whose workflow was placed in context, and separately records the skills the agent directly asked for. Preloaded skills are marked as already in context but not as agent-requested.

**Call relations**: LoadedSkills.reseed calls LoadedSkills.reset to start from a clean slate. It is used when the system reconstructs context state from previous loads, so later skill loading can suppress repeated instructions while still knowing which skills should be carried forward across context boundaries.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.drain`  (lines 120–125)

```
def drain(self) -> tuple[str, ...]
```

**Purpose**: Returns the names of skills the agent explicitly asked for, then clears the tracker. This is useful when a boundary drops detailed workflow text but needs to remember what should be reloaded later.

**Data flow**: It reads the asked_for set, sorts it into a stable tuple of names, and then resets both tracking sets. The output is the saved list of requested skill names, while the LoadedSkills object is left empty.

**Call relations**: LoadedSkills.drain calls LoadedSkills.reset after taking its snapshot. It is the counterpart to reseed: drain extracts the compact memory needed across a boundary, while reseed later rebuilds the live tracking state.

*Call graph*: calls 1 internal fn (reset).


##### `LoadedSkills.reset`  (lines 127–129)

```
def reset(self) -> None
```

**Purpose**: Clears all remembered skill context state. It removes both the list of skills currently believed to be in context and the list the agent directly requested.

**Data flow**: It takes the existing LoadedSkills object and empties its two internal sets. It returns nothing; the change is made to the object itself.

**Call relations**: LoadedSkills.reseed uses reset before rebuilding state from known loads. LoadedSkills.drain uses it after exporting the requested names, so old context does not leak into a new tracking period.

*Call graph*: called by 2 (drain, reseed).


##### `_split_frontmatter`  (lines 132–138)

```
def _split_frontmatter(text: str) -> tuple[str, str]
```

**Purpose**: Splits a SKILL.md file into its metadata section and its instruction body. It also enforces that the file begins and ends its frontmatter block correctly.

**Data flow**: It receives the full SKILL.md text. It checks for the opening `---` marker, searches for the closing marker, and separates the text into metadata and body. If the markers are missing or malformed, it raises an error; otherwise it returns the two pieces.

**Call relations**: parse_skill_content calls this before reading the YAML metadata. This makes malformed skill files fail early, before the system tries to treat them as usable instructions.

*Call graph*: called by 1 (parse_skill_content).


##### `_child_skill_dirs`  (lines 141–146)

```
def _child_skill_dirs(skill_dir: Path) -> list[Path]
```

**Purpose**: Finds immediate child folders that are themselves skills. A child skill is recognized by having its own SKILL.md file.

**Data flow**: It receives a directory path and looks at its direct children. It keeps only child directories that contain a SKILL.md file, sorts them, and returns the list of paths.

**Call relations**: parse_skill uses this to avoid mixing child skill files into the parent’s asset bundle. discover_skills uses it to recursively register nested child skills.

*Call graph*: called by 2 (discover_skills, parse_skill); 1 external calls (iterdir).


##### `parse_skill_content`  (lines 149–182)

```
def parse_skill_content(dir_name: str, files: Mapping[str, bytes], registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Turns an in-memory set of files into a RuntimeSkill object. This lets the system validate and load skills whether they came from disk or were saved as bytes elsewhere.

**Data flow**: It receives the claimed directory name, a mapping of file paths to bytes, and optional registry naming information. It requires SKILL.md, decodes it, splits metadata from body, reads the YAML metadata safely, checks that the frontmatter name matches the folder name, collects dependencies, and keeps all non-SKILL.md files as assets. The result is a RuntimeSkill containing the skill’s name, description, instructions, dependencies, parent, asset files, and raw SKILL.md.

**Call relations**: parse_skill calls parse_skill_content after reading files from disk. Inside, parse_skill_content relies on _split_frontmatter for the markdown structure and yaml.safe_load for the metadata, then creates the RuntimeSkill used by registries and loaders.

*Call graph*: calls 1 internal fn (_split_frontmatter); called by 1 (parse_skill); 3 external calls (__init__, PurePosixPath, safe_load).


##### `parse_skill`  (lines 185–194)

```
def parse_skill(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> RuntimeSkill
```

**Purpose**: Reads one skill directory from disk and parses it into a RuntimeSkill. It treats child skill folders as separate skills rather than as ordinary bundled files.

**Data flow**: It receives a filesystem path. It first finds immediate child skill directories, then walks the directory tree and reads all files except files inside those child skill folders. It passes the collected bytes to parse_skill_content. The output is one RuntimeSkill for the parent directory.

**Call relations**: discover_skills calls parse_skill for each directory it registers. parse_skill depends on _child_skill_dirs to separate parent assets from child skills, and on parse_skill_content to perform the actual validation and object creation.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill_content); called by 1 (discover_skills); 1 external calls (rglob).


##### `discover_skills`  (lines 197–215)

```
def discover_skills(skill_dir: Path, registry_name: str | None=None, parent: str | None=None) -> dict[str, RuntimeSkill]
```

**Purpose**: Discovers a skill and all of its nested child skills, returning them in one flat lookup map. This turns a folder tree into registry entries that can be loaded by name.

**Data flow**: It receives a skill directory and optional registry name and parent name. It parses the current directory into a RuntimeSkill, puts it into a dictionary under its registry name, then finds child skill directories and recursively discovers each one using a path-style name such as `parent/child`. The result is a dictionary from skill names to RuntimeSkill objects.

**Call relations**: _load_core_skills uses discover_skills while building the core skill set. discover_skills calls parse_skill for the current folder and _child_skill_dirs to find the child folders it should recurse into.

*Call graph*: calls 2 internal fn (_child_skill_dirs, parse_skill); called by 1 (_load_core_skills).


##### `_load_core_skills`  (lines 218–225)

```
def _load_core_skills(root: Path) -> dict[str, RuntimeSkill]
```

**Purpose**: Loads the built-in core skills that ship with the project. These are the skills the system expects to have available before packs or user skills are added.

**Data flow**: It receives the root directory containing core skill folders. It lists visible subdirectories, skips hidden or underscore-prefixed folders, discovers skills in each directory, and merges all discovered entries into one dictionary. The output maps core skill names to RuntimeSkill objects.

**Call relations**: This function is run when the module is imported to create the core skill collection. It calls discover_skills for each core skill directory, so nested built-in child skills are included too.

*Call graph*: calls 1 internal fn (discover_skills); 1 external calls (iterdir).


##### `SkillRegistry.named`  (lines 242–247)

```
def named(self, name: str) -> RuntimeSkill
```

**Purpose**: Looks up one skill by name and gives a clear error if it does not exist. This prevents later loading steps from failing with a vague missing-key error.

**Data flow**: It receives a skill name and checks the registry’s by_name dictionary. If the name is present, it returns the RuntimeSkill. If not, it builds a readable list of available skills and raises a ValueError explaining what went wrong.

**Call relations**: SkillRegistry.closure and its helper SkillRegistry.closure.add call named whenever they need to resolve a requested skill or dependency. That keeps all unknown-skill errors consistent.

*Call graph*: called by 2 (closure, add).


##### `SkillRegistry.closure`  (lines 249–272)

```
def closure(self, *names: str) -> tuple[LoadedSkill, ...]
```

**Purpose**: Expands requested skill names into the full set of skills that must be loaded, including dependencies. It avoids duplicates and stays safe even if skills accidentally depend on each other in a loop.

**Data flow**: It receives one or more requested names. It first records each requested skill as directly loaded, preserving the request order and removing duplicate requests. Then it walks each requested skill’s dependency list, adding dependency skills only once and remembering which skill pulled them in. It returns an ordered tuple of LoadedSkill entries.

**Call relations**: The conversation engine calls SkillRegistry.closure when it needs the complete skill load for a request. closure uses SkillRegistry.named to resolve names and creates LoadedSkill objects that later explain whether each workflow was requested directly or arrived as a dependency.

*Call graph*: calls 1 internal fn (named); called by 1 (_loaded_skill_closures); 1 external calls (__init__).


##### `SkillRegistry.closure.add`  (lines 262–267)

```
def add(skill: RuntimeSkill, dependency_of: str | None) -> None
```

**Purpose**: Adds one dependency skill and recursively adds the dependencies that skill requires. It is the private walking step inside SkillRegistry.closure.

**Data flow**: It receives a RuntimeSkill and the name of the skill that caused it to be loaded. If the skill is already in the loaded dictionary, it stops. Otherwise it records a LoadedSkill marked as a dependency, then looks up and adds each dependency declared by that skill. It changes the surrounding closure’s loaded dictionary and returns nothing.

**Call relations**: SkillRegistry.closure calls this helper while expanding dependencies. The helper calls SkillRegistry.named for each dependency name and creates LoadedSkill entries, allowing the outer closure to return one complete ordered load plan.

*Call graph*: calls 1 internal fn (named); 1 external calls (__init__).


##### `SkillRegistry.index`  (lines 274–282)

```
def index(self) -> tuple[tuple[str, str], ...]
```

**Purpose**: Builds the short catalog of loadable top-level skills shown to the agent. It includes names and descriptions, but leaves child skills out of this public index.

**Data flow**: It reads the registry’s skills in registration order. For each skill with no parent, it takes the name and description. It returns a tuple of these pairs, suitable for rendering into a prompt or catalog.

**Call relations**: This function supports the prompt’s skill index. It uses the registry’s already-built contents and filters out child skills because children are meant to be found through their parent skill’s instructions rather than listed alongside top-level choices.


##### `SkillRegistry.merged_with`  (lines 284–296)

```
def merged_with(self, user_skills: tuple[RuntimeSkill, ...]) -> 'SkillRegistry'
```

**Purpose**: Creates a new registry that adds saved user skills after the base skills. It refuses user skills that try to use the same name as an existing core or pack skill.

**Data flow**: It starts by copying the current registry’s name-to-skill map. It then examines each user skill. If the name is new, it appends the skill; if the name already exists, it logs a refusal and leaves the base skill unchanged. The output is a new SkillRegistry containing the safe merged set.

**Call relations**: This function is used when a workspace’s saved user skills need to be added to the normal skill registry. It calls the logging system when a user skill would shadow an existing skill, and then constructs a fresh SkillRegistry for later lookup and closure expansion.

*Call graph*: 2 external calls (__init__, log).


##### `_mounted_tree`  (lines 302–320)

```
def _mounted_tree(loaded: tuple[LoadedSkill, ...]) -> str
```

**Purpose**: Creates a compact text tree showing every file mounted for a skill load. This gives the agent a readable map of where files are, without repeating long path prefixes over and over.

**Data flow**: It receives the LoadedSkill entries for one load. For every skill, it asks which files would be mounted, combines the skill name and file path, sorts all paths, and turns them into an indented directory tree under the `.skills` mount directory. The output is a string listing directories and files.

**Call relations**: loaded_context calls _mounted_tree after building the instruction blocks. The tree is the final map the model sees, so it can refer to mounted assets by path.

*Call graph*: called by 1 (loaded_context); 1 external calls (PurePosixPath).


##### `loaded_context`  (lines 323–337)

```
def loaded_context(loaded: tuple[LoadedSkill, ...], in_context: Container[str]=frozenset()) -> str
```

**Purpose**: Builds the complete text shown to the model for one skill load. It includes new workflow instructions, notes for workflows already in context, and a file tree for everything mounted.

**Data flow**: It receives the closure of LoadedSkill entries and a collection of skill names already in the model’s context. For each not-yet-seen skill, it includes that skill’s prompt body. For repeated skills, it adds one concise note instead of repeating the full instructions. It then appends the mounted file tree for all loaded skills, including repeated ones. The result is the full prompt text for this load.

**Call relations**: This function sits between dependency resolution and the model prompt. It calls _mounted_tree so the text explains both the workflows and the sandbox files made available by the same load.

*Call graph*: calls 1 internal fn (_mounted_tree).


##### `mount_skill`  (lines 340–343)

```
async def mount_skill(sandbox: SandboxSession, skill: RuntimeSkill) -> None
```

**Purpose**: Copies one skill’s SKILL.md and asset files into the sandbox workspace. This makes the skill’s files available to the agent at stable paths under `.skills`.

**Data flow**: It receives a SandboxSession and a RuntimeSkill. It asks the skill for its mount root, asks for the files to mount, then writes each file’s bytes into the sandbox at the matching path. It returns nothing, but the sandbox filesystem is changed.

**Call relations**: When a skill load is applied, mount_skill performs the actual sandbox write. It uses RuntimeSkill.mount_root for the destination folder, RuntimeSkill.mounted_files for the file contents, and SandboxSession.write_file to do the safe workspace write.

*Call graph*: calls 3 internal fn (write_file, mount_root, mounted_files).

## 📊 State Registers Touched

- `reg-extension-catalog` — The loaded list of extensions and packs that tells the system which extra tools, routes, jobs, skills, and backends exist.
- `reg-agent-profile` — The saved assistant setup for each workspace, including model choice, audience, internet access, skills, and control settings.
- `reg-conversation-state` — The durable record of each conversation, including its workspace, surface, audience, agent, sandbox link, and object identity.
- `reg-transcript-state` — The shared conversation notebook containing saved messages, model events, summaries, and compaction records.
- `reg-tool-catalog` — The shared catalog of tool names, descriptions, schemas, and implementations that the model is allowed to call.
- `reg-tool-execution-context` — The per-turn safety envelope that tells tools which files, credentials, browser sessions, memory, accounts, and subagents they may use.
- `reg-model-provider-catalog` — The shared list of AI models and providers, including how to call them, what keys they need, and what features they support.
- `reg-memory-search-index` — The long-term memory and searchable text index that stores remembered facts, chunks, embeddings, and recall results.
- `reg-sandbox-workspace-state` — The remembered sandbox workspace for a conversation, including its backend handle, files, runtime folder, and cleanup ownership.
- `reg-skill-inventory` — The declared and user-created skill inventory, including skill ownership, dependencies, files, and the load order copied into a sandbox.
- `reg-prompt-governance-state` — The prompt templates, rendered fingerprints, proposals, evaluations, approvals, and replacement decisions used to change agent behavior safely.
- `reg-todo-checklist-state` — The durable visible todo/checklist state that agents update during long-running work and reuse across turns.
- `reg-turn-execution-budget-state` — The per-turn live execution limits and counters for context size, tokens, reasoning, tool iterations, cost checks, and stop conditions that gate the model loop before final ledger recording.
