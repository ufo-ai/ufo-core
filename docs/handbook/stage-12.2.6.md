# Writing and Report Production Extensions  `stage-12.2.6`

This stage adds higher-level help for writing and for turning longer reports into short digests. It is not the core work loop itself. Instead, it is shared support that other parts of the system can call when they need clear prose or a scheduled summary.

The documents subagent file defines a built-in “writing” subagent, which is like a specialist assistant inside the larger assistant. It spells out what the writer is allowed to do, what tools it can use, what kind of input it receives, what kind of output it must return, and which model and skill description guide its behavior.

The report digest code has two parts. The digest file defines the shape of a digest entry, meaning the small summary item produced from a full report, and cleans that entry so it is ready to store or show. It also creates the instructions for the digest writer. The manifest file plugs the digest feature into the system by declaring its scheduled job, admin tool, skill text, and report object.

## Files in this stage

### Writing Subagent Definition
Defines the built-in prose-writing child worker that other extensions can rely on for drafting and editing tasks.

### `extensions/documents/ufo_ext_documents/subagent.py`

`config` · `when spawning a writing subagent`

This file is like the job description and equipment list for a writing assistant. When the larger system wants help producing or editing prose, it can spawn a child agent using this profile instead of giving the main agent every writing detail to carry at once.

The file names the profile “writing” and pins it to a specific model, `gpt-5.6-terra`, so this child has a predictable writing voice and capability rather than simply inheriting whatever model the parent is using. It also loads a prompt from `prompts/subagent_writing.md`, which is the child’s standing instruction sheet.

The profile deliberately gives the child only a small set of tools: it can read, write, edit, search files, and load a skill. It cannot run shell commands, use a programming REPL, browse the web, or share files directly. That matters because this worker is meant to write drafts, not act like a second coding agent or fetch outside facts on its own. The workspace becomes the handoff point: the child writes there, and the parent reads the result back.

Two small data models define the message shapes. `WritingTask` describes what the parent asks for, including which skills to preload. By default it preloads `writing-drafts`, so the child starts with the expected workflow already available. `WritingResult` describes the freeform result the child returns.


### Report Digest Production
Defines the digest entry model, cleanup behavior, writer instructions, and extension wiring for scheduled report digest generation.

### `extensions/report_digest/ufo_ext_report_digest/digest.py`

`domain_logic` · `digest creation and validation`

A report can be long, but a digest should be short enough for a reader to scan quickly and decide whether to open the full report. This file is the rulebook and safety rail for that process. It defines two data shapes: `DigestEntry`, which represents the whole digest item, and `DigestPoint`, which represents one important finding inside it.

The file keeps digest text within strict size limits. Instead of rejecting an entry when a title or summary is too long, it trims the text at a word boundary, so the reader does not see broken half-words. It also removes repetition. It compares the title, summary, and bullet-like points using simplified word roots, ignoring common words such as “the” and “and”. If a lower line says mostly what an earlier line already said, it is dropped. This keeps the digest from wasting space by repeating itself.

The file also limits how much of the source report reaches the writer, which helps control cost and keeps the writer focused on the start of the report. Finally, `writing_standard` loads the prompt text and skill instructions that define how the digest should be written. In short, this file turns “a report plus instructions” into a small, consistent, non-redundant digest entry.

#### Function details

##### `_clipped`  (lines 96–107)

```
def _clipped(value: str, ceiling: int) -> str
```

**Purpose**: Shortens a piece of prose to a maximum length without cutting through the middle of a word. It is used so digest fields stay within their display budget while still looking intentional and readable.

**Data flow**: It receives a text value and a character limit. It trims outside whitespace, checks whether the text already fits, and if not, cuts it near the limit at the last space, then removes trailing punctuation or dash-like marks. It returns the cleaned, shortened string.

**Call relations**: The validators for point text, point actor, title, and summary all call this helper when accepting model output. It is the shared trimming rule that keeps every visible digest field tidy.

*Call graph*: called by 4 (_summary, _title, _actor, _text).


##### `_stem`  (lines 110–117)

```
def _stem(word: str) -> str
```

**Purpose**: Reduces a word to a short root-like form so related words can be compared as similar. For example, this helps the code treat different endings of the same idea as overlapping content.

**Data flow**: It receives one word, focuses on the part after the last hyphen when that part is long enough, removes a known ending such as “ing” or “ation” when safe, and returns only the first few root characters. The output is a compact comparison key, not a word meant for readers.

**Call relations**: `_content` calls this for each meaningful word it extracts. The result helps later checks decide whether a summary or point really adds new information.

*Call graph*: called by 1 (_content).


##### `_content`  (lines 120–121)

```
def _content(text: str) -> tuple[str, ...]
```

**Purpose**: Pulls the meaningful comparison words out of a text. It ignores common filler words and turns the remaining words into simplified roots.

**Data flow**: It receives a string of text. It lowercases the text, finds word-like pieces, drops stopwords such as “the” and “with”, passes each remaining word through `_stem`, and returns the resulting roots as a tuple.

