# Document and office automation scripts  `stage-10.3`

This stage is a toolbox of on-demand scripts used when the system needs to inspect, change, or prepare office documents. It is not the main work loop; it is shared support that runs when a workflow asks for a specific document job.

The document-review scripts keep a review organized. constants.py names the saved state and log files. models.py defines what a review issue looks like and how to turn it into a readable comment. manage_state.py records sections, claims, issues, and the final summary. annotate_pdf.py, annotate_pptx.py, and annotate_xlsx.py then take those saved issues and place them into PDFs, PowerPoint slides, or Excel cells as visible notes.

The Office scripts treat DOCX and PPTX files like zipped folders of XML, which is the text-based format inside them. DOCX and PPTX unpack.py scripts unzip and clean these folders; pack.py scripts rebuild usable Office files. DOCX comment.py adds Word comment data, and accept_changes.py uses background LibreOffice to accept tracked edits. PPTX slides.py manages slides and thumbnails, while repair.py fixes known PowerPoint packaging problems.

For spreadsheets, _soffice.py helps run LibreOffice silently, and recalc.py refreshes formulas. The PDF tools render pages as images, inspect page layout, add annotations, and fill real PDF form fields.

## Files in this stage

### Review state and annotations
Shared review models and state management feed scripts that write review findings back into PDF, PowerPoint, and Excel files.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a small constants file. It does not run any logic by itself. Instead, it acts like a labeled shelf in a shared workspace: every script that needs the document review state or review log can use the same label, instead of typing the filename by hand.

The first value, `STATE_FILENAME`, names the JSON file that stores the current document review state. This is likely used so the review process can pause, resume, or remember what has already happened. The second value, `LOG_FILENAME`, names a JSON Lines file, where each line is a separate JSON record. That format is useful for recording a sequence of review events over time.

