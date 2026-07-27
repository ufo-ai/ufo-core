# Per-turn setup: context window, prompts, skills, sandbox lease, and tool catalog  `stage-8`

This stage runs near the start of every claimed turn, before the model is asked what to do. It gathers the material the model needs, trims it to fit, and prepares the safe working area and tool list.

The conversation can be longer than the model’s memory window, so compaction.py acts like an editor: it keeps the newest messages as they are and replaces older parts with a structured summary. render.py then builds the final system prompt, which is the instruction sheet sent to the model. It combines the base rules, enabled skills, capability notes, citation rules, and the model’s knowledge cutoff, and adds a fingerprint so the exact prompt can be identified later. The prompts package file simply makes that prompt code importable.

registry.py is the tool catalog. It describes each tool, checks that tool names do not collide, and provides the input shape the model must use. fs_mount.py prepares the sandbox workspace by creating commands that connect `/workspace` to S3-backed storage, place credentials, and check that the mount is healthy. Together, these parts give each turn its context, instructions, tools, and safe workspace.

## Files in this stage

### Context and prompt assembly
Prepares the conversation context and renders the final system prompt that will be sent to the model.

### `core/src/ufo/loop/compaction.py`

`domain_logic` · `request handling, when a conversation window is checked before sending to the model`

A language model can only read a limited amount of text at once. This file solves the problem of a conversation becoming too large to send back to the model. Instead of simply deleting old messages, it carefully compresses them: it keeps recent conversation turns exactly as they were, asks the model to summarize the older part into a checked JSON structure, and stores both the original and compacted versions for later review.

The main class, Compaction, acts like a librarian making room on a crowded desk. The newest papers stay on the desk. Older papers are summarized onto a cover sheet, but the originals are archived so nothing is truly lost. The file also preserves references to large tool-output files, so the model can re-read those files if needed instead of stuffing their full contents back into the prompt.

The workflow is cautious. It only compacts when the estimated token count is too high, or when forced after a provider says the prompt is too large. It keeps tool calls and tool results together, because separating a question from its answer would confuse the model. If the summary request itself is too large, it drops some of the oldest material and retries. Finally, it writes compressed records to blob storage: the transcript before compaction, the transcript after compaction, and the structured summary.

#### Function details

##### `is_context_overflow`  (lines 78–84)

```
def is_context_overflow(error: Exception) -> bool
```

**Purpose**: This function decides whether an error means the model provider rejected a request because the prompt was too large. It gives the rest of the system a shared way to recognize this recoverable situation.

**Data flow**: It receives an exception, combines the exception type and message into lowercase text, and searches for known phrases like “context length” or “prompt is too large.” It returns true if one of those phrases appears, otherwise false.

**Call relations**: During summarization, Compaction._summarize calls this after a failed model request. If the failure looks like a context overflow, the compaction flow can shrink the input and try again instead of stopping immediately.

*Call graph*: called by 1 (_summarize).


##### `Compaction.maybe_compact`  (lines 116–133)

