# Document review state and annotation scripts  `stage-11.3.3`

This stage is shared support for the document review workflow. It keeps track of what reviewers or automated checks have found, then writes those findings back into the original documents so people can see them in context.

The models.py file defines the basic shape of a review issue, such as what the problem is and where it was found. It also turns an issue into a readable comment. This gives all the other scripts the same “form” to use, like a standard note card.

manage_state.py is the control panel. It stores the review’s progress, claims, issues, and final summary in a JSON file, which is a simple structured text file. Because the state is saved, different steps can run separately and still share the same memory.

The annotation scripts are the output workers. annotate_pdf.py adds highlights and sticky-note comments to PDFs. annotate_pptx.py inserts comments into PowerPoint files by editing the PPTX package. annotate_xlsx.py copies an Excel workbook and adds comments to the right cells. Together, they turn saved findings into visible document feedback.

## Files in this stage

### Review state foundation
Defines the shared review issue structure and maintains the JSON state that coordinates the document-review workflow.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `cross-cutting`

This file is like a standard form for document-review findings. A review issue can describe a spelling problem, a logic concern, a possible non-public information leak, a public-data check, or a number that may not match elsewhere. The `DocumentIssue` type spells out the fields each issue is expected to have, such as its ID, severity, description, where it appears in the document, the original text, and the suggested replacement text. This matters because review scripts can pass issues around safely when they all agree on the same fields.

The file also contains friendly labels for issue types. For example, the internal value `spelling_grammar` becomes the clearer label `Spelling/Grammar` when shown to a person.

Finally, `format_comment` turns one issue into a short comment block. It starts with the issue type and severity, adds the issue description, and optionally adds a suggested replacement. Without this file, other parts of the review tool would either have to guess the issue structure or duplicate the same comment-formatting rules in several places.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns a structured document review issue into a plain text comment that a reviewer can read. It is useful when the tool needs to present an issue clearly, with its category, severity, explanation, and optional suggested wording.

**Data flow**: It receives a `DocumentIssue`, which is a dictionary-like record containing details about one review problem, plus a true-or-false choice called `include_suggestion`. It looks up a human-friendly label for the issue type, reads the severity and description, and then checks whether there is suggested replacement text. It returns one formatted string; it does not change the issue itself.

**Call relations**: When another part of the document-review system needs display text for an issue, this function is the small final step that turns stored issue data into a readable comment. Inside that step, it uses the issue record's `get` method to safely check for `new_text`, so it can include a suggestion only when one is present and wanted.

*Call graph*: 1 external calls (get).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `active whenever a document-review CLI command is run`

A document review often happens in stages: first outline the document, then identify factual claims, check those claims, record writing or accuracy issues, and finally submit a summary. This file makes that staged process concrete. It stores everything in document_review_state.json, which acts like a shared notebook for the review, and writes a separate JSON-lines log so each action leaves an audit trail.

The script exposes commands such as init, add-sections, add-claims, update-claims, add-issues, submit, get-claims, get-issues, and status. Each command reads the current state, checks that the incoming data has the required shape, updates the state, saves it back to disk, and records what happened. Some commands also move the review to the next phase. For example, adding sections moves the review from outlining to finding claims.

The validation helpers are important because later steps depend on clean data: page numbers must be positive, issue types must be known values, and referenced sections or claims must exist. Without this file, review steps would have no reliable shared memory, no simple way to resume work, and no clear record of what changed.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the machine-readable result for commands that change the review. It wraps a friendly message together with structured progress information so another tool can read the output as JSON.

**Data flow**: It receives a message, the current phase, the document name, and optional extra result details. It builds one dictionary containing those values, turns it into JSON text, and prints it to standard output. It does not change saved state.

**Call relations**: The state-changing commands call this after they have saved their work. It is the final handoff from commands such as cmd_init, cmd_add_sections, cmd_add_claims, cmd_update_claims, cmd_add_issues, and cmd_submit to whatever person or automation is watching the command output.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds an audit entry for a command that was run. This matters because a review may involve many steps, and the log shows what changed, when, and from which phase to which phase.