Without this file, those filenames might be repeated in several places. That would make mistakes easier: one script could write to one filename while another tries to read from a slightly different one. By centralizing the names here, the project reduces that risk and makes future renaming simpler.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review output formatting`

This file is a small but important “label sheet” for document review results. A review issue is stored as structured data, with fields such as its ID, type, severity, where it appears, the original text, and any suggested replacement. The `DocumentIssue` type describes that expected shape so other code can treat review findings consistently instead of guessing which keys may exist.

It also translates internal issue codes into friendlier labels. For example, `spelling_grammar` becomes `Spelling/Grammar`, which is easier to show to a person reading comments. If an unknown issue type appears, the code keeps the original value rather than failing, which makes it tolerant of newer or unexpected categories.

Finally, the file provides `format_comment`, which builds a short human-readable comment from one issue. It includes the issue label, severity, description, and, when requested and available, a suggested replacement. Without this file, other scripts would likely duplicate the same field names and comment formatting, making review output less consistent and easier to break.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns one structured document issue into the text of a review comment. It is used when the system needs to show a clear message to a human reviewer, optionally including a suggested fix.

**Data flow**: It receives an issue dictionary and a true-or-false choice about whether to include suggestions. It looks up a friendly label for the issue type, reads the severity and description, and checks whether `new_text` exists using the dictionary-style `get` method. It returns one formatted string, with the header, description, and sometimes a `Suggested:` line.

**Call relations**: Within this file, it relies on the shared issue shape described by `DocumentIssue` and the friendly names in `ISSUE_TYPE_LABELS`. Its only direct call shown in the graph is to `DocumentIssue.get`, which is the normal dictionary lookup used to safely check for a suggested replacement before adding it to the comment.

*Call graph*: 1 external calls (get).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `command invocation during the document review workflow`

This script is the notebook for a structured document review. Instead of keeping review progress in memory, it writes everything to document_review_state.json, so each command can pick up where the last one stopped. Without this file, the document-review skill would not have a simple way to remember which phase it is in, what sections were found, which claims need checking, or which issues should be reported.

The review moves through named phases: outline, find_claims, fact_check, find_issues, and complete. Each command reads the current state, checks that the input makes sense, updates the state, saves it back to disk, and writes a separate JSON-lines log entry. A JSON-lines log is a plain text log where each line is one JSON object, which makes later inspection easier.

The script is intentionally strict about input. It checks that required fields exist, page numbers are positive, claim and issue types come from approved lists, and text fields are not empty. This keeps the saved review file predictable, like using a form with required boxes instead of a blank notebook. Some commands print machine-readable JSON results for automation, while lookup commands print human-friendly summaries.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the final result of a command as one valid JSON object. This gives both a readable message and structured progress data that another tool can reliably parse.

**Data flow**: It receives a message, the current phase, the document name, and optional extra progress details. It combines them into one dictionary, turns that dictionary into JSON text, and prints it to standard output.

**Call relations**: The state-changing commands call this at the end of their work. After commands such as init, add-sections, add-claims, update-claims, add-issues, or submit have saved their changes, they hand their success message and key details to this function for output.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Records what command was run and how it changed the review phase. This creates an audit trail, so someone can later see the sequence of review actions.

**Data flow**: It receives the command name, the phase before and after the command, and any extra details such as counts or IDs. It adds a current UTC timestamp, converts the entry to JSON, and appends it as a new line in the log file.

**Call relations**: Most commands call this after reading or changing review state. It sits beside the main state file as a diary: commands update document_review_state.json for the current truth, then call this function to record what happened.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the saved document review state from disk. If the review has not been initialized yet, it stops the command with a clear error.

**Data flow**: It looks for the state file named by STATE_FILENAME. If the file is missing, it prints an error and exits; if it exists, it reads the JSON text and returns it as a Python dictionary.

**Call relations**: All commands that need an existing review call this first, including adding sections, claims, issues, updating claims, viewing claims or issues, checking status, and submitting. It is the doorway from a command into the saved review record.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk. This is what makes each command's changes survive after the script exits.

**Data flow**: It receives the full state dictionary, converts it into nicely indented JSON text, and writes that text to the state file named by STATE_FILENAME.

**Call relations**: Commands call this after they change the review, such as creating the initial state, adding sections, adding claims, updating claim statuses, adding issues, or submitting. It is paired with load_state: one reads the notebook, the other writes it back.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user if they are running a command during an unexpected review phase. It does not stop the command; it just makes the mismatch visible.

**Data flow**: It receives the current state and the phase the command normally expects. If the stored phase is different, it prints a warning to standard error and leaves the state unchanged.

**Call relations**: Workflow commands call this before doing their main work. For example, adding claims expects the find_claims phase, while submitting expects find_issues; this helper gives a safety notice when the review order looks unusual.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an input object contains all fields needed for the command to make sense. This prevents incomplete sections, claims, claim updates, or issues from being saved.

**Data flow**: It receives an input dictionary, a list of required key names, and a label for the error message. If any keys are missing, it prints an error and exits; otherwise it returns normally without changing anything.

**Call relations**: The add and update commands call this while processing each submitted item. It acts like a checklist before the command builds or modifies state entries.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a fixed set of allowed words. This keeps fields such as claim type, claim status, issue type, and severity consistent.

**Data flow**: It receives a value, a set of allowed values, and the field name. If the value is not allowed, it prints a helpful error and exits; otherwise the caller can safely use the value.

**Call relations**: Claim and issue commands call this before saving typed fields. It protects later reporting code from unexpected spellings or categories.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number input into an integer and makes sure it is at least 1. This helps ensure document sections use valid page ranges.

**Data flow**: It receives a string or integer value and a field name. It tries to convert the value to an integer, rejects non-numbers and numbers below 1, and returns the valid integer.

**Call relations**: cmd_add_sections calls this for start_page and end_page. After this helper confirms both numbers are valid, the command can compare them and store the section range.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a value can be used as meaningful text and is not blank. It is used for required location fields where an empty string would make an entry hard to find in the document.

**Data flow**: It receives a value and a field name. It accepts strings and integers, converts the value to a string, rejects blank text, and returns the cleaned string form.

**Call relations**: The claim and issue creation commands call this when validating location data. It gives those commands a reliable location string before they save a new record.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which is a more precise pointer into the document. The anchor may be missing, but if present it must be real non-empty text.

**Data flow**: It receives a value and a field name. If the value is null, it returns null; if it is a non-empty string, it returns that string; otherwise it prints an error and exits.

**Call relations**: The claim and issue creation commands call this for optional anchors. It lets the state store precise references when available without forcing every item to have one.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input either directly from the command line or from a file. This lets users provide small data snippets inline or larger batches in a separate file.

**Data flow**: It receives parsed command-line arguments. If a file path was provided, it reads and returns that file's text; otherwise it returns the inline data string.

**Call relations**: The commands that accept bulk JSON data call this before parsing the data. It hides the difference between --data and --file so the rest of each command can work with one JSON string.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates the initial state file with an outline phase and empty places for sections, claims, issues, and the final summary.

**Data flow**: It receives command-line arguments containing a filename. It rejects an empty filename, builds a fresh state dictionary, saves it, logs the initialization, and prints a JSON success result.

**Call relations**: main dispatches to this when the user runs the init subcommand. It is the first command in the workflow and prepares the state that all later commands load.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document's major sections and moves the review into the claim-finding phase. Sections give later claims and issues a place to belong.

**Data flow**: It loads the current state, warns if the phase is not outline, reads a JSON array of sections, validates each section name and page range, stores the sections by name, changes the phase to find_claims, saves, logs, and prints a JSON result.

**Call relations**: main calls this for the add-sections subcommand. It relies on the data-reading and validation helpers, then hands off to save_state, log_action, and _emit_result once the sections are accepted.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in a specific section. These are items that will later be checked and marked as verified, refuted, or inconclusive.

**Data flow**: It loads state, warns if the phase is not find_claims, confirms the named section exists, reads a JSON array of claims, validates each claim, assigns each one a new claim ID, stores it with unverified status and empty source URLs, saves, logs, and prints a JSON result.

**Call relations**: main calls this for the add-claims subcommand. It uses the validation helpers to keep every claim well-formed, then records the new claims in the shared state file for cmd_update_claims to revisit later.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the results of fact-checking existing claims. It changes claims from unverified to verified, refuted, or inconclusive, and can attach source links.

**Data flow**: It loads state, remembers the previous phase, warns if the phase is not fact_check, moves from find_claims to fact_check when needed, reads a JSON array of updates, validates each claim ID and status, increments the claim's attempt count, appends any source URLs, saves, logs, and prints a JSON result with counts.

**Call relations**: main calls this for the update-claims subcommand. It works on claims created by cmd_add_claims and prepares the review to proceed toward issue finding.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in the document, such as factual issues, grammar problems, non-public information, or narrative problems. These issues are the concrete review findings that may need edits.

**Data flow**: It loads state, remembers the previous phase, warns if the phase is not find_issues, moves from fact_check to find_issues when needed, confirms the section exists, reads a JSON array of issues, validates each required field, assigns new issue IDs, stores the issue details, saves, logs, and prints a JSON result.

**Call relations**: main calls this for the add-issues subcommand. It uses the same input and validation pattern as claim creation, then leaves stored issues for status reports, issue lookup, and the final submission summary.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Marks the review as complete and saves the final summary. This is the closing step of the workflow.

**Data flow**: It loads state, remembers the previous phase, warns if the phase is not find_issues, checks that the summary is not empty, changes the phase to complete, stores the summary, saves, logs counts of sections, claims, and issues, and prints a JSON result.

**Call relations**: main calls this for the submit subcommand. It depends on the accumulated state from earlier commands and finalizes that state so later status checks show the review as complete.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved claims in a readable format, optionally narrowed by status or section. This helps a reviewer inspect what still needs checking or what was already decided.

**Data flow**: It loads state, starts with all claims, applies optional status and section filters, logs the lookup and result count, then prints either a no-results message or a formatted list of matching claims with text, description, location, anchor, and sources.

**Call relations**: main calls this for the get-claims subcommand. Unlike the state-changing commands, it does not save anything; it only reads the state and records that the lookup happened.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved issues in a readable format, optionally narrowed by severity or section. This helps reviewers focus on particular kinds of problems, such as high-severity issues.

**Data flow**: It loads state, starts with all issues, applies optional severity and section filters, logs the lookup and result count, then prints either a no-results message or a formatted list with location, original text, context, description, and suggested replacement text.

**Call relations**: main calls this for the get-issues subcommand. It reads issues created by cmd_add_issues and presents them for review without changing the state file.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Shows a dashboard-style summary of the current review. It gives a quick picture of phase, sections, claim counts, issue counts, and final summary if one exists.

**Data flow**: It loads state, reads the document name and phase, then prints sections, claim status totals, issue severity totals, issue type totals, and the saved summary when present. It does not modify or log anything.

**Call relations**: main calls this for the status subcommand. It is the broad overview command, pulling together data written by init, add-sections, add-claims, update-claims, add-issues, and submit.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each user command to the right function. It is the script's front door.

**Data flow**: It builds an argument parser with subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, get-claims, get-issues, and status. It parses the user's command-line input, looks up the matching command function, and calls it with the parsed arguments.

**Call relations**: This function runs when the script is executed directly. It does not do review work itself; instead it routes each request to the command function that knows how to read, validate, update, or display the review state.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `manual script run after document review results exist`

This file turns a separate list of document review issues into visible PDF annotations. Without it, the review results would stay in a JSON state file, and a person reading the PDF would not see the comments in context.

The script expects two command-line arguments: an input PDF and an output PDF. It also expects a file named by STATE_FILENAME, normally document_review_state.json, to exist in the current working directory. That state file contains the issues found during review.

For each issue, the script reads the page number, chooses a color based on severity, and builds the comment text using format_comment. It then searches the target page for the issue’s original text. If it finds the text, it highlights that text and places a small PDF note next to it. If the exact text cannot be found, it still adds the note at a fixed fallback spot near the top-left of the page. This means the issue is not silently lost just because the PDF text search failed.

The PDF work is done through PyMuPDF, imported as fitz. PyMuPDF is a library for opening, editing, and saving PDF files. The final result is a new PDF file with colored highlights and collapsed comment icons, like margin notes on a printed draft.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved review issues from the document review state file. It stops the script with a clear error if that file is missing, because there would be nothing reliable to annotate.

**Data flow**: It starts with the expected state filename from STATE_FILENAME and looks for that file in the current working directory. If the file is absent, it prints an error message to standard error and exits the process. If the file exists, it reads the JSON text, turns it into Python data, pulls out the values under the "issues" section, and returns them as a list.

**Call relations**: annotate calls this first, before opening or changing the PDF. This function supplies the raw issue records that drive every later highlight and sticky note.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function tries to find where a piece of issue text appears on a PDF page. It returns the page areas that PyMuPDF can highlight.

**Data flow**: It receives a PDF page and the original text connected to an issue. First it searches using the beginning of that text, up to a longer primary length. If that finds nothing, it tries again with a shorter prefix as a fallback. It returns whatever matching areas the PDF search found, or an empty result if no match was found.

**Call relations**: annotate calls this for each issue after it has chosen the right page. Its result decides whether annotate can place a highlight next to the relevant words, or must fall back to adding only a note at a default location.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It reads review issues, opens the input PDF, adds highlights and sticky notes, then saves the annotated copy.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the issue list, opens the PDF, and walks through each issue one by one. For each valid page number, it chooses a severity color, formats the comment, searches for the original text, adds a highlight when possible, creates a colored comment note, and counts the annotation. At the end it saves the changed document to the output path, closes the PDF, and prints how many annotations were added.

**Call relations**: This function ties the whole script together. When the file is run from the command line, the bottom command-line block checks the arguments and calls annotate. Inside the flow, annotate asks load_issues for the review data, asks find_quads where text appears on the page, uses format_comment to make readable note text, and uses PyMuPDF functions to edit and save the PDF.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `run as a document review export/annotation script`

A PowerPoint .pptx file is really a zip file full of XML files. This script opens that package, adds the XML pieces PowerPoint expects for comments, and zips it back up. Its job is to make review findings usable by a person opening the deck, instead of leaving them only in a separate state file.

First it reads document_review_state.json, which contains the issues found during review. It treats each issue location as a slide number, so issue location "3" becomes a comment on slide 3. Issues without a usable slide number are skipped.

Then it copies the input presentation to the output path and works on the copy. It unpacks the copy into a temporary folder, writes one comments XML file per affected slide, and connects each slide to its comment file through a PowerPoint relationship file. Think of these relationship files like a table of contents that tells PowerPoint, “slide 2 has comments over here.”

It also writes the comment author information, adds the necessary package links, updates the content type list so PowerPoint recognizes the new XML parts, and repacks everything into the final .pptx. The temporary unpacked folder is always deleted afterward. If there are no issues, it prints a message and leaves the presentation alone.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: Reads the saved review state file and pulls out the list of document issues to add as comments. If the state file is missing, it stops the script with a clear error because there is nothing to annotate from.

**Data flow**: It starts with the expected state filename from configuration. It checks whether that file exists, reads its JSON text, and looks for the stored issues inside it. The result is a list of issue records that later steps can place onto slides; if the file is absent, the process exits instead of guessing.

**Call relations**: This is the first helper used by annotate. annotate asks it for the review findings before doing any PowerPoint work, because every later step depends on knowing which issues exist.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: Sorts review issues into buckets by slide number. This lets the script write one comment file for each slide that has issues.

**Data flow**: It receives a list of issue records. For each issue, it reads the issue location and tries to turn it into a slide number. Valid slide-number issues are placed into a dictionary keyed by slide number; issues with missing or non-numeric locations are ignored. The output is a slide-to-issues map.

**Call relations**: annotate calls this right after loading issues. The grouped result is then handed to write_slide_comments, which creates the per-slide comment files, and to write_author_and_rels, which registers those files in the PowerPoint package.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: Looks inside a PowerPoint relationship file and finds the largest existing relationship number. This prevents the script from accidentally reusing an ID that PowerPoint already depends on.

**Data flow**: It receives the path to a .rels file, which is an XML file listing links between package parts. If the file does not exist, it returns 0. Otherwise it parses the XML, scans each relationship Id such as rId5, extracts the number, and returns the highest number it found.

**Call relations**: add_relationship uses this when it needs to create a new link. By asking for the current highest ID first, add_relationship can choose the next safe rId value.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: Adds a link to a PowerPoint relationship file, creating that file if needed. These links are how PowerPoint knows that a slide has a comment file or that the presentation has a comment-author file.

**Data flow**: It receives a relationship file path, a relationship type, and a target file path. It opens the existing XML relationship file or creates a new empty one. If a relationship of the same type already exists, it leaves the file unchanged. Otherwise it finds the next available rId number, adds a new XML relationship entry, and writes the file back to disk.

**Call relations**: write_slide_comments calls this to connect each slide to its comments. write_author_and_rels calls it to connect the whole presentation to the comment author list. It relies on find_max_rel_id to avoid ID collisions.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: Creates the actual comment XML files for slides that have review issues. Each issue becomes a PowerPoint comment with formatted text.

**Data flow**: It receives the temporary unpacked PowerPoint folder and the slide-to-issues map. For each slide, it creates a comments XML file under ppt/comments. For each issue on that slide, it assigns a running comment number, stamps the current UTC time, places the comment at position 0,0, and fills the text using format_comment. It also adds the slide relationship that points to the new comment file. It returns the total number of comments written.

**Call relations**: annotate calls this after unpacking the output presentation. During its work it calls add_relationship so PowerPoint can find each slide’s comment file, and it calls format_comment from the models module to turn an issue record into readable comment text. Its comment count is passed on to write_author_and_rels.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: Adds the package-level information PowerPoint needs to recognize the comments and their author. Without this, the comment files might exist but PowerPoint would not properly treat them as comments.

**Data flow**: It receives the temporary PowerPoint folder, the total number of comments, and the grouped issues. It writes ppt/commentAuthors.xml with a single author named “Flying Object” and records the last comment index. It adds a presentation relationship pointing to that author file. Then it opens [Content_Types].xml and adds entries that tell PowerPoint the author file and each slide comment file are comment-related XML parts. The changed XML files are written back to disk.

**Call relations**: annotate calls this after write_slide_comments has created the slide comment files. It uses add_relationship to connect the presentation to the author file, and it uses the grouped slide list to register every comment file in the package content types.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: Runs the full annotation process from input PowerPoint file to output PowerPoint file. This is the main work function used by the command-line entry point.

**Data flow**: It receives an input .pptx path and an output .pptx path. It loads issues, stops early if there are none, groups issues by slide, and copies the input file to the output location. It unzips the output file into a temporary folder, writes slide comments and supporting metadata, then rebuilds the output .pptx from the modified folder. Finally, it deletes the temporary folder even if something goes wrong during processing.

**Call relations**: The command-line block at the bottom calls annotate after checking that the user provided input and output paths. annotate coordinates the whole flow: it gets issues from load_issues, organizes them with group_by_slide, delegates XML comment creation to write_slide_comments, delegates package registration to write_author_and_rels, and uses file and zip operations to unpack and rebuild the presentation.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `on-demand document review annotation`

This file is a small command-line tool for marking up an Excel workbook after a document review has found issues. It reads a saved review state file, `document_review_state.json`, looks for the spreadsheet cells related to each issue, and writes Excel comments onto those cells. Without this script, the review findings would stay outside the spreadsheet, so a person opening the workbook would not see the feedback in context.

The script works like a careful assistant with a stack of sticky notes. First it loads the list of issues. Then it copies the input spreadsheet to the requested output path, so the original file is not changed. For each issue, it formats the issue into readable comment text, chooses the best worksheet if the issue names one, and tries several ways to attach the comment: first by an exact cell address if one is available, then by searching for the original text in the named sheet, then by searching all sheets. If none of those work, it puts the comment on cell A1 of the first worksheet as a fallback. If A1 already has a fallback comment, it appends the new one below a separator instead of overwriting it.

The important behavior is that annotation is best-effort. The script tries to place each comment where it belongs, but it still records the issue somewhere even when the original text cannot be found.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved review results from `document_review_state.json`. It gives the rest of the script a simple list of issues to place into the spreadsheet.

**Data flow**: It starts with the expected state filename from configuration. If the file is missing, it prints an error message and stops the program. If the file exists, it reads the JSON text, pulls out the stored issues, and returns them as a list.

**Call relations**: The main annotation flow calls this first, before opening the spreadsheet. The issue list it returns becomes the source of every Excel comment that `annotate` later creates.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell whose text contains a given piece of original review text. It is used when the script does not have, or cannot use, an exact cell address.

**Data flow**: It receives a worksheet and some target text. It trims and lowercases the target so the search is forgiving, then checks each non-empty cell in the worksheet. If a cell contains that text, it returns the cell; if no match is found, it returns nothing.

**Call relations**: The `annotate` function calls this after trying a direct cell placement, first within a named worksheet and then across all worksheets. When `find_cell` returns a match, `annotate` attaches the comment there.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks up a worksheet by name without caring about letter case. It helps place a comment on the sheet mentioned by a review issue.

**Data flow**: It receives an open workbook and a location name. It compares that name with each worksheet title in lowercase form. If it finds a matching sheet, it returns that worksheet; otherwise it returns nothing.

**Call relations**: The `annotate` function calls this when an issue includes a location. If a worksheet is found, `annotate` tries that sheet first before falling back to a broader workbook-wide search.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to put a prepared comment directly onto a specific cell address, such as `B12`. It is the most precise placement method the script has.

**Data flow**: It receives a worksheet, a cell reference, and an already-created comment. It tries to retrieve that cell and assign the comment to it. If the cell reference is valid, it returns `True`; if the reference cannot be used, it returns `False` and leaves placement to another method.

**Call relations**: The `annotate` function uses this when an issue includes both a worksheet location and an anchor cell. If this direct placement fails, `annotate` continues with text-based search instead of giving up.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It creates an annotated copy of an Excel file by adding review issues as cell comments.

**Data flow**: It receives an input spreadsheet path and an output spreadsheet path. It loads the review issues, copies the input file to the output file, opens the copied workbook, and loops through each issue. For each issue, it formats the issue as comment text, creates an Excel comment, tries to place it by exact cell, then by matching text in the expected worksheet, then by matching text anywhere in the workbook, and finally on cell A1 if no better spot is found. At the end, it saves the output workbook and prints how many comments were added.

**Call relations**: This function brings all the helpers together. It calls `load_issues` to get the review findings, `find_worksheet` to narrow the search, `_place_on_cell` for exact placement, and `find_cell` for text-based placement. It also uses external Excel tools from `openpyxl` to open the workbook and create comments, and it is invoked by the script’s command-line block when the user runs the file.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).


### Word document packaging
DOCX scripts unpack Word files for XML editing, insert comment support, repack the package, and optionally accept tracked changes.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual DOCX unpacking and XML cleanup`

