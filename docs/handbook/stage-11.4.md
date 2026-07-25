# Document, Office, PDF, and skill-script execution  `stage-11.4`

This stage is a toolbox used during the agent’s main work or during human review, especially when the work involves office files and PDFs. It is not the system’s startup or shutdown; it is the set of helpers the agent calls when documents need to be opened, changed, checked, or marked up.

The document-review scripts keep the review organized. constants.py fixes the filenames for saved state and logs. models.py defines what a review issue looks like. manage_state.py moves the review through steps and records an audit trail. annotate_pdf.py, annotate_pptx.py, and annotate_xlsx.py then turn saved issues into visible comments in PDFs, PowerPoint slides, and Excel sheets.

The Office tools act like unpacking and repacking stations. DOCX and PPTX unpack scripts turn Word and PowerPoint files into editable folders of XML, while pack scripts rebuild them. Word comments, tracked-change acceptance, PowerPoint slide cleanup, contact sheets, and PPTX repair are handled by focused helpers. Spreadsheet recalculation uses LibreOffice in the background, with shared setup in _soffice.py. The PDF tools detect and fill real form fields, place text on non-fillable forms, and render pages as PNG images.

## Files in this stage

### Document review state and annotations
Shared review state, issue models, and exporters turn saved findings into visible comments across PDF, PowerPoint, and Excel files.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a tiny configuration-style file. It defines the standard filenames that the document review workflow uses when it needs to remember progress or record what happened. The state file, `document_review_state.json`, is where the review process can store its current position or saved information so it can continue later instead of starting over. The log file, `review_log.jsonl`, is where review events can be written one line at a time in JSON Lines format, meaning each line is a separate JSON record. That format is useful for logs because new entries can be appended without rewriting the whole file. By putting these names here, the rest of the code can refer to `STATE_FILENAME` and `LOG_FILENAME` instead of repeating string literals. This is like putting important labels on a shared office noticeboard: everyone uses the same label, so files are easier to find and mistakes are less likely.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review output formatting`

This file is like the standard form used by a reviewer. It says what information every document issue should carry: an ID, a type, a severity level, a description, where it appears, the original text, surrounding context, and any suggested replacement text. That shared shape is defined with a TypedDict, which is a Python way to describe the expected keys and value types in a dictionary without creating a full class object.

The file also translates internal issue type names, such as "spelling_grammar", into friendlier labels like "Spelling/Grammar". This matters because internal names are useful for code, but people reading comments need clear wording.

The main behavior is format_comment. It takes one issue and builds the text that should appear as a review comment. The comment starts with a bracketed label and severity, then includes the issue description, and optionally includes a suggested replacement. Without this file, other scripts would either disagree about what an issue looks like or each invent their own comment wording, making review output less consistent and harder to understand.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: Turns a structured document review issue into a plain text comment that a person can read. It is used when the system needs to present an issue clearly, including its category, severity, explanation, and optionally a suggested fix.

**Data flow**: It receives an issue dictionary and a yes-or-no choice about whether to include suggestions. It looks up a friendly label for the issue type, reads the severity and description, and checks whether there is suggested new text. It returns one formatted string with line breaks; it does not change the issue itself.

**Call relations**: This function sits at the point where machine-readable review data becomes human-readable comment text. Inside, it uses the issue dictionary's get method to safely check for optional suggested text, so missing or empty suggestion content does not break the comment.

*Call graph*: 1 external calls (get).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `command-line review steps, from initialization through completion`

This script is the checklist and notebook for a document review workflow. Without it, each review step would have to remember its own progress, claim IDs, issue IDs, and current phase, which would make the process easy to lose or corrupt. The state lives in document_review_state.json, a plain JSON file, and every command also writes a separate JSON-lines log entry so later readers can see what happened and when.

The workflow is deliberately staged. A review starts with init, which creates an empty state for one document. add-sections records the document’s page ranges and moves the review into claim finding. add-claims stores facts or numbers that need checking. update-claims records whether those claims were verified, refuted, or inconclusive, and can attach source URLs. add-issues records problems found in the document, such as grammar, private information, or logic issues. submit marks the review complete with a final summary.

The file also includes read-only commands, such as get-claims, get-issues, and status, which print a human-friendly view of the saved state. Input is checked carefully before saving: required fields must exist, IDs must point to known sections or claims, page numbers must be positive, and categories must come from approved lists. In effect, the script acts like a gatekeeper at each step, making sure the shared review record stays consistent.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the machine-readable success result for commands that change the review. It combines a friendly message with structured progress information so another tool can read the output reliably.

**Data flow**: It receives a message, the current phase, the document name, and optional extra data such as new claims or issues. It builds one dictionary, turns it into JSON text, and prints it to standard output. It does not change saved state.

**Call relations**: The write commands call this after they have saved their changes. It is the final handoff from commands such as init, add-sections, add-claims, update-claims, add-issues, and submit back to the user or automation that launched the script.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds one audit entry to the review log. This makes the workflow traceable, like signing a visitor book each time something important happens.

**Data flow**: It receives the command name, the phase before and after the command, and any extra details. It adds the current UTC timestamp, converts the entry to JSON, and appends it as one line in the log file. The review state itself is not changed.

**Call relations**: Most commands call this after reading or changing the state. The add, update, submit, and lookup commands use it so the project can later reconstruct what actions were taken and what they affected.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the current review state from disk. Commands use it when they need to know what document is being reviewed, what phase it is in, and what sections, claims, and issues already exist.

**Data flow**: It looks for the state JSON file. If the file is missing, it prints an error and stops the program, because most commands cannot safely run before init. If the file exists, it reads the JSON text and returns it as a Python dictionary.

**Call relations**: All commands except init rely on this before doing their work. It is the doorway from the saved review record into the command’s in-memory work.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the updated review state back to disk. It is used whenever a command has changed the official record.

**Data flow**: It receives the full state dictionary, converts it to neatly indented JSON, and writes it to the state file. The before state on disk is replaced by the new state.

**Call relations**: The commands that create or change data call this before logging and reporting success. It is the counterpart to load_state: load brings the notebook in, save puts the updated notebook back.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user when a command is being run outside the expected review phase. It does not block the command; it simply points out that the workflow order may be unusual.

**Data flow**: It receives the current state and the phase the command normally expects. If they differ, it prints a warning to standard error. Nothing is returned and the state is not changed.

**Call relations**: The staged commands call this near the beginning. It gives a soft guardrail before add-sections, add-claims, update-claims, add-issues, or submit continues with its normal validation and saving.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an incoming JSON object contains all fields needed for that kind of item. This prevents half-formed sections, claims, claim updates, or issues from being saved.

**Data flow**: It receives one item, a list of required field names, and a label for error messages. If any required field is absent, it prints a clear error and stops the program. If everything is present, it lets the caller continue.

**Call relations**: The commands that accept JSON arrays call this for each item before using the item’s values. It acts as the first quality check before more specific validation happens.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a fixed set of allowed choices. It keeps categories such as claim type, claim status, issue type, and severity consistent.

**Data flow**: It receives a value, a set of allowed values, and the field name. If the value is not allowed, it prints an error explaining the valid choices and stops the program. If valid, it returns nothing and the caller continues.

**Call relations**: Claim and issue commands use this after confirming required fields exist. It prevents small spelling differences or unsupported categories from entering the saved state.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number into an integer and makes sure it is at least 1. This keeps section page ranges meaningful.

**Data flow**: It receives a value that may be text or a number. It tries to convert it to an integer, rejects it if conversion fails or if the result is less than 1, and returns the valid integer to the caller.

**Call relations**: add-sections uses this for start_page and end_page before saving a section. The command then separately checks that the end page is not before the start page.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Checks that a field can be used as non-empty text. It is used for location fields so every claim or issue points somewhere in the document.

**Data flow**: It receives a value and a field name. If the value is not text-like or is blank after trimming spaces, it prints an error and stops. Otherwise it returns the value as a string.

**Call relations**: add-claims and add-issues call this while preparing each new record. It turns incoming JSON into a safer saved field.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor field, which is a more precise text marker or pointer inside the document. The anchor may be missing, but if present it must contain real text.

**Data flow**: It receives a value and a field name. If the value is null, it returns null. If it is a non-empty string, it returns that string. Any other value causes an error and stops the program.

**Call relations**: add-claims and add-issues call this for optional anchors after validating the required location. It lets records have either a broad location alone or an additional precise marker.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input from either a command-line argument or a file. This lets users paste small data directly or provide larger data through a separate file.

**Data flow**: It receives parsed command-line arguments. If a file path was supplied, it reads and returns that file’s text. Otherwise it returns the direct data string from the command line.

**Call relations**: The commands that accept JSON payloads call this before parsing the JSON. It hides the difference between --data and --file so each command can process one JSON string.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates the initial empty state for one document and sets the first phase to outline.

**Data flow**: It receives parsed arguments containing a filename. It rejects an empty filename, builds a fresh state with no sections, claims, or issues, saves it to disk, logs the initialization, and prints a JSON success result.

**Call relations**: main dispatches here when the user runs init. This command does not call load_state because it creates the state file that later commands depend on.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document’s major sections and page ranges. This creates the map that later claims and issues must attach to.

**Data flow**: It loads the saved state, warns if the review is not in the outline phase, reads a JSON array from --data or --file, and validates each section’s name and page numbers. It saves the sections into state, moves the phase to find_claims, writes the state, logs the action, and prints a JSON result.

**Call relations**: main sends add-sections commands here after init. This function uses the shared validators and storage helpers, then hands a structured result to _emit_result so automation can see what sections were accepted.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds claims that need checking for a specific section. A claim is a statement in the document that should be verified, such as a public fact or a numerical consistency point.

**Data flow**: It loads state, warns if the phase is not find_claims, checks that the named section exists, and reads a JSON array of claims. For each claim, it validates required fields, allowed claim type, location, and optional anchor. It assigns a new claim ID, stores the claim as unverified, saves the state, logs the addition, and prints a JSON result.

**Call relations**: main calls this for add-claims. It depends on sections created earlier, and it prepares the records that update-claims will later mark as verified, refuted, or inconclusive.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records the outcome of fact-checking existing claims. It marks claims as verified, refuted, or inconclusive and can attach source links used during checking.

**Data flow**: It loads state, remembers the starting phase, warns if the phase is unexpected, and moves from find_claims to fact_check if needed. It reads a JSON array of updates, validates each claim ID and status, increments the claim’s attempt count, adds any source URLs, saves the state, logs status counts, and prints a JSON result.

**Call relations**: main calls this for update-claims. It follows add-claims in the review flow and updates the same claim records that get-claims and status later display.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found in the document, tied to a section and location. Issues can cover factual problems, spelling and grammar, private information, narrative logic, or numerical consistency.

**Data flow**: It loads state, records the starting phase, warns if the review is not in find_issues, and moves from fact_check to find_issues if needed. It checks the section exists, reads a JSON array, validates each issue’s required fields, type, severity, location, and optional anchor, assigns new issue IDs, saves the issues, logs the action, and prints a JSON result.

**Call relations**: main dispatches add-issues commands here after claims have been collected or checked. The issues it creates are later shown by get-issues and counted by status and submit.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Finishes the review with a final summary. It marks the saved state as complete so the workflow has a clear endpoint.

**Data flow**: It loads state, remembers the previous phase, warns if the review is not in the expected issue-finding phase, and rejects an empty summary. It sets the phase to complete, stores the summary, saves the state, logs final counts of sections, claims, and issues, and prints a JSON success result.

**Call relations**: main calls this when the user runs submit. It is the closing step after sections, claims, and issues have been recorded.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved claims in a readable format, optionally narrowed by claim status or section. This is a review lookup command rather than a state-changing command.

**Data flow**: It loads state, collects all claims, filters them if status or section options were provided, and logs the lookup. If nothing matches, it says so. Otherwise it prints each claim’s ID, status, type, section, location, text, description, anchor if present, and sources if present, followed by a total.

**Call relations**: main calls this for get-claims. It reads the claim records created by add-claims and updated by update-claims, but it does not save any changes.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved issues in a readable format, optionally narrowed by severity or section. It helps a reviewer inspect the problems found so far.

**Data flow**: It loads state, gathers all issues, applies optional severity and section filters, and logs the lookup. If no issues match, it prints that message. Otherwise it prints each issue’s ID, severity, type, section, location, text, context, description, optional anchor, suggested replacement text if present, and a total.

**Call relations**: main calls this for get-issues. It reads the issue records created by add-issues and leaves the saved state unchanged.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Shows a compact dashboard of the current review. It helps a user quickly see what phase the review is in and how much has been recorded.

**Data flow**: It loads state and prints the document name and current phase. It then prints sections and page ranges if present, counts claims by status, counts issues by severity and type, and shows the final summary if one exists. It does not write anything.

**Call relations**: main calls this for the status command. It pulls together the same saved data written by all earlier workflow commands into one overview.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each command to the right function. It is the script’s front door.

**Data flow**: It builds an argument parser with subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, get-claims, get-issues, and status. It parses the user’s command-line input, looks up the matching command function, and calls it with the parsed arguments.

**Call relations**: When the script is run directly, main starts first. It does not perform review work itself; instead, it routes each user request to the specific cmd_* function that knows how to do that job.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `manual document review annotation step`

This file turns a document review report into marks directly inside a PDF, so a person can open the PDF and see the problems in context. Without it, review issues would stay in a separate state file, forcing readers to manually match each comment back to the right page and text.

The script expects a saved review state file in the current working directory. That file contains issues such as the page number, the original text that was reviewed, the severity, and the comment. The script opens the input PDF with PyMuPDF, a library for reading and editing PDFs. For each issue, it checks that the page number is usable, chooses a color based on severity, and formats the comment text.

It then searches the page for the original text. If the full search snippet is too long or does not match, it tries a shorter prefix as a fallback. When it finds the text, it highlights it and places a sticky note next to the highlight. If it cannot find the text, it still adds the sticky note at a fixed fallback position near the top-left of the page. This is important: the issue is not silently lost just because exact text matching failed. Finally, it saves the annotated PDF to the requested output path.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved review issues from the review state file. It gives the rest of the script a simple list of issues to place into the PDF.

**Data flow**: It starts with the expected state file name from configuration. It checks whether that file exists; if it does not, it prints an error and stops the script. If the file exists, it reads the JSON text, turns it into Python data, pulls out the saved issues, and returns them as a list.

**Call relations**: The main annotation flow calls this first, before opening or changing the PDF. It relies on standard file-path and JSON reading tools, and it can stop the whole script early with an error if the review state file is missing.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function looks for the reviewed text on a PDF page. Its job is to find the exact area that should be highlighted.

**Data flow**: It receives a PDF page and the original text from an issue. First it searches using a longer starting slice of that text. If that finds nothing, it tries again with a shorter slice, which can still work when the PDF text differs slightly or the saved quote is too long. It returns the matching page areas found by the PDF library.

**Call relations**: The annotation flow calls this for each issue after it has chosen the page. The returned areas are handed back to the PDF-editing step, which uses them to draw a highlight and decide where to place the sticky-note icon.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It opens the input PDF, adds highlights and comments for each saved issue, and writes a new annotated PDF.

**Data flow**: It receives an input PDF path and an output PDF path. It loads review issues, opens the PDF, then loops through the issues one by one. For each valid page reference, it chooses a severity color, formats the comment, searches for the issue text, adds a highlight if possible, adds a sticky note, and counts the annotation. At the end it saves the changed PDF to the output path, closes the file, and prints how many annotations were added.

**Call relations**: This function is called by the script’s command-line entry section after the user provides input and output PDF paths. Inside its flow, it calls load_issues to get the review data, find_quads to locate text on the page, and format_comment to turn an issue into readable note text. It also uses PyMuPDF operations to open the PDF, create highlights, create sticky notes, and save the finished file.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `post-review annotation/export`

A PPTX file is really a zip file full of XML files. PowerPoint comments are not just text pasted onto a slide; they need several linked XML files that say who wrote the comment, which slide it belongs to, and what type of file each part is. This script does that careful packaging work so review findings can appear as normal PowerPoint comments.

The script starts by reading document_review_state.json, which is where the document review process has stored its issues. Each issue is expected to have a slide location. Issues with a usable slide number are grouped by slide. The input presentation is copied to the requested output path, then unpacked into a temporary folder, like opening a suitcase before rearranging its contents.

For each slide with issues, the script creates a comment XML file under ppt/comments and adds a relationship from that slide to its comment file. It also writes the shared comment author file, adds a relationship from the presentation to that author file, and updates [Content_Types].xml so PowerPoint knows what these new files are. Finally, it zips everything back into the output PPTX and deletes the temporary folder. Without this file, the review tool could find issues but would not be able to place them into a PowerPoint deck in a way PowerPoint understands as real comments.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: Reads the saved review results from document_review_state.json. If the state file is missing, it stops the script with a clear error because there is nothing to annotate.

**Data flow**: It looks for the configured state filename in the current working directory. If the file exists, it reads the JSON text, turns it into Python data, and returns the issue records stored under the issues section. If the file does not exist, it prints an error to standard error and exits the process.

**Call relations**: The main annotate flow calls this first. Its output is the raw set of review issues that later steps sort by slide and turn into PowerPoint comments.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: Sorts review issues into buckets by slide number. This matters because PowerPoint comments are stored per slide, not as one single list for the whole presentation.

**Data flow**: It receives a list of issue records. For each one, it tries to read the location field as a number, treating that as the one-based slide number. Issues with invalid or missing slide numbers are skipped. It returns a dictionary where each slide number points to the list of issues for that slide.

**Call relations**: The annotate function calls this after loading issues. The grouped result is then passed to the comment-writing functions so they can create one comment file per affected slide.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: Finds the highest relationship ID already used in a PowerPoint relationship file. This lets the script add a new relationship without accidentally reusing an existing ID.

**Data flow**: It receives the path to a .rels file, which is an XML file listing links between PowerPoint package parts. If the file is missing, it returns 0. Otherwise, it reads the XML, scans each relationship ID for a number, and returns the largest number it finds.

**Call relations**: add_relationship calls this when it needs to choose the next rId value. It is a small helper that protects later XML edits from creating duplicate relationship names.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: Adds a link from one PowerPoint XML part to another. In a PPTX package, these relationship links are how PowerPoint knows that a slide has a comment file, or that the presentation has a comment author file.

**Data flow**: It receives a path to a relationship file, a relationship type, and a target file path. If the relationship file already exists, it reads it; otherwise it creates a new Relationships XML document. If a relationship of the same type is already present, it leaves the file unchanged. If not, it finds the next available rId, adds a new Relationship entry, and writes the XML back to disk.

**Call relations**: write_slide_comments uses this to connect each slide to its comments file. write_author_and_rels uses it to connect the main presentation to the comment author file. It relies on find_max_rel_id to choose safe new IDs.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: Creates the actual comment files for the slides that have review issues. Each issue becomes a PowerPoint comment with formatted text from the review system.

**Data flow**: It receives the temporary unpacked PPTX folder and the issues grouped by slide. It creates the ppt/comments folder if needed. For each slide group, it builds a comment XML file, gives each comment a shared author ID, timestamp, position, unique index, and text from format_comment. It also adds the slide relationship that points to the new comment file. It returns the total number of comments written.

**Call relations**: annotate calls this after unpacking the copied PPTX. This function creates the per-slide comment content, then hands back the final comment count so write_author_and_rels can record the author's last comment index.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: Writes the shared PowerPoint metadata that makes the new comments valid. It defines the comment author, links that author file into the presentation, and tells the PPTX package what the new comment-related files are.

**Data flow**: It receives the temporary PPTX folder, the total comment count, and the grouped slide issues. It writes ppt/commentAuthors.xml with the fixed author name and initials. It adds a presentation relationship to that author file. Then it opens [Content_Types].xml and adds entries for the author file and each slide comment file if they are not already listed. The result is written back into the unpacked PPTX folder.

**Call relations**: annotate calls this after write_slide_comments has created the slide comment files. It uses add_relationship for the presentation-level link and completes the package-level bookkeeping PowerPoint needs before the deck is zipped again.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: Runs the whole annotation process from input PPTX to output PPTX. This is the main worker used by the command-line script.

**Data flow**: It receives an input presentation path and an output presentation path. It loads the saved review issues, stops early if there are none, groups them by slide, and copies the input file to the output location. It then unzips the output file into a temporary folder, writes slide comment files, writes author and content-type metadata, zips the modified folder back into the output PPTX, prints how many comments were added, and removes the temporary folder even if something goes wrong.

**Call relations**: The command-line block calls annotate when the script is run with an input and output path. annotate coordinates all the helper functions: load_issues supplies the issue data, group_by_slide organizes it, write_slide_comments creates the comment XML, and write_author_and_rels finishes the package wiring.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `after document review, when exporting annotations into an XLSX file`

This file is a small command-line tool for turning review results into something a person can see directly inside Excel. The review system stores its findings in a JSON state file. That is useful for software, but not very friendly for a spreadsheet user. This script bridges that gap by placing each issue as a comment on the relevant cell.

The flow is simple. First it loads the saved review issues from document_review_state.json. Then it copies the original spreadsheet to a new output file, so the original stays untouched. It opens the copied workbook with openpyxl, a Python library for reading and writing Excel files.

For each issue, it formats the issue into readable comment text, then tries several ways to find the right place for it. If the issue names a worksheet and a cell address, it tries that exact cell first. If that fails, it searches the named worksheet for a cell containing the original text. If that still fails, it searches every worksheet. As a last resort, it puts the comment on cell A1 of the first worksheet, adding multiple comments together there if needed.

Without this file, review findings would remain separate from the spreadsheet, making them harder for a human reviewer to act on.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved review results from the expected state file. It stops the script with a clear error if that file is missing, because there would be no issues to annotate.

**Data flow**: It starts with the configured state filename, checks whether that file exists in the current working directory, and reads it as text. The text is parsed from JSON into Python data. It then pulls out the stored issues and returns them as a list. If the file is not present, it prints an error to standard error and exits the program.

**Call relations**: The main annotation flow calls this first, before opening or changing the spreadsheet. It relies on JSON parsing and path checking, and it gives annotate the list of issues that drive all later comment placement.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches a worksheet for the first cell whose visible value contains a given piece of text. It is used when the script does not have, or cannot use, an exact cell address.

**Data flow**: It receives a worksheet and some target text. It normalizes the target by trimming spaces and comparing in lowercase, then scans every row and cell. Empty cells are skipped. When it finds a cell whose text contains the target text, it returns that cell. If no matching cell is found, it returns nothing.

**Call relations**: The annotate function calls this after trying a precise cell anchor, or when it needs to search across worksheets. It helps connect an issue's original text back to the spreadsheet cell where that text appears.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function looks for a worksheet by name without caring about uppercase or lowercase differences. It lets the script use an issue's recorded location even if capitalization does not match exactly.

**Data flow**: It receives a workbook and a location name. It checks each worksheet title in the workbook, comparing both names in lowercase. If a title matches, it returns that worksheet. If none match, it returns nothing.

**Call relations**: The annotate function calls this when an issue includes a worksheet location. If it finds the right worksheet, later steps can try the issue's exact anchor or search only that sheet before searching the whole workbook.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a prepared comment to a specific cell reference, such as B12. It gives the main flow a safe way to attempt exact placement without crashing on a bad reference.

**Data flow**: It receives a worksheet, a cell address, and a comment object. It tries to look up that cell and assign the comment to it. If the address works, it changes the cell by adding the comment and returns true. If the address is invalid or cannot be found, it leaves the worksheet unchanged and returns false.

**Call relations**: The annotate function calls this when an issue has both a worksheet and an anchor cell. This is the script's most direct placement method; if it fails, annotate falls back to text searching.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It creates an annotated copy of an Excel workbook by placing each review issue as a cell comment in the best matching location it can find.

**Data flow**: It receives an input spreadsheet path and an output spreadsheet path. It loads review issues, copies the input file to the output path, opens the copy as a workbook, and then processes each issue. For every issue, it builds readable comment text, creates an Excel comment, and tries to place it by exact cell, by searching the named worksheet, by searching all worksheets, or finally by appending it to A1. At the end, it saves the workbook and prints how many comments were added.

**Call relations**: This function ties the whole script together. It calls load_issues to get the review data, uses format_comment to turn each issue into human-readable text, uses openpyxl to open and edit the workbook, and calls find_worksheet, _place_on_cell, and find_cell as increasingly broad ways to find where each comment belongs. The command-line block at the bottom invokes it after checking that the user supplied input and output paths.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).


### Word document packaging and comments
DOCX helpers prepare Word files for XML editing, add comments or accept changes, and rebuild clean document packages.

### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/unpack.py`

