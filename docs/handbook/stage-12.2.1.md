# Document Review State and Artifact Annotations  `stage-12.2.1`

This stage is the record keeper and feedback writer for document review. It is shared support used while a review is in progress and after findings have been created. The small package file only makes the scripts importable by Python. The constants file keeps the standard filenames for the saved review state and the audit log, so every script looks in the same place.

The models file defines what a “review issue” looks like: for example, the problem type, where it was found, and the comment text to show a user. The manage_state command-line tool is the control desk. It records each review step, saves progress in a JSON file, and writes a log of changes so the work can be traced later.

The annotation scripts then put the review back into the original documents. The PDF tool highlights matching text and adds sticky-note comments. The PowerPoint tool inserts findings as slide comments. The Excel tool finds matching cells and adds spreadsheet comments, creating an annotated copy.

## Files in this stage

### Shared Review Foundations
Package setup and shared constants/models establish the common files and issue format used by the review scripts.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `import/setup`

This is an empty Python package marker file. In Python, a file named `__init__.py` tells the interpreter that the folder should be treated as an importable package, a bit like putting a label on a drawer so the rest of the program knows it can look inside. Here, it sits inside the document-review skill's `scripts` folder. That means code elsewhere can refer to this folder using Python import paths, even though this particular file does not define any functions, classes, or settings. Without it, some Python environments or tooling might not recognize the folder as a package, which could make script imports less reliable. Its value is structural rather than behavioral: it helps organize the project and makes the surrounding scripts discoverable.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a small configuration file. It does not run any review logic itself. Instead, it acts like a label maker for the document review system. The system needs to write down two kinds of information: its current state, so it can remember progress, and a log, so it can keep a record of what happened during review. This file defines the exact filenames for those two records: `document_review_state.json` and `review_log.jsonl`.

The first name points to a JSON file, which is a plain text format often used to store structured data such as status, settings, or progress. The second name ends in `.jsonl`, meaning “JSON lines”: each line is its own JSON record, which is useful for logs that grow one event at a time.

