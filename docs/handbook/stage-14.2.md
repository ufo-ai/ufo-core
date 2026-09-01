# Offline Product and Quality Maintenance  `stage-14.2`

This stage is background upkeep. It runs outside the main user conversation flow, like a night crew that fixes missed work, rebuilds summaries, and checks whether agents can be improved safely. The preview renderer looks for recently shared files that still lack preview images and asks the preview service to try again. The homepage cleanup removes an old workspace homepage setting when the chat app should now be the main landing page.

The report digest pieces keep scheduled reports easy to skim. The digest code defines what a short entry should look like, trims it, and removes repetition. The writer finds newly published reports without summaries, asks a model to summarize them, and stores the result.

The self-improvement extension reviews past agent activity. Its package file identifies the extension. The corpus builder collects failed tool-use conversations and splits them into learning and test examples. The cron job runs the periodic check. The model wrapper gives a simple way to ask the language model for text or turns. The proposer suggests prompt changes, while replay, evaluation, and gate test those changes cautiously before any human-approved proposal moves forward.

## Files in this stage

### Offline content repairs
Periodic maintenance jobs repair missing generated assets and clean up stale workspace homepage state.

### `core/src/ufo/runtime/media/preview_renderer.py`

`orchestration` · `background retry job`

When someone shares a document, the system tries to create a preview image right away. That first attempt is only “best effort”: if the preview service is briefly down, the file is still shared, but the database row has no preview image recorded. This file fixes that gap later.

The main piece is `PreviewRenderer`, a small background job. It looks for recently shared artifacts whose preview fields are still empty and whose filenames have supported document endings, such as `.pdf`, `.docx`, or `.csv`. It only checks a limited recent time window. That matters because some files may be permanently impossible to render, such as corrupt documents; without the window, the system could keep retrying hopeless files forever.

For each candidate, it does not download the file into the core app. Instead, it creates temporary signed web links: one link lets the preview service read the source file, and another lets it upload the PNG preview. This is like giving a courier a temporary pickup ticket and drop-off ticket, rather than carrying the package yourself. If rendering succeeds, the job records the new preview key, media type, and byte size in the database. If the service is unreachable or refuses the request, the row is left unchanged so the next scheduled run can try again.

#### Function details

##### `_eligible`  (lines 42–43)

```
def _eligible(filename_column: sa.Column) -> sa.ColumnElement[bool]
```

**Purpose**: This helper builds the database test for “is this filename a kind of file we know how to preview?” It checks for supported endings like PDF, Word, spreadsheet, Markdown, SVG, and CSV.

**Data flow**: It receives a database column that contains filenames. It turns the list of allowed suffixes into a single database condition that matches filenames ending in any of those suffixes, without caring about letter case. The result is not a true-or-false value yet; it is a condition that can be placed inside a database query.

**Call relations**: The preview job uses this helper when searching for rows to render, and the workspace-discovery query uses it when deciding which workspaces have possible work waiting. It keeps both queries using the same definition of a previewable file.

*Call graph*: called by 2 (candidate_workspaces, run); 2 external calls (ilike, or_).


##### `PreviewRenderer.run`  (lines 57–79)

```
async def run(self) -> None
```

**Purpose**: This is the main body of the retry job for one workspace. It finds a small batch of recent shared artifacts that are missing previews, then asks `_render_one` to try rendering each of them.

**Data flow**: It starts by calculating a cutoff time, so only recently shared files are considered. It opens a workspace-scoped database transaction, reads up to a small batch of rows whose preview is missing and whose filename is eligible, and then closes the database work. If there are no rows, it stops. If there are rows, it opens an HTTP client and passes each file’s blob key and filename to `_render_one`. The database may be updated later by `_render_one`, but `run` itself mainly gathers candidates and drives the loop.

**Call relations**: A scheduler or jobs layer calls this method for a particular workspace. Inside the job, it relies on `_eligible` to keep the search focused on supported file types, then hands each selected file to `_render_one`, which performs the actual conversation with the blob store, preview service, and database.

*Call graph*: calls 2 internal fn (_render_one, _eligible); 4 external calls (now, AsyncClient, select, workspace_tx).


##### `PreviewRenderer._render_one`  (lines 81–117)

```
async def _render_one(self, client: httpx.AsyncClient, blob_key: str, filename: str) -> None
```

**Purpose**: This function tries to create one missing preview image. It gives the preview service temporary URLs to read the original file and upload the PNG, then records the result if the service succeeds.

**Data flow**: It receives an HTTP client, the stored file’s blob key, and the original filename. From the filename it chooses the document kind and builds a new storage key for the preview PNG. It asks the blob store for a temporary read link for the source file and a temporary upload link for the preview. It sends those links, plus size limits and page count, to the preview service. If the service cannot be reached or returns an error status, it logs the problem and leaves the database unchanged. If the service returns success, it reads the preview size from the response and updates the shared artifact row with the preview’s storage key, PNG media type, and byte size.

**Call relations**: `PreviewRenderer.run` calls this once per candidate row. This function is the point where the retry job hands work to the external preview service. It also uses the workspace-scoped database transaction when saving a successful result, so it only writes inside the currently selected workspace.

*Call graph*: called by 1 (run); 7 external calls (post, dumps, PurePosixPath, update, workspace_tx, log, uuid4).


##### `PreviewRenderer.candidate_workspaces`  (lines 119–133)

```
async def candidate_workspaces(self) -> tuple[UUID, ...]
```

**Purpose**: This function finds which workspaces have recent shared files that might need preview retries. It lets the larger job system avoid running the renderer for workspaces that have nothing to do.

**Data flow**: It calculates the same recent cutoff time used by the renderer. Then it opens an owner-level database transaction, which can look across workspaces, and selects the distinct workspace IDs for shared artifacts whose preview is missing, whose creation time is still inside the retry window, and whose filename is eligible. It returns those workspace IDs as a tuple.

**Call relations**: The broader scheduling layer can call this before running workspace-specific jobs. It uses `_eligible` so the workspace search matches the actual rendering search, then returns only workspace IDs; later, `PreviewRenderer.run` does the per-workspace row lookup and rendering work.

*Call graph*: calls 1 internal fn (_eligible); 3 external calls (now, select, owner_tx).


### `extensions/sites/ufo_ext_sites/main_homepage.py`

`domain_logic` · `scheduled background sweep`

This file fixes a subtle rollout problem. Older setup code could attach a hosted page to every agent, including the workspace's main agent. Later, when the chat app becomes that main agent, the app's real homepage should come from the deployed chat bundle, not from that old hosted-page row. If the old binding stays in place, users see the seeded page instead of the chat screen.

The file defines a scheduled sweep. Think of it like a janitor that periodically checks only the rooms that still have an old sign on the door. It finds workspaces where the main agent is now the declared chat agent, where that agent still has a homepage page bound to it, and where this release has not already been marked as done.

When the sweep runs for one workspace, it looks up the main chat agent. If a hosted site is still bound as that agent's homepage, it releases that binding. While doing so, it chooses a safe visibility level: the released page must not become more public than either the page itself or the agent used to be. Finally, it writes a marker into extension storage so the workspace is not cleaned again. That marker matters because users may later set their own homepage; this job must not remove a real user choice on the next tick.

#### Function details

##### `_the_chat_main_agent`  (lines 67–75)

```
def _the_chat_main_agent() -> sa.ColumnElement[bool]
```

**Purpose**: Builds the database condition that means “this row is the workspace's main agent, and it has already been taken over by the chat app.” This keeps the cleanup from touching an agent before chat adoption has actually happened.

**Data flow**: It takes no outside values. It reads the file's lightweight description of the agent table, combines three checks into one database expression, and returns that expression for use in larger queries.

**Call relations**: The candidate finder uses this condition when searching for workspaces that still need cleanup. The release job uses the same condition again when it verifies the current workspace's main agent before making any change.

*Call graph*: called by 2 (release_main_homepage, with_a_bound_main_homepage); 1 external calls (and_).


##### `unreleased_main_homepage_workspaces`  (lines 78–97)

```
def unreleased_main_homepage_workspaces(extension: str) -> WorkspaceCandidates
```

**Purpose**: Declares which workspaces should be offered to the scheduled release job. A workspace qualifies only if it has a homepage bound to the adopted chat main agent and does not already have the “released” marker.

**Data flow**: It receives the extension name used for the marker lookup. It builds a database query that finds matching workspace IDs, then wraps that query in the job system's workspace-candidate format. The result is not the cleanup itself; it is the list-making rule for the cleanup job.

**Call relations**: This is the seam between the job scheduler and the cleanup logic. The scheduler can call this to discover owners to process, and the inner query uses the shared chat-main-agent condition so the same definition is used for both discovery and release.

*Call graph*: 1 external calls (owner_candidates).


##### `unreleased_main_homepage_workspaces.with_a_bound_main_homepage`  (lines 84–95)

