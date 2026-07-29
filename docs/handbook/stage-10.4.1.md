# Document review state and annotation scripts  `stage-10.4.1`

This stage is shared support for the document-review skill, mostly used after or during a review rather than at app startup. It keeps the review’s memory in order and turns hidden review results into visible notes inside real documents.

The package marker file simply makes the scripts folder importable by Python, like putting a label on a toolbox. constants.py defines the standard filenames for the saved review state and review log, so every script looks in the same place. models.py defines what a review issue looks like, such as its location and message, and can format that issue as clear comment text.

manage_state.py is the control desk. It records the review phase, sections, claims, issues, and final summary in a JSON file, which is a plain text data file with structured fields. The annotation scripts then use that saved information. annotate_pdf.py highlights matching text and adds PDF comments. annotate_pptx.py writes findings as PowerPoint comments. annotate_xlsx.py adds comments to the right spreadsheet cells in a copied Excel file.

## Files in this stage

### Package and shared definitions
Package initialization, shared filenames, and the common issue model provide the vocabulary used by the review scripts.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. Its job is not to perform work directly, but to tell Python that the surrounding `scripts` directory should be treated as an importable package. In everyday terms, it is like putting a label on a folder so the rest of the system knows, “you can look in here for Python modules.”

Without this file, some Python environments or import styles might not reliably recognize the folder as part of the package structure. That could make nearby script modules harder or impossible to import, especially in tooling or older Python setups that expect every package folder to contain an `__init__.py` file.

Because the file is empty, it does not define functions, classes, settings, or startup behavior. Its importance is structural: it supports clean organization and predictable imports for the document-review skill scripts.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a tiny but useful coordination file. The document review skill needs to store two kinds of information on disk: its current state, so it can remember where it is in the review process, and a log, so it can keep a record of review events. Instead of typing those filenames directly in many places, the project defines them once here.

The constant `STATE_FILENAME` names the JSON file that stores the review state. JSON is a common text format for structured data. The constant `LOG_FILENAME` names the JSON Lines file used for the review log. JSON Lines means each line is its own separate JSON record, which is handy for appending new log entries over time.

