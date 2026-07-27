# Document, office, PDF, spreadsheet, and review skill scripts  `stage-10.1`

This stage is a toolbox for working with common office files inside the workspace. It is not the main agent loop; it is shared support the agent or a user can call when a document needs to be inspected, changed, checked, or exported.

The package marker files simply make these folders importable by Python. The document-review tools keep a review organized: constants name the state and log files, models define what an “issue” contains, manage_state records review progress in JSON and a log, and the PDF, PowerPoint, and Excel annotators turn saved findings into visible highlights, comments, or cell notes.

The Word tools unpack a DOCX into editable XML, add comments to the unpacked package, accept tracked changes through LibreOffice, and pack the folder back into a DOCX. The PowerPoint tools similarly unpack and repack PPTX files, repair known presentation-format problems, and help add or preview slides. The spreadsheet tools run LibreOffice invisibly and force Excel formulas to recalculate. The PDF tools detect and fill real form fields, analyze page layout for non-fillable forms, and render pages as PNG images for easier viewing.

## Files in this stage

### Package scaffolding
Package marker files make the document extension and skill script folders importable.

### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import time`

This is an empty package initializer. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. That matters because other parts of the system can then refer to this extension by package name, such as importing modules from `ufo_ext_documents`.

There is no setup code, configuration, or feature behavior here. Its role is more like a label on a folder in a filing cabinet: it does not contain the documents itself, but it makes the folder recognizable and usable by the rest of the filing system. If this file were removed in environments that expect traditional Python packages, imports for this extension could fail or behave differently.


### Document review workflow
Review scripts define shared review data, manage review state, and export saved findings as visible annotations in PDFs, presentations, and spreadsheets.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `startup`

This is an empty Python `__init__.py` file. Its job is not to run logic, but to tell Python that the surrounding `scripts` directory should be treated as an importable package. In plain terms, it is like putting a label on a folder so the rest of the project can reliably refer to things inside it by name.

Without this file, some Python environments or tools might not recognize the folder as part of the package structure, especially if they expect the older, explicit package style. That could make imports from this directory fail or behave inconsistently.

There are no functions, classes, settings, or side effects here. It exists purely as packaging glue for the document-review skill's script area.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a tiny configuration-style file. It defines two fixed file names that other parts of the document review feature can import instead of typing the names themselves. `STATE_FILENAME` is the JSON file used to remember the current review state, like a saved checkpoint. `LOG_FILENAME` is the JSON Lines file used to record review events over time, where each line is a separate log entry. The practical value is consistency. If every script wrote these names by hand, one typo could make part of the system look for `document_review_state.json` while another created a different file. By putting the names here, the project has a single source of truth. If the file names ever need to change, they can be changed here instead of hunting through the whole codebase.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `comment formatting during document review`

This file is like a simple form template for document review feedback. A review issue needs many pieces of information: what kind of problem it is, how serious it is, where it appears, what the original text was, and what replacement text is suggested. The `DocumentIssue` type records that expected shape so other code can treat review issues consistently instead of guessing which fields exist.

The file also translates internal issue type codes, such as `spelling_grammar`, into labels a person would recognize, such as `Spelling/Grammar`. That matters because the computer-friendly names are useful in data files, but they would look awkward in a document comment.

The main behavior here is `format_comment`. It takes one issue and builds a short comment: a header with the issue label and severity, then the explanation, and optionally a suggested replacement. If an issue type is unknown, it safely uses the original code instead of failing. If there is no suggested new text, it leaves that section out. Without this file, different parts of the review tool could format comments differently, or misunderstand the fields inside an issue.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: Turns one document-review issue into a readable comment string. It is used when the tool needs to show a human reviewer what is wrong and, when available, what replacement text is suggested.

**Data flow**: It receives an `issue`, which is a dictionary-like record following the `DocumentIssue` shape, plus a true-or-false choice called `include_suggestion`. It looks up a friendly label for the issue type, reads the severity and description, and builds a comment header and body. If suggestions are allowed and the issue has `new_text`, it adds a `Suggested:` line. The result is a single text block with line breaks; the issue itself is not changed.

**Call relations**: When this helper formats a comment, it reads normal fields directly from the issue and uses the issue's dictionary-style `get` method to safely check for optional suggested text. The provided call graph shows no specific caller, so this function is best understood as a reusable formatting step that other document-review code can call whenever it needs comment text.

*Call graph*: 1 external calls (get).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `active during each document-review command invocation`

This script is like a clipboard for a structured document review. Without it, the reviewer or automation would have to remember which sections were found, which claims still need checking, which problems were discovered, and whether the review is finished. Instead, each command reads a shared state file, updates one part of it, saves it back, and records what happened in a log.

The review starts with `init`, which creates a fresh state for one document. Then `add-sections` records the document’s sections and moves the work to claim finding. `add-claims` adds things that need verification, such as public data or numerical consistency. `update-claims` records fact-checking results and source links. `add-issues` records suggested fixes or concerns, such as grammar problems, non-public information, or logic gaps. Finally, `submit` marks the review complete with a summary.

The file is careful about input. It checks that required fields are present, that page numbers are positive, that categories use known names, and that text fields are not empty. Most updating commands return a single JSON result so another tool can read it reliably. Read-only commands such as `status`, `get-claims`, and `get-issues` print human-friendly summaries.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the command’s final answer as one JSON object. This gives both a human-readable message and structured progress data that another program can safely read.

**Data flow**: It receives a message, the current phase, the document name, and any extra result details. It wraps them into one dictionary, converts that dictionary into JSON text, and prints it to standard output.

**Call relations**: The update-style commands call this at the end after they have changed and saved the state. It is the final handoff from internal Python data to a machine-readable command result.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds one audit entry to the review log. This makes it possible to see later what command ran, when it ran, and how the review phase changed.

**Data flow**: It receives the command name, the phase before and after the command, and extra details such as counts or IDs. It adds the current UTC time, turns the entry into JSON, and appends it as one line to the log file.

**Call relations**: Most commands call this after reading or changing state. It runs alongside the main state changes, creating a separate chronological trail of the review’s activity.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the current review state from disk. Commands use it whenever they need to continue work on an existing review.

**Data flow**: It looks for the configured state file. If the file is missing, it prints an error and stops the program; otherwise it reads the file text, parses the JSON, and returns the resulting dictionary.

**Call relations**: Every command except the initial setup depends on this before doing its work. It is the doorway from the saved review file into the command’s in-memory view of the review.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk. This is what makes changes survive after the command exits.

**Data flow**: It receives the state dictionary, converts it into nicely indented JSON text, and overwrites the configured state file with that text.

**Call relations**: Commands that create or change review data call this before logging and reporting success. It is the persistence step after the command has edited the review state.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user if a command is being run in an unexpected review phase. It does not block the command; it simply points out that the workflow order may be unusual.

**Data flow**: It receives the current state and the phase the command normally expects. If they differ, it prints a warning to standard error and leaves the state unchanged.

**Call relations**: The phase-based commands call this near the start. It acts like a polite checkpoint before the command continues with validation and updates.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an input object contains all fields needed for that kind of item. This prevents half-formed sections, claims, issues, or claim updates from being saved.

**Data flow**: It receives one input dictionary, a list of required field names, and a label for the error message. If any fields are missing, it prints the missing names and stops the program; otherwise it returns without changing anything.

**Call relations**: The commands that accept JSON arrays call this for each submitted item before they build state records. It is one of the first guards against bad input entering the state file.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of the allowed choices for a field. For example, it keeps claim statuses and issue severities consistent instead of allowing arbitrary wording.

**Data flow**: It receives a value, a set of allowed values, and the field name. If the value is not allowed, it prints a clear error and stops; if it is allowed, the command continues.

**Call relations**: Claim and issue commands call this while reading user-provided JSON. It protects later reporting code by ensuring fields use predictable names.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number into an integer and makes sure it is at least 1. This keeps section page ranges meaningful.

**Data flow**: It receives a value that should represent a number. It tries to convert it to an integer, rejects non-numbers and values below 1, and returns the valid integer.

**Call relations**: `cmd_add_sections` calls this while checking each section’s start and end pages. The returned numbers are stored in the review state.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Makes sure a required text-like value is present and not blank. It accepts strings and integers, then stores them as strings.

**Data flow**: It receives a value and a field name. It rejects values that are not strings or integers, rejects blank text, and returns a cleaned string form of the value.

**Call relations**: Claim and issue creation commands use this for locations. It ensures every saved claim or issue can point the reviewer to a real place in the document.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional document anchor. An anchor is a more precise pointer into the document; it may be missing, but if present it must be useful text.

**Data flow**: It receives a value and a field name. If the value is null, it returns null; if it is a non-empty string, it returns it; otherwise it prints an error and stops.

**Call relations**: Claim and issue creation commands call this after reading optional anchor fields. It keeps optional pointers clean without forcing every item to have one.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets the JSON input text for commands that accept either direct data or a file path. This lets users choose the most convenient way to provide larger lists.

**Data flow**: It receives parsed command-line arguments. If a file path was provided, it reads and returns that file’s text; otherwise it returns the text passed through the data argument.

**Call relations**: The commands that add sections, add claims, update claims, and add issues call this before parsing JSON. It hides the difference between `--data` and `--file` from the rest of the command logic.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new review for a document. It creates the first state file with empty sections, claims, issues, and counters.

**Data flow**: It receives the filename from the command line. It rejects a blank filename, builds a fresh state in the `outline` phase, saves it, logs the initialization, and prints a JSON success result.

**Call relations**: The main dispatcher calls this when the user runs `init`. This is the only command that does not load an existing state first, because it creates the state other commands rely on.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document’s section outline to the review. This gives later claims and issues named places to attach to.

**Data flow**: It loads the current state, warns if the review is not in the outline phase, reads a JSON array of sections, validates each section’s name and page range, stores the sections by name, moves the phase to `find_claims`, saves, logs, and prints a JSON result.

**Call relations**: The main dispatcher calls this for `add-sections`. It relies on the shared input, validation, load, save, log, and result helpers, then prepares the state for later `add-claims` commands.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims that need checking within a specific section. Each claim gets a new ID and starts as unverified.

**Data flow**: It loads state, checks that the named section exists, reads a JSON array of claims, validates required fields and allowed claim types, creates numbered claim records with status `unverified`, saves them in the state, logs the new claim IDs, and prints a JSON result.

**Call relations**: The main dispatcher calls this for `add-claims`. It is normally used after sections are known, and it creates the items that `cmd_update_claims` will later mark as verified, refuted, or inconclusive.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records fact-checking results for existing claims. It updates each claim’s status, counts an attempt, and attaches source URLs when provided.

**Data flow**: It loads state, warns if the phase is unexpected, moves from `find_claims` to `fact_check` when needed, reads a JSON array of updates, validates each claim ID and status, appends any source links, saves the state, logs status counts, and prints a JSON result.

**Call relations**: The main dispatcher calls this for `update-claims`. It follows `cmd_add_claims` in the workflow and turns unverified claims into checked claims that can inform later issue finding.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds review problems and suggested fixes for a section. These are the concrete findings that may need to be corrected in the document.