```
def with_a_bound_main_homepage() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds the exact database query for workspaces that still have an old homepage binding on their chat main agent. It also excludes workspaces that already carry the release marker.

**Data flow**: It reads the hosted-site table, the agent table, and the extension storage table. It joins hosted pages to their homepage agent, filters for the adopted chat main agent, checks that no release marker exists for that workspace, groups the results by workspace, and returns a query that produces workspace IDs.

**Call relations**: This helper lives inside the candidate function because it is only useful there. It calls the shared chat-agent condition, then hands the finished query back to the outer function, which gives it to the workspace-candidate machinery.

*Call graph*: calls 1 internal fn (_the_chat_main_agent); 4 external calls (exists, literal, select, join).


##### `released_visibility`  (lines 100–104)

```
def released_visibility(site: str, agent: str) -> Visibility
```

**Purpose**: Chooses the safe visibility for a page after it is no longer hidden behind an agent homepage binding. It prevents the release from accidentally making a page visible to more people than before.

**Data flow**: It receives two visibility names: one from the hosted page and one from the agent. It converts both into ordered levels, picks the narrower one, and returns that visibility value. The page's visibility after release is therefore capped by both sources.

**Call relations**: The main release job calls this just before unbinding a homepage. Its result is passed into the hosted-sites storage layer so the released page resumes with a safe audience.

*Call graph*: called by 1 (release_main_homepage); 1 external calls (visibility_level).


##### `release_main_homepage`  (lines 107–130)

```
async def release_main_homepage(ctx: ExtensionContext) -> None
```

**Purpose**: Performs the cleanup for one workspace: find the adopted chat main agent, release any hosted page bound as its homepage, and record that the workspace has been processed. This is the action the scheduled sweep ultimately runs.

**Data flow**: It receives an extension context, which supplies the current workspace, database transactions, and extension storage. It first opens a transaction and looks up the workspace's main chat agent. If there is no such agent, it stops. Otherwise it asks the hosted-sites store whether that agent has a homepage binding. If one exists, it releases the binding using the safe visibility chosen by released_visibility. Finally, it writes a marker containing the agent ID so later sweeps know not to remove a user-set homepage.

**Call relations**: This function is the end of the sweep story. The candidate logic identifies workspaces worth trying; when this function runs for one of them, it rechecks the chat-main-agent condition, talks to the hosted-sites store to release the binding, and uses the visibility helper to avoid accidental disclosure. The marker is written after the release so that, if a crash happens mid-way, a later run can safely try again.

*Call graph*: calls 3 internal fn (transaction, _the_chat_main_agent, released_visibility); 2 external calls (__init__, select).


### Report digest rebuilding
Scheduled report digests are shaped, constrained, generated, and stored for later feed display.

### `extensions/report_digest/ufo_ext_report_digest/digest.py`

`domain_logic` · `report digest generation`

A full report can be long, detailed, and expensive to send to a language model. This file keeps the digest process small and repeatable: given one report and one reader context, it produces one compact entry that helps a person decide whether to open the full report.

The main idea is like writing a news ticker. The title gets the strongest finding, the summary gets only one short clause, and at most two extra points survive underneath. Each text field is clipped instead of rejected, so one slightly long sentence does not throw away the whole digest.

The file uses Pydantic, a library that checks and cleans data when an object is created. `DigestPoint` represents one supporting finding, with optional actor text saying who did it. `DigestEntry` represents the whole digest item: whether the report contains a real change, plus title, summary, and points.

A key safeguard is the “said once” check. The digest is read from top to bottom, and lower lines are kept only if they add enough new content compared with earlier lines. This prevents a digest from spending precious rows saying the same thing in slightly different words.

The file also limits how much report text is sent onward and builds the exact writing standard by reading prompt and skill files from disk.

#### Function details

##### `_clipped`  (lines 96–107)

```
def _clipped(value: str, ceiling: int) -> str
```

**Purpose**: Shortens a piece of prose to a maximum length without cutting through the middle of a word. This keeps digest fields within display limits while still preserving as much useful text as possible.

**Data flow**: It receives a text value and a character ceiling. It trims surrounding whitespace, checks whether the text already fits, and if not, cuts it near the ceiling at the last space and removes trailing punctuation. It returns the shortened text and does not change anything else.

**Call relations**: This is the shared trimming tool for the digest models. The title, summary, point text, and actor validators all call it so every visible digest field follows the same rule: shorten gracefully instead of rejecting the whole entry.

*Call graph*: called by 4 (_summary, _title, _actor, _text).


##### `_stem`  (lines 110–117)

```
def _stem(word: str) -> str
```

**Purpose**: Reduces a word to a rough root form so similar words can be compared as the same idea. For example, different endings like “changed” and “changes” can be treated more alike.

**Data flow**: It receives one word. It looks at the part after a hyphen when that part is long enough, removes a known ending if doing so leaves a meaningful root, and then keeps only a small prefix of that root. It returns this compact word stem.

**Call relations**: This function is called by `_content`, which prepares text for novelty checks. It is part of the quiet background work that lets `DigestEntry._said_once` decide whether a lower digest line really adds something new.

*Call graph*: called by 1 (_content).


##### `_content`  (lines 120–121)

```
def _content(text: str) -> tuple[str, ...]
```

**Purpose**: Pulls the meaningful searchable words out of a sentence. It ignores common filler words and turns the remaining words into simplified roots for comparison.

**Data flow**: It receives text. It lowercases it, extracts word-like pieces, skips stopwords such as “the” and “and,” sends each remaining word through `_stem`, and returns the resulting stems as a tuple.

**Call relations**: It calls `_stem` for each useful word. `_adds_to` uses it to measure whether one line brings new information, and `DigestEntry._said_once` uses it to remember what the title, summary, and kept points have already said.

*Call graph*: calls 1 internal fn (_stem); called by 2 (_said_once, _adds_to).


##### `_adds_to`  (lines 124–126)

```
def _adds_to(text: str, said: set[str]) -> bool
```

**Purpose**: Decides whether a line says enough that is new to deserve space in the digest. This protects readers from repeated lines that look different but carry the same message.

**Data flow**: It receives a text line and a set of word stems that have already been said. It converts the line into content stems, counts how many are new, and compares that share with the novelty threshold. It returns true only when the line has content and enough of it is new.

**Call relations**: It calls `_content` to turn the line into comparable pieces. `DigestEntry._said_once` calls it while walking through the summary and points, using its answer to decide which lines survive.

*Call graph*: calls 1 internal fn (_content); called by 1 (_said_once).


##### `DigestPoint._text`  (lines 139–140)

```
def _text(cls, value: str) -> str
```

**Purpose**: Cleans the main text of one digest point so it fits the allowed length. It keeps the point usable even if the writer produced a slightly long line.

**Data flow**: It receives the proposed point text while a `DigestPoint` is being validated. It passes that text and the point-text length limit to `_clipped`, then stores the clipped result as the point text.

**Call relations**: This validator relies on `_clipped` for the actual shortening rule. It runs as part of creating or validating a `DigestPoint`, before the point may later be considered by `DigestEntry._said_once`.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestPoint._actor`  (lines 144–145)

```
def _actor(cls, value: str) -> str
```

**Purpose**: Cleans the actor field of a digest point so the named person, group, or system does not exceed its display space.

**Data flow**: It receives the proposed actor text while a `DigestPoint` is being validated. It clips the text to the actor limit using `_clipped` and stores the shortened result.

**Call relations**: Like `DigestPoint._text`, this validator delegates the cutting behavior to `_clipped`. It prepares each point before that point is included in a digest entry.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._decoded`  (lines 161–167)

```
def _decoded(cls, value: object) -> object
```

**Purpose**: Accepts point data even when a provider returns it as a JSON string instead of a real nested list. This makes the digest parser tolerant of a common output shape from external model providers.

**Data flow**: It receives the raw `points` value before normal validation. If the value is a string, it parses it as JSON; if the parsed value is an object containing `points`, it extracts that field, otherwise it uses the parsed value directly. Non-string input is passed through unchanged.

**Call relations**: This validator runs before the rest of `DigestEntry` validation. It calls `json.loads` to decode stringified JSON so the normal Pydantic model-building step can then turn the data into `DigestPoint` objects.

*Call graph*: 1 external calls (loads).


##### `DigestEntry._title`  (lines 171–172)

```
def _title(cls, value: str) -> str
```

**Purpose**: Cleans the digest title so it contains only the first ranked finding and fits the title length limit. If the title contains a semicolon, anything after it is treated as a second finding and removed.

**Data flow**: It receives the proposed title. It splits the text at the first title-join marker, keeps the first part, clips that part with `_clipped`, and stores the result as the title.

**Call relations**: This validator calls `_clipped` for length control. Its output becomes the first line considered by `DigestEntry._said_once`, which uses the title as the baseline for deciding whether lower lines add new information.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._summary`  (lines 176–177)