A .docx file is really a ZIP archive full of XML files. This file is a small command-line tool that opens that archive, extracts it into a directory, and then makes the important XML easier to work with. Without this, someone inspecting a Word document would have to manually unzip it and read dense, hard-to-diff XML.

The main flow is: check that the input exists and looks like a .docx file, unzip it, pretty-print XML indentation, simplify Word's internal markup, and replace curly quote characters with explicit XML character references. The simplification focuses on word/document.xml, the main body of the Word document.

Two cleanups matter most. First, it can coalesce tracked changes, meaning it joins neighboring insertions or deletions from the same author when they are really one continuous change. Second, it can merge adjacent Word “runs.” A run is a stretch of text with the same formatting, like a few words all in the same style. Word often splits text into many tiny runs for internal reasons; this script joins compatible neighbors so the XML reads more like the document a person sees.

The script is cautious: several formatting helpers silently skip files they cannot parse, and invalid ZIP files return a clear error message instead of crashing.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main work function. It takes a .docx file and an output folder, extracts the document, cleans the XML, optionally simplifies tracked changes and text runs, and returns both a structured result and a human-readable message.

**Data flow**: It receives the input file path, output directory path, and two on/off choices for cleanup. It checks the file, creates the output folder, unzips the .docx contents, formats all XML-like files, then focuses on word/document.xml for deeper cleanup. It finishes by replacing curly quotes in the XML files and returns an UnpackResult with counts, or returns no result plus an error message if the input is missing, not a .docx file, or not a valid ZIP archive.

**Call relations**: This function is the conductor for the whole script. It calls _indent_xml for basic readability, _coalesce_tracked_changes when tracked-change cleanup is enabled, _merge_adjacent_runs when run merging is enabled, and _replace_curly_quotes at the end so the extracted files use explicit quote entities. The command-line block at the bottom calls this function and prints its message.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This helper makes an XML file easier to read by adding neat indentation and line breaks. It is like taking a long paragraph of code and spacing it out so people can follow the structure.

**Data flow**: It receives the path to one XML file. It tries to parse the file as XML, asks lxml to indent the tree with two spaces, then writes the formatted XML back to the same file with a UTF-8 XML declaration. If parsing or writing fails, it leaves the file alone.

**Call relations**: unpack_docx calls this for every extracted .xml and .rels file right after unzipping. It prepares the files for later cleanup and for human inspection, but it does not decide which files exist or what the larger process should do.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This helper replaces typographic curly quote characters with XML character references. That keeps those characters visible and explicit in the XML text rather than relying on the raw Unicode characters.

**Data flow**: It receives the path to one XML-like file. It reads the file as UTF-8 text, checks whether any curly single or double quotes appear, and if so rewrites them as numeric XML entities such as &#x201C;. If reading or writing fails, it silently skips the file.

**Call relations**: unpack_docx calls this near the end for every extracted XML and relationship file. It runs after indentation and document cleanup so the final files have consistent quote representation.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This function simplifies the main Word document XML by joining neighboring text runs that have the same formatting. This makes the XML less fragmented and easier to edit or compare.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns 0. Otherwise it parses the XML, removes proofing-error markers, removes run attributes related to Word revision IDs, finds every parent element that contains runs, and asks _merge_runs_in to merge compatible runs inside each parent. If anything was merged, it writes the updated document XML back and returns the number of absorbed runs.

**Call relations**: unpack_docx calls this only for the main document XML and only when run merging is enabled. It delegates the detailed merging decision to _merge_runs_in, which in turn uses _canonical_rpr and _join_adjacent_text.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This helper creates a stable fingerprint of a run's formatting. Two runs with the same fingerprint can be treated as having the same visual style.

**Data flow**: It receives one Word run XML element. It looks for that run's formatting child, called w:rPr in WordprocessingML, which is the XML language Word uses for .docx documents. If there is no formatting child, it returns None. If there is one, it converts that formatting XML into a canonical string, meaning a normalized representation suitable for comparison.

**Call relations**: _merge_runs_in calls this while scanning runs in a container. Its output is the simple comparison key that lets _merge_runs_in decide whether neighboring runs can safely be joined.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This function does the actual joining of adjacent Word runs inside one parent element. It only merges runs that sit next to each other and share the same formatting.

**Data flow**: It receives an XML container element, such as a paragraph or another element that has run children. It walks through the container's direct children, groups consecutive run elements with matching formatting fingerprints, then keeps the first run in each group as the anchor. It moves non-formatting child nodes from later runs into the anchor, removes the now-empty donor runs, joins neighboring text nodes inside the anchor, and returns how many runs were absorbed.

**Call relations**: _merge_adjacent_runs gathers the containers and calls this for each one. This function relies on _canonical_rpr to compare formatting and calls _join_adjacent_text after moving text pieces together so the merged run is clean.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This helper tidies up a run after other runs have been merged into it. If two text nodes end up side by side, it turns them into one text node.

**Data flow**: It receives one Word run element. It scans its child nodes from left to right, and whenever two neighboring children are both text elements, it combines their text into the first one and removes the second. If the combined text starts or ends with a space, it marks the XML text as space-preserving so Word will not trim the space by accident.

**Call relations**: _merge_runs_in calls this after it has moved donor run contents into an anchor run. It is the cleanup step that makes the merged run look like one continuous piece of text instead of several stitched-together fragments.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word tracked-change markup by joining neighboring insertions or deletions from the same author. The result is fewer, larger change blocks that better match how a person would describe the edit.

**Data flow**: It receives the path to word/document.xml. If the file is missing, it returns 0. Otherwise it reads and parses the XML while preserving existing blank text, finds paragraph and table-cell containers, and asks _coalesce_in to merge insertion and deletion blocks within each container. If any change blocks were absorbed, it writes the updated XML back and returns the total reduction count.

**Call relations**: unpack_docx calls this before run merging when tracked-change cleanup is enabled. It does the broad search through the document, while _coalesce_in and _merge_change_run handle the smaller decisions about which specific change elements can be joined.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This helper looks inside one container for tracked insertions or deletions of one chosen type and groups them by author. It prepares likely merge candidates for the more careful merge step.

**Data flow**: It receives an XML container and a change type string, either insertion or deletion. It builds the matching Word XML tag, collects direct child elements of that type, and if there are at least two, groups them by their author attribute. Each group is passed to _merge_change_run, and the function returns the total number of elements merged away.

**Call relations**: _coalesce_tracked_changes calls this for both insertions and deletions inside each paragraph or table cell. It narrows the work from a whole document down to same-type, same-author change runs before handing off to _merge_change_run.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This function merges a sequence of tracked-change elements when they are truly adjacent. It preserves their child content by moving it into the first change element.

**Data flow**: It receives a list of insertion or deletion XML elements, usually from the same author. It uses the first element as the current anchor, then checks each later element. If _changes_adjacent says the later element sits next to the anchor with only whitespace or comments between them, it moves the later element's children into the anchor, preserves any trailing text in the parent, removes the later element, and counts it as absorbed. If they are not adjacent, the later element becomes the new anchor. It returns the number of merged elements.

**Call relations**: _coalesce_in calls this after grouping change elements. This function depends on _changes_adjacent for the safety check, because it must not merge edits that have real document content between them.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This helper answers one careful question: are two tracked-change elements next to each other except for harmless whitespace or comments? It prevents the script from accidentally merging changes across real content.

**Data flow**: It receives two XML elements. It finds their shared parent and positions among that parent's children. If either element is missing from the parent, it returns false. It then looks at the text and nodes between them; if it finds any real element other than comments, or non-whitespace text, it returns false. If only whitespace and comments separate them, it returns true.

**Call relations**: _merge_change_run calls this before combining two tracked-change elements. It acts as the gatekeeper that makes tracked-change coalescing conservative and safe.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`io_transport` · `manual document-editing script run`

A DOCX file is really a zipped folder full of XML files. Adding a visible comment is not just one edit: Word expects several separate files to agree with each other, a bit like several office forms that all need the same case number. This file takes care of that boilerplate.

The main job is done by insert_comment. It checks that the unpacked DOCX has a word folder, creates random Word-style identifiers, records the current UTC time, and makes sure the comment support files exist. If this is the first comment, it copies template files and updates the DOCX relationship and content-type registries so Word knows those files belong to the document.

It then appends matching XML entries to four files: the comment text itself, extra threading/status information, a durable ID record, and newer Word metadata. If the new comment is a reply, it looks up the parent comment’s paragraph ID so the reply can be linked correctly.

One important limitation is deliberate: this script does not insert the actual range markers into document.xml. After it adds the background records, it prints the marker XML the caller must place around the text being annotated.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character uppercase hexadecimal ID used by Word comment metadata. This gives each new comment paragraph or durable record a Word-style identifier.

**Data flow**: It takes no input. It asks Python’s random number generator for a number in a safe range, formats that number as eight hexadecimal characters, and returns the resulting string.

**Call relations**: insert_comment calls this when starting a new comment. The returned IDs are then passed into the XML-building functions so the separate comment files can all refer to the same new comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces smart curly quote characters with XML character references. This helps preserve those characters safely when the XML is written back out.

**Data flow**: It receives a text string. It scans for left and right curly single and double quotes, replaces each with its XML-safe form, and returns the changed string.

**Call relations**: _serialize_xml uses this just before XML bytes are written to disk. It acts as a final cleanup step after lxml has turned the XML tree into text.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an editable XML tree. Other functions use this whenever they need to inspect or add to an existing DOCX XML part.

**Data flow**: It receives a file path. It reads the file’s raw bytes, parses them with lxml, and returns the root XML element that represents the document in memory.

**Call relations**: _append_element_to_file uses it before adding a child element, _ensure_registrations uses it to edit DOCX registry files, and _resolve_parent_paragraph uses it to search existing comments.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an edited XML tree back into bytes suitable for saving to disk. It also applies the project’s special curly-quote escaping step.

**Data flow**: It receives the root XML element of a document. It serializes that tree with an XML declaration and UTF-8 encoding, converts curly quotes to XML references, and returns the final bytes.