**Data flow**: It loads state, warns if the phase is unexpected, moves from `fact_check` to `find_issues` when needed, checks that the section exists, reads a JSON array of issues, validates type, severity, location, and required text fields, creates numbered issue records, saves, logs, and prints a JSON result.

**Call relations**: The main dispatcher calls this for `add-issues`. It uses the same validation and storage pattern as claims, but creates issue records that feed into the final review submission.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Finishes the review with a final summary. It marks the state as complete and records the totals for sections, claims, and issues.

**Data flow**: It loads state, warns if the review is not in the issue-finding phase, rejects an empty summary, sets the phase to `complete`, stores the summary, saves, logs final counts, and prints a JSON completion result.

**Call relations**: The main dispatcher calls this for `submit`. It is the closing step after sections, claims, and issues have been gathered.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved claims in a readable format, optionally narrowed by status or section. This helps a reviewer inspect what still needs work or what was found in one part of the document.

**Data flow**: It loads state, gathers all claims, filters them when requested, logs the search, and prints either a no-results message or detailed claim entries with IDs, status, location, text, description, and sources.

**Call relations**: The main dispatcher calls this for `get-claims`. Unlike the update commands, it does not save state or emit JSON; it is a read-only viewing command for humans.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved issues in a readable format, optionally narrowed by severity or section. This helps a reviewer focus on the most important problems or one document area.

**Data flow**: It loads state, gathers all issues, filters them when requested, logs the search, and prints either a no-results message or detailed issue entries with ID, severity, type, location, context, description, and suggested replacement text.

**Call relations**: The main dispatcher calls this for `get-issues`. It is a read-only companion to `cmd_add_issues`, giving humans a way to review the stored findings.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Prints a compact dashboard of the review’s current progress. It summarizes the phase, sections, claim statuses, issue counts, and final summary if one exists.

**Data flow**: It loads state, reads the document name and phase, then counts and prints sections, claims by status, and issues by severity and type. It does not change or save anything.

**Call relations**: The main dispatcher calls this for `status`. It is the quick check-in command that reads the same state created and updated by the rest of the workflow.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each command to the right function. It is the front door of the script when run from a terminal or another process.

**Data flow**: It builds an argument parser, declares all supported subcommands and their options, parses the user’s command-line input, looks up the matching command function, and calls it with the parsed arguments.

**Call relations**: When the file is executed directly, this function runs first. It does not do the review work itself; it routes the request to functions such as `cmd_init`, `cmd_add_claims`, or `cmd_status`.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `post-review command-line annotation step`

This file is a small command-line tool used after a document review has already found problems. The review results are stored in a JSON state file, and this script applies those results back onto a PDF so a person can open the PDF and see the feedback in context. Without it, the issues would remain separate from the document, making review comments harder to understand and act on.

It works like a careful editor with a highlighter and sticky notes. First, it reads the saved issue list from the review state file. Then it opens the input PDF. For each issue, it checks which page the issue belongs to, chooses a color based on severity, and builds the comment text using the shared comment-formatting code. It searches the page for the original text quoted by the issue. If it finds the text, it highlights it and places the sticky note just beside the highlighted area. If it cannot find the text, it still adds the sticky note at a safe default spot on the page, so the issue is not lost.

At the end, it saves a new annotated PDF. A notable detail is that the search is intentionally forgiving: it first searches for a longer slice of the original text, then tries a shorter prefix if needed. This helps when PDF text extraction or line breaks make exact matching unreliable.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved document-review issues from the expected state file. It gives the rest of the script a simple list of issues to place onto the PDF.

**Data flow**: It starts with no direct input, but looks in the current working directory for the configured review state filename. If the file is missing, it prints an error and stops the program. If the file exists, it reads the JSON text, extracts the stored issue records, and returns them as a list.

**Call relations**: The main annotation flow calls this first, because it cannot mark up a PDF until it knows what issues were found. It relies on the JSON reader to turn the file text into data, uses a filesystem path object to find the file, and exits through the system module when the required file is absent.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to locate a reported text snippet on a specific PDF page. It returns the page areas that PyMuPDF can use for highlighting.

**Data flow**: It receives a PDF page and the original text from an issue. It first searches for the beginning of that text using a longer prefix. If that fails, it searches again using a shorter prefix, which can succeed when the PDF text is broken up or slightly different. It returns whatever matching page regions were found, or an empty result if none were found.

**Call relations**: The annotation function calls this for each issue after it has selected the correct page. Its result decides whether the script can place a real text highlight, or whether it must fall back to adding only a sticky note at a default location.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function: it opens a PDF, adds highlights and sticky-note comments for each saved review issue, and writes a new PDF. Someone uses it to produce a shareable, marked-up version of the reviewed document.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the saved issues, opens the input PDF, and walks through each issue. For each valid page number, it chooses a severity color, formats the comment text, searches for the issue text, adds a highlight if possible, adds a sticky note, and counts the annotation. Finally, it saves the changed document to the output path, closes the PDF, and prints how many annotations were added.

**Call relations**: When the script is run from the command line with an input and output filename, this function is called to do the real work. It calls load_issues to get the review data, find_quads to locate text on each PDF page, PyMuPDF functions to open and edit the PDF, and the shared format_comment function to make each note readable and consistent.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `post-review annotation/export`

PowerPoint files are really zip archives full of XML files. This script uses that fact to add comments without opening PowerPoint itself. It reads the review results from the shared state file, groups the issues by slide number, copies the original presentation, unzips the copy into a temporary folder, adds the XML parts that PowerPoint expects for comments, then zips everything back up.

The main problem it solves is turning an external review report into feedback that lives inside the presentation. Without this file, the review system might know there are issues, but a human opening the PPTX would not see them as slide comments.

The script creates one comment XML file per slide that has issues. It also creates a comment author file, saying the comments come from “Flying Object,” and updates PowerPoint’s relationship files. A relationship file is like a table of contents that tells PowerPoint how one internal file points to another. Finally, it updates the content-types file, which tells PowerPoint what kind of data each added XML file contains. If any of these pieces are missing, PowerPoint may ignore the comments or consider the file malformed.

One important detail: issue locations must be slide numbers. Issues whose location cannot be read as a number are skipped.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: Reads the saved document-review state and extracts the issues that should become PowerPoint comments. If the expected state file is missing, it stops the script with a clear error instead of making an empty or misleading presentation.

**Data flow**: It starts with the known state filename from configuration. It checks whether that file exists, reads its JSON text, turns that text into Python data, and returns the issue records found under the "issues" section. If the file is not there, it prints an error to standard error and exits the program.

**Call relations**: This is the first step used by annotate. The rest of the annotation process depends on the list it returns; without it, there is nothing to place onto slides.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: Sorts review issues into buckets by slide number so each slide can get its own comment file. It quietly ignores issues whose location is not a usable slide number.

**Data flow**: It receives a list of issue records. For each issue, it tries to read the issue's location as a number, treating that number as a 1-based PowerPoint slide number. It builds and returns a dictionary where each slide number points to the issues that belong on that slide.

**Call relations**: annotate calls this after loading issues. The grouped result is then passed to the comment-writing functions, which need to know which comments belong to which slide.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: Looks inside a PowerPoint relationship file and finds the largest existing relationship ID number. This lets the script add a new relationship without accidentally reusing an ID that is already taken.

**Data flow**: It receives the path to a .rels XML file. If the file does not exist, it returns 0. If it exists, it parses the XML, scans each relationship's Id value for a number, and returns the highest number it finds.

**Call relations**: add_relationship calls this when it needs to create a new link inside a relationship file. It acts like checking the last ticket number before issuing the next ticket.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: Adds a link to a PowerPoint relationship file, creating the file if necessary. These links tell PowerPoint where to find related internal files, such as slide comment XML or the comment author list.

**Data flow**: It receives a relationship-file path, a relationship type, and a target file path. It opens the existing relationship XML or creates a new empty one. If a relationship of the same type already exists, it leaves the file unchanged. Otherwise it finds the next available relationship ID, adds a new XML relationship entry, and writes the file back to disk.

**Call relations**: write_slide_comments uses this to connect each slide to its comment file. write_author_and_rels uses it to connect the presentation to the comment author file. It relies on find_max_rel_id to avoid ID collisions.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: Creates the actual PowerPoint comment XML files for slides that have review issues. Each issue becomes a comment with formatted text and a timestamp.

**Data flow**: It receives the temporary extracted PPTX folder and the issues grouped by slide. For every slide with issues, it creates a comment list XML file under ppt/comments. For each issue, it makes a comment entry, fills in the comment text using format_comment, and increments a shared comment count. It also adds the needed slide relationship so PowerPoint can find that slide's comment file. It returns the total number of comments written.

**Call relations**: annotate calls this after extracting the copied PPTX. It hands off relationship creation to add_relationship and text formatting to models.format_comment. Its returned comment count is later used by write_author_and_rels so the author metadata knows the last comment index.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: Adds the shared PowerPoint metadata that makes the comments valid: who wrote them, how the presentation links to the author file, and what content types the new XML files have.

**Data flow**: It receives the temporary extracted PPTX folder, the total comment count, and the issues grouped by slide. It writes ppt/commentAuthors.xml with the configured author name and initials. It updates the presentation relationships to point to that author file. Then it opens [Content_Types].xml and adds entries for the author file and each slide comment file if they are not already listed.

**Call relations**: annotate calls this after write_slide_comments has created the per-slide comment files. It uses add_relationship for the presentation-level link, and it completes the package-level bookkeeping PowerPoint needs before the modified presentation is zipped back up.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: Runs the full process of turning saved review issues into comments inside a new PPTX file. This is the main worker used by the command-line script.

**Data flow**: It receives an input PowerPoint path and an output PowerPoint path. It loads issues, stops early if there are none, groups them by slide, copies the input file to the output file, extracts that output file into a temporary folder, writes comment files and metadata into the extracted structure, then rebuilds the PPTX zip archive from the modified folder. At the end, it prints how many comments were added and always removes the temporary folder.