`entrypoint` · `manual DOCX unpacking and XML preparation`

A .docx file is really a ZIP package full of XML files. This file opens that package, extracts it into a normal folder, and then cleans up the XML so the contents are easier to inspect, compare, or edit. Without it, someone working on Word document internals would have to unzip the file by hand and deal with noisy XML that contains awkward formatting splits, tracked-change fragments, and literal curly quote characters.

The main flow is like unpacking a suitcase and arranging the contents neatly on a table. First, it checks that the input exists and is a .docx file. Then it unzips everything into the requested output directory. Next, it pretty-prints XML files, meaning it adds indentation and line breaks so nested XML is easier to read. For the main Word document file, word/document.xml, it can combine adjacent tracked changes from the same author and merge neighboring text runs that have the same formatting. In Word XML, a “run” is a small stretch of text with the same styling; Word often splits these into many tiny pieces, which makes editing harder. Finally, it replaces curly quote characters with XML numeric entities so those characters are represented consistently in the saved files.

The script can be run from the command line, and two cleanup steps can be turned off with flags if a caller needs to preserve the original structure more closely.

#### Function details

##### `unpack_docx`  (lines 48–95)

```
def unpack_docx(input_file: str, output_directory: str, merge_runs: bool=True, coalesce_changes: bool=True) -> tuple[UnpackResult | None, str]
```

**Purpose**: This is the main worker that unpacks a .docx file and prepares its XML files for reading or editing. A caller uses it when they want a Word document expanded into a folder, with optional cleanup of runs and tracked changes.

**Data flow**: It receives an input file path, an output directory path, and two yes-or-no options for cleanup. It checks that the source file exists and ends in .docx, creates the destination folder, extracts the ZIP contents, formats XML files, optionally cleans word/document.xml, replaces curly quotes, and returns either an UnpackResult with counts plus a summary message, or no result plus an error message.

**Call relations**: This function sits at the top of the file’s workflow. The command-line block calls it after reading user arguments. Inside, it hands individual cleanup jobs to _indent_xml, _coalesce_tracked_changes, _merge_adjacent_runs, and _replace_curly_quotes, then gathers their results into one human-readable summary.

*Call graph*: calls 4 internal fn (_coalesce_tracked_changes, _indent_xml, _merge_adjacent_runs, _replace_curly_quotes); 3 external calls (__init__, Path, ZipFile).


##### `_indent_xml`  (lines 98–108)

```
def _indent_xml(xml_file: Path) -> None
```

**Purpose**: This function makes one XML file easier to read by adding neat indentation and line breaks. It is useful because raw XML from a .docx package can be dense and hard for humans to follow.

**Data flow**: It receives the path to an XML-like file, reads it as XML, asks the XML library to indent it, and writes the formatted bytes back to the same file. If parsing or writing fails, it silently leaves the file as it was.

**Call relations**: unpack_docx calls this once for each extracted .xml and .rels file. It is an early cleanup step, before the more specific Word document cleanup happens.