**Call relations**: _append_element_to_file calls this after it has added a new XML element. The bytes it returns are what get written back over the original file.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main Word XML element that contains the comment’s visible text, author, initials, date, and internal paragraph ID. This is the record that Word reads as the actual comment body.

**Data flow**: It receives the comment ID, author details, timestamp, paragraph ID, and comment text. It creates a nested XML structure with a comment reference run and a text run, then returns that XML element without writing it to disk.

**Call relations**: insert_comment calls this after preparing IDs and time data. The returned element is handed to _append_element_to_file so it can be added to comments.xml.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra Word metadata entry for a comment, including whether it is linked to a parent comment. This is what helps Word understand threaded replies and comment status.

**Data flow**: It receives the new comment’s paragraph ID and, optionally, the parent comment’s paragraph ID. It creates a commentEx XML element with the new ID, marks it as not done, adds the parent link if present, and returns the element.

**Call relations**: insert_comment calls this after it has optionally resolved a parent comment. The element is then appended to commentsExtended.xml alongside the main comment record.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML entry that connects a comment paragraph ID to a durable ID. A durable ID is a stable-looking identifier that newer Word versions use to track comments.

**Data flow**: It receives the paragraph ID and durable ID strings. It places both values into a commentsIds XML element and returns that element.

**Call relations**: insert_comment creates both IDs first, then calls this function. The result is appended to commentsIds.xml so Word’s newer comment tracking data stays in sync.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the newer Word metadata entry that records a comment’s durable ID and UTC date. This supports the more recent comment format used by modern Word versions.

**Data flow**: It receives a durable ID and timestamp. It creates a commentExtensible XML element containing those values and returns it.

**Call relations**: insert_comment calls this after the durable ID and timestamp have been created. The returned element is appended to commentsExtensible.xml as the final metadata piece for the new comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the internal paragraph ID for an existing parent comment. This is needed when adding a reply, because Word links replies to the parent’s paragraph ID rather than just its visible comment number.

**Data flow**: It receives the path to comments.xml and the parent comment ID. It parses the file, searches comment elements for the matching ID, then looks inside that comment for a paragraph ID; it returns the paragraph ID if found, or nothing if not found.

**Call relations**: insert_comment calls this only when the user is adding a threaded reply. If it cannot find the parent paragraph ID, insert_comment stops and returns an error instead of writing incomplete reply metadata.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one prepared XML element to the end of an existing XML file and saves the file. It is the common write step for all four comment-related files.

**Data flow**: It receives a file path and a child XML element. It parses the existing file, appends the child to the root element, serializes the updated tree, and writes the new bytes back to the same path.

**Call relations**: insert_comment uses this repeatedly after building each comment metadata element. It relies on _parse_xml_file to read and _serialize_xml to prepare the updated XML for writing.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure the DOCX package knows that the comment files exist. Without these relationship and content-type entries, Word may ignore the new XML files even if they are present.

**Data flow**: It receives the unpacked DOCX base folder. It opens document.xml.rels to add relationships pointing to the comment files if needed, and opens [Content_Types].xml to add content-type overrides if needed; it writes those registry files back when it changes them.

**Call relations**: insert_comment calls this only when it is setting up comments for the first time. This prepares the package-level bookkeeping before the individual comment records are appended.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds a complete comment record, or reply record, to an unpacked DOCX directory. This is the main function other code or the command-line script would use.

**Data flow**: It receives the path to an unpacked DOCX folder and a CommentSpec containing the comment ID, text, author, initials, and optional parent ID. It checks the folder, creates IDs and a timestamp, copies template files if needed, updates package registrations, builds the needed XML elements, appends them to the right files, and returns the new paragraph ID plus a success or error message.

**Call relations**: This function orchestrates the whole operation. It calls the small ID, XML-building, parent-lookup, registration, and append helpers in order; when the file is run from the command line, the argument-parsing block builds a CommentSpec, calls insert_comment, prints its message, and then prints the document.xml marker instructions.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`entrypoint` · `document packaging`

A DOCX file is really a ZIP archive full of XML files and related parts. This script is the “zip it back up” step after someone or something has edited those unpacked parts. Without it, the folder of document pieces would not become a normal Word document that other tools can open.

The main flow starts by checking two simple rules: the input must be a directory, and the output name must end in .docx. It then copies the whole input folder into a temporary staging area. This is like making a safe workbench copy before packing a suitcase, so the original folder is not changed.

Before creating the final archive, it walks through XML files and relationship files, removing whitespace that is only formatting noise. It is careful not to remove spaces inside Word text elements, because those spaces may be part of the actual document content. Finally, it writes every staged file into a compressed ZIP archive with the requested .docx name.

The script can also be run directly from the command line. It prints a success or error message, and exits with a failure code if the basic checks fail.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a DOCX file from a directory that contains the unpacked document contents. It is used when edited document parts need to be turned back into a single Word-compatible file.

**Data flow**: It receives an input folder path and an output file path. First it checks that the input is really a directory and that the output ends in .docx. If either check fails, it returns no output path and an error message. Otherwise, it copies the folder into a temporary staging area, asks _strip_xml_whitespace to clean each XML and relationship file, creates the output folder if needed, and writes the staged files into a compressed .docx archive. It returns the finished output path and a success message.

**Call relations**: This is the main worker for the script and is also what the command-line section calls after reading arguments. During packing, it calls _strip_xml_whitespace for each XML-like file so the archive is cleaned before being zipped. It also relies on standard file tools for making a temporary directory, copying the folder, and writing the ZIP archive.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper removes XML whitespace that is only there for formatting, while preserving whitespace that belongs to actual Word text. It helps keep the repacked document clean without accidentally changing visible document content.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through each element, and removes blank-only text or tail spacing where it is safe to do so. It skips Word text-related elements such as text runs and instruction text, because spaces there may matter to the document. It also removes unusual callable-tag children, then writes the cleaned XML back to the same file with an XML declaration. If parsing or writing fails, it prints an error message to standard error and raises the failure again.

**Call relations**: pack_docx calls this helper while preparing the temporary staging copy of the document. The helper does the careful XML cleanup, then hands control back to pack_docx so the cleaned files can be included in the final .docx archive.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`orchestration` · `on-demand document cleanup`

This file solves a practical document-cleanup problem: a .docx file may contain tracked edits, and downstream tools or users often need the final accepted version instead. Rather than trying to edit the complex Word file format directly, the script asks LibreOffice to do the job, like using Word’s own “Accept All Changes” button but automatically.

The script first checks that the input exists and is a .docx file. It then copies the input to the requested output path, so the original document is not changed. Next, it makes sure LibreOffice has a small Basic macro installed in a temporary LibreOffice user profile. A macro is a short script that LibreOffice can run inside a document. This one tells LibreOffice to accept all tracked changes, save the document, and close it.

Finally, the script launches LibreOffice with that macro and the copied document. One important quirk is handled deliberately: LibreOffice may finish saving the file but then hang instead of exiting cleanly. If that happens after the timeout, this script treats it as success because the macro usually already saved the cleaned document. The file can also be run directly from the command line with input and output paths.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This builds the environment settings used when starting LibreOffice. It forces LibreOffice to use a non-graphical display backend, which helps it run safely in the background on servers or automation systems.

**Data flow**: It starts with the current process environment, copies it, then adds one LibreOffice-specific setting: SAL_USE_VCLPLUGIN is set to svp. The result is a dictionary of environment variables that can be passed to LibreOffice when it is launched.

**Call relations**: Both _ensure_macro and accept_tracked_changes call this before starting LibreOffice. It gives each LibreOffice subprocess the same headless-friendly setup, so macro installation and macro execution behave consistently.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This creates the command-line option that tells LibreOffice to use a specific temporary user profile. That profile is where the script installs the macro, keeping this automation separate from the user’s normal LibreOffice settings.

**Data flow**: It reads the fixed PROFILE_DIR path and formats it into the special LibreOffice argument -env:UserInstallation=file://.... The output is a string ready to be included in a LibreOffice command.

**Call relations**: _ensure_macro uses this when initializing the temporary LibreOffice profile, and accept_tracked_changes uses it again when running the macro. This keeps both steps pointed at the same profile, so LibreOffice can find the macro later.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure the LibreOffice macro needed to accept tracked changes is installed and ready to run. Without this step, LibreOffice would open the document but would not know what automatic action to perform.

**Data flow**: It checks whether the expected macro file already exists and contains the AcceptAllTrackedChanges macro. If not, it starts LibreOffice briefly to create the profile structure, creates the macro folder if needed, and writes the macro XML file there. It returns True when the macro is in place.

**Call relations**: accept_tracked_changes calls this after copying the document and before launching LibreOffice for the real work. Inside, it calls _profile_arg to point LibreOffice at the temporary profile, _soffice_env to make LibreOffice run headlessly, and subprocess.run to initialize LibreOffice when the profile folders are missing.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main work function: it turns an input .docx with tracked changes into an output .docx where those changes have been accepted. It is useful for automated document processing where reviewers’ edits need to become the final text.

**Data flow**: It receives an input file path and an output file path. It checks that the input exists and has a .docx extension, creates the output folder if needed, and copies the original file to the output location. It then ensures the LibreOffice macro is installed, starts LibreOffice with that macro and the copied file, and returns a pair containing None plus a human-readable success or error message. It changes the filesystem by writing the copied and cleaned output document, and may create or update the temporary LibreOffice profile.