**Call relations**: This function ties the whole file together. The command-line block calls it after checking the user supplied exactly an input and output path. Inside, it calls load_issues, group_by_slide, write_slide_comments, and write_author_and_rels in order, while the standard library does the file copying, unzipping, rezipping, and cleanup.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review annotation`

This file is a small command-line tool for making review feedback visible inside an XLSX spreadsheet. The review system stores its findings in a JSON state file, which is useful for software but not very convenient for a person reading a spreadsheet. This script bridges that gap by turning each issue into an Excel comment attached to the most relevant cell.

The flow is simple. First it reads the saved review issues from `document_review_state.json`. Then it copies the input workbook to the requested output path, so the original stays untouched. For each issue, it formats a human-readable comment, looks for the worksheet named in the issue, and tries to place the comment at the issue's exact cell reference if one was recorded. If that does not work, it searches for a cell containing the original text from the issue. As a last resort, it puts the comment on cell A1 of the first sheet. If A1 already has a fallback comment, it appends the new one below a separator, like adding several sticky notes to the same corner of a page.

The script uses `openpyxl`, a Python library for reading and writing Excel files. Without this file, review findings for spreadsheets would remain outside the workbook, making them harder to inspect in the place where the reviewed content actually lives.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved review results from the expected state file and returns the list of issues to annotate. It also stops the script with a clear error if the state file is missing, because there would be nothing reliable to add to the spreadsheet.

**Data flow**: It starts with the fixed state filename from configuration. It checks whether that file exists, reads its text as UTF-8, parses the JSON into Python data, and pulls out the issue records from the `issues` section. The result is a plain list of issue objects; if the file is absent, it prints an error and exits the program.

**Call relations**: The main `annotate` function calls this first, before opening or copying the workbook. It relies on standard JSON parsing and the filesystem path helper to turn the saved review state into the issue list that drives the rest of the annotation process.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell whose visible value contains a given piece of text. It is used when the script does not have, or cannot use, an exact cell address for an issue.

**Data flow**: It receives a worksheet and a text snippet. It normalizes the snippet by trimming spaces and making it lowercase, then walks through every cell in every row. Empty cells are skipped. When a cell's value contains the target text, ignoring case and surrounding spaces, that cell is returned. If no matching cell is found, it returns nothing.

**Call relations**: `annotate` calls this after trying a more precise placement method. First it searches the worksheet named by the issue, and if that fails it searches every worksheet in the workbook. This gives the script a practical fallback: find the reviewed text where it appears, even if the exact cell reference was not available.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function finds a worksheet by its tab name without caring about uppercase or lowercase differences. It helps connect an issue's recorded location to the actual sheet inside the workbook.

**Data flow**: It receives an open workbook and a location string. It checks each worksheet title, comparing both the sheet title and the requested location in lowercase. If it finds a match, it returns that worksheet. If no sheet name matches, it returns nothing.

**Call relations**: `annotate` uses this near the start of each issue's placement attempt. If it finds the intended sheet, later steps can try the exact anchor cell or search only within that sheet before falling back to the whole workbook.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a prepared comment to a specific cell address, such as `B12`. It keeps the main annotation flow cleaner by hiding the small failure cases around invalid cell references.

**Data flow**: It receives a worksheet, a cell reference, and an already-created Excel comment. It tries to look up that cell in the worksheet and set the cell's comment to the provided comment. If the reference works, it returns `True`. If the reference is invalid or cannot be used, it returns `False` without changing anything.

**Call relations**: `annotate` calls this when an issue includes both a matching worksheet and an `anchor`, meaning a likely exact cell location. If this precise placement succeeds, the issue is done. If it fails, `annotate` continues with broader text-search fallbacks.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It creates an annotated copy of an Excel file by adding one comment per review issue, placing each comment as close as possible to the related spreadsheet content.

**Data flow**: It receives an input workbook path and an output workbook path. It loads the issue list, stops early if there are no issues, copies the input file to the output path, and opens that copy with `openpyxl`. For each issue, it turns the issue into readable comment text, creates an Excel comment, then tries several placement strategies: exact worksheet and cell anchor, matching text on the named worksheet, matching text anywhere in the workbook, and finally cell A1 on the first sheet. After all issues are processed, it saves the output workbook and prints how many comments were added.

**Call relations**: This function ties the whole script together. The command-line block at the bottom calls it after checking that the user supplied input and output filenames. Inside, it calls `load_issues` to get review data, `format_comment` to make each issue readable, `find_worksheet`, `_place_on_cell`, and `find_cell` to decide where comments belong, and `openpyxl` plus file-copying tools to write the final annotated spreadsheet.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).


### Word document editing
DOCX scripts unpack Word files for XML editing, add comments, accept tracked changes, and repack the edited folder into a document.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual document unpacking`

A .docx file is really a ZIP archive full of XML files. This script opens that archive, extracts it into a normal directory, and then makes the XML friendlier for human editing. Without this file, someone working on Word document content would have to manually unzip the document and deal with cramped, repetitive, machine-generated XML.

The main flow is like unpacking a suitcase and then sorting the clothes. First, it checks that the input exists and looks like a .docx file. Then it unzips everything into the requested output folder. Next it finds XML-related files and pretty-prints them with indentation, so nested document structure is easier to see.

For the main Word content file, word/document.xml, it can do two extra cleanups. It can coalesce tracked changes, meaning it joins neighboring insertions or deletions from the same author when they are really one continuous edit. It can also merge adjacent Word “runs,” which are small stretches of text with the same formatting. Word often splits text into many tiny runs for internal reasons, so merging them makes the file less noisy.

Finally, it replaces curly quote characters with XML numeric entities. That keeps those characters visible and stable in the text file. The script returns a short summary and can also be run directly from the command line.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker for the script. It validates the requested .docx file, extracts it into a folder, runs the cleanup steps, and returns both a structured result and a human-readable message.

**Data flow**: It takes an input file path, an output folder path, and two yes/no options for cleanup. It checks the file, creates the output folder, extracts the ZIP contents, formats XML files, optionally cleans word/document.xml, replaces curly quotes, and then returns an UnpackResult plus a summary message. If the input is missing, not a .docx file, or not a valid ZIP archive, it returns no result and an error message instead.

**Call relations**: This function is the conductor of the whole file. The command-line block calls it after reading user arguments. During the run it hands individual jobs to _indent_xml, _coalesce_tracked_changes, _merge_adjacent_runs, and _replace_curly_quotes, then gathers their counts into the final message.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This function reformats one XML file so it is easier for a person to read. It adds consistent indentation without changing the meaning of the XML.

**Data flow**: It receives the path to an XML file. It parses the file as XML, asks the XML library to indent the tree with two spaces, and writes the formatted XML back to the same file. If anything goes wrong, it silently leaves that file alone.

**Call relations**: unpack_docx calls this for every extracted XML or relationship file soon after unzipping. It is an early cleanup step that makes later inspection and editing less painful.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This function rewrites curly quote characters as XML numeric entities, such as turning a left double quote into &#x201C;. This helps preserve those characters clearly and consistently in XML text.

**Data flow**: It reads one XML file as text. If it finds curly single or double quotes, it replaces each one with its matching XML entity and writes the text back. If there are no curly quotes, or if reading or writing fails, it makes no change.

**Call relations**: unpack_docx calls this near the end for every XML-related file. It runs after formatting and document cleanup so the final extracted files have stable quote representation.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word’s main document XML by joining neighboring text runs that have the same formatting. A run is a small chunk of Word text; Word often splits one sentence into many runs even when nothing visually changes.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns zero. Otherwise it parses the XML, removes proofing error markers, removes Word revision-session attributes from runs, finds parent elements that contain runs, and asks _merge_runs_in to simplify each one. If anything was merged, it writes the changed XML back and returns how many run elements were absorbed.

**Call relations**: unpack_docx calls this when run merging is enabled. It delegates the actual per-container merging to _merge_runs_in and uses the total count to report how much the document was simplified.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This function creates a stable text fingerprint of a run’s formatting. It lets the script decide whether two Word runs have the same formatting and can safely be merged.

**Data flow**: It receives one run element from the XML. It looks for that run’s formatting child element, called rPr in WordprocessingML. If there is no formatting element, it returns None; otherwise it serializes the formatting in a canonical, normalized form and returns that string.

**Call relations**: _merge_runs_in calls this while scanning neighboring runs. The returned fingerprint is the comparison key used to decide whether a run belongs with the current group.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This function does the detailed work of merging adjacent runs inside one parent XML element. It only joins runs that sit next to each other and have matching formatting.

**Data flow**: It receives an XML container, such as a paragraph-like element. It walks through the container’s direct children, groups consecutive run elements with the same formatting fingerprint, and then folds each group into its first run. Non-formatting child nodes from later runs are moved into the first run, the emptied runs are removed, adjacent text nodes are joined, and the number of removed runs is returned.

**Call relations**: _merge_adjacent_runs calls this for each container that has runs. It uses _canonical_rpr to compare formatting and _join_adjacent_text to clean up text pieces after several runs have been folded together.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This function tidies a single run after merging by joining neighboring text nodes into one text node. This prevents the merged run from still looking artificially split inside.

**Data flow**: It receives one run element. It scans its child elements from left to right, and whenever two text elements are next to each other, it combines their text into the first and removes the second. If the combined text begins or ends with a space, it marks the XML so that Word preserves that space.

**Call relations**: _merge_runs_in calls this after it has moved content from several runs into one anchor run. It is the final polish step that makes the merged run cleaner and keeps important spaces from being lost.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word tracked-change markup by joining neighboring insertions or deletions that belong together. It reduces clutter when Word has split one author’s continuous change into several separate XML elements.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns zero. Otherwise it reads the XML while preserving existing blank text, finds paragraph and table-cell containers, and asks _coalesce_in to process insertions and deletions inside each container. If any elements were merged, it writes the updated XML back and returns the number of change elements absorbed.

**Call relations**: unpack_docx calls this when tracked-change coalescing is enabled. It breaks the document into likely places where changes appear, then delegates the author- and adjacency-aware merging to _coalesce_in.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This function looks inside one container for tracked insertions or deletions of a chosen type and groups them by author. It prepares only compatible change elements for possible merging.

**Data flow**: It receives an XML container and a change type, either insertion or deletion. It gathers direct child elements of that type, groups them by their author attribute, passes each author group to _merge_change_run, and returns the total number of elements that were merged away.

**Call relations**: _coalesce_tracked_changes calls this for each paragraph or table cell and for both insertion and deletion changes. It narrows the problem down so _merge_change_run can focus on one same-author sequence at a time.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This function merges a sequence of tracked-change elements when they are truly adjacent in the XML. It keeps the first element as the anchor and moves later change content into it.

**Data flow**: It receives a list of same-type, same-author change elements. Starting with the first as the anchor, it checks each later element. If the later element is adjacent to the anchor, its children are moved into the anchor, its leftover tail text is preserved in the parent, and the later element is removed. If it is not adjacent, that later element becomes the new anchor. The function returns how many elements were absorbed.

**Call relations**: _coalesce_in calls this after grouping changes by author. It relies on _changes_adjacent to avoid merging edits that only look related but are separated by real content.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This function answers a safety question: are two tracked-change elements next to each other except for whitespace or comments? It prevents the script from merging changes across real document content.

**Data flow**: It receives two XML elements. It checks that they share a parent, finds their positions among that parent’s children, looks at anything between them, and returns true only if the gap contains no meaningful element and no non-whitespace text. If the parent or positions cannot be found, it returns false.

**Call relations**: _merge_change_run calls this before combining two tracked changes. Its yes/no answer protects the document structure by allowing merges only when the changes are genuinely consecutive.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `document editing / CLI run`

A DOCX file is really a zip folder full of XML files. Adding a comment is not just adding one bit of text: Word keeps comment text, thread information, durable IDs, timestamps, file relationships, and content-type records in separate places. This script is the helper that fills in that boilerplate so a later step can attach the comment to a specific piece of document text.

The main flow is like preparing a new library card and filing it in several catalog drawers. If this is the first comment in the document, the script copies template comment files into the word folder and registers those files with the DOCX package. It then creates a random paragraph ID and durable ID, stamps the comment with the current UTC time, builds the XML entry for the visible comment text, and appends matching entries to Word’s extended comment, ID, and extensible metadata files.