*Call graph*: called by 1 (unpack_docx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_replace_curly_quotes`  (lines 111–121)

```
def _replace_curly_quotes(xml_file: Path) -> None
```

**Purpose**: This function replaces curly quote characters with explicit XML character references. That keeps these special punctuation marks represented in a predictable, plain-text-safe way.

**Data flow**: It receives a file path, reads the file as UTF-8 text, checks whether it contains curly single or double quotes, and if so rewrites those characters as numeric XML entities such as &#x201C;. If anything goes wrong, it silently leaves the file unchanged.

**Call relations**: unpack_docx calls this near the end for every extracted XML-related file. It runs after formatting and document cleanup so the final files use consistent quote encoding.

*Call graph*: called by 1 (unpack_docx); 2 external calls (read_text, write_text).


##### `_merge_adjacent_runs`  (lines 129–158)

```
def _merge_adjacent_runs(doc_xml: Path) -> int
```

**Purpose**: This function simplifies Word’s main document XML by combining neighboring text runs that have the same formatting. This matters because Word often splits continuous text into many tiny XML pieces, which makes diffs and manual edits noisy.

**Data flow**: It receives the path to word/document.xml. If the file exists, it parses the XML, removes proofing-error markers, removes run attributes related to Word revision session IDs, finds parents that contain runs, asks _merge_runs_in to simplify each parent, writes the file back only if something changed, and returns the number of runs absorbed into other runs.

**Call relations**: unpack_docx calls this only for word/document.xml when run merging is enabled. It delegates the actual per-container merging to _merge_runs_in, while it is responsible for loading, broad cleanup, saving, and reporting the total.

*Call graph*: calls 1 internal fn (_merge_runs_in); called by 1 (unpack_docx); 4 external calls (parse, tostring, exists, write_bytes).


##### `_canonical_rpr`  (lines 161–165)

```
def _canonical_rpr(run_elem: etree._Element) -> str | None
```

**Purpose**: This function creates a stable fingerprint for a run’s formatting settings. It lets the merge logic decide whether two neighboring runs are formatted the same way and can safely be joined.

**Data flow**: It receives one run element. It looks for that run’s formatting child, called rPr in Word XML; if none exists, it returns None. If formatting exists, it serializes that formatting in a canonical, consistent XML form and returns it as text.

**Call relations**: _merge_runs_in calls this while scanning runs. Its result is the comparison key that tells _merge_runs_in whether the current run belongs with the previous run group.

*Call graph*: called by 1 (_merge_runs_in); 2 external calls (find, tostring).


##### `_merge_runs_in`  (lines 168–203)

```
def _merge_runs_in(container: etree._Element) -> int
```

**Purpose**: This function does the detailed work of merging adjacent runs inside one parent XML element. It keeps separate runs when formatting differs or when another kind of element appears between them.

**Data flow**: It receives an XML container that may contain Word run elements. It walks through the direct children, groups neighboring runs whose formatting fingerprints match, moves the non-formatting contents from later runs into the first run of each group, removes the now-empty donor runs, joins neighboring text nodes inside the merged run, and returns how many runs were absorbed.

**Call relations**: _merge_adjacent_runs calls this for each parent that contains runs. It relies on _canonical_rpr to compare formatting and calls _join_adjacent_text after merging so the resulting run is not left with unnecessary back-to-back text elements.

*Call graph*: calls 2 internal fn (_canonical_rpr, _join_adjacent_text); called by 1 (_merge_adjacent_runs); 1 external calls (remove).


##### `_join_adjacent_text`  (lines 206–221)

```
def _join_adjacent_text(run: etree._Element) -> None
```

**Purpose**: This function cleans up inside a single merged run by joining text elements that ended up side by side. It prevents a merged run from still containing several separate text fragments when one would do.

**Data flow**: It receives one run element. It scans its children, and whenever two neighboring children are both text nodes, it concatenates their text into the first node, preserves leading or trailing spaces when needed, removes the second node, and continues until no adjacent text nodes remain.

**Call relations**: _merge_runs_in calls this after it has moved content from donor runs into an anchor run. It is the final polish step for each merged run group.

*Call graph*: called by 1 (_merge_runs_in); 1 external calls (remove).


##### `_coalesce_tracked_changes`  (lines 229–248)

```
def _coalesce_tracked_changes(doc_xml: Path) -> int
```

**Purpose**: This function combines consecutive tracked insertions or deletions from the same author in the main Word document XML. It makes tracked-change markup less fragmented and easier to understand.

**Data flow**: It receives the path to word/document.xml. If the file exists, it reads and parses the XML while preserving blank text, finds paragraph and table-cell containers, asks _coalesce_in to combine insertion and deletion elements inside each container, writes the XML back if anything was combined, and returns the number of tracked-change elements removed by merging.

**Call relations**: unpack_docx calls this before run merging when tracked-change coalescing is enabled. It coordinates the search over document containers and delegates the local merge decisions to _coalesce_in.

*Call graph*: calls 1 internal fn (_coalesce_in); called by 1 (unpack_docx); 6 external calls (XMLParser, fromstring, tostring, exists, read_bytes, write_bytes).


##### `_coalesce_in`  (lines 251–260)

```
def _coalesce_in(container: etree._Element, change_type: str) -> int
```

**Purpose**: This function looks inside one XML container for tracked changes of one type, either insertions or deletions, and groups nearby changes by author. It prepares the change elements for possible merging.

**Data flow**: It receives a container element and a change type such as ins or del. It collects direct child elements of that type, groups them by their author attribute, sends each group to _merge_change_run, and returns the total number of elements that were merged away.

**Call relations**: _coalesce_tracked_changes calls this for each paragraph or table cell and for both insertion and deletion changes. It hands each author-based run of changes to _merge_change_run, which performs the actual merging.

*Call graph*: calls 1 internal fn (_merge_change_run); called by 1 (_coalesce_tracked_changes); 1 external calls (groupby).


##### `_merge_change_run`  (lines 263–284)

```
def _merge_change_run(elements: list[etree._Element]) -> int
```

**Purpose**: This function merges a sequence of tracked-change elements when they are truly next to each other in the document. It keeps changes separate when other real content lies between them.

**Data flow**: It receives a list of tracked-change XML elements. Starting with the first as the anchor, it checks each later element with _changes_adjacent; if they are adjacent, it moves the later element’s children into the anchor, preserves any trailing text in the surrounding XML, removes the later element, and counts it as absorbed. If they are not adjacent, the later element becomes the new anchor. It returns the number absorbed.

**Call relations**: _coalesce_in calls this for each group of change elements that share an author. It uses _changes_adjacent as the safety check before altering the XML structure.

*Call graph*: calls 1 internal fn (_changes_adjacent); called by 1 (_coalesce_in).


##### `_changes_adjacent`  (lines 287–301)

```
def _changes_adjacent(a: etree._Element, b: etree._Element) -> bool
```

**Purpose**: This function answers a safety question: are two tracked-change elements next to each other except for whitespace or comments? It prevents the code from merging changes that are separated by meaningful document content.

**Data flow**: It receives two XML elements. It finds their shared parent, locates both elements among the parent’s children, examines any text or nodes between them, and returns true only when nothing meaningful sits between them. If the elements are missing from the expected parent structure, it returns false.

**Call relations**: _merge_change_run calls this before merging one tracked change into another. Its answer controls whether the cleanup is safe or whether the changes must remain separate.

*Call graph*: called by 1 (_merge_change_run); 1 external calls (getparent).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/comment.py`

`entrypoint` · `manual CLI run or document-editing script step`

A DOCX file is really a zipped folder full of XML files. This file works on that folder after it has been unpacked, and adds the hidden bookkeeping that makes a Word comment exist. It does not place the visible comment markers around text in document.xml; instead, after adding the comment data, the command-line script prints the XML snippets a user still needs to insert around the annotated text.

The main job is done by insert_comment. It checks that the unpacked folder has a word directory, creates fresh random-looking paragraph and durable IDs, records the current UTC time, and makes sure the four comment-related XML files exist. On the first comment, it copies template files and registers those files in Word’s relationship and content-type lists, which are like a DOCX table of contents saying “these parts belong to the document.”

Then it appends one new XML element to each comment file: the actual text, the extended thread metadata, the durable ID mapping, and the extensible timestamp data. If the new comment is a reply, it also looks up the parent comment’s paragraph ID so Word can thread them together. One important detail: the input text is expected to already be safe for XML, such as using &amp; for an ampersand.

#### Function details

##### `_make_hex_tag`  (lines 90–91)

```
def _make_hex_tag() -> str
```

**Purpose**: Creates an eight-character uppercase hexadecimal ID, which Word uses as a compact identifier for comment paragraphs and durable comment records. It is used when a new comment needs IDs that are unlikely to collide with existing ones.

**Data flow**: No meaningful input comes in. The function asks the random number generator for a number in a fixed range, formats that number as eight uppercase hexadecimal characters, and returns the resulting string.

**Call relations**: insert_comment calls this when it starts adding a new comment. The two generated values are then passed into the XML-building helpers so the new comment can be linked across Word’s different comment files.

*Call graph*: called by 1 (insert_comment); 1 external calls (randint).


##### `_escape_curly_quotes`  (lines 94–97)

```
def _escape_curly_quotes(raw: str) -> str
```

**Purpose**: Replaces curly quotation marks and apostrophes with XML character references. This helps preserve those characters in the exact escaped form expected by the surrounding DOCX tooling.

**Data flow**: A text string comes in. The function scans it for smart quotes like “ ” ‘ ’, replaces each one with its matching XML entity such as &#x201C;, and returns the changed string.

**Call relations**: _serialize_xml calls this just before XML is written back to disk. It is a small cleanup step in the save path, after lxml has turned an XML tree into text.

*Call graph*: called by 1 (_serialize_xml).


##### `_parse_xml_file`  (lines 100–101)

```
def _parse_xml_file(filepath: Path) -> etree._Element
```

**Purpose**: Reads an XML file from disk and turns it into an editable XML tree. Other functions use it whenever they need to inspect or add to an existing DOCX XML file.

**Data flow**: A file path comes in. The function reads the file’s bytes, gives them to lxml, an XML parsing library, and returns the root XML element that represents the whole document tree.

**Call relations**: _append_element_to_file uses it before adding a new child element, _ensure_registrations uses it while editing relationship and content-type files, and _resolve_parent_paragraph uses it to search existing comments for a parent reply target.

*Call graph*: called by 3 (_append_element_to_file, _ensure_registrations, _resolve_parent_paragraph); 2 external calls (fromstring, read_bytes).


##### `_serialize_xml`  (lines 104–106)

```
def _serialize_xml(root: etree._Element) -> bytes
```

**Purpose**: Turns an edited XML tree back into bytes ready to write to disk. It also applies the project’s special curly-quote escaping before saving.

**Data flow**: An XML root element comes in. The function serializes it with an XML declaration and UTF-8 encoding, converts curly quotes to XML entities, and returns the final bytes.

**Call relations**: _append_element_to_file calls this after it has attached a new XML element. This function is the bridge between the in-memory XML tree and the updated file contents.

*Call graph*: calls 1 internal fn (_escape_curly_quotes); called by 1 (_append_element_to_file); 1 external calls (tostring).


##### `_build_comment_element`  (lines 109–151)

```
def _build_comment_element(cid: int, author_name: str, author_initials: str, timestamp: str, paragraph_hex: str, body_text: str) -> etree._Element
```

**Purpose**: Builds the main Word comment XML element: author, date, initials, comment ID, paragraph ID, and the visible comment text. This is the core record that stores what the comment actually says.

**Data flow**: It receives the comment ID, author name, initials, timestamp, generated paragraph ID, and comment body text. It creates a nested XML structure with a comment paragraph, a small comment-reference run, and a text run containing the comment body, then returns that XML element without writing it yet.

**Call relations**: insert_comment calls this after preparing IDs and a timestamp. The returned element is handed to _append_element_to_file so it can be added to word/comments.xml.

*Call graph*: called by 1 (insert_comment); 2 external calls (Element, SubElement).


##### `_build_extended_element`  (lines 154–163)

```
def _build_extended_element(paragraph_hex: str, parent_para_hex: str | None) -> etree._Element
```

**Purpose**: Builds the extra XML record Word uses for comment state and threading. For replies, it can point this comment back to the parent comment’s paragraph ID.

**Data flow**: A paragraph ID comes in, along with an optional parent paragraph ID. The function creates a commentEx XML element marked as not done, adds the parent link if one was supplied, and returns the element.

**Call relations**: insert_comment calls this after it has either skipped parent lookup for a normal comment or found the parent paragraph for a reply. The result is appended to commentsExtended.xml.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_ids_element`  (lines 166–173)

```
def _build_ids_element(paragraph_hex: str, durable_hex: str) -> etree._Element
```

**Purpose**: Builds the XML record that connects a comment paragraph ID to a durable ID. A durable ID is another identifier Word uses to keep tracking a comment reliably.

**Data flow**: The generated paragraph ID and durable ID come in. The function places both values into a commentId XML element and returns it.

**Call relations**: insert_comment calls this while filling out Word’s companion comment files. The returned element is appended to commentsIds.xml so Word can map between the two IDs.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_build_extensible_element`  (lines 176–183)

```
def _build_extensible_element(durable_hex: str, timestamp: str) -> etree._Element
```

**Purpose**: Builds the newest-style comment metadata record, storing the durable ID and UTC date. This supports the extra comment information used by newer versions of Word.

**Data flow**: A durable ID and timestamp come in. The function creates a commentExtensible XML element with those two values as attributes and returns it.

**Call relations**: insert_comment calls this after creating the durable ID and timestamp. The element is then appended to commentsExtensible.xml as the final companion record for the comment.

*Call graph*: called by 1 (insert_comment); 1 external calls (Element).


##### `_resolve_parent_paragraph`  (lines 186–194)

```
def _resolve_parent_paragraph(comments_path: Path, parent_cid: int) -> str | None
```

**Purpose**: Finds the paragraph ID for an existing parent comment, so a new comment can be recorded as a threaded reply. Without this lookup, Word would not know which earlier comment the reply belongs under.

**Data flow**: It receives the path to comments.xml and the numeric ID of the intended parent comment. It parses the XML, searches each comment for a matching Word comment ID, then looks inside that comment for its paragraph ID and returns it; if nothing matches, it returns nothing.

**Call relations**: insert_comment calls this only when the caller requested a reply by giving a parent ID. It relies on _parse_xml_file to read comments.xml before insert_comment decides whether it can continue building the reply metadata.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment).


##### `_append_element_to_file`  (lines 197–200)

```
def _append_element_to_file(filepath: Path, child: etree._Element) -> None
```

**Purpose**: Adds one new XML element to the end of an existing XML file and saves the file. It is the common write step used for each of Word’s comment support files.

**Data flow**: A file path and a child XML element come in. The function reads the file into an XML tree, appends the child to the root element, serializes the updated tree, and writes the bytes back to the same file.

**Call relations**: insert_comment calls this several times: once for the main comment and once for each companion metadata element. It uses _parse_xml_file to open the current XML and _serialize_xml to prepare the updated XML for saving.

*Call graph*: calls 2 internal fn (_parse_xml_file, _serialize_xml); called by 1 (insert_comment); 1 external calls (write_bytes).


##### `_ensure_registrations`  (lines 203–256)

```
def _ensure_registrations(base_dir: Path) -> None
```

**Purpose**: Makes sure the DOCX package knows about the comment XML files. In a DOCX, adding a file is not enough; the package’s relationship and content-type lists must also mention it, or Word may ignore it.

**Data flow**: An unpacked DOCX base directory comes in. The function looks for word/_rels/document.xml.rels and [Content_Types].xml, reads them if present, checks whether comment entries already exist, and adds the missing relationship and content-type records for all four comment files before writing the updated XML back.

**Call relations**: insert_comment calls this only when it is creating the comment files for the first time. It uses _parse_xml_file to inspect existing package metadata and lxml to add the registration elements that connect the new comment files to the document.

*Call graph*: calls 1 internal fn (_parse_xml_file); called by 1 (insert_comment); 2 external calls (SubElement, tostring).


##### `insert_comment`  (lines 259–304)

```
def insert_comment(unpacked_dir: str, spec: CommentSpec) -> tuple[str, str]
```

**Purpose**: Adds one comment, or one threaded reply, to an unpacked DOCX directory. This is the main reusable operation behind the script.

**Data flow**: It receives the unpacked DOCX path and a CommentSpec containing the comment ID, text, author, initials, and optional parent ID. It checks for the word folder, creates IDs and a timestamp, copies template comment files if this is the first comment, registers those files in the DOCX package, builds the needed XML elements, appends them to the right files, and returns the new paragraph ID plus a human-readable success or error message.

**Call relations**: This is the center of the file. The command-line block calls it after reading user arguments, and it delegates the smaller jobs to the helper functions: ID generation, XML element construction, parent lookup, file appending, and DOCX registration. After it returns, the script prints either an error or instructions for placing comment markers in document.xml.

