# Document review state and annotation scripts  `stage-10.2.1`

This stage is the notebook and markup desk for the document review workflow. It is shared support used while the review is running and when results are written back to user files. First, constants.py keeps common file names, like the review state file and log file, in one place so all tools point to the same records. models.py defines what a review issue looks like, such as its location, message, and severity, and can turn it into a clear human comment. manage_state.py is the progress keeper. It updates a JSON state file, which is a simple structured text file, with sections, claims, issues, and the final summary. The annotation scripts then use that saved state. annotate_pdf.py marks matching text in a PDF and adds note-style comments. annotate_pptx.py inserts the findings as PowerPoint comments. annotate_xlsx.py copies a spreadsheet and adds the findings as Excel cell comments. Together, these files preserve review memory and turn findings into visible feedback.

## Files in this stage

### Review state foundations
Shared constants, issue models, and state-management tooling establish the persistent document-review data used by later annotation scripts.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/constants.py`

`config` · `cross-cutting`

This is a tiny configuration-style file, but it matters because it creates a single source of truth for two important filenames. The document review workflow needs somewhere to save its current progress, and somewhere to record what happened during review. This file names those two outputs: `document_review_state.json` for saved review state, and `review_log.jsonl` for the review log. A `.json` file stores structured data, while `.jsonl` means “JSON Lines,” where each line is a separate JSON record, which is useful for appending events over time. Without this file, other parts of the system would likely repeat these filenames directly. That makes mistakes easier: one script might write to one name while another tries to read from a slightly different name. This file avoids that by acting like a label on a shared cabinet drawer: everyone knows where the state and log should go.


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/models.py`

`data_model` · `document review comment formatting`

This file is a small shared vocabulary for the document review scripts. A review issue is something like a spelling problem, a logic concern, or a place where public data needs checking. The `DocumentIssue` type spells out what information every issue is expected to carry: its ID, kind, severity, location in the document, the original text, surrounding context, suggested replacement text, and links to related root issues when needed.

It also translates internal issue type names into friendlier labels. For example, `spelling_grammar` becomes `Spelling/Grammar`. This matters because machine-friendly names are useful in saved JSON files, but people reading comments need clear labels.

The main behavior here is `format_comment`, which takes one issue and builds the text of a review comment. It starts with a header showing the issue type and severity, adds the issue description, and optionally adds a suggested replacement if one exists. Without this file, different parts of the document-review tool might describe issues in different formats, making comments harder to read and harder to process reliably.

#### Function details

##### `format_comment`  (lines 29–35)

```
def format_comment(issue: DocumentIssue, include_suggestion: bool=True) -> str
```

**Purpose**: This function turns a structured document-review issue into a plain text comment that a person can read. It is used when the review system needs to show what is wrong and, when available, what replacement text is suggested.

**Data flow**: It receives a `DocumentIssue`, which is a dictionary-like record containing fields such as issue type, severity, description, and suggested new text. It looks up a friendly label for the issue type, combines that label with the severity and description, then checks whether suggested text should be included and whether the issue has any. It returns one formatted string with line breaks, and it does not change the issue itself.

**Call relations**: When formatting the comment, it asks the issue record for `new_text` using the record's `get` method so missing or empty suggestions can be skipped safely. No specific caller is shown in the provided graph, but this function is the handoff point from structured review data to human-readable comment text.