For replies, it also looks up the parent comment’s paragraph ID so Word can display the reply as part of a thread. One important caution: the script only adds the comment records. It does not edit document.xml to mark the actual highlighted text. Instead, when run from the command line, it prints the marker XML that must be inserted separately. Also, if a reply names a missing parent, the main comments.xml entry may already have been written before the error is returned.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `on-demand document cleanup`

This file solves a practical document-cleanup problem: Word files can contain “tracked changes,” which are edits shown as suggestions rather than final text. Many automated workflows need the final version only. Without this script, a person would need to open the file in an office app and click “accept all changes” manually.

The script first checks that the input file exists and is a DOCX file. It then copies the original file to the requested output path, so the original is left untouched. Next, it prepares a temporary LibreOffice user profile. Think of this profile like a small throwaway workspace where LibreOffice can store its settings and macros. Into that workspace, the script writes a LibreOffice Basic macro, which is a small script LibreOffice can run internally. The macro tells LibreOffice to accept all tracked changes, save the document, and close it.

Finally, the script starts LibreOffice in “headless” mode, meaning it runs without opening a visible window. One important detail is that LibreOffice may hang even after successfully saving the file. Because of that, if the process times out, this script treats the operation as successful rather than failed. The script can also be run directly from the command line with an input and output DOCX path.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This builds the environment settings used when launching LibreOffice. It forces LibreOffice to use a non-graphical display backend, which helps it run safely in headless mode without needing a desktop window.

**Data flow**: It starts with the current process environment variables, copies them, then adds or replaces one setting: `SAL_USE_VCLPLUGIN` is set to `svp`. The result is a dictionary of environment variables that can be passed to LibreOffice when starting it.

**Call relations**: Whenever this file starts LibreOffice, it asks `_soffice_env` for the right environment first. `_ensure_macro` uses it while preparing the LibreOffice profile, and `accept_tracked_changes` uses it while running the macro on the copied document.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This creates the command-line argument that tells LibreOffice which temporary user profile to use. That keeps this automation separate from any normal LibreOffice settings on the machine.

**Data flow**: It reads the fixed profile directory path defined near the top of the file and formats it into LibreOffice’s expected `-env:UserInstallation=...` argument. The output is a single string ready to be placed in a LibreOffice command.

**Call relations**: Both parts of the LibreOffice flow need the same profile location. `_ensure_macro` uses this argument when initializing the profile, and `accept_tracked_changes` uses it again when running the macro against the output document.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure LibreOffice has the small internal macro needed to accept tracked changes. If the macro is already installed in the temporary profile, it reuses it; otherwise, it creates the profile and writes the macro file.

**Data flow**: It checks whether the macro file already exists and contains the expected macro name. If not, and the macro folder does not yet exist, it briefly starts LibreOffice headlessly to initialize the profile. Then it creates the macro directory if needed and writes the macro XML text into `Module1.xba`. It returns `True` when the macro is in place.

**Call relations**: `accept_tracked_changes` calls this before trying to process a document, because LibreOffice cannot accept the changes unless the macro is available. Inside this setup step, `_ensure_macro` asks `_profile_arg` for the profile command-line option, asks `_soffice_env` for safe headless environment settings, and uses `subprocess.run` to let LibreOffice initialize its profile.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main worker function: it takes an input DOCX, creates an output copy, runs LibreOffice on that copy, and accepts all tracked changes. It returns a message saying either what went wrong or that the cleanup succeeded.

**Data flow**: It receives two file paths: the source DOCX and the desired output DOCX. It turns them into path objects, checks that the source exists and has a `.docx` extension, creates the output folder if needed, and copies the source file to the output location. It then ensures the LibreOffice macro is installed, builds a LibreOffice command, and runs it. If LibreOffice exits cleanly, or if it times out after likely saving the file, the function returns `None` plus a success message. If validation, copying, or LibreOffice execution fails in the checked cases, it returns `None` plus an error message.

**Call relations**: This function is the central path through the file and is what the command-line section calls when the script is run directly. It delegates setup details to `_ensure_macro`, uses `_profile_arg` and `_soffice_env` to build the LibreOffice launch, uses `shutil.copy2` to preserve the original while creating the output copy, and uses `subprocess.run` to hand the real document editing work to LibreOffice.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`entrypoint` · `user-invoked document packaging`

A DOCX file is really a ZIP archive: a bundle of XML files and related resources with a special layout that Microsoft Word understands. This script takes that already-unpacked folder layout and repacks it into a `.docx` file.

Before zipping the folder, it copies everything into a temporary staging area. This matters because the script edits XML files while cleaning them, and it should not change the user’s original working folder. Then it walks through the staged copy and finds XML files, including `.rels` relationship files, which tell Word how parts of the document connect to each other.

For each XML file, it removes whitespace that is only formatting noise, such as indentation between tags. It is careful not to strip spaces inside Word text fields, because those spaces may be visible document content. Think of it like folding a map more neatly without erasing any street names.

Finally, it creates the output folder if needed, writes all staged files into a compressed ZIP archive, and gives that archive the `.docx` name. If the input is not a folder or the output name does not end in `.docx`, it returns a clear error instead of producing a bad file.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This is the main worker that turns an unpacked DOCX directory into a `.docx` file. It checks that the input and output make sense, cleans the XML files in a safe temporary copy, and then creates the final compressed document package.

**Data flow**: It receives two paths: the source folder and the desired output file. It first checks that the source is a directory and that the output ends in `.docx`; if either check fails, it returns no file path and an error message. If the checks pass, it copies the source into a temporary staging folder, asks `_strip_xml_whitespace` to clean each XML and relationship file there, zips the staged files into the destination, and returns the destination path plus a success message.

**Call relations**: When this script is run from the command line, the parsed arguments are passed into `pack_docx`. Inside its packaging flow, it calls `_strip_xml_whitespace` for each XML-like file before handing the staged folder to the ZIP writer. It also relies on standard library tools to make a temporary workspace, copy the directory, and write the final archive.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper removes XML whitespace that only exists to make the file human-readable, while preserving spaces that may be part of the actual Word document text. It keeps the document content intact but makes the XML cleaner before packaging.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through each element, and removes text or tail spacing when that spacing is only blank indentation. It skips Word text elements such as normal text, deleted text, and instruction text, because whitespace there can change what the document says. It then writes the cleaned XML back to the same staged file; if parsing or writing fails, it prints an error to standard error and raises the failure so packaging stops.

**Call relations**: `pack_docx` calls this function while preparing the temporary copy of the DOCX folder. `_strip_xml_whitespace` does the careful XML cleanup step, then hands control back so `pack_docx` can continue zipping the cleaned staged files into the final `.docx` archive.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).


### PowerPoint repair and assembly
PPTX scripts unpack presentations, repair known format issues, edit or preview slides, and repack the presentation archive.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty package marker file. In Python, a file named `__init__.py` tells Python that the folder should be treated as an importable package. That matters because the surrounding project can then refer to code inside `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts` using normal Python import paths. Think of it like putting a label on a drawer: the drawer may contain the actual tools elsewhere, but the label tells the system that the drawer is part of the organized workspace. Nothing runs from this file directly, and it does not define any functions, classes, or settings. Without it, depending on the Python version and import style used by the project, code in this folder might be harder or impossible to import reliably.


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`io_transport` · `manual document unpacking / command-line use`

A .pptx file looks like one file, but it is really a ZIP archive, which is like a folder packed into a single box. Inside are many XML files, which are text files that describe slides, relationships, styles, and other presentation parts. This file opens that box, copies its contents into a normal directory, and then makes the XML easier to work with.

The main flow starts by checking that the input exists and has the .pptx ending. It then creates the output folder if needed and extracts the ZIP contents there. After extraction, it finds both .xml files and .rels files. A .rels file is also XML; it describes links between parts of the PowerPoint package.

For each XML-like file, the script first tries to reformat it with consistent indentation, so nested elements are easier to read. Then it replaces curly “smart quotes” with XML entity text such as &#x201C;. That helps preserve those characters in a form that is safe and explicit for XML editing.

The script is forgiving: if one XML file cannot be formatted or read, it silently skips that cleanup step instead of stopping the whole extraction. If the input is not a real ZIP archive, it reports an error.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main worker for unpacking a PowerPoint file into a folder. Someone would use it when they need the presentation’s internal XML files laid out on disk in a readable, editable form.

**Data flow**: It takes a path to a .pptx file and a destination folder. It checks that the source file exists and looks like a PowerPoint file, creates the destination folder, extracts the ZIP contents, finds XML and relationship files, prettifies them, escapes smart quotes, and then returns either a small success result with the number of XML files found plus a message, or no result plus an error message.

**Call relations**: This function drives the whole unpacking process. During cleanup it calls _prettify_xml first to make each XML file readable, then _escape_smart_quotes to normalize curly quote characters. When the script is run from the command line, the bottom of the file calls this function and prints its message.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper makes one XML file easier for humans to read by adding consistent indentation. It is used after extraction because files inside Office documents are often compact or unevenly formatted.

**Data flow**: It receives the path to one XML-style file. It tries to parse the file as XML, indent the document with two spaces per level, and write the cleaned-up XML bytes back to the same file. If parsing or writing fails, it leaves the file as it was and does not report an error.

**Call relations**: extract_pptx calls this for every .xml and .rels file it finds in the extracted PowerPoint folder. It does not call other project code; it relies on the XML library to parse, indent, and serialize the file.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quote characters with explicit XML entity text. That makes those characters safer and more predictable when the XML is later edited or processed.

**Data flow**: It receives the path to one XML-style file, reads it as UTF-8 text, checks whether it contains curly single or double quotes, and if so replaces each one with its matching XML entity. It writes the changed text back to the same file. If reading or writing fails, it silently leaves the file unchanged.

**Call relations**: extract_pptx calls this after _prettify_xml for each extracted XML-related file. It is the final cleanup pass, focused only on quote characters rather than overall XML formatting.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`entrypoint` · `manual repair/CLI run`

A .pptx file is really a ZIP package full of XML files. This script opens that package, checks for known problems, and rewrites the package only if something needs fixing. It solves three practical issues. First, some generated presentations list slide master files in the package manifest even when those files are not actually present; PowerPoint may show a “cannot read” or “repair” warning for that. Second, it removes ZIP directory entries, which are folder markers inside the ZIP that do not belong in this kind of Office package. Third, it protects text that starts or ends with spaces or tabs by adding the XML marker xml:space="preserve". Without that marker, PowerPoint can quietly trim those spaces, which matters for indented code, aligned text, or carefully formatted labels. The script works like a careful repacker: it reads the original .pptx, decides what entries need changes, writes a temporary cleaned copy, then replaces the original file. If no problems are found, it leaves the file alone. If the replacement fails, it removes the temporary file so a half-finished repair is not left behind.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This helper finds text inside slide-related XML files where leading or trailing spaces might be lost by PowerPoint. It adds the standard XML instruction that tells PowerPoint to keep those spaces exactly as written.

**Data flow**: It receives a dictionary of PPTX package entries, where each name points to that file’s raw bytes. It looks only at slide, layout, master, and notes XML files, parses each one, scans text elements, and updates any text element that begins or ends with a space or tab but lacks xml:space="preserve". It returns a smaller dictionary containing only the changed files, plus a count of how many text elements were fixed.