Without this file, each script might hard-code these filenames separately. That would make mistakes more likely and future changes harder. With this file, the rest of the document review code can refer to the shared constants instead of repeating the raw text.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review output formatting`

This file is a small shared vocabulary for the document-review feature. A review process produces issues, and each issue needs the same basic facts: what kind of problem it is, how serious it is, where it appears, what text was found, and what replacement text may be suggested. The `DocumentIssue` type is like a checklist for those facts, so other code can pass review findings around without guessing what fields should exist.

The file also contains friendly labels for issue types. For example, an internal code like `spelling_grammar` becomes the more readable label `Spelling/Grammar`. That matters because these issues are shown to people, not just machines.

The main behavior is `format_comment`, which builds the text of a review comment. It starts with a header showing the issue type and severity, then adds the issue description. If a suggested replacement is available and suggestions are enabled, it adds a `Suggested:` line. Without this file, other parts of the review tool would have to invent their own issue structure and comment wording, which could lead to inconsistent or confusing review output.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns one document review issue into a plain text comment that can be shown to a user. It makes internal issue codes readable and optionally includes the suggested replacement text.

**Data flow**: It receives an `issue`, which is a dictionary-like record containing fields such as issue type, severity, description, and suggested new text. It looks up a friendly label for the issue type, reads the severity and description, and then builds a multi-line comment. If `include_suggestion` is true and the issue has `new_text`, it adds that suggestion before returning the finished string.

**Call relations**: When another part of the document-review flow is ready to present an issue, it can call `format_comment` to get user-facing comment text. Inside the function, it uses the issue record’s `get` method to safely check whether suggested replacement text is present before adding it.

*Call graph*: 1 external calls (get).


### Review State Management
The state manager records review progress and audit history as the document moves through review stages.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `active whenever a document-review command is run`

A document review has many moving parts: sections, claims that need checking, sources used to check them, issues found in the text, and a final summary. This script acts like a clipboard for that workflow. Each command reads the current review state from document_review_state.json, checks that the new information is shaped correctly, updates the state, saves it back to disk, and often records a log entry in a separate JSON-lines log, where each line is one structured event.

The workflow is phase-based. A new review starts in the outline phase. Adding sections moves it to find_claims. Adding or updating claims supports the fact-checking phase. Adding issues moves it toward find_issues. Submitting a summary marks the review complete. The script warns if a command is being run in an unexpected phase, but usually continues anyway; the phase is guidance, not a hard lock.

Most write commands accept JSON either directly through --data or from a file through --file. Validation helpers protect the state file from missing fields, bad category names, empty locations, invalid page numbers, and similar mistakes. Without this file, the review process would have no shared memory: later steps would not know what sections, claims, or issues earlier steps had found.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints a successful command result as one valid JSON object. This gives both a readable message and structured progress information that another program can safely parse.

**Data flow**: It receives a message, the current phase, the document name, and optional extra lists such as created claims or issues. It builds one dictionary, converts it to JSON text, and prints it to standard output. It does not change the saved review state.

**Call relations**: The state-changing commands call this at the end of a successful operation. It is the final handoff from commands such as cmd_init, cmd_add_sections, cmd_add_claims, cmd_update_claims, cmd_add_issues, and cmd_submit to the outside caller.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds a permanent record of what command was run and how the review phase changed. This is useful for tracing the history of a review after the fact.

**Data flow**: It receives the command name, the phase before and after the command, and any extra details such as counts or filters. It adds the current UTC timestamp, turns the entry into JSON, and appends it as one line to the log file. The review state itself is not changed.

**Call relations**: Most commands call this after reading or changing state. Write commands use it to record changes, while read commands such as cmd_get_claims and cmd_get_issues use it to record what was queried.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the current document review state from disk. Commands use it so they start from the latest saved progress instead of guessing or rebuilding context.

**Data flow**: It looks for the state file named by STATE_FILENAME. If the file is missing, it prints an error telling the user to run init first and exits. If the file exists, it reads the JSON text and returns it as a Python dictionary.

**Call relations**: Every command that needs an existing review calls this before doing its work. It supplies the shared state to commands that add sections, claims, issues, update claims, show claims or issues, submit, and print status.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk. This is what makes changes survive after the command finishes.

**Data flow**: It receives the full state dictionary, converts it to nicely indented JSON, and writes it to the state file named by STATE_FILENAME. The output is the updated file on disk; the function returns nothing.

**Call relations**: Commands that create or change review data call this after editing the in-memory state. It is used by initialization, adding sections, adding claims, updating claims, adding issues, and submitting the review.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user when a command is being run at a surprising point in the review workflow. It helps catch mistakes without always blocking progress.

**Data flow**: It receives the current state and the phase the command expects. If the state's phase is different, it prints a warning to standard error. It does not return data and does not change the state.

**Call relations**: Workflow commands call this near the start, after loading state. It gives context before cmd_add_sections, cmd_add_claims, cmd_update_claims, cmd_add_issues, or cmd_submit continues with its main work.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an incoming JSON item contains all fields needed for the command. This prevents half-formed sections, claims, updates, or issues from being written into the state file.

**Data flow**: It receives one dictionary, a list of required field names, and a label for error messages. It looks for missing fields. If any are absent, it prints a clear error and exits; otherwise it lets the caller continue.

**Call relations**: Commands that ingest structured JSON call this before trusting the data. It is part of the front-door checks in cmd_add_sections, cmd_add_claims, cmd_update_claims, and cmd_add_issues.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a small approved set, such as allowed claim statuses or issue severities. This keeps spelling variations and unsupported categories out of the review data.

**Data flow**: It receives a value, a set of allowed values, and the field name being checked. If the value is not allowed, it prints the valid choices and exits. If it is allowed, nothing is returned and processing continues.

**Call relations**: Commands use this when incoming data must match known categories. It supports claim creation, claim status updates, and issue creation before those commands save anything.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Checks that a page number is an integer greater than or equal to one. It protects section page ranges from invalid values.

**Data flow**: It receives a value that may be text or an integer and a field name. It tries to convert the value to an integer, exits with an error if conversion fails or the number is less than one, and otherwise returns the cleaned integer.

**Call relations**: cmd_add_sections calls this for each section's start_page and end_page. The returned integers are then used to build the saved section records.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a required text-like field is present and not blank. It is used for fields such as locations, where an empty value would make a claim or issue hard to find in the document.

**Data flow**: It receives a value and a field name. It accepts strings and integers, converts the value to a string, rejects blank text, and returns the cleaned string. If the value is the wrong kind or empty, it prints an error and exits.

**Call relations**: cmd_add_claims and cmd_add_issues call this while building new records. The returned location text is stored in the claim or issue that later gets saved.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which is a more precise pointer into the document. The anchor may be absent, but if provided it must be useful text.

**Data flow**: It receives a value and a field name. If the value is null, it returns null. If the value is a non-empty string, it returns that string. Otherwise it prints an error and exits.

**Call relations**: cmd_add_claims and cmd_add_issues use this after checking the required fields. The cleaned anchor is stored with each new claim or issue so later tools can point back to the exact text.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets the JSON input text for commands that can receive data in two ways. It hides the difference between inline --data and a --file path.

**Data flow**: It receives parsed command-line arguments. If a file path was provided, it reads and returns that file's text. Otherwise it returns the inline data string. It does not parse the JSON itself.

**Call relations**: Commands that import lists of sections, claims, claim updates, or issues call this before json.loads turns the text into data. It is the intake step for cmd_add_sections, cmd_add_claims, cmd_update_claims, and cmd_add_issues.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a brand-new review for a document. It creates the first state file with empty sections, claims, issues, counters, and summary.

**Data flow**: It receives command-line arguments containing a filename. It rejects an empty filename, builds the initial state in the outline phase, saves that state, writes a log entry, and prints a JSON success result. The main output is a new document_review_state.json file.

**Call relations**: main dispatches here when the user runs the init command. cmd_init then hands persistence to save_state, history recording to log_action, and final user output to _emit_result.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document's section outline and moves the workflow toward finding claims. This gives later commands named sections to attach claims and issues to.

**Data flow**: It loads the current state, warns if the review is not in the outline phase, reads JSON section data from --data or --file, and checks that each section has a name plus valid start and end pages. It stores each section by name, changes the phase to find_claims, saves the state, logs the addition, and prints a JSON result showing the sections added.

**Call relations**: main calls this for the add-sections command. It relies on load_state and _resolve_data for inputs, validation helpers to check the data, save_state to persist the new outline, log_action for the audit trail, and _emit_result for the command response.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds claims that need fact-checking to a particular section. A claim is a statement in the document that should be verified, refuted, or marked inconclusive later.

**Data flow**: It loads the state, warns if the review is not in the find_claims phase, confirms the named section exists, reads a JSON array of claims, and validates each claim's type, description, original text, location, and optional anchor. For each valid claim it increments the claim counter, creates a new claim ID, marks the claim unverified, stores it, saves the state, logs the new IDs, and prints a JSON result.

**Call relations**: main dispatches here for add-claims. The command uses the shared loading, input resolving, validation, saving, logging, and result-printing helpers to turn raw JSON into stable claim records.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the outcome of fact-checking existing claims. It changes claims from unverified to verified, refuted, or inconclusive and attaches any source URLs used as evidence.

**Data flow**: It loads the state, notes the starting phase, warns if the phase is unexpected, and moves from find_claims to fact_check if needed. It reads a JSON array of updates, checks that each update names an existing claim and a valid non-unverified status, increments that claim's attempt count, adds source URLs, tallies status counts, saves the state, logs the update, and prints a JSON result.

**Call relations**: main calls this for update-claims. It sits between claim collection and issue finding, using load_state and validation helpers first, then save_state and log_action, and finally _emit_result to report what changed.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds review issues found in a section, such as factual problems, grammar problems, non-public information, or narrative logic concerns. These records describe what is wrong and often suggest replacement text.

**Data flow**: It loads the state, records the starting phase, warns if the review is not in the expected issue-finding phase, and moves from fact_check to find_issues if needed. It checks that the section exists, reads a JSON array of issues, validates each issue's type, severity, location, anchor, and required text fields, assigns issue IDs, stores the issue records, saves the state, logs the IDs, and prints a JSON result.

**Call relations**: main dispatches here for add-issues. This command depends on the same helper chain as claim creation: load, resolve input, validate, save, log, and emit a structured response.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Finishes the review with a final summary. It marks the workflow complete so the saved state reflects that the reviewer is done.

**Data flow**: It loads the state, remembers the old phase, warns if the review is not in the issue-finding phase, and checks that the summary is not blank. It sets the phase to complete, stores the summary, saves the state, counts sections, claims, and issues, logs the submission, and prints a JSON result with those totals.

**Call relations**: main calls this when the user runs submit. It uses warn_phase for workflow guidance, save_state for the final state update, log_action for the completion record, and _emit_result for the final machine-readable response.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved claims in a readable text format, with optional filtering by status or section. This helps a reviewer inspect what still needs checking or what has already been decided.

**Data flow**: It loads the state and starts with all saved claims. If a status or section filter was provided, it narrows the list. It logs the query and then prints either a no-matches message or a detailed text listing of each matching claim, including section, location, text, description, anchor, and sources when present.

**Call relations**: main dispatches here for get-claims. Unlike write commands, it does not call save_state or _emit_result; it only reads via load_state, records the lookup with log_action, and prints a human-facing report.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved review issues in a readable text format, with optional filtering by severity or section. This lets someone review the problems found before making edits or preparing a final report.

**Data flow**: It loads the state and gathers all saved issues. It applies a severity filter and/or section filter if requested, logs the query, and prints either a no-matches message or a detailed listing with issue ID, severity, type, section, location, original text, context, description, anchor, and suggested replacement text when present.

**Call relations**: main calls this for get-issues. It follows the read-only path: load_state provides the data, log_action records the query, and the function prints a human-readable view without changing the state file.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Prints a compact dashboard of the current review. It summarizes the phase, sections, claim statuses, issue counts, and final summary if one exists.

**Data flow**: It loads the state, then reads the document name, phase, sections, claims, issues, and summary. It counts claims by status and issues by severity and type, and prints those summaries as plain text. It does not save changes or write a log entry.

**Call relations**: main dispatches here for the status command. This function only depends on load_state, then turns the saved JSON data into a quick progress report for the user.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each user command to the right function. It is the front door of the script.

**Data flow**: It builds an argument parser, registers subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, get-claims, get-issues, and status, then parses the user's command-line input. It looks up the matching command function and calls it with the parsed arguments.

**Call relations**: When the script is run directly, main starts the whole process. It does not perform review work itself; instead it routes control to the command functions, which then read, validate, update, save, log, and print as needed.

*Call graph*: 1 external calls (ArgumentParser).


### Artifact Annotation Writers
Annotation scripts read saved review findings and write them back into PDF, PowerPoint, and Excel artifacts.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `on-demand PDF annotation after document review`

This file turns document review results into visible PDF annotations. Think of it like a reviewer going through a printed document with a highlighter and sticky notes: it marks the questionable text, then adds a note explaining the issue. The script expects to find a saved review state file named by STATE_FILENAME, usually document_review_state.json, in the current working directory. That file contains the issues found earlier in the review process.

When run, the script loads all issues, opens the input PDF with PyMuPDF, and visits each issue one by one. It reads the issue’s page number, severity, original text, and comment details. Severity controls the annotation color: high issues are red, medium issues are orange, and low issues are yellow. For each issue, it tries to find the original text on the target PDF page. If it finds the text, it highlights it and puts a sticky note beside the highlight. If it cannot find the text, it still adds the note at a default spot near the top-left of the page, so the issue is not lost.

At the end, it saves a new annotated PDF. Without this file, review findings would remain only in machine-readable state data instead of becoming something a person can open, read, and act on in a normal PDF viewer.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved document review results from the local state file and returns the issues that should be added to the PDF. It also stops the script with a clear error if the expected state file is missing.

**Data flow**: It starts with no direct input, but looks in the current working directory for the file named by STATE_FILENAME. If the file is not there, it prints an error message to standard error and exits the program. If the file exists, it reads the JSON text, turns it into Python data, takes the values from the "issues" section, and returns them as a list.

**Call relations**: The annotate function calls this first, before opening or changing the PDF. load_issues supplies the review findings that drive every later highlight and sticky note.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function searches one PDF page for the text connected to an issue, so the script knows where to place the highlight. A "quad" is the PDF library’s shape information for where matching text appears on the page.

**Data flow**: It receives a PDF page and the original text from an issue. It first searches using a longer beginning slice of that text. If that finds nothing, it tries again with a shorter beginning slice, which helps when the PDF text differs slightly from the saved review text. It returns the matching page locations, or an empty result if nothing is found.

**Call relations**: The annotate function calls this while processing each issue. Its result decides whether annotate can draw a highlight around the exact text, or must fall back to placing only a sticky note at a default position.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main worker for the script: it reads review issues, opens the input PDF, adds highlights and sticky notes, and saves the annotated output PDF. Someone uses it when they want review findings turned into visible comments inside a PDF file.

**Data flow**: It receives an input PDF path and an output PDF path. It loads issues with load_issues; if there are none, it prints a message and stops. Otherwise, it opens the PDF, loops through each issue, checks that the issue points to a valid page, chooses a color from the issue severity, formats the comment text, searches the page with find_quads, adds a highlight if matching text is found, adds a sticky note either beside the highlight or at a fallback point, then saves the changed document to the output path and closes it.

**Call relations**: When the script is run from the command line with an input and output PDF, the top-level command-line code calls annotate. Inside the larger flow, annotate is the coordinator: it asks load_issues for review data, asks find_quads where text appears on a page, uses models.format_comment to prepare human-readable note text, and uses PyMuPDF functions to open, modify, and save the PDF.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `post-review annotation / command-line run`

PowerPoint files are really ZIP packages full of XML files. This script uses that fact to add comments directly into a `.pptx` file. It reads `document_review_state.json`, finds issues whose location is a slide number, and writes one PowerPoint comment file per affected slide. Think of it like opening a sealed box, adding labeled sticky notes to the right pages, updating the box’s table of contents so PowerPoint knows the notes exist, and sealing the box again.

The script first loads the saved review issues. It groups them by slide number, because PowerPoint stores slide comments separately for each slide. It then copies the input presentation to the output path, unzips that copy into a temporary folder, and edits the internal XML files. For each slide with issues, it creates a comment XML file containing the formatted feedback text, and adds a relationship file entry so the slide points to its comments. It also writes the shared comment author information, registers that author with the presentation, and updates `[Content_Types].xml`, which is PowerPoint’s internal list of what kinds of files are inside the package.

A useful detail is that this script does not use PowerPoint itself. It edits the file structure directly. That makes it automatable, but also means the relationship and content-type records must be correct or PowerPoint may ignore the comments.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: Reads the review results from the saved state file and returns the issues that should become PowerPoint comments. If the state file is missing, it stops the script with a clear error, because there is nothing reliable to annotate.

**Data flow**: It starts with the expected state filename from configuration. It checks whether that JSON file exists in the current working directory. If it exists, it reads the text, parses it as JSON, takes the saved `issues` section, and returns those issue records as a list. If the file is not there, it prints an error to standard error and exits the process.

**Call relations**: This is the first step used by `annotate`. The rest of the script depends on the issue list it returns; without it, there are no slide numbers or comment texts to write into the PowerPoint package.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: Sorts review issues into buckets by slide number so each slide can receive its own comments. It ignores issues whose location cannot be understood as a slide number.

**Data flow**: It receives a list of issue records. For each issue, it tries to read the `location` field and turn it into a number. When that works, it adds the issue to a dictionary under that slide number. When it does not work, the issue is skipped. The result is a slide-number-to-issues map.

**Call relations**: After `annotate` loads all issues, it calls this function to organize them for PowerPoint’s storage layout. `write_slide_comments` and `write_author_and_rels` then use this grouped shape to create the right per-slide files and content-type entries.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: Looks inside a PowerPoint relationship file and finds the largest existing relationship ID number. This helps the script add a new relationship without accidentally reusing an ID that is already taken.

**Data flow**: It receives a path to a `.rels` XML file, which is a small file listing links between parts of a PowerPoint package. If the file does not exist, it returns 0. If it does exist, it parses the XML, checks each relationship’s `Id`, extracts any number in that ID, and returns the highest number found.

**Call relations**: `add_relationship` calls this when it needs to create a fresh `rId` value. In the bigger flow, it is a safety helper that keeps newly added comment links from colliding with links that were already in the presentation.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: Adds a link from one internal PowerPoint XML part to another, such as from a slide to its comments or from the presentation to the comment author list. PowerPoint relies on these relationship files to know which internal files belong together.

**Data flow**: It receives the path to a relationship file, the kind of relationship to add, and the target file it should point to. If the relationship file exists, it opens and parses it. If not, it creates a new relationship XML document and the needed folder. It first checks whether the same relationship type is already present; if so, it leaves the file unchanged. Otherwise, it chooses the next available `rId`, adds the new relationship entry, and writes the XML back to disk.

**Call relations**: `write_slide_comments` uses this to connect each slide to its comment file. `write_author_and_rels` uses it to connect the presentation to the shared comment author file. It relies on `find_max_rel_id` to choose a safe new ID when adding a relationship.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: Creates the actual comment XML files for the slides that have review issues. Each issue becomes a visible PowerPoint comment authored by the review tool.

**Data flow**: It receives the temporary extracted PowerPoint folder and the issues grouped by slide. It creates the internal `ppt/comments` folder if needed. For each slide, it builds an XML comment list, adds one comment entry per issue, formats the issue into human-readable comment text, and writes that slide’s comment file. It also creates or updates the slide’s relationship file so the slide points to its comments. It returns the total number of comments written.

**Call relations**: `annotate` calls this after unzipping the copied presentation. This function is where review findings turn into PowerPoint comment files. It hands the final comment count back to `annotate`, and `write_author_and_rels` uses that count to record the author’s last comment index.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: Writes the shared PowerPoint metadata that makes the inserted comments valid and recognizable. This includes the comment author, the presentation-level link to that author file, and the package content-type records.

**Data flow**: It receives the temporary PowerPoint folder, the total number of comments created, and the grouped slide issues. It writes `commentAuthors.xml` with a single author named `Flying Object`. It updates the presentation relationship file so PowerPoint can find that author file. Then it opens `[Content_Types].xml` and adds entries saying that the author file and each slide comment file are PowerPoint comment-related XML parts. It writes the updated content types back to disk.

**Call relations**: `annotate` calls this after `write_slide_comments` has created the per-slide comment files. It uses `add_relationship` for the presentation-level link. Without this step, the comment XML files might exist in the package but PowerPoint would not know how to interpret or connect them.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: Runs the full annotation process from input presentation to output presentation. It is the main worker used by the command-line script.

**Data flow**: It receives an input `.pptx` path and an output `.pptx` path. It loads review issues, stops early if there are none, groups the issues by slide, and copies the input file to the output path. It then extracts that output file into a temporary folder, writes comment files and metadata into the extracted PowerPoint structure, rebuilds the `.pptx` ZIP package from the modified folder, prints how many comments were added, and finally deletes the temporary folder.

**Call relations**: This function ties the whole script together. The command-line block calls it after checking the user supplied input and output paths. Inside, it delegates reading to `load_issues`, organizing to `group_by_slide`, slide comment creation to `write_slide_comments`, and package metadata updates to `write_author_and_rels`.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review annotation command`