*Call graph*: 1 external calls (get).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/manage_state.py`

`entrypoint` · `command invocation during the document review workflow`

A document review has several stages: first outline the document, then find claims, fact-check them, find writing or content issues, and finally submit a summary. This script acts like a simple clipboard for that process. It stores the current state in document_review_state.json and writes an audit trail to a log file, so another tool or person can see what has already been done. The commands are small workflow steps. init starts a fresh review. add-sections records the document’s page ranges and moves the review toward claim-finding. add-claims attaches claims to a known section. update-claims records whether those claims were verified, refuted, or inconclusive. add-issues records problems and suggested fixes. submit marks the whole review complete. There are also read-only commands for listing claims, listing issues, and showing a dashboard. The file is strict about input: it checks that required fields are present, that values such as issue severity are from known choices, and that page numbers are valid. Without this script, the review process would have no shared memory: later steps would not know the sections, claims, issue IDs, or current phase created by earlier steps.

#### Function details

##### `_emit_result`  (lines 38–54)

```
def _emit_result(message: str, phase: str, document_name: str, **kwargs: list[dict]) -> None
```

**Purpose**: Prints the successful result of a command as one JSON object. This is useful when another program needs to read the command output reliably instead of scraping free-form text.

**Data flow**: It receives a message, the current phase, the document name, and optional extra progress details. It wraps them into a dictionary, converts that dictionary to JSON text, and prints it to standard output. It does not change the saved review state.

**Call relations**: The state-changing commands call this after they have saved their work. It is the final handoff from commands such as cmd_init, cmd_add_sections, cmd_add_claims, cmd_update_claims, cmd_add_issues, and cmd_submit to whatever user or automation is watching the command output.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 1 external calls (dumps).


##### `log_action`  (lines 57–72)

```
def log_action(command: str, phase_before: str | None, phase_after: str | None, **kwargs: object) -> None
```

**Purpose**: Adds a timestamped record of what command ran and how the review phase changed. This creates a simple audit trail, like writing each action in a notebook as the review progresses.

**Data flow**: It receives the command name, the phase before and after the command, and any extra details such as counts or IDs. It adds the current UTC time, turns the entry into JSON, and appends one line to the log file. The review state itself is not changed here.

**Call relations**: Most commands call this after doing their main work, including both write commands and the listing commands. It depends on JSON formatting and the current clock, then hands the log entry to the filesystem by appending to the configured log file.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_init, cmd_submit, cmd_update_claims); 3 external calls (now, dumps, Path).


##### `load_state`  (lines 75–84)

```
def load_state() -> dict
```

**Purpose**: Reads the saved document review state from disk. Commands use it when they need to know the current phase, sections, claims, issues, or summary.

**Data flow**: It looks for the configured state file. If the file is missing, it prints an error telling the user to run init first and stops the program. If the file exists, it reads the JSON text and turns it into a Python dictionary for the caller.

**Call relations**: Nearly every command except init starts by calling this, because they all need the shared review memory. It is the doorway from the saved JSON file into the command’s in-memory work.

*Call graph*: called by 8 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_get_claims, cmd_get_issues, cmd_status, cmd_submit, cmd_update_claims); 3 external calls (loads, Path, exit).


##### `save_state`  (lines 87–89)

```
def save_state(state: dict) -> None
```

**Purpose**: Writes the current review state back to disk. Commands use it after they have added or changed sections, claims, issues, the phase, or the final summary.

**Data flow**: It receives the whole state dictionary. It converts that dictionary into nicely indented JSON and writes it to the configured state file, replacing the previous version.

**Call relations**: The write commands call this after changing the state and before reporting success. It is the point where temporary in-memory changes become durable data for later commands.

*Call graph*: called by 6 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_init, cmd_submit, cmd_update_claims); 2 external calls (dumps, Path).


##### `warn_phase`  (lines 92–98)

```
def warn_phase(state: dict, expected: str) -> None
```

**Purpose**: Warns the user if a command is being run during a different review phase than expected. It does not block the command; it only signals that the workflow may be out of order.

**Data flow**: It receives the current state and the phase the command normally expects. If the actual phase differs, it prints a warning to standard error. It returns nothing and does not modify the state.

**Call relations**: Workflow commands call this near the start, after loading state. It sits between loading the saved state and doing the command’s real work, giving the user a chance to notice a phase mismatch while still allowing flexible operation.

*Call graph*: called by 5 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_submit, cmd_update_claims).


##### `_validate_required_keys`  (lines 101–109)

```
def _validate_required_keys(item: dict, required: list[str], label: str) -> None
```

**Purpose**: Checks that an incoming JSON object contains the fields a command needs. This prevents half-formed sections, claims, issues, or updates from being saved.

**Data flow**: It receives one item, a list of required field names, and a label for the error message. It finds any missing fields. If something is missing, it prints a clear error and stops the program; otherwise the caller continues.

**Call relations**: Commands that accept structured input call this before using that input. It protects cmd_add_sections, cmd_add_claims, cmd_update_claims, and cmd_add_issues from saving incomplete records.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (exit).


##### `_validate_enum`  (lines 112–119)

```
def _validate_enum(value: str, allowed: set[str], field_name: str) -> None
```

**Purpose**: Checks that a value is one of a known set of allowed choices. This keeps fields such as claim type, claim status, issue type, and severity consistent.

**Data flow**: It receives the value to check, the allowed set, and the field name. If the value is not allowed, it prints an error listing valid choices and stops the program. If it is valid, nothing is returned and the caller continues.

**Call relations**: Claim and issue commands call this while checking user-provided JSON. It prevents later code from seeing unexpected labels that would make filtering and summaries unreliable.

*Call graph*: called by 3 (cmd_add_claims, cmd_add_issues, cmd_update_claims); 1 external calls (exit).


##### `_validate_positive_int`  (lines 122–138)

```
def _validate_positive_int(value: str | int, field_name: str) -> int
```

**Purpose**: Turns a page number into an integer and makes sure it is at least 1. This keeps section page ranges meaningful.

**Data flow**: It receives a value that should represent a page number and the field name for messages. It tries to convert the value to an integer, rejects invalid or less-than-one values, and returns the cleaned integer.

**Call relations**: cmd_add_sections calls this for start_page and end_page before saving section ranges. It gives that command safe numeric values to compare and store.

*Call graph*: called by 1 (cmd_add_sections); 1 external calls (exit).


##### `_validate_nonempty_str`  (lines 141–156)

```
def _validate_nonempty_str(value: object, field_name: str) -> str
```

**Purpose**: Makes sure a field has usable text. It accepts strings and integers, converts the value to a string, and rejects blank values.

**Data flow**: It receives a value and a field name. If the value is not string-like enough for this script, or if it becomes empty after trimming spaces, it prints an error and stops. Otherwise it returns the non-empty string.

**Call relations**: cmd_add_claims and cmd_add_issues use this for locations, because every claim or issue needs a clear place in the document. It cleans the input before those commands create saved records.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_validate_anchor`  (lines 159–169)