**Call relations**: The main repair flow calls this after reading the PPTX contents. This helper uses lxml.etree.fromstring to turn XML bytes into a tree it can inspect, then lxml.etree.tostring to turn changed XML trees back into bytes. The repaired entries it returns are later written into the rebuilt PPTX by repair.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for one .pptx file. It checks whether the file exists, detects the known package and text-spacing problems, and replaces the file with a cleaned version when needed.

**Data flow**: It starts with a filename and turns it into a path. If the file is missing, it prints an error and returns False. Otherwise, it opens the .pptx as a ZIP file, reads its non-folder entries, records which slide master XML files really exist, removes manifest references to slide masters that are not present, asks _repair_whitespace_preservation to fix vulnerable text spacing, and decides whether any repair is needed. If repairs are needed, it writes a temporary ZIP without directory entries and with the corrected XML files, then moves that temporary file over the original. It prints either a no-op message or a repair summary and returns True when the run succeeds.

**Call relations**: This function is called by the script’s command-line block when someone runs repair.py with a PPTX filename. Inside the repair process it calls _repair_whitespace_preservation for the text-spacing check, uses zipfile.ZipFile to read and rebuild the PPTX package, uses regular expressions to find and remove bad manifest entries, and uses shutil.move to replace the original file with the repaired temporary copy.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command-line invocation`

A PowerPoint .pptx file is really a zip file full of XML files and resource files such as images, charts, themes, and slide notes. This script gives the project a practical toolbox for working with those pieces without opening PowerPoint. Its three jobs are: clean old unused files out of an unpacked presentation, add a new slide by copying an existing slide or starting from a slide layout, and create contact-sheet style thumbnail images of a presentation.

The clean command acts like a careful housekeeper. It reads the relationship files that say which parts of the presentation point to which other parts, finds slides that are no longer listed in the main presentation, removes leftover resources that nothing points to, and updates the content-type list so the package no longer advertises deleted files.

The add command creates a new slide file and registers it in the places PowerPoint expects. One important detail is that it prints the slide-list XML line the user still needs to add to presentation.xml, instead of editing that list itself.

The thumbnail command renders the presentation through external tools, turns slides into JPEG images, inserts gray placeholders for hidden slides, and lays everything out in one or more labeled grids.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so other code can inspect or change it. This is the common doorway into PowerPoint's XML files.

**Data flow**: It receives a file path. It asks lxml, an XML-reading library, to parse that file and then returns the root element of the parsed XML tree. It does not change the file.

**Call relations**: Many helper functions call this when they need to read relationship files, content-type files, or copied slide relationship files. It is paired with _write_xml, which saves changed XML back to disk.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Saves a changed XML tree back to a file. Other functions use it after removing or adding PowerPoint package entries.

**Data flow**: It receives an XML root element and a path. It converts the XML tree into UTF-8 bytes with an XML declaration, then writes those bytes to the path, replacing the file's previous contents.

**Call relations**: Cleanup and slide-adding helpers call this after they edit relationship or content-type XML. It completes the read-change-save cycle started by _parse_xml.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file inside the unpacked presentation that is mentioned by a relationship file. In PowerPoint packages, relationship files are like address books that say which files belong together.

**Data flow**: It receives the unpacked PPTX directory. It scans all .rels files, reads each relationship target, turns relative paths into paths relative to the unpacked folder, and returns a set of referenced files. Targets outside the folder are ignored.

**Call relations**: run_clean calls this during each cleanup pass. The returned set tells _remove_unreferenced_resources which files are still in use and which are safe to delete.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds the slide XML files that are actually part of the presentation's slide list. This prevents the cleaner from keeping old slide files just because they still exist on disk.

**Data flow**: It receives the unpacked PPTX directory. It reads the main presentation relationship file to map relationship IDs to slide filenames, then reads presentation.xml to find which relationship IDs appear in the slide list. It returns the filenames of the active slides.

**Call relations**: run_clean calls this first. Its result is handed to _remove_orphan_slides so that only slides missing from the real presentation order are removed.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from the special [trash] folder inside an unpacked presentation. This gives the script a simple way to empty a known holding area for unwanted files.

**Data flow**: It receives the unpacked PPTX directory. If a [trash] directory exists, it deletes every plain file inside it, removes the empty trash directory, and returns the relative names of what it deleted. If there is no trash folder, it returns an empty list.

**Call relations**: run_clean calls this after removing orphan slides. Its deletion list is combined with other removed files so content-type entries can be cleaned later.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that exist in ppt/slides but are not listed as active slides in the presentation. These are like loose pages left in a binder after the table of contents no longer mentions them.

**Data flow**: It receives the unpacked directory and the set of active slide filenames. It scans slide*.xml files, removes any inactive slide and its companion .rels file, then edits presentation.xml.rels to remove relationships pointing to deleted slides. It returns the relative paths it deleted.

**Call relations**: run_clean calls this using the active set from _active_slide_names. When it edits presentation relationships, it reads XML through _parse_xml and writes the cleaned version through _write_xml.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes resource files such as images, charts, drawings, themes, and notes that no remaining relationship points to. This shrinks and tidies an unpacked presentation after slides have been removed or changed.

**Data flow**: It receives the unpacked directory and a set of referenced paths. It checks known PowerPoint resource folders and removes files not present in that reference set. It also deletes relationship files whose parent resource file has disappeared. It returns a list of deleted relative paths.

**Call relations**: run_clean calls this after _collect_all_targets. Because deleting one file can make another file unreferenced, run_clean may call it repeatedly until there is nothing more to remove.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes entries from [Content_Types].xml for files that were deleted. This matters because a PPTX package has a catalog describing the type of each important part, and stale catalog entries can confuse readers.

**Data flow**: It receives the unpacked directory and a list of removed file paths. It opens [Content_Types].xml, removes Override entries whose PartName matches a deleted file, and writes the file back only if something changed.

**Call relations**: run_clean calls this at the end, after all deletion work is complete. It relies on _parse_xml and _write_xml to safely edit the XML catalog.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint folder. This is the main workhorse behind the clean command.

**Data flow**: It receives the unpacked directory. It finds active slides, deletes inactive slides, empties [trash], repeatedly removes unreferenced resources until no more are found, then cleans stale content-type entries. It returns a combined list of everything it deleted.

**Call relations**: _cmd_clean calls this after checking that the folder exists. Inside, it coordinates the smaller cleanup helpers in the order needed to avoid leaving dangling references behind.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as slide7.xml. This avoids overwriting an existing slide file when adding a slide.

**Data flow**: It receives the slides directory. It looks at existing files named like slide<number>.xml, extracts their numbers, and returns one higher than the largest number. If there are no slides, it returns 1.

**Call relations**: _create_from_layout and _clone_existing call this before writing a new slide file. It supplies the new filename used by the rest of the add flow.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to [Content_Types].xml so the PPTX package declares that the new file is a slide. Without this, PowerPoint may not recognize the new part correctly.

**Data flow**: It receives the unpacked directory and a slide filename. It reads [Content_Types].xml, checks whether that slide is already listed, and if not adds an Override entry with the slide content type, then writes the XML back.

**Call relations**: Both _create_from_layout and _clone_existing call this after creating the slide file. It uses _parse_xml and _write_xml, and creates a new XML element when registration is needed.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the main presentation to the new slide. In a PPTX, this relationship is the link that lets presentation.xml refer to the slide by an ID.

**Data flow**: It receives the unpacked directory and slide filename. It reads presentation.xml.rels, finds the highest existing rId number, and either returns an existing relationship ID for that slide or creates a new one pointing to it. It writes changes back and returns the relationship ID.

**Call relations**: _create_from_layout and _clone_existing call this after making the slide file and content-type entry. The returned ID is printed to the user as part of the slide-list XML they need to add.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for presentation.xml. This ID is separate from the relationship ID and is part of PowerPoint's main slide list.

**Data flow**: It receives the unpacked directory. It reads presentation.xml as text, finds existing slide IDs, and returns one higher than the largest. If none are found, it starts at 256, which is the usual minimum used by PowerPoint.

**Call relations**: _create_from_layout and _clone_existing call this near the end of adding a slide. They use the returned number in the XML snippet printed for the user.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide based on an existing slide layout. A layout is a template-like part that defines where title boxes, content areas, and styling can come from.

**Data flow**: It receives the unpacked directory and a layout filename. It checks that the layout exists, creates a new slide XML file from a blank template, writes a relationship from the new slide to the layout, registers the slide in package metadata, and prints the XML line needed to insert it into the presentation slide list. If the layout is missing, it prints an error and exits.

**Call relations**: run_add calls this when the source name looks like a slide layout file. It uses the numbering, registration, and ID helpers to make the new slide fit into the PPTX structure.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Creates a new slide by copying an existing slide file. This is useful when the easiest way to make a new slide is to duplicate one that already has the right design or content.

**Data flow**: It receives the unpacked directory and a source slide filename. It checks that the source exists, copies the slide XML to a new slide number, copies its relationship file if present, removes any notes-slide relationship from the copy, registers the new slide, and prints the XML line needed to add it to the presentation's slide list. If the source slide is missing, it prints an error and exits.

**Call relations**: run_add calls this for sources that are not layout filenames. It calls _next_slide_number, _register_content_type, _register_presentation_rel, and _next_slide_id to complete the package-side bookkeeping.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses which kind of slide-add operation to run: create from a layout or clone an existing slide. It is the main workhorse behind the add command.

**Data flow**: It receives the unpacked directory and the user's source string. If the source looks like slideLayout*.xml, it sends the request to _create_from_layout; otherwise it sends it to _clone_existing. It does not return a value; the chosen helper creates files and prints instructions.

**Call relations**: _cmd_add calls this after checking that the unpacked folder exists. It acts as the simple decision point before the more detailed add helpers do the file work.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a .pptx file to learn the real slide order and which slides are hidden. This lets the thumbnail grid match the presentation instead of just sorting slide files by name.

**Data flow**: It receives a .pptx path. It opens the zip file, reads the presentation relationships to map IDs to slide filenames, then reads presentation.xml to walk through the slide list in order. It returns a list of small records containing each slide's filename and whether it is hidden.

**Call relations**: run_thumbnail calls this before rendering images. Later, _pair_slides_with_images uses this order to match rendered images to slide labels and hidden-slide placeholders.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the presentation's visible slides into JPEG image files. It uses external programs because this script itself does not know how to visually draw a PowerPoint slide.

**Data flow**: It receives the .pptx path and a temporary working directory. It runs LibreOffice's soffice command in headless mode, meaning without opening a visible window, to convert the presentation to PDF. Then it runs pdftoppm to turn the PDF pages into JPEG files. It returns the sorted JPEG paths, or raises an error if conversion fails.

**Call relations**: run_thumbnail calls this inside a temporary directory. Its output images are later paired with slide-order information by _pair_slides_with_images.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a gray placeholder image for a hidden slide. This keeps hidden slides visible in the thumbnail grid without pretending they were rendered as normal slides.

**Data flow**: It receives image dimensions. It creates a gray RGB image and draws two diagonal lines across it, then returns the image object. It does not save the image itself.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden. The placeholder is then saved in the temporary work directory and included in the thumbnail grid.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches the slide-order list to the rendered JPEG files and inserts placeholders for hidden slides. This bridges the logical presentation order with the actual image files produced by rendering.

**Data flow**: It receives the ordered slide records, the list of rendered image paths, and a work directory. It uses the first rendered image size for hidden-slide placeholders when possible, then walks through the slide order. Hidden slides become generated placeholder images; visible slides consume the next rendered JPEG. It returns pairs of image path and label text.

**Call relations**: run_thumbnail calls this after extracting order and rendering images. It calls _make_hidden_placeholder for hidden slides and hands the finished pairs to _compose_grid.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one labeled contact sheet image from slide thumbnails. It is like arranging small printed photos on a white board with captions.

**Data flow**: It receives image-and-label pairs, a column count, and a cell width. It calculates thumbnail sizes, creates a white canvas, draws each label, resizes each slide image to fit, pastes it into place, and optionally draws a thin outline. It returns the completed grid image.

**Call relations**: run_thumbnail calls this once for each chunk of slides that should fit in a grid image. The returned image is then saved as a JPEG output file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Runs the full thumbnail-making process for a .pptx file. It creates one or more JPEG grids that show the slides in presentation order.

**Data flow**: It receives a .pptx path, an output prefix, and a column count. It extracts slide order, creates a temporary folder, renders visible slides to images, pairs images with slide labels and hidden placeholders, splits the result into chunks if needed, composes each grid, saves each JPEG, and returns the saved filenames. If no slides can be found, it prints an error and exits.

**Call relations**: _cmd_thumbnail calls this after validating the input file and limiting the column count. It coordinates all thumbnail helpers, from reading the PPTX structure through rendering and final image saving.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line clean subcommand. It checks the user's folder path and prints a readable report of what was removed.

**Data flow**: It receives parsed command-line arguments. It turns the folder argument into a Path, exits with an error if the folder does not exist, calls run_clean, and prints either the deleted file list or a message saying nothing was found.

**Call relations**: build_parser attaches this function to the clean subcommand. When the user runs slides.py clean, argparse dispatches here, and this wrapper hands the real cleanup work to run_clean.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line add subcommand. It validates the unpacked presentation folder before asking the add logic to create a slide.

**Data flow**: It receives parsed command-line arguments. It turns the folder argument into a Path, exits with an error if it does not exist, then calls run_add with the folder and source slide or layout name. The actual file changes happen inside run_add's chosen helper.

**Call relations**: build_parser attaches this function to the add subcommand. When the user runs slides.py add, argparse dispatches here and this wrapper passes control to run_add.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line thumbnail subcommand. It checks the input is a PowerPoint file, applies the column limit, runs thumbnail creation, and prints the output paths.

**Data flow**: It receives parsed command-line arguments. It validates that the input path exists and ends in .pptx, caps the requested column count, calls run_thumbnail, and prints the grids that were created. If thumbnail creation raises an error, it prints that error and exits.

**Call relations**: build_parser attaches this function to the thumbnail subcommand. When the user runs slides.py thumbnail, argparse dispatches here and this wrapper hands the heavy work to run_thumbnail.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface: the available subcommands, their arguments, help text, and which function each subcommand should run. This is the map that turns user text at the terminal into structured instructions.

**Data flow**: It takes no inputs. It creates an argparse parser, adds clean, add, and thumbnail subcommands with their expected arguments, attaches each subcommand to its command function, and returns the completed parser.

**Call relations**: The file's main block calls this when slides.py is run directly. The returned parser reads the user's command line and then calls the selected function such as _cmd_clean, _cmd_add, or _cmd_thumbnail.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`entrypoint` · `manual document packaging/export`

A .pptx file is really a ZIP archive: a compressed folder containing XML files, media, and relationship files that tell PowerPoint how the pieces fit together. This script is the “put it back in the box” step after someone has unpacked and possibly edited that folder.

First, it checks that the input is a real directory and that the output name ends in .pptx. Then it copies the whole folder into a temporary work area, so the original files are not changed. Before zipping everything, it walks through XML files and .rels files, which are relationship XML files used inside Office documents. It removes whitespace that exists only for formatting the XML source, like indentation and blank text between tags. Importantly, it avoids stripping text inside DrawingML text nodes, because those may contain real slide text that must be preserved.

Finally, it creates the destination folder if needed and writes every file from the temporary copy into a compressed ZIP archive with the .pptx name. If used from the command line, it prints a success or error message and exits with an error code when packing fails validation.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: Builds a .pptx file from a directory that contains the unpacked contents of a PowerPoint file. It is the main reusable function for turning edited presentation parts back into something PowerPoint can open.

**Data flow**: It receives a source folder path and an output file path. It checks that the source is a directory and that the output ends in .pptx; if either check fails, it returns no output path and an error message. If the checks pass, it copies the source into a temporary folder, asks _condense_xml to clean every XML and relationship file, zips the cleaned copy into the destination .pptx, and returns the destination path plus a success message.

**Call relations**: This is the top-level packing routine used by the command-line part of the script. During the packing flow, it relies on standard library tools to create a temporary workspace, copy the input folder, and write the final ZIP archive. For each XML-like file it finds, it hands off to _condense_xml so the archive is cleaned before compression.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: Cleans one XML file by removing formatting-only whitespace while keeping real text content safe. This helps produce cleaner, smaller Office XML without accidentally erasing slide text.

**Data flow**: It receives the path to one XML or .rels file. It parses the file into an XML tree, walks through each element, skips protected text elements, removes blank-only text and tail spacing, removes unusual callable-tag child nodes, then writes the cleaned XML bytes back to the same file. If parsing or writing fails, it prints a clear error message to standard error and raises the problem again so the caller knows packing did not finish cleanly.

**Call relations**: assemble_pptx calls this function once for each XML and relationship file in the temporary copy of the presentation. It does the careful cleanup step before assemble_pptx writes the files into the final .pptx archive.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### Spreadsheet recalculation
XLSX scripts provide shared LibreOffice helpers and a command-line recalculation flow for refreshing workbook formulas.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/__init__.py`