**Call relations**: This function is the central path used by the command-line entry at the bottom of the file. It calls _ensure_macro to prepare the automation, then uses _profile_arg and _soffice_env while running LibreOffice through subprocess.run. If LibreOffice times out, it still reports success because the known behavior is that LibreOffice often saves the document before failing to exit.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### PowerPoint package editing
PPTX scripts unpack presentation packages, manipulate slides, repack them, and repair known generated-file issues.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual document preparation before editing PPTX XML`

A .pptx PowerPoint file is really a ZIP archive full of XML files and related resources. This script opens that archive, extracts its contents into a normal folder, and then cleans up the XML so a person or another tool can inspect and edit it more easily. Without this step, the XML inside a presentation can be hard to read because it may be packed onto long lines, and some quote characters may appear in a form that is awkward for XML-based editing workflows.

The main flow is simple. First, the script checks that the input file exists and has the .pptx extension. Then it creates the destination folder if needed. Next, it opens the .pptx as a ZIP file and extracts everything. After extraction, it finds files ending in .xml and .rels. A .rels file is also XML; it describes relationships between PowerPoint parts, such as which slide refers to which image.

Each XML-like file is then passed through two cleanup steps. One step pretty-prints the XML, meaning it adds consistent indentation like tidying a messy outline. The other step replaces “smart quotes” such as curly opening and closing quotes with XML entity text like &#x201C;. The helper steps quietly skip files they cannot parse or rewrite, so one bad file does not necessarily stop the whole unpacking process.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main work function. It checks the requested PowerPoint file, unzips it into a folder, cleans up the XML files it finds, and returns both a result object and a human-readable message.

**Data flow**: It receives a path to a .pptx file and a destination folder path. It turns those strings into filesystem paths, checks that the source exists and looks like a PowerPoint file, creates the output folder, extracts the ZIP contents, finds XML and relationship files, and sends each one through formatting and quote-cleanup helpers. It returns an ExtractionResult containing the number of XML-like files processed, or None plus an error message if the file is missing, has the wrong extension, or is not a valid ZIP archive.

**Call relations**: When the script is run from the command line, the argument parser gathers the input and output paths and calls this function. During its work, it calls _prettify_xml first to make each XML file readable, then _escape_smart_quotes to normalize curly quotes. It also creates the ExtractionResult that summarizes the successful extraction.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper rewrites one XML file with clean indentation and a standard XML declaration. It exists so extracted PowerPoint XML is much easier for people to read and compare.

**Data flow**: It receives the path to one XML-like file. It tries to parse the file as XML, asks the XML library to indent the document with two spaces, converts the document back into UTF-8 bytes, and writes those bytes back to the same file. If parsing or writing fails, it silently leaves the file as it was.

**Call relations**: extract_pptx calls this once for every .xml and .rels file it finds after unpacking the presentation. This formatting pass happens before smart quote replacement, so the file is first turned into neat XML and then scanned as text for quote characters.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly single and double quote characters with XML entity text. That makes those characters explicit in the XML instead of leaving them as literal typographic symbols.

**Data flow**: It receives the path to one XML-like file and reads it as UTF-8 text. If there are no curly quote characters, it does nothing. If there are, it replaces each one with its matching XML numeric entity and writes the changed text back to the same file. If reading or writing fails, it silently skips the file.

**Call relations**: extract_pptx calls this after _prettify_xml for every extracted .xml and .rels file. In the larger flow, it is the final cleanup pass before extract_pptx reports how many XML-like files were unpacked and processed.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `command invocation`

A PowerPoint file is really a zip package full of XML files, images, charts, and relationship files that say how those pieces connect. This script works at that package level, like a careful mechanic opening the machine instead of using the PowerPoint app. Its clean command removes files that are no longer connected to any active slide, including abandoned slides, media, notes, themes, and old content-type records. This matters because leftover pieces can bloat a presentation or confuse later editing tools. Its add command either copies an existing slide or creates a blank slide linked to a chosen layout, then registers the new slide in the package so PowerPoint can recognize it. One important detail is that it prints the XML line the user still needs to add to presentation.xml, rather than fully inserting that slide into the visible slide list. Its thumbnail command reads the slide order from the .pptx, asks LibreOffice to render the presentation to PDF, converts that PDF to JPEG slide images, and arranges them into one or more grid images. Hidden slides get a gray placeholder with an X so the thumbnail sheet still matches the real slide order.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so the rest of the script can inspect or edit it. XML is a structured text format used heavily inside PowerPoint files.

**Data flow**: It receives a file path, opens and parses that XML file, then returns the root element of the parsed document. It does not change the file on disk.

**Call relations**: Many other helpers call this whenever they need to understand a PowerPoint package file, such as relationship files, content-type records, or copied slide relationship files.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes a modified XML tree back to disk with a normal XML declaration and UTF-8 text encoding. It is the counterpart to _parse_xml.

**Data flow**: It receives an XML root element and a destination path, turns the XML tree into bytes, and overwrites the file at that path. The before state is an in-memory XML edit; the after state is that edit saved on disk.

**Call relations**: Cleanup and slide-adding helpers call this after removing old relationships, adding new relationships, or updating content-type records.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file that is still pointed to by a PowerPoint relationship file. This is how the cleaner knows what is still in use.

**Data flow**: It receives the unpacked PowerPoint folder, scans all .rels files under it, reads each relationship target, resolves it to a path inside the package, and returns the set of referenced relative paths. Links that point outside the package are ignored.

**Call relations**: run_clean calls this repeatedly during cleanup. Its result is handed to _remove_unreferenced_resources so that only unused resource files are deleted.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds which slide XML files are actually listed as part of the presentation. These are the slides PowerPoint considers active.

**Data flow**: It reads presentation.xml and presentation.xml.rels from the unpacked folder. It matches slide relationship IDs to slide filenames, then returns the names of slides that appear in the presentation's slide list.

**Call relations**: run_clean calls this first, then passes the active slide names to _remove_orphan_slides so stray slide files can be removed safely.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special temporary trash folder named [trash]. This gives the cleaner a simple way to empty files that were already set aside for removal.

**Data flow**: It receives the unpacked folder, looks for a [trash] directory, deletes regular files inside it, removes the empty directory, and returns the deleted paths. If the folder is not present, it returns an empty list.

**Call relations**: run_clean calls this after removing orphaned slides. The returned filenames are added to the full cleanup report and later used when stale content-type entries are stripped.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that exist in the slides folder but are no longer part of the presentation's active slide list. It also removes matching relationship files and stale presentation relationships.

**Data flow**: It receives the unpacked folder and the set of active slide names. It scans ppt/slides, removes slide XML files not in that active set, removes their companion .rels files if present, updates presentation.xml.rels to stop pointing at removed slides, and returns the deleted paths.

**Call relations**: run_clean calls this early in the cleanup flow. It uses _parse_xml and _write_xml when it needs to edit the presentation relationship file.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, embedded files, charts, themes, drawings, and notes. These are the loose parts that can remain after slides are removed or edited.

**Data flow**: It receives the unpacked folder and a set of paths that are still referenced. It checks known PowerPoint resource folders, deletes files not present in the referenced set, removes relationship files whose parent file has disappeared, and returns the deleted paths.

**Call relations**: run_clean calls this after collecting current references. Because deleting one unused file can make another file unused, run_clean may call it more than once.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes content-type records for files that were deleted. Content types are package labels that tell PowerPoint what kind of part each file is.

**Data flow**: It receives the unpacked folder and the list of removed package paths. It opens [Content_Types].xml, removes Override entries whose PartName matches a deleted file, and saves the XML only if something changed.

**Call relations**: run_clean calls this at the end, after all deletions are known. It relies on _parse_xml and _write_xml to edit the content-type XML file.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Performs the full cleanup operation for an unpacked PowerPoint folder. This is the main reusable function behind the clean command.

**Data flow**: It receives the unpacked folder path. It finds active slides, deletes orphaned slides, empties the trash folder, repeatedly removes unreferenced resources until no more are found, cleans stale content-type records, and returns the full list of removed files.

**Call relations**: _cmd_clean calls this after checking the folder exists. Inside, it coordinates the smaller cleanup helpers in the order needed to avoid leaving broken references behind.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next unused slide filename number, such as slide8.xml after slide7.xml. This prevents the add command from overwriting an existing slide file.

**Data flow**: It receives the slides directory, reads existing filenames that match slide<number>.xml, extracts their numbers, and returns one higher than the largest number. If there are no slides, it returns 1.

**Call relations**: _create_from_layout and _clone_existing both call this before creating a new slide file.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to [Content_Types].xml so the PowerPoint package knows the file is a slide. Without this label, PowerPoint may not interpret the new XML file correctly.

**Data flow**: It receives the unpacked folder and the new slide filename. It opens the content-type XML, checks whether the slide is already listed, adds an Override entry if needed, and writes the file back.

**Call relations**: Both slide-creation paths call this after writing or copying the slide file. It uses _parse_xml, creates a new XML element when needed, and saves through _write_xml.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the presentation to the new slide. A relationship is PowerPoint's internal pointer from one package file to another.

**Data flow**: It receives the unpacked folder and slide filename. It opens presentation.xml.rels, checks whether a relationship already points to that slide, otherwise creates the next rId value, writes the new relationship, and returns the relationship ID.

**Call relations**: _create_from_layout and _clone_existing call this after creating the slide. The returned relationship ID is printed for the user to add to the presentation slide list.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Finds the next numeric slide ID for presentation.xml. This ID is separate from the slide filename and is used inside the presentation's slide list.

**Data flow**: It reads presentation.xml, extracts existing slide ID numbers, and returns one higher than the largest. If none are present, it starts at 256, which is the normal starting range for PowerPoint slide IDs.

**Call relations**: _create_from_layout and _clone_existing call this so they can tell the user what slide-list entry to add for the new slide.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that is linked to an existing slide layout. A layout is the template-like structure that defines placeholders and styling for a slide.

**Data flow**: It receives the unpacked folder and a layout filename. It checks that the layout exists, creates the next slide XML file from a blank template, writes a relationship file linking the slide to the layout, registers the slide in package metadata, and prints the XML entry needed to make it visible in the presentation.

**Call relations**: run_add calls this when the source name looks like a slide layout file. It uses the numbering and registration helpers, and exits the program if the requested layout cannot be found.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Copies an existing slide to make a new slide file. It is useful when a user wants another slide with the same contents and resources.

**Data flow**: It receives the unpacked folder and a source slide filename. It verifies the source exists, chooses a new slide number, copies the slide XML and relationship file, removes any notes-slide relationship from the copy, registers the new slide, and prints the slide-list XML entry the user should add.

**Call relations**: run_add calls this when the source is not a layout filename. It uses file copying, XML parsing and writing, and the same package registration helpers as _create_from_layout.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Chooses how to add a slide: either by creating one from a layout or by cloning an existing slide. This is the main reusable function behind the add command.

**Data flow**: It receives the unpacked folder and the source string supplied by the user. If the source looks like slideLayout*.xml, it creates from that layout; otherwise, it treats the source as an existing slide to copy. It prints instructions through the called helper and returns nothing.

**Call relations**: _cmd_add calls this after checking that the folder exists. It hands the actual work to _create_from_layout or _clone_existing.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a .pptx file to find the presentation's slide order and which slides are hidden. This lets the thumbnail grid match what PowerPoint considers the real sequence.

**Data flow**: It receives a .pptx path, opens it as a zip file, reads the presentation relationship file and presentation XML, maps relationship IDs to slide filenames, and returns a list of slide entries with each slide's name and hidden flag.

**Call relations**: run_thumbnail calls this before rendering images. The result is later combined with rendered slide images by _pair_slides_with_images.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns the PowerPoint deck into JPEG images, one per visible rendered slide. It uses external programs because this script does not implement PowerPoint rendering itself.

**Data flow**: It receives the .pptx path and a temporary working folder. It asks LibreOffice's soffice command to convert the deck to PDF, then asks pdftoppm to convert the PDF pages to JPEG files, and returns the image paths. If either conversion fails, it raises an error.

**Call relations**: run_thumbnail calls this after reading slide order. Its output is paired with slide metadata so thumbnails can be labeled and arranged.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a simple gray image used in the thumbnail grid for hidden slides. This keeps hidden slides visible as placeholders without pretending they were rendered normally.

**Data flow**: It receives image dimensions, creates a gray image of that size, draws two diagonal lines across it like an X, and returns the image object.

**Call relations**: _pair_slides_with_images calls this whenever the slide order says a slide is hidden. The generated placeholder is saved in the temporary folder and then used like any other thumbnail image.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (Draw, new).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches slide names from the PowerPoint order with the JPEG files produced by rendering. It also inserts placeholder images where hidden slides appear.

**Data flow**: It receives the slide-order list, the rendered image paths, and the temporary folder. It uses the first rendered image to choose placeholder size when possible, walks through the ordered slides, pairs visible slides with the next rendered image, creates placeholders for hidden slides, and returns pairs of image path plus label.

**Call relations**: run_thumbnail calls this after rendering. It calls _make_hidden_placeholder for hidden slides, and its pairs are passed to _compose_grid.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one contact-sheet image from slide thumbnails and labels. It is like laying printed slide snapshots on a white table in neat rows and columns.

**Data flow**: It receives image-and-label pairs, a column count, and a target cell width. It calculates the needed canvas size, draws each label, resizes each slide image to fit its cell, pastes it into place, optionally draws a thin outline, and returns the finished image object.

**Call relations**: run_thumbnail calls this once for each chunk of slides that fits in a grid. The returned image is then saved as a JPEG file.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (Draw, load_default, new, open).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG grid images showing thumbnails of the slides in a .pptx file. This is the main reusable function behind the thumbnail command.

**Data flow**: It receives the .pptx path, an output filename prefix, and a column count. It reads slide order, creates a temporary folder, renders visible slides, inserts hidden-slide placeholders, breaks the thumbnails into grid-sized chunks, saves each grid as a JPEG, and returns the saved filenames.

**Call relations**: _cmd_thumbnail calls this after validating command-line input. It coordinates the slide-order reader, renderer, pairing helper, and grid composer.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the clean subcommand. It checks the user's folder argument and prints a human-readable cleanup report.

**Data flow**: It receives parsed command-line arguments, turns the unpacked_dir argument into a path, verifies it exists, calls run_clean, and prints either the removed files or a message saying nothing was found. If the folder is missing, it prints an error and exits.

**Call relations**: The argument parser attaches this function to the clean subcommand. When the script is run from the terminal with clean, the main block calls it through the parsed command object.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the add subcommand. It validates the unpacked PowerPoint folder before asking the add logic to create or copy a slide.

**Data flow**: It receives parsed command-line arguments, converts the folder argument to a path, checks that it exists, and calls run_add with the folder and source name. If the folder is missing, it prints an error and exits.

**Call relations**: The parser attaches this function to the add subcommand. It is the bridge between user input and the reusable run_add function.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for the thumbnail subcommand. It validates the PowerPoint file, enforces the column limit, and reports the files it created.

**Data flow**: It receives parsed command-line arguments, checks that the input exists and ends in .pptx, limits the requested columns to the maximum allowed, calls run_thumbnail, and prints the saved grid filenames. If validation or rendering fails, it prints an error and exits.

**Call relations**: The parser attaches this function to the thumbnail subcommand. It wraps run_thumbnail with user-facing checks and error messages.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface: the available subcommands, their arguments, help text, and which function runs for each command.

**Data flow**: It creates an argument parser, adds clean, add, and thumbnail subcommands, defines each command's expected arguments, attaches the matching _cmd_* function, and returns the finished parser.

**Call relations**: The script's main block calls this when run directly. The parser then reads the user's command-line arguments and dispatches to _cmd_clean, _cmd_add, or _cmd_thumbnail.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`io_transport` · `packaging/export`

A `.pptx` file is really a ZIP archive full of XML files and related assets. This script is the “put it back in the box” step after someone has unpacked and possibly edited that folder. It checks that the input is a real directory and that the output name ends in `.pptx`, then copies the whole folder into a temporary workspace. That copy matters because the script edits XML files while packing, and it avoids changing the original working directory.

The main cleanup step removes whitespace that exists only for formatting the XML itself, like indentation and line breaks between tags. It takes special care not to remove real text from PowerPoint drawing text elements, where spaces can be meaningful. Think of it like folding a neatly spaced recipe card into a compact form while making sure none of the ingredient words disappear.

After cleaning `.xml` and `.rels` files, the script creates the destination folder if needed and writes every file into a compressed ZIP archive with a `.pptx` name. It can also be run directly from the command line, where it prints either a success message or an error and exits with a failure code.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a PowerPoint file from a directory that contains the unpacked contents of a `.pptx`. It validates the inputs, cleans XML files, compresses everything into the final archive, and returns a success or error message.

**Data flow**: It receives a source directory path and an output file path. It first turns them into path objects, checks that the source is a directory and that the destination ends in `.pptx`, then copies the source into a temporary folder. Inside that copy, it finds all `.xml` and `.rels` files and passes each one to `_condense_xml` for whitespace cleanup. Finally, it creates the output directory if needed, writes all copied files into a compressed `.pptx` ZIP file, and returns the output path plus a message. If the input checks fail, it returns no path and an error message instead.

**Call relations**: This is the main worker used by the command-line part of the script. When someone runs the script, the parsed arguments are handed to `assemble_pptx`; during its packing process it calls `_condense_xml` on each XML-style file before using standard file-copying, temporary-directory, and ZIP-writing tools to produce the final PowerPoint file.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there to make the XML easier for humans to read. It protects actual PowerPoint text so visible slide content is not accidentally changed.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through each node, and skips protected text nodes where whitespace might be meaningful. For other nodes, it removes blank-only text before or after child elements, and it also removes unusual parser nodes such as comments or processing instructions when their tag behaves like a callable object. It then writes the cleaned XML bytes back to the same file. If parsing or writing fails, it prints an error to standard error and raises the exception so the packing process stops instead of silently making a bad presentation.

**Call relations**: `assemble_pptx` calls this function once for every `.xml` and `.rels` file in the temporary copy of the unpacked presentation. `_condense_xml` does not create the PowerPoint archive itself; it prepares each XML file so `assemble_pptx` can later zip the cleaned folder into the final `.pptx`.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`entrypoint` · `post-generation repair / command-line run`

A .pptx file is really a ZIP folder full of XML files. This script opens that ZIP-like package, looks for known bad patterns, and rewrites the file cleanly if needed. It exists because some generated presentations can contain small mistakes that are not obvious when the file is created, but later cause PowerPoint to show scary “cannot read” repair dialogs or to quietly remove important spaces from text.

The script fixes three things. First, it removes “phantom” slide master references from the package’s content list when the referenced slide master file does not actually exist. That is like removing a table-of-contents entry for a missing chapter. Second, it strips ZIP directory entries, because PowerPoint packages are expected to contain file entries, not separate folder markers. Third, it checks slide and master XML files for text pieces that start or end with spaces or tabs, and adds the XML marker that tells PowerPoint to preserve those spaces.

The main `repair` function reads the package, decides whether anything is wrong, asks `_repair_whitespace_preservation` to find text spacing fixes, and then writes a temporary corrected package. Only after the new file is safely written does it replace the original. If nothing needs fixing, it leaves the file alone.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects visible text spacing in slide XML. PowerPoint may remove leading or trailing spaces unless the text element explicitly says those spaces should be kept, so this function adds that instruction where needed.

**Data flow**: It receives a dictionary whose keys are file names inside the .pptx package and whose values are the raw file contents. It ignores files that are not slide, layout, master, or notes XML files, parses the relevant XML, finds DrawingML text elements, and checks whether their text begins or ends with a space or tab. For each matching text element without `xml:space="preserve"`, it adds that attribute. It returns a smaller dictionary containing only the XML files that were changed, plus a count of how many text elements were fixed.

**Call relations**: The main `repair` function calls this after reading the .pptx contents. This helper uses `lxml.etree.fromstring` to turn XML bytes into an editable tree and `lxml.etree.tostring` to turn modified XML back into bytes. The changed files it returns are later written into the replacement .pptx package.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for a PowerPoint file. It checks whether the file exists, finds the known .pptx problems, rewrites the package if any fixes are needed, and reports the result to the user.

**Data flow**: It starts with a filename from the caller and turns it into a filesystem path. If the file is missing, it prints an error and returns `False`. Otherwise it opens the .pptx as a ZIP archive, reads all real file entries, records which slide master files actually exist, detects directory entries, removes missing slide master references from `[Content_Types].xml`, and asks `_repair_whitespace_preservation` for text-spacing updates. If no problem is found, it prints that no repairs are needed and returns `True`. If repairs are needed, it writes a new temporary ZIP file with bad directory entries skipped and corrected XML substituted, then moves that temporary file over the original and returns `True`.

**Call relations**: This function is called when the script is run from the command line, and it is also the single place where the repair process is coordinated. It calls `_repair_whitespace_preservation` for the text-specific fix, uses `zipfile.ZipFile` to read and write the .pptx package, uses regular expressions to find bad package references, and uses `shutil.move` to replace the original file only after the repaired copy has been created.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### Spreadsheet recalculation
Shared LibreOffice helpers support an Excel recalculation script that refreshes formulas and reports remaining spreadsheet errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`io_transport` · `document conversion or spreadsheet automation`

This file is a thin wrapper around LibreOffice’s command-line program, usually called `soffice`. The larger system likely needs to open or change spreadsheet files automatically, and LibreOffice can do that even when no person is sitting at a desktop. This is called running “headless”: the program works in the background instead of showing a normal app window.

The file solves three practical problems. First, it prepares the environment LibreOffice needs so it can run safely without a graphical display. It does this by setting `SAL_USE_VCLPLUGIN` to `svp`, which tells LibreOffice to use a non-visual display backend. Second, it works out the folder where LibreOffice Basic macros live. That folder is different on macOS and Linux, so the code chooses the right home-directory path for the current operating system. Third, it gives the rest of the project one simple function for running `soffice` with arguments, collecting its printed output and errors, and optionally stopping it if it takes too long.

Think of this file as a small adapter plug: the rest of the project does not need to remember LibreOffice’s platform quirks or environment settings. It asks this helper to prepare the command properly.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the environment variables used when launching LibreOffice. It copies the current process environment and adds the setting that makes LibreOffice use a headless-friendly display backend.

**Data flow**: It reads the current environment from the operating system, makes a copy, adds or replaces `SAL_USE_VCLPLUGIN` with `svp`, and returns that new dictionary. It does not change the global environment for the whole Python process.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls `soffice_env` to get the safe launch settings. The returned environment is then passed into `subprocess.run` so only that LibreOffice process uses these settings.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Finds the folder where LibreOffice stores standard user macros on the current computer. This matters when scripts need to install or use LibreOffice macros before automating spreadsheet work.

**Data flow**: It asks the operating system name, chooses the matching macro-folder template for macOS or Linux, expands `~` into the user’s home folder, wraps the result as a `Path` object, and returns it. If the system is not recognized, it falls back to the Linux-style path.

**Call relations**: This helper stands on its own for any script that needs the LibreOffice macro location. It uses `platform.system` to identify the operating system and `pathlib.Path` to return the folder in a form that is convenient for file operations.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the `soffice` command-line program with the requested arguments and returns the completed result. It is the shared way for scripts to call LibreOffice in the background.

**Data flow**: It receives a list of command-line arguments and an optional timeout. It builds a command beginning with `soffice`, prepares the environment with `soffice_env`, starts the process, captures its standard output and error text, and returns Python’s `CompletedProcess` object containing the exit status and captured text. If a timeout is supplied and LibreOffice runs too long, `subprocess.run` will raise a timeout error.

**Call relations**: Other spreadsheet automation scripts can call `run_soffice` instead of using `subprocess.run` directly. Inside, it calls `soffice_env` so every LibreOffice launch gets the same headless-safe settings, then hands the actual process execution to Python’s `subprocess.run`.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`entrypoint` · `on-demand spreadsheet recalculation`

Excel files can contain formulas whose saved results are stale or wrong until a spreadsheet program recalculates them. This file solves that problem by using LibreOffice in headless mode, meaning LibreOffice runs in the background without opening a visible window. Think of it like asking a calculator to quietly re-check every formula in a workbook, then handing back a short inspection report.

The script first makes sure LibreOffice has a small macro installed. A macro is a tiny script inside LibreOffice; here it tells LibreOffice to calculate every formula, save the workbook, and close it. Before running that macro, the script takes a snapshot of Excel table styling stored inside the `.xlsx` file. This is important because LibreOffice can sometimes remove or change table style information when saving, so the script tries to put those style pieces back afterward.

After LibreOffice finishes, the script opens the workbook with `openpyxl`, a Python library for reading Excel files, and scans the visible calculated values for common Excel errors such as `#REF!` or `#DIV/0!`. It also counts how many formulas are in the workbook. The final result is a JSON-friendly report showing whether recalculation succeeded, how many errors were found, and where the first errors appear.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: This function makes sure LibreOffice has the Basic macro needed to recalculate and save the spreadsheet. Without this setup, LibreOffice would open the file but would not know to run the specific “recalculate everything and save” action.

