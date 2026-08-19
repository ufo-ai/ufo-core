# Office and PDF command scripts  `stage-15.1`

This stage is a toolbox of command scripts for working with Office files and PDFs. It is mostly behind-the-scenes support: tools the system can run when it needs to review, mark up, repair, or convert documents without asking a person to open each app.

The document-review scripts keep the review moving. One file names the saved state and log files, another defines what a review “issue” looks like, and the state manager records outlines, claims, checks, issues, and summaries. The annotation tools then copy those findings into PDFs, PowerPoint slides, or Excel cells as visible highlights and comments.

The Word tools unpack a DOCX into editable XML, add comment data, repack it into a DOCX, or accept tracked changes using LibreOffice. The PowerPoint tools do the same kind of unpack-and-pack work, plus cleanup, slide/contact-sheet creation, and repairs for fragile generated files. The Excel tools launch LibreOffice quietly and recalculate formulas.

The PDF tools fill real form fields, inspect or write onto static forms, and render pages as images for preview or later processing.

## Files in this stage

### Document review annotations
Shared review constants and issue models support review-state tracking and export of findings into PDF, PowerPoint, and Excel files.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a tiny settings file. It does not run any logic by itself. Instead, it defines two filename constants that other scripts can import and use.

The first name, `STATE_FILENAME`, points to the JSON file that stores the current document review state. In plain terms, this is like a bookmark: it lets the review process remember where it is or what has already happened.

The second name, `LOG_FILENAME`, points to a JSON Lines file, often written as `.jsonl`. A JSON Lines file stores one JSON record per line, which makes it useful for appending a running history of events. Here, it is meant to hold the review log.