`other` · `import setup`

This file is intentionally empty. In Python, a file named `__init__.py` tells Python that the surrounding folder should be treated as an importable package. You can think of it like putting a label on a drawer: the drawer may contain useful tools, and the label lets the rest of the system find them by name. Here, the drawer is the `scripts` folder inside the Office XLSX document skill. Without this file, some Python environments or tooling might not recognize the folder as a package, which could make imports less reliable. There is no runtime logic here: it does not read files, create objects, start services, or change settings. Its value is structural. It helps the larger extension keep its XLSX-related script code organized and reachable.


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`util` · `used whenever document scripts need to locate LibreOffice macros or run LibreOffice headlessly`

Some spreadsheet tasks in this project need LibreOffice to do work in the background, such as opening or processing an XLSX file without a person clicking around in the app. This file is the common toolbox for that job. It prepares the environment LibreOffice should run in, finds the folder where LibreOffice macros live, and starts the `soffice` command-line program safely.

The most important detail is the environment setting `SAL_USE_VCLPLUGIN=svp`. In plain terms, this tells LibreOffice to use a simple off-screen display backend instead of trying to connect to a normal desktop window system. Without this, automated runs can fail on servers or other headless machines where there is no graphical screen.

The file also hides operating-system differences. LibreOffice stores its macro files in different user folders on macOS and Linux, so `macro_dir` chooses the right path based on the current platform. If the platform is unknown, it falls back to the Linux-style path.

Finally, `run_soffice` builds a command beginning with `soffice`, adds the caller’s arguments, and runs it while capturing its output. This gives other scripts one simple, consistent way to ask LibreOffice to do work.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Creates the set of environment variables LibreOffice should see when it runs. Its key job is to force LibreOffice into a headless-friendly mode so it does not need a visible desktop window.

**Data flow**: It takes no input. It starts with a copy of the current process environment, adds or replaces the `SAL_USE_VCLPLUGIN` setting with `svp`, and returns the modified environment dictionary. It does not change the real process environment directly; it prepares a copy for a child process.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls `soffice_env` to get the right environment. That environment is then handed to `subprocess.run` so the external `soffice` program starts with the headless-friendly setting already in place.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Returns the folder where LibreOffice expects user macros to be stored on the current operating system. This lets other scripts find or place macro files without hard-coding separate Linux and macOS paths everywhere.

**Data flow**: It takes no input. It asks the system what operating system is running, chooses the matching macro-folder template, expands `~` into the current user’s home directory, and returns the result as a `Path` object, which is a convenient file-path value.

**Call relations**: This helper stands on its own in this file. Internally it asks `platform.system` for the operating-system name, then wraps the chosen expanded path with `pathlib.Path` so callers receive a path object rather than a raw string.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the LibreOffice command-line program, `soffice`, with caller-supplied arguments. It captures the program’s text output and can stop waiting after a caller-provided timeout.

**Data flow**: It receives a list of command arguments and, optionally, a timeout in seconds. It builds a command that starts with `soffice`, adds the provided arguments, prepares the special LibreOffice environment using `soffice_env`, and runs the external program. It returns a `CompletedProcess` object containing the exit status plus captured standard output and error text.