**Data flow**: It starts by finding LibreOffice’s macro folder. If the macro file already exists and contains the expected macro name, it reports success. If the folder is missing, it briefly starts LibreOffice in setup mode so the folder structure can be created, then writes the macro file. It returns `True` if the macro is ready and `False` if writing it fails.

**Call relations**: The main `recalc` workflow calls this before touching the workbook. It relies on helper functions from `_soffice` to find LibreOffice’s macro location and environment, and it may start LibreOffice through `subprocess.run` when the macro area needs to be initialized.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: This function saves copies of Excel table style snippets before LibreOffice edits the file. It exists because LibreOffice may lose or alter these style snippets when it saves an `.xlsx` file.

**Data flow**: It receives the path to an Excel file, opens the file as a ZIP archive because `.xlsx` files are ZIP bundles internally, and looks through table XML files. For each table that contains a table style element, it stores that element in a dictionary keyed by the internal file name. The output is that dictionary of style fragments.

**Call relations**: `recalc` calls this before running LibreOffice. The saved style fragments are later passed to `_restore_table_styles` so the workbook can keep its original table appearance as much as possible.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: This function inserts or replaces a table style fragment inside one table XML file. It is a small repair tool used while rebuilding the spreadsheet after LibreOffice has saved it.

**Data flow**: It receives raw XML bytes for one table and the original style element to preserve. If the table already has a style element, it replaces it with the saved one. If the style element is missing, it adds the saved one just before the closing table tag. It returns the repaired XML bytes.