The value of this file is consistency. Without it, different parts of the document review script might accidentally use slightly different filenames, causing lost state, split logs, or confusing behavior. By centralizing the names, the project has one small source of truth for these storage files.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review comment generation`

This file is a small but important piece of the document review workflow. A review tool may find many different problems in a document, such as spelling mistakes, unclear logic, private information, or inconsistent numbers. Each problem needs to be stored in a predictable way so other scripts can read it without guessing what fields exist. The `DocumentIssue` type is that shared checklist: it says an issue has an ID, a type, a severity, a description, a location, original and suggested text, and a few linking fields.

The file also keeps a simple lookup table, `ISSUE_TYPE_LABELS`, that turns internal issue codes like `spelling_grammar` into labels that make sense to a reader, such as `Spelling/Grammar`. This is like replacing warehouse part numbers with friendly shelf labels.

Finally, `format_comment` turns a single issue into the text that could be placed in a review comment. It starts with the friendly issue type and severity, adds the issue description, and optionally adds a suggested replacement if one is available. Without this file, different parts of the system might describe issues inconsistently, or comments might expose raw internal codes instead of clear human-facing text.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns one stored document review issue into a clear, human-readable comment. It is used when the system needs to show a reviewer what is wrong and, when available, what replacement text is suggested.

**Data flow**: It receives a `DocumentIssue`, which is a dictionary-like record containing details about one problem, plus a yes-or-no option called `include_suggestion`. It looks up a friendly label for the issue type, reads the severity and description, and builds a short comment. If suggestions are allowed and the issue has `new_text`, it adds a `Suggested:` line. The result is one formatted string; the original issue is not changed.

**Call relations**: When some later part of the document-review process is ready to present an issue as a comment, it can call `format_comment` instead of building the text itself. Inside, the function reads optional suggestion text from the issue using the issue record’s `get` method, so missing or empty suggestion text does not break the comment-building flow.

*Call graph*: 1 external calls (get).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `used whenever a document-review command is run`

This script is the review notebook for the document-review skill. Without it, the review process would have no reliable memory: sections, claims, issue IDs, verification status, and the final summary could be lost or become inconsistent between steps. It stores that memory in document_review_state.json and writes an audit trail to a log file, with one JSON record per action.

The workflow is split into phases: outline, find_claims, fact_check, find_issues, and complete. Think of these phases like stations on an assembly line. The script does not absolutely block work done out of order, but it warns when a command is being run in an unexpected phase.

Most commands follow the same pattern. They load the current state from disk, check that the incoming data has the required shape, update the state, save it back, write a log entry, and print a result. Commands that add data can read JSON either directly from a command-line option or from a file. The script also creates simple IDs such as claim:1 and issue:1 so later steps can refer to exact items. Some commands print machine-readable JSON results, while listing commands print a human-friendly report.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the final result for a command as one valid JSON object. This gives both a human-readable message and structured progress information that another tool can read safely.

**Data flow**: It receives a message, the current phase, the document name, and optional extra result details. It builds one dictionary containing those values, converts it to JSON text, and prints it to standard output.

**Call relations**: The state-changing commands call this at the end, after they have saved their changes and written a log entry. It is the last handoff from commands such as cmd_init, cmd_add_sections, cmd_add_claims, cmd_update_claims, cmd_add_issues, and cmd_submit to the outside caller.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Records what just happened in a separate log file. This is useful as an audit trail, so someone can later see which command ran, when it ran, and how the review phase changed.

**Data flow**: It receives the command name, the phase before and after the command, plus extra details such as counts or IDs. It adds the current UTC timestamp, turns the record into JSON, and appends it as one line to the log file.

**Call relations**: Most commands call this after they have decided what the action means. It supports both write commands, such as adding claims, and read commands, such as getting claims or issues, so the review history includes both changes and lookups.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the saved review state from document_review_state.json. It protects later commands from running without an initialized review.

**Data flow**: It looks for the state file on disk. If the file is missing, it prints an error and stops the program; otherwise it reads the JSON text and returns it as a Python dictionary.

**Call relations**: Every command except initialization relies on this first. Commands such as cmd_add_claims, cmd_update_claims, cmd_get_issues, and cmd_status use it as their starting point before they inspect or change the review.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to document_review_state.json. This is what makes each command’s changes last beyond the current program run.

**Data flow**: It receives the whole state dictionary, converts it to neatly formatted JSON, and writes that text to the state file on disk.

**Call relations**: State-changing commands call this after updating the in-memory state. For example, cmd_add_sections changes the phase and section list, then uses save_state before logging and reporting success.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user when a command is being run in a phase different from the expected one. It does not stop the command; it acts like a caution sign.

**Data flow**: It receives the current state and the phase that would normally be expected. If they do not match, it prints a warning to standard error and leaves the state unchanged.

**Call relations**: Commands that belong to a particular workflow step call this near the beginning. It helps cmd_add_sections, cmd_add_claims, cmd_update_claims, cmd_add_issues, and cmd_submit keep the review process understandable without making it too rigid.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an incoming JSON object contains the fields the command needs. This prevents half-formed sections, claims, claim updates, or issues from being saved.

**Data flow**: It receives one item, a list of required field names, and a label such as 'Claim' or 'Issue'. It finds any missing fields; if any are absent, it prints an error and stops the program.

**Call relations**: The add and update commands call this before using incoming data. It is one of the first guards that keeps bad input from reaching save_state.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a fixed set of allowed choices. This keeps fields like claim type, claim status, issue type, and severity consistent.

**Data flow**: It receives a value, the allowed set of values, and the field name being checked. If the value is not allowed, it prints a clear error listing the valid choices and stops the program.

**Call relations**: Commands that accept category-like input use this after checking required fields. cmd_add_claims uses it for claim types, cmd_update_claims uses it for statuses, and cmd_add_issues uses it for issue types and severities.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number into an integer and makes sure it is at least 1. This keeps document section page ranges meaningful.

**Data flow**: It receives a value that may be text or a number. It tries to convert it to an integer, rejects missing or invalid values, rejects numbers below 1, and returns the clean integer.

**Call relations**: cmd_add_sections calls this while reading each section’s start and end page. The cleaned page numbers are then stored in the state file.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Makes sure a field contains usable text. It accepts strings and integers, converts the value to a string, and rejects empty text.

**Data flow**: It receives a value and the field name being checked. If the value is not a string-like value or becomes blank after conversion, it prints an error and stops; otherwise it returns the text form.

**Call relations**: cmd_add_claims and cmd_add_issues use this for required location fields. That ensures saved claims and issues point to a real place in the document.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which is a more precise pointer into the document text. The anchor may be missing or null, but if present it must be non-empty text.

**Data flow**: It receives the anchor value and its field name. If the value is null, it returns null; if it is a non-empty string, it returns it; otherwise it prints an error and stops.

**Call relations**: cmd_add_claims and cmd_add_issues call this when they accept optional document anchors. The returned value is saved alongside the claim or issue.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input from whichever source the user chose: direct command-line text or a file. This lets commands support both quick small inputs and larger prepared JSON files.

**Data flow**: It receives parsed command-line arguments. If a file path was provided, it reads and returns that file’s text; otherwise it returns the text from the --data argument.

**Call relations**: Commands that import batches of structured data call this before parsing JSON. cmd_add_sections, cmd_add_claims, cmd_update_claims, and cmd_add_issues all use it as the bridge between command-line input and validation.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates the initial state file with empty sections, claims, issues, counters, and the first phase.

**Data flow**: It receives command-line arguments containing a filename. It rejects an empty filename, builds a fresh state dictionary, saves it, logs the initialization, and prints a JSON success result.

**Call relations**: main dispatches to this when the user runs the init command. Unlike the other commands, it does not load an existing state; it creates the state that later commands depend on.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document’s section outline and moves the review into the claim-finding phase. Sections give later claims and issues a place to belong.

**Data flow**: It loads the current state, reads JSON section data from --data or --file, checks that the input is a non-empty list, validates each section’s name and page range, stores the sections by name, changes the phase to find_claims, saves, logs, and prints a JSON result.

**Call relations**: main calls this for the add-sections command, usually after cmd_init. It relies on load_state, _resolve_data, _validate_required_keys, _validate_positive_int, save_state, log_action, and _emit_result to complete the full read-check-save-report cycle.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in a specific section. These claims are later checked and marked as verified, refuted, or inconclusive.

**Data flow**: It loads the state, warns if the phase is not find_claims, confirms the named section exists, reads a JSON array of claims, validates each claim’s required fields and allowed type, creates a new claim ID for each one, stores each claim as unverified, saves the state, logs the added IDs, and prints a JSON result.

**Call relations**: main calls this for add-claims while reviewers are collecting things that need checking. It hands newly created claim records to the shared state file so cmd_update_claims can later find them by ID.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the results of fact-checking existing claims. It changes each claim from unverified to verified, refuted, or inconclusive, and can attach source URLs.

**Data flow**: It loads the state, notes the starting phase, warns if the phase is unexpected, moves from find_claims to fact_check when needed, reads a JSON array of updates, validates each update, finds the matching claim, changes its status, increments its attempt count, appends any source URLs, saves, logs status counts, and prints a JSON result.

**Call relations**: main calls this for update-claims after claims have been collected. It depends on IDs created by cmd_add_claims and prepares the review to continue toward issue-finding.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in the document, such as factual issues, grammar issues, non-public information, or narrative logic problems. These are the concrete review findings that may need edits.

**Data flow**: It loads the state, records the previous phase, warns if the phase is unexpected, moves from fact_check to find_issues when appropriate, confirms the section exists, reads a JSON array of issues, validates required fields, issue type, severity, location, and optional anchor, assigns each issue a new ID, stores it, saves, logs the added IDs, and prints a JSON result.

**Call relations**: main calls this for add-issues after or during the issue-finding part of the workflow. It uses the same helper pattern as claim creation, but writes to the issues part of the state for later review and final reporting.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Finishes the review and records the final summary. It marks the whole review as complete.

**Data flow**: It loads the current state, remembers the previous phase, warns if the review is not in the expected issue-finding phase, checks that the summary is not empty, sets the phase to complete, stores the summary, saves, counts sections, claims, and issues, logs those totals, and prints a JSON result.

**Call relations**: main calls this for the submit command at the end of the workflow. It closes the loop created by cmd_init and filled by the add and update commands.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Prints the saved claims in a readable form, optionally narrowed by status or section. This helps a reviewer inspect what still needs checking or what has already been resolved.

**Data flow**: It loads the state, starts with all saved claims, filters them if the user provided a status or section, logs the lookup and result count, then prints either a no-match message or a detailed list of matching claims and their total.

**Call relations**: main calls this for get-claims. Unlike the add and update commands, it does not save changes; it reads from the state created by cmd_add_claims and updated by cmd_update_claims.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Prints the saved issues in a readable form, optionally narrowed by severity or section. This gives reviewers a focused list of document problems to inspect or fix.

**Data flow**: It loads the state, starts with all saved issues, filters by severity or section if requested, logs the lookup and result count, then prints either a no-match message or a detailed list with text, context, description, and suggested replacement when present.

**Call relations**: main calls this for get-issues. It reads the issues created by cmd_add_issues and provides a human-friendly view without changing the saved review.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Shows a compact dashboard of the whole review. It summarizes the document name, current phase, sections, claim statuses, issue counts, and final summary if one exists.

**Data flow**: It loads the state, reads the document name and phase, then calculates simple counts from sections, claims, and issues. It prints these counts and groupings as a human-readable progress report.

**Call relations**: main calls this for the status command. It is a read-only overview that draws together data created across the entire workflow.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each user command to the right function. It is the front door of the script.

**Data flow**: It builds an argument parser, defines subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, get-claims, get-issues, and status, parses the user’s command-line input, looks up the matching command function, and calls it with the parsed arguments.

**Call relations**: When this file is run as a script, main starts the whole process. It does not do the review work itself; it routes each command to the specialized cmd_* function that performs the requested step.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `manual command-line run after document review state has been produced`

This file turns a document review report into something a person can see directly inside a PDF. Without it, review findings would stay in a separate JSON state file, so a reader would have to cross-check comments against the document by hand.

The script expects two command-line arguments: an input PDF and an output PDF. It also expects a state file, named by `STATE_FILENAME`, in the current working directory. That state file contains review issues, such as the page number, the original text that was flagged, the severity, and the comment content.

For each issue, the script opens the right page in the PDF and tries to find the original text. It first searches using a longer slice of the text, then falls back to a shorter slice if needed. This is like trying to find a quote in a book by searching a full sentence first, then just the first few words if the sentence does not match exactly.

When the text is found, the script highlights it. It also adds a PDF sticky-note annotation nearby. The note color shows severity: red for high, orange for medium, and yellow for low. If the text cannot be found, the note is still added at a fixed fallback spot, so the issue is not lost. Finally, it saves a new annotated PDF.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved document review issues from the expected state file. It exists so the annotation step can work from the review results already written to disk.

**Data flow**: It starts with the known state filename from `STATE_FILENAME`. It checks whether that file exists. If it is missing, it prints an error message and stops the script. If it exists, it reads the file as text, parses the JSON into Python data, takes the values under the `issues` field, and returns them as a list.

**Call relations**: The main annotation flow calls this first, before opening or changing the PDF. It uses standard JSON reading and file-path tools, and if the needed state file is missing it ends the process early through `sys.exit` so the script does not create a misleading output PDF.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to locate the flagged text on a PDF page. The returned locations tell the PDF library exactly where to draw the highlight.

**Data flow**: It receives a PDF page and the original text from an issue. It searches the page using the first 80 characters of that text. If that finds nothing, it tries again with only the first 30 characters. It returns whatever matching page areas the PDF library finds, or an empty result if neither search works.

**Call relations**: The annotation flow calls this for each issue after it has chosen the correct page. Its result decides whether `annotate` can place a highlight directly over the text or must fall back to adding only a note at a default page location.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function: it reads review issues and writes a new PDF containing highlights and sticky-note comments. Someone uses it when they want review findings embedded directly into a copy of the document.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the issue list, opens the input PDF, and walks through each issue. For each valid page number, it chooses a color from the issue severity, formats the comment text, searches for the flagged text, adds a highlight if possible, adds a sticky note, and counts the annotation. At the end, it saves the changed document to the output path, closes the PDF, and prints how many annotations were added.

**Call relations**: This function is called by the command-line block when the script is run directly with the right arguments. It depends on `load_issues` to get the review data, `find_quads` to locate text on the page, `models.format_comment` to prepare the note body, and PyMuPDF functions such as `fitz.open`, `fitz.Point`, and `fitz.Rect` to read and write the actual PDF annotations.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `document review annotation/export`

PowerPoint .pptx files are really zip files full of XML documents. This file opens that package, adds the XML pieces PowerPoint expects for comments, then zips everything back up. Without this script, review issues would remain in document_review_state.json and a user would not see them as native comments inside PowerPoint.

The flow is simple. First it reads the saved review state file and pulls out its issues. Then it groups those issues by slide number, using each issue’s location field. Issues whose location is not a usable slide number are ignored. Next it copies the input presentation to the requested output path, unpacks the copy into a temporary folder, and creates one comment XML file for each slide that has issues. Each comment is stamped with an author, a time, and formatted text from the shared format_comment helper.

PowerPoint also needs bookkeeping files that say, “this slide has a comment file” and “this presentation has a comment author list.” The script adds those relationships and content-type entries too. This is like not only putting notes in a binder, but also updating the table of contents so the binder knows the notes exist. Finally, it rebuilds the .pptx and deletes the temporary folder.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: Reads the saved document-review state file and returns the review issues found inside it. If the expected state file is missing, it stops the script with a clear error instead of creating a misleading empty annotation.

**Data flow**: It starts with the configured state filename, checks whether that file exists in the current working directory, and reads its JSON text. It pulls out the values under the issues section and returns them as a list. If the file is not there, it prints an error to standard error and exits the process.

**Call relations**: The main annotate flow calls this first, because every later step depends on knowing which issues should become PowerPoint comments. It relies on JSON parsing and the shared state filename, then hands the issue list back to annotate.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: Sorts review issues into buckets by slide number. This lets the script create the right comment file for each slide that has feedback.

**Data flow**: It receives a list of issue records. For each issue, it tries to read the location field as an integer slide number. Valid slide numbers become keys in a dictionary, and the matching issues are collected under each key. Issues with missing or non-number locations are skipped. The result is a slide-number-to-issues map.

**Call relations**: After annotate loads all issues, it calls this function to organize them before editing the PowerPoint package. The grouped result is then passed to the functions that write per-slide comments and update PowerPoint’s bookkeeping files.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: Finds the largest relationship ID already used in a PowerPoint relationship file. This helps the script add a new relationship without reusing an existing ID.

**Data flow**: It receives the path to a .rels file, which is an XML file that lists links between parts of the .pptx package. If the file does not exist, it returns 0. Otherwise it reads the XML, scans each relationship’s Id value for a number, and returns the highest number it finds.

**Call relations**: add_relationship calls this just before adding a new relationship. It acts like checking the last ticket number in a roll so the next ticket can be numbered safely.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: Adds a link from one PowerPoint package part to another, such as from a slide to its comment file. These links are required so PowerPoint can discover the new comment XML files.

**Data flow**: It receives a relationship file path, a relationship type, and a target path. If the relationship file exists, it reads it; otherwise it creates a new Relationships XML document and its parent folder. If a relationship of the same type is already present, it leaves the file unchanged. If not, it chooses the next rId number, adds the new XML entry, and writes the file back to disk.

**Call relations**: write_slide_comments uses this to connect each slide to its comment file. write_author_and_rels uses it to connect the presentation to the comment author list. It calls find_max_rel_id so the new relationship gets a safe ID.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: Creates the actual PowerPoint comment XML files for slides that have review issues. It also connects each slide to its new comment file.

**Data flow**: It receives the unpacked PowerPoint folder and the grouped issues. For each slide number, it creates a comments XML file containing one comment entry per issue, using the shared format_comment helper for the visible comment text. It gives each comment an author ID, timestamp, index number, and default position. It then updates that slide’s relationship file so PowerPoint knows where the comment file is. It returns the total number of comments written.

**Call relations**: annotate calls this after unpacking the copied .pptx. This function does the per-slide writing work, and it calls add_relationship each time it needs to attach a comment file to a slide. Its returned comment count is later used when writing the author metadata.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: Writes the shared comment author information and updates package-wide PowerPoint metadata. This is the step that makes the new comment files officially recognizable as PowerPoint comment parts.

**Data flow**: It receives the unpacked PowerPoint folder, the total comment count, and the slide grouping. It writes commentAuthors.xml with the fixed author name and initials. It adds a presentation-level relationship to that author file. Then it opens [Content_Types].xml and adds entries declaring the author file and each slide comment file as valid PowerPoint comment-related XML parts. It writes the updated content types back to disk.

**Call relations**: annotate calls this after write_slide_comments has created the per-slide files. It calls add_relationship for the presentation-to-author link, and it updates the content type list so the PowerPoint package can be opened correctly with the new comment parts.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: Runs the full annotation process from input presentation to output presentation. It is the main worker used by the command-line script.

**Data flow**: It receives an input .pptx path and an output .pptx path. It loads review issues, stops early with a message if there are none, groups the rest by slide, and copies the input file to the output file. It unzips the copied presentation into a temporary folder, writes slide comments, writes author and package metadata, then zips the folder contents back into the output .pptx. At the end it prints how many comments were added and removes the temporary folder even if something goes wrong.

**Call relations**: When the script is run from the command line, the bottom of the file checks the arguments and calls annotate. annotate coordinates the whole job by calling load_issues, group_by_slide, write_slide_comments, and write_author_and_rels in order, while standard file and zip utilities do the copying, unpacking, repacking, and cleanup.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review annotation`

This file is a small command-line tool for turning document review results into something a spreadsheet user can see directly in Excel. The review system stores issues in a JSON file, which is a plain text data file. This script reads those issues, opens an XLSX workbook, and attaches each issue as an Excel comment to the most relevant cell.

Its basic job is like putting sticky notes onto a spreadsheet. For each issue, it first formats the issue into readable comment text. Then it tries to place that comment where it belongs. If the issue says which worksheet and cell to use, the script tries that exact spot first. If that fails, it searches the named worksheet for a cell containing the original text. If that still fails, it searches every worksheet. As a last resort, it puts the comment on cell A1 of the first sheet, combining multiple fallback comments there if needed.

The script copies the input workbook to the output path before editing, so the source spreadsheet is preserved. Without this file, review findings for spreadsheets would remain separate from the document, making them harder for a person to inspect in the context of the actual cells.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the review results from the expected state file and returns the list of issues to annotate. If the state file is missing, it stops the script with a clear error, because there is nothing useful to write into the spreadsheet.

**Data flow**: It starts with the fixed state filename from configuration. It checks whether that file exists, reads its JSON text, turns that text into Python data, and pulls out the stored issue records. The result is a list of issue objects; if the file is missing, the script prints an error and exits instead of continuing with bad input.

**Call relations**: The main annotation flow calls this first. Its output becomes the work list that annotate walks through, one issue at a time, before creating Excel comments.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell that contains a given piece of text. It helps the script attach a review comment near the original text when an exact cell address is not available or does not work.