**Call relations**: Other scripts use this as the doorway to LibreOffice. `run_soffice` first calls `soffice_env` so the process is suitable for headless use, then hands the final command, environment, timeout, and output-capturing settings to `subprocess.run`, which actually launches and waits for LibreOffice.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on demand when recalculating an Excel workbook`

Excel files often contain formulas whose saved results may be stale, especially if the file was created or edited by automation. This file solves that by using LibreOffice in headless mode, meaning LibreOffice runs in the background without opening a visible window. It installs a small LibreOffice macro if needed, asks LibreOffice to open the workbook, recalculate every formula, save, and close it.

After recalculation, the script inspects the workbook for common spreadsheet error values such as #REF!, #DIV/0!, and #VALUE!. It reports how many errors remain and where they appear, using sheet names and cell addresses. It also counts how many formulas are in the workbook, so the caller can see the scale of what was checked.

One important detail is table styling. Excel workbooks are actually ZIP archives containing XML files. LibreOffice can sometimes disturb the small XML element that stores table style information. To avoid accidental visual changes, the script takes a snapshot of table style snippets before recalculation and restores them afterward. In everyday terms, it is like taking a photo of a table setting before cleaning the room, then putting the plates back exactly where they were.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the small Basic macro needed to recalculate and save the workbook. Without this, the script could launch LibreOffice but would not have a reliable command to tell it to recalculate everything.

**Data flow**: It looks in LibreOffice's macro folder for a file named Module1.xba. If the file already contains the expected RecalculateAndSave macro, it reports success. If the macro folder does not exist yet, it briefly starts LibreOffice so the profile folders are created, then writes the macro file. The output is true if setup worked and false if writing the macro failed.

**Call relations**: The main recalc flow calls this before touching the spreadsheet. It relies on the helper functions from _soffice to find the macro folder and build the right LibreOffice environment, and it may start LibreOffice once through subprocess.run to initialize the macro location.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: Copies the table style information from inside the Excel file before LibreOffice changes the file. This protects formatting details that LibreOffice might otherwise rewrite or drop.

**Data flow**: It receives a workbook path, opens the .xlsx file as a ZIP archive, and looks through table XML files under xl/tables/. When it finds a tableStyleInfo element, it stores that XML snippet by filename. It returns a dictionary mapping each table file to its original style snippet.

**Call relations**: The recalc function calls this just before running LibreOffice. The saved style snippets are later passed to _restore_table_styles so the workbook keeps its original table styling after formulas are recalculated.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: Puts one saved table style snippet back into a table XML file. It is the small repair step used while rebuilding the Excel file after LibreOffice has saved it.

**Data flow**: It receives the XML bytes for one table file and the saved table style XML bytes. If a table style element is already present, it replaces it. If it is missing, it inserts the saved style just before the closing table tag. It returns the corrected XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file that had a saved style. It does not work on the whole workbook by itself; it is the focused helper that patches one piece of XML at a time.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: Restores saved table styling back into the recalculated Excel file. This keeps formula recalculation from causing unrelated formatting damage.

**Data flow**: It receives a workbook path and the earlier snapshot of table style snippets. If there are no saved styles, it does nothing. Otherwise it opens the workbook ZIP, writes a temporary copy, patches matching table XML files with _patch_table_style, then replaces the original file with the repaired copy. If something goes wrong, it removes the temporary file when possible.

**Call relations**: The recalc function calls this after LibreOffice finishes successfully. It uses _patch_table_style for each table that needs repair, then uses ZIP and file-moving tools to safely rewrite the workbook.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: Looks through the recalculated workbook for visible spreadsheet error values. This tells the caller whether recalculation succeeded cleanly or left broken formulas behind.

**Data flow**: It receives a workbook path and opens it with computed values loaded rather than formulas. It checks every cell in every worksheet. If a cell contains text matching a known Excel error, it records the sheet name and cell coordinate under that error type. It returns a dictionary of error types to cell locations.

**Call relations**: The recalc function calls this after LibreOffice has saved the recalculated workbook and after table styles have been restored. Its results become the error summary returned to the command-line caller or any code using recalc directly.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: Counts how many formula cells are present in the workbook. This gives useful context for the result, such as whether zero errors came from checking many formulas or from having no formulas at all.

**Data flow**: It receives a workbook path and opens it with formulas preserved. It walks every cell in every worksheet and counts string values that start with '='. It closes the workbook and returns the total count.

**Call relations**: The recalc function calls this near the end, after scanning for errors. The count is included in the final report alongside the error totals.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: Runs the complete workbook recalculation job and returns a structured result. It is the main reusable function for callers that want to refresh an Excel file and learn whether formula errors remain.

**Data flow**: It receives a filename and an optional timeout. First it checks that the file exists, prepares the LibreOffice macro, and saves table style snippets. Then it runs LibreOffice headlessly with the macro command. If LibreOffice times out or fails, it returns an error message. If recalculation succeeds, it restores table styles, scans for spreadsheet errors, counts formulas, and returns a dictionary with status, error totals, formula totals, and a compact error summary.

**Call relations**: main calls this when the script is run from the command line. Inside the workflow, it coordinates all helper functions: _ensure_macro prepares LibreOffice, _snapshot_table_styles and _restore_table_styles protect formatting, _scan_errors checks the recalculated values, and _count_formulas adds context to the final report.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for the script. It reads the file path and optional timeout from the user's command, runs recalculation, and prints the result as JSON.

**Data flow**: It reads command-line arguments from sys.argv. If no workbook path is provided, it prints usage instructions and exits with an error code. Otherwise it converts the optional timeout to a number, calls recalc, formats the returned dictionary as pretty JSON, and prints it to standard output.

**Call relations**: This function is used only when the file is executed as a script. It is the thin outer wrapper around recalc, turning command-line input into a recalculation request and turning the returned result into text that other tools or people can read.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).


### PDF form and page tools
PDF scripts inspect and fill native or layout-based forms and render pages into images for visual review.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command-line PDF form detection, extraction, and filling`

Many PDFs have real form fields built into them: text boxes, checkboxes, radio buttons, and dropdown-style choices. This file gives the project a way to use those fields directly instead of drawing text on top of the page by guesswork. Without it, automated PDF filling would be less reliable, because the system would not know the field names, valid checkbox values, or where each field lives on the page.

The file uses pypdf, a Python library for reading and writing PDFs. It first reads the PDF’s form structure, called an AcroForm. If the PDF is unusual and has form widgets on pages but no normal field list, it falls back to scanning page annotations directly. A widget is the visible PDF object that acts like a form control, similar to the physical button or blank line on a paper form.

The extracted fields are turned into simple Python data objects, then into JSON. The JSON records each field’s name, type, page, position, and allowed values where needed. When filling a form, the file reads that same kind of JSON, checks that each requested value is valid, writes the values into the matching PDF fields, and saves a new PDF. It is careful about checkboxes and radio buttons because PDFs store their “on” values using internal names that are often not obvious.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has visible form widgets even when they are not listed in the PDF’s normal form directory. This helps detect fillable forms that are structured in a non-standard way.

**Data flow**: It receives an opened PDF reader. It looks through each page’s annotations, checks whether any annotation is a widget with a field type, and returns true as soon as it finds one. If it finds none, it returns false and changes nothing.

**Call relations**: The detect command calls this after checking the normal field list. It is the backup check that lets the tool say “fillable fields detected” even for PDFs whose fields are not properly registered in the usual place.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a PDF field by walking up through its parent fields. This matters because PDFs can store field names in pieces, like a folder path made from nested labels.

**Data flow**: It receives one annotation dictionary. Starting at that annotation, it collects each available name part from the current object and its parents, reverses them into top-to-bottom order, joins them with dots, and returns the full name. If no name parts exist, it returns nothing.

**Call relations**: The AcroForm extraction path uses this while scanning page annotations. It lets the extractor match a visible widget on a page back to the field metadata found in the PDF’s form directory.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field information into one of this file’s simpler field objects. It hides the PDF’s short internal type codes behind plain kinds such as text, checkbox, and choice.

**Data flow**: It receives a raw PDF field dictionary and the field’s name. It reads the PDF field type, chooses the right builder for button or choice fields, or creates a plain text or unknown field object. The result is a FormField-style object that the rest of the file can use consistently.

**Call relations**: Both extraction paths call this when they discover a field. It hands checkbox-like fields to _build_checkbox and choice-like fields to _build_choice, so specialized details are captured before the field is added to the extracted list.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which internal value means checked and which means unchecked. This is important because PDFs often use custom names for the checked state.

**Data flow**: It receives a raw PDF button field and a name. It reads the available states from the PDF, chooses an on value and an off value when possible, warns if the states look non-standard, and returns a CheckboxField object.

**Call relations**: _build_field_from_dict calls this whenever it sees a PDF button field. The resulting checkbox metadata later helps extraction output useful JSON and helps fill validation reject impossible checkbox values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a choice field, such as a dropdown or list. It records both the stored value and the human-readable text when the PDF provides both.

**Data flow**: It receives a raw PDF choice field and a name. It loops through the PDF’s listed states, converts each one into a simple value/text pair, and returns a ChoiceField object containing all available choices.

**Call relations**: _build_field_from_dict calls this when it sees a PDF choice field. The extracted choices later appear in JSON and are used to check that fill input only uses allowed values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a checkbox’s checked value when the earlier metadata did not already reveal it. It looks at the checkbox’s visual appearance settings, where PDFs often store the checked-state name.

**Data flow**: It receives a resolved PDF annotation and a CheckboxField object. If the checkbox already has an on value, it does nothing. Otherwise it looks for appearance keys other than /Off and writes the first such key back into the CheckboxField.

**Call relations**: The widget-scanning extractor calls this after building a checkbox field. It is a repair step for orphaned or unusual widgets where the standard field-state list is missing.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from PDF coordinates into a more page-layout-friendly coordinate style. PDFs measure upward from the bottom of the page, while many layout tools think downward from the top.

**Data flow**: It receives a rectangle and the page height. It converts the rectangle numbers to floats, flips the vertical coordinates around the page height, and returns a new rectangle list with top-based vertical positions.

**Call relations**: The field extractors and radio option collector call this whenever they record where a field appears on a page. It makes field positions easier for other tools or humans to interpret.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Finds form fields by scanning the visible widgets on each PDF page. This is the fallback path for PDFs whose form fields are present on the page but not listed in the normal AcroForm field directory.

**Data flow**: It receives an opened PDF reader. It walks through every page and annotation, keeps only widget annotations with a field type and name, builds a field object, records its page and rectangle, fills checkbox on-values when needed, and returns the collected list.

**Call relations**: _extract_from_acroform calls this when the PDF reader cannot provide normal field metadata. Inside the scan it relies on _build_field_from_dict, _flip_rect, and _extract_checkbox_on_value to turn raw PDF details into usable field descriptions.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the useful form-field map from a PDF using the standard AcroForm structure when available. This is the main discovery routine used before exporting fields or filling values.

**Data flow**: It receives an opened PDF reader. It asks pypdf for the form fields, falls back to widget scanning if none are listed, builds simple field objects, scans pages to attach page numbers and positions, collects radio-button options, skips fields that cannot be found on a page, sorts the final list, and returns it.

**Call relations**: The extract and fill commands both call this soon after opening a PDF. It coordinates several helpers: _full_field_name to match widgets to names, _build_field_from_dict to simplify field records, _collect_radio_option for radio groups, _flip_rect for page positions, and _extract_from_widgets for fallback extraction.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one visible radio-button option to its radio group. Radio buttons are special because several clickable widgets share one field name, and each widget represents a different allowed value.

**Data flow**: It receives a radio widget annotation, the group name, the page number information, the page height, and the growing dictionary of radio groups. It finds the one non-off appearance value, creates the group if needed, flips the widget rectangle, and appends that option to the group’s option list.

**Call relations**: _extract_from_acroform calls this while scanning page annotations for fields that looked like radio groups. It hands back its work by mutating the shared radio_groups dictionary that later becomes part of the final extracted field list.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a consistent reading order for extracted fields. It sorts fields by page, then roughly by row, then by left-to-right position.

**Data flow**: It receives a field object. It chooses the field’s own rectangle, or the first radio option’s rectangle for a radio group, rounds the vertical position into a coarse row, and returns a tuple used for sorting.

**Call relations**: The AcroForm extractor uses this as the sorting rule before returning fields. This makes the JSON output easier to read because fields appear in page order rather than in whatever internal order the PDF used.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Turns one internal field object into a JSON-friendly dictionary. This is the final cleanup step before extracted fields are written to disk.

**Data flow**: It receives a FormField or one of its specialized versions. It writes common details such as name, kind, page, and rectangle, then adds checkbox values, radio options, or choice lists when those apply. It returns a plain dictionary that json.dumps can serialize.

**Call relations**: The extract command calls this for every field returned by _extract_from_acroform. It forms the bridge between the program’s Python data objects and the JSON file that users or other tools can edit.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a requested value is allowed for a specific field. This prevents writing invalid checkbox, radio, or choice values into the PDF.

**Data flow**: It receives a field description and a proposed string value. For checkboxes it compares against the on and off values; for radio groups and choices it compares against the known option values. It returns an error message if the value is invalid, or nothing if the value is acceptable.