This file matters because filenames are small details that can easily drift. If one part of the system wrote to `document_review_state.json` but another tried to read a slightly different name, the review skill could fail to resume correctly or appear to have lost its history. Keeping the names in one place is like putting labels on shared folders in an office: everyone knows which folder to use.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review comment creation`

This file is a small model layer for document review results. A review issue is a problem found in a document, such as a spelling mistake, a logic concern, or a piece of information that may need checking. The `DocumentIssue` type spells out the fields every issue is expected to have: an ID, category, severity, description, where it appears, the original text, the suggested replacement, and links to any related root issue. This is like a standard form that every reviewer result must fill in, so later code can read the same fields without guessing.

The file also contains friendly labels for internal issue type names. For example, the code name `spelling_grammar` becomes the human-facing label `Spelling/Grammar`. That matters because comments shown to users should be clear, not written in machine-style identifiers.

Finally, `format_comment` turns one issue into a short block of comment text. It starts with the issue category and severity, adds the issue description, and optionally adds a suggested replacement if one exists. Without this file, other parts of the review tool would either have to duplicate this formatting logic or risk producing inconsistent comments.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns a structured document issue into a readable comment that can be shown to a person. It is useful when the review system needs to explain what is wrong and, when available, suggest replacement text.

**Data flow**: It receives a `DocumentIssue`, which is a dictionary-like record containing details about one document problem, plus a yes-or-no option for whether to include a suggestion. It looks up a friendly label for the issue type, reads the severity and description, and builds a comment string. If suggestions are allowed and the issue has `new_text`, it adds a `Suggested:` line; otherwise it leaves that part out. The output is one formatted text block, and it does not change the issue itself.

**Call relations**: When this function needs to know whether suggested replacement text exists, it asks the issue record for its `new_text` value using the record's `get` method. That lets it safely check the optional suggestion before adding it to the final comment.

*Call graph*: 1 external calls (get).


### Review state management
The state manager records the review lifecycle, including phases, sections, claims, issues, and summaries.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `active throughout the document review workflow`

This script is the notebook for a structured document review. Without it, the reviewer or automation around the reviewer would have no reliable shared record of what document is being reviewed, which step comes next, which claims still need checking, or which issues have already been found.

The review moves through clear phases: outlining the document, finding claims, checking facts, finding issues, and completing the review. The script writes the main record to document_review_state.json and also appends a timeline of actions to a log file. Think of the state file as the current checklist on the reviewer’s desk, and the log file as the audit trail showing how the checklist changed.

Users run subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, status, get-claims, and get-issues. Inputs for larger updates can come directly as JSON text or from a JSON file. The script carefully checks that required fields are present, that page numbers are positive, and that claim types, issue types, statuses, and severities use known values. Most write commands return one clean JSON object, which makes the tool easy for another program to call and understand. Read-only listing commands print human-readable summaries.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the final machine-readable result for successful write-style commands. It combines a plain message with structured progress details so another tool can read the response as valid JSON.

**Data flow**: It receives a message, the current phase, the document name, and any extra progress details. It builds one dictionary, turns it into JSON text, and prints it to standard output.

**Call relations**: After commands such as starting a review, adding sections, adding claims, updating claims, adding issues, or submitting the review finish their work, they call this function to report the new state in a consistent shape.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds a timestamped record of something that happened during the review. This gives the review process an audit trail, like a diary of every command that changed or inspected the state.

**Data flow**: It receives the command name, the phase before and after the command, and extra details such as counts or filters. It adds the current UTC time, converts the entry to JSON, and appends it as one line in the log file.

**Call relations**: Most user-facing commands call this after they have loaded or changed the state. It does not decide what the command means; it simply records what the command reports happened.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the saved review state from disk. It is the common starting point for commands that need to know the current document, phase, sections, claims, or issues.

**Data flow**: It looks for the state JSON file. If the file is missing, it prints an error and stops the program; if present, it reads the file and turns the JSON text into a Python dictionary.

**Call relations**: Commands that work with an existing review call this first. If init has not been run yet, this function prevents later commands from working with an undefined or accidental state.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk. It makes changes permanent so the next command can continue from the same point.

**Data flow**: It receives the state dictionary, formats it as readable JSON, and writes it to the state file using UTF-8 text encoding.

**Call relations**: Commands that create or modify review data call this after updating the in-memory state. It pairs with load_state: one brings the checklist in, the other puts the updated checklist away.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user if they run a command during an unexpected review phase. It does not stop the command; it simply points out that the workflow may be out of order.

**Data flow**: It reads the current phase from the state and compares it with the expected phase. If they differ, it prints a warning to standard error and leaves the state unchanged.

**Call relations**: Workflow commands call this near the beginning. The actual commands still decide whether to continue, change phase, or save data afterward.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that a submitted JSON object contains the fields the command needs. This prevents incomplete sections, claims, issues, or claim updates from being saved.

**Data flow**: It receives one item, a list of required field names, and a label for error messages. If any fields are missing, it prints a clear error and stops the program; otherwise it returns without changing anything.

**Call relations**: Commands that accept structured JSON call this before using the data. It acts like a front-door checklist before the command creates or updates review records.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a known set of allowed words. This keeps categories such as claim status or issue severity consistent across the saved state.

**Data flow**: It receives a value, the allowed set, and the field name. If the value is not allowed, it prints an error showing the valid choices and stops the program; otherwise the value is accepted.

**Call relations**: Claim, issue, and claim-update commands use this before saving category-like fields. This protects later reporting code from unexpected spellings or made-up values.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a value into a page number and confirms it is at least 1. It is used so document sections have sensible page ranges.

**Data flow**: It receives a value and field name, tries to convert the value to an integer, and checks that the number is positive. It returns the cleaned integer or stops the program with an error.

**Call relations**: The add-sections command uses this for start_page and end_page before saving a section. The command then performs the additional check that the ending page is not before the starting page.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a required text-like field is not blank. It allows either a string or an integer input, then converts the value to a string.

**Data flow**: It receives a value and field name. If the value is neither a string nor an integer, or if it becomes empty after trimming whitespace, it prints an error and stops; otherwise it returns the string.

**Call relations**: Commands that add claims or issues use this for locations. This ensures each saved item points to a meaningful place in the document.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which is a more specific text marker in the document. The anchor may be absent, but if present it must be real non-empty text.

**Data flow**: It receives a value and field name. If the value is null, it returns null; if it is a non-empty string, it returns it; otherwise it prints an error and stops.

**Call relations**: The add-claims and add-issues commands use this before saving optional anchors. It lets records be precise when possible without forcing every record to have an anchor.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input either from a command-line --data value or from a file named by --file. This lets users choose between quick inline input and larger saved input files.

**Data flow**: It receives parsed command-line arguments. If a file path was supplied, it reads and returns that file’s text; otherwise it returns the inline data string.

**Call relations**: Commands that accept batches of sections, claims, claim updates, or issues call this before parsing JSON. The argument parser ensures only one of --data or --file is supplied.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates a fresh state file with the first phase set to outline and all review lists empty.

**Data flow**: It receives command-line arguments containing the document filename. It checks the filename is not blank, builds the initial state dictionary, saves it, logs the initialization, and prints a JSON success result.

**Call relations**: The main dispatcher calls this for the init subcommand. This is the command that must run before commands that load an existing state can succeed.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document’s major sections and advances the review to the claim-finding phase. Sections give later claims and issues a place to belong.

**Data flow**: It loads the current state, reads a JSON array from --data or --file, validates each section’s name and page range, and stores sections by name. It then changes the phase to find_claims, saves the state, logs the action, and prints a JSON result with the added sections.

**Call relations**: The main dispatcher calls this for add-sections. It uses the shared loading, validation, saving, logging, and result-output helpers to turn raw section data into durable review structure.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in a specific section. These claims become the items that the fact-checking step will later verify, refute, or mark inconclusive.

**Data flow**: It loads the state, checks the named section exists, reads a JSON array of claims, validates required fields and claim type, assigns each claim a new claim ID, and saves each one with an initial unverified status. It saves the updated state, logs the added IDs, and prints a JSON summary.

**Call relations**: The main dispatcher calls this for add-claims. It relies on the validation helpers to reject malformed claim data and uses the shared save, log, and result helpers after creating the records.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the outcome of fact-checking existing claims. It updates claim statuses and stores any source URLs used as evidence.

**Data flow**: It loads the state, moves from find_claims to fact_check if needed, reads a JSON array of updates, verifies each claim exists, checks the new status is allowed, increments the claim’s attempt count, and appends source URLs. It saves the state, logs status counts, and prints a JSON result.

**Call relations**: The main dispatcher calls this for update-claims. It bridges the claim-finding phase and fact-checking phase, using validation helpers before changing stored claim records.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in the document, such as incorrect public data, inconsistent numbers, grammar problems, or narrative logic issues. These records describe what is wrong and often suggest replacement text.

**Data flow**: It loads the state, moves from fact_check to find_issues if needed, confirms the section exists, reads a JSON array of issues, validates required fields, issue type, severity, location, and optional anchor, then assigns new issue IDs. It saves the new issues, logs them, and prints a JSON result.

**Call relations**: The main dispatcher calls this for add-issues. It reuses the same data-input, validation, persistence, logging, and result-output pattern used by section and claim commands.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Marks the review as finished and saves the final summary. This is the closing step of the workflow.

**Data flow**: It loads the state, checks that the summary is not blank, sets the phase to complete, stores the summary, and saves the state. It counts sections, claims, and issues, logs the submission, and prints a JSON completion message.

**Call relations**: The main dispatcher calls this for submit. It uses the shared state and logging helpers, and it is the command that changes the review into its final complete phase.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved claims in a readable text format, optionally narrowed by status or section. It is useful for checking what still needs fact-checking or reviewing claim outcomes.

**Data flow**: It loads all claims from the state, applies any requested status or section filters, logs the lookup, and prints either a no-match message or a detailed list of matching claims with text, description, location, and sources.

**Call relations**: The main dispatcher calls this for get-claims. Unlike the write commands, it does not save state or emit a JSON result; it reads the saved checklist and presents selected claim records to a person.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Shows saved issues in a readable text format, optionally narrowed by severity or section. It helps a reviewer inspect the problems that have been found.

**Data flow**: It loads all issues from the state, applies severity and section filters if provided, logs the lookup, and prints either a no-match message or a detailed list with location, original text, context, description, and suggested text.

**Call relations**: The main dispatcher calls this for get-issues. It follows the same read-and-report pattern as cmd_get_claims, but for issue records instead of claim records.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Prints a dashboard-style summary of the current review. It gives a quick answer to: what document is this, what phase are we in, and how much has been recorded?

**Data flow**: It loads the state and prints the document name, phase, section list, claim counts by status, issue counts by severity and type, and the final summary if one exists. It does not change the state.

**Call relations**: The main dispatcher calls this for the status subcommand. It depends only on load_state because it is a read-only snapshot of progress.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each user command to the right function. It is the front desk for the whole script.

**Data flow**: It builds an argument parser, defines all supported subcommands and their arguments, parses what the user typed, then looks up and calls the matching command function with the parsed arguments.

**Call relations**: When the script is run directly, this function starts the program. It does not do review work itself; it routes init, add-sections, add-claims, update-claims, add-issues, submit, get-claims, get-issues, and status to their command functions.

*Call graph*: 1 external calls (ArgumentParser).


### Document annotation writers
Format-specific annotation scripts read saved findings and write them back into PDFs, PowerPoint files, and spreadsheets.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `post-review export`

This file turns a document review result into something a human can open and inspect inside a normal PDF reader. Earlier parts of the review process save issues in a JSON state file. This script uses that saved list to mark the original PDF: it looks for the problem text on the right page, highlights it in a color based on severity, and adds a small comment note with the reviewer’s explanation.

The flow is simple. First it loads the issue list from the expected state file. Then it opens the input PDF with PyMuPDF, a library for reading and editing PDF files. For each issue, it treats the issue location as a page number. If the page number is invalid, it skips that issue rather than stopping the whole run. It searches the page for the original text. If the full beginning of the text is not found, it tries a shorter prefix, like searching for the first few words of a quoted sentence when the whole quote is too long or slightly different. When it finds the text, it highlights it and puts the note beside the highlight. If it cannot find the text, it still adds a note at a fixed fallback spot so the issue is not lost.

Finally, it saves a new annotated PDF. Without this file, review findings would remain separate from the document, making them harder to check in context.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved document review results from the local state file. It gives the rest of the script a plain list of issues to place into the PDF.

**Data flow**: It starts with the expected state filename from configuration. It checks whether that file exists; if not, it prints a clear error and stops the script. If the file is present, it reads the JSON text, turns it into Python data, pulls out the saved issues, and returns them as a list.

**Call relations**: The main annotation flow calls this first, before opening or editing the PDF. It depends on standard file-path and JSON reading tools, and it hands the issue list back to annotate so the PDF can be marked up.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to find where a piece of issue text appears on a PDF page. The returned locations are used as the places to draw highlights.

**Data flow**: It receives a PDF page and the original text connected to an issue. It first searches for a longer starting slice of that text. If that finds nothing, it searches again with a shorter slice. It returns whatever matching text locations the PDF library found, or an empty result if there were no matches.

**Call relations**: The annotate function calls this for each issue after it has selected the right page. Its result decides whether annotate can place a highlight beside the exact text, or must fall back to placing only a note at a default position.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It opens the input PDF, adds highlights and comment notes for each saved issue, and writes the finished annotated PDF to a new file.

**Data flow**: It receives an input PDF path and an output PDF path. It loads issues, opens the PDF, checks each issue’s page number and severity, formats the comment text, searches for the original text, and adds a highlight plus a colored sticky note when possible. If the text cannot be found, it still adds the note at a default point. At the end, it saves the changed document to the output path, closes the file, and prints how many annotations were added.

**Call relations**: This function coordinates the whole script. It calls load_issues to get the review findings, calls find_quads to locate quoted text on each page, uses format_comment to build the note text, and uses PyMuPDF to open, edit, and save the PDF. When the file is run from the command line, this is the function invoked with the two paths supplied by the user.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `post-review annotation/export`

PowerPoint files are actually zip files full of XML files. This script uses that fact to add comments without opening PowerPoint itself. It reads document review issues from the saved state file, groups them by slide number, copies the input presentation to a new output file, unzips that copy into a temporary folder, adds the XML files PowerPoint expects for comments, then zips everything back up.

The important problem it solves is visibility. A reviewer or automation system may find problems in a presentation, but those findings are much more useful when they appear on the exact slides they belong to. Without this file, the issues would stay in a separate JSON file and the user would have to manually match them back to slides.

The script creates one comment file per slide that has issues. It also creates a comment author entry, adds the needed relationship files that tell PowerPoint where to find those comments, and updates the content-types file so PowerPoint recognizes the new XML parts. Think of it like adding labeled notes to a binder: the notes themselves are not enough; the binder also needs an index saying where the notes are and what kind of pages they are.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: Reads the saved document review results from the expected state file. If the file is missing, it stops the script with a clear error because there is nothing to annotate.

**Data flow**: It looks for the configured state filename in the current working directory, reads its JSON text, and pulls out the stored issues. The output is a list of issue records; if the file is absent, the script prints an error and exits instead of continuing with bad or missing data.

**Call relations**: The main annotation flow calls this first inside annotate. Its result becomes the raw set of findings that the rest of the script turns into PowerPoint comments.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: Sorts review issues into buckets by slide number so each slide can get its own comment file. Issues whose location is not a usable slide number are skipped.

**Data flow**: It receives a list of issue records, reads each issue's location field, and tries to interpret that location as a 1-based slide number. It returns a dictionary where each slide number points to the issues that belong on that slide.

**Call relations**: annotate calls this after loading the issues. The grouped result is then passed to write_slide_comments and write_author_and_rels so both the actual comments and the PowerPoint bookkeeping match the same slide set.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: Finds the largest existing relationship ID in a PowerPoint relationship file. This lets the script add a new relationship without reusing an ID that is already taken.

**Data flow**: It receives the path to a .rels XML file, which is a PowerPoint index file that links one part of the presentation to another. If the file does not exist, it returns 0; otherwise it parses the XML, scans IDs such as rId1 or rId7, and returns the highest number found.

**Call relations**: add_relationship calls this when it needs to create a fresh relationship entry. It supplies the safe next number that prevents clashes with existing PowerPoint links.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: Adds a link inside a PowerPoint relationship file, creating the file if it does not already exist. These links are how PowerPoint knows that a slide has a comment file or that the presentation has a comment author file.

**Data flow**: It receives a relationship-file path, a relationship type, and a target file path. It opens or creates the XML relationship list, checks whether the same type is already present, and if not, adds a new relationship with the next available rId value. It writes the updated XML back to disk.

**Call relations**: write_slide_comments uses this to connect each slide to its new comment XML file. write_author_and_rels uses it to connect the presentation to the comment author XML file. Internally, it relies on find_max_rel_id to choose an unused ID.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: Creates the actual PowerPoint comment XML files for the slides that have review issues. Each issue becomes a visible comment written in PowerPoint's expected format.

**Data flow**: It receives the temporary unzipped presentation folder and the issues grouped by slide. For each slide, it creates a comment XML file, writes one comment entry per issue using formatted review text, gives each comment an author, timestamp, index, and position, then adds a relationship from that slide to its comment file. It returns the total number of comments created.

**Call relations**: annotate calls this after the presentation has been extracted into a temporary folder. It hands off relationship creation to add_relationship and uses format_comment from the models module to turn each issue into readable comment text. Its comment count is later passed to write_author_and_rels so the author metadata matches the generated comments.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: Writes the extra PowerPoint bookkeeping that makes the comments valid and discoverable. This includes the comment author record, the presentation-level link to that author record, and content-type entries for the new XML files.

**Data flow**: It receives the temporary presentation folder, the total comment count, and the slide grouping. It writes a commentAuthors.xml file naming the author as Flying Object, adds a relationship from the presentation to that file, then edits [Content_Types].xml so PowerPoint knows the author file and each comment file are valid PowerPoint comment parts.

**Call relations**: annotate calls this after write_slide_comments has created the per-slide comment files. It uses add_relationship for the presentation-level relationship, and it updates the package-wide content type list so PowerPoint can open the modified file correctly.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: Runs the full PowerPoint annotation process from input file to output file. It is the main worker used by the command-line script.

**Data flow**: It receives an input PowerPoint path and an output PowerPoint path. It loads issues, stops early if there are none, groups them by slide, copies the input presentation to the output location, unzips the output file into a temporary folder, writes comment files and supporting metadata, zips the folder back into the output file, prints how many comments were added, and finally deletes the temporary folder.

**Call relations**: The command-line block at the bottom calls annotate after checking that the user supplied exactly an input and output path. annotate coordinates the whole sequence by calling load_issues, group_by_slide, write_slide_comments, and write_author_and_rels, while using file-copying, zip, directory-walking, and cleanup tools from the Python standard library.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review export`