**Data flow**: It receives a worksheet and some target text. It normalizes the target by trimming spaces and ignoring letter case, then scans every non-empty cell in the worksheet. If a cell's displayed value contains the target text, it returns that cell; otherwise it returns nothing.

**Call relations**: annotate uses this after trying a direct cell placement. It first searches the worksheet named by the issue, if there is one, and later searches all worksheets as a broader fallback.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks up a worksheet by name without caring about uppercase or lowercase differences. It lets review issues refer to a sheet location in a forgiving way.

**Data flow**: It receives an open workbook and a worksheet name from an issue. It compares that name against each sheet title in the workbook, ignoring case. It returns the matching worksheet if one is found, or nothing if no sheet has that name.

**Call relations**: annotate calls this when an issue includes a location. The worksheet it returns is then used for exact cell placement or text searching.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a prepared Excel comment to one exact cell reference, such as B12. It exists so the main annotation logic can attempt an exact placement safely and then fall back if the cell reference is invalid.

**Data flow**: It receives a worksheet, a cell address, and a comment object. It asks the worksheet for that cell and, if successful, sets the cell's comment to the supplied comment and returns true. If the address cannot be used, it catches the error and returns false without stopping the whole script.

**Call relations**: annotate calls this when an issue provides both a worksheet and an anchor cell. A true result means the issue has been placed; a false result sends annotate on to its backup search methods.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It copies an input Excel file, opens the copy, adds one comment for each review issue, saves the result, and reports how many comments it added.

**Data flow**: It receives an input XLSX path and an output XLSX path. It loads the saved issues, copies the input workbook to the output location, opens that copy, and loops through the issues. For each issue, it turns the issue into readable comment text, creates an Excel comment, tries several placement strategies, and finally saves the workbook. The output is a modified XLSX file containing review comments; it also prints a short status message.

**Call relations**: This function coordinates all the helpers in the file. It gets issue data from load_issues, formats each issue through the external format_comment helper, creates Excel Comment objects, uses find_worksheet and _place_on_cell for precise placement, uses find_cell for text-based placement, and relies on openpyxl and shutil to copy, edit, and save the workbook.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).


### DOCX package editing
The DOCX scripts unpack Word files for XML editing, add comment infrastructure, repack the document, and optionally produce a clean accepted-changes copy.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `document unpacking and preprocessing`

A .docx file is really a ZIP archive full of XML files. This file opens that archive, extracts it into a directory, and makes the XML easier to read and edit. Think of it like unpacking a suitcase and then neatly folding the clothes so you can see what is inside.

After extraction, every XML-related file is pretty-printed with indentation. Then, if the main Word document file exists at word/document.xml, the script can simplify two common kinds of Word clutter. First, it can coalesce tracked changes, meaning it joins neighboring insertions or deletions from the same author when they are really one continuous change. Second, it can merge adjacent Word “runs.” A run is a small piece of text with the same formatting; Word often splits one sentence into many runs for internal reasons. Merging them makes the document easier to process.

Finally, it replaces curly quote characters with numeric XML entities, so those characters are represented explicitly in the saved files. The script is forgiving: if an individual XML file cannot be indented or quote-rewritten, that step is silently skipped for that file. Without this script, other document-editing tools would have to work directly with Word’s compressed, fragmented, hard-to-read XML.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main work function. It checks that the input is a real .docx path, extracts it, runs the cleanup steps, and returns both a structured result and a human-readable summary message.

**Data flow**: It receives an input file path, an output folder path, and two true-or-false options for cleanup. It checks the file, creates the destination folder, unzips the .docx contents there, finds XML and relationship files, formats them, optionally cleans word/document.xml, replaces curly quotes, and then returns either an UnpackResult plus a success summary or None plus an error message. Its main visible change is writing the extracted and rewritten files to disk.

**Call relations**: This function is the conductor for the whole file. When the script is run from the command line, the command-line section calls it after reading the user’s options. During the pipeline it hands individual files to _indent_xml and _replace_curly_quotes, and it hands the main document XML to _coalesce_tracked_changes and _merge_adjacent_runs when those options are enabled.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This helper rewrites one XML file with clean indentation. The goal is to make the extracted XML readable for humans and stable for later editing.

**Data flow**: It receives the path to one XML file. It parses the file as XML, asks lxml to add indentation, and writes the formatted XML bytes back to the same path. If parsing or writing fails, it leaves the file alone and does not raise an error.

**Call relations**: unpack_docx calls this once for each XML or .rels file after extraction. It is an early cleanup step before the more Word-specific changes happen.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This helper changes curly quotation marks and apostrophes into explicit XML numeric entities. That makes those characters visible and predictable in the saved XML text.

**Data flow**: It receives a file path, reads the file as UTF-8 text, searches for curly quote characters, and if it finds any, rewrites the file with each one replaced by its numeric entity form such as &#x201C;. If reading or writing fails, it silently leaves the file unchanged.

**Call relations**: unpack_docx calls this near the end for every XML-related file. It runs after indentation and document cleanup so the final files have escaped quote characters.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word’s main document XML by joining neighboring text runs that have the same formatting. This reduces unnecessary fragmentation caused by Word’s internal editing history.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns 0. Otherwise it parses the XML, removes proofing-error markers, removes run attributes related to revision IDs, finds every parent element that contains Word runs, and asks _merge_runs_in to merge compatible runs inside each parent. If anything was merged, it writes the updated XML back and returns the number of absorbed runs.

**Call relations**: unpack_docx calls this when run merging is enabled. It delegates the local merging work to _merge_runs_in, which in turn compares formatting and joins text nodes.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This helper creates a consistent fingerprint for a run’s formatting. It lets the script decide whether two Word runs look the same and can safely be merged.

**Data flow**: It receives one Word run XML element. It looks for that run’s formatting child element, called w:rPr in WordprocessingML. If there is no formatting element, it returns None; otherwise it serializes the formatting in canonical form, meaning a normalized XML representation suitable for comparison.

**Call relations**: _merge_runs_in calls this for each run it examines. The returned fingerprint is the basis for grouping adjacent runs with identical formatting.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This function performs the actual merging of neighboring runs inside one parent XML element. It keeps the first run and moves the useful content from later matching runs into it.

**Data flow**: It receives a container element that may have Word run children. It walks through the children in order, groups consecutive runs that share the same formatting fingerprint, and ignores groups of only one run. For each larger group, it keeps the first run, moves non-formatting child nodes from the later runs into it, removes the emptied later runs, joins neighboring text nodes inside the kept run, and returns how many runs were absorbed.

**Call relations**: _merge_adjacent_runs calls this for each parent that contains runs. It uses _canonical_rpr to compare formatting and _join_adjacent_text to clean up text nodes after XML children have been moved.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This helper tidies one run after merging by combining neighboring text nodes. Without it, a merged run could still contain several separate text pieces that behave like one piece of text.

**Data flow**: It receives one Word run element. It scans its child nodes from left to right, and whenever two neighboring children are both text elements, it combines their text into the first one and removes the second. If the combined text starts or ends with a space, it marks the XML text as space-preserving so Word does not trim important whitespace.

**Call relations**: _merge_runs_in calls this after it has moved content from several runs into one anchor run. It is the final polish step inside run merging.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This function joins neighboring tracked-change elements in the main document XML when they represent the same kind of change by the same author. It makes Word’s change history less chopped up and easier to inspect.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns 0. Otherwise it reads and parses the XML while preserving existing blank text, finds paragraph and table-cell containers, and for each one tries to coalesce insertion and deletion elements. If anything was reduced, it writes the XML back and returns the number of change elements absorbed.

**Call relations**: unpack_docx calls this before run merging when tracked-change coalescing is enabled. It delegates each container and change kind to _coalesce_in.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This helper looks inside one container, such as a paragraph, for tracked insertions or deletions of one chosen type. It groups changes by author so only changes from the same person are considered for merging.

**Data flow**: It receives a container XML element and a change type string, either insertion or deletion. It collects direct child elements of that type, groups consecutive matching elements by their author attribute, asks _merge_change_run to merge each group where possible, and returns the total number of absorbed change elements.

**Call relations**: _coalesce_tracked_changes calls this for each paragraph or table cell and for both insertion and deletion changes. It passes likely merge candidates onward to _merge_change_run, which checks whether they are truly adjacent.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This function merges a list of same-author tracked-change elements, but only when each later element is actually next to the current one in the XML. It protects document meaning by not joining changes that have real content between them.

**Data flow**: It receives a list of tracked-change XML elements. It uses the first as the current anchor, checks each later element with _changes_adjacent, and when they are adjacent, moves the later element’s children into the anchor and removes the later element from its parent. It also preserves any tail text attached to the removed element. It returns how many elements were absorbed.

**Call relations**: _coalesce_in calls this after grouping possible candidates by author. This function relies on _changes_adjacent as the safety check before it changes the XML structure.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This helper answers a yes-or-no question: are two tracked-change elements next to each other except for whitespace or comments? That check prevents accidental merging across meaningful document content.

**Data flow**: It receives two XML elements. It finds their shared parent and their positions among that parent’s children. If either element is missing from the parent, it returns false. It then inspects the space and nodes between them; if there is any non-comment element or non-whitespace text between them, it returns false, otherwise true.

**Call relations**: _merge_change_run calls this before merging one tracked-change element into another. It acts like a gatekeeper: only changes that pass this adjacency test are combined.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`io_transport` · `document modification tool run`

A DOCX file is really a zip file full of XML files. Adding a comment is not as simple as writing one line of text: Word expects several separate files to agree with each other, including the main comment text, newer threaded-comment metadata, relationship records, and content-type records. This file does that boilerplate so a caller does not have to know all of Word’s internal bookkeeping.

The main input is an unpacked DOCX directory, a numeric comment ID, and the comment text. If this is the first comment in the document, the script copies template comment files into the word folder and registers them so Word knows they exist. It then creates four matching XML entries: the visible comment body, extended information such as whether it is a reply, a stable ID record, and a newer extensibility record with a timestamp. For replies, it looks up the parent comment’s paragraph ID so Word can thread the reply under the right comment.

One important limit is that this script does not place the visible comment markers around text in document.xml. Instead, after adding the comment data, the command-line mode prints the XML snippets the user or another tool must insert around the annotated text. Think of it as creating the comment card and filing it correctly, but leaving a note about where to put the sticky tab in the document.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character uppercase hexadecimal ID, which Word uses as a compact internal tag for comment paragraphs and durable comment records.

**Data flow**: It takes no input. It asks the random number generator for a number in Word’s expected range, formats that number as eight hexadecimal characters, and returns the resulting text.

**Call relations**: The main insertion routine calls this when it needs fresh internal IDs for a new comment. Those IDs are then passed into the XML-building functions so the separate comment files can refer to the same comment consistently.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces smart curly quote characters with XML character references. This helps preserve those characters safely when the XML is written back to disk.

**Data flow**: It receives a string of XML text. It scans for four curly quote characters and replaces each one with its numeric XML form, then returns the updated string.

**Call relations**: The XML serializer calls this just before bytes are written to a file. It is a small cleanup step between turning an XML tree into text and saving that text.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an editable XML tree. Other functions use this when they need to inspect or change an existing DOCX part.

**Data flow**: It receives a file path. It reads the file’s raw bytes, asks lxml, the XML library, to parse those bytes, and returns the root XML element.

**Call relations**: Several file-changing helpers rely on this as their first step. They call it before appending a new element, registering comment files, or looking up a parent comment’s paragraph ID.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an edited XML tree back into saved XML bytes, with a standard XML declaration and safe handling for curly quotes.