**Data flow**: It receives the command name, the phase before and after the action, and any extra details. It adds the current UTC time, converts the entry to JSON, and appends one line to the log file. The review state itself is not changed.

**Call relations**: Most commands call this after reading or updating state. It uses the current time and JSON writing as the bridge between a command’s in-memory work and the persistent activity log.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the saved review notebook from disk. Commands use it so they can continue from the current review progress instead of starting fresh each time.

**Data flow**: It looks for the state file named by STATE_FILENAME. If the file is missing, it prints an error and stops the command. If it exists, it reads the JSON text and returns it as a Python dictionary.

**Call relations**: All commands that need existing review information call this first, including adding claims or issues, updating claims, printing status, and filtering claims or issues. It is the doorway from disk into command logic.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review notebook back to disk. It is used whenever a command has made a real change that must survive after the script exits.

**Data flow**: It receives the whole state dictionary, converts it to nicely indented JSON, and writes it to the state file. The output is an updated document_review_state.json file.

**Call relations**: State-changing commands call this before logging and printing their result. It pairs with load_state: commands read with load_state, edit the dictionary, then persist those edits with save_state.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user if a command is being run outside the expected review phase. It does not block the command; it simply makes the unusual order visible.

**Data flow**: It receives the current state and the phase the command normally expects. If they differ, it prints a warning to standard error. Nothing is returned and the state is not changed.

**Call relations**: Workflow commands call this before proceeding, such as adding sections, adding claims, updating claims, adding issues, or submitting. It acts like a traffic sign: it warns about the normal route but lets the driver continue.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an incoming JSON object contains the fields a command needs. This prevents incomplete sections, claims, issues, or claim updates from being saved.

**Data flow**: It receives one item, a list of required field names, and a label for the error message. It finds any missing fields. If any are missing, it prints a clear error and stops the command; otherwise, it lets execution continue.

**Call relations**: The add and update commands call this before trusting incoming JSON data. It protects later code from assuming a field exists when it does not.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a small set of allowed choices. For example, it keeps claim statuses and issue severities consistent across the state file.

**Data flow**: It receives a value, an allowed set, and the field’s name. If the value is not allowed, it prints an error showing the valid options and stops the command. If valid, it returns nothing and the caller continues.

**Call relations**: Commands that add claims, update claims, or add issues call this when reading fields such as claim_type, claim_status, issue_type, and severity. It keeps the saved data predictable for later reporting.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number into an integer and confirms it is at least 1. It is used to make section page ranges usable and sensible.

**Data flow**: It receives a value and the field name. It tries to convert the value to an integer, stops with an error if that fails, stops if the number is less than 1, and otherwise returns the integer.

**Call relations**: cmd_add_sections calls this for start_page and end_page before saving a section. The command then uses the returned numbers to check that the ending page is not before the starting page.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a required text-like value is present and not blank. It is used for locations so every claim or issue can be found in the document.

**Data flow**: It receives a value and a field name. It accepts strings and integers, converts the value to a string, rejects blank text, and returns the cleaned string. On invalid input, it prints an error and stops.

**Call relations**: cmd_add_claims and cmd_add_issues call this while building new saved records. It gives those commands a dependable location string to store.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which can point to a more exact spot in the document. The anchor may be missing, but if it exists it must be real text.

**Data flow**: It receives a value and field name. If the value is null, it returns null. If the value is a non-empty string, it returns it. Otherwise, it prints an error and stops the command.

**Call relations**: cmd_add_claims and cmd_add_issues call this after checking the required location. It lets records include a precise anchor when available without forcing every record to have one.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input either directly from the command line or from a file. This lets users provide small data inline or larger data in a separate file.

**Data flow**: It receives parsed command-line arguments. If a file path was provided, it reads and returns that file’s text. Otherwise, it returns the inline data string from the arguments.

**Call relations**: Commands that accept JSON arrays call this before parsing their input: adding sections, adding claims, updating claims, and adding issues. It hides the difference between --data and --file from the rest of the command.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new review for a document. It creates the first state file with empty sections, claims, issues, counters, and an initial phase of outline.