**Call relations**: `_adds_to` uses this to measure novelty, and `DigestEntry._said_once` uses it to remember what the reader has already been told. It is the file’s way of turning prose into a rough fingerprint of meaning.

*Call graph*: calls 1 internal fn (_stem); called by 2 (_said_once, _adds_to).


##### `_adds_to`  (lines 124–126)

```
def _adds_to(text: str, said: set[str]) -> bool
```

**Purpose**: Decides whether a line adds enough new information to be worth keeping. It protects the digest from repeating the same idea in different words.

**Data flow**: It receives a text line and a set of word roots that have already appeared. It extracts the line’s meaningful roots with `_content`, counts how many are new, and compares that share with the required novelty threshold. It returns true only when the line has content and enough of it is new.

**Call relations**: `DigestEntry._said_once` calls this while reading the digest from top to bottom. It acts like a gatekeeper for the summary and each point.

*Call graph*: calls 1 internal fn (_content); called by 1 (_said_once).


##### `DigestPoint._text`  (lines 139–140)

```
def _text(cls, value: str) -> str
```

**Purpose**: Keeps the text of a single digest point short enough for the digest format. It preserves the point instead of rejecting it when it runs long.

**Data flow**: It receives the proposed point text during `DigestPoint` validation. It sends that text to `_clipped` with the point-text character limit and stores the returned shortened version in the model.

**Call relations**: Pydantic, the data validation library used here, calls this automatically when a `DigestPoint` is created. It relies on `_clipped` so point text follows the same neat word-boundary trimming rule as other fields.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestPoint._actor`  (lines 144–145)

```
def _actor(cls, value: str) -> str
```

**Purpose**: Keeps the actor name or label attached to a digest point within its allowed length. The actor is whoever or whatever the report says did the thing.

**Data flow**: It receives the proposed actor string during `DigestPoint` validation. It trims it through `_clipped` using the actor-length limit, then stores the cleaned result.

**Call relations**: Pydantic calls this automatically when building a `DigestPoint`. It shares the same clipping helper used for titles, summaries, and point text.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._decoded`  (lines 161–167)

```
def _decoded(cls, value: object) -> object
```

**Purpose**: Accepts point data even when a provider returns it as JSON text instead of a normal list. This makes the digest entry more tolerant of slightly awkward model output.

**Data flow**: It receives the raw value intended for `points`. If that value is a string, it parses it as JSON. If the parsed result is a dictionary containing `points`, it extracts that field; otherwise it uses the parsed value directly. Non-string values pass through unchanged.

**Call relations**: Pydantic calls this before normal point validation. It uses `json.loads` to turn text back into structured data so the rest of the `DigestEntry` validation can continue as if the provider had returned the expected shape.

*Call graph*: 1 external calls (loads).


##### `DigestEntry._title`  (lines 171–172)

```
def _title(cls, value: str) -> str
```

**Purpose**: Cleans and limits the digest title. It also prevents the title from carrying a second finding after the configured join mark.

**Data flow**: It receives the proposed title. It keeps only the part before the first title-join character, then passes that through `_clipped` with the title-length limit. The result becomes the stored title.

**Call relations**: Pydantic calls this when creating a `DigestEntry`. It uses `_clipped` so the title remains short, readable, and focused on the top-ranked finding.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._summary`  (lines 176–177)

```
def _summary(cls, value: str) -> str
```

**Purpose**: Keeps the digest summary to one compact clause. It trims long summaries rather than rejecting the whole digest entry.

**Data flow**: It receives the proposed summary text during validation. It passes the text to `_clipped` with the summary-length limit and stores the shortened result.

**Call relations**: Pydantic calls this automatically for a `DigestEntry`. Later, `DigestEntry._said_once` may remove the summary entirely if it repeats the title too closely.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._titled`  (lines 180–183)

```
def _titled(self) -> 'DigestEntry'
```

**Purpose**: Enforces the rule that a digest entry claiming to contain a real change must have a title. Without this, a useful report could appear as an empty or unclear row.

**Data flow**: It reads the already-built `DigestEntry`. If `holds_a_change` is true and the title is empty, it raises a validation error. Otherwise it returns the entry unchanged.

**Call relations**: Pydantic calls this after field validation. It is a final sanity check before the entry is accepted.


##### `DigestEntry._said_once`  (lines 186–205)

```
def _said_once(self) -> 'DigestEntry'
```

**Purpose**: Removes repeated information from a digest entry. It keeps the title, then only keeps the summary and points if they add enough new content for the reader.

**Data flow**: It starts with the title and turns it into a set of meaningful word roots using `_content`. It checks whether the summary adds enough new roots with `_adds_to`; if not, it clears the summary. Then it walks through the points in order, keeping only points that add enough new content, updating the remembered roots as it goes, and stopping after the allowed number of points. It returns the modified entry.

**Call relations**: Pydantic calls this after the entry has been built and basic fields have been cleaned. It depends on `_content` and `_adds_to` to compare lines by meaning rather than exact wording.

*Call graph*: calls 2 internal fn (_adds_to, _content).


##### `bounded`  (lines 208–210)

```
def bounded(report: str) -> str
```