**Call relations**: _restore_table_styles calls this for each table file whose style was saved earlier. It does not read or write files itself; it only edits one piece of XML data handed to it.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: This function puts saved Excel table styles back into the workbook after LibreOffice has recalculated and saved it. It helps prevent a calculation pass from accidentally changing how formatted tables look.

**Data flow**: It receives the workbook path and the dictionary of saved style fragments. If there are no styles to restore, it does nothing. Otherwise, it opens the original `.xlsx` archive, writes a temporary rebuilt archive, patches the relevant table XML entries, and then replaces the original file with the rebuilt one. If something goes wrong, it removes the temporary file when possible.

**Call relations**: `recalc` calls this after LibreOffice finishes successfully. During the rebuild, it hands individual table XML chunks to `_patch_table_style`, then uses archive and file-moving operations to put the repaired workbook back in place.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: This function checks the recalculated workbook for common Excel error values such as `#REF!`, `#DIV/0!`, and `#N/A`. It gives the caller a map of which errors appeared and where they were found.

**Data flow**: It receives a workbook path and opens the workbook in “data only” mode, meaning it reads the saved formula results rather than the formula text. It walks through every worksheet, row, and cell. When a cell contains a string with a known Excel error, it records the sheet name and cell address. It returns a dictionary from each error type to a list of locations.

**Call relations**: `recalc` calls this after recalculation and style restoration. Its findings are turned into the final report that tells the user whether the workbook is clean or still contains formula problems.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: This function counts how many formulas are present in the workbook. The count gives context to the final report, for example whether errors were found in a small or formula-heavy spreadsheet.

**Data flow**: It receives a workbook path and opens the workbook with formulas visible instead of only their saved results. It checks every cell and counts strings that begin with `=`, which is how Excel formulas are written. It closes the workbook and returns the total number.

**Call relations**: `recalc` calls this near the end, after error scanning succeeds. Its result is included beside the error summary so the caller can understand the scale of the recalculation.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: This is the main work function for recalculating one Excel file and producing a structured result. It checks the file, prepares LibreOffice, runs the recalculation macro, repairs table styling if possible, and summarizes any remaining formula errors.

**Data flow**: It receives a filename and an optional timeout. It first verifies that the file exists, then ensures the LibreOffice macro is installed. It takes a best-effort snapshot of table styles, runs LibreOffice headlessly with the recalculation macro, and handles timeout or failure messages. If LibreOffice succeeds, it restores table styles, scans for Excel error values, counts formulas, and returns a dictionary describing success, errors found, or any failure.

**Call relations**: `main` calls this when the script is run from the command line. Inside, it coordinates the helper functions in order: macro setup, style snapshot, LibreOffice execution through `_soffice.run_soffice`, style restoration, error scanning, and formula counting.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: This function is the command-line doorway into the script. It reads the user’s arguments, runs recalculation, and prints the result as formatted JSON.

**Data flow**: It reads `sys.argv` for the Excel filename and optional timeout. If no filename is provided, it prints a usage message and exits with an error code. Otherwise, it calls `recalc`, converts the returned dictionary to pretty JSON text, and prints it to standard output.

**Call relations**: This function runs only when the file is executed directly as a script. It hands the real work to `recalc`, then formats the answer for people or other tools that launched the command.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).


### PDF forms and rendering
PDF utilities render pages as images, inspect or annotate layout-based PDFs, and extract or fill native PDF form fields.

### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command / document rendering`

This script solves a practical document problem: many tools can work with images more easily than with PDF pages. Given a PDF file and an output folder, it opens the PDF, renders each page as an image, makes sure the image is not too large, and saves it as a numbered PNG file such as `page_1.png`.

The file uses `pdf2image`, a library that converts PDF pages into image objects. It renders at 200 DPI, meaning a fairly detailed image resolution. After each page is rendered, the script checks its width and height. If either side is bigger than 1000 pixels, it shrinks the image while keeping the same proportions, like resizing a photo so it fits inside a frame without stretching it.

The output folder is created if it does not already exist. As each page is saved, the script prints where it went and what size it ended up being. At the end, it prints a short summary. Without this file, a user or workflow needing page images from a PDF would need to rely on some other conversion step before image-based document processing could happen.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: Converts every page of a PDF into a PNG image and writes those images into a chosen folder. It also shrinks oversized page images so the saved files stay within a manageable size.

**Data flow**: It receives the path to a PDF and the path to an output directory. It creates the directory if needed, asks `pdf2image` to turn the PDF pages into images, optionally resizes each image to fit within 1000 by 1000 pixels, then saves each one as `page_1.png`, `page_2.png`, and so on. Its visible outputs are the image files on disk and progress messages printed to the console.

**Call relations**: This is the worker function for the script. `main` calls it after checking that the user supplied the right command-line arguments. Inside, it relies on `pathlib.Path` to work with folders and file paths, and on `pdf2image.convert_from_path` to do the actual PDF-to-image conversion.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: Provides the command-line entry point for the script. It checks that the user gave exactly two arguments: the input PDF and the output folder.

**Data flow**: It reads the command-line arguments from `sys.argv`. If the arguments are missing or incorrect, it prints a usage message and exits with an error. If they are correct, it passes the PDF path and output directory path to `render`, which performs the conversion.

**Call relations**: This function is called when the file is run directly as a script. It acts like the front desk: it validates the request, then hands the real work to `render`. If the request is malformed, it stops the program using `sys.exit`.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `command-line document processing`

Some PDFs look like forms but do not contain actual form fields that software can fill in. This file solves that by treating the PDF like a printed page: it finds words, long horizontal lines, and small square boxes, then later places text on top of chosen areas as PDF annotations. Think of it like putting transparent sticky notes onto a paper form.

The tool has three commands. The extract command reads each PDF page and writes a JSON file describing the page size, visible words, horizontal rules, likely checkbox boxes, and row bands between lines. That JSON can help another person or tool decide where answers should go. The preview command draws red and blue rectangles onto an image of a page so the user can visually check whether the planned fill areas are correct. The fill command reads a field-definition JSON file, checks for common mistakes such as overlapping boxes or text areas too short for the chosen font size, converts coordinates into the coordinate system used by PDF annotations, and writes the text into a new PDF.

One important detail is coordinate conversion. Images usually measure from the top-left corner downward, while PDF annotations use a bottom-left-based coordinate system. CoordMapper bridges that difference so boxes land in the right physical place.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: This method converts a rectangle from the user-facing coordinate system into the rectangle format needed for a PDF annotation. It matters because image coordinates and PDF coordinates count vertical position in opposite directions.

**Data flow**: It receives a box as four numbers, plus the mapper’s stored page size and coordinate-system settings. If the box came from an image, it first scales the box to the PDF page size, then flips the vertical coordinates. If the box already uses PDF-sized coordinates, it only flips the vertical direction. It returns a four-number rectangle ready to give to the PDF annotation library.

**Call relations**: During the fill command, _validate_and_fill creates a CoordMapper for the target page and uses this conversion before adding text to the PDF. This is the bridge between the field JSON and the PDF library’s annotation placement rules.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: This function scans one PDF page and records the visible layout clues that could help identify where form answers belong. It looks for text, long horizontal lines, and small square boxes that probably represent checkboxes.

**Data flow**: It takes a pdfplumber page object and a page number. It creates a PageLayout record with the page size, then reads the page’s line objects, rectangle objects, and extracted words. Long lines become horizontal rules, small near-square rectangles become tick boxes, and words become text elements with their positions. It returns the filled PageLayout for that page.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. It relies on pdfplumber’s page data and word extraction, then hands the page-level layout back so row ranges can be added afterward.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: This function turns detected horizontal lines into row bands. It helps describe table-like forms where each row sits between two lines.

**Data flow**: It receives a PageLayout that already contains horizontal rules. It sorts the rule positions from top to bottom, then creates a row range between each neighboring pair of lines. It changes the PageLayout in place by adding these row ranges; it does not return a separate value.

**Call relations**: _extract_all_pages calls this right after _extract_page. In the overall extraction flow, _extract_page finds the raw lines, and this function interprets those lines as possible rows.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: This function scans an entire PDF and produces a layout summary for every page. It is the main worker behind the extract command.

**Data flow**: It receives the path to a PDF file. It opens the PDF with pdfplumber, loops through each page, extracts that page’s layout with _extract_page, adds row ranges with _compute_row_ranges, and collects all page layouts into a list. It returns that list.

**Call relations**: cmd_extract calls this when the user runs the extract subcommand. It coordinates the page-by-page work and hands the collected layouts to _pages_to_dict so they can be written as JSON.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: This function converts PageLayout objects into plain dictionaries that can be saved as JSON. It is a packaging step between the internal Python objects and the output file.

**Data flow**: It receives a list of PageLayout objects. For each page, it copies out the page number, size, text elements, horizontal rules, tick boxes, and row ranges into a normal dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after _extract_all_pages has finished scanning the PDF. The result is then passed to JSON writing so other tools or people can read the layout data.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: This function checks a planned set of PDF fill fields and then writes the requested text into the PDF as annotations. It is the main worker behind the fill command.

**Data flow**: It receives an input PDF path, a field-definition JSON path, and an output PDF path. It reads the JSON, opens the PDF, records each page’s dimensions, and walks through the requested form fields. For each field with text, it checks whether the content box is tall enough for the font and whether it overlaps with already placed boxes. If the data passes validation, it converts the field’s rectangle into PDF annotation coordinates, creates a FreeText annotation, and adds it to the correct page. If validation errors are found, it prints them and exits instead of writing a bad PDF. Otherwise, it writes the completed PDF to the output path.

**Call relations**: cmd_fill calls this after checking the command-line arguments. Inside the fill flow, it uses _rects_overlap to catch clashing boxes, CoordMapper to translate coordinates, and pypdf’s reader, writer, and FreeText annotation objects to create the finished PDF.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: This small helper answers one question: do two rectangular boxes touch or cover the same space? It helps prevent annotations from being placed on top of each other or over a label area.

**Data flow**: It receives two rectangles, each represented by four numbers. It compares their left, right, top, and bottom edges. It returns true if the rectangles overlap and false if one is completely to the side or above the other.

**Call relations**: _validate_and_fill calls this while checking each new field against boxes that have already been placed. Its answer becomes part of the validation errors that can stop the fill operation.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: This is the command-line handler for creating a layout JSON file from a PDF. A user runs it when they want a machine-readable map of the PDF’s visible structure.

**Data flow**: It receives the command arguments after the word extract. It expects an input PDF path and an output JSON path. If the arguments are wrong, it prints usage text and exits. Otherwise, it scans the PDF with _extract_all_pages, converts the results with _pages_to_dict, writes the JSON file, and prints a short summary of what it found.