*Call graph*: calls 8 internal fn (_append_element_to_file, _build_comment_element, _build_extended_element, _build_extensible_element, _build_ids_element, _ensure_registrations, _make_hex_tag, _resolve_parent_paragraph); 3 external calls (now, Path, copy).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/pack.py`

`io_transport` · `document repackaging/export`

A DOCX file is really a ZIP archive containing many XML files and related resources. This script is the “put it back in the box” step after a DOCX has been unpacked and edited as a folder. Without it, the edited folder would not be usable as a normal Word document.

The main flow starts by checking two simple things: the input must be a directory, and the output name must end in .docx. It then copies the whole input folder into a temporary staging area. This is like making a safe workbench copy before packing a suitcase, so the original files are not changed.

Before creating the ZIP archive, it scans XML and relationship files and removes whitespace-only text in places where that whitespace is just formatting noise. It is careful not to remove whitespace inside Word text tags, because spaces inside actual document text can matter. Finally, it writes every file from the staging folder into a compressed ZIP file with a .docx extension.

The file can also be run directly from the command line. Server-side validation is left to a later step when the file is shared, so this script focuses only on packaging and light cleanup.

#### Function details

##### `pack_docx`  (lines 22–44)

```
def pack_docx(input_directory: str, output_file: str) -> tuple[Path | None, str]
```

**Purpose**: This function repacks an unpacked DOCX directory into a .docx file. It is used when the project has a folder version of a Word document and needs to produce a standard file that Word or other tools can open.

**Data flow**: It receives an input folder path and an output file path. First it checks that the input is really a directory and that the output ends in .docx; if either check fails, it returns no output path and an error message. If the checks pass, it copies the folder into a temporary staging area, asks _strip_xml_whitespace to clean each XML and .rels file, then writes all staged files into a compressed DOCX archive. It returns the created output path and a success message.

**Call relations**: This is the top-level packing routine used by the command-line part of the script. During packing it calls _strip_xml_whitespace for each XML-like file before handing the staged folder contents to the ZIP writer, so cleanup happens before the final document package is created.

*Call graph*: calls 1 internal fn (_strip_xml_whitespace); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_strip_xml_whitespace`  (lines 47–73)

```
def _strip_xml_whitespace(xml_file: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only there for indentation or formatting. It deliberately preserves whitespace inside Word text-related tags, where spaces may be part of the actual document content.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through each element, removes blank text and blank tails where safe, and drops special non-element nodes that cannot be written normally. It then writes the cleaned XML back to the same file. If parsing or writing fails, it prints an error message to standard error and raises the failure again.

**Call relations**: pack_docx calls this helper while preparing the temporary staging copy. Its cleaned files are then included in the final DOCX archive, so it acts as the cleanup step between copying the source folder and writing the compressed output.

*Call graph*: called by 1 (pack_docx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-docx/scripts/accept_changes.py`

`entrypoint` · `document-processing run`

This file solves a practical document-cleanup problem: Word documents can contain tracked edits, and sometimes the system needs a final clean version with every edit accepted. Instead of trying to understand and rewrite the complicated DOCX format directly, the script asks LibreOffice to do the job, much like asking a word processor to click “Accept All Changes” for you.

The script first checks that the input file exists and is really a .docx file. It then copies that file to the requested output path, so the original is not changed. Next it makes sure LibreOffice has a small Basic macro installed in a temporary LibreOffice user profile. A macro is a tiny script that runs inside LibreOffice; here, it triggers LibreOffice’s built-in “Accept All Tracked Changes” command, saves the document, and closes it.

Finally, the script launches LibreOffice with that copied document and macro. LibreOffice is known to sometimes keep running even after the macro has finished, so this file treats a timeout as success. That is unusual but intentional: by the time the timeout happens, the document has often already been saved correctly. If LibreOffice exits with an actual error, the script reports that instead.

#### Function details

##### `_soffice_env`  (lines 46–49)

```
def _soffice_env() -> dict[str, str]
```

**Purpose**: This prepares the environment settings used when starting LibreOffice. It tells LibreOffice to use a non-graphical display backend, which helps it run safely in the background without a desktop window.

**Data flow**: It starts with the current process environment, which is the set of settings inherited from the shell or parent program. It adds or overrides one setting, `SAL_USE_VCLPLUGIN`, with the value `svp`. It returns the updated environment dictionary for LibreOffice launches.

**Call relations**: Whenever this file starts LibreOffice, the caller asks `_soffice_env` for the right environment first. `_ensure_macro` uses it while preparing the temporary LibreOffice profile, and `accept_tracked_changes` uses it while running the macro on the document.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_profile_arg`  (lines 52–53)

```
def _profile_arg() -> str
```

**Purpose**: This builds the command-line option that tells LibreOffice to use this script’s temporary user profile. Using a separate profile keeps this automation away from a real user’s LibreOffice settings.

**Data flow**: It reads the fixed profile directory path defined near the top of the file. It turns that path into the special `-env:UserInstallation=...` argument LibreOffice expects. The returned string is later placed directly into LibreOffice command lines.

**Call relations**: Both `_ensure_macro` and `accept_tracked_changes` need LibreOffice to use the same temporary profile. `_profile_arg` gives them the shared profile argument so the installed macro and the later document-processing run happen in the same LibreOffice environment.

*Call graph*: called by 2 (_ensure_macro, accept_tracked_changes).


##### `_ensure_macro`  (lines 56–71)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure the LibreOffice macro needed to accept tracked changes is installed before the document is processed. Without it, LibreOffice would open the file but would not know which automated action to run.

**Data flow**: It checks the expected macro file in the temporary LibreOffice profile. If the file already exists and contains the needed macro name, it returns success immediately. If not, it starts LibreOffice briefly to initialize the profile, creates the macro folder if needed, writes the macro XML file, and returns success.

**Call relations**: `accept_tracked_changes` calls `_ensure_macro` after copying the input document and before launching LibreOffice on the output document. Inside this preparation step, `_ensure_macro` asks `_profile_arg` for the correct profile location, asks `_soffice_env` for background-friendly LibreOffice settings, and uses `subprocess.run` to start LibreOffice just long enough to create its profile structure.

*Call graph*: calls 2 internal fn (_profile_arg, _soffice_env); called by 1 (accept_tracked_changes); 1 external calls (run).


##### `accept_tracked_changes`  (lines 74–114)

```
def accept_tracked_changes(input_file: str, output_file: str) -> tuple[None, str]
```

**Purpose**: This is the main work function: it creates a cleaned copy of a DOCX file with all tracked edits accepted. A caller can use it from another Python module, and the command-line block at the bottom also uses it when the script is run directly.

**Data flow**: It receives an input file path and an output file path. It checks that the input exists and has a `.docx` extension, creates the output folder if needed, and copies the input file to the output location. It then makes sure the LibreOffice macro is installed, starts LibreOffice in headless mode on the copied file, and returns a pair of values: `None` plus a human-readable success or error message. If LibreOffice times out, the function still reports success because the macro commonly finishes and saves before LibreOffice hangs.

**Call relations**: This function is the center of the script’s flow. The command-line entry code parses the two file paths and hands them to `accept_tracked_changes`. During the run, it delegates setup details to `_ensure_macro`, gets LibreOffice command pieces from `_profile_arg` and `_soffice_env`, copies the file with `shutil.copy2`, and starts LibreOffice through `subprocess.run` to perform the actual document change.

*Call graph*: calls 3 internal fn (_ensure_macro, _profile_arg, _soffice_env); 3 external calls (Path, copy2, run).


### PowerPoint packaging and slide utilities
PPTX helpers unpack presentations, manipulate slides and contact sheets, rebuild packages, and repair generated files.

### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/unpack.py`

`entrypoint` · `manual document-preparation tool, run before editing PPTX internals`

A .pptx PowerPoint file is really a ZIP archive: a bundle of many smaller files, mostly XML, packed together. This file is a small helper tool for opening that bundle into a normal folder so people or other tools can inspect and edit the slide data directly. Without it, anyone working on the raw contents of a presentation would have to manually unzip the file and clean up hard-to-read XML.

The main workflow is simple. First, it checks that the input file exists and has the .pptx extension. Then it creates the destination folder if needed and extracts the presentation contents there. After extraction, it finds XML files and relationship files, whose .rels extension is used by Office documents to describe links between parts of the package.

For each of those files, it tries to pretty-print the XML, meaning it adds consistent indentation so the structure is readable, like turning a packed paragraph into an outline. Then it replaces “smart quotes” or curly quotation marks with XML entity text such as &#x201C;. This avoids accidental encoding or parsing problems when the XML is later edited.

The helper functions are deliberately forgiving: if one XML file cannot be parsed or rewritten, they quietly leave it alone rather than stopping the whole unpacking job.

#### Function details

##### `extract_pptx`  (lines 33–63)

```
def extract_pptx(pptx_path: str, dest_dir: str) -> tuple[ExtractionResult | None, str]
```

**Purpose**: This is the main work function. It checks a PowerPoint file, unpacks it into a folder, cleans up the XML-like files inside, and reports either success or a clear error message.

**Data flow**: It receives a path to a .pptx file and a path to an output folder. It turns those into filesystem paths, checks that the source exists and looks like a PowerPoint file, creates the output folder, extracts the ZIP contents, finds .xml and .rels files, sends each one through formatting and quote-cleaning helpers, and finally returns an ExtractionResult with the number of processed XML files plus a human-readable message. If the file is missing, has the wrong extension, or is not a valid ZIP archive, it returns no result and an error message.

**Call relations**: This function is the central coordinator for the script. The command-line block calls it after reading the user’s arguments. During its run, it hands each XML-related file to _prettify_xml first so the file becomes readable, then to _escape_smart_quotes so curly quotes are normalized before the final success message is produced.

*Call graph*: calls 2 internal fn (_escape_smart_quotes, _prettify_xml); 3 external calls (__init__, Path, ZipFile).


##### `_prettify_xml`  (lines 66–76)

```
def _prettify_xml(path: Path) -> None
```

**Purpose**: This helper makes one XML file easier for humans to read. It parses the XML, adds neat two-space indentation, and writes the cleaned-up version back to the same file.

**Data flow**: It receives the path to one XML-related file. It tries to read and parse that file as XML, reshape the document with consistent spacing, convert it back into UTF-8 XML bytes with an XML declaration, and overwrite the original file. If parsing or writing fails, it does nothing and leaves the file as it was.

**Call relations**: extract_pptx calls this helper for every .xml and .rels file it finds after unpacking the presentation. It performs the readability step before _escape_smart_quotes does the text-normalization step.

*Call graph*: called by 1 (extract_pptx); 4 external calls (indent, parse, tostring, write_bytes).


##### `_escape_smart_quotes`  (lines 79–87)

```
def _escape_smart_quotes(path: Path) -> None
```

**Purpose**: This helper replaces curly quotation marks in one file with XML entity codes. That keeps the quote characters explicit and less likely to cause trouble in XML editing or processing.

**Data flow**: It receives the path to one XML-related file and reads it as UTF-8 text. If it finds no curly quotes, it stops without changing anything. If it does find them, it replaces each one with its matching XML entity string and writes the updated text back to the same file. If reading or writing fails, it silently leaves the file alone.

**Call relations**: extract_pptx calls this helper after _prettify_xml has had a chance to reformat each file. It is the final cleanup pass before extract_pptx reports how many XML-related files were unpacked and processed.