**Data flow**: It receives the root of an XML tree. It converts the tree to UTF-8 XML text, runs the curly-quote escaping step, and returns bytes ready to write to disk.

**Call relations**: The append helper calls this after it has added a new child element to a comment file. This keeps the read-edit-write cycle in one consistent format.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main Word XML element that contains the actual comment text, author, initials, date, and the comment’s internal paragraph ID.

**Data flow**: It receives the public comment ID, author details, timestamp, internal paragraph tag, and comment body text. It creates a nested XML structure with Word’s expected tags for a comment, including a reference marker and a text run, then returns that XML element.

**Call relations**: The insertion routine calls this to create the main entry for comments.xml. Afterward, that entry is appended to the file by the shared append helper.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra Word metadata used for modern comments, including whether the comment is linked to a parent comment as a reply.

**Data flow**: It receives the new comment’s paragraph ID and, if this is a reply, the parent comment’s paragraph ID. It creates one XML element that marks the comment as not resolved and optionally records the parent link, then returns it.

**Call relations**: The insertion routine calls this after it knows whether a parent comment exists. The returned element is written into commentsExtended.xml so Word can display comment threading correctly.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML record that connects a comment paragraph ID to a durable ID. A durable ID is a stable-looking identifier Word uses to track comments beyond the visible numeric ID.

**Data flow**: It receives the paragraph ID and durable ID as text. It places both into a commentsIds XML element and returns that element.

**Call relations**: The insertion routine calls this for every new comment. The result is appended to commentsIds.xml, keeping Word’s newer comment identity records in step with the main comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds an additional modern Word comment metadata element that records the durable ID and the UTC creation time.

**Data flow**: It receives a durable ID and a timestamp. It creates a commentsExtensible XML element containing both values and returns it.

**Call relations**: The insertion routine calls this after generating the durable ID and timestamp. The returned element is saved into commentsExtensible.xml, alongside the other comment support files.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the internal paragraph ID for an existing parent comment. This is needed when adding a threaded reply, because Word links replies by paragraph ID rather than just by the visible comment number.

**Data flow**: It receives the path to comments.xml and the parent comment’s numeric ID. It parses the XML, searches comments until it finds the matching ID, then looks inside that comment for a paragraph ID and returns it. If it cannot find one, it returns nothing.

**Call relations**: The main insertion routine calls this only for replies. Its answer is passed to the extended-comment builder so the new comment can be attached to the right parent; if no parent is found, insertion reports an error.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to an existing XML file and saves the file again. It is the common write step for the different comment support files.

**Data flow**: It receives a file path and an XML element to add. It reads and parses the file, appends the new element to the root, serializes the updated tree, and writes the bytes back to the same path.

**Call relations**: The main insertion routine uses this repeatedly: once for the main comment and once for each supporting metadata entry. Internally, it relies on the parse and serialize helpers to keep file reading and writing consistent.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure Word’s package bookkeeping says the comment files exist. Without these relationship and content-type records, Word may ignore the new comment XML files.

**Data flow**: It receives the unpacked DOCX base directory. It opens document.xml.rels to add relationship entries for the comment files if they are missing, and opens [Content_Types].xml to add content-type overrides if they are missing. It writes those files back only after adding the needed records.

**Call relations**: The insertion routine calls this when it is creating the first comment files from templates. This prepares the DOCX package so the later appended comment entries are discoverable by Word.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds one comment or threaded reply to an unpacked DOCX directory. This is the main function other code would call when it wants the XML comment files updated.

**Data flow**: It receives the path to an unpacked DOCX folder and a CommentSpec containing the comment ID, text, author, initials, and optional parent ID. It checks that the word folder exists, creates fresh internal IDs and a UTC timestamp, copies template files if this is the first comment, registers those files, builds the four needed XML entries, appends each one to its file, and returns the new paragraph ID plus a success or error message.

**Call relations**: This is the center of the file. The command-line block builds a CommentSpec from user arguments and calls this function; inside, it calls the ID generator, XML builders, registration helper, parent lookup helper, and append helper in order to make all of Word’s comment files agree.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`entrypoint` · `manual document packaging`

A DOCX file is really a ZIP archive full of XML files and related parts. This script is the “put it back in the box” tool: given a folder that contains the unpacked pieces of a Word document, it copies those pieces to a temporary workspace, cleans up XML formatting whitespace, and writes the result as a .docx archive.

The cleanup step is careful. In Word documents, some XML text nodes are actual document content, such as the words a user typed. The script avoids stripping whitespace from those text-bearing tags, because removing spaces there could change the visible document. For other XML areas, it removes whitespace that is only indentation or formatting noise. It also removes unusual parser artifacts that cannot belong in the final XML tree.

The file can be used from the command line with an input folder and output path. It checks that the input is really a directory and that the output name ends in .docx. Without this file, another part of the system could unpack and edit a DOCX, but would not have this simple local way to reassemble it into the format Word expects.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This function repacks an unpacked Word document folder into a .docx file. It is the main worker used by the command-line script, and it returns both the output path and a human-readable success or error message.

**Data flow**: It receives an input directory path and an output file path. First it checks that the input is a folder and the output name ends in .docx. Then it copies the folder into a temporary staging area, cleans each XML and relationship file there, creates the output folder if needed, and writes every staged file into a compressed ZIP archive. The result is either a path to the new DOCX plus a success message, or no path plus an error message.

**Call relations**: When the script is run, the command-line code gathers the two paths and calls this function. During packing, it asks _strip_xml_whitespace to clean each XML-like file before handing the staged files to the ZIP writer, which creates the final .docx package.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there for formatting, while preserving whitespace that may be real document text. It keeps the DOCX package cleaner without changing what the document says.

**Data flow**: It receives the path to one XML or relationship file. It parses that file into an XML tree, walks through each element, skips tags where spaces may be meaningful text, removes blank-only text and tail whitespace elsewhere, removes invalid callable-tag child nodes, and writes the cleaned XML back to the same file. If parsing or writing fails, it prints an error message and raises the problem so packing stops.

**Call relations**: pack_docx calls this helper for every .xml and .rels file in the temporary staging copy. This means the original unpacked folder is left untouched, while the cleaned staging copy is what gets zipped into the final DOCX.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `document processing`

This file solves a very practical document-cleanup problem: a DOCX file may contain tracked edits, and another part of the system may need the final version where every proposed change has been accepted. Instead of trying to edit the complicated DOCX format directly, the script asks LibreOffice to do it, the same way a person might click “Accept All Changes” in a word processor.

The script first checks that the input file exists and is really a .docx file. It then copies the original file to the requested output path, so the source document is not changed. Next, it prepares a small LibreOffice Basic macro. A macro is a short script that LibreOffice can run inside a document. This one runs LibreOffice’s built-in “Accept All Tracked Changes” command, saves the document, and closes it.

LibreOffice is run in headless mode, meaning it works without showing a window. The script also uses a temporary LibreOffice user profile under /tmp so the macro can be installed in a predictable place without depending on a real user’s settings. One important quirk is handled deliberately: LibreOffice may hang even after the document has already been saved. Because of that, if the process times out, this script treats the operation as successful.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment settings used when starting LibreOffice. In particular, it tells LibreOffice to use a non-graphical display backend so it can run safely without opening a visible window.

**Data flow**: It starts with the current process environment, which is the set of operating-system settings already available to the script. It adds or overrides one LibreOffice display setting. It returns the updated environment dictionary for subprocess calls.

**Call relations**: Whenever the script starts LibreOffice, the caller asks this helper for the right environment first. _ensure_macro uses it while preparing the macro profile, and accept_tracked_changes uses it when running the actual document-cleaning command.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: Creates the command-line argument that tells LibreOffice which temporary user profile to use. This keeps the script’s macro setup separate from a person’s normal LibreOffice settings.

**Data flow**: It reads the fixed profile directory path stored in the file. It formats that path into LibreOffice’s expected UserInstallation command-line option. It returns that option as a string ready to place in a command.

**Call relations**: This helper is used any time LibreOffice is launched by this script. _ensure_macro uses it when initializing the profile, and accept_tracked_changes uses it when running the macro on the copied document.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the macro needed to accept all tracked changes. It installs the macro into the temporary LibreOffice profile if it is not already there.

**Data flow**: It first checks whether the expected macro file already exists and contains the macro name. If not, it may start LibreOffice once to create the profile folders, then creates the macro directory if needed and writes the macro XML file. It returns True when the macro is in place.

**Call relations**: accept_tracked_changes calls this after copying the DOCX file but before asking LibreOffice to process it. To do its work, _ensure_macro builds the LibreOffice profile argument with _profile_arg, gets safe headless environment settings from _soffice_env, and may launch LibreOffice through subprocess.run to initialize the profile.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: Creates an output DOCX where every tracked change from the input file has been accepted. This is the main reusable function for the script and is also what the command-line mode calls.

**Data flow**: It receives an input file path and an output file path. It checks that the input exists and has a .docx extension, creates the output folder if needed, and copies the input document to the output location. It then ensures the LibreOffice macro is installed and runs LibreOffice headlessly on the copied file. It returns a pair whose first value is always None and whose second value is a human-readable success or error message. It changes the filesystem by creating or replacing the output DOCX and by creating the temporary LibreOffice macro profile if needed.

**Call relations**: This function is the center of the file’s workflow. The command-line block calls it after reading the two file paths from the user. Inside, it delegates setup details to _ensure_macro, _profile_arg, and _soffice_env, uses shutil.copy2 to preserve the original while making the working copy, and uses subprocess.run to ask LibreOffice to run the macro. If LibreOffice times out, it still reports success because the macro often saves the file before LibreOffice gets stuck.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### PPTX package maintenance
The PowerPoint tools unpack presentations, manipulate slide packages, repack them, and repair known packaging issues.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual document preparation`

A .pptx file is really a ZIP archive, like a folder squeezed into one file. Inside it are many XML files, which are text files that describe slides, layouts, relationships, and other PowerPoint details. This file is a small helper tool for opening that package up in a useful way.

First, it checks that the input file exists and that its name ends in .pptx. Then it creates the output folder if needed and extracts the PowerPoint archive into it. After extraction, it finds every .xml file and every .rels file. A .rels file is also XML; it records relationships, such as which slide points to which image or layout.

The script then makes those XML files easier to work with by pretty-printing them, meaning it adds consistent line breaks and indentation. This is like taking a cramped paragraph and turning it into a clear outline. Finally, it replaces curly “smart quotes” with XML-safe entity text, so those characters are preserved in a more explicit form.

If the PowerPoint file is not actually a valid ZIP archive, the script returns a clear error. Some XML cleanup errors are silently ignored, so one bad file does not stop the whole unpacking job.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: Unpacks a PowerPoint file into a directory and prepares its XML files for editing. This is the main function someone would call when they want to inspect or modify the contents of a .pptx outside PowerPoint.

**Data flow**: It receives a path to a .pptx file and a destination folder. It checks the source file, creates the destination folder, extracts the ZIP contents, finds XML-like files, prettifies them, escapes smart quotes, and then returns either a success result with the number of XML files found or an error message if the input is missing, not a .pptx, or not a valid archive.

**Call relations**: This is the central flow of the script. When run from the command line, the script passes the user’s two arguments into this function. During its work, it asks _prettify_xml to make each XML file readable and then asks _escape_smart_quotes to normalize quotation marks before reporting the final outcome.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: Rewrites one XML file with clean indentation and a standard XML declaration. This makes the extracted PowerPoint internals easier for humans and version-control tools to read.

**Data flow**: It receives the path to one XML file. It reads and parses the XML, adds two-space indentation, converts it back into UTF-8 XML text, and writes that cleaned version back to the same file. If parsing or writing fails, it quietly leaves the file as it was.

**Call relations**: extract_pptx calls this once for each .xml and .rels file it finds after unpacking the PowerPoint archive. It performs the formatting step before smart quotes are escaped, so the files are first made structurally readable and then text-normalized.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: Replaces curly quotation marks in one XML file with explicit XML entity codes. This helps preserve those characters in a form that is safe and predictable in XML text.

**Data flow**: It receives the path to one XML file and reads the file as UTF-8 text. If it finds curly single or double quotes, it replaces each one with its matching XML entity, then writes the updated text back to the same file. If there are no smart quotes, it changes nothing; if reading or writing fails, it silently skips the file.

**Call relations**: extract_pptx calls this after _prettify_xml for each extracted XML-related file. It is the final cleanup pass, making the text content more explicit after the XML structure has already been reformatted.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command execution`