**Data flow**: It receives command-line arguments containing a filename. It rejects a blank filename, builds a fresh state dictionary, saves it, logs the initialization, and prints a JSON result showing the review has started.

**Call relations**: main dispatches here when the user runs init. This command calls save_state to create the state file, log_action to record the start, and _emit_result to report success.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document’s section outline to the review. This gives later claims and issues named places to attach to.

**Data flow**: It loads the current state, warns if the phase is not outline, reads a JSON array from --data or --file, validates each section’s name and page range, stores the sections, advances the phase to find_claims, saves the state, logs the change, and prints a JSON result.

**Call relations**: main dispatches here for add-sections. This command uses the validation helpers to clean incoming section data, then hands persistence to save_state, audit recording to log_action, and command output to _emit_result.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in a particular section. These claims become the checklist for later fact-checking.

**Data flow**: It loads state, warns if the phase is not find_claims, confirms the named section exists, reads a JSON array, validates each claim, assigns each one a new claim ID, stores it with status unverified and zero attempts, saves the state, logs the added IDs, and prints a JSON result.

**Call relations**: main dispatches here for add-claims. It depends on load_state for existing sections, validation helpers for safe input, save_state for persistence, log_action for the audit trail, and _emit_result for structured feedback.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the results of checking claims. It marks claims as verified, refuted, or inconclusive and can attach source URLs as evidence.

**Data flow**: It loads state, remembers the old phase, warns if the phase is not fact_check, moves from find_claims to fact_check if needed, reads update records, validates each referenced claim and status, increments each claim’s attempt count, appends any source URLs, saves, logs status counts, and prints a JSON result.

**Call relations**: main dispatches here for update-claims. It uses helper validation before changing claim records, then saves and logs the update before returning a structured result through _emit_result.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in a section, such as factual issues, grammar problems, or narrative logic concerns. These issues become the concrete revision list for the reviewed document.

**Data flow**: It loads state, remembers the old phase, warns if the phase is not find_issues, moves from fact_check to find_issues if needed, confirms the section exists, reads issue records, validates type, severity, location, and anchor, assigns new issue IDs, stores each issue, saves, logs the IDs, and prints a JSON result.

**Call relations**: main dispatches here for add-issues. It follows the same pattern as claim creation: load current context, validate incoming JSON, update counters and records, save the state, log the action, and emit a structured result.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Finishes the review with a final summary. It marks the saved state as complete so downstream tools know the review is no longer in progress.

**Data flow**: It loads state, remembers the old phase, warns if the phase is not find_issues, rejects an empty summary, sets the phase to complete, stores the summary, saves the state, counts sections, claims, and issues, logs the completion, and prints a JSON result.

**Call relations**: main dispatches here for submit. It is the closing command in the workflow, using load_state and save_state around the final update, then log_action and _emit_result to record and report completion.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved claims for a person to inspect, optionally narrowed by claim status or section. It is a reading command rather than a state-changing command.

**Data flow**: It loads state, collects all claims, filters them if status or section arguments were supplied, logs the query and result count, then prints either a no-match message or a readable list with IDs, status, type, section, location, text, description, and sources.

**Call relations**: main dispatches here for get-claims. Unlike the commands that modify state, it only calls load_state and log_action; it does not save because it does not change the review.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved issues for a person to inspect, optionally narrowed by severity or section. It helps reviewers focus on the problems most relevant to them.

**Data flow**: It loads state, collects all issues, filters them if severity or section arguments were supplied, logs the query and result count, then prints either a no-match message or a readable list with IDs, severity, type, section, location, text, context, description, and suggested replacement text.

**Call relations**: main dispatches here for get-issues. It reads through load_state, records the lookup through log_action, and prints directly for human viewing without changing or saving the state.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Shows a compact dashboard of the current review. It summarizes the phase, sections, claim status counts, issue counts, and final summary if one exists.

**Data flow**: It loads state, reads the document name and phase, then prints section page ranges, counts claims by status, counts issues by severity and type, and prints the saved summary when present. It does not change the state file.

**Call relations**: main dispatches here for status. This is the simplest reporting command: it only needs load_state, then it formats the saved information for a human reader.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each user command to the right function. It is the front door of the script.