```
def _validate_anchor(value: object, field_name: str) -> str | None
```

**Purpose**: Checks an optional anchor, which is a more precise text marker for where something appears in the document. It allows the anchor to be absent, but if present it must be real text.

**Data flow**: It receives a value and field name. If the value is null, it returns null. If the value is a non-empty string, it returns that string. Otherwise it prints an error and stops the program.

**Call relations**: cmd_add_claims and cmd_add_issues call this when building records. It lets those commands store either a useful anchor or a clean empty value, instead of saving ambiguous junk.

*Call graph*: called by 2 (cmd_add_claims, cmd_add_issues); 1 external calls (exit).


##### `_resolve_data`  (lines 172–176)

```
def _resolve_data(args: argparse.Namespace) -> str
```

**Purpose**: Gets JSON input from either a command-line argument or a file. This lets users provide short data directly or larger data through a separate file.

**Data flow**: It receives parsed command-line arguments. If a file path was provided, it reads and returns that file’s text. Otherwise it returns the direct data string from the command line.

**Call relations**: Commands that import lists of sections, claims, claim updates, or issues call this before parsing JSON. It hides the difference between --data and --file so those commands can work with one text value.

*Call graph*: called by 4 (cmd_add_claims, cmd_add_issues, cmd_add_sections, cmd_update_claims); 1 external calls (Path).


##### `cmd_init`  (lines 179–205)

```
def cmd_init(args: argparse.Namespace) -> None
```

**Purpose**: Starts a new document review. It creates the first state file with an outline phase and empty places for sections, claims, issues, counters, and summary.

**Data flow**: It receives command-line arguments containing the document filename. It rejects an empty filename, builds a fresh state dictionary, saves it to disk, logs the initialization, and prints a JSON success result.

**Call relations**: main dispatches to this when the user runs the init command. This is the one write command that does not load an existing state first; instead it creates the shared state that later commands will load.

*Call graph*: calls 3 internal fn (_emit_result, log_action, save_state); 1 external calls (exit).


##### `cmd_add_sections`  (lines 208–272)

```
def cmd_add_sections(args: argparse.Namespace) -> None
```

**Purpose**: Adds the document’s section outline and moves the review into the claim-finding phase. Sections give later claims and issues a place to belong.