**Call relations**: main calls this when the first command-line word is extract. It starts the extraction pipeline, using _extract_all_pages for the real PDF reading work and _pages_to_dict for the JSON-ready shape.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: This is the command-line handler for drawing planned field boxes onto a page image. It lets a person visually confirm that the JSON field coordinates line up with the form.

**Data flow**: It receives a page number, a field JSON path, an input image path, and an output image path. It reads the field JSON, opens the image, draws red rectangles for content areas and blue rectangles for label boxes on fields from the chosen page, saves the marked-up image, and prints how many fields it highlighted. If the arguments are wrong, it prints usage text and exits.

**Call relations**: main calls this when the first command-line word is preview. Unlike fill, it does not change a PDF; it uses the same field definitions to create a visual check before someone commits to writing annotations.

*Call graph*: 5 external calls (Draw, open, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: This is the command-line handler for filling a PDF using a field-definition JSON file. It gives users a simple command that turns planned fields into actual PDF text annotations.

**Data flow**: It receives the command arguments after the word fill. It expects an input PDF path, a fields JSON path, and an output PDF path. If the arguments are wrong, it prints usage text and exits. Otherwise, it passes those three paths to _validate_and_fill, which performs the checks and writes the output PDF.

**Call relations**: main calls this when the first command-line word is fill. It is intentionally thin: it only checks the command shape, then hands the real validation and PDF-writing work to _validate_and_fill.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: This is the script’s starting point when the file is run from the command line. It chooses which subcommand should run.

**Data flow**: It reads the process arguments from sys.argv. If there is no recognized subcommand, it prints a usage message and exits. If the subcommand is extract, preview, or fill, it passes the remaining arguments to the matching command function.

**Call relations**: The Python runtime calls main when this file is executed directly. main dispatches to cmd_extract, cmd_preview, or cmd_fill through the SUBCOMMANDS table, making the rest of the file available as one small command-line tool.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command invocation`

PDF forms can store real interactive fields, such as text boxes, checkboxes, radio buttons, and drop-down choices. This file is the project’s small toolkit for using those fields directly, instead of guessing where to place text on the page. Without it, a user would have to inspect PDFs by hand or fall back to manual overlay tools when a document already has proper fillable fields inside it.

The file uses pypdf, a Python library for reading and writing PDF files. First, it can check whether a PDF has form fields at all. Then it can extract a plain JSON description of each field: its name, type, page number, page rectangle, and allowed values where relevant. That JSON acts like a map of the form, similar to a seating chart that tells you which seat is where and what label it has.

When filling a PDF, the file reads a JSON list of requested field values, checks that every field name, page, and allowed choice is valid, and then writes a new PDF with those form values applied. It also includes several safety helpers for tricky PDF behavior, such as fields that exist as page widgets but are missing from the usual form catalog, and coordinate conversion so extracted rectangles are easier for humans and layout tools to understand.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF contains fillable-looking widgets that are not reported through the normal form-field list. This matters because some PDFs are built oddly, and a simple field lookup would wrongly say they have no fillable fields.

**Data flow**: It takes a PDF reader, looks through every page, then scans each page annotation. If it finds an annotation marked as a widget with a field type, it returns true; otherwise it returns false after checking all pages.

**Call relations**: The detect command calls this as a backup check after asking pypdf for normal form fields. It helps the command give a more accurate answer for imperfect or unusual PDFs.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a PDF field by walking from a widget up through its parent fields. This is needed because PDFs can store field names in pieces, like folders inside folders.

**Data flow**: It takes one field annotation, collects each `/T` name it finds on that annotation and its parents, reverses the pieces into parent-to-child order, and joins them with dots. If no name pieces exist, it returns nothing.

**Call relations**: The AcroForm extraction path uses this when it is matching page annotations back to their logical field records. It provides the name needed to decide whether an annotation belongs to a regular field or to a radio-button group.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field data into one of this file’s simpler field objects. It hides PDF codes such as `/Tx`, `/Btn`, and `/Ch` behind plain kinds like text, checkbox, and choice.

**Data flow**: It receives a raw PDF field dictionary and a field name. It reads the field type code, creates the matching field object, and returns that object with the human-facing kind filled in.

**Call relations**: Both main extraction paths call this when they discover a field. For button and choice fields, it hands off to the more specific checkbox and choice builders so those field types can include their allowed values.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox field description and tries to identify which PDF value means checked and which means unchecked. This matters because PDFs do not always use the same internal word for a checked box.

**Data flow**: It receives raw PDF field data and a name, reads any listed states, and chooses on and off values. If the states are unusual, it prints a warning and still returns the best checkbox description it can.

**Call relations**: It is used by the general field builder when a PDF button field is treated as a checkbox. The resulting checkbox metadata is later used when extracting JSON and when validating fill values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description for a choice field, such as a drop-down list. It records the values the PDF accepts and the text a person would see for each option.

**Data flow**: It receives raw PDF field data and a name, loops through the field’s state or option list, normalizes each option into a value-and-text pair, and returns a choice field object.

**Call relations**: The general field builder calls this for PDF choice fields. The extracted choice list later appears in the JSON field map and is used to reject invalid fill requests.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Finds a checkbox’s checked value from its appearance data when that value was not already known. In PDF terms, appearance data describes how the field looks in different states.

**Data flow**: It receives a resolved PDF widget and an existing checkbox object. If the checkbox already has an on value, it leaves it alone; otherwise it looks inside the widget’s normal appearances, picks the first state that is not `/Off`, and stores it as the checked value.

**Call relations**: The widget-based extraction path calls this after building a checkbox. It fills in missing checkbox details so later JSON output and value validation have the right on/off values.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle into a top-down coordinate style that is easier to use with page layouts. PDFs usually measure from the bottom of the page, while many people and tools think from the top down.

**Data flow**: It receives a rectangle and the page height. It converts the rectangle numbers to floats, flips the vertical coordinates around the page height, and returns the adjusted rectangle.

**Call relations**: The extraction code uses this whenever it records where a field sits on the page. Radio option collection also uses it so radio buttons and ordinary fields share the same coordinate style.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts fields directly from page widgets when the normal PDF form structure is missing or incomplete. This is the fallback path for PDFs that still have visible fillable controls but do not report them cleanly.

**Data flow**: It takes a PDF reader, walks through each page and each annotation, keeps only widget annotations with field types, builds simple field objects, fills in page numbers and rectangles, improves checkbox metadata when possible, and returns the collected list.

**Call relations**: The AcroForm extractor calls this when pypdf cannot find normal form fields. Inside the loop it relies on the field builder, rectangle converter, and checkbox-on-value helper to turn raw widgets into usable field descriptions.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the full list of fillable fields from a PDF’s AcroForm, which is the PDF feature that stores interactive form fields. This is the main way the file builds a reliable field map.

**Data flow**: It takes a PDF reader and asks pypdf for the form fields. If none are found, it falls back to scanning widgets. Otherwise it builds field objects, identifies possible radio groups, walks page annotations to attach page locations, collects radio options, warns about fields it could not locate, sorts the final list, and returns it.

**Call relations**: Both the extract command and the fill command call this first, because they need the same field metadata. It coordinates several helpers: name reconstruction, field creation, coordinate conversion, radio option collection, and the widget fallback.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one radio-button option to a radio group description. Radio buttons are special because one logical field can have several separate buttons on the page.

**Data flow**: It receives a page annotation, the group name, the page index, page height, and the growing dictionary of radio groups. It reads the annotation’s non-off appearance key as the option value, creates the group if needed, converts the option rectangle, and appends that option to the group.

**Call relations**: The AcroForm extractor calls this when it finds an annotation that belongs to a candidate radio group. It hands back its work by modifying the shared radio-groups dictionary.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Gives fields a stable, human-friendly order: by page, then roughly by row, then from left to right. This makes extracted JSON easier to read and review.

**Data flow**: It receives a field. For a radio group, it uses the first option’s rectangle; for other fields, it uses the field rectangle. It turns the vertical position into a coarse row number and returns a tuple used for sorting.

**Call relations**: The extraction flow uses this as the ordering rule before returning combined fields. It does not change any fields; it only supplies the comparison information used by sorting.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts an internal field object into a plain dictionary that can be written as JSON. This is what turns Python objects into a portable field map users and other tools can read.

**Data flow**: It receives a field object, starts with its name and kind, adds page and rectangle if present, then adds type-specific details such as checkbox on/off values, radio options, or choice lists. It returns the completed dictionary.

**Call relations**: The extract command calls this for every discovered field before writing the JSON file. It is the bridge between the internal extraction model and the external file format.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a requested value is allowed for a specific field. It prevents writing values that the PDF form cannot meaningfully accept, such as a checkbox value that is neither checked nor unchecked.

**Data flow**: It receives a field and a proposed string value. For checkboxes, radio groups, and choice fields, it compares the value with the allowed values and returns an error message if invalid; otherwise it returns nothing.

**Call relations**: The fill-entry validator calls this only after it has confirmed that the field itself exists. Its error message helps the fill command stop before producing a bad or misleading PDF.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the `detect` command, which tells the user whether a PDF appears to contain native fillable fields. It is a quick yes-or-no check before choosing how to process a document.

**Data flow**: It receives command arguments, checks that exactly one PDF path was provided, opens the PDF, asks for normal form fields, then also checks for orphaned widgets. It prints either a success message or a suggestion to use manual layout annotation instead; on bad usage it exits with an error.

**Call relations**: The main dispatcher calls this when the user chooses `detect`. It relies on the orphaned-widget helper to avoid missing PDFs whose fields are present but not listed in the usual place.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the `extract` command, which writes a JSON map of all fillable fields in a PDF. Users can inspect or edit this map before preparing fill values.

**Data flow**: It receives an input PDF path and output JSON path, opens the PDF, extracts field metadata, converts each field to a dictionary, creates the output folder if needed, writes formatted JSON, and prints how many fields were written. On bad usage it exits with an error.

**Call relations**: The main dispatcher calls this for the `extract` subcommand. It depends on the AcroForm extractor for discovery and the dictionary converter for producing JSON-ready output.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the `fill` command, which creates a new PDF with form values filled in from a JSON file. It is the final step after a user or another tool has prepared field values.

**Data flow**: It receives an input PDF, a values JSON file, and an output PDF path. It reads the requested values, extracts the PDF’s field metadata, validates names, pages, and allowed values, groups values by page, writes those values into a cloned PDF, saves the result, and prints a summary. If validation fails, it exits without writing the filled PDF.

**Call relations**: The main dispatcher calls this for the `fill` subcommand. It uses extraction to know what fields exist, validation to protect against bad input, and pypdf’s writer to apply the accepted values to the output file.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks a whole list of requested field entries before filling the PDF. It catches invalid field names, wrong page numbers, and values that are not allowed for that field type.

**Data flow**: It receives the user’s value entries and a lookup table of real fields by name. It walks each entry, compares it with the extracted field metadata, prints clear errors for problems, and returns true if any error was found or false if everything is acceptable.

**Call relations**: The fill command calls this before writing anything. For entries that include an actual value, it delegates the type-specific value check to `_validate_fill_value`.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Acts as the command-line front door for the file. It reads the requested subcommand and sends the remaining arguments to the right command function.

**Data flow**: It reads `sys.argv`, checks that the user supplied a known subcommand, prints a usage line and exits on invalid input, or calls the selected command with the rest of the arguments.

**Call relations**: This runs when the file is executed as a script. It dispatches to `cmd_detect`, `cmd_extract`, or `cmd_fill` through the subcommand table.

*Call graph*: 1 external calls (exit).