**Data flow**: It creates an argument parser, registers each subcommand and its arguments, parses what the user typed, looks up the matching command function, and calls it with the parsed arguments. Its output depends on the command it dispatches.

**Call relations**: When the script is run directly, main starts the flow. It sets up the command names used by people or automation, then hands control to command functions such as cmd_init, cmd_add_claims, cmd_status, and the others.

*Call graph*: 1 external calls (ArgumentParser).


### Annotated document outputs
Writes saved review findings back into PDFs, PowerPoint presentations, and Excel workbooks as highlights, comments, or notes.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `manual command-line use after document review results have been created`

This file turns a separate review report into something a person can see inside the PDF itself. Without it, the review issues would stay in a JSON state file, separate from the document, so a reader would have to cross-reference page numbers and text by hand.

The script expects two command-line inputs: an input PDF and an output PDF. It also expects a review state file in the current working directory, using the project’s configured state filename. That state file contains the issues found earlier in the review process.

For each issue, the script checks the page number, chooses a color based on severity, and formats the issue into a readable comment. It then searches the page for the original text from the issue. If it finds the text, it highlights that text and places a sticky-note icon beside it. If it cannot find the text, it still adds the note at a safe default spot on the page, so the issue is not lost. This is like leaving comments on a printed draft: ideally the note sits next to the marked sentence, but if the exact sentence cannot be found, the note still goes on the page.

The file uses PyMuPDF, imported as `fitz`, which is a library for reading and editing PDF files.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved review issues from the document review state file. It is used so the annotator knows what comments need to be placed into the PDF.

**Data flow**: It starts with the expected state filename from configuration. It checks whether that file exists in the current folder. If the file is missing, it prints an error message and stops the script. If the file exists, it reads the JSON text, turns it into Python data, pulls out the saved issues, and returns them as a list.

**Call relations**: The main `annotate` function calls `load_issues` at the start of its work. `load_issues` is the bridge from the earlier review step, which saved issues to disk, into this PDF-marking step.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to find where a piece of issue text appears on a PDF page. It returns the page areas that can be highlighted.

**Data flow**: It receives a PDF page and the original text connected to an issue. First it searches using a longer beginning slice of that text. If that finds nothing, it tries again with a shorter beginning slice, which gives it a better chance when the PDF text differs slightly from the saved text. It returns whatever matching page regions the PDF library finds.

**Call relations**: `annotate` calls `find_quads` for each issue after it has chosen the right page. The result decides whether `annotate` can place a highlight on exact text or must fall back to adding only a sticky note.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main worker that opens a PDF, adds review annotations, saves the marked-up copy, and reports how many notes it added. It is what the command-line script runs after checking the user provided input and output paths.

**Data flow**: It receives the path to the source PDF and the path where the annotated PDF should be written. It loads issues from the state file, opens the PDF, and loops through the issues one by one. For each issue, it reads the page number, severity, original text, and comment details. Valid issues are turned into colored highlights and sticky notes; invalid page numbers are skipped. At the end, it saves the changed PDF to the output path, closes the file, and prints a summary.

**Call relations**: `annotate` sits at the center of the script. The command-line block calls it after reading the two file paths from the user. Inside, it calls `load_issues` to get the review data, `find_quads` to locate text on the page, and `models.format_comment` to turn an issue into human-readable note text. It also hands PDF editing tasks to PyMuPDF through `fitz.open`, `fitz.Point`, and `fitz.Rect`.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `document review export / annotation step`

A PowerPoint file is really a zip file full of XML files. This script opens that package and adds the extra XML pieces PowerPoint expects for comments: comment text files, an author file, relationship files that link slides to comments, and content-type entries that tell PowerPoint what those new files are. Without this script, review issues found by the document-review skill would stay in a JSON state file and would not appear inside the presentation for a human to inspect in PowerPoint.

The flow is simple. First it reads issues from document_review_state.json. Each issue has a location, and this script treats that location as a 1-based slide number. It groups the issues by slide, copies the input presentation to the requested output path, unzips the copied PPTX into a temporary folder, writes comment XML for each affected slide, then zips everything back up.