```
async def maybe_compact(self, messages: tuple[Message, ...], force: bool=False) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: This is the public decision point for compaction. It checks whether the current conversation is too large, or whether compaction has been forced, and returns either the original messages or a compacted replacement.

**Data flow**: It receives the current messages and a force flag. It first refuses to compact very short histories, then estimates token usage with Compaction._tokens and compares that to the trigger limit. If compaction is needed, it passes the messages to Compaction._compact and returns the new message window plus any model-usage records.

**Call relations**: The turn flow calls this before sending a transcript to the model. It uses Compaction._tokens for the size estimate and hands off to Compaction._compact only when the window actually needs shrinking.

*Call graph*: calls 2 internal fn (_compact, _tokens).


##### `Compaction._compact`  (lines 136–178)

```
async def _compact(self, messages: tuple[Message, ...], reason: Literal['auto', 'force']) -> tuple[tuple[Message, ...], tuple[Usage, ...]]
```

**Purpose**: This runs the full compaction workflow once the decision to compact has been made. It chooses what to summarize, gets the summary, builds the replacement message, stores records, and fires observation hooks.

**Data flow**: It receives the full message window and the reason for compaction. It splits the window into an older head and recent tail, records the size before compaction, fires a pre-compaction hook, finds the next archive index, summarizes the head, adds drained loaded-skill information, gathers durable file references, renders one compacted-context message, stores before/after/summary records, fires a post-compaction hook, and returns the new window plus usage data.

**Call relations**: Compaction.maybe_compact calls this when compaction should happen. This method coordinates the helpers: Compaction._select chooses the split, Compaction._summarize calls the model, Compaction._references and Compaction._render build the replacement text, Compaction._persist saves the archive, and Compaction._tokens measures before and after sizes.

*Call graph*: calls 7 internal fn (_next_index, _persist, _references, _render, _select, _summarize, _tokens); called by 1 (maybe_compact); 3 external calls (__init__, __init__, __init__).


##### `Compaction._select`  (lines 180–196)

```
def _select(self, messages: tuple[Message, ...]) -> tuple[tuple[tuple[Message, ...], ...], tuple[Message, ...]] | None
```

**Purpose**: This chooses which messages become the summary and which recent messages stay untouched. It protects conversational structure by keeping whole model interaction rounds together.

**Data flow**: It receives all messages, groups them into rounds with Compaction._rounds, then keeps enough recent whole rounds to cover the configured minimum number of messages. If nothing is left to summarize, it returns nothing; otherwise it returns the head rounds and the flattened tail messages.

**Call relations**: Compaction._compact calls this at the start of the workflow. Its result determines what Compaction._summarize compresses and what remains visible verbatim in the final transcript.

*Call graph*: calls 1 internal fn (_rounds); called by 1 (_compact).


##### `Compaction._rounds`  (lines 198–212)

```
def _rounds(self, messages: tuple[Message, ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: This groups raw messages into conversation rounds so related messages are not split apart. In particular, it keeps an assistant tool request near the user/tool response that follows it.

**Data flow**: It receives a tuple of messages and walks through them in order. Each assistant message starts a new round if there is already a current group; all other messages are added to the current group. It returns a tuple of message groups.

**Call relations**: Compaction._select uses this before deciding where to cut the transcript. It provides the safe units that compaction can summarize or keep.

*Call graph*: called by 1 (_select).


##### `Compaction._summarize`  (lines 214–236)

```
async def _summarize(self, head_rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, tuple[Usage, ...]]
```

**Purpose**: This asks for a structured summary of the older transcript, with a safety retry if the summary prompt is itself too large. It prevents compaction from failing at the first oversized attempt.

**Data flow**: It receives the head rounds to summarize. It calls Compaction._summarize_once; if that fails with a recognized context-overflow error, it removes some of the oldest rounds with Compaction._drop_oldest and tries again until the retry limit is reached. On success, it returns the validated summary and the usage record from the model call.

**Call relations**: Compaction._compact calls this after choosing the head. This method relies on is_context_overflow to know which failures are worth retrying, uses Compaction._drop_oldest to shrink the input, and delegates the actual model request to Compaction._summarize_once.

*Call graph*: calls 3 internal fn (_drop_oldest, _summarize_once, is_context_overflow); called by 1 (_compact).


##### `Compaction._summarize_once`  (lines 238–258)

```
async def _summarize_once(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[CompactionSummary, Usage]
```

**Purpose**: This performs one actual model call to turn old transcript text into a CompactionSummary. It is the single place where the compaction pipeline asks the language model for new content.

**Data flow**: It receives prepared rounds, builds a ModelRequest with the compaction system prompt and rendered transcript text from Compaction._prepare, streams text chunks and usage information from the model client, then parses the combined text with Compaction._parse_summary. It returns the structured summary and usage count, or raises an error if usage is missing or parsing fails.

**Call relations**: Compaction._summarize calls this for each attempt. It hands the model's raw answer to Compaction._parse_summary so the rest of the pipeline only works with validated structured data.

*Call graph*: calls 2 internal fn (_parse_summary, _prepare); called by 1 (_summarize); 2 external calls (__init__, __init__).


##### `Compaction._prepare`  (lines 260–279)

```
def _prepare(self, rounds: tuple[tuple[Message, ...], ...]) -> str
```

**Purpose**: This turns the old transcript rounds into the text sent to the summarizing model. It also adds a final instruction reminding the model to return only the required JSON object.

**Data flow**: It receives grouped messages, converts each message into readable text with Compaction._text, labels it by role, joins all messages into one transcript-like block, folds long repeated runs with Compaction._fold_repeated_runs, and appends the format reminder. It returns one string for the summarizer prompt.

**Call relations**: Compaction._summarize_once calls this when building the ModelRequest. It depends on Compaction._text to render mixed message content and on Compaction._fold_repeated_runs to avoid wasting prompt space on repeated spam or loops.

*Call graph*: calls 2 internal fn (_fold_repeated_runs, _text); called by 1 (_summarize_once).


##### `Compaction._fold_repeated_runs`  (lines 281–294)

```
def _fold_repeated_runs(self, text: str) -> str
```

**Purpose**: This shrinks long repeated text sequences into one copy plus a count marker. It helps compaction succeed when a transcript contains huge repeated tool output, pasted text, or looped content.

**Data flow**: It receives a text string and applies a regular expression that finds short word sequences repeated many times in a row. Each matching run is replaced with the repeated unit followed by a marker such as “[repeated 12 times].” It returns the shorter text.

**Call relations**: Compaction._prepare calls this just before sending text to the summarizer. Its inner fold helper performs the replacement for each match found by the regular expression.

*Call graph*: called by 1 (_prepare).


##### `Compaction._fold_repeated_runs.fold`  (lines 289–292)

```
def fold(match: re.Match[str]) -> str
```

**Purpose**: This small inner helper builds the replacement text for one repeated run. It keeps one example of the repeated phrase and records how many times it appeared.

**Data flow**: It receives a regular-expression match, extracts the repeated unit, estimates the number of repetitions from the matched text length, and returns the unit followed by the repeated-count marker.

**Call relations**: It is used only inside Compaction._fold_repeated_runs as the callback for each repeated-text match. It supplies the exact replacement string that the outer function inserts.


##### `Compaction._drop_oldest`  (lines 296–301)

```
def _drop_oldest(self, rounds: tuple[tuple[Message, ...], ...]) -> tuple[tuple[Message, ...], ...]
```

**Purpose**: This removes part of the oldest material before retrying a summary request that was too large. It is a controlled way to make the next attempt smaller.

**Data flow**: It receives the head rounds and cuts off the oldest fifth, always dropping at least one round. It returns the remaining newer rounds.

**Call relations**: Compaction._summarize calls this only after a context-overflow failure from Compaction._summarize_once. The reduced rounds are then retried in another summarization attempt.

*Call graph*: called by 1 (_summarize).


##### `Compaction._parse_summary`  (lines 303–344)

```
def _parse_summary(self, text: str) -> CompactionSummary
```

**Purpose**: This turns the model's raw text reply into a valid CompactionSummary object. It protects the live transcript from being replaced by malformed or empty summary text.

**Data flow**: It receives raw text, finds the first balanced JSON object inside it, and asks the CompactionSummary schema to validate that JSON. If there is no JSON, the JSON is incomplete, the fields are invalid, or the main intent is empty, it raises a RuntimeError. Otherwise it returns the validated summary object.

**Call relations**: Compaction._summarize_once calls this after collecting the model's streamed text. It is the gatekeeper between uncertain model output and the deterministic rendering done later by Compaction._render.

*Call graph*: called by 1 (_summarize_once); 1 external calls (model_validate_json).


##### `Compaction._references`  (lines 346–363)

```
def _references(self, head_rounds: tuple[tuple[Message, ...], ...], tail: tuple[Message, ...]) -> tuple[str, ...]
```

**Purpose**: This finds durable tool-output file paths from the summarized part that should remain visible after compaction. It lets the model re-open important large outputs without re-inserting all their contents.

**Data flow**: It receives the head rounds and the kept tail. It first finds paths already visible in the tail, then scans text from the head for tool-output paths, skipping duplicates and paths still visible later. It returns only the most recent limited set of paths.

**Call relations**: Compaction._compact calls this after summarization. It uses Compaction._text to inspect messages and passes the resulting paths to Compaction._render so they appear in the compacted context message.

*Call graph*: calls 1 internal fn (_text); called by 1 (_compact).


##### `Compaction._render`  (lines 365–396)

```
def _render(self, summary: CompactionSummary, references: tuple[str, ...]) -> str
```

**Purpose**: This converts the validated structured summary into the single user message that replaces the old transcript head. It produces consistent, readable compacted context for the next model call.

**Data flow**: It receives a CompactionSummary and durable reference paths. It creates labeled sections for non-empty parts such as intent, concepts, files, errors, decisions, pending tasks, current work, next step, loaded skills, and references. It returns one text block beginning with the compacted-context prefix.

**Call relations**: Compaction._compact calls this after obtaining the summary and references. It uses Compaction._bullets for list-style sections, and its output becomes both the replacement message content and the summary text observed by the post-compaction hook.

*Call graph*: calls 1 internal fn (_bullets); called by 1 (_compact).


##### `Compaction._bullets`  (lines 398–399)

```
def _bullets(self, items: tuple[str, ...]) -> str
```

**Purpose**: This formats a list of strings as Markdown-style bullet points. It keeps rendered summary sections simple and consistent.

**Data flow**: It receives a tuple of strings and prefixes each item with “- ”, joining them with newlines. It returns the formatted string.

**Call relations**: Compaction._render calls this whenever a summary section is a list, such as concepts, pending tasks, loaded skills, or durable references.

*Call graph*: called by 1 (_render).


##### `Compaction._persist`  (lines 401–412)

```
async def _persist(self, index: int, before: tuple[Message, ...], after: tuple[Message, ...], summary: CompactionSummary) -> None
```

**Purpose**: This archives the compaction result so the system can inspect or replay what happened later. It stores the original window, the compacted window, and the structured summary.

**Data flow**: It receives an index, the before messages, the after messages, and the summary. It writes the before and after windows through Compaction._write, serializes the summary to JSON, compresses it with LZ4, and stores it in the blob store under a generated key.

**Call relations**: Compaction._compact calls this near the end of a successful compaction. It uses Compaction._key to name stored objects and Compaction._write for the two transcript windows.

*Call graph*: calls 2 internal fn (_key, _write); called by 1 (_compact); 1 external calls (model_dump_json).


##### `Compaction._next_index`  (lines 414–418)

```
async def _next_index(self) -> int
```

**Purpose**: This finds the next unused compaction number for a conversation. It prevents new archive files from overwriting earlier compactions.

**Data flow**: It starts at index 1 and checks blob storage for an existing “after” record at that index. It increments until it finds a missing slot, then returns that index.

**Call relations**: Compaction._compact calls this before persisting records. It uses Compaction._key to ask the blob store about each possible archive path.

*Call graph*: calls 1 internal fn (_key); called by 1 (_compact).


##### `Compaction.read_record`  (lines 420–427)

```
async def read_record(self, index: int) -> CompactionRecord | None
```

**Purpose**: This reads back a previously stored compaction archive. It is useful for debugging, evaluation, or any feature that needs to inspect what was compressed.

**Data flow**: It receives a compaction index and tries to load the stored before window, after window, and summary bytes from the blob store. If any part is missing, it returns None. If all parts are present, it decodes them into a CompactionRecord and returns it.

**Call relations**: This is an external read helper rather than part of the live compaction path. It uses Compaction._key to locate stored blobs and hands the raw data to decode_compaction to rebuild the record.

*Call graph*: calls 1 internal fn (_key); 1 external calls (decode_compaction).


##### `Compaction._write`  (lines 429–437)

```
async def _write(self, index: int, half: Literal['before', 'after'], messages: tuple[Message, ...]) -> None
```

**Purpose**: This stores one transcript window, either the pre-compaction version or the post-compaction version. It writes messages in a stable compressed JSON form.

**Data flow**: It receives an index, a label saying “before” or “after,” and the messages. It wraps the messages in a CompactionWindow, serializes them to sorted compact JSON, compresses the bytes with LZ4, and writes them to blob storage under the matching key.

**Call relations**: Compaction._persist calls this twice: once for the original messages and once for the compacted messages. It uses Compaction._key to choose the storage location.

*Call graph*: calls 1 internal fn (_key); called by 1 (_persist); 2 external calls (__init__, dumps).


##### `Compaction._key`  (lines 439–440)

```
def _key(self, index: int, half: Literal['before', 'after', 'summary']) -> str
```

**Purpose**: This builds the blob-storage key for one compaction artifact. It centralizes the naming rule so all reads and writes point to the same paths.

**Data flow**: It receives a compaction index and a part name: before, after, or summary. It combines those with the conversation ID through compaction_key and returns the resulting storage key string.

**Call relations**: Compaction._next_index, Compaction._persist, Compaction._write, and Compaction.read_record all call this whenever they need to check, store, or retrieve a compaction file.

*Call graph*: called by 4 (_next_index, _persist, _write, read_record); 1 external calls (compaction_key).


##### `Compaction._tokens`  (lines 442–458)

```
def _tokens(self, messages: tuple[Message, ...]) -> int
```

**Purpose**: This estimates how many model tokens the current message window will cost. It decides when automatic compaction should happen before the provider rejects an oversized prompt.

**Data flow**: It receives messages, converts each message to text with Compaction._text, counts text length using a measured characters-per-token estimate, adds a fixed estimate for each image counted by Compaction._image_count, and returns the total estimated token count.

**Call relations**: Compaction.maybe_compact calls this to decide whether the transcript crosses the trigger. Compaction._compact also calls it to report before-and-after sizes to hooks.

*Call graph*: calls 2 internal fn (_image_count, _text); called by 2 (_compact, maybe_compact).


##### `Compaction._image_count`  (lines 460–470)

```
def _image_count(self, message: Message) -> int
```

**Purpose**: This counts images inside a message so token estimation does not ignore them. Images can consume a lot of model context even though they have little or no text.

**Data flow**: It receives one message. If the content is plain text, it returns zero. If the content is made of blocks, it counts top-level image blocks and images nested inside tool-result blocks, then returns the total.

**Call relations**: Compaction._tokens calls this for each message. Its count is multiplied by a fixed image-token estimate and added to the text estimate.

*Call graph*: called by 1 (_tokens).


##### `Compaction._text`  (lines 472–490)

```
def _text(self, message: Message) -> str
```

**Purpose**: This turns a message's mixed content into readable text for counting, summarizing, and path scanning. It makes text, images, tool results, and tool uses visible in a simple string form.

**Data flow**: It receives one message. If the content is already a string, it returns it. Otherwise it walks through content blocks: text becomes text, images become “[image]”, tool-result text is included, tool-result images become markers, and tool calls become a function-like string with JSON arguments. It joins the pieces with newlines and returns the result.

**Call relations**: Compaction._prepare uses this to render the transcript for the summarizer, Compaction._references uses it to find durable tool-output paths, and Compaction._tokens uses it to estimate prompt size.

*Call graph*: called by 3 (_prepare, _references, _tokens); 1 external calls (dumps).


### `core/src/ufo/loop/prompts/__init__.py`

`other` · `import time`

This is an empty Python package marker file. In Python, a folder can be treated as an importable package when it contains an `__init__.py` file. That means code elsewhere can refer to this directory as `ufo.loop.prompts` and import files inside it in a clean, organized way.

There is no executable logic here, no settings, and no functions. Its value is structural: it tells Python and readers of the project that the surrounding folder is a named part of the system. You can think of it like a label on a drawer. The label does not contain the tools, but it makes the drawer part of the workshop and gives people a reliable way to find what is inside.

Without this file, depending on the Python version and packaging setup, imports involving `ufo.loop.prompts` might be less predictable or fail in some environments.


### `core/src/ufo/loop/prompts/render.py`

`domain_logic` · `prompt construction before a model turn`

This file is the prompt assembly line. The project keeps its system prompt in pieces: a main shell, an agent-specific instruction block, optional skill descriptions, capability sections from packs, citation guidance, and a knowledge cutoff note. This renderer puts those pieces together into one clean text prompt for the model.

It is deliberately strict. If an agent prompt says it needs a variable like {{name}}, the caller must supply exactly that variable. Missing variables fail immediately. Extra variables also fail, because they may mean the caller and prompt are out of sync. After all replacements are done, the renderer checks for any leftover {{slot}} text. A leftover slot means something was not filled, so the prompt is rejected instead of being silently sent to the model with broken braces in it.

The file also normalizes the finished text by collapsing long blank gaps and trimming the end. Finally, it computes a SHA-256 digest, which is a stable fingerprint of the exact prompt content. That digest is useful for logging and debugging: if the prompt changes, the fingerprint changes too. Without this file, prompt construction would be scattered and fragile, and mistakes in templates could reach the model unnoticed.

#### Function details

##### `rendered_prompt`  (lines 49–50)

```
def rendered_prompt(content: str) -> RenderedPrompt
```

**Purpose**: This wraps finished prompt text in a small result object and adds a digest, which is a fingerprint of the exact text. Someone uses it when they want both the prompt content and a reliable way to identify which version of that content was sent.

**Data flow**: It receives the final prompt text. It converts that text into bytes, computes a SHA-256 hash from it, prefixes the hash with "sha256:", and returns a RenderedPrompt containing both the digest and the original text. It does not change any outside state.

**Call relations**: This is the final packaging step after render_template has filled and checked the prompt. render_template hands it the cleaned prompt text, and rendered_prompt hands back the object that later code can send to the model and record in logs.

*Call graph*: called by 1 (render_template); 2 external calls (__init__, sha256).


##### `render_system_prompt`  (lines 53–70)

```
def render_system_prompt(agent_prompt: str, sections: Sequence[tuple[str, str]], skills: Sequence[tuple[str, str]]=(), *, knowledge_cutoff: str) -> RenderedPrompt
```

**Purpose**: This builds the normal main-agent system prompt from the project's standard shell template. It adds the model's knowledge cutoff in a human-readable form, then delegates the rest of the filling work to the general template renderer.

**Data flow**: It receives the agent's instruction text, a list of contributed sections, an optional list of skills, and a required knowledge cutoff such as "2026-02". It turns that date into a phrase like "February 2026", inserts it into the knowledge-cutoff block, places that block into the shell template, and passes the prepared template onward. The result is a RenderedPrompt with final text and digest.

**Call relations**: This is the higher-level entry point for rendering the standard system prompt. When code wants the main prompt, it calls render_system_prompt, which prepares the knowledge-cutoff part and then calls render_template to fill the remaining slots and perform validation.

*Call graph*: calls 1 internal fn (render_template); 1 external calls (strptime).


##### `render_template`  (lines 73–91)

```
def render_template(template: str, agent_prompt: str, variables: Mapping[str, str], skills: Sequence[tuple[str, str]], sections: Sequence[tuple[str, str]]) -> RenderedPrompt
```

**Purpose**: This is the main template-filling engine. It combines the template, agent prompt, variables, skills, citation block, and capability sections into one finished prompt, while rejecting anything incomplete or inconsistent.

**Data flow**: It receives a template string, an agent prompt, a mapping of variable names to replacement text, a skill list, and a section list. First it asks _substitute_vars to fill variables inside the agent prompt. If there is agent prompt text but the template has no place for it, it raises an error. Then it replaces the skill, citation, section, and agent-prompt slots. It checks for any remaining {{...}} placeholders, raises an error if any remain, cleans up excessive blank lines, trims the end, and returns a RenderedPrompt.

**Call relations**: render_system_prompt calls this after preparing the standard shell. Inside the rendering flow, render_template calls _substitute_vars to make the agent prompt safe, render_skill_index to format the skills block, and rendered_prompt to package the final checked text.

*Call graph*: calls 3 internal fn (_substitute_vars, render_skill_index, rendered_prompt); called by 1 (render_system_prompt).


##### `render_skill_index`  (lines 94–103)

```
def render_skill_index(skills: Sequence[tuple[str, str]]) -> str
```

**Purpose**: This turns the list of available skills into a small text block the model can read. If there are no skills, it returns an empty string so the prompt does not include an unnecessary empty section.

**Data flow**: It receives a sequence of skill name and description pairs. With no skills, it outputs an empty string. With skills, it outputs a block wrapped in <available_skills> tags, with one bullet per skill in the form "name: description".

**Call relations**: render_template calls this while filling the skill slot in the prompt template. Its output becomes the skill index text that is inserted into the final prompt.

*Call graph*: called by 1 (render_template).


##### `_substitute_vars`  (lines 106–113)

```
def _substitute_vars(template: str, variables: Mapping[str, str]) -> str
```

**Purpose**: This fills variables inside an agent prompt, but only when the prompt and caller agree exactly on which variables exist. It prevents broken prompts caused by missing values or accidental extra values.

**Data flow**: It receives a prompt-like text containing placeholders such as {{user_name}} and a mapping of replacement values. It scans the text to find declared variable names, compares them with the supplied names, and raises an error if any declared value is missing or any supplied value was not declared. If everything matches, it replaces each placeholder with its supplied text and returns the filled string.

**Call relations**: render_template calls this before placing the agent prompt into the larger shell. This makes sure the agent-specific part is already complete and trustworthy before the full prompt is assembled.

*Call graph*: called by 1 (render_template).


### Sandbox workspace mount
Builds the shared filesystem mount recipe that connects a sandbox workspace to object storage.

### `core/src/ufo/sandbox/fs_mount.py`

`io_transport` · `sandbox create/attach workspace mount`

A sandbox needs a working folder that survives outside the container and can be shared between turns of a conversation. This file describes how to mount that folder from S3, which is cloud object storage, into the sandbox as if it were a normal local directory. Without this, tools inside the sandbox would not reliably see the conversation workspace at `/workspace`.

The file does not run the commands itself. Instead, it safely builds command strings for another part of the system to run as root or as a special unprivileged user. Think of it like writing a checklist for a technician: open access to the FUSE device, prepare a private token file, start a local credential relay, then run `s3fs`, the program that makes S3 look like a filesystem.

A key detail is credential refresh. The mount does not store permanent cloud keys. Instead, a small local relay reads a root-owned token file and answers credential requests from `s3fs`. Later sandbox attachments can rotate that token without tearing down the mount.

The file also builds a health probe. Before remounting, the system can ask: is `/workspace` still mounted, can the relay answer, and can the directory be listed? If yes, it skips unnecessary teardown work.

#### Function details

##### `s3fs_command`  (lines 26–58)

```
def s3fs_command(bucket: str, key_prefix: str, mountpoint: str, s3_url: str, region: str, path_style: bool) -> str
```

**Purpose**: Builds the exact `s3fs` command that mounts one S3 bucket prefix at a local folder. It includes options that make S3 behave more like a normal directory tree and ensure the sandbox user owns files seen through the mount.

**Data flow**: It receives the bucket name, S3 key prefix, local mount folder, S3 service URL, region, and whether path-style S3 requests are needed. It shell-quotes those values so they can be safely placed inside a command string, adds the needed `s3fs` options, and returns one complete command line as text. It does not run the command or change the machine by itself.

**Call relations**: This is one of the command builders used when a sandbox workspace is being mounted. It relies on `shlex.quote` to protect command-line values before the returned command is handed to the larger mount script built elsewhere.

*Call graph*: 1 external calls (quote).


##### `prepare_token_staging_command`  (lines 61–63)

```
def prepare_token_staging_command() -> str
```

**Purpose**: Builds a command that creates the private directory used to stage filesystem access tokens. This gives the system a safe place to write a new token before installing it for use by the credential relay.

**Data flow**: It reads the fixed staging-directory path from this file, quotes it for shell use, and returns an `install -d` command that creates the directory owned by root with private permissions. The result is text for another component to execute; the function itself does not touch the filesystem.

**Call relations**: This helper supports the credential setup that must happen before the S3 mount can refresh credentials. Like the other builders in this file, it delegates shell escaping to `shlex.quote` and hands back a command string to the sandbox carrier.

*Call graph*: 1 external calls (quote).


##### `install_token_command`  (lines 66–69)

```
def install_token_command() -> str
```

**Purpose**: Builds a command that moves a freshly staged token into its live location with strict permissions. The token is private because it lets the local relay ask for credentials used by the S3 filesystem mount.

**Data flow**: It takes no outside arguments. It reads the fixed staging path and final token path, quotes both, and returns a command that makes the staged token root-owned, makes it readable only by root, and atomically moves it into place. The output is a shell command string; executing that string changes the token file on disk.

**Call relations**: This command is part of the token-rotation story used by the mount relay. It does not start the relay or mount S3 itself, but prepares the secret that the relay later reads when `mount_scripts` has started the filesystem path.

*Call graph*: 1 external calls (quote).


##### `mount_scripts`  (lines 72–121)

```
def mount_scripts(mountpoint: str, s3fs: str, credential_url: str) -> tuple[str, str]
```

**Purpose**: Builds the two main root shell commands for mounting the workspace: one to prepare the environment and one to perform the mount. These commands are designed to be safe to run again when a sandbox is reused.

**Data flow**: It receives the local mountpoint, a prebuilt `s3fs` command, and the credential endpoint URL. It quotes those pieces, then creates a `prepare` command that enables FUSE access, allows `allow_other`, and lazily unmounts anything already at the mountpoint. It also creates a `mount` command that makes the mount directory, protects the token file, creates a relay secret if needed, starts the local credential relay if it is not already running, and finally runs `s3fs` as the dedicated mount user. It returns both command strings as a pair.

**Call relations**: This is the central recipe in the file. It expects an `s3fs` command, often produced by `s3fs_command`, and wraps it with the surrounding setup needed inside the sandbox. It uses `shlex.quote` throughout because these commands cross a privileged shell boundary.

*Call graph*: 1 external calls (quote).


##### `mount_health_check`  (lines 124–136)

```
def mount_health_check(mountpoint: str) -> str
```

**Purpose**: Builds a command that checks whether an existing workspace mount is still usable. This lets the system avoid tearing down and rebuilding a healthy mount when reattaching to a running sandbox.

**Data flow**: It receives the mountpoint path, quotes it, and returns a shell command that performs three checks in order: confirms the path is a mountpoint, asks the local credential relay for a response using its secret, and lists the mounted directory through `s3fs`. The function only returns the probe command; the caller decides what to do with success or failure.

**Call relations**: This function supports the attach-time decision of skip versus remount. Its returned command checks the relay created by `mount_scripts` and the filesystem mounted by `s3fs_command`, using `shlex.quote` to safely embed the mountpoint path.

*Call graph*: 1 external calls (quote).


### Tool catalog
Defines the registry used to describe, publish, and look up tools available during the turn.

### `core/src/ufo/tools/registry.py`

`data_model` · `startup and tool dispatch`

A tool is something the model can ask the system to do, such as fetch a page, search, call another service, or write something outside the program. This file gives each tool a standard wrapper called `ToolDef`: it records the tool’s name, human-readable description, expected input format, and the async function that actually runs it. It also records safety flags. For example, `untrusted` means the result may contain outside text that should not be treated as instructions, and `side_effecting` means the tool may change the outside world, so retries need extra care.

`ToolRegistry` is the fixed catalog of these tool definitions. Think of it like a labeled toolbox: every tool must have a unique label, and if someone asks for a missing label, the registry raises a clear error instead of guessing. The registry can also produce the simplified schemas sent to the model, so the model knows which tools exist and what inputs each one expects.

Without this file, the engine would have no reliable way to advertise tools, prevent duplicate names, or turn a model’s requested tool name into the correct handler function.

#### Function details

##### `ToolDef.schema`  (lines 37–42)

```
def schema(self) -> ToolSchema
```

**Purpose**: Builds the public description of one tool that can be sent to the model. It includes the tool’s name, its plain-language description, and the JSON-style shape of the input the tool expects.

**Data flow**: It starts with a `ToolDef`, which already contains a tool name, description, and a Pydantic input model. It asks that input model to describe its fields as a JSON schema, then packages the name, description, and input schema into a `ToolSchema`. The result is a clean wire-ready description of the tool, not the tool’s executable handler.

**Call relations**: This is used by the registry when it needs to publish all available tools. It hands the finished schema to `ToolRegistry.schemas`, which gathers schemas for the whole toolbox.

*Call graph*: 1 external calls (__init__).


##### `ToolRegistry.__post_init__`  (lines 49–53)

```
def __post_init__(self) -> None
```

**Purpose**: Checks the tool catalog right after it is created and refuses to allow two tools with the same name. This prevents confusing situations where a model asks for one name but more than one handler could match.

**Data flow**: It reads the list of tool definitions stored in the registry, extracts their names, and looks for names that appear more than once. If all names are unique, nothing changes. If duplicates are found, it raises a `ValueError` with the repeated names so the configuration problem is visible immediately.

**Call relations**: This runs automatically when a `ToolRegistry` is constructed. It protects later dispatch code by making sure `ToolRegistry.get` can safely treat a tool name as pointing to one clear tool.


##### `ToolRegistry.schemas`  (lines 55–56)

```
def schemas(self) -> tuple[ToolSchema, ...]
```

**Purpose**: Returns the model-facing descriptions for every registered tool. This is how the system turns its internal toolbox into the list of tools the model is allowed to call.

**Data flow**: It reads the registry’s tuple of `ToolDef` objects. For each one, it calls `ToolDef.schema` to make a `ToolSchema`. It returns all of those schemas as a tuple, leaving the registry itself unchanged.

**Call relations**: This sits between the internal tool definitions and the model-facing interface. It relies on each `ToolDef` to describe itself, then collects those descriptions into one complete set.


##### `ToolRegistry.get`  (lines 58–62)

```
def get(self, name: str) -> ToolDef[Any]
```

**Purpose**: Finds the tool definition with a requested name, so the engine can run the correct handler. If the name is not known, it fails clearly instead of silently doing nothing or choosing the wrong tool.

**Data flow**: It receives a tool name as text and scans through the registered tools. When it finds a matching name, it returns that full `ToolDef`, including the input model, handler, and safety flags. If no tool matches, it raises a `KeyError` saying the tool is unknown.

**Call relations**: The engine’s dispatch path calls this when the model has requested a tool by name. `ToolRegistry.get` translates that name into the concrete tool definition the engine needs before it can validate inputs, apply safety behavior, and call the tool handler.

*Call graph*: called by 1 (_dispatch_segments).

## 📊 State Registers Touched

- `reg-effective-config` — The running service’s merged settings, such as required keys, enabled backends, safety options, and service behavior.
- `reg-extension-pack-manifest` — The installed pack and extension menu that says what tools, routes, jobs, skills, credentials, and backends exist.
- `reg-agent-profile-settings` — The saved assistant settings for a workspace, including which agent is used and what it is allowed to do.
- `reg-conversation-thread-state` — The saved conversation identity and history that let the system continue the same thread over time.
- `reg-turn-record-lifecycle` — The durable record of each unit of agent work, including who started it, its status, parent links, and final result.
- `reg-credential-secret-store` — The encrypted store of workspace secrets and credential kinds used without exposing raw tokens to agents.
- `reg-tool-catalog` — The live list of tools the model can call, including their names, descriptions, schemas, and dispatch targets.
- `reg-model-catalog-pricing` — The shared list of available AI models, provider details, limits, credentials, and prices.
- `reg-prompt-skill-library` — The enabled instructions, skill folders, helper profiles, and prompt versions that shape how the agent behaves.
- `reg-sandbox-workspace-handle` — The saved handle and lease for the safe workspace where a conversation can run commands and keep files.
- `reg-blob-storage-backend` — The shared large-file storage used for workspace files, transcripts, source snapshots, and artifacts.
- `reg-network-egress-policy` — The allow-or-deny rules for outbound network calls, including when approved secrets may be attached.
- `reg-browser-session-provider` — The shared way to obtain a browser automation endpoint for a turn, regardless of where the browser runs.
- `reg-mcp-server-connections` — The configured MCP tool-server connections used to discover and call extra provider tools.
- `reg-search-index-memory-graph` — The shared recall stores for searchable chunks, remembered facts, memory pages, and knowledge-graph links.
- `reg-transcript-compaction-store` — The saved conversation transcript and compacted summaries used to rebuild context and inspect past turns.
- `reg-turn-context-token-budget` — The per-turn context-window and token-budget state used to compact history, construct model requests, and constrain model/tool work.
- `reg-user-created-skill-store` — Persistent user-authored skill definitions and metadata that are loaded into the skill library and made available to prompts and tools across turns.
- `reg-browser-runtime-session-state` — Mutable browser automation session state such as active CDP sessions, tabs, cookies, screenshots, and downloads used while browser tools and subagents operate.
- `reg-workspace-file-access-leases` — Short-lived scoped credentials or signed grants that let sandboxes mount or access only approved workspace blob/file paths.