*Call graph*: called by 1 (extract_pptx); 2 external calls (read_text, write_text).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/slides.py`

`entrypoint` · `on-demand CLI execution`

A PowerPoint file is really a zip file full of XML files, images, charts, and relationship files that say which pieces belong together. This script is a small toolbox for editing those parts without opening PowerPoint. Its `clean` command removes slide and resource files that are no longer referenced, like clearing out props from backstage after they have been cut from the show. Its `add` command either copies an existing slide or creates a blank slide tied to a chosen layout, then updates the package records so PowerPoint can recognize it. One important detail: after adding a slide, it prints the XML line the user still needs to add to `presentation.xml`; it does not insert that final slide-list entry itself. Its `thumbnail` command takes a normal `.pptx`, asks LibreOffice to render it to PDF, converts the PDF pages to JPEG images, and lays those images into one or more labeled grids. Hidden slides are represented by gray placeholder images with an X, so the grid still shows where they sit in the deck. The file matters because PPTX packages are easy to corrupt if their XML relationship files and content type records do not agree. This script keeps those bookkeeping pieces mostly in sync while doing common slide maintenance tasks.

#### Function details

##### `_parse_xml`  (lines 60–61)

```
def _parse_xml(path: Path) -> etree._Element
```

**Purpose**: Reads an XML file and returns its top-level XML element so other code can inspect or edit it. XML is the structured text format PowerPoint uses for most of its internal files.

**Data flow**: It takes a file path, asks the XML parser to read that file, and returns the root element of the parsed document. It does not change the file on disk.

**Call relations**: Many helper functions call this before they can understand PowerPoint relationship files, content type files, or slide relationship files. It is the common doorway from disk text into editable XML objects.

*Call graph*: called by 7 (_active_slide_names, _clone_existing, _collect_all_targets, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 1 external calls (parse).


##### `_write_xml`  (lines 64–65)

```
def _write_xml(root: etree._Element, path: Path) -> None
```

**Purpose**: Writes an edited XML tree back to a file in a form PowerPoint can read. It is used after the script has removed or added XML entries.

**Data flow**: It takes an XML root element and a destination path, converts the XML tree into UTF-8 bytes with an XML declaration, and writes those bytes to disk. The target file is replaced with the new XML content.

**Call relations**: Cleanup and add helpers call this after they change relationship or content type XML. It pairs with `_parse_xml`: one reads XML into memory, the other saves the updated version.

*Call graph*: called by 5 (_clone_existing, _register_content_type, _register_presentation_rel, _remove_orphan_slides, _strip_stale_content_types); 2 external calls (tostring, write_bytes).


##### `_collect_all_targets`  (lines 68–82)

```
def _collect_all_targets(unpacked_dir: Path) -> set[Path]
```

**Purpose**: Builds a list of every file that is referenced by any PowerPoint relationship file. This tells the cleaner which package parts are still in use.

**Data flow**: It starts with an unpacked PPTX folder, searches for all `.rels` relationship files, reads each one, and follows each `Target` path. It returns paths inside the unpacked folder that are pointed to by those relationships, ignoring targets outside the package.

**Call relations**: `run_clean` calls this during each cleanup pass. Its result is handed to `_remove_unreferenced_resources`, which deletes resource files that are not in the returned set.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 2 external calls (resolve, rglob).


##### `_active_slide_names`  (lines 85–100)

```
def _active_slide_names(unpacked_dir: Path) -> set[str]
```

**Purpose**: Finds the slide XML files that are actually part of the presentation’s slide list. This separates real slides from leftover slide files sitting in the folder.

**Data flow**: It reads `presentation.xml.rels` to connect relationship IDs to slide filenames, then reads `presentation.xml` to see which relationship IDs are listed as slides. It returns the slide filenames that are active in the deck.

**Call relations**: `run_clean` calls this first, then passes the active names to `_remove_orphan_slides`. That lets the cleaner remove slide files that PowerPoint would not show.

*Call graph*: calls 1 internal fn (_parse_xml); called by 1 (run_clean); 1 external calls (findall).


##### `_purge_trash`  (lines 103–113)

```
def _purge_trash(unpacked_dir: Path) -> list[str]
```

**Purpose**: Deletes files from a special trash folder inside the unpacked presentation. This is a final sweep for files that were intentionally set aside for removal.

**Data flow**: It looks for a folder named `[trash]` under the unpacked directory. If present, it deletes regular files inside it, removes the empty trash folder, and returns the deleted paths as text.

**Call relations**: `run_clean` calls this after removing orphan slides. Its deleted-file list is combined with the other cleanup results so content type records can later be stripped if needed.

*Call graph*: called by 1 (run_clean).


##### `_remove_orphan_slides`  (lines 116–146)

```
def _remove_orphan_slides(unpacked_dir: Path, active: set[str]) -> list[str]
```

**Purpose**: Deletes slide files that exist on disk but are not listed as active slides in the presentation. It also removes their matching relationship files and stale links from the presentation relationships file.

**Data flow**: It receives the unpacked folder and the set of active slide filenames. It scans `ppt/slides`, deletes any `slide*.xml` not in that active set, deletes the matching `.rels` file if present, edits `presentation.xml.rels` to remove links to inactive slides, and returns the deleted paths.

**Call relations**: `run_clean` calls this after `_active_slide_names` has identified the real slide list. It uses `_parse_xml` and `_write_xml` when it needs to update the presentation relationship file.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `_remove_unreferenced_resources`  (lines 149–200)

```
def _remove_unreferenced_resources(unpacked_dir: Path, referenced: set[Path]) -> list[str]
```

**Purpose**: Deletes unused supporting files such as images, charts, drawings, themes, and notes slides. These files can accumulate after slides are copied, deleted, or edited by other tools.

**Data flow**: It takes the unpacked folder and a set of referenced paths. It walks known resource folders under `ppt`, deletes files that are not referenced, and removes relationship files whose parent resource no longer exists. It returns the list of removed paths.

**Call relations**: `run_clean` calls this repeatedly after `_collect_all_targets`. Repeating matters because deleting one unused file can make another relationship file irrelevant, so the cleaner keeps going until no more resources are removed.

*Call graph*: called by 1 (run_clean).


##### `_strip_stale_content_types`  (lines 203–219)

```
def _strip_stale_content_types(unpacked_dir: Path, removed_parts: list[str]) -> None
```

**Purpose**: Removes package registry entries for files that have just been deleted. In PPTX files, `[Content_Types].xml` is like a table of contents that tells PowerPoint what each part is.

**Data flow**: It takes the unpacked folder and the list of removed parts. It opens `[Content_Types].xml`, removes any `Override` entry whose part name matches a deleted file, and writes the file back only if something changed.

**Call relations**: `run_clean` calls this at the end, after all deletion work is done. It uses `_parse_xml` to read the registry and `_write_xml` to save the cleaned version.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 1 (run_clean).


##### `run_clean`  (lines 222–237)

```
def run_clean(unpacked_dir: Path) -> list[str]
```

**Purpose**: Runs the full cleanup process for an unpacked PowerPoint package. It removes inactive slides, trash files, unused resources, and stale content type entries.

**Data flow**: It takes a folder containing an unpacked PPTX. It finds active slides, deletes slide files not in that list, clears the trash folder, repeatedly removes unreferenced resources, updates `[Content_Types].xml`, and returns every deleted path.

**Call relations**: `_cmd_clean` calls this when the user runs the `clean` subcommand. It coordinates the cleanup helpers in the right order so the package does not keep references to files that no longer exist.

*Call graph*: calls 6 internal fn (_active_slide_names, _collect_all_targets, _purge_trash, _remove_orphan_slides, _remove_unreferenced_resources, _strip_stale_content_types); called by 1 (_cmd_clean).


##### `_next_slide_number`  (lines 276–282)

```
def _next_slide_number(slides_dir: Path) -> int
```

**Purpose**: Chooses the next available slide filename number, such as turning existing `slide1.xml` and `slide2.xml` into a new `slide3.xml`. This avoids overwriting an existing slide file.

**Data flow**: It looks through the slides directory for filenames shaped like `slide<number>.xml`, extracts the numbers, and returns one more than the largest number. If there are no slides, it returns 1.

**Call relations**: Both `_create_from_layout` and `_clone_existing` call this before creating a new slide file. It gives them a safe filename to write.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 2 external calls (glob, match).


##### `_register_content_type`  (lines 285–296)

```
def _register_content_type(unpacked_dir: Path, slide_filename: str) -> None
```

**Purpose**: Adds the new slide to PowerPoint’s content type registry if it is not already there. Without this entry, the package may contain the slide file but PowerPoint may not know what kind of part it is.

**Data flow**: It reads `[Content_Types].xml`, checks whether the new slide already has an `Override` entry, and if not, adds one with the official slide content type. It then writes the updated XML back to disk.

**Call relations**: `_create_from_layout` and `_clone_existing` call this after writing or copying a slide file. It uses `_parse_xml`, creates a new XML element when needed, and uses `_write_xml` to save the registry.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 1 external calls (SubElement).


##### `_register_presentation_rel`  (lines 299–321)

```
def _register_presentation_rel(unpacked_dir: Path, slide_filename: str) -> str
```

**Purpose**: Adds a relationship from the presentation to the new slide. In PPTX files, relationships are the links that tell one part of the package how to find another.

**Data flow**: It reads `ppt/_rels/presentation.xml.rels`, checks whether the slide is already linked, and otherwise picks the next `rId` number and adds a new slide relationship. It writes the relationship file back and returns the relationship ID.

**Call relations**: `_create_from_layout` and `_clone_existing` call this after creating a slide. The returned relationship ID is printed for the user to insert into `presentation.xml` as the final slide-list entry.

*Call graph*: calls 2 internal fn (_parse_xml, _write_xml); called by 2 (_clone_existing, _create_from_layout); 2 external calls (SubElement, match).


##### `_next_slide_id`  (lines 324–332)

```
def _next_slide_id(unpacked_dir: Path) -> int
```

**Purpose**: Chooses the next numeric slide ID for the presentation’s slide list. This ID is separate from the filename and relationship ID, and PowerPoint expects it to be unique.

**Data flow**: It reads `ppt/presentation.xml`, finds all existing slide-list IDs, and returns one more than the largest value. If no IDs are found, it starts at 256, which is a common starting point in PowerPoint files.

**Call relations**: `_create_from_layout` and `_clone_existing` call this near the end. They print the returned ID in the XML snippet the user must add to the presentation slide list.

*Call graph*: called by 2 (_clone_existing, _create_from_layout); 1 external calls (findall).


##### `_create_from_layout`  (lines 335–358)

```
def _create_from_layout(unpacked_dir: Path, layout_name: str) -> None
```

**Purpose**: Creates a new blank slide that is connected to an existing slide layout. A layout is the template-like part that defines where title boxes, body text, and other placeholders belong.

**Data flow**: It receives an unpacked PPTX folder and a layout filename. It checks that the layout exists, creates a new slide XML file from a blank template, creates a relationship file pointing to the layout, registers the slide in content types and presentation relationships, then prints the slide-list XML the user should add manually. If the layout is missing, it prints an error and exits.

**Call relations**: `run_add` calls this when the source name looks like a slide layout file. It relies on `_next_slide_number`, `_register_content_type`, `_register_presentation_rel`, and `_next_slide_id` to make the new slide fit into the package bookkeeping.

*Call graph*: calls 4 internal fn (_next_slide_id, _next_slide_number, _register_content_type, _register_presentation_rel); called by 1 (run_add); 1 external calls (exit).


##### `_clone_existing`  (lines 361–388)

```
def _clone_existing(unpacked_dir: Path, source_name: str) -> None
```

**Purpose**: Duplicates an existing slide file and its relationships, while removing any copied link to speaker notes. This creates a new slide that starts as a copy of another slide.

**Data flow**: It receives an unpacked PPTX folder and a source slide filename. It checks that the source exists, picks a new slide filename, copies the slide XML, copies its relationship file if present, removes notes-slide relationships from the copy, registers the new slide in content types and presentation relationships, then prints the XML slide-list entry the user should add. If the source slide is missing, it prints an error and exits.

**Call relations**: `run_add` calls this when the source is not a slide layout filename. It uses the XML read/write helpers to edit the copied relationship file and the registration helpers to connect the clone to the presentation.

*Call graph*: calls 6 internal fn (_next_slide_id, _next_slide_number, _parse_xml, _register_content_type, _register_presentation_rel, _write_xml); called by 1 (run_add); 2 external calls (copy2, exit).


##### `run_add`  (lines 391–395)

```
def run_add(unpacked_dir: Path, source: str) -> None
```

**Purpose**: Decides which kind of slide creation the user requested: create from a layout or duplicate an existing slide. It is the main entry for the `add` feature.

**Data flow**: It takes an unpacked PPTX folder and a source name. If the source name looks like `slideLayout*.xml`, it creates a blank slide from that layout; otherwise, it clones the named slide. It does not return a value, but it writes files and prints instructions.

**Call relations**: `_cmd_add` calls this after checking that the folder exists. It hands off the actual work to `_create_from_layout` or `_clone_existing`.

*Call graph*: calls 2 internal fn (_clone_existing, _create_from_layout); called by 1 (_cmd_add).


##### `_extract_slide_order`  (lines 403–418)

```
def _extract_slide_order(pptx_path: Path) -> list[dict]
```

**Purpose**: Reads a `.pptx` file to discover the presentation’s slide order and which slides are hidden. This lets the thumbnail grid match the deck’s real order instead of just sorting filenames.

**Data flow**: It opens the PPTX as a zip archive, reads the presentation relationships to map relationship IDs to slide filenames, then reads `presentation.xml` to walk the slide list. It returns a list of entries containing each slide name and whether it is hidden.

**Call relations**: `run_thumbnail` calls this before rendering images. Its slide order is later matched with rendered images by `_pair_slides_with_images`.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (fromstring, ZipFile).


##### `_render_slide_images`  (lines 421–456)

```
def _render_slide_images(pptx_path: Path, work_dir: Path) -> list[Path]
```

**Purpose**: Turns a PowerPoint file into one JPEG image per visible slide. It uses external command-line tools because this script does not render PowerPoint graphics by itself.

**Data flow**: It takes a `.pptx` path and a temporary work folder. It runs LibreOffice in headless mode to convert the presentation to PDF, then runs `pdftoppm` to convert PDF pages into JPEG files. It returns the generated JPEG paths, or raises an error if conversion fails.

**Call relations**: `run_thumbnail` calls this inside a temporary directory. The returned image files are paired with slide names and then passed into the grid-building step.

*Call graph*: called by 1 (run_thumbnail); 2 external calls (glob, run).


##### `_make_hidden_placeholder`  (lines 459–465)

```
def _make_hidden_placeholder(dimensions: tuple[int, int]) -> Image.Image
```

**Purpose**: Creates a simple gray image with an X through it to stand in for a hidden slide. Hidden slides usually are not rendered into normal output, but the thumbnail grid still needs to show that they exist.

**Data flow**: It takes image dimensions, creates a blank gray RGB image, draws two diagonal lines across it, and returns the image object. It does not save the image itself.

**Call relations**: `_pair_slides_with_images` calls this whenever it sees a hidden slide in the slide order. The caller saves the placeholder as a JPEG and labels it as hidden.

*Call graph*: called by 1 (_pair_slides_with_images); 2 external calls (new, Draw).


##### `_pair_slides_with_images`  (lines 468–488)

```
def _pair_slides_with_images(slide_order: list[dict], rendered: list[Path], work_dir: Path) -> list[tuple[Path, str]]
```

**Purpose**: Matches the ordered slide list with the rendered JPEG files, while inserting placeholders for hidden slides. This keeps labels, hidden markers, and images aligned.

**Data flow**: It receives the slide order, the list of rendered images, and a work folder. It uses the first rendered image size for placeholder dimensions when possible, then walks the slide list: hidden entries get a generated placeholder, visible entries get the next rendered image. It returns pairs of image path and label text.

**Call relations**: `run_thumbnail` calls this after `_extract_slide_order` and `_render_slide_images`. It calls `_make_hidden_placeholder` for hidden slides and hands the completed image-label pairs to `_compose_grid`.

*Call graph*: calls 1 internal fn (_make_hidden_placeholder); called by 1 (run_thumbnail); 1 external calls (open).


##### `_compose_grid`  (lines 491–544)

```
def _compose_grid(items: list[tuple[Path, str]], cols: int, cell_w: int) -> Image.Image
```

**Purpose**: Builds one contact-sheet image from slide thumbnail images and labels. It turns separate slide pictures into a clean visual overview.

**Data flow**: It takes image-and-label pairs, a column count, and a cell width. It calculates row and canvas sizes, creates a white canvas, draws each label, resizes each slide image to fit its cell, pastes it into place, and draws a thin outline around it. It returns the finished image object.

**Call relations**: `run_thumbnail` calls this once for each chunk of slides that should fit into one output grid. The returned image is then saved as a JPEG.

*Call graph*: called by 1 (run_thumbnail); 4 external calls (new, open, Draw, load_default).


##### `run_thumbnail`  (lines 547–578)

```
def run_thumbnail(pptx_path: Path, output_prefix: str, cols: int) -> list[str]
```

**Purpose**: Creates one or more JPEG grids showing the slides in a PowerPoint file. It is the main worker behind the `thumbnail` command.

**Data flow**: It takes a PPTX path, an output filename prefix, and a column count. It reads slide order, renders visible slides in a temporary folder, inserts hidden-slide placeholders, splits the result into grid-sized chunks, composes each grid, saves the JPEG files, and returns their paths. If there are no slides to show, it prints an error and exits.

**Call relations**: `_cmd_thumbnail` calls this after validating the input file and limiting the column count. It coordinates `_extract_slide_order`, `_render_slide_images`, `_pair_slides_with_images`, and `_compose_grid`.

*Call graph*: calls 4 internal fn (_compose_grid, _extract_slide_order, _pair_slides_with_images, _render_slide_images); called by 1 (_cmd_thumbnail); 3 external calls (Path, exit, TemporaryDirectory).


##### `_cmd_clean`  (lines 586–597)

```
def _cmd_clean(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `slides.py clean`. It checks the user’s folder argument, runs cleanup, and prints a human-readable report.

**Data flow**: It receives parsed command-line arguments, turns the unpacked directory string into a path, and exits with an error if the folder does not exist. Otherwise it calls `run_clean`, then prints either the removed files or a message saying nothing was removed.

**Call relations**: The argument parser attaches this function to the `clean` subcommand. It is the bridge between command-line input and the cleanup engine.

*Call graph*: calls 1 internal fn (run_clean); 2 external calls (Path, exit).


##### `_cmd_add`  (lines 600–605)

```
def _cmd_add(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `slides.py add`. It validates the unpacked folder and starts the slide creation process.

**Data flow**: It receives parsed command-line arguments, converts the folder to a path, and exits if the folder is missing. If the folder exists, it passes the folder and source name to `run_add`, which writes the new slide files and prints follow-up instructions.

**Call relations**: The argument parser attaches this function to the `add` subcommand. It passes user input into `run_add` without deciding the detailed add strategy itself.

*Call graph*: calls 1 internal fn (run_add); 2 external calls (Path, exit).


##### `_cmd_thumbnail`  (lines 608–623)

```
def _cmd_thumbnail(args: argparse.Namespace) -> None
```

**Purpose**: Implements the command-line behavior for `slides.py thumbnail`. It validates the PowerPoint file, applies the column limit, runs thumbnail generation, and prints the output paths.

**Data flow**: It receives parsed command-line arguments, checks that the input exists and ends in `.pptx`, limits the requested columns to the maximum allowed, then calls `run_thumbnail`. On success it prints the created grid files; on failure it prints the error and exits.

**Call relations**: The argument parser attaches this function to the `thumbnail` subcommand. It wraps `run_thumbnail` with command-line validation and user-facing messages.

*Call graph*: calls 1 internal fn (run_thumbnail); 2 external calls (Path, exit).


##### `build_parser`  (lines 626–664)

```
def build_parser() -> argparse.ArgumentParser
```

**Purpose**: Defines the command-line interface: the available subcommands, their arguments, help text, and which function runs for each subcommand.

**Data flow**: It creates an argument parser, adds `clean`, `add`, and `thumbnail` subcommands, defines each command’s expected arguments, connects each subcommand to its `_cmd_*` function, and returns the parser.

**Call relations**: When the script is run directly, this parser reads the user’s command-line arguments and selects the matching command function. It is the front desk that routes the user to the right tool.

*Call graph*: 1 external calls (ArgumentParser).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/pack.py`

`io_transport` · `packaging/export`

A PowerPoint `.pptx` file is really a ZIP archive full of folders and XML files. This script is the “put it back in the box” step after those files have been unpacked and possibly edited. Without it, the edited folder would remain just a pile of parts, not a presentation PowerPoint can open.

The main flow is simple. First, it checks that the input is a directory and that the output filename ends in `.pptx`. Then it copies the whole directory into a temporary working area, so the original files are not changed. Inside that temporary copy, it finds XML files and relationship files (`.rels`, which describe links between parts of the presentation) and runs a cleanup step on each one.

That cleanup removes indentation-only whitespace from XML. This is like taking extra blank spaces out of a recipe while keeping the actual ingredient names. The important exception is PowerPoint text content: text nodes such as DrawingML text elements are protected so visible slide text is not accidentally erased.

Finally, the script writes every file from the cleaned working folder into a ZIP archive using the requested `.pptx` name. It can also be run directly from the command line with an input folder and output file.

#### Function details

##### `assemble_pptx`  (lines 22–44)

```
def assemble_pptx(source_dir: str, output_path: str) -> tuple[Path | None, str]
```

**Purpose**: This function rebuilds a PowerPoint file from a directory containing unpacked `.pptx` contents. It is the main worker used by the command-line script and returns either the created path with a success message or an error message if the inputs are wrong.

**Data flow**: It takes a source folder path and an output file path. It first turns them into path objects, checks that the source is really a folder, and checks that the destination ends in `.pptx`. If those checks pass, it copies the source folder into a temporary workspace, cleans XML-like files there, creates the output folder if needed, and writes the cleaned files into a compressed ZIP archive. The result is the destination path plus a human-readable message; the original source folder is left unchanged.

**Call relations**: This is the top-level packing step. When the script is run from the command line, the parsed arguments are passed here. During its work, it calls `_condense_xml` for each XML or `.rels` file before handing the full temporary folder to Python’s ZIP writer to create the final presentation file.

*Call graph*: calls 1 internal fn (_condense_xml); 4 external calls (Path, copytree, TemporaryDirectory, ZipFile).


##### `_condense_xml`  (lines 47–71)

```
def _condense_xml(filepath: Path) -> None
```

**Purpose**: This helper cleans one XML file by removing whitespace that is only formatting, while preserving actual PowerPoint text. It makes the packed presentation smaller and neater without changing what appears on slides.

**Data flow**: It receives the path to one XML or relationship file. It parses the file into an XML tree, walks through every node, and removes text or tail content that is only blank spacing, except in protected text elements where whitespace may be meaningful. It also removes unusual processing-style child nodes if encountered. Then it writes the cleaned XML back to the same file using a UTF-8 XML declaration. If parsing or writing fails, it prints an error to standard error and raises the problem again.

**Call relations**: This function is called by `assemble_pptx` while preparing the temporary copy of the presentation contents. It does not create the `.pptx` itself; instead, it cleans individual XML files so that `assemble_pptx` can later zip the cleaned folder into the final PowerPoint file.

*Call graph*: called by 1 (assemble_pptx); 3 external calls (parse, tostring, write_bytes).


### `extensions/documents/ufo_ext_documents/skills/office-pptx/scripts/repair.py`

`entrypoint` · `manual or post-generation PPTX repair`

A .pptx file is really a ZIP file full of XML files. If some of those XML files describe parts that do not exist, or if the ZIP contains directory entries PowerPoint does not expect, PowerPoint may show a scary “cannot read” or “repair” message. This file is a cleanup tool for those cases.

The script opens the PPTX as a ZIP archive and looks at its internal file list. First, it checks which slide master files really exist, then removes “phantom” slide master references from [Content_Types].xml. That file is like the package’s table of contents, so wrong entries there can confuse PowerPoint.

It also removes ZIP directory entries, because the PowerPoint packaging rules expect only file entries. Finally, it scans slide, layout, master, and notes XML files for text runs. If a text run starts or ends with a space or tab, the script adds xml:space="preserve". That tells PowerPoint, “do not trim this whitespace.” Without it, indented code, aligned text, or intentional spacing can be silently damaged.

If nothing is wrong, it leaves the file alone. If repairs are needed, it writes a temporary fixed PPTX and then replaces the original file.

#### Function details

##### `_repair_whitespace_preservation`  (lines 35–65)

```
def _repair_whitespace_preservation(entries: dict[str, bytes]) -> tuple[dict[str, bytes], int]
```

**Purpose**: This function protects intentional spaces and tabs inside PowerPoint text. It adds the XML marker that tells PowerPoint to keep leading or trailing whitespace instead of silently trimming it.

**Data flow**: It receives a dictionary of PPTX ZIP entries, where each name points to that file’s raw bytes. It only examines XML files that can contain visible text, parses each one, finds drawing text elements, and checks whether their text begins or ends with a space or tab. For any matching text element missing xml:space="preserve", it adds that marker and stores the rewritten XML. It returns the changed files plus a count of how many text elements were fixed.

**Call relations**: The main repair function calls this after reading the PPTX contents. This helper uses XML parsing and writing from lxml so the main function does not need to know the details of walking through text elements. It hands back only the files that changed, so repair can write those corrected versions into the new PPTX.

*Call graph*: called by 1 (repair); 2 external calls (fromstring, tostring).


##### `repair`  (lines 68–134)

```
def repair(filename)
```

**Purpose**: This is the main repair routine for one PPTX file. It checks for known problems, rewrites the package if needed, and reports whether repairs were applied.

**Data flow**: It receives a filename, turns it into a filesystem path, and first checks that the file exists. It opens the PPTX as a ZIP file, reads all real file entries, notes any directory entries, and records which slide master XML files actually exist. It then cleans false slide master references from [Content_Types].xml, asks _repair_whitespace_preservation to fix text whitespace markers, and decides whether anything changed. If repairs are needed, it writes a temporary ZIP without bad directory entries and with corrected XML files, then moves that temporary file over the original. It returns True when the file was successfully checked or repaired, and False when the input file was missing.

**Call relations**: When the script is run from the command line, this is the function used to do the work. Inside its flow, it calls _repair_whitespace_preservation for the text-specific cleanup, uses regular expressions to find and remove bad package references, uses zipfile to read and rewrite the PPTX container, and uses shutil.move to replace the original file only after the repaired copy has been written.

*Call graph*: calls 1 internal fn (_repair_whitespace_preservation); 5 external calls (Path, match, sub, move, ZipFile).


### Spreadsheet recalculation
LibreOffice helpers run headless spreadsheet recalculation and report remaining workbook formula errors.

### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/_soffice.py`

`util` · `cross-cutting`

LibreOffice is normally a desktop app, but these document scripts need to use it like a background tool. This file makes that safer and more consistent. Think of it as a small adapter that tells LibreOffice, “run quietly in the back room, not on the main stage.”

The file does three main things. First, it builds an environment for LibreOffice with `SAL_USE_VCLPLUGIN` set to `svp`. This asks LibreOffice to use a headless-friendly display backend, which helps it run on Linux and macOS without needing a normal graphical window. Second, it knows where LibreOffice stores user macros on the current operating system. macOS and Linux keep those files in different home-folder locations, so the helper chooses the right path and expands `~` into the real user directory. Third, it wraps the `soffice` command-line program, which is the executable used to start LibreOffice. The wrapper captures LibreOffice’s output, returns text instead of raw bytes, and can stop waiting after a timeout.

Without this file, every script that talks to LibreOffice would need to repeat these details. That would make the code easier to get wrong, especially across different operating systems.

#### Function details

##### `soffice_env`  (lines 16–19)

```
def soffice_env() -> dict[str, str]
```

**Purpose**: Builds the set of environment variables used when starting LibreOffice. It copies the current process environment, then adds the setting that encourages LibreOffice to run in a headless-friendly mode.

**Data flow**: It starts with the current operating system environment. It copies those values, adds or replaces `SAL_USE_VCLPLUGIN` with `svp`, and returns the new dictionary. It does not change the original environment for the whole program.

**Call relations**: When `run_soffice` is about to start LibreOffice, it calls this function to prepare the environment for that child process. The prepared environment is then handed to `subprocess.run`, so LibreOffice starts with the intended headless setting.

*Call graph*: called by 1 (run_soffice).


##### `macro_dir`  (lines 22–25)

```
def macro_dir() -> Path
```

**Purpose**: Returns the folder where LibreOffice stores user macros for the current operating system. This lets other scripts find or install macros without hard-coding separate macOS and Linux paths.

**Data flow**: It reads the current platform name, chooses the matching macro directory pattern, falls back to the Linux path if the platform is not known, expands `~` into the user’s home directory, and returns the result as a `Path` object.

**Call relations**: This helper stands ready for scripts that need LibreOffice’s macro folder. In this file’s shown call graph, no other local function calls it, but it uses the platform check and path-building library to produce a usable filesystem path.

*Call graph*: 2 external calls (Path, system).


##### `run_soffice`  (lines 28–34)

```
def run_soffice(args: list[str], timeout: int | None=None) -> subprocess.CompletedProcess[str]
```

**Purpose**: Runs the `soffice` command-line program with the given arguments. It is the shared doorway through which scripts start LibreOffice in the background.

**Data flow**: It receives a list of command arguments and an optional timeout. It puts `soffice` at the front to form the full command, prepares the environment with `soffice_env`, runs the command while capturing standard output and error as text, and returns the completed process result. If a timeout is supplied and LibreOffice takes too long, the underlying subprocess call can raise a timeout error.

**Call relations**: Higher-level scripts can call this function whenever they need LibreOffice to convert, inspect, or modify a document. Inside, it calls `soffice_env` to get the right headless environment, then hands the full command to `subprocess.run`, which actually starts and waits for LibreOffice.

*Call graph*: calls 1 internal fn (soffice_env); 1 external calls (run).


### `extensions/documents/ufo_ext_documents/skills/office-xlsx/scripts/recalc.py`

`orchestration` · `on-demand spreadsheet recalculation`

Excel files can contain formulas whose saved results are stale, missing, or different from what a spreadsheet program would calculate today. This file solves that by using LibreOffice in headless mode, meaning LibreOffice runs in the background without opening a visible window. It installs a small LibreOffice macro if needed, asks that macro to calculate all formulas in the workbook, saves the workbook, and closes it.

There is one extra wrinkle: LibreOffice may alter table style information inside the zipped Excel file. An .xlsx file is really a zip package full of XML files, so the script takes a snapshot of table style snippets before recalculation and puts them back afterward. This is like taking a photo of a carefully formatted table before repairs, then restoring the tablecloth if the repair tool disturbed it.

After recalculation, the script opens the workbook with openpyxl, a Python library for reading Excel files, and scans cell values for common spreadsheet errors such as #REF! or #DIV/0!. It returns a JSON-friendly report with the overall status, number of errors, number of formulas, and a short list of where errors appear. If anything important fails, such as the file not existing or LibreOffice timing out, it returns an error message instead.

#### Function details

##### `_ensure_macro`  (lines 43–62)

```
def _ensure_macro() -> bool
```

**Purpose**: This makes sure LibreOffice has the small Basic macro needed to recalculate and save the workbook. Without this macro, the script could start LibreOffice but would not have a reliable command to tell the open spreadsheet to calculate everything and store the result.

**Data flow**: It asks the shared LibreOffice helper code where macros live, then checks whether the expected macro file already exists and contains the recalculation routine. If the macro folder is missing, it starts LibreOffice once in headless initialization mode to create the user profile area, then writes the macro text. It returns true if the macro is ready and false if writing it fails.

**Call relations**: The main recalc flow calls this near the start, before asking LibreOffice to touch the workbook. It relies on _soffice.macro_dir to find the macro location, _soffice.soffice_env to run LibreOffice with the right environment, and subprocess.run when it needs LibreOffice to initialize its macro directory.

*Call graph*: called by 1 (recalc); 3 external calls (macro_dir, soffice_env, run).


##### `_snapshot_table_styles`  (lines 65–73)

```
def _snapshot_table_styles(path: str) -> dict[str, bytes]
```

**Purpose**: This records existing Excel table style XML snippets before LibreOffice rewrites the workbook. It protects formatting details that might otherwise be changed or lost during recalculation.

**Data flow**: It receives the workbook path, opens the .xlsx file as a zip archive, and looks through XML files under xl/tables. For each table file, it extracts the self-contained tableStyleInfo element if present. It returns a dictionary mapping each table XML filename to the exact style bytes found there.

**Call relations**: The recalc function calls this before running LibreOffice. Its output is later passed to _restore_table_styles, so the formatting snapshot can be put back after recalculation.

*Call graph*: called by 1 (recalc); 1 external calls (ZipFile).


##### `_patch_table_style`  (lines 76–79)

```
def _patch_table_style(data: bytes, style_element: bytes) -> bytes
```

**Purpose**: This inserts or replaces one table style XML snippet inside one table XML document. It is the small repair step used when restoring styles after LibreOffice has saved the workbook.

**Data flow**: It receives raw XML bytes for a table and the saved table style bytes. If the table already has a tableStyleInfo element, it replaces that element with the saved one. If not, it inserts the saved style just before the closing table tag. It returns the corrected XML bytes.

**Call relations**: _restore_table_styles calls this for each table file that had a saved style. It does not deal with files directly; it only transforms one piece of XML and hands the modified bytes back to the restore process.

*Call graph*: called by 1 (_restore_table_styles).


##### `_restore_table_styles`  (lines 82–99)

```
def _restore_table_styles(path: str, styles: dict[str, bytes]) -> None
```

**Purpose**: This puts saved table style information back into the recalculated workbook. It exists because LibreOffice can save a valid spreadsheet while still disturbing style details that this project wants to preserve.

**Data flow**: It receives the workbook path and the saved style dictionary. If there are no saved styles, it does nothing. Otherwise, it opens the original workbook zip for reading and a temporary zip for writing, copies every file across, and patches table XML files that have saved styles. When the temporary file is complete, it replaces the original workbook with it. If something goes wrong, it removes the temporary file if it exists.

**Call relations**: The recalc function calls this after LibreOffice finishes successfully. Inside the restore loop it delegates the actual XML edit to _patch_table_style, while zipfile handles reading and writing the Excel package and shutil.move swaps the repaired file into place.

*Call graph*: calls 1 internal fn (_patch_table_style); called by 1 (recalc); 3 external calls (remove, move, ZipFile).


##### `_scan_errors`  (lines 102–114)

```
def _scan_errors(path: str) -> dict[str, list[str]]
```

**Purpose**: This checks the recalculated workbook for visible spreadsheet error values, such as #DIV/0! or #REF!. It turns a potentially huge workbook into a clear list of error types and cell locations.

**Data flow**: It receives a workbook path and opens it with formulas resolved to their saved results. It walks through every worksheet, row, and cell. When a text cell contains one of the known Excel error strings, it records the worksheet name and cell address under that error type. It closes the workbook and returns a dictionary of error types to location lists.

**Call relations**: The recalc function calls this after LibreOffice has recalculated and saved the file. The returned error map is then summarized into the final JSON-style result.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `_count_formulas`  (lines 117–126)

```
def _count_formulas(path: str) -> int
```

**Purpose**: This counts how many formulas are present in the workbook. The count gives useful context: a clean report is more meaningful when the reader can see how many formulas were checked.

**Data flow**: It receives a workbook path and opens it with formula text preserved instead of calculated values. It walks every worksheet and cell, counting string values that start with an equals sign, which is how Excel formulas are stored. It closes the workbook and returns the total number found.

**Call relations**: The recalc function calls this while building the final success or error-found report. It uses openpyxl to read the workbook and gives recalc one number to include in the output.

*Call graph*: called by 1 (recalc); 1 external calls (load_workbook).


##### `recalc`  (lines 129–181)

```
def recalc(filename: str, timeout: int=DEFAULT_TIMEOUT) -> dict
```

**Purpose**: This is the main work function: it validates the file, prepares LibreOffice, recalculates the workbook, restores table styles, scans for formula errors, and returns a structured result. Other code can call it when it needs a workbook refreshed and checked without using the command line.

**Data flow**: It receives a filename and an optional timeout in seconds. It first checks that the file exists, converts it to an absolute path, and makes sure the LibreOffice macro is installed. It snapshots table styles, runs LibreOffice headlessly with the recalculation macro, handles timeout or LibreOffice failures, restores table styles, scans for errors, counts formulas, and returns a dictionary describing either success, errors found in cells, or a failure message.

**Call relations**: The command-line main function calls recalc after reading user arguments. recalc is the coordinator for the whole file: it calls _ensure_macro before LibreOffice, _snapshot_table_styles before saving, _restore_table_styles after saving, and then _scan_errors and _count_formulas to build the final report. It hands the actual LibreOffice process launch to _soffice.run_soffice.

*Call graph*: calls 5 internal fn (_count_formulas, _ensure_macro, _restore_table_styles, _scan_errors, _snapshot_table_styles); called by 1 (main); 2 external calls (run_soffice, Path).


##### `main`  (lines 184–191)

```
def main() -> None
```

**Purpose**: This is the command-line entry for the script. It lets a person or automation run the recalculation from a shell and receive a JSON report on standard output.

**Data flow**: It reads command-line arguments from sys.argv. If no workbook path is provided, it prints a usage message and exits with an error code. Otherwise, it reads the filename and optional timeout, calls recalc, converts the returned dictionary to nicely indented JSON text, and prints it.

**Call relations**: Python calls main when this file is run directly. main is a thin wrapper around recalc: it handles command-line input and output, while recalc performs the actual spreadsheet work.

*Call graph*: calls 1 internal fn (recalc); 2 external calls (dumps, exit).


### PDF form and rendering tools
PDF command-line tools inspect and fill forms, place text on non-fillable layouts, and render pages as images.

### `extensions/documents/ufo_ext_documents/skills/pdf/formfill.py`

`entrypoint` · `command invocation`

PDF forms store their fields in a special internal structure called an AcroForm. Sometimes the fields are well organized there, and sometimes they only appear as page annotations called widgets, which are visible form controls on a page. This file hides those PDF details behind three simple commands: detect, extract, and fill.

The tool first reads a PDF with pypdf, a Python library for reading and writing PDFs. For extraction, it looks for form fields, works out each field’s name, type, page number, and rectangle on the page, then writes that information as JSON. It understands text fields, checkboxes, radio-button groups, and choice lists. It also converts PDF coordinates into a more human-friendly top-down page coordinate system, like reading a document from the top-left rather than measuring from the bottom-left.

For filling, it reads a JSON list of field values, checks that each name, page, and allowed value is valid, then writes a new PDF with those form fields updated. Without this file, other tools would have to manually understand the many quirks of PDF form internals, including missing AcroForm entries, checkbox on/off names, and radio options.

#### Function details

##### `_has_orphaned_widgets`  (lines 41–50)

```
def _has_orphaned_widgets(reader: PdfReader) -> bool
```

**Purpose**: Checks whether a PDF has fillable controls that are not reported through the normal form-field list. These are called orphaned widgets: visible form controls on pages that still act like fields but are not neatly listed in the PDF’s main form table.

**Data flow**: It receives a PDF reader, walks through every page, and looks at each page annotation. If it finds an annotation that is a widget and has a field type, it returns true; otherwise it returns false after checking the whole document.

**Call relations**: cmd_detect uses this as a fallback after asking pypdf for normal form fields. That lets the detect command recognize messy PDFs that still contain fillable controls.

*Call graph*: called by 1 (cmd_detect).


##### `_full_field_name`  (lines 53–61)

```
def _full_field_name(annotation: dict) -> str | None
```

**Purpose**: Builds the complete name of a PDF field when the name is split across parent and child field objects. This matters because PDFs can store a field name like a folder path, with each level contributing one part.

**Data flow**: It starts with an annotation, reads its local name part, then climbs through its parent objects collecting more name parts. It reverses those parts and joins them with dots, returning the full field name, or nothing if no name parts exist.

**Call relations**: _extract_from_acroform calls this while scanning page annotations. The full name lets extraction match a visible widget on a page back to the field metadata found earlier.

*Call graph*: called by 1 (_extract_from_acroform).


##### `_build_field_from_dict`  (lines 64–74)

```
def _build_field_from_dict(raw: dict, name: str) -> FormField
```

**Purpose**: Turns raw PDF field data into one of this file’s simpler Python field objects. It translates PDF field type codes into plain categories such as text, checkbox, choice, or unknown.

**Data flow**: It receives a raw PDF field dictionary and a field name. It reads the PDF field type, chooses the right builder for checkboxes or choice lists when needed, and returns a FormField-style object that the rest of the file can use.

**Call relations**: Both _extract_from_acroform and _extract_from_widgets call this when they discover a field. It delegates checkbox details to _build_checkbox and choice-list details to _build_choice.

*Call graph*: calls 2 internal fn (_build_checkbox, _build_choice); called by 2 (_extract_from_acroform, _extract_from_widgets); 1 external calls (__init__).


##### `_build_checkbox`  (lines 77–92)

```
def _build_checkbox(raw: dict, name: str) -> CheckboxField
```

**Purpose**: Creates a checkbox description and tries to identify which stored value means checked and which means unchecked. This is important because PDFs may use custom names for the checked state instead of a simple true or false.

**Data flow**: It receives raw PDF data and a field name, reads the available state names, and decides the on and off values. It returns a CheckboxField, and if the states look unusual it prints a warning so a person knows to visually check the result.

**Call relations**: _build_field_from_dict calls this whenever it sees a button-type PDF field that it treats as a checkbox. The returned object later helps extraction output usable JSON and helps filling validate values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_build_choice`  (lines 95–102)

```
def _build_choice(raw: dict, name: str) -> ChoiceField
```

**Purpose**: Creates a description of a dropdown or list-style PDF choice field. It records both the stored value and the text a person may see.

**Data flow**: It receives raw PDF data and a field name, walks through the field’s available states, and turns each option into a small dictionary with value and text. It returns a ChoiceField containing all options.

**Call relations**: _build_field_from_dict calls this when it finds a PDF choice field. The choice list is later written into extracted JSON and used to reject invalid fill values.

*Call graph*: called by 1 (_build_field_from_dict); 1 external calls (__init__).


##### `_extract_checkbox_on_value`  (lines 105–115)

```
def _extract_checkbox_on_value(resolved: dict, cb: CheckboxField) -> None
```

**Purpose**: Fills in a checkbox’s checked value when the earlier metadata did not provide one. PDFs often hide this value inside the appearance data, which describes how the checkbox should look.

**Data flow**: It receives a resolved PDF annotation and a CheckboxField object. If the checkbox already has an on value, it leaves it alone; otherwise it looks inside the annotation’s appearance states, picks the non-Off state as the on value, and updates the CheckboxField in place.

**Call relations**: _extract_from_widgets calls this after building a checkbox from a page widget. It patches missing checkbox information before the field is added to the extracted list.

*Call graph*: called by 1 (_extract_from_widgets).


##### `_flip_rect`  (lines 118–120)

```
def _flip_rect(rect: list, page_height: float) -> list[float]
```

**Purpose**: Converts a PDF rectangle from PDF-style coordinates to top-down coordinates that are easier for people and layout tools to read. PDF coordinates usually start at the bottom-left of the page, while people often think from the top-left.

**Data flow**: It receives a rectangle and the page height. It keeps the left and right values, flips the vertical positions around the page height, and returns a new rectangle in the adjusted coordinate system.

**Call relations**: _extract_from_widgets, _extract_from_acroform, and _collect_radio_option call this whenever they record where a field appears on a page. It gives all extracted field locations the same coordinate convention.

*Call graph*: called by 3 (_collect_radio_option, _extract_from_acroform, _extract_from_widgets).


##### `_extract_from_widgets`  (lines 123–144)

```
def _extract_from_widgets(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts fillable fields directly from page widgets when the PDF’s main form list is missing or incomplete. This is the backup path for less tidy PDFs.

**Data flow**: It receives a PDF reader, scans each page’s annotations, keeps only widget annotations with field types, builds field objects, records page number and rectangle, fixes checkbox on values when possible, and returns a list of fields.

**Call relations**: _extract_from_acroform calls this when pypdf cannot find normal AcroForm fields. It uses _build_field_from_dict, _flip_rect, and _extract_checkbox_on_value to turn raw page annotations into the same kind of field list used elsewhere.

*Call graph*: calls 3 internal fn (_build_field_from_dict, _extract_checkbox_on_value, _flip_rect); called by 1 (_extract_from_acroform).


##### `_extract_from_acroform`  (lines 147–182)

```
def _extract_from_acroform(reader: PdfReader) -> list[FormField]
```

**Purpose**: Extracts the usable field map from a PDF form. This is the main reader for form metadata, and it also knows how to locate fields on pages and collect radio-button options.

**Data flow**: It receives a PDF reader, asks pypdf for the form fields, and falls back to widget scanning if none are found. For normal fields, it builds field objects, matches them to page annotations to set page and rectangle, gathers radio groups separately, skips fields that cannot be located, sorts the result, and returns the combined field list.

**Call relations**: cmd_extract calls this to produce the JSON field map, and cmd_fill calls it to know what values are valid before writing a filled PDF. Inside, it uses _full_field_name, _build_field_from_dict, _collect_radio_option, _flip_rect, and sometimes _extract_from_widgets.

*Call graph*: calls 5 internal fn (_build_field_from_dict, _collect_radio_option, _extract_from_widgets, _flip_rect, _full_field_name); called by 2 (cmd_extract, cmd_fill); 1 external calls (get_fields).


##### `_collect_radio_option`  (lines 185–204)

```
def _collect_radio_option(ann: dict, name: str, page_idx: int, page_height: float, radio_groups: dict[str, RadioGroup]) -> None
```

**Purpose**: Adds one visible radio-button option to a radio group description. Radio buttons are grouped fields where one name has several possible choices, each represented by its own widget.

**Data flow**: It receives one annotation, the group name, page information, page height, and the radio-group collection being built. It finds the single non-Off appearance value, creates the group if needed, converts the option rectangle, and appends that option to the group.

**Call relations**: _extract_from_acroform calls this while walking page annotations for fields that look like radio groups. It uses _flip_rect so radio option locations match the rest of the extracted field data.

*Call graph*: calls 1 internal fn (_flip_rect); called by 1 (_extract_from_acroform); 1 external calls (__init__).


##### `_field_sort_key`  (lines 210–217)

```
def _field_sort_key(f: FormField) -> tuple
```

**Purpose**: Provides a predictable reading order for extracted fields. It sorts fields by page, then approximate row, then left-to-right position.

**Data flow**: It receives a field, chooses the field rectangle or the first radio option rectangle, groups nearby vertical positions into rows, and returns a sorting tuple. The tuple is not shown to users; it is only used to order the extracted list.

**Call relations**: _extract_from_acroform uses this when sorting the final combined field list. The result makes the JSON output easier to read and compare because fields appear in page order rather than random PDF storage order.


##### `_field_to_dict`  (lines 220–236)

```
def _field_to_dict(f: FormField) -> dict
```

**Purpose**: Converts one internal field object into a JSON-friendly dictionary. This is the last translation step before writing the extracted form map to disk.

**Data flow**: It receives a FormField or one of its specialized versions. It writes common information such as name, kind, page, and rectangle, then adds checkbox values, radio options, or choice options when those apply, and returns a plain dictionary.

**Call relations**: cmd_extract calls this for every field returned by _extract_from_acroform. The dictionaries it produces are then serialized as JSON.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_fill_value`  (lines 239–252)

```
def _validate_fill_value(f: FormField, value: str) -> str | None
```

**Purpose**: Checks whether a requested value is allowed for a specific field. It prevents writing impossible checkbox, radio, or choice values into a PDF.

**Data flow**: It receives a field description and a proposed string value. For checkboxes it checks against the known on/off values; for radio groups and choice fields it checks against the known option values. It returns an error message if the value is invalid, or nothing if it is acceptable.

**Call relations**: _validate_fill_entries calls this while checking the JSON values supplied to the fill command. Its result decides whether filling can continue or must stop with errors.

*Call graph*: called by 1 (_validate_fill_entries).


##### `cmd_detect`  (lines 255–265)

```
def cmd_detect(args: list[str]) -> None
```

**Purpose**: Implements the detect command, which answers a simple question: does this PDF appear to contain fillable form fields?

**Data flow**: It receives command-line arguments, expects exactly one PDF path, opens that PDF, and checks for normal form fields or orphaned widgets. It prints either a success message or a message suggesting manual layout annotation if no fields are found; on bad usage it prints help and exits with an error.

**Call relations**: main dispatches to this command when the user runs formfill.py detect. It uses pypdf to read the file and _has_orphaned_widgets as the backup check.

*Call graph*: calls 1 internal fn (_has_orphaned_widgets); 3 external calls (Path, PdfReader, exit).


##### `cmd_extract`  (lines 268–278)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: Implements the extract command, which writes a JSON description of all fillable fields in a PDF. This gives people or other tools a template for what can be filled.

**Data flow**: It receives command-line arguments, expects an input PDF path and output JSON path, opens the PDF, extracts fields, converts each field to a dictionary, creates the output folder if needed, writes pretty-printed JSON, and reports how many fields were written.

**Call relations**: main dispatches to this command when the user runs formfill.py extract. It relies on _extract_from_acroform for PDF understanding and _field_to_dict for JSON-ready output.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _field_to_dict); 4 external calls (dumps, Path, PdfReader, exit).


##### `cmd_fill`  (lines 281–311)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: Implements the fill command, which creates a new PDF with form fields populated from JSON values. It is the write side of this tool.

**Data flow**: It receives command-line arguments, expects an input PDF, a values JSON file, and an output PDF path. It loads the values, extracts field metadata from the PDF, validates every requested entry, groups values by page, updates the corresponding PDF pages, writes the new PDF, and prints how many fields were filled.

**Call relations**: main dispatches to this command when the user runs formfill.py fill. It calls _extract_from_acroform to understand the form and _validate_fill_entries before handing valid page values to pypdf’s writer.

*Call graph*: calls 2 internal fn (_extract_from_acroform, _validate_fill_entries); 5 external calls (loads, Path, PdfReader, PdfWriter, exit).


##### `_validate_fill_entries`  (lines 314–334)

```
def _validate_fill_entries(values: list[dict], meta_by_name: dict[str, FormField]) -> bool
```

**Purpose**: Checks the whole list of requested fill entries before any PDF is written. This gives the user clear errors for bad field names, wrong pages, or invalid option values.

**Data flow**: It receives the JSON entries and a lookup table of known fields by name. For each entry, it checks that the field exists, that the page matches when supplied, and that the value is allowed when supplied. It prints any errors it finds and returns true if there was at least one error.

**Call relations**: cmd_fill calls this after reading the values file and extracting field metadata. It calls _validate_fill_value for field-type-specific checks, and cmd_fill stops if this function reports errors.

*Call graph*: calls 1 internal fn (_validate_fill_value); called by 1 (cmd_fill).


##### `main`  (lines 344–348)

```
def main() -> None
```

**Purpose**: Acts as the command-line doorway for this file. It reads the requested subcommand and sends the remaining arguments to the matching command function.

**Data flow**: It reads sys.argv, checks that a known subcommand was supplied, prints a usage message and exits if not, and otherwise invokes the selected command with the rest of the arguments.

**Call relations**: Python calls this when the file is run directly as a script. It is the top-level dispatcher for cmd_detect, cmd_extract, and cmd_fill through the SUBCOMMANDS table.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/layout.py`

`entrypoint` · `manual command-line PDF extraction, preview, or fill`

Many PDFs look like forms but do not contain real fillable fields. That means software cannot simply ask the PDF, “where should this answer go?” This file helps bridge that gap. It treats the PDF more like a picture with structure: words, horizontal lines, small checkbox-like rectangles, and page sizes.

The tool has three modes. In extract mode, it opens a PDF and records each page’s text, long horizontal rules, likely tick boxes, and row bands between rules. This produces JSON that a person or another tool can use as a map of the page. In preview mode, it draws red and blue boxes onto an image of a page so someone can visually check whether the planned answer areas are in the right place. In fill mode, it reads a field-definition JSON file and places text onto the PDF using FreeText annotations, which are visible text boxes added on top of the page.

A key detail is coordinate conversion. Images often count positions from the top-left corner, while PDFs commonly place annotations using a bottom-left origin. CoordMapper is the “translator” between those coordinate systems, like converting between two map grids. The fill step also checks for common mistakes, such as text boxes that are too short for the font or boxes that overlap.

#### Function details

##### `CoordMapper.to_annotation_rect`  (lines 44–63)

```
def to_annotation_rect(self, bbox: list[float]) -> tuple[float, float, float, float]
```

**Purpose**: This method converts a rectangle from the source coordinate system into the rectangle format needed for a PDF annotation. Someone uses it when they know where text should appear, but the coordinates may have come from an image or from PDF layout data.

**Data flow**: It receives a bounding box as four numbers. It reads the mapper’s PDF size, source size, and coordinate-system setting. If the source is an image, it scales the box to PDF page size and flips the vertical direction; otherwise it keeps the horizontal numbers and flips the vertical direction for PDF annotation placement. It returns a four-number rectangle ready for pypdf to use.

**Call relations**: During the fill command, _validate_and_fill creates a CoordMapper for each field’s page and asks this method to translate the field’s content area. The translated rectangle is then handed to the FreeText annotation so the text lands in the intended place on the PDF.


##### `_extract_page`  (lines 66–120)

```
def _extract_page(page: pdfplumber.page.Page, page_num: int) -> PageLayout
```

**Purpose**: This function reads one PDF page and turns visible page features into a simple PageLayout record. It is used to build a practical map of a non-fillable form page.

**Data flow**: It receives a pdfplumber page object and a page number. It reads the page width and height, scans line objects to find long horizontal rules, scans rectangle objects to find small square-ish boxes that look like checkboxes, and asks the page for extracted words. It stores these findings as rounded coordinates in a PageLayout object and returns that object.

**Call relations**: _extract_all_pages calls this once for each page in the PDF. After _extract_page returns the raw page layout, _extract_all_pages passes that layout to _compute_row_ranges so row bands can be added before the page is included in the final extraction result.

*Call graph*: called by 1 (_extract_all_pages); 2 external calls (__init__, extract_words).


##### `_compute_row_ranges`  (lines 123–132)

```
def _compute_row_ranges(layout: PageLayout) -> None
```

**Purpose**: This function finds the vertical spaces between horizontal rules on a page. Those spaces can represent rows in a form or table, which makes the extracted layout easier to understand.

**Data flow**: It receives a PageLayout that already contains horizontal rule positions. It sorts the rule heights from top to bottom and, for each neighboring pair, creates a row range with a top, bottom, and height. It changes the PageLayout in place by adding these row ranges and does not return a separate value.

**Call relations**: _extract_all_pages calls this right after _extract_page has collected the page’s horizontal rules. The added row ranges later travel through _pages_to_dict into the JSON output written by cmd_extract.

*Call graph*: called by 1 (_extract_all_pages).


##### `_extract_all_pages`  (lines 135–142)

```
def _extract_all_pages(pdf_path: str) -> list[PageLayout]
```

**Purpose**: This function scans an entire PDF and builds layout maps for every page. It is the main worker behind the extract command.

**Data flow**: It receives the path to a PDF file. It opens the file with pdfplumber, loops through each page, extracts the page layout with _extract_page, adds row ranges with _compute_row_ranges, and appends the finished layout to a list. It returns the list of PageLayout objects.

**Call relations**: cmd_extract calls this after checking the command-line arguments. _extract_all_pages coordinates the page-by-page work, then hands the collected layouts back to cmd_extract so they can be converted to plain dictionaries and saved as JSON.

*Call graph*: calls 2 internal fn (_compute_row_ranges, _extract_page); called by 1 (cmd_extract); 1 external calls (open).


##### `_pages_to_dict`  (lines 145–157)

```
def _pages_to_dict(pages: list[PageLayout]) -> list[dict]
```

**Purpose**: This function turns PageLayout objects into plain dictionaries that can be written as JSON. It exists because JSON files need basic data shapes, not Python dataclass instances.

**Data flow**: It receives a list of PageLayout objects. For each one, it copies the page number, size, text elements, horizontal rules, tick boxes, and row ranges into a dictionary. It returns a list of those dictionaries.

**Call relations**: cmd_extract calls this after _extract_all_pages finishes scanning the PDF. The returned dictionaries are then passed to json.dumps so the layout map can be saved to the requested output file.

*Call graph*: called by 1 (cmd_extract).


##### `_validate_and_fill`  (lines 160–243)

```
def _validate_and_fill(input_path: str, fields_path: str, output_path: str) -> None
```

**Purpose**: This function reads planned form fields, checks for obvious layout problems, and writes the requested text onto a PDF. It is the main worker behind the fill command.

**Data flow**: It receives an input PDF path, a fields JSON path, and an output PDF path. It reads the JSON, opens the PDF, records each page’s dimensions, and loops through each field definition. For fields with text, it checks whether the content area is tall enough for the font and whether it overlaps previously placed content or labels. If problems are found, it prints errors and exits. If the field is valid, it converts the field rectangle with CoordMapper, creates a FreeText annotation, and adds it to the correct page. At the end it writes the new PDF to disk and prints how many boxes were placed.

**Call relations**: cmd_fill calls this after validating the command-line argument count. Inside the fill flow, this function uses _rects_overlap to catch collisions and CoordMapper.to_annotation_rect to translate field coordinates before creating pypdf FreeText annotations.

*Call graph*: calls 1 internal fn (_rects_overlap); called by 1 (cmd_fill); 7 external calls (__init__, loads, Path, PdfReader, PdfWriter, FreeText, exit).


##### `_rects_overlap`  (lines 246–247)

```
def _rects_overlap(a: list[float], b: list[float]) -> bool
```

**Purpose**: This helper answers one simple question: do two rectangles touch or cover the same space? It is used to prevent filled-in fields from being placed on top of each other.

**Data flow**: It receives two rectangles, each described by left, top, right, and bottom numbers. It compares their edges. If one rectangle is completely to the side or above the other, it returns false; otherwise it returns true, meaning the rectangles overlap.

**Call relations**: _validate_and_fill calls this while checking each new field against fields already placed on the same page. Its result becomes part of the validation decision: overlaps are collected as errors before any output PDF is accepted.

*Call graph*: called by 1 (_validate_and_fill).


##### `cmd_extract`  (lines 250–273)

```
def cmd_extract(args: list[str]) -> None
```

**Purpose**: This is the command handler for `layout.py extract`. It scans a PDF and writes a JSON layout map that describes visible form-like features.

**Data flow**: It receives the command arguments after the word `extract`. It expects an input PDF path and an output JSON path. If the arguments are wrong, it prints usage text and exits. Otherwise it scans all pages with _extract_all_pages, converts the result with _pages_to_dict, writes formatted JSON to disk, and prints a short summary of what it found.

**Call relations**: main calls this when the user chooses the extract subcommand. cmd_extract then drives the extraction pipeline: it delegates PDF reading to _extract_all_pages, JSON-friendly conversion to _pages_to_dict, and finally writes the result for later review or use.

*Call graph*: calls 2 internal fn (_extract_all_pages, _pages_to_dict); 3 external calls (dumps, Path, exit).


##### `cmd_preview`  (lines 276–299)

```
def cmd_preview(args: list[str]) -> None
```

**Purpose**: This is the command handler for `layout.py preview`. It draws the planned field boxes onto a page image so a person can check placement before modifying a PDF.

**Data flow**: It receives the command arguments after the word `preview`. It expects a page number, a fields JSON path, an input image path, and an output image path. It reads the field definitions, opens the image, draws red rectangles around content areas and blue rectangles around label boxes for the chosen page, saves the marked-up image, and prints how many fields were highlighted.

**Call relations**: main calls this when the user chooses the preview subcommand. Unlike fill, it does not write to the PDF; it uses the same field-definition data as a visual safety check before cmd_fill and _validate_and_fill are used.

*Call graph*: 5 external calls (open, Draw, loads, Path, exit).


##### `cmd_fill`  (lines 302–306)

```
def cmd_fill(args: list[str]) -> None
```

**Purpose**: This is the command handler for `layout.py fill`. It starts the process of writing text annotations into a PDF based on a fields JSON file.

**Data flow**: It receives the command arguments after the word `fill`. It expects an input PDF path, a fields JSON path, and an output PDF path. If the arguments are wrong, it prints usage text and exits. If they are correct, it passes the three paths to _validate_and_fill.

**Call relations**: main calls this when the user chooses the fill subcommand. cmd_fill is a thin doorway into _validate_and_fill, which performs the real validation, coordinate conversion, annotation creation, and PDF writing.

*Call graph*: calls 1 internal fn (_validate_and_fill); 1 external calls (exit).


##### `main`  (lines 316–320)

```
def main() -> None
```

**Purpose**: This is the script’s command-line entry point. It decides which subcommand the user requested and sends the remaining arguments to the matching command handler.

**Data flow**: It reads sys.argv, which contains the command typed by the user. If there is no valid subcommand, it prints a usage message and exits with an error. If the subcommand is recognized, it looks up the matching function in SUBCOMMANDS and calls it with the rest of the arguments.

**Call relations**: When this file is run directly, the `if __name__ == "__main__"` block calls main. main then dispatches to cmd_extract, cmd_preview, or cmd_fill, which each take over their own workflow.

*Call graph*: 1 external calls (exit).


### `extensions/documents/ufo_ext_documents/skills/pdf/render.py`

`entrypoint` · `manual command-line PDF rendering`

This script solves a practical document problem: many tools can inspect or display images more easily than PDF pages. Given a PDF and an output folder, it renders every page as a separate PNG file named like page_1.png, page_2.png, and so on.

The main work is done by the pdf2image library, which reads the PDF and produces image objects. The script asks for a fairly clear render quality, 200 DPI, where DPI means “dots per inch” and controls how detailed the page image is. After each page is rendered, the script checks its width and height. If either side is larger than 1000 pixels, it shrinks the image while keeping the same shape. This is like photocopying a large page down to fit inside a smaller frame without stretching it.

The output folder is created if it does not already exist. Each image is saved there, and the script prints a short progress line showing the page number, file path, and final image size. At the end, it reports how many pages were rendered. Without this file, someone would need another way to convert PDFs into page images before image-based document processing could happen.

#### Function details

##### `render`  (lines 16–31)

```
def render(pdf_path: str, output_dir: str) -> None
```

**Purpose**: This function converts a PDF into one PNG image per page. It also keeps very large page images within a maximum size so later tools do not have to work with oversized files.

**Data flow**: It takes two pieces of text: the path to the input PDF and the path to the output folder. It creates the output folder if needed, asks pdf2image to turn the PDF pages into images, optionally shrinks each image to fit within 1000 by 1000 pixels, then saves each one as page_N.png. It does not return a value; its results are the PNG files written to disk and the progress messages printed to the screen.

**Call relations**: The command-line wrapper, main, calls this after checking that the user supplied the right two arguments. Inside, it relies on pathlib.Path to work with folders and file paths, and on pdf2image.convert_from_path to do the actual PDF-to-image conversion.

*Call graph*: called by 1 (main); 2 external calls (Path, convert_from_path).


##### `main`  (lines 34–38)

```
def main() -> None
```

**Purpose**: This is the command-line entry point for the script. It checks that the user gave an input PDF and an output directory, then starts the rendering work.

**Data flow**: It reads the command-line arguments from sys.argv. If there are not exactly two user-provided arguments, it prints the correct usage format and exits with an error code. If the arguments are present, it passes them to render, which creates the image files.

**Call relations**: This function runs when the file is executed directly as a script. It acts as the front desk: it validates the user request, stops early with sys.exit when the request is incomplete, or hands the valid PDF path and output folder to render for the real conversion work.

*Call graph*: calls 1 internal fn (render); 1 external calls (exit).