```
def _summary(cls, value: str) -> str
```

**Purpose**: Cleans the short summary so it stays within one compact clause. This keeps the digest easy to scan.

**Data flow**: It receives the proposed summary text. It clips the text to the summary limit using `_clipped` and stores the shortened result.

**Call relations**: This validator calls `_clipped` before later whole-entry checks run. Afterward, `DigestEntry._said_once` may keep or erase the summary depending on whether it adds enough beyond the title.

*Call graph*: calls 1 internal fn (_clipped).


##### `DigestEntry._titled`  (lines 180–183)

```
def _titled(self) -> 'DigestEntry'
```

**Purpose**: Enforces the rule that a digest entry which claims there is a real change must have a title. A change without a title would leave the reader with no quick reason to care.

**Data flow**: It receives the fully built `DigestEntry` after field validation. If `holds_a_change` is true but the title is empty, it raises an error; otherwise it returns the entry unchanged.

**Call relations**: This whole-entry validator runs after individual fields have been cleaned. It acts as a gate before the digest can be accepted, making sure later consumers do not receive a change entry with no headline.


##### `DigestEntry._said_once`  (lines 186–205)

```
def _said_once(self) -> 'DigestEntry'
```

**Purpose**: Removes summary and point lines that repeat what earlier lines already said. It keeps the digest short, ranked, and useful instead of padded with reworded duplicates.

**Data flow**: It starts with the title’s content words as the set of ideas already said. It tests the summary with `_adds_to`; if the summary is not novel enough, it clears it. Then it checks each point in order, keeping only points that add enough new content, updating the remembered words as it goes, and stopping after the maximum number of points. It returns the updated entry.

**Call relations**: This validator calls `_content` to remember what has been said and `_adds_to` to judge each lower line. It is the final cleanup pass that turns the separately validated title, summary, and points into a non-repetitive digest entry.

*Call graph*: calls 2 internal fn (_adds_to, _content).


##### `bounded`  (lines 208–210)

```
def bounded(report: str) -> str
```

**Purpose**: Cuts a report down to the maximum amount the digest writer is allowed to read. This keeps the model input predictable and avoids spending effort on report text beyond the intended budget.

**Data flow**: It receives the full report as text. It takes only the first allowed number of characters and returns that shortened report text. It does not inspect or rewrite the contents.

**Call relations**: This helper is used before writing a digest entry, at the point where the long report is prepared for the writer. It does not call other project functions; it simply enforces the report-size boundary.


##### `writing_standard`  (lines 213–227)

```
def writing_standard() -> str
```

**Purpose**: Builds the instruction text that tells the digest writer how to write entries. It combines the local prompt, the shared delivery rules, and the report-digest skill instructions into one standard.

**Data flow**: It reads the skill file from disk, removes its frontmatter metadata, reads the digest prompt file, and combines those instructions with the delivery register block. It returns one complete instruction string.

**Call relations**: This function is used when setting up the digest writer’s prompt. It hands off a single, consistent writing standard so job-based digest writing and agent-based digest writing follow the same rules.


### `extensions/report_digest/ufo_ext_report_digest/writer.py`

`orchestration` · `scheduled background tick and manual rebuild`

This file is the “digest writer” for scheduled reports. A scheduled run can publish a markdown report, but readers usually need a quick explanation of what changed, not the whole document. This writer fills that gap by finding recent finished scheduled runs, reading their first markdown report, asking the language model for a structured digest, and saving one row per report.

It is careful not to waste money or trap itself. It only looks back seven days, works in small batches, and skips any report that already has either a digest entry or a note saying “we read this and there was no change.” That note matters because quiet reports should not be sent to the model again every time the job wakes up.

The flow is like a postal worker sorting mail: pick only undelivered letters, open one, decide who it is for, write a short card, then file the card in the right box. If a letter is missing, unreadable, or the model does not answer in the expected format, the writer leaves that report alone and moves on, so one bad report cannot block all later ones.

The file also includes a rebuild helper. It deletes recent digest rows and quiet notes so the same reports can be summarized again, for example after the digest-writing rules change.

#### Function details

##### `DigestWriter.run`  (lines 117–126)

```
async def run(self) -> None
```

**Purpose**: Runs one digest-writing tick. It gathers a small batch of reports that still need digest entries and tries to process each one without letting one failure stop the whole batch.

**Data flow**: It starts with the current workspace context, model access, and blob store already attached to the writer. It asks for unwritten reports, then sends each report through the digest pipeline. It produces no direct return value, but it may add digest rows or “unchanged” rows to the database.

**Call relations**: This is the top-level method for the writer. It calls `DigestWriter._unwritten` to find work, then calls `DigestWriter._digest` for each report. If `_digest` raises an error for one report, `run` catches it and continues with the next report so the queue can keep draining.

*Call graph*: calls 2 internal fn (_digest, _unwritten).


##### `DigestWriter._digest`  (lines 128–139)

```
async def _digest(self, report: Report) -> None
```

**Purpose**: Processes one report from start to finish. It reads the report, decides who the digest is written for, asks the model for a digest, and stores either a digest entry or a note that the report contained no change.

**Data flow**: A `Report` goes in. The function fetches its markdown body, builds a reader description, sends both to the model, then checks the model’s structured answer. If there is no body or no valid answer, nothing is stored. If the answer says the report contains a meaningful change, a digest entry is saved. If it says there is no change, an “unchanged” marker is saved instead.

**Call relations**: `DigestWriter.run` calls this for every report in the batch. Inside, it hands off to `_body` for blob reading, `_reader` for audience wording, `_written` for the model call, and then either `_store` or `_store_unchanged` depending on what the model says.

*Call graph*: calls 5 internal fn (_body, _reader, _store, _store_unchanged, _written); called by 1 (run).


##### `DigestWriter._unwritten`  (lines 141–213)

```
async def _unwritten(self) -> tuple[Report, ...]
```

**Purpose**: Finds the reports this writer should work on next. It looks for recent, successful scheduled runs in the current workspace that published a markdown report and have not already been read by the digest system.

**Data flow**: It reads the workspace id from the extension context and queries several database tables: turns, conversations, agents, members, shared artifacts, existing digest entries, and unchanged markers. It filters to recent finished scheduled runs with markdown output, chooses the first markdown report per run, orders newest first, limits the batch size, and turns the database rows into `Report` objects.

**Call relations**: `DigestWriter.run` calls this before doing any model work. It uses database selection logic to define the job’s backlog, and its output becomes the list of reports that `run` passes one by one to `_digest`.

*Call graph*: called by 1 (run); 3 external calls (__init__, now, select).


##### `DigestWriter._body`  (lines 215–228)

```
async def _body(self, report: Report) -> str | None
```

**Purpose**: Reads the markdown text for one report safely. It limits how many bytes are pulled from storage and how many characters are later sent to the model, so oversized files cannot overload the worker or create huge model requests.

**Data flow**: A `Report` with a blob key goes in. The function streams bytes from the blob store until it reaches the read limit, decodes those bytes into text, replaces invalid characters if needed, and trims the text with `bounded`. If the blob is missing, it returns `None` instead of raising an error.

**Call relations**: `DigestWriter._digest` calls this first for each report. If `_body` returns text, `_digest` can continue to reader selection and model writing. If it returns `None`, `_digest` stops for that report.

*Call graph*: called by 1 (_digest); 1 external calls (bounded).


##### `DigestWriter._reader`  (lines 230–239)

```
def _reader(self, report: Report) -> str
```

**Purpose**: Builds a plain-language description of who will read the digest. This gives the model context so it can write the summary for either a specific member or the whole workspace.

**Data flow**: A `Report` goes in with its audience, agent name, and possibly the owner’s email address. If the report belongs to a member-specific conversation and an email is known, it returns a sentence naming that person as the reader. Otherwise, it returns a sentence saying the workspace is the reader.

**Call relations**: `DigestWriter._digest` calls this after reading the report body. The returned reader description is passed to `_written`, where it becomes part of the model prompt.

*Call graph*: called by 1 (_digest).


##### `DigestWriter._written`  (lines 241–278)

```
async def _written(self, body: str, reader: str) -> DigestEntry | None
```

**Purpose**: Asks the language model to turn a report into a structured digest entry. It only accepts an answer if the model uses the expected tool-style response, which is a structured format rather than free-form prose.

**Data flow**: The report body and reader description go in. The function builds a model request containing the writing instructions, the report text, the reader context, a token limit, and a schema describing the required `DigestEntry` shape. It sends the request to the model. If the model returns the expected tool call, the function validates it into a `DigestEntry` and returns it; otherwise it returns `None`.

**Call relations**: `DigestWriter._digest` calls this after preparing the body and reader. `_written` uses the digest module’s writing standard and schema to shape the model request. Its result tells `_digest` whether to store a digest, store an unchanged marker, or leave the report for a future tick.