**Data flow**: It loads the current state, warns if the phase is not outline, reads a JSON array from --data or --file, and checks each section for a name and valid page range. It stores the sections by name, changes the phase to find_claims, saves the state, logs the action, and prints a structured success result.

**Call relations**: main calls this for the add-sections command. It relies on load_state, _resolve_data, validation helpers, save_state, log_action, and _emit_result to move from raw user input to saved review progress.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_positive_int, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_claims`  (lines 275–357)

```
def cmd_add_claims(args: argparse.Namespace) -> None
```

**Purpose**: Adds factual or numerical claims found in a specific section. These claims become the checklist for the later fact-checking step.

**Data flow**: It loads the state, warns if the phase is not find_claims, checks that the named section exists, and reads a JSON array of claims. For each claim it checks required fields, allowed claim type, location, and optional anchor; then it assigns a new claim ID, marks the claim unverified, stores it, saves the state, logs the IDs, and prints the new claims.

**Call relations**: main dispatches here for add-claims. This command uses the shared validators and storage helpers, then hands a compact JSON summary to _emit_result so automation can see which claim IDs were created.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_update_claims`  (lines 360–445)

```
def cmd_update_claims(args: argparse.Namespace) -> None
```

**Purpose**: Records fact-check results for existing claims. It changes claims from unverified to verified, refuted, or inconclusive and can attach source URLs.

**Data flow**: It loads the state, warns if the phase is not fact_check, and automatically advances from find_claims to fact_check when needed. It reads a JSON array of updates, checks each claim ID and status, increments the claim’s attempt count, appends any source URLs, saves the state, logs status counts, and prints an update summary.

**Call relations**: main calls this for update-claims. It sits between claim collection and issue finding, using load_state and validators first, then save_state, log_action, and _emit_result after applying the fact-check results.