A key detail is that comments in PPTX are not stored directly inside slide XML. They live in separate comment files, and slides point to them through relationship files. Think of it like adding sticky notes to pages in a binder: the note text is stored elsewhere, but each page needs a tab saying which note belongs to it. The script also creates a single comment author named “Flying Object” so the inserted comments have a consistent visible author.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: Reads the saved review results from the state file used by the document-review skill. If the file is missing, it stops the script with a clear error because there is nothing to annotate.

**Data flow**: It starts with the expected filename from STATE_FILENAME. It checks whether that file exists, reads its JSON text, and pulls out the values under the "issues" section. The result is a list of issue records that later steps can place onto slides; if the file is absent, the process exits instead of continuing with bad input.

**Call relations**: This is the first helper used by annotate. annotate depends on it to turn the saved review state into issue data before any PowerPoint files are copied or edited.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: Sorts review issues into buckets by slide number so the script can write one comment file per slide. Issues whose location is not a usable number are skipped because they cannot be safely attached to a slide.

**Data flow**: It receives a list of issue records. For each issue, it reads the issue's "location" field and tries to convert it to an integer slide number. It returns a dictionary where each slide number points to the list of issues that should become comments on that slide.

**Call relations**: annotate calls this after loading the issues. The grouped result is then handed to write_slide_comments to create per-slide comment XML and to write_author_and_rels so the package advertises those comment files correctly.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: Finds the largest existing relationship ID in a PowerPoint relationship file. This lets the script add a new link without accidentally reusing an ID that is already taken.

**Data flow**: It receives the path to a .rels XML file. If the file does not exist, it returns 0. Otherwise it reads the XML, scans each relationship's Id such as "rId3", extracts the number, and returns the highest number it finds.

**Call relations**: add_relationship calls this right before inserting a new relationship. It supplies the next safe number so newly added comment or author links fit into the existing PPTX structure.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: Adds a link inside a PowerPoint relationship file, creating the relationship file if it is missing. These links are how PowerPoint knows, for example, that a slide has a separate comments file.

**Data flow**: It receives a relationship-file path, a relationship type, and a target file path. It loads or creates the XML relationship list, checks whether a relationship of that type already exists, and if not, adds a new Relationship element with the next available rId. It writes the updated XML back to disk.

**Call relations**: write_slide_comments uses this to connect each slide to its comment file. write_author_and_rels uses it to connect the whole presentation to the comment author file. It relies on find_max_rel_id so the new link gets a safe ID.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: Creates the actual comment XML files for slides that have review issues. It also links each slide to its new comment file so PowerPoint can display the comments.

**Data flow**: It receives the temporary unzipped PPTX folder and the issues grouped by slide. For each slide, it creates a comment list XML file under ppt/comments, turns each issue into readable comment text using format_comment, assigns comment numbers and timestamps, and writes the file. It then updates that slide's relationship file to point at the comment XML. It returns the total number of comments written.

**Call relations**: annotate calls this after extracting the copied PPTX. This function hands off relationship updates to add_relationship, and its returned comment count is passed into write_author_and_rels so the author metadata knows the last comment index.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: Adds the package-level metadata PowerPoint needs before it will recognize the inserted comments. This includes the comment author, the presentation-level link to that author file, and the content-type declarations for all new comment-related files.

**Data flow**: It receives the temporary PPTX folder, the total comment count, and the grouped slide issues. It writes ppt/commentAuthors.xml with the fixed author name and initials, links the presentation to that author file, then edits [Content_Types].xml so PowerPoint knows the new author file and each slide comment file are valid PPTX parts. The main output is changed XML files inside the temporary folder.

**Call relations**: annotate calls this after write_slide_comments has created the comment files. It uses add_relationship for the presentation-level author link and completes the structural work needed before annotate zips the folder back into a PPTX.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: Runs the full annotation job from input PPTX to output PPTX. It is the main work function used by the command-line entry point.

**Data flow**: It receives an input file path and an output file path. It loads issues, stops early if there are none, groups them by slide, copies the input presentation to the output path, extracts that output PPTX into a temporary folder, writes comment files and metadata, then rebuilds the output PPTX from the modified folder. It prints how many comments were added and always removes the temporary folder at the end.