A PowerPoint file is really a zip file full of XML files, images, and relationship files that point from one part to another. This script works at that package level. It is useful when a system needs to edit slides without opening PowerPoint itself, or when leftover files inside a presentation would make it messy or unreliable.

The file provides three user-facing commands. The clean command looks through an unpacked .pptx folder, finds which slides are still listed in the main presentation, removes slide files that are no longer active, then repeatedly removes media, charts, themes, notes, and similar resources that nothing points to anymore. It also cleans matching entries from the content-type file, which is like the package’s table of contents.

The add command either copies an existing slide or creates a blank slide linked to a chosen layout. It writes the new slide file, registers it in the package metadata, and prints the exact XML snippet the caller must add to the slide list.

The thumbnail command treats a finished .pptx as input. It uses LibreOffice to render it to PDF, converts the PDF pages to JPEG images, matches those images to the presentation’s slide order, inserts placeholders for hidden slides, and combines everything into one or more labeled grid images.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so other functions can inspect or edit it. This is the shared doorway into the many XML files inside a PowerPoint package.

**Data flow**: It receives a file path. It asks the XML library to parse that file, takes the root element from the parsed document, and returns that root element for later reading or modification.

**Call relations**: Cleaning and slide-adding helpers call this whenever they need to understand a relationship file, the content-types file, or copied slide relationship data. It delegates the actual XML parsing to lxml.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to disk in a standard encoded form. Without this, changes made in memory would never be saved into the unpacked presentation.

**Data flow**: It receives an XML root element and a destination path. It turns the XML tree into UTF-8 bytes with an XML declaration, then overwrites the file at that path.

**Call relations**: Functions that remove stale relationships or register new slide metadata call this after changing an XML tree. It uses lxml to serialize the XML and pathlib to write the bytes.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file that is still pointed to by any relationship file in the unpacked PowerPoint folder. This is like walking through all the signposts in a building to see which rooms are still reachable.

**Data flow**: It receives the unpacked presentation folder. It scans for every .rels file, reads each one, resolves each relationship target into a path inside the package, and returns a set of relative paths that are referenced.

**Call relations**: run_clean calls this during each cleanup pass. It relies on _parse_xml to read relationship files, and its result tells _remove_unreferenced_resources what must be kept.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds which slide XML files are actually part of the presentation’s current slide list. This matters because a .pptx folder can contain slide files that are no longer shown anywhere.

**Data flow**: It receives the unpacked presentation folder. It reads the presentation relationship file to map relationship IDs to slide filenames, reads the main presentation XML to find the active slide IDs, and returns the slide filenames that are still active.

**Call relations**: run_clean calls this at the start of cleanup. It uses _parse_xml for the relationship file and regular expression matching to find slide references in presentation.xml.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from the special trash folder used by this script or workflow. It clears out known throwaway material before the more careful reference-based cleanup continues.

**Data flow**: It receives the unpacked presentation folder. If a [trash] directory exists, it deletes each file inside it, removes the empty directory, and returns the list of deleted paths.

**Call relations**: run_clean calls this after removing orphan slides. It does not call other project helpers; it directly checks and deletes files on disk.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Removes slide files that exist in the slides folder but are not listed as active in the presentation. It also removes their companion relationship files so the folder does not keep broken leftovers.

**Data flow**: It receives the unpacked presentation folder and the set of active slide filenames. It deletes slide XML files not in that set, deletes matching .rels files, updates presentation.xml.rels to remove links to those inactive slides, and returns the deleted paths.

**Call relations**: run_clean calls this after discovering active slides. When it must edit the presentation relationship file, it uses _parse_xml to read it and _write_xml to save the trimmed version.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, embedded objects, charts, diagrams, themes, and notes. These are files that no remaining relationship points to, so they are package clutter.

**Data flow**: It receives the unpacked presentation folder and the set of referenced paths. It checks known resource folders, deletes files not present in the referenced set, removes relationship files whose parent file is gone, and returns the deleted paths.

**Call relations**: run_clean calls this repeatedly after _collect_all_targets. Repeating matters because deleting one unused file can make another relationship file useless on the next pass.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes content-type entries for files that were deleted. The content-types file tells readers what kind of part each package file is, so stale entries can make the package inconsistent.

**Data flow**: It receives the unpacked presentation folder and a list of removed paths. It reads [Content_Types].xml, removes Override entries whose PartName matches a deleted file, and writes the file back only if something changed.

**Call relations**: run_clean calls this once at the end of cleanup. It uses _parse_xml and _write_xml to safely edit the XML table of contents.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint directory. It is the main worker behind the clean command.

**Data flow**: It receives the unpacked presentation folder. It finds active slides, deletes orphan slides and trash files, repeatedly removes unreferenced resources until no more can be removed, strips stale content-type entries, and returns all deleted paths.

**Call relations**: _cmd_clean calls this after checking that the folder exists. It coordinates the smaller cleanup helpers in the right order, passing each helper the information it needs.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as slide7.xml after slide1.xml through slide6.xml. This prevents new slides from overwriting existing files.

**Data flow**: It receives the slides directory. It scans filenames matching slide<number>.xml, finds the largest number, and returns one higher, or 1 if there are no slides.

**Call relations**: _create_from_layout and _clone_existing call this before writing a new slide file. It uses filename matching rather than presentation order.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to [Content_Types].xml, the package file that says what each important part is. A new slide file needs this entry so PowerPoint-style readers recognize it as a slide.

**Data flow**: It receives the unpacked presentation folder and the new slide filename. It reads the content-types XML, checks whether the slide is already listed, adds an Override entry if needed, and writes the XML back.

**Call relations**: _create_from_layout and _clone_existing call this after creating the slide file. It uses _parse_xml, adds an XML element through lxml, and saves through _write_xml.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the main presentation to the new slide file. In a .pptx, the main presentation does not point to slides by filename directly; it uses relationship IDs as labels.

**Data flow**: It receives the unpacked presentation folder and slide filename. It reads presentation.xml.rels, returns an existing relationship ID if one already points to that slide, or creates the next rId value, writes the relationship file, and returns the new ID.

**Call relations**: _create_from_layout and _clone_existing call this after the slide exists. The returned relationship ID is later printed so the caller can add the matching slide-list entry.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for the presentation’s slide list. This ID is separate from the slide filename and the relationship ID.

**Data flow**: It receives the unpacked presentation folder. It reads presentation.xml, finds existing slide ID numbers, and returns one higher, or 256 if none are present.

**Call relations**: _create_from_layout and _clone_existing call this when preparing the XML snippet that the user should add to presentation.xml.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that is connected to an existing slide layout. A layout is a template-like part of a PowerPoint file that defines default positioning and style.

**Data flow**: It receives the unpacked presentation folder and a layout filename. It checks that the layout exists, creates the next slide XML file from a built-in blank template, writes a relationship from the slide to the layout, registers the slide in package metadata, and prints the slide-list XML the user must add.

**Call relations**: run_add calls this when the source name looks like a slideLayout XML file. It calls the numbering and registration helpers, and exits with an error if the requested layout is missing.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Copies an existing slide into a new slide file. It is useful when the caller wants a duplicate that keeps the same visible content and most relationships.

**Data flow**: It receives the unpacked presentation folder and source slide filename. It verifies the source exists, copies the slide XML, copies its relationship file if present, removes any notes-slide relationship from the copy, registers the new slide, and prints the slide-list XML the user must add.

**Call relations**: run_add calls this for ordinary slide filenames. It uses _next_slide_number, _register_content_type, _register_presentation_rel, and _next_slide_id, plus _parse_xml and _write_xml if it needs to edit copied relationships.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Decides whether the add command should create a blank slide from a layout or duplicate an existing slide. It is the main worker behind the add command.

**Data flow**: It receives the unpacked presentation folder and a source name. If the source name looks like slideLayout*.xml, it sends the work to _create_from_layout; otherwise it sends it to _clone_existing. It does not return a value.

**Call relations**: _cmd_add calls this after validating the folder. It is a simple dispatcher between the two slide-creation paths.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a .pptx file and discovers the presentation’s slide order, including whether each slide is hidden. This keeps the thumbnail grid aligned with the order a presenter sees.

**Data flow**: It receives a .pptx path. It opens the file as a zip archive, reads the main presentation relationships to map IDs to slide names, reads presentation.xml for the ordered slide list, and returns a list of slide-name and hidden-status records.

**Call relations**: run_thumbnail calls this before rendering images. It uses zipfile to read inside the .pptx and lxml to parse the XML strings.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the visible slides of a PowerPoint file into JPEG images. It uses external command-line programs because this script does not render PowerPoint graphics itself.

**Data flow**: It receives a .pptx path and a temporary work folder. It asks LibreOffice, through the soffice command, to convert the presentation to PDF, then asks pdftoppm to convert PDF pages to JPEG files, and returns the created image paths.

**Call relations**: run_thumbnail calls this after reading slide order. If either external conversion fails, it raises an error that the command wrapper reports to the user.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a simple gray crossed-out image to stand in for a hidden slide. Hidden slides are not rendered by the PDF conversion, so this preserves their place in the thumbnail grid.

**Data flow**: It receives image dimensions. It creates a gray image of that size, draws two diagonal lines across it, and returns the image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden. It uses Pillow’s image and drawing tools.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches slide-order entries to the rendered image files and inserts placeholders for hidden slides. This bridges the difference between logical slide order and the images produced by rendering.

**Data flow**: It receives the slide order, rendered JPEG paths, and a work folder. It chooses placeholder dimensions from the first rendered image when possible, walks through each slide entry, pairs visible slides with the next rendered image, creates placeholder JPEGs for hidden slides, and returns labeled image pairs.

**Call relations**: run_thumbnail calls this after rendering. It calls _make_hidden_placeholder for hidden slides and uses Pillow to inspect the first rendered image size.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Combines several slide images into one labeled contact sheet. This makes it easy to review a deck at a glance.

**Data flow**: It receives labeled image paths, a column count, and a thumbnail cell width. It calculates row and canvas sizes, creates a white canvas, draws each slide label, resizes each slide image to fit its cell, pastes it into place, outlines it, and returns the finished image.

**Call relations**: run_thumbnail calls this for each chunk of slides that fits in one output grid. It uses Pillow to create the canvas, load fonts, open images, draw text, and paste thumbnails.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Runs the full thumbnail-grid creation process for a PowerPoint file. It is the main worker behind the thumbnail command.