*Call graph*: called by 1 (_digest); 7 external calls (__init__, __init__, __init__, model_json_schema, model_validate, dumps, writing_standard).


##### `DigestWriter._store_unchanged`  (lines 280–286)

```
async def _store_unchanged(self, report: Report) -> None
```

**Purpose**: Records that a report was read and found not to contain a meaningful change. This prevents the same quiet report from being sent to the model again and again.

**Data flow**: A `Report` goes in. The function opens a database transaction and inserts the current workspace id and the report’s turn id into the `report_digest_unchanged` table. It returns nothing, but the database now remembers that this report is settled.

**Call relations**: `DigestWriter._digest` calls this when the model returns a valid digest answer whose `holds_a_change` flag is false. Later, `_unwritten` and `undigested_workspaces` use this marker to avoid treating the report as unfinished work.

*Call graph*: called by 1 (_digest); 1 external calls (insert).


##### `DigestWriter._store`  (lines 288–301)

```
async def _store(self, report: Report, entry: DigestEntry, reader: str) -> None
```

**Purpose**: Saves a completed digest entry for a report. This is the row the feed can later show instead of making readers open or understand the full report.

**Data flow**: A `Report`, a validated `DigestEntry`, and the reader description go in. The function opens a database transaction and inserts the workspace id, turn id, title, summary, bullet points, reader text, model name, and current timestamp into the digest entry table. It returns nothing, but the digest is now stored.

**Call relations**: `DigestWriter._digest` calls this when the model says the report contains a real change. Later candidate searches treat this stored row as proof that the report has already been read and should not be processed again.

*Call graph*: called by 1 (_digest); 2 external calls (now, insert).


##### `DigestRebuild.run`  (lines 321–339)

```
async def run(self) -> int
```

**Purpose**: Makes recent reports eligible to be digested again. This is useful when the digest rules or model behavior have changed and recent entries should be rebuilt from the original reports.

**Data flow**: It reads the current workspace id and defines the same recent time window the writer uses. Inside a database transaction, it deletes digest entries and unchanged markers for turns in that window. It returns the number of rows it deleted, combining both tables.

**Call relations**: This method stands beside the normal writer flow rather than being called by it. After it deletes the writer’s own previous results, the next scheduled `DigestWriter.run` can discover those reports again through `_unwritten` and recreate their entries.

*Call graph*: 3 external calls (now, delete, select).


##### `undigested_workspaces`  (lines 342–366)

```
def undigested_workspaces() -> sa.Select[tuple[UUID]]
```

**Purpose**: Builds a database query that finds workspaces with at least one recent scheduled markdown report still waiting for the digest writer. A scheduler can use this to avoid waking up workspaces that have nothing to do.

**Data flow**: No workspace id is passed in. The function constructs a SQL query over turns and shared artifacts, excluding reports that already have a digest entry or unchanged marker and limiting the search to the recent window. It returns the query object itself, not the query results.

**Call relations**: This is a helper for code outside this file that decides which workspaces need digest work. It mirrors the same “is this report due?” rules used by `DigestWriter._unwritten`, so the scheduler and the writer agree about what counts as unfinished.

*Call graph*: 2 external calls (now, select).


### Self-improvement corpus and proposals
The self-improvement extension builds failure-based evaluation corpora and proposes cautious prompt changes through its periodic runner.

### `extensions/self_improvement/ufo_ext_self_improvement/__init__.py`

`other` · `startup`

This file is the front door for the self-improvement extension package. It does not contain executable logic itself. Instead, it explains the extension’s role: it runs an offline replay and evaluation loop using saved trajectories, which are records of previous agent runs or workspace activity. In plain terms, it lets the system look back at what happened before, learn from those examples, and propose improvements to prompts.

The important safety idea is that the extension does not silently rewrite how the system behaves. It opens governed prompt changes, meaning suggested edits go through an approval process. A human member must review and approve them before they take effect. This matters because prompt changes can alter the system’s future behavior, so they should not happen automatically without oversight.

As an `__init__.py` file, it also tells Python that this directory should be treated as an importable package. Without it, other parts of the project may not be able to refer to this extension in the normal package-based way. Think of it like a label on a folder: it says what is inside and makes the folder usable by the larger system.


### `extensions/self_improvement/ufo_ext_self_improvement/corpus.py`

`domain_logic` · `self-improvement corpus building`

This file helps the self-improvement system decide what problems are worth working on. It uses a simple signal: if a past conversation contains a tool error, that conversation showed real friction. The failed tool becomes the “task class,” meaning the kind of problem the system may try to improve.

The file defines two small frozen data shapes. A TaskExample is one useful failed conversation: it stores the conversation ID, the first user request, the full message history, and a plain description of the tool problem. A TaskClass is a group of those examples for one failed tool. Each group is split into two parts: “mine,” which can be used to propose improvements, and “held_out,” which is kept separate for later checking. This is like studying from some practice questions while saving other similar questions for the final quiz.

The main flow starts with full trajectories, which are recorded conversations. The code finds the first real user request and the first tool result marked as an error. If either is missing, the trajectory is ignored. Useful trajectories are grouped by failed tool name, then small groups are dropped because they cannot be split safely. The remaining groups are sorted so the largest, most evidence-backed problem areas come first.

#### Function details

##### `first_request`  (lines 39–43)

```
def first_request(messages: tuple[Message, ...]) -> str | None
```

**Purpose**: Finds the first meaningful user request in a conversation. This gives the system the original task that later tool behavior should be judged against.

**Data flow**: It receives the full list of messages from a trajectory. It scans from the beginning until it finds a message from the user whose content is non-empty plain text. It returns that text, or returns nothing if no suitable user request exists.

**Call relations**: When bad_trajectory is deciding whether a past conversation is useful for learning, it calls first_request to get the grading key: what the user originally wanted. Without that request, the trajectory is skipped because there is no clear goal to evaluate against.

*Call graph*: called by 1 (bad_trajectory).


##### `first_tool_error`  (lines 46–65)

```
def first_tool_error(messages: tuple[Message, ...]) -> tuple[str, str] | None
```

**Purpose**: Finds the first tool call in a conversation that ended in an error, and reports which tool failed. This is the file’s main signal for deciding that a conversation is worth studying.

**Data flow**: It receives the conversation messages. First it reads tool-use blocks and remembers which tool name belongs to each tool-use ID. Then it scans tool-result blocks looking for the first one marked as an error. If it can match that error result back to a tool use, it returns the tool name and the error text. If no matching tool error is found, it returns nothing.

**Call relations**: bad_trajectory calls this before building a TaskExample. The returned tool name becomes the class label, such as a bucket for all failures from the same tool, and the error text becomes part of the problem description shown to the improvement proposer.

*Call graph*: called by 1 (bad_trajectory).


##### `bad_trajectory`  (lines 68–78)

```
def bad_trajectory(trajectory: Trajectory) -> tuple[str, TaskExample] | None
```

**Purpose**: Turns one past conversation into a learning example if it contains both a user request and a tool error. If the conversation is not useful for this purpose, it filters it out.

**Data flow**: It takes a Trajectory, which includes a conversation ID and all messages. It asks first_tool_error for the earliest failed tool round and first_request for the first user request. If either is missing, it returns nothing. Otherwise it creates a TaskExample containing the conversation ID, request, full messages, and a readable problem statement, then returns it together with a class name based on the failed tool.

**Call relations**: task_classes calls bad_trajectory for every trajectory it is given. bad_trajectory is the gatekeeper between raw transcripts and the cleaner corpus format used by the rest of this file.

*Call graph*: calls 2 internal fn (first_request, first_tool_error); called by 1 (task_classes); 1 external calls (__init__).


##### `task_classes`  (lines 81–94)

```
def task_classes(trajectories: tuple[Trajectory, ...]) -> tuple[TaskClass, ...]
```

**Purpose**: Builds the final set of task classes from many recorded conversations. It groups useful failed conversations by the tool that failed and prepares each group for learning and testing.

**Data flow**: It receives a tuple of trajectories. For each one, it calls bad_trajectory; ignored conversations disappear from the flow, while useful ones are placed into a list keyed by class name. It then calls _split for each group, drops any group too small to split, and returns the remaining TaskClass objects sorted by largest total example count first, then by name.

**Call relations**: This is the main public assembly function in the file. It coordinates the smaller helpers: bad_trajectory extracts useful examples, and _split turns each bucket of examples into a safe learn-versus-test split.

*Call graph*: calls 2 internal fn (_split, bad_trajectory).


##### `_split`  (lines 97–102)

```
def _split(name: str, examples: tuple[TaskExample, ...]) -> TaskClass | None
```

**Purpose**: Splits one group of examples into examples to learn from and examples to hold back for evaluation. It refuses groups that are too small to make that split meaningful.