This file is a small command-line tool for turning review results into something a person can see directly in Excel. The review system stores its findings in a JSON state file, but that is not a friendly place for a spreadsheet user to look. This script bridges that gap by copying the original XLSX file, opening the copy, and attaching each issue as an Excel cell comment.

The process is like putting sticky notes onto a spreadsheet. For each issue, the script formats the issue text into a readable comment, then tries several ways to decide where the sticky note belongs. First it looks for the named worksheet, if the issue says which sheet it came from. If the issue also has a cell reference, it tries to place the comment exactly there. If that does not work, it searches for a cell containing the issue’s original text. If it still cannot find a match, it searches every worksheet. As a last resort, it puts the comment on cell A1 of the first sheet, combining multiple fallback comments if needed.

Without this file, review issues for Excel documents would remain separate from the spreadsheet, making them harder to inspect, share, and fix in context.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved document-review state and returns the review issues that need to be added to the spreadsheet. If the state file is missing, it stops the script with a clear error because there is nothing to annotate.

**Data flow**: It starts with the expected state filename from configuration. It checks whether that JSON file exists, reads its text, turns the JSON text into Python data, then takes the values from the `issues` section. The result is a list of issue records for the rest of the script to use; if the file is absent, the script exits instead.

**Call relations**: The main annotation flow calls this first, before opening the spreadsheet. It depends on standard file-path and JSON reading tools, and it hands the collected issue list back to `annotate` so each issue can become an Excel comment.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell whose displayed value contains a given piece of text. It helps place a comment near the text that triggered the review issue.