**Call relations**: _validate_fill_entries calls this while reviewing the user’s fill JSON. It is the field-type-specific check that catches mistakes before cmd_fill writes the output PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the detect command, which tells the user whether a PDF appears to contain fillable native form fields. This helps decide whether to use this tool or a different layout-based approach.

**Data flow**: It receives the command arguments. It expects one PDF path, opens that PDF, checks the normal field list and then the orphaned-widget fallback, prints the result, and exits with an error if the arguments are wrong.

**Call relations**: main dispatches here when the user runs the detect subcommand. cmd_detect uses _has_orphaned_widgets only if the standard pypdf field check is not enough to prove the PDF is fillable.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract command, which writes a PDF’s fillable field information to a JSON file. Users can inspect this JSON to learn the exact field names and allowed values before filling the form.

**Data flow**: It receives the command arguments. It expects an input PDF path and an output JSON path, opens the PDF, extracts the fields, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written.

**Call relations**: main dispatches here when the user runs the extract subcommand. cmd_extract relies on _extract_from_acroform for discovery and _field_to_dict for JSON-shaped output.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill command, which takes user-provided field values and writes them into a new PDF. It is the command that actually produces a completed form.

**Data flow**: It receives the command arguments. It expects an input PDF, a values JSON file, and an output PDF path; reads the values; extracts field metadata from the input PDF; validates names, pages, and allowed values; groups values by page; asks pypdf to update the fields; then writes the finished PDF to disk.

**Call relations**: main dispatches here when the user runs the fill subcommand. cmd_fill first calls _extract_from_acroform so it knows what fields exist, then calls _validate_fill_entries before handing page-by-page values to pypdf’s writer.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks the whole fill-input JSON before any PDF is written. It makes sure every named field exists, is on the expected page if a page is supplied, and receives a valid value.

**Data flow**: It receives a list of fill entries and a lookup table of known fields by name. It loops through each entry, prints clear errors for unknown names, wrong pages, or invalid values, and returns true if any error was found. It does not change the PDF or the input data.

**Call relations**: cmd_fill calls this as a safety gate. For entries that include a value, it delegates the type-specific value check to _validate_fill_value.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Provides the script’s command-line entry point. It reads the first command word and sends the remaining arguments to the matching command function.

**Data flow**: It reads sys.argv, checks that the user supplied a known subcommand, prints a usage message and exits on bad input, or calls the selected command with the rest of the arguments. It returns nothing when the command finishes normally.

**Call relations**: Python calls this when the file is run directly as a script. It dispatches to cmd_detect, cmd_extract, or cmd_fill through the SUBCOMMANDS table.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line execution`

Many PDF forms are only pictures or printed layouts, not true fillable forms. That means software cannot simply ask the PDF, “where is the name field?” This file helps bridge that gap. First, it can inspect a PDF page and record practical landmarks: words, long horizontal lines, small square boxes that look like checkboxes, and row-like spaces between lines. That output becomes a JSON file, a plain text data file that other tools or people can edit.

It also supports a preview step. Given field definitions and an image of a page, it draws red boxes where text would go and blue boxes around labels. This is like placing transparent sticky notes on a photocopy before writing on the real document.

Finally, it can fill the PDF. It reads a fields JSON file, checks for obvious problems such as text boxes that are too short or boxes that overlap, converts the field coordinates into PDF annotation coordinates, and adds FreeText annotations, which are visible text blocks placed on the page. A small helper, CoordMapper, matters because images and PDFs often count vertical position in opposite directions: images usually start at the top-left, while PDF annotation coordinates are based from the bottom-left. Without this file, the project would lack a practical way to locate, preview, and place text on static PDF forms.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: Converts a rectangle from the coordinate system used by the field data into the coordinate system needed for PDF annotations. This is necessary because image coordinates and PDF coordinates measure vertical position differently.

**Data flow**: It receives a box as four numbers, plus the mapper’s stored PDF size and source size. If the box came from an image, it scales the box to the PDF page size and flips the vertical direction. If the box already uses PDF-style page measurements, it only flips the vertical direction into the rectangle format expected by the annotation writer. It returns a four-number rectangle ready to pass to the PDF library.

**Call relations**: During PDF filling, _validate_and_fill creates a CoordMapper for the page being written. It then asks this method to turn each field’s content area into a correct PDF annotation rectangle before creating the visible text annotation.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: Reads one PDF page and gathers the layout clues that are useful for finding form fields. It looks for text, long horizontal rules, and small square boxes that likely represent checkboxes.

**Data flow**: It receives a page object from pdfplumber and the page number. It creates a PageLayout record, scans the page’s line objects for long horizontal lines, scans rectangle objects for checkbox-sized squares, and asks the page to extract words. It returns a PageLayout containing the page size and all the clues it found.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. The extracted page layout is then enriched with row ranges and later turned into JSON by cmd_extract.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: Finds the vertical spaces between long horizontal lines on a page. These spaces often correspond to rows in a printed form or table.

**Data flow**: It reads the horizontal rule positions already stored in a PageLayout. It sorts their vertical positions, pairs each line with the next one, and adds a row range with a top, bottom, and height. It changes the PageLayout in place and does not return a separate value.

**Call relations**: _extract_all_pages calls this after _extract_page has found the page’s horizontal lines. The resulting row ranges become part of the JSON produced by the extract command.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: Scans an entire PDF and builds layout information for every page. It is the main worker behind the extract command.

**Data flow**: It receives the path to a PDF file. It opens the file with pdfplumber, walks through the pages in order, extracts each page’s layout, computes row ranges for that page, and gathers all PageLayout records into a list. It returns that list.

**Call relations**: cmd_extract calls this when the user runs the extract subcommand. It delegates the detailed per-page scanning to _extract_page and the row calculation to _compute_row_ranges.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: Turns PageLayout objects into plain dictionaries that can be written as JSON. This makes the extracted layout easy to save, inspect, and reuse outside Python.

**Data flow**: It receives a list of PageLayout records. For each page, it copies the page number, dimensions, text elements, horizontal rules, checkbox candidates, and row ranges into a dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after scanning the PDF. The result is passed to JSON writing so the layout data can become an output file.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: Checks a field-definition file for common placement problems, then writes the requested text into the PDF as visible annotations. It is the main worker behind the fill command.

**Data flow**: It reads the fields JSON file, opens the input PDF, and prepares a PDF writer. For each form field with text content, it checks whether the target box is tall enough for the chosen font size and whether it overlaps boxes already placed on the same page. If the checks pass, it maps the field’s rectangle into PDF annotation coordinates, creates a FreeText annotation with the chosen font, size, and color, and adds it to the right PDF page. If it finds errors, it prints them and stops. Otherwise, it creates the output folder if needed, writes the filled PDF, and prints a summary.

**Call relations**: cmd_fill calls this after checking the command-line arguments. Inside the filling loop it uses _rects_overlap to detect collisions and CoordMapper.to_annotation_rect to prepare each annotation’s position before handing the final annotation to the PDF writer.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: Answers the simple question: do two rectangular boxes overlap? This protects the filled PDF from placing text or labels on top of each other.

**Data flow**: It receives two boxes, each described by left, top, right, and bottom numbers. It compares their edges. If one box is fully to the side or above/below the other, it returns false; otherwise it returns true.

**Call relations**: _validate_and_fill calls this while checking each new field against boxes that have already been placed on the same page.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the command-line extract action. It turns a PDF into a JSON description of page layout clues.

**Data flow**: It receives the command arguments after the word extract. If the user did not provide an input PDF and output JSON path, it prints the correct usage and exits. Otherwise, it scans the PDF, converts the results to JSON-friendly dictionaries, writes the output file, and prints counts of the items found.

**Call relations**: main dispatches to this when the user runs layout.py extract. This command uses _extract_all_pages for the scanning work and _pages_to_dict to prepare the data for saving.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: Implements the command-line preview action. It draws the planned field boxes onto an image so a person can visually check placement before modifying a PDF.

**Data flow**: It receives the page number, a fields JSON path, an input image path, and an output image path. It loads the field definitions and the image, then draws red rectangles for content areas and blue rectangles for label boxes on the chosen page. It saves the marked-up image and prints how many fields it highlighted.

**Call relations**: main dispatches to this when the user runs layout.py preview. Unlike the fill path, this does not alter a PDF; it uses the same field-definition data to provide a visual safety check.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the command-line fill action. It checks the user’s arguments and starts the PDF filling process.

**Data flow**: It receives the command arguments after the word fill. If the user did not provide an input PDF, fields JSON, and output PDF path, it prints the correct usage and exits. Otherwise, it passes those three paths to _validate_and_fill.

**Call relations**: main dispatches to this when the user runs layout.py fill. It is a thin command wrapper around _validate_and_fill, which does the real validation and annotation writing.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: Chooses which command to run based on the first command-line word. It is the entry point when this file is run directly.

**Data flow**: It reads the process command-line arguments from sys.argv. If no known subcommand is provided, it prints a usage message and exits. If the subcommand is extract, preview, or fill, it passes the remaining arguments to the matching command function.

**Call relations**: When Python runs this file as a script, the bottom of the file calls main. main then dispatches to cmd_extract, cmd_preview, or cmd_fill, which perform the requested operation.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `command execution`

This script solves a simple but important problem: many systems can work with images more easily than with PDF files. A PDF is like a bound booklet, while this file tears that booklet into separate page pictures and saves each one as a PNG file.

When run, it takes two pieces of information: the path to an input PDF and the folder where the page images should be written. It first creates the output folder if it does not already exist. Then it uses `pdf2image`, an external library that renders PDF pages into image objects, to convert every page at a fixed resolution. Resolution here means how much visual detail is captured; this file uses 200 DPI, or dots per inch.

After each page is rendered, the script checks its width and height. If either side is larger than 1000 pixels, it shrinks the image while keeping the same shape, so the page does not become too large to store or process comfortably. Each page is then saved as `page_1.png`, `page_2.png`, and so on. The script prints progress messages so a user can see what was created and where it went.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts every page of a PDF file into a PNG image in a chosen output folder. It also limits very large page images to a maximum size so the results stay practical to use.

**Data flow**: It receives a PDF file path and an output folder path. It creates the folder if needed, asks `pdf2image.convert_from_path` to turn the PDF pages into images, resizes any oversized image, saves each page as a numbered PNG file, and prints a short report. Nothing is returned; the main result is the set of image files written to disk.

**Call relations**: The `main` function calls this after checking that the user supplied the right command-line arguments. Inside, it relies on `pathlib.Path` to work with the output folder and on the external `pdf2image` library to do the actual PDF-to-image conversion.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Acts as the command-line doorway for the script. It checks that the user provided exactly an input PDF and an output folder, then starts the rendering work.

**Data flow**: It reads the command-line arguments from `sys.argv`. If the arguments are missing or extra, it prints a usage message and exits with an error code. If the arguments are valid, it passes the PDF path and output folder path to `render`.

**Call relations**: This is called when the file is run directly as a script. Its job is to guard the entrance: it either stops early with `sys.exit` when the command is wrong, or hands the real work to `render` when the command is usable.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
