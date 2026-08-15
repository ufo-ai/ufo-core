# Document extension and review annotation workflow  `stage-14.5`

This stage is shared support for the document review skill. It is not the main reviewer itself; it is the toolbox that records what the review found and writes those findings back into finished documents so people can see them in familiar apps.

Two small package marker files make the document extension and its scripts folder importable by Python. They are like labels on drawers, so other code can find what is inside. The constants file keeps the agreed names for the review state file and the review log file, so every script looks in the same place.

The models file defines what a “review issue” looks like, such as the problem text and where it belongs, and turns it into a readable comment. The manage_state command-line tool keeps the review’s running notebook: progress, claims, issues, final summary, and log entries, saved as JSON. The annotation scripts then use that saved state. One adds highlights and sticky-note comments to PDFs. One writes comments into PowerPoint slides. One copies an Excel workbook and adds the issues as cell comments.

## Files in this stage

### Package scaffolding
Package marker files make the document extension and review scripts importable without adding runtime behavior.

### `extensions/documents/ufo_ext_documents/__init__.py`

`other` · `import/package discovery`

This is an empty package initializer. In Python, a file named `__init__.py` tells the interpreter that the surrounding folder should be treated as an importable package. Think of it like a label on a drawer: the label does not contain the tools, but it lets the rest of the workshop find the drawer by name. Without this file, depending on the Python version and packaging setup, imports that refer to `ufo_ext_documents` could fail or behave differently. Since the file is empty, it does not run setup code, expose shortcut names, or change any settings. Its value is structural: it helps the documents extension have a clear package boundary.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/__init__.py`

`other` · `import/package discovery`

This is an empty Python `__init__.py` file. Its main job is structural: it tells Python that the surrounding `scripts` directory should be treated as an importable package. Think of it like a label on a folder in a filing cabinet. The label does not contain the documents, but it helps the rest of the system find and refer to what is inside.

In this project, the folder belongs to a document-review skill under the documents extension. Other files may contain the actual scripts or helper code for reviewing documents. This file simply makes that folder easier and more reliable to import from Python code, especially in environments or tools that still expect an `__init__.py` file to recognize packages.

Nothing is executed here, no settings are loaded, and no functions or classes are defined. If it were removed, some import paths or packaging tools might stop recognizing this directory as part of the Python package structure.


### Review state definitions
Shared filenames, state-management commands, and issue models define how document review progress and findings are stored.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a small constants file. It does not run any logic by itself. Instead, it gives clear, shared names to two important output files used during document review.

`STATE_FILENAME` is the JSON file where the review process can store its current state. In plain terms, this is like a bookmark: it lets the system remember what it was doing, instead of starting from scratch every time.

`LOG_FILENAME` is the JSON Lines log file, meaning a text file where each line is a separate JSON record. This is useful for recording review events one after another, like entries in a diary.

The value of this file is consistency. Without it, different parts of the document review code might accidentally use slightly different filenames, which could cause missing state, scattered logs, or hard-to-debug file problems. By putting the filenames here, other scripts can import the same names and stay coordinated.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `command-line document review steps`

A document review has several steps: outline the document, find claims that need checking, check those claims, find writing or factual issues, and finally submit a summary. This script is the small “ledger” that remembers where the review is and what has been found so far. Without it, each step would have to pass around loose notes, and later tools or people could not reliably know what has already been done.

The script is run from the command line with subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, status, and lookup commands. Most write commands load document_review_state.json, check that the incoming data has the expected shape, update the state, save it back to disk, and append a JSON-line log entry. Think of the state file as the review notebook, and the log file as the timestamped sign-in sheet showing who changed the notebook and when.

The file is careful about bad input. It rejects missing required fields, unknown claim or issue types, empty names, invalid page numbers, and unknown IDs. It warns if a command is run in an unexpected phase, but usually continues, which gives the workflow some flexibility while still making mistakes visible.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the successful result of a write command as one clean JSON object. This gives both a human-readable message and structured progress data that another program can read.

**Data flow**: It receives a message, the current phase, the document name, and optional extra result details. It builds one dictionary containing those pieces, turns it into JSON text, and prints it to standard output. It does not change the saved review state.

**Call relations**: The state-changing commands call this after they have saved their work and logged the action. It is the final handoff from internal state changes to a machine-readable command result.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Records that a command happened, including the time and the phase before and after it ran. This creates an audit trail so someone can later see how the review progressed.

**Data flow**: It receives the command name, the old phase, the new phase, and any extra details such as counts or IDs. It adds a current UTC timestamp, converts the entry to JSON, and appends it as one line to the log file. The review state itself is not changed.

**Call relations**: Most commands call this after reading or changing state. It sits beside the main state file as a history recorder, so commands can report not only what the state is now but also what was done to get there.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the current document review state from disk. If the state file does not exist, it stops the command and tells the user to initialize a review first.

**Data flow**: It looks for the configured state filename. If the file is missing, it prints an error and exits. If it exists, it reads the JSON text and returns it as a Python dictionary for the command to inspect or modify.

**Call relations**: Every command that needs an existing review calls this first. It is the doorway into the saved review notebook before commands add sections, claims, issues, status changes, or reports.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk in readable JSON form. Commands use it after they have changed the review notebook.

**Data flow**: It receives the full state dictionary, converts it to indented JSON text, and writes it to the configured state file. The output is the updated file on disk; nothing is returned.

**Call relations**: The commands that create or update review data call this before logging and printing their final result. It is the point where in-memory changes become durable state.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user when a command is being run in a different review phase than expected. It does not block the command; it only makes the mismatch visible.

**Data flow**: It receives the loaded state and the phase the command normally expects. If the state’s current phase is different, it prints a warning to standard error. It returns nothing and changes nothing.

**Call relations**: Workflow commands call this near the start, after loading state. It acts like a caution sign: the command may still continue, but the user is told the review may be out of the usual order.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that a section, claim, issue, or claim update contains all fields needed for the command to make sense. It prevents half-formed records from entering the state file.

**Data flow**: It receives one input dictionary, a list of required field names, and a label for the error message. If any required field is missing, it prints a clear error and exits. If all fields are present, it simply lets the caller continue.

**Call relations**: Commands that accept JSON arrays call this for each incoming item before building or updating records. It is one of the first safety checks before state is saved.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a known set of allowed choices, such as an approved claim status or issue severity. This keeps the state file consistent and searchable.

**Data flow**: It receives a value, the allowed set, and the field name being checked. If the value is not allowed, it prints an error listing the valid choices and exits. Otherwise, control returns to the caller with no returned value.

**Call relations**: Claim, claim-update, and issue commands call this while validating incoming JSON. It helps those commands reject misspellings or unsupported categories before saving them.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number into an integer and checks that it is at least 1. This protects section ranges from impossible page values.

**Data flow**: It receives a value that should represent a page number. It tries to convert it to an integer, rejects non-numbers or numbers below 1, and returns the valid integer. Bad input causes an error message and command exit.

**Call relations**: The add-sections command uses this for start_page and end_page before storing a section. It supplies clean page numbers for the later section-range check.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a value can be used as meaningful text and is not blank. It is used for fields like locations, where an empty value would make a finding hard to locate in the document.

**Data flow**: It receives any value and a field name. It allows strings and integers, converts the value to text, rejects blank text, and returns the cleaned string. If the value is the wrong kind or empty, it prints an error and exits.

**Call relations**: The add-claims and add-issues commands call this when they need a usable location. It gives those commands a dependable text value to store in each record.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which can point more precisely to text in the document. The anchor may be missing, but if present it must be real non-empty text.

**Data flow**: It receives a value and field name. If the value is null, it returns null. If the value is a non-empty string, it returns that string. Anything else prints an error and exits.

**Call relations**: The add-claims and add-issues commands call this while building records. It lets records either omit an anchor cleanly or store a valid one.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets the JSON input text for commands that can accept data directly or from a file. This keeps each command from duplicating the same input-choice logic.

**Data flow**: It receives parsed command-line arguments. If a file path was provided, it reads and returns that file’s text. Otherwise, it returns the direct --data string. The returned text is then parsed as JSON by the caller.

**Call relations**: The commands that add sections, add claims, update claims, or add issues call this before validating their input. It is the bridge from command-line input into structured review records.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates the first empty state file with the review in the outline phase.

**Data flow**: It receives parsed arguments containing the document filename. It rejects a blank filename, builds an empty state with counters, sections, claims, issues, and summary fields, saves it, logs the initialization, and prints a JSON result.

**Call relations**: The command dispatcher calls this when the user runs init. It starts the workflow so later commands have a state file to load.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document’s section outline and moves the review into the claim-finding phase. Sections give later claims and issues a place in the document.

**Data flow**: It loads the current state, warns if the phase is not outline, reads JSON section data, checks required fields and page ranges, and stores each section by name. It then changes the phase to find_claims, saves the state, logs the addition, and prints a JSON result with the added section basics.

**Call relations**: The dispatcher calls this for add-sections. It uses the shared loading, validation, saving, logging, and result-printing helpers, and it prepares the state so add-claims can attach findings to known sections.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in a specific section. These are items that should later be verified, refuted, or marked inconclusive.

**Data flow**: It loads state, warns if the phase is not find_claims, checks that the named section exists, reads JSON claim data, validates required fields and claim type, and assigns each claim a new claim ID. Each new claim starts as unverified with zero attempts and an empty source list. It saves, logs, and prints a JSON summary of the created claims.

**Call relations**: The dispatcher calls this for add-claims. It relies on section data created earlier by add-sections, and it creates the records that update-claims will later mark after fact checking.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the results of checking claims. It marks claims as verified, refuted, or inconclusive and stores any source URLs used as evidence.

**Data flow**: It loads state, remembers the starting phase, warns if the phase is not fact_check, and automatically moves from find_claims to fact_check if needed. It reads JSON updates, validates each claim ID and status, increments the claim’s attempt count, appends source URLs, counts each outcome, saves the state, logs the update, and prints a JSON result.

**Call relations**: The dispatcher calls this for update-claims. It consumes claim records created by add-claims and prepares the review to move toward issue finding by making the fact-check results explicit.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in the document, such as factual concerns, grammar issues, or narrative logic problems. These records describe what is wrong and often suggest replacement text.

**Data flow**: It loads state, remembers the old phase, warns if the phase is not find_issues, and automatically moves from fact_check to find_issues if needed. It checks the section exists, reads issue JSON, validates type, severity, location, anchor, and required text fields, assigns each issue a new issue ID, stores it, saves, logs, and prints a JSON result.

**Call relations**: The dispatcher calls this for add-issues. It uses sections as the document map and often follows claim checking, turning review observations into structured issues that are counted in the final submission.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Finishes the review by storing a final summary and marking the state as complete. It is the closing step of the workflow.

**Data flow**: It loads state, remembers the old phase, warns if the phase is not find_issues, and checks that the summary text is not blank. It sets the phase to complete, saves the summary, writes the updated state, logs final counts, and prints a JSON completion result.

**Call relations**: The dispatcher calls this for submit. It comes after sections, claims, and issues have been recorded, and it closes the review with totals that summarize the work.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved claims in a readable text format, optionally narrowed by status or section. This is for looking up what still needs checking or what was already decided.

**Data flow**: It loads state, gathers all claims, filters them if status or section arguments were given, logs the lookup, and prints each matching claim with its ID, status, type, location, text, description, anchor, and sources. If none match, it prints a simple no-results message.

**Call relations**: The dispatcher calls this for get-claims. Unlike the write commands, it does not save state; it reads the review notebook and gives the user a focused report.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved issues in a readable text format, optionally narrowed by severity or section. This helps reviewers inspect the problems that have been found.

**Data flow**: It loads state, gathers all issues, filters by severity or section if requested, logs the lookup, and prints each matching issue with its ID, severity, type, location, original text, context, description, anchor, and suggested replacement when present. If none match, it prints a no-results message.

**Call relations**: The dispatcher calls this for get-issues. It is a read-only reporting command that turns structured issue records into a human-friendly checklist.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Shows a dashboard-style overview of the current review. It helps someone quickly see the document, phase, sections, claim counts, issue counts, and summary if available.

**Data flow**: It loads state and prints the document name and current phase. It then prints section ranges, claim totals grouped by status, issue totals grouped by severity and type, and the final summary if one has been saved. It does not modify or log anything.

**Call relations**: The dispatcher calls this for status. It reads the same state built by the other commands and presents the whole review at a glance.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each user command to the right function. It is the front door of the script.

**Data flow**: It builds an argument parser, defines subcommands and their options, parses the user’s command-line input, looks up the matching command function, and calls it with the parsed arguments. The result is that the requested workflow action runs.

**Call relations**: When this file is executed as a script, main is called from the bottom of the file. It does not do the review work itself; it routes commands such as init, add-sections, update-claims, and status to the specialized functions above.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `cross-cutting`

This file is a simple model file for document-review results. A review issue is one finding from a document check, such as a spelling problem, a logic concern, or a place where public data needs verification. The `DocumentIssue` type spells out the expected fields for each issue: its ID, type, severity, description, where it appears, the original text, nearby context, any replacement text, and links to a root issue if needed. This is like a standard form that every review finding must fill out, so other code can read the data without guessing what fields exist.

The file also contains `ISSUE_TYPE_LABELS`, which turns internal issue type codes like `spelling_grammar` into friendlier labels like `Spelling/Grammar`. That matters because internal codes are useful for programs, but comments shown to people should be easy to scan.

Finally, `format_comment` builds the text of a review comment. It starts with a header showing the issue category and severity, adds the issue description, and optionally includes a suggested replacement. Without this file, different parts of the review system might describe issues in inconsistent ways or produce harder-to-read comments.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns one document review issue into a human-readable comment. It is useful when the system needs to display or insert a clear review note for a person to read.

**Data flow**: It receives an `issue`, which is a dictionary-like review finding with fields such as issue type, severity, description, and suggested new text. It looks up a friendly label for the issue type, keeps the raw type if no friendly label is known, then builds a multi-line comment. If suggestions are enabled and the issue contains `new_text`, it adds a `Suggested:` line. The result is a single formatted string; it does not change the issue.

**Call relations**: When this formatter runs, it reads optional data from the issue using `DocumentIssue.get`, specifically to check whether suggested replacement text is present. It is the final presentation step for an issue: structured review data comes in, and readable comment text comes out.

*Call graph*: 1 external calls (get).


### Document annotation writers
Annotation scripts read saved review findings and write them back into PDF, PowerPoint, and Excel outputs as visible comments.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `manual PDF annotation run`

This file turns the document review system’s saved findings into annotations inside a PDF. Without it, the review results would stay in a separate JSON state file, and a person opening the PDF would not see the comments in the document itself.

The script expects two command-line arguments: an input PDF and an output PDF. It also expects a review state file, named by STATE_FILENAME, to exist in the current working directory. That state file contains the issues found earlier in the review process.

For each issue, the script reads the page number, chooses a color based on severity, and builds the comment text using format_comment. It then searches the target page for the issue’s original text. If it finds the text, it highlights it and places a comment icon beside the highlight. If it cannot find the text, it still adds the note at a default spot near the top-left of the page. This is a useful fallback: the reviewer still gets the comment, even if text matching fails because the PDF’s internal text differs from what the review system recorded.

It uses PyMuPDF, imported as fitz, which is a library for reading and editing PDF files. At the end, it saves a new annotated PDF and reports how many annotations were added.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved document review issues from the review state file. It gives the annotation step the list of problems that need to be placed into the PDF.

**Data flow**: It starts with the expected state filename from STATE_FILENAME. It checks whether that file exists; if it does not, it prints an error and stops the script. If the file exists, it reads the JSON text, turns it into Python data, takes the values under the "issues" field, and returns them as a list.

**Call relations**: The main annotation flow calls this first, before opening or editing the PDF. It relies on pathlib.Path to find and read the file, json.loads to understand the saved JSON text, and sys.exit to stop early if the required state file is missing.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to find where a piece of issue text appears on a PDF page. The result tells the script where to draw the highlight.

**Data flow**: It receives a PDF page and the original text connected to an issue. First it searches for a longer starting slice of that text. If that fails, it searches again using a shorter slice, which gives it a better chance when the PDF text is incomplete or formatted oddly. It returns the matching page areas, or an empty result if nothing is found.

**Call relations**: The annotate function calls this for each issue after it has selected the correct page. Its answer decides whether annotate can place a highlight beside the exact text or must fall back to adding only a note at a default page location.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function for the script. It opens the input PDF, adds highlights and comment notes for all saved issues it can place, then saves the result as a new PDF.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the review issues, stops early if there are none, opens the PDF, and then visits each issue. For each valid page number, it chooses a severity color, formats the comment text, searches for the original text, adds a highlight if the text is found, adds a sticky note either beside the highlight or at a fallback point, and counts the annotation. Finally it saves the edited PDF to the output path, closes the document, and prints the number of annotations added.

**Call relations**: This function is called when the script is run from the command line with an input and output PDF. It starts by calling load_issues, asks find_quads to locate issue text on each page, uses models.format_comment to turn issue data into readable note text, and uses PyMuPDF functions such as fitz.open, fitz.Point, and fitz.Rect to edit and save the PDF.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `manual document annotation command`

A PowerPoint `.pptx` file is really a zipped folder full of XML files. This script uses that fact to add comments without needing to automate PowerPoint itself. It reads review issues from `document_review_state.json`, groups them by slide number, copies the input presentation to a new output file, unzips that output into a temporary folder, and then adds the XML pieces PowerPoint expects for comments.

There are three main parts. First, it loads the stored review issues and ignores any issue whose location is not a slide number. Second, for each slide with issues, it creates a comment XML file containing the comment text and adds a slide relationship so PowerPoint knows that slide has comments. Third, it writes the shared comment author file, connects that author file to the presentation, and updates `[Content_Types].xml`, which is like a table of contents telling PowerPoint what kinds of files are inside the package.

The script is careful to preserve the presentation by copying the input before changing anything. It also cleans up its temporary folder afterward. Without this file, the review system could find issues in a deck, but it would not be able to hand back a PowerPoint file with those issues embedded as ordinary comments.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: This function reads the saved document review results from `document_review_state.json`. If that file is missing, it stops the script with a clear error because there is nothing to annotate.

**Data flow**: It starts with the expected state filename from configuration. It checks whether that file exists, reads its text as JSON, and looks for the stored `issues` section. It returns the issue records as a list; if the file is absent, it prints an error and exits instead of returning.

**Call relations**: The main annotation flow calls this first. Everything else depends on the issues it returns: if there are no issues, `annotate` can stop early; if there are issues, they are passed onward to be grouped by slide.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: This function sorts review issues into buckets by slide number. PowerPoint comments are attached slide by slide, so the script needs this grouping before it can write the comment files.

**Data flow**: It receives a list of issue records. For each issue, it tries to read the `location` value as a number, treating that number as the slide number. Valid slide-number issues are collected into a dictionary keyed by slide; issues with missing or non-numeric locations are skipped. The result is a slide-to-issues map.

**Call relations**: `annotate` calls this after loading the issues. The grouped result is then used by `write_slide_comments` to create one comment file per affected slide and by `write_author_and_rels` to declare those new comment files inside the PowerPoint package.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: This helper finds the largest existing relationship ID in a PowerPoint `.rels` file. Relationship IDs are labels like `rId3`; finding the largest one lets the script add a new label without reusing an old one.

**Data flow**: It receives the path to a relationship file. If the file does not exist, it returns `0`. If it does exist, it parses the XML, scans each relationship’s `Id`, extracts any number from it, and returns the highest number found.

**Call relations**: `add_relationship` calls this right before inserting a new relationship. In the larger flow, that happens when slide comment files and the shared comment author file need to be connected to the PowerPoint package.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: This function adds a link inside a PowerPoint relationship file. In a `.pptx`, these relationship files are the signposts that tell PowerPoint which internal files belong to a slide or presentation.

**Data flow**: It receives a path to a `.rels` file, a relationship type, and a target file path. If the relationship file already exists, it reads it; otherwise it creates a new XML relationships document. If a relationship of the same type is already present, it leaves the file unchanged. If not, it chooses the next available `rId`, adds the new relationship, and writes the XML back to disk.

**Call relations**: Both comment-writing stages rely on this helper. `write_slide_comments` uses it to connect each slide to its comment file, and `write_author_and_rels` uses it to connect the presentation to the comment author file.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: This function creates the actual comment files for the slides. Each generated XML file contains the text that PowerPoint will show as comments on one slide.

**Data flow**: It receives the temporary unpacked PowerPoint folder and the issues grouped by slide. For each slide, it creates a comments XML document, adds one comment entry per issue, formats the issue into readable comment text, and writes that file under `ppt/comments`. It also updates that slide’s relationship file so PowerPoint can find the comments. It returns the total number of comments it wrote.

**Call relations**: `annotate` calls this after unpacking the copied presentation. While doing its work, it calls `add_relationship` so each slide points to its new comment file. Its returned comment count is then passed to `write_author_and_rels`, which records how many comments the author has made.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: This function writes the shared PowerPoint metadata needed for comments to work. It declares who wrote the comments, connects that author information to the presentation, and updates the package’s content-type list.

**Data flow**: It receives the temporary unpacked PowerPoint folder, the total comment count, and the slide grouping. It writes `ppt/commentAuthors.xml` with a fixed author name and initials, adds a presentation-level relationship to that author file, then opens `[Content_Types].xml` and adds entries for the author file and each slide comment file if they are not already listed. It changes files on disk and returns nothing.

**Call relations**: `annotate` calls this after `write_slide_comments` has created the per-slide comment files. It uses `add_relationship` to make the presentation aware of the author file, completing the set of XML links PowerPoint needs in order to display the comments.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: This is the main work function for the script. It takes an input PowerPoint path and an output PowerPoint path, then produces a copy of the presentation with review issues embedded as comments.

**Data flow**: It receives two file paths from the command line or another caller. It loads issues, stops early if there are none, groups the issues by slide, copies the input file to the output path, unzips the output into a temporary folder, writes comment XML and supporting metadata into that folder, and zips everything back into the output `.pptx`. At the end it prints how many comments were added and deletes the temporary folder.

**Call relations**: This function ties the whole script together. It calls `load_issues` to get the review results, `group_by_slide` to organize them, `write_slide_comments` to create slide-level comment files, and `write_author_and_rels` to add the package metadata. The `__main__` command-line block calls `annotate` after checking that the user supplied an input and output path.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `run as a document-review export/annotation command`

This file exists to turn document-review results into something people can see directly inside a spreadsheet. Without it, the review issues would stay in a separate JSON file, and a user would have to manually match each issue back to the right Excel cell.

The script expects two command-line arguments: an input `.xlsx` file and an output `.xlsx` file. It first reads `document_review_state.json`, which contains the issues found during review. For each issue, it builds a human-readable comment using `format_comment`, then tries to attach that comment to the most relevant cell.

It searches in a sensible order. If the issue names a worksheet and gives a direct cell reference, it tries that first. If that does not work, it looks for a cell on that worksheet containing the issue’s original text. If that still fails, it searches every worksheet. As a last resort, it puts the comment on cell A1 of the first worksheet, adding multiple comments together if needed. This fallback is like leaving a note at the front desk when you cannot find the exact office: the feedback is not lost, even if its exact location is uncertain.

The script copies the original workbook before editing, so the input file is preserved.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved review results from `document_review_state.json`. It gives the rest of the script a simple list of issues to place into the spreadsheet.

**Data flow**: It starts with the expected state filename from configuration, checks whether that file exists, and stops the program with an error message if it does not. If the file is present, it reads the JSON text, turns it into Python data, takes the issue records from the `issues` section, and returns them as a list.

**Call relations**: The main annotation flow calls this first inside `annotate`. If it cannot load the issues, there is nothing meaningful for the workbook step to do, so it exits early rather than creating a misleading output file.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell whose visible value contains a given piece of text. It is used when the script knows what text an issue came from but does not have a reliable cell address.

**Data flow**: It receives a worksheet and a text value to look for. It normalizes the target text by trimming spaces and ignoring letter case, then checks every non-empty cell in the worksheet. If it finds a cell containing that text, it returns the cell; if none match, it returns `None`.

**Call relations**: `annotate` calls this after direct placement fails or when no direct cell reference is available. It helps bridge the gap between review data and the actual spreadsheet by finding the cell that appears to contain the reviewed text.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks up a worksheet by name without caring about uppercase or lowercase differences. It lets issue locations such as `Sheet1` and `sheet1` still match the same worksheet.

**Data flow**: It receives an open workbook and a location name. It compares that name with each worksheet title in a case-insensitive way. If it finds a match, it returns that worksheet; otherwise it returns `None`.

**Call relations**: `annotate` uses this before trying to place a comment in a specific sheet. When it succeeds, later steps can search or write comments in the intended worksheet before falling back to a workbook-wide search.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a comment to an exact cell reference, such as `B12`. It is the fastest and most precise placement method when the review issue already includes a trustworthy anchor.

**Data flow**: It receives a worksheet, a cell reference, and a prepared comment. It tries to look up that cell and set its comment. If the reference works, it changes the cell and returns `True`; if the reference is invalid for the worksheet, it leaves things unchanged and returns `False`.

**Call relations**: `annotate` calls this only after it has found the target worksheet and the issue includes an anchor. Its success decides whether the script can skip slower text-search fallbacks.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function for the script. It copies an Excel file, opens the copy, and adds review issues as comments in the best matching cells.

**Data flow**: It receives an input workbook path and an output workbook path. It loads issues from the state file, stops politely if there are none, copies the input workbook to the output location, and opens that copy. For each issue, it creates a spreadsheet comment, then tries several placement strategies: exact worksheet and cell, matching text on the named worksheet, matching text anywhere in the workbook, and finally cell A1 as a safety net. It saves the workbook and prints how many comments were added.

**Call relations**: This function coordinates the whole file. It calls `load_issues` to get review data, uses `format_comment` to turn each issue into readable comment text, asks `find_worksheet`, `_place_on_cell`, and `find_cell` to locate the best cell, and relies on OpenPyXL to read and write Excel files. The command-line block at the bottom calls `annotate` after checking that the user supplied the input and output paths.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).