**Data flow**: It receives a worksheet and some target text. It normalizes the target by converting it to lowercase and trimming extra spaces, then checks every non-empty cell in the worksheet the same way. It returns the first matching cell it finds, or `None` if no cell contains the text.

**Call relations**: `annotate` uses this when an exact cell reference is missing or fails. It is the script’s backup way to locate the right spot: first within the expected worksheet, then across all worksheets if needed.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks through an Excel workbook for a worksheet with a given name, ignoring differences in uppercase and lowercase letters. It lets review issues refer to a sheet without requiring the capitalization to match perfectly.

**Data flow**: It receives a workbook and a worksheet name from an issue’s location field. It compares that name against every worksheet title in the workbook in a case-insensitive way. It returns the matching worksheet, or `None` if no sheet name matches.

**Call relations**: `annotate` calls this before trying to place an issue on a specific sheet. If it finds the sheet, later steps can try an exact cell anchor or a text search on that sheet before falling back to a broader search.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a prepared Excel comment to one exact cell reference, such as `B12`. It keeps bad or unusable cell references from crashing the whole annotation run.

**Data flow**: It receives a worksheet, a cell reference, and a comment object. It asks the worksheet for that cell and assigns the comment to it. If the cell reference is accepted, it returns `True`; if the reference is invalid or cannot be used, it returns `False` and leaves placement to another strategy.

**Call relations**: `annotate` uses this as the first placement attempt when an issue includes both a worksheet and an anchor cell. Its success or failure decides whether `annotate` can move on or must search for matching text instead.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It creates an annotated copy of an Excel file by adding one comment for each saved review issue.

**Data flow**: It receives an input XLSX path and an output XLSX path. It loads the saved issues, copies the original spreadsheet to the output path, opens that copy, and then processes each issue. For every issue it formats readable comment text, chooses a worksheet or cell if possible, attaches the comment, and counts it. At the end it saves the workbook and prints how many comments were added.

**Call relations**: This function ties the whole script together. It calls `load_issues` to get review findings, uses `format_comment` to turn each finding into readable text, creates Excel comment objects, and uses `find_worksheet`, `_place_on_cell`, and `find_cell` as a sequence of increasingly broad placement attempts. The command-line block at the bottom calls `annotate` after checking that the user supplied an input and output filename.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).