This file is a small command-line tool for the final step of a document review workflow. Earlier parts of the system write review issues into a JSON file named by STATE_FILENAME. This script reads those issues, copies the original spreadsheet to a new output file, and then places each issue into the spreadsheet as an Excel cell comment.

The script tries to put each comment where a human would expect it. First it looks for the worksheet named in the issue location. If the issue gives an exact cell reference, called an anchor, it tries that cell first. If that does not work, it searches the worksheet for a cell containing the issue’s original text. If it still cannot find a match, it searches all worksheets. As a last resort, it puts the note on cell A1 of the first sheet, combining multiple fallback comments if needed.

This matters because review findings are easier to understand when they appear directly in the spreadsheet, like sticky notes on the page. Without this script, users would have to cross-reference a separate JSON file against the spreadsheet by hand.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved document review results from the local state file. It stops the script with a clear error if that state file is missing, because there would be nothing to annotate.

**Data flow**: It starts with the expected state filename from STATE_FILENAME. It checks whether that file exists, reads its JSON text, turns that text into Python data, and returns the issue records from the state. If the file is absent, it prints an error to standard error and exits the program.

**Call relations**: The main annotate flow calls this first, before touching the spreadsheet. It supplies annotate with the list of review issues that later become Excel comments.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell whose visible value contains a given piece of text. It is used when the script does not have, or cannot use, an exact cell address.