**Data flow**: It receives a .pptx path, an output prefix, and a requested column count. It reads slide order, creates a temporary folder, renders slides to images, pairs images with slide labels and hidden placeholders, splits the work into grid-sized chunks, saves one or more JPEG grids, and returns the saved filenames.

**Call relations**: _cmd_thumbnail calls this after validating the input file and limiting the column count. It coordinates _extract_slide_order, _render_slide_images, _pair_slides_with_images, and _compose_grid.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Connects the clean command-line arguments to the cleanup logic. It also turns the result into user-friendly console output.

**Data flow**: It receives parsed command-line arguments. It turns the unpacked_dir argument into a path, checks that it exists, runs run_clean, then prints either the deleted files or a message saying nothing was found.

**Call relations**: build_parser attaches this function to the clean subcommand. When the script runs and that subcommand is chosen, the main block calls it through the parsed func field.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Connects the add command-line arguments to the slide-creation logic. It performs the basic folder existence check before changing files.

**Data flow**: It receives parsed command-line arguments. It turns the unpacked_dir argument into a path, exits with an error if the folder does not exist, and passes the folder and source name to run_add.

**Call relations**: build_parser attaches this function to the add subcommand. It is the command wrapper around run_add.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Connects the thumbnail command-line arguments to the thumbnail-building logic. It validates the input and reports either saved grid files or an error.

**Data flow**: It receives parsed command-line arguments. It checks that the input exists and has a .pptx extension, caps the requested column count, calls run_thumbnail, prints created grid paths, and exits with an error if thumbnail creation fails.

**Call relations**: build_parser attaches this function to the thumbnail subcommand. It wraps run_thumbnail so command-line users get clear messages instead of raw exceptions.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface for the script. It tells Python which subcommands exist, what arguments they require, and which function should run for each one.

**Data flow**: It creates an argument parser, adds clean, add, and thumbnail subcommands with their options, stores the matching command function on each subcommand, and returns the finished parser.

**Call relations**: The script’s main block calls this when the file is run directly. The parser it returns reads the user’s command and supplies the function that should be invoked.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`io_transport` · `manual packaging / command-line run`

A `.pptx` PowerPoint file is really a ZIP archive full of XML files and related resources. This script is the “zip it back up correctly” tool for a folder that already contains those unpacked pieces. Without it, edits made to the unpacked presentation folder would not become a normal PowerPoint file that PowerPoint can open.

The main flow checks two simple things first: the input must be a directory, and the output name must end in `.pptx`. It then copies the source folder into a temporary workspace, so the original folder is not changed while cleaning happens. Next it visits XML-style files, including `.xml` and `.rels` relationship files, and removes formatting-only whitespace. That means it strips indentation and line breaks that exist only to make XML readable to humans. It deliberately protects text elements, especially DrawingML text nodes used inside Office documents, because whitespace there can be meaningful slide content.

After cleanup, it creates the destination folder if needed and writes every file from the temporary workspace into a compressed ZIP archive with the `.pptx` name. The file can also be run directly from the command line, where it accepts an unpacked folder and an output file path.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: Rebuilds a PowerPoint `.pptx` file from a folder of unpacked presentation contents. Someone would use it after editing or generating the folder structure that belongs inside a PowerPoint file.

**Data flow**: It receives a source directory path and an output file path. It first checks that the source is a real folder and that the destination ends in `.pptx`; if either check fails, it returns no output path and an error message. If the inputs are valid, it copies the folder to a temporary work area, asks `_condense_xml` to clean each XML and relationship file there, then writes all files into a compressed `.pptx` ZIP archive. It returns the destination path and a success message.

**Call relations**: This is the main worker for the script and is also what the command-line section calls after reading arguments. During its run, it relies on `_condense_xml` for safe XML cleanup before handing the finished folder contents to the ZIP writer that creates the final PowerPoint file.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: Cleans one XML-related file by removing whitespace that is only formatting, while keeping actual text content safe. This makes the packaged presentation smaller and neater without accidentally changing visible slide text.

**Data flow**: It receives the path to one XML or `.rels` file. It parses the file into an XML tree, walks through each node, skips protected text nodes, removes blank-only text and tail whitespace, and removes special non-element children that cannot be written normally. It then writes the cleaned XML back to the same file using UTF-8 encoding. If parsing or writing fails, it prints a clear error message to standard error and raises the problem again so the packing process stops.

**Call relations**: This function is called by `assemble_pptx` once for each XML-style file in the temporary copy of the presentation. It does the careful cleanup step before `assemble_pptx` compresses everything into the final `.pptx` archive.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`io_transport` · `manual repair step or post-processing after PPTX generation`

A `.pptx` file is really a ZIP archive full of XML files. This script opens that archive, checks for three known problems, and rewrites the presentation only if something needs fixing. First, it removes “phantom” slide master references from `[Content_Types].xml` when the referenced slide master file does not actually exist. Without that, PowerPoint may show a repair dialog because the table of contents points to a missing part. Second, it removes ZIP directory entries. These folder-like entries can violate the Open Packaging Convention, which is the set of rules Office files must follow. Third, it protects text that starts or ends with spaces or tabs. In PowerPoint XML, such text needs `xml:space="preserve"`; otherwise PowerPoint may silently trim the whitespace, like removing indentation from code or alignment spaces from a layout. The script is careful: it reads the existing file, decides whether repairs are needed, writes a temporary fixed ZIP, and only then replaces the original file. This is like copying a damaged binder into a clean binder, fixing the index and preserving sticky-note spacing before swapping it back in.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This helper looks inside presentation XML files for text runs that begin or end with a space or tab, then marks them so PowerPoint keeps that whitespace. It prevents quiet content damage, such as indentation disappearing from generated slides.

**Data flow**: It receives a dictionary of ZIP entry names mapped to their raw file bytes. It checks only slide, layout, master, and notes XML files; parses each one as XML; finds DrawingML text elements named `<a:t>`; and adds `xml:space="preserve"` where needed. It returns a smaller dictionary containing only the changed XML files, plus a count of how many text elements were fixed.

**Call relations**: The main `repair` function calls this after reading the PPTX archive into memory. This helper uses `lxml.etree.fromstring` to turn XML bytes into a tree it can inspect, and `lxml.etree.tostring` to turn modified trees back into bytes. Its results are handed back to `repair`, which later writes the updated entries into the replacement PPTX file.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for one `.pptx` file. It checks whether the file exists, scans the ZIP contents for known PowerPoint compatibility problems, and rewrites the file with safe fixes when needed.

**Data flow**: It starts with a filename and turns it into a filesystem path. It reads the PPTX as a ZIP archive, records the real slide master files that exist, ignores folder entries when building the file map, removes references to missing slide masters from `[Content_Types].xml`, and asks `_repair_whitespace_preservation` to fix risky text spacing. If nothing is wrong, it prints a message and returns `True`. If repairs are needed, it writes a temporary ZIP without directory entries and with the corrected XML, moves that temporary file over the original, prints how many fixes were applied, and returns `True`. If the input file is missing, it prints an error and returns `False`.

**Call relations**: This function is used as the script’s top-level action when the file is run from the command line. It relies on `zipfile.ZipFile` to read and write the PPTX archive, regular expression helpers such as `re.match` and `re.sub` to recognize and clean XML references, `_repair_whitespace_preservation` for the whitespace-specific XML fix, and `shutil.move` to safely replace the old presentation with the repaired temporary copy.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### XLSX recalculation
Shared LibreOffice launch helpers support the workbook recalculation command for refreshing formulas and reporting remaining spreadsheet errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`util` · `during document automation when LibreOffice needs to be launched`

Some document workflows need LibreOffice to do work in the background, such as opening or converting spreadsheet files, without showing the usual office application on screen. This file is a small toolbox for that job. It prepares the right environment so LibreOffice can run in a headless or low-graphics mode, finds the folder where LibreOffice macros live, and gives other scripts a simple way to call the `soffice` command, which is LibreOffice’s command-line program.

The most important detail is the `SAL_USE_VCLPLUGIN` setting. In plain terms, it tells LibreOffice to use a simple, non-visual display backend instead of trying to connect to a full desktop interface. Without this, automated document tasks can fail on servers or background environments where there is no normal screen session.

The file also hides operating-system differences. LibreOffice keeps its macro folder in different places on macOS and Linux, so `macro_dir` chooses the right path. This is like having one address book entry for “LibreOffice macros” even though the actual street address changes by city.

Finally, `run_soffice` builds and runs the command, captures its output, and optionally stops waiting after a timeout. Other scripts can use this instead of repeating the same command setup each time.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the set of environment variables used when starting LibreOffice. It copies the current process environment, then adds a setting that encourages LibreOffice to run without needing a full graphical desktop.

**Data flow**: It starts with the current operating-system environment variables. It adds or overwrites `SAL_USE_VCLPLUGIN` with the value `svp`, then returns the updated dictionary. It does not change the global environment for the whole program; it only returns a prepared copy for a LibreOffice subprocess.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls `soffice_env` to get a safe environment for that external command. The returned environment is handed directly to the process launcher so LibreOffice starts with the intended headless-friendly setting.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Finds the folder where LibreOffice stores user macros for the current operating system. Scripts can use this when they need to install, read, or refer to LibreOffice macro files.

**Data flow**: It reads the current platform name, such as macOS or Linux. It picks the matching macro folder pattern, expands the `~` home-directory shortcut into a real user path, wraps that path in a `Path` object, and returns it. If the platform is not explicitly known, it falls back to the Linux-style location.

**Call relations**: This helper stands on its own for scripts that need the LibreOffice macro location. Internally, it asks the system what platform it is running on and uses `Path` so callers receive a path object that is easy to combine with filenames or folders.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs LibreOffice’s command-line program, `soffice`, with the given arguments. It captures what LibreOffice prints and can enforce a time limit so a stuck office process does not wait forever.

**Data flow**: It receives a list of command-line arguments and an optional timeout. It builds a full command beginning with `soffice`, prepares the LibreOffice environment through `soffice_env`, then starts the external process. The result is a completed-process object containing the exit status, standard output, and error output.