*Call graph*: calls 8 internal fn (_emit_result, _resolve_data, _validate_enum, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_add_issues`  (lines 448–543)

```
def cmd_add_issues(args: argparse.Namespace) -> None
```

**Purpose**: Adds problems found during review, such as factual problems, wording problems, or narrative logic issues. These records describe what should be fixed and often include suggested replacement text.

**Data flow**: It loads the state, warns if the phase is not find_issues, and advances from fact_check to find_issues when appropriate. It checks that the target section exists, reads a JSON array of issues, validates required fields, allowed issue type, severity, location, and anchor, assigns issue IDs, stores the issue records, saves, logs, and prints the new issue summaries.

**Call relations**: main dispatches here for add-issues. It uses the same input-reading and validation pattern as claims, but writes to the issues collection and uses the DocumentIssue shape for the saved record.

*Call graph*: calls 10 internal fn (_emit_result, _resolve_data, _validate_anchor, _validate_enum, _validate_nonempty_str, _validate_required_keys, load_state, log_action, save_state, warn_phase); 2 external calls (loads, exit).


##### `cmd_submit`  (lines 546–579)

```
def cmd_submit(args: argparse.Namespace) -> None
```

**Purpose**: Finishes the review by saving a final summary and marking the phase complete. This is the closing step of the workflow.

**Data flow**: It loads the state, warns if the current phase is not find_issues, and rejects an empty summary. It sets the phase to complete, stores the summary, saves the state, counts sections, claims, and issues, logs those totals, and prints a completion result.

**Call relations**: main calls this for the submit command. It follows the usual load-check-save-log-report flow and is the last state-changing command in the review process.

*Call graph*: calls 5 internal fn (_emit_result, load_state, log_action, save_state, warn_phase); 1 external calls (exit).


##### `cmd_get_claims`  (lines 582–622)

```
def cmd_get_claims(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved claims for a person to inspect, with optional filters by status or section. It is a read-only view into the review state.

**Data flow**: It loads all claims from the state, filters them if requested, logs the query and number of results, and prints a readable list with IDs, status, type, section, location, text, description, anchor, and sources when available. If nothing matches, it says so.

**Call relations**: main dispatches here for get-claims. Unlike the write commands, it does not save state or emit a JSON progress object; it loads state and logs the lookup, then produces human-friendly output.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_get_issues`  (lines 625–665)

```
def cmd_get_issues(args: argparse.Namespace) -> None
```

**Purpose**: Prints saved review issues for a person to inspect, with optional filters by severity or section. It helps reviewers focus on, for example, high-severity issues only.

**Data flow**: It loads all issues from the state, applies any severity or section filters, logs the lookup and result count, and prints each matching issue with its ID, severity, type, section, location, text, context, description, anchor, and suggested text when present. If no issue matches, it reports that.

**Call relations**: main calls this for get-issues. It mirrors cmd_get_claims, but reads from the issues collection instead of the claims collection and produces readable diagnostic output.

*Call graph*: calls 2 internal fn (load_state, log_action).


##### `cmd_status`  (lines 668–716)

```
def cmd_status(_args: argparse.Namespace) -> None
```

**Purpose**: Shows a dashboard of the current review progress. It gives a quick answer to: what document is being reviewed, what phase is it in, and how much work has been recorded?

**Data flow**: It loads the state and prints the document name and phase. If sections exist, it lists their page ranges. If claims exist, it counts them by status. If issues exist, it counts them by severity and type. If a final summary exists, it prints that too.

**Call relations**: main dispatches here for the status command. This is a read-only command that only needs load_state; it does not log the view and does not change the saved file.

*Call graph*: calls 1 internal fn (load_state).


##### `main`  (lines 719–775)

```
def main() -> None
```

**Purpose**: Defines the command-line interface and sends each command to the right function. It is the front desk for the script.

**Data flow**: It builds an argument parser with subcommands such as init, add-sections, add-claims, update-claims, add-issues, submit, get-claims, get-issues, and status. It reads the user’s command-line arguments, selects the matching command function, and passes the parsed arguments to it.

**Call relations**: When the file is run as a script, main is called first. It uses argparse, Python’s command-line parsing library, and then hands control to one of the cmd_* functions that performs the actual review action.

*Call graph*: 1 external calls (ArgumentParser).


### Annotated document outputs
Format-specific scripts read saved review issues and write them back into PDFs, PowerPoint decks, and spreadsheets as visible annotations or comments.

### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pdf.py`

`entrypoint` · `manual command-line run after document review results have been saved`

This file is a small command-line tool for turning document review results into something a person can inspect directly inside a PDF reader. The review system stores issues in a JSON file, which is a plain text data file. This script reads that file, opens the chosen PDF, and writes the issues back onto the pages as PDF annotations.

For each issue, it first checks which page the issue belongs to. If the page number is missing or invalid, it skips that issue rather than stopping the whole job. It then chooses a color based on severity: red for high, orange for medium, and yellow for low. It formats the issue into a readable comment, searches the page for the original text, and highlights the matching area if found. Think of it like a person using a highlighter pen and then sticking a note beside the highlighted sentence.

If the exact text cannot be found, the script still adds the sticky note at a fixed fallback spot on the page. That means the issue is not lost just because the PDF text search failed. Finally, it saves the annotated PDF to the requested output path and reports how many annotations were added.

#### Function details

##### `load_issues`  (lines 31–41)

```
def load_issues()
```

**Purpose**: This function reads the saved document review issues from the expected state file. It exists so the annotation step has a clean list of problems to place into the PDF.

**Data flow**: It starts with no direct input, but looks in the current working directory for the configured state filename. If the file is missing, it prints an error message and stops the script. If the file exists, it reads the JSON text, pulls out the stored issues, and returns them as a list.

**Call relations**: The main annotation flow calls this first, before opening and changing the PDF. It relies on the JSON reader to turn file text into Python data, and it may stop the whole command early if the required review state is not present.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_quads`  (lines 44–49)

```
def find_quads(page, original_text)
```

**Purpose**: This function searches one PDF page for the text that was originally flagged in a review issue. It returns the PDF locations that can be highlighted.

**Data flow**: It receives a PDF page and the original issue text. It first searches using a longer beginning slice of that text. If that finds nothing, it tries again with a shorter prefix, which gives the search a better chance when the full text does not match perfectly. It returns whatever matching page areas were found, or an empty result if none were found.

**Call relations**: The annotation function calls this for each issue after choosing the right page. Its result decides whether the script can draw a highlight around the exact text or must fall back to placing only a sticky note on the page.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 52–98)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It opens the input PDF, adds highlights and sticky notes for all saved review issues, and saves the result as a new PDF.

**Data flow**: It receives an input PDF path and an output PDF path. It loads the issue list, opens the PDF, then walks through the issues one by one. For each usable issue, it finds the page, chooses a severity color, formats the note text, searches for the original text, adds a highlight if possible, adds a sticky note, and counts the annotation. At the end, it saves the changed PDF to the output path, closes the file, and prints a summary.

**Call relations**: This function ties the whole script together. It calls `load_issues` to get the review data, `find_quads` to locate text on each page, PyMuPDF functions to open and modify the PDF, and `format_comment` to turn an issue into readable note text. The command-line block at the bottom calls it when a user runs the script with an input and output filename.

*Call graph*: calls 2 internal fn (find_quads, load_issues); 4 external calls (Point, Rect, open, format_comment).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_pptx.py`

`entrypoint` · `post-review annotation`

A PPTX file is really a ZIP archive full of XML files. PowerPoint comments are not stored as simple text pasted onto slides; they require several connected XML parts: comment files, author information, relationship links, and content type records. This file does that careful packaging work so review findings can appear as normal PowerPoint comments when the document is opened.

The script starts by reading document_review_state.json, the local file where the document review step saved its issues. Each issue is expected to say which slide it belongs to. The script groups issues by slide number, copies the input PPTX to the requested output path, then unzips the copy into a temporary folder. It writes one comment XML file for each slide that has issues, adds links from each slide to its comment file, writes a comment author named “Flying Object,” updates the presentation-level relationship file, and records the new XML parts in PowerPoint’s content type list. Finally, it zips everything back into the output PPTX and deletes the temporary folder.

An everyday analogy is adding labeled sticky notes inside a sealed binder: the script opens the binder, inserts the notes, updates the table of contents so PowerPoint can find them, then seals the binder again.

#### Function details

##### `load_issues`  (lines 49–59)

```
def load_issues() -> list[DocumentIssue]
```

**Purpose**: This function reads the saved document review issues from the expected state file. If that file is missing, it stops the script with a clear error because there is nothing reliable to annotate.

**Data flow**: It looks for the configured state filename in the current working directory. If the file exists, it reads its JSON text, takes the values under the “issues” section, and returns them as a list of issue records. If the file is absent, it prints an error to standard error and exits the program.

**Call relations**: This is the first step used by annotate. It supplies the raw issue data that the rest of the script turns into slide comments.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `group_by_slide`  (lines 62–71)

```
def group_by_slide(issues: list[DocumentIssue]) -> dict[int, list[DocumentIssue]]
```

**Purpose**: This function sorts review issues into buckets by slide number. It makes the later PowerPoint-writing step simpler because comments are stored per slide.

**Data flow**: It receives a list of issue records. For each issue, it tries to read the issue’s location as a whole number, treating that number as the slide number. Issues with missing or non-numeric locations are skipped. The result is a dictionary where each slide number points to the list of issues for that slide.

**Call relations**: annotate calls this after loading the issues. The grouped result is then passed to write_slide_comments and write_author_and_rels so those functions know which slide comment files and content-type entries to create.

*Call graph*: called by 1 (annotate).


##### `find_max_rel_id`  (lines 74–86)

```
def find_max_rel_id(rels_path: Path) -> int
```

**Purpose**: This function finds the largest existing relationship ID in a PowerPoint relationship file. It is used so newly added links get a fresh ID and do not collide with existing ones.

**Data flow**: It receives the path to a .rels XML file, which is a file PowerPoint uses to describe links between internal parts of the PPTX. If the file does not exist, it returns 0. If it exists, it parses the XML, scans each relationship’s Id value for a number, and returns the highest number it finds.

**Call relations**: add_relationship calls this when it needs to create a new relationship. It acts like checking the last ticket number before printing the next ticket.

*Call graph*: called by 1 (add_relationship); 3 external calls (exists, search, parse).


##### `add_relationship`  (lines 89–116)

```
def add_relationship(rels_path: Path, rel_type: str, target: str) -> None
```

**Purpose**: This function adds a link inside a PowerPoint relationship file, creating the file if needed. These links tell PowerPoint that a slide or presentation has a related comment file or comment-author file.

**Data flow**: It receives a relationship file path, a relationship type, and a target path. It opens the existing XML file or creates a new relationship list. If a relationship of the same type is already present, it leaves the file unchanged. Otherwise, it asks find_max_rel_id for the next safe ID number, adds the new relationship element, and writes the XML back to disk.

**Call relations**: write_slide_comments uses this to connect slides to their comment XML files. write_author_and_rels uses it to connect the overall presentation to the comment author list.

*Call graph*: calls 1 internal fn (find_max_rel_id); called by 2 (write_author_and_rels, write_slide_comments); 6 external calls (exists, Element, ElementTree, SubElement, parse, register_namespace).


##### `write_slide_comments`  (lines 119–162)

```
def write_slide_comments(tmp: Path, grouped: dict[int, list[DocumentIssue]]) -> int
```

**Purpose**: This function creates the actual comment XML files for slides that have review issues. It turns each issue into a PowerPoint comment entry.

**Data flow**: It receives the temporary unpacked PPTX folder and the issues grouped by slide. For each slide, it creates a comment list XML file under ppt/comments. For each issue on that slide, it assigns a running comment number, stamps it with the current UTC time, sets a default position, and writes formatted comment text using format_comment. It also adds the required relationship from the slide to its comment file. It returns the total number of comments created.

**Call relations**: annotate calls this after unpacking the PPTX. It hands off relationship creation to add_relationship and hands its final comment count to write_author_and_rels, which needs that count for the author metadata.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (now, format_comment, Element, ElementTree, SubElement).


##### `write_author_and_rels`  (lines 165–219)

```
def write_author_and_rels(tmp: Path, comment_idx: int, grouped: dict[int, list[DocumentIssue]]) -> None
```

**Purpose**: This function writes the supporting PowerPoint metadata that makes the comments valid and discoverable. Without it, the comment files might exist in the PPTX but PowerPoint would not know how to use them.

**Data flow**: It receives the temporary unpacked PPTX folder, the total comment count, and the issues grouped by slide. It writes ppt/commentAuthors.xml with a single author named “Flying Object,” records the highest comment index for that author, adds a presentation-level relationship to that author file, and updates [Content_Types].xml so the new author and comment XML parts have the correct PowerPoint content types.

**Call relations**: annotate calls this after write_slide_comments has created the slide comment files. This function relies on add_relationship to add the presentation-to-author link, and it completes the package-level bookkeeping needed for PowerPoint to recognize the comments.

*Call graph*: calls 1 internal fn (add_relationship); called by 1 (annotate); 5 external calls (Element, ElementTree, SubElement, parse, register_namespace).


##### `annotate`  (lines 222–251)

```
def annotate(input_path: str, output_path: str) -> None
```

**Purpose**: This is the main workflow for adding review comments to a PPTX. It coordinates reading issues, modifying the PowerPoint package, and writing the final annotated file.

**Data flow**: It receives an input PPTX path and an output PPTX path. It loads issues, stops early if there are none, groups them by slide, copies the input file to the output path, unzips the output into a temporary folder, writes comment files and supporting metadata, then rebuilds the PPTX ZIP archive from the modified folder. At the end it prints how many comments were added and always removes the temporary folder.

**Call relations**: This function is called by the command-line entry block when the script is run directly. It is the conductor for the file: it calls load_issues, group_by_slide, write_slide_comments, and write_author_and_rels in order, while using file-copying, ZIP, and cleanup operations around those steps.

*Call graph*: calls 4 internal fn (group_by_slide, load_issues, write_author_and_rels, write_slide_comments); 6 external calls (walk, Path, copy2, rmtree, mkdtemp, ZipFile).


### `extensions/documents/ufo_ext_documents/skills/document-review/scripts/annotate_xlsx.py`

`entrypoint` · `post-review annotation/export`

This file turns document review results into something a spreadsheet user can see directly inside Excel or another XLSX viewer. Without it, the review issues would stay in a separate JSON file, disconnected from the cells they refer to.

The script expects two command-line arguments: an input spreadsheet and an output spreadsheet. It also expects a saved review state file, named by STATE_FILENAME, in the current working directory. That state file contains the issues found during review. The script copies the input file first, so the original spreadsheet is not changed, then opens the copy and starts placing comments.

For each issue, it builds a readable comment using format_comment. Then it tries several ways to find the best cell. First it looks for the worksheet named in the issue location, ignoring letter case. If the issue gives an exact cell reference, called an anchor, it tries that cell first. If that fails, it searches for a cell containing the original text. If that still fails, it searches every worksheet. As a last resort, it puts the comment on cell A1 of the first worksheet, adding multiple fallback comments together if needed.

In everyday terms, it is like taking sticky notes from a review report and placing them onto the spreadsheet where they belong.

#### Function details

##### `load_issues`  (lines 25–35)

```
def load_issues()
```

**Purpose**: This function reads the saved document review results from the state file and returns the list of issues to add to the spreadsheet. If the state file is missing, it stops the script with a clear error because there is nothing to annotate.

**Data flow**: It starts with the expected state file name from STATE_FILENAME. It checks whether that file exists, reads its JSON text, turns that text into Python data, and pulls out the issue records. The result is a list of issue objects; if the file is missing, the program prints an error and exits instead.

**Call relations**: annotate calls this at the start, before touching the spreadsheet. load_issues uses the filesystem path helper, JSON parsing, and sys.exit so annotate can either receive usable review issues or stop early when the required review state is absent.

*Call graph*: called by 1 (annotate); 3 external calls (loads, Path, exit).


##### `find_cell`  (lines 38–47)

```
def find_cell(ws, text)
```

**Purpose**: This function searches one worksheet for the first cell that contains a given piece of text. It helps place a review comment near the spreadsheet content that caused the issue.

**Data flow**: It receives a worksheet and some target text. It normalizes the target text by trimming spaces and ignoring letter case, then scans every cell in every row. If a non-empty cell contains that text, it returns that cell; if no match is found, it returns nothing.

**Call relations**: annotate uses this whenever an exact cell reference is not available or does not work. It is first used inside the issue's named worksheet, then as a broader search across all worksheets if needed.

*Call graph*: called by 1 (annotate).


##### `find_worksheet`  (lines 50–55)

```
def find_worksheet(wb, location)
```

**Purpose**: This function finds a worksheet by name while ignoring differences in capital letters. It lets review issues point to a sheet in a forgiving way, so 'Budget' and 'budget' are treated as the same location.

**Data flow**: It receives a workbook and a location name. It compares that name with each worksheet title in lowercase form. If it finds a matching worksheet, it returns that worksheet; otherwise it returns nothing.

**Call relations**: annotate calls this when an issue includes a location. The returned worksheet becomes the preferred place to try exact cell placement and text search before the script falls back to searching the whole workbook.

*Call graph*: called by 1 (annotate).


##### `_place_on_cell`  (lines 58–65)

```
def _place_on_cell(ws, anchor, comment)
```

**Purpose**: This helper tries to attach a comment to one exact cell reference, such as B4. It is used when the review issue already knows precisely where the comment should go.

**Data flow**: It receives a worksheet, a cell reference, and a prepared comment. It tries to look up that cell and assign the comment to it. If the reference is valid, it returns true; if the reference is invalid or cannot be used, it returns false and leaves placement to later fallback steps.

**Call relations**: annotate calls this after finding the target worksheet and seeing that an issue has an anchor. If this direct placement succeeds, annotate does not need to search by text for that issue.

*Call graph*: called by 1 (annotate).


##### `annotate`  (lines 68–122)

```
def annotate(input_path, output_path)
```

**Purpose**: This is the main work function. It copies an input XLSX file, opens the copy, adds comments for every review issue, saves the result, and reports how many comments were added.

**Data flow**: It receives an input spreadsheet path and an output spreadsheet path. It reads review issues, copies the input file to the output path, opens the copied workbook, and turns each issue into an Excel comment. For each issue, it tries exact placement, then worksheet text search, then whole-workbook text search, and finally cell A1 as a fallback. It saves the workbook at the output path and prints a summary.

**Call relations**: This function is called by the command-line block when the script is run directly. It coordinates all helper functions: load_issues supplies the review data, format_comment creates readable comment text, find_worksheet and find_cell locate likely cells, _place_on_cell tries exact placement, and openpyxl writes the comments into the XLSX file.

*Call graph*: calls 4 internal fn (_place_on_cell, find_cell, find_worksheet, load_issues); 4 external calls (format_comment, Comment, load_workbook, copy2).