**Purpose**: Cuts a source report down to the maximum amount the digest writer is allowed to read. This keeps the writing task focused and limits the size of the payload sent to an outside model or API.

**Data flow**: It receives the full report text. It returns only the first configured number of characters, leaving the original report unchanged.

**Call relations**: Other digest-writing code can call this before sending report text to the writer. It is the simple front gate that enforces the report-size budget.


##### `writing_standard`  (lines 213–227)

```
def writing_standard() -> str
```

**Purpose**: Builds the instruction text that tells the digest writer how to write entries. It combines the local prompt, the shared delivery register, and the skill instructions into one standard.

**Data flow**: It reads the skill document from disk, removes its frontmatter metadata, reads the subagent prompt file, adds the shared delivery-register text, and joins those parts with blank lines. The returned string is the full instruction package for the digest writer.

**Call relations**: Digest-writing code can call this when preparing the model or agent that creates digest entries. It reaches out to files on disk and to the imported delivery-register block so the writer and any agent using the same skill follow the same rules.


### `extensions/report_digest/ufo_ext_report_digest/manifest.py`

`orchestration` · `extension load, scheduled background runs, and admin tool calls`

This file is like the front desk for the report-digest extension. It tells the host system, “Here is my name, here is the writing standard I use, here is the background job to run, here is the admin-only tool people can call, and here is the kind of object I add to reports.” Without this file, the digest writer might exist in the codebase, but the platform would not know when to run it or how users should reach it.

The scheduled job runs every ten minutes. It looks for workspaces with reports that still need digest entries, then uses the background language model and stored report data to write those entries. The file does not contain the writing logic itself; it hands that work to `DigestWriter`.

It also defines a tool called `rebuild_report_digest`. This is for workspace admins who want recent digest entries regenerated. The tool does not immediately rewrite every entry. Instead, it marks eligible reports from the last seven days as due again, then the normal scheduled job rewrites them gradually. That keeps one admin action from causing a sudden burst of expensive or slow work.

The `manifest` function ties all of this together into a single declaration the extension system can load.

#### Function details

##### `write_digests`  (lines 31–36)

```
async def write_digests(ctx: ExtensionContext) -> None
```

**Purpose**: Runs the scheduled digest-writing job. It checks that the extension has the background model and blob storage access it needs, then starts the digest writer.

**Data flow**: It receives an `ExtensionContext`, which is the bundle of services and settings the extension gets from the host system. It verifies that a background language model is available and that report content can be read from stored member-context blobs. If either is missing, it stops with a clear error. If both are present, it creates a `DigestWriter` with the context, model, and blob access, then runs it. The result is not returned directly; the important change is that missing digest entries may be written into stored report data.

**Call relations**: The job registered in `manifest` calls this function on the configured schedule. This function is only the launcher: after doing safety checks, it hands the real digest-writing work to `DigestWriter`.

*Call graph*: 1 external calls (__init__).


##### `rebuild_report_digest_handler`  (lines 43–66)

```
async def rebuild_report_digest_handler(ctx: ToolContext, args: RebuildReportDigestInput) -> ToolResult
```

**Purpose**: Implements the admin tool that asks the system to rewrite recent report-digest entries. It protects the action so only workspace admins can trigger it, then marks recent reports to be processed again later.

**Data flow**: It receives a `ToolContext`, which describes the current tool call, and an input object with no allowed fields. It first makes sure the tool call has access to the extension context. Then it checks whether the speaker is a workspace admin. If not, it raises an error with a user-facing message. If the speaker is allowed, it runs `DigestRebuild`, which marks reports from the last seven days as needing fresh digest entries. It returns a `ToolResult` containing either a message that nothing qualified or a message saying how many reports were queued for rewriting.

**Call relations**: This function is attached to the `rebuild_report_digest` tool by `manifest`. When an admin invokes that tool, this handler checks permission, delegates the marking work to `DigestRebuild`, and wraps the outcome in text content for the caller. The actual rewriting is deliberately left to the scheduled digest job.

*Call graph*: calls 1 internal fn (speaker_is_admin); 3 external calls (__init__, __init__, __init__).


##### `manifest`  (lines 69–101)

```
def manifest() -> Manifest
```

**Purpose**: Builds the extension declaration that the host system loads. It lists the extension’s name and version, its skill text, admin tool, scheduled job, report object, and needed read access.

**Data flow**: It takes no input. It gathers constants and imported pieces from this file and neighboring modules, then creates a `Manifest` object. Inside that manifest, it registers the skill directory, the `rebuild_report_digest` tool and its input model, the scheduled `report_digest` job, the candidate-selection function that finds workspaces needing work, and the report object definition. The returned manifest is the structured description the platform uses to install and run the extension.

**Call relations**: The extension loader calls this function when it discovers the extension. The manifest it returns connects the platform to the other functions in this file: the scheduled job points to `write_digests`, and the tool definition points to `rebuild_report_digest_handler`. It also wires in `owner_candidates` so the background job runs only where there is digest work to do.

*Call graph*: 5 external calls (__init__, __init__, __init__, __init__, owner_candidates).