**Data flow**: It receives a worksheet and some target text. It normalizes the target text by trimming spaces and ignoring letter case, then scans every cell in every row. If a cell has a value containing that target text, it returns that cell; if no match is found, it returns nothing.

**Call relations**: annotate calls this when it needs to locate the original reviewed text in a worksheet. It may be used first on the named worksheet, then across all worksheets if the first search fails.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks through an Excel workbook for a worksheet with a matching name. It ignores differences in uppercase and lowercase letters so small naming differences do not stop annotation.

**Data flow**: It receives a workbook and a location name. It compares that name with each worksheet title after converting both to lowercase. It returns the matching worksheet if there is one, otherwise it returns nothing.

**Call relations**: annotate uses this near the start of placing each issue. If it finds the named worksheet, later steps can try an exact cell anchor or a text search inside that sheet.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a prepared comment to a specific cell reference, such as B12. It exists because exact cell placement can fail if the reference is invalid.

**Data flow**: It receives a worksheet, a cell reference, and an already-created comment. It tries to look up that cell and assign the comment to it. If that works, it returns true; if the cell reference is not valid for the worksheet, it returns false without crashing the whole script.

**Call relations**: annotate calls this when an issue includes both a target worksheet and an anchor. If this exact placement succeeds, annotate does not need to search for the text; if it fails, annotate falls back to broader searches.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It copies an input XLSX file, opens the copy, and adds each review issue as an Excel comment in the best cell it can find.

**Data flow**: It receives an input spreadsheet path and an output spreadsheet path. It loads review issues, copies the input file to the output location, opens the output workbook, turns each issue into comment text, and tries several placement strategies: exact worksheet and cell, matching text in the named worksheet, matching text anywhere, then cell A1 as a fallback. It saves the workbook and prints how many comments were added.

**Call relations**: This function coordinates the whole script. It calls load_issues to get the review findings, uses format_comment to turn each issue into readable comment text, creates openpyxl Comment objects, asks find_worksheet and find_cell where to put them, and uses _place_on_cell for exact cell placement. The command-line block at the bottom calls annotate after checking that the user provided an input and output path.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).