**Call relations**: Other document scripts call `run_soffice` when they need LibreOffice to perform a background task. `run_soffice` delegates environment preparation to `soffice_env`, then hands the final command to Python’s process-running tool so the external LibreOffice program actually runs.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on-demand spreadsheet recalculation`

Excel files can contain formulas whose saved results are stale, especially after another tool has edited the workbook. This file solves that by using LibreOffice in “headless” mode, meaning LibreOffice runs in the background without showing a window. It installs a tiny LibreOffice macro if needed, runs that macro on the target spreadsheet, and asks LibreOffice to calculate every formula, save, and close the file.

There is one extra wrinkle: LibreOffice can sometimes disturb table styling inside XLSX files. An XLSX file is really a zip archive full of XML files, so before recalculation this script takes a small snapshot of table style XML snippets. After LibreOffice saves the workbook, it puts those snippets back. This is like taking a photo of a table setting before moving the table, then putting the napkins and cutlery back where they were.

After recalculation, the script opens the workbook with openpyxl, a Python library for reading Excel files, and scans every cell for familiar Excel error strings such as #REF! or #DIV/0!. It returns a JSON-friendly summary: success, number of formulas, number of errors, and up to a limited number of cell locations for each error type. If something goes wrong, such as a missing file, broken macro setup, LibreOffice failure, or timeout, it returns a clear error message instead.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: Makes sure LibreOffice has the small Basic macro needed to recalculate and save the spreadsheet. Without this macro, the script would not have a reliable way to tell LibreOffice to calculate all formulas from the command line.

**Data flow**: It asks the helper module where LibreOffice stores macros, then checks whether the macro file already exists and contains the expected routine. If the macro folder is missing, it starts LibreOffice once in headless setup mode so the folder structure can be created. It then writes the macro text to disk and returns true if that worked, or false if it could not write the file.

**Call relations**: The main recalculation flow calls this before touching the spreadsheet. It relies on _soffice.macro_dir to find the macro location, _soffice.soffice_env to prepare LibreOffice’s environment, and subprocess.run to do the one-time LibreOffice initialization when needed.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: Copies the table style snippets from inside the XLSX file before LibreOffice edits it. This protects visual table formatting that might otherwise be changed or lost during the save.

**Data flow**: It receives a spreadsheet path, opens the XLSX as a zip archive, looks through table XML files under xl/tables/, and extracts any self-contained tableStyleInfo element it finds. It returns a dictionary mapping each table XML file name to the exact bytes of its saved style snippet.

**Call relations**: The recalc function calls this just before running LibreOffice. Its output is later passed to _restore_table_styles so the workbook can be repaired after LibreOffice has recalculated and saved it.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: Inserts or replaces one saved table style snippet inside a table XML file. It is the small repair tool used when putting original table styles back into the workbook.

**Data flow**: It receives the bytes of one table XML file and the saved style element bytes. If the XML already contains a tableStyleInfo element, it replaces that element. If not, it inserts the saved style just before the closing table tag. It returns the patched XML bytes.

**Call relations**: _restore_table_styles calls this for each table XML file that had a saved style. It does not work with files directly; it only transforms one piece of XML data handed to it.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: Rebuilds the XLSX file with the original table style snippets restored. This helps preserve workbook appearance after LibreOffice has recalculated and saved the file.

**Data flow**: It receives the spreadsheet path and the dictionary of saved style snippets. If there are no saved styles, it does nothing. Otherwise it creates a temporary zip file, copies every entry from the original workbook into it, patches any table XML files that need their style restored, then replaces the original file with the temporary one. If an error happens, it removes the temporary file if it exists.

**Call relations**: The recalc function calls this after LibreOffice finishes successfully. It uses _patch_table_style to repair individual table XML files, zipfile to read and write the XLSX archive, shutil.move to replace the workbook, and os.remove for cleanup after failed attempts.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: Looks through the recalculated workbook for visible Excel error values such as #REF!, #DIV/0!, and #N/A. This tells the caller whether recalculation produced a clean workbook or left broken formulas behind.

**Data flow**: It receives a spreadsheet path and opens the workbook with calculated cell values rather than formula text. It walks through every worksheet, row, and cell. When a string cell contains one of the known Excel error markers, it records the sheet name and cell coordinate. It returns a dictionary from each error type to the list of places where that error was found.

**Call relations**: The recalc function calls this after LibreOffice has recalculated and table styles have been restored. The error locations it returns are turned into the final JSON summary.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: Counts how many formula cells are in the workbook. This gives useful context for the result, such as whether a clean scan means many formulas succeeded or simply that there were few formulas.

**Data flow**: It receives a spreadsheet path and opens the workbook showing formulas rather than calculated values. It walks every worksheet and every cell, counting string values that start with '='. It closes the workbook and returns the final count.

**Call relations**: The recalc function calls this near the end, after scanning for errors. Its result is included in the final response alongside the error count and error summary.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: Runs the full spreadsheet recalculation job and returns a structured result. This is the main worker: it checks the file, prepares LibreOffice, protects table styles, runs recalculation, scans for errors, and reports what happened.

**Data flow**: It receives a filename and an optional timeout. First it verifies the file exists and converts the path to an absolute path. It ensures the LibreOffice macro is installed, snapshots table styles if possible, then runs LibreOffice headlessly with the recalculation macro. If LibreOffice times out or fails, it returns an error dictionary. If it succeeds, it restores table styles, scans for Excel error values, counts formulas, and returns a dictionary containing status, total errors, total formulas, and a compact error summary.

**Call relations**: main calls this when the script is run from the command line. recalc is the central coordinator: it calls _ensure_macro before LibreOffice, _snapshot_table_styles before saving, _restore_table_styles afterward, then _scan_errors and _count_formulas to build the final report. It hands the actual LibreOffice launch to _soffice.run_soffice.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: Provides the command-line interface for the script. It reads the spreadsheet path and optional timeout from the terminal command, runs recalculation, and prints the result as formatted JSON.

**Data flow**: It reads command-line arguments from sys.argv. If no file was provided, it prints a usage message and exits with an error code. Otherwise it takes the filename, converts the optional timeout to an integer or uses the default, calls recalc, converts the returned dictionary to pretty JSON text, and prints it.

**Call relations**: This function is called only when the file is executed directly as a script. It is the thin outer layer around recalc: users interact with main, and main delegates the real spreadsheet work to recalc.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).


### PDF form and preview tools
The PDF commands inspect and fill native forms, place text on static layouts, and render pages to images for preview or downstream processing.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command execution`

PDF forms are not just flat pages with text on them. A fillable PDF can contain hidden field objects for text boxes, checkboxes, radio buttons, and dropdown choices. This file reads those objects so the rest of the system can understand what can be filled, where it appears on the page, and what values are allowed. Without it, the project would have to guess field locations visually or place text manually, which is much less reliable.

The file uses pypdf, a Python library for reading and writing PDFs. It first looks for an AcroForm, which is the standard PDF structure for form fields. If that is missing or incomplete, it can fall back to scanning page annotations called widgets, which are the visible form controls on each page. Think of the AcroForm as the form’s address book, and widgets as the actual boxes and buttons printed on each page.

The extracted fields are represented as simple data classes: general fields, checkboxes, radio groups, and choices. The code also converts PDF coordinates into a more familiar top-down page coordinate system. For filling, it validates field names, pages, and allowed values before writing a new PDF, so bad input is caught before producing a broken or misleading document.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF contains visible fillable controls that are not listed in the main form directory. This matters because some PDFs have usable fields even when the normal form lookup says there are none.

**Data flow**: It receives a PDF reader, walks through every page, and looks at each page annotation. If it finds a widget annotation with a field type, it returns true; otherwise, after checking all pages, it returns false.

**Call relations**: The detect command uses this as a backup check after asking pypdf for normal form fields. It helps decide whether to tell the user that the PDF is fillable or that they should use manual layout tools instead.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a field by walking up its parent chain. This is needed because PDFs can store field names in pieces, like folders in a file path.

**Data flow**: It receives one annotation dictionary, collects each name part from the annotation and its parents, reverses those parts into top-to-bottom order, and returns a dotted name such as section.field. If no name parts exist, it returns nothing.

**Call relations**: The AcroForm extractor calls this while matching page widgets back to the fields found in the PDF’s form directory. The resulting name lets the extractor attach page numbers and rectangles to the right field.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns a raw PDF field dictionary into one of this file’s simpler field objects. It hides the PDF’s short internal field codes behind plain categories like text, checkbox, and choice.

**Data flow**: It receives raw field data and a field name. It reads the PDF field type, creates the matching FormField, CheckboxField, or ChoiceField object, and returns that object for later extraction or validation.

**Call relations**: Both extraction paths call this whenever they discover a field. It delegates checkbox details to _build_checkbox and choice-list details to _build_choice, so the rest of the code works with clean Python objects instead of raw PDF dictionaries.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which PDF value means checked and which means unchecked. This is important because PDFs do not always use the same checked value.

**Data flow**: It receives raw checkbox data and a field name. It reads the available states, chooses an on value and an off value when it can, prints a warning for unusual two-state checkboxes, and returns a CheckboxField.

**Call relations**: _build_field_from_dict calls this when it sees a button-type field that should be represented as a checkbox. The returned object is later used when extracting JSON and when validating fill values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a dropdown or list-style choice field. It records both the stored value and the human-readable text when the PDF provides both.

**Data flow**: It receives raw choice-field data and a field name. It loops through the available states, normalizes each option into a small dictionary with value and text, and returns a ChoiceField containing those options.

**Call relations**: _build_field_from_dict calls this for PDF choice fields. The result is later written to extracted JSON so users know which values are valid when filling the form.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Finds the checked value for a checkbox by looking at its appearance settings when that value was not already known. This helps with PDFs that do not expose checkbox states in the usual place.

**Data flow**: It receives a resolved PDF widget dictionary and an existing CheckboxField object. If the checkbox already has an on value, it leaves it alone; otherwise it looks for appearance names other than /Off and writes the first one into the checkbox object.

**Call relations**: The widget-based extractor calls this after building a checkbox from a page annotation. It enriches the checkbox before the field is added to the extracted field list.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from PDF coordinates into a more natural top-down page coordinate system. PDFs measure from the bottom of the page, while many layout tools think from the top.

**Data flow**: It receives a rectangle and the page height. It converts the rectangle’s numbers to floats, flips the vertical positions around the page height, and returns the adjusted rectangle.

**Call relations**: The field extractors and radio-option collector call this whenever they record where a field appears. It makes the exported positions easier for other tools and humans to understand.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts fillable fields by scanning the visible widgets on each page. This is the fallback path for PDFs whose main form directory is missing or incomplete.

**Data flow**: It receives a PDF reader, loops through pages and their annotations, keeps only widget annotations with field types, builds field objects, assigns page numbers and rectangles, fills in checkbox on-values when possible, and returns a list of fields.

**Call relations**: _extract_from_acroform calls this when pypdf cannot find normal form fields. It uses _build_field_from_dict, _flip_rect, and _extract_checkbox_on_value to turn page-level PDF details into the same field objects used by the main extraction path.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the form fields from the PDF’s standard AcroForm structure and attaches page locations to them. This is the main discovery routine used before exporting or filling a form.

**Data flow**: It receives a PDF reader, asks pypdf for the form fields, falls back to widget scanning if none are found, builds field objects, detects radio-button groups, walks page annotations to find locations, skips fields that cannot be located, sorts the final list, and returns it.

**Call relations**: The extract and fill commands both call this to understand the PDF before doing their work. It calls helper functions to build field objects, recover full names, collect radio options, flip rectangles, and fall back to widget-only extraction when needed.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one visible radio-button option to its radio group. Radio buttons are stored as several related widgets, so this gathers the separate buttons into one group description.

**Data flow**: It receives a radio widget annotation, the group name, page information, page height, and the dictionary of groups being built. It finds the one non-off value, creates the group if needed, records the option value and rectangle, and updates the shared radio-groups dictionary.

**Call relations**: _extract_from_acroform calls this when it sees a page widget that belongs to a radio-button candidate. It uses _flip_rect so each option’s location is recorded in the same coordinate style as other fields.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a consistent reading order for extracted fields. It sorts by page, then rough row, then left-to-right position.

**Data flow**: It receives a field object. For normal fields it uses the field rectangle; for a radio group it uses the first option’s rectangle. It turns that position into a tuple that Python can use for ordering.

**Call relations**: The AcroForm extraction flow uses this when sorting the combined list of ordinary fields and radio groups. The result is a JSON output that is easier to read and closer to how the form appears on the page.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts an internal field object into a plain dictionary that can be written as JSON. This is the bridge between Python objects and the user-facing extracted field file.

**Data flow**: It receives a FormField or one of its specialized forms. It writes common information such as name, kind, page, and rectangle, then adds checkbox values, radio options, or choice options when present, and returns the dictionary.

**Call relations**: The extract command calls this for each discovered field before writing the JSON file. It makes sure the output includes the extra details users need to fill special field types correctly.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a requested value is allowed for a checkbox, radio group, or choice field. It protects the output PDF from values the form does not understand.