**Data flow**: It receives a class name and all TaskExample objects for that class. If there are not enough examples, it returns nothing. Otherwise it sorts examples by conversation ID for stable, repeatable behavior, chooses a held-out count, and returns a TaskClass with held-out examples first and the remaining examples as the mine set.

**Call relations**: task_classes calls _split after grouping examples by failed tool. _split is the final shaping step that prevents the system from testing a proposed improvement on the exact same conversations it learned from.

*Call graph*: called by 1 (task_classes); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/cron.py`

`orchestration` · `scheduled self-improvement tick`

This file is the safety gate for automatic prompt improvement. A prompt is the instruction text an agent uses to decide how to act. The extension may discover a better prompt, but this code makes sure that discovery does not directly rewrite the agent. Instead, it creates a proposal that a person or governance process must approve.

On each scheduled tick, `ImproveCron` gathers recorded agent runs, called trajectories. A trajectory is a saved trace of what an agent did in a conversation. The code groups those traces by agent, then works through each agent separately.

For each agent, it checks whether there is already a stored candidate prompt tied to the agent’s current prompt version. That stored record is important: it prevents the system from opening the same suggestion again and again. If there is no active candidate, the file asks a `PromptProposer` to suggest a new prompt based on one task class found in the agent’s history.

Then the candidate is tested. The evaluator replays selected held-out examples, meaning examples kept aside as a small exam rather than used to create the suggestion. If the candidate fails, it is marked rejected. If it passes, it must keep passing for a configured number of cron ticks before anything is proposed. This is like requiring a student to pass the same safety exam more than once before graduating. Only after those stable passes does the code call `ctx.propose_change`, recording a proposal rather than changing the agent directly.

#### Function details

##### `ImproveCron.run`  (lines 50–52)

```
async def run(self) -> None
```

**Purpose**: This is the top-level scheduled action for the self-improvement loop. It fetches all available trajectories, groups them by agent, and asks the rest of the file to advance each agent’s candidate prompt, if any.

**Data flow**: It starts with no direct input besides the cron object’s context. It reads trajectories from `ctx.trajectories()`, groups them by agent, and then passes each agent ID plus that agent’s trajectories onward. It returns nothing; its effect is to drive candidate creation, evaluation, and possible proposal creation for each agent.

**Call relations**: This function begins the flow. It uses `_by_agent` to sort the raw trajectory list into per-agent bundles, then calls `ImproveCron._advance` once for each bundle so that each agent is considered independently.

*Call graph*: calls 2 internal fn (_advance, _by_agent).


##### `ImproveCron._advance`  (lines 54–60)

```
async def _advance(self, agent_id: UUID, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This moves one agent one step through the improvement process. It finds or opens a candidate prompt for the agent’s current prompt version, then runs the safety gate on that candidate.

**Data flow**: It receives an agent ID and that agent’s trajectories. It reads the prompt digest from the first trajectory, using that digest as the version marker for the current prompt. It builds the storage key for this agent, asks `_active_or_open` for a candidate, and if a candidate exists, sends it to `_gate`. It returns nothing; it may cause stored candidate state to change or a governed proposal to be opened.

**Call relations**: It is called by `ImproveCron.run` for each agent. It first delegates candidate lookup or creation to `ImproveCron._active_or_open`; only if that produces an active candidate does it hand the work to `ImproveCron._gate` for evaluation and possible promotion.

*Call graph*: calls 2 internal fn (_active_or_open, _gate); called by 1 (run).


##### `ImproveCron._active_or_open`  (lines 62–83)

```
async def _active_or_open(self, key: str, from_digest: str, trajectories: tuple[Trajectory, ...]) -> CandidateState | None
```

**Purpose**: This finds the current candidate prompt for an agent, or creates one if it is safe and useful to do so. It also prevents already promoted or rejected candidates from being proposed again for the same prompt version.

**Data flow**: It receives a store key, the digest of the agent’s current prompt, and that agent’s trajectories. It first reads the scoped store. If the store already contains a candidate for the same prompt digest, it returns that candidate only if it is still evaluating; if it was already promoted or rejected, it returns nothing. If there is no usable existing candidate, it looks for task classes in the trajectories, asks the proposer for a new prompt, stores the new `CandidateState`, and returns it.

**Call relations**: It is called by `ImproveCron._advance` before any evaluation happens. It uses `task_classes` to find meaningful groups of work to improve from, creates a `CandidateState` when a proposal is available, and leaves later pass/fail decisions to `ImproveCron._gate`.

*Call graph*: called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._gate`  (lines 85–117)

```
async def _gate(self, agent_id: UUID, key: str, from_digest: str, candidate: CandidateState, trajectories: tuple[Trajectory, ...]) -> None
```

**Purpose**: This is the safety test for a candidate prompt. It evaluates the candidate against held-out examples, counts repeated successful ticks, and only opens a governed change proposal after enough consecutive passes.

**Data flow**: It receives the agent ID, storage key, current prompt digest, candidate state, and trajectories. It builds two sets of examples: the candidate’s own held-out examples and held-out examples from other task classes as a broader safety check. It sends those to the evaluator along with the candidate prompt and current prompt. If evaluation fails, it saves the candidate as rejected with zero passes. If evaluation passes but has not passed enough times yet, it saves the higher pass count. If the pass count reaches the stability requirement, it creates an `AgentChange` proposal and saves the candidate as promoted with the proposal ID.

**Call relations**: It is called by `ImproveCron._advance` after a candidate has been found or opened. It calls `_held_out` to recover usable held-out examples from trajectories, uses `task_classes` to gather broader examples, calls the evaluator to judge the candidate, calls `ctx.propose_change` when promotion is allowed, and uses `ImproveCron._save` for every stored state update.

*Call graph*: calls 2 internal fn (_save, _held_out); called by 1 (_advance); 2 external calls (__init__, task_classes).


##### `ImproveCron._save`  (lines 119–131)

```
async def _save(self, key: str, candidate: CandidateState, *, status: CandidateStatus, gate_passes: int, proposal_id: str | None=None) -> None
```

**Purpose**: This writes an updated candidate record back to the extension’s store. It keeps the candidate’s main details the same while changing its status, pass count, and optional proposal ID.

**Data flow**: It receives a storage key, the current candidate object, a new status, a new gate-pass count, and possibly a proposal ID. It copies the candidate with those updated fields, converts it to plain JSON-friendly data, and writes it to `ctx.store`. It returns nothing; the lasting output is the updated stored record.

**Call relations**: It is used by `ImproveCron._gate` whenever an evaluation result changes the candidate’s state. It relies on `CandidateState.model_copy` to make the updated record cleanly rather than mutating the original object in place.

*Call graph*: called by 1 (_gate); 1 external calls (model_copy).


##### `_by_agent`  (lines 134–138)

```
def _by_agent(trajectories: tuple[Trajectory, ...]) -> Mapping[UUID, tuple[Trajectory, ...]]
```

**Purpose**: This sorts a mixed list of trajectories into separate bundles for each agent. The improvement loop needs this so that one agent’s prompt history is not mixed with another’s.

**Data flow**: It receives a tuple of trajectories. It reads each trajectory’s agent ID, collects trajectories with the same agent ID together, and returns a mapping from each agent ID to that agent’s tuple of trajectories. It does not change the trajectories themselves.

**Call relations**: It is called by `ImproveCron.run` immediately after trajectories are fetched. Its output shapes the rest of the cron run: each grouped bundle is passed to `ImproveCron._advance` for independent processing.

*Call graph*: called by 1 (run).


##### `_held_out`  (lines 141–153)

```
def _held_out(trajectories: tuple[Trajectory, ...], held_out: tuple[str, ...]) -> tuple[TaskExample, ...]
```

**Purpose**: This rebuilds the candidate’s held-out test examples from the available trajectories. It only includes examples that are currently recognized as bad trajectories, because those are the cases the improvement is meant to fix or learn from.

**Data flow**: It receives all trajectories for an agent and a tuple of held-out conversation IDs saved in the candidate state. It makes a lookup table from conversation ID to trajectory, walks through the saved IDs, skips any missing trajectory, and asks `bad_trajectory` whether the found trajectory represents a useful failing example. It returns a tuple of `TaskExample` objects for the examples that qualify.

**Call relations**: It is called by `ImproveCron._gate` when preparing the evaluator’s input. It uses `bad_trajectory` to turn raw trajectories into task examples, then hands those examples back to the gate so the evaluator can test the candidate prompt.

*Call graph*: called by 1 (_gate); 1 external calls (bad_trajectory).


### `extensions/self_improvement/ufo_ext_self_improvement/model.py`

`io_transport` · `cross-cutting`

The self-improvement extension needs to ask a language model for help in several places, such as proposing changes, replaying past behavior, and grading results. This file is the shared doorway for those requests. Without it, each part of the extension would need to know the details of how to build a model request, which would make the code more repeated and easier to get wrong.

The file defines two small promises, called protocols: `ModelLeg` for “give me a text completion” and `ReplayLeg` for “take one conversation turn, possibly with tools.” A protocol is like saying, “anything with this method can be used here,” without caring about the exact class behind it.

`ModelAccessLeg` is the real adapter. It wraps the SDK’s `ModelAccess`, which is the project’s metered model connection. “Metered” means calls are tracked or limited, like using a taxi meter. When asked to complete or take a turn, it builds a `ModelRequest` with shared settings: the selected model, the system instructions, the message history, a 2048-token output limit, a short conversation cache, and reasoning turned off. For tool-based turns, it also includes the available tool descriptions.

In short, this file keeps model use consistent across the extension and gives the rest of the code a narrow, predictable interface.

#### Function details

##### `ModelLeg.complete`  (lines 13–13)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This defines the shape of a simple model text request. Any object that follows `ModelLeg` must be able to take system instructions and prior messages, then return a string response.

**Data flow**: It receives a system prompt and a tuple of conversation messages. An implementing object sends those to a model or replay source, then produces plain text as the result. This protocol itself does not perform the work; it describes what the work must look like.

**Call relations**: Other parts of the extension can depend on `ModelLeg` when they only need text back from a model. `ModelAccessLeg.complete` is one concrete method that satisfies this promise.


##### `ReplayLeg.turn`  (lines 17–19)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This defines the shape of a model conversation turn that can include tools. Any object that follows `ReplayLeg` must be able to take instructions, prior messages, and tool descriptions, then return the model's next message.

**Data flow**: It receives the system prompt, the conversation so far, and the tools the model may use. An implementing object uses those inputs to produce one `Message`, which may be normal text or a tool-related response depending on the model system. The protocol only states the expected behavior; it does not run anything itself.

**Call relations**: Replay or grading code can depend on `ReplayLeg` when it needs to simulate or request the next model turn. `ModelAccessLeg.turn` provides the real SDK-backed version of this behavior.


##### `ModelAccessLeg.complete`  (lines 28–38)

```
async def complete(self, system: str, messages: tuple[Message, ...]) -> str
```

**Purpose**: This sends a plain text request to the SDK model connection and returns the model's text answer. It is used when the caller wants a completion without tool-calling behavior.

**Data flow**: It starts with system instructions and a tuple of messages. It wraps them in a `ModelRequest`, adding the chosen model name, a maximum output size, a short cache lifetime, and the setting that turns extra reasoning mode off. It then passes that request to the underlying SDK `complete` method and returns the resulting string.

**Call relations**: This is the concrete implementation behind the `ModelLeg.complete` promise. When extension code asks this adapter for a completion, this method builds the SDK request and hands it to `ModelAccess`; creating the `ModelRequest` is the main handoff point to the wider SDK.

*Call graph*: 1 external calls (__init__).


##### `ModelAccessLeg.turn`  (lines 40–53)

```
async def turn(self, system: str, messages: tuple[Message, ...], tools: tuple[ToolSchema, ...]) -> Message
```

**Purpose**: This asks the SDK model connection for one full conversation turn, including the option to use tools. It is used when replay or evaluation needs the model's next structured message rather than just raw text.

**Data flow**: It receives system instructions, previous messages, and tool schemas, which are descriptions of tools the model is allowed to call. It packages them into a `ModelRequest` with the selected model, output limit, cache setting, and reasoning disabled. It sends that request to the SDK `turn` method and returns the resulting `Message`.

**Call relations**: This is the concrete implementation behind the `ReplayLeg.turn` promise. Callers that need a tool-aware model turn use this adapter, and the method hands the fully prepared `ModelRequest` to the underlying `ModelAccess` object.

*Call graph*: 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/proposer.py`

`domain_logic` · `self-improvement proposal phase`

This file is one step in a self-improvement loop. Imagine a coach reviewing moments where an assistant got stuck, then suggesting a small edit to the assistant’s instruction sheet so it does better next time. That is what `PromptProposer` does.

It receives the agent’s current system prompt, which is the high-level instruction text that guides the agent’s behavior, and a `TaskClass`, which groups together similar tasks where problems were found. If there are no mined examples of trouble, it stops immediately, because there is nothing concrete to learn from.

When examples do exist, it builds a clear request for another model: here is the task class, here is the current prompt, and here are a few shortened examples showing the user request and what went wrong. The fixed `PROPOSER_SYSTEM` instruction tells the model to preserve the prompt’s general role and style, make the smallest helpful change, and return only the full revised prompt.

After the model replies, the file cleans up common formatting mistakes, such as wrapping the answer in code fences. If the reply is empty or exactly the same as the original prompt, it treats that as no proposal. Otherwise, it returns a `PromptCandidate`, which pairs the task class name with the revised prompt text.

#### Function details

##### `PromptProposer.propose`  (lines 33–43)

```
async def propose(self, current_prompt: str, task_class: TaskClass) -> PromptCandidate | None
```

**Purpose**: This is the main entry point for creating a possible prompt improvement. It looks at one task class the agent struggled with, asks a model for a revised system prompt, and only returns a candidate if the model produced a real change.

**Data flow**: It takes the current prompt text and a task class containing mined problem examples. If the task class has no examples, it returns nothing. Otherwise, it builds a user message with `_prompt`, sends that message plus the fixed proposer instructions to the model, cleans the model’s answer with `_clean`, compares it with the original prompt, and finally returns a `PromptCandidate` containing the task name and new prompt text if the answer is usable.

**Call relations**: This function drives the whole file’s flow. It calls `_prompt` to prepare the material the model needs, wraps that material in a `Message` for the model call, then calls `_clean` to normalize the model’s reply before deciding whether to create a `PromptCandidate`.

*Call graph*: calls 2 internal fn (_prompt, _clean); 2 external calls (__init__, __init__).


##### `PromptProposer._prompt`  (lines 45–56)

```
def _prompt(self, current_prompt: str, task_class: TaskClass) -> str
```

**Purpose**: This builds the actual instruction text shown to the model as the user message. It packages the task class name, the current system prompt, and a limited set of failure examples into a format the model can easily follow.

**Data flow**: It receives the current prompt and the task class. It reads the task class name and its mined examples, trims each request and problem description to the configured character limit, includes only the configured maximum number of examples, and returns one combined text block asking for the full revised system prompt.

**Call relations**: It is called by `PromptProposer.propose` just before the model is asked for a rewrite. Its output becomes the content of the user-facing `Message` sent to the model, so it controls what evidence and context the model sees.

*Call graph*: called by 1 (propose).


##### `_clean`  (lines 59–68)

```
def _clean(text: str) -> str
```

**Purpose**: This tidies the model’s reply so the rest of the system can compare and store plain prompt text. It mainly removes extra whitespace and strips off Markdown code fences if the model added them despite being told not to.

**Data flow**: It takes raw text returned by the model. It trims leading and trailing whitespace, checks whether the answer starts with a triple-backtick code block, removes the opening and closing fence lines when present, trims again, and returns the cleaned prompt body.

**Call relations**: It is called by `PromptProposer.propose` after the model completes. Its cleaned result is what `propose` uses to decide whether the model produced an empty answer, an unchanged prompt, or a real `PromptCandidate`.

*Call graph*: called by 1 (propose).


### Prompt replay and gating
Candidate prompts are replayed against archived tasks, judged for quality, and accepted only when the evidence clears the promotion gate.

### `extensions/self_improvement/ufo_ext_self_improvement/evaluation.py`

`domain_logic` · `self-improvement evaluation`

This file is the evidence-gathering step for self-improvement. When the system has a possible better prompt, it cannot simply trust that it sounds better. It must check whether the prompt helps on real tasks and does not hurt elsewhere. The file does that by replaying held-out examples: saved user requests with their recorded conversation context and tool results. Think of it like testing two recipes in the same kitchen with the same ingredients, so the recipe is the only thing being compared.

The main class, CandidateEvaluation, compares two prompt “arms”: absent means the current prompt, and present means the candidate prompt. For each saved task, it reruns the agent once with the current prompt and once with the candidate prompt. Then it asks a separate judge model to grade whether the final answer satisfies the original request. Each run becomes an OutcomeLabel saying which prompt was used and whether it succeeded.

There are two sets of examples. The local set checks the task type the candidate was meant to improve. The global set checks other task types, to catch harmful side effects. Finally, those labels go to two_stage_gate, which applies the acceptance rules. If this file were missing, prompt changes could be accepted based on weak or one-sided evidence instead of controlled comparison.

#### Function details

##### `CandidateEvaluation.evaluate`  (lines 24–33)

```
async def evaluate(self, candidate_prompt: str, current_prompt: str, local_held_out: tuple[TaskExample, ...], global_held_out: tuple[TaskExample, ...]=()) -> GateVerdict
```

**Purpose**: This is the top-level check for a candidate prompt. It compares the candidate against the current prompt on local examples and global safety examples, then asks the gate for the final pass-or-fail verdict.

**Data flow**: It receives the candidate prompt, the current prompt, and two groups of saved task examples. It turns each group into success-or-failure labels by calling _labels. It then gives the local labels and global labels to two_stage_gate, which returns a GateVerdict saying whether the candidate passed the improvement test.

**Call relations**: This method starts the evaluation flow. It relies on _labels to produce fair side-by-side evidence for each example, then hands that evidence to the external two_stage_gate function, which makes the final decision.

*Call graph*: calls 1 internal fn (_labels); 1 external calls (two_stage_gate).


##### `CandidateEvaluation._labels`  (lines 35–45)

```
async def _labels(self, candidate_prompt: str, current_prompt: str, held_out: tuple[TaskExample, ...]) -> tuple[OutcomeLabel, ...]
```

**Purpose**: This helper reruns each held-out task twice: once with the current prompt and once with the candidate prompt. It records whether each resulting answer was accepted by the judge.

**Data flow**: It receives both prompts and a tuple of saved task examples. It creates a ReplayEvaluation object to rerun the agent under controlled conditions. For every example, it replays the task with the current prompt and with the candidate prompt, sends each final answer to _accepts for grading, and collects OutcomeLabel records. It returns those labels as a tuple.

**Call relations**: CandidateEvaluation.evaluate calls this once for the local examples and once for the global examples. Inside the loop, _labels uses ReplayEvaluation to regenerate answers and _accepts to judge them, then packages the results as OutcomeLabel objects for the gate to inspect later.

*Call graph*: calls 1 internal fn (_accepts); called by 1 (evaluate); 2 external calls (__init__, __init__).


##### `CandidateEvaluation._accepts`  (lines 47–59)

```
async def _accepts(self, request: str, answer: str) -> bool
```

**Purpose**: This helper asks the judge model whether an answer correctly satisfies the original user request. It is deliberately strict about the judge response: only a valid JSON object with accepted set to true counts as success.

**Data flow**: It receives the original request and the generated answer. It builds a user message containing both, sends it to the judge model with grading instructions, and gets back text. It looks for a JSON object inside that text, tries to parse it, and returns true only if the parsed object says {"accepted": true}. If the judge gives malformed text or anything unclear, the result is false.

**Call relations**: _labels calls this after each replayed answer is produced. This function is the bridge between raw generated text and the simple success-or-failure labels that the rest of the evaluation process needs.

*Call graph*: called by 1 (_labels); 2 external calls (__init__, loads).


### `extensions/self_improvement/ufo_ext_self_improvement/gate.py`

`domain_logic` · `self-improvement evaluation before prompt promotion`

This file is the promotion gate for self-improvement. Imagine testing a new recipe against an old one: you do not switch the whole kitchen just because the new recipe won once or twice. You want enough taste tests, and you want to be sure the new recipe does not ruin other dishes. Here, the “recipe” is a candidate prompt, and the taste tests are replayed tasks judged as accepted or not accepted.

The file records each replay as an OutcomeLabel: whether the candidate prompt was present, and whether the result succeeded. It then groups those labels into a Contingency, which counts successes and totals for the candidate side and the current-prompt side.

The core question is “how much better is the candidate?” That improvement is called lift: candidate acceptance rate minus old acceptance rate. Because small samples can be misleading, the file does not trust the raw difference alone. It builds confidence bounds, meaning cautious best- and worst-case estimates based on the amount of evidence. The local gate requires the lower, cautious estimate of improvement to clear a fixed floor, with enough examples on both sides.

There is also a global safety check. A prompt may improve one task class but damage others. The two-stage gate first asks whether the candidate clearly helps the targeted task, then asks whether there is strong evidence it regresses the remaining tasks. If the local win is not convincing, or the global check finds clear harm, the prompt does not promote.

#### Function details

##### `wilson_lower_bound`  (lines 53–60)

```
def wilson_lower_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious lower estimate of a success rate. Someone uses it when they know how many tries succeeded out of a total and want to avoid being fooled by a lucky small sample.

**Data flow**: It takes a number of accepted results, a total number of results, and an optional confidence setting. If there are no results, it returns 0. Otherwise it calculates the observed success rate, adjusts it for uncertainty, and returns the lower end of that uncertainty range, never below 0.

**Call relations**: The lift calculations call this when they need the pessimistic side of one prompt arm’s success rate. It uses square root math to compute the uncertainty width, then hands that cautious rate back to lift_lower_bound or lift_upper_bound.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `wilson_upper_bound`  (lines 63–70)

```
def wilson_upper_bound(accepted: int, total: int, z: float=WILSON_Z_95) -> float
```

**Purpose**: This function gives a cautious upper estimate of a success rate. It is the optimistic counterpart to wilson_lower_bound and is used when the code needs to know how good an arm could plausibly be.

**Data flow**: It takes accepted count, total count, and an optional confidence setting. With no data, it returns 1, meaning the rate could still be anything up to perfect. With data, it calculates the observed success rate, adds an uncertainty allowance, and returns the upper end, capped at 1.

**Call relations**: The lift calculations call this when comparing the candidate prompt with the old prompt. It supplies the optimistic rate for one side of the comparison, using square root math to size the uncertainty.

*Call graph*: called by 2 (lift_lower_bound, lift_upper_bound); 1 external calls (sqrt).


##### `lift_lower_bound`  (lines 73–84)

```
def lift_lower_bound(cont: Contingency) -> float
```

**Purpose**: This function answers the strict question: “How much better is the candidate prompt at minimum, after accounting for uncertainty?” It is used to decide whether the local improvement is convincing enough to promote.

**Data flow**: It takes a Contingency count table with successes and totals for candidate-present and candidate-absent runs. If either side has no examples, it returns 0 because no fair comparison is possible. Otherwise it compares the two observed success rates and subtracts an uncertainty penalty built from Wilson confidence bounds, producing a cautious lower estimate of the lift.

**Call relations**: score_gate calls this after turning replay labels into counts. This function depends on wilson_lower_bound and wilson_upper_bound to measure uncertainty on both sides, then returns the number that the local gate tests against its promotion floor.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (score_gate); 1 external calls (sqrt).


##### `lift_upper_bound`  (lines 87–98)

```
def lift_upper_bound(cont: Contingency) -> float
```

**Purpose**: This function answers the generous question: “How good could the candidate’s lift plausibly be?” It is mainly used for the safety check, where the system rejects only if even the optimistic estimate shows meaningful harm.

**Data flow**: It takes the same Contingency count table. If either prompt arm has no examples, it returns 0. Otherwise it compares the observed rates and adds an uncertainty allowance, producing the upper end of the plausible lift range.

**Call relations**: global_non_inferior calls this during the global regression check. It uses wilson_lower_bound and wilson_upper_bound to estimate the best plausible lift; if even this optimistic value is too negative, the candidate is considered harmful.

*Call graph*: calls 2 internal fn (wilson_lower_bound, wilson_upper_bound); called by 1 (global_non_inferior); 1 external calls (sqrt).


##### `contingency`  (lines 101–109)

```
def contingency(labels: tuple[OutcomeLabel, ...]) -> Contingency
```

**Purpose**: This function turns individual replay outcomes into a simple four-number summary. It separates examples where the candidate prompt was present from examples where it was absent, then counts successes and totals for each side.

**Data flow**: It takes a tuple of OutcomeLabel records. It splits them into present and absent groups, counts how many succeeded in each group, counts how many examples each group contains, and returns a Contingency object with those counts.

**Call relations**: score_gate and global_non_inferior call this before doing statistical comparisons. It is the counting step that prepares raw replay labels for the lift-bound functions.

*Call graph*: called by 2 (global_non_inferior, score_gate); 1 external calls (__init__).


##### `score_gate`  (lines 112–139)

```
def score_gate(labels: tuple[OutcomeLabel, ...], lower_bound: float=LIFT_LOWER_BOUND, n_floor: int=N_FLOOR) -> GateVerdict
```

**Purpose**: This function makes the local promotion decision for the task class the candidate was meant to improve. It requires both enough replay examples and a cautious lower-bound improvement above the configured floor.

**Data flow**: It receives replay labels plus optional thresholds for minimum lift and minimum examples per side. It summarizes the labels with contingency, computes the cautious lower lift with lift_lower_bound, then returns a GateVerdict. The verdict says whether the gate passed, why, the calculated lower bound, and how many examples were on each side.

**Call relations**: two_stage_gate calls this first. If score_gate returns a failed verdict, the larger process stops there. If it passes, two_stage_gate moves on to the global safety check.

*Call graph*: calls 2 internal fn (contingency, lift_lower_bound); called by 1 (two_stage_gate); 1 external calls (__init__).


##### `global_non_inferior`  (lines 142–154)

```
def global_non_inferior(labels: tuple[OutcomeLabel, ...], margin: float=GLOBAL_REGRESSION_MARGIN, n_floor: int=N_FLOOR) -> bool
```

**Purpose**: This function checks whether the candidate prompt appears safe on other task classes. It is deliberately tolerant of weak evidence: it blocks only when there is confident evidence of a meaningful regression.

**Data flow**: It receives replay labels for the broader, held-out task set plus optional margin and sample-size settings. It summarizes the labels into counts. If either side has too few examples, it returns true, meaning “do not block on thin evidence.” Otherwise it computes the optimistic upper bound of lift and returns whether that value is not worse than the allowed negative margin.

**Call relations**: two_stage_gate calls this only after the local gate has passed. It relies on contingency for counts and lift_upper_bound for the optimistic comparison, then tells the full gate whether the candidate should be blocked for global harm.

*Call graph*: calls 2 internal fn (contingency, lift_upper_bound); called by 1 (two_stage_gate).


##### `two_stage_gate`  (lines 157–174)

```
def two_stage_gate(local_labels: tuple[OutcomeLabel, ...], global_labels: tuple[OutcomeLabel, ...]) -> GateVerdict
```

**Purpose**: This function gives the final promote-or-reject verdict. It combines a local improvement test with a global safety test, so a candidate must both help its target area and avoid clearly hurting other areas.

**Data flow**: It takes one set of labels for the local task class and another set for global held-out tasks. It first runs score_gate on the local labels. If that fails, it returns that failure verdict. If local scoring passes, it runs global_non_inferior. A global failure produces a new rejection verdict explaining the regression; otherwise it returns the successful local verdict.

**Call relations**: This is the top-level decision function in the file. It coordinates score_gate and global_non_inferior in order, using GateVerdict to report the final outcome to whatever promotion workflow called into this gate.

*Call graph*: calls 2 internal fn (global_non_inferior, score_gate); 1 external calls (__init__).


### `extensions/self_improvement/ufo_ext_self_improvement/replay.py`

`domain_logic` · `offline self-improvement evaluation`

This file solves a careful testing problem: how can you ask, “Would a different prompt have produced a better final answer?” without letting the agent take new actions in the outside world. It does this by replaying only the model parts of an archived task. If the model asks for a tool, the file does not execute that tool. Instead, it looks up the same tool call from the old conversation and feeds back the same saved result.

The replay starts by trimming away the original final assistant answer, so the model must produce a new one. It also removes saved reasoning blocks, because those belong to the original model run and may not be accepted in a new model call. Then it builds a small fake tool catalog from the tools that appeared in the archive. This catalog is permissive: it mainly tells the model which tool names exist, while the old conversation shows the expected call shapes.

During replay, the model is called round by round. If it gives a final text answer, that answer is returned. If it requests tools that match archived calls, the saved results are fed back. If it asks for a tool call that was not in the archive, the replay stops and returns the best text seen so far. This keeps the replay side-effect-free, like using a flight simulator instead of flying a real plane.

#### Function details

##### `_canonical_input`  (lines 40–41)

```
def _canonical_input(value: object) -> str
```

**Purpose**: This function turns a tool input into a stable text key. It is used so two tool calls with the same data can be matched even if their object fields were originally ordered differently.

**Data flow**: It receives any input value, usually the argument object passed to a tool. It converts that value to JSON text with sorted keys and compact spacing. The result is a predictable string that can be used as part of a lookup key.

**Call relations**: When archived tool calls are indexed, archived_tool_results uses this function to store each call under a stable name-and-input key. Later, _feed_archived uses the same conversion on a replayed tool call so it can find the matching saved result.

*Call graph*: called by 2 (_feed_archived, archived_tool_results); 1 external calls (dumps).


##### `replay_head`  (lines 44–60)

```
def replay_head(messages: tuple[Message, ...]) -> tuple[Message, ...]
```

**Purpose**: This function prepares the archived conversation for replay by removing the original final answer. It keeps the useful context, including prior user turns and tool exchanges, so the new prompt can generate its own ending.

**Data flow**: It receives the full archived message history. Starting from the end, it drops trailing assistant messages that are final answers rather than tool requests. It then removes model reasoning blocks from the remaining messages and returns the cleaned conversation head.

**Call relations**: ReplayEvaluation.replay calls this near the start of a replay to create the message history sent to the model. replay_head delegates the cleanup of reasoning content to _without_reasoning for each kept message.

*Call graph*: calls 1 internal fn (_without_reasoning); called by 1 (replay).


##### `_without_reasoning`  (lines 63–71)

```
def _without_reasoning(message: Message) -> Message
```

**Purpose**: This function removes hidden or model-specific reasoning blocks from one message. That matters because replay is meant to run cleanly with reasoning off, and old reasoning artifacts may be rejected by the model provider.

**Data flow**: It receives one message. If the message is plain text, it returns it unchanged. If the message is made of content blocks, it filters out thinking or reasoning blocks and builds a new message with the same role and the remaining visible content.

**Call relations**: replay_head calls this after trimming the conversation. It acts as the final cleaning step before ReplayEvaluation.replay sends the archived context into the new model run.

*Call graph*: called by 1 (replay_head); 1 external calls (__init__).


##### `archived_tool_results`  (lines 74–96)

```
def archived_tool_results(messages: tuple[Message, ...]) -> dict[tuple[str, str], ToolResultBlock]
```

**Purpose**: This function builds a lookup table of saved tool results from the archived conversation. Its job is to make sure replayed tool calls get the same answers the original run received, instead of touching real tools again.

**Data flow**: It receives the archived messages. First it collects tool result blocks by their tool-use identifier. Then it finds each tool-use block, pairs it with its saved result, and stores that result under a key made from the tool name and canonicalized input. It returns this lookup table.

**Call relations**: ReplayEvaluation.replay calls this before the replay loop begins. The lookup it creates is later passed to _feed_archived, which uses it to answer the model’s replayed tool requests.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay).


##### `replay_tools`  (lines 99–116)

```
def replay_tools(messages: tuple[Message, ...]) -> tuple[ToolSchema, ...]
```

**Purpose**: This function creates the fake tool list shown to the model during replay. It includes only the tool names that appeared in the archived run, so the model is nudged to follow the same tool path.

**Data flow**: It receives the archived messages and scans them for tool-use blocks. For each distinct tool name, it creates a simple tool schema with a broad input shape that accepts object-like arguments. It returns all of these schemas as the replay tool catalog.

**Call relations**: ReplayEvaluation.replay calls this during setup, before asking the model for new turns. The returned tool schemas are passed into the model leg so the model knows which archived tools it may call.

*Call graph*: called by 1 (replay); 1 external calls (__init__).


##### `_feed_archived`  (lines 119–135)

```
def _feed_archived(tool_uses: tuple[ToolUseBlock, ...], results: Mapping[tuple[str, str], ToolResultBlock]) -> Message | None
```

**Purpose**: This function answers the model’s tool requests using saved archive data. If every requested call matches the old run, it creates one user message containing the corresponding tool results; if any call is new, it signals that replay has diverged.

**Data flow**: It receives the tool calls requested in the current replay turn and the saved result lookup. For each call, it builds the same name-and-input key used during indexing. If a matching archived result exists, it copies that result content into a new tool result block tied to the new call id. If any match is missing, it returns None. Otherwise, it returns a user message containing all copied tool results.

**Call relations**: ReplayEvaluation.replay calls this whenever the model asks for tools. A returned message is appended to the replay conversation so the model can continue; a None return tells the replay loop to stop because the model has left the archived path.

*Call graph*: calls 1 internal fn (_canonical_input); called by 1 (replay); 2 external calls (__init__, __init__).


##### `ReplayEvaluation.replay`  (lines 148–169)

```
async def replay(self, archived: tuple[Message, ...], system_prompt: str) -> ReplayResult
```

**Purpose**: This is the main replay flow for one archived task and one candidate system prompt. It reruns the model’s part of the conversation, reuses old tool results, and returns the final text produced under the new prompt.

**Data flow**: It receives the archived conversation and the system prompt to test. It builds the saved tool-result lookup, the replay tool catalog, and the cleaned conversation head. Then it repeatedly asks the model for the next assistant turn. If the model returns only text, that text becomes the replay result. If the model requests tools, the method tries to feed matching archived results back into the conversation. If the model asks for an unmatched tool call or the round limit is reached, it returns the latest useful text it has seen.

**Call relations**: This method ties together all helpers in the file. It calls archived_tool_results, replay_tools, and replay_head for setup, then calls _feed_archived inside the turn-by-turn loop. It is the function outside code would use when it wants to evaluate how a different prompt behaves on a saved task.

*Call graph*: calls 4 internal fn (_feed_archived, archived_tool_results, replay_head, replay_tools); 1 external calls (__init__).