**Call relations**: The bottom command-line block calls annotate when the script is run directly with two arguments. annotate coordinates the whole sequence by calling load_issues, group_by_slide, write_slide_comments, and write_author_and_rels, while using file and zip operations to unpack and rebuild the presentation.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review export/annotation`

This file is a small command-line tool for turning review results into something a person can see inside Excel. The review system stores issues in a JSON file named by STATE_FILENAME, but that is not convenient for someone reading a spreadsheet. This script bridges that gap by placing each issue as an Excel cell comment, like a sticky note attached to the workbook.

The main flow starts by loading the saved issues. If there are no issues, it stops cleanly. Otherwise, it copies the input XLSX file to the output path so the original is not changed. It then opens the copied workbook and tries to place one comment per issue.

For each issue, it builds readable comment text using format_comment. It then looks for the best place to attach it. First it tries the worksheet named in the issue location and an exact cell reference, called an anchor. If that fails, it searches that worksheet for a cell containing the original text. If that still fails, it searches every worksheet. As a last resort, it puts the comment on cell A1 of the first sheet, combining multiple fallback comments if needed. This fallback matters because it prevents review information from being silently lost when the script cannot find the exact original cell.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved document review results from the state JSON file. It gives the rest of the script a simple list of issues to turn into spreadsheet comments.

**Data flow**: It starts with the expected state file name from STATE_FILENAME. It checks whether that file exists; if not, it prints an error and stops the program. If the file exists, it reads the JSON text, turns it into Python data, pulls out the saved issues, and returns them as a list.

**Call relations**: The main annotation flow calls this first, because it needs to know what comments to add before opening or changing the workbook. It relies on standard JSON reading and exits early if the required review state is missing.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell that contains a given piece of text. It is used when the script does not have, or cannot use, an exact cell address.

**Data flow**: It receives a worksheet and some target text. It normalizes the target by converting it to lowercase and trimming extra spaces, then scans every non-empty cell in the worksheet. If a cell's text contains the target text, it returns that cell; if no match is found, it returns nothing.

**Call relations**: The annotate function uses this as a backup placement method. After trying a precise cell anchor, it asks find_cell to locate the original reviewed text first in the named worksheet, and then across all worksheets if needed.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function finds a worksheet by name without caring about uppercase or lowercase differences. It helps connect a review issue's saved location to the matching Excel sheet.

**Data flow**: It receives a workbook and a location name. It compares that name with each worksheet title after lowercasing both sides. If it finds a matching title, it returns that worksheet; otherwise it returns nothing.

**Call relations**: The annotate function calls this when an issue includes a location. If a matching sheet is found, the script can try more targeted comment placement before falling back to a whole-workbook search.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a comment to one exact cell reference, such as B12. It exists because exact placement can fail if the saved anchor is not a valid Excel cell address.

**Data flow**: It receives a worksheet, a cell reference, and a prepared Excel comment. It tries to look up that cell and assign the comment to it. If that works, it returns true; if the cell reference is invalid or cannot be used, it returns false instead of crashing the script.

**Call relations**: The annotate function uses this as the first and most accurate placement attempt when both a worksheet and an anchor are available. Its true-or-false result tells annotate whether it needs to try broader search methods.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It creates an annotated copy of an Excel file by placing review issues as cell comments in the best matching cells it can find.

**Data flow**: It receives an input workbook path and an output workbook path. It loads issues, copies the input file to the output location, opens that copied workbook, and loops through each issue. For every issue, it formats the issue into comment text, tries to place the comment using an exact sheet and cell, then by searching for the original text, and finally falls back to cell A1 if no better match exists. At the end it saves the workbook and prints how many comments were added.

**Call relations**: This function ties together all helpers in the file. It calls load_issues to get the review data, find_worksheet to narrow the search to the right sheet, _place_on_cell for exact placement, and find_cell for text-based placement. It also hands issue data to format_comment to make human-readable notes and uses openpyxl to read, edit, and save the Excel workbook.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).