**Data flow**: It receives a field description and a proposed string value. For fields with fixed choices, it compares the value with the allowed values and returns an error message if it is invalid; otherwise it returns nothing.

**Call relations**: _validate_fill_entries calls this while checking a user’s fill JSON. It provides the field-type-specific validation so the higher-level checker can report all input problems before writing a PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the detect command, which tells the user whether a PDF appears to contain native fillable fields. It is a quick yes-or-no check before trying extraction or filling.

**Data flow**: It receives command-line arguments, expects one PDF path, opens that PDF, checks for normal form fields or orphaned widget fields, and prints a message. If the arguments are wrong, it prints usage text and exits with an error.

**Call relations**: The main function dispatches to this when the user chooses detect. It relies on pypdf for normal field lookup and _has_orphaned_widgets for the backup scan.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract command, which writes a JSON description of all fillable fields in a PDF. Users can inspect or edit this JSON to prepare values for filling.

**Data flow**: It receives command-line arguments, expects an input PDF and output JSON path, opens the PDF, extracts fields, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written.

**Call relations**: The main function dispatches to this when the user chooses extract. It depends on _extract_from_acroform for discovery and _field_to_dict for producing JSON-friendly output.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill command, which writes values into a fillable PDF and saves a new PDF. It validates the requested values first so mistakes are caught early.

**Data flow**: It receives command-line arguments, expects an input PDF, a values JSON file, and an output PDF path. It reads the values, extracts field metadata from the PDF, validates the entries, groups values by page, updates the form fields in a cloned PDF writer, writes the output file, and prints a summary.

**Call relations**: The main function dispatches to this when the user chooses fill. It calls _extract_from_acroform to learn the form structure and _validate_fill_entries before handing page-specific values to pypdf’s PDF writer.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks the user’s fill JSON against the fields actually found in the PDF. It catches unknown field names, wrong page numbers, and invalid values.

**Data flow**: It receives a list of value entries and a lookup table of field metadata by name. It walks each entry, prints clear error messages for problems, asks _validate_fill_value to check allowed values, and returns true if any error was found.

**Call relations**: cmd_fill calls this before changing the PDF. If this function reports an error, the fill command exits instead of creating an output file with bad or ignored data.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Chooses which command to run based on the first command-line word. It is the script’s front door when someone runs formfill.py directly.

**Data flow**: It reads sys.argv, checks that a known subcommand was provided, prints general usage and exits on bad input, or calls the selected command with the remaining arguments.

**Call relations**: The Python __main__ guard calls this when the file is executed as a script. It dispatches to cmd_detect, cmd_extract, or cmd_fill through the SUBCOMMANDS table.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `on-demand command-line PDF extraction, preview, and filling`

Some PDFs look like forms but do not contain real fillable fields. They are more like a printed sheet: the boxes and lines are just shapes on a page. This file solves that problem by detecting useful layout clues, such as words, long horizontal rules, and small square boxes that are probably checkboxes. It can save those clues as JSON so a person or another tool can decide where answers should be placed.

The file also supports a preview step. Given an image of a page and a JSON field definition, it draws colored boxes over the areas where text and labels are expected to appear. This is like laying tracing paper over a form before writing on the real copy.

Finally, it can fill the PDF by adding text annotations. Before writing, it checks for common mistakes: text boxes that are too short for their font size, and boxes that overlap each other. It also translates coordinates when the field positions came from an image instead of directly from the PDF, because image coordinates and PDF coordinates measure the page differently. Without this file, the project would have no simple way to map static PDF pages, verify proposed field placements, and produce a filled output PDF.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: This method converts a box location into the coordinate format needed for a PDF annotation. It is used because images and PDFs count page positions differently, especially from the top versus from the bottom.

**Data flow**: It receives a bounding box as four numbers. If the box came from an image, it first scales the numbers from image size to PDF page size, then flips the vertical direction to match PDF coordinates. If the box is already in PDF-style coordinates, it only flips the vertical direction. It returns a four-number rectangle ready to be used when placing text on the PDF.

**Call relations**: During the fill command, the validation-and-fill step creates a coordinate mapper for the page being edited. It then asks this method to translate each field's content area before handing that rectangle to the PDF annotation writer.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: This function reads one PDF page and records the visual clues that matter for form layout: text, long horizontal lines, and small square boxes. Someone would use it when they need a machine-readable sketch of what is on a static PDF page.

**Data flow**: It takes a PDF page and its page number. It creates a page layout record, scans the page's drawn lines to find long horizontal rules, scans rectangles to find checkbox-sized squares, and asks the PDF library to extract words with their positions. It returns a filled PageLayout object containing the page size and all those detected items.

**Call relations**: The all-pages extraction flow calls this once for every page in the PDF. After this function captures the raw page clues, the caller adds row ranges so the final extraction output is more useful.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: This function turns detected horizontal lines into vertical row bands. It helps identify the spaces between form rows, not just the lines themselves.

**Data flow**: It reads the horizontal rule positions already stored in a PageLayout. It sorts their vertical positions, pairs each neighboring pair, and appends a row range with a top, bottom, and height. It changes the PageLayout in place and does not return a separate value.

**Call relations**: The all-pages extraction flow runs this after each page has been scanned. It depends on the horizontal rules collected by _extract_page and enriches that same page layout before it is saved.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: This function scans an entire PDF and builds layout information for every page. It is the main extraction worker behind the extract command.

**Data flow**: It receives a PDF file path, opens the PDF, and loops through its pages in order. For each page, it extracts page-level layout clues, computes row ranges, and adds the finished page layout to a list. It returns the list of PageLayout objects for the whole document.

**Call relations**: The extract command calls this after checking its command-line arguments. This function coordinates the page-by-page work by calling _extract_page and _compute_row_ranges, using pdfplumber to read the PDF contents.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: This function converts PageLayout objects into plain dictionaries that can be written as JSON. It exists because JSON cannot directly store custom Python dataclass objects.

**Data flow**: It receives a list of PageLayout objects. For each one, it copies the page number, size, text elements, horizontal rules, tick boxes, and row ranges into a normal dictionary. It returns a list of those dictionaries.

**Call relations**: After cmd_extract has scanned a PDF, it calls this function to prepare the results for saving. The returned plain data is then passed to JSON writing.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: This function checks a field-definition JSON file and then writes the requested text onto a PDF as annotations. It is the safety gate that tries to catch bad placements before producing the final filled document.

**Data flow**: It takes an input PDF path, a fields JSON path, and an output PDF path. It reads the JSON, opens the PDF, records each page's size, and walks through the requested form fields. For each field with text, it checks that the content box is tall enough and that it does not overlap earlier boxes on the same page. If the field passes, it converts the coordinates and creates a free-text PDF annotation. If any errors were found, it prints them and exits instead of writing a bad PDF. Otherwise it writes the annotated PDF to the output path.

**Call relations**: cmd_fill calls this after validating the command-line shape. Inside the filling flow, it uses _rects_overlap to detect collisions, CoordMapper.to_annotation_rect to translate field boxes into PDF annotation coordinates, and pypdf objects to read, annotate, and write the PDF.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: This small helper answers a simple question: do two rectangular boxes touch or cover the same space? It is used to prevent fields from being placed on top of each other.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges and returns true if they overlap, or false if one is clearly to the left, right, above, or below the other.

**Call relations**: _validate_and_fill calls this while checking each new field against fields that were already accepted on the same page. Its answer becomes either a validation error or permission to keep placing the annotation.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: This is the command handler for creating a JSON layout report from a PDF. It gives users a way to inspect a static PDF before deciding where form fields should go.

**Data flow**: It receives command-line arguments and expects an input PDF path and output JSON path. If the arguments are wrong, it prints usage help and exits. Otherwise it scans the PDF, converts the extracted layouts into JSON-friendly data, writes the JSON file, and prints a short summary of what it found.

**Call relations**: main dispatches to this function when the user runs the extract subcommand. It hands the real scanning to _extract_all_pages, then hands the result to _pages_to_dict before saving it.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: This is the command handler for drawing a visual preview of proposed field locations on a page image. It helps a user spot misplaced boxes before modifying the PDF.

**Data flow**: It receives a page number, a field-definition JSON path, an input image path, and an output image path. It opens the JSON and the image, then draws red rectangles around content areas and blue rectangles around label boxes for fields on the chosen page. It saves the marked-up image and prints how many fields were highlighted.

**Call relations**: main dispatches to this function when the user runs the preview subcommand. Unlike the fill path, this does not change the PDF; it uses image drawing tools to produce a checkable visual aid.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: This is the command handler for producing a filled PDF from a PDF and a field-definition JSON file. It is the user-facing entry point for the final writing step.

**Data flow**: It receives command-line arguments and expects an input PDF path, fields JSON path, and output PDF path. If the arguments are wrong, it prints usage help and exits. If they are correct, it passes the paths to the validation-and-fill routine.

**Call relations**: main dispatches to this function when the user runs the fill subcommand. It keeps the command interface simple and delegates the real checking and PDF writing to _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: This function is the command-line front door for the file. It chooses which operation to run: extract, preview, or fill.

**Data flow**: It reads the process arguments from sys.argv. If no known subcommand is provided, it prints the allowed usage and exits. If the subcommand is valid, it passes the remaining arguments to the matching command function.

**Call relations**: This runs when the file is executed directly as a script. It uses the SUBCOMMANDS table as a small routing map, sending the user's request to cmd_extract, cmd_preview, or cmd_fill.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command-line execution`

A PDF is often hard for image tools to work with directly, because its pages are stored as document pages rather than ordinary pictures. This file solves that by opening a PDF, rendering each page as an image, shrinking very large images to a practical size, and saving the results into a folder. Think of it like photocopying each page of a document into a separate photo file.

The main work happens in `render`. It first makes sure the output folder exists. Then it asks `pdf2image`, an external library that converts PDF pages into image objects, to render the PDF at 200 DPI. DPI means “dots per inch”; a higher value gives more detail but larger images. For each page image, the code checks whether either side is bigger than 1000 pixels. If so, it scales the image down while keeping the same shape, so pages do not become too large to store or display comfortably. Each page is saved as `page_1.png`, `page_2.png`, and so on.

The `main` function makes this usable from a terminal. It expects exactly two arguments: the input PDF path and the output directory. If they are missing, it prints a short usage message and exits.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts every page of a PDF file into a PNG image and saves those images in a chosen folder. It also limits image size so the output stays reasonably small and easy to use.

**Data flow**: It receives a PDF file path and an output folder path. It creates the folder if needed, reads the PDF through `pdf2image.convert_from_path`, then loops through the produced page images. Each image may be resized if it is wider or taller than 1000 pixels, then it is saved as a numbered PNG file. The function does not return a value; its visible results are the PNG files it writes and the progress messages it prints.

**Call relations**: This is the worker function for the script. `main` calls it after checking the command-line arguments. Inside, it relies on `pathlib.Path` to create and name output files safely, and on the external `pdf2image.convert_from_path` library call to do the actual PDF-to-image conversion.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the tool. It checks that the user supplied the expected PDF path and output directory, then starts the rendering work.

**Data flow**: It reads the program arguments from `sys.argv`. If there are not exactly two user-provided arguments, it prints the correct usage pattern and exits with an error code. If the arguments are present, it passes them to `render`, which creates the image files.

**Call relations**: This function runs when the file is executed directly as a script. It is the small front desk of the tool: it validates the command-line shape, stops early with `sys.exit` when the input is wrong, and otherwise hands the real conversion job to `render`.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
